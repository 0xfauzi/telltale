"""E03: what TimesFM 3.0.0 costs and how it behaves on this machine.

Nothing here starts an agent session or spends a token. It downloads
`google/timesfm-3.0-pytorch` into experiments/E03/out/hf, runs it against synthetic
series, and writes every number it measured to experiments/E03/out/results.json. The
decision file docs/experiments/E03.md cites that JSON by key, so a number printed on a
terminal and nowhere else does not exist.

Eight probes, in the order the adapter in design 6.12 will meet them: checkpoint
(bytes on disk, seconds to fetch, and the load cost of a process that already has the
cache); matrix (wall time per call over three series shapes, three lengths, two horizons
and two variate counts, n=3); monotone (whether the RAW quantile head is ordered, with
sort_quantiles off, because the default sorts and would answer that by fiat); nan (an
interior NaN, a leading NaN and an all-NaN row, each against the array the adapter would
have to pass instead); short (5 points, below one 32-point input patch); device (cpu
against mps, difference and speed factor); cap (33 variates, one past the cap the
forecaster does not enforce); padding (a past-future covariate of length context + H,
with and without edge padding).

Wall time is measured around `list(...)`: `predict_batch` is a generator and returns
before it has done any work.

Usage:
    uv run --extra forecast python experiments/E03/run.py
    uv run --extra forecast python experiments/E03/run.py --reps 5
"""

from __future__ import annotations

import argparse
import json
import platform
import resource
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import report
import timesfm3
import torch
from timesfm3 import ModelConfig, TimesFM3Forecaster

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

E03_DIR = Path(__file__).resolve().parent
OUT = E03_DIR / "out"
HF_CACHE = OUT / "hf"

CHECKPOINT = "google/timesfm-3.0-pytorch"
# Recorded in every ForecastRun by design 6.12. Research use only; not production.
WEIGHTS_LICENSE = "timesfm-non-commercial-license-v1.0"

# The brief's stop conditions. Exceeding either ends the run with the bytes and the
# seconds observed rather than a partial matrix.
MAX_CHECKPOINT_BYTES = 10 * 1024**3
MAX_DOWNLOAD_S = 30 * 60

LENGTHS = (16, 64, 512)
HORIZONS = (8, 24)
KINDS = ("random_walk", "ar1", "step")
# Fixed so a re-run compares against the same arrays rather than against new noise.
BASE_SEED = 20260902
# Discarded calls before the matrix. See warmup() for why they are there.
WARMUP = 12

# Decision rule (b), from docs/experiments/E03.md.
AFFORDABLE_CALL_S = 60.0
# Decision rule (c).
NAN_INTERPOLATION_TOL = 1e-6
# Decision rule (d).
DEVICE_RELATIVE_TOL = 1e-3


def make_series(kind: str, length: int, seed: int) -> np.ndarray:
    """One synthetic series of `length` points, shaped (1, length) for one variate."""
    rng = np.random.default_rng(seed)
    if kind == "random_walk":
        values = np.cumsum(rng.normal(0.0, 1.0, length))
    elif kind == "ar1":
        values = np.zeros(length, dtype=np.float64)
        noise = rng.normal(0.0, 1.0, length)
        for i in range(1, length):
            values[i] = 0.8 * values[i - 1] + noise[i]
    elif kind == "step":
        values = np.zeros(length, dtype=np.float64)
        values[length // 2 :] = 5.0
        values += rng.normal(0.0, 0.2, length)
    else:
        raise ValueError(f"unknown series kind: {kind}")
    return values.astype(np.float32).reshape(1, length)


def on_disk_bytes(root: Path) -> dict[str, int]:
    """Bytes a directory occupies, counting each blob once.

    The Hugging Face cache is symlinks into `blobs/`, so summing `stat()` over every
    path double-counts: measured, 2,645,800,425 apparent against 1,322,900,328 real.
    """
    seen: set[tuple[int, int]] = set()
    real = 0
    apparent = 0
    files = 0
    for path in root.rglob("*"):
        info = path.lstat()
        if path.is_file():
            apparent += path.stat().st_size
            files += 1
        if not path.is_symlink() and path.is_file():
            key = (info.st_dev, info.st_ino)
            if key not in seen:
                seen.add(key)
                real += info.st_size
    return {"bytes_on_disk": real, "bytes_apparent": apparent, "files": files}


def peak_rss_bytes() -> int:
    """ru_maxrss. Bytes on Darwin, kibibytes on Linux; the meta block records which."""
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def build_forecaster(
    device: str, *, local_only: bool
) -> tuple[TimesFM3Forecaster, float]:
    config = ModelConfig(
        checkpoint_path=CHECKPOINT,
        per_core_batch_size=4,
        device=device,
        cache_dir=str(HF_CACHE),
        local_files_only=local_only,
    )
    t0 = time.monotonic()
    forecaster = TimesFM3Forecaster(config)
    return forecaster, time.monotonic() - t0


def timed_predict(
    forecaster: TimesFM3Forecaster, **kwargs: Any
) -> tuple[list[Any], float]:
    """One predict_batch call, fully consumed, with the wall time around it."""
    t0 = time.monotonic()
    outputs = list(forecaster.predict_batch(**kwargs))
    return outputs, time.monotonic() - t0


def quantile_disorder(quantiles: np.ndarray) -> dict[str, float | int]:
    """How far the quantile axis departs from non-decreasing, per (variate, step)."""
    diffs = np.diff(quantiles, axis=-1)
    bad = diffs < 0.0
    steps = int(np.prod(quantiles.shape[:-1]))
    return {
        "steps": steps,
        "steps_disordered": int(np.any(bad, axis=-1).sum()),
        "max_decrease": float(-diffs.min()) if bad.any() else 0.0,
    }


def call_shapes(output: Any) -> dict[str, list[int]]:
    return {
        "forecast_shape": list(output.forecast.shape),
        "quantiles_shape": list(output.quantiles.shape),
    }


def stats_of(values: Iterable[float]) -> dict[str, Any]:
    seq = sorted(values)
    return {
        "n": len(seq),
        "median_s": round(statistics.median(seq), 4),
        "min_s": round(seq[0], 4),
        "max_s": round(seq[-1], 4),
    }


def covariates_for(kind: str, length: int, horizon: int, seed: int) -> dict[str, Any]:
    """One past-only and one past-future covariate, so the call carries three variates.

    The past-future array spans context + horizon, and `padding_mode="edge"` replicates
    its last column out to context + 64, which is what design 6.12 says to record.
    """
    past_only = make_series(kind, length, seed + 101)
    past_future = make_series(kind, length + horizon, seed + 202)
    return {
        "past_only_covariates": [past_only],
        "past_future_covariates": [past_future],
    }


def matrix_cell(
    forecaster: TimesFM3Forecaster,
    kind: str,
    length: int,
    horizon: int,
    variates: int,
    reps: int,
) -> dict[str, Any]:
    seed = BASE_SEED + length + horizon
    context = make_series(kind, length, seed)
    kwargs: dict[str, Any] = {
        "contexts": [context],
        "horizon": horizon,
        "return_quantiles": True,
    }
    if variates == 3:
        kwargs.update(covariates_for(kind, length, horizon, seed))
        kwargs["padding_mode"] = "edge"
    walls: list[float] = []
    outputs: list[Any] = []
    for _ in range(reps):
        outputs, wall = timed_predict(forecaster, **kwargs)
        walls.append(wall)
    cell = {
        "kind": kind,
        "length": length,
        "horizon": horizon,
        "variates": variates,
        **stats_of(walls),
        "walls_s": [round(w, 4) for w in walls],
        **call_shapes(outputs[0]),
        "quantile_order": quantile_disorder(outputs[0].quantiles),
    }
    return cell


def phase_checkpoint(reps_note: str) -> dict[str, Any]:
    """Download, then load again from the warm cache. The difference is the download."""
    before = on_disk_bytes(HF_CACHE) if HF_CACHE.exists() else {"bytes_on_disk": 0}
    HF_CACHE.mkdir(parents=True, exist_ok=True)
    forecaster, cold_s = build_forecaster("cpu", local_only=False)
    del forecaster
    after = on_disk_bytes(HF_CACHE)
    _, warm_s = build_forecaster("cpu", local_only=True)
    record = {
        "checkpoint": CHECKPOINT,
        "license": WEIGHTS_LICENSE,
        "cache_dir": str(HF_CACHE),
        "cache_was_empty": before["bytes_on_disk"] == 0,
        "first_construct_s": round(cold_s, 3),
        "warm_construct_s": round(warm_s, 3),
        "download_s_estimate": round(cold_s - warm_s, 3),
        "note": reps_note,
        **after,
    }
    over_bytes = record["bytes_on_disk"] > MAX_CHECKPOINT_BYTES
    over_time = record["first_construct_s"] > MAX_DOWNLOAD_S
    record["stop_condition_hit"] = bool(over_bytes or over_time)
    return record


def warmup(forecaster: TimesFM3Forecaster, calls: int) -> list[float]:
    """Discarded calls, so a cell's median is not charged for the process settling.

    A rehearsal of this matrix in canonical order with no warmup ran its later cells
    about 4x faster than its earlier ones, which read as "step changes forecast faster
    than random walks". Whatever caused it, it is not the cell order: `--canonical`
    reproduces exactly that arrangement in out/results_canonical.json and the two halves
    of the matrix come out within 2 percent of each other. The warmup, the shuffle in
    phase_matrix and the control cell either side of it stay because they make the
    question answerable at all, not because they explained that rehearsal.
    """
    context = make_series("random_walk", 64, BASE_SEED - 1)
    walls = []
    for _ in range(calls):
        _, wall = timed_predict(
            forecaster, contexts=[context], horizon=24, return_quantiles=True
        )
        walls.append(round(wall, 4))
    return walls


def phase_matrix(
    forecaster: TimesFM3Forecaster, reps: int, *, shuffle: bool
) -> list[dict[str, Any]]:
    specs = [
        (kind, length, horizon, variates)
        for kind in KINDS
        for length in LENGTHS
        for horizon in HORIZONS
        for variates in (1, 3)
    ]
    order = (
        np.random.default_rng(BASE_SEED).permutation(len(specs))
        if shuffle
        else range(len(specs))
    )
    cells = []
    for position, index in enumerate(order):
        kind, length, horizon, variates = specs[int(index)]
        cell = matrix_cell(forecaster, kind, length, horizon, variates, reps)
        cell["execution_position"] = position
        cells.append(cell)
    return sorted(
        cells, key=lambda c: (c["kind"], c["length"], c["horizon"], c["variates"])
    )


def control_cell(forecaster: TimesFM3Forecaster, reps: int) -> dict[str, Any]:
    """One fixed cell, run either side of the matrix. Drift is machine, not data."""
    context = make_series("random_walk", 64, BASE_SEED)
    walls = []
    for _ in range(reps):
        _, wall = timed_predict(
            forecaster, contexts=[context], horizon=24, return_quantiles=True
        )
        walls.append(wall)
    return {"cell": "random_walk length 64 horizon 24 univariate", **stats_of(walls)}


def phase_raw_quantiles(forecaster: TimesFM3Forecaster) -> list[dict[str, Any]]:
    """Monotonicity of the head itself, with the default sort switched off."""
    rows = []
    for kind in KINDS:
        for length in LENGTHS:
            seed = BASE_SEED + length
            outputs, _ = timed_predict(
                forecaster,
                contexts=[make_series(kind, length, seed)],
                horizon=24,
                return_quantiles=True,
                sort_quantiles=False,
            )
            rows.append(
                {
                    "kind": kind,
                    "length": length,
                    "horizon": 24,
                    "sort_quantiles": False,
                    **quantile_disorder(outputs[0].quantiles),
                }
            )
    return rows


def _forecast_of(forecaster: TimesFM3Forecaster, context: np.ndarray) -> np.ndarray:
    outputs, _ = timed_predict(
        forecaster, contexts=[context], horizon=24, return_quantiles=True
    )
    return np.asarray(outputs[0].forecast, dtype=np.float64)


def _diff(a: np.ndarray, b: np.ndarray) -> dict[str, Any]:
    if a.shape != b.shape:
        return {"same_shape": False, "shape_a": list(a.shape), "shape_b": list(b.shape)}
    delta = float(np.abs(a - b).max())
    return {
        "same_shape": True,
        "max_abs_diff": delta,
        "within_tolerance": delta < NAN_INTERPOLATION_TOL,
    }


def phase_nan(forecaster: TimesFM3Forecaster) -> dict[str, Any]:
    """Four NaN shapes, each against the array the adapter would have to build."""
    base = make_series("random_walk", 64, BASE_SEED)

    interior = base.copy()
    interior[0, 30] = np.nan
    filled = base.copy()
    filled[0, 30] = (base[0, 29] + base[0, 31]) / 2.0

    leading = base.copy()
    leading[0, 0] = np.nan
    trimmed = base[:, 1:].copy()
    backfilled = base.copy()
    backfilled[0, 0] = base[0, 1]

    # Design 6.12 says trailing NaN is interpolated. np.interp clamps at the edges,
    # so the comparison it has to beat is a forward fill, not a linear one.
    trailing = base.copy()
    trailing[0, 63] = np.nan
    forward_filled = base.copy()
    forward_filled[0, 63] = base[0, 62]
    linear_out = base.copy()
    linear_out[0, 63] = 2.0 * base[0, 62] - base[0, 61]

    two_row = np.vstack([base, make_series("ar1", 64, BASE_SEED + 7)])
    nan_row = two_row.copy()
    nan_row[1, :] = np.nan
    zero_row = two_row.copy()
    zero_row[1, :] = 0.0

    pairs = [
        ("interior_nan_vs_linear_fill", interior, filled),
        ("leading_nan_vs_trimmed", leading, trimmed),
        ("leading_nan_vs_backfilled", leading, backfilled),
        ("trailing_nan_vs_forward_fill", trailing, forward_filled),
        ("trailing_nan_vs_linear_extrapolation", trailing, linear_out),
        ("all_nan_row_vs_zero_row", nan_row, zero_row),
    ]
    record: dict[str, Any] = {
        label: _diff(_forecast_of(forecaster, a), _forecast_of(forecaster, b))
        for label, a, b in pairs
    }
    record["nan_output_is_finite"] = bool(
        np.isfinite(_forecast_of(forecaster, interior)).all()
    )
    return record


def attempt(label: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run a probe that may raise, and keep the exception verbatim if it does."""
    try:
        return {"label": label, "raised": False, **fn()}
    except Exception as exc:
        # Broad on purpose: an unsupported device, an unenforced cap and a covariate of
        # the wrong length each fail differently, and the message IS the measurement.
        return {
            "label": label,
            "raised": True,
            "exception_type": type(exc).__name__,
            "exception": str(exc),
        }


def phase_short_context(forecaster: TimesFM3Forecaster) -> dict[str, Any]:
    """Five points, against an input patch of 32 and a c_min the adapter must own."""

    def probe() -> dict[str, Any]:
        context = make_series("random_walk", 5, BASE_SEED)
        outputs, wall = timed_predict(
            forecaster, contexts=[context], horizon=8, return_quantiles=True
        )
        return {
            "wall_s": round(wall, 4),
            **call_shapes(outputs[0]),
            "forecast": [round(v, 6) for v in outputs[0].forecast[0].tolist()],
            "context": [round(v, 6) for v in context[0].tolist()],
        }

    return attempt("context_length_5_horizon_8", probe)


def _relative(cpu: np.ndarray, other: np.ndarray) -> dict[str, float]:
    abs_diff = np.abs(cpu - other)
    per_point = abs_diff / np.maximum(np.abs(cpu), np.finfo(np.float64).tiny)
    scale = float(np.abs(cpu).max())
    return {
        "max_abs_diff": float(abs_diff.max()),
        "max_relative_diff_per_point": float(per_point.max()),
        "max_relative_diff_to_scale": float(abs_diff.max() / scale) if scale else 0.0,
        "cpu_max_abs": scale,
    }


def phase_device(cpu_forecaster: TimesFM3Forecaster, reps: int) -> dict[str, Any]:
    """cpu against mps on the 64-point horizon-64 case named by decision rule (d)."""
    context = make_series("random_walk", 64, BASE_SEED)
    kwargs: dict[str, Any] = {
        "contexts": [context],
        "horizon": 64,
        "return_quantiles": True,
    }
    record: dict[str, Any] = {
        "mps_available": bool(torch.backends.mps.is_available()),
        "mps_built": bool(torch.backends.mps.is_built()),
    }

    def probe() -> dict[str, Any]:
        mps_forecaster, load_s = build_forecaster("mps", local_only=True)
        cpu_walls: list[float] = []
        mps_walls: list[float] = []
        cpu_out = mps_out = None
        # Interleaved. A block of cpu followed by a block of mps would charge the
        # speed factor with whatever the machine was doing during the first block,
        # and the warmup measurement above shows that is worth up to a factor of 4.
        for _ in range(reps):
            outputs, wall = timed_predict(cpu_forecaster, **kwargs)
            cpu_walls.append(wall)
            cpu_out = outputs[0]
            outputs, wall = timed_predict(mps_forecaster, **kwargs)
            mps_walls.append(wall)
            mps_out = outputs[0]
        cpu_median = stats_of(cpu_walls)["median_s"]
        mps_median = stats_of(mps_walls)["median_s"]
        return {
            "load_s": round(load_s, 3),
            "cpu": stats_of(cpu_walls),
            "mps": stats_of(mps_walls),
            "speed_factor_cpu_over_mps": round(cpu_median / mps_median, 3),
            "forecast": _relative(
                np.asarray(cpu_out.forecast, dtype=np.float64),
                np.asarray(mps_out.forecast, dtype=np.float64),
            ),
            "quantiles": _relative(
                np.asarray(cpu_out.quantiles, dtype=np.float64),
                np.asarray(mps_out.quantiles, dtype=np.float64),
            ),
        }

    record["mps_run"] = attempt("device_mps", probe)
    return record


def phase_variate_cap(forecaster: TimesFM3Forecaster) -> list[dict[str, Any]]:
    """33 variates, twice: all as targets, and as one target plus 32 covariates."""
    length, horizon = 64, 24

    def as_targets() -> dict[str, Any]:
        rows = [make_series("ar1", length, BASE_SEED + i)[0] for i in range(33)]
        context = np.vstack(rows)
        outputs, wall = timed_predict(
            forecaster, contexts=[context], horizon=horizon, return_quantiles=True
        )
        return {"wall_s": round(wall, 4), **call_shapes(outputs[0])}

    def as_covariates() -> dict[str, Any]:
        context = make_series("ar1", length, BASE_SEED)
        past_only = np.vstack(
            [make_series("ar1", length, BASE_SEED + 300 + i)[0] for i in range(31)]
        )
        past_future = make_series("ar1", length + horizon, BASE_SEED + 900)
        outputs, wall = timed_predict(
            forecaster,
            contexts=[context],
            horizon=horizon,
            past_only_covariates=[past_only],
            past_future_covariates=[past_future],
            padding_mode="edge",
            return_quantiles=True,
        )
        return {"wall_s": round(wall, 4), **call_shapes(outputs[0])}

    return [
        attempt("33_target_variates", as_targets),
        attempt("1_target_31_past_only_1_past_future", as_covariates),
    ]


def phase_padding(forecaster: TimesFM3Forecaster) -> list[dict[str, Any]]:
    """What a past-future covariate of the wrong length does without edge padding."""
    length, horizon = 64, 24
    context = make_series("random_walk", length, BASE_SEED)
    past_future_short = make_series("ar1", length + horizon, BASE_SEED + 11)
    past_future_full = make_series("ar1", length + 64, BASE_SEED + 11)

    forecasts: dict[str, np.ndarray] = {}

    def run(
        label: str, covariate: np.ndarray, mode: str
    ) -> Callable[[], dict[str, Any]]:
        def probe() -> dict[str, Any]:
            outputs, wall = timed_predict(
                forecaster,
                contexts=[context],
                horizon=horizon,
                past_future_covariates=[covariate],
                padding_mode=mode,
                return_quantiles=True,
            )
            forecasts[label] = np.asarray(outputs[0].forecast, dtype=np.float64)
            return {
                "wall_s": round(wall, 4),
                "covariate_length": int(covariate.shape[-1]),
                **call_shapes(outputs[0]),
            }

        return probe

    labels = [
        ("pf_ctx_plus_h_padding_none", past_future_short, "none"),
        ("pf_ctx_plus_64_padding_none", past_future_full, "none"),
        ("pf_ctx_plus_h_padding_edge", past_future_short, "edge"),
    ]
    records = [attempt(label, run(label, cov, mode)) for label, cov, mode in labels]
    # Whether the padding mode is a validity switch or a numbers switch. If a covariate
    # of length context + H runs without edge padding AND gives a different forecast,
    # the adapter's padding_mode is part of the result, not part of the plumbing.
    if len(forecasts) == 3:
        records.append(
            {
                "label": "none_vs_edge_same_covariate",
                "raised": False,
                "wall_s": 0.0,
                "quantiles_shape": [],
                **_diff(
                    forecasts["pf_ctx_plus_h_padding_none"],
                    forecasts["pf_ctx_plus_h_padding_edge"],
                ),
            }
        )
    return records


def environment() -> dict[str, Any]:
    return {
        "run_started_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "torch": torch.__version__,
        "numpy": np.__version__,
        "timesfm3_path": str(Path(timesfm3.__file__).parent),
        "ru_maxrss_unit": "bytes" if sys.platform == "darwin" else "kibibytes",
        "torch_num_threads": torch.get_num_threads(),
    }


def decisions(results: dict[str, Any]) -> dict[str, Any]:
    """The four rules of docs/experiments/E03.md, resolved against what was measured."""
    slowest = max(c["max_s"] for c in results["matrix"])
    nan = results["nan_probe"]["interior_nan_vs_linear_fill"]
    run = results["device"]["mps_run"]
    if run["raised"]:
        device_branch = "cpu pinned: mps raised"
        device_number = run["exception"]
    else:
        worst = run["forecast"]["max_relative_diff_per_point"]
        ok = worst <= DEVICE_RELATIVE_TOL
        device_branch = "mps allowed" if ok else "cpu pinned"
        device_number = f"max relative diff {worst:.3e} against {DEVICE_RELATIVE_TOL}"
    cheap = slowest < AFFORDABLE_CALL_S
    interpolates = bool(nan.get("within_tolerance"))
    return {
        "a_install": {
            "branch": "resolved and installed on python 3.12",
            "evidence": "out/uv_sync_forecast.txt, out/uv_sync_forecast_cold.txt",
        },
        "b_per_call_cost": {
            "branch": "per-window calls affordable"
            if cheap
            else "backtester must batch",
            "evidence": f"slowest single call {slowest:.4f}s / {AFFORDABLE_CALL_S}s",
        },
        "c_nan": {
            "branch": "internal interpolation confirmed"
            if interpolates
            else "difference recorded, adapter still refuses NaN",
            "evidence": f"max abs diff {nan.get('max_abs_diff')} / "
            f"{NAN_INTERPOLATION_TOL}",
        },
        "d_device": {"branch": device_branch, "evidence": device_number},
    }


def stop_now(checkpoint: dict[str, Any], results_path: Path) -> int:
    print(
        "STOP: checkpoint "
        f"{checkpoint['bytes_on_disk']} bytes in "
        f"{checkpoint['first_construct_s']} s exceeds the brief's limit "
        f"({MAX_CHECKPOINT_BYTES} bytes / {MAX_DOWNLOAD_S} s)."
    )
    results_path.write_text(json.dumps({"checkpoint": checkpoint}, indent=2) + "\n")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reps", type=int, default=3, help="calls per matrix cell")
    parser.add_argument(
        "--canonical",
        action="store_true",
        help="no warmup, cells in canonical order, written to results_canonical.json",
    )
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    name = "results_canonical.json" if args.canonical else "results.json"
    results_path = OUT / name
    results: dict[str, Any] = {
        "meta": environment(),
        "reps": args.reps,
        "canonical_order": args.canonical,
    }

    checkpoint = phase_checkpoint(f"reps={args.reps}")
    results["checkpoint"] = checkpoint
    if checkpoint["stop_condition_hit"]:
        return stop_now(checkpoint, results_path)

    forecaster, load_s = build_forecaster("cpu", local_only=True)
    results["cpu_load_s"] = round(load_s, 3)
    results["rss_after_load_bytes"] = peak_rss_bytes()

    results["warmup_walls_s"] = warmup(forecaster, 0 if args.canonical else WARMUP)
    results["control_before"] = control_cell(forecaster, args.reps)
    results["matrix"] = phase_matrix(forecaster, args.reps, shuffle=not args.canonical)
    results["control_after"] = control_cell(forecaster, args.reps)
    results["rss_after_matrix_bytes"] = peak_rss_bytes()
    results["raw_quantiles"] = phase_raw_quantiles(forecaster)
    results["nan_probe"] = phase_nan(forecaster)
    results["short_context"] = phase_short_context(forecaster)
    results["variate_cap"] = phase_variate_cap(forecaster)
    results["padding"] = phase_padding(forecaster)
    results["rss_before_device_bytes"] = peak_rss_bytes()
    results["device"] = phase_device(forecaster, args.reps)
    results["peak_rss_bytes"] = peak_rss_bytes()
    results["decisions"] = decisions(results)

    results_path.write_text(json.dumps(results, indent=2, sort_keys=False) + "\n")

    report.render(results)
    print(f"written: {results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
