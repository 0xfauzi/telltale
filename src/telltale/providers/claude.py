"""Claude Code 2.1.257, as four surfaces that say different things about one session.

Design 6.7, measured in docs/experiments/E01.md over eight captured sessions. Where E01
and docs/design/00-digest.md 2.1 disagree, E01 wins and DRIFT below names it.

The four surfaces are not four views of one thing. Only the stream carries the context
window and the subagent's parent link, only the OTel logs carry tool input sizes, cost
and compaction counts together, only the hooks carry the permission mode on every call,
and nothing carries a bash exit code, which is why the one on the stream is parsed out
of the failure text and labelled as parsed.

The privacy shape follows from the same experiment: the OTel surfaces carried no file
content at level 1, while the hooks carried a secret file in `tool_response.stdout` and
the stream carried it in a tool_result block, in `tool_use_result` and in the
assistant's own text. So this module lifts named scalars out of those containers and
never copies one. `tool_input`, `tool_parameters`, `tool_response` and `tool_result` are
in sanitize.NEVER_PERSIST, and handing one on would be a bug the third gate happens to
catch.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from telltale.allowlist import ALLOWLIST, Kind
from telltale.model import Observation, now_iso, ulid
from telltale.providers import LaunchPlan, iso_from_nanos, otlp_attrs
from telltale.sanitize import sanitize

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from telltale.providers import ParseCtx

PARSER_VERSION = 1
ADAPTER = f"claude@{PARSER_VERSION}"
SURFACES = ("otel_logs", "otel_metrics", "hook", "stream")

# Our own resource attribute, set by launch() through OTEL_RESOURCE_ATTRIBUTES. It is
# how an OTLP request says which capture it belongs to before anything is parsed.
CAPTURE_ATTR = "telltale.capture_id"

# Spec 9.1 facts against the four surfaces, one row per capability, in SURFACES order.
# Every cell is a cell of the E01 matrix, and a `partial` carries its note with it: tool
# success on the hooks surface is partial because the EVENT NAME differed, not because
# a success field said so. The notes are the matrix rows in docs/experiments/E01.md.
CAPABILITIES: dict[str, dict[str, str]] = {
    name: dict(zip(SURFACES, cells, strict=True))
    for name, cells in (
        # otel api_request has the four counters; the token.usage counter carries no
        # request id, so it cannot be split per request; no hook payload has tokens.
        ("request_usage", ("observed", "partial", "unavailable", "observed")),
        # result.modelUsage.<model>.contextWindow, on the result message alone.
        ("context_window", ("unavailable", "unavailable", "unavailable", "observed")),
        # claude_code.compaction, which the digest denies, carries the token counts.
        # PreCompact and PostCompact carry the trigger and no counts.
        ("compaction", ("observed", "unavailable", "partial", "observed")),
        # code_edit_tool.decision is edit tools only, and only as a decision counter.
        ("tool_calls", ("observed", "partial", "observed", "observed")),
        ("file_paths", ("observed", "unavailable", "observed", "observed")),
        # otel: tool_parameters.full_command, beside bash_command (the first word).
        ("commands", ("observed", "unavailable", "observed", "observed")),
        # subagent_completed describes the child and names no agent id and no parent;
        # SubagentStart.agent_id links start to stop but not to the spawning call;
        # only the stream has parent_tool_use_id with task_started.tool_use_id.
        ("subagents", ("partial", "unavailable", "partial", "observed")),
        # E01 marks the stream cell partial: the failure text begins "Exit code N" and
        # nothing structured carries it. What this parser makes of that is derived,
        # which is design 6.3's word and the weaker claim of the two.
        ("exit_codes", ("unavailable", "unavailable", "unavailable", "derived")),
        # commit.count counts commits and never names one.
        ("commit_ids", ("observed", "partial", "observed", "observed")),
        ("permission_mode", ("unavailable", "unavailable", "observed", "observed")),
    )
}

# Every difference from docs/design/00-digest.md 2.1 this parser had to code around.
# Each line is a measurement in docs/experiments/E01.md, not a judgement, and
# docs/log/W0-T4.md carries the long form.
DRIFT: list[str] = [
    "claude_code.compaction exists, against the digest's 'no compaction OTel event': "
    "trigger, pre_tokens, post_tokens, duration_ms, success, error. post_tokens is "
    "absent when success is false, so a reducer reads success first.",
    "Six OTel log events the digest omits: compaction, hook_execution_start, "
    "hook_execution_complete, hook_registered, plugin_loaded, subagent_completed.",
    "Four documented OTel events never arrived in 8 sessions: api_error, api_refusal, "
    "auth, permission_mode_changed. Their allowlist entries stay, unexercised.",
    "code_edit_tool.decision is a metric the digest omits; pull_request.count is "
    "documented and was never seen.",
    "user_prompt.prompt and assistant_response.response arrive with the literal value "
    "<REDACTED>: dropped, and only the lengths kept.",
    "OTLP number encoding is mixed. intValue is a JSON number, timeUnixNano a decimal "
    "string, and duration_ms, pre_tokens, num_hooks, prompt_length and both size "
    "attributes are stringValue. Every value is coerced by its allowlist Kind.",
    "OTel attribute keys are dotted (event.name, session.id, prompt.id, message.uuid, "
    "agent.name, terminal.type, plugin.name); the parser rewrites the dot.",
    "tool_input and tool_parameters are JSON strings inside an attribute, not objects, "
    "and tool_input carries whole Edit contents.",
    "query_source is sdk in headless mode, not repl_main_thread, and compact while "
    "compacting.",
    "Hook bodies carry no timestamp, so provider_ts is None on every hook observation.",
    "Twelve stream types the digest omits: rate_limit_event and the system subtypes "
    "hook_started, hook_response, hook_progress, notification, status, task_started, "
    "task_progress, task_notification, task_updated, thinking_tokens, "
    "vcs_state_changed. System messages are claude.stream.system.<subtype>, renaming "
    "W0-T2's claude.stream.init and claude.stream.compact_boundary.",
    "A result can carry subtype success while is_error is true (E01 S7): is_error is "
    "the field that says whether the session failed.",
    "The result message holds the final assistant text in `result` and "
    "system:task_notification a subagent's answer in `summary`. Neither is in "
    "NEVER_PERSIST, so the allowlist gate drops them and neither is ever added to it.",
    "The SessionStart http hook registers and never fires, so the plan declares it and "
    "the launcher may not wait for it.",
    "service.version is a resource attribute on every record without "
    "OTEL_METRICS_INCLUDE_VERSION; kept as service_version.",
    "OTel tool_result_size_bytes and the stream block it names are different numbers "
    "(910 against 568 on one S1 Bash call), so the stream number is stored as "
    "tool_result_content_bytes.",
]

# Hook events the launch plan declares. The nine E01 saw fire, plus SessionStart (which
# registers and never runs on 2.1.257), UserPromptSubmit and PostModelSwitch (neither
# was declared in E01, so neither has been measured).
HOOK_EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "PreCompact", "PostCompact", "SubagentStart", "SubagentStop",
    "Stop", "PostModelSwitch",
)  # fmt: skip

# Removed from the child's environment rather than set empty. The first three are the
# content switches Telltale never turns on, so a value exported in the parent shell
# cannot reach the child. The last two are E01 finding 6: a child inheriting VIRTUAL_ENV
# from `uv run` cannot run `uv run pytest` in its own repository, and S1 spent a turn on
# `rm -rf .venv && uv sync` because of it.
ENV_REMOVE = (
    "OTEL_LOG_USER_PROMPTS",
    "OTEL_LOG_ASSISTANT_RESPONSES",
    "OTEL_LOG_TOOL_CONTENT",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)

# Stream keys the parser consumes itself. Everything else on a stream message is passed
# to the sanitizer, so a field this version does not know about is dropped AND named.
_STREAM_CONSUMED = frozenset({
    "type", "subtype", "uuid", "session_id", "timestamp", "parent_tool_use_id",
    "request_id", "message", "usage", "compact_metadata", "patch", "description",
})  # fmt: skip

_STREAM_RENAME = {
    "permissionMode": "permission_mode",
    "apiKeySource": "api_key_source",
    "modelUsage": "model_usage",
}

# E01: on failure a Bash tool_result block begins with this line and nothing structured
# carries the code. Anchored at the start, and only read when is_error is true, so a
# command whose OUTPUT happens to start with these words is not mistaken for one.
_EXIT_CODE = re.compile(r"Exit code (\d+)\b")

_MUTATING_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


@dataclass(frozen=True)
class _Common:
    """The columns every observation of one provider record shares."""

    provider_ts: str | None = None
    session_id: str | None = None
    correlations: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Point:
    """One OTLP unit of work: a log record, or one data point of one metric."""

    resource: dict[str, Any]
    attrs: dict[str, Any]
    record: Mapping[str, Any]
    metric: Mapping[str, Any] | None = None


def parse(surface: str, raw: Any, ctx: ParseCtx) -> list[Observation]:
    """One received body as observations. Raises on a body this parser cannot read."""
    if surface == "otel_logs":
        return [_otel_log(point, ctx) for point in _otel_walk(surface, raw)]
    if surface == "otel_metrics":
        return [_otel_metric(point, ctx) for point in _otel_walk(surface, raw)]
    if surface == "hook":
        return _hook(raw, ctx)
    if surface == "stream":
        return _stream(raw, ctx)
    raise ValueError(f"claude has no {surface!r} surface")


def hints(surface: str, raw: Any) -> tuple[str | None, str | None]:
    """The capture id and the provider session id, read without parsing the body.

    Attribution has to happen first: the receiver needs to know which capture a record
    belongs to before it can pick the sanitizer context that rewrites its paths.
    """
    if surface in ("otel_logs", "otel_metrics"):
        capture: str | None = None
        session: str | None = None
        for point in _otel_walk(surface, raw):
            capture = capture or _text(point.resource.get(CAPTURE_ATTR))
            capture = capture or _text(point.attrs.get(CAPTURE_ATTR))
            session = session or _text(point.attrs.get("session.id"))
            if capture and session:
                break
        return capture, session
    if isinstance(raw, dict):
        return None, _text(raw.get("session_id"))
    return None, None


def launch(
    argv: Sequence[str],
    port: int,
    level: int,
    session_id: str | None,
    capture_id: str | None = None,
) -> LaunchPlan:
    """The child's argv, environment and tee decision. Design 6.9.

    `capture_id` is not in the design's signature and the environment variable it names
    is: without it the OTel records carry no telltale.capture_id and the receiver has to
    fall back to the session-id map, which only works once the session id is known.
    """
    child = list(argv)
    surfaces = ["otel_logs", "otel_metrics"]
    settings = _settings(child)
    if settings is not None:
        _apply_settings(child, settings, port)
        surfaces.append("hook")
    if _has(child, "-p", "--print") and not _has(child, "--session-id", "--resume"):
        # Inserted right after the executable, before any positional prompt:
        # options before positionals are valid for every argv shape, and whether
        # Claude Code accepts options AFTER the prompt has not been measured.
        child[1:1] = ["--session-id", session_id or str(uuid.uuid4())]
    tee = _has(child, "--output-format") and "stream-json" in child
    if tee:
        surfaces.append("stream")
    return LaunchPlan(
        argv=child,
        env=_env(port, level, capture_id),
        tee=tee,
        env_remove=ENV_REMOVE,
        surfaces=tuple(surfaces),
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
    body, redaction, _unknown = sanitize(obs_type, typed, ctx.level, ctx.paths)
    return Observation(
        observation_id=ulid(),
        capture_id=ctx.capture_id,
        observation_type=obs_type,
        surface=surface,
        provider="claude",
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

    The provider sends numbers and booleans as strings on some attributes and as JSON
    numbers on others (DRIFT). A SIZE that stays a string is dropped by the sanitizer
    with `not_a_number`, which is the honest outcome: no number is invented here.
    """
    if not isinstance(value, str) or kind is None:
        return value
    if kind is Kind.SIZE:
        return _number(value)
    if kind is Kind.SCALAR and value in ("true", "false"):
        return value == "true"
    return value


def _number(text: str) -> Any:
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


# -- otel ---------------------------------------------------------------------------
def _otel_walk(surface: str, raw: Any) -> Iterator[_Point]:
    """Every log record, or every data point of every metric, with its resource."""
    if surface == "otel_logs":
        yield from _log_points(raw)
    else:
        yield from _metric_walk(raw)


def _log_points(raw: Any) -> Iterator[_Point]:
    for block in _items(raw, "resourceLogs"):
        resource = _resource(block)
        for scope in _items(block, "scopeLogs"):
            for record in _items(scope, "logRecords"):
                yield _Point(resource, otlp_attrs(record.get("attributes")), record)


def _metric_walk(raw: Any) -> Iterator[_Point]:
    for block in _items(raw, "resourceMetrics"):
        resource = _resource(block)
        for scope in _items(block, "scopeMetrics"):
            for metric in _items(scope, "metrics"):
                yield from _points_of(metric, resource)


def _points_of(metric: Mapping[str, Any], resource: dict[str, Any]) -> Iterator[_Point]:
    """Every data point of one metric, whatever aggregation it arrived under."""
    for aggregation in ("sum", "gauge", "histogram", "exponentialHistogram", "summary"):
        for point in _items(metric.get(aggregation), "dataPoints"):
            yield _Point(resource, otlp_attrs(point.get("attributes")), point, metric)


def _resource(block: Mapping[str, Any]) -> dict[str, Any]:
    return otlp_attrs(_mapping(block.get("resource")).get("attributes"))


def _otel_log(point: _Point, ctx: ParseCtx) -> Observation:
    attrs = point.attrs
    name = _event_name(point)
    session = _text(attrs.get("session.id"))
    payload = _otel_payload(point, ("session.id", "prompt.id", "message.uuid"))
    payload.update(
        _lifted(
            _json_object(attrs.get("tool_input")),
            _json_object(attrs.get("tool_parameters")),
        )
    )
    common = _Common(
        provider_ts=_text(attrs.get("event.timestamp"))
        or iso_from_nanos(point.record.get("timeUnixNano")),
        session_id=session,
        correlations={
            "session_id": session,
            "prompt_id": attrs.get("prompt.id"),
            "message_uuid": attrs.get("message.uuid"),
            "request_id": attrs.get("request_id"),
            "tool_use_id": attrs.get("tool_use_id"),
        },
    )
    return _observe(f"claude.otel.{name}", "otel_logs", payload, ctx, common)


def _otel_metric(point: _Point, ctx: ParseCtx) -> Observation:
    metric = point.metric or {}
    session = _text(point.attrs.get("session.id"))
    payload = _otel_payload(point, ("session.id",))
    payload["name"] = metric.get("name")
    payload["value"] = _point_value(point.record)
    _put(payload, "unit", metric.get("unit"))
    common = _Common(
        provider_ts=iso_from_nanos(point.record.get("timeUnixNano")),
        session_id=session,
        correlations={"session_id": session},
    )
    return _observe("claude.otel.metric", "otel_metrics", payload, ctx, common)


def _otel_payload(point: _Point, consumed: Sequence[str]) -> dict[str, Any]:
    """Attributes as payload fields: dots to underscores, ours and the ids removed.

    The identity attributes (user.id, user.email, organization.id) are NOT removed here.
    They are unlisted, so the sanitizer drops them AND names them, which is what makes a
    provider that starts sending something new visible.
    """
    payload = {
        key.replace(".", "_"): value
        for key, value in point.attrs.items()
        if key not in consumed and not key.startswith("telltale.")
    }
    _put(payload, "service_version", point.resource.get("service.version"))
    return payload


def _event_name(point: _Point) -> str:
    """The event name with the `claude_code.` prefix off, from the attribute or body."""
    name = _text(point.attrs.get("event.name"))
    if name is None:
        body = _mapping(point.record.get("body")).get("stringValue")
        name = _text(body)
    if name is None:
        raise ValueError("otel log record carries no event.name and no body")
    return name.removeprefix("claude_code.")


def _point_value(point: Mapping[str, Any]) -> float | int | None:
    """A data point's number, or None for an aggregation with no single value."""
    for key in ("asDouble", "asInt"):
        if key in point:
            value = _number(str(point[key]))
            return value if isinstance(value, float | int) else None
    return None


# -- hooks --------------------------------------------------------------------------
def _hook(raw: Any, ctx: ParseCtx) -> list[Observation]:
    body = _require_object(raw, "hook body")
    event = _text(body.get("hook_event_name"))
    if event is None:
        raise ValueError("hook body carries no hook_event_name")
    payload = {key: value for key, value in body.items() if key != "effort"}
    _put(payload, "effort_level", _mapping(body.get("effort")).get("level"))
    payload.update(_lifted(_mapping(body.get("tool_input")), {}))
    payload.update(_git_fields(_mapping(body.get("tool_response")).get("gitOperation")))
    session = _text(body.get("session_id"))
    # provider_ts stays None: no hook body carries a timestamp of any kind (DRIFT).
    common = _Common(
        session_id=session,
        correlations={
            "session_id": session,
            "prompt_id": body.get("prompt_id"),
            "tool_use_id": body.get("tool_use_id"),
            "agent_id": body.get("agent_id"),
        },
    )
    return [_observe(f"claude.hook.{event}", "hook", payload, ctx, common)]


# -- stream -------------------------------------------------------------------------
def _stream(raw: Any, ctx: ParseCtx) -> list[Observation]:
    message = _require_object(raw, "stream line")
    kind = _text(message.get("type"))
    if kind is None:
        raise ValueError("stream line carries no type")
    if kind == "assistant":
        return _stream_assistant(message, ctx)
    if kind == "user":
        return _stream_user(message, ctx)
    obs_type = _stream_type(message, kind)
    payload = _stream_payload(message)
    return [_observe(obs_type, "stream", payload, ctx, _stream_common(message))]


def _stream_type(message: Mapping[str, Any], kind: str) -> str:
    if kind != "system":
        return f"claude.stream.{kind}"
    subtype = _text(message.get("subtype"))
    if subtype is None:
        raise ValueError("stream system message carries no subtype")
    return f"claude.stream.system.{subtype}"


def _stream_common(message: Mapping[str, Any]) -> _Common:
    session = _text(message.get("session_id"))
    return _Common(
        provider_ts=_text(message.get("timestamp")),
        session_id=session,
        correlations={
            "session_id": session,
            "message_uuid": message.get("uuid"),
            "parent_tool_use_id": message.get("parent_tool_use_id"),
            "request_id": message.get("request_id"),
            "tool_use_id": message.get("tool_use_id"),
        },
    )


def _stream_payload(message: Mapping[str, Any]) -> dict[str, Any]:
    """Top-level fields, with the containers this parser reads itself flattened.

    Everything not consumed is passed on, so `result`, `summary` and `text` reach the
    sanitizer and are dropped there by name. That is deliberate: the drop is recorded in
    the observation's redaction, so a row can show that model text arrived and went.
    """
    payload = {
        _STREAM_RENAME.get(key, key): value
        for key, value in message.items()
        if key not in _STREAM_CONSUMED
    }
    _put(payload, "message_uuid", message.get("uuid"))
    payload.update(_stream_flattened(message))
    return payload


def _stream_flattened(message: Mapping[str, Any]) -> dict[str, Any]:
    """compact_metadata, usage, patch and description, as scalars.

    One function for all four because the key names do not collide: a compaction
    message has no usage and a task message has no compact_metadata.
    """
    out: dict[str, Any] = {}
    meta = _mapping(message.get("compact_metadata"))
    usage = _mapping(message.get("usage"))
    for key in ("trigger", "pre_tokens", "post_tokens", "cumulative_dropped_tokens",
                "duration_ms"):  # fmt: skip
        _put(out, key, meta.get(key))
    for key in (*_USAGE_KEYS, "total_tokens", "tool_uses", "duration_ms"):
        _put(out, key, usage.get(key))
    _put(out, "status", _mapping(message.get("patch")).get("status"))
    description = message.get("description")
    if isinstance(description, str):
        out["description_length"] = len(description)
    return out


_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
               "cache_creation_input_tokens")  # fmt: skip


def _stream_assistant(message: Mapping[str, Any], ctx: ParseCtx) -> list[Observation]:
    """One observation for the message, and one for each tool call it asks for."""
    inner = _mapping(message.get("message"))
    common = _stream_common(message)
    payload = _stream_payload(message)
    _put(payload, "model", inner.get("model"))
    _put(payload, "stop_reason", inner.get("stop_reason"))
    usage = _mapping(inner.get("usage"))
    for key in _USAGE_KEYS:
        _put(payload, key, usage.get(key))
    calls = _blocks(inner, "tool_use")
    if calls:
        payload["tool_names"] = [call.get("name") for call in calls]
        payload["tool_use_ids"] = [call.get("id") for call in calls]
    out = [_observe("claude.stream.assistant", "stream", payload, ctx, common)]
    out += [_stream_tool_use(call, common, ctx) for call in calls]
    return out


def _stream_tool_use(
    call: Mapping[str, Any], common: _Common, ctx: ParseCtx
) -> Observation:
    """A tool_use block. `input` holds whole file contents, so four scalars come out."""
    payload: dict[str, Any] = {}
    _put(payload, "tool_name", call.get("name"))
    _put(payload, "tool_use_id", call.get("id"))
    payload.update(_lifted(_mapping(call.get("input")), {}))
    return _observe(
        "claude.stream.assistant",
        "stream",
        payload,
        ctx,
        _with_tool(common, call["id"]),
    )


def _with_tool(common: _Common, tool_use_id: Any) -> _Common:
    """The same record, correlated to one tool call rather than to the message."""
    correlations = {**common.correlations, "tool_use_id": tool_use_id}
    return _Common(common.provider_ts, common.session_id, correlations)


def _stream_user(message: Mapping[str, Any], ctx: ParseCtx) -> list[Observation]:
    """One observation per tool_result block, and one for the prompt text if any."""
    inner = _mapping(message.get("message"))
    common = _stream_common(message)
    results = _blocks(inner, "tool_result")
    # The commit id sits on tool_use_result, which names no tool_use_id. With one result
    # block the join is certain; with two it is a guess, and a guess here would attach a
    # commit to the wrong call.
    git = _git_fields(_mapping(message.get("tool_use_result")).get("gitOperation"))
    out = [
        _stream_tool_result(block, common, ctx, git if len(results) == 1 else {})
        for block in results
    ]
    length = _text_length(inner.get("content"))
    if length is not None:
        payload = _stream_payload(message)
        payload["prompt_length"] = length
        out.append(_observe("claude.stream.user", "stream", payload, ctx, common))
    return out


def _stream_tool_result(
    block: Mapping[str, Any], common: _Common, ctx: ParseCtx, git: Mapping[str, Any]
) -> Observation:
    payload: dict[str, Any] = dict(git)
    _put(payload, "tool_use_id", block.get("tool_use_id"))
    _put(payload, "is_error", block.get("is_error"))
    _put(payload, "message_uuid", common.correlations.get("message_uuid"))
    content = block.get("content")
    if isinstance(content, str):
        payload["tool_result_content_bytes"] = len(content.encode("utf-8"))
        code = _exit_code(content) if block.get("is_error") is True else None
        if code is not None:
            payload["exit_code"] = code
            payload["exit_code_source"] = "stream_text"
    tool_use_id = block.get("tool_use_id")
    return _observe(
        "claude.stream.user", "stream", payload, ctx, _with_tool(common, tool_use_id)
    )


def _exit_code(text: str) -> int | None:
    match = _EXIT_CODE.match(text)
    return int(match.group(1)) if match else None


def _text_length(content: Any) -> int | None:
    """How long the prompt in a user message is, or None when it carries none."""
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        return None
    lengths = [
        len(block["text"])
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return sum(lengths) if lengths else None


def _blocks(message: Mapping[str, Any], kind: str) -> list[Mapping[str, Any]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == kind
    ]


# -- lifting out of the containers that never persist --------------------------------
def _lifted(
    tool_input: Mapping[str, Any], parameters: Mapping[str, Any]
) -> dict[str, Any]:
    """The scalars design 6.3 allows out of a tool call's arguments, and no others.

    Everything else in these two containers stays behind: the Edit strings, the Write
    contents, the subagent prompt. `description` becomes a length, because the text is
    the model's and the length is the fact a reducer wants.
    """
    out: dict[str, Any] = {}
    agent = tool_input.get("subagent_type") or parameters.get("subagent_type")
    _put(out, "command", tool_input.get("command") or parameters.get("full_command"))
    _put(out, "file_path", tool_input.get("file_path"))
    _put(out, "git_commit_id", parameters.get("git_commit_id"))
    _put(out, "subagent_type", agent)
    description = tool_input.get("description")
    if isinstance(description, str):
        out["description_length"] = len(description)
    return out


def _git_fields(operation: Any) -> dict[str, Any]:
    """`gitOperation.commit` as three scalars. Provider-reported, per design 6.8."""
    commit = _mapping(_mapping(operation).get("commit"))
    out: dict[str, Any] = {}
    _put(out, "git_commit_id", commit.get("sha"))
    _put(out, "git_commit_kind", commit.get("kind"))
    _put(out, "git_branch", commit.get("branch"))
    return out


def names_file_mutation(observation: Observation) -> bool:
    """True when this observation is an agent changing a file. Design 6.6."""
    return str(observation.payload.get("tool_name", "")) in _MUTATING_TOOLS


# -- launch -------------------------------------------------------------------------
def _env(port: int, level: int, capture_id: str | None) -> dict[str, str]:
    env = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        # No default exists for the protocol: without this the exporter has none.
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{port}",
        # Design 6.9. The documented defaults are 5000 and 60000 ms, and E01 measured
        # every session with these two values.
        "OTEL_LOGS_EXPORT_INTERVAL": "1000",
        "OTEL_METRIC_EXPORT_INTERVAL": "5000",
    }
    if level >= 1:
        # tool_input, tool_parameters, file paths, the commit id. E01 measured that this
        # switch carries no file CONTENT on either OTel surface.
        env["OTEL_LOG_TOOL_DETAILS"] = "1"
    if capture_id:
        env["OTEL_RESOURCE_ATTRIBUTES"] = f"{CAPTURE_ATTR}={capture_id}"
    return env


def _settings(argv: list[str]) -> dict[str, Any] | None:
    """The settings object to add http hooks to, or None when we must not touch it.

    A `--settings` that names a FILE cannot be merged without reading and rewriting the
    child's configuration, and a second `--settings` would replace it. Design rule 8
    says capture never changes the child's behaviour, so the hook surface is given up
    instead, and the plan says so by leaving `hook` out of its surfaces.
    """
    if "--settings" not in argv:
        return {}
    value = argv[argv.index("--settings") + 1] if _has_value(argv, "--settings") else ""
    text = value.strip()
    if not text.startswith("{"):
        return None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _apply_settings(argv: list[str], settings: dict[str, Any], port: int) -> None:
    """Merge one http hook per event into `settings`, in place, then into argv."""
    url = f"http://127.0.0.1:{port}/hooks/claude"
    # Seconds, not milliseconds: 5000 here would be a 5000-second hook timeout.
    hook = {"type": "http", "url": url, "timeout": 5}
    hooks = dict(settings.get("hooks") or {})
    for event in HOOK_EVENTS:
        entries = list(hooks.get(event) or [])
        entries.append({"hooks": [hook]})
        hooks[event] = entries
    settings["hooks"] = hooks
    text = json.dumps(settings, separators=(",", ":"))
    if "--settings" in argv and _has_value(argv, "--settings"):
        argv[argv.index("--settings") + 1] = text
    else:
        argv[1:1] = ["--settings", text]  # before any positional, see launch()


def _has(argv: Sequence[str], *flags: str) -> bool:
    return any(flag in argv for flag in flags)


def _has_value(argv: Sequence[str], flag: str) -> bool:
    return flag in argv and argv.index(flag) + 1 < len(argv)


# -- small readers -------------------------------------------------------------------
def _items(value: Any, key: str) -> list[Any]:
    """`value[key]` as a list of objects. Anything else in there is not one of ours."""
    inner = value.get(key) if isinstance(value, dict) else None
    inner = inner if isinstance(inner, list) else []
    return [item for item in inner if isinstance(item, dict)]


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
