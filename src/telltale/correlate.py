"""One thing, seen on several surfaces, becoming one Activity. Design 6.10.

The reducer's inner vocabulary, split out of activities.py because that file crossed the
800-line limit and this is where it divides: activities.py decides WHICH observations
describe one thing, and this file decides what the resulting row says.

Three rules live here rather than in any one builder.

  A value carries the observation it came from. `Fields.put` records a value and its
  sources together, and `activity()` copies them into `Activity.provenance`, so every
  number `telltale explain` prints has an observation id behind it. `put` drops a None
  rather than writing it: absence is not zero (design invariant 5).

  A field read from a correlated group comes from the best-ranked surface that carries
  it, never from an average and never from the last writer. `SOURCE_ORDER` is that
  ranking, measured per observation type in E01. A type absent from it contributes
  provenance and timestamps and no values, because an unranked surface is one nobody has
  compared against the others.

  An id is a function of the capture and the observation, never of the clock. Two
  rebuilds of one capture write byte-identical rows, which is what makes `telltale
  rebuild` safe to run after every capture and what lets a golden file exist at all.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import commands
from telltale.model import Activity

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Every module whose source decides what a reduction writes. commands.py is here because
# its classifier decides which tool call becomes a verification_run; measures.py because
# an Evidence is only comparable with the activities it was computed from.
_RULE_MODULES = ("correlate.py", "activities.py", "activities_codex.py", "measures.py")


def _version() -> str:
    """The hash of the rules, not a number somebody remembers to bump."""
    digest = hashlib.sha256()
    here = Path(__file__)
    paths = [here.with_name(name) for name in _RULE_MODULES]
    paths.append(Path(commands.__file__))
    for path in paths:
        try:
            digest.update(path.read_bytes())
        except OSError:
            return "act-source-unavailable"
    return f"act-{digest.hexdigest()}"


REDUCER_VERSION = _version()

# Which observation type may supply a field value, best first, for every group the
# reducer builds. Ties inside one rank are broken by observation id, which is arrival.
SOURCE_ORDER = (
    "claude.otel.tool_result",
    "claude.hook.PostToolUseFailure",
    "claude.hook.PostToolUse",
    "claude.stream.user",
    "claude.stream.assistant",
    "claude.otel.tool_decision",
    "claude.hook.PreToolUse",
    # Compaction: the OTel event is the only one carrying success and the counts
    # together, and E01 measured that it omits post_tokens when success is false.
    "claude.otel.compaction",
    "claude.stream.system.compact_boundary",
    "claude.hook.PreCompact",
    "claude.hook.PostCompact",
    # Subagents: the OTel record describes the child, the hooks name it, the stream
    # links it to the call that spawned it.
    "claude.otel.subagent_completed",
    "claude.hook.SubagentStart",
    "claude.hook.SubagentStop",
    "claude.stream.system.task_started",
    # Codex, measured on E02 S6, where 8 tool calls are named by four surfaces. The
    # OTel tool_result is the only one that states success and the only one that times
    # the call (71 ms against the rollout's 0, which is completed_at minus started_at
    # on a record written after the fact); the rollout is the only one with an exit
    # code and a changed path; the hooks carry the permission mode and the command.
    "codex.otel.tool_result",
    "codex.rollout.event_msg.item_completed",
    "codex.hook.PostToolUse",
    "codex.hook.PreToolUse",
    "codex.otel.tool_decision",
    "codex.otel.sandbox_outcome",
    # Turns: the exec stream states the totals, the rollout times the turn and names
    # the context window, turn_context names the model and the policies.
    "codex.exec.turn_completed",
    "codex.rollout.event_msg.task_complete",
    "codex.rollout.event_msg.task_started",
    "codex.rollout.turn_context",
    "codex.rollout.event_msg.token_count",
    "codex.hook.PostCompact",
    "codex.hook.PreCompact",
)

# One capture-level observation type per role. W1-T3 adds codex's rows; the roles are
# provider-neutral and the spellings are not.
ROLES: dict[str, str] = {
    "claude.otel.api_request": "request",
    "claude.stream.assistant": "assistant",
    "claude.otel.compaction": "compaction",
    "claude.hook.PreCompact": "pre_compact",
    "claude.hook.PostCompact": "post_compact",
    "claude.stream.system.compact_boundary": "compact_boundary",
    "claude.hook.SubagentStart": "subagent_start",
    "claude.hook.SubagentStop": "subagent_stop",
    "claude.otel.subagent_completed": "subagent_done",
    "claude.stream.system.task_started": "task_started",
    "claude.stream.system.init": "session_start",
    "claude.hook.SessionEnd": "session_end",
    "claude.stream.result": "session_result",
    "claude.hook.PostModelSwitch": "model_switch",
    "telltale.capture_started": "capture_started",
    "telltale.capture_ended": "capture_ended",
    "telltale.repo.snapshot": "repo_snapshot",
    "telltale.repo.commit": "repo_commit",
    "external.correlation": "correlation",
    "external.outcome": "outcome",
    # Codex CLI 0.150.1 (W1-T3). Three surfaces announce the session and each carries
    # something the others do not: the OTel record has the clock, the model and the
    # policies, the hook has the permission mode and the cwd, and the exec line has the
    # thread id. One lifecycle row each, which is what Claude already does for its two
    # session_end records.
    "codex.otel.conversation_starts": "session_start",
    "codex.hook.SessionStart": "session_start",
    "codex.exec.thread_started": "session_start",
    "codex.hook.SessionEnd": "session_end",
    "codex.hook.PreCompact": "pre_compact",
    "codex.hook.PostCompact": "post_compact",
    # The backfill surface (W2-T2). An imported assistant line is the same role a
    # stream assistant message has, so `_requests` groups it by request_id and reads
    # its usage; a compact_boundary that matches no OTel record becomes its own
    # episode, which on a transcript capture is every one of them.
    "claude.transcript.assistant": "assistant",
    "claude.transcript.system.compact_boundary": "compact_boundary",
}


@dataclass(frozen=True)
class Obs:
    """One stored observation, in the shape the reducer reads it."""

    id: str
    type: str
    surface: str
    provider: str
    ts: str | None
    ingest: str
    session: str | None
    # Design 6.2 puts these two on every observation ROW rather than in its payload,
    # so a reducer that read only the payload would report a capture with no repository
    # and no environment while both are on every row of it.
    repo: str | None
    environment: str | None
    corr: dict[str, str]
    payload: dict[str, Any]

    @property
    def role(self) -> str | None:
        return ROLES.get(self.type)


@dataclass
class Fields:
    """Field values and the observation each came from, built one `put` at a time."""

    fields: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, list[str]] = field(default_factory=dict)

    def put(self, name: str, value: Any, *sources: str | None) -> None:
        """Record a value and its sources. An absent value stays absent, never zero."""
        if value is None:
            return
        self.fields[name] = value
        found = [source for source in sources if source]
        if found:
            self.provenance[name] = found

    def take(self, obs: Sequence[Obs], name: str, key: str | None = None) -> Any:
        """Read one field from the best-ranked observation that carries it."""
        value, source = pick(obs, key or name)
        self.put(name, value, source)
        return value


def read(row: Mapping[str, Any]) -> Obs:
    return Obs(
        id=str(row["observation_id"]),
        type=str(row["observation_type"]),
        surface=str(row["surface"]),
        provider=str(row["provider"]),
        ts=row["provider_ts"],
        ingest=str(row["ingest_ts"]),
        session=row["provider_session_id"],
        repo=row["repo_id"],
        environment=row["environment_fingerprint_id"],
        corr=dict(row["correlation_ids"]),
        payload=dict(row["payload"]),
    )


def as_activity(row: Mapping[str, Any]) -> Activity:
    return Activity(
        activity_id=str(row["activity_id"]),
        capture_id=str(row["capture_id"]),
        activity_type=str(row["activity_type"]),
        actor=str(row["actor"]),
        started_at=str(row["started_at"]),
        ended_at=row["ended_at"],
        fields=dict(row["fields"]),
        provenance=dict(row["provenance"]),
        reducer_version=str(row["reducer_version"]),
    )


def of_type(activities: Sequence[Activity], kinds: Sequence[str]) -> list[Activity]:
    return [item for item in activities if item.activity_type in kinds]


def pick(group: Sequence[Obs], key: str) -> tuple[Any, str | None]:
    """One field from the best-ranked observation that carries it, with its id."""
    for item in sorted(group, key=lambda obs: (_rank(obs.type), obs.id)):
        if key in item.payload:
            return item.payload[key], item.id
    return None, None


def _rank(name: str) -> int:
    return SOURCE_ORDER.index(name) if name in SOURCE_ORDER else len(SOURCE_ORDER)


def first(group: Sequence[Obs], key: str) -> tuple[Any, str | None]:
    """One correlation id from the earliest observation that carries it, with its id."""
    for item in group:
        if key in item.corr:
            return item.corr[key], item.id
    return None, None


def ids(observed: Sequence[Obs], role: str) -> list[str]:
    return [item.id for item in observed if item.role == role]


def started(group: Sequence[Obs]) -> str | None:
    """The earliest PROVIDER timestamp in the group, or None when none has one.

    Provider time and arrival time are never mixed inside one activity: a group where no
    observation carries a clock has no provider start, and `activity()` records which of
    the two the row ended up on.
    """
    stamps = [item.ts for item in group if item.ts]
    return min(stamps) if stamps else None


def ended(group: Sequence[Obs]) -> str | None:
    stamps = [item.ts for item in group if item.ts]
    return max(stamps) if stamps else None


def hashed_id(prefix: str, capture_id: str, kind: str, key: str) -> str:
    digest = hashlib.sha256(f"{capture_id}|{kind}|{key}".encode()).hexdigest()
    return f"{prefix}_{digest[:26]}"


def activity(
    capture_id: str,
    kind: str,
    primary: str,
    actor: str,
    started_at: str | None,
    arrival: str,
    ended_at: str | None,
    built: Fields,
    extra: Mapping[str, Any] | None = None,
    key: str | None = None,
) -> Activity:
    """One activity, with the id and the tie-break key that make a rebuild repeatable.

    `primary_observation` is the observation that opened the activity. It is the tie
    break the timeline sorts on, because two activities can share a start (parallel tool
    calls do) and arrival order is the only order this system has measured. The id is a
    hash of the capture, the type and that observation, so a rebuild produces the same
    row and `ulid()` never enters a derived table.

    `key` is what makes the id unique when two activities of one TYPE open on one
    observation. That happens: the capture's own lifecycle row opens on the capture's
    first observation, and on Codex S3 that observation is
    codex.otel.conversation_starts, which is also a session_start with its own row.
    Measured before this argument existed: `UNIQUE constraint failed:
    activities.activity_id`. It defaults to the primary observation, so every other
    row's id is unchanged.

    `clock` says which clock `started_at` is on. E01 measured that hook bodies, stream
    system messages and the stream result all arrive with no timestamp of any kind, so
    for those the only clock is arrival at the receiver. That is a fact about the
    recorder rather than about the session, and the column names it so that a reader,
    and the timeline, can tell the two apart instead of reading one as the other.
    """
    fields = {
        **(extra or {}),
        **built.fields,
        "clock": "provider" if started_at else "arrival",
        "primary_observation": primary,
    }
    return Activity(
        activity_id=hashed_id("act", capture_id, kind, key or primary),
        capture_id=capture_id,
        activity_type=kind,
        actor=actor,
        started_at=started_at or arrival,
        ended_at=ended_at,
        fields=fields,
        provenance={**built.provenance, "primary_observation": [primary]},
        reducer_version=REDUCER_VERSION,
    )
