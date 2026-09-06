"""E16: the one-step candidate protocol on the git-backfilled change clock of four
repositories, never pooled.

E11 ran this protocol on the one repository the store then held and reached 0 windows on
every target: the change clock carried 40 rows, `merge_verification_ms` was unavailable
on all of them and `rework_within_3` was 0 on every decided row. Wave 8 made the
first-parent history of four repositories into rows (W8-T1, W8-T2), made a target with
holes forecastable over the rows where it is known (W8-T3), made a hole in the
conditioning block cost rows rather than the run (W8-F1), made the tracked base define
the lineage (W8-T4) and rescued the path-derived cells from the payload bound (W8-T5).
This runner is the first time H8 has rows to be wrong on.

Three refusals shape it and none may be relaxed to admit data.

  It never touches the owner's store. `copy` backs the source database up through a
  read-only connection into `out/run-home`, TELLTALE_HOME is pointed there first, and
  the run refuses if that path resolves to `~/.telltale`.

  It never lowers a constant. c_min, k_min, delta, w, the placebo seeds and the A block
  all come from `telltale.forecast` and are printed. A count that failed is the result.

  It pools nothing. One repository is one lineage, twelve (repository, target) rows are
  twelve answers, and the synthetic control at the end is a control ON THIS RUNNER and
  never evidence about any repository.

    uv run --extra forecast python experiments/E16/run.py --device mps
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

E16 = Path(__file__).resolve().parent
OUT = E16 / "out"
# `out/home` on the owner's checkout is the SOURCE. The copy this run reads and writes
# is `out/run-home` under this worktree, so the two names can never be the same path.
HOME = OUT / "run-home"
REAL_HOME = Path("~/.telltale").expanduser()
REPO_ROOT = E16.parents[1]
# The store the orchestrator measured on 2026-09-06: the owner's rebuilt store plus
# `import git-history` with check runs for the four repositories. Absolute because
# experiments/*/out/ is gitignored, so this worktree's own out/ starts empty.
SOURCE_DB = Path(
    "/Users/wumpinihussein/Documents/code/telltale/experiments/E16/out/home/telltale.db"
)


def _e11() -> Any:
    """E11's runner as a module, with its paths pointed at ours.

    Loaded by path rather than by `sys.path`, because both experiments name their
    runner `run.py`. Its module body calls `point_home_at_the_copy` once with E11's own
    HOME, which creates `experiments/E11/out/home` and sets TELLTALE_HOME to it; the
    three assignments below and the call underneath them are what move both to E16's.
    """
    path = E16.parent / "E11" / "run.py"
    spec = importlib.util.spec_from_file_location("e11_run", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["e11_run"] = module
    spec.loader.exec_module(module)
    module.SOURCE_DB = SOURCE_DB
    module.OUT = OUT
    module.HOME = HOME
    module.REAL_HOME = REAL_HOME
    return module


e11 = _e11()
e11.point_home_at_the_copy()

import rows as one_row  # noqa: E402

from telltale import config  # noqa: E402
from telltale import series as compiler  # noqa: E402
from telltale.forecast import CANDIDATE_SENTENCE, make  # noqa: E402
from telltale.store import Store  # noqa: E402


def _since(started: float) -> float:
    """Milliseconds since a `time.perf_counter()` mark, to one decimal."""
    return round((time.perf_counter() - started) * 1000.0, 1)


# -- the copy -------------------------------------------------------------------------


def copy() -> dict[str, Any]:
    """`out/run-home/telltale.db`, a consistent copy of the source. E11's `snapshot`.

    The source is opened `mode=ro` and read through sqlite3's backup API rather than
    copied with `cp`, which would leave the write-ahead log behind.
    """
    if not SOURCE_DB.exists():
        raise SystemExit(f"{SOURCE_DB}: no database to copy.")
    return dict(e11.snapshot())


def write(path: Path, payload: dict[str, Any]) -> None:
    e11.write(path, payload)


# -- the series -----------------------------------------------------------------------


def build(store: Store, repo_id: str) -> Any:
    """The change clock of one repository, compiled fresh and stored in the copy."""
    built = compiler.build(store, one_row.CLOCK, repo_id, one_row.POLICY)
    store.put_series(built)
    return built


# -- the control ----------------------------------------------------------------------

CONTROL_NOTE = (
    "A CONTROL ON THIS RUNNER, never evidence about any repository. The rows are a"
    " seeded random walk from tests/integration/synthetic_series.py; nothing in them"
    " was measured and no number here may be pooled with or read as anything about any"
    " repository. It exists because a runner that printed 'not assessable' for every"
    " row would print that if it were broken, and this tells the two apart."
)


def control(store: Store, forecasters: dict[str, Any]) -> dict[str, Any]:
    """The same protocol on a synthetic change series. E11's control, at E16's targets.

    Re-implemented rather than called: E11's `control` runs `CANDIDATE_TARGETS` through
    a `one_target` that carries a module-wide H = 1, and `rework_within_3_lag3` is
    registered for H = 4 alone. 60 rows and seed 3 are E11's, so the numbers below can
    be read against the paste in docs/experiments/E11.md.
    """
    sys.path.insert(0, str(REPO_ROOT / "tests" / "integration"))
    import synthetic_series

    built = synthetic_series.write_change(
        store, rows=one_row.CONTROL_ROWS, seed=one_row.CONTROL_SEED
    )
    started = time.perf_counter()
    found = {
        target: one_row.one_target(store, "synthetic", built, target, forecasters)
        for target in one_row.E16_TARGETS
    }
    return {
        "note": CONTROL_NOTE, "series_id": built.series_id,
        "n_rows": len(built.rows), "rows": one_row.CONTROL_ROWS,
        "seed": one_row.CONTROL_SEED,
        "reducer_version": built.reducer_version,
        "targets": found, "rows_table": [one_row.row(one) for one in found.values()],
        "wall_ms": _since(started),
    }  # fmt: skip


# -- the run --------------------------------------------------------------------------


def line(text: str = "") -> None:
    print(text, flush=True)


def report_target(found: dict[str, Any]) -> None:
    one = one_row.row(found)
    rate = "" if one["base_rate"] is None else f"  base rate {one['base_rate']:.4f}"
    line(f"--- {found['repository']} / {found['target']} ({found['unit']})"
         f"  H {found['horizon']}{rate} ---")  # fmt: skip
    line(f"  rows {one['rows']}  retained {one['retained']}"
         f"  excluded {one['excluded']} ({one['excluded_reason']})"
         f"  conditioned frame {one['retained_conditioned']}"
         f"  excluded_for_block {one['excluded_for_block']}"
         f" {one['excluded_for_block_by_column']}")  # fmt: skip
    if found["readiness"]["refusal"]:
        line(f"  readiness refused: {found['readiness']['refusal']}")
    for check in found["readiness"]["checks"]:
        line(f"  readiness {check['check']:12s} {check['result']:4s}"
             f"  measured {check['measured']}  needed {check['needed']}"
             f"  {check['detail']}")  # fmt: skip
    line(f"  planned windows {one['planned_windows']}"
         f"  run windows {one['n_windows']}  dropped {one['dropped']}"
         f"  excluded columns {one['excluded_columns']}")  # fmt: skip
    line(f"  E_M {one['e_m']}  E_B {one['e_b']} ({one['best_baseline']})"
         f"  E_P {one['e_p']} range {one['e_p_range']}"
         f"  W_MB {one['w_mb']}  W_MP {one['w_mp']}")  # fmt: skip
    for test in one["inequalities"] or []:
        line(f"    {test['test']:22s} {test['lhs']} vs {test['rhs']}"
             f"  {'yes' if test['holds'] else 'no'}")  # fmt: skip
    line(f"  label {one['label']}  placebo valid {one['placebo_valid']}"
         f" ({one['placebo_worse']} worse)  reason {one['label_reason']}")  # fmt: skip
    for half in (one_row.WHOLE, one_row.FIRST, one_row.SECOND):
        entry = found["pairs"].get(half)
        if entry is None:
            line(f"  pair {half:6s} did not run")
            continue
        line(f"  pair {half:6s} origins {entry['origins']}"
             f"  paired {entry['n_paired']}"
             f"  median MAE difference {entry['mae_median_difference']}"
             f"  median pinball difference"
             f" {entry['pinball_median_difference']}")  # fmt: skip
    line(f"  calibration {one['calibration']}")
    line(f"  H5 branch ({one['h5_branch']}) {one['h5_verdict']}"
         f"  {one['h5_stated'] or ''}")  # fmt: skip
    line(f"  H8 branch ({one['h8_branch']}) {one['h8_verdict']}"
         f"  {one['h8_stated'] or ''}  [reading: {one['reading']}]")  # fmt: skip
    for refused in one["refusals"]:
        line(f"  REFUSED {refused['where']}: {refused['refusal']}")
    line()


def report(summary: dict[str, Any], targets: list[dict[str, Any]]) -> None:
    line(f"E16  {summary['started_at']}  device {summary['device']}")
    line(one_row.RULE)
    line()
    for facts in summary["series"]:
        line(f"{facts['repository']:10s} series {facts['series_id']}"
             f"  {facts['n_rows']} rows  changepoints {facts['changepoints']}"
             f"  cohort {json.dumps(facts['cohort'], sort_keys=True)}")  # fmt: skip
    line()
    for found in targets:
        report_target(found)
    line(summary["sentence"])
    line()
    if "n_rows" in summary["control"]:
        control_lines(summary["control"])
    else:
        line(f"control: {summary['control'].get('note')}")
    line()


def control_lines(found: dict[str, Any]) -> None:
    line(f"control (synthetic, {found['n_rows']} rows, seed {found['seed']}):"
         f" {found['series_id']}  wall {found['wall_ms']} ms")  # fmt: skip
    line(f"  {found['note']}")
    for one in found["rows_table"]:
        line(f"  {one['target']:26s} H5 ({one['h5_branch']}) H8 ({one['h8_branch']})"
             f"  windows {one['n_windows']}  paired {one['n_paired'][one_row.WHOLE]}"
             f"  median MAE difference {one['paired'][one_row.WHOLE]}"
             f"  label {one['label']}  placebo valid {one['placebo_valid']}"
             f"  reading {one['reading']}")  # fmt: skip


def control_headline(found: dict[str, Any]) -> dict[str, Any]:
    """The control without its windows: summary.json keeps the headline, and
    `out/control/control.json` keeps everything."""
    return {key: value for key, value in found.items() if key != "targets"}


def parse_options() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="E16 candidate protocol, four lineages"
    )
    parser.add_argument("--device", default="cpu", choices=("cpu", "mps"))
    parser.add_argument(
        "--repo", default="", help="only these repositories, comma list"
    )
    parser.add_argument("--target", default="", help="only these targets, comma list")
    parser.add_argument(
        "--forecasters",
        default=",".join(one_row.FULL_FORECASTERS),
        help="registry names; the stub-only dry run is"
        f" --forecasters {','.join(one_row.STUB_FORECASTERS)}",
    )
    parser.add_argument(
        "--no-control", action="store_true", help="skip the synthetic control"
    )
    options = parser.parse_args()
    options.forecasters = [one for one in options.forecasters.split(",") if one]
    options.repo = [one for one in options.repo.split(",") if one]
    options.target = [one for one in options.target.split(",") if one]
    return options


def chosen(options: argparse.Namespace) -> tuple[list[tuple[str, str]], list[str]]:
    repos: list[tuple[str, str]] = [
        one for one in one_row.REPOS if not options.repo or one[0] in options.repo
    ]
    targets = [
        one
        for one in one_row.E16_TARGETS
        if not options.target or one in options.target
    ]
    if not repos or not targets:
        raise SystemExit(
            f"--repo {options.repo} --target {options.target}: nothing to run"
        )
    return repos, targets


def main() -> int:
    options = parse_options()
    started = datetime.now(UTC).isoformat(timespec="seconds")
    wall = time.perf_counter()
    repos, targets = chosen(options)
    # The model is named in the run whatever ran: a paired difference is about ONE
    # forecaster, and a dry run scores MODEL only if the caller asked for it.
    copied = copy()
    store = Store(config.db_path()).open()
    table: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []
    found: list[dict[str, Any]] = []
    try:
        forecasters = {name: make(name, options.device) for name in options.forecasters}
        one_row.choose_model(options.forecasters)
        for repo, repo_id in repos:
            built = build(store, repo_id)
            facts = e11.series_facts(built)
            facts["repository"] = repo
            series.append(facts)
            write(OUT / repo / "series.json", facts)
            for target in targets:
                one = one_row.one_target(store, repo, built, target, forecasters)
                write(OUT / repo / f"{target}.json", one)
                found.append(one)
                table.append(one_row.row(one))
                report_target(one)
        control_found = (
            {"note": "skipped with --no-control"}
            if options.no_control
            else control(store, forecasters)
        )
    finally:
        store.close()
    summary = {
        "experiment": "E16",
        "started_at": started,
        "device": options.device,
        "rule": one_row.RULE,
        "constants": one_row.constants(options.device, list(forecasters)),
        "snapshot": copied,
        "home": str(HOME),
        "command": list(sys.argv),
        "repositories": [{"repository": name, "repo_id": one} for name, one in repos],
        "targets": list(targets),
        "series": series,
        "rows": table,
        "forecasters": e11.declared(forecasters),
        "control": control_found,
        "sentence": CANDIDATE_SENTENCE,
    }
    summary["wall_ms"] = round((time.perf_counter() - wall) * 1000.0, 1)
    if not options.no_control:
        write(OUT / "control" / "control.json", control_found)
        summary["control"] = control_headline(control_found)
    report(summary, found)
    write(OUT / "summary.json", summary)
    line(f"wall {summary['wall_ms']} ms  home {HOME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
