"""Build the E01 observability matrix and drift record from the raw capture in out/.

The matrix answers one question per row: which surfaces carry this fact, and under what
field name. A cell is not an opinion. Each cell DECLARES the field that would carry the
fact, and the declared field is then looked up in an index built from every request the
sink actually received. A declared field that is not in the index is reported as
unavailable and says so, which is why a stale declaration cannot quietly become a claim.

Field naming, one scheme per surface, so a cell names something a reader can grep for:
    otel_logs    <event name>.<attribute>, plus the keys inside the JSON-encoded
                 tool_input and tool_parameters attributes
    otel_metrics <metric name>, or <metric name>.<data point attribute>
    hooks        <HookEventName>.<dotted path in the hook body>
    stream       <type>[:<subtype>].<dotted path in the message>

Usage:
    uv run python experiments/E01/matrix.py            # matrix, then the drift record
    uv run python experiments/E01/matrix.py --index    # every key the capture contains
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

E01_DIR = Path(__file__).resolve().parent
OUT_ROOT = E01_DIR / "out"

SURFACES = ("otel_logs", "otel_metrics", "hooks", "stream")

# Attributes whose value is itself a JSON document. Their keys are indexed one level
# deeper so a cell can name tool_input.file_path rather than tool_input.
JSON_ATTRS = ("tool_input", "tool_parameters")

OBSERVED = "observed"
PARTIAL = "partial"
UNAVAILABLE = "unavailable"


def scenario_dirs() -> list[Path]:
    return sorted(p for p in OUT_ROOT.glob("S*") if p.is_dir())


def jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def attr_map(attributes: list[dict[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for attribute in attributes or []:
        value = attribute.get("value") or {}
        out[attribute["key"]] = next(iter(value.values())) if value else None
    return out


def flatten(value: Any, prefix: str = "") -> Iterator[str]:
    """Dotted key paths. A list contributes its element paths under the same prefix."""
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield child
            yield from flatten(item, child)
    elif isinstance(value, list):
        for item in value:
            yield from flatten(item, prefix)


def log_records(scen: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    for request in jsonl(scen / "otel_logs.jsonl"):
        for resource in request["body_json"]["resourceLogs"]:
            resource_attrs = attr_map(resource["resource"]["attributes"])
            for scope in resource["scopeLogs"]:
                for record in scope["logRecords"]:
                    name = record["body"]["stringValue"]
                    yield name, {**resource_attrs, **attr_map(record.get("attributes"))}


def scope_points(
    scopes: list[dict[str, Any]], resource_attrs: dict[str, Any]
) -> Iterator[tuple[str, dict[str, Any]]]:
    for scope in scopes:
        for metric in scope["metrics"]:
            kind = next(k for k in metric if k in ("sum", "gauge", "histogram"))
            for point in metric[kind]["dataPoints"]:
                merged = {**resource_attrs, **attr_map(point.get("attributes"))}
                yield metric["name"], merged


def metric_points(scen: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    for request in jsonl(scen / "otel_metrics.jsonl"):
        for resource in request["body_json"]["resourceMetrics"]:
            attrs = attr_map(resource["resource"]["attributes"])
            yield from scope_points(resource["scopeMetrics"], attrs)


def hook_bodies(scen: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    for request in jsonl(scen / "hooks.jsonl"):
        body = request["body_json"]
        yield str(body.get("hook_event_name")), body


def stream_messages(scen: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    for message in jsonl(scen / "stream.jsonl"):
        kind = str(message.get("type"))
        subtype = message.get("subtype")
        yield (f"{kind}:{subtype}" if subtype else kind), message


def index_logs(scen: Path, keys: Counter[str], events: Counter[str]) -> None:
    for name, attrs in log_records(scen):
        events[name] += 1
        for key, value in attrs.items():
            keys[f"{name}.{key}"] += 1
            if key in JSON_ATTRS and isinstance(value, str):
                for inner in flatten(json.loads(value)):
                    keys[f"{name}.{key}.{inner}"] += 1


def index_metrics(scen: Path, keys: Counter[str], events: Counter[str]) -> None:
    for name, attrs in metric_points(scen):
        events[name] += 1
        keys[name] += 1
        for key in attrs:
            keys[f"{name}.{key}"] += 1


def index_flat(
    pairs: Iterator[tuple[str, dict[str, Any]]],
    keys: Counter[str],
    events: Counter[str],
) -> None:
    for name, body in pairs:
        events[name] += 1
        for path in flatten(body):
            keys[f"{name}.{path}"] += 1


def build_index() -> tuple[dict[str, Counter[str]], dict[str, Counter[str]]]:
    keys = {surface: Counter[str]() for surface in SURFACES}
    events = {surface: Counter[str]() for surface in SURFACES}
    for scen in scenario_dirs():
        index_logs(scen, keys["otel_logs"], events["otel_logs"])
        index_metrics(scen, keys["otel_metrics"], events["otel_metrics"])
        index_flat(hook_bodies(scen), keys["hooks"], events["hooks"])
        index_flat(stream_messages(scen), keys["stream"], events["stream"])
    return keys, events


# Each cell: (status, declared field or None, note or None). The status is what the
# capture supports, and the field is checked against the index before it is printed.
Cell = tuple[str, str | None, str | None]

MATRIX: list[tuple[str, dict[str, Cell]]] = [
    (
        "session start and end",
        {
            "otel_logs": (
                PARTIAL,
                "claude_code.api_request.session.id",
                "no session start or end event; session.id is on every record and "
                "event.timestamp bounds the session",
            ),
            "otel_metrics": (
                PARTIAL,
                "claude_code.session.count.start_type",
                "start only, with start_type fresh or resume; no end",
            ),
            "hooks": (
                PARTIAL,
                "SessionEnd.reason",
                "SessionEnd arrives; the SessionStart http hook is registered and "
                "never fires (see E01.md)",
            ),
            "stream": (
                OBSERVED,
                "system:init.session_id",
                "and result:success ends it",
            ),
        },
    ),
    (
        "model id",
        {
            "otel_logs": (OBSERVED, "claude_code.api_request.model", None),
            "otel_metrics": (OBSERVED, "claude_code.token.usage.model", None),
            "hooks": (UNAVAILABLE, None, "no hook payload names the model"),
            "stream": (OBSERVED, "system:init.model", "and assistant.message.model"),
        },
    ),
    (
        "per-request tokens by type",
        {
            "otel_logs": (
                OBSERVED,
                "claude_code.api_request.cache_read_tokens",
                "with input_tokens, output_tokens, cache_creation_tokens",
            ),
            "otel_metrics": (
                PARTIAL,
                "claude_code.token.usage.type",
                "a counter per type, model and query_source; no request id, so it "
                "cannot be split per request",
            ),
            "hooks": (UNAVAILABLE, None, "no hook payload carries token counts"),
            "stream": (
                OBSERVED,
                "assistant.message.usage.cache_read_input_tokens",
                "per assistant message, with cache_creation split 5m and 1h",
            ),
        },
    ),
    (
        "cost",
        {
            "otel_logs": (
                OBSERVED,
                "claude_code.api_request.cost_usd",
                "also cost_usd_micros",
            ),
            "otel_metrics": (OBSERVED, "claude_code.cost.usage", "counter, USD"),
            "hooks": (UNAVAILABLE, None, "no hook payload carries cost"),
            "stream": (
                PARTIAL,
                "result:success.total_cost_usd",
                "session total and per model in modelUsage; never per request",
            ),
        },
    ),
    (
        "tool name",
        {
            "otel_logs": (OBSERVED, "claude_code.tool_result.tool_name", None),
            "otel_metrics": (
                PARTIAL,
                "claude_code.code_edit_tool.decision.tool_name",
                "edit tools only, and only as a decision counter",
            ),
            "hooks": (OBSERVED, "PreToolUse.tool_name", "and PostToolUse.tool_name"),
            "stream": (
                OBSERVED,
                "assistant.message.content.name",
                "on tool_use blocks",
            ),
        },
    ),
    (
        "tool input size",
        {
            "otel_logs": (
                OBSERVED,
                "claude_code.tool_result.tool_input_size_bytes",
                "with tool_result_size_bytes beside it",
            ),
            "otel_metrics": (UNAVAILABLE, None, None),
            "hooks": (
                PARTIAL,
                "PreToolUse.tool_input",
                "the input is carried in full; its size is not stated",
            ),
            "stream": (
                PARTIAL,
                "assistant.message.content.input",
                "the input is carried in full; its size is not stated",
            ),
        },
    ),
    (
        "file path for Read, Edit, Write",
        {
            "otel_logs": (
                OBSERVED,
                "claude_code.tool_result.tool_input.file_path",
                "inside the tool_input JSON; tool_parameters is null for these tools",
            ),
            "otel_metrics": (UNAVAILABLE, None, None),
            "hooks": (OBSERVED, "PostToolUse.tool_input.file_path", None),
            "stream": (
                OBSERVED,
                "assistant.message.content.input.file_path",
                "on tool_use blocks",
            ),
        },
    ),
    (
        "bash command",
        {
            "otel_logs": (
                OBSERVED,
                "claude_code.tool_result.tool_parameters.full_command",
                "with bash_command, the first word",
            ),
            "otel_metrics": (UNAVAILABLE, None, None),
            "hooks": (OBSERVED, "PreToolUse.tool_input.command", None),
            "stream": (OBSERVED, "assistant.message.content.input.command", None),
        },
    ),
    (
        "exit code",
        {
            "otel_logs": (
                UNAVAILABLE,
                None,
                "success and error_type only; no exit status anywhere",
            ),
            "otel_metrics": (UNAVAILABLE, None, None),
            "hooks": (
                UNAVAILABLE,
                None,
                "tool_response carries stdout, stderr and interrupted, no exit code",
            ),
            "stream": (
                PARTIAL,
                "user.tool_use_result",
                "on failure the text begins 'Exit code N'; nothing structured, and "
                "nothing at all on success",
            ),
        },
    ),
    (
        "tool success",
        {
            "otel_logs": (
                OBSERVED,
                "claude_code.tool_result.success",
                "with error_type and error on failure",
            ),
            "otel_metrics": (UNAVAILABLE, None, None),
            "hooks": (
                PARTIAL,
                "PostToolUseFailure.error",
                "no success field: the event name is the signal, PostToolUse against "
                "PostToolUseFailure",
            ),
            "stream": (
                PARTIAL,
                "user.message.content.is_error",
                "present only on a failed tool_result block, so success is an absence",
            ),
        },
    ),
    (
        "tool duration",
        {
            "otel_logs": (OBSERVED, "claude_code.tool_result.duration_ms", None),
            "otel_metrics": (UNAVAILABLE, None, None),
            "hooks": (
                OBSERVED,
                "PostToolUse.duration_ms",
                "and PostToolUseFailure.duration_ms",
            ),
            "stream": (
                UNAVAILABLE,
                None,
                "no per-tool timing; only session totals on the result message",
            ),
        },
    ),
    (
        "subagent parent-child linkage",
        {
            "otel_logs": (
                PARTIAL,
                "claude_code.subagent_completed.agent_type",
                "the child is described (model, total_tokens, total_tool_uses, "
                "duration_ms) but carries no agent_id and no parent tool_use_id",
            ),
            "otel_metrics": (UNAVAILABLE, None, None),
            "hooks": (
                PARTIAL,
                "SubagentStart.agent_id",
                "agent_id links start to stop; the spawning tool_use_id is absent",
            ),
            "stream": (
                OBSERVED,
                "assistant.parent_tool_use_id",
                "with system:task_started.tool_use_id and subagent_type",
            ),
        },
    ),
    (
        "compaction trigger and pre/post tokens",
        {
            "otel_logs": (
                OBSERVED,
                "claude_code.compaction.pre_tokens",
                "with trigger, post_tokens, duration_ms, success; post_tokens is "
                "absent when success is false",
            ),
            "otel_metrics": (UNAVAILABLE, None, None),
            "hooks": (
                PARTIAL,
                "PreCompact.trigger",
                "trigger only, on PreCompact and PostCompact; no token counts",
            ),
            "stream": (
                OBSERVED,
                "system:compact_boundary.compact_metadata.pre_tokens",
                "with post_tokens, trigger, cumulative_dropped_tokens, duration_ms",
            ),
        },
    ),
    (
        "git commit id",
        {
            "otel_logs": (
                OBSERVED,
                "claude_code.tool_result.tool_parameters.git_commit_id",
                "short sha, only on the Bash call whose commit succeeded",
            ),
            "otel_metrics": (
                PARTIAL,
                "claude_code.commit.count",
                "a count of commits, never an id",
            ),
            "hooks": (
                OBSERVED,
                "PostToolUse.tool_response.gitOperation.commit.sha",
                "with kind and branch",
            ),
            "stream": (
                OBSERVED,
                "user.tool_use_result.gitOperation.commit.sha",
                "with kind and branch",
            ),
        },
    ),
    (
        "permission mode",
        {
            "otel_logs": (
                UNAVAILABLE,
                None,
                "no attribute carries it; no permission_mode_changed event was seen",
            ),
            "otel_metrics": (UNAVAILABLE, None, None),
            "hooks": (
                OBSERVED,
                "PreToolUse.permission_mode",
                "on every tool and stop hook",
            ),
            "stream": (OBSERVED, "system:init.permissionMode", None),
        },
    ),
    (
        "context window size",
        {
            "otel_logs": (UNAVAILABLE, None, None),
            "otel_metrics": (UNAVAILABLE, None, None),
            "hooks": (UNAVAILABLE, None, None),
            "stream": (
                OBSERVED,
                "result:success.modelUsage.claude-sonnet-5.contextWindow",
                "per model, on the result message only",
            ),
        },
    ),
    (
        "user prompt length",
        {
            "otel_logs": (
                OBSERVED,
                "claude_code.user_prompt.prompt_length",
                "the prompt attribute exists and reads <REDACTED>",
            ),
            "otel_metrics": (UNAVAILABLE, None, None),
            "hooks": (
                UNAVAILABLE,
                None,
                "not measured here: UserPromptSubmit was not one of the declared http "
                "hooks, and it is the hook that would carry it",
            ),
            "stream": (
                OBSERVED,
                "user.message.content",
                "the prompt text itself, in full, on the first user message",
            ),
        },
    ),
    (
        "claude version",
        {
            "otel_logs": (
                OBSERVED,
                "claude_code.api_request.service.version",
                "resource attribute on every record, with service.name claude-code",
            ),
            "otel_metrics": (
                OBSERVED,
                "claude_code.session.count.service.version",
                "same resource attribute",
            ),
            "hooks": (UNAVAILABLE, None, "no hook payload names the version"),
            "stream": (OBSERVED, "system:init.claude_code_version", None),
        },
    ),
]

# What docs/design/00-digest.md section 2.1 says this version emits. The drift record is
# the difference between these lists and what the capture contains.
DIGEST_LOG_EVENTS = {
    "claude_code.api_request",
    "claude_code.api_error",
    "claude_code.api_refusal",
    "claude_code.tool_result",
    "claude_code.tool_decision",
    "claude_code.user_prompt",
    "claude_code.assistant_response",
    "claude_code.permission_mode_changed",
    "claude_code.auth",
    "claude_code.mcp_server_connection",
}
DIGEST_METRICS = {
    "claude_code.session.count",
    "claude_code.token.usage",
    "claude_code.cost.usage",
    "claude_code.lines_of_code.count",
    "claude_code.commit.count",
    "claude_code.pull_request.count",
    "claude_code.active_time.total",
}
DIGEST_HOOK_EVENTS = {
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PostToolBatch",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "PreCompact",
    "PostCompact",
    "PreModelSwitch",
    "PostModelSwitch",
    "WorktreeCreate",
    "WorktreeRemove",
}
DIGEST_STREAM_TYPES = {
    "system:init",
    "assistant",
    "user",
    "system:compact_boundary",
    "system:api_retry",
    "result:success",
    "result:error",
}
# The http hooks run.py declares per scenario. An event outside this set was never
# asked for, so its absence from the capture says nothing about the version.
DECLARED_HOOKS = {
    "SessionStart",
    "SessionEnd",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PreCompact",
    "PostCompact",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "PostModelSwitch",
}

DIGEST = {
    "otel_logs": DIGEST_LOG_EVENTS,
    "otel_metrics": DIGEST_METRICS,
    "hooks": DIGEST_HOOK_EVENTS,
    "stream": DIGEST_STREAM_TYPES,
}


def cell_text(cell: Cell, present: bool) -> str:
    status, field, note = cell
    if field is not None and not present:
        return f"unavailable (declared {field} is not in the capture)"
    parts = [status]
    if field:
        parts.append(f"`{field}`")
    text = ": ".join(parts) if field else status
    return f"{text} ({note})" if note else text


def render_matrix(keys: dict[str, Counter[str]]) -> str:
    lines = [
        "| fact | " + " | ".join(SURFACES) + " |",
        "|---|" + "---|" * len(SURFACES),
    ]
    for fact, cells in MATRIX:
        rendered = []
        for surface in SURFACES:
            cell = cells[surface]
            rendered.append(
                cell_text(cell, cell[1] in keys[surface] if cell[1] else True)
            )
        lines.append(f"| {fact} | " + " | ".join(rendered) + " |")
    return "\n".join(lines)


def hook_absence(missing: set[str]) -> list[str]:
    """Split a hook absence into the two statements it actually contains.

    A declared hook that never arrived is a fact about this version. A hook this
    capture never declared is a fact about this capture, and calling both "not seen"
    would turn the second into evidence it is not.
    """
    silent = sorted(missing & DECLARED_HOOKS)
    undeclared = sorted(missing - DECLARED_HOOKS)
    return [
        "declared here and never arrived: " + (", ".join(silent) if silent else "none"),
        "not declared in this capture, so not measured: "
        + (", ".join(undeclared) if undeclared else "none"),
    ]


def render_drift(events: dict[str, Counter[str]]) -> str:
    lines = []
    for surface in SURFACES:
        seen = set(events[surface])
        documented = DIGEST[surface]
        new = sorted(seen - documented)
        missing = documented - seen
        lines.append(f"\n### {surface}")
        lines.append(f"seen ({len(seen)}): " + ", ".join(sorted(seen)))
        lines.append("not in the digest: " + (", ".join(new) if new else "none"))
        if surface == "hooks":
            lines.extend(hook_absence(missing))
        else:
            lines.append(
                "documented and not seen: "
                + (", ".join(sorted(missing)) if missing else "none")
            )
    return "\n".join(lines)


def render_counts(events: dict[str, Counter[str]]) -> str:
    lines = ["| surface | name | observations |", "|---|---|---|"]
    for surface in SURFACES:
        for name, count in sorted(events[surface].items()):
            lines.append(f"| {surface} | {name} | {count} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="E01 observability matrix")
    parser.add_argument(
        "--index", action="store_true", help="print every key the capture contains"
    )
    args = parser.parse_args()
    keys, events = build_index()
    if args.index:
        for surface in SURFACES:
            for key, count in sorted(keys[surface].items()):
                print(f"{surface}\t{count}\t{key}")
        return 0
    print("## Observability matrix\n")
    print(render_matrix(keys))
    print("\n## Observation counts by event name\n")
    print(render_counts(events))
    print("\n## Drift against docs/design/00-digest.md section 2.1")
    print(render_drift(events))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
