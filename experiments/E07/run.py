"""E07: what can the request clock of this build's own sessions be forecast for?

Wave 2 ends with a laboratory that has never been pointed at real data. W1-T6 built the
rolling-origin backtester and scored it on a synthetic random walk; W2-T5 built the
readiness checklist and measured that 52 model requests is the shortest request clock
that yields k_min = 20 windows at H = 1, and 112 at H = 4; W2-T7 replaced the column
whose None cells were dropping every window of every real capture. This script is the
first run of all of it over the sessions that built Telltale.

Three refusals shape it, and each is a defect this repository exists to prevent.

  It never touches the owner's store. The first thing it does is take a read-only
  snapshot of `$TELLTALE_HOME/telltale.db` into `out/home/telltale.db` and point
  TELLTALE_HOME at that copy; every rebuild, series, backtest and forecast_runs row
  after that lands in the copy. It refuses to run when TELLTALE_HOME then resolves to
  the real `~/.telltale`.

  It never widens the pre-registered constants to admit more data. delta, w, k_min,
  c_min, H, the stride, the baseline window and the threshold rule come from
  `telltale.forecast` and are printed, never computed. A capture that fails readiness
  is excluded and listed with the first check that failed, its measured number and the
  number it needed.

  It never labels a run beyond what it can support. Design 6.12's decision rule has two
  halves: the baseline comparison, which this run makes, and the chronology placebo,
  which needs `block_shuffle` from W3-T2 and does not exist. So a run is labelled
  "baseline sufficient" or "not baseline sufficient at this n, placebo pending", with
  both inequalities and their values printed beside the label, and never "temporal
  evolution" or "conditional prediction".

Every number the write-up quotes is in `out/summary.json` or in one of the per-run
files beside it, and every metric in those files was read back out of the
`forecast_runs` row the backtest wrote, so it is traceable to a row anyone can query.

Usage:
    uv run --extra forecast python experiments/E07/run.py
    uv run --extra forecast python experiments/E07/run.py --device mps
    uv run --extra forecast python experiments/E07/run.py --forecasters persistence,echo
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from decide import decide, mae_table

import telltale.measures  # noqa: F401 - importing it registers the reducers
from telltale import config
from telltale import series as compiler
from telltale.forecast import (
    BASELINE_NAMES,
    BASELINE_WINDOW,
    C_MIN,
    DEFAULT_FORECASTERS,
    DELTA,
    DEVICES,
    HORIZONS,
    K_MIN,
    TARGETS,
    THRESHOLD_RULE,
    TIMESFM,
    W,
    make,
    readiness,
)
from telltale.forecast import backtest as backtester
from telltale.report import render_table
from telltale.store import Store

E07_DIR = Path(__file__).resolve().parent
REPO_ROOT = E07_DIR.parents[1]
OUT = E07_DIR / "out"
HOME = OUT / "home"
REAL_HOME = Path("~/.telltale").expanduser()
# Read at import, BEFORE `point_home_at_the_copy` overwrites the variable: this is the
# store being snapshotted, and it is the only path this script ever opens read-only.
SOURCE_DB = (
    Path(os.environ.get("TELLTALE_HOME") or "~/.telltale").expanduser() / "telltale.db"
)

# The cohort. Design 6.12 forbids pooling across providers and there is nothing else on
# this disk to pool with: a capture that is not a launcher capture of the build, or not
# this provider, is excluded by name with the reason in the exclusion table.
EXPERIMENT = "build"
PROVIDER = "claude"

# Why the pooled rows carry no lead-time number. Every capture has its own tau, so the
# concatenation has none, and a threshold borrowed from one capture scores every other
# capture against a level nobody measured on it.
POOLED_NO_TAU = (
    "tau is q80 of each capture's own first c_min rows, so the cohort has no single"
    " threshold and lead time is not assessable over pooled windows"
)

_SUMMARY = (
    "capture",
    "task",
    "target",
    "h",
    "n_windows",
    "e_m",
    "e_b",
    "best_baseline",
    "w_mb",
    "label",
)
_READINESS = ("capture", "task", "requests", "target", "h", "verdict", "first_failure")


# -- the copy, and the refusal that protects the owner's store ------------------------


def point_home_at_the_copy() -> Path:
    """Everything after this call reads and writes `out/home`, and nothing else.

    The check is made AFTER the variable is set rather than before, because what has
    to be true is that the store the rest of this script opens is not the owner's.
    """
    HOME.mkdir(parents=True, exist_ok=True)
    os.environ["TELLTALE_HOME"] = str(HOME)
    resolved = config.home().resolve()
    if resolved == REAL_HOME.resolve():
        raise SystemExit(
            f"TELLTALE_HOME resolves to {resolved}, which is the owner's store."
            " E07 runs against a copy and refuses to write to that file."
        )
    return resolved


def copy_store(source: Path, dest: Path) -> dict[str, Any]:
    """A consistent snapshot of a store that is being written to right now.

    `cp` of `telltale.db` alone leaves the write-ahead log behind, and this session is
    itself being recorded into the source: W2-T7's copy of the same file was taken that
    way. sqlite3's backup API reads through the WAL, and the source connection is
    opened `mode=ro`, so no code path here can write to it.
    """
    if not source.exists():
        raise SystemExit(f"{source}: no database to copy.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    started = time.perf_counter()
    reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    writer = sqlite3.connect(dest)
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()
    return {
        "source": str(source),
        "dest": str(dest),
        "taken_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_bytes": source.stat().st_size,
        "copy_bytes": dest.stat().st_size,
        "wall_s": round(time.perf_counter() - started, 3),
    }


# -- who is in the cohort -------------------------------------------------------------


_CENSUS = """
select c.capture_id,
       s.payload,
       (select count(*) from activities a
         where a.capture_id = c.capture_id and a.activity_type = 'model_request'),
       (select group_concat(distinct json_extract(e.payload, '$.runtime_version'))
          from observations e
         where e.capture_id = c.capture_id
           and e.observation_type = 'telltale.environment'),
       (select group_concat(distinct json_extract(e.payload, '$.model'))
          from observations e
         where e.capture_id = c.capture_id
           and e.observation_type = 'telltale.environment')
from captures c
left join observations s
  on s.capture_id = c.capture_id
 and s.observation_type = 'telltale.capture_started'
order by 3 desc, 1
"""


def census(db: Path) -> list[dict[str, Any]]:
    """Every capture on the copy with its request-clock length and its launch record.

    The join is the brief's: model_request activities grouped by capture, against the
    capture_started payload that carries experiment, task_id and attempt. A capture
    with no capture_started row is a replayed fixture or an imported transcript, and
    the left join is what keeps it visible instead of silently absent.

    runtime_version and model are the DISTINCT set each capture carries, not the first
    one found: a capture that drifted mid-session shows both, and one whose launcher
    never resolved a version shows None rather than a value borrowed from a neighbour.
    """
    reader = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = reader.execute(_CENSUS).fetchall()
    finally:
        reader.close()
    return [_census_row(one) for one in rows]


def _census_row(row: tuple[Any, ...]) -> dict[str, Any]:
    capture_id, payload, requests, runtimes, models = row
    launch = json.loads(payload) if payload else None
    return {
        "capture_id": str(capture_id),
        "requests": int(requests),
        "launched": launch is not None,
        "experiment": None if launch is None else launch.get("experiment"),
        "task_id": None if launch is None else launch.get("task_id"),
        "attempt": None if launch is None else launch.get("attempt"),
        "provider": None if launch is None else launch.get("provider"),
        "content_level": None if launch is None else launch.get("content_level"),
        "runtime_versions": None if runtimes is None else str(runtimes).split(","),
        "models": None if models is None else str(models).split(","),
    }


def cohort_refusal(row: dict[str, Any]) -> str | None:
    """Why this capture is not in the cohort, or None when it is."""
    if not row["launched"]:
        return (
            "no telltale.capture_started observation: a replayed fixture or an"
            " imported transcript, not a launcher capture"
        )
    if row["experiment"] != EXPERIMENT:
        return f"experiment {row['experiment']!r} is not {EXPERIMENT!r}"
    if row["provider"] != PROVIDER:
        return (
            f"provider {row['provider']!r}: the cohort is {PROVIDER} and design 6.12"
            " never pools across providers"
        )
    return None


# -- one backtest ---------------------------------------------------------------------


def build_series(store: Any, capture_id: str) -> Any:
    built = compiler.build(store, "request", capture_id, "exclude")
    store.put_series(built)
    return built


def readiness_of(built: Any, target: str, horizon: int) -> dict[str, Any]:
    """The eight lines of design 6.12, and the first one that failed."""
    checks = readiness.check(built, target, horizon)
    failed = [item for item in checks if not item.passed]
    return {
        "ready": not failed,
        "checks": [
            {
                "check": item.name,
                "passed": item.passed,
                "measured": item.measured,
                "needed": item.needed,
                "detail": item.detail,
            }
            for item in checks
        ],
        "first_failure": None if not failed else failed[0].stated(),
        "first_failure_detail": None if not failed else failed[0].detail,
    }


def backtest_once(
    store: Any, built: Any, target: str, horizon: int, names: list[str], device: str
) -> dict[str, Any]:
    """Run, persist, and read the run back out of the store. Never out of memory."""
    forecasters = {name: _forecaster(name, device) for name in names}
    started = time.perf_counter()
    run = backtester.run(built, target, horizon, forecasters)
    wall_ms = (time.perf_counter() - started) * 1000.0
    run_id = backtester.persist(store, run)
    stored = _stored_run(store, built.series_id, run_id)
    return {
        "forecast_run_id": run_id,
        "wall_ms": round(wall_ms, 3),
        "report": backtester.report(run),
        "stored": stored,
    }


_MODELS: dict[tuple[str, str], Any] = {}


def _forecaster(name: str, device: str) -> Any:
    """One instance per (name, device). The checkpoint is loaded once per process."""
    if name != TIMESFM:
        return make(name, device)
    key = (name, device)
    if key not in _MODELS:
        _MODELS[key] = make(name, device)
    return _MODELS[key]


def _stored_run(store: Any, series_id: str, run_id: str) -> dict[str, Any]:
    for row in store.forecast_runs(series_id):
        if row["forecast_run_id"] == run_id:
            return dict(row)
    raise SystemExit(f"{run_id}: written and not readable back from forecast_runs")


def run_one(
    store: Any,
    built: Any,
    capture: dict[str, Any],
    pair: tuple[str, int],
    options: argparse.Namespace,
) -> dict[str, Any]:
    """One backtest, the file that records it in full, and the row the summary keeps."""
    target, horizon = pair
    outcome = backtest_once(
        store, built, target, horizon, options.forecasters, options.device
    )
    stored = outcome["stored"]
    windows = list(stored["windows"]["retained"])
    metrics = dict(stored["metrics"])
    record = {
        "capture": capture,
        "series_id": built.series_id,
        "cohort": built.cohort,
        "target": target,
        "horizon": horizon,
        "stride": horizon,
        "forecast_run_id": outcome["forecast_run_id"],
        "read_back_from": "the forecast_runs row of the copied store",
        "wall_ms": outcome["wall_ms"],
        "n_windows": metrics["n_windows"],
        "tau": metrics["tau"],
        "dropped_counts": stored["windows"]["dropped_counts"],
        "constants": stored["scenario"]["constants"],
        "covariates": stored["scenario"]["covariates"],
        "forecasters": stored["forecasters"],
        "warnings": stored["warnings"],
        "claim_class": stored["claim_class"],
        "metrics": mae_table(metrics, windows),
        "decision": decide(metrics, windows, options.model),
    }
    return _filed(record, OUT / capture["capture_id"] / f"{target}-H{horizon}.json")


def pooled_row(
    store: Any, records: list[dict[str, Any]], pair: tuple[str, int], model: str
) -> dict[str, Any]:
    """Every window of the cohort concatenated, scored once. Design 6.12's metrics.

    tau is q80 of each capture's own first c_min rows, so the cohort has no single
    threshold. `backtest.metrics` is called with tau None, and the lead-time cell of
    every forecaster is then overwritten with the reason THIS caller has, because the
    module's own None-tau string names a gap in the head of a column and that is not
    what happened here.
    """
    windows: list[dict[str, Any]] = []
    for row in records:
        stored = _stored_run(store, row["series_id"], row["forecast_run_id"])
        windows += list(stored["windows"]["retained"])
    metrics = backtester.metrics(windows, None)
    scored = mae_table(metrics, windows)
    for entry in scored.values():
        entry["lead_time"] = {"assessable": False, "reason": POOLED_NO_TAU}
    target, horizon = pair
    record = {
        "capture": {"capture_id": "pooled", "task_id": f"{len(records)} captures"},
        "captures": [row["capture_id"] for row in records],
        "forecast_run_ids": [row["forecast_run_id"] for row in records],
        "target": target,
        "horizon": horizon,
        "stride": horizon,
        "n_windows": len(windows),
        "tau": None,
        "tau_reason": POOLED_NO_TAU,
        "metrics": scored,
        "decision": decide(metrics, windows, model),
    }
    return _filed(record, OUT / "pooled" / f"{target}-H{horizon}.json")


# -- output ---------------------------------------------------------------------------


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", "utf-8")


def _filed(record: dict[str, Any], path: Path) -> dict[str, Any]:
    """Write the whole record, and return the row summary.json carries.

    The summary keeps a row per run rather than the run: the full sorted MAE list of
    every forecaster lives in the file named here, and a summary carrying them too is
    the same numbers in two places, one of which nobody updates.
    """
    _write(path, record)
    return {
        "capture_id": record["capture"]["capture_id"],
        "task_id": record["capture"]["task_id"],
        "series_id": record.get("series_id"),
        "forecast_run_id": record.get("forecast_run_id"),
        "captures": record.get("captures"),
        "target": record["target"],
        "horizon": record["horizon"],
        "n_windows": record["n_windows"],
        "tau": record["tau"],
        "wall_ms": record.get("wall_ms"),
        "file": str(path.relative_to(REPO_ROOT)),
        "declared": record.get("forecasters"),
        "metrics": {name: _compact(entry) for name, entry in record["metrics"].items()},
        "decision": record["decision"],
    }


def _compact(entry: dict[str, Any]) -> dict[str, Any]:
    """One forecaster's row without the full sorted list, which stays in the file."""
    spread = entry["mae"]
    return {
        **{key: value for key, value in entry.items() if key != "mae"},
        "mae_median": spread["median"],
        "mae_mad_scaled": spread["mad_scaled"],
        "mae_iqr": spread["iqr"],
        "mae_min": spread["min"],
        "mae_max": spread["max"],
    }


def _summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "capture": row["capture_id"],
            "task": row["task_id"],
            "target": row["target"],
            "h": row["horizon"],
            "n_windows": row["n_windows"],
            "e_m": _round(row["decision"]["E_M"]),
            "e_b": _round(row["decision"]["E_B"]),
            "best_baseline": row["decision"]["best_baseline"],
            "w_mb": _round(row["decision"]["W_MB"]),
            "label": row["decision"]["label"],
        }
        for row in rows
    ]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def constants(model_ran: bool, device: str) -> dict[str, Any]:
    """The pre-registered numbers of design 6.12, read from the registry and printed."""
    return {
        "delta": DELTA,
        "w": W,
        "k_min": K_MIN,
        "c_min": C_MIN,
        "horizons": list(HORIZONS),
        "stride": "H",
        "baseline_window": BASELINE_WINDOW,
        "threshold_rule": THRESHOLD_RULE,
        "relative_resolution": 0.25,
        "baselines": list(BASELINE_NAMES),
        "missingness_policy": "exclude",
        "device": device,
        "timesfm_ran": model_ran,
    }


def print_report(summary: dict[str, Any]) -> None:
    print(
        f"E07  {summary['captures_on_disk']} captures on the copy,"
        f" {summary['cohort_size']} in the build cohort"
    )
    print(
        "constants: "
        + "  ".join(f"{key} {value}" for key, value in summary["constants"].items())
    )
    print("\nreadiness, every capture and every target:")
    print(render_table(summary["readiness_table"], _READINESS))
    print("\nbacktests, per capture then pooled:")
    print(
        render_table(
            _summary_rows(summary["runs"]) + _summary_rows(summary["pooled"]), _SUMMARY
        )
    )
    for line in summary["licenses"]:
        print(line)
    print(f"\nwall {summary['wall_s']} s")


# -- the whole run --------------------------------------------------------------------


def pairs() -> list[tuple[str, int]]:
    """Every (target, horizon) the registry allows, in the order the tables print."""
    return [
        (target, horizon)
        for target in sorted(TARGETS)
        for horizon in TARGETS[target].horizons
    ]


def load_model(options: argparse.Namespace) -> str | None:
    """Load the checkpoint once, or return the reason it did not load.

    A missing extra is a STOP condition in the brief: the baselines and the stub still
    run and the headline says the model was absent. It is not a reason to write no
    decision file.
    """
    if TIMESFM not in options.forecasters:
        return f"{TIMESFM} is not in --forecasters"
    try:
        _forecaster(TIMESFM, options.device)
    except Exception as failure:
        options.forecasters = [name for name in options.forecasters if name != TIMESFM]
        return f"{type(failure).__name__}: {failure}"
    return None


def survey(store: Any, kept: list[dict[str, Any]]) -> Any:
    """Build every cohort capture's series and check readiness for every pair.

    Yields (capture, series, ready pairs, readiness rows). A capture whose series
    cannot be compiled at all is yielded with the compiler's own words rather than a
    guess about why.
    """
    for capture in kept:
        try:
            built = build_series(store, capture["capture_id"])
        except compiler.Refused as refused:
            yield capture, None, [], [_unbuilt(capture, str(refused))]
            continue
        rows, ready = [], []
        for target, horizon in pairs():
            found = readiness_of(built, target, horizon)
            rows.append(_readiness_row(capture, target, horizon, found))
            if found["ready"]:
                ready.append((target, horizon))
        yield capture, built, ready, rows


def _readiness_row(
    capture: dict[str, Any], target: str, horizon: int, found: dict[str, Any]
) -> dict[str, Any]:
    return {
        "capture": capture["capture_id"],
        "task": capture["task_id"],
        "requests": capture["requests"],
        "target": target,
        "h": horizon,
        "verdict": "ready" if found["ready"] else "NOT ready",
        "first_failure": found["first_failure"] or "-",
        "checks": found["checks"],
    }


def _unbuilt(capture: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "capture": capture["capture_id"],
        "task": capture["task_id"],
        "requests": capture["requests"],
        "target": "-",
        "h": "-",
        "verdict": "no series",
        "first_failure": reason,
        "checks": [],
    }


def execute(store: Any, kept: list[dict[str, Any]], options: argparse.Namespace) -> Any:
    """The per-capture backtests and the readiness table they came from."""
    runs: list[dict[str, Any]] = []
    readiness_table: list[dict[str, Any]] = []
    for capture, built, ready, rows in survey(store, kept):
        readiness_table += rows
        for pair in ready:
            runs.append(run_one(store, built, capture, pair, options))
    return runs, readiness_table


def pool(store: Any, runs: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for run in runs:
        grouped.setdefault((run["target"], run["horizon"]), []).append(run)
    return [
        pooled_row(store, records, pair, model)
        for pair, records in sorted(grouped.items())
    ]


def licenses(runs: list[dict[str, Any]]) -> list[str]:
    """The weights licence, once per checkpoint. Design 6.12 prints it every time."""
    found = {
        f"weights: {entry['checkpoint']} under {entry['license']}"
        f" (research use, not production), device {entry['device']},"
        f" padding_mode {entry['padding_mode']}"
        for run in runs
        for entry in run["declared"] or []
        if entry["license"]
    }
    return sorted(found)


def main() -> int:
    parser = argparse.ArgumentParser(description="E07 request-clock backtests")
    parser.add_argument("--device", default="cpu", choices=DEVICES)
    parser.add_argument(
        "--forecasters", default=",".join([*DEFAULT_FORECASTERS, TIMESFM])
    )
    options = parser.parse_args()
    options.forecasters = [one for one in options.forecasters.split(",") if one]
    options.model = TIMESFM

    started = time.perf_counter()
    home = point_home_at_the_copy()
    copied = copy_store(SOURCE_DB, home / "telltale.db")
    store = Store(home / "telltale.db").open()
    try:
        rebuilt = store.rebuild()
        rows = census(home / "telltale.db")
        kept = [row for row in rows if cohort_refusal(row) is None]
        excluded = [
            {**row, "reason": cohort_refusal(row)}
            for row in rows
            if cohort_refusal(row) is not None
        ]
        model_error = load_model(options)
        runs, readiness_table = execute(store, kept, options)
        pooled = pool(store, runs, options.model)
    finally:
        store.close()

    summary = {
        "experiment": "E07",
        "copy": copied,
        "home": str(home),
        "rebuilt_captures": rebuilt,
        "captures_on_disk": len(rows),
        "cohort_size": len(kept),
        "cohort": kept,
        "constants": constants(model_error is None, options.device),
        "forecasters": options.forecasters,
        "timesfm_error": model_error,
        "excluded_captures": excluded,
        "readiness_table": readiness_table,
        "runs": runs,
        "pooled": pooled,
        "licenses": licenses(runs),
        "wall_s": round(time.perf_counter() - started, 3),
    }
    _write(OUT / "summary.json", summary)
    print_report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
