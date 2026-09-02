"""The tool-call half of the reducer: one activity per tool_use_id. Design 6.10.

Split out of activities.py at the 800-line ratchet. This is the block that moved
because it is the one with a thin interface: `tool_calls(capture_id, observed)` is the
whole of it, called once by `activities._activities` and once more by
`activities._subagents`, which reads the ACTIVITIES it returns and none of the
functions below. The request reducers were the other candidate and they are not
separable that way: `_requests` and `_attributed` share `_agent_of`, and `_requests`
takes the subagent activities as an argument, so moving them would either duplicate
that function or make the two files import each other.

Nothing here reads the store, writes a row or prints. It decides what one call WAS:
which of design 6.10's five types it is, whether it ran at all, and whether the exit
status a surface reported is a statement about the command that was classified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telltale import commands, correlate
from telltale.correlate import Fields, Obs

if TYPE_CHECKING:
    from collections.abc import Sequence

    from telltale.model import Activity


# Design 6.10: the categories that make a command a verification run.
VERIFICATION = frozenset(
    {"test", "typecheck", "lint", "format", "build", "benchmark", "security_scan"}
)
_READ_TOOLS = frozenset({"Read", "Glob", "Grep", "NotebookRead"})
_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_COMMAND_TOOLS = frozenset({"Bash", "BashOutput"})


def tool_calls(capture_id: str, observed: Sequence[Obs]) -> list[Activity]:
    """One activity per tool_use_id, from every surface that named it. Design 6.10.

    Correlation is by tool_use_id and never by time: hooks arrive with no clock, and
    parallel tool calls overlap, so the only join that holds is the id the provider put
    on all four surfaces.
    """
    groups: dict[str, list[Obs]] = {}
    for item in observed:
        tool_use_id = item.corr.get("tool_use_id")
        if tool_use_id:
            groups.setdefault(tool_use_id, []).append(item)
    denied = _denials(observed)
    return [
        _tool_call(capture_id, key, group, denied.get(key))
        for key, group in sorted(groups.items())
    ]


def _denials(observed: Sequence[Obs]) -> dict[str, str]:
    """The tool_use ids the provider REFUSED, each with the observation that said so.

    Two shapes, both on the stream and both measured on the five W2-E05 pilot captures:
    one `permission_denied` message per refused call carrying the id in its
    correlations, and the session result listing every one of them under
    `permission_denials` as {tool_name, tool_use_id}. BOTH are read because neither is
    always there. Four of the five captures carry three of each; the fifth carries
    three messages and no `claude.stream.result` observation at all, so reading the
    result alone would report its three refusals as three failed test runs. Across the
    owner's store the list is present and empty on 25 results, non-empty on 4 and
    absent on 3.

    A dict keyed by the id rather than a set of ids, because the source has to travel
    with the fact: the field this decides is written with the observation that stated
    it, so `telltale explain` reaches the row rather than the reducer's opinion. The
    first surface to name an id keeps the source; the two never disagree about whether
    it was refused, only about which row to point at.
    """
    found: dict[str, str] = {}
    for item in observed:
        for key in _denied_ids(item):
            found.setdefault(key, item.id)
    return found


def _denied_ids(item: Obs) -> list[str]:
    """The tool_use ids this one observation says were refused, over either shape.

    Every guard is an isinstance check because `permission_denials` is payload: the
    provider states its shape and this reducer does not get to assume it. A malformed
    entry contributes nothing rather than a key of None, which would refuse a call
    nobody named.
    """
    if item.role == "permission_denied":
        one = item.corr.get("tool_use_id")
        return [one] if isinstance(one, str) and one else []
    if item.role != "session_result":
        return []
    listed = item.payload.get("permission_denials")
    entries = listed if isinstance(listed, list) else []
    named = [entry.get("tool_use_id") for entry in entries if isinstance(entry, dict)]
    return [one for one in named if isinstance(one, str) and one]


def _tool_call(
    capture_id: str, tool_use_id: str, group: Sequence[Obs], denied: str | None
) -> Activity:
    built = Fields()
    built.put("tool_use_id", tool_use_id, group[0].id)
    name = built.take(group, "tool_name")
    command = built.take(group, "command_norm", "command")
    for scalar in ("file_path", "duration_ms", "exit_code", "exit_code_source",
                   "tool_input_size_bytes", "tool_result_size_bytes",
                   "tool_result_content_bytes", "subagent_type",
                   "permission_mode", "decision"):  # fmt: skip
        built.take(group, scalar)
    category, scope = commands.classify(command) if command else (None, None)
    built.put("category", category)
    built.put("scope", scope)
    built.put("classifier_version", commands.CLASSIFIER_VERSION if command else None)
    _tool_outcome(built, group, denied)
    parent = correlate.first(group, "parent_tool_use_id")
    agent_id = correlate.first(group, "agent_id")
    built.put("parent_tool_use_id", parent[0], parent[1])
    built.put("agent_id", agent_id[0], agent_id[1])
    return correlate.activity(
        capture_id,
        _tool_type(str(name or ""), category, denied is not None),
        group[0].id,
        actor=f"subagent:{agent_id[0]}" if agent_id[0] else "agent",
        started_at=correlate.started(group),
        arrival=min(item.ingest for item in group),
        ended_at=correlate.ended(group),
        built=built,
    )


def _tool_outcome(built: Fields, group: Sequence[Obs], denied: str | None) -> None:
    """What became of the call: refused before it ran, or executed and then success.

    A refusal is decided first and stops there: the denial arrives as a tool_result
    with `is_error` true (W2-E05, all five pilot captures), so every success route
    below would report a refused call as a failed one. Nothing ran. `executed` is the
    field measures read and `outcome` is the word a timeline shows.

    E01: OTel `tool_result.success` is the only field that STATES success. The hooks
    say it by event NAME (PostToolUse against PostToolUseFailure) and the stream by the
    presence of `is_error` on a result block. An exit code exists only on a failed
    stream result, so a successful call records `exit_code` None rather than 0: nothing
    observed a 0.
    """
    if denied is not None:
        built.put("outcome", "refused", denied)
        built.put("executed", False, denied)
        return
    # No source: nothing states that a call ran. What is observed is the absence of a
    # refusal, and every surface that could carry one was read to decide it.
    built.put("outcome", "executed")
    built.put("executed", True)
    stated, source = correlate.pick(group, "success")
    if isinstance(stated, bool):
        built.put("success", stated, source)
        return
    failed = [item for item in group if item.type.endswith("PostToolUseFailure")]
    if failed:
        built.put("success", False, failed[0].id)
        return
    is_error, where = correlate.pick(group, "is_error")
    if isinstance(is_error, bool):
        built.put("success", not is_error, where)


def _tool_type(name: str, category: str | None, refused: bool) -> str:
    """The narrowest of design 6.10's five types that fits this call.

    A refused call is never a verification_run. Design 6.10 defines one as a command
    that RAN a check, and a call the user was never asked about ran nothing: counting it
    would put a number in `agent_test_runs` for a test that does not exist. The category
    is still classified, so the row still says the agent tried to run a test, and
    `refused_tool_calls` counts it.
    """
    if category in VERIFICATION and not refused:
        return "verification_run"
    if name in _EDIT_TOOLS:
        return "file_edit"
    if name in _READ_TOOLS:
        return "file_read"
    if name in _COMMAND_TOOLS or category is not None:
        return "command"
    return "tool_call"
