"""E13: does TimesFM-3 clear the baselines anywhere in the whole Claude population?

E07 asked this of 21 launcher captures of the build and found 19 labels, all baseline
sufficient. This run asks it of every Claude capture on the store: the launcher captures
(59 on 2026-09-05, E07's cohort grown by wave 6) and the 930 imported day-to-day
transcripts with at least c_min model requests. Two cohorts, pooled inside each and
never
across them, because the transcript surface cannot carry two of the eleven request-clock
columns (request_duration_ms and env_changed are `unavailable` on every one of the 930,
measured by the census) and the pre-registered variant `request_past_only` needs all
eleven. Readiness check 1 therefore refuses every import.

The rule for the import cohort, written before the run: a second named variant,
`transcript_past_only`, is the nine forecastable request-clock columns as past-only
covariates. Check 1 is waived for an imported capture ONLY when its not-forecastable set
is exactly {request_duration_ms, env_changed}; checks 2 to 8 must pass as written. The
variant name is stored on every forecast_runs row, so a run made under it says so on its
own face, and no pooled row ever mixes the two variants. Two assumptions ride with the
import cohort and are recorded on every row: regime boundaries inside a transcript are
not observable (no fingerprint, so no changepoint, so check 5 passes trivially), and
request duration is not observable at all on this surface.

Codex captures are excluded by measurement, not by rule: the Codex reducer emits `turn`
activities and the request clock reads `model_request`, so every Codex capture builds to
0 rows (imp_869276e15eb90a31ac51856f, 5,736 token_count rows, 0 series rows).

Decision rule per (capture, target, H), identical to E07's: baseline sufficient iff
E_M > (1 - delta) E_B or W_MB < w, with delta = 0.10, w = 0.60 and E_B the best baseline
in true order; otherwise "not baseline sufficient at this n, placebo pending", because
E08 measured the chronology control failing on this clock and no positive label is
reachable without it. The numbers this run adds are counts per cohort: labels of each
kind, and the pooled E_M against E_B per (target, H).

Everything else is E07's code, imported and unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "E07"))
import run as e07  # noqa: E402

from telltale.forecast import TARGETS  # noqa: E402
from telltale.forecast import backtest as backtester  # noqa: E402
from telltale.store import Store  # noqa: E402

OUT = HERE / "out"
C_MIN = min(TARGETS[t].c_min for t in TARGETS if TARGETS[t].clock == "request")
PROVIDER = "claude"
TRANSCRIPT_VARIANT = "transcript_past_only"
# The two columns the transcript surface cannot carry. A capture whose weak set is
# anything else is refused like any other: the waiver is for this set, not for check 1.
TRANSCRIPT_UNAVAILABLE = frozenset({"request_duration_ms", "env_changed"})
IMPORT_ASSUMPTIONS = [
    "variant transcript_past_only: nine request-clock columns; request_duration_ms and"
    " env_changed are unavailable on the transcript surface and are not covariates",
    "regime boundaries inside an imported transcript are not observable: no environment"
    " fingerprint is emitted for an import, so no changepoint is, and readiness check 5"
    " passes on one regime",
]


def surface_of(row: dict[str, Any]) -> str:
    prefix = row["capture_id"][:4]
    return {"imp_": "import", "cap_": "launcher"}.get(prefix, prefix.rstrip("_"))


def refusal(row: dict[str, Any]) -> str | None:
    if row["provider"] != PROVIDER:
        return f"provider {row['provider']}: no request clock (turn activities only)"
    if row["requests"] < C_MIN:
        return f"fewer than c_min {C_MIN} model requests"
    if surface_of(row) not in ("launcher", "import"):
        return f"capture kind {surface_of(row)} is not a session"
    return None


def cohort_key(row: dict[str, Any]) -> str:
    return f"{PROVIDER}/{surface_of(row)}/level {row['content_level']}"


def request_pairs() -> list[tuple[str, int]]:
    return [(t, h) for t, h in e07.pairs() if TARGETS[t].clock == "request"]


def weak_columns(readiness: dict[str, Any]) -> frozenset[str]:
    """The names check 1 listed as not forecastable, parsed back out of its detail."""
    first = readiness["checks"][0]
    if first["passed"] or "not forecastable: " not in str(first["detail"]):
        return frozenset()
    listed = str(first["detail"]).split("not forecastable: ", 1)[1]
    return frozenset(item.split(" (")[0] for item in listed.split(", "))


def admitted(surface: str, readiness: dict[str, Any]) -> tuple[bool, str | None]:
    """E07's rule for launcher captures; the named waiver for imports.

    Returns (admitted, variant or refusal). With the waiver taken, the refusal named is
    the first of checks 2 to 8 that failed, so an import's real blocker is what the
    table shows rather than the check 1 line every import fails.
    """
    checks = readiness["checks"]
    failed = [c for c in checks if not c["passed"]]
    if not failed:
        return True, None
    variant = None
    if surface == "import" and not checks[0]["passed"]:
        weak = weak_columns(readiness)
        if weak != TRANSCRIPT_UNAVAILABLE:
            return (
                False,
                f"coverage: not forecastable {sorted(weak)} is not the transcript set",
            )
        variant = TRANSCRIPT_VARIANT
        failed = [c for c in failed if c["check"] != checks[0]["check"]]
        if not failed:
            return True, variant
    first = failed[0]
    return (
        False,
        f"{first['check']}: measured {first['measured']}, needed {first['needed']}",
    )


def covariates_for(built: Any, target: str) -> list[str]:
    return [
        c.name
        for c in built.columns
        if c.name != target and c.coverage in backtester.FORECASTABLE
    ]


def backtest_once(
    store: Any,
    built: Any,
    target: str,
    horizon: int,
    options: argparse.Namespace,
    variant: str | None,
) -> dict[str, Any]:
    forecasters = {
        name: e07._forecaster(name, options.device) for name in options.forecasters
    }
    started = time.perf_counter()
    if variant is None:
        run = backtester.run(built, target, horizon, forecasters)
    else:
        run = backtester.run(
            built,
            target,
            horizon,
            forecasters,
            variant,
            covariates=covariates_for(built, target),
        )
        run["assumptions"] = [*run.get("assumptions", []), *IMPORT_ASSUMPTIONS]
    wall_ms = (time.perf_counter() - started) * 1000.0
    run_id = backtester.persist(store, run)
    return {
        "forecast_run_id": run_id,
        "wall_ms": round(wall_ms, 3),
        "stored": e07._stored_run(store, built.series_id, run_id),
    }


def run_one(
    store: Any,
    built: Any,
    capture: dict[str, Any],
    pair: tuple[str, int],
    options: argparse.Namespace,
    variant: str | None,
) -> dict[str, Any]:
    target, horizon = pair
    outcome = backtest_once(store, built, target, horizon, options, variant)
    stored = outcome["stored"]
    windows = list(stored["windows"]["retained"])
    metrics = dict(stored["metrics"])
    record = {
        "capture": capture,
        "series_id": built.series_id,
        "cohort": built.cohort,
        "surface": surface_of(capture),
        "variant": stored["variant"],
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
        "assumptions": stored.get("assumptions"),
        "claim_class": stored["claim_class"],
        "metrics": e07.mae_table(metrics, windows),
        "decision": e07.decide(metrics, windows, options.model),
    }
    row = e07._filed(record, OUT / capture["capture_id"] / f"{target}-H{horizon}.json")
    return {**row, "surface": record["surface"], "variant": record["variant"]}


def survey(
    store: Any, members: list[dict[str, Any]]
) -> list[
    tuple[dict[str, Any], Any, list[tuple[str, int, str | None]], list[dict[str, Any]]]
]:
    found = []
    for capture in members:
        built = e07.build_series(store, capture["capture_id"])
        ready: list[tuple[str, int, str | None]] = []
        rows: list[dict[str, Any]] = []
        for target, horizon in request_pairs():
            readiness = e07.readiness_of(built, target, horizon)
            ok, note = admitted(surface_of(capture), readiness)
            rows.append(
                {
                    "capture": capture["capture_id"],
                    "surface": surface_of(capture),
                    "rows": len(built.rows),
                    "target": target,
                    "h": horizon,
                    "verdict": "ready" if ok else "not ready",
                    "variant": (note if ok else None)
                    or (None if not ok else "request_past_only"),
                    "first_failure": None if ok else note,
                }
            )
            if ok:
                ready.append((target, horizon, note))
        found.append((capture, built, ready, rows))
    return found


def execute(
    store: Any, members: list[dict[str, Any]], options: argparse.Namespace
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    runs: list[dict[str, Any]] = []
    table: list[dict[str, Any]] = []
    for capture, built, ready, rows in survey(store, members):
        table += rows
        for target, horizon, variant in ready:
            runs.append(
                run_one(store, built, capture, (target, horizon), options, variant)
            )
    return runs, table


def failure_tally(table: list[dict[str, Any]]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for row in table:
        if row["verdict"] != "ready":
            key = str(row["first_failure"]).split(":")[0]
            tally[key] = tally.get(key, 0) + 1
    return dict(sorted(tally.items()))


def parse_options() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="E13 whole-population request-clock backtests"
    )
    parser.add_argument("--device", default="cpu", choices=e07.DEVICES)
    parser.add_argument(
        "--forecasters", default=",".join([*e07.DEFAULT_FORECASTERS, e07.TIMESFM])
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="pilot: at most N captures per cohort"
    )
    parser.add_argument(
        "--cohorts", default="", help="pilot: only cohorts whose key contains this"
    )
    options = parser.parse_args()
    options.forecasters = [one for one in options.forecasters.split(",") if one]
    options.model = e07.TIMESFM
    return options


def partition(
    rows: list[dict[str, Any]], options: argparse.Namespace
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """The captures to score, by cohort key, and a tally of every one refused by name.

    The pilot filters are applied after the refusals, so `--cohorts` and `--limit`
    narrow what runs without changing what the exclusion table says was on the disk.
    """
    excluded: dict[str, int] = {}
    by_cohort: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        why = refusal(row)
        if why is None:
            by_cohort.setdefault(cohort_key(row), []).append(row)
        else:
            excluded[why] = excluded.get(why, 0) + 1
    if options.cohorts:
        by_cohort = {k: v for k, v in by_cohort.items() if options.cohorts in k}
    if options.limit:
        by_cohort = {k: v[: options.limit] for k, v in by_cohort.items()}
    return by_cohort, excluded


def score_cohort(
    store: Any, members: list[dict[str, Any]], options: argparse.Namespace
) -> dict[str, Any]:
    """Every ready triple in one cohort, run and pooled. Nothing crosses a cohort."""
    cohort_started = time.perf_counter()
    runs, table = execute(store, members, options)
    labels: dict[str, int] = {}
    for run in runs:
        label = str(run["decision"].get("label"))
        labels[label] = labels.get(label, 0) + 1
    pooled = e07.pool(store, runs, options.model)
    return {
        "captures": len(members),
        "ready_triples": len(runs),
        "windows": sum(int(run["n_windows"]) for run in runs),
        "variants": sorted({str(run["variant"]) for run in runs}),
        "labels": labels,
        "not_ready_by_check": failure_tally(table),
        "model_wall_ms": round(sum(float(run["wall_ms"]) for run in runs), 1),
        "wall_s": round(time.perf_counter() - cohort_started, 3),
        "runs": runs,
        "readiness_table": table,
        "pooled": pooled,
        "licenses": e07.licenses(runs),
    }


def report(cohorts: dict[str, Any]) -> None:
    for key, found in cohorts.items():
        print(
            f"\n== {key}: {found['captures']} captures,"
            f" {found['ready_triples']} ready triples, {found['windows']} windows,"
            f" variants {found['variants']}, model wall {found['model_wall_ms']} ms"
        )
        print(f"   labels: {found['labels']}")
        print(f"   not ready by check: {found['not_ready_by_check']}")
        for row in found["pooled"]:
            keep = {
                k: row[k]
                for k in row
                if k
                in (
                    "target",
                    "horizon",
                    "h",
                    "n_windows",
                    "captures",
                    "e_m",
                    "e_b",
                    "best_baseline",
                    "w_mb",
                    "label",
                    "decision",
                )
            }
            print(f"   pooled {json.dumps(keep, default=str)[:300]}")


def main() -> int:
    options = parse_options()

    started = time.perf_counter()
    e07.OUT, e07.HOME = OUT, OUT / "home"
    home = e07.point_home_at_the_copy()
    copied = e07.copy_store(e07.SOURCE_DB, home / "telltale.db")
    store = Store(home / "telltale.db").open()
    try:
        rows = e07.census(home / "telltale.db")
        by_cohort, excluded = partition(rows, options)
        model_error = e07.load_model(options)
        cohorts: dict[str, Any] = {}
        for key, members in sorted(by_cohort.items()):
            cohorts[key] = score_cohort(store, members, options)
    finally:
        store.close()
    summary = {
        "experiment": "E13",
        "copy": copied,
        "home": str(home),
        "captures_on_disk": len(rows),
        "excluded": excluded,
        "c_min": C_MIN,
        "constants": e07.constants(model_error is None, options.device),
        "forecasters": options.forecasters,
        "timesfm_error": model_error,
        "limit": options.limit,
        "import_assumptions": IMPORT_ASSUMPTIONS,
        "cohorts": cohorts,
        "wall_s": round(time.perf_counter() - started, 3),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str)
    )
    print(
        f"captures on disk {len(rows)}; excluded {excluded};"
        f" model error {model_error}; wall {summary['wall_s']} s"
    )
    report(cohorts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
