"""The eight-line readiness checklist of design 6.12, run before any forecast is.

A forecast that should never have been attempted does not announce itself. It returns
numbers of the usual size, in the usual units, with the usual quantile band around
them, and the only thing wrong with it is that nothing in the series supported it.
This module is the preflight that says so first: eight questions, each answered with
the number it measured and the number it needed, and a refusal is cheaper than a
result nobody can trust.

Three rules make the answers mean something.

  The window count is the backtester's own. Check 2 calls `backtest.plan`, which is
  the function `backtest.run` calls, so the origins the checklist counts are the
  origins that would run. Design 6.12's formula `floor((N - H - c_min) / s) + 1` is
  printed beside it and is NOT what the check compares: the formula knows nothing
  about changepoints, and a regime boundary can take a third of the origins away.

  Coverage words are read, never recomputed. Check 1 takes each column's coverage from
  the Series, where the activities reducer measured it. A checklist that re-derived
  them would be checking its own arithmetic.

  A gap is judged by where it sits. Check 3 counts the unknown cells inside the
  windows the plan would form, so a None that no window reads is not a failure (policy
  exclude excludes it, which is what exclude means) and a None inside one is. No
  policy imputes, here or anywhere.

  Every line is measured over the frame the run will score. A target with holes is
  forecast over the rows where it is known (forecast/frame.py), so the checklist takes
  `frame.retained` first and counts windows, contexts, regimes and tau on that; the
  header line prints the rows the series has, the rows this target retained and the
  rows it excluded, because three numbers that differ are three facts.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from telltale import series as compiler
from telltale.forecast import HORIZONS, MAX_CONTEXT, TARGETS, THRESHOLD_RULE
from telltale.forecast import backtest as backtester
from telltale.forecast import frame as frames
from telltale.forecast.baselines import DRIFT_MIN
from telltale.report import render_table
from telltale.series_lineage import uncaptured_keys

if TYPE_CHECKING:
    from collections.abc import Sequence

    from telltale.model import Series
    from telltale.store import Store

# The eight lines of design 6.12, in the order it lists them. The order is part of the
# report: a reader stops at the first FAIL and the earlier lines are the cheaper facts.
CHECKS = (
    "coverage",
    "windows",
    "missingness",
    "baselines",
    "changepoints",
    "placebo",
    "variation",
    "threshold",
)

# The scale that makes the median absolute deviation comparable with a standard
# deviation on normal data. Check 7 only asks whether it is above zero, so the scaling
# changes no verdict; it is here so the number printed is the one experiments.py prints.
MAD_SCALE = 1.4826

# Design 6.12: the chronology placebo cuts the context into blocks of B = max(2, H)
# whole rows, so a context shorter than two blocks cannot be shuffled at all.
PLACEBO_BLOCK_MIN = 2

# What the session summary is computed at. One horizon, because the summary field says
# whether a capture can be forecast at all, and H = 1 is the shortest claim available.
SUMMARY_HORIZON = HORIZONS[0]

_TABLE = ("check", "result", "measured", "needed", "detail")

_NOT_BUILT = (
    "the attempt and change clocks are activity presence as W1-T2 measured it,"
    " not a checklist: those clocks are W3-T1's and no series can be built for them yet"
)


@dataclass(frozen=True)
class Check:
    """One line of the checklist: what was measured, what was needed, and where."""

    name: str
    passed: bool
    measured: float | None
    needed: float | None
    detail: str

    def stated(self) -> str:
        """`name: measured M, needed N`, the form the session summary stores."""
        return (
            f"{self.name}: measured {_number(self.measured)},"
            f" needed {_number(self.needed)}"
        )


def check(series: Series, target: str, horizon: int = SUMMARY_HORIZON) -> list[Check]:
    """The eight checks of design 6.12 for one target on one series, in order.

    Refuses through `backtest.registered` for a target the registry does not carry, a
    horizon it does not allow and a column the series does not have, and through
    `backtest._variant` for a target whose own coverage forbids forecasting it. Those
    are the backtester's refusals, raised by the same code, so a checklist can never
    report on a run that would have been refused.

    Every line below is measured over `frame.retained(series, target)`, which is the
    frame `backtest.run` will score: the rows where the target is known. A checklist
    over the parent series would count windows nobody runs and would report holes in a
    column the run never reads.
    """
    spec = backtester.registered(series, target, horizon)
    frame = frames.retained(series, target)
    plan = backtester.plan(frame, target, horizon)
    windows = _candidates(frame, plan)
    values = _column(frame, target)
    return [
        _coverage(frame, target),
        _windows(frame, plan, horizon, spec.c_min, spec.k_min),
        _missingness(frame, plan, windows, horizon),
        _context(CHECKS[3], plan, DRIFT_MIN, "local_drift needs 9 context rows"),
        _changepoints(frame, horizon, spec.c_min),
        _context(
            CHECKS[5],
            plan,
            PLACEBO_BLOCK_MIN * max(PLACEBO_BLOCK_MIN, horizon),
            f"the placebo shuffles blocks of B = max(2, {horizon}) whole rows",
        ),
        _variation(values, target, len(frame.rows)),
        _threshold(frame, target, spec.c_min, spec.threshold_rule, values),
    ]


def ready(checks: Sequence[Check]) -> bool:
    """True when every line passed. A forecast is attempted on nothing less."""
    return all(item.passed for item in checks)


def report(series: Series, target: str, horizon: int, checks: Sequence[Check]) -> str:
    """The checklist as one line per check, with the verdict underneath."""
    rows = [
        {
            "check": item.name,
            "result": "pass" if item.passed else "FAIL",
            "measured": _number(item.measured),
            "needed": _number(item.needed),
            "detail": item.detail,
        }
        for item in checks
    ]
    failed = [item.name for item in checks if not item.passed]
    frame = frames.retained(series, target)
    return "\n".join([
        f"forecast readiness  series {series.series_id}  target {target}"
        f"  horizon {horizon}  clock {series.clock}",
        f"policy {series.missingness_policy}  rows {len(series.rows)}"
        f"{_uncaptured(series)}"
        f"  retained {len(frame.rows)}"
        f"  excluded {frames.excluded(frame)['count']}"
        f"  changepoints {_listed(frame.changepoints)}",
        "",
        render_table(rows, _TABLE),
        "",
        "ready" if ready(checks) else f"NOT ready: {', '.join(failed)}",
    ])  # fmt: skip


# -- the eight checks -----------------------------------------------------------------


def _coverage(series: Series, target: str) -> Check:
    """(1) The target forecastable, and every column it cannot use never observable.

    W7-T3's amendment, and the rule it replaced is the reason for it. Until now this
    line counted every column and refused when ANY of them was not `observed` or
    `derived`, which refused 2389 captures on the owner's store for a column their
    surface cannot carry: an imported Claude transcript has no environment fingerprint
    (`env_changed`) and, before W7-T3, no request duration, and both are `unavailable`
    by construction rather than by anything that went wrong. The backtester already
    dropped such a column by name and recorded the reason (`backtest._variant`), so
    readiness was refusing runs the backtester was willing to make.

    W8-T3 finishes the same argument. A `partial` COVARIATE was still a failure here
    while `backtest._variant` excluded it by name and ran, which is the exact shape
    W7-T3 removed for `unavailable`: readiness was refusing a column the run would
    have excluded anyway. Both words are now excluded by name, with the word printed
    beside each, and neither fails this line.

    What is left to fail is the target. Its coverage must be one of
    `backtest.TARGET_COVERAGE`, which is one word wider than a covariate's because a
    holed target is forecast over the rows where it is known: this checklist runs over
    that retained frame, so a `partial` target has no hole in the frame measured here
    and an `unavailable` one has no value anywhere.

    `measured` and `needed` keep their meanings (forecastable columns, columns), so a
    stored summary line still reads `coverage: measured 9, needed 11`.
    """
    weak = [
        (column.name, column.coverage)
        for column in series.columns
        if column.name != target and column.coverage not in backtester.FORECASTABLE
    ]
    coverage = {column.name: column.coverage for column in series.columns}
    forecastable = coverage.get(target) in backtester.TARGET_COVERAGE
    detail = f"target {target} ({coverage.get(target)}) and {len(series.columns) - 1}"
    detail = f"{detail} variant columns"
    if weak:
        named = ", ".join(f"{name} ({word})" for name, word in weak)
        detail = f"{detail}; excluded by name: {named}"
    if not forecastable:
        detail = f"{detail}; a target must be one of {list(backtester.TARGET_COVERAGE)}"
    return Check(
        name=CHECKS[0],
        passed=forecastable,
        measured=len(series.columns) - len(weak),
        needed=len(series.columns),
        detail=detail,
    )


def _windows(
    series: Series, plan: backtester.Plan, horizon: int, c_min: int, k_min: int
) -> Check:
    """(2) Enough windows AFTER the changepoint exclusions and the short regimes.

    The measured number is the plan's, never the formula's. Both are printed, because
    the gap between them is the whole content of the check: on the synthetic series
    the formula counts 168 origins at H = 1 and the plan runs 136, and the 32 origins
    it drops are the ones too close to the changepoint at 120 to have a regime behind
    them.
    """
    formula = (len(series.rows) - horizon - c_min) // horizon + 1
    counts = ", ".join(
        f"{reason} {count}" for reason, count in sorted(plan.counts().items())
    )
    return Check(
        name=CHECKS[1],
        passed=len(plan.records) >= k_min,
        measured=len(plan.records),
        needed=k_min,
        detail=f"{len(series.rows)} rows, c_min {c_min}, H {horizon}, stride {horizon}:"
        f" formula {formula}, planned {len(plan.records)}"
        f"; dropped: {counts or 'none'}",
    )


def _missingness(
    series: Series,
    plan: backtester.Plan,
    windows: Sequence[tuple[int, int]],
    horizon: int,
) -> Check:
    """(3) Zero unknown cells inside any window the plan would form.

    The cells counted are the cells `_block` in backtest.py reads: every frame column
    over the context rows, and the target alone over the actual rows. Counting the
    covariates of an actual row would fail this line for cells no window ever reads.

    The windows are the ones policy exclude either keeps or drops for a gap. An origin
    skipped as regime_too_short or excluded for a changepoint reads no rows at all, so
    a None under one of those is not a hole in anything, and exclude excludes it.
    """
    names = [column.name for column in series.columns]
    frame = [(name, names.index(name)) for name in plan.columns]
    cells: dict[tuple[int, str], int] = {}
    for origin, start in windows:
        for row in range(start, origin):
            for name, _ in frame:
                cells.setdefault((row, name), origin)
        for row in range(origin, origin + horizon):
            cells.setdefault((row, plan.columns[0]), origin)
    index = dict(frame)
    holes = sorted(
        (row, name, origin)
        for (row, name), origin in cells.items()
        if series.rows[row][index[name]] is None
    )
    detail = (
        f"{len(cells)} cells inside {len(windows)} planned windows"
        f" over {len(frame)} frame columns"
    )
    if holes:
        row, name, origin = holes[0]
        detail = (
            f"row {row} column {name} is inside the window at origin {origin}"
            f"; {len(holes)} unknown cells in {len(windows)} planned windows,"
            " and policy exclude drops the windows that read them"
        )
    return Check(
        name=CHECKS[2], passed=not holes, measured=len(holes), needed=0, detail=detail
    )


def _context(name: str, plan: backtester.Plan, needed: int, why: str) -> Check:
    """(4) and (6): the shortest context the plan hands out, against a floor.

    None and a FAIL when the plan retained no window, rather than the vacuous pass
    that "at every origin" gives over no origins: a floor nobody measured against is
    not a floor that held.
    """
    lengths = [int(record["n_ctx"]) for record in plan.records]
    return Check(
        name=name,
        passed=bool(lengths) and min(lengths) >= needed,
        measured=min(lengths) if lengths else None,
        needed=needed,
        detail=f"{why}; shortest context over {len(lengths)} retained windows"
        if lengths
        else f"{why}; no window was retained, so no context was measured",
    )


def _changepoints(series: Series, horizon: int, c_min: int) -> Check:
    """(5) At least one regime long enough to hold a context and its horizon."""
    bounds = [0, *sorted(series.changepoints), len(series.rows)]
    lengths = [end - start for start, end in pairwise(bounds) if end > start]
    return Check(
        name=CHECKS[4],
        passed=bool(lengths) and max(lengths) >= c_min + horizon,
        measured=max(lengths) if lengths else None,
        needed=c_min + horizon,
        detail=f"{len(lengths)} regime(s) from {len(series.changepoints)}"
        f" changepoint(s): longest {max(lengths) if lengths else 'no'} rows"
        " against c_min + H",
    )


def _variation(values: Sequence[float | None], target: str, rows: int) -> Check:
    """(7) The scaled MAD of the target over the rows that carry one, above zero.

    Exactly 0 fails: every baseline is then perfect, skill has no denominator, and the
    comparison the whole laboratory is built on cannot be made. S1's
    fresh_input_tokens is 2 on all seven rows, which is the real case this catches.
    """
    known = [float(value) for value in values if value is not None]
    scaled = None if not known else MAD_SCALE * _mad(known)
    return Check(
        name=CHECKS[6],
        passed=scaled is not None and scaled > 0.0,
        measured=None if scaled is None else round(scaled, 4),
        needed=0.0,
        detail=f"scaled MAD (1.4826 MAD) of {target} over {len(known)} known values"
        f" in {rows} rows, needed above 0",
    )


def _threshold(
    series: Series,
    target: str,
    c_min: int,
    rule: str,
    values: Sequence[float | None],
) -> Check:
    """(8) tau computable from the first c_min rows, or declared by the registry.

    No entry in TARGETS declares an absolute threshold today: all three carry the
    q80_first_c_min rule, so this line is about the head of the target column and the
    detail says so rather than implying a declaration that could have saved it.
    """
    head = values[:c_min]
    known = sum(1 for value in head if value is not None)
    tau = frames.threshold(series, target, c_min)
    declared = rule != THRESHOLD_RULE
    return Check(
        name=CHECKS[7],
        passed=declared or tau is not None,
        measured=known,
        needed=c_min,
        detail=f"rule {rule} declares an absolute threshold"
        if declared
        else f"tau {_number(None if tau is None else round(tau, 4))} by rule {rule}"
        f" from the first {c_min} rows; no registry entry declares an absolute value",
    )


# -- the plan's windows, and the summary field ----------------------------------------


def _candidates(series: Series, plan: backtester.Plan) -> list[tuple[int, int]]:
    """(origin, ctx_start) for every window policy exclude keeps or drops for a gap.

    The retained ones carry their own ctx_start. The dropped ones carry only the origin
    and the reason, so their context start is recomputed with the backtester's own
    `regime_start` rather than with a second copy of the rule.
    """
    found = [
        (int(record["origin"]), int(record["ctx_start"])) for record in plan.records
    ]
    found += [
        (origin, _ctx_start(series, origin))
        for origin in [
            int(drop["origin"])
            for drop in plan.dropped
            if str(drop["reason"]).startswith("missing_")
        ]
    ]
    return sorted(found)


def _ctx_start(series: Series, origin: int) -> int:
    start = backtester.regime_start(series.changepoints, origin)
    return max(start, origin - MAX_CONTEXT)


def _mad(values: Sequence[float]) -> float:
    middle = statistics.median(values)
    return statistics.median([abs(value - middle) for value in values])


def _uncaptured(series: Series) -> str:
    """` uncaptured N` when the frame holds a row no capture landed, else nothing.

    On the header line rather than in a check, because it is not one: an uncaptured row
    is not excluded and it fails nothing. It is what makes check 1 below name the seven
    process columns, and a reader who sees `not forecastable: compactions
    (unavailable)` with no idea that half the frame came out of a git history has been
    told the symptom and not the cause. The backtester states the same count in the
    assumptions of every run it stores (series_lineage.uncaptured).

    Counted over the SERIES and not over the retained frame, because it sits beside the
    row count on the same line and describes those rows. `retained` and `excluded`
    follow it and say how many of them this target is forecast over.
    """
    found = uncaptured_keys(series)
    return f"  uncaptured {len(found)}" if found else ""


def _listed(values: Sequence[int]) -> str:
    return ", ".join(str(one) for one in values) or "none"


def _column(series: Series, target: str) -> list[float | None]:
    index = [column.name for column in series.columns].index(target)
    return [row[index] for row in series.rows]


def _number(value: float | None) -> str:
    """A count prints as a count. `measured` is a float only where the check is one."""
    if value is None:
        return "-"
    return str(int(value)) if float(value).is_integer() else str(value)


def summary_field(store: Store, capture_id: str) -> dict[str, Any]:
    """`forecast_readiness` for one capture: the checklist, not activity presence.

    The request clock is compiled here under policy exclude, which is what `series
    build` produces by default, so the field describes the series a reader would get.
    A capture whose series cannot be built carries the refusal's own words: "no
    activities" and "every window is short" are different answers and a boolean cannot
    tell them apart.

    The other two clocks keep W1-T2's reading. They have no compiler yet (W3-T1), so
    activity presence is the only true statement available about them.
    """
    kinds = {str(row["activity_type"]) for row in store.activities(capture_id)}
    return {
        "request_clock": _request_clock(store, capture_id),
        "attempt_clock": "correlation" in kinds,
        "change_clock": "repo_commit" in kinds,
        "horizon": SUMMARY_HORIZON,
        "note": _NOT_BUILT,
    }


def _request_clock(store: Store, capture_id: str) -> dict[str, Any]:
    try:
        built = compiler.build(store, "request", capture_id, "exclude")
    except compiler.Refused as refused:
        return {"refused": str(refused)}
    return {
        target: _entry(built, target)
        for target, spec in sorted(TARGETS.items())
        if spec.clock == built.clock
    }


def _entry(series: Series, target: str) -> dict[str, Any]:
    try:
        checks = check(series, target, SUMMARY_HORIZON)
    except backtester.Refused as refused:
        return {"refused": str(refused)}
    windows = next(item for item in checks if item.name == CHECKS[1])
    return {
        "ready": ready(checks),
        "failed": [item.stated() for item in checks if not item.passed],
        "windows": windows.measured,
    }
