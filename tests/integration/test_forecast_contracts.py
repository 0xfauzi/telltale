"""The two forecast contracts of design 6.12, run against the real system.

Nothing here is stubbed except the thing the design calls a stub: `EchoStub` is a
forecaster, it goes through the registry like every other one, and its whole job is to
KEEP what it was handed so a test can compare it against what it was handed on a
different series. The store is the real store, the series come through `put_series` and
`Store.series`, and the backtester is the one the CLI calls.

Neither test imports torch, and that is a property of the suite rather than of these
files: `telltale.forecast` maps "timesfm" to a factory that imports the adapter inside
its body, so a default environment runs both of these and `import torch` fails in it.

(1) NO LOOK-AHEAD. A forecaster at origin o may see rows [ctx_start, o) and nothing
    else. The test replaces every row at index >= o with 1e9, runs the whole backtest
    again, and asserts that the window recorded at o, the contexts the stub received
    and every baseline's numbers are identical. It repeats that for every origin in the
    run, so the claim is checked once per window rather than once.

(2) METRICS. Twelve hand-written rows, a two-window run, and MAE, coverage_q, WQS and
    lead time computed with a pencil in the comments below. A metric that agrees with
    the code because both were written by the same hand is not a check; these numbers
    are arithmetic anybody can redo from the twelve values.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
import synthetic_series

from telltale import series
from telltale.forecast import (
    ECHO_OFFSET,
    ECHO_SPREAD,
    QUANTILE_LEVELS,
    EchoStub,
    make,
)
from telltale.forecast import backtest as backtester
from telltale.model import RowMeta, Series
from telltale.providers import claude

if TYPE_CHECKING:
    from telltale.store import Store

pytestmark = pytest.mark.integration

# Short enough that the no-look-ahead loop can re-run the whole backtest once per
# origin (28 origins at c_min 32), long enough that there are origins at all.
LOOK_AHEAD_ROWS = 60
POISON = 1e9

BASELINES = ("persistence", "rolling_median", "rolling_mean", "local_drift")
NAMES = (*BASELINES, "echo")

# The hand-written fixture of test 2. Twelve rows of fresh_input_tokens, chosen so that
# tau falls between the two origins' last context values and both lead-time branches
# that a two-window run can reach are reached.
FIXTURE = [20.0, 22.0, 18.0, 25.0, 30.0, 19.0, 21.0, 24.0, 17.0, 23.0, 10.0, 40.0]
FIXTURE_C_MIN = 10


def _forecasters() -> dict[str, Any]:
    """One instance per name, with the echo stub kept where a test can read it."""
    return {name: make(name) for name in NAMES}


def _run(built: Series, **kwargs: Any) -> tuple[dict[str, Any], EchoStub]:
    forecasters = _forecasters()
    run = backtester.run(built, "fresh_input_tokens", 1, forecasters, **kwargs)
    stub = forecasters["echo"]
    assert isinstance(stub, EchoStub)
    return run, stub


def _poisoned(built: Series, origin: int) -> Series:
    """The same series with every row at index >= origin replaced by 1e9.

    Every column, not just the target: a forecaster that reached forward through a
    covariate would be just as wrong, and the covariates are the variant.
    """
    rows: list[list[float | None]] = [
        [POISON] * len(row) if index >= origin else list(row)
        for index, row in enumerate(built.rows)
    ]
    return replace(built, rows=rows)


def _at(run: dict[str, Any], origin: int) -> dict[str, Any]:
    return next(record for record in run["windows"] if record["origin"] == origin)


def _numbers(record: dict[str, Any]) -> dict[str, Any]:
    """A window record without its actual and without any wall time.

    The actual IS the poisoned future: row o is the first row replaced by 1e9, so it
    must move and the test asserts separately that it did. wall_ms is a measurement of
    this machine at this instant and is not a property of the window.
    """
    return {
        **{key: record[key] for key in ("origin", "ctx_start", "n_ctx", "horizon")},
        "last_context": record["last_context"],
        "forecasts": {
            name: {"point": entry["point"], "quantiles": entry["quantiles"]}
            for name, entry in record["forecasts"].items()
        },
    }


def test_no_look_ahead_at_every_origin(store: Store) -> None:
    """Poison the future at each origin in turn; that origin's window must not move."""
    written = synthetic_series.write(store, rows=LOOK_AHEAD_ROWS, seed=1)
    built = store.series(written.series_id)
    assert built is not None, "the series must come back through the real reader"

    truth, stub = _run(built)
    assert truth["windows"], "the synthetic series must produce origins to check"
    assert len(stub.seen) == len(truth["windows"])
    seen = {window.origin: window for window in stub.seen}

    for record in truth["windows"]:
        origin = record["origin"]
        poisoned, poisoned_stub = _run(_poisoned(built, origin))
        after = {window.origin: window for window in poisoned_stub.seen}
        assert after[origin] == seen[origin], (
            f"origin {origin}: the window changed when the future was replaced by 1e9"
        )
        moved = _at(poisoned, origin)
        assert _numbers(moved) == _numbers(record), (
            f"origin {origin}: a forecaster's numbers moved with the future"
        )
        assert moved["actual"] == [POISON], (
            f"origin {origin}: the poison did not reach the row it was meant to reach"
        )


def test_window_rows_all_close_before_their_origin(store: Store) -> None:
    """The provenance form of the same claim, read off row_meta rather than the fold.

    A synthetic row's provenance names no stored activity, so `series.check` cannot
    resolve it and says so by name. What it CAN check is the ordering, and what this
    test adds is the window-level statement: every row a window consumed closed at or
    before the row its actual came from.
    """
    written = synthetic_series.write(store, rows=LOOK_AHEAD_ROWS, seed=1)
    built = store.series(written.series_id)
    assert built is not None

    # A synthetic series has no capture behind it, so `series.check` cannot resolve a
    # provenance id to a timestamp and says exactly that instead of passing quietly.
    # The window-level statement below is what this test can check, and does.
    assert series.check(store, built) == [
        "capture 'synthetic-1' has no activities, so the provenance of"
        f" {LOOK_AHEAD_ROWS} rows could not be checked"
    ]

    stamps = [meta.row_end_ts for meta in built.row_meta]
    assert stamps == sorted(stamps), "row_end_ts must not go backwards"

    run, _ = _run(built)
    for record in run["windows"]:
        origin, start = record["origin"], record["ctx_start"]
        assert start + record["n_ctx"] == origin
        assert stamps[origin - 1] <= stamps[origin]
        assert max(stamps[start:origin]) <= stamps[origin]


def test_a_changepoint_inside_the_horizon_excludes_the_window() -> None:
    """The other exclusion of design 6.12 step 3, on a series that can reach it.

    The synthetic series puts its changepoint at row 120, which is also an origin, so
    that window is dropped as regime_too_short before the changepoint rule is reached.
    A changepoint strictly inside [o, o + H) is a different case and needs H > 1.
    """
    built = replace(synthetic_series.make(rows=200, seed=1), changepoints=[122])
    forecasters = _forecasters()
    run = backtester.run(built, "fresh_input_tokens", 4, forecasters)
    straddling = [
        drop["origin"]
        for drop in run["dropped"]
        if drop["reason"] == "changepoint_in_horizon"
    ]
    assert run["dropped_counts"]["changepoint_in_horizon"] == 1
    # The one origin whose [o, o + 4) contains row 122, at a stride of 4 from 32.
    assert straddling == [120]
    assert 120 not in [record["origin"] for record in run["windows"]]


# -- test 2: the metrics, computed by hand ---------------------------------------------


def _stamp(index: int) -> str:
    return f"2026-01-01T00:{index:02d}:00.000000Z"


def _fixture_series() -> Series:
    """Twelve rows through the real ColumnSpec table, every column observed."""
    specs = series.columns(dict.fromkeys(claude.CAPABILITIES, "observed"), "observed")
    rows: list[list[float | None]] = [
        [value, 1000.0, 50.0, 900.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0] for value in FIXTURE
    ]
    cohort = {"capture_id": "fixture-12", "provider": "hand", "content_level": None}
    return Series(
        series_id=series.series_id("request", cohort, specs, "hand-12", rows),
        clock="request",
        cohort=cohort,
        columns=specs,
        rows=rows,
        row_meta=[
            RowMeta(row_key=f"fix_{index:02d}", row_end_ts=_stamp(index))
            for index in range(len(FIXTURE))
        ],
        changepoints=[],
        missingness_policy="exclude",
        reducer_version="hand-12",
    )


def test_metrics_equal_the_arithmetic(store: Store) -> None:
    """Every headline number of a two-window run, against a pencil.

    The fixture is FIXTURE above. c_min is 10 and H is 1, so the origins are
    range(10, 12, 1) = [10, 11] and the two windows are:

      o = 10: context FIXTURE[0:10], y_9 = 23, actual y_10 = 10
      o = 11: context FIXTURE[0:11], y_10 = 10, actual y_11 = 40

    echo forecasts y_{o-1} + 1, so its points are 24 and 11, and its errors are
    |10 - 24| = 14 and |40 - 11| = 29. Mean and median of [14, 29] are both 21.5.
    """
    built = _fixture_series()
    store.put_series(built)
    run, _ = _run(built, c_min=FIXTURE_C_MIN)
    scored = run["metrics"]["forecasters"]
    echo = scored["echo"]

    assert [record["origin"] for record in run["windows"]] == [10, 11]
    assert echo["n_windows"] == 2
    assert echo["mae_mean"] == pytest.approx(21.5)
    assert echo["mae_median"] == pytest.approx(21.5)

    # The stub's own arithmetic, so the numbers below rest on a band this test read
    # rather than on one it assumed: point = y_{o-1} + 1, band = point + (q - 0.5) * 2.
    first = _at(run, 10)["forecasts"]["echo"]
    assert first["point"] == pytest.approx([23.0 + ECHO_OFFSET])
    assert first["quantiles"][0] == pytest.approx(
        [24.0 + (level - 0.5) * ECHO_SPREAD for level in QUANTILE_LEVELS]
    )

    # The four baselines, same two windows:
    #   persistence   points 23, 10          errors 13, 30      mean 21.5
    #   rolling_median  last 8 of FIXTURE[0:10] is [18,25,30,19,21,24,17,23], median 22
    #                   last 8 of FIXTURE[0:11] is [25,30,19,21,24,17,23,10], median 22
    #                   errors |10-22| = 12 and |40-22| = 18       mean 15
    #   rolling_mean    means 177/8 = 22.125 and 169/8 = 21.125
    #                   errors 12.125 and 18.875                   mean 15.5
    #   local_drift     o=10: 23 + 1*(23-22)/8 = 23.125, error 13.125
    #                   o=11: 10 + 1*(10-18)/8 = 9.0,    error 31
    #                                                              mean 22.0625
    assert scored["persistence"]["mae_mean"] == pytest.approx(21.5)
    assert scored["rolling_median"]["mae_mean"] == pytest.approx(15.0)
    assert scored["rolling_mean"]["mae_mean"] == pytest.approx(15.5)
    assert scored["local_drift"]["mae_mean"] == pytest.approx(22.0625)

    # Best baseline is rolling_median at 15. skill = 1 - 21.5/15 = -0.43333...
    assert echo["skill_against"] == "rolling_median"
    assert echo["skill"] == pytest.approx(1.0 - 21.5 / 15.0)

    _assert_calibration(echo["calibration"])
    _assert_wqs(echo)
    _assert_lead_time(run, echo)


def _assert_calibration(calibration: dict[str, Any]) -> None:
    """echo's band is point + (q - 0.5) * 2, so the offsets are -0.8 .. +0.8 by 0.2.

    o = 10: point 24, band 23.2 .. 24.8, actual 10, which is <= every one of the nine.
    o = 11: point 11, band 10.2 .. 11.8, actual 40, which is <= none of them.
    So coverage_q is 1/2 at every q, n = 2, and |coverage_q - q| runs
    0.4, 0.3, 0.2, 0.1, 0.0, 0.1, 0.2, 0.3, 0.4: the maximum is 0.4.
    Central 80 percent needs 23.2 <= 10 <= 24.8 (no) and 10.2 <= 40 <= 11.8 (no): 0.
    Nothing is flagged, because 2 sqrt(0.1 * 0.9 / 2) = 0.4243 is wider than 0.4 and
    every other bound is wider still. Two windows cannot flag a calibration.
    """
    assert calibration["n"] == 2
    assert calibration["coverage_q"] == pytest.approx([0.5] * 9)
    assert calibration["max_deviation"] == pytest.approx(0.4)
    assert calibration["coverage80"] == pytest.approx(0.0)
    assert calibration["flagged"] is False
    assert 2.0 * math.sqrt(0.1 * 0.9 / 2) > 0.4


def _assert_wqs(echo: dict[str, Any]) -> None:
    """rho_q(y, yhat) = max(q d, (q - 1) d) with d = y - yhat.

    o = 10: d is negative at every q (10 - 23.2 .. 10 - 24.8), so rho = (q - 1) d:
      0.9*13.2 + 0.8*13.4 + 0.7*13.6 + 0.6*13.8 + 0.5*14.0
      + 0.4*14.2 + 0.3*14.4 + 0.2*14.6 + 0.1*14.8
      = 11.88 + 10.72 + 9.52 + 8.28 + 7.00 + 5.68 + 4.32 + 2.92 + 1.48 = 61.80
    o = 11: d is positive at every q (40 - 10.2 .. 40 - 11.8), so rho = q d:
      0.1*29.8 + 0.2*29.6 + 0.3*29.4 + 0.4*29.2 + 0.5*29.0
      + 0.6*28.8 + 0.7*28.6 + 0.8*28.4 + 0.9*28.2
      = 2.98 + 5.92 + 8.82 + 11.68 + 14.50 + 17.28 + 20.02 + 22.72 + 25.38 = 129.30
    sum |y| = 10 + 40 = 50, so WQS = 2 * 191.10 / (9 * 50) = 382.20 / 450.
    """
    assert echo["wqs"] == pytest.approx(2.0 * 191.10 / (9.0 * 50.0))
    assert echo["wqs"] == pytest.approx(0.8493333333)
    assert echo["wqs_normalized"] is True


def _assert_lead_time(run: dict[str, Any], echo: dict[str, Any]) -> None:
    """tau is q80 of FIXTURE[0:10] by linear interpolation on the order statistics.

    Sorted, those ten are 17,18,19,20,21,22,23,24,25,30. Position 0.8 * 9 = 7.2, so
    tau = 24 + 0.2 * (25 - 24) = 24.2.

    Both windows are eligible, because y_9 = 23 and y_10 = 10 are both below tau.
      o = 10: actual 10 never reaches tau, so a* does not exist. yhat_0.8 = 24.6 does,
              so f* = 0. Only f*: a FALSE ALARM.
      o = 11: actual 40 reaches tau at h = 0, so a* = 0. yhat_0.8 = 11.6 does not, so
              f* does not exist. Only a*: a MISS.
    hits 0, misses 1, false alarms 1, quiet 0.
    hit rate = 0 / (0 + 1) = 0. false alarm rate = 1 / (1 + 0) = 1.
    Neither median is defined, because a lead and a timing error need a hit.
    """
    assert run["tau"] == pytest.approx(24.2)
    lead = echo["lead_time"]
    assert (lead["eligible"], lead["hits"], lead["misses"]) == (2, 0, 1)
    assert (lead["false_alarms"], lead["quiet"]) == (1, 0)
    assert lead["hit_rate"] == pytest.approx(0.0)
    assert lead["false_alarm_rate"] == pytest.approx(1.0)
    assert lead["median_lead"] is None
    assert lead["median_timing_error"] is None

    # The hit branch, on the same two windows at a lower threshold. At tau = 11.5 the
    # first window is no longer eligible (y_9 = 23 is above it) and the second is a
    # HIT: actual 40 >= 11.5 at h = 0, and yhat_0.8 = 11.6 >= 11.5 at h = 0 too, so
    # the lead is 0 and the timing error is |0 - 0| = 0.
    lower = backtester.metrics(run["windows"], 11.5)["forecasters"]["echo"]["lead_time"]
    assert (lower["eligible"], lower["hits"]) == (1, 1)
    assert lower["hit_rate"] == pytest.approx(1.0)
    assert lower["false_alarm_rate"] is None
    assert lower["median_lead"] == 0
    assert lower["median_timing_error"] == 0


def test_persistence_has_a_nonzero_error_on_a_random_walk(store: Store) -> None:
    """A seeded random walk is not constant, so the trivial baseline is not perfect.

    The point of the whole laboratory is the comparison against these four, and a
    baseline scoring 0 would mean the series carried no variation to forecast.
    """
    written = synthetic_series.write(store, rows=200, seed=1)
    built = store.series(written.series_id)
    assert built is not None
    run, _ = _run(built)
    assert run["metrics"]["forecasters"]["persistence"]["mae_mean"] > 0.0
    assert run["metrics"]["n_windows"] >= run["k_min"]
