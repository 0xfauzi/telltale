"""The Codex half of the reducer. Design 6.10, and the same rows activities.py writes.

Split from activities.py because that file is at the 800-line limit and this is where it
divides: the shapes, the ids and the provenance rules are shared and imported from
there, and what is here is the part that is true of Codex CLI 0.150.1 and of nothing
else. `activities.rebuild` dispatches to `activities()` below when every observation of
a capture came from the codex provider.

Four things Codex does that Claude does not, each measured on
fixtures/sources/codex/0.150.1 and each the reason for a block of code below.

  ONE TOOL CALL, THREE ID SPACES. Measured on S6: the EXECUTION id (`exec-<uuid>`) is
  on the hooks, on the OTel tool_decision, tool_result and sandbox_outcome, and on the
  rollout's item_completed. The MODEL's call id (`call_<random>`) is on a SECOND OTel
  tool_result and on the rollout's response_item pair, and carries no command, no path
  and no exit code. `item_N` is private to the exec stream and shares no id with
  anything. S6 has 8 calls and 16 tool_result records, so a group keyed by "any call
  id" would report 16 tool calls. Activities are keyed by the execution id, which is
  the only key more than one surface agrees on and the only one that reaches the
  command. The other two views stay observations, and `explain` still reaches them.

  A TURN IS NOT A REQUEST. `codex.exec.turn_completed` and the rollout's token_count
  are TURN totals, and one turn holds many model responses: S1 has 1 turn and 7
  responses. Summing the turn totals into per-request usage would double every number
  in the capture, so they are on their own `turn` activity, which no summary metric
  reads.

  INPUT TOKENS INCLUDE THE CACHED ONES. Measured: `last_token_usage.total_tokens` is
  input plus output and never mentions cached, and one S6 response reports input 20006
  with cached 19200. Claude's `input_tokens` is the FRESH part and design 6.11 defines
  `fresh_input = input_tokens`, so this module stores the difference under that name
  and the wire value under `total_input_tokens`. Storing the wire value as
  `input_tokens` would put 203844 in a field the summary prints as fresh input, where
  the fresh part is 20804.

  HOOK BODIES CARRY NO CLOCK, and on Codex neither does the exec stream. The OTel
  records and the rollout do. A tool call therefore takes its start from whichever
  correlated observation has a provider clock, and `clock` on the row says which of the
  two clocks it ended up on, exactly as it does for Claude.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telltale import activities as shared
from telltale import activities_tools, commands, correlate
from telltale.correlate import Fields

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.correlate import Obs
    from telltale.model import Activity

# One model response, with its own token counts. The other event_kinds of
# codex.otel.sse_event carry no usage at all (E02: response.created, response.output_*).
_RESPONSE = "codex.otel.sse_event"
_RESPONSE_KIND = "response.completed"

# The turn's own records. The first two carry turn_id; the last three do not, and
# `_turns` says what it does about that.
_TURN_KEYED = (
    "codex.rollout.event_msg.task_started",
    "codex.rollout.event_msg.task_complete",
    "codex.rollout.turn_context",
)
_TURN_LOOSE = (
    "codex.exec.turn_started",
    "codex.exec.turn_completed",
    "codex.rollout.event_msg.token_count",
)

# The observation types that name one tool call, in the execution id space.
_TOOL_TYPES = (
    "codex.hook.PreToolUse",
    "codex.hook.PostToolUse",
    "codex.otel.tool_decision",
    "codex.otel.tool_result",
    "codex.otel.sandbox_outcome",
    "codex.rollout.event_msg.item_completed",
)

# What an execution id looks like on every surface that carries one (E02: 8 of 8 on S6).
_EXEC_ID = "exec-"

# Design 6.10 names these as Codex's read-like commands. The classifier puts all of
# them in category `shell` along with echo, wc and which, which are not reads, so the
# set is here and not there: this module asks which SEGMENT HEADS a normal form has.
_READ_HEADS = frozenset({"cat", "sed", "rg", "grep", "ls", "find", "head", "tail"})
_SEPARATORS = frozenset({"&&", "||", ";", "|"})

# The item types the rollout and the exec stream give a changed file.
_EDIT_ITEMS = frozenset({"file_change"})
_EDIT_TOOLS = frozenset({"apply_patch"})

# The four counters under the summary's name for each, from the sse_event spelling.
_USAGE = (
    ("cache_read_tokens", "cached_token_count"),
    ("cache_creation_tokens", "cache_write_token_count"),
    ("output_tokens", "output_token_count"),
    ("reasoning_output_tokens", "reasoning_token_count"),
)
# The same four from the exec stream's turn.completed spelling.
_TURN_USAGE = (
    ("cache_read_tokens", "cached_input_tokens"),
    ("cache_creation_tokens", "cache_write_input_tokens"),
    ("output_tokens", "output_tokens"),
    ("reasoning_output_tokens", "reasoning_output_tokens"),
)


def activities(capture_id: str, observed: Sequence[Obs]) -> list[Activity]:
    """Every activity of one Codex capture, in no particular order.

    The lifecycle, compaction and passthrough rows are activities.py's, unchanged: the
    first two are driven by `correlate.ROLES`, which now names the Codex spellings, and
    the third is one row per telltale or external observation whatever wrote it.
    """
    return [
        shared.capture_activity(capture_id, observed, _context_window),
        *shared.lifecycle(capture_id, observed),
        *_requests(capture_id, observed),
        *_turns(capture_id, observed),
        *_tool_calls(capture_id, observed),
        *shared.compactions(capture_id, observed),
        *shared.passthrough(capture_id, observed),
    ]


def _context_window(built: Fields, observed: Sequence[Obs]) -> None:
    """The occupancy denominator design 6.11 says Codex has, and where it lives.

    `event_msg/task_started.model_context_window`, 258400 on every scenario that wrote
    a rollout. `--ephemeral` writes no rollout, so an ephemeral session has no
    denominator and the field is absent rather than defaulted. The ratio itself is
    W2-T1's; this records the number and where it came from.
    """
    for item in observed:
        window = item.payload.get("model_context_window")
        if item.type == "codex.rollout.event_msg.task_started" and window is not None:
            built.put("context_window", window, item.id)
            built.put("context_window_source", f"{item.type}.model_context_window")
            break
    models = sorted(
        {
            str(item.payload["model"])
            for item in observed
            if item.type == _RESPONSE and item.payload.get("model")
        }
    )
    built.put("models", models or None, *_ids(observed, _RESPONSE))


# -- model requests -------------------------------------------------------------------
def _requests(capture_id: str, observed: Sequence[Obs]) -> list[Activity]:
    """One activity per model RESPONSE, which is the only per-request record Codex has.

    E02's matrix says otel_logs cannot show per-request usage; that cell is corrected in
    codex.DRIFT. `codex.sse_event` with event.kind response.completed carries the six
    counters of one response, and S1 has 7 of them against 1 turn.
    """
    rows = [
        item
        for item in observed
        if item.type == _RESPONSE and item.payload.get("event_kind") == _RESPONSE_KIND
    ]
    rows.sort(key=lambda item: (item.ts or "", item.id))
    return [
        _request(capture_id, index, item) for index, item in enumerate(rows, start=1)
    ]


def _request(capture_id: str, index: int, item: Obs) -> Activity:
    built = Fields()
    built.put("request_index", index)
    built.put("usage_source", _RESPONSE)
    built.put("model", item.payload.get("model"), item.id)
    _fresh(built, item.payload, item.id, "input_token_count", "cached_token_count")
    for name, key in _USAGE:
        built.put(name, item.payload.get(key), item.id)
    return correlate.activity(
        capture_id,
        "model_request",
        item.id,
        actor="agent",
        started_at=item.ts,
        arrival=item.ingest,
        ended_at=None,
        built=built,
    )


def _fresh(
    built: Fields, usage: Mapping[str, Any], source: str, total: str, cached: str
) -> None:
    """Input tokens as the two quantities Codex reports in one field.

    `input_tokens` is the fresh part, which is what design 6.11 means by fresh_input and
    what the summary prints. `total_input_tokens` is the wire value. The subtraction is
    skipped, and `input_tokens` stays absent, unless both are integers and the cached
    part is not larger: an unknown stays unknown and is never a negative token count.
    """
    whole, reused = usage.get(total), usage.get(cached)
    built.put("total_input_tokens", whole, source)
    if isinstance(whole, int) and isinstance(reused, int) and whole >= reused:
        built.put("input_tokens", whole - reused, source)


# -- turns ----------------------------------------------------------------------------
def _turns(capture_id: str, observed: Sequence[Obs]) -> list[Activity]:
    """One activity per turn, holding the totals that must never reach a request row."""
    return [_turn(capture_id, turn, group) for turn, group in _turn_groups(observed)]


def _turn_groups(observed: Sequence[Obs]) -> list[tuple[str | None, list[Obs]]]:
    """The turn-level records by turn id, and where the ones with no turn id go.

    The turn's own usage is on records that carry NO turn id: the exec stream's
    turn.completed (DRIFT) and the rollout's token_count. They are attached to the turn
    only when the capture proves there is exactly one, which is what every session in
    E02's cohort is; with two turns and no id, each such record becomes its own row
    rather than being attached to a guess.
    """
    turns = sorted(
        {item.corr["turn_id"] for item in observed if item.corr.get("turn_id")}
    )
    groups: list[tuple[str | None, list[Obs]]] = [
        (turn, _keyed(observed, turn)) for turn in turns
    ]
    groups = [(turn, group) for turn, group in groups if group]
    loose = [item for item in observed if item.type in _TURN_LOOSE]
    if len(groups) == 1 and loose:
        return [(groups[0][0], [*groups[0][1], *loose])]
    return groups + [(None, [item]) for item in loose]


def _keyed(observed: Sequence[Obs], turn: str) -> list[Obs]:
    return [
        item
        for item in observed
        if item.type in _TURN_KEYED and item.corr.get("turn_id") == turn
    ]


def _turn(capture_id: str, turn_id: str | None, group: Sequence[Obs]) -> Activity:
    built = Fields()
    built.put("turn_id", turn_id, *[item.id for item in group])
    for name in ("duration_ms", "time_to_first_token_ms", "model_context_window",
                 "model", "effort", "approval_policy"):  # fmt: skip
        built.take(group, name)
    _turn_totals(built, group)
    return correlate.activity(
        capture_id,
        "turn",
        group[0].id,
        actor="agent",
        started_at=correlate.started(group),
        arrival=min(item.ingest for item in group),
        ended_at=correlate.ended(group),
        built=built,
    )


def _turn_totals(built: Fields, group: Sequence[Obs]) -> None:
    """The turn's totals from the exec stream, or from the rollout when there is no tee.

    Two surfaces carry them and they agree: measured on S1 and S6, turn.completed equals
    the LAST token_count's info.total_token_usage field for field (S6: 192407, 183040,
    1841, 564). The exec record is primary because it is one record and states the turn
    is over; the rollout's is a running total after every response, so only its last row
    is the turn's, and `pick` ranks by surface and not by position. Nothing is averaged
    and nothing is summed across the ten rows.
    """
    exec_row = _last(group, "codex.exec.turn_completed")
    if exec_row is not None:
        built.put("usage_source", "codex.exec.turn_completed")
        usage, source = exec_row.payload, exec_row.id
    else:
        counted = _last(group, "codex.rollout.event_msg.token_count")
        totals = counted.payload.get("total_token_usage") if counted else None
        if counted is None or not isinstance(totals, dict):
            return
        built.put("usage_source", f"{counted.type}.total_token_usage")
        usage, source = totals, counted.id
    _fresh(built, usage, source, "input_tokens", "cached_input_tokens")
    for name, key in _TURN_USAGE:
        built.put(name, usage.get(key), source)


def _last(group: Sequence[Obs], obs_type: str) -> Obs | None:
    """The latest observation of one type by arrival, or None when there is none."""
    rows = sorted((item for item in group if item.type == obs_type), key=lambda i: i.id)
    return rows[-1] if rows else None


# -- tool calls -----------------------------------------------------------------------
def _tool_calls(capture_id: str, observed: Sequence[Obs]) -> list[Activity]:
    """One activity per EXECUTION id, from the surfaces that named it. Design 6.10."""
    groups: dict[str, list[Obs]] = {}
    for item in observed:
        key = _exec_id(item)
        if key:
            groups.setdefault(key, []).append(item)
    return [_tool_call(capture_id, key, group) for key, group in sorted(groups.items())]


def _exec_id(item: Obs) -> str | None:
    """This observation's execution id, under whichever name its surface uses."""
    if item.type not in _TOOL_TYPES:
        return None
    found = (
        item.corr.get("tool_use_id")
        or item.corr.get("call_id")
        or item.payload.get("item_id")
    )
    text = str(found) if found else ""
    return text if text.startswith(_EXEC_ID) else None


def _tool_call(capture_id: str, exec_id: str, group: Sequence[Obs]) -> Activity:
    built = Fields()
    built.put("tool_use_id", exec_id, group[0].id)
    name = built.take(group, "tool_name")
    command = built.take(group, "command_norm", "command")
    built.take(group, "file_path", "path")
    for scalar in ("duration_ms", "status", "item_type", "kind",
                   "permission_mode", "decision", "outcome", "patch_bytes",
                   "output_truncated", "source"):  # fmt: skip
        built.take(group, scalar)
    category, scope = commands.classify(command) if command else (None, None)
    kind = _tool_type(str(name or ""), built.fields, category, command)
    # The same rule as the Claude half, and it has to be here too: a Codex tool call is
    # a shell command, so `uv run pytest | tail` is a chain here as well. The rollout
    # states a real exit code, which is the chain's; on a masked chain it is another
    # program's and is left off the row rather than read as the check's result.
    masked = kind == "verification_run" and commands.exit_masked(str(command))
    if not masked:
        built.take(group, "exit_code")
    built.put("exit_masked", masked or None)
    built.put("category", category)
    built.put(
        "categories",
        activities_tools.verification_categories(command)
        if kind == "verification_run"
        else None,
    )
    built.put("scope", scope)
    built.put("classifier_version", commands.CLASSIFIER_VERSION if command else None)
    if not masked:
        _outcome(built, group)
    return correlate.activity(
        capture_id,
        kind,
        group[0].id,
        actor="agent",
        started_at=correlate.started(group),
        arrival=min(item.ingest for item in group),
        ended_at=correlate.ended(group),
        built=built,
    )


def _outcome(built: Fields, group: Sequence[Obs]) -> None:
    """success, from the one surface that states it, then from the one that implies it.

    E02: OTel `tool_result.success` is the only field that says so outright. The rollout
    reports an exit code, and a command that exited 0 succeeded. A call with neither
    records no success at all rather than a default, which is why coverage for
    tool_calls is `partial` on the hook surface.
    """
    stated, source = correlate.pick(group, "success")
    if isinstance(stated, bool):
        built.put("success", stated, source)
        return
    code, where = correlate.pick(group, "exit_code")
    if isinstance(code, int):
        built.put("success", code == 0, where)


def _tool_type(
    name: str, fields: dict[str, Any], category: str | None, command: Any
) -> str:
    """The narrowest of design 6.10's five types that fits this Codex call."""
    if name in _EDIT_TOOLS or fields.get("item_type") in _EDIT_ITEMS:
        return "file_edit"
    if category in activities_tools.VERIFICATION:
        return "verification_run"
    if category == "shell" and _READ_HEADS.intersection(_heads(str(command or ""))):
        return "file_read"
    if category is not None:
        return "command"
    return "tool_call"


def _heads(command_norm: str) -> list[str]:
    """The first word of each `&&`, `||`, `;` or `|` segment of a normal form.

    The normal form is already tokenized by spaces with the separators as their own
    tokens (commands.normalize), so this is a split and not a second lexer. A leading
    flag is skipped so that `- -n sed` cannot happen; a leading VAR=value assignment
    cannot, because normalize has already reduced it to its name.
    """
    heads: list[str] = []
    expect = True
    for token in command_norm.split():
        if token in _SEPARATORS:
            expect = True
        elif expect and not token.startswith("-"):
            heads.append(token)
            expect = False
    return heads


def _ids(observed: Sequence[Obs], obs_type: str) -> list[str]:
    return [item.id for item in observed if item.type == obs_type]
