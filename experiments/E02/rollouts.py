"""Find each scenario's rollout file and copy it beside the rest of that capture.

Rollouts are the one E02 surface Telltale does not configure: Codex writes them to
$CODEX_HOME/sessions whether or not anything is listening, which makes them the backfill
path and the only place some facts appear. This script reads them, never writes there.

Matching is by thread id, twice over. The id is in the FILENAME and again in the first
record's `session_meta` payload, so the filename is used to find the candidate and the
payload is used to confirm it. A file whose name matches but whose payload does not is
reported rather than copied: on a machine with 1400 rollouts, "close enough" is how one
session's evidence ends up filed under another's.

Usage:
  uv run python experiments/E02/rollouts.py --scenario S2
  uv run python experiments/E02/rollouts.py --all
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_ROOT = HERE / "out"
SESSIONS = Path.home() / ".codex" / "sessions"

# Fields the write-up quotes. Absence is recorded as absence: a fact missing on a failed
# turn and present on a successful one is what the matrix has to keep apart.
SESSION_META_FIELDS = (
    "session_id",
    "id",
    "timestamp",
    "cwd",
    "originator",
    "cli_version",
    "source",
    "thread_source",
    "model_provider",
    "history_mode",
    "context_window",
    "git",
)
TURN_CONTEXT_FIELDS = (
    "turn_id",
    "cwd",
    "workspace_roots",
    "current_date",
    "timezone",
    "approval_policy",
    "sandbox_policy",
    "model",
    "effort",
    "summary",
    "personality",
    "collaboration_mode",
)


def thread_id_of(scenario: str) -> str | None:
    meta = OUT_ROOT / scenario / "meta.json"
    if not meta.exists():
        return None
    value = (
        json.loads(meta.read_text(encoding="utf-8")).get("exec", {}).get("thread_id")
    )
    return str(value) if value else None


def candidates_for(thread: str) -> list[Path]:
    return sorted(SESSIONS.rglob(f"*{thread}*.jsonl"))


def read_rows(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def payload_of(row: dict[str, object]) -> dict[str, object]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


def record_kinds(rows: list[dict[str, object]]) -> dict[str, int]:
    kinds: Counter[str] = Counter()
    for row in rows:
        kind = str(row.get("type", "_untyped"))
        sub = payload_of(row).get("type")
        kinds[f"{kind}/{sub}" if isinstance(sub, str) else kind] += 1
    return dict(kinds)


def first_payload(
    rows: list[dict[str, object]], kind: str, sub: str | None = None
) -> dict[str, object]:
    for row in rows:
        payload = payload_of(row)
        if row.get("type") == kind and (sub is None or payload.get("type") == sub):
            return payload
    return {}


def field_report(
    payload: dict[str, object], fields: tuple[str, ...]
) -> dict[str, object]:
    """Present-with-a-value, present-but-null and absent are three answers."""
    report: dict[str, object] = {}
    for field in fields:
        if field not in payload:
            report[field] = "absent"
        elif payload[field] is None:
            report[field] = "present-null"
        else:
            report[field] = payload[field]
    return report


def token_report(rows: list[dict[str, object]]) -> dict[str, object]:
    """Every token_count record, because the LAST one is the session total."""
    records: list[dict[str, object]] = []
    for row in rows:
        payload = payload_of(row)
        if row.get("type") == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            records.append(
                {
                    "ordinal": row.get("ordinal"),
                    "info": info,
                    "model_context_window": (
                        info.get("model_context_window")
                        if isinstance(info, dict)
                        else None
                    ),
                    "rate_limits_present": payload.get("rate_limits") is not None,
                }
            )
    return {"count": len(records), "records": records}


def summarize(
    scenario: str, path: Path, rows: list[dict[str, object]]
) -> dict[str, object]:
    meta = first_payload(rows, "session_meta")
    return {
        "scenario": scenario,
        "source_path": str(path),
        "path_pattern": str(path.relative_to(SESSIONS.parent)),
        "size_bytes": path.stat().st_size,
        "record_count": len(rows),
        "record_kinds": record_kinds(rows),
        "session_meta": field_report(meta, SESSION_META_FIELDS),
        "session_meta_all_keys": sorted(meta),
        "turn_context": field_report(
            first_payload(rows, "turn_context"), TURN_CONTEXT_FIELDS
        ),
        "turn_context_all_keys": sorted(first_payload(rows, "turn_context")),
        "token_count": token_report(rows),
        "reasoning_records": sum(
            1
            for r in rows
            if "reasoning" in json.dumps(r.get("payload", {}))[:200].lower()
        ),
    }


def collect(scenario: str) -> dict[str, object]:
    out_dir = OUT_ROOT / scenario
    thread = thread_id_of(scenario)
    if thread is None:
        return {"scenario": scenario, "status": "no thread id in meta.json"}
    found = candidates_for(thread)
    if not found:
        # --ephemeral is supposed to produce exactly this, so it is a result and not
        # an error. The caller decides which it is.
        return {"scenario": scenario, "thread_id": thread, "status": "no rollout file"}
    if len(found) > 1:
        return {
            "scenario": scenario,
            "thread_id": thread,
            "status": "ambiguous",
            "candidates": [str(p) for p in found],
        }
    path = found[0]
    rows = read_rows(path)
    stated = first_payload(rows, "session_meta").get("id")
    if stated != thread:
        return {
            "scenario": scenario,
            "thread_id": thread,
            "status": f"filename matched but session_meta.id is {stated!r}",
            "source_path": str(path),
        }
    shutil.copy2(path, out_dir / "rollout.jsonl")
    summary = summarize(scenario, path, rows)
    summary["status"] = "copied"
    (out_dir / "rollout_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--scenario")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    names = (
        sorted(p.name for p in OUT_ROOT.iterdir() if (p / "meta.json").exists())
        if args.all
        else [args.scenario]
    )
    if not names or names == [None]:
        parser.error("pass --scenario NAME or --all")
    results = [collect(name) for name in names]
    for result in results:
        name = result["scenario"]
        print(f"{name}: {result.get('status')} {result.get('source_path', '')}")
    (OUT_ROOT / "rollouts.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
