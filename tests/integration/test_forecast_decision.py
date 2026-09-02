"""Contract test 3 of design 6.12: the placebo, the decision rule and the runs stored.

Not in test_forecast_contracts.py beside contracts 1 and 2, and the reason is
mechanical rather than editorial: that file is 562 lines and the file-length pre-commit
hook refuses a Python file that grows past 800. Same suite, same marker, same fixtures.

Six claims, in the order design 6.12 lists them.

(a) A forecaster no better than the four one-line baselines is labelled "baseline
    sufficient", and the label is printed with the value on both sides of each
    inequality that produced it.

(b) A forecaster that BEATS the baselines but is unchanged by the placebo is labelled
    "conditional prediction". The forecaster used here reads only the MULTISET of its
    context: it sorts the context before it fits anything. A permutation cannot change
    a multiset, so E_P is E_M to the last bit and the temporal branch cannot be taken,
    which is the cleanest possible statement of what that label means.

(c) Every stored row carries missingness_policy, the placebo definition, the baseline
    outputs, the licence field, claim_class predictive, and, on a candidate run, the
    mandatory sentence.

(d) `block_shuffle` returns the same multiset of rows, and persistence scored on the
    shuffled contexts is worse than persistence scored in true order.

(e) A forecast report holding a forbidden word raises before it is printed or stored,
    and the candidate sentence, which holds none of them, passes.

(f) 6.12 as amended by W3-E08b: an invalid placebo withholds the two labels that READ
    the placebo and withholds nothing else. Two runs, both with a control that failed:
    one where the baseline clause fires, which is still "baseline sufficient" and
    carries the warning, and one where it does not, which is "not assessable: placebo
    invalid" with the baseline inequalities shown.

Nothing here is stubbed except forecasters, which is what a forecaster is: design 6.12
defines one as anything with `forecast(window, horizon)`. The store is the real store,
the series go through `put_series` and `Store.series`, the runs go through
`store.put_forecast_run`, and two of the tests below drive the real CLI.
"""

from __future__ import annotations

import random
import statistics
from typing import TYPE_CHECKING, Any

import pytest
import synthetic_series

from telltale import cli
from telltale import series as compiler
from telltale.forecast import (
    CANDIDATE_SENTENCE,
    DELTA,
    ORDERING_BLOCK,
    ORDERING_ROW,
    ORDERING_TRUE,
    PLACEBO_INVALID_WARNING,
    PLACEBO_SEEDS,
    QUANTILE_LEVELS,
    ForbiddenWord,
    W,
    make,
    placebo_block,
    refuse_words,
)
from telltale.forecast import backtest as backtester
from telltale.forecast import decide as decider
from telltale.forecast import placebo as placebos
from telltale.model import ForecastResult, RowMeta, Series
from telltale.providers import claude

if TYPE_CHECKING:
    from telltale.forecast import Window
    from telltale.store import Store

pytestmark = pytest.mark.integration

BASELINES = ("persistence", "rolling_median", "rolling_mean", "local_drift")
TARGET = "fresh_input_tokens"

# The row index where the halves fixture stops being a ramp and becomes noise. It is a
# changepoint too, so no context straddles it and the two halves of the origin range are
# exactly the two regimes.
SPLIT = 150
RAMP_ROWS = 300


# -- three forecasters, each written to make one claim testable ------------------------


def _result(name: str, point: list[float], spread: float, window: Window) -> Any:
    """A ForecastResult with a band, so calibration and WQS are assessable."""
    return ForecastResult(
        forecaster=name,
        horizon=len(point),
        point=[point],
        quantile_levels=list(QUANTILE_LEVELS),
        targets=[window.target],
        covariates=window.covariates,
        missingness_policy="exclude",
        quantiles=[
            [[value + (level - 0.5) * spread for level in QUANTILE_LEVELS]]
            for value in point
        ],
    )


class Offset:
    """Persistence plus a large constant. Worse than persistence, on purpose.

    Test (a) needs a forecaster that is WORSE than the trivial baseline, and the
    registry's own stub is not: `echo` is persistence plus 1, and on the 200-row
    synthetic walk its mean MAE is bit-for-bit persistence's (the target is integers,
    so the 68 windows where the walk rose cancel the 68 where it did not). The CLI test
    below runs the registry stub and this one makes the same point unambiguously.
    """

    name = "offset"

    def __init__(self, offset: float = 400.0):
        self.offset = offset

    def forecast(self, window: Window, horizon: int) -> Any:
        last = window.column(window.target)[-1]
        return _result(self.name, [last + self.offset] * horizon, 40.0, window)


class SortedLine:
    """A least-squares line through the SORTED context, one step past its end.

    This is the (b) forecaster, and sorting is the whole point. Its forecast depends on
    the multiset of the context and on nothing else, so `block_shuffle`, which permutes
    whole rows and therefore preserves the multiset exactly, cannot move it by one bit.
    E_P is then E_M, `E_M <= (1 - delta) E_P` reads `x <= 0.9x` and is false for every
    positive x, and the temporal branch is unreachable by construction rather than by
    luck of a seed.
    """

    name = "sorted_line"

    def forecast(self, window: Window, horizon: int) -> Any:
        ordered = sorted(window.column(window.target))
        return _result(self.name, _line(ordered, horizon), 20.0, window)


class TrueLine:
    """The same line fitted in TRUE order, which the placebo therefore destroys."""

    name = "true_line"

    def forecast(self, window: Window, horizon: int) -> Any:
        values = window.column(window.target)
        return _result(self.name, _line(values, horizon), 8.0, window)


class Parity:
    """The context row TWO back, which on the alternating fixture is the answer.

    It reads a position, so it reads the order; the placebo destroys it. This is the
    (f) forecaster and it never reaches the placebo branch, which is the point: the
    control on that fixture is invalid, so what it uses is a question the run cannot
    answer, and the rule has to say so rather than guess.
    """

    name = "parity"

    def forecast(self, window: Window, horizon: int) -> Any:
        values = window.column(window.target)
        return _result(
            self.name, [values[step % 2 - 2] for step in range(horizon)], 10.0, window
        )


class Licensed:
    """Persistence, declaring the four provenance fields a checkpoint carries.

    Test (c) has to check that a stored run and a printed report carry the weights
    licence. The one forecaster that really carries one is TimesFM, and loading it would
    download a 1.32 GB checkpoint and import torch, which this suite never does. What
    `backtest._declared` reads is four ATTRIBUTES off the instance, so a forecaster that
    declares them exercises the identical path.
    """

    name = "licensed"
    checkpoint = "test/checkpoint"
    license = "test-non-commercial-license-v1.0"
    device = "cpu"
    padding_mode = "edge"

    def forecast(self, window: Window, horizon: int) -> Any:
        last = window.column(window.target)[-1]
        return _result(self.name, [last] * horizon, 10.0, window)


def _line(values: list[float], horizon: int) -> list[float]:
    """Least squares over `values` against 0..n-1, evaluated at n, n+1, ..."""
    n = len(values)
    mean_x, mean_y = (n - 1) / 2, statistics.fmean(values)
    sxx = sum((x - mean_x) ** 2 for x in range(n))
    sxy = sum(
        (x - mean_x) * (y - mean_y) for x, y in zip(range(n), values, strict=True)
    )
    slope = sxy / sxx
    return [mean_y + slope * (n - mean_x + step) for step in range(horizon)]


def _forecasters(*extra: Any) -> dict[str, Any]:
    return {name: make(name) for name in BASELINES} | {one.name: one for one in extra}


# -- two request-clock fixtures, both built through the real compiler's column table ---


def _series(name: str, values: list[float], changepoints: list[int]) -> Series:
    """One target column of `values` and ten constant covariates, as a real Series.

    The width and the coverage words come from `series.columns`, so a column added to
    the request clock fails here rather than inside the backtester's `_block`.
    """
    specs = compiler.columns(dict.fromkeys(claude.CAPABILITIES, "observed"), "observed")
    rows: list[list[float | None]] = [
        [value, 20000.0, 400.0, 2500.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0,
         1.0 if index in changepoints else 0.0]
        for index, value in enumerate(values)
    ]  # fmt: skip
    assert len(rows[0]) == len(specs)
    cohort = {"capture_id": name, "provider": "synthetic", "content_level": None}
    return Series(
        series_id=compiler.series_id("request", cohort, specs, name, rows),
        clock="request",
        cohort=cohort,
        columns=specs,
        rows=rows,
        row_meta=[
            RowMeta(row_key=f"{name}_{index:04d}", row_end_ts=_stamp(index))
            for index in range(len(values))
        ],
        changepoints=changepoints,
        missingness_policy="exclude",
        reducer_version=name,
    )


def _stamp(index: int) -> str:
    return f"2026-01-01T{index // 3600:02d}:{index // 60 % 60:02d}:{index % 60:02d}.0Z"


def ramp(seed: int = 8, rows: int = RAMP_ROWS) -> Series:
    """A noisy straight line. Its multiset determines its order, which is the point."""
    dice = random.Random(seed)
    return _series(
        "ramp",
        [round(100.0 + 10.0 * index + dice.gauss(0, 3), 4) for index in range(rows)],
        [],
    )


def ramp_then_noise(seed: int = 3, rows: int = RAMP_ROWS) -> Series:
    """A ramp, a changepoint, then independent draws. One half has order in it."""
    dice = random.Random(seed)
    values = [
        round(
            100.0 + 10.0 * index + dice.gauss(0, 3)
            if index < SPLIT
            else 500.0 + dice.gauss(0, 1),
            4,
        )
        for index in range(rows)
    ]
    return _series("halves", values, [SPLIT])


def noise(seed: int = 11, rows: int = 200) -> Series:
    """Independent draws: nothing for a shuffle to destroy, which invalidates it."""
    dice = random.Random(seed)
    return _series("noise", [round(dice.gauss(1000, 100), 4) for _ in range(rows)], [])


def alternating(seed: int = 5, rows: int = 200) -> Series:
    """Two levels, strictly by parity. Persistence is at its worst in TRUE order.

    Persistence predicts y_{o-1}, which here is always the other level, so its
    true-order score is the largest one this series can hand it and no permutation can
    make it larger. The validity check therefore fails by construction rather than by
    the luck of a seed, and it fails while a forecaster reading y_{o-2} is exact. That
    is the pair the amendment needs: a control that controlled for nothing AND a model
    the baseline clause does not refuse.
    """
    dice = random.Random(seed)
    return _series(
        "alternating",
        [
            round((1500.0 if index % 2 else 500.0) + dice.gauss(0, 5), 4)
            for index in range(rows)
        ],
        [],
    )


# -- (d) the shuffle itself ------------------------------------------------------------


def test_block_shuffle_keeps_every_row_and_costs_persistence_its_score(
    store: Store,
) -> None:
    """The multiset survives, the order does not, and the trivial baseline pays for it.

    Both halves matter. A shuffle that dropped or edited a row would make the placebo a
    control for two things at once, so the sorted rows are compared cell by cell. And a
    shuffle that never reached a forecaster would leave every score untouched, so
    persistence, which reads exactly one row of the context, is scored on the shuffled
    contexts and must be worse under every one of the five seeds.
    """
    written = synthetic_series.write(store, rows=200, seed=1)
    built = store.series(written.series_id)
    assert built is not None

    plan = backtester.plan(built, TARGET, 1)
    block = placebo_block(1)
    for window in plan.windows[:8]:
        for seed in range(PLACEBO_SEEDS):
            moved = placebos.block_shuffle(window.rows, block, seed)
            assert sorted(moved) == sorted(window.rows)
            assert len(moved) == len(window.rows)
        # Blocks of 2 over an even context cannot leave every row in place, and this is
        # the claim the equality above cannot make: the multiset is right AND the order
        # is different.
        assert placebos.block_shuffle(window.rows, block, 0) != window.rows

    truth = backtester.run(built, TARGET, 1, _forecasters())
    shuffled = placebos.run(built, TARGET, 1, _forecasters())
    check = placebos.sentinel(truth, shuffled)
    assert check["n_runs"] == 2 * PLACEBO_SEEDS
    assert check["n_worse"] == check["n_runs"], check["placebo_mae_mean"]
    assert check["valid"] is True
    assert check["true_mae_mean"] == pytest.approx(90.4338, abs=1e-3)


def test_the_shuffle_reaches_the_context_and_nothing_else(store: Store) -> None:
    """Every placebo window is its true-order twin in origin, actual and y_{o-1}.

    This is what makes the pairing legitimate. `backtest.run` builds the window record
    from the true rows and only then hands the window to the `prepare` hook, so a
    placebo run answers the same question at the same origins against the same actuals.
    """
    written = synthetic_series.write(store, rows=200, seed=1)
    built = store.series(written.series_id)
    assert built is not None

    truth = backtester.run(built, TARGET, 1, _forecasters())
    keys = ("origin", "ctx_start", "n_ctx", "horizon", "actual", "last_context")
    expected = [{key: record[key] for key in keys} for record in truth["windows"]]
    for one in placebos.run(built, TARGET, 1, _forecasters()):
        assert [{key: record[key] for key in keys} for record in one["windows"]] == (
            expected
        )
        assert one["ordering"] in (ORDERING_BLOCK, ORDERING_ROW)
        assert one["placebo_seed"] in range(PLACEBO_SEEDS)


# -- (a) worse than the baselines ------------------------------------------------------


def test_a_stub_worse_than_persistence_is_baseline_sufficient(store: Store) -> None:
    """Persistence plus 400 tokens, on the walk. Both clauses of the rule fire."""
    written = synthetic_series.write(store, rows=200, seed=1)
    built = store.series(written.series_id)
    assert built is not None

    found = placebos.paired(built, TARGET, 1, _forecasters(Offset()), "offset")
    decision = found["decision"]
    assert decision.label == decider.BASELINE_SUFFICIENT
    assert decision.best_baseline == "persistence"
    assert decision.e_m > (1.0 - DELTA) * decision.e_b
    assert decision.w_mb < W
    # The rule stops at the first branch it can take, so the placebo half was not
    # evaluated: baseline sufficient needs no control, which is why E07 could label 19
    # pairs without one.
    assert [item["test"] for item in decision.inequalities] == [
        "E_M > (1 - delta) E_B",
        "W_MB < w",
    ]
    assert all(item["holds"] for item in decision.inequalities)
    assert found["sentinel"]["valid"] is True


def test_the_placebo_command_stores_every_run_and_prints_both_sides(
    store: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole path through the real CLI, and every field (c) says a row must carry.

    The registry stub `echo` is the model here, because that is what the command runs
    when nobody names one. Its mean MAE on this series is persistence's to the last bit
    and it wins exactly half the windows, so `W_MB < w` is what refuses it.
    """
    written = synthetic_series.write(store, rows=200, seed=1)
    store.close()
    argv = ["forecast", "placebo", "--series", written.series_id, "--target", TARGET]
    assert cli.main(argv) == 0
    printed = capsys.readouterr().out

    assert f"decision: {decider.BASELINE_SUFFICIENT}" in printed
    assert "E_M > (1 - delta) E_B  90.4338  81.3904  yes" in printed
    assert "W_MB < w               0.5      0.6      yes" in printed
    assert "10 of 10 placebo runs worse -> valid" in printed
    # The pre-registered constants, printed beside the label they were applied under.
    assert "delta 0.1  w 0.6  k_min 20  c_min 32" in printed
    assert "placebo_block 2  placebo_seeds 5  placebo_row_block 1" in printed
    assert "demotion_resolution 0.25" in printed

    rows = store.forecast_runs(written.series_id)
    assert len(rows) == 1 + 2 * PLACEBO_SEEDS
    counted = [row["ordering"] for row in rows]
    assert counted.count(ORDERING_TRUE) == 1
    assert counted.count(ORDERING_BLOCK) == PLACEBO_SEEDS
    assert counted.count(ORDERING_ROW) == PLACEBO_SEEDS
    for row in rows:
        _assert_stored(row)
    truth = next(row for row in rows if row["ordering"] == ORDERING_TRUE)
    assert truth["placebo_seed"] is None
    assert truth["decision"]["label"] == decider.BASELINE_SUFFICIENT
    floor = truth["decision"]["inequalities"][0]["rhs"]
    assert floor == pytest.approx(81.3904, abs=1e-3)
    seeds = [row["placebo_seed"] for row in rows if row["ordering"] == ORDERING_BLOCK]
    assert seeds == list(range(PLACEBO_SEEDS))


def _assert_stored(row: dict[str, Any]) -> None:
    """(c) Everything design 6.12 says a stored forecast run must carry."""
    assert row["claim_class"] == "predictive"
    assert row["missingness_policy"] == "exclude"
    assert row["scenario"]["placebo"] == {
        "block": 2,
        "row_block": 1,
        "seeds": list(range(PLACEBO_SEEDS)),
        "generator": "random.Random(seed).shuffle over consecutive whole-row blocks",
        "shuffles": "context only; origin, test rows and actual are true order",
    }
    # The baseline OUTPUTS, not only their scores: every retained window holds a point
    # and a band per baseline, which is what makes a stored run re-scorable.
    first = row["windows"]["retained"][0]
    for name in BASELINES:
        assert len(first["forecasts"][name]["point"]) == row["horizon"]
        assert len(first["forecasts"][name]["quantiles"][0]) == len(QUANTILE_LEVELS)
    # The licence FIELD is on every declared forecaster. It is None for a baseline and
    # for the stub, because a forecaster with no weights has no licence, and None is not
    # the same statement as a missing key.
    assert [entry["name"] for entry in row["forecasters"]]
    for entry in row["forecasters"]:
        assert set(entry) == {"name", "checkpoint", "license", "device", "padding_mode"}


def test_a_declared_licence_reaches_the_report_and_the_stored_row(store: Store) -> None:
    """The weights-licence line of design 6.12, without loading a 1.32 GB checkpoint."""
    written = synthetic_series.write(store, rows=200, seed=1)
    built = store.series(written.series_id)
    assert built is not None

    run = backtester.run(built, TARGET, 1, _forecasters(Licensed()))
    printed = backtester.report(run)
    assert (
        "weights: test/checkpoint under test-non-commercial-license-v1.0"
        " (research use, not production), device cpu, padding_mode edge"
    ) in printed
    run_id = backtester.persist(store, run)
    stored = next(
        row for row in store.forecast_runs(written.series_id)
        if row["forecast_run_id"] == run_id
    )  # fmt: skip
    entry = next(one for one in stored["forecasters"] if one["name"] == "licensed")
    assert entry["license"] == "test-non-commercial-license-v1.0"
    assert entry["checkpoint"] == "test/checkpoint"


# -- (b) better than the baselines, and unmoved by the placebo -------------------------


def test_a_multiset_stub_that_beats_the_baselines_is_a_conditional_prediction(
    store: Store,
) -> None:
    """E_P is E_M to the last bit, so the temporal branch cannot be taken.

    On a noisy straight line the four baselines all read at most the last 8 rows, so
    each is estimating the level from a short window; the stub fits a line to the whole
    context and its error is the noise of the one row it is forecasting. It beats the
    best baseline by more than delta and on more than w of the windows, so the run is
    not baseline sufficient, and it sorts its context first, so the placebo returns the
    identical number. That is the definition of a conditional prediction.
    """
    built = ramp()
    store.put_series(built)
    read = store.series(built.series_id)
    assert read is not None

    found = placebos.paired(read, TARGET, 1, _forecasters(SortedLine()), "sorted_line")
    decision = found["decision"]
    assert found["sentinel"]["valid"] is True
    assert decision.label == decider.CONDITIONAL_PREDICTION
    assert decision.best_baseline == "local_drift"
    assert decision.e_m == pytest.approx(2.5263, abs=1e-3)
    assert decision.e_b == pytest.approx(3.9610, abs=1e-3)
    assert decision.w_mb == pytest.approx(0.6978, abs=1e-3)
    # The claim of this test, stated as an equality rather than an approximation: a
    # permutation of whole rows cannot change a function of the multiset.
    assert decision.e_p == decision.e_m
    assert decision.w_mp == 0.0
    assert [item["holds"] for item in decision.inequalities] == [
        False,
        False,
        False,
        False,
    ]
    # Not the level-prediction wording: this variant carries ten covariates.
    assert decision.covariate_free is False
    assert decision.notes == []


def test_a_covariate_free_variant_says_level_prediction(store: Store) -> None:
    """The same run with no covariates at all names what is left to be reading."""
    built = ramp()
    store.put_series(built)
    read = store.series(built.series_id)
    assert read is not None

    found = placebos.paired(
        read, TARGET, 1, _forecasters(SortedLine()), "sorted_line", covariates=[]
    )
    decision = found["decision"]
    assert decision.label == decider.CONDITIONAL_PREDICTION
    assert decision.covariate_free is True
    assert decision.notes == [decider.LEVEL_PREDICTION]
    assert "level prediction: the gain is distributional" in placebos.report(found)


# -- the anti-cherry-pick clause -------------------------------------------------------


def test_the_half_clause_refuses_a_label_the_whole_range_earns(store: Store) -> None:
    """Both temporal inequalities hold over 236 windows and fail in the second half.

    The fixture is a ramp for 150 rows, a changepoint, then independent draws, so no
    context straddles the boundary and the two halves of the origin range are exactly
    the two regimes. A line fitted in true order beats the baselines everywhere and
    beats its placebo only where there is an order to destroy. Pooled, the ramp half
    carries both inequalities past their thresholds; separately, the noise half does
    not. With the half clause the label is conditional prediction. Delete the clause
    from `decide._label` and this test reads temporal evolution and fails, which is the
    only way to know the clause is load-bearing.
    """
    built = ramp_then_noise()
    store.put_series(built)
    read = store.series(built.series_id)
    assert read is not None

    found = placebos.paired(read, TARGET, 1, _forecasters(TrueLine()), "true_line")
    decision = found["decision"]
    assert decision.n_windows == 236
    assert [item["holds"] for item in decision.inequalities] == [
        False,
        False,
        True,
        True,
    ]
    first, second = decision.halves
    assert (first["half"], first["origins"], first["n_windows"]) == (
        "first",
        [32, 149],
        118,
    )
    assert (second["half"], second["origins"], second["n_windows"]) == (
        "second",
        [182, 299],
        118,
    )
    assert first["both_hold"] is True
    assert second["both_hold"] is False
    assert second["w_mp"] == pytest.approx(0.4407, abs=1e-3)
    assert decision.label == decider.CONDITIONAL_PREDICTION


# -- the two refusals the rule makes ---------------------------------------------------


def test_a_backtest_alone_withholds_the_label(
    store: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    """`forecast backtest` prints no label, and says which command would earn one."""
    written = synthetic_series.write(store, rows=200, seed=1)
    store.close()
    assert cli.main([
        "forecast", "backtest", "--series", written.series_id, "--target", TARGET,
    ]) == 0  # fmt: skip
    printed = capsys.readouterr().out
    assert "placebo not run: label withheld" in printed
    # The three words appear inside the reason, which is what the reason is FOR; what
    # must not appear is a decision line carrying one of them as the verdict.
    assert f"decision: {decider.NOT_ASSESSABLE} (" in printed
    for label in (decider.BASELINE_SUFFICIENT, decider.TEMPORAL_EVOLUTION):
        assert f"decision: {label}" not in printed
    assert f"decision: {decider.CONDITIONAL_PREDICTION}" not in printed
    assert "telltale forecast placebo --series" in printed
    row = store.forecast_runs(written.series_id)[0]
    assert row["ordering"] == ORDERING_TRUE
    assert row["decision"] is None


def test_an_invalid_placebo_still_earns_baseline_sufficient_with_the_warning(
    store: Store,
) -> None:
    """(f) 6.12 as amended by W3-E08b. The control failed; the comparison stands.

    Independent draws carry no recency, so the row the permutation put last is as good
    as the row that was there and persistence scores about the same either way: the
    control controlled for nothing. The stub is persistence plus 400 tokens, so both
    baseline clauses fire on the true-order windows, and neither of them reads a
    placebo. The label is therefore written, the two labels that WOULD have read the
    placebo are named as lost in the warning, and the run keeps its invalidity warning.
    """
    built = noise()
    store.put_series(built)
    read = store.series(built.series_id)
    assert read is not None

    found = placebos.paired(read, TARGET, 1, _forecasters(Offset()), "offset")
    check = found["sentinel"]
    assert check["valid"] is False
    assert 0 < check["n_worse"] < check["n_runs"]

    decision = found["decision"]
    assert decision.label == decider.BASELINE_SUFFICIENT
    assert decision.placebo_valid is False
    assert decision.reason is None
    assert decision.notes == [PLACEBO_INVALID_WARNING]
    assert [item["test"] for item in decision.inequalities] == [
        "E_M > (1 - delta) E_B",
        "W_MB < w",
    ]
    assert all(item["holds"] for item in decision.inequalities)
    # The run still says its control failed. The label and the warning are two
    # statements and the amendment keeps both.
    assert placebos.INVALID in found["truth"]["warnings"]
    printed = placebos.report(found)
    assert "-> INVALID" in printed
    assert f"decision: {decider.BASELINE_SUFFICIENT}" in printed
    assert f"note: {PLACEBO_INVALID_WARNING}" in printed


def test_an_invalid_placebo_refuses_the_two_labels_that_read_it(store: Store) -> None:
    """(f) The other half of the amendment: nothing left for the label to be.

    The fixture alternates between two levels, so persistence, which predicts y_{o-1},
    is always exactly one level out and scores the worst number the series can give it.
    No permutation can beat that, so 0 of the 10 placebo runs come out worse and the
    control is invalid by construction. The forecaster reads y_{o-2} and is exact, so
    the baseline clause does not fire: E_M is far below (1 - delta) E_B and W_MB is far
    above w. Everything that is left reads the placebo, so the answer is a refusal, and
    it is printed with the baseline inequalities that got it there.
    """
    built = alternating()
    store.put_series(built)
    read = store.series(built.series_id)
    assert read is not None

    found = placebos.paired(read, TARGET, 1, _forecasters(Parity()), "parity")
    check = found["sentinel"]
    assert check["valid"] is False
    assert check["n_worse"] == 0
    assert check["n_runs"] == 2 * PLACEBO_SEEDS

    decision = found["decision"]
    assert decision.label == decider.NOT_ASSESSABLE
    assert decision.reason == decider.INVALID_PLACEBO
    assert decision.placebo_valid is False
    assert decision.notes == []
    # The baseline clause was evaluated and both halves of it failed, which is exactly
    # what makes this row different from the one above.
    assert [item["test"] for item in decision.inequalities] == [
        "E_M > (1 - delta) E_B",
        "W_MB < w",
    ]
    assert not any(item["holds"] for item in decision.inequalities)
    assert decision.e_m < (1.0 - DELTA) * decision.e_b
    assert decision.w_mb >= W
    printed = placebos.report(found)
    assert f"decision: {decider.NOT_ASSESSABLE}  (placebo invalid:" in printed
    assert "E_M > (1 - delta) E_B" in printed


# -- (e) the word refusal --------------------------------------------------------------


def test_a_report_that_claims_a_cause_raises_and_the_sentence_passes() -> None:
    """ADR-014, on the real renderers rather than on the checker alone."""
    for bad in ("this would help", "the cause of it", "a large impact", "IMPACTED it"):
        with pytest.raises(ForbiddenWord):
            refuse_words(bad)
    # `because` holds the letters of `cause` and is not a hit: there is no word boundary
    # in front of them. The mandatory candidate sentence contains it, and passes.
    assert refuse_words(CANDIDATE_SENTENCE) == CANDIDATE_SENTENCE
    assert "because" in CANDIDATE_SENTENCE


def test_the_renderer_refuses_before_it_prints_or_stores(store: Store) -> None:
    """A run whose assumptions claim a cause never reaches the report or the row."""
    written = synthetic_series.write(store, rows=200, seed=1)
    built = store.series(written.series_id)
    assert built is not None
    run = backtester.run(built, TARGET, 1, _forecasters())
    assert backtester.report(run)

    run["assumptions"] = [*run["assumptions"], "the compaction would have been avoided"]
    with pytest.raises(ForbiddenWord) as refused:
        backtester.report(run)
    assert "would" in str(refused.value)
    assert store.forecast_runs(written.series_id) == []


def test_one_score_two_labels_and_the_placebo_is_the_whole_difference(
    store: Store,
) -> None:
    """The same series, the same MAE, and two labels. This is what the placebo buys.

    Both forecasters fit a straight line to the context and step one row past it. One
    sorts the context first and one does not, and on a noisy ramp sorting very nearly
    recovers the true order, so the two agree on E_M to three decimals and are ranked
    identically against the baselines. The placebo separates them completely: a
    permutation cannot move the sorted fit at all, and it destroys the unsorted one. A
    reader given only the backtest table could not tell these two apart.
    """
    built = ramp()
    store.put_series(built)
    read = store.series(built.series_id)
    assert read is not None

    blind = placebos.paired(read, TARGET, 1, _forecasters(SortedLine()), "sorted_line")
    sighted = placebos.paired(read, TARGET, 1, _forecasters(TrueLine()), "true_line")
    assert blind["decision"].e_m == pytest.approx(sighted["decision"].e_m, abs=1e-3)
    assert blind["decision"].e_b == sighted["decision"].e_b
    assert blind["decision"].w_mb == sighted["decision"].w_mb
    assert blind["decision"].label == decider.CONDITIONAL_PREDICTION
    assert sighted["decision"].label == decider.TEMPORAL_EVOLUTION
    assert blind["decision"].e_p == blind["decision"].e_m
    assert sighted["decision"].e_p > 700.0
    assert (blind["decision"].w_mp, sighted["decision"].w_mp) == (0.0, 1.0)
    assert all(half["both_hold"] for half in sighted["decision"].halves)
