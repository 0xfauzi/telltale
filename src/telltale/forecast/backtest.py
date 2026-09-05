"""The rolling-origin backtester, its metrics and its report. Design 6.12.

A rolling origin is the only honest way to score a forecaster on a series that has
already happened. Pick a row o, hand the forecaster rows [ctx_start, o) and nothing
else, ask for H steps, and compare them against rows [o, o + H) which it never saw.
Then move o forward by the stride and do it again. Every window is a separate
out-of-sample test and the score is over all of them.

Four rules here are refusals rather than conveniences, and each is a defect this
system exists to prevent.

  Nothing is imputed. A window whose context or actual carries an unknown is DROPPED
  and counted with its reason. Zero-filling one would put a number nobody measured
  inside a predictive claim.

  A regime boundary is not context. `ctx_start` begins at the last changepoint at or
  before the origin, and a window with fewer than c_min rows since that changepoint is
  skipped as regime_too_short rather than padded from the regime before it.

  MASE and RMSE are rejected by name. MASE divides by the mean absolute step of the
  context, which is zero on a constant context; RMSE squares, and these are heavy-
  tailed counts where one compaction moves the score more than fifty ordinary rows.
  The headline is MAE of the median forecast per window, aggregated by mean AND median.

  The point forecast is quantile index 4 and never an average of quantiles. The mean of
  a quantile set is not a quantile of anything.

`ordering` defaults to 'true'. A placebo run is the SAME function with a `prepare`
hook that rewrites each window before the forecasters see it (forecast/placebo.py): the
records are built first, so origins and actuals stay true and pair the two runs.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from telltale.forecast import (
    ALARM_INDEX,
    BASELINE_NAMES,
    BASELINE_WINDOW,
    DELTA,
    MAX_CONTEXT,
    ORDERING_TRUE,
    QUANTILE_LEVELS,
    TARGETS,
    W,
    Window,
    refuse_words,
)
from telltale.forecast.baselines import quantile
from telltale.report import render_table

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from telltale.forecast import Forecaster, TargetSpec
    from telltale.model import Series
    from telltale.store import Store

# The coverage words a column may carry and still be forecast or ride as a covariate.
# Design 6.12's readiness checklist: "target and variant columns observed or
# derived-from-observed on retained rows". `partial` and `unavailable` are excluded by
# name rather than silently accepted, because a partial column's holes are exactly the
# rows nobody could see.
FORECASTABLE = ("observed", "derived")

# What the metrics are. Every number in the table below is computed from actuals that
# were observed, so it is derived; the FORECASTS are predictive, and the stored run
# carries that class on the row itself. A claim class is never upgraded (AGENTS.md 6).
CLAIM = "derived"

NO_QUANTILES = "this forecaster returned no quantiles: not assessable"
NO_TAU = "tau is not computable: the first c_min rows of the target hold an unknown"

# One name per column of the printed table, folded to hold the 800-line ratchet.
_TABLE = (
    "forecaster", "n_windows", "mae_mean", "mae_median", "skill", "cal_max_dev",
    "coverage80", "wqs", "lead_hit_rate", "false_alarm_rate", "median_lead",
    "claim_class",
)  # fmt: skip


class Refused(Exception):
    """A backtest that stopped on purpose. The CLI turns it into exit 2."""


@dataclass
class Plan:
    """The origins that survived, their windows, and every drop with its reason.

    Public because the readiness checklist (forecast/readiness.py) has to count the
    origins this module WOULD run. A checklist that recomputed the origin arithmetic
    would be a checklist of a backtest nobody runs.
    """

    columns: list[str] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    windows: list[Window] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        found: dict[str, int] = {}
        for drop in self.dropped:
            reason = str(drop["reason"])
            found[reason] = found.get(reason, 0) + 1
        return found


# -- the run --------------------------------------------------------------------------


def run(
    series: Series,
    target: str,
    horizon: int,
    forecasters: Mapping[str, Forecaster],
    variant: str | None = None,
    *,
    c_min: int | None = None,
    covariates: Sequence[str] | None = None,
    ordering: str = ORDERING_TRUE,
    placebo_seed: int | None = None,
    prepare: Callable[[Window], Window] | None = None,
) -> dict[str, Any]:
    """Steps 1 to 6 of design 6.12. Returns the run, unstored.

    No `store` parameter, and that is this function's one deviation from its brief. A
    backtest reads the Series it was handed and the registry, and nothing else: a store
    handle here would be an argument that is never used and, worse, the one thing a
    Window is forbidden to carry. `persist` below takes the store.

    `c_min` overrides the registry value. It exists for the hand-computed metrics
    fixture; it is recorded in the run and printed by the report, so a run made under a
    c_min other than the pre-registered 32 says so on its own face.

    `covariates` names the variant's columns instead of taking every forecastable one.
    The A/B/C ablation (forecast/ablate.py) is three runs that differ in nothing else.

    `prepare` rewrites each Window between planning it and forecasting it, and it is
    the ONE place a placebo or a candidate conditioning may reach. The record is already
    built when it runs, so origin, actual and y_{o-1} stay true whatever it did.
    """
    spec = registered(series, target, horizon)
    floor = spec.c_min if c_min is None else c_min
    selected, excluded = (
        _variant(series, target)
        if covariates is None
        else _chosen(series, target, covariates)
    )
    planned = _plan(series, target, horizon, selected, floor)
    for record, window in zip(planned.records, planned.windows, strict=True):
        _forecast_window(
            record, window if prepare is None else prepare(window), forecasters, horizon
        )
    tau = _tau(series, target, floor)
    return {
        "series_id": series.series_id,
        "clock": series.clock,
        "target": target,
        "unit": spec.unit,
        "variant": variant or spec.variant,
        "ordering": ordering,
        "placebo_seed": placebo_seed,
        "horizon": horizon,
        "stride": horizon,
        "c_min": floor,
        "k_min": spec.k_min,
        "delta": DELTA,
        "w": W,
        "baseline_window": BASELINE_WINDOW,
        "threshold_rule": spec.threshold_rule,
        "tau": tau,
        "covariates": selected,
        # The columns this variant does NOT carry, and the word that kept each out.
        # A variant is named by its column set: two runs pool only when these match.
        "excluded": excluded,
        "n_rows": len(series.rows),
        "missingness_policy": series.missingness_policy,
        "command": list(sys.argv),
        "forecasters": [_declared(name, obj) for name, obj in forecasters.items()],
        "windows": planned.records,
        "dropped": planned.dropped,
        "dropped_counts": planned.counts(),
        "metrics": metrics(planned.records, tau),
        "warnings": _warnings(planned, excluded, spec.k_min),
        "assumptions": _assumptions(target, series, ordering),
        # Filled by the decision function (forecast/decide.py) on the true-order run
        # that a placebo was paired with, and null on every run that has no placebo.
        "decision": None,
    }


def _variant(series: Series, target: str) -> tuple[list[str], list[dict[str, str]]]:
    """The past-only covariates, and the columns kept out of them with the reason.

    A column whose coverage is `unavailable` is all None by construction, so including
    it would drop every window in the run for a reason that is a property of the
    capture rather than of any window. Excluding it by name, with its coverage beside
    it in the report, is the same rule stated where a reader can see it.
    """
    selected: list[str] = []
    excluded: list[dict[str, str]] = []
    for column in series.columns:
        if column.name == target:
            if column.coverage not in FORECASTABLE:
                raise Refused(
                    f"target {target} has coverage {column.coverage}:"
                    f" a target must be one of {list(FORECASTABLE)}"
                )
        elif column.coverage in FORECASTABLE:
            selected.append(column.name)
        else:
            excluded.append({"column": column.name, "coverage": column.coverage})
    return selected, excluded


def _chosen(
    series: Series, target: str, names: Sequence[str]
) -> tuple[list[str], list[dict[str, str]]]:
    """A NAMED variant: these columns, minus the target, minus the unforecastable.

    The target is dropped silently because an ablation block contains it by design and
    a target is excluded from its own covariates; a column the series does not have is
    a refusal, because a variant that quietly ran narrower than it was asked for is an
    ablation reporting on a variant nobody chose.
    """
    _variant(series, target)
    coverage = {column.name: column.coverage for column in series.columns}
    selected: list[str] = []
    excluded: list[dict[str, str]] = []
    for name in names:
        if name == target:
            continue
        if name not in coverage:
            raise Refused(f"series {series.series_id} has no column {name}")
        if coverage[name] in FORECASTABLE:
            selected.append(name)
        else:
            excluded.append({"column": name, "coverage": coverage[name]})
    return selected, excluded


def registered(series: Series, target: str, horizon: int) -> TargetSpec:
    """The registry entry, or the refusal that says why this pair cannot be run."""
    spec = TARGETS.get(target)
    if spec is None:
        raise Refused(f"{target} is not a forecast target. Known: {sorted(TARGETS)}")
    if horizon not in spec.horizons:
        raise Refused(f"horizon {horizon} is not one of {list(spec.horizons)}")
    if spec.clock != series.clock:
        raise Refused(
            f"{target} is a {spec.clock}-clock target and series"
            f" {series.series_id} is on the {series.clock} clock"
        )
    if target not in [column.name for column in series.columns]:
        raise Refused(f"series {series.series_id} has no column {target}")
    return spec


def plan(
    series: Series, target: str, horizon: int, *, c_min: int | None = None
) -> Plan:
    """The origins a backtest would run, planned without running a forecaster.

    `run` above is this function plus the forecasts and the metrics. The readiness
    checklist calls it to count windows, and counting them any other way would let the
    checklist pass a series the backtester then declines to score.
    """
    spec = registered(series, target, horizon)
    selected, _ = _variant(series, target)
    return _plan(
        series, target, horizon, selected, spec.c_min if c_min is None else c_min
    )


def _plan(
    series: Series, target: str, horizon: int, covariates: Sequence[str], c_min: int
) -> Plan:
    """One Window per surviving origin, plus every drop with its reason."""
    names = [column.name for column in series.columns]
    columns = [target, *covariates]
    indices = [names.index(name) for name in columns]
    found = Plan(columns=columns)
    for origin in range(c_min, len(series.rows) - horizon + 1, horizon):
        window, reason = _at_origin(series, origin, horizon, columns, indices, c_min)
        if window is None:
            found.dropped.append({"origin": origin, "reason": reason})
            continue
        actual, missing = _block(series.rows, indices[:1], columns[:1], origin, horizon)
        if actual is None:
            found.dropped.append(
                {"origin": origin, "reason": f"missing_actual_value({missing})"}
            )
            continue
        found.windows.append(window)
        found.records.append(
            {
                "origin": origin,
                "ctx_start": window.ctx_start,
                "n_ctx": window.n_ctx,
                "horizon": horizon,
                "actual": [cells[0] for cells in actual],
                # y_{o-1}: the one context value the lead-time rule reads. The context
                # itself is NOT stored (design 6.12 lists what a window record holds),
                # and a stored run must still be scorable against a threshold.
                "last_context": window.column(target)[-1],
                "flags": [],
                "forecasts": {},
            }
        )
    return found


def _at_origin(
    series: Series,
    origin: int,
    horizon: int,
    columns: Sequence[str],
    indices: Sequence[int],
    c_min: int,
) -> tuple[Window | None, str]:
    """One origin's window, or None and the reason the origin was dropped."""
    ctx_start = max(regime_start(series.changepoints, origin), origin - MAX_CONTEXT)
    if origin - ctx_start < c_min:
        return None, "regime_too_short"
    if any(origin <= point < origin + horizon for point in series.changepoints):
        return None, "changepoint_in_horizon"
    rows, gap = _block(series.rows, indices, columns, ctx_start, origin - ctx_start)
    if rows is None:
        return None, f"missing_context_value({gap})"
    return Window(
        columns=list(columns),
        rows=rows,
        origin=origin,
        ctx_start=ctx_start,
        n_ctx=origin - ctx_start,
        horizon=horizon,
        target=columns[0],
    ), ""


def regime_start(changepoints: Sequence[int], origin: int) -> int:
    """The last changepoint at or before the origin, or row 0 when there is none."""
    return max([point for point in changepoints if point <= origin], default=0)


def _block(
    rows: Sequence[Sequence[float | None]],
    indices: Sequence[int],
    columns: Sequence[str],
    start: int,
    length: int,
) -> tuple[list[list[float]] | None, str]:
    """The sub-table as floats, or None and the first column holding an unknown."""
    out: list[list[float]] = []
    for row in rows[start : start + length]:
        cells: list[float] = []
        for index, name in zip(indices, columns, strict=True):
            value = row[index]
            if value is None:
                return None, name
            cells.append(float(value))
        out.append(cells)
    return out, ""


def _forecast_window(
    record: dict[str, Any],
    window: Window,
    forecasters: Mapping[str, Forecaster],
    horizon: int,
) -> None:
    """Every forecaster on the IDENTICAL window, each timed on its own call."""
    for name, forecaster in forecasters.items():
        started = time.perf_counter()
        result = forecaster.forecast(window, horizon)
        wall_ms = (time.perf_counter() - started) * 1000.0
        record["forecasts"][name] = {
            "point": list(result.point[0]),
            "quantiles": None if result.quantiles is None else result.quantiles[0],
            "wall_ms": round(wall_ms, 3),
            # The adapter's own timing of predict_batch alone, when it kept one. The
            # wall_ms above is the whole call and includes building the arrays.
            "model_ms": getattr(forecaster, "last_wall_ms", None),
            "warnings": list(result.warnings),
        }


def _declared(name: str, forecaster: Forecaster) -> dict[str, Any]:
    """What a forecaster says about itself, for the stored run and the report.

    Read off the instance rather than imported: naming the licence here would mean
    importing forecast/timesfm.py, which imports torch, into a module the collector's
    isolation rule keeps clear of it.
    """
    return {
        "name": name,
        "checkpoint": getattr(forecaster, "checkpoint", None),
        "license": getattr(forecaster, "license", None),
        "device": getattr(forecaster, "device", None),
        "padding_mode": getattr(forecaster, "padding_mode", None),
    }


def threshold(series: Series, target: str, c_min: int | None = None) -> float | None:
    """tau under the registry's rule, or None when it is not computable.

    The public spelling of `_tau` for the readiness checklist, so that check 8 asks the
    same question the lead-time metric will answer against.
    """
    spec = TARGETS[target]
    return _tau(series, target, spec.c_min if c_min is None else c_min)


def _tau(series: Series, target: str, c_min: int) -> float | None:
    """q80 of the first c_min rows of the target. None when they hold an unknown."""
    index = [column.name for column in series.columns].index(target)
    head = [row[index] for row in series.rows[:c_min]]
    if len(head) < c_min or any(value is None for value in head):
        return None
    return quantile([float(value) for value in head if value is not None], 0.8)


def _warnings(
    planned: Plan, excluded: Sequence[Mapping[str, str]], k_min: int
) -> list[str]:
    found = [
        f"column {item['column']} was excluded from the variant:"
        f" coverage {item['coverage']}"
        for item in excluded
    ]
    if len(planned.records) < k_min:
        found.append(
            f"{len(planned.records)} windows is below k_min {k_min}: design 6.12 labels"
            " this run not assessable, and no decision is written"
        )
    return found


def _assumptions(target: str, series: Series, ordering: str) -> list[str]:
    return [
        f"ordering {ordering}. Only the CONTEXT of a window is ever rewritten: the"
        " origin, the actual and y_{o-1} of every record below are the true ones, which"
        " is what pairs a placebo window with its true-order twin by origin.",
        "windows whose context or actual held an unknown were dropped and counted."
        " Nothing was imputed and no policy here imputes.",
        "point error is MAE of the median forecast per window, aggregated by mean and"
        " median. MASE and RMSE are rejected by design 6.12.",
        "local_drift counts its step index h in rows from y_{o-1}, so h runs 1..H.",
        "the point forecast is whatever the forecaster declares as its point: for"
        " TimesFM that is quantile index 4, the median, and never an average of"
        " quantiles; for a baseline it is the baseline value itself.",
        "lead-time hit rate is hits / (hits + misses) and false-alarm rate is"
        " false_alarms / (false_alarms + quiet), over windows with y_{o-1} < tau.",
        f"target {target} on the {series.clock} clock, missingness policy"
        f" {series.missingness_policy}.",
    ]


# -- the metrics ----------------------------------------------------------------------


def metrics(windows: Sequence[Mapping[str, Any]], tau: float | None) -> dict[str, Any]:
    """Every score design 6.12 names, per forecaster, over the retained windows."""
    names = sorted({name for record in windows for name in record["forecasts"]})
    scored = {name: _score(windows, name, tau) for name in names}
    best, best_name = _best_baseline(scored)
    for entry in scored.values():
        entry["skill"], entry["skill_reason"] = _skill(entry["mae_mean"], best)
        entry["skill_against"] = best_name
    return {
        "n_windows": len(windows),
        "tau": tau,
        "forecasters": scored,
    }


def _score(
    windows: Sequence[Mapping[str, Any]], name: str, tau: float | None
) -> dict[str, Any]:
    errors = [window_mae(record, name) for record in windows]
    walls = [float(record["forecasts"][name]["wall_ms"]) for record in windows]
    return {
        "n_windows": len(errors),
        "mae_mean": statistics.fmean(errors) if errors else None,
        "mae_median": statistics.median(errors) if errors else None,
        "calibration": _calibration(windows, name),
        **_wqs(windows, name),
        "lead_time": _lead_time(windows, name, tau),
        "wall_ms": {
            "min": min(walls) if walls else None,
            "median": statistics.median(walls) if walls else None,
            "max": max(walls) if walls else None,
        },
        "claim_class": CLAIM,
    }


def window_mae(record: Mapping[str, Any], name: str) -> float:
    """MAE of one forecaster over one window. Public: the decision rule and the
    ablation pair windows by origin, and a second copy is a second definition."""
    point = record["forecasts"][name]["point"]
    pairs = zip(record["actual"], point, strict=True)
    return statistics.fmean([abs(actual - forecast) for actual, forecast in pairs])


def _best_baseline(
    scored: Mapping[str, Mapping[str, Any]],
) -> tuple[float | None, str | None]:
    """The lowest mean MAE among the four baselines that actually ran."""
    ran = {
        name: entry["mae_mean"]
        for name, entry in scored.items()
        if name in BASELINE_NAMES and entry["mae_mean"] is not None
    }
    if not ran:
        return None, None
    best = min(ran, key=lambda name: float(ran[name]))
    return float(ran[best]), best


def _skill(model: float | None, best: float | None) -> tuple[float | None, str | None]:
    if model is None or best is None:
        return None, "no baseline ran in this backtest: skill has no denominator"
    if best == 0.0:
        return None, "the best baseline has MAE 0: skill is not defined"
    return 1.0 - model / best, None


def _pairs(
    windows: Sequence[Mapping[str, Any]], name: str
) -> list[tuple[float, list[float]]] | None:
    """(actual, nine quantiles) per (window, step). None when a window lacks them."""
    out: list[tuple[float, list[float]]] = []
    for record in windows:
        band = record["forecasts"][name]["quantiles"]
        if band is None:
            return None
        out += list(zip(record["actual"], band, strict=True))
    return out


def _calibration(windows: Sequence[Mapping[str, Any]], name: str) -> dict[str, Any]:
    pairs = _pairs(windows, name)
    if pairs is None or not pairs:
        return {"coverage_q": None, "max_deviation": None, "reason": NO_QUANTILES}
    n = len(pairs)
    coverage = [
        sum(1 for actual, band in pairs if actual <= band[index]) / n
        for index in range(len(QUANTILE_LEVELS))
    ]
    deviations = [
        abs(share - level)
        for share, level in zip(coverage, QUANTILE_LEVELS, strict=True)
    ]
    bounds = [2.0 * math.sqrt(level * (1.0 - level) / n) for level in QUANTILE_LEVELS]
    return {
        "n": n,
        "coverage_q": coverage,
        "max_deviation": max(deviations),
        "coverage80": sum(1 for a, band in pairs if band[0] <= a <= band[-1]) / n,
        "flagged": any(
            deviation > bound
            for deviation, bound in zip(deviations, bounds, strict=True)
        ),
        "reason": None,
    }


def _wqs(windows: Sequence[Mapping[str, Any]], name: str) -> dict[str, Any]:
    """2 sum_q sum_w,h rho_q / (9 sum |y|). The mean pinball loss when sum |y| = 0."""
    pairs = _pairs(windows, name)
    if pairs is None or not pairs:
        return {"wqs": None, "wqs_normalized": None, "wqs_reason": NO_QUANTILES}
    total = sum(
        _pinball(actual, band[index], level)
        for actual, band in pairs
        for index, level in enumerate(QUANTILE_LEVELS)
    )
    scale = sum(abs(actual) for actual, _ in pairs)
    if scale == 0.0:
        return {
            "wqs": total / (len(QUANTILE_LEVELS) * len(pairs)),
            "wqs_normalized": False,
            "wqs_reason": "sum |y| is 0: this is the unnormalized mean pinball loss",
        }
    return {
        "wqs": 2.0 * total / (len(QUANTILE_LEVELS) * scale),
        "wqs_normalized": True,
        "wqs_reason": None,
    }


def _pinball(actual: float, forecast: float, level: float) -> float:
    delta = actual - forecast
    return max(level * delta, (level - 1.0) * delta)


def _lead_time(
    windows: Sequence[Mapping[str, Any]], name: str, tau: float | None
) -> dict[str, Any]:
    """Design 6.12's threshold-crossing score, over windows that start below tau."""
    if tau is None:
        return {"reason": NO_TAU}
    if any(record["forecasts"][name]["quantiles"] is None for record in windows):
        return {"reason": NO_QUANTILES}
    leads: list[int] = []
    timing: list[int] = []
    tally = {"eligible": 0, "hits": 0, "misses": 0, "false_alarms": 0, "quiet": 0}
    for record in windows:
        if record["last_context"] >= tau:
            continue
        tally["eligible"] += 1
        band = record["forecasts"][name]["quantiles"]
        crossed = _first_at_or_above(record["actual"], tau)
        alarmed = _first_at_or_above([step[ALARM_INDEX] for step in band], tau)
        _tally(tally, crossed, alarmed, leads, timing)
    return {
        **tally,
        "hit_rate": _share(tally["hits"], tally["hits"] + tally["misses"]),
        "false_alarm_rate": _share(
            tally["false_alarms"], tally["false_alarms"] + tally["quiet"]
        ),
        "median_lead": statistics.median(leads) if leads else None,
        "median_timing_error": statistics.median(timing) if timing else None,
        "reason": None,
    }


def _tally(
    tally: dict[str, int],
    crossed: int | None,
    alarmed: int | None,
    leads: list[int],
    timing: list[int],
) -> None:
    if crossed is not None and alarmed is not None:
        tally["hits"] += 1
        leads.append(crossed)
        timing.append(abs(crossed - alarmed))
    elif crossed is not None:
        tally["misses"] += 1
    elif alarmed is not None:
        tally["false_alarms"] += 1
    else:
        tally["quiet"] += 1


def _first_at_or_above(values: Sequence[float], tau: float) -> int | None:
    return next((index for index, value in enumerate(values) if value >= tau), None)


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


# -- storing and printing -------------------------------------------------------------


def persist(
    store: Store, backtest: Mapping[str, Any], *, pooled_across: Sequence[str] = ()
) -> str:
    """Write one forecast_runs row. `put_forecast_run` fills the id and the claim."""
    # Direct callers must satisfy the same word refusal as the report.
    refuse_words(
        json.dumps(
            [backtest["warnings"], backtest["assumptions"], backtest["decision"]]
        )
    )
    return store.put_forecast_run({
        "series_id": backtest["series_id"],
        "target": backtest["target"],
        "variant": backtest["variant"],
        "ordering": backtest["ordering"],
        "placebo_seed": backtest["placebo_seed"],
        "horizon": backtest["horizon"],
        "c_min": backtest["c_min"],
        "stride": backtest["stride"],
        "forecasters": backtest["forecasters"],
        "windows": {
            "retained": backtest["windows"],
            "dropped": backtest["dropped"],
            "dropped_counts": backtest["dropped_counts"],
        },
        "metrics": backtest["metrics"],
        "decision": backtest["decision"],
        # The invocation and the constants it ran under. `scenario` is the one free
        # column on this row, and a stored number whose constants are not beside it is
        # a number nobody can compare against the next run.
        "scenario": {
            **({"pooled_across": list(pooled_across)} if pooled_across else {}),
            "command": backtest["command"],
            "covariates": backtest["covariates"],
            # None, not [], on a row older than W7-T3: it recorded no exclusions.
            "excluded": backtest.get("excluded"),
            "tau": backtest["tau"],
            "placebo": backtest.get("placebo"),
            "constants": _constants(backtest),
        },
        "missingness_policy": backtest["missingness_policy"],
        "warnings": backtest["warnings"],
        "assumptions": backtest["assumptions"],
    })  # fmt: skip


def _constants(backtest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "delta": backtest["delta"],
        "w": backtest["w"],
        "k_min": backtest["k_min"],
        "c_min": backtest["c_min"],
        "horizon": backtest["horizon"],
        "stride": backtest["stride"],
        "baseline_window": backtest["baseline_window"],
        "threshold_rule": backtest["threshold_rule"],
    }


def report(backtest: Mapping[str, Any]) -> str:
    """The whole run on one page: constants, table, drops, warnings, licence."""
    excluded = backtest.get("excluded")
    named = ", ".join(f"{one['column']} ({one['coverage']})" for one in excluded or ())
    lines = [
        f"forecast backtest  series {backtest['series_id']}"
        f"  target {backtest['target']} ({backtest['unit']})",
        f"clock {backtest['clock']}  variant {backtest['variant']}"
        f"  ordering {backtest['ordering']}"
        f"  placebo_seed {backtest['placebo_seed']}"
        f"  policy {backtest['missingness_policy']}",
        "constants: "
        + "  ".join(f"{key} {value}" for key, value in _constants(backtest).items()),
        f"tau {_round(backtest['tau'])} ({backtest['threshold_rule']})"
        f"  rows {backtest['n_rows']}"
        f"  windows {len(backtest['windows'])} retained,"
        f" {len(backtest['dropped'])} dropped",
        f"covariates: {', '.join(backtest['covariates']) or 'none'}",
        f"excluded: {'not recorded' if excluded is None else named or 'none'}."
        " A variant is named by its column set.",
        f"command: {' '.join(backtest['command'])}",
        "",
        render_table(_rows(backtest), _TABLE),
        "",
        "dropped windows:",
        *_counted(backtest["dropped_counts"]),
        "warnings:",
        *[f"  {line}" for line in backtest["warnings"] or ["none"]],
        "assumptions:",
        *[f"  {line}" for line in backtest["assumptions"]],
    ]
    # ADR-014, before anything is printed and before the caller stores the run.
    return refuse_words("\n".join([*lines, *_licences(backtest)]))


def _rows(backtest: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name, entry in backtest["metrics"]["forecasters"].items():
        calibration = entry["calibration"]
        lead = entry["lead_time"]
        rows.append({
            "forecaster": name,
            "n_windows": entry["n_windows"],
            "mae_mean": _round(entry["mae_mean"]),
            "mae_median": _round(entry["mae_median"]),
            "skill": _round(entry["skill"]),
            "cal_max_dev": _round(calibration["max_deviation"]),
            "coverage80": _round(calibration.get("coverage80")),
            "wqs": _round(entry["wqs"]),
            "lead_hit_rate": _round(lead.get("hit_rate")),
            "false_alarm_rate": _round(lead.get("false_alarm_rate")),
            "median_lead": _round(lead.get("median_lead")),
            "claim_class": entry["claim_class"],
        })  # fmt: skip
    return rows


def _counted(counts: Mapping[str, int]) -> list[str]:
    if not counts:
        return ["  none"]
    return [f"  {reason}  {count}" for reason, count in sorted(counts.items())]


def _licences(backtest: Mapping[str, Any]) -> list[str]:
    """The weights licence, printed whenever a checkpoint was involved. Design 6.12."""
    return [
        f"weights: {entry['checkpoint']} under {entry['license']}"
        f" (research use, not production), device {entry['device']},"
        f" padding_mode {entry['padding_mode']}"
        for entry in backtest["forecasters"]
        if entry["license"]
    ]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
