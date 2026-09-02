"""E04: what recording costs a session, and whether it still fails open under load.

Two arms, and they answer two different questions.

The overhead arm runs the SAME headless Claude Code session twice, once wrapped in
`telltale run` (ON) and once bare (OFF), five times, interleaved ON, OFF, ON, OFF, ...
E01 has one such pair (S2 against S6, +0.913 s) and one pair cannot tell an overhead
from a slow morning. Five pairs cannot resolve a small one either, which is the point:
the decision rule in docs/experiments/E04.md compares the median delta against the OFF
arm's own spread and says "not resolved at n = 5" when it loses.

The trap this script exists to avoid: this process runs under `telltale run`, so its
environment already carries the capture variables the launcher sets. A child that
inherits them exports to the ORCHESTRATOR's receiver and is not off. `stripped_env`
removes every name starting with OTEL_ and the name CLAUDE_CODE_ENABLE_TELEMETRY, and
records the names it removed, because the list is a fact about the machine this ran on
rather than a constant.

The load arm spends no tokens. A scripted agent (tests/integration/fake_agent.py) is
captured twice, once against a quiet receiver and once against one being flooded by
experiments/E04/load.py, and the two captures are compared row for row.

Usage:
    uv run python experiments/E04/run.py --floor    # the wrapper's cost, no agent
    uv run python experiments/E04/run.py --pilot    # ONE pair, and the budget gate
    uv run python experiments/E04/run.py --all      # pairs 2 to 5
    uv run python experiments/E04/run.py --load     # the fail-open arm, no tokens
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

E04_DIR = Path(__file__).resolve().parent
REPO_ROOT = E04_DIR.parents[1]
OUT = E04_DIR / "out"
LOAD = E04_DIR / "load.py"
CHILD = E04_DIR / "child.py"
DB = Path(os.environ.get("TELLTALE_HOME") or "~/.telltale").expanduser() / "telltale.db"

PAIRS = 5
# The owner's pilot budget. E01 measured S2 at 8.772 s and 99,661 tokens.
PILOT_MAX_WALL_S = 60.0
PILOT_MAX_TOKENS = 200_000
CHILD_TIMEOUT_S = 300.0

# Removed from the OFF arm's environment. A PREFIX and a NAME, not a list of names: the
# orchestrator's launcher chooses which OTEL_ variables to set and a list written here
# would go stale the day it sets one more.
STRIP_PREFIX = "OTEL_"
STRIP_NAME = "CLAUDE_CODE_ENABLE_TELEMETRY"

# The load arm's shape. The flood starts LOAD_DELAY_S into the capture and runs
# LOAD_SECONDS; the agent runs HOLD_BEFORE_S in, which is inside the flood, and the
# capture stays alive HOLD_AFTER_S after the agent exits, which outlasts the flood.
LOAD_DELAY_S = 1.0
LOAD_SECONDS = 20.0
LOAD_THREADS = 4
HOLD_BEFORE_S = 6.0
HOLD_AFTER_S = 16.0
ENDPOINT_WAIT_S = 30.0

FLOOR_RUNS = 10

_E01 = REPO_ROOT / "experiments" / "E01" / "run.py"


def _e01() -> Any:
    """E01's runner as a module. Loaded by path: both files are named run.py.

    Registered in sys.modules BEFORE it is executed, which is not decoration: E01's
    Scenario is a frozen dataclass, and @dataclass looks its own module up by name to
    resolve annotations. Without the line it raises while the class body runs.
    """
    spec = importlib.util.spec_from_file_location("e01_run", _E01)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_E01}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


E01 = _e01()


# -- the two arms' command lines --------------------------------------------------


def agent_argv() -> list[str]:
    """The session under test. Identical in both arms, byte for byte."""
    return [
        "claude",
        "-p",
        "--model",
        "sonnet",
        "--permission-mode",
        "acceptEdits",
        "--max-turns",
        "8",
        "--output-format",
        "stream-json",
        "--verbose",
        E01.EXPLORE_PROMPT,
    ]


def launcher_argv(task_id: str, attempt: int, child: list[str]) -> list[str]:
    """`telltale run` around a child. `--project` names which project uv resolves.

    The brief's command is `uv run telltale run ...`, and `uv run` resolves the project
    from the CURRENT DIRECTORY: every session here runs in a copy of E01's fixture
    repository, which has a pyproject.toml of its own (`e01-fixture`, no telltale
    dependency), so a bare `uv run` from there resolves the wrong project. `--project`
    pins it to this checkout and changes nothing else.
    """
    return [
        "uv",
        "run",
        "--project",
        str(REPO_ROOT),
        "telltale",
        "run",
        "--provider",
        "claude",
        "--level",
        "1",
        "--task-id",
        task_id,
        "--attempt",
        str(attempt),
        "--experiment",
        "E04",
        "--",
        *child,
    ]


def stripped_env() -> tuple[dict[str, str], list[str]]:
    """The OFF arm's environment, and the names taken out of it.

    Removed, never emptied: an empty OTEL_EXPORTER_OTLP_ENDPOINT is a second wrong
    answer rather than no answer, and CLAUDE_CODE_ENABLE_TELEMETRY=1 with no endpoint
    still turns an exporter on.
    """
    env = dict(os.environ)
    removed = sorted(
        name for name in env if name.startswith(STRIP_PREFIX) or name == STRIP_NAME
    )
    for name in removed:
        env.pop(name)
    return env, removed


# -- running one session ------------------------------------------------------------


def session(
    argv: list[str], cwd: Path, env: dict[str, str], out_dir: Path, stem: str
) -> dict[str, Any]:
    """Run one child to completion and return the timing facts. Never raises."""
    stream = out_dir / f"{stem}.stream.jsonl"
    stderr = out_dir / f"{stem}.stderr.txt"
    with stream.open("wb") as out_fh, stderr.open("wb") as err_fh:
        start = time.perf_counter()
        try:
            done = subprocess.run(
                argv,
                cwd=str(cwd),
                env=env,
                stdout=out_fh,
                stderr=err_fh,
                stdin=subprocess.DEVNULL,
                timeout=CHILD_TIMEOUT_S,
                check=False,
            )
            code: int | None = done.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            code = None
            timed_out = True
        wall = time.perf_counter() - start
    return {
        "argv": argv,
        "cwd": str(cwd),
        "wall_s": round(wall, 3),
        "exit_code": code,
        "timed_out": timed_out,
        "stdout_bytes": stream.stat().st_size,
        "stderr_bytes": stderr.stat().st_size,
        "stderr_head": stderr.read_text(encoding="utf-8", errors="replace")[:2000],
        "stream_path": str(stream.relative_to(E04_DIR)),
    }


def result_facts(stream: Path) -> dict[str, Any]:
    """What the child's own result line says. Absent stays None, never 0."""
    result = E01.result_message(stream)
    facts = E01.result_facts(result)
    usage = facts["usage"]
    tokens, missing = E01.token_total(usage if isinstance(usage, dict) else None)
    return {**facts, "tokens_total": tokens, "tokens_missing_keys": missing}


# -- reading the capture back -------------------------------------------------------


def _query(sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """One read-only query against the capture database. Never writes, never creates."""
    conn = sqlite3.connect(f"{DB.as_uri()}?mode=ro", uri=True, timeout=10.0)
    try:
        return list(conn.execute(sql, params))
    finally:
        conn.close()


def capture_of(task_id: str, attempt: int) -> str | None:
    """The newest E04 capture with this task id and attempt, or None."""
    rows = _query(
        "SELECT capture_id FROM observations"
        " WHERE observation_type = 'telltale.capture_started'"
        "   AND json_extract(payload, '$.experiment') = 'E04'"
        "   AND json_extract(payload, '$.task_id') = ?"
        "   AND json_extract(payload, '$.attempt') = ?"
        " ORDER BY ingest_ts DESC, observation_id DESC LIMIT 1",
        (task_id, attempt),
    )
    return None if not rows else str(rows[0][0])


def capture_facts(capture_id: str | None) -> dict[str, Any]:
    """capture_ended, the observation counts by surface, and the diagnostics.

    `dropped` diagnostics carry NO capture id (store.py writes them on the writer
    thread, outside any capture), so they are found by the capture's own time window
    instead. Counting only the rows that name the capture would report zero drops for
    a receiver that dropped thousands.
    """
    if capture_id is None:
        return {
            "capture_id": None,
            "capture_ended": None,
            "observations_by_surface": None,
            "diagnostics_by_kind": None,
            "dropped_diagnostics_in_window": None,
        }
    ended = _query(
        "SELECT payload FROM observations WHERE capture_id = ?"
        "   AND observation_type = 'telltale.capture_ended'"
        " ORDER BY ingest_ts DESC LIMIT 1",
        (capture_id,),
    )
    window = _query(
        "SELECT min(ingest_ts), max(ingest_ts) FROM observations WHERE capture_id = ?",
        (capture_id,),
    )
    first, last = window[0] if window else (None, None)
    dropped: list[dict[str, Any]] = []
    if first is not None:
        dropped = [
            {"ingest_ts": row[0], "detail": row[1]}
            for row in _query(
                "SELECT ingest_ts, detail FROM diagnostics"
                " WHERE kind = 'dropped' AND ingest_ts >= ? AND ingest_ts <= ?"
                " ORDER BY ingest_ts",
                (first, last),
            )
        ]
    return {
        "capture_id": capture_id,
        "capture_ended": json.loads(ended[0][0]) if ended else None,
        "observations_by_surface": dict(
            _query(
                "SELECT surface, count(*) FROM observations"
                " WHERE capture_id = ? GROUP BY 1 ORDER BY 1",
                (capture_id,),
            )
        ),
        "diagnostics_by_kind": dict(
            _query(
                "SELECT kind, count(*) FROM diagnostics"
                " WHERE capture_id = ? GROUP BY 1 ORDER BY 1",
                (capture_id,),
            )
        ),
        "window": {"first_ingest_ts": first, "last_ingest_ts": last},
        "dropped_diagnostics_in_window": dropped,
    }


# -- the overhead arm ----------------------------------------------------------------


def fresh(out_dir: Path) -> Path:
    """A fresh copy of E01's fixture repository, with its own git history."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    return Path(E01.prepare_repo(out_dir))


def one_arm(pair: int, arm: str) -> dict[str, Any]:
    """One session of one arm, its meta file written before anything reads it."""
    out_dir = OUT / f"pair{pair}" / arm
    repo = fresh(out_dir)
    if arm == "on":
        argv = launcher_argv("E04", pair, agent_argv())
        env, removed = dict(os.environ), []
    else:
        argv = agent_argv()
        env, removed = stripped_env()
    run = session(argv, repo, env, out_dir.parent, arm)
    meta = {
        "pair": pair,
        "arm": arm,
        "claude_version": E01.claude_version(),
        "env_removed": removed,
        **run,
        **result_facts(out_dir.parent / f"{arm}.stream.jsonl"),
    }
    if arm == "on":
        meta.update(capture_facts(capture_of("E04", pair)))
    path = OUT / f"pair{pair}" / f"{arm}.meta.json"
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def one_pair(pair: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """ON then OFF, in that order, each in its own fresh repository copy."""
    on = one_arm(pair, "on")
    print(_line(on))
    off = one_arm(pair, "off")
    print(_line(off))
    return on, off


def _line(meta: dict[str, Any]) -> str:
    return (
        f"pair {meta['pair']} {meta['arm']}: wall {meta['wall_s']} s, "
        f"exit {meta['exit_code']}, turns {meta['num_turns']}, "
        f"tokens {meta['tokens_total']}, duration_ms {meta['duration_ms']}"
    )


def within_budget(meta: dict[str, Any]) -> tuple[bool, str]:
    tokens = meta["tokens_total"]
    if meta["wall_s"] > PILOT_MAX_WALL_S:
        return False, f"wall {meta['wall_s']} s over the {PILOT_MAX_WALL_S} s limit"
    if tokens is None:
        return False, "the result message carried no token counters"
    if tokens > PILOT_MAX_TOKENS:
        return False, f"{tokens} tokens over the {PILOT_MAX_TOKENS} limit"
    if meta["exit_code"] != 0:
        return False, f"exit code {meta['exit_code']}"
    return True, f"wall {meta['wall_s']} s and {tokens} tokens, inside the limits"


# -- the floor: what the wrapper costs with no agent in it ---------------------------


def floor() -> dict[str, Any]:
    """`uv run --project ... telltale run -- true` against `true`, n each.

    Not in the brief. It is here because the ON arm's wall time contains three things
    (uv's own startup, the launcher, and the session) and the OFF arm's contains one,
    and without this the delta cannot be read as anything but their sum. It spends no
    tokens and starts no agent session.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    measured: dict[str, list[float]] = {"wrapped": [], "bare": []}
    argv = {
        "wrapped": [
            "uv", "run", "--project", str(REPO_ROOT), "telltale", "run", "--", "true"
        ],
        "bare": ["true"],
    }  # fmt: skip
    for _ in range(FLOOR_RUNS):
        for name in ("wrapped", "bare"):
            start = time.perf_counter()
            subprocess.run(
                argv[name], check=False, capture_output=True, cwd=str(REPO_ROOT)
            )
            measured[name].append(round(time.perf_counter() - start, 4))
    found = {
        "runs": FLOOR_RUNS,
        "argv": argv,
        "wrapped_s": stats(measured["wrapped"]),
        "bare_s": stats(measured["bare"]),
        "delta_median_s": round(
            stats(measured["wrapped"])["median"] - stats(measured["bare"])["median"], 4
        ),
    }
    (OUT / "floor.json").write_text(json.dumps(found, indent=2) + "\n", "utf-8")
    print(
        f"floor: wrapped median {found['wrapped_s']['median']} s, "
        f"bare median {found['bare_s']['median']} s, "
        f"delta {found['delta_median_s']} s"
    )
    return found


# -- the load arm --------------------------------------------------------------------


def load_repo(out_dir: Path) -> Path:
    """A throwaway git repository with the file the scripted agent rewrites."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    repo = out_dir / "repo"
    repo.mkdir()
    (repo / "answer.txt").write_text("0\n", encoding="utf-8")
    (repo / "README.md").write_text("E04 load arm fixture.\n", encoding="utf-8")
    E01.git(repo, "init", "-q", "-b", "main")
    for key, value in E01.GIT_IDENTITY:
        E01.git(repo, "config", key, value)
    E01.git(repo, "add", "-A")
    E01.git(repo, "commit", "-q", "--no-verify", "-m", "fixture")
    return repo


def load_child() -> list[str]:
    return [
        sys.executable,
        str(CHILD),
        "-p",
        "make the answer 42",
        "--output-format",
        "stream-json",
        "--seed",
        "7",
    ]


def load_env() -> dict[str, str]:
    return {
        **os.environ,
        "E04_HOLD_BEFORE_S": str(HOLD_BEFORE_S),
        "E04_HOLD_AFTER_S": str(HOLD_AFTER_S),
    }


def capture_under_load(out_dir: Path, repo: Path, flood: bool) -> dict[str, Any]:
    """One scripted-agent capture, with the load generator started against it or not."""
    attempt = 2 if flood else 1
    argv = launcher_argv("E04-load", attempt, load_child())
    stem = "loaded" if flood else "unloaded"
    stream = out_dir / f"{stem}.stream.jsonl"
    stderr = out_dir / f"{stem}.stderr.txt"
    generator: subprocess.Popen[bytes] | None = None
    with stream.open("wb") as out_fh, stderr.open("wb") as err_fh:
        start = time.perf_counter()
        child = subprocess.Popen(
            argv,
            cwd=str(repo),
            env=load_env(),
            stdout=out_fh,
            stderr=err_fh,
            stdin=subprocess.DEVNULL,
        )
        if flood:
            time.sleep(LOAD_DELAY_S)
            generator = _start_load(repo, out_dir)
        code = child.wait(timeout=CHILD_TIMEOUT_S)
        wall = time.perf_counter() - start
    if generator is not None:
        generator.wait(timeout=CHILD_TIMEOUT_S)
    facts = capture_facts(capture_of("E04-load", attempt))
    return {
        "arm": stem,
        "attempt": attempt,
        "argv": argv,
        "wall_s": round(wall, 3),
        "exit_code": code,
        "stdout_bytes": stream.stat().st_size,
        "stderr_bytes": stderr.stat().st_size,
        "stderr_head": stderr.read_text(encoding="utf-8", errors="replace")[:2000],
        "endpoint": (repo / "endpoint.txt").read_text(encoding="utf-8").strip(),
        **facts,
        **result_facts(stream),
    }


def _start_load(repo: Path, out_dir: Path) -> subprocess.Popen[bytes]:
    """The generator, pointed at the port the child wrote into endpoint.txt."""
    endpoint = _endpoint(repo / "endpoint.txt")
    port = int(endpoint.rsplit(":", 1)[1])
    return subprocess.Popen(
        [
            sys.executable,
            str(LOAD),
            "--port",
            str(port),
            "--seconds",
            str(LOAD_SECONDS),
            "--threads",
            str(LOAD_THREADS),
            "--out",
            str(out_dir / "load.json"),
        ],
        stdout=(out_dir / "load.stdout.txt").open("wb"),
        stderr=subprocess.STDOUT,
    )


def _endpoint(path: Path) -> str:
    """Wait for the child to name its receiver. Refuses rather than guesses a port."""
    deadline = time.monotonic() + ENDPOINT_WAIT_S
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text.startswith("http://"):
                return text
        time.sleep(0.05)
    raise RuntimeError(f"{path} never named a receiver endpoint")


def argv_probe(out_dir: Path, repo: Path) -> dict[str, Any]:
    """The brief's `bash -c` child, run once to record what surfaces it configures.

    Kept because the answer is the reason child.py exists, and a claim about a launcher
    behaviour that nobody ran is a claim about a reading of the source.
    """
    script = (
        "echo $OTEL_EXPORTER_OTLP_ENDPOINT > endpoint.txt; "
        f"exec {sys.executable} {REPO_ROOT / 'tests' / 'integration' / 'fake_agent.py'}"
        ' -p "make the answer 42" --output-format stream-json --seed 7'
    )
    argv = launcher_argv("E04-argv", 1, ["bash", "-c", script])
    run = session(argv, repo, dict(os.environ), out_dir, "argv_probe")
    return {**run, **capture_facts(capture_of("E04-argv", 1))}


def load_arm() -> dict[str, Any]:
    """The whole fail-open arm: a probe, a quiet capture, then a flooded one."""
    out_dir = OUT / "load"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    probe = argv_probe(out_dir, load_repo(out_dir / "argv"))
    print(f"argv probe: surfaces {probe.get('capture_ended', {})}")
    # `quiet` and `flood`, not `unloaded` and `loaded`: the scripted agent prints its
    # working directory in the system init line, so two directory names of unequal
    # length make the two stdouts differ in byte count for a reason that has nothing to
    # do with the load. Measured first with the longer names: 5639 against 5637 bytes,
    # exactly the two characters. Rule (2) compares byte counts, so the harness must
    # not be what makes them differ.
    unloaded = capture_under_load(out_dir, load_repo(out_dir / "quiet"), flood=False)
    print(_load_line(unloaded))
    loaded = capture_under_load(out_dir, load_repo(out_dir / "flood"), flood=True)
    print(_load_line(loaded))
    generator = out_dir / "load.json"
    found = {
        "argv_probe": probe,
        "unloaded": unloaded,
        "loaded": loaded,
        "generator": (
            json.loads(generator.read_text(encoding="utf-8"))
            if generator.exists()
            else None
        ),
        "shape": {
            "load_delay_s": LOAD_DELAY_S,
            "load_seconds": LOAD_SECONDS,
            "load_threads": LOAD_THREADS,
            "hold_before_s": HOLD_BEFORE_S,
            "hold_after_s": HOLD_AFTER_S,
        },
    }
    (out_dir / "results.json").write_text(json.dumps(found, indent=2) + "\n", "utf-8")
    return found


def _load_line(meta: dict[str, Any]) -> str:
    return (
        f"{meta['arm']}: exit {meta['exit_code']}, stdout {meta['stdout_bytes']} B, "
        f"surfaces {meta['observations_by_surface']}, "
        f"diagnostics {meta['diagnostics_by_kind']}"
    )


# -- statistics and the results file -------------------------------------------------


def stats(values: list[float]) -> dict[str, Any]:
    """Median, scaled MAD, min, max and the full sorted list. Never a mean alone.

    The scaled MAD of one value is None rather than 0: W1-T4 measured that a 0 there
    makes design 6.12's MDD 0, which claims every difference is resolvable from one
    observation. One value has a median and no dispersion.
    """
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "median": None, "mad_scaled": None, "min": None, "max": None,
                "sorted": []}  # fmt: skip
    median = statistics.median(ordered)
    mad = (
        statistics.median([abs(one - median) for one in ordered])
        if len(ordered) > 1
        else None
    )
    return {
        "n": len(ordered),
        "median": round(median, 4),
        "mad_scaled": None if mad is None else round(1.4826 * mad, 4),
        "min": ordered[0],
        "max": ordered[-1],
        "sorted": ordered,
    }


def mdd(spread: float | None, n: int) -> float | None:
    """Design 6.12: MDD = 2.8 s sqrt(2 / n). The smallest resolvable difference."""
    if spread is None or n < 2:
        return None
    return round(2.8 * spread * (2 / n) ** 0.5, 4)


def needed(spread: float | None, target: float | None) -> int | None:
    """The n at which MDD would equal `target`. Design 6.12's N_needed, rearranged."""
    if spread is None or not target:
        return None
    return int(-(-2 * (2.8 * spread / abs(target)) ** 2 // 1))


def results() -> dict[str, Any]:
    """Every number the write-up quotes, with the file each came from."""
    pairs = []
    for pair in range(1, PAIRS + 1):
        on = OUT / f"pair{pair}" / "on.meta.json"
        off = OUT / f"pair{pair}" / "off.meta.json"
        if not (on.exists() and off.exists()):
            continue
        first = json.loads(on.read_text(encoding="utf-8"))
        second = json.loads(off.read_text(encoding="utf-8"))
        pairs.append(_pair_row(pair, first, second))
    found: dict[str, Any] = {
        "pairs": pairs,
        "sources": {
            "pairs": "experiments/E04/out/pair<k>/<arm>.meta.json",
            "floor": "experiments/E04/out/floor.json",
            "load": "experiments/E04/out/load/results.json",
            "generator": "experiments/E04/out/load/load.json",
        },
    }
    found["overhead"] = _overhead(pairs)
    for name, path in (
        ("floor", OUT / "floor.json"),
        ("load", OUT / "load" / "results.json"),
        # The load arm as it ran BEFORE the two repository directories were given
        # names of equal length. Kept because the write-up quotes its two stdout byte
        # counts as the evidence that the 2-byte difference was the harness.
        ("load_unequal_names", OUT / "load-unequal-names.json"),
    ):
        found[name] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        )
    found["store"] = store_size()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(found, indent=2) + "\n", "utf-8")
    return found


def store_size() -> dict[str, Any]:
    """What this experiment left on the owner's disk.

    The load arm writes its synthetic records into the real capture database, which is
    what makes it a test of the real store, and 100 records per POST for 20 s is a lot
    of rows. The size is here so the write-up can say the cost rather than imply it.
    """
    wal = DB.with_name(DB.name + "-wal")
    return {
        "path": str(DB),
        "db_bytes": DB.stat().st_size if DB.exists() else None,
        "wal_bytes": wal.stat().st_size if wal.exists() else None,
        "observations_total": _query("SELECT count(*) FROM observations")[0][0],
        "diagnostics_dropped_total": _query(
            "SELECT count(*) FROM diagnostics WHERE kind = 'dropped'"
        )[0][0],
    }


def _pair_row(pair: int, on: dict[str, Any], off: dict[str, Any]) -> dict[str, Any]:
    """One pair. A delta whose either side is missing is None, never 0."""
    api_on, api_off = on["duration_ms"], off["duration_ms"]
    return {
        "pair": pair,
        "wall_on_s": on["wall_s"],
        "wall_off_s": off["wall_s"],
        "delta_s": round(on["wall_s"] - off["wall_s"], 3),
        "duration_ms_on": api_on,
        "duration_ms_off": api_off,
        "delta_api_ms": (
            None if api_on is None or api_off is None else api_on - api_off
        ),
        "tokens_on": on["tokens_total"],
        "tokens_off": off["tokens_total"],
        "turns_on": on["num_turns"],
        "turns_off": off["num_turns"],
        "exit_on": on["exit_code"],
        "exit_off": off["exit_code"],
        "is_error_on": on["result_is_error"],
        "is_error_off": off["result_is_error"],
        "capture_id": on.get("capture_id"),
        "surfaces_received": (on.get("capture_ended") or {}).get("surfaces_received"),
    }


def _overhead(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """The decision rule's numbers, and the branch they take. No threshold invented."""
    deltas = [row["delta_s"] for row in pairs]
    api = [row["delta_api_ms"] for row in pairs if row["delta_api_ms"] is not None]
    wall_off = stats([row["wall_off_s"] for row in pairs])
    delta = stats(deltas)
    noise = wall_off["mad_scaled"]
    resolved = (
        None
        if delta["median"] is None or noise is None
        else abs(delta["median"]) > noise
    )
    return {
        "n": len(pairs),
        "wall_on_s": stats([row["wall_on_s"] for row in pairs]),
        "wall_off_s": wall_off,
        "delta_s": delta,
        "delta_api_ms": stats([float(one) for one in api]),
        "off_arm_mad_scaled_s": noise,
        "exceeds_noise": resolved,
        "verdict": (
            "not measured"
            if resolved is None
            else (
                "the overhead exceeds run-to-run noise at n = 5"
                if resolved
                else "not resolved at n = 5"
            )
        ),
        "mdd_s": mdd(noise, len(pairs)),
        "n_needed_for_measured_delta": needed(noise, delta["median"]),
        # A sensitivity check, NOT a replacement for the rule above. The two arms are
        # the same prompt and not the same session, so the model may take a different
        # number of turns in each; a pair whose turn counts differ is comparing two
        # amounts of work as well as two recorders. This is the same statistic over the
        # pairs where it does not. Nothing here re-decides the rule.
        "delta_s_matched_turns": stats(
            [row["delta_s"] for row in pairs if row["turns_on"] == row["turns_off"]]
        ),
    }


# -- the command ---------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="E04 perturbation and fail-open")
    parser.add_argument("--floor", action="store_true", help="the wrapper's own cost")
    parser.add_argument("--pilot", action="store_true", help="pair 1 and the gate")
    parser.add_argument("--all", action="store_true", help="pairs 2 to 5")
    parser.add_argument("--load", action="store_true", help="the fail-open arm")
    args = parser.parse_args()
    if not (args.floor or args.pilot or args.all or args.load):
        parser.error("name at least one of --floor, --pilot, --all, --load")

    if args.floor:
        floor()
    if args.pilot:
        on, off = one_pair(1)
        for meta in (on, off):
            ok, why = within_budget(meta)
            print(f"PILOT {meta['arm']}: {why}")
            if not ok:
                results()
                print("STOP: the pilot left the budget; no further pair was run")
                return 2
    if args.all:
        for pair in range(2, PAIRS + 1):
            one_pair(pair)
    if args.load:
        load_arm()
    found = results()
    print(json.dumps(found.get("overhead"), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
