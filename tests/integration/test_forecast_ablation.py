"""The A/B/C ablation and the one-step candidate protocol. Design 6.12, H7 and H8.

Both run on the change clock, and W3-T1 is building the real change-clock compiler in
parallel, so the series here is `synthetic_series.write_change`: eighteen columns of
seeded numbers written through the real store, past the real CHECK constraints, and read
back through `Store.series`. Nothing below claims anything about any repository.

What each half is for.

  The ablation asks whether the C columns (how the session behaved) forecast a change
  better than the A columns (what the change is) and the B columns (what it cost). Its
  answer here is "diagnostic only", and the interesting part of that answer is WHY: the
  three variants score identically to the last bit, because no forecaster in the default
  set reads a covariate at all. The runner says so in a warning rather than letting a
  reader take the verdict for a statement about the columns.

  The candidate protocol asks how much a change's own known features move a forecast of
  what happens after it is merged. Its answer here is 0 for the same reason, and it says
  so the same way. What the tests below can check without a model is everything else:
  that the two runs are paired by origin, that a delayed label stops three rows early,
  that the target which is already known at merge time is refused, and that the
  mandatory sentence is on both stored rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import synthetic_series

from telltale import cli
from telltale.forecast import (
    ABLATION_A,
    ABLATION_B,
    ABLATION_C,
    CANDIDATE_FORBIDDEN,
    CANDIDATE_SENTENCE,
    MAX_VARIATES,
    ORDERING_BLOCK,
    ORDERING_ROW,
    ORDERING_TRUE,
    PLACEBO_SEEDS,
    REWORK_TAIL,
    REWORK_TARGET,
    make,
)
from telltale.forecast import ablate as ablator
from telltale.forecast import candidate as protocol
from telltale.forecast import decide as decider
from telltale.forecast.backtest import Refused

if TYPE_CHECKING:
    from collections.abc import Callable

    from telltale.model import Series
    from telltale.store import Store

pytestmark = pytest.mark.integration

NAMES = ("persistence", "rolling_median", "rolling_mean", "local_drift", "echo")
MODEL = "echo"
CHANGE_ROWS = 60
# c_min is 16 on the change clock, H is 1 and the series has no changepoint, so the
# origins are range(16, 60) and every one of them is retained. A delayed label stops at
# o <= N - 3, which is origin 57, so it keeps 57 - 16 + 1 of them.
C_MIN = 16
ORIGINS = CHANGE_ROWS - C_MIN
DELAYED_ORIGINS = CHANGE_ROWS - REWORK_TAIL - C_MIN + 1


def _factory() -> Callable[[], dict[str, Any]]:
    return lambda: {name: make(name) for name in NAMES}


def _change(store: Store) -> Series:
    written = synthetic_series.write_change(store, rows=CHANGE_ROWS, seed=1)
    read = store.series(written.series_id)
    assert read is not None, "the change series must come back through the real reader"
    return read


def test_the_change_series_carries_the_eighteen_columns_of_design_6_12(
    store: Store,
) -> None:
    """Block C plus the three post-merge targets, round-tripped through the store."""
    built = _change(store)
    assert built.clock == "change"
    assert [column.name for column in built.columns] == list(
        synthetic_series.CHANGE_COLUMNS
    )
    assert len(ABLATION_A) == 6
    assert len(ABLATION_B) == 11
    assert len(ABLATION_C) == 15
    assert set(ABLATION_A) < set(ABLATION_B) < set(ABLATION_C)
    assert len(built.rows) == CHANGE_ROWS
    assert all(value is not None for row in built.rows for value in row)


# -- the A/B/C ablation ----------------------------------------------------------------


def test_the_three_variants_run_over_identical_origins(store: Store) -> None:
    """Same origins, same actuals, nested covariate sets, and 15 variates at C.

    Identical origins is the claim that makes the three numbers comparable at all, and
    it is checked here rather than assumed: the runner intersects the three origin sets
    and rescores on the intersection, and on this series nothing is dropped, so the
    three window lists must be equal record for record on origin and actual.
    """
    built = _change(store)
    found = ablator.run(built, "attempts_to_land", 1, _factory(), MODEL)

    assert found["aligned_origins"] == ORIGINS
    assert found["dropped_for_alignment"] == {"A": 0, "B": 0, "C": 0}
    keys = ("origin", "ctx_start", "n_ctx", "actual")
    shapes = [
        [
            {key: record[key] for key in keys}
            for record in found["runs"][name]["windows"]
        ]
        for name in ("A", "B", "C")
    ]
    assert shapes[0] == shapes[1] == shapes[2]

    # The target is a column of block B, and it is excluded from its own covariates.
    assert "attempts_to_land" not in found["runs"]["B"]["covariates"]
    variates = {
        name: 1 + len(found["runs"][name]["covariates"]) for name in ("A", "B", "C")
    }
    assert variates == {"A": 7, "B": 11, "C": 15}
    assert variates["C"] == MAX_VARIATES

    assert found["verdict"]["label"] == ablator.DIAGNOSTIC
    assert found["verdict"]["E_A"] == found["verdict"]["E_C"]
    assert found["warnings"] == [ablator.NO_COVARIATE_READER]
    assert [item["holds"] for item in found["verdict"]["tests"]] == [False, False]
    # The winner's placebo, beside it, and valid: the change series walks, so the
    # shuffle has recency to destroy and persistence pays for it.
    assert found["placebo"]["sentinel"]["valid"] is True
    assert found["placebo"]["sentinel"]["n_worse"] == 2 * PLACEBO_SEEDS


def test_the_ablation_refuses_a_sixteenth_variate(store: Store) -> None:
    """Design 6.12 caps a variant at 15 variates. It refuses, and never subsamples."""
    built = _change(store)
    # Sixteen columns none of which is the target, so none is dropped as the target's
    # own covariate: 1 + 16 variates against a cap of 15.
    wide = (*ABLATION_C, "merge_verification_ms")
    assert len(wide) == 16
    assert REWORK_TARGET not in wide
    blocks = {"A": ABLATION_A, "B": ABLATION_B, "C": wide}
    with pytest.raises(Refused) as refused:
        ablator.run(built, REWORK_TARGET, 1, _factory(), MODEL, blocks=blocks)
    assert f"against the cap of {MAX_VARIATES}" in str(refused.value)


def test_the_ablate_command_stores_the_variants_and_the_winners_placebo(
    store: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole path through the real CLI, and the rows it leaves on the disk."""
    built = _change(store)
    store.close()
    argv = ["forecast", "ablate", "--series", built.series_id]
    assert cli.main([*argv, "--target", "verification_cycles"]) == 0
    printed = capsys.readouterr().out
    assert "verdict: C diagnostic only" in printed
    assert "E_C <= (1 - delta) min(E_A, E_B)" in printed
    assert ablator.NO_COVARIATE_READER in printed
    assert "the placebo of the winning variant" in printed
    assert "c_min 16" in printed, "the change clock's floor, not the request clock's"

    rows = store.forecast_runs(built.series_id)
    # Three variant rows, then the placebo pair: one true-order run and 2R placebos.
    assert len(rows) == 3 + 1 + 2 * PLACEBO_SEEDS
    variants = sorted(
        row["variant"] for row in rows if row["ordering"] == ORDERING_TRUE
    )
    assert variants == [
        "change_past_only_a",
        "change_past_only_a",
        "change_past_only_b",
        "change_past_only_c",
    ], "A won, so it is stored twice: as the ablation variant and as the placebo's twin"
    assert all(row["claim_class"] == "predictive" for row in rows)
    assert all(row["c_min"] == 16 for row in rows)
    assert sum(row["ordering"] == ORDERING_BLOCK for row in rows) == PLACEBO_SEEDS
    assert sum(row["ordering"] == ORDERING_ROW for row in rows) == PLACEBO_SEEDS
    decided = [row for row in rows if row["decision"] is not None]
    assert len(decided) == 1
    assert decided[0]["decision"]["label"] in decider.LABELS


# -- the one-step candidate protocol ---------------------------------------------------


def test_a_candidate_run_pairs_by_origin_and_stores_the_sentence(
    store: Store,
) -> None:
    """Two runs over the same origins, and the sentence on both of the stored rows."""
    built = _change(store)
    forecasters = {name: make(name) for name in NAMES}
    found = protocol.conditioned(
        store, built, "merge_verification_ms", forecasters, MODEL
    )

    plain = found["runs"][protocol.UNCONDITIONED]["windows"]
    fitted = found["runs"][protocol.CONDITIONED]["windows"]
    assert [record["origin"] for record in plain] == [
        record["origin"] for record in fitted
    ]
    assert [record["actual"] for record in plain] == [
        record["actual"] for record in fitted
    ]
    assert found["paired"]["n_paired"] == ORIGINS
    # No forecaster here reads a past-future covariate, so the difference is 0 and the
    # run says so instead of letting a reader take 0 for a measurement of a candidate.
    assert found["paired"]["mae_median_difference"] == 0.0
    assert found["paired"]["pinball_median_difference"] == 0.0
    assert found["warnings"] == [protocol.NO_FUTURE_READER]
    assert CANDIDATE_SENTENCE in protocol.report(found)

    stored = [
        row
        for row in store.forecast_runs(built.series_id)
        if row["forecast_run_id"] in found["forecast_run_ids"]
    ]
    assert len(stored) == 2
    for row in stored:
        assert CANDIDATE_SENTENCE in row["assumptions"]
        assert row["claim_class"] == "predictive"
        assert row["missingness_policy"] == "exclude"
        assert row["variant"].startswith("change_past_only_candidate_")


def test_the_conditioned_window_carries_the_a_block_one_row_past_the_context(
    store: Store,
) -> None:
    """The past-future covariate is n_ctx + H long and ends on the candidate's own row.

    This is what makes the run "conditioned": the last column of the block is row o,
    which is known at the moment the merge decision is taken and is the one row the
    unconditioned run cannot see.
    """
    built = _change(store)
    seen: list[Any] = []

    class Keeper:
        name = "keeper"

        def forecast(self, window: Any, horizon: int) -> Any:
            seen.append(window)
            return make("persistence").forecast(window, horizon)

    protocol.conditioned(None, built, "rework_within_3", {"keeper": Keeper()}, "keeper")
    conditioned = [window for window in seen if window.future is not None]
    assert len(conditioned) == len(seen) // 2
    index = [column.name for column in built.columns].index("files_changed")
    for window in conditioned:
        assert sorted(window.future) == sorted(ABLATION_A)
        block = window.future["files_changed"]
        assert len(block) == window.n_ctx + window.horizon
        assert block[-1] == built.rows[window.origin][index]
        assert block[0] == built.rows[window.ctx_start][index]


def test_the_candidate_protocol_refuses_the_target_known_at_merge_time(
    store: Store,
) -> None:
    """`attempts_to_land` is known when the decision is taken, so it is not a target.

    Break this by deleting the refusal in `candidate.conditioned` and the run happens:
    the A block and the attempt count come from the same landed change, and the protocol
    would be scoring a lookup while printing the sentence that says it is not one.
    """
    built = _change(store)
    forecasters = {name: make(name) for name in NAMES}
    with pytest.raises(Refused) as refused:
        protocol.conditioned(None, built, CANDIDATE_FORBIDDEN, forecasters, MODEL)
    assert CANDIDATE_FORBIDDEN in str(refused.value)
    assert "known at merge time" in str(refused.value)
    # And it fires before any forecaster is called, which is why an empty set of them
    # reaches the same refusal rather than a KeyError from somewhere inside the run.
    with pytest.raises(Refused):
        protocol.conditioned(None, built, CANDIDATE_FORBIDDEN, {}, MODEL)
    # It is still a legitimate ABLATION target and a legitimate covariate, which is why
    # the refusal names the protocol rather than the column.
    assert CANDIDATE_FORBIDDEN in ABLATION_B


def test_a_delayed_label_stops_three_rows_early(store: Store) -> None:
    """`rework_within_3` is known three changes later, so its origins stop early."""
    built = _change(store)
    forecasters = {name: make(name) for name in NAMES}
    delayed = protocol.conditioned(None, built, REWORK_TARGET, forecasters, MODEL)
    prompt = protocol.conditioned(
        None, built, "merge_verification_failed", forecasters, MODEL
    )

    windows = delayed["runs"][protocol.UNCONDITIONED]["windows"]
    assert max(record["origin"] for record in windows) == CHANGE_ROWS - REWORK_TAIL
    assert delayed["paired"]["n_paired"] == DELAYED_ORIGINS == 42
    assert prompt["paired"]["n_paired"] == ORIGINS
    assert f"o <= N - {REWORK_TAIL}" in delayed["origin_limit"]["reason"]
