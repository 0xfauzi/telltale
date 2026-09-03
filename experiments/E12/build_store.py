"""Build the E12 material store: every archived raw stream captured through the real
launcher into experiments/E12/out/store at the classifier version of this checkout.

Why not the owner's store: a rebuild there reaches 19 of the 51 pytest runs in these six
sessions, because the raw command line was never stored and W4-T3's newline and heredoc
rules need it; a launcher capture of the archived stream reaches 49 (docs/log/W4-T3.md,
reproduced by the orchestrator on 2026-09-03). Writes `material_capture_id` into
manifest.json beside the original capture id.

Usage:
  uv run python experiments/E12/build_store.py
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORE = HERE / "out" / "store"


def _newest_capture() -> str:
    conn = sqlite3.connect(STORE / "telltale.db")
    try:
        row = conn.execute(
            "SELECT capture_id FROM captures ORDER BY first_ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return str(row[0])


def main() -> int:
    manifest_path = HERE / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if STORE.exists():
        shutil.rmtree(STORE)
    STORE.mkdir(parents=True)
    env = dict(os.environ, TELLTALE_HOME=str(STORE))
    for index, (label, entry) in enumerate(manifest["sessions"].items(), start=1):
        raw = HERE.parents[1] / entry["raw"]
        subprocess.run(
            ["telltale", "run", "--provider", "claude", "--task-id", f"E12-M{index}",
             "--attempt", "1", "--experiment", "E12", "--", sys.executable,
             str(HERE / "emit.py"), str(raw), "--output-format", "stream-json"],
            env=env, check=True, stdout=subprocess.DEVNULL,
        )  # fmt: skip
        entry["material_capture_id"] = _newest_capture()
        print(f"{label:<10} E12-M{index} {entry['material_capture_id']}")
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
