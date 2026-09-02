"""E08: does anything the request clock can be forecast for survive its own placebo?

E07 scored TimesFM-3 against four baselines on the sessions that built Telltale and
labelled all 19 of its pairs "baseline sufficient". It could not do more: design 6.12's
rule has a second half, the chronology placebo, and `block_shuffle` did not exist. W3-T2
built it. This run is the same experiment with the missing half attached, on a fresh
copy of a store that has grown since.

Three refusals shape it, and they are E07's, reused rather than restated.

  It never touches the owner's store. `copy_store` takes a `.backup` snapshot of
  `$TELLTALE_HOME/telltale.db` into `out/home/telltale.db` and TELLTALE_HOME is
  pointed at the copy before anything else runs; the run refuses when TELLTALE_HOME
  then resolves to the real `~/.telltale`.

  It never widens a pre-registered constant to admit more data. delta, w, k_min, c_min,
  c_min_short, the horizons, the stride, the baseline window, the placebo seeds and the
  block sizes come from `telltale.forecast` and are printed. A capture or a clock that
  fails readiness is excluded and listed with the first check that failed, its measured
  number and the number it needed.

  It never labels a run its own control refused. `placebo.sentinel` demands that
  persistence is worse under every one of the ten placebo runs; where it is not, the
  label is "not assessable" and the reason is stored beside it.

Every number the write-up quotes is in `out/summary.json` or in one of the per-pair
files beside it, and every metric in those files was read back out of a `forecast_runs`
row.

Usage, one stage per invocation so a long run resumes from out/:

    uv run --extra forecast python experiments/E08/run.py --stage copy
    uv run --extra forecast python experiments/E08/run.py --stage survey
    uv run --extra forecast python experiments/E08/run.py --stage pair \
        --capture cap_... --target output_tokens --horizon 1
    uv run --extra forecast python experiments/E08/run.py --stage pairs
    uv run --extra forecast python experiments/E08/run.py --stage pooled
    uv run --extra forecast python experiments/E08/run.py --stage lineage
    uv run --extra forecast python experiments/E08/run.py --stage summary
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

import lineage
import pooled
from shared import (
    HOME,
    HORIZONS,
    OUT,
    REPO_ROOT,
    REQUEST_CLOCK,
    constants,
    e07,
    forecasters,
    pair_path,
    placebo_row,
    point_home_at_the_copy,
    read,
    rounded,
    write,
)
from shared import store as open_store

from telltale import series as compiler
from telltale.forecast import DEFAULT_FORECASTERS, DEVICES, TARGETS, TIMESFM
from telltale.forecast import backtest as backtester
from telltale.forecast import placebo as placebos
from telltale.report import render_table

_SUMMARY = ("capture", "task", "target", "h", "n_windows", "e_m", "e_b", "e_p",
            "w_mb", "w_mp", "valid", "label")  # fmt: skip
_READINESS = ("capture", "task", "requests", "target", "h", "verdict", "first_failure")


# -- stage 1: the copy and the census -------------------------------------------------


def stage_copy(options: argparse.Namespace) -> dict[str, Any]:
    """Snapshot the owner's store, rebuild every capture on the copy, census it."""
    started = time.perf_counter()
    copied = e07.copy_store(e07.SOURCE_DB, HOME / "telltale.db")
    store = open_store()
    try:
        rebuild_started = time.perf_counter()
        rebuilt = store.rebuild()
        rebuild_s = round(time.perf_counter() - rebuild_started, 3)
    finally:
        store.close()
    rows = e07.census(HOME / "telltale.db")
    payload = {
        "copy": copied,
        "home": str(HOME),
        "source_home": str(e07.SOURCE_DB),
        "rebuilt_captures": rebuilt,
        "rebuild_s": rebuild_s,
        "captures_on_disk": len(rows),
        "captures": rows,
        "excluded": [
            {"capture_id": row["capture_id"], "reason": e07.cohort_refusal(row)}
            for row in rows
            if e07.cohort_refusal(row) is not None
        ],
        "wall_s": round(time.perf_counter() - started, 3),
        "device": options.device,
    }
    write(OUT / "census.json", payload)
    kept = len(rows) - len(payload["excluded"])
    print(f"copy: {len(rows)} captures, {kept} in the build cohort,"
          f" rebuild {rebuild_s} s, stage {payload['wall_s']} s")  # fmt: skip
    return payload


def cohort(census: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in census["captures"] if e07.cohort_refusal(row) is None]


# -- stage 2: the request-clock series and the readiness table ------------------------


def request_pairs() -> list[tuple[str, int]]:
    """Every (target, horizon) the registry allows ON THE REQUEST CLOCK.

    E07's `pairs()` took every entry of TARGETS, which held three. W3-T2 added six
    change-clock entries, and `backtest.registered` refuses a change-clock target on a
    request-clock series, so the filter is the registry's own `clock` field.
    """
    return [
        (target, horizon)
        for target in sorted(TARGETS)
        if TARGETS[target].clock == REQUEST_CLOCK
        for horizon in TARGETS[target].horizons
    ]


def stage_survey(options: argparse.Namespace) -> dict[str, Any]:
    """Build one request-clock series per cohort capture and check every pair."""
    started = time.perf_counter()
    census = read(OUT / "census.json")
    store = open_store()
    entries = []
    try:
        for capture in cohort(census):
            entries.append(_surveyed(store, capture))
    finally:
        store.close()
    payload = {
        "cohort_size": len(entries),
        "captures": entries,
        "pairs": [list(pair) for pair in request_pairs()],
        "ready_pairs": [
            [entry["capture"]["capture_id"], row["target"], row["h"]]
            for entry in entries
            for row in entry["readiness"]
            if row["verdict"] == "ready"
        ],
        "wall_s": round(time.perf_counter() - started, 3),
        "device": options.device,
    }
    write(OUT / "survey.json", payload)
    print(render_table([row for entry in entries for row in entry["readiness"]],
                       _READINESS))  # fmt: skip
    print(f"\n{len(payload['ready_pairs'])} ready pairs,"
          f" stage {payload['wall_s']} s")  # fmt: skip
    return payload


def _surveyed(store: Any, capture: dict[str, Any]) -> dict[str, Any]:
    """One capture: its series, and the eight checks for every request-clock pair."""
    try:
        built = e07.build_series(store, capture["capture_id"])
    except compiler.Refused as refused:
        failed = _row(capture, "-", "-", "no series", str(refused))
        return {
            "capture": capture,
            "series_id": None,
            "refused": str(refused),
            "readiness": [failed],
        }
    rows = []
    for target, horizon in request_pairs():
        found = e07.readiness_of(built, target, horizon)
        rows.append({
            **_row(capture, target, horizon,
                   "ready" if found["ready"] else "NOT ready",
                   found["first_failure"] or "-"),
            "checks": found["checks"],
        })  # fmt: skip
    return {
        "capture": capture,
        "series_id": built.series_id,
        "rows": len(built.rows),
        "changepoints": list(built.changepoints),
        "refused": None,
        "readiness": rows,
    }


def _row(
    capture: dict[str, Any], target: Any, horizon: Any, verdict: str, failure: str
) -> dict[str, Any]:
    return {
        "capture": capture["capture_id"],
        "task": capture["task_id"],
        "requests": capture["requests"],
        "target": target,
        "h": horizon,
        "verdict": verdict,
        "first_failure": failure,
    }


# -- stage 3: one pair, true order and its ten placebo runs ---------------------------


def stage_pair(
    store: Any,
    entry: dict[str, Any],
    pair: tuple[str, int],
    options: argparse.Namespace,
) -> dict[str, Any]:
    """The true-order backtest, the ten placebos, the validity check and the label."""
    target, horizon = pair
    capture = entry["capture"]
    started = time.perf_counter()
    built = store.series(entry["series_id"])
    truth = e07.backtest_once(
        store, built, target, horizon, options.forecasters, options.device
    )
    spec = backtester.registered(built, target, horizon)
    restored = placebos.restored(truth["stored"], built, spec.unit)
    placebo_started = time.perf_counter()
    found = placebos.paired(
        built, target, horizon, forecasters(options), options.model, truth=restored
    )
    placebo_ms = round((time.perf_counter() - placebo_started) * 1000.0, 3)
    run_ids = placebos.store_all(store, found)
    record = _pair_record(entry, capture, pair, truth, found, run_ids)
    record["wall_ms"] = {
        "true_order_backtest": truth["wall_ms"],
        "placebo_runs_total": placebo_ms,
        "pair_total": round((time.perf_counter() - started) * 1000.0, 3),
        "per_forecaster": _walls(found),
    }
    write(pair_path(capture["capture_id"], target, horizon), record)
    return record


def _pair_record(
    entry: dict[str, Any],
    capture: dict[str, Any],
    pair: tuple[str, int],
    truth: dict[str, Any],
    found: dict[str, Any],
    run_ids: list[str],
) -> dict[str, Any]:
    target, horizon = pair
    stored = truth["stored"]
    windows = list(stored["windows"]["retained"])
    metrics = dict(stored["metrics"])
    return {
        "capture": capture,
        "series_id": entry["series_id"],
        "target": target,
        "horizon": horizon,
        "stride": horizon,
        "forecast_run_id": truth["forecast_run_id"],
        "placebo_run_ids": run_ids,
        "read_back_from": "the forecast_runs row of the copied store",
        "n_windows": metrics["n_windows"],
        "tau": metrics["tau"],
        "dropped_counts": stored["windows"]["dropped_counts"],
        "constants": stored["scenario"]["constants"],
        "covariates": stored["scenario"]["covariates"],
        "forecasters": stored["forecasters"],
        "warnings": stored["warnings"],
        "claim_class": stored["claim_class"],
        "metrics": e07.mae_table(metrics, windows),
        "placebo": found["definition"],
        "sentinel": found["sentinel"],
        "placebo_metrics": [placebo_row(one) for one in found["placebos"]],
        "decision": found["decision"].as_dict(),
        "printed": placebos.report(found),
    }


def _walls(found: dict[str, Any]) -> dict[str, Any]:
    """Median wall_ms per window per forecaster, true order and over the placebos."""
    scored = found["truth"]["metrics"]["forecasters"]
    return {
        "true_order": {
            name: entry["wall_ms"]["median"] for name, entry in scored.items()
        },
        "placebo_runs": [
            {
                "ordering": run["ordering"],
                "seed": run["placebo_seed"],
                "median_ms": {
                    name: entry["wall_ms"]["median"]
                    for name, entry in run["metrics"]["forecasters"].items()
                },
            }
            for run in found["placebos"]
        ],
    }


def stage_pairs(options: argparse.Namespace) -> list[dict[str, Any]]:
    """Every ready pair that has no file yet. Resumable: an existing file is kept."""
    survey = read(OUT / "survey.json")
    wanted = _wanted(survey, options)
    store = open_store()
    records = []
    try:
        for entry, pair in wanted:
            path = pair_path(entry["capture"]["capture_id"], *pair)
            if path.exists() and not options.force:
                print(f"kept {path.relative_to(REPO_ROOT)}")
                records.append(read(path))
                continue
            started = time.perf_counter()
            record = stage_pair(store, entry, pair, options)
            print(f"{path.relative_to(REPO_ROOT)}  {record['n_windows']} windows"
                  f"  {record['decision']['label']}"
                  f"  {round(time.perf_counter() - started, 1)} s")  # fmt: skip
            records.append(record)
    finally:
        store.close()
    return records


def _wanted(
    survey: dict[str, Any], options: argparse.Namespace
) -> list[tuple[dict[str, Any], tuple[str, int]]]:
    """The ready pairs this invocation covers, filtered by the command line."""
    by_id = {entry["capture"]["capture_id"]: entry for entry in survey["captures"]}
    chosen = []
    for capture_id, target, horizon in survey["ready_pairs"]:
        if options.capture and capture_id != options.capture:
            continue
        if options.target and target != options.target:
            continue
        if options.horizon and horizon != options.horizon:
            continue
        chosen.append((by_id[capture_id], (target, horizon)))
    return chosen


# -- stage 6: the summary -------------------------------------------------------------


def _pair_files(survey: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        read(pair_path(capture_id, target, horizon))
        for capture_id, target, horizon in survey["ready_pairs"]
        if pair_path(capture_id, target, horizon).exists()
    ]


def _reason_counts(excluded: list[dict[str, Any]]) -> dict[str, int]:
    """One count per cohort refusal, in place of the 3223-row census the copy holds."""
    counted: dict[str, int] = {}
    for row in excluded:
        counted[row["reason"]] = counted.get(row["reason"], 0) + 1
    return counted


def _labels(rows: list[dict[str, Any]]) -> dict[str, int]:
    """One count per label. A label nobody took is absent rather than 0."""
    counted: dict[str, int] = {}
    for row in rows:
        counted[row["label"]] = counted.get(row["label"], 0) + 1
    return counted


def stage_summary(options: argparse.Namespace) -> dict[str, Any]:
    """Every file under out/ read back into one row per run, and the label counts."""
    census = read(OUT / "census.json")
    survey = read(OUT / "survey.json")
    runs = _pair_files(survey)
    pooled = [read(path) for path in sorted((OUT / "pooled").glob("*.json"))]
    # `clocks` rather than `lineage`: the module of that name is imported above, and a
    # local that shadows it turns `lineage.COLUMNS` into an attribute of a dict.
    clocks = read(OUT / "lineage.json") if (OUT / "lineage.json").exists() else None
    rows = _summary_rows(runs) + _summary_rows(pooled)
    labels = _labels(rows)
    summary = {
        "experiment": "E08",
        "copy": census["copy"],
        "home": census["home"],
        "captures_on_disk": census["captures_on_disk"],
        "cohort_size": survey["cohort_size"],
        "cohort": [entry["capture"] for entry in survey["captures"]],
        "rebuilt_captures": census["rebuilt_captures"],
        # census.json holds one row per capture and is 1.2 MB, over the 500 KB the
        # check-added-large-files hook allows, so it is not re-included in git. These
        # are the counts the write-up quotes from it.
        "excluded_capture_counts": _reason_counts(census["excluded"]),
        "constants": constants(options.device),
        "forecasters": options.forecasters,
        "model": options.model,
        "readiness_table": [
            {key: row[key] for key in _READINESS}
            for entry in survey["captures"]
            for row in entry["readiness"]
        ],
        "runs": rows,
        "label_counts": labels,
        "invalid_placebos": [row for row in rows if not row["valid"]],
        "lineage": None if clocks is None else _lineage_rows(clocks),
        "stage_wall_s": _walls_by_stage(census, survey, runs, pooled, clocks),
        "licenses": e07.licenses([{"declared": run["forecasters"]} for run in runs]),
    }
    write(OUT / "summary.json", summary)
    print(render_table(rows, _SUMMARY))
    print(f"\nlabels: {json.dumps(labels, sort_keys=True)}")
    for line in summary["licenses"]:
        print(line)
    return summary


def _walls_by_stage(
    census: dict[str, Any],
    survey: dict[str, Any],
    runs: list[dict[str, Any]],
    pooled: list[dict[str, Any]],
    clocks: dict[str, Any] | None,
) -> dict[str, Any]:
    """One wall per stage and their total, in seconds.

    Process start-up is NOT in here and is measured separately in out/pilot.json: a
    stage times itself from inside the process, and the checkpoint load happens before
    the first stage function runs.
    """
    stages = {
        "copy": census["wall_s"],
        "survey": survey["wall_s"],
        "pairs": round(sum(row["wall_ms"]["pair_total"] for row in runs) / 1000.0, 3),
        "pooled": pooled[0]["wall_s"] if pooled else None,
        "lineage": None if clocks is None else clocks["wall_s"],
    }
    known = [value for value in stages.values() if value is not None]
    return {
        **stages,
        "total": round(sum(known), 3),
        "total_minutes": round(sum(known) / 60.0, 2),
    }


def _summary_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "capture": run["capture"]["capture_id"],
            "task": run["capture"]["task_id"],
            "target": run["target"],
            "h": run["horizon"],
            "n_windows": run["n_windows"],
            "e_m": rounded(run["decision"]["e_m"]),
            "e_b": rounded(run["decision"]["e_b"]),
            "e_p": rounded(run["decision"]["e_p"]),
            "w_mb": rounded(run["decision"]["w_mb"]),
            "w_mp": rounded(run["decision"]["w_mp"]),
            "valid": run["sentinel"]["valid"],
            "label": run["decision"]["label"],
            "file": str((OUT / run["capture"]["capture_id"]).relative_to(REPO_ROOT)),
        }
        for run in runs
    ]


def _lineage_rows(clocks: dict[str, Any]) -> dict[str, Any]:
    """out/lineage.json cut down to the rows the summary table prints."""
    return {
        "repo_id": clocks["repo_id"],
        "captures_carrying_repo_id": clocks["captures_carrying_repo_id"],
        "attempt_note": clocks["attempt_note"],
        "clocks": {
            clock: {
                "rows": found["rows"],
                "series_id": found["series_id"],
                "readiness": [
                    {key: row[key] for key in lineage.COLUMNS}
                    for row in found["readiness"]
                ],
            }
            for clock, found in clocks["clocks"].items()
        },
        "ablation": {
            key: value for key, value in clocks["ablation"].items() if key != "printed"
        },
    }


# -- the command line -----------------------------------------------------------------


STAGES = ("copy", "survey", "pair", "pairs", "pooled", "lineage", "summary")


def main() -> int:
    parser = argparse.ArgumentParser(description="E08 temporal validity on captures")
    parser.add_argument("--stage", required=True, choices=STAGES)
    parser.add_argument("--device", default="cpu", choices=DEVICES)
    parser.add_argument(
        "--forecasters", default=",".join([*DEFAULT_FORECASTERS, TIMESFM])
    )
    parser.add_argument("--capture", default=None)
    parser.add_argument("--target", default=None)
    parser.add_argument("--horizon", type=int, default=None, choices=HORIZONS)
    parser.add_argument("--ablation-target", default="attempts_to_land")
    parser.add_argument("--force", action="store_true")
    options = parser.parse_args()
    options.forecasters = [one for one in options.forecasters.split(",") if one]
    options.model = TIMESFM
    point_home_at_the_copy()
    _dispatch(options)
    return 0


def _dispatch(options: argparse.Namespace) -> None:
    if options.stage == "copy":
        stage_copy(options)
    elif options.stage == "survey":
        stage_survey(options)
    elif options.stage in ("pair", "pairs"):
        stage_pairs(options)
    elif options.stage == "pooled":
        pooled.stage_pooled(options)
    elif options.stage == "lineage":
        lineage.stage_lineage(options)
    else:
        stage_summary(options)


if __name__ == "__main__":
    raise SystemExit(main())
