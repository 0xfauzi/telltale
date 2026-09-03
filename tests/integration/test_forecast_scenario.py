"""`telltale forecast scenario` end to end. Design 6.12, W6-T1.

A scenario is a conditional forecast at row N under a declared future, and the four
things under test are four different questions.

  WHAT MAY BE DECLARED. `DECLARABLE` is a registry keyed by clock, and every refusal it
  produces names the column. A path for `fresh_input_tokens` is not a wish the system
  can grant: nobody plans how many tokens a model reads, and the refusal says so in the
  one sentence `PAST_ONLY` holds.

  WHAT REACHES THE DISK. Two scenarios differing in one declared path leave two
  `forecast_runs` rows, both `predictive`, both carrying SCENARIO_SENTENCE in their
  assumptions and in `scenario.sentence`, both with `scenario.paths` equal to what was
  declared, and both with exactly one window whose `actual` is None. A scenario has no
  actual, so it has no error, and the row says that rather than storing an empty score.

  WHETHER A FORECASTER READS THE DECLARATION. Until something reads `Window.future`,
  two scenarios are one forecast printed twice. The baselines are the control and say
  so through NOT_A_READER; `FuturePath` below is the reader, so the whole protocol is
  exercised without the model stack. It is a forecaster and not a stub of one: a
  Forecaster is a signature, and this is a function behind that signature.

  WHAT `compare` PRINTS. One difference line per step per forecaster, the declared
  difference above it, and the sentence at the bottom.

Everything runs against the real store, the real registry, the real CLI and the real
window builder. The only thing written for the test is the forecaster.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import synthetic_series

from telltale import cli
from telltale.forecast import (
    DECLARABLE,
    PAST_ONLY,
    QUANTILE_LEVELS,
    SCENARIO_HORIZONS,
    SCENARIO_SENTENCE,
    Window,
    make,
)
from telltale.forecast import scenario as scenarios
from telltale.forecast import scenario_report as scenario_page
from telltale.model import ForecastResult

if TYPE_CHECKING:
    from telltale.model import Series
    from telltale.store import Store

pytestmark = pytest.mark.integration

TARGET = "output_tokens"
DECLARED = "tool_calls_since_prev"
HORIZON = 4
QUIET = [2.0, 2.0, 2.0, 2.0]
BUSY = [8.0, 8.0, 8.0, 8.0]
# Two baselines rather than four: the point of the pair below is the SECOND row, and
# every baseline in it is one more forecast nobody reads.
BASELINES = ["persistence", "rolling_median"]


def _series(store: Store) -> Series:
    """The seeded 200-row request-clock series, written through the real store."""
    return synthetic_series.write(store, rows=200, seed=1)


def _scenario(name: str, path: list[float]) -> scenarios.Scenario:
    return scenarios.Scenario(name=name, horizon=HORIZON, paths={DECLARED: path})


def _run(series: Series, declared: scenarios.Scenario) -> dict[str, Any]:
    forecasters = {name: make(name) for name in BASELINES}
    return scenarios.run(series, TARGET, declared, forecasters, BASELINES[0])


# -- (a) what reaches the disk ---------------------------------------------------------


def test_two_scenarios_store_two_predictive_rows_carrying_the_sentence(
    store: Store,
) -> None:
    """The pair of rows, and every field the amendment says a scenario row holds."""
    series = _series(store)
    for name, path in (("quiet", QUIET), ("busy", BUSY)):
        found = _run(series, _scenario(name, path))
        found["calibration"] = scenarios.calibration(store, found)
        scenarios.persist(store, found)
    store.close()

    rows = store.forecast_runs(series.series_id)
    assert len(rows) == 2
    assert [row["scenario"]["name"] for row in rows] == ["quiet", "busy"]
    assert [row["scenario"]["paths"][DECLARED] for row in rows] == [QUIET, BUSY]
    for row in rows:
        assert row["claim_class"] == "predictive"
        assert row["horizon"] == HORIZON
        assert row["ordering"] == "true"
        assert row["variant"] == "request_past_only_scenario"
        assert row["scenario"]["sentence"] == SCENARIO_SENTENCE
        assert SCENARIO_SENTENCE in row["assumptions"]
        assert row["scenario"]["declarable"] == list(DECLARABLE["request"])
        # No actual and so no error: `metrics` is empty and the row says which of the
        # two empties it is.
        assert row["metrics"] == {}
        assert scenarios.NO_ACTUAL in row["warnings"]
        retained = row["windows"]["retained"]
        assert len(retained) == 1
        assert retained[0]["actual"] is None
        assert retained[0]["origin"] == len(series.rows)
        assert row["windows"]["dropped"] == []
        # Nothing scored this pair, so nothing is quoted. `None` here is "nobody
        # measured", which is the honest answer and not a zero.
        assert row["scenario"]["calibration_from"] is None


def test_the_declared_column_rides_as_both_a_past_history_and_a_future_path(
    store: Store,
) -> None:
    """n_ctx + H long, the context observed and the tail declared. Nothing imputed."""
    series = _series(store)
    declared = _scenario("quiet", QUIET)
    window = scenarios.window_at_end(
        series, TARGET, declared, [DECLARED, "cache_read_tokens"]
    )
    assert window.origin == len(series.rows)
    # The changepoint at 120 is the regime start, so the context is [120, 200).
    assert window.ctx_start == synthetic_series.CHANGEPOINT
    assert window.n_ctx == len(series.rows) - synthetic_series.CHANGEPOINT
    assert window.future is not None
    block = window.future[DECLARED]
    assert len(block) == window.n_ctx + HORIZON
    assert block[-HORIZON:] == QUIET
    assert block[: window.n_ctx] == window.column(DECLARED), (
        "the head of a declared path is the column's OBSERVED history, not a repeat"
        " of the declaration"
    )


# -- (b) the refusals ------------------------------------------------------------------


def test_a_path_for_a_past_only_column_is_refused_naming_the_column(
    store: Store,
) -> None:
    series = _series(store)
    declared = scenarios.Scenario(
        name="wish", horizon=HORIZON, paths={"fresh_input_tokens": QUIET}
    )
    with pytest.raises(scenarios.NotDeclarable) as refused:
        scenarios.check(series, TARGET, declared)
    assert "fresh_input_tokens" in str(refused.value)
    assert PAST_ONLY in str(refused.value)


def test_a_path_of_the_wrong_length_is_refused(store: Store) -> None:
    series = _series(store)
    declared = scenarios.Scenario(
        name="short", horizon=HORIZON, paths={DECLARED: [2.0, 2.0, 2.0]}
    )
    with pytest.raises(scenarios.WrongLength) as refused:
        scenarios.check(series, TARGET, declared)
    assert f"3 values declared for horizon {HORIZON}" in str(refused.value)


def test_a_path_for_the_target_is_refused(store: Store) -> None:
    series = _series(store)
    declared = scenarios.Scenario(name="answer", horizon=HORIZON, paths={TARGET: QUIET})
    with pytest.raises(scenarios.TargetPath) as refused:
        scenarios.check(series, TARGET, declared)
    assert TARGET in str(refused.value)


def test_a_horizon_outside_the_scenario_horizons_is_refused(store: Store) -> None:
    series = _series(store)
    assert 3 not in SCENARIO_HORIZONS
    declared = scenarios.Scenario(
        name="three", horizon=3, paths={DECLARED: [2.0, 2.0, 2.0]}
    )
    with pytest.raises(scenarios.BadHorizon) as refused:
        scenarios.check(series, TARGET, declared)
    assert str(list(SCENARIO_HORIZONS)) in str(refused.value)


def test_a_negative_value_on_a_counting_column_is_refused(store: Store) -> None:
    series = _series(store)
    declared = _scenario("negative", [2.0, -1.0, 2.0, 2.0])
    with pytest.raises(scenarios.NegativePath) as refused:
        scenarios.check(series, TARGET, declared)
    assert "step 2" in str(refused.value)


def test_two_flags_for_one_column_are_refused_rather_than_one_overwriting_the_other(
    store: Store,
) -> None:
    """Duplicate is not one: two suppositions about one column pick neither."""
    _series(store)
    with pytest.raises(scenarios.BadPath) as refused:
        scenarios.parse_paths([f"{DECLARED}=2,2,2,2", f"{DECLARED}=8,8,8,8"], HORIZON)
    assert "declared twice" in str(refused.value)


def test_the_command_refuses_a_past_only_path_and_a_bad_horizon_with_exit_2(
    store: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    """Through the real CLI, which is where a refusal has to reach a reader."""
    series = _series(store)
    store.close()
    base = ["forecast", "scenario", "--series", series.series_id, "--target", TARGET]
    assert cli.main([
        *base, "--horizon", "4", "--future", "fresh_input_tokens=1,1,1,1",
        "--forecasters", "persistence",
    ]) == 2  # fmt: skip
    assert PAST_ONLY in capsys.readouterr().out
    assert cli.main([
        *base, "--horizon", "3", "--future", f"{DECLARED}=1,1,1",
        "--forecasters", "persistence",
    ]) == 2  # fmt: skip
    assert str(list(SCENARIO_HORIZONS)) in capsys.readouterr().out


# -- (c) compare -----------------------------------------------------------------------


def test_compare_prints_a_difference_line_per_step_and_the_sentence(
    store: Store,
) -> None:
    series = _series(store)
    pair = [
        scenarios.record(_prepared(series, name, path))
        for name, path in (("quiet", QUIET), ("busy", BUSY))
    ]
    printed = scenario_page.compare(pair[0], pair[1])
    body = printed.splitlines()
    steps = [line for line in body if line.startswith(tuple(BASELINES))]
    assert len(steps) == HORIZON * len(BASELINES), (
        "one difference line per step per forecaster"
    )
    assert f"{DECLARED}: a 2, 2, 2, 2  b 8, 8, 8, 8" in printed
    assert printed.rstrip().endswith(SCENARIO_SENTENCE)
    # Under the baselines the two are the same forecast, and `compare` says so from
    # the numbers rather than from what the forecasters declared.
    assert "identical under both declarations" in printed


def test_compare_refuses_two_rows_that_are_not_about_the_same_thing(
    store: Store,
) -> None:
    series = _series(store)
    one = scenarios.record(_prepared(series, "quiet", QUIET))
    other = scenarios.record(_prepared(series, "quiet", QUIET))
    other["target"] = "fresh_input_tokens"
    with pytest.raises(scenarios.NotComparable) as refused:
        scenario_page.compare(one, other)
    assert "target differs" in str(refused.value)


def _prepared(series: Series, name: str, path: list[float]) -> dict[str, Any]:
    return _run(series, _scenario(name, path))


# -- (d) a forecaster that reads the declaration ---------------------------------------


class FuturePath:
    """Persistence plus the last value of the declared path. A reader, not a stub.

    It is here rather than in `telltale.forecast` for the reason `FutureEcho` is in
    test_forecast_candidate.py: the one forecaster in src/ that reads a past-future
    covariate is the TimesFM adapter, which needs an extra CI does not install. Without
    a future block this is exactly persistence, so the two scenarios below differ in
    the declaration and in nothing else.
    """

    name = "future_path"

    def __init__(self, reads: str = DECLARED) -> None:
        self.reads = reads

    def forecast(self, window: Window, horizon: int) -> ForecastResult:
        last = window.column(window.target)[-1]
        block = (window.future or {}).get(self.reads)
        point = [last + (0.0 if block is None else block[-1])] * horizon
        return ForecastResult(
            forecaster=self.name,
            horizon=horizon,
            point=[point],
            quantile_levels=list(QUANTILE_LEVELS),
            targets=[window.target],
            covariates=[
                *window.covariates,
                *(f"future:{name}" for name in sorted(window.future or {})),
            ],
            missingness_policy="exclude",
            quantiles=[[[value] * len(QUANTILE_LEVELS) for value in point]],
        )


def test_a_reader_makes_the_two_scenarios_differ_by_the_declared_difference(
    store: Store,
) -> None:
    """The whole point of a scenario, measured: the paths move the forecast.

    The difference is exactly BUSY[-1] - QUIET[-1] because that is what this forecaster
    reads. It is a fact about the forecaster, which is what makes it checkable: under
    a model the difference is whatever the model makes of the path, and there is no
    number a test could assert about that without pinning a checkpoint.
    """
    series = _series(store)
    forecasters = {FuturePath.name: FuturePath()}
    points = {}
    for name, path in (("quiet", QUIET), ("busy", BUSY)):
        found = scenarios.run(
            series, TARGET, _scenario(name, path), forecasters, FuturePath.name
        )
        points[name] = found["forecasts"][FuturePath.name]["point"]
        assert found["forecasts"][FuturePath.name]["read_future"] is True
        assert scenarios.NOT_A_READER not in " ".join(found["warnings"])

    difference = [
        after - before
        for before, after in zip(points["quiet"], points["busy"], strict=True)
    ]
    assert difference == [BUSY[-1] - QUIET[-1]] * HORIZON, (
        "the declared path reached the forecaster: this is the break-and-restore"
        " assertion, and it fails when window_at_end drops the path from `future`"
    )


def test_the_baselines_say_they_read_no_declaration(store: Store) -> None:
    """The control for the test above: under a baseline a scenario changes nothing."""
    series = _series(store)
    found = _run(series, _scenario("quiet", QUIET))
    warned = [
        line for line in found["warnings"] if line.startswith(scenarios.NOT_A_READER)
    ]
    assert len(warned) == 1
    for name in BASELINES:
        assert name in warned[0]
        assert found["forecasts"][name]["read_future"] is False


# -- the whole command -----------------------------------------------------------------


def test_the_command_runs_stores_and_compares_through_the_real_cli(
    store: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    series = _series(store)
    store.close()
    base = [
        "forecast", "scenario", "--series", series.series_id, "--target", TARGET,
        "--horizon", str(HORIZON), "--forecasters", ",".join(BASELINES),
    ]  # fmt: skip
    assert cli.main([*base, "--future", f"{DECLARED}=2,2,2,2", "--name", "quiet"]) == 0
    printed = capsys.readouterr().out
    assert SCENARIO_SENTENCE in printed
    assert scenarios.NO_CALIBRATION in printed
    first = printed.rsplit("forecast_run_id ", 1)[1].strip()

    assert cli.main([
        *base, "--future", f"{DECLARED}=8,8,8,8", "--name", "busy",
        "--compare-with", first,
    ]) == 0  # fmt: skip
    second = capsys.readouterr().out
    assert "forecast scenario compare" in second
    assert f"a = quiet ({first})" in second
    assert "b = busy (not stored yet)" in second

    rows = store.forecast_runs(series.series_id)
    assert len(rows) == 2
    assert [row["scenario"]["name"] for row in rows] == ["quiet", "busy"]
    assert rows[0]["forecast_run_id"] == first


def test_the_command_refuses_a_compare_with_that_names_no_stored_scenario(
    store: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    series = _series(store)
    store.close()
    assert cli.main([
        "forecast", "scenario", "--series", series.series_id, "--target", TARGET,
        "--horizon", str(HORIZON), "--future", f"{DECLARED}=2,2,2,2",
        "--forecasters", "persistence", "--compare-with", "fc_nothing",
    ]) == 2  # fmt: skip
    assert "no stored scenario" in capsys.readouterr().out
