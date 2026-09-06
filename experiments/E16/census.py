"""Post-wave-7 census: E13's readiness census, re-run on a store rebuilt with the wave
7 reducers (W7-T1 Codex request clock, W7-T2 backfilled fingerprints, W7-T3 derived
request duration and coverage-named variants), at the request clock's c_min of 32.

E13's census used c_min 16 (the minimum over every registered target) and counted 930
Claude imports; the E13 run applied 32 and assessed 545. This census applies 32 from
the start so its counts are the run's counts. It reads the rebuilt copy named by
TELLTALE_HOME (experiments/E16/out/rebuilt) and copies it under
experiments/E16/out/home, so E13's own copy and its persisted runs stay untouched.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "E07"))
import run as e07  # noqa: E402

from telltale.forecast import TARGETS  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "e13_census", HERE.parent / "E13" / "census.py"
)
assert _spec is not None
assert _spec.loader is not None
e13 = importlib.util.module_from_spec(_spec)
sys.modules["e13_census"] = e13
_spec.loader.exec_module(e13)

e13.OUT = HERE / "out"
e13.C_MIN = min(
    TARGETS[target].c_min for target in TARGETS if TARGETS[target].clock == "request"
)
e07.OUT, e07.HOME = HERE / "out", HERE / "out" / "home"

if __name__ == "__main__":
    raise SystemExit(e13.main())
