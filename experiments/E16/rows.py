"""One (repository, target) row of E16: readiness, the label, the conditioned pair.

Split out of `run.py` because the two files have different jobs. This one holds the
pre-registered constants and the mechanics that turn one lineage and one target into one
answer; `run.py` holds the store copy, the four lineages, the synthetic control, the
printed report and the command line.

IMPORT ORDER IS LOAD-BEARING. Importing this module imports `telltale`, and `telltale`
reads `$TELLTALE_HOME` when a path is asked for rather than at import time, so this
module must not be imported until `run.py` has pointed that variable at the copy.
`run.py` imports it after `point_home_at_the_copy`, and `_e11` below turns that contract
into a refusal rather than a comment.
"""

from __future__ import annotations

import statistics
import sys
import time
from typing import TYPE_CHECKING, Any

from telltale.forecast import (
    ABLATION_A,
    ABLATION_C,
    BASELINE_NAMES,
    C_MIN_SHORT,
    CANDIDATE_HORIZON,
    CANDIDATE_SENTENCE,
    CANDIDATE_TARGETS,
    CHANGE_VARIANT,
    DELTA,
    ECHO,
    K_MIN,
    MAX_CONTEXT,
    PLACEBO_SEEDS,
    SCORED_STEPS,
    TARGETS,
    TIMESFM,
    W,
    readiness,
)
from telltale.forecast import backtest as backtester
from telltale.forecast import candidate as protocol
from telltale.forecast import frame as frames
from telltale.forecast import placebo as placebos

if TYPE_CHECKING:
    from telltale.store import Store


def _e11() -> Any:
    """E11's runner, which `run.py` loaded and pointed at E16's paths before it
    imported this module. The absence check is the import-order contract made a
    refusal: this module reuses `verdict` and `column_facts` from E11 and `run.py`
    reuses `snapshot`, `write`, `series_facts` and `declared`, and loading E11 a second
    time here would repoint `TELLTALE_HOME` at E11's own directory mid-run."""
    found = sys.modules.get("e11_run")
    if found is None:
        raise SystemExit(
            "experiments/E16/rows.py was imported before run.py loaded E11 and pointed"
            " TELLTALE_HOME at the copy. Run experiments/E16/run.py."
        )
    return found


e11 = _e11()

# The cohort, named by id rather than found. A runner that picked whichever repository
# had the most rows would be choosing its own sample. chopin is not here: 592 of its
# 594 first-parent commits in the last 90 days are by two other authors, and the
# pre-registration excluded it before any number existed.
REPOS: tuple[tuple[str, str], ...] = (
    ("telltale", "d9f783b1d318c0d65ec6b86fe729f6ff1a3581983e4287a71f6510eb80d84d6c"),
    ("systemap", "5b8cb70af348fd97bbfcc1304c01a43c8de20d5f25cdb6e2e67c2b9406354a83"),
    ("deckgen", "0ee20334979eb72c72c53cd29874eec55246e073a7d5240eedd3b4f07f627ee4"),
    ("kstrl", "2cae1649bab6d950ab76cec0325fa86fe8b23288ac13894ccdc6e4ce6541d762"),
)
CLOCK = "change"
POLICY = "exclude"
# The three the pre-registration names. `rework_within_3` itself is NOT here: its
# context rows carry labels decided by commits at or after the origin, which is
# look-ahead (docs/design/amendments/W8-T2.md), and `rework_within_3_lag3` is the
# column W8-T2 added so the question can be asked without it.
E16_TARGETS = (
    "merge_verification_ms",
    "merge_verification_failed",
    "rework_within_3_lag3",
)
FLAG_TARGETS = ("merge_verification_failed", "rework_within_3_lag3")
# Every forecaster in report order. The stub is in the dry run only, where it stands in
# for the model so the plumbing can be checked without the GPU.
FULL_FORECASTERS = (*BASELINE_NAMES, TIMESFM)
STUB_FORECASTERS = (*BASELINE_NAMES, ECHO)
# The forecaster every paired difference and every label is ABOUT. Named rather than
# inferred, because a run holds several and the answer changes with which one is asked.
# `choose_model` moves it to the stub for a dry run that did not load the model at all:
# a rule evaluated on a forecaster that did not run raises rather than answers, and a
# dry run is plumbing rather than a result. Every JSON file records what it was.
MODEL = TIMESFM

CONTROL_ROWS = 60
CONTROL_SEED = 3

WHOLE, FIRST, SECOND = "whole", "first", "second"

MOVES = "the candidate's features move the forecast"
NO_CONDITIONING = "no measurable conditioning at this n"
NOT_ASSESSABLE = "not assessable"

BRANCH_A, BRANCH_B, BRANCH_C = "a", "b", "c"

RULE = f"""Decision rule, pre-registered in docs/experiments/E16.md before this run and
not added to afterwards. Per (repository, target), never pooled:

(a) readiness over the retained frame fails -> "{NOT_ASSESSABLE}", with the failing
    check and its two numbers;
(b) fewer than k_min = {K_MIN} windows after exclusions, or a placebo under which
    persistence did not get worse -> "{NOT_ASSESSABLE}", with the count;
(c) otherwise the unconditioned true-order label from design 6.12's rule as amended by
    E08b, and the conditioned pair's reading:
    "{MOVES}" iff the paired median MAE
    difference (conditioned minus unconditioned) is below zero in BOTH halves of the
    origin range and the conditioned run's calibration is not flagged where the
    unconditioned one is not; otherwise "{NO_CONDITIONING}".

Two readings of that sentence were fixed here before any number was seen, because the
pre-registration does not spell them out and choosing after the fact would be choosing
a result. Both are recorded on every row.
  The calibration clause is read on the pair over the WHOLE origin range, because "the
  conditioned run" and "the unconditioned one" are the two runs of the pair, and the
  halves clause is written about the difference alone.
  The k_min clause is read on the (repository, target), not on each half: a half is
  about half the origins by construction, so requiring k_min in each would be a
  constant the pre-registration never wrote.

For the two flag targets the base rate over the retained rows is printed beside every
MAE, and MAE against a 0/1 label is reported as it is."""

# The reading the calibration clause takes, spelled out where the JSON carries it.
CALIBRATION_CLAUSE = (
    "not (conditioned flagged and unconditioned not flagged), on the pair over the"
    " whole origin range"
)


def _since(started: float) -> float:
    """Milliseconds since a `time.perf_counter()` mark, to one decimal."""
    return round((time.perf_counter() - started) * 1000.0, 1)


def a_block(built: Any, target: str) -> list[str]:
    """The candidate's A block on this series, with the target dropped.

    The same list `candidate.conditioned` computes, so `origin_ranges` below cuts the
    same frame the protocol will cut. A missing column is a refusal there and here.
    """
    names = {column.name for column in built.columns}
    missing = [name for name in ABLATION_A if name not in names]
    if missing:
        raise backtester.Refused(f"series {built.series_id} has no column {missing}")
    return [name for name in ABLATION_A if name != target]


def origin_ranges(frame: Any, horizon: int) -> list[dict[str, Any]]:
    """The whole origin range of the CONDITIONED frame and its two halves.

    Positions are rows of that frame, which is what `candidate._attach` indexes by.
    There is no delayed-label ceiling any more: a row whose target is unknown is not in
    the frame at all, so `o <= N - H` over it is the whole ceiling (W8-T3).
    """
    start, stop = C_MIN_SHORT, len(frame.rows) - horizon
    if stop < start:
        return [{"half": WHOLE, "range": None, "reason": "no origin is admissible"}]
    middle = start + (stop - start) // 2
    return [
        {"half": WHOLE, "range": (start, stop)},
        {"half": FIRST, "range": (start, middle)},
        {"half": SECOND, "range": (middle + 1, stop)},
    ]


def base_rate(frame: Any, target: str) -> float | None:
    """The share of 1s over the rows a run scores, or None off a flag.

    Beside every MAE on a flag target, because MAE against a 0/1 label is the mean
    absolute distance from a base rate, and reading it without one is reading a number
    whose scale nobody stated. The distinct values are checked rather than assumed: a
    "base rate" computed over a column that is not 0/1 is a mean wearing another name.
    """
    if target not in FLAG_TARGETS:
        return None
    at = [column.name for column in frame.columns].index(target)
    known = [float(one[at]) for one in frame.rows if one[at] is not None]
    if not set(known) <= {0.0, 1.0}:
        raise SystemExit(f"{target} holds {sorted(set(known))}: not a 0/1 label")
    return statistics.fmean(known) if known else None


# -- readiness ------------------------------------------------------------------------


def readiness_facts(built: Any, target: str, horizon: int) -> dict[str, Any]:
    """The eight checks over the retained frame, or the refusal that stopped them.

    `readiness.check` cuts `frame.retained(series, target)` itself, so every line is
    measured over the rows the backtester will score. E11's `verdict` and `as_check`
    turn the Check objects into the JSON a summary carries.
    """
    try:
        checks = readiness.check(built, target, horizon)
    except backtester.Refused as refused:
        return {"ran": False, "refusal": str(refused), "ready": False,
                "checks": [], "first_failure": None}  # fmt: skip
    return {"ran": True, "refusal": None, **e11.verdict(checks, True)}


def planned_windows(ready: dict[str, Any]) -> int | None:
    """Check 2's measured number: the origins the backtester will run."""
    for check in ready["checks"]:
        if check["check"] == "windows":
            return None if check["measured"] is None else int(check["measured"])
    return None


# -- the unconditioned run, its placebo and its label ---------------------------------


def unconditioned(
    built: Any, target: str, horizon: int, forecasters: dict[str, Any]
) -> dict[str, Any]:
    """True order, ten placebos, the validity check and design 6.12's label.

    The variant is the registry's (`change_past_only`): no `covariates` argument, so
    `backtest._variant` takes every forecastable column and lists the partial and
    unavailable ones it kept out. `backtest.run` cuts the target-retained frame itself.
    """
    started = time.perf_counter()
    try:
        found = placebos.paired(built, target, horizon, forecasters, MODEL)
    except backtester.Refused as refused:
        return {"ran": False, "refusal": str(refused), "wall_ms": _since(started)}
    truth = found["truth"]
    metrics = truth["metrics"]
    return {
        "ran": True,
        "refusal": None,
        "wall_ms": _since(started),
        "series_id": truth["series_id"],
        "variant": truth["variant"],
        "n_rows": truth["n_rows"],
        "n_windows": metrics["n_windows"],
        "tau": metrics["tau"],
        "covariates": truth["covariates"],
        "excluded_columns": truth["excluded"],
        "excluded_rows": truth["excluded_rows"],
        "dropped_counts": truth["dropped_counts"],
        "warnings": truth["warnings"],
        "assumptions": truth["assumptions"],
        "mae_mean": {
            name: entry["mae_mean"] for name, entry in metrics["forecasters"].items()
        },
        "calibration": metrics["forecasters"].get(MODEL, {}).get("calibration"),
        "decision": found["decision"].as_dict(),
        "sentinel": found["sentinel"],
        "placebo_definition": found["definition"],
    }


# -- the conditioned pair -------------------------------------------------------------


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
            a_block=ABLATION_A,
            past_only=ABLATION_C,
        )
    except backtester.Refused as refused:
        return {"half": span["half"], "range": span["range"], "ran": False,
                "refusal": str(refused), "wall_ms": _since(started)}  # fmt: skip
    return {
        "half": span["half"],
        "range": span["range"],
        "ran": True,
        "refusal": None,
        "wall_ms": _since(started),
        "report": protocol.report(found),
        "found": found,
    }


def pair_facts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """What each range's pair scored, without its windows."""
    return {
        one["half"]: None
        if not one["ran"]
        else {
            "range": one["range"],
            "n_paired": one["found"]["paired"]["n_paired"],
            "origins": one["found"]["paired"]["origins"],
            "retained_rows": one["found"]["retained_rows"],
            "excluded_for_block": one["found"]["excluded_for_block"],
            "scored_steps": one["found"]["scored_steps"],
            "mae_median_difference": one["found"]["paired"]["mae_median_difference"],
            "pinball_median_difference": one["found"]["paired"][
                "pinball_median_difference"
            ],
            "calibration": one["found"]["paired"]["calibration"],
            "mae_mean": {
                run: {
                    name: entry["mae_mean"]
                    for name, entry in one["found"]["runs"][run]["metrics"][
                        "forecasters"
                    ].items()
                }
                for run in (protocol.UNCONDITIONED, protocol.CONDITIONED)
            },
            "warnings": one["found"]["warnings"],
        }
        for one in attempts
    }


def flagged(entry: dict[str, Any] | None, run: str) -> bool | None:
    """Whether one run's calibration was flagged, or None when it was not computed."""
    if entry is None:
        return None
    found = (entry["calibration"] or {}).get(run)
    return None if found is None else bool(found.get("flagged"))


def reading(pairs: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered sentence and the numbers it was evaluated on.

    Both halves below zero AND the calibration clause. The whole range is recorded
    because a reader is entitled to it, and it is NOT part of the rule: E11's version
    of this rule read the whole range as a third sign, and E16's pre-registration
    writes "in both halves of the origin range" and stops there.
    """
    values = {
        half: None if entry is None else entry["mae_median_difference"]
        for half, entry in pairs.items()
    }
    negative = {
        half: None if value is None else value < 0 for half, value in values.items()
    }
    halves = all(negative.get(half) for half in (FIRST, SECOND))
    whole = pairs.get(WHOLE)
    calibration = {
        "unconditioned_flagged": flagged(whole, protocol.UNCONDITIONED),
        "conditioned_flagged": flagged(whole, protocol.CONDITIONED),
        "clause": CALIBRATION_CLAUSE,
    }
    not_worse = not (
        calibration["conditioned_flagged"] is True
        and calibration["unconditioned_flagged"] is False
    )
    holds = bool(halves and not_worse)
    return {
        "mae_median_difference": values,
        "negative": negative,
        "both_halves_below_zero": bool(halves),
        "calibration": calibration,
        "calibration_not_worse": bool(not_worse),
        "reading": MOVES if holds else NO_CONDITIONING,
        "sentence": CANDIDATE_SENTENCE,
    }


# -- one (repository, target) ---------------------------------------------------------


# Which run each "not assessable" clause of the pre-registration gates. Fixed here
# before any number was seen. The paragraph reads "fewer than k_min = 20 windows after
# exclusions, or a placebo under which persistence did not get worse" without naming a
# run, and the two runs of this protocol have different window counts BY CONSTRUCTION:
# the conditioned frame also drops the rows where the A block cannot be read, which the
# pre-registration itself anticipates for deckgen. So the window clause is applied to
# each question over the run that answers it, and the placebo clause to the question
# that reads a placebo, which is H5 alone: the H8 rule is written about the paired
# difference and the calibration and names no control.
GATES = (
    "H5 is gated by the unconditioned run's window count and its placebo's validity;"
    " H8 by the conditioned pair's window count over the whole origin range. The H8"
    " rule reads no placebo, so an invalid placebo does not withhold the reading; it"
    " is carried on the row either way."
)


def _stated(ready: dict[str, Any]) -> str | None:
    """The failing readiness line as `name: measured M, needed N`, or the refusal."""
    failure = ready["first_failure"]
    return str(ready["refusal"]) if failure is None else str(failure["stated"])


def h5_outcome(ready: dict[str, Any], plain: dict[str, Any]) -> dict[str, Any]:
    """Branch (a), (b) or (c) for the unconditioned question, and its label."""
    windows = None if not plain["ran"] else int(plain["n_windows"])
    base = {"question": "H5", "branch": None, "verdict": None, "because": None,
            "n_windows": windows, "k_min": K_MIN, "failing_check": None,
            "stated": None, "label": None}  # fmt: skip
    if not plain["ran"]:
        return {**base, "branch": BRANCH_A, "verdict": NOT_ASSESSABLE,
                "because": "the unconditioned run refused",
                "stated": plain["refusal"]}  # fmt: skip
    if not ready["ready"]:
        failure = ready["first_failure"]
        return {**base, "branch": BRANCH_A, "verdict": NOT_ASSESSABLE,
                "because": "readiness fails over the retained frame",
                "failing_check": failure, "stated": _stated(ready)}  # fmt: skip
    label = str(plain["decision"]["label"])
    if windows is None or windows < K_MIN:
        return {**base, "branch": BRANCH_B, "verdict": NOT_ASSESSABLE, "label": label,
                "because": "fewer than k_min windows after exclusions",
                "stated": f"n_windows {windows}, k_min {K_MIN}"}  # fmt: skip
    if not plain["sentinel"]["valid"]:
        worse = f"{plain['sentinel']['n_worse']} of {plain['sentinel']['n_runs']}"
        return {**base, "branch": BRANCH_B, "verdict": NOT_ASSESSABLE, "label": label,
                "because": "the placebo left persistence unharmed on at least one run",
                "stated": f"{worse} placebo runs made persistence worse"}  # fmt: skip
    return {**base, "branch": BRANCH_C, "verdict": label, "label": label,
            "because": "readiness passed, k_min met, placebo valid"}  # fmt: skip


def h8_outcome(
    ready: dict[str, Any], pairs: dict[str, Any], read: dict[str, Any]
) -> dict[str, Any]:
    """Branch (a), (b) or (c) for the conditioned question, and its reading."""
    whole = pairs.get(WHOLE)
    paired = None if whole is None else int(whole["n_paired"])
    base = {"question": "H8", "branch": None, "verdict": None, "because": None,
            "n_paired": paired, "k_min": K_MIN, "failing_check": None,
            "stated": None, "reading": None}  # fmt: skip
    if whole is None:
        return {**base, "branch": BRANCH_A, "verdict": NOT_ASSESSABLE,
                "because": "the pair refused over the whole range"}  # fmt: skip
    if not ready["ready"]:
        failure = ready["first_failure"]
        return {**base, "branch": BRANCH_A, "verdict": NOT_ASSESSABLE,
                "because": "readiness fails over the retained frame",
                "failing_check": failure, "stated": _stated(ready)}  # fmt: skip
    if paired is None or paired < K_MIN:
        return {**base, "branch": BRANCH_B, "verdict": NOT_ASSESSABLE,
                "because": "fewer than k_min paired windows after exclusions",
                "stated": f"n_paired {paired}, k_min {K_MIN}"}  # fmt: skip
    return {**base, "branch": BRANCH_C, "verdict": read["reading"],
            "reading": read["reading"],
            "because": "readiness passed and k_min paired windows"}  # fmt: skip


def outcome(
    ready: dict[str, Any],
    plain: dict[str, Any],
    attempts: list[dict[str, Any]],
    pairs: dict[str, Any],
    read: dict[str, Any],
) -> dict[str, Any]:
    """Both questions, and every refusal this row raised, with its text.

    `refusals` is what the brief's STOP condition reads: a refusal whose text is not
    one the pre-registration anticipated is a reason to stop rather than to patch.
    """
    refusals = [
        {"where": "unconditioned", "refusal": plain.get("refusal")},
        *[
            {"where": f"pair {one['half']}", "refusal": one["refusal"]}
            for one in attempts
        ],
    ]
    return {
        "gates": GATES,
        "h5": h5_outcome(ready, plain),
        "h8": h8_outcome(ready, pairs, read),
        "refusals": [one for one in refusals if one["refusal"]],
    }


def one_target(
    store: Store,
    repo: str,
    built: Any,
    target: str,
    forecasters: dict[str, Any],
) -> dict[str, Any]:
    """Readiness, the unconditioned run with its placebo, the pair over three ranges."""
    started = time.perf_counter()
    horizon = CANDIDATE_HORIZON[target]
    block = a_block(built, target)
    target_frame = frames.retained(built, target)
    cond_frame = frames.retained(built, target, block)
    ready = readiness_facts(built, target, horizon)
    plain = unconditioned(built, target, horizon, forecasters)
    spans = origin_ranges(cond_frame, horizon)
    attempts = [attempt(store, built, target, forecasters, span) for span in spans]
    pairs = pair_facts(attempts)
    read = reading(pairs)
    return {
        "repository": repo,
        "repo_id": built.cohort.get("repo_id"),
        "target": target,
        "unit": TARGETS[target].unit,
        "horizon": horizon,
        "scored_steps": list(SCORED_STEPS.get(target, ()) or ()) or None,
        "series_id": built.series_id,
        "model": MODEL,
        "forecasters": sorted(forecasters),
        "a_block": list(ABLATION_A),
        "past_only": list(ABLATION_C),
        "rows": len(built.rows),
        "retained": len(target_frame.rows),
        "retained_conditioned": len(cond_frame.rows),
        "excluded": frames.excluded(target_frame),
        "excluded_for_block": frames.for_columns(cond_frame),
        "base_rate": base_rate(target_frame, target),
        "base_rate_conditioned_frame": base_rate(cond_frame, target),
        "column": e11.column_facts(target_frame, target),
        "readiness": ready,
        "planned_windows": planned_windows(ready),
        "unconditioned": plain,
        "origin_ranges": spans,
        "pairs": pairs,
        "reading": read,
        "outcome": outcome(ready, plain, attempts, pairs, read),
        "wall_ms": _since(started),
        "sentence": CANDIDATE_SENTENCE,
    }


def row(found: dict[str, Any]) -> dict[str, Any]:
    """One (repository, target) as summary.json carries it: no windows, every number."""
    plain = found["unconditioned"]
    decision: dict[str, Any] = dict(plain.get("decision") or {})
    sentinel: dict[str, Any] = dict(plain.get("sentinel") or {})
    return {
        "repository": found["repository"],
        "target": found["target"],
        "horizon": found["horizon"],
        "rows": found["rows"],
        "retained": found["retained"],
        "retained_conditioned": found["retained_conditioned"],
        "excluded": found["excluded"]["count"],
        "excluded_reason": found["excluded"]["reason"],
        "excluded_for_block": found["excluded_for_block"]["count"],
        "excluded_for_block_by_column": found["excluded_for_block"]["by_column"],
        "excluded_columns": [
            one["column"] for one in found["unconditioned"].get("excluded_columns", [])
        ],
        "planned_windows": found["planned_windows"],
        "n_windows": found["unconditioned"].get("n_windows"),
        "dropped": found["unconditioned"].get("dropped_counts"),
        "base_rate": found["base_rate"],
        "ready": found["readiness"]["ready"],
        "e_m": decision.get("e_m"),
        "e_b": decision.get("e_b"),
        "best_baseline": decision.get("best_baseline"),
        "e_p": decision.get("e_p"),
        "e_p_range": (decision.get("placebo") or {}).get("range"),
        "w_mb": decision.get("w_mb"),
        "w_mp": decision.get("w_mp"),
        "inequalities": decision.get("inequalities"),
        "label": decision.get("label"),
        "label_reason": decision.get("reason"),
        "label_notes": decision.get("notes"),
        "placebo_valid": sentinel.get("valid"),
        "placebo_worse": f"{sentinel.get('n_worse')} of {sentinel.get('n_runs')}",
        "paired": {
            half: None if entry is None else entry["mae_median_difference"]
            for half, entry in found["pairs"].items()
        },
        "n_paired": {
            half: None if entry is None else entry["n_paired"]
            for half, entry in found["pairs"].items()
        },
        "calibration": found["reading"]["calibration"],
        "reading": found["reading"]["reading"],
        "h5_branch": found["outcome"]["h5"]["branch"],
        "h5_verdict": found["outcome"]["h5"]["verdict"],
        "h5_stated": found["outcome"]["h5"]["stated"],
        "h8_branch": found["outcome"]["h8"]["branch"],
        "h8_verdict": found["outcome"]["h8"]["verdict"],
        "h8_stated": found["outcome"]["h8"]["stated"],
        "refusals": found["outcome"]["refusals"],
        "wall_ms": found["wall_ms"],
    }


def constants(device: str, names: list[str]) -> dict[str, Any]:
    """The pre-registered numbers, read from the registry and never computed here."""
    return {
        "delta": DELTA, "w": W, "k_min": K_MIN, "c_min_change_clock": C_MIN_SHORT,
        "max_context": MAX_CONTEXT, "placebo_seeds": PLACEBO_SEEDS,
        "horizons": {t: CANDIDATE_HORIZON[t] for t in E16_TARGETS},
        "scored_steps": {t: list(SCORED_STEPS[t]) for t in SCORED_STEPS},
        "variant": CHANGE_VARIANT, "a_block": list(ABLATION_A),
        "past_only": list(ABLATION_C), "missingness_policy": POLICY,
        "candidate_targets": list(CANDIDATE_TARGETS), "e16_targets": list(E16_TARGETS),
        "flag_targets": list(FLAG_TARGETS), "device": device, "forecasters": names,
        "model": MODEL,
    }  # fmt: skip


def choose_model(names: list[str]) -> str:
    """The forecaster the rule is about: the model when it ran, else the stub.

    A dry run without the `forecast` extra scores baselines and the stub only, and
    `decide` raises on a forecaster with no windows. Substituting is what makes the
    plumbing checkable; NO number from such a run is a result about any repository,
    and `constants.model` on every file it writes says which one it was.
    """
    global MODEL
    MODEL = TIMESFM if TIMESFM in names else ECHO
    if MODEL not in names:
        raise SystemExit(f"--forecasters {names} holds neither {TIMESFM} nor {ECHO}")
    return MODEL
