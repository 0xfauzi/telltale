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
`--deny` adds one more Bash call and has it REFUSED rather than run, which is what
`claude -p` did to all three of W2-E05's Bash calls with nobody at the keyboard to
approve them. See `DENIED_CALL` and `_denied`. `--effort` and `--model` change both
deterministically, which is what an experiment
varying one launch flag between arms needs. `--seed-max N` bounds the drawn seed to
0..N-1, which is what a between-arm experiment needs: every number here is linear in
the seed, so at the default bound of 100 the draw moves the token counts by more than
`--effort` does, and the sign of a between-arm shift would be a property of the draw
rather than of the flag. `--fail` writes the wrong answer, so the acceptance command
the harness runs afterwards fails while the agent still exits 0.

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

# What Claude Code 2.1.258 sends when a tool call is refused, recovered two ways
# because no single record holds all of it. The stream line's KEYS come from the
# redaction of the stored observation on W2-E05's captures, which names every payload
# field the allowlist dropped as unknown (tool_name, tool_use_id,
# decision_reason_type) beside the four the parser consumes (type, subtype, uuid,
# session_id) and no timestamp, which is why provider_ts is null on those rows. The
# tool_result TEXT comes from the transcript of session
# 2761a993-8865-47f5-9f4e-080fa00be634, read-only: two of the three refusals there say
# exactly this. The value of decision_reason_type is not recoverable from either, and
# nothing stores it, so the word below is fabricated like every other value here.
DENIED_TEXT = "This command requires approval"
DENIED_REASON = "permission_prompt_denied"

# The call `--deny` adds and is refused. A verification command on purpose: all three
# of W2-E05's refusals were `uv run pytest ...`, and a refused TEST command is the case
# that separates "0 test runs and 1 refused call" from "1 failed test run". It is added
# rather than substituted so that the run is otherwise the same run, and it never
# executes, so this process never starts a test runner.
DENIED_CALL: tuple[str, dict[str, Any]] = ("Bash", {"command": "uv run pytest"})

EFFORTS = ("low", "medium", "high")
MODELS = ("haiku", "sonnet", "opus")

# What a run does, in order: `_reads(...)` Read calls, one Edit, one Bash.
_MIN_READS = 1
_MAX_EXTRA_READS = 3
# The largest seed drawn when --seed was not given. Bounded because every fabricated
# number below is linear in the seed, and an unbounded one prints as a token count no
# agent could produce.
_SEED_MAX = 100


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


def _drawn(bound: int = _SEED_MAX) -> int:
    """A seed for a run that did not name one, bounded so the numbers stay readable."""
    return int.from_bytes(os.urandom(2), "big") % bound


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
    deny: bool = False,
) -> str | None:
    """One assistant tool_use message, the real work, and its tool_result message.

    Returns the refused tool_use id when the call was denied, so `run` can list it
    under `permission_denials` on the result message the way a real session does.
    """
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
    if deny:
        _denied(stream, session, tool_use_id, name)
        text, failed = DENIED_TEXT, True
    else:
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
    return tool_use_id if deny else None


def _denied(stream: bool, session: str, tool_use_id: str, name: str) -> None:
    """The system message a refusal puts on the stream, before the tool_result.

    No timestamp: the stored observations of W2-E05's refusals carry provider_ts null,
    and this line is the reason. The tool_result that follows carries `is_error` true,
    which is the whole of finding 1: the reducer read that field and counted a test run
    that never happened.
    """
    _emit(
        stream,
        {
            "type": "system",
            "subtype": "permission_denied",
            "session_id": session,
            "uuid": str(uuid.uuid4()),
            "tool_use_id": tool_use_id,
            "tool_name": name,
            "decision_reason_type": DENIED_REASON,
        },
    )


def _totals(usages: list[dict[str, int]]) -> dict[str, int]:
    return {key: sum(one[key] for one in usages) for key in usages[0]}


def run(args: argparse.Namespace) -> int:
    stream = args.output_format == "stream-json"
    session = args.session_id or str(uuid.uuid4())
    seed = args.seed if args.seed is not None else _drawn(args.seed_max)
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
    denials: list[dict[str, str]] = []
    calls = [*_calls(seed, rank, args.fail), *([DENIED_CALL] if args.deny else [])]
    for turn, call in enumerate(calls):
        usage = _usage(seed, rank, args.model, turn)
        usages.append(usage)
        # The added call and no other: the Read and the Edit are what
        # `--permission-mode acceptEdits` auto-accepts, and the first Bash call is work
        # this process really does.
        deny = args.deny and turn == len(calls) - 1
        refused = _tool_turn(stream, session, args.model, usage, call, deny)
        if refused is not None:
            denials.append({"tool_name": call[0], "tool_use_id": refused})
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
            # Present and empty on a session with nothing refused, which is what a
            # real result message does: measured over the owner's store, the key is
            # present and empty on 25 results, non-empty on 4 and absent on 3. The key
            # is not a marker, the list is the fact.
            "permission_denials": denials,
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
    parser.add_argument("--seed-max", type=int, default=_SEED_MAX)
    parser.add_argument("--fail", action="store_true", help="write the wrong answer")
    parser.add_argument(
        "--deny", action="store_true", help="add one Bash call and have it refused"
    )
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
