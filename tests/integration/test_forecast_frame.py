"""A target with holes, forecast over the rows where it is known. Design 6.12 step (2).

Split out of test_forecast_contracts.py, which is the two contracts of design 6.12 and
was at the file-length ratchet. What is under test here is one question those two do not
ask: WHICH ROWS a run is made of. Every post-merge column of a change lineage has holes
by construction, so `forecast/frame.py` builds the frame a target is forecast on, and
the count, the row keys and the reason ride in the run.

The last test runs the real compiler over a real git-history backfill, so the three
frames it checks are the frames E16 will get.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
import synthetic_series
from test_series_backfill import _backfill, _commit_payload, _history_sha
from test_series_lineage import _built, _repository

from telltale import repo
from telltale.forecast import backtest as backtester
from telltale.forecast import make, readiness
from telltale.forecast.frame import retained as frame_of

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from telltale.model import Series
    from telltale.store import Store

pytestmark = pytest.mark.integration

# The five forecasters every run here is scored with, the same set the contract tests
# use: four baselines and the stub that keeps its windows.
NAMES = ("persistence", "rolling_median", "rolling_mean", "local_drift", "echo")
# A request-clock series with no hole anywhere, for the identity claim below. 60 rows
# because the identity is about the object and not about how many windows it forms.
WHOLE_ROWS = 60

# The 60-row change lineage of the frame tests, its holes and its changepoint. Seven
# scattered rows rather than a block, because a block is the easy case: the count and
# the changepoint arithmetic have to hold when the holes are interleaved with the rows
# a window reads.
FRAME_ROWS = 60
FRAME_HOLES = (3, 4, 17, 28, 41, 42, 55)
FRAME_TARGET = "merge_verification_ms"
FRAME_CHANGEPOINT = 30
# Four holes sit below the changepoint at 30 (rows 3, 4, 17 and 28), so row 30 is the
# 27th retained row and the boundary in front of it is at index 26.
FRAME_MOVED = 26
CHANGE_C_MIN = 16


def _lineage(holes: Sequence[int], changepoints: Sequence[int] = ()) -> Series:
    """A change-clock series whose target is unknown on `holes`, and says so.

    The coverage word is moved to `partial` with the cells, because that is what a real
    lineage carries: a commit with no check run has no merge_verification_ms, and the
    compiler's word for a column some rows carry and some do not is `partial`.
    """
    built = synthetic_series.make_change(rows=FRAME_ROWS, seed=1)
    index = [spec.name for spec in built.columns].index(FRAME_TARGET)
    specs = list(built.columns)
    specs[index] = replace(specs[index], coverage="partial")
    rows: list[list[float | None]] = [list(cells) for cells in built.rows]
    for row in holes:
        rows[row][index] = None
    return replace(built, columns=specs, rows=rows, changepoints=list(changepoints))


def test_retained_drops_exactly_the_rows_whose_target_is_unknown() -> None:
    """The projection itself: the rows, the row keys, the cohort and the identity."""
    built = _lineage(FRAME_HOLES)

    frame = frame_of(built, FRAME_TARGET)

    keys = [meta.row_key for meta in built.row_meta]
    assert len(frame.rows) == FRAME_ROWS - len(FRAME_HOLES)
    assert [meta.row_key for meta in frame.row_meta] == [
        key for at, key in enumerate(keys) if at not in FRAME_HOLES
    ]
    assert frame.cohort["excluded_row_keys"] == [keys[at] for at in FRAME_HOLES]
    assert frame.cohort["retained_rows"] == FRAME_ROWS - len(FRAME_HOLES)
    assert frame.cohort["excluded_rows"] == len(FRAME_HOLES)
    assert frame.cohort["excluded_reason"] == f"target {FRAME_TARGET} unknown"
    assert frame.series_id == f"{built.series_id}:{FRAME_TARGET}"
    # Nothing was imputed and no value moved: the retained rows are the same objects.
    index = [spec.name for spec in built.columns].index(FRAME_TARGET)
    assert [row[index] for row in frame.rows] == [
        row[index] for at, row in enumerate(built.rows) if at not in FRAME_HOLES
    ]
    # The identity a request-clock run rests on: no hole, the same object back, so the
    # series id and every stored byte of that run are what they were before W8-T3.
    whole = synthetic_series.make(rows=WHOLE_ROWS, seed=1)
    assert frame_of(whole, "fresh_input_tokens") is whole


def test_a_changepoint_after_a_hole_moves_by_the_holes_before_it() -> None:
    """A changepoint is a boundary between rows, so it moves to where those rows are.

    Break the remap by dropping the `bisect_left` and this fails: a boundary left at 30
    would put the regime start three rows past the change it marks.
    """
    built = _lineage(FRAME_HOLES, changepoints=[FRAME_CHANGEPOINT])

    frame = frame_of(built, FRAME_TARGET)

    assert frame.changepoints == [FRAME_MOVED]
    # The row the boundary marks is the same row either way, which is the claim.
    marked = built.row_meta[FRAME_CHANGEPOINT].row_key
    assert frame.row_meta[FRAME_MOVED].row_key == marked


def test_a_backtest_over_a_holed_target_counts_its_windows_on_the_retained_rows(
    store: Store,
) -> None:
    """N' - c_min - H + 1 windows, the exclusion recorded, and no window dropped.

    Every window of a 60-row lineage at c_min 16 starts at row 0, so dropping a window
    for a hole in the target would have kept almost none of them. The rows go instead,
    and the run says how many and which.
    """
    built = _lineage(FRAME_HOLES)
    store.put_series(built)
    kept = FRAME_ROWS - len(FRAME_HOLES)
    forecasters = {name: make(name) for name in NAMES}

    run = backtester.run(built, FRAME_TARGET, 1, forecasters)

    assert run["excluded_rows"]["count"] == len(FRAME_HOLES)
    assert run["excluded_rows"]["reason"] == "target unknown"
    assert run["excluded_rows"]["row_keys"] == [
        built.row_meta[at].row_key for at in FRAME_HOLES
    ]
    assert len(run["windows"]) == kept - CHANGE_C_MIN - 1 + 1 == 37
    assert run["dropped_counts"] == {}
    assert run["n_rows"] == kept
    assert run["series_id"] == f"{built.series_id}:{FRAME_TARGET}"
    assert any(
        "were excluded because the target was unknown" in line
        for line in run["assumptions"]
    )
    # The readiness checklist counts the same windows, because it plans them with the
    # same function over the same frame.
    checks = {item.name: item for item in readiness.check(built, FRAME_TARGET, 1)}
    assert checks["windows"].measured == len(run["windows"])
    assert checks["coverage"].passed
    assert [name for name, item in checks.items() if not item.passed] == []
    printed = readiness.report(built, FRAME_TARGET, 1, list(checks.values()))
    assert f"rows {FRAME_ROWS}  retained {kept}  excluded {len(FRAME_HOLES)}" in printed


# The 8-row git-history lineage of W8-T2, and what each post-merge column knows on it.
# Two commits carry a check run, so merge_verification_ms is known on those two alone;
# the rework label is undecidable on the last three rows of any lineage and its lagged
# twin on the first three. Three targets, three different frames, one series.
BACKFILL_ROWS = 8
BACKFILL_KNOWN = {
    "merge_verification_ms": (0, 1),
    "rework_within_3": (0, 1, 2, 3, 4),
    "rework_within_3_lag3": (3, 4, 5, 6, 7),
}


@pytest.mark.usefixtures("telltale_home")
def test_each_post_merge_target_of_a_real_lineage_gets_its_own_frame(
    tmp_path: Path,
) -> None:
    """W8-T2's backfilled lineage, through the real compiler, projected three ways.

    Every post-merge column of a lineage is `partial` by construction and no run could
    be made of one before W8-T3. What each of them is forecast over is a different set
    of rows, which is why the frame is per target and its id names the target.
    """
    root = _repository(tmp_path / "history")
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)
    _backfill(root, repo_id, [_commit_payload(index) for index in range(BACKFILL_ROWS)])

    built = _built(repo_id, clock="change")

    assert [meta.row_key for meta in built.row_meta] == [
        _history_sha(index) for index in range(BACKFILL_ROWS)
    ]
    for target, known in BACKFILL_KNOWN.items():
        _assert_lineage_frame(built, target, known)


def _assert_lineage_frame(built: Series, target: str, known: Sequence[int]) -> None:
    """One post-merge column's frame: its word, its rows, its excluded keys, no hole."""
    spec = next(one for one in built.columns if one.name == target)
    assert spec.coverage == "partial", target
    frame = frame_of(built, target)
    assert [meta.row_key for meta in frame.row_meta] == [
        _history_sha(index) for index in known
    ], target
    assert frame.cohort["excluded_row_keys"] == [
        _history_sha(index) for index in range(BACKFILL_ROWS) if index not in known
    ], target
    index = [one.name for one in built.columns].index(target)
    assert all(row[index] is not None for row in frame.rows), target
