"""E07's arithmetic: the spread of an MAE, and design 6.12's decision rule.

Split from run.py, which orchestrates. Nothing here opens a store, a file or a model:
every function takes the windows a stored `forecast_runs` row carries and returns a
number or the string that says why there is none.

The decision rule implemented here is HALF of design 6.12's, and the half that is
missing is named in every label. E_M, E_B and W_MB compare the model against the
baselines; E_P, W_MP and the two-halves clause compare it against its own chronology
placebo, which needs `block_shuffle` from W3-T2. So the strongest label available is
"not baseline sufficient at this n, placebo pending", and "temporal evolution" and
"conditional prediction" cannot be written by this file at all.
"""

from __future__ import annotations

import statistics
from typing import Any

from telltale.forecast import BASELINE_NAMES, DELTA, K_MIN, W

# 1.4826 MAD, the scale that makes a median absolute deviation comparable with a
# standard deviation on normal data. AGENTS.md: never a mean alone.
MAD_SCALE = 1.4826


def stats(values: list[float]) -> dict[str, Any]:
    """Median, scaled MAD, IQR, min, max and the full sorted list. Never a mean alone.

    The scaled MAD and the IQR of a single value are None rather than 0: one value has
    a median and no dispersion, and a 0 there reads as a measured agreement.
    """
    ordered = sorted(values)
    if not ordered:
        return _EMPTY_STATS
    median = statistics.median(ordered)
    spread = len(ordered) > 1
    quartiles = statistics.quantiles(ordered, n=4) if spread else None
    mad = statistics.median([abs(one - median) for one in ordered]) if spread else None
    return {
        "n": len(ordered),
        "median": median,
        "mad_scaled": None if mad is None else MAD_SCALE * mad,
        "iqr": None if quartiles is None else quartiles[2] - quartiles[0],
        "min": ordered[0],
        "max": ordered[-1],
        "sorted": ordered,
    }


_EMPTY_STATS: dict[str, Any] = {
    "n": 0,
    "median": None,
    "mad_scaled": None,
    "iqr": None,
    "min": None,
    "max": None,
    "sorted": [],
}


def window_mae(record: dict[str, Any], name: str) -> float:
    """MAE of one forecaster over one window's H steps. backtest.py's own definition."""
    point = record["forecasts"][name]["point"]
    pairs = zip(record["actual"], point, strict=True)
    return statistics.fmean([abs(actual - value) for actual, value in pairs])


def decide(
    metrics: dict[str, Any], windows: list[dict[str, Any]], model: str
) -> dict[str, Any]:
    """Design 6.12's baseline half, with the placebo half stated as missing.

    E_M is the model's mean MAE in true order, E_B the lowest mean MAE over the four
    baselines, and W_MB the share of windows where the model's own MAE is below that
    baseline's on the same window. Ties count as losses, and the count is printed.
    """
    scored = metrics["forecasters"]
    baselines = {
        name: entry["mae_mean"]
        for name, entry in scored.items()
        if name in BASELINE_NAMES and entry["mae_mean"] is not None
    }
    if model not in scored or not baselines:
        return _no_decision(model, baselines, len(windows))
    best = min(baselines, key=lambda name: float(baselines[name]))
    e_m, e_b = float(scored[model]["mae_mean"]), float(baselines[best])
    beaten = [window_mae(one, model) < window_mae(one, best) for one in windows]
    w_mb = sum(beaten) / len(beaten) if beaten else None
    return {
        "model": model,
        "E_M": e_m,
        "E_B": e_b,
        "best_baseline": best,
        "baselines": baselines,
        "W_MB": w_mb,
        "wins": sum(beaten),
        "losses_or_ties": len(beaten) - sum(beaten),
        "n_windows": len(windows),
        **_label(e_m, e_b, w_mb, len(windows), DELTA, W, K_MIN),
    }


def _label(
    e_m: float, e_b: float, w_mb: float | None, n: int, delta: float, w: float, k: int
) -> dict[str, Any]:
    """The label and both inequalities with their values. Never a bare verdict."""
    if n < k or w_mb is None:
        return {
            "label": "not assessable",
            "label_reason": f"{n} windows is below k_min {k}: design 6.12 writes no"
            " label",
            "inequalities": [],
        }
    sufficient = [e_m > (1.0 - delta) * e_b, w_mb < w]
    return {
        "label": "baseline sufficient"
        if any(sufficient)
        else "not baseline sufficient at this n, placebo pending",
        "label_reason": "the placebo half of design 6.12 (E_P, W_MP and the two-halves"
        " clause) needs block_shuffle, which is W3-T2 and does not exist, so no run"
        " here can be labelled temporal evolution or conditional prediction",
        "inequalities": [
            {
                "test": "E_M > (1 - delta) E_B",
                "lhs": e_m,
                "rhs": (1.0 - delta) * e_b,
                "holds": sufficient[0],
            },
            {"test": "W_MB < w", "lhs": w_mb, "rhs": w, "holds": sufficient[1]},
        ],
    }


def _no_decision(model: str, baselines: dict[str, float], n: int) -> dict[str, Any]:
    missing = "no baseline ran" if baselines else f"{model} did not run"
    return {
        "model": model,
        "E_M": None,
        "E_B": None,
        "best_baseline": None,
        "baselines": baselines,
        "W_MB": None,
        "wins": None,
        "losses_or_ties": None,
        "n_windows": n,
        "label": "not assessable",
        "label_reason": f"{missing}: the decision rule has no term to compare",
        "inequalities": [],
    }


def mae_table(
    metrics: dict[str, Any], windows: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Per forecaster: design 6.12's mean, and the full spread beside it."""
    return {
        name: {
            "mae_mean": entry["mae_mean"],
            "mae": stats([window_mae(one, name) for one in windows]),
            "skill": entry["skill"],
            "skill_against": entry["skill_against"],
            "calibration_flagged": entry["calibration"].get("flagged"),
            "calibration_max_deviation": entry["calibration"].get("max_deviation"),
            "coverage80": entry["calibration"].get("coverage80"),
            "calibration_reason": entry["calibration"].get("reason"),
            "wqs": entry["wqs"],
            "wqs_reason": entry["wqs_reason"],
            "lead_time": _lead(entry["lead_time"]),
            "wall_ms": entry["wall_ms"],
        }
        for name, entry in metrics["forecasters"].items()
    }


def _lead(lead: dict[str, Any]) -> dict[str, Any]:
    """The lead-time block, or the string that says the preconditions failed.

    Design 6.12 scores lead time only over windows that start below tau. No such
    window, or no quantile band, is "not assessable" and never a number in that cell.
    """
    if lead.get("reason"):
        return {"assessable": False, "reason": lead["reason"]}
    if not lead.get("eligible"):
        return {
            "assessable": False,
            "reason": "no window has y_{o-1} below tau: nothing to score",
        }
    return {"assessable": True, **lead}
