"""E05: how much does a measure move when five real sessions run the same task?

The H2 pilot of design 6.12. Five `claude -p --model sonnet` sessions on one
fix-the-test task, one base commit, one environment fingerprint, one fresh detached
worktree each, started by `telltale experiment repeat` and nothing else. What comes out
is a within-condition floor: the spread a measure has when nothing varies except the
run. Every later comparison of two repositories has to clear that floor to mean
anything, and the demotion table is the list of measures that cannot clear it at n = 5.

This script does three things and no more. It prepares out/repo, a copy of E01's fixture
repository with its own git history, and records the sha. It runs the repeat runner as a
subprocess, times it with perf_counter, and watches the owner's stop rule on the first
repetition from outside, because the runner prints nothing until all five are done. Then
it reads out/E05/report.json and writes out/decision.json.

The stop rule is the owner's and it is watched rather than trusted. E01 measured one
such session at 19.5 s of wall time and 246,950 tokens; if the first repetition passes
180 s or 600,000 tokens the whole process group is killed and the failure is the result.
It is watched through the capture database rather than through the runner's stdout,
because the runner has none until it finishes.

Nothing here retries. A repetition that fails its acceptance command is a result, its
numbers stay in the table, and the failure sits beside them.

Usage:
    uv run python experiments/E05/run.py                  # prepare, run five, decide
    uv run python experiments/E05/run.py --from-store     # rebuild the report, run none
    uv run python experiments/E05/run.py --finish-attempt 5   # close one repetition
    uv run python experiments/E05/run.py --decide-only    # recompute out/decision.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from math import sqrt
from pathlib import Path
from typing import Any

from telltale import experiments
from telltale import report as report_module
from telltale import stats as between

E05_DIR = Path(__file__).resolve().parent
REPO_ROOT = E05_DIR.parents[1]
OUT = E05_DIR / "out"
DB = Path(os.environ.get("TELLTALE_HOME") or "~/.telltale").expanduser() / "telltale.db"

EXPERIMENT = "E05"
TASK_ID = "E05"

# Five, because the owner approved five real sessions for this task on 2026-09-02.
# Overridable ONLY so the whole pipeline can be rehearsed against a stand-in agent that
# spends nothing; out/spec.json and out/decision.json both record the argv and the count
# that ran, so a rehearsal cannot be mistaken for the experiment.
REPETITIONS = int(os.environ.get("E05_REPETITIONS", "5"))

# The owner approved five sessions on 2026-09-02 and set these two limits on the FIRST
# repetition. E01 measured one session of this task at 19.5 s and 246,950 tokens.
FIRST_MAX_WALL_S = 180.0
FIRST_MAX_TOKENS = 600_000
POLL_S = 2.0
# Long enough for five sessions of E01's size with room to spare. The first-repetition
# gate is the real limit; this one only stops a hung runner from waiting forever.
RUNNER_TIMEOUT_S = 2400.0

# The four counters that make a session's token total, under the names measures.py
# writes them. A counter with no value is NAMED rather than counted as zero.
TOKEN_METRICS = (
    "fresh_input_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "output_tokens",
)

# The decision rule, from design 6.12, in the words of the brief for this task, which
# was written before any session started. Copied into out/decision.json and into
# docs/experiments/E05.md so the three cannot drift.
RULE = (
    "Per measure over the five captures: n, median, s (scaled MAD),"
    " MDD = 2.8 s sqrt(2 / n), demoted from repository comparison while"
    " MDD > 0.25 median, N_needed = ceil(2 (2.8 s / (0.25 median))^2). s = 0 means"
    ' "no within-condition variation observed at n = 5", neither demoted nor endorsed.'
    ' median = 0 with s > 0 is demoted with N_needed "not computable at median 0".'
    " A measure with fewer than 5 known values reports its n and is not judged."
    " Scaling to N = min(20, N_needed) is NOT this task: the report lists N_needed per"
    " measure and the orchestrator takes the list to the owner."
)

# The four verdicts the rule allows. Vocabulary fixed here so a table cell is one of
# these strings and never a sentence somebody wrote twice.
NO_VARIATION = f"no within-condition variation observed at n = {REPETITIONS}"
DEMOTED = "demoted from repository comparison"
RESOLVABLE = f"resolvable at n = {REPETITIONS}"
NOT_COMPUTABLE = "not computable at median 0"

# The command in the spec, minus the prompt, which is E01's FIX_PROMPT and is appended
# rather than retyped.
#
# CORRECTED after the five sessions of 2026-09-02. The spec ran with
# `--permission-mode acceptEdits` and no --max-turns, and all five sessions ended having
# done nothing: acceptEdits auto-accepts file edits and nothing else, so each session's
# `uv run pytest` raised a permission request that nobody in a headless run can answer,
# and the store holds three `claude.stream.system.permission_denied` observations per
# capture. E01's S1 ran this prompt with bypassPermissions and --max-turns 40 and fixed
# the test in 7 turns, so the two flags below are E01's. `experiments._approvable`
# refuses the old spelling now, so a run under the old flag cannot happen by accident.
#
# This corrected command HAS NOT BEEN RUN. Running it is five new sessions and an owner
# decision. See docs/experiments/E05.md.
COMMAND = [
    "claude", "-p", "--model", "sonnet", "--permission-mode", "bypassPermissions",
    "--max-turns", "40", "--output-format", "stream-json", "--verbose",
]  # fmt: skip

# The acceptance command, run by the HARNESS in the worktree after the agent exits.
# `-m pytest` rather than the pytest console script: `-m` puts the working directory on
# sys.path, which is how `from pkg.calc import ...` resolves in a fixture that installs
# nothing. pytest is in this checkout's dev group and the fixture needs no environment.
ACCEPTANCE = [str(REPO_ROOT / ".venv" / "bin" / "python"), "-m", "pytest", "-q"]

_E01 = REPO_ROOT / "experiments" / "E01" / "run.py"


def _e01() -> Any:
    """E01's runner as a module. Loaded by path: both files are named run.py.

    Registered in sys.modules BEFORE it is executed, for the reason E04 records: E01's
    Scenario is a frozen dataclass, and @dataclass looks its own module up by name.
    """
    spec = importlib.util.spec_from_file_location("e01_run", _E01)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_E01}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


E01 = _e01()


# -- the condition ------------------------------------------------------------------


def prepare() -> tuple[Path, str]:
    """out/repo: a copy of E01's fixture repository with one commit of its own.

    The privacy probes in secrets_note.txt are left in place. Their presence is what
    re-tests the claim that no secret reaches the store, on five more sessions.
    """
    repo = OUT / "repo"
    if repo.exists():
        shutil.rmtree(repo)
    OUT.mkdir(parents=True, exist_ok=True)
    repo = Path(E01.prepare_repo(OUT))
    return repo, _sha(repo)


def _command() -> list[str]:
    """The agent under test: the brief's command, or the stand-in of a rehearsal."""
    stand_in = os.environ.get("E05_AGENT")
    if stand_in is None:
        return [*COMMAND, E01.FIX_PROMPT]
    return [str(word) for word in json.loads(stand_in)]


def _sha(repo: Path) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def spec(repo: Path, base_sha: str) -> dict[str, Any]:
    """The spec the runner takes. Nine keys, every one of them the brief's."""
    return {
        "task_id": TASK_ID,
        "experiment": EXPERIMENT,
        "repo": str(repo),
        "base_sha": base_sha,
        "command": _command(),
        "acceptance": list(ACCEPTANCE),
        "repetitions": REPETITIONS,
        "provider": "claude",
        "level": 1,
    }


def spec_as_run() -> dict[str, Any]:
    """The spec the sessions in the store actually ran under, off out/spec.json.

    Read rather than rebuilt. A recovery that reconstructed the spec from this file's
    constants would report the five sessions of 2026-09-02 against the CORRECTED
    command, which is not the command they ran, and the write-up quotes this field.
    """
    path = OUT / "spec.json"
    if not path.exists():
        raise SystemExit(f"{path} is missing: there is no spec to recover against")
    return dict(json.loads(path.read_text(encoding="utf-8")))


# -- running the runner, and the owner's stop rule ------------------------------------


def run_repeat(spec_path: Path) -> dict[str, Any]:
    """`uv run telltale experiment repeat`, timed, with the first-repetition gate.

    `--project` pins which project uv resolves, for E04's reason: the runner's children
    run in copies of a fixture repository that has a pyproject.toml of its own.
    `start_new_session` puts the whole tree in one process group, so the gate can stop
    the agent and not just the runner that started it.
    """
    argv = [
        "uv", "run", "--project", str(REPO_ROOT), "telltale", "experiment", "repeat",
        str(spec_path), "--out", str(OUT),
    ]  # fmt: skip
    with (
        (OUT / "repeat.stdout.txt").open("wb") as out_fh,
        (OUT / "repeat.stderr.txt").open("wb") as err_fh,
    ):
        start = time.perf_counter()
        child = subprocess.Popen(
            argv,
            cwd=str(REPO_ROOT),
            stdout=out_fh,
            stderr=err_fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        stopped = _gate(child, start)
        code = child.wait(timeout=RUNNER_TIMEOUT_S)
        wall = time.perf_counter() - start
    return {
        "argv": argv,
        "wall_s": round(wall, 3),
        "exit_code": code,
        "stopped_by_the_gate": stopped,
        "stdout": (OUT / "repeat.stdout.txt").read_text(encoding="utf-8"),
        "stderr_head": (OUT / "repeat.stderr.txt").read_text(
            encoding="utf-8", errors="replace"
        )[:4000],
    }


def _gate(child: subprocess.Popen[bytes], start: float) -> str | None:
    """The owner's stop rule on the first repetition, watched from outside the runner.

    Returns the reason the group was killed, or None. Once the first capture has ended
    inside both limits the gate is done: the owner set it on the first repetition, and
    the four that follow are the experiment.
    """
    while child.poll() is None:
        time.sleep(POLL_S)
        capture = _capture_of(1)
        if capture is not None and _ended(capture):
            tokens, missing = _token_total(capture)
            if tokens is not None and tokens > FIRST_MAX_TOKENS:
                return _kill(
                    child, f"attempt 1 spent {tokens} tokens (missing counters:"
                    f" {missing}), over the {FIRST_MAX_TOKENS} limit"
                )  # fmt: skip
            return None
        if time.perf_counter() - start > FIRST_MAX_WALL_S:
            return _kill(
                child,
                f"attempt 1 did not end within {FIRST_MAX_WALL_S} s of wall time",
            )
    return None


def _kill(child: subprocess.Popen[bytes], why: str) -> str:
    """Stop the whole process group, agent included, and say why.

    A killed runner does NOT remove its worktree: the removal is in a finally block and
    SIGTERM does not run one. The reason names the directory to sweep.
    """
    print(f"STOP: {why}")
    os.killpg(os.getpgid(child.pid), signal.SIGTERM)
    try:
        child.wait(timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
    worktrees = DB.parent / "worktrees"
    return f"{why}; a worktree may be left under {worktrees}"


# -- reading the capture database ------------------------------------------------------


def _query(sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """One read-only query against the owner's capture database. Never writes."""
    if not DB.exists():
        return []
    conn = sqlite3.connect(f"{DB.as_uri()}?mode=ro", uri=True, timeout=10.0)
    try:
        return list(conn.execute(sql, params))
    finally:
        conn.close()


def _capture_of(attempt: int) -> str | None:
    """The newest E05 capture with this attempt number, or None."""
    rows = _query(
        "SELECT capture_id FROM observations"
        " WHERE observation_type = 'telltale.capture_started'"
        "   AND json_extract(payload, '$.experiment') = ?"
        "   AND json_extract(payload, '$.task_id') = ?"
        "   AND json_extract(payload, '$.attempt') = ?"
        " ORDER BY ingest_ts DESC, observation_id DESC LIMIT 1",
        (EXPERIMENT, TASK_ID, attempt),
    )
    return None if not rows else str(rows[0][0])


def _ended(capture_id: str) -> bool:
    return bool(
        _query(
            "SELECT 1 FROM observations WHERE capture_id = ?"
            "   AND observation_type = 'telltale.capture_ended' LIMIT 1",
            (capture_id,),
        )
    )


def _token_total(capture_id: str) -> tuple[int | None, list[str]]:
    """The session's four token counters summed, and the names of any with no value.

    A missing counter is not a zero. With every counter missing the total is None,
    because "no tokens" and "the reducer could not see one" are different answers.
    """
    found = dict(
        _query("SELECT metric, value FROM evidence WHERE capture_id = ?", (capture_id,))
    )
    present = [
        int(found[name]) for name in TOKEN_METRICS if found.get(name) is not None
    ]
    missing = [name for name in TOKEN_METRICS if found.get(name) is None]
    return (sum(present) if present else None), missing


def _permission_denied(capture_id: str) -> int:
    """How many tool calls this session was refused, from the stream's own observation.

    Counted from `claude.stream.system.permission_denied` rather than from the result
    message's `permission_denials`, because a session killed before its result line has
    no result message and would otherwise report 0 refusals rather than the three it
    recorded.
    """
    return int(
        _query(
            "SELECT count(*) FROM observations WHERE capture_id = ?"
            "   AND observation_type = 'claude.stream.system.permission_denied'",
            (capture_id,),
        )[0][0]
    )


def _result(capture_id: str) -> dict[str, Any]:
    """The stream result message of one capture, as the store kept it. {} when none."""
    rows = _query(
        "SELECT payload FROM observations WHERE capture_id = ?"
        "   AND observation_type = 'claude.stream.result'"
        " ORDER BY ingest_ts DESC LIMIT 1",
        (capture_id,),
    )
    return {} if not rows else dict(json.loads(rows[0][0]))


# -- the decision table ----------------------------------------------------------------


def decide(report: dict[str, Any]) -> dict[str, Any]:
    """The rule above, applied per measure, and every number it was applied to."""
    rows = [
        _decision(metric, found) for metric, found in sorted(report["stats"].items())
    ]
    return {
        "experiment": EXPERIMENT,
        "task_id": TASK_ID,
        "created_at": report["created_at"],
        "base_sha": report["spec"]["base_sha"],
        "command": report["spec"]["command"],
        "acceptance_command": report["spec"]["acceptance"],
        "environment_fingerprint_id": report["environment_fingerprint_id"],
        "captures": report["captures"],
        "acceptance": report["acceptance"],
        "repetitions": [_repetition(run) for run in report["repetitions"]],
        "rule": RULE,
        "constants": {
            "power_z": between.POWER_Z,
            "relative_resolution": between.RESOLUTION,
            "mad_scale": 1.4826,
            "repetitions": len(report["captures"]),
        },
        "decisions": rows,
        "demoted": sorted(row["metric"] for row in rows if row["verdict"] == DEMOTED),
        "no_variation": sorted(
            row["metric"] for row in rows if row["verdict"] == NO_VARIATION
        ),
        "not_judged": sorted(
            row["metric"] for row in rows if row["verdict"].startswith("not judged")
        ),
        "claim_class": {"per_capture": "derived", "decision": "comparative"},
        "cohort": report["cohort"],
        "assumptions": list(report["assumptions"]),
        "warnings": list(report["warnings"]),
        "sources": {
            "report": "experiments/E05/out/E05/report.json",
            "spec": "experiments/E05/out/spec.json",
            "runner_stdout": "experiments/E05/out/repeat.stdout.txt",
        },
    }


def _decision(metric: str, found: dict[str, Any]) -> dict[str, Any]:
    """One measure's row. Every branch of the rule, and no fifth answer."""
    n, median, spread = found["n"], found["median"], found["mad_scaled"]
    row: dict[str, Any] = {
        "metric": metric,
        "n": n,
        "unknown": found["unknown"],
        "median": median,
        "s": spread,
        "mdd": between.mdd(spread, n),
        "n_needed": None,
        "n_needed_note": None,
        "demoted": None,
        "verdict": "",
        "values": found["values"],
    }
    if n < REPETITIONS:
        row["verdict"] = f"not judged: {n} known value(s) of {REPETITIONS}"
        return row
    if spread == 0:
        row["verdict"] = NO_VARIATION
        return row
    row["demoted"] = row["mdd"] > between.RESOLUTION * abs(median)
    row["n_needed"] = between.n_needed(spread, median)
    if median == 0:
        row["n_needed_note"] = NOT_COMPUTABLE
    row["verdict"] = DEMOTED if row["demoted"] else RESOLVABLE
    return row


def _repetition(run: dict[str, Any]) -> dict[str, Any]:
    """One session as the write-up quotes it: the harness's numbers and the child's."""
    capture_id = str(run["capture_id"])
    tokens, missing = _token_total(capture_id)
    result = _result(capture_id)
    return {
        "attempt": run["attempt"],
        "capture_id": capture_id,
        "exit_code": run["exit_code"],
        "acceptance": run["acceptance"]["status"],
        "acceptance_exit_code": run["acceptance"]["exit_code"],
        "wall_ms": run["wall_ms"],
        # capture_started to capture_ended on the arrival clock. Present only on a
        # report rebuilt by `from_store`, where the wrapper's wall time is gone.
        "capture_span_ms": run.get("capture_span_ms"),
        "duration_ms": run["duration_ms"],
        "tokens_total": tokens,
        "tokens_missing_counters": missing,
        "coverage": run["coverage"],
        "provider_session_id": run["provider_session_id"],
        "result_subtype": result.get("subtype"),
        "result_is_error": result.get("is_error"),
        "num_turns": result.get("num_turns"),
        "total_cost_usd": result.get("total_cost_usd"),
        "permission_denials": result.get("permission_denials"),
        "permission_denied_observations": _permission_denied(capture_id),
        "stop_reason": result.get("stop_reason"),
    }


# -- printing --------------------------------------------------------------------------


def _table(decision: dict[str, Any]) -> str:
    head = f"{'METRIC':52} {'N':>2} {'MEDIAN':>12} {'S':>12} {'MDD':>12} {'N_NEEDED':>9}  VERDICT"  # noqa: E501
    lines = [head]
    for row in decision["decisions"]:
        needed = row["n_needed_note"] or (
            "" if row["n_needed"] is None else str(row["n_needed"])
        )
        lines.append(
            f"{row['metric']:52} {row['n']:>2} {_cell(row['median']):>12}"
            f" {_cell(row['s']):>12} {_cell(row['mdd']):>12} {needed:>9}"
            f"  {row['verdict']}"
        )
    return "\n".join(lines)


def _cell(value: float | None) -> str:
    if value is None:
        return "unknown"
    return str(int(value)) if float(value).is_integer() else f"{value:.3f}"


# -- recovery ---------------------------------------------------------------------


def rebuild() -> None:
    """out/E05/report.json from the captures already in the store. Starts nothing.

    Home is `$TELLTALE_HOME` or `~/.telltale`, the same one the sessions were written
    into, and this opens it read-write only because `Store` has one constructor; the
    call below writes no row.
    """
    home = DB.parent
    report = experiments.from_store(spec_as_run(), home, OUT)
    print(report_module.experiment(report))


def finish_attempt(attempt: int) -> int:
    """The two records a killed runner never wrote, for one repetition.

    The acceptance command is run HERE, by the harness, in the worktree the killed
    runner left behind, so the recorded outcome is measured rather than decided. The
    worktree is removed afterwards, which is what `_repetition` would have done.
    """
    written = spec_as_run()
    worktree = DB.parent / "worktrees" / f"{EXPERIMENT}-{TASK_ID}-{attempt}"
    if not worktree.exists():
        print(f"no worktree at {worktree}: nothing to finish")
        return 2
    done = experiments.finish(written, DB.parent, attempt, worktree)
    print(
        f"attempt {done['attempt']} {done['capture_id']}:"
        f" acceptance {done['acceptance']['status']}"
        f" (exit {done['acceptance']['exit_code']}), worktree removed"
    )
    return 0


# -- the command -----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="E05 repeated task variance, H2 pilot")
    parser.add_argument(
        "--decide-only",
        action="store_true",
        help="recompute out/decision.json from the report already written",
    )
    parser.add_argument(
        "--from-store",
        action="store_true",
        help="rebuild the report from the captures already in the store, run nothing",
    )
    parser.add_argument(
        "--finish-attempt",
        type=int,
        metavar="N",
        help="record the correlation and acceptance of a repetition the runner"
        " never closed, running the acceptance command in the worktree it left",
    )
    args = parser.parse_args()
    # Line buffered, so a killed run leaves the lines it had already printed. The five
    # sessions of 2026-09-02 left an empty repeat.stdout.txt for exactly this reason.
    sys.stdout.reconfigure(line_buffering=True)

    if args.finish_attempt is not None:
        return finish_attempt(args.finish_attempt)
    if args.from_store:
        rebuild()
    elif not args.decide_only:
        repo, base_sha = prepare()
        written = spec(repo, base_sha)
        (OUT / "spec.json").write_text(json.dumps(written, indent=2) + "\n", "utf-8")
        print(f"repo {repo} at {base_sha}")
        run = run_repeat(OUT / "spec.json")
        kept = {name: value for name, value in run.items() if name != "stdout"}
        (OUT / "runner.json").write_text(json.dumps(kept, indent=2) + "\n", "utf-8")
        print(run["stdout"])
        print(f"runner wall {run['wall_s']} s, exit {run['exit_code']}")
        if run["stopped_by_the_gate"] is not None:
            print(f"STOP: {run['stopped_by_the_gate']}")
            return 2

    path = OUT / TASK_ID / "report.json"
    if not path.exists():
        print(f"no report at {path}")
        return 2
    decision = decide(json.loads(path.read_text(encoding="utf-8")))
    (OUT / "decision.json").write_text(json.dumps(decision, indent=2) + "\n", "utf-8")
    print(_table(decision))
    print(f"\nMDD = {between.POWER_Z} s sqrt(2 / n), n = {REPETITIONS},"
          f" sqrt(2 / {REPETITIONS}) = {sqrt(2 / REPETITIONS):.6f}")  # fmt: skip
    print(f"demoted: {len(decision['demoted'])}, no variation:"
          f" {len(decision['no_variation'])}, not judged:"
          f" {len(decision['not_judged'])}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
