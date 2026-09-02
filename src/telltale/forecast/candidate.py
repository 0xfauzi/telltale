"""One-step candidate conditioning. Design 6.12 (H8), spec 15.8.

The situation is a merge decision. A change is sitting there, and what is known about it
is what anybody can read off the diff: how many files, how many lines, how many
subsystems, whether it touched tests, whether it moved a lockfile. That is block A. What
is not known is anything that happens after the merge, so those are the targets:
`merge_verification_ms`, `merge_verification_failed` and `rework_within_3`.

The protocol is two runs over the SAME origins.

  The unconditioned run knows the history and nothing about the candidate. Context
  [ctx_start, o), H = 1, past-only covariates.

  The conditioned run is the same context and the same origin, plus the candidate's own
  A block as a PAST-FUTURE covariate of length n_ctx + 1: the block's values over rows
  [ctx_start, o], which is one row longer than the context because row o's features are
  known at the moment the decision is taken. Edge padding is what makes that length
  legal to the model, which extends a horizon covariate beyond step H by replicating its
  last column, and the padding mode is recorded in every run.

Two refusals and one sentence hold the meaning of the difference in place.

  `attempts_to_land` is refused as a target. It is known at merge time, so conditioning
  a forecast of it on the candidate's features is scoring a lookup.

  `rework_within_3` is a delayed label: row o is only labelled once three more changes
  have landed, so its origins stop at `o <= N - 3`. An origin past that has no actual
  and would be scored against a value the history has not produced yet.

  Every output carries CANDIDATE_SENTENCE. The difference between two forecasts of one
  observed future is a statement about the forecasts, and the moment it is read as a
  statement about merging is the moment this whole protocol has misled somebody.
"""

from __future__ import annotations

import statistics
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from telltale.forecast import (
    ABLATION_A,
    ABLATION_C,
    CANDIDATE_FORBIDDEN,
    CANDIDATE_SENTENCE,
    QUANTILE_LEVELS,
    REWORK_TAIL,
    REWORK_TARGET,
    Window,
    refuse_words,
)
from telltale.forecast import backtest as backtester
from telltale.forecast.backtest import Refused, window_mae
from telltale.report import render_table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.forecast import Forecaster
    from telltale.model import Series
    from telltale.store import Store

HORIZON = 1
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
) -> dict[str, Any]:
    """The two runs, paired by origin, with the mandatory sentence on both.

    `store` may be None, which runs the protocol without writing it. Everything else is
    the same either way: the refusals fire before any forecaster is called, and the
    sentence is in the assumptions of both runs whether or not they reach the disk.
    """
    if target == CANDIDATE_FORBIDDEN:
        raise Refused(FORBIDDEN)
    spec = backtester.registered(series, target, HORIZON)
    limit = _limit(series, target, origin_range)
    plain = _run(series, target, forecasters, past_only, c_min, None)
    fitted = _run(
        series,
        target,
        forecasters,
        past_only,
        c_min,
        _attach(series, a_block),
    )
    runs = {
        UNCONDITIONED: _within(plain, limit),
        CONDITIONED: _within(fitted, limit),
    }
    for name, one in runs.items():
        one["variant"] = f"{spec.variant}_candidate_{name}"
        one["assumptions"] = [*one["assumptions"], CANDIDATE_SENTENCE]
        one["candidate"] = {
            "a_block": list(a_block),
            "past_only": list(past_only),
            "future_length": "n_ctx + H",
            "padding_mode": "edge",
            "origin_limit": limit,
            "sentence": CANDIDATE_SENTENCE,
        }
    found = {
        "series_id": series.series_id,
        "target": target,
        "unit": spec.unit,
        "model": model,
        "horizon": HORIZON,
        "origin_limit": limit,
        "runs": runs,
        "paired": _paired(runs, model),
        "sentence": CANDIDATE_SENTENCE,
        "warnings": _warnings(runs, model),
    }
    if store is not None:
        found["forecast_run_ids"] = [
            backtester.persist(store, one) for one in runs.values()
        ]
    return found


def _limit(
    series: Series, target: str, origin_range: tuple[int, int] | None
) -> dict[str, Any]:
    """The origins this target may be scored at, and the reason for the ceiling."""
    tail = REWORK_TAIL if target == REWORK_TARGET else 0
    stop = len(series.rows) - tail
    if origin_range is not None:
        stop = min(stop, origin_range[1])
    return {
        "start": None if origin_range is None else origin_range[0],
        "stop": stop,
        "reason": f"{REWORK_TARGET} is a delayed label: design 6.12 stops its origins"
        f" at o <= N - {REWORK_TAIL}"
        if tail
        else "no delayed-label ceiling on this target",
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
        HORIZON,
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
    index = {column.name: position for position, column in enumerate(series.columns)}
    missing = [name for name in a_block if name not in index]
    if missing:
        raise Refused(f"series {series.series_id} has no column {missing}")

    def prepare(window: Window) -> Window:
        span = range(window.ctx_start, window.origin + window.horizon)
        block = {
            name: _known(series, index[name], span, name, window.origin)
            for name in a_block
        }
        return replace(window, future=block)

    return prepare


def _known(
    series: Series, position: int, span: range, name: str, origin: int
) -> list[float]:
    """One candidate column over [ctx_start, o + H), refusing on an unknown."""
    values = [series.rows[row][position] for row in span]
    if any(value is None for value in values):
        raise Refused(
            f"origin {origin}: candidate column {name} holds an unknown inside"
            " [ctx_start, o + H). Nothing here imputes one."
        )
    return [float(value) for value in values if value is not None]


def _within(one: dict[str, Any], limit: Mapping[str, Any]) -> dict[str, Any]:
    """The run cut down to the origins the limit allows, rescored on those."""
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
        "metrics": backtester.metrics(kept, one["tau"]),
    }


def _paired(runs: Mapping[str, Mapping[str, Any]], model: str) -> dict[str, Any]:
    """Paired median difference in MAE and pinball loss, conditioned minus plain."""
    plain = {int(r["origin"]): r for r in runs[UNCONDITIONED]["windows"]}
    fitted = {int(r["origin"]): r for r in runs[CONDITIONED]["windows"]}
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
    ]
    return refuse_words("\n".join(lines))


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
    value = scored.get("mae_mean")
    return None if value is None else float(value)


def _loss(found: Mapping[str, Any], name: str) -> float | None:
    values = [
        _pinball(record, found["model"]) for record in found["runs"][name]["windows"]
    ]
    known = [value for value in values if value is not None]
    return statistics.fmean(known) if known else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
