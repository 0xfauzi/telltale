"""E06: does one launch flag move a measure further than the run-to-run floor?

The H3 pilot of design 6.12. Ten `claude -p --model sonnet` sessions on E05's
fix-the-test task, five per arm, differing in exactly one launch flag: `--effort low`
against `--effort high`. E05 measured how far a measure moves when NOTHING varies
except the run. This one measures how far it moves when one declared thing does, and
the between-arm rule is what separates the two: a shift larger than the minimal
detectable difference at n = 5 is a material environment effect for that measure, and a
shift smaller than it is not resolved at n = 5, which is a statement about n.

This script does four things and no more.

  It prepares out/repo, a copy of E01's fixture repository with its own git history, so
  the two experiments' worktrees never share a repository, and records the sha.

  It PROVES the acceptance command before any token is spent, in a scratch worktree of
  that commit: the command must exit non-zero on the unfixed code and 0 after the one
  line that guards the zero. E05's first attempt recorded a fail on every repetition and
  the write-up could not say from the harness alone whether that was the agent or the
  acceptance command. Both exit codes are in out/decision.json.

  It runs `telltale experiment environment` as a subprocess, times it with perf_counter,
  and watches the owner's stop rule on the first session from outside, because the
  runner prints nothing until all ten are done.

  It reads out/E06/environment.json and writes out/decision.json: the decision rule
  applied per measure, with the inequality and both of its numbers beside every verdict.

Nothing here re-implements the experiment. The two arms, the fingerprint assertions, the
per-arm tables and every between-arm number are `telltale experiment environment`, which
is `src/telltale/experiments_env.py` and `src/telltale/stats.py`. The repository
preparation, the command, the acceptance command and the prompt are E05's and E01's, by
import. What this file adds is the spec, the stop rule and the decision table.

Nothing here retries. A session that fails its acceptance command is a result, its
numbers stay in its arm's table, and the failure sits beside them.

Usage:
    uv run python experiments/E06/run.py                 # prepare, prove, run ten
    uv run python experiments/E06/run.py --from-store    # rebuild the report, run none
    uv run python experiments/E06/run.py --decide-only   # recompute out/decision.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from math import sqrt
from pathlib import Path
from typing import Any

from telltale import experiments, experiments_env
from telltale import report as report_module
from telltale import stats as between
from telltale.store import Store

E06_DIR = Path(__file__).resolve().parent
REPO_ROOT = E06_DIR.parents[1]
OUT = E06_DIR / "out"
HOME = Path(os.environ.get("TELLTALE_HOME") or "~/.telltale").expanduser()
DB = HOME / "telltale.db"

EXPERIMENT = "E06"
TASK_ID = os.environ.get("E06_TASK_ID", "E06")

# The factor, and the two values of it. Design 6.12 varies one launch flag at a time and
# names effort first. Claude Code 2.1.258 accepts low, medium, high, xhigh and max for
# `--effort`; it WARNS on any other value and runs at the default, so a typo here would
# be a third condition wearing one of these two names. Measured, not assumed:
# `claude --effort bogus --version` prints
# "Unknown --effort value 'bogus' - ignoring it and using the default effort".
FACTOR = "effort"
ARM_VALUES = ("low", "high")

# Five per arm, because the owner approved ten real sessions for this task on
# 2026-09-02. Overridable ONLY so the whole pipeline can be rehearsed against a stand-in
# agent that spends nothing; out/spec.json records the argv and the count that ran.
REPETITIONS_PER_ARM = int(os.environ.get("E06_REPETITIONS", "5"))

# The owner's stop rule, on the FIRST session of the first arm. E05's five sessions of
# this task measured 13.6 to 15.2 s of capture span and 173,496 to 173,713 tokens; a
# high-effort session may cost more, and that is a measurement this experiment reports
# rather than a reason to cap it. These two limits are the owner's and are watched on
# the first session only.
FIRST_MAX_WALL_S = 180.0
FIRST_MAX_TOKENS = 600_000
POLL_S = 2.0
# Ten sessions of E05's size with room to spare. The first-session gate is the real
# limit; this one only stops a hung runner from waiting forever.
RUNNER_TIMEOUT_S = 2400.0

# The decision rule, from design 6.12, in the words of the brief for this task, which
# was written before any session started. Copied into out/decision.json and into
# docs/experiments/E06.md so the three cannot drift.
RULE = (
    "Per measure: the factor has a material environment effect iff |HL shift| > MDD,"
    " where MDD = 2.8 s sqrt(2 / n) with s the pooled scaled MAD the runner prints;"
    ' otherwise "not resolved at n = 5", never "no effect". A measure whose s is 0 in'
    " both arms and whose values are identical across arms reads"
    ' "no variation and no shift at n = 5". The per-arm demotion rule of E05 applies to'
    " each arm's own floors. A material effect makes the effort fingerprint a"
    " changepoint for that measure (design 6.12); the write-up says which measures, and"
    " says that a changepoint is a statement about this task on this day."
)

# The verdict vocabulary, and the whole of it. Two of the three strings are stats.py's,
# so the decision file and the runner's own table cannot disagree about a label; the
# third is the brief's narrowing of "not resolved" for a measure that did not move at
# all. None of them says effect of, cause, impact or "no effect".
MATERIAL = between.MATERIAL
UNRESOLVED = f"{between.UNRESOLVED} = {REPETITIONS_PER_ARM}"
NO_VARIATION = f"no variation and no shift at n = {REPETITIONS_PER_ARM}"

# The one line that fixes the fixture, and the line it goes before. Used only by the
# acceptance proof, in a scratch worktree that is removed afterwards: no session ever
# sees it, and out/repo is left at the base commit.
FIX_ANCHOR = "    return left / right\n"
FIX_LINE = '    if right == 0: raise ValueError("divide by zero")\n'

_E05 = REPO_ROOT / "experiments" / "E05" / "run.py"


def _e05() -> Any:
    """E05's runner as a module. Loaded by path: every experiment's runner is run.py.

    Registered in sys.modules BEFORE it is executed, for the reason E04 records: E01's
    Scenario is a frozen dataclass and @dataclass looks its own module up by name. E05
    loads E01 the same way, so importing E05 gives both and neither is loaded twice.
    """
    spec = importlib.util.spec_from_file_location("e05_run", _E05)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_E05}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


E05 = _e05()
E01 = E05.E01


# -- the condition ---------------------------------------------------------------------


def prepare() -> tuple[Path, str]:
    """out/repo: a copy of E01's fixture with one commit of its own, under E06's out.

    E01's `prepare_repo` is the preparation, reached through E05 so one loader owns it.
    E05's own `prepare()` writes into E05's out directory, and the injected facts of
    this task ask for a separate copy so the two experiments' worktrees never share a
    repository: same fixture files, a commit of their own, a different sha.

    The privacy probes in secrets_note.txt are left in place. Their presence is what
    re-tests the claim that no secret reaches the store, on ten more sessions.
    """
    repo = OUT / "repo"
    if repo.exists():
        shutil.rmtree(repo)
    OUT.mkdir(parents=True, exist_ok=True)
    prepared = Path(E01.prepare_repo(OUT))
    return prepared, _sha(prepared)


def _sha(repo: Path) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _arm_command(effort: str) -> list[str]:
    """E05's argv with `--effort <value>` after `--model sonnet`, then E01's prompt.

    Built from E05's COMMAND rather than retyped, so the two experiments cannot drift in
    the flags that are NOT the factor: the permission mode, the turn cap and the output
    format are E05's, and `experiments._approvable` refuses a headless claude without
    bypassPermissions before the store is opened.
    """
    words = [str(word) for word in E05.COMMAND]
    after = words.index("--model") + 2
    return [*words[:after], "--effort", effort, *words[after:], E01.FIX_PROMPT]


def _command(effort: str) -> list[str]:
    """The agent under test: the arm's command, or the stand-in of a rehearsal."""
    stand_in = os.environ.get(f"E06_AGENT_{effort.upper()}")
    if stand_in is None:
        return _arm_command(effort)
    return [str(word) for word in json.loads(stand_in)]


def spec(repo: Path, base_sha: str) -> dict[str, Any]:
    """The spec the runner takes. Ten keys, every one of them the brief's."""
    return {
        "task_id": TASK_ID,
        "experiment": EXPERIMENT,
        "repo": str(repo),
        "base_sha": base_sha,
        "acceptance": list(E05.ACCEPTANCE),
        "repetitions_per_arm": REPETITIONS_PER_ARM,
        "provider": "claude",
        "level": 1,
        "factor": FACTOR,
        "arms": [{"name": value, "command": _command(value)} for value in ARM_VALUES],
    }


def condition() -> dict[str, Any]:
    """The spec to run, reusing the one already written when it is still standing.

    A second call of this script is a RESUME: `repeat` reads back any attempt the store
    already holds a capture for instead of running it again, and those ran in worktrees
    of one particular base commit. Rebuilding out/repo here would give it a new commit
    sha, and the report would then name one base_sha for captures that did not share it.
    """
    path = OUT / "spec.json"
    if path.exists():
        written = dict(json.loads(path.read_text(encoding="utf-8")))
        repo = Path(str(written["repo"]))
        if repo.exists() and _sha(repo) == written["base_sha"]:
            return _grown(path, written, repo)
    repo, base_sha = prepare()
    written = spec(repo, base_sha)
    _write(path, written)
    print(f"repo {repo} at {base_sha}, task {TASK_ID}")
    return written


def _grown(path: Path, written: dict[str, Any], repo: Path) -> dict[str, Any]:
    """The stored spec with today's repetition count. The arms may not have moved.

    A condition grows by repetitions and by nothing else. A stored spec whose arms
    differ from this file's is a different experiment wearing the same task id, and
    design 6.12 says changing one after a result is a new experiment id, so that is
    refused here rather than merged.
    """
    wanted = spec(repo, str(written["base_sha"]))
    if written["arms"] != wanted["arms"]:
        raise SystemExit(
            f"{path.name} was written for different arms; an experiment whose arms"
            " changed is a new experiment and needs a new experiment id"
        )
    written["repetitions_per_arm"] = REPETITIONS_PER_ARM
    _write(path, written)
    print(
        f"resuming {path.name}: repo {repo} at {written['base_sha']},"
        f" {REPETITIONS_PER_ARM} repetitions per arm"
    )
    return written


def _write(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


# -- the acceptance command, proved before anything is spent ---------------------------


def prove_acceptance(written: dict[str, Any]) -> dict[str, Any]:
    """The acceptance command must fail on the base commit and pass after the fix.

    Run in a scratch worktree of the base commit, the same way `experiments._accept`
    runs it after each session: same argv, same cwd shape, same harness. An acceptance
    command that cannot tell the fixed tree from the unfixed one reports the same
    status whatever the agent does, and E05's first attempt could not say from the
    harness alone whether five fails were the agent or the command.

    Refuses rather than returns when either exit code is the wrong one: a run under an
    acceptance command that has not been proved is ten sessions whose outcome column
    means nothing.
    """
    repo = Path(str(written["repo"]))
    worktree = OUT / "acceptance-proof"
    argv = [str(word) for word in written["acceptance"]]
    _git(repo, "worktree", "add", "--detach", str(worktree), str(written["base_sha"]))
    try:
        unfixed = _run_acceptance(argv, worktree)
        calc = worktree / "pkg" / "calc.py"
        source = calc.read_text(encoding="utf-8")
        if FIX_ANCHOR not in source:
            raise SystemExit(f"{calc} does not contain {FIX_ANCHOR!r}: no fix to apply")
        calc.write_text(source.replace(FIX_ANCHOR, FIX_LINE + FIX_ANCHOR), "utf-8")
        fixed = _run_acceptance(argv, worktree)
    finally:
        _git(repo, "worktree", "remove", "--force", str(worktree))
        _git(repo, "worktree", "prune")
    proof = {
        "command": argv,
        "base_sha": str(written["base_sha"]),
        "fix": FIX_LINE.strip(),
        "unfixed_exit_code": unfixed["exit_code"],
        "unfixed_tail": unfixed["tail"],
        "fixed_exit_code": fixed["exit_code"],
        "fixed_tail": fixed["tail"],
        "proved": unfixed["exit_code"] != 0 and fixed["exit_code"] == 0,
    }
    print(
        f"acceptance proof: unfixed exit {proof['unfixed_exit_code']},"
        f" fixed exit {proof['fixed_exit_code']}"
    )
    if not proof["proved"]:
        raise SystemExit(
            "the acceptance command does not separate the fixed tree from the unfixed"
            f" one ({proof['unfixed_exit_code']} then {proof['fixed_exit_code']}):"
            " nothing is run"
        )
    return proof


def _run_acceptance(argv: list[str], worktree: Path) -> dict[str, Any]:
    done = subprocess.run(argv, cwd=worktree, capture_output=True, check=False)
    tail = done.stdout.decode("utf-8", "replace").strip().splitlines()
    return {"exit_code": done.returncode, "tail": tail[-1:] or [""]}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


# -- running the runner, and the owner's stop rule -------------------------------------


def run_environment(spec_path: Path) -> dict[str, Any]:
    """`uv run telltale experiment environment`, timed, with the first-session gate.

    `--project` pins which project uv resolves, for E04's reason: the runner's children
    run in copies of a fixture repository that has a pyproject.toml of its own.
    `start_new_session` puts the whole tree in one process group, so the gate can stop
    the agent and not just the runner that started it.
    """
    argv = [
        "uv", "run", "--project", str(REPO_ROOT), "telltale", "experiment",
        "environment", str(spec_path), "--out", str(OUT),
    ]  # fmt: skip
    with (
        (OUT / "environment.stdout.txt").open("wb") as out_fh,
        (OUT / "environment.stderr.txt").open("wb") as err_fh,
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
        "stdout": (OUT / "environment.stdout.txt").read_text(encoding="utf-8"),
        "stderr_head": (OUT / "environment.stderr.txt").read_text(
            encoding="utf-8", errors="replace"
        )[:4000],
    }


def _gate(child: subprocess.Popen[bytes], start: float) -> str | None:
    """The owner's stop rule on the first session, watched from outside the runner.

    The first session is attempt 1 of the first arm, because `experiments_env` runs
    the arms in the order the spec lists them. Once that capture has ended inside both
    limits the gate is done: the owner set it on the first session, and the nine that
    follow are the experiment.
    """
    first = f"{TASK_ID}-{ARM_VALUES[0]}"
    while child.poll() is None:
        time.sleep(POLL_S)
        capture = _capture_of(first, 1)
        if capture is not None and _ended(capture):
            tokens, missing = E05._token_total(capture)
            if tokens is not None and tokens > FIRST_MAX_TOKENS:
                return _kill(
                    child, f"session 1 spent {tokens} tokens (missing counters:"
                    f" {missing}), over the {FIRST_MAX_TOKENS} limit"
                )  # fmt: skip
            return None
        if time.perf_counter() - start > FIRST_MAX_WALL_S:
            return _kill(
                child,
                f"session 1 did not end within {FIRST_MAX_WALL_S} s of wall time",
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
    return f"{why}; a worktree may be left under {HOME / 'worktrees'}"


def _capture_of(task_id: str, attempt: int) -> str | None:
    """The newest E06 capture claiming this attempt of this arm's task, or None."""
    rows = E05._query(
        "SELECT capture_id FROM observations"
        " WHERE observation_type = 'telltale.capture_started'"
        "   AND json_extract(payload, '$.experiment') = ?"
        "   AND json_extract(payload, '$.task_id') = ?"
        "   AND json_extract(payload, '$.attempt') = ?"
        " ORDER BY ingest_ts DESC, observation_id DESC LIMIT 1",
        (EXPERIMENT, task_id, attempt),
    )
    return None if not rows else str(rows[0][0])


def _ended(capture_id: str) -> bool:
    return bool(
        E05._query(
            "SELECT 1 FROM observations WHERE capture_id = ?"
            "   AND observation_type = 'telltale.capture_ended' LIMIT 1",
            (capture_id,),
        )
    )


# -- recovery --------------------------------------------------------------------------


def rebuild(written: dict[str, Any]) -> dict[str, Any]:
    """out/E06/environment.json from the captures already in the store. Starts nothing.

    `telltale experiment environment` writes nothing until both arms are done, so a
    runner killed part-way leaves captures and no report. Each arm is rebuilt by
    `experiments.from_store`, and the fingerprint assertion and the between-arm table
    are then `experiments_env`'s own, called rather than re-implemented: a recovered
    report that computed its own statistics would be a second implementation of the
    thing being reported.
    """
    checked = experiments_env._checked_environment(written)
    arms = [
        {
            "name": str(arm["name"]),
            "report": experiments.from_store(
                experiments_env._arm_spec(checked, arm), HOME, OUT
            ),
        }
        for arm in checked["arms"]
    ]
    store = Store(DB)
    try:
        assertion = experiments_env._assert_between(store, checked, arms)
    finally:
        store.close()
    return experiments_env._environment_report(checked, arms, assertion, OUT)


# -- the decision table ----------------------------------------------------------------


def decide(measured: dict[str, Any], proof: dict[str, Any] | None) -> dict[str, Any]:
    """The rule above, applied per measure, and every number it was applied to."""
    arms = {str(arm["name"]): arm for arm in measured["arms"]}
    rows = [
        _decision(metric, row, arms)
        for metric, row in sorted(measured["between"].items())
    ]
    return {
        "experiment": EXPERIMENT,
        "task_id": TASK_ID,
        "created_at": measured["created_at"],
        "factor": measured["factor"],
        "base_sha": measured["spec"]["base_sha"],
        "acceptance_command": measured["spec"]["acceptance"],
        "acceptance_proof": proof,
        "fingerprint_assertion": measured["fingerprint_assertion"],
        "arms": [_arm(arms[name]) for name in ARM_VALUES if name in arms],
        "rule": RULE,
        "constants": {
            **measured["constants"],
            "repetitions_per_arm": REPETITIONS_PER_ARM,
        },
        "decisions": rows,
        "material": sorted(row["metric"] for row in rows if row["verdict"] == MATERIAL),
        "not_resolved": sorted(
            row["metric"] for row in rows if row["verdict"] == UNRESOLVED
        ),
        "no_variation": sorted(
            row["metric"] for row in rows if row["verdict"] == NO_VARIATION
        ),
        "not_judged": sorted(
            row["metric"] for row in rows if row["verdict"].startswith("not judged")
        ),
        "claim_class": {
            "per_capture": "derived",
            "per_arm": "comparative",
            "between": between.CLAIM_CLASS,
        },
        "assumptions": list(measured["assumptions"]),
        "warnings": list(measured["warnings"]),
        "sources": {
            "environment": f"experiments/E06/out/{TASK_ID}/environment.json",
            "spec": "experiments/E06/out/spec.json",
            "runner_stdout": "experiments/E06/out/environment.stdout.txt",
            "arm_reports": [
                f"experiments/E06/out/{TASK_ID}-{name}/report.json"
                for name in ARM_VALUES
            ],
        },
    }


def _decision(
    metric: str, row: dict[str, Any], arms: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """One measure's row: the between-arm inequality, and each arm's own floor.

    `runner_label` is what `stats.compare` put on the row and `verdict` is this rule's
    reading of it. They agree except on the one case the brief narrows: a measure with
    no spread in either arm and the same values in both is "no variation and no shift",
    which the runner would call "not resolved" because a shift of 0 is not above an MDD
    of 0.
    """
    shift, limit = row["hl_shift"], row["mdd"]
    values = {name: arms[name]["report"]["stats"].get(metric) for name in arms}
    out: dict[str, Any] = {
        "metric": metric,
        **{name: row[name] for name in ("n_a", "n_b", "n", "s_a", "s_b", "s")},
        "hl_shift": shift,
        "cliffs_delta": row["cliffs_delta"],
        "u": row["u"],
        "p": row["p"],
        "median": row["median"],
        "mdd": limit,
        "n_needed": row["n_needed"],
        "demoted_between": row["demoted"],
        "inequality": _inequality(shift, limit),
        "runner_label": row["label"],
        "verdict": _verdict(row, values),
        "values": {name: (found or {}).get("values") for name, found in values.items()},
        "per_arm": {
            name: E05._decision(metric, found)
            for name, found in values.items()
            if found is not None
        },
        "warnings": list(row["warnings"]),
    }
    out["changepoint"] = out["verdict"] == MATERIAL
    return out


def _verdict(row: dict[str, Any], values: dict[str, Any]) -> str:
    """The rule's four answers, and no fifth one."""
    if row["hl_shift"] is None or row["mdd"] is None:
        return f"not judged: n_a = {row['n_a']}, n_b = {row['n_b']}"
    spreads = [(found or {}).get("mad_scaled") for found in values.values()]
    sorted_values = [
        sorted((found or {}).get("values") or []) for found in values.values()
    ]
    if (
        all(spread == 0 for spread in spreads)
        and len(set(map(tuple, sorted_values))) == 1
    ):
        return NO_VARIATION
    return MATERIAL if abs(row["hl_shift"]) > row["mdd"] else UNRESOLVED


def _inequality(shift: float | None, limit: float | None) -> str:
    """The rule as it was applied to this row, with both of its numbers in it."""
    if shift is None or limit is None:
        return "not computable: the shift or the spread is unknown at this n"
    sign = ">" if abs(shift) > limit else "<=" if abs(shift) < limit else "="
    return f"|HL shift| = {_cell(abs(shift))} {sign} MDD = {_cell(limit)}"


def _arm(arm: dict[str, Any]) -> dict[str, Any]:
    """One arm as the write-up quotes it: the command, the fingerprint, the sessions."""
    report = arm["report"]
    return {
        "name": arm["name"],
        "task_id": report["task_id"],
        "command": report["spec"]["command"],
        "effort": _effort(report["spec"]["command"]),
        "environment_fingerprint_id": report["environment_fingerprint_id"],
        "captures": report["captures"],
        "acceptance": report["acceptance"],
        "sessions": [E05._repetition(run) for run in report["repetitions"]],
    }


def _effort(command: list[Any]) -> str | None:
    words = [str(word) for word in command]
    return None if "--effort" not in words else words[words.index("--effort") + 1]


# -- printing --------------------------------------------------------------------------


def _table(decision: dict[str, Any]) -> str:
    head = (
        f"{'METRIC':52} {'HL_SHIFT':>10} {'MDD':>10} {'P':>7} {'DELTA':>6}"
        f" {'N_NEEDED':>8}  VERDICT"
    )
    lines = [head]
    for row in decision["decisions"]:
        needed = "" if row["n_needed"] is None else str(row["n_needed"])
        lines.append(
            f"{row['metric']:52} {_cell(row['hl_shift']):>10} {_cell(row['mdd']):>10}"
            f" {_cell(row['p']):>7} {_cell(row['cliffs_delta']):>6} {needed:>8}"
            f"  {row['verdict']}"
        )
    return "\n".join(lines)


def _cell(value: float | None) -> str:
    if value is None:
        return "unknown"
    return str(int(value)) if float(value).is_integer() else f"{value:.3f}"


# -- the command -----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="E06 environment sensitivity, H3 pilot"
    )
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
    args = parser.parse_args()
    # Line buffered, so a killed run leaves the lines it had already printed. E05's five
    # sessions of 2026-09-02 left an empty stdout file for exactly this reason.
    sys.stdout.reconfigure(line_buffering=True)

    written = condition()
    proof_path = OUT / "acceptance-proof.json"
    proof: dict[str, Any] | None = None
    if not (args.decide_only or args.from_store):
        proof = prove_acceptance(written)
        _write(proof_path, proof)
        run = run_environment(OUT / "spec.json")
        _write(OUT / "runner.json", {k: v for k, v in run.items() if k != "stdout"})
        print(run["stdout"])
        print(f"runner wall {run['wall_s']} s, exit {run['exit_code']}")
        if run["stopped_by_the_gate"] is not None:
            print(f"STOP: {run['stopped_by_the_gate']}")
            return 2
    if proof is None and proof_path.exists():
        proof = dict(json.loads(proof_path.read_text(encoding="utf-8")))

    if args.from_store:
        measured = rebuild(written)
        print(report_module.environment(measured))
    else:
        path = OUT / TASK_ID / "environment.json"
        if not path.exists():
            print(f"no report at {path}")
            return 2
        measured = dict(json.loads(path.read_text(encoding="utf-8")))
    decision = decide(measured, proof)
    _write(OUT / "decision.json", decision)
    print(_table(decision))
    print(f"\nMDD = {between.POWER_Z} s sqrt(2 / n), n = {REPETITIONS_PER_ARM},"
          f" sqrt(2 / {REPETITIONS_PER_ARM})"
          f" = {sqrt(2 / REPETITIONS_PER_ARM):.6f}")  # fmt: skip
    print(f"material: {len(decision['material'])}, not resolved:"
          f" {len(decision['not_resolved'])}, no variation:"
          f" {len(decision['no_variation'])}, not judged:"
          f" {len(decision['not_judged'])}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
