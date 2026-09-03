"""What each surface says the output token count of one session is. W4-T4 DO (1).

Four readings per session, none of them Telltale's own arithmetic:

  stream_snapshot  the sum over DISTINCT assistant message ids of
                   `message.usage.output_tokens` on the stream-json records. Distinct,
                   because every partial record of one message repeats one usage block
                   and summing the records would multiply the same number by how many
                   times the provider re-sent it.
  stream_result    `usage.output_tokens` on the session's single `result` record, and
                   the sum of `modelUsage[*].outputTokens` beside it.
  otel_sum         the OTel surface's own total, read from a `.backup` copy of a store
                   that has the same session captured with OTel configured: the sum of
                   `output_tokens` over that capture's model_request activities whose
                   usage_source is `primary`.
  transcript_sum   the same distinct-message sum over the provider's own transcript
                   file, `~/.claude/projects/<slug>/<session_id>.jsonl`, READ ONLY.

The transcript reading is the one that decides scope: if a transcript carries final
per-message counts the backfill importer is right and only the stream parse is wrong;
if it carries the same start-of-message snapshot, the importer is wrong the same way.

Usage:
  uv run python experiments/E12/usage_check.py [--store <a TELLTALE_HOME>]

Nothing here writes to any store or to ~/.claude.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TRANSCRIPTS = Path.home() / ".claude" / "projects"


def _records(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _per_message(records: list[dict[str, Any]], kind: str) -> dict[str, list[int]]:
    """usage.output_tokens per distinct message id, and every value seen for it."""
    seen: dict[str, list[int]] = {}
    for item in records:
        if item.get("type") != kind:
            continue
        inner = item.get("message")
        if not isinstance(inner, dict):
            continue
        usage = inner.get("usage")
        if not isinstance(usage, dict):
            continue
        value = usage.get("output_tokens")
        if not isinstance(value, int):
            continue
        seen.setdefault(str(inner.get("id")), []).append(value)
    return seen


def _model_usage(result: dict[str, Any] | None, key: str) -> int | None:
    """The sum of one modelUsage counter across models, or None if none states it."""
    models = (result or {}).get("modelUsage")
    if not isinstance(models, dict):
        return None
    values = [
        facts[key]
        for facts in models.values()
        if isinstance(facts, dict) and isinstance(facts.get(key), int)
    ]
    return sum(values) if values else None


def _shape(per: dict[str, list[int]]) -> dict[str, Any]:
    """The distinct-message reading of one usage counter."""
    firsts = [values[0] for values in per.values()]
    return {
        "assistant_records": sum(len(values) for values in per.values()),
        "message_ids": len(per),
        "one_usage_per_message": all(len(set(v)) == 1 for v in per.values()),
        "sum": sum(firsts),
        "min": min(firsts, default=None),
        "max": max(firsts, default=None),
    }


def _stream(path: Path) -> dict[str, Any]:
    records = _records(path)
    result = next((item for item in records if item.get("type") == "result"), None)
    usage = (result or {}).get("usage")
    return {
        **_shape(_per_message(records, "assistant")),
        "result_output_tokens": usage.get("output_tokens")
        if isinstance(usage, dict)
        else None,
        "model_usage_output_tokens": _model_usage(result, "outputTokens"),
        "model_usage_thinking_tokens": _model_usage(result, "thinkingTokens"),
    }


def _store_facts(db: Path, capture_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT fields FROM activities WHERE capture_id = ?"
            " AND activity_type = 'model_request'",
            (capture_id,),
        ).fetchall()
        session = conn.execute(
            "SELECT provider_session_id FROM observations WHERE capture_id = ?"
            " AND provider_session_id IS NOT NULL LIMIT 1",
            (capture_id,),
        ).fetchone()
        shown = conn.execute(
            "SELECT value, coverage FROM evidence WHERE capture_id = ?"
            " AND metric = 'output_tokens'",
            (capture_id,),
        ).fetchone()
    finally:
        conn.close()
    fields = [json.loads(row[0]) for row in rows]
    by_source: dict[str, list[int]] = {}
    for item in fields:
        value = item.get("output_tokens")
        if isinstance(value, int):
            by_source.setdefault(str(item.get("usage_source")), []).append(value)
    return {
        "requests": len(fields),
        "sums_by_usage_source": {k: sum(v) for k, v in sorted(by_source.items())},
        "provider_session_id": session[0] if session else None,
        "evidence_output_tokens": shown[0] if shown else None,
        "evidence_coverage": shown[1] if shown else None,
    }


def _transcript(session_id: str | None) -> dict[str, Any]:
    if session_id is None:
        return {"found": False}
    hits = sorted(TRANSCRIPTS.glob(f"*/{session_id}.jsonl"))
    if not hits:
        return {"found": False}
    per = _per_message(_records(hits[0]), "assistant")
    tail = f"{hits[0].parent.name}/{hits[0].name}"
    return {"found": True, "path_tail": tail, **_shape(per)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--store",
        type=Path,
        default=None,
        help="a TELLTALE_HOME holding telltale.db; a .backup copy, never the live one",
    )
    ap.add_argument("--material-store", type=Path, default=HERE / "out" / "store")
    args = ap.parse_args()

    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    out: dict[str, Any] = {}
    for label, entry in manifest["sessions"].items():
        raw = HERE.parents[1] / entry["raw"]
        row: dict[str, Any] = {"stream": _stream(raw)}
        if args.store is not None:
            row["otel_capture"] = _store_facts(
                args.store / "telltale.db", entry["capture_id"]
            )
            row["transcript"] = _transcript(row["otel_capture"]["provider_session_id"])
        material = args.material_store / "telltale.db"
        mid = entry.get("material_capture_id")
        if material.exists() and mid:
            row["material_capture"] = _store_facts(material, mid)
        out[label] = row
    print(json.dumps(out, indent=1))

    print("\n" + "-" * 100)
    head = f"{'session':<10} {'snapshot':>10} {'result':>10} {'modelUsage':>11}"
    head += f" {'otel':>10} {'transcript':>11} {'evidence(otel)':>15}"
    print(head)
    for label, row in out.items():
        stream = row["stream"]
        otel = row.get("otel_capture", {})
        tr = row.get("transcript", {})
        otel_sum = otel.get("sums_by_usage_source", {}).get("primary")
        line = f"{label:<10} {stream['sum']:>10}"
        line += f" {stream['result_output_tokens']!s:>10}"
        line += f" {stream['model_usage_output_tokens']!s:>11}"
        line += f" {otel_sum!s:>10}"
        line += f" {(tr.get('sum') if tr.get('found') else 'not found')!s:>11}"
        line += f" {otel.get('evidence_output_tokens')!s:>15}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
