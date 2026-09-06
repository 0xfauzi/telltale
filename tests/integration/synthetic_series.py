"""A seeded request-clock Series with no capture behind it. Design 6.12.

W1-T6 needs a series long enough to backtest (c_min is 32 rows on the request clock and
the origins step by the horizon), and the longest real capture on this disk has eight
model requests. So this writes one: 200 rows of a seeded random walk through the same
eleven columns `telltale series build` produces, stored through `Store.put_series` like
any other snapshot.

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
from telltale.forecast import ABLATION_C, CANDIDATE_TARGETS, REWORK_TAIL
from telltale.model import ColumnSpec, RowMeta, Series
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
                # The two verification flags (W2-T7). No verification precedes row 0
                # and one precedes every row after it, which is the shape a real
                # capture has; the draw is the one the old exit column made, kept in
                # place so that the walk above is the same walk it was.
                1 if index else 0,
                0 if index == 0 or dice.random() < 0.8 else 1,
                1 if index == CHANGEPOINT else 0,
            ]
        )
    return table


# -- the change clock (design 6.12) ---------------------------------------------------

# The change clock's eighteen columns and their units: block C of the A/B/C ablation,
# which contains B, which contains A, plus the three post-merge columns the one-step
# candidate protocol forecasts. The names come from forecast/__init__.py rather than
# being spelled again here, so a column renamed there fails at import instead of
# producing a change series the ablation cannot run on.
_CHANGE_UNITS = {
    "files_changed": "files",
    "lines_added": "lines",
    "lines_removed": "lines",
    "subsystems_touched": "directories",
    "test_files_changed": "files",
    "dependency_delta": "flag",
    "fresh_input_tokens_total": "tokens",
    "cache_read_tokens_total": "tokens",
    "compactions": "compactions",
    "attempts_to_land": "attempts",
    "env_changed": "flag",
    "verification_cycles": "cycles",
    "edit_turnover_ratio": "ratio",
    "stable_state_intervals": "intervals",
    "unique_files_read": "files",
    "merge_verification_ms": "ms",
    "merge_verification_failed": "flag",
    "rework_within_3": "flag",
    "rework_within_3_lag3": "flag",
}
CHANGE_COLUMNS = (*ABLATION_C, *CANDIDATE_TARGETS)
if sorted(CHANGE_COLUMNS) != sorted(_CHANGE_UNITS):
    raise ImportError(f"{sorted(CHANGE_COLUMNS)} is not {sorted(_CHANGE_UNITS)}")

# One change every twenty minutes. As on the request clock the spacing carries no claim;
# it exists so that row_end_ts is non-decreasing, which `series check` tests.
_CHANGE_STEP = timedelta(minutes=20)
_CHANGE_FINGERPRINT = "env_synthetic_change"


def write_change(store: Store, rows: int = 60, seed: int = 1) -> Series:
    """Build a change-clock series, store it, return it. W3-T1 builds the real one.

    This is the SHAPE the A/B/C ablation and the candidate protocol consume, written
    through the real store past the real CHECK constraints. It is not evidence and it is
    not a stub of a compiler that does not exist yet: `reducer_version` says `syn-`, and
    nothing here claims anything about any repository.
    """
    built = make_change(rows=rows, seed=seed)
    store.put_series(built)
    return built


def make_change(rows: int = 60, seed: int = 1) -> Series:
    dice = random.Random(seed)
    table = _changes(dice, rows)
    # The coverage word is read off the cells rather than declared: every column here is
    # filled except `rework_within_3_lag3`, whose first REWORK_TAIL rows have no source
    # change, and calling that one `observed` would claim a cell that is not there.
    specs = [
        ColumnSpec(
            name=name,
            unit=_CHANGE_UNITS[name],
            role="past_covariate",
            coverage="partial" if any(row[at] is None for row in table) else "observed",
        )
        for at, name in enumerate(CHANGE_COLUMNS)
    ]
    cohort = {
        "capture_id": f"synthetic-change-{seed}",
        "provider": "synthetic",
        "repo_id": "syn_repo",
        "environment_fingerprint_id": _CHANGE_FINGERPRINT,
        "content_level": None,
    }
    version = _version()
    return Series(
        series_id=series.series_id("change", cohort, specs, version, table),
        clock="change",
        cohort=cohort,
        columns=specs,
        rows=table,
        row_meta=[
            RowMeta(
                row_key=f"chg_{index:04d}",
                row_end_ts=_change_stamp(index),
                env_fingerprint_id=_CHANGE_FINGERPRINT,
                provenance=[f"chg_{index:04d}"],
            )
            for index in range(rows)
        ],
        # One fingerprint over the whole lineage, so there is no changepoint. The
        # request-clock series above is where a changepoint is exercised; here the
        # ablation needs every origin it can get at c_min 16.
        changepoints=[],
        missingness_policy="exclude",
        reducer_version=version,
    )


def _changes(dice: random.Random, rows: int) -> list[list[float | None]]:
    """One row per landed change, with the size of the change driving what it cost.

    Two properties are deliberate, and neither is a claim about any repository.

    The size of a change WALKS rather than being drawn afresh each row, for the reason
    `_walk` above gives on the request clock and for one more: the chronology placebo
    is only a control on a series that carries recency, and on a table of independent
    draws persistence scores the same in either order, so the placebo cannot make it
    worse and forecast/placebo.py rightly calls the run invalid. Measured on the first
    version of this function, which drew every row independently: persistence went from
    0.7045 in true order to worse in only 7 of 10 placebo runs, and the ablation could
    not be labelled at all.

    What it cost follows how big it was, because the ablation's A, B and C blocks are
    meant to be distinguishable in principle and a table of unrelated columns could not
    be told apart by any forecaster.
    """
    table: list[list[float | None]] = []
    size = 8.0
    for _ in range(rows):
        size = min(30.0, max(1.0, size + dice.gauss(0, 1.6)))
        files = max(1, round(size + dice.gauss(0, 1.2)))
        added = max(0, round(files * dice.gauss(40, 15)))
        removed = max(0, round(added * dice.uniform(0.1, 0.7)))
        attempts = max(1, round(1 + size / 6 + dice.gauss(0, 0.5)))
        cycles = max(0, round(attempts * dice.uniform(0.5, 2.5)))
        values = {
            "files_changed": float(files),
            "lines_added": float(added),
            "lines_removed": float(removed),
            "subsystems_touched": float(max(1, min(files, round(dice.gauss(2, 1))))),
            "test_files_changed": float(max(0, round(files * dice.uniform(0.0, 0.5)))),
            "dependency_delta": float(dice.random() < 0.15),
            "fresh_input_tokens_total": float(
                max(0, round(attempts * dice.gauss(9000, 2500)))
            ),
            "cache_read_tokens_total": float(
                max(0, round(attempts * dice.gauss(120000, 30000)))
            ),
            "compactions": float(max(0, round(attempts / 3 + dice.gauss(0, 0.5)))),
            "attempts_to_land": float(attempts),
            "env_changed": 0.0,
            "verification_cycles": float(cycles),
            "edit_turnover_ratio": round(dice.uniform(0.0, 0.9), 4),
            "stable_state_intervals": float(max(0, round(dice.gauss(3, 1.5)))),
            "unique_files_read": float(
                max(files, round(files * dice.uniform(1.0, 4.0)))
            ),
            "merge_verification_ms": float(max(0, round(dice.gauss(90000, 25000)))),
            "merge_verification_failed": float(dice.random() < 0.2),
            "rework_within_3": float(dice.random() < 0.25),
            # Filled below, once the row REWORK_TAIL back exists.
            "rework_within_3_lag3": None,
        }
        table.append([values[name] for name in CHANGE_COLUMNS])
    return _lagged(table)


def _lagged(table: list[list[float | None]]) -> list[list[float | None]]:
    """`rework_within_3_lag3` on row j is row j - REWORK_TAIL's `rework_within_3`.

    W8-T2's column, built here the way series_changes builds it: the first three rows
    of any lineage have no source change and stay None, which is what makes this column
    `partial` and the frame it is forecast on three rows shorter than the series.
    """
    source = CHANGE_COLUMNS.index("rework_within_3")
    lagged = CHANGE_COLUMNS.index("rework_within_3_lag3")
    for at, row in enumerate(table):
        if at >= REWORK_TAIL:
            row[lagged] = table[at - REWORK_TAIL][source]
    return table


def _change_stamp(index: int) -> str:
    moment = _START + _CHANGE_STEP * index
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _stamp(index: int) -> str:
    moment = _START + _STEP * index
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="synthetic_series.py")
    parser.add_argument("--db", required=True, metavar="PATH")
    parser.add_argument("--clock", default="request", choices=("request", "change"))
    parser.add_argument("--rows", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    store = Store(args.db).open()
    try:
        writer = write if args.clock == "request" else write_change
        built = writer(store, rows=args.rows, seed=args.seed)
    finally:
        store.close()
    print(built.series_id)  # noqa: T201 - the id IS this script's output
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
