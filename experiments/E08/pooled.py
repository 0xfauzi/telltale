"""E08 stage 4: the pooled rows, one per (target, horizon) over the whole cohort.

A pooled row is every capture's retained windows concatenated and scored once. It is
not a Series and it has no forecast_runs row: the windows in it were read back out of
the stored runs the per-pair stage wrote, and the numbers here are arithmetic over
those windows.

Two refusals of E07's carry over, and one is new.

  No tau. Each capture's threshold is q80 of ITS OWN first c_min rows, so the cohort
  has no single threshold and every lead-time cell says so instead of holding a number.

  No stored row. store.py hangs a forecast_runs row on a series_id, and this has none.

  The origins are re-keyed. Design 6.12 pairs a true window with its placebo twin BY
  ORIGIN and origin 32 exists in every capture, so concatenating without a re-key would
  pair one capture's true window with another capture's placebo.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from shared import (
    BASELINE_WINDOW,
    C_MIN,
    DELTA,
    DEMOTION_RESOLUTION,
    K_MIN,
    OUT,
    PLACEBO_ROW_BLOCK,
    PLACEBO_SEEDS,
    THRESHOLD_RULE,
    W,
    e07,
    pair_path,
    placebo_block,
    placebo_row,
    read,
    write,
)
from shared import (
    store as open_store,
)

from telltale.forecast import backtest as backtester
from telltale.forecast import decide as decider
from telltale.forecast import placebo as placebos

if TYPE_CHECKING:
    import argparse

# Why a pooled row carries no lead-time number: every capture has its own tau, so the
# concatenation has none, and a threshold borrowed from one capture scores every other
# capture against a level nobody measured on it.
POOLED_NO_TAU = e07.POOLED_NO_TAU
POOLED_NOT_STORED = (
    "a pooled row is a concatenation of the windows of several stored runs and is not"
    " a series, so it has no forecast_runs row of its own; every window in it was read"
    " back out of the stored run named in forecast_run_ids"
)
ORIGIN_OFFSET = 1_000_000


def stage_pooled(options: argparse.Namespace) -> list[dict[str, Any]]:
    """Every window of the cohort concatenated per (target, H), scored once."""
    started = time.perf_counter()
    survey = read(OUT / "survey.json")
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for capture_id, target, horizon in survey["ready_pairs"]:
        path = pair_path(capture_id, target, horizon)
        if path.exists():
            grouped.setdefault((target, horizon), []).append(read(path))
    store = open_store()
    try:
        rows = [
            _pooled(store, records, pair, options.model)
            for pair, records in sorted(grouped.items())
        ]
    finally:
        store.close()
    wall = round(time.perf_counter() - started, 3)
    for row in rows:
        row["wall_s"] = wall
        write(OUT / "pooled" / f"{row['target']}-H{row['horizon']}.json", row)
    return rows


def _pooled(
    store: Any, records: list[dict[str, Any]], pair: tuple[str, int], model: str
) -> dict[str, Any]:
    """The cohort's pooled row: true order, its placebos, the check and the label."""
    target, horizon = pair
    truth = _pooled_run(store, records, "true", None, pair)
    placebo_runs = [
        _pooled_run(store, records, ordering, seed, pair)
        for ordering in ("placebo_block", "placebo_row")
        for seed in range(PLACEBO_SEEDS)
    ]
    check = placebos.sentinel(truth, placebo_runs)
    decision = decider.decide(truth, placebo_runs, model)
    if not check["valid"]:
        decision = _refused(decision, check["reason"])
    scored = e07.mae_table(truth["metrics"], truth["windows"])
    for entry in scored.values():
        entry["lead_time"] = {"assessable": False, "reason": POOLED_NO_TAU}
    record = {
        "capture": {"capture_id": "pooled", "task_id": f"{len(records)} captures"},
        "captures": [row["capture"]["capture_id"] for row in records],
        "forecast_run_ids": [row["forecast_run_id"] for row in records],
        "placebo_run_ids": [one for row in records for one in row["placebo_run_ids"]],
        "target": target,
        "horizon": horizon,
        "stride": horizon,
        "n_windows": len(truth["windows"]),
        "tau": None,
        "tau_reason": POOLED_NO_TAU,
        "not_stored": POOLED_NOT_STORED,
        "origin_rekey": (
            f"origin + {ORIGIN_OFFSET} times the capture's index in `captures`, so a"
            " true window pairs with its own capture's placebo twin and never with"
            " another capture's window at the same origin"
        ),
        "metrics": scored,
        "placebo_metrics": [placebo_row(one) for one in placebo_runs],
        "sentinel": check,
        "decision": decision.as_dict(),
        "printed": decider.report(decision, _pooled_constants(truth)),
    }
    write(OUT / "pooled" / f"{target}-H{horizon}.json", record)
    return record


def _pooled_run(
    store: Any,
    records: list[dict[str, Any]],
    ordering: str,
    seed: int | None,
    pair: tuple[str, int],
) -> dict[str, Any]:
    """One ordering's windows from every capture, re-keyed, scored as one run.

    Not a stored row and not a Series: `decide` and `sentinel` read a run's windows and
    its metrics, and these are the same windows the stored rows carry.
    """
    windows = []
    for index, record in enumerate(records):
        found = _stored_ordering(store, record, ordering, seed)
        windows += [
            {**one, "origin": index * ORIGIN_OFFSET + int(one["origin"])}
            for one in found["windows"]["retained"]
        ]
    first = records[0]
    return {
        "ordering": ordering,
        "placebo_seed": seed,
        "windows": windows,
        "metrics": backtester.metrics(windows, None),
        "k_min": K_MIN,
        "delta": DELTA,
        "w": W,
        "covariates": first["covariates"],
        "target": pair[0],
        "horizon": pair[1],
    }


def _stored_ordering(
    store: Any, record: dict[str, Any], ordering: str, seed: int | None
) -> dict[str, Any]:
    """The stored run of one capture with this ordering and this seed. Never guessed."""
    wanted = [
        row
        for row in store.forecast_runs(record["series_id"])
        if row["ordering"] == ordering
        and row["placebo_seed"] == seed
        and row["target"] == record["target"]
        and int(row["horizon"]) == int(record["horizon"])
        and row["forecast_run_id"] in _ids(record)
    ]
    if not wanted:
        raise SystemExit(
            f"{record['capture']['capture_id']} {record['target']}"
            f" H{record['horizon']}: no stored {ordering} run at seed {seed}"
        )
    return dict(wanted[-1])


def _ids(record: dict[str, Any]) -> set[str]:
    return {record["forecast_run_id"], *record["placebo_run_ids"]}


def _refused(decision: Any, reason: str) -> Any:
    """The label a failed validity check leaves: not assessable, with its reason."""
    return replace(decision, label=decider.NOT_ASSESSABLE, reason=reason)


def _pooled_constants(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "delta": DELTA,
        "w": W,
        "k_min": K_MIN,
        "c_min": C_MIN,
        "horizon": run["horizon"],
        "stride": run["horizon"],
        "baseline_window": BASELINE_WINDOW,
        "threshold_rule": THRESHOLD_RULE,
        "placebo_block": placebo_block(int(run["horizon"])),
        "placebo_seeds": PLACEBO_SEEDS,
        "placebo_row_block": PLACEBO_ROW_BLOCK,
        "demotion_resolution": DEMOTION_RESOLUTION,
    }
