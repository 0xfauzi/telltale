"""E11: the one-step candidate protocol over every landed change of this repository.

Design 6.12's H8 asks whether knowing a candidate's own diff features changes the
forecast of what happens after it is merged. This runner asks that question of the only
repository on this disk, on the change clock, at H = 1, with the four baselines and
TimesFM-3, and it is written to reach a STOP as readily as a result: the decision rule
below was fixed before the run and every branch of it ends in a printed outcome.

Four refusals shape it, and none may be relaxed to admit data. It never touches the
owner's store: `snapshot` backs `$TELLTALE_HOME/telltale.db` up into `out/home` through
a read-only connection, TELLTALE_HOME is pointed at the copy first, and the run refuses
if that resolves to `~/.telltale`. It never lowers c_min, k_min or the origin limit:
every constant comes from `telltale.forecast` and is printed, and the count that failed
is the result. It pools nothing: one repository, one clock, and the synthetic control at
the end is a control on THIS RUNNER, never evidence about this repository. It says which
forecaster a difference is about: `MODEL` names it.

    uv run --extra forecast python experiments/E11/run.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys
import time
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

E11 = Path(__file__).resolve().parent
OUT = E11 / "out"
HOME = OUT / "home"
REAL_HOME = Path("~/.telltale").expanduser()
REPO_ROOT = E11.parents[1]


def point_home_at_the_copy() -> Path:
    """Everything after this call reads and writes `out/home`, and nothing else."""
    HOME.mkdir(parents=True, exist_ok=True)
    os.environ["TELLTALE_HOME"] = str(HOME)
    resolved = Path(os.environ["TELLTALE_HOME"]).expanduser().resolve()
    if resolved == REAL_HOME.resolve():
        raise SystemExit(
            f"TELLTALE_HOME resolves to {resolved}, which is the owner's store."
            " E11 runs against a copy and refuses to write to that file."
        )
    return resolved


SOURCE_DB = (
    Path(os.environ.get("TELLTALE_HOME") or "~/.telltale").expanduser() / "telltale.db"
)
point_home_at_the_copy()

from telltale import config  # noqa: E402
from telltale import series as compiler  # noqa: E402
from telltale.forecast import (  # noqa: E402
    ABLATION_A,
    ABLATION_C,
    BASELINE_NAMES,
    C_MIN_SHORT,
    CANDIDATE_FORBIDDEN,
    CANDIDATE_SENTENCE,
    CANDIDATE_TARGETS,
    DELTA,
    K_MIN,
    MAX_CONTEXT,
    PLACEBO_SEEDS,
    REWORK_TAIL,
    REWORK_TARGET,
    TARGETS,
    TIMESFM,
    W,
    make,
    readiness,
)
from telltale.forecast import backtest as backtester  # noqa: E402
from telltale.forecast import candidate as protocol  # noqa: E402
from telltale.forecast import placebo as placebos  # noqa: E402
from telltale.store import Store  # noqa: E402

# The cohort. One repository, named by its id rather than found: a runner that picked
# whichever repository had the most rows would be choosing its own sample.
REPO_ID = "d9f783b1d318c0d65ec6b86fe729f6ff1a3581983e4287a71f6510eb80d84d6c"
CLOCK = "change"
POLICY = "exclude"
HORIZON = 1
DEVICE = "cpu"
# Every forecaster the brief names, in report order, and the one the paired difference
# is about. `candidate.conditioned` scores all of them and pairs on MODEL.
FORECASTERS = (*BASELINE_NAMES, TIMESFM)
MODEL = TIMESFM

# 60 rows and seed 3 are W5-T1's, so the control's numbers can be compared with the
# paste in docs/log/W5-T1.md rather than standing alone.
CONTROL_ROWS = 60
CONTROL_SEED = 3

# Design 6.12's rule for how many rows one regime needs before k_min origins exist at
# H = 1: origins run from ctx_start + c_min to N - H, so a regime of R rows yields
# R - c_min - H + 1 origins and k_min of them needs R >= k_min + c_min + H - 1.
ROWS_FOR_K_MIN = K_MIN + C_MIN_SHORT + HORIZON - 1

RULE = """Decision rule, written before the run (design 6.12 "One-step candidate
conditioning" and the readiness checklist). Per target in {merge_verification_ms,
merge_verification_failed, rework_within_3}:
(a) readiness fails -> "not assessable", the failing check and its two numbers printed;
(b) n_windows < k_min -> "not assessable" with n_windows and k_min;
(c) otherwise the paired median difference in MAE and in pinball loss between
    conditioned and unconditioned, each run's calibration, and the label from `decide`
    on the conditioned run's true order against its placebo; candidate conditioning
    "changes the forecast" iff the paired median MAE difference is negative and its
    sign holds in both halves of the origin range; never "improves prediction" without
    both halves, and never a causal word."""

BRANCH_A = "a"
BRANCH_B = "b"
BRANCH_C = "c"
NOT_ASSESSABLE = "not assessable"


# -- the copy -------------------------------------------------------------------------


def snapshot() -> dict[str, Any]:
    """A consistent copy of a store being written to right now. `cp` leaves the
    write-ahead log behind and this session is itself being recorded into the source;
    sqlite3's backup API reads through the WAL and the source is `mode=ro`."""
    if not SOURCE_DB.exists():
        raise SystemExit(f"{SOURCE_DB}: no database to copy.")
    (dest := HOME / "telltale.db").unlink(missing_ok=True)
    started = time.perf_counter()
    reader = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    writer = sqlite3.connect(dest)
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()
    return {
        "source": str(SOURCE_DB), "dest": str(dest),
        "taken_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_bytes": SOURCE_DB.stat().st_size, "copy_bytes": dest.stat().st_size,
        "wall_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }  # fmt: skip


def write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                    encoding="utf-8")  # fmt: skip


# -- the series -----------------------------------------------------------------------


def build(store: Store) -> Any:
    """The change clock of this repository, compiled fresh and stored in the copy."""
    built = compiler.build(store, CLOCK, REPO_ID, POLICY)
    store.put_series(built)
    return built


def series_facts(built: Any) -> dict[str, Any]:
    """What the series IS, before any forecast reads it."""
    return {
        "series_id": built.series_id,
        "clock": built.clock,
        "n_rows": len(built.rows),
        "changepoints": list(built.changepoints),
        "regimes": regimes(built),
        "missingness_policy": built.missingness_policy,
        "reducer_version": built.reducer_version,
        "built_at": built.built_at,
        "cohort": built.cohort,
        "columns": [
            {
                "name": column.name,
                "unit": column.unit,
                "role": column.role,
                "coverage": column.coverage,
                "nulls": nulls_in(built, column.name),
            }
            for column in built.columns
        ],
        "row_keys": [meta.row_key for meta in built.row_meta],
        "env_fingerprint_ids": [meta.env_fingerprint_id for meta in built.row_meta],
        "distinct_env_fingerprint_ids": sorted(
            {one.env_fingerprint_id for one in built.row_meta if one.env_fingerprint_id}
        ),
    }


def regimes(built: Any) -> list[dict[str, Any]]:
    """The rows between changepoints, and the ceiling each puts on the origin count at
    H = 1. `origins_at_h1` is that ceiling and NOT a window count."""
    edges = [0, *built.changepoints, len(built.rows)]
    return [
        {"start": start, "stop": stop - 1, "rows": stop - start,
         "origins_at_h1": max(0, stop - start - C_MIN_SHORT - HORIZON + 1),
         "fingerprints": sorted({one.env_fingerprint_id
                                 for one in built.row_meta[start:stop]
                                 if one.env_fingerprint_id})}
        for start, stop in pairwise(edges)
    ]  # fmt: skip


def _at(built: Any, name: str) -> int:
    return [column.name for column in built.columns].index(name)


def nulls_in(built: Any, name: str) -> int:
    return sum(1 for row in built.rows if row[_at(built, name)] is None)


def column_facts(built: Any, name: str) -> dict[str, Any]:
    """One column's values, holes and spread. Scaled MAD and not a standard deviation:
    that is the statistic `readiness._variation` compares against zero."""
    values = [row[_at(built, name)] for row in built.rows]
    known = [float(value) for value in values if value is not None]
    centre = statistics.median(known) if known else None
    return {
        "column": name,
        "coverage": {c.name: c.coverage for c in built.columns}[name],
        "n_rows": len(values),
        "n_known": len(known),
        "n_null": len(values) - len(known),
        "null_rows": [index for index, value in enumerate(values) if value is None],
        "distinct_known": sorted(set(known)),
        "median": centre,
        "scaled_mad": (
            readiness.MAD_SCALE
            * statistics.median([abs(one - centre) for one in known])
            if known and centre is not None
            else None
        ),
    }


# -- readiness and the window plan ----------------------------------------------------

# `backtest._variant` refuses a target whose own coverage is not forecastable, before
# the checklist runs its first line, so `readiness.check` produces no table at all. The
# two cheapest lines are therefore computed through readiness.py's OWN functions rather
# than restated, on the covariate list `_variant` would have returned had it not raised.
REFUSED_BEFORE_CHECK_ONE = (
    "readiness.check refuses on this target before check 1, because"
    " backtest._variant refuses a target whose coverage is not observed or derived."
    " Checks 1 and 2 below are readiness.py's own _coverage and _windows, run on the"
    " covariate set _variant would have selected; checks 3 to 8 are not reachable."
)


def variant_columns(built: Any, target: str) -> list[str]:
    """`backtest._variant`'s selection, without its target-coverage refusal."""
    return [
        column.name
        for column in built.columns
        if column.name != target and column.coverage in backtester.FORECASTABLE
    ]


def protocol_columns(built: Any, target: str) -> list[str]:
    """`backtest._chosen(series, target, ABLATION_C)`'s selection, same exception: the
    past-only set `conditioned` runs under, ABLATION_C minus the target minus every
    column this series does not carry forecastably."""
    coverage = {column.name: column.coverage for column in built.columns}
    return [
        name
        for name in ABLATION_C
        if name != target and coverage.get(name) in backtester.FORECASTABLE
    ]


def last_origin(built: Any, target: str) -> int:
    """The highest origin `forecast candidate` scores, the lower of two ceilings:
    `backtest._plan` stops at N - H because a window needs H actual rows after it, and
    `candidate._limit` stops `rework_within_3` at N - 3 (6.12's delayed label)."""
    tail = REWORK_TAIL if target == REWORK_TARGET else 0
    return len(built.rows) - max(HORIZON, tail)


def plan_facts(
    built: Any, target: str, columns: list[str], label: str
) -> dict[str, Any]:
    """The origins a backtest runs here, with every drop. `n_windows` is after the
    delayed-label ceiling, which is what clause (b) compares against k_min;
    `n_planned` is the planner's own count, and only rework_within_3 differs."""
    planned = backtester._plan(built, target, HORIZON, columns, C_MIN_SHORT)
    ceiling = last_origin(built, target)
    kept = [one for one in planned.records if int(one["origin"]) <= ceiling]
    return {
        "covariate_set": label,
        "covariates": columns,
        "n_covariates": len(columns),
        "origins": [record["origin"] for record in kept],
        "n_windows": len(kept),
        "n_planned": len(planned.records),
        "last_origin": ceiling,
        "dropped": planned.dropped,
        "dropped_counts": planned.counts(),
        "formula": (len(built.rows) - HORIZON - C_MIN_SHORT) // HORIZON + 1,
        "c_min": C_MIN_SHORT,
        "k_min": TARGETS[target].k_min,
        "max_context": MAX_CONTEXT,
    }


# The pre-registration forbids lowering c_min and the runner does not lower it. The
# sweep is recorded so "forbidden" and "pointless" can be told apart by a number.
C_MIN_SWEEP = (4, 6, 8, 12, C_MIN_SHORT)


def sweep(built: Any, target: str, columns: list[str]) -> list[dict[str, Any]]:
    """What the window count does at each floor. NOT a run: the run uses C_MIN_SHORT."""
    plans = [
        (floor, backtester._plan(built, target, HORIZON, columns, floor))
        for floor in C_MIN_SWEEP
    ]
    return [
        {"c_min": floor, "n_planned": len(one.records), "dropped_counts": one.counts()}
        for floor, one in plans
    ]


def readiness_facts(built: Any, target: str) -> dict[str, Any]:
    """The checklist, or the refusal and the two lines that survive it."""
    try:
        checks = readiness.check(built, target, HORIZON)
    except backtester.Refused as refused:
        columns = variant_columns(built, target)
        planned = backtester._plan(built, target, HORIZON, columns, C_MIN_SHORT)
        checks = [
            readiness._coverage(built, target),
            readiness._windows(built, planned, HORIZON, C_MIN_SHORT,
                               TARGETS[target].k_min),
        ]  # fmt: skip
        return {"ran": False, "refusal": str(refused),
                "note": REFUSED_BEFORE_CHECK_ONE, **verdict(checks, False)}  # fmt: skip
    return {"ran": True, "refusal": None, "note": None, **verdict(checks, True)}


def verdict(checks: list[readiness.Check], complete: bool) -> dict[str, Any]:
    """The checklist as JSON: every line, whether it passed, and the first that did not.
    `complete` is False on the two lines computed after a refusal and forces `ready` to
    False: two passing lines out of eight is not a checklist that passed."""
    failed = [one for one in checks if not one.passed]
    return {
        "ready": complete and readiness.ready(checks),
        "checks": [as_check(one) for one in checks],
        "first_failure": as_check(failed[0]) if failed else None,
    }


def as_check(one: readiness.Check) -> dict[str, Any]:
    return {"check": one.name, "result": "pass" if one.passed else "FAIL",
            "measured": one.measured, "needed": one.needed, "detail": one.detail,
            "stated": one.stated()}  # fmt: skip


# -- the protocol ---------------------------------------------------------------------


def origin_ranges(built: Any, target: str) -> list[dict[str, Any]]:
    """The whole origin range and its two halves, as `conditioned` takes them. The
    halves are of the range the series and the ceiling allow, computed before any window
    is dropped, so a run that drops windows still has two halves of one interval."""
    start = C_MIN_SHORT
    stop = last_origin(built, target)
    if stop < start:
        return [{"half": "whole", "range": None, "reason": "no origin is admissible"}]
    middle = start + (stop - start) // 2
    return [
        {"half": "whole", "range": (start, stop)},
        {"half": "first", "range": (start, middle)},
        {"half": "second", "range": (middle + 1, stop)},
    ]


def attempt(
    store: Store,
    built: Any,
    target: str,
    forecasters: dict[str, Any],
    span: dict[str, Any],
) -> dict[str, Any]:
    """One `forecast candidate` over one origin range, or the refusal it raised."""
    started = time.perf_counter()
    try:
        found = protocol.conditioned(
            store,
            built,
            target,
            forecasters,
            MODEL,
            origin_range=tuple(span["range"]) if span["range"] else None,
        )
    except backtester.Refused as refused:
        return {
            "half": span["half"],
            "range": span["range"],
            "ran": False,
            "refusal": str(refused),
            "wall_ms": round((time.perf_counter() - started) * 1000.0, 1),
        }
    return {
        "half": span["half"],
        "range": span["range"],
        "ran": True,
        "refusal": None,
        "wall_ms": round((time.perf_counter() - started) * 1000.0, 1),
        "report": protocol.report(found),
        "found": found,
    }


# The placebo twin of a conditioned run is UNCONDITIONED, and that is a limit of the
# protocol rather than a choice made here: `placebo.run` reaches the window through the
# same `prepare` hook `candidate._attach` uses, so only one of them can be attached. So
# the label says whether the conditioned run's SCORES survive a shuffled context, which
# is the question design 6.12's rule asks of any run, and not whether the A block does.
PLACEBO_TWIN = (
    "the placebo twin is unconditioned: placebo.run and candidate._attach reach the"
    " window through the same prepare hook, so a placebo of a conditioned run cannot"
    " carry the A block. The label is about the conditioned run's scores under a"
    " shuffled context."
)


def labelled(built: Any, target: str, whole: dict[str, Any],
             forecasters: dict[str, Any]) -> dict[str, Any] | None:  # fmt: skip
    """The decision on the conditioned run's true order against its placebo."""
    if not whole["ran"]:
        return None
    found = placebos.paired(
        built, target, HORIZON, forecasters, MODEL,
        truth=dict(whole["found"]["runs"][protocol.CONDITIONED]),
        covariates=protocol_columns(built, target),
    )  # fmt: skip
    return {
        "label": found["decision"].label,
        "reason": found["decision"].reason,
        "decision": found["decision"].as_dict(),
        "sentinel": found["sentinel"],
        "definition": found["definition"],
        "placebo_note": PLACEBO_TWIN,
    }


def paired_signs(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """The paired median MAE difference on the whole range and on each half."""
    keep = ("n_paired", "origins", "mae_median_difference",
            "pinball_median_difference", "calibration")  # fmt: skip
    return {
        one["half"]: None
        if not one["ran"]
        else {key: one["found"]["paired"][key] for key in keep}
        for one in attempts
    }


def changes_the_forecast(signs: dict[str, Any]) -> dict[str, Any]:
    """The rule's sentence and its three numbers: negative on the whole range AND in
    both halves, which is spec 15.7's anti-cherry-pick clause."""
    values = {
        half: None if entry is None else entry["mae_median_difference"]
        for half, entry in signs.items()
    }
    negative = {
        half: None if value is None else value < 0 for half, value in values.items()
    }
    holds = all(negative.get(half) for half in ("whole", "first", "second"))
    return {
        "mae_median_difference": values,
        "negative": negative,
        "changes_the_forecast": bool(holds),
        "sentence": "candidate conditioning "
        + ("changes" if holds else "does not change")
        + " the forecast under this rule",
    }


def outcome(
    target: str,
    ready: dict[str, Any],
    plan: dict[str, Any],
    attempts: list[dict[str, Any]],
    label: dict[str, Any] | None,
) -> dict[str, Any]:
    """Which branch of the pre-registered rule this target took, and its numbers."""
    base = {
        "target": target,
        "n_windows": plan["n_windows"],
        "k_min": plan["k_min"],
        "failing_check": None,
        "stated": None,
        "refusal": None,
    }
    if not ready["ready"]:
        failure = ready["first_failure"]
        return {**base, "branch": BRANCH_A, "verdict": NOT_ASSESSABLE,
                "because": "readiness fails", "failing_check": failure,
                "stated": None if failure is None else failure["stated"],
                "refusal": ready["refusal"]}  # fmt: skip
    if plan["n_windows"] < plan["k_min"]:
        stated = f"n_windows {plan['n_windows']}, k_min {plan['k_min']}"
        return {**base, "branch": BRANCH_B, "verdict": NOT_ASSESSABLE,
                "because": "n_windows < k_min", "stated": stated}  # fmt: skip
    signs = paired_signs(attempts)
    rule = changes_the_forecast(signs)
    return {**base, "branch": BRANCH_C, "verdict": rule["sentence"],
            "because": "readiness passed and n_windows >= k_min", "paired": signs,
            "rule": rule,
            "label": None if label is None else label["label"]}  # fmt: skip


# -- what would make it assessable ----------------------------------------------------


def what_would(built: Any, target: str, plan: dict[str, Any],
               facts: dict[str, Any]) -> list[str]:  # fmt: skip
    """Every blocker this run measured, with the number that clears it. A list and not
    the first item: a target that clears one still fails on the next."""
    found = []
    if facts["coverage"] not in backtester.FORECASTABLE:
        found.append(
            f"coverage of {target} is {facts['coverage']} ({facts['n_known']} of"
            f" {facts['n_rows']} rows known): a target must be observed or derived,"
            " which needs an outcome on every landed change"
        )
    biggest = max(one["rows"] for one in regimes(built))
    if biggest < ROWS_FOR_K_MIN:
        found.append(
            f"the longest regime holds {biggest} rows and {K_MIN} origins at H ="
            f" {HORIZON} needs {ROWS_FOR_K_MIN}: {ROWS_FOR_K_MIN - biggest} more"
            " landed changes under one environment fingerprint"
        )
    for reason, count in sorted(plan["dropped_counts"].items()):
        if reason.startswith("missing_context_value"):
            found.append(
                f"{count} origins dropped for {reason}: no policy imputes, so the"
                " column has to be known on every row a context covers"
            )
    if facts["scaled_mad"] == 0:
        found.append(
            f"{target} is {facts['distinct_known']} on all {facts['n_known']} known"
            " rows, so its scaled MAD is 0 and readiness check 7 fails: an event of the"
            " other kind has to occur and be recorded"
        )
    return found


# -- one target -----------------------------------------------------------------------


def one_target(store: Store, built: Any, target: str,
               forecasters: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    """Readiness, the plan, the pair over three origin ranges, and the outcome."""
    columns = protocol_columns(built, target)
    ready = readiness_facts(built, target)
    plan = plan_facts(built, target, columns, "ABLATION_C")
    spans = origin_ranges(built, target)
    attempts = [attempt(store, built, target, forecasters, span) for span in spans]
    whole = attempts[0]
    label = labelled(built, target, whole, forecasters) if whole["ran"] else None
    facts = column_facts(built, target)
    return {
        "target": target,
        "unit": TARGETS[target].unit,
        "series_id": built.series_id,
        "horizon": HORIZON,
        "model": MODEL,
        "forecasters": list(FORECASTERS),
        "device": DEVICE,
        "a_block": list(ABLATION_A),
        "column": facts,
        "readiness": ready,
        # The checklist's own window count is `readiness.checks[1]`, which plans over
        # the wider variant_past_only set. It is not repeated here.
        "plan": plan,
        "origin_ranges": spans,
        "attempts": attempts,
        "label": label,
        "outcome": outcome(target, ready, plan, attempts, label),
        "what_makes_it_assessable": what_would(built, target, plan, facts),
        "c_min_sweep": sweep(built, target, columns),
        "sentence": CANDIDATE_SENTENCE,
    }


# -- the control ----------------------------------------------------------------------

CONTROL_NOTE = (
    "A CONTROL ON THIS RUNNER, never evidence about this repository. The rows are a"
    " seeded random walk from tests/integration/synthetic_series.py; nothing in them"
    " was measured and no number here may be pooled with or read as anything about any"
    " repository. It exists because a runner that printed 'not assessable' for every"
    " target would print that if it were broken, and this tells the two apart."
)


def control(store: Store, forecasters: dict[str, Any]) -> dict[str, Any]:
    """The same protocol on a synthetic change series whose coverage permits it."""
    sys.path.insert(0, str(REPO_ROOT / "tests" / "integration"))
    import synthetic_series

    built = synthetic_series.write_change(store, rows=CONTROL_ROWS, seed=CONTROL_SEED)
    started = time.perf_counter()
    found = {
        target: one_target(store, built, target, forecasters)
        for target in CANDIDATE_TARGETS
    }
    return {
        "note": CONTROL_NOTE, "series_id": built.series_id,
        "n_rows": len(built.rows), "rows": CONTROL_ROWS, "seed": CONTROL_SEED,
        "reducer_version": built.reducer_version, "targets": found,
        "wall_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }  # fmt: skip


# -- the run --------------------------------------------------------------------------


def constants() -> dict[str, Any]:
    """The pre-registered numbers, read from the registry and never computed here."""
    return {
        "delta": DELTA, "w": W, "k_min": K_MIN, "c_min_change_clock": C_MIN_SHORT,
        "horizon": HORIZON, "stride": HORIZON, "max_context": MAX_CONTEXT,
        "placebo_seeds": PLACEBO_SEEDS, "rework_tail": REWORK_TAIL,
        "forbidden_target": CANDIDATE_FORBIDDEN,
        "rows_for_k_min_in_one_regime": ROWS_FOR_K_MIN, "a_block": list(ABLATION_A),
        "past_only": list(ABLATION_C), "missingness_policy": POLICY, "device": DEVICE,
    }  # fmt: skip


def declared(forecasters: dict[str, Any]) -> list[dict[str, Any]]:
    """Every forecaster on itself, through `backtest._declared`: what a stored run
    carries, so a windowless run prints the licence a scored one stores."""
    return [backtester._declared(name, obj) for name, obj in forecasters.items()]


def forbidden(built: Any, forecasters: dict[str, Any]) -> dict[str, Any]:
    """The refusal design 6.12 puts in front of `attempts_to_land`, exercised."""
    try:
        protocol.conditioned(None, built, CANDIDATE_FORBIDDEN, forecasters, MODEL)
    except backtester.Refused as refused:
        return {"target": CANDIDATE_FORBIDDEN, "refused": True, "refusal": str(refused)}
    return {"target": CANDIDATE_FORBIDDEN, "refused": False, "refusal": None}


def line(text: str = "") -> None:
    print(text, flush=True)


def report(summary: dict[str, Any], targets: dict[str, Any]) -> None:
    """The series, one block per target, then the control. `targets` is the full
    per-target record and `summary["targets"]` is its outcome alone: summary.json is
    what a reader opens first, and the windows belong in the files beside it."""
    facts = summary["series"]
    line(f"E11  {summary['started_at']}  device {summary['device']}")
    line(RULE)
    line()
    line(f"series {facts['series_id']}  clock {facts['clock']}  {facts['n_rows']} rows"
         f"  changepoints {facts['changepoints']}")  # fmt: skip
    for one in facts["regimes"]:
        line(f"  regime rows {one['start']}..{one['stop']}  {one['rows']} rows"
             f"  origins at H {HORIZON}: {one['origins_at_h1']}"
             f"  fingerprints {one['fingerprints']}")  # fmt: skip
    line()
    for target, found in targets.items():
        report_target(target, found)
    for text in protocol.licences([{"forecasters": summary["forecasters"]}]):
        line(f"weights: {text}" if not text.startswith("weights") else text)
    line(f"devices: {[one['device'] for one in summary['forecasters']]}")
    line()
    line(f"forbidden target: {summary['forbidden']['refusal']}")
    line()
    line(summary["sentence"])
    line()
    control_lines(summary["control"])
    line()
    line(f"STOP: {summary['stop']['reason']}" if summary["stop"]["stopped"]
         else "every target reached branch (c)")  # fmt: skip


def report_target(target: str, found: dict[str, Any]) -> None:
    verdict, column, plan = found["outcome"], found["column"], found["plan"]
    line(f"--- {target} ({found['unit']}) ---")
    line(f"  column: coverage {column['coverage']}  known {column['n_known']}"
         f" of {column['n_rows']}  distinct {column['distinct_known']}"
         f"  scaled MAD {column['scaled_mad']}")  # fmt: skip
    if found["readiness"]["refusal"]:
        line(f"  readiness refused: {found['readiness']['refusal']}")
    for check in found["readiness"]["checks"]:
        line(f"  readiness {check['check']:12s} {check['result']:4s}"
             f"  measured {check['measured']}  needed {check['needed']}")  # fmt: skip
    line(f"  windows over {plan['covariate_set']} ({plan['n_covariates']} covariates):"
         f" formula {plan['formula']}, planned {plan['n_planned']}, at or below origin"
         f" {plan['last_origin']} {plan['n_windows']}, k_min {plan['k_min']};"
         f" dropped {plan['dropped_counts']}")  # fmt: skip
    for one in found["attempts"]:
        state = "ran" if one["ran"] else f"refused: {one['refusal']}"
        line(f"  origins {one['half']:6s} {one['range']!s:10s} {state}")
    line(f"  OUTCOME branch ({verdict['branch']}): {verdict['verdict']}"
         f"  [{verdict['because']}]")  # fmt: skip
    for text in [verdict["stated"], *found["what_makes_it_assessable"]]:
        if text:
            line(f"    {text}")
    line()


def control_headline(found: dict[str, Any]) -> dict[str, Any]:
    """The control without its windows: the branch and the paired difference."""
    return {
        **{key: value for key, value in found.items() if key != "targets"},
        "targets": {target: one["outcome"] for target, one in found["targets"].items()},
    }


def control_lines(found: dict[str, Any]) -> None:
    line(f"control (synthetic, {found['n_rows']} rows, seed {found['seed']}):"
         f" {found['series_id']}  wall {found['wall_ms']} ms")  # fmt: skip
    line(f"  {found['note']}")
    for target, one in found["targets"].items():
        verdict = one["outcome"]
        whole = (verdict.get("paired") or {}).get("whole")
        line(f"  {target:26s} branch ({verdict['branch']})"
             f"  n_windows {verdict['n_windows']}  paired median MAE difference"
             f" {None if whole is None else whole['mae_median_difference']}"
             f"  label {verdict.get('label')}")  # fmt: skip


def stopped(targets: dict[str, Any]) -> dict[str, Any]:
    """The STOP the brief names: fewer than k_min windows for every target."""
    counts = {name: found["plan"]["n_windows"] for name, found in targets.items()}
    below = {
        name: count
        for name, count in counts.items()
        if count < targets[name]["plan"]["k_min"]
    }
    return {
        "stopped": len(below) == len(counts),
        "n_windows": counts,
        "k_min": K_MIN,
        "reason": f"fewer than k_min {K_MIN} windows for every target: {counts}."
        f" No target reached the paired comparison, so no forecast number and no"
        f" paired difference exists for this repository at H = {HORIZON}.",
    }


def main() -> int:
    started = datetime.now(UTC).isoformat(timespec="seconds")
    wall = time.perf_counter()
    copy = snapshot()
    store = Store(config.db_path()).open()
    try:
        built = build(store)
        forecasters = {name: make(name, DEVICE) for name in FORECASTERS}
        targets = {
            target: one_target(store, built, target, forecasters)
            for target in CANDIDATE_TARGETS
        }
        summary = {
            "started_at": started,
            "device": DEVICE,
            "rule": RULE,
            "constants": constants(),
            "snapshot": copy,
            "command": list(sys.argv),
            "series": series_facts(built),
            "forecasters": declared(forecasters),
            "forbidden": forbidden(built, forecasters),
            "targets": {name: found["outcome"] for name, found in targets.items()},
            "stop": stopped(targets),
            "control": control(store, forecasters),
            "sentence": CANDIDATE_SENTENCE,
        }
    finally:
        store.close()
    summary["wall_ms"] = round((time.perf_counter() - wall) * 1000.0, 1)
    for name, found in targets.items():
        write(OUT / f"{name}.json", found)
    write(OUT / "series.json", summary["series"])
    # The control's windows are its bulk and summary.json is the file a reader opens
    # first, so the control goes to its own file and the summary keeps the headline.
    write(OUT / "control" / "control.json", summary["control"])
    report(summary, targets)
    summary["control"] = control_headline(summary["control"])
    write(OUT / "summary.json", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
