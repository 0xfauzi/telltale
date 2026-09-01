"""Run one E02 capture scenario end to end and record every number the write-up needs.

Each scenario is a throwaway copy of experiments/E02/fixture_repo/ with its own history,
its own sink port and its own project .codex/hooks.json, so no two scenarios can be
confused for one another and none of them touches ~/.codex or ~/.claude.

The three surfaces are collected at once because a Codex session costs a session:
  - `codex exec --json` on stdout, teed to out/<S>/exec.jsonl
  - OTel over otlp-http/json to the sink, which files it by request path
  - hooks, as `command` hooks running experiments/E02/hook_post.py, POSTed to the sink

Two numbers need the sink to outlive the child. The LATE-EXPORT WINDOW is the seconds
between the child's exit and the last request the sink saw, which tells a launcher how
long it must wait before tearing a receiver down; the sink is therefore kept alive
`--linger` seconds past the exit. The FAIL-OPEN cost is the same scenario run against a
port nothing is listening on, and it is only meaningful next to the same scenario run
against a live one.

Usage:
  uv run python experiments/E02/run.py --scenario S2
  uv run python experiments/E02/run.py --scenario S2 --spelling c
  uv run python experiments/E02/run.py --list
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE_REPO = HERE / "fixture_repo"
OUT_ROOT = HERE / "out"
RECEIVER = HERE.parent / "common" / "capture_receiver.py"
HOOK_SCRIPT = HERE / "hook_post.py"

# Every hook event 00-digest.md 2.2 names for Codex, plus `Interrupt`, which the digest
# does not list and the 0.150.1 binary does. It is registered so the run can report
# whether it fires rather than leaving an event the CLI knows about untested.
HOOK_EVENTS = (
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SessionStart",
    "SessionEnd",
    "SubagentStart",
    "SubagentStop",
    "UserPromptSubmit",
    "Stop",
    "Interrupt",
)

SINK_SURFACES = ("otel_logs", "otel_metrics", "otel_traces", "hooks", "other")

EXPLORE_PROMPT = "Explain how divide in pkg/calc.py handles zero. Do not edit any file."


@dataclass(frozen=True)
class Scenario:
    name: str
    prompt: str
    what: str
    resume_of: str | None = None
    ephemeral: bool = False
    receiver_down: bool = False
    extra_argv: tuple[str, ...] = field(default_factory=tuple)


SCENARIOS: dict[str, Scenario] = {
    "S1": Scenario(
        "S1",
        "Run the tests with `uv run pytest`, fix the bug in pkg/calc.py so the "
        "failing test passes, run the tests again, and stop.",
        "commands, a file change and a second command: the busiest surface set",
    ),
    "S2": Scenario(
        "S2",
        EXPLORE_PROMPT,
        "read-only turn: the pilot, and the thread S4 resumes",
    ),
    "S3": Scenario(
        "S3",
        "Print the contents of secrets_note.txt with cat and then summarize what "
        "kinds of values it contains.",
        "the privacy probe: which surfaces carry the TELLTALEFAKE values",
    ),
    "S4": Scenario(
        "S4",
        "Now say which file you would edit to guard against zero, without editing.",
        "resume: does a second turn on one thread keep the -c and the hook config",
        resume_of="S2",
    ),
    "S5": Scenario(
        "S5",
        EXPLORE_PROMPT,
        "fail-open: the same turn as S2 with nothing listening on the port",
        receiver_down=True,
    ),
    "S6": Scenario(
        "S6",
        "Fix the bug in pkg/calc.py and commit it with the message E02.",
        "a commit: is the commit id observable on any surface",
    ),
    "S7": Scenario(
        "S7",
        EXPLORE_PROMPT,
        "--ephemeral: confirms no rollout file is written",
        ephemeral=True,
    ),
}


def codex_version() -> str:
    proc = subprocess.run(
        ["codex", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip().split()[-1]


def free_port() -> int:
    """A port that was free a moment ago and has nothing listening on it now.

    Used only by the receiver-down scenario. `capture_receiver.py --down` prints
    PORT=0 and exits, which says "no sink"; 0 is not a port a client can be pointed
    at, and connecting to it fails differently from connecting to a closed one. The
    measurement wanted is a refused connection, so the scenario reserves an ephemeral
    port and releases it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_sink(out_dir: Path) -> tuple[subprocess.Popen[str], int]:
    proc = subprocess.Popen(
        [sys.executable, str(RECEIVER), "--out", str(out_dir), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    line = proc.stdout.readline().strip()
    if not line.startswith("PORT="):
        proc.kill()
        raise RuntimeError(f"receiver did not announce a port, said: {line!r}")
    return proc, int(line.split("=", 1)[1])


def confirm_sink_down(out_dir: Path) -> str:
    """Run the receiver with --down and keep what it said: a record, not a claim."""
    proc = subprocess.run(
        [sys.executable, str(RECEIVER), "--out", str(out_dir), "--down"],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=E02 fixture",
            "-c",
            "user.email=e02@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def prepare_repo(out_dir: Path) -> Path:
    repo = out_dir / "repo"
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(FIXTURE_REPO, repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture")
    return repo


def write_hooks_json(repo: Path, port: int, local_log: Path) -> Path:
    command = f"{sys.executable} {HOOK_SCRIPT} {port} {local_log}"
    hooks = {
        "description": "E02 capture feasibility: forward every hook event to the sink",
        "hooks": {
            event: [{"hooks": [{"type": "command", "command": command, "timeout": 15}]}]
            for event in HOOK_EVENTS
        },
    }
    path = repo / ".codex" / "hooks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
    return path


def _exporter_table(endpoint: str) -> str:
    """Serde's externally tagged spelling of OtelExporterKind::OtlpHttp, as inline TOML.

    The variant is a STRUCT variant in the binary, which is why the tag on its own
    (`otel.exporter="otlp-http"`) is rejected: see experiments/E02/config_probe.py.
    """
    return f'{{ "otlp-http" = {{ endpoint = "{endpoint}", protocol = "json" }} }}'


def otel_args(port: int, spelling: str) -> list[str]:
    """The `-c` overrides under test. Recorded verbatim in each scenario meta.json."""
    base = f"http://127.0.0.1:{port}"
    environment = ["-c", 'otel.environment="telltale-e02"']
    signals = {
        "exporter": "logs",
        "trace_exporter": "traces",
        "metrics_exporter": "metrics",
    }
    if spelling == "a":
        table = _exporter_table(base)
        args = [arg for key in signals for arg in ("-c", f"otel.{key}={table}")]
        return [*args, *environment]
    if spelling == "a-signal-path":
        args = [
            arg
            for key, signal in signals.items()
            for arg in ("-c", f"otel.{key}={_exporter_table(f'{base}/v1/{signal}')}")
        ]
        return [*args, *environment]
    if spelling == "c":
        args = []
        for key in signals:
            args += ["-c", f'otel.{key}.otlp-http.endpoint="{base}"']
            args += ["-c", f'otel.{key}.otlp-http.protocol="json"']
        return [*args, *environment]
    raise ValueError(f"unknown spelling {spelling!r}")


def build_argv(
    scenario: Scenario, repo: Path, port: int, spelling: str, thread: str | None
) -> list[str]:
    common = [
        "--skip-git-repo-check",
        "--dangerously-bypass-hook-trust",
        # NOT --strict-config. It is the only flag that turns a misspelled -c key into a
        # failure instead of a silent no-op, and the first S2 attempt used it: exit 1 in
        # 0.036 s, zero tokens, `unknown configuration field features.multo_agent` at
        # ~/.codex/config.toml:72. The owner's config has a key this Codex build does
        # not know, so strict mode cannot be used for a capture that must run beside the
        # owner's own config. The otel keys are validated separately and for free by
        # config_probe.py, which runs strict mode from a neutral cwd.
        "-c",
        'model_reasoning_effort="low"',
        *otel_args(port, spelling),
    ]
    if scenario.ephemeral:
        common.append("--ephemeral")
    common.extend(scenario.extra_argv)
    if scenario.resume_of is not None:
        if thread is None:
            raise RuntimeError(
                f"{scenario.name} resumes {scenario.resume_of}: no thread id found"
            )
        # resume takes neither -C nor -s: the thread carries its own working root, and
        # the
        # subprocess cwd is what makes the project-level hooks.json discoverable.
        return ["codex", "exec", "resume", thread, "--json", *common, scenario.prompt]
    return [
        "codex",
        "exec",
        "--json",
        "-C",
        str(repo),
        "-s",
        "workspace-write",
        *common,
        scenario.prompt,
    ]


def run_child(
    argv: list[str], cwd: Path, out_dir: Path, timeout: float
) -> dict[str, object]:
    exec_path = out_dir / "exec.jsonl"
    stderr_path = out_dir / "stderr.txt"
    started_wall = time.time()
    started = time.monotonic()
    with (
        exec_path.open("w", encoding="utf-8") as out,
        stderr_path.open("w", encoding="utf-8") as err,
    ):
        proc = subprocess.Popen(argv, cwd=cwd, stdout=out, stderr=err, text=True)
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    exited_wall = time.time()
    return {
        "started_wall": started_wall,
        "exited_wall": exited_wall,
        "wall_s": round(time.monotonic() - started, 3),
        "exit_code": proc.returncode,
        "timed_out": timed_out,
    }


def read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"_unparsed": line})
    return rows


def exec_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    types: Counter[str] = Counter()
    item_types: Counter[str] = Counter()
    thread_id: str | None = None
    usage: list[dict[str, object]] = []
    for row in rows:
        kind = str(row.get("type", "_untyped"))
        types[kind] += 1
        item = row.get("item")
        if isinstance(item, dict):
            item_types[
                str(item.get("item_type") or item.get("type") or "_unknown")
            ] += 1
        if kind == "thread.started":
            thread_id = str(row.get("thread_id") or row.get("thread", {}))
        turn_usage = row.get("usage")
        if kind == "turn.completed" and isinstance(turn_usage, dict):
            usage.append(dict(turn_usage))
    return {
        "event_type_counts": dict(types),
        "item_type_counts": dict(item_types),
        "thread_id": thread_id,
        "turn_completed_usage": usage,
    }


def sink_summary(out_dir: Path, exited_wall: float) -> dict[str, object]:
    counts: dict[str, int] = {}
    last_ingest: dict[str, float | None] = {}
    late: dict[str, float | None] = {}
    for surface in SINK_SURFACES:
        rows = read_jsonl(out_dir / f"{surface}.jsonl")
        counts[surface] = len(rows)
        stamps = [
            float(stamp)
            for stamp in (r.get("ingest_ts") for r in rows)
            if isinstance(stamp, int | float)
        ]
        last_ingest[surface] = max(stamps) if stamps else None
        late[surface] = round(max(stamps) - exited_wall, 3) if stamps else None
    return {
        "request_counts": counts,
        "last_ingest_ts": last_ingest,
        "late_export_window_s": late,
    }


def _hook_event_name(row: dict[str, object]) -> str:
    payload = row.get("payload")
    if isinstance(payload, dict):
        return str(payload.get("hook_event_name", "_missing"))
    return "_unparsed"


def _hook_delivery(
    row: dict[str, object], failures: Counter[str], elapsed: list[float]
) -> int:
    delivery = row.get("delivery")
    if not isinstance(delivery, dict):
        return 0
    error = delivery.get("error")
    if isinstance(error, str):
        failures[error.split(":", 1)[0]] += 1
    seconds = delivery.get("elapsed_s")
    if isinstance(seconds, int | float):
        elapsed.append(float(seconds))
    return 1 if delivery.get("status") == 200 else 0


def hooks_local_summary(path: Path) -> dict[str, object]:
    rows = read_jsonl(path)
    events: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    elapsed: list[float] = []
    delivered = 0
    for row in rows:
        events[_hook_event_name(row)] += 1
        delivered += _hook_delivery(row, failures, elapsed)
    return {
        "hook_invocations": len(rows),
        "event_counts": dict(events),
        "delivered_200": delivered,
        "delivery_error_kinds": dict(failures),
        "delivery_elapsed_s_total": round(sum(elapsed), 6) if elapsed else None,
        "delivery_elapsed_s_max": round(max(elapsed), 6) if elapsed else None,
    }


def resolve_thread(name: str) -> str | None:
    meta = OUT_ROOT / name / "meta.json"
    if not meta.exists():
        return None
    value = (
        json.loads(meta.read_text(encoding="utf-8")).get("exec", {}).get("thread_id")
    )
    return str(value) if value else None


def run_scenario(
    scenario: Scenario, spelling: str, timeout: float, linger: float
) -> dict[str, object]:
    out_dir = OUT_ROOT / scenario.name
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    thread = resolve_thread(scenario.resume_of) if scenario.resume_of else None
    if scenario.resume_of is not None:
        repo = OUT_ROOT / scenario.resume_of / "repo"
        if not repo.exists():
            raise RuntimeError(f"{scenario.name} needs {repo}, which does not exist")
    else:
        repo = prepare_repo(out_dir)

    down_line = None
    sink: subprocess.Popen[str] | None = None
    if scenario.receiver_down:
        down_line = confirm_sink_down(out_dir)
        port = free_port()
    else:
        sink, port = start_sink(out_dir)

    local_log = out_dir / "hooks_local.jsonl"
    hooks_path = write_hooks_json(repo, port, local_log)
    argv = build_argv(scenario, repo, port, spelling, thread)

    print(f"[{scenario.name}] port={port} spelling={spelling}", flush=True)
    print(f"[{scenario.name}] argv={argv}", flush=True)
    timing = run_child(argv, repo, out_dir, timeout)
    print(
        f"[{scenario.name}] exit={timing['exit_code']} wall={timing['wall_s']}s"
        f" timed_out={timing['timed_out']}; lingering {linger}s",
        flush=True,
    )
    time.sleep(linger)
    if sink is not None:
        sink.terminate()
        sink.wait(timeout=15)

    rows = read_jsonl(out_dir / "exec.jsonl")
    meta: dict[str, object] = {
        "scenario": scenario.name,
        "what": scenario.what,
        "prompt": scenario.prompt,
        "codex_version": codex_version(),
        "spelling": spelling,
        "otel_args": otel_args(port, spelling),
        "argv": argv,
        "child_cwd": str(repo),
        "sink_port": port,
        "sink_mode": "down" if scenario.receiver_down else "live",
        "receiver_down_stdout": down_line,
        "linger_s": linger,
        "hooks_json": str(hooks_path),
        "hook_events_registered": list(HOOK_EVENTS),
        "timing": timing,
        "exec": exec_summary(rows),
        "sink": sink_summary(out_dir, float(timing["exited_wall"])),  # type: ignore[arg-type]
        "hooks_local": hooks_local_summary(local_log),
        "stderr_tail": (out_dir / "stderr.txt").read_text(
            encoding="utf-8", errors="replace"
        )[-4000:],
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--scenario", help="one of " + ", ".join(SCENARIOS))
    parser.add_argument("--spelling", default="a", choices=["a", "a-signal-path", "c"])
    parser.add_argument("--timeout", type=float, default=420.0)
    parser.add_argument("--linger", type=float, default=15.0)
    parser.add_argument(
        "--list", action="store_true", help="print the scenarios and exit"
    )
    parser.add_argument(
        "--print-otel-args",
        action="store_true",
        help="print the -c overrides for --spelling as JSON and exit; config_probe.py "
        "calls this instead of importing, so there is one spelling and not two",
    )
    parser.add_argument(
        "--port", type=int, default=9999, help="port for --print-otel-args"
    )
    args = parser.parse_args()

    if args.print_otel_args:
        print(json.dumps(otel_args(args.port, args.spelling)))
        return 0

    if args.list or not args.scenario:
        for name, scenario in SCENARIOS.items():
            print(f"{name}: {scenario.what}\n    prompt: {scenario.prompt}")
        return 0 if args.list else 2

    if args.scenario not in SCENARIOS:
        parser.error(f"unknown scenario {args.scenario!r}")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    meta = run_scenario(
        SCENARIOS[args.scenario], args.spelling, args.timeout, args.linger
    )
    print(
        json.dumps(
            {k: meta[k] for k in ("timing", "exec", "sink", "hooks_local")}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
