"""The cross-run numbers docs/experiments/E07.md quotes, written where they can be read.

run.py stores one row per backtest. Every statement the write-up makes ABOUT those rows
(the median skill over twelve runs, how many calibration flags were raised, how many
quantile points fall below zero on a nonnegative target) is arithmetic over them that
existed only in the prose until this file. A number in a decision file that is in no
artefact is a number nobody can check, so each one is computed here and written to
`out/aggregates.json`, and `check_numbers.py` reads that file along with the rest.

It re-runs no forecast. It reads `out/summary.json` and the stored windows in
`out/home/telltale.db`, both of which run.py wrote, so it is cheap and it cannot change
a result.

    uv run python experiments/E07/aggregate.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from decide import stats

E07_DIR = Path(__file__).resolve().parent
OUT = E07_DIR / "out"
SUMMARY = OUT / "summary.json"
DB = OUT / "home" / "telltale.db"

FORECASTERS = ("persistence", "rolling_median", "rolling_mean", "local_drift", "echo")
MODEL = "timesfm"


def by_horizon(runs: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    return [run for run in runs if run["horizon"] == horizon]


def _model(runs: list[dict[str, Any]], key: str) -> list[Any]:
    """One field of the model's metrics, per run."""
    return [run["metrics"][MODEL][key] for run in runs]


def _best(runs: list[dict[str, Any]], key: str) -> list[Any]:
    """The same field of whichever baseline that run's decision called best."""
    return [run["metrics"][run["decision"]["best_baseline"]][key] for run in runs]


def _decision(runs: list[dict[str, Any]], key: str) -> list[Any]:
    return [run["decision"][key] for run in runs]


def spreads(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Every per-run series the write-up quotes a median or a spread of."""
    lead_model = _model(runs, "lead_time")
    return {
        "n_runs": len(runs),
        "skill_timesfm": stats(_model(runs, "skill")),
        "w_mb": stats(_decision(runs, "W_MB")),
        "wqs_timesfm": stats(_model(runs, "wqs")),
        "wqs_best_baseline": stats(_best(runs, "wqs")),
        "cov80_timesfm": stats(_model(runs, "coverage80")),
        "cov80_best_baseline": stats(_best(runs, "coverage80")),
        "e_b": stats(_decision(runs, "E_B")),
        "e_m": stats(_decision(runs, "E_M")),
        "wall_s": stats([run["wall_ms"] / 1000.0 for run in runs]),
        "timesfm_ms_median": stats([one["median"] for one in _model(runs, "wall_ms")]),
        "calibration_flagged_timesfm": sum(_model(runs, "calibration_flagged")),
        "calibration_flagged_best_baseline": sum(_best(runs, "calibration_flagged")),
        "best_baselines": sorted(set(_decision(runs, "best_baseline"))),
        "median_lead_timesfm": [one.get("median_lead") for one in lead_model],
        "median_lead_best_baseline": [
            one.get("median_lead") for one in _best(runs, "lead_time")
        ],
        "quiet_windows_timesfm": [one.get("quiet") for one in lead_model],
    }


def inequalities(runs: list[dict[str, Any]]) -> dict[str, Any]:
    tests = [run["decision"]["inequalities"] for run in runs]
    return {
        "n": len(tests),
        "first_holds": sum(1 for pair in tests if pair[0]["holds"]),
        "second_holds": sum(1 for pair in tests if pair[1]["holds"]),
        "both_hold": sum(1 for pair in tests if all(one["holds"] for one in pair)),
        "labels": sorted({run["decision"]["label"] for run in runs}),
    }


def readiness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts: dict[str, int] = {}
    failures: dict[str, int] = {}
    variation: dict[str, dict[str, float | None]] = {}
    for row in rows:
        key = f"{row['target']}|H{row['h']}|{row['verdict']}"
        verdicts[key] = verdicts.get(key, 0) + 1
        if row["verdict"] != "ready":
            first = row["first_failure"].split(":")[0]
            failures[first] = failures.get(first, 0) + 1
        if row["h"] == 1:
            check = next(one for one in row["checks"] if one["check"] == "variation")
            variation.setdefault(row["capture"], {})[row["target"]] = check["measured"]
    return {
        "triples": len(rows),
        "verdicts": dict(sorted(verdicts.items())),
        "first_failures": dict(sorted(failures.items())),
        "scaled_mad_by_capture": variation,
    }


def negative_quantiles(db: Path) -> dict[str, dict[str, float]]:
    """Quantile points below zero, per forecaster, over every stored window.

    `output_tokens` is nonnegative in the registry. TimesFM-3 takes that flag as
    `make_positive`; a baseline band is its point plus its own residual quantiles and
    nothing clips it. The counts are what that difference is worth in practice.
    """
    reader = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        stored = reader.execute("select windows from forecast_runs").fetchall()
    finally:
        reader.close()
    total: dict[str, int] = {}
    below: dict[str, int] = {}
    for (windows,) in stored:
        for record in json.loads(windows)["retained"]:
            for name, made in record["forecasts"].items():
                for step in made["quantiles"] or []:
                    total[name] = total.get(name, 0) + len(step)
                    below[name] = below.get(name, 0) + sum(1 for v in step if v < 0)
    return {
        name: {
            "quantile_points": total[name],
            "below_zero": below[name],
            "share": below[name] / total[name],
        }
        for name in sorted(total)
    }


def ratios(runs: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        run["metrics"][MODEL]["wqs"]
        / run["metrics"][run["decision"]["best_baseline"]]["wqs"]
        for run in runs
    ]
    return {
        **stats(values),
        "above_one": sum(1 for one in values if one > 1.0),
    }


def main() -> int:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    runs = summary["runs"]
    found = {
        "source": str(SUMMARY),
        "n_runs": len(runs),
        "n_pooled": len(summary["pooled"]),
        "horizon_1": spreads(by_horizon(runs, 1)),
        "horizon_4": spreads(by_horizon(runs, 4)),
        "inequalities_per_capture": inequalities(runs),
        "inequalities_pooled": inequalities(summary["pooled"]),
        "wqs_ratio_all_runs": ratios(runs),
        "readiness": readiness(summary["readiness_table"]),
        "negative_quantile_points": negative_quantiles(DB),
        "cohort_runtime_versions": sorted(
            {str(one["runtime_versions"]) for one in summary["cohort"]}
        ),
        "exclusion_reasons": _counted(summary["excluded_captures"]),
    }
    path = OUT / "aggregates.json"
    path.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n", "utf-8")
    print(f"{path}: {len(json.dumps(found))} bytes")
    print(json.dumps({
        "skill_h1_median": found["horizon_1"]["skill_timesfm"]["median"],
        "skill_h4_median": found["horizon_4"]["skill_timesfm"]["median"],
        "wqs_ratio_median": found["wqs_ratio_all_runs"]["median"],
        "labels": found["inequalities_per_capture"]["labels"],
    }, indent=2))  # fmt: skip
    return 0


def _counted(rows: list[dict[str, Any]]) -> dict[str, int]:
    found: dict[str, int] = {}
    for row in rows:
        found[row["reason"]] = found.get(row["reason"], 0) + 1
    return dict(sorted(found.items()))


if __name__ == "__main__":
    raise SystemExit(main())
