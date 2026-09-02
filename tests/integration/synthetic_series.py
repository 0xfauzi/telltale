"""A seeded request-clock Series with no capture behind it. Design 6.12.

W1-T6 needs a series long enough to backtest (c_min is 32 rows on the request clock and
the origins step by the horizon), and the longest real capture on this disk has eight
model requests. So this writes one: 200 rows of a seeded random walk through the same
ten columns `telltale series build` produces, stored through `Store.put_series` like any
other snapshot.

Three things it is NOT, and each matters. It is not evidence: nothing here was measured,
the numbers are a random walk with no claim about any session, and `reducer_version`
says `syn-` so that no reader can mistake a synthetic series for a compiled one. It is
not a stub of the compiler: it writes through the real store, into the real table,
past the real CHECK constraints, and `Store.series` reads it back through the real
reader. And it is not a fixture of anybody's behaviour: what it exercises is the SHAPE,
which is the only thing a forecaster consumes.

    uv run python tests/integration/synthetic_series.py --db PATH --rows 200 --seed 1
"""

from __future__ import annotations

import argparse
import hashlib
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from telltale import series
from telltale.model import RowMeta, Series
from telltale.providers import claude
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Sequence

# Where the environment fingerprint changes, which is the one changepoint in the walk.
# A model switch mid-session is the case design 6.12 names, and a backtester has to be
# able to see one: windows that straddle it are excluded from the headline.
CHANGEPOINT = 120

# One row every 30 seconds from a fixed instant. Real requests are seconds apart and
# irregular; the clock is an index here and the spacing carries no claim.
_START = datetime(2026, 1, 1, tzinfo=UTC)
_STEP = timedelta(seconds=30)

_FINGERPRINTS = ("env_synthetic_a", "env_synthetic_b")


def _version() -> str:
    """`syn-` plus this file's hash: what wrote the rows, not what compiled them."""
    return f"syn-{hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}"


def write(store: Store, rows: int = 200, seed: int = 1) -> Series:
    """Build the walk, store it, return it. The id is a content hash, so this replaces.

    Every column is marked `observed` because every column is filled: a synthetic row
    with an unavailable column would be claiming a capability nobody measured.
    """
    built = make(rows=rows, seed=seed)
    store.put_series(built)
    return built


def make(rows: int = 200, seed: int = 1) -> Series:
    dice = random.Random(seed)
    specs = series.columns(dict.fromkeys(claude.CAPABILITIES, "observed"), "observed")
    table = _walk(dice, rows)
    meta = [
        RowMeta(
            row_key=f"syn_{index:04d}",
            row_end_ts=_stamp(index),
            env_fingerprint_id=_FINGERPRINTS[1 if index >= CHANGEPOINT else 0],
            provenance=[f"syn_{index:04d}"],
        )
        for index in range(rows)
    ]
    cohort = {
        "capture_id": f"synthetic-{seed}",
        "provider": "synthetic",
        "repo_id": None,
        "environment_fingerprint_id": None,
        "content_level": None,
    }
    version = _version()
    return Series(
        series_id=series.series_id("request", cohort, specs, version, table),
        clock="request",
        cohort=cohort,
        columns=specs,
        rows=table,
        row_meta=meta,
        changepoints=[CHANGEPOINT] if rows > CHANGEPOINT else [],
        missingness_policy="exclude",
        reducer_version=version,
    )


def _walk(dice: random.Random, rows: int) -> list[list[float | None]]:
    """A random walk in the three token columns and independent draws in the rest.

    A walk rather than independent draws for the tokens because a forecaster that beats
    persistence on white noise has been given an easy problem, and design 6.12's whole
    decision rule is the model against the baselines.
    """
    fresh, cached, out, duration = 1200.0, 20000.0, 400.0, 2500.0
    table: list[list[float | None]] = []
    for index in range(rows):
        fresh = max(0.0, fresh + dice.gauss(0, 120))
        cached = max(0.0, cached + dice.gauss(200, 900))
        out = max(0.0, out + dice.gauss(0, 60))
        duration = max(1.0, duration + dice.gauss(0, 250))
        table.append(
            [
                round(fresh),
                round(cached),
                round(out),
                round(duration),
                1 if dice.random() < 0.02 else 0,
                dice.randint(0, 4),
                dice.randint(0, 2),
                dice.randint(0, 1),
                0 if index == 0 or dice.random() < 0.8 else 1,
                1 if index == CHANGEPOINT else 0,
            ]
        )
    return table


def _stamp(index: int) -> str:
    moment = _START + _STEP * index
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="synthetic_series.py")
    parser.add_argument("--db", required=True, metavar="PATH")
    parser.add_argument("--rows", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    store = Store(args.db).open()
    try:
        built = write(store, rows=args.rows, seed=args.seed)
    finally:
        store.close()
    print(built.series_id)  # noqa: T201 - the id IS this script's output
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
