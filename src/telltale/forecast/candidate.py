"""Candidate conditioning. Design 6.12 (H8), spec 15.8.

The situation is a merge decision. A change is sitting there, and what is known about it
is what anybody can read off the diff: how many files, how many lines, how many
subsystems, whether it touched tests, whether it moved a lockfile. That is block A. What
is not known is anything that happens after the merge, so those are the targets:
`merge_verification_ms`, `merge_verification_failed`, `rework_within_3` and
`rework_within_3_lag3`.

The protocol is two runs over the SAME origins.

  The unconditioned run knows the history and nothing about the candidate. Context
  [ctx_start, o), past-only covariates, H from `CANDIDATE_HORIZON`.

  The conditioned run is the same context and the same origin, plus the candidate's own
  A block as a PAST-FUTURE covariate of length n_ctx + H: the block's values over rows
  [ctx_start, o], which is one row longer than the context because row o's features are
  known at the moment the decision is taken, then row o's own value again for steps 2
  to H. That edge padding is done HERE and recorded, rather than being left to the
  model, which pads a short horizon covariate the same way and says nothing. No row
  past the origin is ever read: the rows after the candidate are exactly what nobody
  knows at a merge decision.

Three refusals, one horizon rule and one sentence hold the meaning of the difference in
place.

  `attempts_to_land` is refused as a target. It is known at merge time, so conditioning
  a forecast of it on the candidate's features is scoring a lookup.

  A row whose target is unknown, or whose A block cannot be read, is not in the frame
  at all (forecast/frame.py), so the backtester's own `o <= N - H` over that frame is
  the whole origin ceiling and the `o <= N - REWORK_TAIL` ceiling W3-T2 wrote is gone
  with it. Both runs of the pair are cut from that one frame, so a binary commit costs
  the pair two rows rather than costing E16 the run, and the block is never narrowed.

  `rework_within_3_lag3` runs at H = 4 and is SCORED ON STEP 4 ALONE. Row j of that
  column carries change j - 3's label, so from origin o the fourth step is change o's
  own label; steps 1 to 3 are the labels of changes o - 3 .. o - 1, which are not what
  a merge decision asks. `SCORED_STEPS` names the steps and every output prints them.

  Every output carries CANDIDATE_SENTENCE. The difference between two forecasts of one
  observed future is a statement about the forecasts, and the moment it is read as a
  statement about merging is the moment this whole protocol has misled somebody.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from telltale.forecast import (
    ABLATION_A,
    ABLATION_C,
    CANDIDATE_FORBIDDEN,
    CANDIDATE_HORIZON,
    CANDIDATE_SENTENCE,
    CANDIDATE_TARGETS,
    MAX_CONTEXT,
    QUANTILE_LEVELS,
    SCORED_STEPS,
    Window,
    refuse_words,
)
from telltale.forecast import backtest as backtester
from telltale.forecast import features as extractor
from telltale.forecast import frame as frames
from telltale.forecast.backtest import FORECASTABLE, Refused, window_mae
from telltale.report import render_table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.forecast import Forecaster
    from telltale.model import Series
    from telltale.store import Store

# W8-T3 split the git-diff extractor into forecast/features.py; these three names are
# re-exported for the callers that reach them through the protocol module. Bound rather
# than star-imported, so what is public here is a list somebody wrote.
features = extractor.features
NotACandidate = extractor.NotACandidate
PROVENANCE = extractor.PROVENANCE

UNCONDITIONED = "unconditioned"
CONDITIONED = "conditioned"

FORBIDDEN = (
    f"{CANDIDATE_FORBIDDEN} is known at merge time and design 6.12 forbids it as a"
    " candidate target: conditioning a forecast of it on the candidate's known features"
    " scores a lookup rather than a forecast"
)
# Read off the numbers rather than off a declared capability: two runs that agree to the
# last bit agree because nothing in them looked at the covariate that differs.
NO_FUTURE_READER = (
    "the conditioned and unconditioned runs scored identically: no forecaster in this"
    " run read a past-future covariate, so the difference below is 0 by construction"
    " and is a fact about the forecasters rather than about the candidate"
)
# Printed by every candidate report whether or not a checkpoint was involved, so that
# "this run used no licensed weights" and "somebody left the licence line out" are
# different lines on the page rather than the same absence.
NO_LICENCE = "weights: no licensed model in this run"

# How a past-future covariate is spelled in a stored run's covariate list and in what
# the TimesFM adapter reports it saw. One spelling, because a reader comparing the two
# runs is reading those lists against each other.
FUTURE_PREFIX = "future:"


def horizon(target: str) -> int:
    """H for this target, from the registry. Never a module-wide constant.

    The three quantities of the candidate row itself are one step away; the lagged
    rework label is four, because its fourth step is the candidate's own label.
    """
    return CANDIDATE_HORIZON[target]


def steps(target: str) -> tuple[int, ...] | None:
    """The steps this target is scored on, or None for every step. Absence from
    SCORED_STEPS is the registry's default: a target whose whole horizon is about the
    candidate is scored over all of it."""
    return SCORED_STEPS.get(target)


def future_names(a_block: Sequence[str]) -> list[str]:
    """The A block's names as they appear in a stored run's covariate list."""
    return [f"{FUTURE_PREFIX}{name}" for name in sorted(a_block)]


def check_target(target: str) -> None:
    """The two refusals design 6.12 puts in front of the protocol, before anything runs.

    Here rather than in the CLI because the rule is the protocol's: `telltale advise`
    (W5-T2) and any other caller reach the same two sentences, and a refusal that lived
    in one command would be a refusal the next caller does not get.
    """
    if target == CANDIDATE_FORBIDDEN:
        raise Refused(FORBIDDEN)
    if target not in CANDIDATE_TARGETS:
        raise Refused(
            f"{target} is not a candidate target: design 6.12 forecasts the POST-MERGE"
            f" columns {list(CANDIDATE_TARGETS)}, because the protocol conditions on"
            " what is known at the merge decision and every other column is either"
            " known then or is not about this change at all"
        )


_TABLE = ("run", "n_windows", "mae_mean", "mae_median", "pinball", "cal_max_dev",
          "coverage80")  # fmt: skip
_PAIRED = ("statistic", "unconditioned", "conditioned", "paired_median_difference")


def conditioned(
    store: Store | None,
    series: Series,
    target: str,
    forecasters: Mapping[str, Forecaster],
    model: str,
    *,
    origin_range: tuple[int, int] | None = None,
    a_block: Sequence[str] = ABLATION_A,
    past_only: Sequence[str] = ABLATION_C,
    c_min: int | None = None,
    pooled_across: Sequence[str] = (),
) -> dict[str, Any]:
    """The two runs, paired by origin, with the mandatory sentence on both.

    `store` may be None, which runs the protocol without writing it. Everything else is
    the same either way: the refusals fire before any forecaster is called, and the
    sentence is in the assumptions of both runs whether or not they reach the disk.
    """
    check_target(target)
    at = horizon(target)
    spec = backtester.registered(series, target, at)
    # The frame the backtester will score, taken HERE because `_attach` indexes rows by
    # a window's own ctx_start and origin, which are positions in that frame. The block
    # is named to it so a row whose features cannot be read costs that row and not the
    # run: `lines_added` is unknown on a commit that touched a binary file, and two of
    # those in a 169-row lineage refused every origin above the lower of them.
    block = _block(series, a_block, target)
    frame = frames.retained(series, target, block)
    for_block = frames.for_columns(frame)
    limit = _limit(frame, origin_range)
    plain = _run(frame, target, forecasters, past_only, c_min, None)
    fitted = _run(
        frame,
        target,
        forecasters,
        past_only,
        c_min,
        _attach(frame, a_block),
    )
    scored = steps(target)
    runs = {
        UNCONDITIONED: _within(plain, limit, scored),
        CONDITIONED: _within(fitted, limit, scored),
    }
    for name, one in runs.items():
        one["variant"] = f"{spec.variant}_candidate_{name}"
        one["assumptions"] = [*one["assumptions"], CANDIDATE_SENTENCE]
        # What the model SAW, on the row a reader compares the two runs by. Only the
        # conditioned run carries the A block, and `backtest.persist` writes this key
        # into `scenario.covariates`, so the difference between the two stored rows is
        # the difference the protocol is about rather than a variant name alone.
        if name == CONDITIONED:
            one["covariates"] = [*one["covariates"], *future_names(a_block)]
        one["candidate"] = {
            "a_block": list(a_block),
            "past_only": list(past_only),
            "future_length": "n_ctx + H",
            "padding_mode": "edge",
            "future_beyond_step_1": "edge-replicated from the candidate row",
            "origin_limit": limit,
            # On BOTH runs: the twin is scored over the same frame, and a reader
            # comparing them is entitled to know which rows the block cost.
            "excluded_for_block": for_block,
            "sentence": CANDIDATE_SENTENCE,
        }
    found = {
        "series_id": frame.series_id,
        "target": target,
        "unit": spec.unit,
        "model": model,
        "horizon": at,
        "scored_steps": None if scored is None else list(scored),
        "retained_rows": len(frame.rows),
        "excluded_for_block": for_block,
        "origin_limit": limit,
        "runs": runs,
        "paired": _paired(runs, model, scored),
        "sentence": CANDIDATE_SENTENCE,
        "warnings": _warnings(runs, model),
    }
    if store is not None:
        found["forecast_run_ids"] = [
            backtester.persist(store, one, pooled_across=pooled_across)
            for one in runs.values()
        ]
    return found


def _block(series: Series, a_block: Sequence[str], target: str = "") -> list[str]:
    """The A block this series can carry, with `target` dropped. The one refusal.

    Refused HERE rather than inside `retained`, which raises what `list.index` raises
    and loses the name of the missing column. The target is dropped because `retained`
    already keeps the rows where it is known, and naming it twice would count the same
    rows under two headings in the reason.
    """
    names = {column.name for column in series.columns}
    missing = [name for name in a_block if name not in names]
    if missing:
        raise Refused(f"series {series.series_id} has no column {missing}")
    return [name for name in a_block if name != target]


def _limit(frame: Series, origin_range: tuple[int, int] | None) -> dict[str, Any]:
    """The origins a run may be cut down to, and the reason there is no other ceiling.

    The reason is the module docstring's second refusal: an excluded row protects the
    actual side of a delayed label, so `o <= N - H` over the frame is the whole
    ceiling and W3-T2's `o <= N - REWORK_TAIL` would drop three known labels on top.
    """
    stop = len(frame.rows)
    if origin_range is not None:
        stop = min(stop, origin_range[1])
    return {
        "start": None if origin_range is None else origin_range[0],
        "stop": stop,
        "reason": "no delayed-label ceiling: a row whose target is unknown is not in"
        " the retained frame, so o <= N - H over that frame is the whole ceiling",
    }


def _run(
    series: Series,
    target: str,
    forecasters: Mapping[str, Forecaster],
    past_only: Sequence[str],
    c_min: int | None,
    prepare: Any,
) -> dict[str, Any]:
    return backtester.run(
        series,
        target,
        horizon(target),
        forecasters,
        c_min=c_min,
        covariates=past_only,
        prepare=prepare,
    )


def _attach(series: Series, a_block: Sequence[str]) -> Any:
    """A `prepare` hook that hangs the candidate's A block on the window.

    A hole in the block is a REFUSAL and never a dropped window: the candidate's known
    features are the whole subject of the conditioned run, and a candidate whose
    features are unknown is not a candidate this protocol has anything to say about.
    """
    # `_block` refused a column this series has no place for before the frame was cut.
    index = {column.name: position for position, column in enumerate(series.columns)}

    def prepare(window: Window) -> Window:
        # [ctx_start, o], and never one row further. At H > 1 the rows after the origin
        # are the next changes, which nobody has made at a merge decision; steps 2 to H
        # repeat the candidate's own value instead (frame.edge_padded), which is what
        # the model would have done silently to a block of this length.
        span = range(window.ctx_start, window.origin + 1)
        block = {
            name: frames.edge_padded(
                _known(series, index[name], span, name, window.origin), window.horizon
            )
            for name in a_block
        }
        return replace(window, future=block)

    return prepare


def _known(
    series: Series, position: int, span: range, name: str, origin: int
) -> list[float]:
    """One candidate column over [ctx_start, o], refusing on an unknown.

    Unreachable on a frame `conditioned` cut, which already dropped every row where a
    block column is unknown. It stays as the guard behind that: `_attach` is a
    `prepare` hook whose caller chooses the frame, and a block read that imputed a cell
    would be the unconditioned run wearing the conditioned run's name.
    """
    values = [series.rows[row][position] for row in span]
    if any(value is None for value in values):
        raise Refused(
            f"origin {origin}: candidate column {name} holds an unknown inside"
            " [ctx_start, o]. Nothing here imputes one."
        )
    return [float(value) for value in values if value is not None]


def _within(
    one: dict[str, Any], limit: Mapping[str, Any], scored: Sequence[int] | None
) -> dict[str, Any]:
    """The run cut down to the origins the limit allows, rescored on `scored` steps.

    The window RECORDS keep every step: they are what a reader recomputes a metric
    from, and cutting them down would hide the three steps this target does not score.
    What is cut is what the metrics are computed over.
    """
    start = limit["start"]
    kept = [
        record
        for record in one["windows"]
        if (start is None or int(record["origin"]) >= start)
        and int(record["origin"]) <= limit["stop"]
    ]
    return {
        **one,
        "windows": kept,
        "scored_steps": None if scored is None else list(scored),
        "metrics": backtester.metrics(
            [frames.scored(record, scored) for record in kept], one["tau"]
        ),
    }


def _paired(
    runs: Mapping[str, Mapping[str, Any]], model: str, scored: Sequence[int] | None
) -> dict[str, Any]:
    """Paired median difference in MAE and pinball loss, conditioned minus plain.

    Over the same steps the metrics are, so the paired number and the table above it
    are two views of one comparison rather than two comparisons.
    """
    plain, fitted = (
        {int(r["origin"]): frames.scored(r, scored) for r in runs[name]["windows"]}
        for name in (UNCONDITIONED, CONDITIONED)
    )
    shared = sorted(set(plain) & set(fitted))
    mae = [window_mae(fitted[o], model) - window_mae(plain[o], model) for o in shared]
    pinball = [
        (before, after)
        for o in shared
        for before, after in [(_pinball(plain[o], model), _pinball(fitted[o], model))]
        if before is not None and after is not None
    ]
    return {
        "n_paired": len(shared),
        "origins": [shared[0], shared[-1]] if shared else None,
        "mae_median_difference": statistics.median(mae) if mae else None,
        "pinball_median_difference": statistics.median(
            [after - before for before, after in pinball]
        )
        if pinball
        else None,
        "n_paired_pinball": len(pinball),
        "calibration": {
            name: runs[name]["metrics"]["forecasters"].get(model, {}).get("calibration")
            for name in (UNCONDITIONED, CONDITIONED)
        },
    }


def _pinball(record: Mapping[str, Any], model: str) -> float | None:
    """Mean pinball loss over this window's steps and the nine levels, or None."""
    band = record["forecasts"][model]["quantiles"]
    if band is None:
        return None
    losses = [
        max(
            level * (actual - step[position]), (level - 1.0) * (actual - step[position])
        )
        for actual, step in zip(record["actual"], band, strict=True)
        for position, level in enumerate(QUANTILE_LEVELS)
    ]
    return statistics.fmean(losses) if losses else None


def _warnings(runs: Mapping[str, Mapping[str, Any]], model: str) -> list[str]:
    scores = [
        runs[name]["metrics"]["forecasters"].get(model, {}).get("mae_mean")
        for name in (UNCONDITIONED, CONDITIONED)
    ]
    return [NO_FUTURE_READER] if scores[0] == scores[1] else []


def report(found: Mapping[str, Any]) -> str:
    """The two runs, the paired differences, the warnings and the mandatory sentence."""
    paired = found["paired"]
    lines = [
        f"forecast candidate  series {found['series_id']}  target {found['target']}"
        f" ({found['unit']})  horizon {found['horizon']}  model {found['model']}",
        f"scored on {_scored_line(found)}",
        f"retained {found['retained_rows']} rows  excluded_for_block"
        f" {found['excluded_for_block']['count']}"
        f" {found['excluded_for_block']['by_column']}",
        f"origins {paired['origins']}  paired {paired['n_paired']}"
        f"  limit {found['origin_limit']['reason']}",
        "",
        render_table(
            [_row(name, found) for name in (UNCONDITIONED, CONDITIONED)], _TABLE
        ),
        "",
        render_table(
            [
                {
                    "statistic": "MAE",
                    "unconditioned": _round(_mae(found, UNCONDITIONED)),
                    "conditioned": _round(_mae(found, CONDITIONED)),
                    "paired_median_difference": _round(paired["mae_median_difference"]),
                },
                {
                    "statistic": "pinball loss",
                    "unconditioned": _round(_loss(found, UNCONDITIONED)),
                    "conditioned": _round(_loss(found, CONDITIONED)),
                    "paired_median_difference": _round(
                        paired["pinball_median_difference"]
                    ),
                },
            ],
            _PAIRED,
        ),
        "",
        "warnings:",
        *[f"  {line}" for line in found["warnings"] or ["none"]],
        "",
        found["sentence"],
        "",
        *licences(found["runs"].values()),
    ]
    return refuse_words("\n".join(lines))


def _scored_line(found: Mapping[str, Any]) -> str:
    """`step 4 of 4`, or `every step of 1`. On the face of the report because a number
    scored over part of a horizon and one over all of it are different numbers."""
    at, of = found["scored_steps"], found["horizon"]
    if at is None:
        return f"every step of {of}"
    return f"step{'' if len(at) == 1 else 's'} {', '.join(map(str, at))} of {of}"


def licences(runs: Any) -> list[str]:
    """The weights licence of every checkpoint in these runs, or the line saying none.

    Read off the forecaster instances a run declared (`backtest._licences`): naming a
    licence here would mean importing forecast/timesfm.py, which imports torch
    (ADR-009). A run with no checkpoint prints that rather than printing nothing.
    """
    found = [line for run in runs for line in backtester._licences(run)]
    return list(dict.fromkeys(found)) or [NO_LICENCE]


def _row(name: str, found: Mapping[str, Any]) -> dict[str, Any]:
    one = found["runs"][name]
    scored = one["metrics"]["forecasters"].get(found["model"], {})
    calibration = scored.get("calibration") or {}
    return {
        "run": name,
        "n_windows": len(one["windows"]),
        "mae_mean": _round(scored.get("mae_mean")),
        "mae_median": _round(scored.get("mae_median")),
        "pinball": _round(_loss(found, name)),
        "cal_max_dev": _round(calibration.get("max_deviation")),
        "coverage80": _round(calibration.get("coverage80")),
    }


def _mae(found: Mapping[str, Any], name: str) -> float | None:
    scored = found["runs"][name]["metrics"]["forecasters"].get(found["model"], {})
    return None if scored.get("mae_mean") is None else float(scored["mae_mean"])


def _loss(found: Mapping[str, Any], name: str) -> float | None:
    values = [
        _pinball(record, found["model"]) for record in found["runs"][name]["windows"]
    ]
    known = [value for value in values if value is not None]
    return statistics.fmean(known) if known else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def advise_row(
    series: Series, found: Mapping[str, Any], *, a_block: Sequence[str] = ABLATION_A
) -> list[float | None]:
    """The A block as one past-future covariate row, in `a_block` order.

    The row for origin N, the change that has not landed. `series` is read to refuse a
    block it does not carry: a run that quietly dropped a column would be the
    unconditioned run wearing the conditioned run's name.
    """
    _block(series, a_block)
    absent = [name for name in a_block if name not in found]
    if absent:
        raise Refused(f"this candidate's features do not carry {absent}")
    return [None if found[name] is None else float(found[name]) for name in a_block]


# -- the forecast of the change that has not landed ------------------------------------

_BLOCK_TABLE = ("column", "value")
_ONE_STEP_TABLE = ("run", "forecaster", "point", "q10", "q50", "q90", "wall_ms")
UNKNOWN = "unknown"


def one_step(
    series: Series,
    target: str,
    forecasters: Mapping[str, Forecaster],
    found: Mapping[str, Any],
    *,
    a_block: Sequence[str] = ABLATION_A,
    past_only: Sequence[str] = ABLATION_C,
    c_min: int | None = None,
) -> dict[str, Any]:
    """The two forecasts of row N, the change this candidate would become.

    Not a backtest and never scored: row N does not exist, so there is no actual and no
    MAE. What it is for is the pair. The unconditioned forecast is what the history
    alone says about the next change; the conditioned one is the same context with this
    candidate's A block hung on the window at [ctx_start, N].

    Refused rather than scored when any A-block value is unknown, which is `_known`'s
    rule at an origin inside the history: a candidate whose own features cannot be read
    is not a candidate this protocol has anything to say about.
    """
    check_target(target)
    at = horizon(target)
    spec = backtester.registered(series, target, at)
    floor = spec.c_min if c_min is None else c_min
    frame = frames.retained(series, target)
    origin = len(frame.rows)
    columns = [target, *_covariates(frame, target, past_only)]
    window = _window_at(frame, columns, origin, floor, at)
    block = advise_row(frame, found, a_block=a_block)
    if any(value is None for value in block):
        unknown = [
            name for name, value in zip(a_block, block, strict=True) if value is None
        ]
        raise Refused(
            f"the candidate's A block holds an unknown in {unknown}, so the conditioned"
            " forecast has nothing to condition on. Nothing here imputes one."
        )
    return {
        "series_id": frame.series_id,
        "target": target,
        "unit": spec.unit,
        "horizon": at,
        # Which step of the forecast the report prints: the last step this target is
        # scored on, which is the one that is about the candidate itself.
        "reported_step": (steps(target) or (1,))[-1],
        "origin": origin,
        "ctx_start": window.ctx_start,
        "n_ctx": window.n_ctx,
        "covariates": columns[1:],
        "a_block": {name: found.get(name) for name in a_block},
        "provenance": {name: found.get(name) for name in PROVENANCE},
        "runs": {
            UNCONDITIONED: _forecasts(window, forecasters),
            CONDITIONED: _forecasts(
                replace(window, future=_future(frame, window, a_block, block)),
                forecasters,
            ),
        },
        "sentence": CANDIDATE_SENTENCE,
        "padding_mode": "edge",
        # The same shape `backtest.persist` and `licences` read off a scored run, so a
        # forecast of row N prints the weights licence the same way a backtest does.
        "forecasters": [
            backtester._declared(name, obj) for name, obj in forecasters.items()
        ],
    }


def _covariates(series: Series, target: str, past_only: Sequence[str]) -> list[str]:
    """The named variant's columns, minus the target, minus the unforecastable.

    The same rule as `backtest._chosen`, spelled again because a forecast at origin N
    is not a backtest window. Both read `backtest.FORECASTABLE`, so what is duplicated
    is the loop and not the decision.
    """
    coverage = {column.name: column.coverage for column in series.columns}
    if coverage.get(target) not in FORECASTABLE:
        raise Refused(
            f"target {target} has coverage {coverage.get(target)}:"
            f" a target must be one of {list(FORECASTABLE)}"
        )
    selected = []
    for name in past_only:
        if name == target:
            continue
        if name not in coverage:
            raise Refused(f"series {series.series_id} has no column {name}")
        if coverage[name] in FORECASTABLE:
            selected.append(name)
    return selected


def _window_at(
    series: Series, columns: Sequence[str], origin: int, c_min: int, at: int
) -> Window:
    """The context [ctx_start, N) as a Window, refusing a hole and a short regime.

    `backtest._at_origin`'s arithmetic at an origin one past the last row: the regime
    starts at the last changepoint at or before it, and one shorter than c_min is
    refused rather than padded from the regime before it.
    """
    names = [column.name for column in series.columns]
    indices = [names.index(name) for name in columns]
    regime = backtester.regime_start(series.changepoints, origin)
    ctx_start = max(regime, origin - MAX_CONTEXT)
    if origin - ctx_start < c_min:
        raise Refused(
            f"origin {origin}: {origin - ctx_start} rows since the last changepoint is"
            f" below c_min {c_min}, so this series cannot forecast its next change yet"
        )
    return Window(
        columns=list(columns),
        rows=[
            _row_at(series, columns, indices, index)
            for index in range(ctx_start, origin)
        ],
        origin=origin,
        ctx_start=ctx_start,
        n_ctx=origin - ctx_start,
        horizon=at,
        target=columns[0],
    )


def _row_at(
    series: Series, columns: Sequence[str], indices: Sequence[int], index: int
) -> list[float]:
    """One context row as floats, refusing on an unknown and naming the columns."""
    cells = [series.rows[index][position] for position in indices]
    holes = [name for name, cell in zip(columns, cells, strict=True) if cell is None]
    if holes:
        raise Refused(
            f"row {index} of the context holds an unknown in {holes}."
            " Nothing here imputes one."
        )
    return [float(cell) for cell in cells if cell is not None]


def _future(
    series: Series,
    window: Window,
    a_block: Sequence[str],
    row: Sequence[float | None],
) -> dict[str, list[float]]:
    """The A block over [ctx_start, N]: the context plus the candidate's own row.

    n_ctx + H long, and the candidate's row is the whole of what the conditioned run
    knows and the unconditioned run does not. Past step 1 it repeats, which is the same
    edge padding `_attach` records inside the history.
    """
    index = {column.name: position for position, column in enumerate(series.columns)}
    block: dict[str, list[float]] = {}
    for name, value in zip(a_block, row, strict=True):
        span = range(window.ctx_start, window.origin)
        past = [series.rows[at][index[name]] for at in span]
        if any(cell is None for cell in past) or value is None:
            raise Refused(
                f"candidate column {name} holds an unknown inside [ctx_start, N]."
                " Nothing here imputes one."
            )
        block[name] = frames.edge_padded(
            [float(cell) for cell in past if cell is not None] + [value],
            window.horizon,
        )
    return block


def _forecasts(
    window: Window, forecasters: Mapping[str, Forecaster]
) -> dict[str, dict[str, Any]]:
    """Every forecaster on the identical window, each timed on its own call."""
    out: dict[str, dict[str, Any]] = {}
    for name, forecaster in forecasters.items():
        started = time.perf_counter()
        result = forecaster.forecast(window, window.horizon)
        wall_ms = (time.perf_counter() - started) * 1000.0
        out[name] = {
            "point": list(result.point[0]),
            "quantiles": None if result.quantiles is None else result.quantiles[0],
            "wall_ms": round(wall_ms, 3),
            "model_ms": getattr(forecaster, "last_wall_ms", None),
            "covariates": list(result.covariates),
        }
    return out


def one_step_report(found: Mapping[str, Any]) -> str:
    """The A block, both forecasts of row N, and the mandatory sentence."""
    lines = [
        f"candidate  {found['provenance']['base_sha'][:12]}"
        f"...{found['provenance']['head_sha'][:12]}"
        f"  merge_base {found['provenance']['merge_base'][:12]}",
        f"forecast of row {found['origin']} (the next change on this clock)"
        f"  target {found['target']} ({found['unit']})  horizon {found['horizon']}"
        f"  step {found['reported_step']} shown",
        f"context [{found['ctx_start']}, {found['origin']})"
        f" = {found['n_ctx']} rows  past-only covariates {len(found['covariates'])}"
        f"  padding_mode {found['padding_mode']}",
        "",
        render_table(
            [
                {"column": name, "value": UNKNOWN if value is None else value}
                for name, value in found["a_block"].items()
            ],
            _BLOCK_TABLE,
        ),
        "",
        render_table(_one_step_rows(found), _ONE_STEP_TABLE),
        "",
        found["sentence"],
        "",
        *licences([found]),
    ]
    return refuse_words("\n".join(lines))


def _one_step_rows(found: Mapping[str, Any]) -> list[dict[str, Any]]:
    at = int(found["reported_step"]) - 1
    return [
        _one_step_row(run, name, one, at)
        for run in (UNCONDITIONED, CONDITIONED)
        for name, one in found["runs"][run].items()
    ]


def _one_step_row(
    run: str, name: str, one: Mapping[str, Any], at: int
) -> dict[str, Any]:
    """One forecaster's forecast of row N: the point, three quantiles, the wall time.

    `at` is the step this target's forecast is about: step 4 for the lagged label and
    step 1 elsewhere. Step 1 of a lagged label is a change three before the candidate.
    """
    band = one["quantiles"][at] if one["quantiles"] else [None] * len(QUANTILE_LEVELS)
    return {
        "run": run,
        "forecaster": name,
        "point": _round(one["point"][at]),
        "q10": _round(band[0]),
        "q50": _round(band[POINT_AT]),
        "q90": _round(band[-1]),
        "wall_ms": one["wall_ms"],
    }


# Read off the tuple, so a registry that adds a level does not move what this prints.
POINT_AT = QUANTILE_LEVELS.index(0.5)
