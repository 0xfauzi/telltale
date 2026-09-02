"""The chronology placebo. Design 6.12.

A backtest that beats four baselines has shown that a forecaster is better than four
one-line rules. It has not shown that the forecaster used the ORDER of the past, and
that is the claim a time-series result is read as making. The placebo is the control
that separates the two: run the identical backtest again with the past scrambled, and
see how much of the score survives.

Scrambling has to preserve everything except the order, or the control tests two things
at once. So the context is cut into consecutive blocks of B whole rows and the blocks
are permuted. Whole rows move together, so every row still holds the values it held and
a covariate still sits beside the target value it was measured with; the multiset of
rows is preserved exactly. What is destroyed is recency and any dependence at a lag
above B. B = 1 is the second control and destroys dependence at every lag.

Three rules make the result mean something, and each of them is a refusal.

  ONLY the context moves. The origin, the test rows and the actual are the true ones,
  because `backtest.run` builds the window record before the `prepare` hook rewrites
  the window. That is what pairs every placebo window with its true-order twin by
  origin, and a paired comparison is the only one a share-of-windows statistic can be
  computed from.

  The baselines run on the shuffled contexts too. They are not a fixed reference: they
  read the same context the model reads, and persistence reads the last row of it.

  Persistence MUST get worse. Persistence is `y_{o-1}`, so on a shuffled context it
  reads whatever row the permutation put last. If that is not worse than the true
  order, either the series carries no recency at all or the shuffle did not reach the
  forecasters, and the run is invalid. What that costs is the two labels that READ the
  placebo, temporal evolution and conditional prediction, and nothing else: the
  baseline comparison is made on the true-order windows alone and stands, with a
  warning (6.12 as amended by W3-E08b, and `decide` is where the one rule lives).
"""

from __future__ import annotations

import random
import statistics
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from telltale.forecast import (
    DEMOTION_RESOLUTION,
    ORDERING_BLOCK,
    ORDERING_ROW,
    ORDERING_TRUE,
    PLACEBO_ROW_BLOCK,
    PLACEBO_SEEDS,
    Window,
    placebo_block,
    refuse_words,
)
from telltale.forecast import backtest as backtester
from telltale.forecast import decide as decider
from telltale.report import render_table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.forecast import Forecaster
    from telltale.model import Series
    from telltale.store import Store

# The baseline whose behaviour under a shuffle is the validity check. Persistence is
# the only one of the four that reads a single row, so it is the one whose score is a
# direct statement about which row the permutation left last.
SENTINEL = "persistence"

INVALID = (
    "the placebo did not make persistence worse. Persistence reads y_{o-1}, so a"
    " shuffle that reached the forecasters must move it; design 6.12 as amended by"
    " W3-E08b calls this run invalid, and temporal evolution and conditional"
    " prediction, the two labels that read it, are not assessable from it"
)


def block_shuffle(
    rows: Sequence[Sequence[float]], block: int, seed: int
) -> list[list[float]]:
    """Consecutive blocks of `block` whole rows, permuted, concatenated.

    The multiset of rows is preserved exactly: no row is copied, dropped or edited, and
    a covariate never leaves the target value it was measured beside. The last block is
    short when `block` does not divide the context, and it is permuted with the rest
    rather than held in place, because holding it would leave the most recent rows
    where they were, which is the one thing this function exists to destroy.
    """
    if block < 1:
        raise ValueError(f"block {block} is not a positive number of rows")
    blocks = [
        [list(row) for row in rows[start : start + block]]
        for start in range(0, len(rows), block)
    ]
    random.Random(seed).shuffle(blocks)
    return [row for chunk in blocks for row in chunk]


def shuffled(block: int, seed: int) -> Any:
    """A `prepare` hook for `backtest.run`: this window with its context permuted."""

    def prepare(window: Window) -> Window:
        return replace(window, rows=block_shuffle(window.rows, block, seed))

    return prepare


def run(
    series: Series,
    target: str,
    horizon: int,
    forecasters: Mapping[str, Forecaster],
    *,
    variant: str | None = None,
    c_min: int | None = None,
    covariates: Sequence[str] | None = None,
    seeds: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    """One run per (ordering, seed): R block placebos and R B = 1 controls.

    Every one of them has the origins the true-order run has, because they are the same
    call to the same planner with the same arguments; only the `prepare` hook differs.
    """
    sizes = (
        (ORDERING_BLOCK, placebo_block(horizon)),
        (ORDERING_ROW, PLACEBO_ROW_BLOCK),
    )
    chosen = list(range(PLACEBO_SEEDS)) if seeds is None else list(seeds)
    return [
        backtester.run(
            series,
            target,
            horizon,
            forecasters,
            variant,
            c_min=c_min,
            covariates=covariates,
            ordering=ordering,
            placebo_seed=seed,
            prepare=shuffled(size, seed),
        )
        for ordering, size in sizes
        for seed in chosen
    ]


def definition(horizon: int, seeds: Sequence[int] | None = None) -> dict[str, Any]:
    """What the placebo WAS, in the form a stored run carries it.

    A placebo number nobody can reproduce is not a control. This is the block size, the
    seeds and the generator, and it goes in the scenario of every run this module makes.
    """
    return {
        "block": placebo_block(horizon),
        "row_block": PLACEBO_ROW_BLOCK,
        "seeds": list(range(PLACEBO_SEEDS)) if seeds is None else list(seeds),
        "generator": "random.Random(seed).shuffle over consecutive whole-row blocks",
        "shuffles": "context only; origin, test rows and actual are true order",
    }


def sentinel(
    truth: Mapping[str, Any], placebos: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Design 6.12's validity check: persistence, true order against every placebo.

    `valid` is False when ANY placebo run scored persistence at or below its true-order
    score. Not the median and not most of them: one seed on which the shuffle left
    persistence unharmed is one seed on which the control did not control for anything.
    """
    true_mae = _mae(truth, SENTINEL)
    scored = [
        {
            "ordering": run["ordering"],
            "seed": run["placebo_seed"],
            "mae_mean": _mae(run, SENTINEL),
        }
        for run in placebos
    ]
    known = [entry for entry in scored if entry["mae_mean"] is not None]
    worse = [
        entry for entry in known if float(entry["mae_mean"]) > float(true_mae or 0.0)
    ]
    return {
        "forecaster": SENTINEL,
        "true_mae_mean": true_mae,
        "placebo_mae_mean": scored,
        "median_placebo_mae_mean": (
            statistics.median([float(entry["mae_mean"]) for entry in known])
            if known
            else None
        ),
        "n_runs": len(scored),
        "n_worse": len(worse),
        "valid": bool(known) and true_mae is not None and len(worse) == len(known),
        "reason": None
        if bool(known) and true_mae is not None and len(worse) == len(known)
        else INVALID,
    }


def _mae(run: Mapping[str, Any], name: str) -> float | None:
    scored = run["metrics"]["forecasters"].get(name)
    return None if scored is None else scored["mae_mean"]


# -- the paired run, its report, and the stored true-order twin -----------------------


def paired(
    series: Series,
    target: str,
    horizon: int,
    forecasters: Mapping[str, Forecaster],
    model: str,
    *,
    truth: Mapping[str, Any] | None = None,
    variant: str | None = None,
    c_min: int | None = None,
    covariates: Sequence[str] | None = None,
    seeds: Sequence[int] | None = None,
) -> dict[str, Any]:
    """The true-order run, its placebos, the validity check and the decision.

    The check is handed to `decide` rather than applied to its answer afterwards. One
    rule, in one place: an override here could only ever disagree with the module that
    holds design 6.12, and under the W3-E08b amendment it did.

    `truth` is a run already made (read back out of the store, or produced by the
    ablation) and it is reused rather than recomputed. The placebos are run either way,
    because a placebo is only a control for the run it is paired with.
    """
    true_run = dict(
        truth
        if truth is not None
        else backtester.run(
            series,
            target,
            horizon,
            forecasters,
            variant,
            c_min=c_min,
            covariates=covariates,
        )
    )
    placebos = run(
        series,
        target,
        horizon,
        forecasters,
        variant=variant,
        c_min=c_min,
        covariates=covariates,
        seeds=seeds,
    )
    check = sentinel(true_run, placebos)
    found = decider.decide(true_run, placebos, model, placebo_valid=check["valid"])
    spelled = definition(horizon, seeds)
    true_run["decision"] = found.as_dict()
    for one in (true_run, *placebos):
        one["placebo"] = spelled
        one["warnings"] = [*one["warnings"], *([] if check["valid"] else [INVALID])]
    return {
        "truth": true_run,
        "placebos": placebos,
        "sentinel": check,
        "decision": found,
        "definition": spelled,
    }


def report(found: Mapping[str, Any]) -> str:
    """The placebo table, the sentinel check and the decision, in that order."""
    truth = found["truth"]
    names = sorted(truth["metrics"]["forecasters"])
    columns = ("ordering", "seed", "n_windows", *names)
    rows = [_row(run, names) for run in (truth, *found["placebos"])]
    check = found["sentinel"]
    lines = [
        f"forecast placebo  series {truth['series_id']}  target {truth['target']}"
        f" ({truth['unit']})",
        "placebo: "
        + "  ".join(f"{key} {value}" for key, value in found["definition"].items()),
        "",
        render_table(rows, columns),
        "",
        f"validity ({SENTINEL} must be worse under every placebo):"
        f" true {_round(check['true_mae_mean'])},"
        f" {check['n_worse']} of {check['n_runs']} placebo runs worse"
        f" -> {'valid' if check['valid'] else 'INVALID'}",
        *([f"  {check['reason']}"] if check["reason"] else []),
        "",
        decider.report(found["decision"], _constants(truth)),
    ]
    return refuse_words("\n".join(lines))


def _row(run: Mapping[str, Any], names: Sequence[str]) -> dict[str, Any]:
    scored = run["metrics"]["forecasters"]
    return {
        "ordering": run["ordering"],
        "seed": run["placebo_seed"],
        "n_windows": run["metrics"]["n_windows"],
        **{name: _round(scored[name]["mae_mean"]) for name in names if name in scored},
    }


def _constants(run: Mapping[str, Any]) -> dict[str, Any]:
    """Every pre-registered number this decision was taken under, printed with it."""
    return {
        "delta": run["delta"],
        "w": run["w"],
        "k_min": run["k_min"],
        "c_min": run["c_min"],
        "horizon": run["horizon"],
        "stride": run["stride"],
        "baseline_window": run["baseline_window"],
        "threshold_rule": run["threshold_rule"],
        "placebo_block": placebo_block(int(run["horizon"])),
        "placebo_seeds": PLACEBO_SEEDS,
        "placebo_row_block": PLACEBO_ROW_BLOCK,
        "demotion_resolution": DEMOTION_RESOLUTION,
    }


def store_all(store: Store, found: Mapping[str, Any]) -> list[str]:
    """Write the true-order run and every placebo. The decision rides on the true row.

    One decision, one row. A placebo row carries no decision because the label is not
    about it: it is about the pair, and the true-order run is the half of the pair a
    reader looks up by (series, target, variant).
    """
    return [
        backtester.persist(store, run) for run in (found["truth"], *found["placebos"])
    ]


def matches(
    row: Mapping[str, Any], run: Mapping[str, Any], names: Sequence[str]
) -> bool:
    """Is this stored row the true-order twin of the run about to be placeboed?

    Every field that changes a number is compared, the forecaster NAMES included: a
    stored run made with three forecasters is not the true-order twin of a placebo made
    with five, and pairing them would compare two different experiments.
    """
    return (
        row["ordering"] == ORDERING_TRUE
        and row["target"] == run["target"]
        and row["variant"] == run["variant"]
        and int(row["horizon"]) == int(run["horizon"])
        and int(row["c_min"]) == int(run["c_min"])
        and int(row["stride"]) == int(run["stride"])
        and sorted(entry["name"] for entry in row["forecasters"]) == sorted(names)
    )


def restored(row: Mapping[str, Any], series: Series, unit: str) -> dict[str, Any]:
    """A stored forecast_runs row back in the shape `backtest.run` returns.

    The stored row is the contract (store.FORECAST_RUN_COLUMNS); the four fields it
    does not carry are properties of the series and the registry rather than of the
    run, so they are read from those and never guessed. What comes back is scored
    exactly as the run that wrote it was, because the windows carry the forecasts.
    """
    constants = dict(row["scenario"]["constants"])
    return {
        "series_id": row["series_id"],
        "clock": series.clock,
        "target": row["target"],
        "unit": unit,
        "variant": row["variant"],
        "ordering": row["ordering"],
        "placebo_seed": row["placebo_seed"],
        "horizon": int(row["horizon"]),
        "stride": int(row["stride"]),
        "c_min": int(row["c_min"]),
        "k_min": int(constants["k_min"]),
        "delta": float(constants["delta"]),
        "w": float(constants["w"]),
        "baseline_window": int(constants["baseline_window"]),
        "threshold_rule": constants["threshold_rule"],
        "tau": row["scenario"]["tau"],
        "covariates": list(row["scenario"]["covariates"]),
        "n_rows": len(series.rows),
        "missingness_policy": row["missingness_policy"],
        "command": list(row["scenario"]["command"]),
        "forecasters": list(row["forecasters"]),
        "windows": list(row["windows"]["retained"]),
        "dropped": list(row["windows"]["dropped"]),
        "dropped_counts": dict(row["windows"]["dropped_counts"]),
        "metrics": row["metrics"],
        "warnings": list(row["warnings"]),
        "assumptions": list(row["assumptions"]),
        "placebo": row["scenario"].get("placebo"),
        "decision": row["decision"],
        "reused_from": row["forecast_run_id"],
    }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
