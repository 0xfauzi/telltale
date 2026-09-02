"""The transcript file Claude Code has already written, as observations. Design 6.3.

Split out of claude.py for the reason claude_launch.py was: that file was at the
800-line ratchet. The surface is `transcript` and the types are
`claude.transcript.<kind>`; the parse contract, the sanitizer and the allowlist are the
same three gates every other surface goes through.

Backfill differs from capture in one way that shapes everything here. Nobody configured
this surface, so nothing was withheld: a transcript line carries what the OTel exporter
was asked not to send. `message.content` holds the assistant's own words and its
thinking, a tool_use block's `input` holds the whole Edit and the whole Write, and
`toolUseResult` holds what the command printed. So this module reads those containers
itself and lifts named scalars out of them, exactly as the stream path does, and hands
the sanitizer nothing it would have to be trusted to drop.

Every shape below was measured over the owner's own ~/.claude/projects on 2026-09-02
(1806 files, 1.7 GB, Claude Code 2.1.219 to 2.1.257). The counts are in claude.DRIFT
and in docs/log/W2-T2.md.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from telltale.providers.claude import (
    _blocks,
    _Common,
    _exit_code,
    _git_fields,
    _lifted,
    _mapping,
    _observe,
    _put,
    _require_object,
    _text,
    _text_length,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from telltale.model import Observation
    from telltale.providers import ParseCtx

SURFACE = "transcript"

# Read by this parser itself, and each one is a column or a correlation id rather than
# a payload field, so passing them on would name `type` and `timestamp` as unknown
# fields on every line of every file.
#
# `message`, `toolUseResult` and `compactMetadata` are NOT here, and that is the point.
# This module reads all three and lifts scalars out of them, and then hands the husk to
# the sanitizer anyway, where `message` is dropped by NEVER_PERSIST and the other two
# by the allowlist gate. Neither gate walks a field it drops, so the cost is a dict
# lookup, and what is bought is `redaction.dropped` naming the container on the
# observation: without it a transcript that carried a whole Edit and one that carried
# nothing look identical in the database (design 6.2).
_CONSUMED = frozenset({
    "type", "subtype", "timestamp", "uuid", "parentUuid", "requestId", "sessionId",
})  # fmt: skip

# The usage names the transcript spells, which are the STREAM's names and not the OTel
# ones. Kept as spelled: cache_read_input_tokens and cache_read_tokens are the same
# quantity under two provider spellings, and renaming one to the other here would be
# this module asserting that rather than the provider.
_USAGE = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "service_tier",
)
_EPHEMERAL = ("ephemeral_1h_input_tokens", "ephemeral_5m_input_tokens")

# compactMetadata, whose keys are camel case where the stream's compact_metadata is
# snake. preCompactDiscoveredTools, preservedSegment and preservedMessages are read by
# nobody: they are a tool list and two message containers.
_COMPACTION = {
    "trigger": "trigger",
    "preTokens": "pre_tokens",
    "postTokens": "post_tokens",
    "cumulativeDroppedTokens": "cumulative_dropped_tokens",
    "durationMs": "duration_ms",
}

# camelCase to snake_case, in two passes so that a run of capitals stays one word:
# toolUseID becomes tool_use_id rather than tool_use_i_d.
_CAMEL_WORD = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_TAIL = re.compile(r"([a-z0-9])([A-Z])")


def snake(name: str) -> str:
    return _CAMEL_TAIL.sub(r"\1_\2", _CAMEL_WORD.sub(r"\1_\2", name)).lower()


def transcript(raw: Any, ctx: ParseCtx) -> list[Observation]:
    """One line of one transcript file. Raises on a line this parser cannot read.

    A line kind this parser does not read becomes no observation and one note, which
    the importer turns into a `dropped` diagnostic per capture. Storing a type nobody
    has measured would put a shape in the database that no allowlist entry describes.
    """
    line = _require_object(raw, "transcript line")
    kind = _text(line.get("type"))
    if kind is None:
        raise ValueError("transcript line carries no type")
    common = _common(line)
    if kind == "assistant":
        return _assistant(line, common, ctx)
    if kind == "user":
        return _user(line, common, ctx)
    if kind == "system":
        return _system(line, common, ctx)
    if kind == "summary":
        # Never seen: no summary line exists in any of the owner's 1806 files. The
        # presence is the whole observation, because the field beside it is prose.
        return [_observe(_type("summary"), SURFACE, _base(line), ctx, common)]
    ctx.notes.append(f"claude.transcript:{kind}")
    return []


def _type(kind: str) -> str:
    return f"claude.transcript.{kind}"


def _common(line: Mapping[str, Any]) -> _Common:
    session = _text(line.get("sessionId"))
    return _Common(
        provider_ts=_text(line.get("timestamp")),
        session_id=session,
        correlations={
            "session_id": session,
            "message_uuid": line.get("uuid"),
            "parent_uuid": line.get("parentUuid"),
            "request_id": line.get("requestId"),
        },
    )


def _base(line: Mapping[str, Any]) -> dict[str, Any]:
    """The line's own top-level fields, snake-cased, minus the ones read here.

    Passed on rather than selected, so a field this version has never seen is dropped
    AND named by the allowlist gate (design 6.4) instead of vanishing. That is how a
    transcript format change becomes a diagnostic rather than silence.
    """
    payload = {snake(key): value for key, value in line.items() if key not in _CONSUMED}
    _put(payload, "message_uuid", line.get("uuid"))
    return payload


def _assistant(
    line: Mapping[str, Any], common: _Common, ctx: ParseCtx
) -> list[Observation]:
    """The request's usage, and one observation for each tool call it asks for."""
    inner = _mapping(line.get("message"))
    payload = _base(line)
    _put(payload, "model", inner.get("model"))
    _put(payload, "stop_reason", inner.get("stop_reason"))
    _put(payload, "message_id", inner.get("id"))
    payload.update(_usage(inner))
    payload.update(_content_counts(inner))
    calls = _blocks(inner, "tool_use")
    if calls:
        payload["tool_names"] = [call.get("name") for call in calls]
        payload["tool_use_ids"] = [call.get("id") for call in calls]
    out = [_observe(_type("assistant"), SURFACE, payload, ctx, common)]
    out += [_tool_use(call, common, ctx) for call in calls]
    return out


def _usage(inner: Mapping[str, Any]) -> dict[str, Any]:
    """message.usage as scalars, including the two ephemeral cache counters."""
    usage = _mapping(inner.get("usage"))
    out: dict[str, Any] = {}
    for key in _USAGE:
        _put(out, key, usage.get(key))
    creation = _mapping(usage.get("cache_creation"))
    for key in _EPHEMERAL:
        _put(out, key, creation.get(key))
    return out


def _content_counts(inner: Mapping[str, Any]) -> dict[str, Any]:
    """How much of each block kind there was. The blocks themselves stay behind.

    A thinking block is reasoning (design 6.3: never persisted) and a text block is the
    assistant's answer. The count and the length are the facts a reducer wants, and
    neither is the text.
    """
    out: dict[str, Any] = {}
    for kind, name in (("thinking", "thinking_blocks"), ("text", "text_blocks")):
        found = _blocks(inner, kind)
        if found:
            out[name] = len(found)
    length = _text_length(inner.get("content"))
    if length is not None:
        out["response_length"] = length
    return out


def _tool_use(call: Mapping[str, Any], common: _Common, ctx: ParseCtx) -> Observation:
    """One tool_use block. `input` holds whole file contents, so scalars come out."""
    payload: dict[str, Any] = {}
    _put(payload, "tool_name", call.get("name"))
    _put(payload, "tool_use_id", call.get("id"))
    payload.update(_lifted(_mapping(call.get("input")), {}))
    return _observe(
        _type("assistant"), SURFACE, payload, ctx, _tooled(common, call.get("id"))
    )


def _tooled(common: _Common, tool_use_id: Any) -> _Common:
    """The same record, correlated to one tool call rather than to the message."""
    return _Common(
        common.provider_ts,
        common.session_id,
        {**common.correlations, "tool_use_id": tool_use_id},
    )


def _user(line: Mapping[str, Any], common: _Common, ctx: ParseCtx) -> list[Observation]:
    """One observation per tool_result block, and one for the prompt's length."""
    inner = _mapping(line.get("message"))
    results = _blocks(inner, "tool_result")
    # The commit id sits on toolUseResult, which names no tool_use_id. With one result
    # block the join is certain; with two it would be a guess, and a guess here would
    # attach a commit to the wrong call. The same rule the stream path uses.
    git = _git_fields(_mapping(line.get("toolUseResult")).get("gitOperation"))
    out = [
        _tool_result(block, common, ctx, git if len(results) == 1 else {}, line)
        for block in results
    ]
    length = _text_length(inner.get("content"))
    if length is not None:
        payload = _base(line)
        payload["prompt_length"] = length
        out.append(_observe(_type("user"), SURFACE, payload, ctx, common))
    return out


def _tool_result(
    block: Mapping[str, Any],
    common: _Common,
    ctx: ParseCtx,
    git: Mapping[str, Any],
    line: Mapping[str, Any],
) -> Observation:
    payload: dict[str, Any] = dict(git)
    _put(payload, "tool_use_id", block.get("tool_use_id"))
    _put(payload, "is_error", block.get("is_error"))
    _put(payload, "message_uuid", common.correlations.get("message_uuid"))
    payload.update(_result_content(block))
    # The husk, so the drop is on the record. toolUseResult holds stdout, stderr,
    # originalFile, oldString and newString; the allowlist gate drops it unread and
    # names it, which is the only evidence that this observation met one.
    _put(payload, "tool_use_result", line.get("toolUseResult"))
    return _observe(
        _type("user"), SURFACE, payload, ctx, _tooled(common, block.get("tool_use_id"))
    )


def _result_content(block: Mapping[str, Any]) -> dict[str, Any]:
    """The size of what the tool printed, and the exit code hidden in its first line.

    Measured over the owner's files: a tool_result content is a string on 6177 blocks
    and a list of blocks on 110, and 110 of 110 results beginning `Exit code N` also
    carry is_error. So the same derived rule the stream uses holds here, and it is read
    only when is_error is true.
    """
    content = block.get("content")
    if not isinstance(content, str):
        return {}
    out: dict[str, Any] = {"tool_result_content_bytes": len(content.encode("utf-8"))}
    code = _exit_code(content) if block.get("is_error") is True else None
    if code is not None:
        out["exit_code"] = code
        out["exit_code_source"] = "transcript_text"
    return out


def _system(
    line: Mapping[str, Any], common: _Common, ctx: ParseCtx
) -> list[Observation]:
    """compact_boundary, and a counted note for the five other subtypes."""
    subtype = _text(line.get("subtype"))
    if subtype != "compact_boundary":
        ctx.notes.append(f"claude.transcript.system:{subtype or 'absent'}")
        return []
    payload = _base(line)
    meta = _mapping(line.get("compactMetadata"))
    for key, name in _COMPACTION.items():
        _put(payload, name, meta.get(key))
    return [_observe(_type("system.compact_boundary"), SURFACE, payload, ctx, common)]
