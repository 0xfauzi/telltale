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

import re
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
# c_min on the attempt and change clocks. A change is a coarser row than a model
# request and a repository lineage holds fewer of them, so design 6.12 halves the
# context floor rather than making those clocks unforecastable.
C_MIN_SHORT = 16
# The smallest placebo block. Design 6.12: a context shorter than two blocks cannot be
# cut into blocks that could be permuted at all.
PLACEBO_BLOCK_MIN = 2
# Design 6.12's demotion rule for H2 and H3: a measure is withheld from repository
# comparison while its minimal detectable difference is above this share of the median.
# Not read by any forecast in this package; printed by every report that prints the
# pre-registered constants, because the constants are one pre-registration.
DEMOTION_RESOLUTION = 0.25
# The ablation's variate cap (design 6.12), which is NOT the model's 32-variate cap in
# forecast/timesfm.py. This one is a pre-registered limit on how wide a variant may be.
MAX_VARIATES = 15
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

# The one variant W1-T6 built: every other request-clock column, past-only.
REQUEST_VARIANT = "request_past_only"
# The change clock's equivalent, and the three ablation blocks that cut it down.
CHANGE_VARIANT = "change_past_only"

# The chronology placebo (design 6.12). B = max(2, H) whole rows move together, so the
# multiset of context rows survives and the order does not; R seeds are run and the
# median and the range are reported. B = 1 is the second control, which destroys
# dependence at every lag rather than only above B.
PLACEBO_SEEDS = 5
PLACEBO_ROW_BLOCK = 1
ORDERING_TRUE = "true"
ORDERING_BLOCK = "placebo_block"
ORDERING_ROW = "placebo_row"
# The three words the forecast_runs CHECK constraint allows, in the order a report
# lists them. store.py holds the constraint; this is the copy the code compares against.
ORDERINGS = (ORDERING_TRUE, ORDERING_BLOCK, ORDERING_ROW)


def placebo_block(horizon: int) -> int:
    """B = max(2, H). A block of one row is the separate ORDERING_ROW control."""
    return max(PLACEBO_BLOCK_MIN, horizon)


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
    # The one-step candidate protocol's past-future covariates (design 6.12): the
    # candidate row's known features, one column per name, spanning [ctx_start, o + H)
    # and so of length n_ctx + H. None on every other run, and a forecaster that does
    # not declare `reads_future` never sees it. It is a separate field rather than more
    # rows because `rows` is a rectangle whose first column is the target, and the
    # target at row o is exactly what a forecast is not allowed to hold.
    future: dict[str, list[float]] | None = None

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
    # The change clock's three ablation targets (design 6.12, H7). c_min is 16 rather
    # than 32 and the variant is the change clock's, so a run on one of these prints
    # different constants from a request-clock run and says so on its own face.
    **{
        name: TargetSpec(
            clock="change",
            unit=unit,
            nonnegative=True,
            c_min=C_MIN_SHORT,
            variant=CHANGE_VARIANT,
        )
        for name, unit in (
            ("attempts_to_land", "attempts"),
            ("fresh_input_tokens_total", "tokens"),
            ("verification_cycles", "cycles"),
        )
    },
    # The one-step candidate protocol's three targets (design 6.12, H8). Every one of
    # them is a POST-MERGE quantity of the candidate row, which is what makes the
    # candidate's own A block legitimate as a future covariate. H = 1 only: the protocol
    # conditions on one candidate and forecasts the row it becomes.
    **{
        name: TargetSpec(
            clock="change",
            unit=unit,
            nonnegative=True,
            c_min=C_MIN_SHORT,
            horizons=(1,),
            variant=CHANGE_VARIANT,
        )
        for name, unit in (
            ("merge_verification_ms", "ms"),
            ("merge_verification_failed", "flag"),
            ("rework_within_3", "flag"),
        )
    },
}

# The A/B/C ablation of design 6.12 (H7), in the order the design lists them. Each
# block CONTAINS the earlier one, so the rule below compares nested variants and the
# question "does C add temporal information" is the only question they can answer.
ABLATION_A = (
    "files_changed",
    "lines_added",
    "lines_removed",
    "subsystems_touched",
    "test_files_changed",
    "dependency_delta",
)
ABLATION_B = (
    *ABLATION_A,
    "fresh_input_tokens_total",
    "cache_read_tokens_total",
    "compactions",
    "attempts_to_land",
    "env_changed",
)
ABLATION_C = (
    *ABLATION_B,
    "verification_cycles",
    "edit_turnover_ratio",
    "stable_state_intervals",
    "unique_files_read",
)
ABLATION_BLOCKS: dict[str, tuple[str, ...]] = {
    "A": ABLATION_A,
    "B": ABLATION_B,
    "C": ABLATION_C,
}

# Known at merge time, so it can never be a candidate TARGET: conditioning a forecast
# of it on the A block would be conditioning it on a set that already determines it.
# It is a legitimate ablation target and a legitimate covariate, which is why the
# refusal names the protocol rather than the column.
CANDIDATE_FORBIDDEN = "attempts_to_land"
CANDIDATE_TARGETS = (
    "merge_verification_ms",
    "merge_verification_failed",
    "rework_within_3",
)
# rework_within_3 is a delayed label: row o is only labelled once three more changes
# have landed, so design 6.12 limits its origins to o <= N - REWORK_TAIL.
REWORK_TARGET = "rework_within_3"
REWORK_TAIL = 3

# Design 6.12, spec 15.8: this sentence rides in the assumptions of every candidate run
# and in every candidate report. It is mandatory rather than advisory because the
# difference it describes is the exact number a reader is most likely to read as an
# effect, and only one future was ever observed.
CANDIDATE_SENTENCE = (
    "The difference between the conditioned and unconditioned forecast measures how"
    " much the candidate's known features change the forecast; it is not the effect of"
    " merging the candidate, because only one future is observed."
)


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


# -- the word refusal (ADR-014) -------------------------------------------------------

# Three words a forecast may not use about itself. `cause` and `impact` claim a
# counterfactual that a backtest of one observed history cannot support; `would` claims
# the other branch of it. Inflections are refused with the stem, because "caused" and
# "impacted" make the same claim as the bare word. `because` is not a hit: the `b` and
# the `e` before `cause` are word characters, so there is no word boundary there.
REFUSED_WORDS = ("cause", "impact", "would")
_REFUSED = re.compile(
    r"\b(caus(?:e|es|ed|ing)|impact(?:s|ed|ing)?|would)\b", re.IGNORECASE
)


class ForbiddenWord(Exception):
    """A forecast report that claimed a cause. Raised before anything is printed."""


def refuse_words(text: str) -> str:
    """The text back, or the refusal naming every forbidden word it holds.

    Every renderer in this package passes its finished string through here, and every
    caller renders BEFORE it stores: design 6.12 says the check runs before anything is
    printed or stored, and a check that ran after the INSERT would be a check of a row
    that is already on the disk.
    """
    found = sorted({match.group(0).lower() for match in _REFUSED.finditer(text)})
    if found:
        raise ForbiddenWord(
            f"a forecast report may not use {', '.join(found)}:"
            f" design 6.12 and ADR-014 refuse {', '.join(REFUSED_WORDS)},"
            " and nothing here may present a forecast as a statement about a cause"
        )
    return text
