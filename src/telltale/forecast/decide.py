"""The decision rule of design 6.12: four labels, and every inequality behind them.

A backtest produces numbers. What a reader does with them is decide whether the system
may say anything at all about the future, and that decision is a rule written before
the numbers existed. This module is that rule, and nothing here computes a constant:
delta, w and k_min come from forecast/__init__.py and every comparison is printed with
the value on each side of it, so the label is never something a reader has to trust.

The four labels, in the order the rule tries them.

  NOT ASSESSABLE. Fewer than k_min windows, a term the run does not carry, or no
  placebo. There is no label, and "no label" is a result rather than a gap: the
  strongest thing that may be said about the pair is that it was not assessed.

  BASELINE SUFFICIENT. `E_M > (1 - delta) E_B` or `W_MB < w`. The model did not beat
  four one-line rules by enough, or did not beat them often enough. Either clause is
  enough on its own, which is deliberate: a model that wins on average by winning
  enormously on three windows out of a hundred has not earned a forecast.

  TEMPORAL EVOLUTION. It beat the baselines, AND it beat its own chronology placebo,
  AND both of those hold separately in each half of the origin range. The half clause
  is spec 15.7's anti-cherry-pick rule: a result that lives in one half of the history
  is a result about that half.

  CONDITIONAL PREDICTION. It beat the baselines and the placebo did not explain it.
  The model is using something in the window, but not the ORDER of the window; the
  gain survives a shuffle of the past. Where the variant carries no covariates at all
  there is nothing left for it to be using but the level, and the report says so.

W_MP pairs by ORIGIN: the true-order model's MAE at origin o against the median over
the R placebo seeds of its MAE at the same origin. Median rather than each seed
separately, because design 6.12 says "its placebo twin" and a window has R of them; the
share over all (window, seed) pairs is computed too and printed beside it.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from telltale.forecast import (
    BASELINE_NAMES,
    DELTA,
    ORDERING_BLOCK,
    W,
    refuse_words,
)
from telltale.forecast.backtest import window_mae
from telltale.report import render_table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

NOT_ASSESSABLE = "not assessable"
BASELINE_SUFFICIENT = "baseline sufficient"
TEMPORAL_EVOLUTION = "temporal evolution"
CONDITIONAL_PREDICTION = "conditional prediction"
LABELS = (
    NOT_ASSESSABLE,
    BASELINE_SUFFICIENT,
    TEMPORAL_EVOLUTION,
    CONDITIONAL_PREDICTION,
)

NO_PLACEBO = (
    "placebo not run: label withheld. Without the chronology control the rule cannot"
    " tell temporal evolution from conditional prediction, and either label makes a"
    " claim about order that nothing here has tested"
)
# Design 6.12: what a conditional prediction is when the variant has no covariates.
LEVEL_PREDICTION = (
    "level prediction: the gain is distributional. This variant carries no covariates,"
    " so what the model is reading is the level of the target itself and not the order"
    " of it and not a second series"
)

_TABLE = ("test", "lhs", "rhs", "holds")
_HALVES = ("half", "origins", "n_windows", "E_M", "E_P", "W_MP", "both hold")


@dataclass(frozen=True)
class Decision:
    """One label, every number it was computed from, and every comparison it made."""

    label: str
    reason: str | None
    model: str
    best_baseline: str | None
    e_m: float | None
    e_b: float | None
    e_p: float | None
    w_mb: float | None
    w_mp: float | None
    n_windows: int
    k_min: int
    delta: float
    w: float
    covariate_free: bool
    inequalities: list[dict[str, Any]] = field(default_factory=list)
    halves: list[dict[str, Any]] = field(default_factory=list)
    placebo: dict[str, Any] = field(default_factory=dict)
    baselines: dict[str, float | None] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """The whole decision as JSON, which is how a forecast_runs row carries it."""
        return asdict(self)


def decide(
    true_run: Mapping[str, Any],
    placebo_runs: Sequence[Mapping[str, Any]],
    model: str,
    baselines: Sequence[str] = BASELINE_NAMES,
) -> Decision:
    """Design 6.12's rule over one true-order run and its block placebos.

    `model` is named rather than inferred. A run holds several forecasters and which
    one the decision is about changes the answer, so it is an argument: a rule that
    guessed would put a label on a forecaster nobody asked about.
    """
    windows = list(true_run["windows"])
    scored = true_run["metrics"]["forecasters"]
    blocks = [run for run in placebo_runs if run["ordering"] == ORDERING_BLOCK]
    ran = {
        name: scored[name]["mae_mean"]
        for name in baselines
        if name in scored and scored[name]["mae_mean"] is not None
    }
    state = _terms(true_run, windows, scored, ran, model, blocks)
    return Decision(**state, **_label(state))


def _terms(
    true_run: Mapping[str, Any],
    windows: Sequence[Mapping[str, Any]],
    scored: Mapping[str, Any],
    ran: Mapping[str, float],
    model: str,
    blocks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """E_M, E_B, E_P, W_MB, W_MP and the per-half values. No label yet."""
    best = min(ran, key=lambda name: float(ran[name])) if ran else None
    e_m = scored[model]["mae_mean"] if model in scored else None
    e_b = None if best is None else float(ran[best])
    paired = _paired(windows, blocks, model)
    return {
        "model": model,
        "best_baseline": best,
        "e_m": e_m,
        "e_b": e_b,
        "e_p": _median([_mean(run, model) for run in blocks]),
        "w_mb": _share(
            [window_mae(record, model) < window_mae(record, best) for record in windows]
        )
        if best is not None and model in scored
        else None,
        "w_mp": _share([true < twin for true, twin in paired]),
        "n_windows": len(windows),
        "k_min": int(true_run["k_min"]),
        "delta": float(true_run["delta"]),
        "w": float(true_run["w"]),
        "covariate_free": not true_run["covariates"],
        "baselines": dict(ran),
        "halves": _halves(windows, blocks, model),
        "placebo": _placebo(blocks, model, paired),
    }


def _label(state: Mapping[str, Any]) -> dict[str, Any]:
    """The label, the reason and every inequality that was evaluated to reach it."""
    delta, w = state["delta"], state["w"]
    e_m, e_b, e_p = state["e_m"], state["e_b"], state["e_p"]
    if state["n_windows"] < state["k_min"]:
        return _none(
            f"{state['n_windows']} windows is below k_min {state['k_min']}:"
            " design 6.12 writes no label"
        )
    if e_m is None or e_b is None or state["w_mb"] is None:
        return _none(
            f"{state['model']} or a baseline did not run: the rule has no term to"
            " compare"
        )
    w_mb, floor = state["w_mb"], (1.0 - delta) * e_b
    sufficient = [
        _test("E_M > (1 - delta) E_B", e_m, floor, e_m > floor),
        _test("W_MB < w", w_mb, w, w_mb < w),
    ]
    if any(item["holds"] for item in sufficient):
        return {
            "label": BASELINE_SUFFICIENT,
            "reason": None,
            "inequalities": sufficient,
        }
    if not state["placebo"]["n_runs"]:
        return _none(NO_PLACEBO, sufficient)
    if e_p is None:
        return _none(f"{state['model']} scored nothing on the placebo runs", sufficient)
    w_mp, ceiling = state["w_mp"], (1.0 - delta) * e_p
    temporal = [
        _test("E_M <= (1 - delta) E_P", e_m, ceiling, e_m <= ceiling),
        _test("W_MP >= w", w_mp, w, w_mp is not None and w_mp >= w),
    ]
    halves = all(half["both_hold"] for half in state["halves"])
    return {
        "label": TEMPORAL_EVOLUTION
        if all(item["holds"] for item in temporal) and halves
        else CONDITIONAL_PREDICTION,
        "reason": None,
        "inequalities": [*sufficient, *temporal],
        "notes": [LEVEL_PREDICTION]
        if state["covariate_free"]
        and not (all(item["holds"] for item in temporal) and halves)
        else [],
    }


def _none(reason: str, evaluated: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "label": NOT_ASSESSABLE,
        "reason": reason,
        "inequalities": evaluated or [],
    }


def _test(name: str, lhs: float | None, rhs: float, holds: bool) -> dict[str, Any]:
    """One comparison with the value on BOTH sides, so a reader can redo it.

    `holds` is evaluated at the call site rather than from the name: a comparison whose
    direction is parsed out of a label is a comparison that changes when the label does.
    """
    return {"test": name, "lhs": lhs, "rhs": rhs, "holds": bool(holds)}


def _halves(
    windows: Sequence[Mapping[str, Any]],
    blocks: Sequence[Mapping[str, Any]],
    model: str,
) -> list[dict[str, Any]]:
    """The two inequalities of the temporal branch, in each half of the origin range.

    Spec 15.7. The split is by POSITION in the retained origins, not by row index: the
    two halves then hold the same number of windows to within one, and an odd count
    gives the extra window to the second half.
    """
    ordered = sorted(windows, key=lambda record: int(record["origin"]))
    middle = len(ordered) // 2
    return [
        _half(name, part, blocks, model)
        for name, part in (("first", ordered[:middle]), ("second", ordered[middle:]))
    ]


def _half(
    name: str,
    part: Sequence[Mapping[str, Any]],
    blocks: Sequence[Mapping[str, Any]],
    model: str,
) -> dict[str, Any]:
    origins = {int(record["origin"]) for record in part}
    e_m = _mean_of([window_mae(record, model) for record in part])
    e_p = _median(
        [
            _mean_of(
                [
                    window_mae(record, model)
                    for record in run["windows"]
                    if int(record["origin"]) in origins
                ]
            )
            for run in blocks
        ]
    )
    paired = _paired(part, blocks, model)
    w_mp = _share([true < twin for true, twin in paired])
    holds = (
        e_m is not None
        and e_p is not None
        and w_mp is not None
        and e_m <= (1.0 - DELTA) * e_p
        and w_mp >= W
    )
    return {
        "half": name,
        "origins": [min(origins), max(origins)] if origins else None,
        "n_windows": len(part),
        "e_m": e_m,
        "e_p": e_p,
        "w_mp": w_mp,
        "both_hold": bool(holds),
    }


def _paired(
    windows: Sequence[Mapping[str, Any]],
    blocks: Sequence[Mapping[str, Any]],
    model: str,
) -> list[tuple[float, float]]:
    """(true MAE, median placebo MAE) per origin. Empty when nothing pairs.

    A window with no twin in a placebo run is DROPPED from the pairing rather than
    compared against something else: the two runs are meant to have identical origins,
    and an origin present in one and not the other is a fact about the pair.
    """
    if not blocks:
        return []
    twins: dict[int, list[float]] = {}
    for run in blocks:
        for record in run["windows"]:
            twins.setdefault(int(record["origin"]), []).append(
                window_mae(record, model)
            )
    return [
        (window_mae(record, model), statistics.median(twins[int(record["origin"])]))
        for record in windows
        if int(record["origin"]) in twins
    ]


def _placebo(
    blocks: Sequence[Mapping[str, Any]],
    model: str,
    paired: Sequence[tuple[float, float]],
) -> dict[str, Any]:
    """Per-seed E_P, the median and the range, and the all-pairs share of wins."""
    per_seed = [
        {"seed": run["placebo_seed"], "mae_mean": _mean(run, model)} for run in blocks
    ]
    known = [
        float(entry["mae_mean"]) for entry in per_seed if entry["mae_mean"] is not None
    ]
    all_pairs = [
        window_mae(record, model) < twin
        for run in blocks
        for record, twin in _twins(run, model)
    ]
    return {
        "model": model,
        "n_runs": len(blocks),
        "per_seed": per_seed,
        "median": statistics.median(known) if known else None,
        "range": [min(known), max(known)] if known else None,
        "n_paired_windows": len(paired),
        "w_mp_all_pairs": _share(all_pairs),
    }


def _twins(run: Mapping[str, Any], model: str) -> list[tuple[Mapping[str, Any], float]]:
    return [(record, window_mae(record, model)) for record in run["windows"]]


def _mean(run: Mapping[str, Any], model: str) -> float | None:
    scored = run["metrics"]["forecasters"].get(model)
    return None if scored is None else scored["mae_mean"]


def _mean_of(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: Sequence[float | None]) -> float | None:
    known = [float(value) for value in values if value is not None]
    return statistics.median(known) if known else None


def _share(flags: Sequence[bool]) -> float | None:
    """Share of True. Ties count as False: a tie is not a win. None when nothing ran."""
    return sum(flags) / len(flags) if flags else None


# -- printing -------------------------------------------------------------------------


def report(decision: Decision, constants: Mapping[str, Any]) -> str:
    """The label, both inequalities with their values, the halves and the constants."""
    lines = [
        f"decision: {decision.label}"
        + (f"  ({decision.reason})" if decision.reason else ""),
        f"model {decision.model}  best baseline {decision.best_baseline}"
        f"  n_windows {decision.n_windows}  k_min {decision.k_min}",
        f"E_M {_round(decision.e_m)}  E_B {_round(decision.e_b)}"
        f"  E_P {_round(decision.e_p)}"
        f"  W_MB {_round(decision.w_mb)}  W_MP {_round(decision.w_mp)}",
        "constants: " + "  ".join(f"{key} {value}" for key, value in constants.items()),
        "",
        render_table(
            [
                {
                    "test": item["test"],
                    "lhs": _round(item["lhs"]),
                    "rhs": _round(item["rhs"]),
                    "holds": "yes" if item["holds"] else "no",
                }
                for item in decision.inequalities
            ]
            or [{"test": "none evaluated"}],
            _TABLE,
        ),
        "",
        render_table(
            [
                {
                    "half": half["half"],
                    "origins": None
                    if half["origins"] is None
                    else f"{half['origins'][0]}..{half['origins'][1]}",
                    "n_windows": half["n_windows"],
                    "E_M": _round(half["e_m"]),
                    "E_P": _round(half["e_p"]),
                    "W_MP": _round(half["w_mp"]),
                    "both hold": "yes" if half["both_hold"] else "no",
                }
                for half in decision.halves
            ]
            or [{"half": "not computed"}],
            _HALVES,
        ),
        *[f"note: {line}" for line in decision.notes],
    ]
    return refuse_words("\n".join(lines))


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
