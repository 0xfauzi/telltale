"""What a provider module is, and the OTLP JSON shapes more than one of them speaks.

Design 6.1 and 6.7. A provider module turns one recorded surface into Observations and
does nothing else: it never touches the database, never reads the filesystem, and never
decides which capture a record belongs to. It exposes `parse`, `hints`, `launch`,
`CAPABILITIES` and `DRIFT`, and the receiver calls those four and nothing more.

`hints` is here because capture attribution has to happen before parsing: the receiver
needs the capture id and the provider session id out of a body it has not parsed yet,
and where they sit is provider knowledge.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

from telltale.sanitize import Ctx

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from telltale.model import Observation

# Providers this package will import. A name from a URL path reaches `get()`, so the
# tuple is the difference between routing and letting a request name a module to import.
KNOWN = ("claude", "codex")

# `service.name` on an OTLP resource, which is how a /v1/logs POST says who sent it.
SERVICE_NAMES = {"claude-code": "claude", "codex": "codex"}


@dataclass(frozen=True)
class ParseCtx:
    """Everything a parse() call needs that is not in the bytes it was handed.

    `paths` is the sanitizer's context, resolved once by the launcher; a parser passes
    it through and never builds its own, because a wrong repo root turns a repo-relative
    path back into an absolute one.
    """

    capture_id: str
    level: int = 1
    paths: Ctx = field(default_factory=Ctx)
    repo_id: str | None = None
    environment_fingerprint_id: str | None = None


@dataclass(frozen=True)
class LaunchPlan:
    """What the launcher does to the child process. Design 6.9.

    `env_remove` is a list of names to DELETE from the inherited environment, which is
    not the same as setting them empty: E01 measured that a child inheriting
    VIRTUAL_ENV from `uv run` cannot run `uv run pytest` in its own repository, and an
    empty VIRTUAL_ENV would be a second wrong answer rather than no answer.

    `surfaces` is what this plan actually configures, which is not always what the
    provider can do: a `--settings` naming a file cannot be merged without rewriting the
    child's own configuration, so the hook surface is given up rather than taken over.
    The launcher records this as `surfaces_configured` (design 6.3), and a surface that
    was never configured is not the same as one that delivered nothing.
    """

    argv: list[str]
    env: dict[str, str]
    tee: bool
    env_remove: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()


class Provider(Protocol):
    """The whole of what a provider module owes the receiver and the launcher."""

    CAPABILITIES: dict[str, dict[str, str]]
    DRIFT: list[str]
    PARSER_VERSION: int
    ADAPTER: str

    def parse(self, surface: str, raw: Any, ctx: ParseCtx) -> list[Observation]: ...

    def hints(self, surface: str, raw: Any) -> tuple[str | None, str | None]: ...

    def launch(
        self,
        argv: Sequence[str],
        port: int,
        level: int,
        session_id: str | None,
        capture_id: str | None = None,
    ) -> LaunchPlan: ...


def get(name: str) -> Provider:
    """The provider module called `name`, or ValueError for a name nobody declared."""
    if name not in KNOWN:
        raise ValueError(f"no provider named {name!r}")
    return cast("Provider", importlib.import_module(f"telltale.providers.{name}"))


def otlp_value(wrapper: Any) -> Any:
    """One OTLP AnyValue as a Python value.

    Every attribute on the wire is a one-key object naming its type. The types are not
    reliable across encoders: E01's fixtures carry `intValue` as a JSON number while
    `timeUnixNano` beside it is a decimal STRING, and the OTLP JSON specification says
    64-bit integers are strings. Both spellings are read here, and a string that will
    not convert is returned unchanged so that the sanitizer's SIZE gate drops it and
    says so, rather than this function inventing a zero.
    """
    if not isinstance(wrapper, dict):
        return None
    for key, convert in (
        ("stringValue", str),
        ("intValue", _as_int),
        ("doubleValue", _as_float),
        ("boolValue", bool),
    ):
        if key in wrapper:
            return convert(wrapper[key])
    if "arrayValue" in wrapper:
        values = wrapper["arrayValue"].get("values") if wrapper["arrayValue"] else None
        return [otlp_value(item) for item in values or []]
    if "kvlistValue" in wrapper:
        values = (
            wrapper["kvlistValue"].get("values") if wrapper["kvlistValue"] else None
        )
        return otlp_attrs(values)
    # bytesValue and an empty AnyValue both land here. Unknown stays None.
    return None


def otlp_attrs(items: Iterable[Any] | None) -> dict[str, Any]:
    """`[{"key": k, "value": {...}}, ...]` as a dict, keys kept in wire spelling."""
    out: dict[str, Any] = {}
    for item in items or []:
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            out[item["key"]] = otlp_value(item.get("value"))
    return out


def iso_from_nanos(value: Any) -> str | None:
    """Unix nanoseconds (string or int) as ISO 8601 UTC with a Z, or None.

    Integer arithmetic rather than a float division: at 2026 timestamps a float carries
    about a microsecond of error, and this is the field that orders a session.
    """
    try:
        nanos = int(value)
    except (TypeError, ValueError):
        return None
    seconds, remainder = divmod(nanos, 1_000_000_000)
    moment = datetime.fromtimestamp(seconds, UTC).replace(microsecond=remainder // 1000)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _as_int(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _as_float(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


__all__ = [
    "KNOWN",
    "SERVICE_NAMES",
    "Ctx",
    "LaunchPlan",
    "ParseCtx",
    "Provider",
    "get",
    "iso_from_nanos",
    "otlp_attrs",
    "otlp_value",
]
