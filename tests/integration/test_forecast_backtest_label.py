"""`telltale forecast backtest` labels the row it stores. W3-V finding 1.

Split from test_forecast_decision.py by the 800-line ratchet. The stubs, the series
makers and the forecaster dict are imported from there rather than copied: one
definition of what a run under test is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import synthetic_series
from test_forecast_decision import TARGET, SortedLine, _forecasters, ramp

from telltale import cli
from telltale.forecast import ORDERING_TRUE, PLACEBO_ABSENT_WARNING, PLACEBO_SEEDS
from telltale.forecast import backtest as backtester
from telltale.forecast import decide as decider
from telltale.forecast import placebo as placebos

if TYPE_CHECKING:
    from telltale.store import Store

pytestmark = pytest.mark.integration


def test_a_backtest_alone_labels_its_row_through_the_same_rule(
    store: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    """W3-V finding 1: `forecast backtest` stores a label and its inequalities.

    The echo stub is persistence plus 400 tokens, so on the random walk the baseline
    clause fires on the true-order windows, and it reads no placebo: the label is
    "baseline sufficient" (6.12 as amended by W3-E08b), the note names the two labels
    that could not be tried, and the stored row carries all of it. Before the fix the
    stored decision was NULL and the printed reason was "placebo not run" whether or
    not one had been.
    """
    written = synthetic_series.write(store, rows=200, seed=1)
    store.close()
    assert cli.main([
        "forecast", "backtest", "--series", written.series_id, "--target", TARGET,
    ]) == 0  # fmt: skip
    printed = capsys.readouterr().out
    assert f"decision: {decider.BASELINE_SUFFICIENT}" in printed
    assert f"note: {PLACEBO_ABSENT_WARNING}" in printed
    assert "E_M > (1 - delta) E_B" in printed
    assert "placebo rows stored for this pair" not in printed
    assert "telltale forecast placebo --series" not in printed
    row = store.forecast_runs(written.series_id)[0]
    assert row["ordering"] == ORDERING_TRUE
    decision = row["decision"]
    assert decision["label"] == decider.BASELINE_SUFFICIENT
    assert decision["placebo_valid"] is None
    assert decision["placebo"]["n_runs"] == 0
    assert [item["test"] for item in decision["inequalities"]] == [
        "E_M > (1 - delta) E_B",
        "W_MB < w",
    ]
    assert any(item["holds"] for item in decision["inequalities"])
    assert decision["notes"] == [PLACEBO_ABSENT_WARNING]


def test_a_backtest_after_a_placebo_names_the_stored_pair(
    store: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    """W3-V break 8: a pair with placebo rows in the store is not \"placebo not run\".

    The placebo rows are not reused: a placebo is a control for the run it was made
    for. They are counted and the newest is named, so the reader can find the label
    that was earned with them.
    """
    written = synthetic_series.write(store, rows=200, seed=1)
    store.close()
    assert cli.main([
        "forecast", "placebo", "--series", written.series_id, "--target", TARGET,
    ]) == 0  # fmt: skip
    capsys.readouterr()
    assert cli.main([
        "forecast", "backtest", "--series", written.series_id, "--target", TARGET,
    ]) == 0  # fmt: skip
    printed = capsys.readouterr().out
    rows = store.forecast_runs(written.series_id)
    placebo_rows = [row for row in rows if row["ordering"] != ORDERING_TRUE]
    assert len(placebo_rows) == 2 * PLACEBO_SEEDS
    assert (
        f"placebo rows stored for this pair: {len(placebo_rows)}"
        f" (newest {placebo_rows[-1]['forecast_run_id']})"
    ) in printed
    assert "label withheld" not in printed
    assert rows[-1]["ordering"] == ORDERING_TRUE
    assert rows[-1]["decision"]["label"] == decider.BASELINE_SUFFICIENT


def test_a_run_that_beats_the_baselines_with_no_placebo_is_not_assessable(
    store: Store,
) -> None:
    """The other branch of `unpaired`: a gain nothing has controlled earns no label.

    SortedLine beats every baseline on the ramp (the conditional-prediction test
    above measures it), so the baseline clause does not fire; with no placebo the
    rule stops there, names the reason, and the two inequalities it did evaluate are
    stored beside the metrics.
    """
    built = ramp()
    store.put_series(built)
    read = store.series(built.series_id)
    assert read is not None
    run = backtester.run(read, TARGET, 1, _forecasters(SortedLine()))
    decision = placebos.unpaired(run, "sorted_line")
    assert decision.label == decider.NOT_ASSESSABLE
    assert decision.reason == decider.NO_PLACEBO
    assert decision.placebo_valid is None
    assert decision.e_p is None
    assert [item["holds"] for item in decision.inequalities] == [False, False]
    run_id = backtester.persist(store, run)
    stored = store.forecast_runs(built.series_id)
    assert [row["forecast_run_id"] for row in stored] == [run_id]
    assert stored[0]["decision"]["label"] == decider.NOT_ASSESSABLE
    assert stored[0]["decision"]["reason"] == decider.NO_PLACEBO
