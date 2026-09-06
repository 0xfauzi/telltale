"""W7-T1 step (2): does a rollout-derived request duration agree with the OTel one?

Decision rule, fixed before the run (brief W7-T1): adopt the rollout derivation only if
at least 90 percent of the paired responses (S1, S3 and S6 pooled) agree within 10
percent relative. Otherwise the rollout duration stays None and the rollout cell of the
`request_duration` capability is `unavailable`.

The OTel duration is codex.sse_event response.completed event.timestamp minus the
codex.websocket_request event.timestamp paired with it by position. The rollout duration
is the last response_item of the response minus the last tool output, user_message or
task_started before the response's first response_item.

Reads fixtures/sources/codex/0.150.1 only. Writes nothing.

    uv run python experiments/W7-T1/duration_probe.py
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime
from typing import Any

ROOT = pathlib.Path("fixtures/sources/codex/0.150.1")
FIXTURES = ("S1", "S3", "S6")
MODEL_ITEMS = ("message", "function_call", "custom_tool_call", "local_shell_call")
START_ITEMS = ("custom_tool_call_output", "function_call_output")
START_EVENTS = ("user_message", "task_started")
WIDTHS = (8, 5, 24, 9, 10, 8)
TOLERANCE = 0.10
NEEDED = 0.90


def ms(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000


def attributes(record: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for a in record.get("attributes", []):
        value = a["value"]
        out[a["key"]] = next(iter(value.values())) if value else None
    return out


def otel(path: pathlib.Path) -> list[dict[str, Any]]:
    """Every OTel log record of one sink file, as its attributes."""
    out: list[dict[str, Any]] = []
    for line in path.open():
        body = json.loads(line)["body_json"]
        for resource in body.get("resourceLogs", []):
            for scope in resource.get("scopeLogs", []):
                out += [attributes(rec) for rec in scope.get("logRecords", [])]
    return out


def triple(a: dict[str, Any]) -> tuple[int, ...] | None:
    if "input_token_count" not in a:
        return None
    return (
        int(a["input_token_count"]),
        int(a["cached_token_count"]),
        int(a["output_token_count"]),
    )


def rows(scen: str) -> tuple[list[Any], list[Any], list[Any]]:
    d = ROOT / scen
    recs = otel(d / "otel_logs.jsonl")
    sse = [
        a
        for a in recs
        if a.get("event.name") == "codex.sse_event"
        and a.get("event.kind") == "response.completed"
    ]
    ws = [a for a in recs if a.get("event.name") == "codex.websocket_request"]
    roll = [json.loads(line) for line in (d / "rollout.jsonl").open()]
    return sse, ws, roll


def kind(rec: dict[str, Any]) -> tuple[str, str, Any]:
    payload = rec.get("payload") or {}
    return rec.get("type", ""), payload.get("type", ""), payload.get("role")


def is_model_item(rec: dict[str, Any]) -> bool:
    outer, inner, role = kind(rec)
    return (
        outer == "response_item"
        and inner in MODEL_ITEMS
        and role in (None, "assistant")
    )


def is_start_marker(rec: dict[str, Any]) -> bool:
    outer, inner = kind(rec)[:2]
    return (outer == "response_item" and inner in START_ITEMS) or (
        outer == "event_msg" and inner in START_EVENTS
    )


def usage_triple(rec: dict[str, Any]) -> tuple[int, ...] | None:
    usage = ((rec.get("payload") or {}).get("info") or {}).get("last_token_usage")
    if usage is None:
        return None
    return (
        usage["input_tokens"],
        usage["cached_input_tokens"],
        usage["output_tokens"],
    )


def span(
    roll: list[dict[str, Any]], window: list[dict[str, Any]], at: int
) -> float | None:
    """The rollout's own span for the response that ends at token_count line `at`."""
    model = [rec for rec in window if is_model_item(rec)]
    if not model:
        return None
    first, last = ms(model[0]["timestamp"]), ms(model[-1]["timestamp"])
    starts = [
        ms(rec["timestamp"])
        for rec in roll[:at]
        if ms(rec["timestamp"]) < first and is_start_marker(rec)
    ]
    return last - max(starts) if starts else None


def rollout_durations(
    roll: list[dict[str, Any]],
) -> list[tuple[int, float | None, tuple[int, ...] | None]]:
    """Per token_count line: (line number, rollout duration in ms, usage triple)."""
    out = []
    window_start = 0
    for index, rec in enumerate(roll):
        if kind(rec)[:2] != ("event_msg", "token_count"):
            continue
        window = roll[window_start:index]
        out.append((index + 1, span(roll, window, index), usage_triple(rec)))
        window_start = index + 1
    return out


def show(*cells: object) -> None:
    print(" ".join(f"{cell!s:>{w}}" for cell, w in zip(cells, WIDTHS, strict=True)))


def one_fixture(scen: str) -> tuple[int, int]:
    """(paired responses, those that agree within the tolerance) for one fixture."""
    sse, ws, roll = rows(scen)
    assert len(sse) == len(ws), (scen, len(sse), len(ws))
    reference = {
        id(a): ms(a["event.timestamp"]) - ms(w["event.timestamp"])
        for w, a in zip(ws, sse, strict=True)
    }
    total = agree = 0
    pointer = 0
    for line, dur, trip in rollout_durations(roll):
        if trip is None:
            show(scen, line, "(no usage)", "-", "-", "-")
            continue
        found = next(
            (j for j in range(pointer, len(sse)) if triple(sse[j]) == trip), None
        )
        if found is None:
            show(scen, line, trip, "NO TWIN", "-", "-")
            continue
        pointer, total = found + 1, total + 1
        otel_ms = reference[id(sse[found])]
        if dur is None:
            show(scen, line, trip, f"{otel_ms:.0f}", "None", "-")
            continue
        rel = abs(dur - otel_ms) / otel_ms
        agree += rel <= TOLERANCE
        show(scen, line, trip, f"{otel_ms:.0f}", f"{dur:.0f}", f"{rel:.3f}")
    return total, agree


def main() -> None:
    show("fixture", "line", "usage triple", "otel_ms", "rollout_ms", "rel")
    counted = [one_fixture(scen) for scen in FIXTURES]
    total = sum(pair[0] for pair in counted)
    agree = sum(pair[1] for pair in counted)
    print()
    print(
        f"paired responses n = {total}; agree within {TOLERANCE:.0%} = {agree}"
        f" ({agree / total:.1%}); rule needs {NEEDED:.0%}"
    )
    print("VERDICT:", "adopt" if agree / total >= NEEDED else "reject")


def nearest_check() -> None:
    """Pairing by position against pairing by nearest preceding send. Same records."""
    for scen in FIXTURES:
        sse, ws, _ = rows(scen)
        by_index = [
            ms(a["event.timestamp"]) - ms(w["event.timestamp"])
            for w, a in zip(ws, sse, strict=True)
        ]
        sends = [ms(w["event.timestamp"]) for w in ws]
        nearest = [
            ms(a["event.timestamp"])
            - max(s for s in sends if s <= ms(a["event.timestamp"]))
            for a in sse
        ]
        same = by_index == nearest
        print(scen, "n", len(sse), "position == nearest preceding:", same)
        print("   ", [f"{v:.0f}" for v in by_index])


main()
nearest_check()
