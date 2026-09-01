"""E01: one `claude -p` session per scenario, every capture surface pointed at a sink.

What a scenario is: a fresh copy of experiments/E01/fixture_repo with its own git
history, a throwaway HTTP sink (experiments/common/capture_receiver.py) bound to a free
port, and one headless Claude Code session whose OTel exporter and whose http hooks both
point at that port while its stream-json output is teed to a file. Four surfaces, one
session, so a fact seen on one and missing on another is a property of the surfaces
rather than of two different runs.

Everything this script measures is written to out/<scenario>/meta.json beside the raw
capture, because docs/experiments/E01.md cites numbers by file and a number that only
ever existed on a terminal cannot be checked.

The late-export window is why the sink outlives the child by SINK_GRACE_S seconds: OTel
batches, so the last records of a session can arrive after the process that made
them is gone. That window is what a launcher must wait out before it may call a
capture complete, and it is measured here rather than assumed.

Usage:
    uv run python experiments/E01/run.py --scenario S2      # the pilot, on its own
    uv run python experiments/E01/run.py --scenario all     # pilot first, then the rest
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

E01_DIR = Path(__file__).resolve().parent
REPO_ROOT = E01_DIR.parents[1]
FIXTURE_REPO = E01_DIR / "fixture_repo"
OUT_ROOT = E01_DIR / "out"
RECEIVER = REPO_ROOT / "experiments" / "common" / "capture_receiver.py"

# The binary under test. Overridable ONLY so this harness can be exercised end to end
# against a stand-in that spends no tokens: every scenario reported in
# docs/experiments/E01.md ran with the default, and each meta.json records the argv
# it used, so a run made with a stand-in cannot be mistaken for a real one.
CLAUDE_BIN = os.environ.get("E01_CLAUDE_BIN", "claude")

# The files capture_receiver.py appends to, one per request path family. stream.jsonl is
# not among them: that one is written here, from the child's stdout.
SINK_SURFACES = ("otel_logs", "otel_metrics", "otel_traces", "hooks", "other")

# Matcher-less http hook entries, one per event the digest lists. An event this version
# does not fire is silently inert, so what arrives at the sink is itself the measurement
# of which events exist.
HOOK_EVENTS = (
    "SessionStart",
    "SessionEnd",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PreCompact",
    "PostCompact",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "PostModelSwitch",
)

# Telltale never turns these on and never captures what they would carry. Popped from
# the inherited environment rather than merely left unset, so a value already exported
# in the parent shell cannot leak prompt or tool content into a fixture.
FORBIDDEN_ENV = (
    "OTEL_LOG_USER_PROMPTS",
    "OTEL_LOG_ASSISTANT_RESPONSES",
    "OTEL_LOG_TOOL_CONTENT",
)

# The four counters a result message reports. Summed for the pilot gate; a counter the
# payload does not carry is NAMED in meta.json rather than counted as zero.
TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

SINK_GRACE_S = 15.0
CHILD_TIMEOUT_S = 420.0
PILOT_MAX_WALL_S = 300.0
PILOT_MAX_TOKENS = 200_000
# A child that fails this fast has not talked to the API. Used only to decide whether an
# exit is worth retrying with CLAUDECODE unset.
STARTUP_REFUSAL_S = 20.0

GIT_IDENTITY = (("user.email", "e01@telltale.invalid"), ("user.name", "Telltale E01"))

FIX_PROMPT = (
    "Run the tests with `uv run pytest`, fix the bug in pkg/calc.py so the failing "
    "test passes, run the tests again, and stop."
)
EXPLORE_PROMPT = "Explain how divide in pkg/calc.py handles zero. Do not edit any file."


@dataclass(frozen=True)
class Scenario:
    """One session: what to ask, where to ask it, and whether the sink is up."""

    name: str
    question: str
    prompt: str
    receiver_down: bool = False
    # The scenario whose session id this one resumes, and whose repository copy it runs
    # in: `claude --resume` looks a session up under the directory it was started in.
    resume_of: str | None = None
    settings_env: Mapping[str, str] = field(default_factory=dict)


SCENARIOS: dict[str, Scenario] = {
    "S1": Scenario("S1", "fix-the-test", FIX_PROMPT),
    "S2": Scenario("S2", "explore-only (PILOT)", EXPLORE_PROMPT),
    "S3": Scenario(
        "S3",
        "secret-touch",
        "Print the contents of secrets_note.txt with cat and then summarize what "
        "kinds of values it contains.",
    ),
    "S4": Scenario(
        "S4",
        "subagent",
        "Use a subagent to list the files in this project, then report the list.",
    ),
    "S5": Scenario(
        "S5",
        "resume",
        "Now say which file you would edit to guard against zero, without editing.",
        resume_of="S2",
    ),
    "S6": Scenario("S6", "receiver-down", EXPLORE_PROMPT, receiver_down=True),
    "S7": Scenario(
        "S7",
        "compaction attempt",
        "First read every file in the project twice, one file per Read call. "
        + FIX_PROMPT,
        settings_env={"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "5"},
    ),
    "S8": Scenario(
        "S8",
        "commit",
        "Fix the bug in pkg/calc.py and commit it with the message E01.",
    ),
}

# The pilot runs first by owner instruction. S5 resumes S2, so it cannot precede it.
RUN_ORDER = ("S2", "S1", "S3", "S4", "S5", "S6", "S7", "S8")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def prepare_repo(scenario_out: Path) -> Path:
    """A fresh copy of the fixture with one commit of its own, under out/<scenario>/."""
    repo = scenario_out / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    git(repo, "init", "-q", "-b", "main")
    for key, value in GIT_IDENTITY:
        git(repo, "config", key, value)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "fixture")
    return repo


def free_port() -> int:
    """A port nothing listens on: bound to learn the number, then released."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_sink(out_dir: Path) -> tuple[subprocess.Popen[str], int]:
    proc = subprocess.Popen(
        [sys.executable, str(RECEIVER), "--out", str(out_dir), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    line = "" if proc.stdout is None else proc.stdout.readline().strip()
    if not line.startswith("PORT="):
        proc.kill()
        raise RuntimeError(f"receiver printed {line!r} instead of a port")
    return proc, int(line.removeprefix("PORT="))


def stop_sink(sink: subprocess.Popen[str] | None) -> None:
    if sink is None:
        return
    sink.terminate()
    try:
        sink.wait(timeout=10)
    except subprocess.TimeoutExpired:
        sink.kill()


def down_port(out_dir: Path) -> int:
    """The closed port S6 aims at.

    `capture_receiver.py --down` prints PORT=0 and exits, which is the protocol's way of
    saying there is no listener. 0 is not a port a client can be pointed at, so the port
    used is an ephemeral one bound and released here: nothing is listening on it.
    """
    proc = subprocess.run(
        [sys.executable, str(RECEIVER), "--out", str(out_dir), "--down"],
        check=True,
        capture_output=True,
        text=True,
    )
    if proc.stdout.strip() != "PORT=0":
        raise RuntimeError(f"--down printed {proc.stdout.strip()!r}, expected PORT=0")
    return free_port()


def build_settings(port: int, scenario: Scenario) -> dict[str, Any]:
    """The --settings payload: one matcher-less http hook per event, plus S7's env."""
    hook = {
        "type": "http",
        "url": f"http://127.0.0.1:{port}/hooks/claude",
        # Seconds. Not milliseconds: 5000 here would be a 5000-second hook timeout.
        "timeout": 5,
    }
    settings: dict[str, Any] = {
        "hooks": {event: [{"hooks": [hook]}] for event in HOOK_EVENTS}
    }
    if scenario.settings_env:
        settings["env"] = dict(scenario.settings_env)
    return settings


def otel_env(port: int, scenario: Scenario, capture_id: str) -> dict[str, str]:
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        # No default exists. Without this the exporter has no protocol to speak.
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{port}",
        "OTEL_LOG_TOOL_DETAILS": "1",
        "OTEL_LOGS_EXPORT_INTERVAL": "1000",
        # 5 s only because this says so: the documented default is 60000 ms, which is
        # longer than most of these sessions.
        "OTEL_METRIC_EXPORT_INTERVAL": "5000",
        # Comma separated, no spaces.
        "OTEL_RESOURCE_ATTRIBUTES": (
            f"telltale.capture_id={capture_id},telltale.scenario={scenario.name}"
        ),
    }


def build_env(
    port: int, scenario: Scenario, capture_id: str, *, drop_claudecode: bool
) -> dict[str, str]:
    env = dict(os.environ)
    for name in FORBIDDEN_ENV:
        env.pop(name, None)
    if drop_claudecode:
        env.pop("CLAUDECODE", None)
    env.update(otel_env(port, scenario, capture_id))
    return env


def build_argv(scenario: Scenario, settings: str, session_id: str) -> list[str]:
    argv = [
        CLAUDE_BIN,
        "-p",
        "--model",
        "sonnet",
        "--permission-mode",
        "bypassPermissions",
        # Runaway guard. Undocumented in `claude --help` for 2.1.257 and still accepted:
        # see docs/experiments/E01.md.
        "--max-turns",
        "40",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if scenario.resume_of is None:
        argv += ["--session-id", session_id]
    else:
        argv += ["--resume", session_id]
    argv += ["--settings", settings, scenario.prompt]
    return argv


def launch(
    argv: list[str], cwd: Path, env: dict[str, str], out_dir: Path
) -> dict[str, Any]:
    """Run the child to completion or to the timeout. Returns the timing facts."""
    stream = out_dir / "stream.jsonl"
    stderr = out_dir / "stderr.txt"
    with stream.open("wb") as out_fh, stderr.open("wb") as err_fh:
        start = time.time()
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=out_fh,
            stderr=err_fh,
            stdin=subprocess.DEVNULL,
        )
        try:
            code = proc.wait(timeout=CHILD_TIMEOUT_S)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()
            timed_out = True
        exit_ts = time.time()
    return {
        "started_at": start,
        "started_at_iso": datetime.fromtimestamp(start, tz=UTC).isoformat(),
        "exit_ts": exit_ts,
        "wall_s": round(exit_ts - start, 3),
        "exit_code": code,
        "timed_out": timed_out,
        "stderr_bytes": stderr.stat().st_size,
        "stderr_head": stderr.read_text(encoding="utf-8", errors="replace")[:2000],
    }


def result_message(stream: Path) -> dict[str, Any] | None:
    """The last `type=result` line, which is where usage and cost live."""
    if not stream.exists():
        return None
    found: dict[str, Any] | None = None
    for line in stream.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and message.get("type") == "result":
            found = message
    return found


def token_total(usage: Mapping[str, Any] | None) -> tuple[int | None, list[str]]:
    """Sum of the four counters, and the names of any the payload did not carry.

    A missing counter is not a zero. If every counter is missing the total is None,
    because "no tokens" and "the payload did not say" are different answers.
    """
    if usage is None:
        return None, list(TOKEN_KEYS)
    missing = [key for key in TOKEN_KEYS if key not in usage]
    present = [int(usage[key]) for key in TOKEN_KEYS if key in usage]
    return (sum(present) if present else None), missing


def sink_records(out_dir: Path) -> list[tuple[str, float]]:
    """(surface, ingest_ts) for every request the sink wrote for this scenario."""
    seen: list[tuple[str, float]] = []
    for surface in SINK_SURFACES:
        path = out_dir / f"{surface}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.append((surface, float(json.loads(line)["ingest_ts"])))
    return seen


def late_export(out_dir: Path, exit_ts: float) -> dict[str, Any]:
    """How long after the child exited the sink was still being written to.

    late_export_window_s is None when nothing arrived after the exit, which is a
    different statement from 0.0 seconds and is kept different on purpose.
    """
    records = sink_records(out_dir)
    per_surface: dict[str, dict[str, Any]] = {}
    for surface, ingest_ts in records:
        row = per_surface.setdefault(
            surface, {"requests": 0, "last_ingest_ts": ingest_ts, "after_exit": 0}
        )
        row["requests"] += 1
        row["last_ingest_ts"] = max(row["last_ingest_ts"], ingest_ts)
        row["after_exit"] += int(ingest_ts > exit_ts)
    for row in per_surface.values():
        row["last_ingest_minus_exit_s"] = round(row["last_ingest_ts"] - exit_ts, 3)
    after = [ts for _, ts in records if ts > exit_ts]
    return {
        "requests_total": len(records),
        "per_surface": per_surface,
        "requests_after_exit": len(after),
        "late_export_window_s": (round(max(after) - exit_ts, 3) if after else None),
        "last_ingest_minus_exit_s": (
            round(max(ts for _, ts in records) - exit_ts, 3) if records else None
        ),
    }


def looks_like_startup_refusal(run: dict[str, Any], stream: Path) -> bool:
    """A fast non-zero exit with no result line: the child never got going."""
    if run["exit_code"] == 0 or run["wall_s"] > STARTUP_REFUSAL_S:
        return False
    return result_message(stream) is None


def probe_path() -> Path:
    return OUT_ROOT / "claudecode-probe.json"


def known_drop_claudecode() -> bool | None:
    """What the first session of this experiment learned about nesting, if anything.

    Cached across scenarios so the retry costs at most one session for the whole
    experiment rather than one per scenario.
    """
    path = probe_path()
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))["drop_claudecode"]
    return bool(value)


def record_drop_claudecode(scenario: Scenario, *, drop: bool, refused: bool) -> None:
    probe_path().parent.mkdir(parents=True, exist_ok=True)
    probe_path().write_text(
        json.dumps(
            {
                "drop_claudecode": drop,
                "measured_by": scenario.name,
                "first_attempt_refused": refused,
                "claudecode_in_parent_env": "CLAUDECODE" in os.environ,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def run_child(
    argv: list[str], cwd: Path, out_dir: Path, port: int, scenario: Scenario, cid: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the session, retrying once without CLAUDECODE if the first try never starts.

    This script runs inside a Claude Code session, so CLAUDECODE=1 is inherited. Whether
    a nested `claude -p` tolerates that is one of the things E01 answers, so the first
    session keeps it and the answer is recorded instead of assumed.
    """
    cached = known_drop_claudecode()
    drops = (cached,) if cached is not None else (False, True)
    attempts: list[dict[str, Any]] = []
    run: dict[str, Any] = {}
    for index, drop in enumerate(drops):
        env = build_env(port, scenario, cid, drop_claudecode=bool(drop))
        run = launch(argv, cwd, env, out_dir)
        attempts.append({"drop_claudecode": bool(drop), **run})
        refused = looks_like_startup_refusal(run, out_dir / "stream.jsonl")
        if not refused or index == len(drops) - 1:
            if cached is None:
                record_drop_claudecode(scenario, drop=bool(drop), refused=refused)
            break
        # Only reached when another attempt follows: keep the refused attempt's bytes.
        for name in ("stream.jsonl", "stderr.txt"):
            (out_dir / name).replace(out_dir / f"attempt1-claudecode-set-{name}")
    return run, attempts


def resolve_target(scenario: Scenario, out_dir: Path) -> tuple[Path, str]:
    """The directory the session runs in, and the session id it uses.

    A resume scenario runs in the repository copy of the session it resumes, because
    `claude --resume <id>` looks the session up under the project directory it started
    in. It gets no copy of its own.
    """
    if scenario.resume_of is None:
        return prepare_repo(out_dir), str(uuid.uuid4())
    meta_path = OUT_ROOT / scenario.resume_of / "meta.json"
    if not meta_path.exists():
        raise RuntimeError(
            f"{scenario.name} resumes {scenario.resume_of}, which has not run: "
            f"{meta_path} is missing"
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    session_id = meta.get("result_session_id")
    if not session_id:
        raise RuntimeError(f"{meta_path} carries no result_session_id to resume")
    return Path(meta["cwd"]), str(session_id)


def claude_version() -> str:
    proc = subprocess.run(
        [CLAUDE_BIN, "--version"], check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def result_facts(result: dict[str, Any] | None) -> dict[str, Any]:
    """The result message fields the report quotes. Absent stays None."""
    if result is None:
        return {
            "result_subtype": None,
            "result_is_error": None,
            "result_session_id": None,
            "num_turns": None,
            "total_cost_usd": None,
            "duration_ms": None,
            "duration_api_ms": None,
            "usage": None,
            "model_usage": None,
            "permission_denials": None,
        }
    return {
        "result_subtype": result.get("subtype"),
        "result_is_error": result.get("is_error"),
        "result_session_id": result.get("session_id"),
        "num_turns": result.get("num_turns"),
        "total_cost_usd": result.get("total_cost_usd"),
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
        "usage": result.get("usage"),
        "model_usage": result.get("modelUsage"),
        "permission_denials": result.get("permission_denials"),
    }


def run_scenario(scenario: Scenario) -> dict[str, Any]:
    out_dir = OUT_ROOT / scenario.name
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    cwd, session_id = resolve_target(scenario, out_dir)
    capture_id = uuid.uuid4().hex
    sink: subprocess.Popen[str] | None = None
    port = down_port(out_dir) if scenario.receiver_down else 0
    try:
        if not scenario.receiver_down:
            sink, port = start_sink(out_dir)
        settings = json.dumps(build_settings(port, scenario), separators=(",", ":"))
        argv = build_argv(scenario, settings, session_id)
        run, attempts = run_child(argv, cwd, out_dir, port, scenario, capture_id)
        # The sink outlives the child on purpose: this wait is what makes a late export
        # visible instead of being cut off by the sink dying with the session.
        time.sleep(SINK_GRACE_S)
    finally:
        stop_sink(sink)
    meta = scenario_meta(scenario, cwd, port, argv, capture_id, run, attempts)
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", "utf-8")
    return meta


def scenario_meta(
    scenario: Scenario,
    cwd: Path,
    port: int,
    argv: list[str],
    capture_id: str,
    run: dict[str, Any],
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    out_dir = OUT_ROOT / scenario.name
    result = result_message(out_dir / "stream.jsonl")
    facts = result_facts(result)
    usage = facts["usage"]
    tokens, missing = token_total(usage if isinstance(usage, Mapping) else None)
    return {
        "scenario": scenario.name,
        "question": scenario.question,
        "prompt": scenario.prompt,
        "cwd": str(cwd),
        "capture_id": capture_id,
        "claude_version": claude_version(),
        "receiver_down": scenario.receiver_down,
        "port": port,
        "argv": argv,
        "otel_env": otel_env(port, scenario, capture_id),
        "settings_env": dict(scenario.settings_env),
        "hook_events_declared": list(HOOK_EVENTS),
        "attempts": attempts,
        "drop_claudecode_used": attempts[-1]["drop_claudecode"],
        "wall_s": run["wall_s"],
        "exit_code": run["exit_code"],
        "timed_out": run["timed_out"],
        "started_at_iso": run["started_at_iso"],
        "stderr_bytes": run["stderr_bytes"],
        "stderr_head": run["stderr_head"],
        "tokens_total": tokens,
        "tokens_missing_keys": missing,
        **facts,
        "late_export": late_export(out_dir, run["exit_ts"]),
    }


def summarise(meta: dict[str, Any]) -> str:
    return (
        f"{meta['scenario']} ({meta['question']}): wall {meta['wall_s']} s, "
        f"exit {meta['exit_code']}, tokens {meta['tokens_total']}, "
        f"cost_usd {meta['total_cost_usd']}, turns {meta['num_turns']}, "
        f"sink requests {meta['late_export']['requests_total']}, "
        f"late window {meta['late_export']['late_export_window_s']} s"
    )


def within_pilot_limits(meta: dict[str, Any]) -> tuple[bool, str]:
    tokens = meta["tokens_total"]
    if meta["wall_s"] > PILOT_MAX_WALL_S:
        return False, f"wall {meta['wall_s']} s over the {PILOT_MAX_WALL_S} s limit"
    if tokens is None:
        return False, "the result message carried no token counters"
    if tokens > PILOT_MAX_TOKENS:
        return False, f"{tokens} tokens over the {PILOT_MAX_TOKENS} limit"
    return True, f"wall {meta['wall_s']} s and {tokens} tokens, both inside the limits"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="E01 capture-feasibility scenarios")
    parser.add_argument(
        "--scenario",
        default="all",
        help="S1..S8, or all (pilot S2 first, then the rest if it stayed in budget)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    names = list(RUN_ORDER) if args.scenario == "all" else [args.scenario]
    unknown = [name for name in names if name not in SCENARIOS]
    if unknown:
        raise SystemExit(f"unknown scenario {unknown}; known: {sorted(SCENARIOS)}")
    for index, name in enumerate(names):
        meta = run_scenario(SCENARIOS[name])
        print(summarise(meta))
        if name != "S2":
            continue
        ok, why = within_pilot_limits(meta)
        print(f"PILOT: {why}")
        if not ok and index + 1 < len(names):
            print("STOP: the pilot exceeded its budget; the rest was not run")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
