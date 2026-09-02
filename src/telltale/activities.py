"""Observations in, activities out, with per-field provenance. Design 6.10.

`rebuild(store, capture_id)` reads every observation of one capture and writes the
activities that measures and the reading commands are allowed to see. It is registered
in `Store.reducers`, so `telltale rebuild` and `store.purge()` both run it.

This file decides WHICH observations describe one thing. correlate.py decides what the
resulting row says, and holds the rules about provenance, source ranking and ids.

Three rules shape the grouping below.

  Correlation is by id, never by time. A tool call is its tool_use_id across four
  surfaces, a request is its request_id across two, a subagent is the agent id the
  hooks and the stream share. Hook bodies carry no timestamp at all (E01) and parallel
  tool calls overlap, so a join on time would be a guess with the shape of a fact. The
  one exception is compaction, which no surface gives an id: it is cut into episodes
  in arrival order and the stream is joined to them by exact token match, and
  `compactions` says why at length.

  Two surfaces that disagree do not average. The primary keeps its value, the secondary
  is recorded as a `conflict` diagnostic naming both observation ids, and nothing is
  silently reconciled (spec 7.2). A conflict means the two surfaces answered the SAME
  question differently, which is why `_COMPARABLE_USAGE` is a list of three counters
  and not four: the stream's `output_tokens` is per assistant message and the OTel
  `api_request`'s is per request, so their difference is a fact about the surfaces and
  belongs on the Evidence rather than in a diagnostic per request.

  Time comes from the provider clock or it does not come at all. `started_at` is the
  earliest PROVIDER timestamp among the correlated observations, and falls back to
  arrival order only when no correlated observation carries a provider clock at all.
  The activity records which of the two it ended up on, so a reader is never shown a
  fact about the recorder as though it were a fact about the session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telltale import commands, correlate, providers
from telltale.correlate import Fields, Obs
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from telltale.model import Activity


# Design 6.10: the categories that make a command a verification run.
VERIFICATION = frozenset(
    {"test", "typecheck", "lint", "format", "build", "benchmark", "security_scan"}
)
_READ_TOOLS = frozenset({"Read", "Glob", "Grep", "NotebookRead"})
_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_COMMAND_TOOLS = frozenset({"Bash", "BashOutput"})

# Which lifecycle role becomes which one-per-observation activity (design 6.10).
_LIFECYCLE_ROLES = {
    "capture_started": "capture_start",
    "capture_ended": "capture_end",
    "session_start": "session_start",
    "session_end": "session_end",
    "session_result": "session_end",
    "model_switch": "model_switch",
}
_PASSTHROUGH_ROLES = {
    "repo_snapshot": "repo_snapshot",
    "repo_commit": "repo_commit",
    "correlation": "correlation",
    "outcome": "outcome",
}

# Strongest first. A capability seen on two delivered surfaces takes the stronger cell,
# and the ORDER of the middle two is a decision rather than a measurement: `partial`
# names a field that carries part of the fact, `derived` names a number read out of
# prose, and this file treats the field as the better evidence.
_COVERAGE_RANK = ("observed", "partial", "derived", "unavailable")


def rebuild(store: Store, capture_id: str) -> None:
    """Rewrite one capture's activities from its observations. Design 6.10.

    Evidence is the next reducer's job (measures.py, design 6.11), so this one writes
    exactly one table and the two can be read separately.
    """
    observed = [correlate.read(row) for row in store.observations(capture_id)]
    store.replace_activities(capture_id, _activities(capture_id, observed))
    _diagnose(store, capture_id, observed)


def _diagnose(store: Store, capture_id: str, observed: Sequence[Obs]) -> None:
    """Write the rows this reduction found, and only the ones not already there.

    A rebuild is a pure function of the capture, so it finds the same conflicts every
    time. Diagnostics are append-only and `store.rebuild` does not clear them, so
    writing unconditionally would make `show` report a growing count of problems for a
    capture where nothing changed. One conflict is one fact, recorded once.
    """
    already = {str(row["detail"]) for row in store.diagnostics(capture_id)}
    found = correlate.conflicts(observed, _by_request(observed))
    rows = [("conflict", detail) for detail in found]
    rows += [
        (
            "launcher",
            f"surface {surface} was configured and delivered no observation,"
            " so every capability that needs it is unavailable for this capture",
        )
        for surface in _silent(observed)
    ]
    for kind, detail in rows:
        if detail not in already:
            store.diagnose(kind, detail, capture_id)


def _activities(capture_id: str, observed: Sequence[Obs]) -> list[Activity]:
    """Every activity of one capture, in no particular order (the reader sorts).

    Dispatched on the provider of the whole capture, not of one row: a Codex session
    correlates by different ids and reports usage per turn as well as per response, and
    activities_codex.py holds that. Imported there and not at the top, because that
    module reuses the builders below.
    """
    if not observed:
        return []
    if {item.provider for item in observed} == {"codex"}:
        from telltale import activities_codex

        return activities_codex.activities(capture_id, observed)
    tools = _tool_calls(capture_id, observed)
    subagents = _subagents(capture_id, observed, tools)
    return [
        capture_activity(capture_id, observed),
        *lifecycle(capture_id, observed),
        *_requests(capture_id, observed, subagents),
        *tools,
        *compactions(capture_id, observed),
        *subagents,
        *passthrough(capture_id, observed),
    ]


# -- the capture itself ---------------------------------------------------------------
def capture_activity(
    capture_id: str,
    observed: Sequence[Obs],
    window: Callable[[Fields, Sequence[Obs]], None] | None = None,
) -> Activity:
    """The lifecycle row that carries measured coverage. Design 6.7 and 6.10.

    It exists whether or not the launcher wrote a telltale.capture_started observation,
    because a replayed fixture has none and coverage is not optional. Without one,
    `surfaces_configured` is None and the coverage below is computed from the surfaces
    that delivered, which is a weaker statement and says so.
    """
    first, last = observed[0], observed[-1]
    started = correlate.started(observed)
    delivered = sorted({item.surface for item in observed})
    configured = _configured(observed)
    built = Fields()
    built.put("provider", first.provider, first.id)
    built.put("surfaces_delivered", delivered, first.id, last.id)
    _stamped(built, observed)
    built.put(
        "surfaces_configured", configured, *correlate.ids(observed, "capture_started")
    )
    built.put("silent_surfaces", _silent(observed) or None)
    built.put("coverage", _coverage(first.provider, delivered))
    built.put("observation_count", len(observed))
    built.put("diagnostics_note", "counted by summary(), not stored here")
    (window or _context_window)(built, observed)
    built.fields.pop("diagnostics_note", None)
    return correlate.activity(
        capture_id,
        "lifecycle",
        first.id,
        actor="telltale",
        started_at=started,
        arrival=first.ingest,
        ended_at=correlate.ended(observed) or last.ingest,
        built=built,
        extra={"event": "capture"},
        key=f"capture:{first.id}",
    )


def _stamped(built: Fields, observed: Sequence[Obs]) -> None:
    """The repository and the environment this capture ran in, off the observation row.

    The launcher stamps both on every observation it accepts (design 6.2), so the first
    row that carries one is as good as any. A capture with no launcher has neither, and
    the summary says null rather than naming a repository nobody recorded.
    """
    repo = next((item for item in observed if item.repo), None)
    if repo is not None:
        built.put("repo_id", repo.repo, repo.id)
    env = next((item for item in observed if item.environment), None)
    if env is not None:
        built.put("environment_fingerprint_id", env.environment, env.id)


def _context_window(built: Fields, observed: Sequence[Obs]) -> None:
    """The only trustworthy occupancy denominator this provider offers (design 6.11).

    Claude reports it once, per model, on the stream result message. It is recorded
    here rather than turned into a ratio because the numerator is measures.py's to
    define, and a ratio with an invented numerator would be a number nobody measured.
    """
    for item in observed:
        usage = item.payload.get("model_usage")
        windows = _windows(usage)
        if windows:
            built.put("context_window", max(windows.values()), item.id)
            built.put("context_window_source", f"{item.type}.model_usage.contextWindow")
            built.put("models", sorted(windows), item.id)
            return


def _windows(usage: Any) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    return {
        str(model): int(facts["contextWindow"])
        for model, facts in usage.items()
        if isinstance(facts, dict) and isinstance(facts.get("contextWindow"), int)
    }


def _configured(observed: Sequence[Obs]) -> list[str] | None:
    """What the launcher said it configured, or None when nothing said. Design 6.7."""
    for item in observed:
        if item.role == "capture_started":
            value = item.payload.get("surfaces_configured")
            if isinstance(value, list):
                return sorted(str(name) for name in value)
            if isinstance(value, str):
                return [value]
    return None


def _silent(observed: Sequence[Obs]) -> list[str]:
    """Surfaces the launcher configured that delivered nothing at all."""
    configured = _configured(observed)
    if configured is None:
        return []
    return sorted(set(configured) - {item.surface for item in observed})


def _coverage(provider: str, delivered: Sequence[str]) -> dict[str, str]:
    """Per capability, the best cell among the surfaces that actually delivered."""
    try:
        table = providers.get(provider).CAPABILITIES
    except (ValueError, ImportError):
        return {}
    return {
        name: _best(cells.get(surface, "unavailable") for surface in delivered)
        for name, cells in sorted(table.items())
    }


def _best(states: Iterable[str]) -> str:
    ranked = sorted(states, key=_COVERAGE_RANK.index)
    return ranked[0] if ranked else "unavailable"


# -- lifecycle, one per observation ---------------------------------------------------
def lifecycle(capture_id: str, observed: Sequence[Obs]) -> list[Activity]:
    return [
        _lifecycle_one(capture_id, item, event)
        for item in observed
        if (event := _LIFECYCLE_ROLES.get(item.role or "")) is not None
    ]


def _lifecycle_one(capture_id: str, item: Obs, event: str) -> Activity:
    built = Fields()
    for name in ("model", "reason", "trigger", "claude_code_version", "permission_mode",
                 "exit_code", "is_error", "num_turns", "terminal_reason", "stop_reason",
                 "start_type", "surfaces_received"):  # fmt: skip
        built.put(name, item.payload.get(name), item.id)
    built.put("provider_session_id", item.session, item.id)
    return correlate.activity(
        capture_id,
        "lifecycle",
        item.id,
        actor="agent",
        started_at=item.ts,
        arrival=item.ingest,
        ended_at=None,
        built=built,
        extra={"event": event},
    )


# -- model requests -------------------------------------------------------------------
def _requests(
    capture_id: str, observed: Sequence[Obs], subagents: Sequence[Activity]
) -> list[Activity]:
    """One activity per api_request, plus one per stream-only request. Design 6.10.

    The OTel api_request is primary. A request that only the stream saw becomes an
    activity from the stream's per-message usage, which is the weaker source and is
    marked `usage_source` so that nothing later mistakes the two for one measurement.
    """
    primary = [item for item in observed if item.role == "request"]
    seen = {item.corr.get("request_id") for item in primary}
    extra = [
        group
        for key, group in _by_request(observed).items()
        if key not in seen and key is not None
    ]
    rows = [(item.ts, item.id, [item], "primary") for item in primary]
    rows += [(group[0].ts, group[0].id, group, "stream") for group in extra]
    rows.sort(key=lambda row: (row[0] or "", row[1]))
    agents = {
        str(activity.fields.get("agent_type")): activity.fields.get("agent_id")
        for activity in subagents
    }
    return [
        _request(capture_id, index, group, source, agents)
        for index, (_ts, _id, group, source) in enumerate(rows, start=1)
    ]


def _by_request(observed: Sequence[Obs]) -> dict[str | None, list[Obs]]:
    """Stream assistant messages carrying usage, grouped by the request they name."""
    groups: dict[str | None, list[Obs]] = {}
    for item in observed:
        if item.role == "assistant" and "input_tokens" in item.payload:
            groups.setdefault(item.corr.get("request_id"), []).append(item)
    return groups


def _request(
    capture_id: str,
    index: int,
    group: Sequence[Obs],
    source: str,
    agents: Mapping[str, Any],
) -> Activity:
    head = group[0]
    built = Fields()
    built.put("request_index", index)
    built.put("usage_source", source)
    for name in ("model", "query_source", "duration_ms", "cost_usd"):
        built.put(name, head.payload.get(name), head.id)
    for name in correlate.USAGE_KEYS:
        built.put(name, correlate.usage(head.payload, name), head.id)
    built.put("request_id", head.corr.get("request_id"), head.id)
    agent = _agent_of(head)
    built.put("agent_type", agent, head.id)
    return correlate.activity(
        capture_id,
        "model_request",
        head.id,
        actor=_actor(agent, agents.get(agent or "")),
        started_at=head.ts,
        arrival=head.ingest,
        ended_at=None,
        built=built,
    )


def _agent_of(item: Obs) -> str | None:
    """The subagent a request belongs to, from query_source. E01: `agent:builtin:X`."""
    source = item.payload.get("query_source")
    name = item.payload.get("agent_name")
    if isinstance(name, str) and name:
        return name
    if isinstance(source, str) and source.startswith("agent:"):
        return source.rsplit(":", 1)[-1]
    return None


def _actor(agent: str | None, agent_id: Any) -> str:
    if agent is None:
        return "agent"
    return f"subagent:{agent_id or agent}"


# -- tool calls -----------------------------------------------------------------------
def _tool_calls(capture_id: str, observed: Sequence[Obs]) -> list[Activity]:
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


# -- compaction -----------------------------------------------------------------------
def compactions(capture_id: str, observed: Sequence[Obs]) -> list[Activity]:
    """One activity per compaction, from the three surfaces that see one. Design 6.10.

    Episodes are cut in arrival order over the surfaces that arrive in real time: a
    record whose role the open episode already holds opens the next one. Measured on S7,
    that reproduces the four episodes exactly, including the PostCompact hooks, which
    carry no counts and no clock and would otherwise be unattributable.

    The stream is joined afterwards and by VALUE, not by order: a replay posts every
    stream line after every hook, so arrival order across the two is a replay artefact,
    while `pre_tokens` and `post_tokens` on a compact_boundary equal the OTel event's to
    the token. A boundary that matches nothing becomes its own episode rather than being
    attached to a guess.
    """
    episodes = _episodes(observed)
    _join_boundaries(episodes, observed)
    return [
        _compaction(capture_id, index, episode)
        for index, episode in enumerate(episodes, start=1)
    ]


_COMPACTION_ROLES = ("pre_compact", "compaction", "post_compact")


def _episodes(observed: Sequence[Obs]) -> list[list[Obs]]:
    episodes: list[list[Obs]] = []
    for item in observed:
        if item.role not in _COMPACTION_ROLES:
            continue
        if not episodes or any(other.role == item.role for other in episodes[-1]):
            episodes.append([])
        episodes[-1].append(item)
    return episodes


def _join_boundaries(episodes: list[list[Obs]], observed: Sequence[Obs]) -> None:
    for item in observed:
        if item.role != "compact_boundary":
            continue
        target = next(
            (
                episode
                for episode in episodes
                if _tokens(episode) == _tokens([item])
                and not any(other.role == "compact_boundary" for other in episode)
            ),
            None,
        )
        if target is None:
            episodes.append([])
            target = episodes[-1]
        target.append(item)


def _tokens(group: Sequence[Obs]) -> tuple[Any, Any]:
    return (
        correlate.pick(group, "pre_tokens")[0],
        correlate.pick(group, "post_tokens")[0],
    )


def _compaction(capture_id: str, index: int, group: Sequence[Obs]) -> Activity:
    built = Fields()
    built.put("compaction_index", index)
    for name in ("trigger", "pre_tokens", "post_tokens", "duration_ms", "success",
                 "error", "cumulative_dropped_tokens"):  # fmt: skip
        built.take(group, name)
    ids = [item.id for item in group]
    built.put("surfaces", sorted({item.surface for item in group}), *ids)
    return correlate.activity(
        capture_id,
        "compaction",
        group[0].id,
        actor="agent",
        started_at=correlate.started(group),
        arrival=min(item.ingest for item in group),
        ended_at=correlate.ended(group),
        built=built,
    )


# -- subagents ------------------------------------------------------------------------
def _subagents(
    capture_id: str, observed: Sequence[Obs], tools: Sequence[Activity]
) -> list[Activity]:
    """One activity per subagent, keyed by the id the hooks and the stream share.

    E01: `SubagentStart.agent_id` and `system:task_started.task_id` are the same string,
    and only the stream links either to the tool call that spawned it. The OTel
    `subagent_completed` names neither, so it is paired by agent type and only when the
    pairing is unambiguous; otherwise its numbers are left off rather than guessed.
    """
    groups: dict[str, list[Obs]] = {}
    for item in observed:
        key = _subagent_key(item)
        if key:
            groups.setdefault(key, []).append(item)
    done = [item for item in observed if item.role == "subagent_done"]
    return [
        _subagent(capture_id, key, group, _completion(done, group), observed, tools)
        for key, group in sorted(groups.items())
    ]


_SUBAGENT_ROLES = ("subagent_start", "subagent_stop", "task_started")

# A task the agent runs ITSELF, which starts no child. Claude Code 2.1.258 emits
# `system.task_started` for every Bash call with `task_type` "local_bash" and
# `is_backgrounded` false, and 2.1.257 did not. Measured on capture
# cap_01M1GPSMW1ZRXVADWZPF0KZ9H3 of this build: 5 such messages became 5 subagent
# activities on a session whose stream holds 29 Bash and 2 Write tool_use blocks and no
# Task call at all. S4 on 2.1.257 is the counter-example the rule has to keep: its one
# real subagent announces `task_type` "local_agent" and still counts. A task_started
# that names no task_type at all is not refused here, because no version has been
# measured emitting one and refusing it would drop a subagent on a guess.
_LOCAL_TASK_TYPES = frozenset({"local_bash"})


def _subagent_key(item: Obs) -> str | None:
    if item.role not in _SUBAGENT_ROLES:
        return None
    if item.role == "task_started" and _is_local_task(item):
        return None
    key = item.corr.get("agent_id") or item.payload.get("task_id")
    return str(key) if key else None


def _is_local_task(item: Obs) -> bool:
    return item.payload.get("task_type") in _LOCAL_TASK_TYPES


def _completion(done: Sequence[Obs], group: Sequence[Obs]) -> Obs | None:
    """The subagent_completed record for this agent, when only one can be meant."""
    kind = (
        correlate.pick(group, "agent_type")[0]
        or correlate.pick(group, "subagent_type")[0]
    )
    matches = [item for item in done if item.payload.get("agent_type") == kind]
    return matches[0] if len(matches) == 1 else None


def _subagent(
    capture_id: str,
    agent_id: str,
    group: Sequence[Obs],
    done: Obs | None,
    observed: Sequence[Obs],
    tools: Sequence[Activity],
) -> Activity:
    built = Fields()
    built.put("agent_id", agent_id, group[0].id)
    kind = (
        correlate.pick(group, "agent_type")[0]
        or correlate.pick(group, "subagent_type")[0]
    )
    built.put("agent_type", kind, *[item.id for item in group])
    spawn, spawn_id = correlate.pick(group, "tool_use_id")
    built.put("spawned_by_tool_use_id", spawn, spawn_id)
    if done is not None:
        built.put("provider_total_tokens", done.payload.get("total_tokens"), done.id)
        built.put("provider_tool_uses", done.payload.get("total_tool_uses"), done.id)
        built.put("duration_ms", done.payload.get("duration_ms"), done.id)
    _attributed(built, built.fields.get("agent_type"), observed)
    built.put("delegated_tool_calls", _delegated(agent_id, spawn, tools) or None)
    parent = next((item for item in tools if _is_spawn(item, spawn)), None)
    borrowed = None if correlate.started(group) else _spawn_start(parent)
    built.put("start_source", "spawning tool call" if borrowed else None)
    return correlate.activity(
        capture_id,
        "subagent",
        group[0].id,
        actor=f"subagent:{agent_id}",
        # SubagentStart and task_started carry no clock of any kind, so the spawning
        # tool call's start is the only provider time this capture holds for the child.
        # It is the same span: S4's Agent call reports 24885 ms and the child's own
        # subagent_completed reports 24876 ms, 9 ms apart on a 25 second call.
        started_at=correlate.started(group) or borrowed,
        arrival=min(item.ingest for item in group),
        ended_at=(done.ts if done else None) or correlate.ended(group),
        built=built,
    )


def _is_spawn(activity: Activity, tool_use_id: Any) -> bool:
    return bool(tool_use_id) and activity.fields.get("tool_use_id") == tool_use_id


def _spawn_start(parent: Activity | None) -> str | None:
    return parent.started_at if parent is not None else None


def _delegated(agent_id: str, spawn: Any, tools: Sequence[Activity]) -> list[str]:
    return sorted(
        activity.activity_id
        for activity in tools
        if activity.fields.get("agent_id") == agent_id
        or (spawn and activity.fields.get("parent_tool_use_id") == spawn)
    )


def _attributed(built: Fields, agent_type: Any, observed: Sequence[Obs]) -> None:
    """The tokens of the requests this agent made. Spec 13.6: never summed twice.

    These requests are already inside the capture's own totals; this is an attribution
    of part of that total, not an addition to it. The provider's own
    `subagent_completed.total_tokens` is recorded beside it under a different name
    because the two are different quantities: on S4 they are 20093 and 92479.
    """
    if not agent_type:
        return
    mine = [
        item
        for item in observed
        if item.role == "request" and _agent_of(item) == agent_type
    ]
    if not mine:
        return
    ids = [item.id for item in mine]
    built.put("attributed_requests", len(mine), *ids)
    total = 0
    for name in correlate.USAGE_KEYS:
        found = (correlate.usage(item.payload, name) for item in mine)
        values = [value for value in found if value is not None]
        if values:
            built.put(f"attributed_{name}", sum(values), *ids)
            total += sum(values)
    built.put("attributed_total_tokens", total, *ids)


# -- one activity per observation -----------------------------------------------------
def passthrough(capture_id: str, observed: Sequence[Obs]) -> list[Activity]:
    """repo_snapshot, repo_commit, correlation and outcome: one row per observation."""
    rows = []
    for item in observed:
        kind = _PASSTHROUGH_ROLES.get(item.role or "")
        if kind is None:
            continue
        built = Fields()
        for name, value in sorted(item.payload.items()):
            built.put(name, value, item.id)
        rows.append(
            correlate.activity(
                capture_id,
                kind,
                item.id,
                # By the observation TYPE, not the provider: the launcher stamps its
                # own rows with the provider it is wrapping (measured: a
                # telltale.repo.snapshot from `telltale run --provider claude` has
                # provider "claude"), so the provider column cannot say who wrote it.
                actor="telltale" if item.type.startswith("telltale.") else "external",
                started_at=item.ts,
                arrival=item.ingest,
                ended_at=None,
                built=built,
            )
        )
    return rows


# Design 6.10: the reducer registry is how `telltale rebuild` finds this. Registration
# at import is the whole wiring; nothing calls rebuild() by name outside the store.
_REDUCERS: list[Callable[[Store, str], None]] = Store.reducers
if rebuild not in _REDUCERS:
    _REDUCERS.append(rebuild)
