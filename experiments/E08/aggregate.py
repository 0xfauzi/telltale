"""The cross-run numbers docs/experiments/E08.md quotes, written where they can be read.

run.py stores one file per pair. Every statement the write-up makes ABOUT those files
(how many placebo runs made persistence worse, what E07's half of the rule would have
labelled the same pairs, whether the target carries any recency at all) is arithmetic
over them that would otherwise exist only in the prose. A number in a decision file that
is in no artefact is a number nobody can check.

Two of the numbers here are measured OUTSIDE the forecasting machinery on purpose. The
whole result of this experiment rests on the placebo's validity check, which is a
statement about persistence, so the lag-1 step of every target column is computed
directly from the series rows and compared against the same column's spread. If the
machinery were wrong about recency, these two numbers would disagree with it.

It re-runs no forecast. It reads out/ and the copy the runner made, so it is cheap and
it cannot change a result.

    uv run python experiments/E08/aggregate.py
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any

E08_DIR = Path(__file__).resolve().parent
REPO_ROOT = E08_DIR.parents[1]
OUT = E08_DIR / "out"
DB = OUT / "home" / "telltale.db"
sys.path.insert(0, str(REPO_ROOT / "experiments" / "E07"))

from decide import decide as baseline_only  # noqa: E402
from decide import stats  # noqa: E402

from telltale.store import Store  # noqa: E402

MODEL = "timesfm"
SENTINEL = "persistence"
# Seeds for the model-free recency check below. Five, the placebo's own R, so the two
# numbers are comparable; the check is not a placebo and never labels anything.
RECENCY_SEEDS = 5


def pair_files() -> list[Path]:
    return sorted(OUT.glob("cap_*/*.json")) + sorted((OUT / "pooled").glob("*.json"))


def _sentinel(record: dict[str, Any]) -> dict[str, Any]:
    """The validity check as stored, plus the same count over the block runs alone.

    design 6.12's placebo is the BLOCK shuffle; B = 1 is a second control that the
    reading guide says is reported and is not part of the rule. `placebo.sentinel`
    counts all ten runs, so the block-only count is computed here to show whether that
    choice is what decided anything.
    """
    check = record["sentinel"]
    true = check["true_mae_mean"]
    worse = {
        ordering: sum(
            1
            for entry in check["placebo_mae_mean"]
            if entry["ordering"] == ordering
            and entry["mae_mean"] is not None
            and true is not None
            and float(entry["mae_mean"]) > float(true)
        )
        for ordering in ("placebo_block", "placebo_row")
    }
    return {
        "n_worse": check["n_worse"],
        "n_runs": check["n_runs"],
        "n_worse_block_only": worse["placebo_block"],
        "n_worse_row_only": worse["placebo_row"],
        "valid_block_only": worse["placebo_block"] == 5,
        "valid": check["valid"],
        "true_persistence_mae": check["true_mae_mean"],
        "median_placebo_persistence_mae": check["median_placebo_mae_mean"],
        "placebo_over_true": (
            None
            if not check["true_mae_mean"]
            else check["median_placebo_mae_mean"] / check["true_mae_mean"]
        ),
    }


def e07_label(store: Any, record: dict[str, Any]) -> dict[str, Any]:
    """What E07's half of the rule would have said about this pair, and nothing more.

    E07 could compute the baseline clause and not the placebo clause, and it labelled
    all 19 of its pairs "baseline sufficient". The same function is called here on this
    run's stored true-order rows, so the two experiments' labels are comparable rather
    than similar.
    """
    if record["capture"]["capture_id"] == "pooled":
        return {"label": None, "reason": "a pooled row has no stored forecast_runs row"}
    stored = _stored(store, record)
    found = baseline_only(stored["metrics"], list(stored["windows"]["retained"]), MODEL)
    return {
        "label": found["label"],
        "E_M": found["E_M"],
        "E_B": found["E_B"],
        "W_MB": found["W_MB"],
        "best_baseline": found["best_baseline"],
        "inequalities": found["inequalities"],
    }


def _stored(store: Any, record: dict[str, Any]) -> dict[str, Any]:
    for row in store.forecast_runs(record["series_id"]):
        if row["forecast_run_id"] == record["forecast_run_id"]:
            return dict(row)
    raise SystemExit(f"{record['forecast_run_id']}: not readable back")


def recency(store: Any, record: dict[str, Any]) -> dict[str, Any]:
    """Does the last context row predict the next one better than an earlier row does?

    Model-free, and the reason the placebo's verdict can be believed. Over the SAME
    origins the run retained: the mean absolute step from y_{o-1} to the actuals, and
    the same distance from a uniformly chosen earlier row of the same context. A ratio
    at or above 1 says the most recent row is no better than any other row, which is
    what makes a shuffle of the context harmless to persistence.
    """
    if record["capture"]["capture_id"] == "pooled":
        return {"assessable": False, "reason": "a pooled row spans several series"}
    stored = _stored(store, record)
    series = store.series(record["series_id"])
    names = [column.name for column in series.columns]
    column = [row[names.index(record["target"])] for row in series.rows]
    windows = list(stored["windows"]["retained"])
    last = statistics.fmean(
        [_distance(column, one, int(one["origin"]) - 1) for one in windows]
    )
    drawn = [
        statistics.fmean([_distance(column, one, _draw(one, seed)) for one in windows])
        for seed in range(RECENCY_SEEDS)
    ]
    return {
        "assessable": True,
        "n_windows": len(windows),
        "mae_from_last_context_row": last,
        "mae_from_a_random_context_row": stats(drawn),
        "ratio_random_over_last": statistics.median(drawn) / last if last else None,
        "lag1_autocorrelation": _lag1([value for value in column if value is not None]),
    }


def _distance(column: list[Any], window: dict[str, Any], index: int) -> float:
    """Mean |actual - column[index]| over the H steps of one window.

    At `index = origin - 1` this is persistence computed by hand, from the series rows
    rather than from anything the backtester stored.
    """
    origin, horizon = int(window["origin"]), int(window["horizon"])
    return statistics.fmean(
        [abs(column[origin + step] - column[index]) for step in range(horizon)]
    )


def _draw(window: dict[str, Any], seed: int) -> int:
    """A uniformly chosen row of the same context, seeded per (origin, seed)."""
    return random.Random(seed + int(window["origin"])).randrange(
        int(window["ctx_start"]), int(window["origin"])
    )


def _lag1(values: list[float]) -> float | None:
    """Pearson autocorrelation at lag 1. None when the column does not vary."""
    if len(values) < 3:
        return None
    mean = statistics.fmean(values)
    centred = [value - mean for value in values]
    bottom = sum(one * one for one in centred)
    if bottom == 0:
        return None
    top = sum(centred[index] * centred[index - 1] for index in range(1, len(centred)))
    return top / bottom


def main() -> int:
    store = Store(DB).open()
    rows = []
    try:
        for path in pair_files():
            record = json.loads(path.read_text(encoding="utf-8"))
            rows.append({
                "file": str(path.relative_to(REPO_ROOT)),
                "capture": record["capture"]["capture_id"],
                "task": record["capture"]["task_id"],
                "target": record["target"],
                "horizon": record["horizon"],
                "n_windows": record["n_windows"],
                "label": record["decision"]["label"],
                "w_mb": record["decision"]["w_mb"],
                "e_m": record["decision"]["e_m"],
                "e_b": record["decision"]["e_b"],
                "sentinel": _sentinel(record),
                "e07_baseline_only": e07_label(store, record),
                "recency": recency(store, record),
            })  # fmt: skip
    finally:
        store.close()
    payload = {"pairs": rows, **_totals(rows)}
    (OUT / "aggregates.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    print(json.dumps({key: value for key, value in payload.items() if key != "pairs"},
                     indent=2, sort_keys=True))  # fmt: skip
    return 0


def _counts(values: list[Any]) -> dict[str, int]:
    """One count per distinct value. A value nobody took is absent rather than 0."""
    known = sorted({one for one in values if one is not None})
    return {str(one): values.count(one) for one in known}


def _column(rows: list[dict[str, Any]], block: str, key: str) -> list[Any]:
    return [row[block][key] for row in rows if row[block].get(key) is not None]


def _placebo_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The validity check over every row: how often the control controlled."""
    worse = [row["sentinel"]["n_worse"] for row in rows]
    return {
        "n_rows": len(rows),
        "n_valid_placebos": sum(1 for row in rows if row["sentinel"]["valid"]),
        "n_invalid_placebos": sum(1 for row in rows if not row["sentinel"]["valid"]),
        "n_valid_block_only": sum(
            1 for row in rows if row["sentinel"]["valid_block_only"]
        ),
        "placebo_runs_worse": stats([float(one) for one in worse]),
        "placebo_runs_worse_counts": _counts(worse),
        "placebo_block_runs_worse_counts": _counts(
            [row["sentinel"]["n_worse_block_only"] for row in rows]
        ),
        "placebo_over_true_persistence": stats(
            _column(rows, "sentinel", "placebo_over_true")
        ),
    }


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The counts the write-up states, computed once and written down."""
    return {
        **_placebo_totals(rows),
        "recency_ratio_random_over_last": stats(
            _column(rows, "recency", "ratio_random_over_last")
        ),
        # Below 1 means a random earlier row of the same context is a BETTER anchor
        # than the most recent one, which is the property that voids the placebo.
        "n_recency_ratio_below_one": sum(
            1 for one in _column(rows, "recency", "ratio_random_over_last") if one < 1.0
        ),
        "lag1_autocorrelation": stats(_column(rows, "recency", "lag1_autocorrelation")),
        "w_mb": stats([row["w_mb"] for row in rows if row["w_mb"] is not None]),
        "n_e_m_below_e_b": sum(1 for row in rows if row["e_m"] < row["e_b"]),
        "n_e_m_above_e_b": sum(1 for row in rows if row["e_m"] > row["e_b"]),
        "labels": _counts([row["label"] for row in rows]),
        "e07_baseline_only_labels": _counts(
            [row["e07_baseline_only"]["label"] for row in rows]
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
