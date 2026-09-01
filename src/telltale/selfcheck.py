"""`python -m telltale.store --selfcheck DB`, which is the store's own proof.

Split out of store.py, which sat two lines under the 800-line ratchet. It is the one
piece of that module nothing else calls: it writes 1000 observations from 8 threads,
reads them back from a reopened database, and checks the three promises the store makes
that no type signature can state. That two observations recorded in the same millisecond
by two threads still sort in arrival order, that everything accepted is stored, and that
a drop is counted rather than silent.

Kept as a command rather than a test because it runs the real thing against a real file
and prints the numbers, which is what the owner reads after a change to the writer.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from telltale.model import Observation, now_iso, to_json, ulid
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass
class _Made:
    """One ulid() call: when it started, when it returned, and what it returned."""

    before: int
    after: int
    oid: str


def _worker(store: Store, surface: str, count: int, made: list[_Made]) -> None:
    batch: list[Observation] = []
    for index in range(count):
        before = time.monotonic_ns()
        oid = ulid()
        made.append(_Made(before, time.monotonic_ns(), oid))
        obs = Observation(
            oid, "cap_selfcheck", "telltale.capture_started", surface, "telltale",
            "selfcheck@1", now_iso(), payload={"index": index},
        )  # fmt: skip
        batch.append(obs)
        if len(batch) == 5:
            store.append(batch)
            batch = []
    if batch:
        store.append(batch)


def _order_violations(made: list[_Made]) -> int:
    """Count pairs where one ulid() call finished before another began, out of order.

    That is the promise of the id: if call A returned before call B was entered, A's id
    must sort before B's. Overlapping pairs are not compared, because neither call is
    "first" in any sense a reader could use.
    """
    by_after = sorted(made, key=lambda item: item.after)
    by_before = sorted(made, key=lambda item: item.before)
    pointer = 0
    largest = ""
    violations = 0
    for item in by_before:
        while pointer < len(by_after) and by_after[pointer].after <= item.before:
            largest = max(largest, by_after[pointer].oid)
            pointer += 1
        if largest and largest >= item.oid:
            violations += 1
    return violations


def _spawn(store: Store, threads: int, per_thread: int) -> tuple[list[_Made], float]:
    surfaces = ("otel_logs", "otel_metrics", "hook", "stream")
    made: list[list[_Made]] = [[] for _ in range(threads)]
    workers = [
        threading.Thread(
            target=_worker,
            args=(store, surfaces[index % len(surfaces)], per_thread, made[index]),
        )
        for index in range(threads)
    ]
    started = time.monotonic()
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    elapsed_ms = (time.monotonic() - started) * 1000
    return [item for chunk in made for item in chunk], elapsed_ms


def _selfcheck(path: Path, threads: int = 8, per_thread: int = 125) -> int:
    write = sys.stdout.write  # ruff T20: print() lives in cli.py and report.py only
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)

    store = Store(path).open()
    made, elapsed_ms = _spawn(store, threads, per_thread)
    before_close = store.health()
    store.close()

    reopened = Store(path).open()
    stored = reopened.observations("cap_selfcheck")
    captures = reopened.captures()
    after_open = reopened.health()
    reopened.close()

    expected = threads * per_thread
    generated = {item.oid for item in made}
    stored_ids = {str(row["observation_id"]) for row in stored}
    violations = _order_violations(made)
    write(f"db {path}\n")
    write(f"threads {threads} per_thread {per_thread} wall_ms {elapsed_ms:.1f}\n")
    write(f"generated {len(made)} distinct_ids {len(generated)}\n")
    write(f"stored {len(stored)} expected {expected}\n")
    write(f"arrival_order_violations {violations}\n")
    write(f"drops_total {before_close['drops_total']}\n")
    write(f"captures {to_json(captures)}\n")
    write(f"health_before_close {to_json(before_close)}\n")
    write(f"health_after_reopen {to_json(after_open)}\n")
    ok = all(
        (
            len(generated) == expected,
            stored_ids == generated,
            violations == 0,
            before_close["drops_total"] == 0,
        )
    )
    write("selfcheck PASS\n" if ok else "selfcheck FAIL\n")
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    """The entry point of both `telltale.selfcheck` and `telltale.store --selfcheck`."""
    parser = argparse.ArgumentParser(prog="python -m telltale.selfcheck")
    parser.add_argument(
        "db",
        metavar="DB",
        help="write 1000 observations from 8 threads into DB and read them back",
    )
    args = parser.parse_args(argv)
    return _selfcheck(Path(args.db))


if __name__ == "__main__":
    raise SystemExit(main())
