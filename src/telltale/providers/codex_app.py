"""The OpenAI Codex desktop app, as four OTLP log-record shapes and nothing else.

W11-T2. The app exports OTLP JSON to the daemon's port under `service.name`
"codex-app-server". Telltale never launches it, so this module is only ever handed
bodies the receiver already has, and `launch` refuses.

What was measured, and all that was measured (the orchestrator, 2026-09-14, two
schema-only sniffs of about 25 seconds each; attribute key NAMES, plus the values of
`event.name` and `event.kind`): four attribute-key sets, and twelve event names, with no
pairing between a name and a set. So a log record is classified by its keys. It becomes
an observation only when

  1. its `event.name` is one of the twelve, and
  2. its attribute keys are EXACTLY one of the four measured sets: none missing, none
     added, none repeated.

Anything else becomes no observation and one `dropped` note saying why. Equality rather
than "contains a measured set": a key more is a shape nobody has seen, and the extra key
is exactly where a differently-spelled content field would sit. A key fewer is refused
too, because which keys are optional was not measured and a subset rule would be a
guess about that.

The four sets are, key for key, sets E02 recorded from Codex CLI 0.150.1 under
codex.tool_result, codex.sse_event, codex.tool_decision and codex.websocket_request
(DRIFT has the counts). That is evidence for a pairing on the CLI and none on the app,
so the observation types name the SHAPE, and `event_name` is stored beside it: the rows
will say which name carries which set.

Only record attributes are read. The log record's body, its resource attributes other
than the `service.name` the receiver routes on, and its scope are never read, so none of
them can be stored.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from telltale.allowlist import ALLOWLIST, Kind
from telltale.model import Observation, now_iso, ulid
from telltale.providers import OtlpPoint, iso_from_nanos, otlp_points
from telltale.sanitize import sanitize

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.providers import LaunchPlan, ParseCtx

PARSER_VERSION = 1
ADAPTER = f"codex_app@{PARSER_VERSION}"

SURFACES = ("otel_logs", "otel_metrics")

# The twelve `event.name` values seen on this app's traffic. A name outside this set is
# refused whatever its keys are, and one inside it is refused unless its keys are one of
# the four sets below: being on this list is necessary and never sufficient.
EVENT_NAMES = frozenset({
    "codex.agent_communication", "codex.api_request",
    "codex.browser_use.security_check", "codex.conversation_starts", "codex.sse_event",
    "codex.startup_phase", "codex.tool_decision", "codex.tool_result",
    "codex.turn_ttft", "codex.user_prompt", "codex.websocket_connect",
    "codex.websocket_request",
})  # fmt: skip

# The four measured key sets, in wire spelling, copied from the brief.
SHAPES: dict[frozenset[str], str] = {
    frozenset(keys.split()): obs_type
    for obs_type, keys in (
        (
            "codex_app.otel.tool_call_outcome",
            "agent_name app.version arguments auth_mode call_id conversation.id"
            " duration_ms event.name event.timestamp mcp_server mcp_server_origin model"
            " originator output output_truncated slug success terminal.type tool_name"
            " tool_namespace tool_result_seq user.account_id user.email",
        ),
        (
            "codex_app.otel.token_usage",
            "app.version auth_mode cache_write_token_count cached_token_count"
            " conversation.id event.kind event.name event.timestamp input_token_count"
            " model model_reasoning_effort originator output_token_count"
            " reasoning_token_count slug terminal.type tool_token_count ttft_ms"
            " user.account_id user.email",
        ),
        (
            "codex_app.otel.tool_decision",
            "app.version auth_mode call_id conversation.id decision event.name"
            " event.timestamp model originator slug source terminal.type tool_name"
            " tool_namespace user.account_id user.email",
        ),
        (
            "codex_app.otel.authenticated_call",
            "app.version auth.connection_reused auth.env_codex_api_key_enabled"
            " auth.env_codex_api_key_present auth.env_openai_api_key_present"
            " auth.env_refresh_token_url_override_present auth_mode conversation.id"
            " duration_ms event.name event.timestamp model originator slug success"
            " terminal.type user.account_id user.email",
        ),
    )
}

# Read into a column, so not passed on as a payload field.
_CONSUMED = frozenset({"conversation.id"})

# What a provider id may look like before it reaches a column the sanitizer never
# sees (provider_session_id, correlation_ids). E02 measured 36-character uuids for
# conversation.id and `call_...` strings of 29 and 41 for call_id; anything that is
# not this shape is treated as absent rather than written.
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")

_UNAVAILABLE = ("unavailable", "unavailable")

# Spec 9.1 facts against the two surfaces, in SURFACES order. `partial` on both built
# rows, not `observed`: a record is kept only when its key set is exactly a measured
# one, and which unit of work a token_usage record counts (a turn, a request, a
# response) is not measured on the app. No metric is parsed.
CAPABILITIES: dict[str, dict[str, str]] = {
    name: dict(zip(SURFACES, cells, strict=True))
    for name, cells in (
        ("request_usage", ("partial", "unavailable")),
        ("tool_calls", ("partial", "unavailable")),
        *(
            (name, _UNAVAILABLE)
            for name in (
                "request_duration", "context_window", "compaction", "file_paths",
                "commands", "subagents", "exit_codes", "commit_ids", "permission_mode",
            )
        ),
    )
}  # fmt: skip

DRIFT: list[str] = [
    "W11-T2: the four key sets measured on the app equal, key for key, the sets E02 "
    "recorded from Codex CLI 0.150.1 under codex.tool_result (36 of 36 records), "
    "codex.sse_event (24 of 33), codex.tool_decision (17 of 17) and "
    "codex.websocket_request (33 of 33), over fixtures/sources/codex/0.150.1/*/"
    "otel_logs.jsonl. No other E02 event has any of the four. The pairing on the app "
    "is not measured; `event_name` on the stored rows is what will measure it.",
    "W11-T2: on the CLI, 9 of 33 codex.sse_event records have another key set: 6 lack "
    "model_reasoning_effort and ttft_ms, 3 carry error.message and no token counts. "
    "If the app does the same, those records are refused as an unmeasured shape and "
    "their token counts are not stored.",
    "W11-T2: on the CLI `success` is a stringValue 'true' or 'false' on all 69 records "
    "of those four events that carry it; output_truncated, auth.connection_reused and "
    "the four auth.env_* are boolValue on every one. On the app only "
    "output_truncated's type was measured (pass 2: never a string). The parser admits "
    "a boolean or those two strings for every flag and refuses anything else as "
    "not_a_bool.",
]


def parse(surface: str, raw: Any, ctx: ParseCtx) -> list[Observation]:
    """One received body as observations. Raises for a surface this app does not have.

    A metric point is never stored: pass 1 names no metric and no metric attribute was
    measured. Each one is a note, so how many arrived is still on record.
    """
    if surface == "otel_logs":
        found = (_otel_log(point, ctx) for point in otlp_points(surface, raw))
        return [observation for observation in found if observation is not None]
    if surface == "otel_metrics":
        ctx.notes.extend(
            "codex_app.otel.metric:unmeasured" for _ in otlp_points(surface, raw)
        )
        return []
    raise ValueError(f"codex_app has no {surface!r} surface")


def hints(surface: str, raw: Any) -> tuple[str | None, str | None]:
    """No capture id, ever, and the first readable conversation id as the session.

    Nothing launched the app, so nothing put a capture id in its resource: in the daemon
    the conversation id is what derives the capture. A metrics batch names no
    conversation.
    """
    if surface != "otel_logs":
        return None, None
    for point in otlp_points(surface, raw):
        session = _identifier(point.attrs.get("conversation.id"))
        if session is not None:
            return None, session
    return None, None


def launch(
    argv: Sequence[str],  # noqa: ARG001
    port: int,  # noqa: ARG001
    level: int,  # noqa: ARG001
    session_id: str | None,  # noqa: ARG001
    capture_id: str | None = None,  # noqa: ARG001
) -> LaunchPlan:
    """Refused. The app is configured by its owner and only ever received.

    Here because the Provider protocol asks every module for it. launch.py `_plan`
    turns the refusal into a `launcher` diagnostic and runs the child with its own argv,
    which is the fail-open path for a provider without a launch plan.
    """
    raise ValueError("codex_app is received from the desktop app, never launched")


def _otel_log(point: OtlpPoint, ctx: ParseCtx) -> Observation | None:
    obs_type, refusal = _classify(point)
    if obs_type is None:
        ctx.notes.append(refusal)
        return None
    attrs = point.attrs
    session = _identifier(attrs.get("conversation.id"))
    payload = {
        key.replace(".", "_"): value
        for key, value in attrs.items()
        if key not in _CONSUMED
    }
    typed, refused = _admit(payload, ALLOWLIST[obs_type])
    body, redaction, _unknown = sanitize(obs_type, typed, ctx.level, ctx.paths)
    redaction["dropped"].extend(refused)
    correlations = {"session_id": session, "call_id": _identifier(attrs.get("call_id"))}
    return Observation(
        observation_id=ulid(),
        capture_id=ctx.capture_id,
        observation_type=obs_type,
        surface="otel_logs",
        provider="codex_app",
        adapter=ADAPTER,
        ingest_ts=now_iso(),
        provider_ts=_provider_ts(point),
        provider_session_id=session,
        environment_fingerprint_id=ctx.environment_fingerprint_id,
        repo_id=ctx.repo_id,
        correlation_ids={k: v for k, v in correlations.items() if v is not None},
        payload=body,
        redaction=redaction,
    )


def _classify(point: OtlpPoint) -> tuple[str | None, str]:
    """(observation type, "") for a measured record, or (None, the note refusing it)."""
    keys = _wire_keys(point.record)
    if keys is None:
        return None, "codex_app.otel:unreadable_attributes"
    name = point.attrs.get("event.name")
    if name is None:
        return None, "codex_app.otel:no_event_name"
    if not isinstance(name, str) or name not in EVENT_NAMES:
        # The value is not written: it is outside the twelve, so unmeasured.
        return None, "codex_app.otel:unknown_event_name"
    obs_type = SHAPES.get(keys)
    if obs_type is None:
        return None, f"codex_app.otel.{name}:unmeasured_shape"
    return obs_type, ""


def _wire_keys(record: Mapping[str, Any]) -> frozenset[str] | None:
    """The record's attribute keys, or None when they are not one value per key.

    Read off the wire list and not off OtlpPoint.attrs: that dict keeps the last of two
    attributes with one key and skips an item it cannot read, and either would let a
    record pass for a measured shape it is not.
    """
    items = record.get("attributes")
    if not isinstance(items, list):
        return None
    keys = [item.get("key") if isinstance(item, dict) else None for item in items]
    unique = {key for key in keys if isinstance(key, str)}
    return frozenset(unique) if len(unique) == len(keys) else None


def _admit(
    payload: Mapping[str, Any], kinds: Mapping[str, Kind]
) -> tuple[dict[str, Any], list[str]]:
    """Each listed field in the type its Kind says, and `field:reason` for each refused.

    A field this table does not list goes on unchanged: the sanitizer drops it, and says
    whether it was never_persist, refused or unknown. A listed field whose value is the
    wrong type never reaches the sanitizer, because the sanitizer would keep it: a
    string under Kind.SCALAR is stored as text, and a list or an object under Kind.ENUM
    is walked and stored whole.
    """
    typed: dict[str, Any] = {}
    refused: list[str] = []
    for name, value in payload.items():
        kind = kinds.get(name)
        admitted, reason = (value, None) if kind is None else _typed(value, kind)
        if reason is None:
            typed[name] = admitted
        else:
            refused.append(f"{name}:{reason}")
    return typed, refused


def _typed(value: Any, kind: Kind) -> tuple[Any, str | None]:
    if value is None:
        return None, None  # unknown stays unknown
    if isinstance(value, list | dict):
        return None, "not_a_scalar"
    if kind is Kind.SCALAR:
        return _flag(value)
    if kind is Kind.SIZE and isinstance(value, str):
        # E02: the CLI sends durations and token counts as decimal strings. One that
        # does not convert stays a string, and the sanitizer drops it as not_a_number.
        return _number(value), None
    return value, None


def _flag(value: Any) -> tuple[Any, str | None]:
    """A boolean, from a boolValue or from the strings the CLI sends; nothing else."""
    if isinstance(value, bool):
        return value, None
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true", None
    return None, "not_a_bool"


def _number(text: str) -> Any:
    for convert in (int, float):
        try:
            return convert(text)
        except ValueError:
            continue
    return text


def _identifier(value: Any) -> str | None:
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _provider_ts(point: OtlpPoint) -> str | None:
    """event.timestamp when it reads as a timestamp, else the record's own clock.

    A zero clock is unknown, not 1970: E02 measured timeUnixNano as "0" on every Codex
    CLI log record.
    """
    stamp = point.attrs.get("event.timestamp")
    if isinstance(stamp, str) and len(stamp) <= 64:
        try:
            datetime.fromisoformat(stamp)
        except ValueError:
            pass
        else:
            return stamp
    for key in ("observedTimeUnixNano", "timeUnixNano"):
        value = point.record.get(key)
        if value not in (None, 0, "0"):
            found = iso_from_nanos(value)
            if found is not None:
                return found
    return None
