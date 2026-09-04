"""A conditional forecast at the end of a series. Design 6.12, W6-T1.

A backtest asks what a forecaster gets right about a history that already happened. A
scenario asks a different question, at a place a backtest never stands: at row N, the
end of the series, under a future somebody has DECLARED.

    telltale forecast scenario --series S --target output_tokens --horizon 4 \\
        --future tool_calls_since_prev=2,2,2,2 --name quiet

That says: suppose the next four model requests each make two tool calls. It does not
say the next four requests will. It does not say anything about what happens if you
arrange for them to. It hands the forecaster a window whose past-future covariate block
holds the declared path over [ctx_start, N + H) and reports what came back, and the
last line of every page it prints is SCENARIO_SENTENCE.

Four rules make that a claim somebody can check rather than a number somebody guessed.

  A path may only be declared for a column the registry marks DECLARABLE on this clock,
  and every other column is refused by name with `PAST_ONLY`. The distinction is not
  about data and not about the model: a declared path is a supposition about a quantity
  somebody chooses, and `output_tokens` is not one of those.

  There is no actual and so there is no error. Origin N is one past the last row, so
  `metrics` is `{}` and the row carries NO_ACTUAL saying why. Calibration is QUOTED from
  the newest stored true-order backtest of the same (series, target, horizon) by run id,
  and never recomputed here: a number computed at an origin with no actual behind it is
  not a calibration of anything.

  Nothing is imputed. A hole in the context of a declared column is a refusal, exactly
  as it is in the candidate protocol: the whole subject of a scenario run is a covariate
  whose past is known and whose future is declared.

  A scenario horizon is not a backtested horizon. `SCENARIO_HORIZONS` reaches 16 where
  `TargetSpec.horizons` stops at 4, and the report says on its own face when the horizon
  it ran has no stored score behind it.

The baselines read the target column and nothing else, so under a baseline two
scenarios return the same numbers. That is not a bug in either, and the report says it:
NOT_A_READER names every forecaster that reported reading no past-future covariate, and
`compare` says it again from the numbers when two declared paths give one answer.
"""

from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from telltale.forecast import (
    DECLARABLE,
    MAX_CONTEXT,
    ORDERING_TRUE,
    PAST_ONLY,
    SCENARIO_HORIZONS,
    SCENARIO_SENTENCE,
    TARGETS,
    Window,
    refuse_words,
)
from telltale.forecast import backtest as backtester
from telltale.forecast.backtest import Refused, regime_start
from telltale.forecast.candidate import FUTURE_PREFIX, future_names

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.forecast import Forecaster, TargetSpec
    from telltale.model import Series
    from telltale.store import Store

# What the stored row's variant is called, appended to the registry's own variant name
# so that a scenario row and the backtest it quotes calibration from are two different
# variants in the table rather than two rows nobody can tell apart.
VARIANT = "scenario"

# The warning every scenario row carries. Not a caveat: origin N is one past the last
# row of the series, so there is no value to subtract a forecast from, and a stored run
# whose `metrics` is `{}` has to say whether that is an empty measurement or no
# measurement at all.
NO_ACTUAL = "no actual: a scenario has no error"
# Named by the forecasters it applies to. A forecaster reports the covariates it read
# in `ForecastResult.covariates`, and the past-future ones carry FUTURE_PREFIX; this
# reads that DECLARATION, which is why `compare` below repeats the finding from the
# numbers. Two scenarios that differ in a path and agree to the last bit is the measured
# version of the same sentence.
NOT_A_READER = (
    "these forecasters reported reading no past-future covariate, so their forecast is"
    " the same under every declared path and the declaration changed nothing for them:"
)
# Printed and stored whenever the horizon has no rolling-origin score behind it, which
# is every horizon outside TargetSpec.horizons. Design 6.12 pre-registered 1 and 4.
NO_BACKTEST = (
    "horizon {horizon} is outside the backtested horizons {horizons} for this target:"
    " no rolling-origin score exists at this horizon, so nothing here is calibrated"
)
# When the store holds no true-order backtest of this (series, target, horizon) at all.
# Different from a calibration that came back wide: one is a measurement, the other is
# the absence of one, and a report may not spend the same words on both.
NO_CALIBRATION = (
    "no stored true-order backtest of this series, target and horizon:"
    " calibration is not assessable and none is quoted"
)

# Every column DECLARABLE names on either clock is a count or a flag, and its unit in
# the compiled series says which. A declared path of minus two tool calls supposes
# nothing, so a negative value is refused for those units whatever the registry says
# about the column as a target. A column whose unit is not one of these (there is none
# today) keeps the registry's `nonnegative` flag when it is also a target, and is
# otherwise unconstrained: this list is the rule, not a guess about future columns.
COUNTING_UNITS = ("calls", "files", "runs", "lines", "directories", "flag")

# `col=v1,v2,...` on the command line. One `=`, split once, so a column name is
# whatever precedes the first one and every value is parsed as a float.
ASSIGN = "="
SEPARATOR = ","

# -- the refusals ---------------------------------------------------------------------
#
# Every one subclasses backtest.Refused, so the CLI's existing `except Refused` turns
# each into exit 2 with the text below, and a test can still name the one it means.


class BadHorizon(Refused):
    """A horizon outside SCENARIO_HORIZONS."""


class UnknownTarget(Refused):
    """A target the registry does not carry."""


class BadPath(Refused):
    """A `--future` string that is not `col=v1,v2,...` of finite numbers."""


class NoPaths(Refused):
    """A scenario declaring nothing. That is a forecast at N, not a scenario."""


class TargetPath(Refused):
    """A declared path for the target itself."""


class NoColumn(Refused):
    """A declared path for a column the series does not have."""


class NotDeclarable(Refused):
    """A declared path for a past-only column. PAST_ONLY is the reason."""


class WrongLength(Refused):
    """A declared path that is not H long."""


class NotFinite(Refused):
    """A declared path holding a NaN or an infinity."""


class NegativePath(Refused):
    """A negative value declared for a counting column."""


class ShortRegime(Refused):
    """Fewer than c_min rows since the last changepoint at or before N."""


class NotComparable(Refused):
    """Two scenario rows that are not about the same series, target and horizon."""


@dataclass(frozen=True)
class Scenario:
    """One declared future: a name to print it under, a horizon, and the paths.

    Frozen, and `paths` is built once by `parse_paths` or by a caller that already has
    the floats. `check` below is what makes an instance legal; constructing one proves
    nothing, which is why `run` calls `check` rather than trusting its argument.
    """

    name: str
    horizon: int
    paths: dict[str, list[float]] = field(default_factory=dict)


# -- parsing and checking -------------------------------------------------------------


def parse_paths(specs: Sequence[str], horizon: int) -> dict[str, list[float]]:
    """`col=v1,v2,...` strings into one dict, refusing anything that is not that.

    A repeated column is a refusal rather than a last-one-wins overwrite: two `--future`
    flags naming one column are two different suppositions, and silently keeping the
    second is the "duplicate is not one" defect AGENTS.md names.
    """
    found: dict[str, list[float]] = {}
    for spec in specs:
        name, path = _one(spec, horizon)
        if name in found:
            raise BadPath(
                f"{name}: declared twice. Two --future flags for one column are two"
                " different suppositions and nothing here picks between them."
            )
        found[name] = path
    if not found:
        raise NoPaths(
            "a scenario declares at least one --future path. With none it is a"
            " forecast at the end of the series under no conditions, which is what"
            " `forecast backtest` scores at every other origin."
        )
    return found


def _one(spec: str, horizon: int) -> tuple[str, list[float]]:
    """One `col=v1,v2,...`, with the column named in every refusal it can raise."""
    if ASSIGN not in spec:
        raise BadPath(
            f"{spec!r} is not a declared path: the spelling is"
            f" col{ASSIGN}v1{SEPARATOR}v2{SEPARATOR}... with one value per step"
        )
    name, _, values = spec.partition(ASSIGN)
    cells = [cell.strip() for cell in values.split(SEPARATOR)]
    path: list[float] = []
    for cell in cells:
        try:
            path.append(float(cell))
        except ValueError as bad:
            raise BadPath(f"{name}: {cell!r} is not a number ({bad})") from bad
    _length(name, path, horizon)
    return name.strip(), path


def _length(name: str, path: Sequence[float], horizon: int) -> None:
    """One value per step, and the refusal names both numbers.

    Checked here and again in `check`, because a Scenario can be built without going
    through the parser and a path of the wrong length reaches the model as a covariate
    the model takes without a word (forecast/timesfm.py `_span`).
    """
    if len(path) != horizon:
        raise WrongLength(
            f"{name}: {len(path)} values declared for horizon {horizon}."
            " A declared path is one value per step."
        )


def check_horizon(horizon: int) -> None:
    """The one refusal a caller can ask for before it builds a forecaster.

    Split out of `registered` because building the timesfm forecaster loads a 1.32 GB
    checkpoint, and a horizon nobody may ask for should not have that behind it.
    """
    if horizon not in SCENARIO_HORIZONS:
        raise BadHorizon(
            f"horizon {horizon} is not one of {list(SCENARIO_HORIZONS)}."
            " Every scenario horizon is one TimesFM call whose internal horizon is 64,"
            " and this list is what W6-T1 measured rather than what fits."
        )


def registered(series: Series, target: str, horizon: int) -> TargetSpec:
    """The registry entry for this scenario, or the refusal that says why not.

    The horizon is checked against SCENARIO_HORIZONS and NOT against
    `TargetSpec.horizons`: those are the backtested horizons, and a scenario is not
    scored. Everything else a target has to satisfy is `backtest.registered`'s question
    and is asked THROUGH it, at the registry's own first horizon, so that a rule added
    there reaches a scenario too rather than being re-spelled here and drifting.
    """
    check_horizon(horizon)
    spec = TARGETS.get(target)
    if spec is None:
        raise UnknownTarget(
            f"{target} is not a forecast target. Known: {sorted(TARGETS)}"
        )
    backtester.registered(series, target, spec.horizons[0])
    return spec


def check(series: Series, target: str, scenario: Scenario) -> TargetSpec:
    """Every refusal a scenario faces, before a forecaster is built or a store opened.

    Returns the registry entry, so a caller that has checked has the constants too and
    nobody looks the target up twice.
    """
    spec = registered(series, target, scenario.horizon)
    if not scenario.paths:
        raise NoPaths(
            f"scenario {scenario.name!r} declares no path. A conditional forecast"
            " conditions on something."
        )
    for name, path in scenario.paths.items():
        _declarable(series, target, name)
        _values(series, name, path, scenario.horizon)
    return spec


def _declarable(series: Series, target: str, name: str) -> None:
    """Whether this column may carry a path at all. Three refusals, in this order."""
    if name == target:
        raise TargetPath(
            f"{name} is the target of this scenario: declaring its path declares the"
            " answer. A scenario conditions a forecast, it does not supply one."
        )
    if name not in [column.name for column in series.columns]:
        raise NoColumn(f"series {series.series_id} has no column {name}")
    allowed = DECLARABLE.get(series.clock, ())
    if name not in allowed:
        raise NotDeclarable(
            f"{name}: {PAST_ONLY}. The {series.clock} clock declares"
            f" {list(allowed) or 'nothing'}."
        )


def _values(series: Series, name: str, path: Sequence[float], horizon: int) -> None:
    """One value per step, every one finite, and none negative on a counting column."""
    _length(name, path, horizon)
    negative = _nonnegative(series, name)
    for step, value in enumerate(path, start=1):
        if not math.isfinite(value):
            raise NotFinite(
                f"{name}: step {step} is {value}. A declared path is finite numbers;"
                " TimesFM would take a NaN and interpolate it without a word."
            )
        if negative and value < 0:
            raise NegativePath(
                f"{name}: step {step} declares {value}, and {name} is counted in"
                f" {_unit(series, name)}. There is no such quantity to suppose."
            )


def _nonnegative(series: Series, name: str) -> bool:
    """Whether a negative value is refusable for this column, and on what authority."""
    spec = TARGETS.get(name)
    if spec is not None:
        return spec.nonnegative
    return _unit(series, name) in COUNTING_UNITS


def _unit(series: Series, name: str) -> str:
    return next(column.unit for column in series.columns if column.name == name)


# -- the window at the end of the series ----------------------------------------------


def window_at_end(
    series: Series,
    target: str,
    scenario: Scenario,
    past_only: Sequence[str],
) -> Window:
    """The one window a scenario forecasts from: context [ctx_start, N), paths beyond.

    Origin N is one past the last row, and the context arithmetic is the backtester's:
    `ctx_start = max(regime_start(changepoints, N), N - MAX_CONTEXT)`, through
    `backtest.regime_start` itself so there is one definition of a regime boundary.
    A regime shorter than c_min is refused rather than padded from the regime before it.

    `future` holds each declared column's KNOWN context values over [ctx_start, N)
    followed by its declared path, which is n_ctx + H long: the length
    forecast/timesfm.py asserts by name, and the length the model takes silently when it
    is wrong. A hole in the context of a declared column is a refusal, and nothing here
    imputes one.
    """
    spec = TARGETS[target]
    origin = len(series.rows)
    ctx_start = max(regime_start(series.changepoints, origin), origin - MAX_CONTEXT)
    if origin - ctx_start < spec.c_min:
        raise ShortRegime(
            f"origin {origin}: {origin - ctx_start} rows since the last changepoint is"
            f" below c_min {spec.c_min}, so nothing can be forecast from the end of"
            " this series yet"
        )
    names = [column.name for column in series.columns]
    columns = [target, *[name for name in past_only if name != target]]
    indices = [names.index(name) for name in columns]
    return Window(
        columns=columns,
        rows=[_row_at(series, columns, indices, at) for at in range(ctx_start, origin)],
        origin=origin,
        ctx_start=ctx_start,
        n_ctx=origin - ctx_start,
        horizon=scenario.horizon,
        target=target,
        future={
            name: [
                *_context(series, names.index(name), name, ctx_start, origin),
                *path,
            ]
            for name, path in scenario.paths.items()
        },
    )


def _row_at(
    series: Series, columns: Sequence[str], indices: Sequence[int], at: int
) -> list[float]:
    """One context row as floats, refusing on an unknown and naming the columns."""
    cells = [series.rows[at][position] for position in indices]
    holes = [name for name, cell in zip(columns, cells, strict=True) if cell is None]
    if holes:
        raise Refused(
            f"row {at} of the context holds an unknown in {holes}."
            " Nothing here imputes one."
        )
    return [float(cell) for cell in cells if cell is not None]


def _context(
    series: Series, position: int, name: str, ctx_start: int, origin: int
) -> list[float]:
    """One declared column's observed values over [ctx_start, N), refusing a hole."""
    values = [series.rows[at][position] for at in range(ctx_start, origin)]
    if any(value is None for value in values):
        raise Refused(
            f"declared column {name} holds an unknown inside [{ctx_start}, {origin})."
            " A declared path continues a column whose past is known, and nothing here"
            " imputes one."
        )
    return [float(value) for value in values if value is not None]


# -- the run --------------------------------------------------------------------------


def run(
    series: Series,
    target: str,
    scenario: Scenario,
    forecasters: Mapping[str, Forecaster],
    model: str,
) -> dict[str, Any]:
    """Every forecaster on the one window at origin N. Returns the run, unstored.

    No `store` parameter, for backtest.run's reason: a run reads the Series it was
    handed and the registry, and a Window is forbidden to carry a store handle.
    `calibration` below takes the store, and `persist` writes.
    """
    spec = check(series, target, scenario)
    if model not in forecasters:
        raise Refused(f"--model {model} is not among --forecasters {list(forecasters)}")
    selected, excluded = backtester._variant(series, target)
    window = window_at_end(series, target, scenario, selected)
    forecasts = {
        name: _forecast(one, window, scenario.horizon)
        for name, one in forecasters.items()
    }
    return {
        "series_id": series.series_id,
        "clock": series.clock,
        "target": target,
        "unit": spec.unit,
        "variant": f"{spec.variant}_{VARIANT}",
        "ordering": ORDERING_TRUE,
        "placebo_seed": None,
        "name": scenario.name,
        "horizon": scenario.horizon,
        "stride": scenario.horizon,
        "c_min": spec.c_min,
        "origin": window.origin,
        "ctx_start": window.ctx_start,
        "n_ctx": window.n_ctx,
        # y_{o-1}: the one context value a stored window record keeps, so that a row
        # read back can be read against a threshold without the context itself.
        "last_context": window.column(target)[-1],
        "model": model,
        "paths": {name: list(path) for name, path in scenario.paths.items()},
        "declarable": list(DECLARABLE.get(series.clock, ())),
        "units": {name: _unit(series, name) for name in scenario.paths},
        # Both blocks, named apart, exactly as the candidate protocol spells them: a
        # declared column appears as the past-only history it has and as the
        # past-future path it was given, and the two are different claims.
        "covariates": [*window.covariates, *future_names(sorted(scenario.paths))],
        "n_rows": len(series.rows),
        "missingness_policy": series.missingness_policy,
        "command": list(sys.argv),
        "forecasters": [
            backtester._declared(name, one) for name, one in forecasters.items()
        ],
        "forecasts": forecasts,
        "sentence": SCENARIO_SENTENCE,
        # Filled by `calibration` when a caller has a store. None is "nobody looked",
        # and the report says which of the two it is printing.
        "calibration": None,
        "warnings": _warnings(spec, scenario, forecasts, excluded),
        "assumptions": _assumptions(series, target, scenario),
    }


def _forecast(forecaster: Forecaster, window: Window, horizon: int) -> dict[str, Any]:
    """One forecaster on the window, timed on its own call."""
    started = time.perf_counter()
    result = forecaster.forecast(window, horizon)
    wall_ms = (time.perf_counter() - started) * 1000.0
    return {
        "point": list(result.point[0]),
        "quantiles": None if result.quantiles is None else result.quantiles[0],
        "wall_ms": round(wall_ms, 3),
        # The adapter's own timing of the model call alone, when it kept one.
        "model_ms": getattr(forecaster, "last_wall_ms", None),
        "covariates": list(result.covariates),
        "read_future": any(n.startswith(FUTURE_PREFIX) for n in result.covariates),
        "warnings": list(result.warnings),
    }


def _warnings(
    spec: TargetSpec,
    scenario: Scenario,
    forecasts: Mapping[str, Mapping[str, Any]],
    excluded: Sequence[Mapping[str, str]],
) -> list[str]:
    found = [NO_ACTUAL]
    if scenario.horizon not in spec.horizons:
        found.append(
            NO_BACKTEST.format(horizon=scenario.horizon, horizons=list(spec.horizons))
        )
    blind = sorted(name for name, one in forecasts.items() if not one["read_future"])
    if blind:
        found.append(f"{NOT_A_READER} {', '.join(blind)}")
    found.extend(
        f"column {item['column']} was excluded from the variant:"
        f" coverage {item['coverage']}"
        for item in excluded
    )
    return found


def _assumptions(series: Series, target: str, scenario: Scenario) -> list[str]:
    """What this run rests on, ending with the sentence that rides on every scenario."""
    return [
        f"origin {len(series.rows)} is one past the last row of the series. There is no"
        " actual at any step, so there is no error, no MAE and no calibration computed"
        " here; `metrics` is empty and the run carries a warning saying so.",
        f"each declared path spans the {scenario.horizon} steps after the origin and is"
        " hung on the window as a past-future covariate of length n_ctx + H, the same"
        " shape and the same padding_mode the candidate protocol uses.",
        "the declared columns also ride as past-only covariates over the context, which"
        " is their observed history; the declared path is the same column's supposed"
        " continuation and is named apart with the future: prefix.",
        "windows are not scored and none was dropped: a scenario is one window. A hole"
        " in the context or in a declared column refused the run instead.",
        f"target {target} on the {series.clock} clock, missingness policy"
        f" {series.missingness_policy}. Nothing here imputes an unknown.",
        SCENARIO_SENTENCE,
    ]


# -- calibration, quoted and never computed -------------------------------------------


def calibration(store: Store, found: Mapping[str, Any]) -> dict[str, Any] | None:
    """The newest stored true-order backtest of this (series, target, horizon), or None.

    Quoted by run id and never recomputed. The registry's own variant is required, so a
    candidate run and another scenario are not mistaken for a rolling-origin score of
    this pair, and `metrics` must carry forecasters, which is what a scenario row does
    not have.

    Newest rather than any: `forecast_runs` is ordered by creation and a re-run of a
    pair is a correction of the older one, which is the rule `cli_forecast._stored_true`
    already applies to this table.
    """
    spec = TARGETS[str(found["target"])]
    matched = [
        row
        for row in store.forecast_runs(str(found["series_id"]))
        if row["ordering"] == ORDERING_TRUE
        and row["target"] == found["target"]
        and row["variant"] == spec.variant
        and int(row["horizon"]) == int(found["horizon"])
        and (row["metrics"] or {}).get("forecasters")
    ]
    if not matched:
        return None
    newest = matched[-1]
    return {
        "forecast_run_id": str(newest["forecast_run_id"]),
        "created_at": str(newest["created_at"]),
        "forecasters": {
            name: {
                "coverage80": (entry.get("calibration") or {}).get("coverage80"),
                "cal_max_dev": (entry.get("calibration") or {}).get("max_deviation"),
            }
            for name, entry in newest["metrics"]["forecasters"].items()
        },
    }


# -- storing --------------------------------------------------------------------------


def stored(store: Store, series_id: str, run_id: str) -> dict[str, Any]:
    """One stored scenario row of this series by id, or the refusal that lists them.

    A scenario row is the one on this table with a `scenario.name` and an empty
    `metrics`: a backtest, a placebo and a candidate run all carry scores, and this
    command compares point paths that were never scored against anything.
    """
    rows = [
        row
        for row in store.forecast_runs(series_id)
        if (row["scenario"] or {}).get("name") and not row["metrics"]
    ]
    for row in rows:
        if row["forecast_run_id"] == run_id:
            return dict(row)
    listed = ", ".join(
        f"{row['forecast_run_id']} ({row['scenario']['name']})" for row in rows
    )
    raise NotComparable(
        f"{run_id}: no stored scenario of series {series_id}."
        f" Stored: {listed or 'none'}"
    )


def persist(
    store: Store, found: Mapping[str, Any], *, pooled_across: Sequence[str] = ()
) -> str:
    """Write one forecast_runs row. `put_forecast_run` fills the id and the claim.

    `metrics` is `{}` and that is the point: this row holds a forecast and no score.
    The claim class the store writes is predictive, which is what a forecast of steps
    nobody has observed is, and nothing here may present it as anything else.
    """
    row = record(found)
    if pooled_across:
        row["scenario"]["pooled_across"] = list(pooled_across)
    # ADR-014 at the row and not only at the renderer: a caller of this function alone
    # may not store what the report may not print.
    refuse_words(json_text(row))
    return store.put_forecast_run(row)


def record(found: Mapping[str, Any]) -> dict[str, Any]:
    """The forecast_runs mapping, which is also the shape `compare` reads.

    A freshly built run and a row read back out of the store are the same shape here,
    so `--compare-with` compares a scenario against a stored one without a second
    decoder in between.
    """
    return {
        "series_id": found["series_id"],
        "target": found["target"],
        "variant": found["variant"],
        "ordering": found["ordering"],
        "placebo_seed": found["placebo_seed"],
        "horizon": found["horizon"],
        "c_min": found["c_min"],
        "stride": found["stride"],
        "forecasters": found["forecasters"],
        "windows": {
            "retained": [
                {
                    "origin": found["origin"],
                    "ctx_start": found["ctx_start"],
                    "n_ctx": found["n_ctx"],
                    "horizon": found["horizon"],
                    # No actual, and None rather than an empty list: "nobody observed
                    # these steps" and "these steps were observed and held nothing" are
                    # different statements about a window.
                    "actual": None,
                    "last_context": found["last_context"],
                    "flags": [VARIANT],
                    "forecasts": found["forecasts"],
                }
            ],
            "dropped": [],
            "dropped_counts": {},
        },
        "metrics": {},
        "decision": None,
        "scenario": {
            "name": found["name"],
            "horizon": found["horizon"],
            "paths": found["paths"],
            "declarable": found["declarable"],
            "units": found["units"],
            "sentence": found["sentence"],
            "command": found["command"],
            "covariates": found["covariates"],
            "model": found["model"],
            "calibration_from": _quoted(found["calibration"]),
            "calibration": found["calibration"],
            "constants": {
                "c_min": found["c_min"],
                "horizon": found["horizon"],
                "stride": found["stride"],
                "max_context": MAX_CONTEXT,
                "scenario_horizons": list(SCENARIO_HORIZONS),
                "origin": found["origin"],
            },
        },
        "missingness_policy": found["missingness_policy"],
        "warnings": found["warnings"],
        "assumptions": found["assumptions"],
    }


def _quoted(found: Mapping[str, Any] | None) -> str | None:
    return None if found is None else str(found["forecast_run_id"])


def json_text(row: Mapping[str, Any]) -> str:
    """The text ADR-014's word refusal runs over: everything this row says in words."""
    return json.dumps([
        row["warnings"], row["assumptions"], row["decision"],
        row["scenario"]["sentence"], row["scenario"]["name"],
    ])  # fmt: skip
