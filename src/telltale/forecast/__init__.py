"""The Forecaster interface, the target registry and the stub. Design 6.12.

A forecaster is anything with `forecast(window, horizon) -> ForecastResult`, and a
Window is a COPY of the context rows with no store handle on it. That is the whole
interface, and the shape of it is the no-look-ahead guarantee: a forecaster cannot
reach a row it was not handed, because it holds nothing that could fetch one.

Three things live here rather than in backtest.py, because a forecaster must be
constructible without a backtest and a backtest must be readable without a model.

  `TARGETS` is the pre-registered registry: which columns may be forecast, on which
  clock, in which unit, and with which constants. Changing an entry is a new
  experiment id, which is why the numbers are here and not in a call site.

  `EchoStub` is persistence plus a fixed offset, and it keeps every Window it was
  handed. The keeping is the point: the no-look-ahead contract test compares what the
  stub received against what it received on a poisoned copy of the same series.

  `FORECASTERS` maps a name to a FACTORY, never to an instance, and the timesfm entry
  imports `telltale.forecast.timesfm` inside the factory body. ADR-009: torch is 1.02 s
  of import and 201 MB resident (E03, measured), and `telltale` must start without it.
  `import telltale.forecast` in an environment with no `forecast` extra succeeds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Protocol

from telltale.model import ForecastResult

if TYPE_CHECKING:
    from collections.abc import Callable

# The pre-registered constants of design 6.12, printed by every forecast report.
# Changing one after seeing results requires a new experiment id: they are decision
# rules, not estimates, and nothing here is allowed to compute them.
DELTA = 0.10
W = 0.60
K_MIN = 20
C_MIN = 32
HORIZONS = (1, 4)
BASELINE_WINDOW = 8
THRESHOLD_RULE = "q80_first_c_min"
# The longest context a window may carry. Design 6.12: ctx_start = max(last changepoint
# <= o, o - 512).
MAX_CONTEXT = 512

# The nine quantile levels TimesFM-3 returns, spelled out rather than computed: 0.1 * 3
# is 0.30000000000000004, and these levels are compared against a coverage share and
# written into a stored run.
QUANTILE_LEVELS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
# The median. Design 6.12: the point forecast is this quantile and never an average of
# quantiles, because the mean of a quantile set is not a quantile of anything.
POINT_INDEX = 4
# The quantile the lead-time rule reads. QUANTILE_LEVELS[7] is 0.8.
ALARM_INDEX = 7

# The one variant this task builds: every other request-clock column, past-only.
REQUEST_VARIANT = "request_past_only"

# The four of design 6.12, in the order the report lists them. baselines.py holds
# the functions and checks its own table against this tuple.
BASELINE_NAMES = ("persistence", "rolling_median", "rolling_mean", "local_drift")

TIMESFM = "timesfm"
ECHO = "echo"
DEFAULT_DEVICE = "cpu"
DEVICES = ("cpu", "mps")

# EchoStub's two numbers. The offset is what separates it from the persistence baseline
# in a table; the spread is what gives it a quantile band a calibration test can read.
ECHO_OFFSET = 1.0
ECHO_SPREAD = 2.0


@dataclass(frozen=True)
class Window:
    """One origin's context: a copy of the rows, and nothing that could fetch more.

    `rows` holds floats only. A window whose context or actual carried an unknown was
    dropped by the backtester with its reason counted, so a forecaster never has to
    decide what a gap means and no policy here imputes one.
    """

    columns: list[str]
    rows: list[list[float]]
    origin: int
    ctx_start: int
    n_ctx: int
    horizon: int
    target: str

    def column(self, name: str) -> list[float]:
        index = self.columns.index(name)
        return [row[index] for row in self.rows]

    @property
    def covariates(self) -> list[str]:
        return [name for name in self.columns if name != self.target]


class Forecaster(Protocol):
    """The one signature. The baselines are pure functions behind it (design 6.12)."""

    def forecast(self, window: Window, horizon: int) -> ForecastResult: ...


@dataclass(frozen=True)
class TargetSpec:
    """What may be forecast, and the constants it is forecast under."""

    clock: str
    unit: str
    nonnegative: bool
    c_min: int = C_MIN
    horizons: tuple[int, ...] = HORIZONS
    k_min: int = K_MIN
    threshold_rule: str = THRESHOLD_RULE
    variant: str = REQUEST_VARIANT


# The request clock's three targets. Not every column: a target has to be a quantity
# somebody would act on, and `env_changed`, `verification_seen` and
# `last_verification_failed` are flags whose MAE means nothing. The other request-clock
# columns ride along as past-only covariates, which is what `variant` names.
TARGETS: dict[str, TargetSpec] = {
    "fresh_input_tokens": TargetSpec(clock="request", unit="tokens", nonnegative=True),
    "output_tokens": TargetSpec(clock="request", unit="tokens", nonnegative=True),
    "tool_calls_since_prev": TargetSpec(
        clock="request", unit="calls", nonnegative=True
    ),
}


@dataclass
class EchoStub:
    """Persistence plus a fixed offset, and a record of every Window it was handed.

    `seen` is what the no-look-ahead test reads. It is a list rather than a count
    because the test compares the CONTENTS of each window against the same window from
    a run over a series whose future rows were replaced with 1e9: equal windows are the
    claim, and a count would pass while the contents differed.
    """

    seen: list[Window] = field(default_factory=list)
    name: str = ECHO

    def forecast(self, window: Window, horizon: int) -> ForecastResult:
        self.seen.append(window)
        last = window.column(window.target)[-1]
        point = [last + ECHO_OFFSET] * horizon
        return ForecastResult(
            forecaster=self.name,
            horizon=horizon,
            point=[point],
            quantile_levels=list(QUANTILE_LEVELS),
            targets=[window.target],
            covariates=window.covariates,
            # A Window is gap-free by construction: the backtester dropped every window
            # whose context or actual held an unknown. So the policy this forecast was
            # made under is the one that produced the window, and it imputed nothing.
            missingness_policy="exclude",
            quantiles=[
                [
                    [value + (level - 0.5) * ECHO_SPREAD for level in QUANTILE_LEVELS]
                    for value in point
                ]
            ],
        )


def _timesfm(device: str = DEFAULT_DEVICE) -> Forecaster:
    """The model adapter, imported HERE and nowhere else in this package.

    The import is inside the body because the module pulls torch, and ADR-009 says the
    collector never pays for it. Measured by E03: torch alone is 1.02 s and 201 MB.
    """
    from telltale.forecast.timesfm import TimesFM

    return TimesFM(device=device)


def _baseline(name: str) -> Forecaster:
    """One baseline behind the Forecaster signature, imported inside the body.

    Not at module scope: baselines.py imports Window and QUANTILE_LEVELS from HERE, so
    importing it from here at import time would be a cycle that happens to work because
    of statement order. Inside the factory there is no cycle at all.
    """
    from telltale.forecast.baselines import BASELINES, Baseline

    return Baseline(name=name, fn=BASELINES[name])


FORECASTERS: dict[str, Callable[[], Forecaster]] = {
    **{name: partial(_baseline, name) for name in BASELINE_NAMES},
    ECHO: EchoStub,
    TIMESFM: _timesfm,
}

# What `forecast backtest` runs when nobody says. timesfm is absent on purpose: it
# needs an extra that is not installed by default, and a default that fails on a
# machine without torch is a default nobody can run.
DEFAULT_FORECASTERS = (*BASELINE_NAMES, ECHO)


def make(name: str, device: str = DEFAULT_DEVICE) -> Forecaster:
    """One forecaster by name. `device` reaches the model adapter and nothing else."""
    if name == TIMESFM:
        return _timesfm(device)
    return FORECASTERS[name]()
