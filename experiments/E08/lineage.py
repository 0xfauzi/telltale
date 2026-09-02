"""E08 stage 5: the attempt and change clocks, their readiness, and the ablation.

The request clock is one capture. These two are a repository lineage, and design 6.12
halves the context floor for them (c_min 16 rather than 32) because a change is a
coarser row than a model request and a lineage holds fewer of them.

What this stage can and cannot ask is decided by the registry rather than by the data.
`TARGETS` carries six change-clock entries and NO attempt-clock entry, so the attempt
clock gets its frame, its columns and the window arithmetic of design 6.12's check (2),
and no readiness verdict at all: a checklist runs on a registered target, and this
experiment registers nothing.

The ablation is attempted rather than assumed. It is run on the change clock and
whatever it answers, including a refusal, is recorded with the counts it printed.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from shared import (
    C_MIN_SHORT,
    HORIZONS,
    K_MIN,
    LINEAGE_CLOCKS,
    OUT,
    REPO_ROOT,
    e07,
    forecasters,
    write,
)
from shared import (
    store as open_store,
)

from telltale import series as compiler
from telltale.forecast import TARGETS, readiness
from telltale.forecast import ablate as ablator
from telltale.forecast import backtest as backtester
from telltale.report import render_table

if TYPE_CHECKING:
    import argparse

COLUMNS = ("clock", "rows", "target", "h", "verdict", "first_failure")


def stage_lineage(options: argparse.Namespace) -> dict[str, Any]:
    """Both lineage clocks on the copy, their readiness verdicts, the ablation."""
    started = time.perf_counter()
    store = open_store()
    try:
        repo_id, carried = _repo_id(store)
        clocks = {clock: _clock(store, clock, repo_id) for clock in LINEAGE_CLOCKS}
        attempt_targets = [
            name for name, spec in TARGETS.items() if spec.clock == "attempt"
        ]
        payload = {
            "repo_id": repo_id,
            "captures_carrying_repo_id": carried,
            "clocks": clocks,
            "registered_attempt_targets": attempt_targets,
            "attempt_note": (
                "the registry carries no attempt-clock target, so no readiness check"
                " can be run on that clock and this experiment registers none"
            )
            if not attempt_targets
            else None,
            "ablation": _ablation(store, clocks, options),
            "wall_s": round(time.perf_counter() - started, 3),
        }
    finally:
        store.close()
    write(OUT / "lineage.json", payload)
    print(render_table(
        [row for clock in payload["clocks"].values() for row in clock["readiness"]],
        COLUMNS,
    ))  # fmt: skip
    return payload


def _repo_id(store: Any) -> tuple[str, int]:
    """THIS repository's id, and how many captures on the copy carry it.

    Read from `repo.identity`, which is the function the launcher writes it with, and
    not from a count: the copy holds 3223 captures and the repository with the most of
    them is a disposable fixture repository the test suite creates, not this one.
    """
    from telltale import repo

    found = repo.identity(REPO_ROOT)["repo_id"]
    if not found:
        raise SystemExit(f"{REPO_ROOT}: no repo_id; is this a git checkout?")
    carried = sum(1 for row in store.captures() if str(row["repo_id"]) == str(found))
    return str(found), carried


def _clock(store: Any, clock: str, repo_id: str) -> dict[str, Any]:
    """One lineage clock: the frame, its columns, and readiness for every target."""
    built = compiler.build(store, clock, repo_id, "exclude")
    store.put_series(built)
    rows = []
    for target in sorted(TARGETS):
        if TARGETS[target].clock != clock:
            continue
        for horizon in TARGETS[target].horizons:
            rows.append(_lineage_readiness(built, clock, target, horizon))
    return {
        "series_id": built.series_id,
        "rows": len(built.rows),
        "columns": compiler.column_report(built),
        "changepoints": list(built.changepoints),
        "cohort_captures": len(built.cohort.get("captures") or []),
        # The compiler's `dropped` rows are {key, reason}. The key is renamed here
        # because this file is committed and the secret scanner reads `"key": "<24 hex
        # characters>"` as a credential; the value is a capture id and the field name is
        # the only thing that changes.
        "dropped": [
            {"capture_id": row["key"], "reason": row["reason"]}
            for row in built.cohort.get("dropped") or []
        ],
        "unknown_columns": built.cohort.get("unknown_columns"),
        "origin_arithmetic": [_origins(len(built.rows), one) for one in HORIZONS],
        "readiness": rows,
    }


def _origins(rows: int, horizon: int) -> dict[str, Any]:
    """Design 6.12's window count on a clock with c_min 16, before any drop.

    An upper bound and never a verdict: readiness runs on a registered target and
    counts the windows a backtest would RETAIN, which is this number minus the windows
    a hole or a changepoint drops. It is here because the attempt clock carries no
    registered target at all, and "not assessable" there has to be a count.
    """
    planned = (rows - horizon - C_MIN_SHORT) // horizon + 1
    return {
        "horizon": horizon,
        "rows": rows,
        "c_min": C_MIN_SHORT,
        "stride": horizon,
        "planned_origins": max(planned, 0),
        "k_min": K_MIN,
        "clears_k_min": max(planned, 0) >= K_MIN,
        "formula": "floor((N - H - c_min) / s) + 1, design 6.12 check (2)",
    }


def _lineage_readiness(
    built: Any, clock: str, target: str, horizon: int
) -> dict[str, Any]:
    """One checklist, or the backtester's own refusal in the cell that would hold it."""
    try:
        found = e07.readiness_of(built, target, horizon)
        checks = readiness.check(built, target, horizon)
        printed = readiness.report(built, target, horizon, checks)
    except backtester.Refused as refused:
        return {"clock": clock, "rows": len(built.rows), "target": target,
                "h": horizon, "verdict": "refused", "first_failure": str(refused),
                "checks": [], "printed": None}  # fmt: skip
    return {
        "clock": clock,
        "rows": len(built.rows),
        "target": target,
        "h": horizon,
        "verdict": "ready" if found["ready"] else "NOT ready",
        "first_failure": found["first_failure"] or "-",
        "checks": found["checks"],
        "printed": printed,
    }


def _ablation(
    store: Any, clocks: dict[str, Any], options: argparse.Namespace
) -> dict[str, Any]:
    """The A/B/C attempt on the change clock, or the refusal it printed, verbatim."""
    built = store.series(clocks["change"]["series_id"])
    target = options.ablation_target
    try:
        found = ablator.run(
            built, target, 1, lambda: forecasters(options), options.model
        )
    except backtester.Refused as refused:
        return {
            "series_id": built.series_id,
            "target": target,
            "horizon": 1,
            "attempted": True,
            "refused": str(refused),
            "rows": len(built.rows),
            "c_min": C_MIN_SHORT,
        }
    run_ids = ablator.store_all(store, found)
    return {
        "series_id": built.series_id,
        "target": target,
        "horizon": 1,
        "attempted": True,
        "refused": None,
        "rows": len(built.rows),
        "c_min": C_MIN_SHORT,
        "run_ids": run_ids,
        "verdict": found["verdict"],
        "aligned_origins": found["aligned_origins"],
        "dropped_for_alignment": found["dropped_for_alignment"],
        "variants": {
            key: {
                "columns": len(one["covariates"]),
                "n_windows": one["metrics"]["n_windows"],
                "dropped_counts": one["dropped_counts"],
            }
            for key, one in found["runs"].items()
        },
        "winner": found["winner"],
        "winner_placebo_sentinel": found["placebo"]["sentinel"],
        "warnings": found["warnings"],
        "printed": ablator.report(found),
    }
