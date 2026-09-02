"""Codex CLI 0.150.1, as five surfaces that answer different halves of one session.

Design 6.7, measured in docs/experiments/E02.md over seven captured sessions. Where E02
and docs/design/00-digest.md 2.2 disagree, E02 wins; where THIS module's own reading of
the E02 fixtures disagrees with E02's matrix, the reading is named in DRIFT with the
record that shows it, and the matrix cell it corrects.

The five surfaces are not five views of one thing. Only the rollout carries
`model_context_window`, and `--ephemeral` writes no rollout, so occupancy is None for an
ephemeral session and never a default. Only the OTel logs carry the model, the effort
and a per-model-response token count together. Only the hooks carry `cwd` and the
permission mode on every tool call. Nothing carries a commit id the agent made.

The privacy shape is stricter than Claude's, because E02 measured that whatever a
captured command PRINTS travels on four of the five surfaces: `aggregated_output` on the
exec stream, `stdout`, `stderr` and `formatted_output` in the rollout, `tool_response`
on the hooks, and the `output` attribute of `codex.tool_result` with no content switch
turned on. Two of those names are not in sanitize.NEVER_PERSIST, so this module copies
no item container: it lifts named scalars and leaves the rest. E02's own sanitizer is
the argument for that. It matched patterns over command output and the owner's login
name still reached five fixture files, because `ls -la` prints the file owner.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from telltale.allowlist import ALLOWLIST, Kind
from telltale.model import Observation, now_iso, ulid
from telltale.providers import LaunchPlan, OtlpPoint, iso_from_nanos, otlp_points, prose
from telltale.sanitize import sanitize

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.providers import ParseCtx

PARSER_VERSION = 1
ADAPTER = f"codex@{PARSER_VERSION}"
SURFACES = ("exec_json", "otel_logs", "otel_metrics", "hook", "rollout")

# Codex has no OTEL_RESOURCE_ATTRIBUTES equivalent: the only launcher-settable resource
# attribute is `otel.environment`, which E02 measured arriving verbatim as `env`. The
# capture id rides behind this prefix so that a metrics batch, which carries no
# conversation id anywhere, can still be attributed. A value without the prefix (E02's
# own `telltale-e02`) is somebody else's environment name and names no capture.
CAPTURE_PREFIX = "telltale:"
ENVIRONMENT = "telltale"

# Shorthands for the matrix below.
_NONE3, _NONE4, _NONE5 = (("unavailable",) * n for n in (3, 4, 5))
_SOME2 = ("partial",) * 2

# Spec 9.1 facts against the five surfaces, one row per capability, in SURFACES order.
# Every cell is a cell of the E02 matrix except the three marked (corrected), which are
# this module's own reading of the same fixtures; DRIFT names each one and the record.
# Every `unavailable` means "not observed in E02's cohort of seven single-prompt
# sessions", which is not the same as "does not exist".
CAPABILITIES: dict[str, dict[str, str]] = {
    name: dict(zip(SURFACES, cells, strict=True))
    for name, cells in (
        # exec and rollout carry usage per TURN, and a turn is many model requests;
        # otel_logs carries it per model RESPONSE on codex.sse_event (corrected); the
        # metric is one histogram whose sum is usable and whose buckets are lossy.
        ("request_usage", ("partial", "observed", "partial", "unavailable", "partial")),
        # event_msg/task_started.model_context_window, 258400. The rollout alone, and
        # --ephemeral writes no rollout at all.
        ("context_window", (*_NONE4, "observed")),
        # Nothing compacted and PreCompact/PostCompact never fired.
        ("compaction", _NONE5),
        # hooks are partial because PreToolUse and PostToolUse do not pair (E02: 8
        # against 7 on S6). The metric counts calls and names no call id.
        ("tool_calls", ("observed", "observed", "partial", "partial", "observed")),
        # exec: item.changes[].path. rollout: the KEYS of item.changes.
        ("file_paths", ("observed", *_NONE3, "observed")),
        # otel_logs corrected: the command is inside the JSON-string attribute
        # `arguments` as `cmd`, which E02's path probe could not index.
        ("commands", ("observed", "observed", "unavailable", "observed", "observed")),
        ("subagents", _NONE5),
        # A real integer on both, not read out of prose the way Claude's is.
        ("exit_codes", ("observed", *_NONE3, "observed")),
        # Nothing names a commit the agent made. session_meta.git.commit_hash is HEAD
        # at session start, a different fact stored under its own name.
        ("commit_ids", _NONE5),
        # hooks: permission_mode on every body. The others carry sandbox_policy and
        # approval_policy, the same posture under other names, so partial.
        ("permission_mode", ("unavailable", *_SOME2, "observed", "partial")),
    )
}

# Every difference from docs/design/00-digest.md 2.2, from docs/experiments/E02.md, or
# from what a reader of either would expect, that this parser had to code around. Each
# line is a measurement over fixtures/sources/codex/0.150.1 or a probe recorded in
# docs/log/W1-T3.md, not a judgement.
DRIFT: list[str] = [
    "E02 matrix correction 1: command text IS on otel_logs. codex.tool_result carries "
    "`arguments`, a JSON STRING that holds {cmd, workdir} when tool_name is "
    "exec_command; the matrix probe indexed JSON paths and could not see inside a "
    "string. The parser lifts cmd and workdir and `arguments` stays NEVER_PERSIST, "
    "because for apply_patch the same attribute is the whole patch.",
    "E02 matrix correction 2: per-request token counts ARE on otel_logs, on "
    "codex.sse_event with event.kind response.completed: input_token_count, "
    "output_token_count, cached_token_count, cache_write_token_count, "
    "reasoning_token_count, tool_token_count. The wire names are kept, because "
    "tool_token_count was input plus output on every record and is not a tool count.",
    "E02 matrix correction 3: file change KIND is on the rollout too, as the `type` of "
    "each entry of item.changes, a dict keyed by absolute path.",
    'timeUnixNano is the string "0" on every OTel log record (211 of 211): the time '
    "is in observedTimeUnixNano and in the event.timestamp attribute where there is "
    "one, and provider_ts reads those in that order.",
    "Six OTel log records carry no event.name: DEBUG lines of the CLI's own Rust "
    "tracing, body 'flushing OTEL metrics'. They become codex.otel.log rather than "
    "failing the batch of up to 20 records they arrive in.",
    "OTLP encoding is mixed within one record: success is boolValue on api_request and "
    "the string 'true' on tool_result, output_truncated is the Python-spelled 'False', "
    "duration_ms and every *_token_count are stringValue while "
    "http.response.status_code is intValue. Every value is coerced by its allowlist "
    "Kind, and 'True'/'False' are read as well as 'true'/'false'.",
    "OTel attribute keys are dotted (event.name, event.kind, conversation.id, "
    "app.version, terminal.type, startup.phase, auth.mode) and the parser rewrites the "
    "dot. api_request spells it auth.mode and every other event auth_mode, so both "
    "arrive as auth_mode and never in one record.",
    "Every OTel log record carries user.account_id and user.email unconditionally. The "
    "parser removes both before sanitize sees them rather than leaving them unlisted: "
    "an unlisted field costs one diagnostic per request, and these are on every "
    "request of every session.",
    "service.name is codex_exec, not codex; providers.SERVICE_NAMES carries both.",
    "The exec stream spells a command as one string and the rollout as an argv LIST, "
    "and both wrap it in /bin/zsh -lc, which commands.normalize would store as "
    "`zsh -lc _`. Unwrapped here: measured, on all 10 calls that appear on both the "
    "rollout and the hooks, the script equals the hook's tool_input.command exactly.",
    "Two ways a Codex path gets past the rewriter, both measured here. The rollout "
    "spells CommandExecution.cwd as a file:// URL, which is not an absolute path: "
    "joined to the repository root it comes back as file:/Users/... with the machine "
    "path still in it, so the scheme comes off first. And Codex writes absolute paths "
    "into its own error prose (`failed to parse hooks config <home>/.codex/"
    "hooks.json`), which is Kind.SCALAR: scrubbed for secrets, never rewritten. The "
    "home directory reached the store on all seven scenarios until providers.prose() "
    "relativized each path inside the text. claude.py has the same gap on its own "
    "`error` field, and W0-T5 exempted it for the repo root.",
    "The rollout spells item types in CamelCase (CommandExecution, FileChange, "
    "Reasoning) where the exec stream uses snake case; the parser lowers the rollout "
    "spelling so one item_type means one thing on both.",
    "Codex apply_patch puts the WHOLE PATCH, new file contents included, in the hook "
    "body's tool_input.command. The parser lifts command only for other tools and "
    "stores patch_bytes instead: a normalized command is bounded prose, a patch is "
    "file content.",
    "Three fields carry command output or model text that sanitize.NEVER_PERSIST does "
    "not name: the rollout's CommandExecution.formatted_output and "
    "item.changes[].unified_diff, which no item container is copied so neither is, and "
    "event_msg/task_complete.last_agent_message, which the Stop hook spells "
    "last_assistant_message and NEVER_PERSIST does name. All three are dropped today; "
    "the file that would make that permanent is not this task's.",
    "turn.started and turn.completed carry no turn id, so exec usage cannot be joined "
    "to a rollout turn. The rollout carries turn_id on task_started, task_complete, "
    "item_completed and turn_context.",
    "model_context_window is in event_msg/task_started (258400), not in "
    "event_msg/token_count.info as the digest says. token_count.info carries its own, "
    "null on every turn E02 measured, so each is stored under its own record type and "
    "never copied to the other.",
    "PreToolUse and PostToolUse do not pair (E02: 8 against 7 on S6), so tool_calls is "
    "partial on the hook surface and a count that pairs them is wrong by one.",
    '-c \'hooks={ PreToolUse = [ { hooks = [ { type = "command", command = "true", '
    "timeout = 5 } ] } ] }' LOADS: measured with codex doctor --json (zero tokens) "
    'against a rejected hooks="nonsense", so the loader validates the shape, and '
    "`command` must be a STRING. Whether a hook registered that way FIRES under codex "
    "exec NEEDS MEASURING in the first launcher capture; until then launch() declares "
    "no hook surface and writes no file (invariant 8).",
    "otel.tool_result={ max_bytes = 0 } is accepted by the loader (re-measured here); "
    "its EFFECT on the output attribute NEEDS MEASURING on the first launcher capture. "
    "Until then the parser drops `output` by NEVER_PERSIST, which records the drop on "
    "every record.",
    "otel.log_user_prompt=false is accepted by the loader and is NOT passed: a -c "
    "override that some build rejects exits the child 1 before the session starts, and "
    "`prompt` is dropped by NEVER_PERSIST on both surfaces that carry it anyway.",
    "codex exec assigns its own thread id and takes no flag that names one, so "
    "launch() ignores session_id.",
]

# E01 finding 6, measured again on Codex: S1's first `uv run pytest` failed with
# `Failed to initialize cache` and its second printed `VIRTUAL_ENV=<...>/.venv does not
# match the project environment path`. A child inheriting these from `uv run` cannot run
# uv in its own repository, and S1 spent a turn working around it.
ENV_REMOVE = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")

# W0-E02 finding 11: the rule has to match `reasoning` (exec stream and response_item),
# `agent_reasoning` (4139 occurrences across the owner's own rollouts) and `Reasoning`
# with a capital R (the rollout's item type). A rule that matched only the first missed
# half of 884 rows.
_REASONING = re.compile(r"^(agent[_ ]?)?reasoning$", re.IGNORECASE)

# Read by the parser itself, so they are not passed on as payload fields.
_EXEC_CONSUMED = frozenset({"type", "item", "usage"})
_ROLLOUT_CONSUMED = frozenset({
    "type", "payload", "item", "info", "state", "thread_settings", "git",
    "internal_chat_message_metadata_passthrough",
})  # fmt: skip

# Codex runs every command through a login shell, so the command a reducer wants is
# the third argv element. Measured: /bin/zsh on all 14 command items of E02's cohort.
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_SHELL_FLAG = re.compile(r"^-[a-z]*c$")

# E02: the whole patch, new file contents included, arrives as tool_input.command.
_PATCH_TOOLS = frozenset({"apply_patch"})

# The fields whose value is a sentence, which is why they are Kind.SCALAR and not
# Kind.PATH. Codex writes absolute paths into its own messages: measured on all seven
# E02 scenarios, `failed to parse hooks config <home>/.codex/hooks.json`.
_PROSE = frozenset({"error", "error_message"})

_USAGE_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
               "output_tokens", "reasoning_output_tokens", "total_tokens")  # fmt: skip


@dataclass(frozen=True)
class _Common:
    """The columns every observation of one provider record shares."""

    provider_ts: str | None = None
    session_id: str | None = None
    correlations: dict[str, Any] = field(default_factory=dict)


def parse(surface: str, raw: Any, ctx: ParseCtx) -> list[Observation]:
    """One received body as observations. Raises on a body this parser cannot read."""
    if surface == "otel_logs":
        return [_otel_log(point, ctx) for point in otlp_points(surface, raw)]
    if surface == "otel_metrics":
        return [_otel_metric(point, ctx) for point in otlp_points(surface, raw)]
    if surface == "hook":
        return _hook(raw, ctx)
    if surface == "rollout":
        return _rollout(raw, ctx)
    # `stream` is the receiver's name for the route the launcher tees stdout to.
    # For Codex that stdout IS the exec JSONL, which design 6.3 calls exec_json.
    if surface in ("exec_json", "stream"):
        return _exec(raw, ctx)
    raise ValueError(f"codex has no {surface!r} surface")


def hints(surface: str, raw: Any) -> tuple[str | None, str | None]:
    """The capture id and the provider session id, read without parsing the body.

    The session id is the THREAD id on every surface that names one, which is how the
    exec stream's first line teaches the receiver's map.
    """
    if surface in ("otel_logs", "otel_metrics"):
        return _otel_hints(surface, raw)
    if not isinstance(raw, dict):
        return None, None
    if surface == "rollout":
        payload = _mapping(raw.get("payload"))
        return None, _text(payload.get("session_id") or payload.get("thread_id"))
    return None, _text(raw.get("session_id") or raw.get("thread_id"))


def _otel_hints(surface: str, raw: Any) -> tuple[str | None, str | None]:
    capture: str | None = None
    session: str | None = None
    for point in otlp_points(surface, raw):
        env = _text(point.resource.get("env")) or ""
        if not capture and env.startswith(CAPTURE_PREFIX):
            capture = env.removeprefix(CAPTURE_PREFIX)
        session = session or _text(point.attrs.get("conversation.id"))
        if capture and session:
            break
    return capture, session


def launch(
    argv: Sequence[str],
    port: int,
    level: int,  # noqa: ARG001
    session_id: str | None,  # noqa: ARG001
    capture_id: str | None = None,
) -> LaunchPlan:
    """The child's argv, environment and tee decision. Design 6.9.

    `level` changes nothing: Codex has no content switch to withhold, every content
    field is dropped at every level, and the one bound that exists (otel.tool_result)
    costs nothing to pass. `session_id` changes nothing either: `codex exec` assigns
    its own thread id and takes no flag that names one.

    `--json` is never inserted. It changes the child's stdout format, and invariant 8
    says capture never changes the child's output bytes: the tee happens only when the
    caller asked for it.
    """
    child = list(argv)
    surfaces: list[str] = []
    at = _override_index(child)
    if at is not None:
        child[at:at] = _overrides(port, capture_id)
        surfaces += ["otel_logs", "otel_metrics"]
    tee = "--json" in child
    if tee:
        surfaces.append("exec_json")
    return LaunchPlan(
        argv=child, env={}, tee=tee, env_remove=ENV_REMOVE, surfaces=tuple(surfaces)
    )


def names_file_mutation(observation: Observation) -> bool:
    """True when this observation is an agent having changed a file. Design 6.6.

    The COMPLETED phase only. `item.started` for a file_change arrives before the write,
    so a snapshot taken from it would photograph the repository as it was.
    """
    payload = observation.payload
    phase = str(payload.get("phase", "completed"))
    return payload.get("item_type") == "file_change" and phase in (
        "completed",
        "failed",
    )


# -- observations ------------------------------------------------------------------
def _observe(
    obs_type: str,
    surface: str,
    payload: Mapping[str, Any],
    ctx: ParseCtx,
    common: _Common,
) -> Observation:
    """Sanitize one payload and wrap it. The only place an Observation is built here."""
    kinds = ALLOWLIST.get(obs_type, {})
    typed = {name: _coerce(value, kinds.get(name)) for name, value in payload.items()}
    for name in _PROSE.intersection(typed):
        if isinstance(typed[name], str):
            typed[name] = prose(typed[name], ctx.paths, ctx.level)
    body, redaction, _unknown = sanitize(obs_type, typed, ctx.level, ctx.paths)
    return Observation(
        observation_id=ulid(),
        capture_id=ctx.capture_id,
        observation_type=obs_type,
        surface=surface,
        provider="codex",
        adapter=ADAPTER,
        ingest_ts=now_iso(),
        provider_ts=common.provider_ts,
        provider_session_id=common.session_id,
        environment_fingerprint_id=ctx.environment_fingerprint_id,
        repo_id=ctx.repo_id,
        correlation_ids={
            name: str(value)
            for name, value in common.correlations.items()
            if value is not None and value != ""
        },
        payload=body,
        redaction=redaction,
    )


def _coerce(value: Any, kind: Kind | None) -> Any:
    """Give a wire string the type its Kind says it has, or leave it to be dropped.

    A SIZE that stays a string is dropped by the sanitizer with `not_a_number`, which is
    the honest outcome: no number is invented here. The capitalized booleans are
    measured, not defensive: output_truncated arrives as the string "False".
    """
    if not isinstance(value, str) or kind is None:
        return value
    if kind is Kind.SIZE:
        return _number(value)
    if kind is Kind.SCALAR and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def _number(text: str) -> Any:
    for convert in (int, float):
        try:
            return convert(text)
        except ValueError:
            continue
    return text


# -- exec stream ---------------------------------------------------------------------
def _exec(raw: Any, ctx: ParseCtx) -> list[Observation]:
    """One line of `codex exec --json` stdout."""
    message = _require_object(raw, "exec line")
    kind = _text(message.get("type"))
    if kind is None:
        raise ValueError("exec line carries no type")
    if kind.startswith("item."):
        return _exec_item(kind.removeprefix("item."), message, ctx)
    session = _text(message.get("thread_id"))
    payload = {
        key: value for key, value in message.items() if key not in _EXEC_CONSUMED
    }
    usage = _mapping(message.get("usage"))
    for key in _USAGE_KEYS:
        _put(payload, key, usage.get(key))
    common = _Common(session_id=session, correlations={"session_id": session})
    name = kind.replace(".", "_")
    return [_observe(f"codex.exec.{name}", "exec_json", payload, ctx, common)]


def _exec_item(
    phase: str, message: Mapping[str, Any], ctx: ParseCtx
) -> list[Observation]:
    """An item.started or item.completed line, as one observation per changed file."""
    item = _mapping(message.get("item"))
    item_type = _text(item.get("type"))
    if item_type is None:
        raise ValueError("exec item carries no type")
    common = _Common(correlations={"item_id": item.get("id")})
    if _REASONING.match(item_type):
        ctx.notes.append("codex.exec.item:reasoning")
        return []
    if item_type == "error":
        payload = {"item_id": item.get("id"), "error": item.get("message")}
        return [_observe("codex.exec.error", "exec_json", payload, ctx, common)]
    base = _item_fields(item) | {"item_type": item_type, "phase": phase}
    listed = item.get("changes")
    changes = [entry for entry in listed if isinstance(entry, dict)] if listed else []
    if not changes:
        return [_observe("codex.exec.item", "exec_json", base, ctx, common)]
    return [
        _observe("codex.exec.item", "exec_json", base | fields, ctx, common)
        for fields in (_change(entry.get("path"), entry) for entry in changes)
    ]


def _change(path: Any, entry: Any) -> dict[str, Any]:
    """One changed file, from either spelling of item.changes: the exec stream lists
    `{path, kind}` and the rollout is a dict KEYED by the path (DRIFT).
    """
    out: dict[str, Any] = {}
    _put(out, "path", path)
    _put(out, "kind", _mapping(entry).get("kind") or _mapping(entry).get("type"))
    return out


def _item_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    """The scalars design 6.3 allows out of an item, and no others.

    Never a copy of the container: aggregated_output, formatted_output, stdout, stderr
    and unified_diff stay behind, and so does a field this version does not know about.
    That is the trade: a new Codex item field is invisible here rather than
    dropped-and-named, and the alternative is one more route for command output.
    """
    out: dict[str, Any] = {}
    for name, value in {
        "item_id": item.get("id"), "status": item.get("status"),
        "command": _command(item.get("command")), "exit_code": item.get("exit_code"),
        "duration_ms": _duration_ms(item.get("duration")),
        "cwd": _plain_path(item.get("cwd")), "source": item.get("source"),
        "server": item.get("server"), "tool": item.get("tool"),
    }.items():  # fmt: skip
        _put(out, name, value)
    return out


def _command(value: Any) -> str | None:
    """A command as one string, with Codex's own login-shell wrapper off (DRIFT).

    Left wrapped, commands.normalize keeps argv[0] and the flags and makes `_` of the
    quoted script, so every Codex command in the store would read `zsh -lc _`. Measured
    before unwrapping: on all 10 calls that appear on both the rollout and the hooks,
    the script equals the hook's bare tool_input.command, so this makes two surfaces
    agree rather than inventing a third answer.
    """
    if isinstance(value, list):
        if not all(isinstance(part, str) for part in value):
            return None
        return _unshell(value) or shlex.join(value)
    if not isinstance(value, str):
        return None
    try:
        parts = shlex.split(value)
    except ValueError:
        return value
    return _unshell(parts) or value


def _unshell(parts: Sequence[str]) -> str | None:
    """The script out of `<shell> -lc '<script>'`, or None for anything else.

    An explicit shell list, not a name test: `ssh` ends in sh and also takes a `-c`.
    """
    if len(parts) != 3 or not _SHELL_FLAG.match(parts[1]):
        return None
    return parts[2] if parts[0].rsplit("/", 1)[-1] in _SHELLS else None


def _plain_path(value: Any) -> str | None:
    """A path with any file:// scheme off (DRIFT).

    A URL is not an absolute path, so the rewriter would join it to the repository root
    and hand back `file:/Users/...` with the machine path in it.
    """
    if not isinstance(value, str):
        return None
    return value.removeprefix("file://") or None


def _duration_ms(value: Any) -> int | None:
    """`{secs, nanos}` as whole milliseconds. Integer arithmetic, so no float error."""
    duration = _mapping(value)
    secs, nanos = duration.get("secs"), duration.get("nanos")
    if not isinstance(secs, int) or not isinstance(nanos, int):
        return None
    return secs * 1000 + nanos // 1_000_000


# -- otel ---------------------------------------------------------------------------
def _otel_log(point: OtlpPoint, ctx: ParseCtx) -> Observation:
    attrs = point.attrs
    name = _event_name(point)
    session = _text(attrs.get("conversation.id"))
    payload = _otel_payload(point, ("event.name", "conversation.id"))
    payload.update(_arguments(attrs.get("arguments")))
    common = _Common(
        provider_ts=_text(attrs.get("event.timestamp")) or _otel_ts(point.record),
        session_id=session,
        correlations={"session_id": session, "call_id": attrs.get("call_id")},
    )
    return _observe(f"codex.otel.{name}", "otel_logs", payload, ctx, common)


def _otel_metric(point: OtlpPoint, ctx: ParseCtx) -> Observation:
    metric = point.metric or {}
    payload = _otel_payload(point, ())
    payload["name"] = metric.get("name")
    payload["value"] = _point_value(point.record)
    _put(payload, "unit", metric.get("unit"))
    common = _Common(provider_ts=_otel_ts(point.record))
    return _observe("codex.otel.metric", "otel_metrics", payload, ctx, common)


def _otel_payload(point: OtlpPoint, consumed: Sequence[str]) -> dict[str, Any]:
    """Attributes as payload fields: dots to underscores, ours and the identity gone.

    user.account_id and user.email are removed HERE rather than left unlisted. They
    arrive on every record of every session (E02), so leaving them to the allowlist gate
    would write one unknown_field diagnostic per OTLP request forever and bury the next
    field a Codex release adds.
    """
    identity = (*consumed, "user.account_id", "user.email")
    payload = {
        key.replace(".", "_"): value
        for key, value in point.attrs.items()
        if key not in identity
    }
    _put(payload, "service_version", point.resource.get("service.version"))
    return payload


def _event_name(point: OtlpPoint) -> str:
    """The event name with the `codex.` prefix off.

    A record with no event.name is one of the CLI's own Rust tracing lines (DRIFT), and
    it becomes codex.otel.log rather than an exception that would fail the whole batch
    of up to 20 records it arrived in.
    """
    name = _text(point.attrs.get("event.name"))
    return name.removeprefix("codex.") if name else "log"


def _otel_ts(record: Mapping[str, Any]) -> str | None:
    """timeUnixNano is the string "0" on every Codex log record (DRIFT)."""
    return iso_from_nanos(record.get("observedTimeUnixNano")) or iso_from_nanos(
        record.get("timeUnixNano")
    )


def _point_value(point: Mapping[str, Any]) -> float | int | None:
    """A data point's number: the counter's value, or a histogram's sum.

    E02: `codex.turn.token_usage` is one histogram dimensioned by token_type, and the
    usable number is the sum. The buckets are lossy and are not stored.
    """
    for key in ("asDouble", "asInt", "sum"):
        if key in point:
            value = _number(str(point[key]))
            return value if isinstance(value, float | int) else None
    return None


def _arguments(value: Any) -> dict[str, Any]:
    """`arguments` is a JSON string on codex.tool_result, and only sometimes an object.

    For tool_name exec_command it parses and holds the command. For `exec` it is the
    model's JavaScript and for apply_patch it is the patch; neither parses, so `{}`
    comes back and nothing is lifted. The attribute never reaches the sanitizer.
    """
    parsed = _json_object(value)
    out: dict[str, Any] = {}
    _put(out, "command", parsed.get("cmd"))
    _put(out, "cwd", parsed.get("workdir"))
    return out


# -- hooks --------------------------------------------------------------------------
def _hook(raw: Any, ctx: ParseCtx) -> list[Observation]:
    body = _require_object(raw, "hook body")
    event = _text(body.get("hook_event_name"))
    if event is None:
        raise ValueError("hook body carries no hook_event_name")
    payload = dict(body)
    payload.update(_hook_lifted(body))
    session = _text(body.get("session_id"))
    # provider_ts stays None: no Codex hook body carries a timestamp, exactly as E01
    # measured for Claude.
    common = _Common(
        session_id=session,
        correlations={
            "session_id": session,
            "turn_id": body.get("turn_id"),
            "tool_use_id": body.get("tool_use_id"),
        },
    )
    return [_observe(f"codex.hook.{event}", "hook", payload, ctx, common)]


def _hook_lifted(body: Mapping[str, Any]) -> dict[str, Any]:
    """The scalars allowed out of tool_input and prompt, and no others.

    The containers themselves are passed on to the sanitizer, which drops them by
    name: the drop is then recorded in the observation's redaction, so a row can show
    that a command's output arrived and went. `command` is taken out only when the tool
    is not a patch tool: for apply_patch it holds the whole patch (DRIFT), so its SIZE
    is kept instead.
    """
    out: dict[str, Any] = {}
    command = _mapping(body.get("tool_input")).get("command")
    if isinstance(command, str):
        if _text(body.get("tool_name")) in _PATCH_TOOLS:
            out["patch_bytes"] = len(command.encode("utf-8"))
        else:
            out["command"] = command
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        out["prompt_length"] = len(prompt)
    return out


# -- rollout ------------------------------------------------------------------------
def _rollout(raw: Any, ctx: ParseCtx) -> list[Observation]:
    """One line of a rollout file. Backfill: a whole session, after the fact.

    Design 6.3 calls this a wave 2 surface. It is parsed now because the same lines are
    the only place `model_context_window` exists.
    """
    record = _require_object(raw, "rollout line")
    kind = _text(record.get("type"))
    if kind is None:
        raise ValueError("rollout line carries no type")
    payload_in = _mapping(record.get("payload"))
    sub = _text(payload_in.get("type"))
    name = kind if sub is None else f"{kind}.{sub}"
    inner = _mapping(payload_in.get("internal_chat_message_metadata_passthrough"))
    session = _text(payload_in.get("session_id") or payload_in.get("thread_id"))
    common = _Common(
        provider_ts=_text(record.get("timestamp")),
        session_id=session,
        correlations={
            "session_id": session,
            "turn_id": payload_in.get("turn_id") or inner.get("turn_id"),
            "call_id": payload_in.get("call_id"),
        },
    )
    return _rollout_body(f"codex.rollout.{name}", payload_in, ctx, common)


def _rollout_body(
    obs_type: str, payload_in: Mapping[str, Any], ctx: ParseCtx, common: _Common
) -> list[Observation]:
    """The record's own fields, plus whatever the containers it holds are worth."""
    payload = {
        key: value for key, value in payload_in.items() if key not in _ROLLOUT_CONSUMED
    }
    payload.update(_rollout_nested(payload_in))
    item = _mapping(payload_in.get("item"))
    item_type = _snake(_text(item.get("type")))
    if item_type is None:
        return [_observe(obs_type, "rollout", payload, ctx, common)]
    if _REASONING.match(item_type):
        ctx.notes.append("codex.rollout.item:reasoning")
        return []
    payload |= _item_fields(item) | {"item_type": item_type}
    changes = _mapping(item.get("changes"))
    if not changes:
        return [_observe(obs_type, "rollout", payload, ctx, common)]
    return [
        _observe(obs_type, "rollout", payload | _change(path, entry), ctx, common)
        for path, entry in changes.items()
    ]


def _rollout_nested(payload_in: Mapping[str, Any]) -> dict[str, Any]:
    """The scalars allowed out of `info`, `thread_settings` and `git`.

    One function for all three because the names do not collide: a token_count record
    has no thread_settings and a session_meta record has neither.
    """
    out: dict[str, Any] = {}
    info = _mapping(payload_in.get("info"))
    for key in ("last_token_usage", "total_token_usage", "model_context_window"):
        _put(out, key, info.get(key))
    settings = _mapping(payload_in.get("thread_settings"))
    for key in ("model", "model_provider_id", "service_tier", "approval_policy"):
        _put(out, key, settings.get(key))
    git = _mapping(payload_in.get("git"))
    _put(out, "git_commit_hash", git.get("commit_hash"))
    _put(out, "git_branch", git.get("branch"))
    _put(out, "cwd", _plain_path(payload_in.get("cwd")))
    return out


def _snake(name: str | None) -> str | None:
    """CommandExecution -> command_execution. DRIFT: the two surfaces differ in case."""
    if name is None:
        return None
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# -- launch -------------------------------------------------------------------------
def _overrides(port: int, capture_id: str | None) -> list[str]:
    """The `-c` config overrides E02 measured, in the one spelling that is accepted.

    The endpoint carries the full signal path because Codex uses it VERBATIM and
    appends nothing (E02, two runs differing in one argument). `otel.environment` is
    the only launcher-settable resource attribute, so it carries the capture id.
    """
    base = f"http://127.0.0.1:{port}"
    environment = f"{CAPTURE_PREFIX}{capture_id}" if capture_id else ENVIRONMENT
    return [
        *_exporter("otel.exporter", f"{base}/v1/logs"),
        *_exporter("otel.metrics_exporter", f"{base}/v1/metrics"),
        # A ToolResultLogConfig, not a boolean: `-c otel.tool_result=false` is refused.
        # Accepted; its effect on the output attribute is unmeasured (DRIFT).
        "-c",
        "otel.tool_result={ max_bytes = 0 }",
        "-c",
        f'otel.environment="{environment}"',
    ]


def _exporter(key: str, endpoint: str) -> list[str]:
    """One exporter override, in the inline-table spelling E02's probe accepted. The
    doubled braces are f-string escapes; Codex receives
    `otel.exporter={ "otlp-http" = { endpoint = "URL", protocol = "json" } }`.
    """
    table = f'{{ "otlp-http" = {{ endpoint = "{endpoint}", protocol = "json" }} }}'
    return ["-c", f"{key}={table}"]


def _override_index(argv: Sequence[str]) -> int | None:
    """Where the `-c` overrides go, or None when this is not a `codex exec` run.

    After the subcommand chain, which is where E02 put them on all seven scenarios.
    `exec resume <thread>` takes its thread id positionally, so the id is stepped over
    too: an option in front of it would be read as the id.
    """
    if "exec" not in argv:
        return None
    at = argv.index("exec") + 1
    if at < len(argv) and argv[at] == "resume":
        at += 1
        if at < len(argv) and not argv[at].startswith("-"):
            at += 1
    return at


# -- small readers -------------------------------------------------------------------
def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _require_object(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{what} is {type(value).__name__}, not a JSON object")
    return value


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _put(target: dict[str, Any], key: str, value: Any) -> None:
    """Set a field only when there is one. An absent value stays absent, never zero."""
    if value is not None and value != "":
        target[key] = value


def _json_object(value: Any) -> Mapping[str, Any]:
    """An attribute holding JSON text, as a mapping. `null` and junk give {}."""
    if not isinstance(value, str):
        return _mapping(value)
    try:
        return _mapping(json.loads(value))
    except json.JSONDecodeError:
        return {}
