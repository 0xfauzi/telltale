"""Build the observability matrix by SEARCHING the captures, not by asserting a table.

Every cell is the result of running a probe over the bytes in experiments/E02/out/, so a
cell that says `observed` names the field path it found and a cell that says
`unavailable` means the probe ran and found nothing. A hand-written table would say the
same words with none of that behind them, and this repository's whole point is that a
number nobody measured is a guess.

Three cell values, and the difference matters:
  observed     a probe matched and at least one occurrence carried a non-null value
  partial      a probe matched but every occurrence was null or empty, so the surface
               knows the field exists and this capture never saw it filled
  unavailable  no probe matched anywhere on that surface, in any scenario

Two probe kinds, because not every fact is a field:
  key    a leaf key name, including OTLP attributes, which hide their names in a
         sibling `key`/`value` pair that a plain walk never sees as a key
  event  a record type, an `event.name`, or a `hook_event_name`; session start is an
         EVENT on three surfaces and a field on none of them

Usage: uv run python experiments/E02/matrix.py [--markdown]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_ROOT = HERE / "out"

# Sink files land by request PATH. Codex posts OTLP to the endpoint verbatim, so with a
# base endpoint everything arrives on `/` and the receiver files it under `other`. The
# signal is then told apart by the payload's top-level key. Both spellings are listed so
# this works whichever endpoint form was used.
#
# Each source names an EXTRACTOR, because what the matrix is measuring is what CODEX
# emitted, not what the sink or hook_post.py wrapped it in. Indexing a hook line whole
# would let `delivery.status`, which this experiment invented, answer a question about
# what Codex reports.
SURFACES: dict[str, tuple[tuple[str, str], ...]] = {
    "exec_json": (("exec.jsonl", "whole"),),
    "otel_logs": (("otel_logs.jsonl", "body"), ("other.jsonl", "otlp:resourceLogs")),
    "otel_metrics": (
        ("otel_metrics.jsonl", "body"),
        ("other.jsonl", "otlp:resourceMetrics"),
    ),
    "otel_traces": (
        ("otel_traces.jsonl", "body"),
        ("other.jsonl", "otlp:resourceSpans"),
    ),
    "hooks": (("hooks.jsonl", "hook_sink"), ("hooks_local.jsonl", "hook_local")),
    "rollout": (("rollout.jsonl", "whole"),),
}

# The five columns the brief asks for, in order. otel_traces is measured too and
# reported under the table rather than in it.
COLUMNS = ("exec_json", "otel_logs", "otel_metrics", "hooks", "rollout")

# (fact, key probes, event probes). A key probe matches a PATH SUFFIX on a dot boundary,
# and the ambiguous ones are anchored to a parent. That is not fussiness. The first
# version of this file probed for `path`, `status` and `kind` by bare name; every one
# matched something unrelated (`path` matched the receiver's own request path, `status`
# the delivery status this experiment invented, `kind` a sandbox policy discriminant),
# and three cells read `observed` for facts nothing had observed. Duplicate is not one.
FACTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "session start",
        ("session_id", "conversation.id"),
        (
            "thread.started",
            "SessionStart",
            "codex.conversation_starts",
            "session_meta",
            "codex.thread.started",
        ),
        (),
    ),
    (
        "session end",
        ("completed_at",),
        (
            "turn.completed",
            "turn.failed",
            "SessionEnd",
            "event_msg/task_complete",
            "codex.turn.e2e_duration_ms",
        ),
        (),
    ),
    ("model", ("model",), (), ()),
    ("effort", ("effort", "reasoning_effort"), (), ()),
    ("sandbox policy", ("sandbox_policy", "sandbox_mode", "permission_mode"), (), ()),
    ("tokens: input", ("input_tokens",), (), ("token_type=input",)),
    (
        "tokens: cached input",
        ("cached_input_tokens",),
        (),
        ("token_type=cached_input",),
    ),
    ("tokens: output", ("output_tokens",), (), ("token_type=output",)),
    (
        "tokens: reasoning output",
        ("reasoning_output_tokens",),
        (),
        ("token_type=reasoning_output",),
    ),
    ("tokens: total", ("total_tokens",), (), ("token_type=total",)),
    ("model_context_window", ("model_context_window",), (), ()),
    (
        "command text",
        ("item.command", "tool_input.command", "parsed_cmd", "input.command"),
        (),
        (),
    ),
    ("command exit code", ("item.exit_code", "exit_code"), (), ()),
    ("command status", ("item.status", "tool_response.status"), (), ()),
    ("command output", ("aggregated_output", "tool_response.output"), (), ()),
    ("file change path", ("item.path", "changes.path", "item.changes"), (), ()),
    ("file change kind", ("item.kind", "changes.kind"), (), ()),
    (
        "tool name (mcp)",
        ("item.tool", "item.server", "tool_name", "mcp_servers"),
        (),
        (),
    ),
    ("thread id", ("thread_id", "conversation.id", "session_id"), (), ()),
    ("cwd", ("cwd", "workspace_roots"), (), ()),
    ("cli version", ("cli_version", "app.version", "service.version"), (), ()),
)


def read_rows(path: Path) -> list[object]:
    if not path.exists():
        return []
    rows: list[object] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def unwrap_attr(value: object) -> object:
    """OTLP wraps every value: {"stringValue": "x"} and {"intValue": "3"}."""
    if isinstance(value, dict) and len(value) == 1:
        return next(iter(value.values()))
    return value


@dataclass
class Index:
    """Every JSON path seen on one surface, and which of them held nothing.

    A flat list rather than a dict keyed by name, because a probe is a path SUFFIX and a
    name-keyed store can only answer name questions.
    """

    paths: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    events: set[str] = field(default_factory=set)
    values: set[str] = field(default_factory=set)

    def note(self, where: str, value: object) -> None:
        self.paths.append(where)
        if value is None or value == "" or value == {} or value == []:
            self.empty.append(where)


def index_attribute(node: dict[str, object], path: str, idx: Index) -> None:
    """The OTLP `{key, value}` pair, whose name a plain walk never sees as a key."""
    attr_key = node.get("key")
    if not isinstance(attr_key, str) or "value" not in node:
        return
    value = unwrap_attr(node.get("value"))
    idx.note(f"{path}.{attr_key}" if path else attr_key, value)
    if attr_key == "event.name" and isinstance(value, str):
        idx.events.add(value)
    if isinstance(value, str):
        # A metric dimension carries the fact. `codex.turn.token_usage` is ONE
        # histogram with a `token_type` label, so "are output tokens on this surface"
        # is a question about a VALUE, and a key-name probe answers it wrongly.
        idx.values.add(f"{attr_key}={value}")


def index_member(
    node: dict[str, object], key: str, value: object, here: str, idx: Index
) -> None:
    idx.note(here, value)
    if key in {"type", "hook_event_name", "name"} and isinstance(value, str):
        idx.events.add(value)
    kind = node.get("type")
    if key == "payload" and isinstance(value, dict) and isinstance(kind, str):
        sub = value.get("type")
        idx.events.add(f"{kind}/{sub}" if isinstance(sub, str) else kind)


def index(node: object, path: str, idx: Index) -> None:
    """Record every leaf key with the JSON path it was found at, plus event names."""
    if isinstance(node, list):
        for i, item in enumerate(node):
            index(item, f"{path}[{i}]" if path else f"[{i}]", idx)
        return
    if not isinstance(node, dict):
        return
    index_attribute(node, path, idx)
    for key, value in node.items():
        here = f"{path}.{key}" if path else key
        index_member(node, key, value, here, idx)
        index(value, here, idx)


def dig(row: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(row, dict):
            return None
        row = row.get(key)
    return row


UNWRAP: dict[str, tuple[str, ...]] = {
    "whole": (),
    "body": ("body_json",),
    "hook_sink": ("body_json", "payload"),
    "hook_local": ("payload",),
}


def extract_one(row: object, extractor: str) -> object:
    if extractor.startswith("otlp:"):
        body = dig(row, "body_json")
        wanted = extractor.split(":", 1)[1]
        return body if isinstance(body, dict) and wanted in body else None
    if extractor not in UNWRAP:
        raise ValueError(f"unknown extractor {extractor!r}")
    return dig(row, *UNWRAP[extractor])


def signal_rows(path: Path, extractor: str) -> list[object]:
    """Unwrap each line down to what Codex itself emitted."""
    extracted = (extract_one(row, extractor) for row in read_rows(path))
    return [row for row in extracted if row is not None]


def surface_index(scenarios: list[str]) -> dict[str, Index]:
    built: dict[str, Index] = {}
    for surface, sources in SURFACES.items():
        idx = Index()
        for scenario in scenarios:
            for filename, extractor in sources:
                for row in signal_rows(OUT_ROOT / scenario / filename, extractor):
                    index(row, "", idx)
        built[surface] = idx
    return built


def normalize(path: str) -> str:
    """Drop list indices so `attributes[3].model` and `attributes[7].model` are one."""
    out: list[str] = []
    depth = 0
    for char in path:
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif depth == 0:
            out.append(char)
    return "".join(out)


def matches(probe: str, path: str) -> bool:
    """A probe is a path suffix. A leading dot demands a boundary; a bare name is exact
    at the root or a suffix after a dot."""
    normalized = normalize(path)
    if probe.startswith("."):
        return normalized.endswith(probe)
    return normalized == probe or normalized.endswith("." + probe)


def event_hit(idx: Index, event_probes: tuple[str, ...]) -> dict[str, object] | None:
    for probe in event_probes:
        if probe in idx.events:
            return {"verdict": "observed", "field": f"event {probe}"}
    return None


def value_hit(idx: Index, value_probes: tuple[str, ...]) -> dict[str, object] | None:
    for probe in value_probes:
        if probe in idx.values:
            # A histogram bucketed by a label is not the same as a plain per-turn
            # count: the value is in `sum` and the buckets are lossy, so the honest
            # verdict is partial.
            return {"verdict": "partial", "field": f"metric label {probe}"}
    return None


def path_hit(idx: Index, probes: tuple[str, ...]) -> dict[str, object] | None:
    for probe in probes:
        hits = [p for p in idx.paths if matches(probe, p)]
        if not hits:
            continue
        # A path that only ever held null is not evidence that the fact is there.
        empties = [p for p in idx.empty if matches(probe, p)]
        verdict = "partial" if len(empties) >= len(hits) else "observed"
        return {"verdict": verdict, "field": shortest(sorted(set(hits)))}
    return None


def cell(
    idx: Index,
    probes: tuple[str, ...],
    event_probes: tuple[str, ...],
    value_probes: tuple[str, ...] = (),
) -> dict[str, object]:
    found = (
        event_hit(idx, event_probes)
        or value_hit(idx, value_probes)
        or path_hit(idx, probes)
    )
    return found or {"verdict": "unavailable", "field": ""}


def shortest(paths: list[str]) -> str:
    return min(paths, key=len)


def build(scenarios: list[str]) -> dict[str, object]:
    built = surface_index(scenarios)
    rows: dict[str, dict[str, object]] = {}
    for fact, probes, event_probes, value_probes in FACTS:
        rows[fact] = {
            surface: cell(built[surface], probes, event_probes, value_probes)
            for surface in (*COLUMNS, "otel_traces")
        }
    sizes = {
        surface: {
            "paths_indexed": len(idx.paths),
            "distinct_events": sorted(idx.events),
        }
        for surface, idx in built.items()
    }
    return {"scenarios": scenarios, "rows": rows, "surface_sizes": sizes}


def markdown(matrix: dict[str, object]) -> str:
    rows = matrix["rows"]
    assert isinstance(rows, dict)
    out = ["| fact | " + " | ".join(COLUMNS) + " |", "|---" * (len(COLUMNS) + 1) + "|"]
    for fact, cells in rows.items():
        assert isinstance(cells, dict)
        rendered = []
        for surface in COLUMNS:
            item = cells[surface]
            assert isinstance(item, dict)
            field = f" `{item['field']}`" if item["field"] else ""
            rendered.append(f"{item['verdict']}{field}")
        out.append(f"| {fact} | " + " | ".join(rendered) + " |")
    return "\n".join(out)


def unlabelled_scenarios() -> list[str]:
    names: list[str] = []
    for path in sorted(OUT_ROOT.iterdir()):
        meta = path / "meta.json"
        if not meta.exists():
            continue
        if json.loads(meta.read_text(encoding="utf-8")).get("label"):
            continue
        names.append(path.name)
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument(
        "--scenarios",
        nargs="*",
        help="default: every unlabelled out/<S>/meta.json. A labelled run is a "
        "rehearsal against a Codex that could not reach the model and is named "
        "explicitly or not at all.",
    )
    args = parser.parse_args()
    scenarios = args.scenarios or unlabelled_scenarios()
    matrix = build(scenarios)
    (OUT_ROOT / "matrix.json").write_text(
        json.dumps(matrix, indent=2) + "\n", encoding="utf-8"
    )
    print(
        markdown(matrix)
        if args.markdown
        else json.dumps(matrix["surface_sizes"], indent=2)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
