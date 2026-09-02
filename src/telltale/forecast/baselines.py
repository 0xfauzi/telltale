"""The four baselines and their residual quantiles. Design 6.12.

Every one of them is a pure function of the context array and the horizon, and the
`Baseline` wrapper at the bottom is the only thing that knows about Windows. That
split is deliberate: a baseline that beats a 330M-parameter model is the finding this
laboratory exists to be able to report, so a baseline has to be small enough that
nobody has to trust it. Four functions, twelve lines between them.

No seasonal naive: no clock here has a season. A request clock's row is one model
request, and requests do not arrive on a weekly cycle.

Quantiles for a baseline are the point forecast plus the empirical quantiles of the
baseline's OWN one-step residuals inside the context. That is an honest interval and a
cheap one, and where there are too few residuals to form one the answer is None with a
warning rather than a band nobody measured: calibration and WQS are then "not
assessable" for that baseline, which is a different statement from "wide".
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

from telltale.forecast import (
    BASELINE_NAMES,
    BASELINE_WINDOW,
    QUANTILE_LEVELS,
    Window,
)
from telltale.model import ForecastResult

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# local_drift needs y_{o-1} and y_{o-9}, so nine rows of context.
DRIFT_SPAN = 8
DRIFT_MIN = DRIFT_SPAN + 1
DRIFT_WARNING = "local_drift undefined below 9 context rows: persistence used"

# Below this many one-step residuals a baseline gets no quantiles at all. Design 6.12
# fixes the number; with c_min at 32 the fallback is unreachable on a real window and
# exists for the short fixtures the contract tests use.
MIN_RESIDUALS = 8
NO_RESIDUALS = (
    "fewer than 8 one-step residuals in the context:"
    " calibration and WQS are not assessable for this baseline"
)


def persistence(context: Sequence[float], horizon: int) -> list[float]:
    """yhat_{o+h} = y_{o-1}, for every h."""
    return [float(context[-1])] * horizon


def rolling_median(context: Sequence[float], horizon: int) -> list[float]:
    """The median of the last 8 context values, held flat across the horizon."""
    return [statistics.median(context[-BASELINE_WINDOW:])] * horizon


def rolling_mean(context: Sequence[float], horizon: int) -> list[float]:
    """The mean of the last 8 context values, held flat across the horizon."""
    return [statistics.fmean(context[-BASELINE_WINDOW:])] * horizon


def local_drift(context: Sequence[float], horizon: int) -> list[float]:
    """y_{o-1} + h (y_{o-1} - y_{o-9}) / 8, with h counted in ROWS from y_{o-1}.

    h runs 1..H rather than 0..H-1, and the reading is load-bearing. The first forecast
    step is y_o, which is one row after y_{o-1}, so its drift term is one slope. Read
    the other way, local_drift and persistence are the same function at H = 1, which is
    the default horizon: one of the four baselines would carry no information at all
    where the design says to run it.

    Below nine context rows the slope has no second point, so this falls back to
    persistence and `Baseline` attaches DRIFT_WARNING.
    """
    if len(context) < DRIFT_MIN:
        return persistence(context, horizon)
    slope = (context[-1] - context[-DRIFT_MIN]) / DRIFT_SPAN
    return [float(context[-1]) + (step + 1) * slope for step in range(horizon)]


BASELINES: dict[str, Callable[[Sequence[float], int], list[float]]] = {
    "persistence": persistence,
    "rolling_median": rolling_median,
    "rolling_mean": rolling_mean,
    "local_drift": local_drift,
}
# One table, two files: the registry in forecast/__init__.py names the four so that it
# can build factories without importing this module, and this is where the two are
# compared. A name in one and not the other is an import-time failure, not a KeyError
# during somebody's backtest.
if tuple(BASELINES) != BASELINE_NAMES:
    raise ImportError(f"BASELINES {tuple(BASELINES)} is not {BASELINE_NAMES}")


def quantile(values: Sequence[float], level: float) -> float:
    """The empirical quantile by linear interpolation between order statistics.

    Named because the answer depends on the rule: this is numpy's default and the
    `inclusive` method of `statistics.quantiles`, and it is the one every quantile in
    this package uses (residual bands here, tau in backtest.py). A different rule would
    move tau, which would move the lead-time table.
    """
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = level * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def residual_quantiles(
    context: Sequence[float], fn: Callable[[Sequence[float], int], list[float]]
) -> tuple[list[float] | None, list[str]]:
    """The nine quantiles of fn's own one-step residuals inside the context.

    Each residual is `y_i - fn(context[:i], 1)`, so the prediction of row i is made from
    rows before i and the band is an out-of-sample one within the window. None with a
    warning below MIN_RESIDUALS: a band from three residuals is not a band.
    """
    residuals = [
        context[index] - fn(context[:index], 1)[0] for index in range(1, len(context))
    ]
    if len(residuals) < MIN_RESIDUALS:
        return None, [NO_RESIDUALS]
    return [quantile(residuals, level) for level in QUANTILE_LEVELS], []


@dataclass(frozen=True)
class Baseline:
    """A pure function behind the Forecaster signature. Design 6.12."""

    name: str
    fn: Callable[[Sequence[float], int], list[float]]

    def forecast(self, window: Window, horizon: int) -> ForecastResult:
        context = window.column(window.target)
        point = self.fn(context, horizon)
        band, warnings = residual_quantiles(context, self.fn)
        if self.fn is local_drift and len(context) < DRIFT_MIN:
            warnings = [DRIFT_WARNING, *warnings]
        return ForecastResult(
            forecaster=self.name,
            horizon=horizon,
            point=[point],
            quantile_levels=list(QUANTILE_LEVELS),
            targets=[window.target],
            covariates=window.covariates,
            # A Window is gap-free by construction (backtest.py drops any window with an
            # unknown in it), so this records the policy that produced the window.
            missingness_policy="exclude",
            quantiles=None
            if band is None
            else [[[value + offset for offset in band] for value in point]],
            warnings=warnings,
        )
