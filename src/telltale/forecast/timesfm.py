"""The TimesFM-3 adapter. The ONE module in src/ that may reach the model stack.

The forecast-isolation pre-commit hook enforces that sentence, and ADR-009 is the
reason: the collector runs beside the agent it records, and importing torch costs
1.02 s and 201 MB resident (E03, measured on this machine). `telltale.forecast`
imports this module inside a factory body, so a machine without the `forecast` extra
runs every other command untouched.

Everything below is a guard against something E03 measured the model doing SILENTLY.
None of these raise on their own; each returns a plausible number instead, which is
the worst failure a recorder can have.

  NaN of any position is substituted without a word: an interior NaN is exactly a
  linear interpolation, a leading one exactly a trim, a trailing one exactly a forward
  fill, and an all-NaN row exactly a row of zeros (differences of 0.0, twice). So this
  asserts `np.isfinite` on every array before the call and raises NonFinite otherwise.

  The 32-variate cap is not enforced. 33 variates run and return the shape a caller
  asked for. So `1 + past_only + past_future <= 32` is asserted here.

  A context shorter than one 32-point input patch is left-padded and forecast with no
  signal at all: 5 points returned finite numbers. So c_min comes from the registry
  and a short window is refused by name.

  `padding_mode="none"` and `padding_mode="edge"` on the same covariate differ by 1.41
  on the E03 probe. padding_mode is part of the answer, so it is fixed here and
  recorded in every ForecastRun.

  `predict_batch` RETURNS A GENERATOR. Timing the call alone measures the construction
  of a generator object, so the call is consumed with `list()` inside the timer.

The weights are `timesfm-non-commercial-license-v1.0`: research use, not production.
Every forecast command prints it and every stored run carries it.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from telltale.forecast import (
    C_MIN,
    DEFAULT_DEVICE,
    POINT_INDEX,
    QUANTILE_LEVELS,
    TARGETS,
)
from telltale.forecast.candidate import FUTURE_PREFIX
from telltale.model import ForecastResult

if TYPE_CHECKING:
    from telltale.forecast import Window

CHECKPOINT = "google/timesfm-3.0-pytorch"
LICENSE = "timesfm-non-commercial-license-v1.0"
# E03: `decode` computes the horizon from the past-future covariate length, and edge
# padding replicates the last column out to context + 64. Fixed here and recorded,
# because the two modes give different numbers on the same input.
PADDING_MODE = "edge"
# The cap the forecaster does not enforce. Design 6.12.
MAX_VARIATES = 32
# Where the Hugging Face cache lives when the owner points at one. E03's cache is the
# intended value; absent, huggingface_hub uses its own default.
CACHE_ENV = "TELLTALE_HF_CACHE"


def _future_names(window: Window) -> list[str]:
    """The past-future covariate names of a window, sorted, or an empty list.

    Sorted rather than in dict order: the array this builds is positional, and a run
    stored on Monday must line its variates up the same way as one stored on Tuesday.
    """
    return sorted(window.future or {})


class Unusable(Exception):
    """The adapter refusing a call the model would have answered anyway."""


class NonFinite(Unusable):
    """An array holding NaN or an infinity. The model would have imputed it silently."""


class ShortContext(Unusable):
    """Fewer than c_min context rows. The model would have left-padded and forecast."""


class TooManyVariates(Unusable):
    """More than 32 variates. The model would have returned the shape asked for."""


class WrongLength(Unusable):
    """A past-future covariate that is not n_ctx + H long. The model takes it anyway."""


class TimesFM:
    """One loaded checkpoint per instance, and one `predict_batch` call per window.

    Per-window calls are affordable: E03 measured 0.35 s median over 216 calls on CPU,
    worst 0.81 s, against a 60 s decision threshold. The horizon is rounded up to 64
    inside the model, so H = 1 costs what H = 64 costs.

    `device` defaults to cpu. E03 measured MPS agreeing with CPU to 9.072e-07 relative
    on ONE probe and running about twice as fast, but its load cost varied between
    2.0 s and 4.9 s against CPU's stable 1.9 to 2.1 s. The CPU numbers are the measured
    cost model; MPS is an opt-in whose device is recorded in the run.
    """

    checkpoint = CHECKPOINT
    license = LICENSE
    padding_mode = PADDING_MODE

    def __init__(self, device: str = DEFAULT_DEVICE, cache_dir: str | None = None):
        # torch and timesfm3 are imported HERE, not at module scope: constructing this
        # object is the moment somebody has asked for the model, and importing the
        # module should not be.
        from importlib.metadata import version

        import torch
        from timesfm3 import ModelConfig, TimesFM3Forecaster

        self.device = device
        self.cache_dir = cache_dir or os.environ.get(CACHE_ENV)
        self.torch_version = str(torch.__version__)
        # The DISTRIBUTION version, not a module attribute: timesfm3 carries no
        # __version__, and "unknown" in a stored run is a provenance field that says
        # nothing. The pin is exact in pyproject.toml for the reason this records.
        self.timesfm_version = version("timesfm")
        # None until the first call. A wall time nobody has measured is not 0.
        self.last_wall_ms: float | None = None
        started = time.perf_counter()
        self._model = TimesFM3Forecaster(
            ModelConfig(
                checkpoint_path=CHECKPOINT,
                device=device,
                cache_dir=self.cache_dir,
                local_files_only=False,
            )
        )
        self.load_ms = (time.perf_counter() - started) * 1000.0

    def forecast(self, window: Window, horizon: int) -> ForecastResult:
        """One window, one `predict_batch` call, the target's nine quantiles back."""
        floor = TARGETS[window.target].c_min if window.target in TARGETS else C_MIN
        if window.n_ctx < floor:
            raise ShortContext(
                f"origin {window.origin}: {window.n_ctx} context rows is below c_min"
                f" {floor}. The model would have left-padded and answered anyway."
            )
        target, past_only, future = self._arrays(window, horizon)
        outputs, wall_ms = self._call(target, past_only, future, horizon, window.target)
        self.last_wall_ms = wall_ms
        quantiles = np.asarray(outputs[0].quantiles, dtype=np.float64)
        band = quantiles.reshape(-1, len(QUANTILE_LEVELS))[:horizon]
        return ForecastResult(
            forecaster="timesfm",
            horizon=horizon,
            point=[[float(step[POINT_INDEX]) for step in band]],
            quantile_levels=list(QUANTILE_LEVELS),
            targets=[window.target],
            # Both blocks, named apart: a stored run has to say what the model SAW, and
            # a past-future covariate is a different claim from a past-only one. The
            # candidate protocol's whole difference between two runs is this list.
            covariates=[
                *window.covariates,
                *(f"{FUTURE_PREFIX}{name}" for name in _future_names(window)),
            ],
            # A Window is gap-free by construction, and this adapter refuses a
            # non-finite array rather than letting the model impute one.
            missingness_policy="exclude",
            checkpoint=CHECKPOINT,
            quantiles=[[[float(value) for value in step] for step in band]],
            warnings=[],
        )

    def _arrays(self, window: Window, horizon: int) -> tuple[Any, Any, Any]:
        """(1, n_ctx) target, (P, n_ctx) past-only and (F, n_ctx + H) past-future."""
        # Annotated Any on purpose: numpy is `follow_imports = "skip"` in mypy.ini so
        # that the gate answers the same question with and without the forecast extra,
        # and an unannotated ndarray is `Need type annotation` in one environment only.
        target: Any = np.asarray([window.column(window.target)], dtype=np.float32)
        names = window.covariates
        past_only: Any = (
            np.asarray([window.column(name) for name in names], dtype=np.float32)
            if names
            else None
        )
        ahead = _future_names(window)
        future: Any = (
            np.asarray(
                [self._span(window, name, horizon) for name in ahead], dtype=np.float32
            )
            if ahead
            else None
        )
        variates = 1 + len(names) + len(ahead)
        if variates > MAX_VARIATES:
            raise TooManyVariates(
                f"{variates} variates (1 target + {len(names)} past-only +"
                f" {len(ahead)} past-future) is past the cap of {MAX_VARIATES}."
                " The forecaster does not enforce it and would have answered."
                " Nothing here subsamples to fit."
            )
        self._finite(target, window.target)
        if past_only is not None:
            self._finite(past_only, ", ".join(names))
        if future is not None:
            self._finite(future, ", ".join(ahead))
        return target, past_only, future

    @staticmethod
    def _span(window: Window, name: str, horizon: int) -> list[float]:
        """One past-future covariate over [ctx_start, o + H), length asserted here.

        Measured on timesfm 3.0.0, 2026-09-03: `predict_batch` ACCEPTS a past-future
        covariate of length n_ctx and of length n_ctx + 64 as readily as n_ctx + H, and
        returns a different, plausible forecast for each. Nothing in the model says
        which length it was given, so a length the caller did not mean is a number
        nobody can trace. The assertion names the variate for that reason.
        """
        block = (window.future or {})[name]
        wanted = window.n_ctx + horizon
        if len(block) != wanted:
            raise WrongLength(
                f"past-future covariate {name} is {len(block)} long and origin"
                f" {window.origin} needs n_ctx + H = {window.n_ctx} + {horizon}"
                f" = {wanted}. TimesFM would have taken it and answered."
            )
        return block

    @staticmethod
    def _finite(array: Any, what: str) -> None:
        if not bool(np.isfinite(array).all()):
            raise NonFinite(
                f"{what}: the array holds NaN or an infinity. TimesFM would have"
                " interpolated, trimmed, forward-filled or zeroed it and said nothing."
            )

    def _call(
        self, target: Any, past_only: Any, future: Any, horizon: int, name: str
    ) -> tuple[list[Any], float]:
        """`predict_batch`, consumed with list() inside the timer. It is a generator."""
        kwargs: dict[str, Any] = {
            "contexts": [target],
            "horizon": horizon,
            "return_quantiles": True,
            # From the registry's nonnegative flag: tokens and tool calls cannot be
            # negative, and a forecast that says they can is not a forecast of them.
            "make_positive": TARGETS[name].nonnegative if name in TARGETS else False,
            # Recorded in every run, and load-bearing whenever `future` is not None:
            # read off the 3.0.0 source, `predict_batch` edge-pads a past-future
            # covariate from H out to the internal horizon of 64, and `padding_mode`
            # anything but "edge" or "none" raises. With "none" a covariate of length
            # n_ctx + H reaches `decode` short and the answer moves (E03 measured 1.41
            # on one probe), so the mode is fixed here rather than chosen per call.
            "padding_mode": PADDING_MODE,
        }
        if past_only is not None:
            kwargs["past_only_covariates"] = [past_only]
        if future is not None:
            kwargs["past_future_covariates"] = [future]
        started = time.perf_counter()
        outputs = list(self._model.predict_batch(**kwargs))
        return outputs, (time.perf_counter() - started) * 1000.0
