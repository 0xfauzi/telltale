"""E13b: E13's run over the Codex import cohort, which W7-T1 gave a request clock.

E13 refused every Codex capture by name ("no request clock (turn activities only)") and
measured why. After W7-T1 a rollout's `event_msg/token_count` is one model_request each,
so the same runner, with the provider constant flipped and the readiness checklist as
amended by W7-T3 (an `unavailable` column is excluded by name rather than refusing the
capture), scores the cohort E13 could not. Nothing else in E13's mechanics changes: the
copy, the census, the readiness gate, the backtest, the persisted run, the per-capture
files and the pooled rows are E13's, imported by path. The pre-registered rule is in
docs/experiments/E13b.md; the source store is the copy named by TELLTALE_HOME.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "E07"))
import run as e07  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "e13_run", HERE.parent / "E13" / "run.py"
)
assert _spec is not None
assert _spec.loader is not None
e13 = importlib.util.module_from_spec(_spec)
sys.modules["e13_run"] = e13
_spec.loader.exec_module(e13)

e13.PROVIDER = "codex"
e13.OUT = HERE / "out"
e07.OUT, e07.HOME = HERE / "out", HERE / "out" / "home"

if __name__ == "__main__":
    raise SystemExit(e13.main())
