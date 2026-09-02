"""A scripted agent: a real child process the launcher wraps, for no tokens.

It is an agent in the two ways that matter to a recorder. It prints the stream-json
lines Claude Code prints (a system init, assistant messages carrying tool_use blocks,
user messages carrying tool_result blocks, a result message with usage and num_turns),
and it does the work those lines describe: it reads files, rewrites one, and runs a
command, in the working directory it was started in. Nothing is simulated behind the
lines, so a capture of this process is a capture of real file mutations and a real
subprocess.

Every number it reports is FABRICATED. The token counts and the durations are a
deterministic function of the flags, and they exist so that a runner can be verified
against numbers whose right answer is known in advance. They are not measurements of
anything and no report may present them as such. What is real here is the shape of the
records, the tool calls and the exit code.

Flags. `--seed N` fixes the number of tool calls and the token numbers, so two runs of
one seed are identical; WITHOUT it a seed is drawn per run, so repetitions of one
condition vary the way real ones do and a median has something to be a median of.
`--effort` and `--model` change both deterministically, which is what an experiment
varying one launch flag between arms needs. `--fail` writes the wrong answer, so the
acceptance command the harness runs afterwards fails while the agent still exits 0.

stdout is written with sys.stdout.write rather than print: ruff T20 keeps print in
cli.py and report.py alone, and this file is neither.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The file this agent rewrites and the answer the acceptance command checks for. The
# two spellings are the whole of the --fail switch: the agent always exits 0, and the
# acceptance command is what decides pass or fail (design 6.12: the acceptance command
# is deterministic and run by the harness, never by the agent).
TARGET = "answer.txt"
PASS_TEXT = "42"
FAIL_TEXT = "41"

EFFORTS = ("low", "medium", "high")
MODELS = ("haiku", "sonnet", "opus")

# What a run does, in order: `_reads(...)` Read calls, one Edit, one Bash.
_MIN_READS = 1
_MAX_EXTRA_READS = 3


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _emit(stream: bool, message: dict[str, Any]) -> None:
    """One line of stream-json, flushed. The launcher tees stdout line by line."""
    if not stream:
        return
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _usage(seed: int, rank: int, model: str, turn: int) -> dict[str, int]:
    """Fabricated per-request token counts. See the module docstring."""
    return {
        "input_tokens": 100 + 13 * seed + 50 * rank + len(model) + turn,
        "output_tokens": 20 + 7 * turn + 30 * rank,
        "cache_read_input_tokens": 1000 * turn,
        "cache_creation_input_tokens": 300 if turn == 0 else 0,
    }


def _reads(seed: int, rank: int) -> int:
    return _MIN_READS + seed % _MAX_EXTRA_READS + rank


def _calls(seed: int, rank: int, failing: bool) -> list[tuple[str, dict[str, Any]]]:
    """The tool calls this run makes, as (name, tool_use input)."""
    readable = sorted(path.name for path in Path().iterdir() if path.is_file())
    calls: list[tuple[str, dict[str, Any]]] = [
        # Cycling rather than sampling: which file is read must depend on the seed and
        # on nothing else, or two runs of one seed would differ.
        ("Read", {"file_path": str(Path(readable[(seed + index) % len(readable)]))})
        for index in range(_reads(seed, rank))
        if readable
    ]
    calls.append(
        (
            "Edit",
            {
                "file_path": TARGET,
                "old_string": "",
                "new_string": FAIL_TEXT if failing else PASS_TEXT,
            },
        )
    )
    script = f"import pathlib;print(pathlib.Path({TARGET!r}).read_text())"
    calls.append(("Bash", {"command": shlex.join([sys.executable, "-c", script])}))
    return calls


def _act(name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    """Do the tool call for real. Returns (result text, is_error)."""
    try:
        if name == "Read":
            return Path(str(arguments["file_path"])).read_text(encoding="utf-8"), False
        if name == "Edit":
            text = str(arguments["new_string"]) + "\n"
            Path(TARGET).write_text(text, encoding="utf-8")
            return f"updated {TARGET}", False
        # A fixed argv, split from a string this file built: never a shell string.
        done = subprocess.run(
            shlex.split(str(arguments["command"])),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        # The shape a failed Claude Code Bash tool_result has: E01 measured that the
        # exit code of a shell command is in this first line and nowhere else.
        return f"Exit code 1\n{error}", True
    if done.returncode != 0:
        return f"Exit code {done.returncode}\n{done.stderr}", True
    return done.stdout, False


def _tool_turn(
    stream: bool,
    session: str,
    model: str,
    usage: dict[str, int],
    call: tuple[str, dict[str, Any]],
) -> None:
    """One assistant tool_use message, the real work, and its tool_result message."""
    name, arguments = call
    tool_use_id = f"toolu_{uuid.uuid4().hex[:24]}"
    _emit(
        stream,
        {
            "type": "assistant",
            "message": {
                "model": model,
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": name,
                        "input": arguments,
                    }
                ],
                "stop_reason": "tool_use",
                "usage": usage,
            },
            "parent_tool_use_id": None,
            "session_id": session,
            "uuid": str(uuid.uuid4()),
            "timestamp": _now(),
            "request_id": f"req_{uuid.uuid4().hex[:24]}",
        },
    )
    text, failed = _act(name, arguments)
    _emit(
        stream,
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "tool_use_id": tool_use_id,
                        "type": "tool_result",
                        "content": text,
                        "is_error": failed,
                    }
                ],
            },
            "parent_tool_use_id": None,
            "session_id": session,
            "uuid": str(uuid.uuid4()),
            "timestamp": _now(),
        },
    )


def _totals(usages: list[dict[str, int]]) -> dict[str, int]:
    return {key: sum(one[key] for one in usages) for key in usages[0]}


def run(args: argparse.Namespace) -> int:
    stream = args.output_format == "stream-json"
    session = args.session_id or str(uuid.uuid4())
    seed = args.seed if args.seed is not None else int.from_bytes(os.urandom(2), "big")
    rank = EFFORTS.index(args.effort)
    _emit(
        stream,
        {
            "type": "system",
            "subtype": "init",
            "session_id": session,
            "uuid": str(uuid.uuid4()),
            "model": args.model,
            "cwd": str(Path.cwd()),
            "permissionMode": "bypassPermissions",
            "tools": ["Bash", "Edit", "Read"],
            "claude_code_version": "fake-agent",
        },
    )
    usages: list[dict[str, int]] = []
    for turn, call in enumerate(_calls(seed, rank, args.fail)):
        usage = _usage(seed, rank, args.model, turn)
        usages.append(usage)
        _tool_turn(stream, session, args.model, usage, call)
    total = _totals(usages)
    _emit(
        stream,
        {
            "type": "result",
            "subtype": "success",
            "session_id": session,
            "uuid": str(uuid.uuid4()),
            "is_error": False,
            "num_turns": len(usages),
            # Fabricated, from the same inputs as the token counts, so a duration and
            # a token total never disagree about which run was the bigger one.
            "duration_ms": 100 * len(usages) + seed,
            "stop_reason": "end_turn",
            "terminal_reason": "completed",
            "total_cost_usd": round(total["output_tokens"] / 1000.0, 6),
            "usage": total,
            "modelUsage": {
                args.model: {
                    "inputTokens": total["input_tokens"],
                    "outputTokens": total["output_tokens"],
                    "contextWindow": 200000,
                }
            },
        },
    )
    if not stream:
        sys.stdout.write(f"{TARGET}: {FAIL_TEXT if args.fail else PASS_TEXT}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fake-agent", description=__doc__)
    parser.add_argument("-p", "--print", action="store_true", help="one-shot mode")
    parser.add_argument("prompt", nargs="?", default=None)
    parser.add_argument("--model", default="sonnet", choices=MODELS)
    parser.add_argument("--effort", default="medium", choices=EFFORTS)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--fail", action="store_true", help="write the wrong answer")
    parser.add_argument("--output-format", default="text")
    # The launcher's Claude plan appends --session-id and --settings to the child's
    # argv (W1-T1). Both are accepted here so that this process is wrapped by the same
    # plan a real `claude -p` is, rather than by a special case.
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--settings", default=None)
    parser.add_argument("--verbose", action="store_true")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
