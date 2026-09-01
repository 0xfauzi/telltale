"""What may be persisted, and what is removed before anything is written.

Design 6.4 and spec 11.2. Three gates, in this order, each failing closed:

  1. An ALLOWLIST keyed by observation type says which field names survive. A field
     nobody listed is dropped and its name returned, so the caller can write an
     `unknown_field` diagnostic. A provider that adds a field therefore lowers coverage
     and raises a diagnostic instead of quietly persisting something new.
  2. Each surviving field is cleaned by its declared Kind: a path is made repo-relative
     or hashed, a command is normalized, a string is scrubbed of secrets and bounded.
  3. NEVER_PERSIST removes a set of field names at every depth, whatever the allowlist
     says. It is the hard stop for the case where the first gate is what is wrong.

Nothing here touches the filesystem. It runs on the receiver's request thread, where a
stat() on a path an agent mentioned would be both a delay and a disclosure.
"""

from __future__ import annotations

import hashlib
import os.path
import re
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from telltale.allowlist import ALLOWLIST, Kind
from telltale.model import to_json

# Bounds. The first two are design 6.4; the rest are this module's decisions, and each
# one is here because an unbounded structure is a way to write an unbounded row.
MAX_STRING = 512
MAX_PAYLOAD_BYTES = 8 * 1024
MAX_ENUM = 64  # a symbolic value that arrives holding a paragraph is a provider change
MAX_ID = 128  # ULIDs are 26 and uuids 36; 128 leaves room and still caps a leak
MAX_ITEMS = 256  # per list or dict; a longer one is truncated and recorded
MAX_DEPTH = 6  # deeper than any payload shape in design 6.3


# Field names that never reach the store, at any content level and at any depth of the
# payload, whatever the allowlist says. Design 6.3 fixes the list: prompt text,
# assistant text, tool_response bodies, edit contents, command output, environment
# variable values and reasoning. The extra spellings are the same content under the name
# a provider happens to use.
#
# A field here is not merely unlisted: the allowlist could list it by mistake, and this
# set is what makes that mistake harmless. Matched on the exact key, because a substring
# rule would take `content_level` and `prompt_length` with it, and those are the
# quantities that make the drop measurable.
#
# `result`, `summary` and `compact_summary` were added by W0-T5 on W0-T4's measurement
# (its finding 6 and DRIFT 13): on Claude Code 2.1.257 a stream `result` message carries
# the whole final assistant answer in `result`, `system:task_notification` carries a
# subagent's answer in `summary`, and the PostCompact hook carries the compacted
# transcript in `compact_summary`. All three were already dropped, by the allowlist gate
# alone, which is the gate a future allowlist entry can undo.
NEVER_PERSIST: frozenset[str] = frozenset({
    "aggregated_output", "arguments", "assistant_response", "body", "compact_summary",
    "content", "contents", "custom_instructions", "env", "environment", "input",
    "last_assistant_message", "message", "new_str", "new_string", "old_str",
    "old_string", "output", "prompt", "prompt_text", "reasoning", "reasoning_content",
    "response", "result", "stderr", "stdout", "summary", "text", "tool_input",
    "tool_parameters", "tool_response", "tool_result",
})  # fmt: skip

# A private key header, with any word between BEGIN and PRIVATE: OPENSSH, RSA, EC, and
# vendors invent more, so the word is a wildcard rather than a list.
_KEY_HEADER = r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY[ A-Z0-9]{0,20}-----"
_KEY_FOOTER = r"-----END [A-Z0-9 ]{0,40}PRIVATE KEY[ A-Z0-9]{0,20}-----"

# Secret patterns, design 6.4. Applied in this order to every string that survives, and
# each match becomes `<redacted:N>`, N counting the redactions inside that string.
# The key=value rule keeps the key and replaces only the value, because the name of a
# variable is a fact worth having and its value never is.
_KEEP_KEY = 1
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    # The whole block first, then the bare header, because a bounded provider field is
    # exactly where a key arrives cut in half and the footer never comes.
    (re.compile(_KEY_HEADER + r"[\s\S]*?" + _KEY_FOOTER), 0),
    (re.compile(_KEY_HEADER), 0),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 0),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"), 0),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), 0),
    (re.compile(r"\bgh[po]_[A-Za-z0-9]{16,}\b"), 0),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), 0),
    (
        re.compile(
            r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_.-]{0,63}=)"
            r"([A-Za-z0-9+/=_-]{32,})"
        ),
        _KEEP_KEY,
    ),
)


@dataclass
class Ctx:
    """Where the capture is happening, as far as path rewriting is concerned.

    Both paths must be absolute and already resolved by the caller: this module does no
    filesystem access, so a symlinked home is not followed here. The launcher resolves
    once, at capture start, and passes the answer down.
    """

    repo_root: Path | None = None
    home: Path = field(default_factory=Path.home)

    def __post_init__(self) -> None:
        self.home = Path(os.path.normpath(self.home))
        if self.repo_root is not None:
            self.repo_root = Path(os.path.normpath(self.repo_root))
            if not self.repo_root.is_absolute():
                raise ValueError(f"repo_root must be absolute: {self.repo_root}")
        if not self.home.is_absolute():
            raise ValueError(f"home must be absolute: {self.home}")


@dataclass
class _ScrubState:
    count: int = 0

    def replace(self, match: re.Match[str], group: int) -> str:
        self.count += 1
        marker = f"<redacted:{self.count}>"
        return marker if group == 0 else match.group(group) + marker


def scrub(text: str) -> tuple[str, int]:
    """Replace secrets with `<redacted:N>`; return the text and how many were replaced.

    N is the position of the redaction inside this string, so two secrets in one value
    read `<redacted:1>` and `<redacted:2>` and a reader can tell one leak from two.
    """
    state = _ScrubState()
    for pattern, keep_group in _SECRET_PATTERNS:
        text = pattern.sub(partial(state.replace, group=keep_group), text)
    return text, state.count


def relativize(path: str, ctx: Ctx, level: int) -> str | None:
    """Rewrite a path so that nothing outside the repository survives it.

    Inside the repository it becomes repo-relative. Anywhere else, the home directory
    included, it becomes `<outside>/<8 hex>`: one directory gives one token, so "the
    agent read two files somewhere it should not have" stays visible while the place
    does not. Level 0 keeps no paths (design 6.4) and returns None, so the field goes.
    """
    if level <= 0:
        return None
    text = path.strip()
    if not text:
        return None
    candidate = Path(_expand_home(text, ctx))
    if not candidate.is_absolute():
        if ctx.repo_root is None:
            return _bound(scrub(text)[0], MAX_STRING)
        candidate = ctx.repo_root / candidate
    resolved = Path(os.path.normpath(candidate))
    if ctx.repo_root is not None and resolved.is_relative_to(ctx.repo_root):
        return str(resolved.relative_to(ctx.repo_root)) or "."
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    return f"<outside>/{digest}"


def _expand_home(text: str, ctx: Ctx) -> str:
    if text == "~":
        return str(ctx.home)
    if text.startswith("~/"):
        return str(ctx.home / text[2:])
    return text


def _bound(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _encodable(text: str) -> tuple[str, bool]:
    """Replace anything UTF-8 cannot encode, and say whether it had to.

    json.loads turns "\\ud800" into a lone surrogate that sqlite3 refuses to store, and
    measured before this existed, one such string killed the writer thread. The ascii
    check is the fast path, which almost every provider string takes.
    """
    if text.isascii():
        return text, False
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8"), True
    return text, False


class _Drop:
    """The one value that means "this field does not survive"; None means unknown."""


_DROP = _Drop()


@dataclass
class _Trace:
    """What the walk removed, in the shape design 6.2 gives `Observation.redaction`.

    Entries are `field:reason` so that a reader of one observation can tell an unknown
    field from a forbidden one without going back to the diagnostics table.

    `normalization` is carried here rather than returned because it is produced deep
    inside the walk, by whichever field turned out to be a command.
    """

    dropped: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    redacted: list[str] = field(default_factory=list)
    normalization: str = ""

    def note(self, bucket: list[str], name: str, reason: str = "") -> None:
        entry = f"{name}:{reason}" if reason else name
        if entry not in bucket:
            bucket.append(entry)

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "dropped": self.dropped,
            "truncated": self.truncated,
            "redacted": self.redacted,
        }


def sanitize(
    obs_type: str,
    raw_payload: dict[str, Any],
    level: int,
    ctx: Ctx,
) -> tuple[dict[str, Any], dict[str, list[str]], list[str]]:
    """Return the payload that may be stored, what was removed, and unknown field names.

    The third return value is what the caller turns into an `unknown_field` diagnostic.
    It is separate from `redaction.dropped` on purpose: the redaction travels with the
    observation for a reader, the list drives the diagnostic that says a parser is now
    behind its provider.
    """
    if level not in (0, 1, 2):
        raise ValueError(f"content level {level!r} is not 0, 1 or 2")
    trace = _Trace()
    allowed = ALLOWLIST.get(obs_type)
    payload: dict[str, Any] = {}
    unknown: list[str] = []
    for name, value in raw_payload.items():
        if name in NEVER_PERSIST:
            trace.note(trace.dropped, name, "never_persist")
            continue
        if allowed is None or name not in allowed:
            unknown.append(name)
            trace.note(trace.dropped, name, "unknown")
            continue
        cleaned = _clean(value, allowed[name], ctx, level, trace, name, 0)
        if not isinstance(cleaned, _Drop):
            payload[name] = cleaned
    if trace.normalization:
        # Design 6.4: the normalization version is recorded in the payload, so a command
        # normalized by an older rule set is not compared against a newer one.
        payload["normalization_version"] = trace.normalization
    _bound_payload(payload, trace)
    return payload, trace.as_dict(), unknown


def _clean(
    value: Any,
    kind: Kind,
    ctx: Ctx,
    level: int,
    red: _Trace,
    name: str,
    depth: int,
) -> Any:
    if value is None:
        return None  # unknown stays unknown
    if depth > MAX_DEPTH:
        red.note(red.dropped, name, "depth")
        return _DROP
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _clean_str(value, kind, ctx, level, red, name)
    if isinstance(value, list):
        return _clean_list(value, kind, ctx, level, red, name, depth)
    if isinstance(value, dict):
        return _clean_dict(value, kind, ctx, level, red, name, depth)
    red.note(red.dropped, name, "type")
    return _DROP


def _clean_str(
    value: str,
    kind: Kind,
    ctx: Ctx,
    level: int,
    red: _Trace,
    name: str,
) -> Any:
    value, mangled = _encodable(value)
    if mangled:
        red.note(red.redacted, name, "encoding")
    if kind is Kind.SIZE:
        # A size is a number. A string here means the parser read the wrong attribute,
        # and keeping it would put free text in a column later code will do sums on.
        red.note(red.dropped, name, "not_a_number")
        return _DROP
    if kind is Kind.PATH:
        relative = relativize(value, ctx, level)
        if relative is None:
            red.note(red.dropped, name, "level0_path" if level <= 0 else "empty_path")
            return _DROP
        return relative
    if kind is Kind.COMMAND:
        # Imported here, not at the top: commands.py needs relativize() from this
        # module, so one of the two directions has to be deferred. This one is called
        # once per command field, and a repeat import is a dict lookup in sys.modules.
        from telltale.commands import MAX_COMMAND, normalize

        normalized, version = normalize(value, ctx, level)
        red.normalization = version
        if len(normalized) >= MAX_COMMAND:
            # A normal form that reaches the bound was cut by it. One that is exactly
            # 200 characters and was not cut is recorded here too, which overstates by
            # one list entry and never understates.
            red.note(red.truncated, name, "command_bound")
        return normalized
    scrubbed, hits = scrub(value)
    if hits:
        red.note(red.redacted, name)
    limit = {Kind.ENUM: MAX_ENUM, Kind.ID: MAX_ID}.get(kind, MAX_STRING)
    bounded = _bound(scrubbed, limit)
    if len(bounded) < len(scrubbed):
        red.note(red.truncated, name, "string_bound")
    return bounded


def _clean_list(
    value: list[Any],
    kind: Kind,
    ctx: Ctx,
    level: int,
    red: _Trace,
    name: str,
    depth: int,
) -> Any:
    if len(value) > MAX_ITEMS:
        red.note(red.truncated, name, "items")
    out: list[Any] = []
    for item in value[:MAX_ITEMS]:
        cleaned = _clean(item, kind, ctx, level, red, name, depth + 1)
        if not isinstance(cleaned, _Drop):
            out.append(cleaned)
    return out


def _clean_dict(
    value: dict[str, Any],
    kind: Kind,
    ctx: Ctx,
    level: int,
    red: _Trace,
    name: str,
    depth: int,
) -> Any:
    if len(value) > MAX_ITEMS:
        red.note(red.truncated, name, "items")
    out: dict[str, Any] = {}
    for raw_key, item in list(value.items())[:MAX_ITEMS]:
        # str() because a JSON object read by another parser can arrive with an integer
        # key, and a dict with both kinds cannot be serialized with sorted keys at all.
        key = _encodable(str(raw_key))[0]
        if key in NEVER_PERSIST:
            red.note(red.dropped, f"{name}.{key}", "never_persist")
            continue
        cleaned = _clean(item, kind, ctx, level, red, name, depth + 1)
        if isinstance(cleaned, _Drop):
            continue
        # A PATH field can be keyed BY a path: design 6.3's instruction_hashes is
        # {path: {sha256, bytes}}, so the key needs the same rewrite as a value.
        out_key = key if kind is not Kind.PATH else relativize(key, ctx, level)
        if out_key is None:
            red.note(red.dropped, name, "level0_path")
            continue
        out[out_key] = cleaned
    return out


def _bound_payload(payload: dict[str, Any], red: _Trace) -> None:
    """Drop whole fields, largest first, until the payload fits MAX_PAYLOAD_BYTES.

    Design 6.4 bounds the payload after sanitization. Dropping the largest field is the
    only rule that terminates without inventing a value: truncating a structure in place
    would leave a list that looks complete and is not.
    """
    while payload and len(to_json(payload).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        widest = max(payload, key=lambda key: len(to_json(payload[key])))
        del payload[widest]
        red.note(red.truncated, widest, "payload_bound")
