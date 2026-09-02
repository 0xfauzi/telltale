"""Is a TimesFM-3 backtest of one pair the same number twice, on this machine?

Fourteen of the seventeen pairs E07 and E08 share reproduce to the last bit. Two differ
in the timesfm column alone, by 0.03 and 0.11 percent, with identical retained windows,
identical baselines and an identical tau, so the series rows are identical and the
difference is inside the model stack. This script asks the only question that separates
a defect from an environment: run the same pair twice HERE and see whether the two runs
agree.

It runs the true-order backtest only (no placebo), stores nothing, and writes
`out/determinism.json`.

    uv run --extra forecast python experiments/E08/determinism.py [--capture ID]
"""

from __future__ import annotations

import argparse
import time
from typing import Any

from shared import OUT, forecasters, pair_path, point_home_at_the_copy, read, write
from shared import store as open_store

from telltale.forecast import TIMESFM
from telltale.forecast import backtest as backtester

# The smallest shared pair that changed between E07 and E08, so a repeat is cheap.
DEFAULT_CAPTURE = "cap_01M1HE3ZCX63HRX8PAWJDYSNG6"
REPEATS = 3


def once(series: Any, target: str, horizon: int, options: argparse.Namespace) -> Any:
    started = time.perf_counter()
    run = backtester.run(series, target, horizon, forecasters(options))
    return {
        "wall_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "n_windows": run["metrics"]["n_windows"],
        "mae_mean": {
            name: entry["mae_mean"]
            for name, entry in run["metrics"]["forecasters"].items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="E08 model determinism check")
    parser.add_argument("--capture", default=DEFAULT_CAPTURE)
    parser.add_argument("--target", default="output_tokens")
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--device", default="cpu")
    options = parser.parse_args()
    options.forecasters = [TIMESFM]
    point_home_at_the_copy()
    record = read(pair_path(options.capture, options.target, options.horizon))
    store = open_store()
    try:
        series = store.series(record["series_id"])
        repeats = [
            once(series, options.target, options.horizon, options)
            for _ in range(REPEATS)
        ]
    finally:
        store.close()
    values = [one["mae_mean"][TIMESFM] for one in repeats]
    stored = record["metrics"][TIMESFM]["mae_mean"]
    payload = {
        "capture": options.capture,
        "target": options.target,
        "horizon": options.horizon,
        "series_id": record["series_id"],
        "repeats": repeats,
        "stored_in_the_pair_file": stored,
        "identical_across_repeats": len(set(values)) == 1,
        "identical_to_the_stored_run": all(one == stored for one in values),
        "spread": max(values) - min(values),
    }
    write(OUT / "determinism.json", payload)
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
