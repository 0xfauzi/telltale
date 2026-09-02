"""What every stage of E08 needs: the copy's paths, E07's runner, and the constants.

Four modules make up this experiment (run.py, pooled.py, lineage.py and this one) for
the reason W3-T2 split backtest.py: a file that grows past 800 lines fails the
file-length hook, and the cut has to be somewhere a reader would make it anyway. This
one holds what a stage cannot run without and nothing that is a stage.

The E07 import is the load-bearing line here. `experiments/E07/run.py` reads its
`SOURCE_DB` from TELLTALE_HOME AT ITS OWN IMPORT TIME, so it has to be imported while
that variable still names the owner's store and before `point_home_at_the_copy` moves
it. Importing this module first is what guarantees the order.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse

E08_DIR = Path(__file__).resolve().parent
REPO_ROOT = E08_DIR.parents[1]
E07_DIR = REPO_ROOT / "experiments" / "E07"
OUT = E08_DIR / "out"
HOME = OUT / "home"
REAL_HOME = Path("~/.telltale").expanduser()


def _e07() -> Any:
    """E07's runner as a module, with `experiments/E07` on sys.path.

    Inside a function because E07's run.py imports its sibling `decide` by bare name,
    which is the convention every experiment in this tree uses and the reason mypy
    excludes the whole directory.
    """
    sys.path.insert(0, str(E07_DIR))
    import run as e07_run

    return e07_run


e07 = _e07()

from telltale.forecast import (  # noqa: E402
    BASELINE_NAMES,
    BASELINE_WINDOW,
    C_MIN,
    C_MIN_SHORT,
    DELTA,
    DEMOTION_RESOLUTION,
    HORIZONS,
    K_MIN,
    PLACEBO_ROW_BLOCK,
    PLACEBO_SEEDS,
    THRESHOLD_RULE,
    W,
    placebo_block,
)
from telltale.store import Store  # noqa: E402

# The cohort, E07's: a launcher capture of the build, on this provider. Design 6.12
# never pools across providers and there is nothing else on this disk to pool with.
REQUEST_CLOCK = "request"
LINEAGE_CLOCKS = ("attempt", "change")


def point_home_at_the_copy() -> Path:
    """Everything after this call reads and writes `out/home`, and nothing else."""
    HOME.mkdir(parents=True, exist_ok=True)
    os.environ["TELLTALE_HOME"] = str(HOME)
    resolved = Path(os.environ["TELLTALE_HOME"]).expanduser().resolve()
    if resolved == REAL_HOME.resolve():
        raise SystemExit(
            f"TELLTALE_HOME resolves to {resolved}, which is the owner's store."
            " E08 runs against a copy and refuses to write to that file."
        )
    return resolved


def store() -> Any:
    return Store(HOME / "telltale.db").open()


def write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", "utf-8")


def read(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"{path}: run the earlier stage first.")
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def pair_path(capture_id: str, target: str, horizon: int) -> Path:
    return OUT / capture_id / f"{target}-H{horizon}.json"


def forecasters(options: argparse.Namespace) -> dict[str, Any]:
    """One instance per name, the model cached across pairs by E07's `_forecaster`."""
    return {name: e07._forecaster(name, options.device) for name in options.forecasters}


def placebo_row(run: dict[str, Any]) -> dict[str, Any]:
    """One placebo run as a summary keeps it: who it was, and what everyone scored."""
    return {
        "ordering": run["ordering"],
        "seed": run["placebo_seed"],
        "n_windows": run["metrics"]["n_windows"],
        "mae_mean": {
            name: entry["mae_mean"]
            for name, entry in run["metrics"]["forecasters"].items()
        },
    }


def rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def constants(device: str) -> dict[str, Any]:
    """The pre-registered numbers of design 6.12, read from the registry and printed."""
    return {
        "delta": DELTA,
        "w": W,
        "k_min": K_MIN,
        "c_min_request": C_MIN,
        "c_min_short": C_MIN_SHORT,
        "horizons": list(HORIZONS),
        "stride": "H",
        "baseline_window": BASELINE_WINDOW,
        "threshold_rule": THRESHOLD_RULE,
        "placebo_block": {str(one): placebo_block(one) for one in HORIZONS},
        "placebo_row_block": PLACEBO_ROW_BLOCK,
        "placebo_seeds": PLACEBO_SEEDS,
        "demotion_resolution": DEMOTION_RESOLUTION,
        "baselines": list(BASELINE_NAMES),
        "missingness_policy": "exclude",
        "device": device,
    }
