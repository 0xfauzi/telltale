"""Per-capture facts E09's write-up quotes that the runner's probe.json does not carry.

`telltale experiment probe` writes the score, the evidence vector and the statistics.
Three things the E09 write-up needs are in the store and not in that file: the session's
`total_cost_usd`, which the stream result message carries and `telltale show` does not
render; `cache_creation_tokens`, which spec 13.7's `context_token_burden` does not name
and which the write-up prints beside the other three components; and the repository
paths and normalized commands of each session, which is how the contamination check (did
a session read its own answer key?) is answered from the capture rather than from an
assumption.

Only numbers and repository paths are written here. No agent text of any kind is read,
and none could be: the store does not hold any.

    uv run python experiments/E09/store_facts.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import config, measures
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

E09 = Path(__file__).resolve().parent
OUT = E09 / "out"
EXPERIMENT = "E09"
TASKS = ("E09-pilot-", "E09-P")
TOUCHED = ("file_read", "file_edit")
RAN = ("command", "verification_run")


def main() -> int:
    store = Store(config.home() / "telltale.db").open()
    try:
        found = _captures(store)
        _add_result(store, found)
        for capture_id, one in found.items():
            _add_capture(store, capture_id, one)
    finally:
        store.close()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "store_facts.json"
    path.write_text(
        json.dumps(found, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    total = sum(one.get("total_cost_usd") or 0.0 for one in found.values())
    print(f"{path}: {len(found)} captures")
    print(f"total_cost_usd over {len(found)} captures: {total:.6f}")
    return 0


def _captures(store: Store) -> dict[str, dict[str, Any]]:
    """Every capture this experiment's two suites started, by capture id."""
    return {
        str(row["capture_id"]): {
            "task_id": row["payload"].get("task_id"),
            "attempt": row["payload"].get("attempt"),
        }
        for row in store.observations_of_type("telltale.capture_started")
        if row["payload"].get("experiment") == EXPERIMENT
        and str(row["payload"].get("task_id", "")).startswith(TASKS)
    }


def _add_result(store: Store, found: Mapping[str, dict[str, Any]]) -> None:
    for row in store.observations_of_type("claude.stream.result"):
        one = found.get(str(row["capture_id"]))
        if one is not None:
            one["total_cost_usd"] = row["payload"].get("total_cost_usd")
            one["num_turns"] = row["payload"].get("num_turns")


def _add_capture(store: Store, capture_id: str, one: dict[str, Any]) -> None:
    rows = store.activities(capture_id)
    one["usage"] = dict(measures.summary(store, capture_id)["usage"])
    one["capture_ms"] = _span(store, capture_id)
    one["paths_touched"] = sorted(
        {
            str(row["fields"]["file_path"])
            for row in rows
            if row["activity_type"] in TOUCHED and row["fields"].get("file_path")
        }
    )
    one["commands"] = [
        str(row["fields"]["command_norm"])
        for row in rows
        if row["activity_type"] in RAN and row["fields"].get("command_norm")
    ]


def _span(store: Store, capture_id: str) -> int | None:
    """capture_started to capture_ended in milliseconds, on the arrival clock.

    Not the agent's own duration and not the runner's wall: those two are in
    probe.json under their own names and the write-up keeps all three apart.
    """
    edges = [_stamps(store, capture_id, one) for one in ("started", "ended")]
    if not edges[0] or not edges[1]:
        return None
    delta = _parsed(edges[1][0]) - _parsed(edges[0][0])
    return int(delta.total_seconds() * 1000)


def _stamps(store: Store, capture_id: str, event: str) -> Sequence[str]:
    return [
        str(row["ingest_ts"])
        for row in store.observations_of_type(f"telltale.capture_{event}")
        if row["capture_id"] == capture_id
    ]


def _parsed(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


if __name__ == "__main__":
    raise SystemExit(main())
