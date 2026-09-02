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
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol, cast

from telltale.sanitize import Ctx, relativize

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from telltale.model import Observation

# Providers this package will import. A name from a URL path reaches `get()`, so the
# tuple is the difference between routing and letting a request name a module to import.
KNOWN = ("claude", "codex")

# `service.name` on an OTLP resource, which is how a /v1/logs POST says who sent it.
# `codex_exec` is what E02 measured on every Codex record; `codex` stays because it is
# what the digest documents and a different subcommand may well send it.
SERVICE_NAMES = {"claude-code": "claude", "codex": "codex", "codex_exec": "codex"}


@dataclass(frozen=True)
class ParseCtx:
    """Everything a parse() call needs that is not in the bytes it was handed.

    `paths` is the sanitizer's context, resolved once by the launcher; a parser passes
    it through and never builds its own, because a wrong repo root turns a repo-relative
    path back into an absolute one.

    `notes` is how a parser reports a record it refused BEFORE an Observation existed.
    An unknown field travels to the receiver inside `Observation.redaction`, but a
    record that becomes no observation at all has nowhere to put that, and a reasoning
    item is exactly such a record (design 6.3: reasoning is never persisted). The
    receiver turns whatever is here into one `dropped` diagnostic per request, so the
    count is queryable and no provider module ever touches the store.
    """

    capture_id: str
    level: int = 1
    paths: Ctx = field(default_factory=Ctx)
    repo_id: str | None = None
    environment_fingerprint_id: str | None = None
    notes: list[str] = field(default_factory=list)


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


@dataclass(frozen=True)
class OtlpPoint:
    """One OTLP unit of work: a log record, or one data point of one metric."""

    resource: dict[str, Any]
    attrs: dict[str, Any]
    record: Mapping[str, Any]
    metric: Mapping[str, Any] | None = None


def otlp_points(surface: str, raw: Any) -> Iterator[OtlpPoint]:
    """Every log record, or every data point of every metric, with its resource.

    The shape of an OTLP JSON body is the same whoever sent it, which is why it is here
    rather than in a provider module: `resourceLogs/scopeLogs/logRecords` and
    `resourceMetrics/scopeMetrics/metrics/<aggregation>/dataPoints`. Anything that is
    not a JSON object at any level of that walk is skipped rather than guessed at.
    """
    if surface == "otel_logs":
        yield from _log_points(raw)
        return
    for block in _objects(raw, "resourceMetrics"):
        resource = otlp_attrs(_dict(block.get("resource")).get("attributes"))
        for scope in _objects(block, "scopeMetrics"):
            for metric in _objects(scope, "metrics"):
                yield from _metric_points(metric, resource)


def _log_points(raw: Any) -> Iterator[OtlpPoint]:
    for block in _objects(raw, "resourceLogs"):
        resource = otlp_attrs(_dict(block.get("resource")).get("attributes"))
        for scope in _objects(block, "scopeLogs"):
            for record in _objects(scope, "logRecords"):
                yield OtlpPoint(resource, otlp_attrs(record.get("attributes")), record)


def _metric_points(
    metric: Mapping[str, Any], resource: dict[str, Any]
) -> Iterator[OtlpPoint]:
    """Every data point of one metric, whatever aggregation it arrived under."""
    for aggregation in ("sum", "gauge", "histogram", "exponentialHistogram", "summary"):
        for point in _objects(metric.get(aggregation), "dataPoints"):
            yield OtlpPoint(
                resource, otlp_attrs(point.get("attributes")), point, metric
            )


def _objects(value: Any, key: str) -> list[dict[str, Any]]:
    inner = value.get(key) if isinstance(value, dict) else None
    inner = inner if isinstance(inner, list) else []
    return [item for item in inner if isinstance(item, dict)]


def _dict(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


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


# An absolute path inside a sentence. `(?<![\w~:/])` keeps the slash of `and/or`, of
# `10/20` and of a `scheme://host/path` out; `\S+` runs to the next space and the tail
# characters below come back off, so a path at the end of a clause keeps its comma.
_PROSE_PATH = re.compile(r"(?<![\w~:/])~?/(?!/)\S+")
_PROSE_TAIL = ".,:;!?)]}>'\"`"


def prose(text: str, paths: Ctx, level: int) -> str:
    """Rewrite the absolute paths inside free text, and leave the words alone.

    Kind.SCALAR scrubs secrets and bounds the length. It does not rewrite a path,
    because the value it was handed is not one; a provider that lifts the agent's own
    error message lifts prose with a path INSIDE it. Measured on all seven E02
    scenarios: `failed to parse hooks config <home>/.codex/hooks.json` put the home
    directory in the store, which is the one thing the privacy test forbids outright.

    Every run goes through the same `relativize` the PATH kind uses, so a path in prose
    and a path in a field are hidden by one mechanism and not two, and level 0 keeps no
    path here either.
    """
    return _PROSE_PATH.sub(partial(_prose_run, paths=paths, level=level), text)


def _prose_run(match: re.Match[str], paths: Ctx, level: int) -> str:
    run, tail = match.group(0), ""
    while run and run[-1] in _PROSE_TAIL:
        run, tail = run[:-1], run[-1] + tail
    rewritten = relativize(run, paths, level)
    return (rewritten if rewritten is not None else "<path>") + tail


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
    "OtlpPoint",
    "ParseCtx",
    "Provider",
    "get",
    "iso_from_nanos",
    "otlp_attrs",
    "otlp_points",
    "otlp_value",
    "prose",
]
