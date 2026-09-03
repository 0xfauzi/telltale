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
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from telltale import repo, series_paths
from telltale.forecast import (
    ABLATION_A,
    ABLATION_C,
    CANDIDATE_FORBIDDEN,
    CANDIDATE_SENTENCE,
    CANDIDATE_TARGETS,
    MAX_CONTEXT,
    QUANTILE_LEVELS,
    REWORK_TAIL,
    REWORK_TARGET,
    Window,
    refuse_words,
)
from telltale.forecast import backtest as backtester
from telltale.forecast.backtest import FORECASTABLE, Refused, window_mae
from telltale.report import render_table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

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
# Printed by every candidate report whether or not a checkpoint was involved, so that
# "this run used no licensed weights" and "somebody left the licence line out" are
# different lines on the page rather than the same absence.
NO_LICENCE = "weights: no licensed model in this run"

# How a past-future covariate is spelled where a past-only one could stand beside it:
# in the stored run's covariate list, and in what the TimesFM adapter reports it saw.
# The two are the same claim and there is one spelling of it, because a reader
# comparing the conditioned run against the unconditioned one is reading these lists.
FUTURE_PREFIX = "future:"


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
) -> dict[str, Any]:
    """The two runs, paired by origin, with the mandatory sentence on both.

    `store` may be None, which runs the protocol without writing it. Everything else is
    the same either way: the refusals fire before any forecaster is called, and the
    sentence is in the assumptions of both runs whether or not they reach the disk.
    """
    check_target(target)
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
        "",
        *licences(found["runs"].values()),
    ]
    return refuse_words("\n".join(lines))


def licences(runs: Any) -> list[str]:
    """The weights licence of every checkpoint in these runs, or the line saying none.

    `backtest._licences` reads the licence off the forecaster instances a run declared,
    which is where it has to be read: naming it here would mean importing
    forecast/timesfm.py, and that module imports torch (ADR-009). Design 6.12 says
    every forecast command prints the licence, so a run with no checkpoint prints that
    it had none rather than printing nothing at all.
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


# -- what is known about a candidate before it is merged -------------------------------

# The three provenance keys `features` returns beside the A block. Not part of the block
# and never covariates: they are what was diffed, so a stored advisory can be re-derived
# rather than trusted.
PROVENANCE = ("base_sha", "head_sha", "merge_base")

# `git diff base...head`. THREE dots, and this is the whole reason the constant exists
# rather than an f-string at the call site: `base..head` is main's drift plus the
# candidate's changes, and a candidate scored on somebody else's commits is not scored
# on anything. Three dots is `merge_base(base, head)..head`, which is the change the
# candidate would bring.
_SYMMETRIC = "..."
# -M so that a rename is one changed file here and one changed file in repo_link's
# diff-tree of the commit it becomes. Without it the same change measures 2 files
# before the merge and 1 after, and the A block would not be the row it predicts.
_DIFF_ARGS = (*repo.DIFF_SAFE, "-M")


class NotACandidate(Exception):
    """A (base, head) pair whose diff is not one candidate's own changes."""


def features(cwd: str | Path, base: str, head: str) -> dict[str, Any]:
    """The A block of a candidate, read off a git diff. Design 6.12's H8, block A.

    The six keys of ABLATION_A, each an int or None, plus the three of PROVENANCE, each
    a sha. Nothing else: this is what anybody deciding a merge can read off the diff,
    which is what makes it legitimate as a past-future covariate at the origin.

    Paths and numstat only. The rejected alternative was to read the file CONTENTS and
    say something about what the change does; that is a different claim class, it puts
    somebody's source into a recorder that promises never to persist diff text (design
    6.3), and none of the six columns needs it.

    A binary file leaves lines_added and lines_removed None and never 0: git prints
    "-\\t-" there because lines are not the unit, and `repo.totals` is where that rule
    already lives. files_changed still counts it.
    """
    base_sha = _rev(cwd, base)
    head_sha = _rev(cwd, head)
    merge_base = _merge_base(cwd, base_sha, head_sha)
    _not_a_merge(cwd, head, head_sha)
    span = f"{base_sha}{_SYMMETRIC}{head_sha}"
    changes = repo.git_numstat(cwd, "diff", *_DIFF_ARGS, span)
    if changes is None:
        raise NotACandidate(
            f"git diff {base}{_SYMMETRIC}{head} refused in {cwd}: there is no diff to"
            " read, and an empty A block is not the same answer as an empty diff"
        )
    added, removed = repo.totals(changes)
    subsystems, tests, dependency = series_paths.columns(
        {"per_file": repo.per_file(changes)}
    )
    return {
        "files_changed": len(changes),
        "lines_added": added,
        "lines_removed": removed,
        "subsystems_touched": _int(subsystems),
        "test_files_changed": _int(tests),
        "dependency_delta": _int(dependency),
        "base_sha": base_sha,
        "head_sha": head_sha,
        "merge_base": merge_base,
    }


def _rev(cwd: str | Path, ref: str) -> str:
    """One ref as a full sha, or the refusal naming the ref git could not resolve."""
    found = repo.git_line(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not found:
        raise NotACandidate(f"{ref}: no such commit in {cwd}")
    return found


def _merge_base(cwd: str | Path, base_sha: str, head_sha: str) -> str:
    """The commit `base...head` diffs from, or the refusal that there is not one.

    Two histories with no common ancestor have no candidate between them: `git diff`
    would still answer, by diffing one whole tree against the other, and every one of
    the six numbers would then be the size of the repository rather than of a change.
    """
    found = repo.git_line(cwd, "merge-base", base_sha, head_sha)
    if not found:
        raise NotACandidate(
            f"{base_sha[:12]} and {head_sha[:12]} share no ancestor, so there is no"
            " base this candidate is a change against"
        )
    return found


def _not_a_merge(cwd: str | Path, ref: str, head_sha: str) -> None:
    """Refuse a head that is a merge. Its own contribution is not one diff.

    repo_link._commit_stats already refuses a merge for this reason on the change clock,
    and the A block has to be the same measurement or a candidate's predicted row and
    its landed row are two different things. The parents are named so the caller can
    pick one and ask again.
    """
    listed = repo.git_line(cwd, "show", "-s", "--format=%P", head_sha)
    parents = (listed or "").split()
    if len(parents) > 1:
        raise NotACandidate(
            f"{ref} ({head_sha[:12]}) is a merge of {len(parents)} parents"
            f" ({', '.join(sha[:12] for sha in parents)}): its own changes are not one"
            " diff, because every per-file number depends on which parent is picked"
        )


def _int(value: float | None) -> int | None:
    """A path column as a whole count. None stays None and is never rounded."""
    return None if value is None else int(value)


def advise_row(
    series: Series, found: Mapping[str, Any], *, a_block: Sequence[str] = ABLATION_A
) -> list[float | None]:
    """The A block as one past-future covariate row, in `a_block` order.

    The row for origin N, which is the change that has not landed. `series` is read to
    refuse a block the series does not carry: a covariate the history has no column for
    cannot be conditioned on, and a run that quietly dropped it would be the
    unconditioned run wearing the conditioned run's name.
    """
    names = {column.name for column in series.columns}
    missing = [name for name in a_block if name not in names]
    if missing:
        raise Refused(f"series {series.series_id} has no column {missing}")
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
    candidate's A block hung on the window at [ctx_start, N], and the difference is what
    the mandatory sentence is about.

    Refused rather than scored when any A-block value is unknown, which is `_known`'s
    rule at an origin inside the history: a candidate whose own features cannot be read
    is not a candidate this protocol has anything to say about.
    """
    check_target(target)
    spec = backtester.registered(series, target, HORIZON)
    floor = spec.c_min if c_min is None else c_min
    origin = len(series.rows)
    columns = [target, *_covariates(series, target, past_only)]
    window = _window_at(series, columns, origin, floor)
    block = advise_row(series, found, a_block=a_block)
    if any(value is None for value in block):
        unknown = [
            name for name, value in zip(a_block, block, strict=True) if value is None
        ]
        raise Refused(
            f"the candidate's A block holds an unknown in {unknown}, so the conditioned"
            " forecast has nothing to condition on. Nothing here imputes one."
        )
    return {
        "series_id": series.series_id,
        "target": target,
        "unit": spec.unit,
        "horizon": HORIZON,
        "origin": origin,
        "ctx_start": window.ctx_start,
        "n_ctx": window.n_ctx,
        "covariates": columns[1:],
        "a_block": {name: found.get(name) for name in a_block},
        "provenance": {name: found.get(name) for name in PROVENANCE},
        "runs": {
            UNCONDITIONED: _forecasts(window, forecasters),
            CONDITIONED: _forecasts(
                replace(window, future=_future(series, window, a_block, block)),
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

    The same rule as `backtest._chosen`, spelled again because backtest.py is not this
    task's to change and a forecast at origin N is not a backtest window. Both read
    `backtest.FORECASTABLE`, so the two cannot disagree about which coverage words are
    allowed; what is duplicated is the loop, not the decision.
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
    series: Series, columns: Sequence[str], origin: int, c_min: int
) -> Window:
    """The context [ctx_start, N) as a Window, refusing a hole and a short regime.

    Same arithmetic as `backtest._at_origin` at an origin one past the last row: the
    regime starts at the last changepoint at or before it, and a regime shorter than
    c_min is refused rather than padded from the regime before it.
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
        horizon=HORIZON,
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
    """The A block over [ctx_start, N], which is the context plus the candidate's row.

    n_ctx + H long, and the last column is the candidate itself: that one column is the
    whole of what the conditioned run knows and the unconditioned run does not.
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
        block[name] = [float(cell) for cell in past if cell is not None] + [value]
    return block


def _forecasts(
    window: Window, forecasters: Mapping[str, Forecaster]
) -> dict[str, dict[str, Any]]:
    """Every forecaster on the identical window, each timed on its own call."""
    out: dict[str, dict[str, Any]] = {}
    for name, forecaster in forecasters.items():
        started = time.perf_counter()
        result = forecaster.forecast(window, HORIZON)
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
        f"  target {found['target']} ({found['unit']})  horizon {found['horizon']}",
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
    return [
        _one_step_row(run, name, one)
        for run in (UNCONDITIONED, CONDITIONED)
        for name, one in found["runs"][run].items()
    ]


def _one_step_row(run: str, name: str, one: Mapping[str, Any]) -> dict[str, Any]:
    """One forecaster's forecast of row N: the point, three quantiles, the wall time."""
    band = one["quantiles"][0] if one["quantiles"] else [None] * len(QUANTILE_LEVELS)
    return {
        "run": run,
        "forecaster": name,
        "point": _round(one["point"][0]),
        "q10": _round(band[0]),
        "q50": _round(band[POINT_AT]),
        "q90": _round(band[-1]),
        "wall_ms": one["wall_ms"],
    }


# QUANTILE_LEVELS[4] is 0.5. Spelled from the tuple rather than as a literal 4, so a
# registry that ever adds a level does not silently move which number this prints.
POINT_AT = QUANTILE_LEVELS.index(0.5)
