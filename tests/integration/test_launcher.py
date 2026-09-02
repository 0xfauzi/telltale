"""The launcher, run as the binary, wrapping real child processes.

Every test here runs the installed `telltale` console script in a subprocess and lets it
start a second process of its own, because everything the launcher promises is about
those two processes and not about a function: the exit code a shell sees, the bytes on
stdout, the signal that reaches the child, the environment the child inherits, and a
second `telltale run` writing into the same SQLite file at the same time.

The child is `bash` or `python` rather than an agent binary. That is the point: what is
under test is the launcher, and a test that needed Claude Code would spend tokens to
learn nothing about it. The one thing that needs a real agent is the shape of the
records, and those come from E01's captured fixtures, which this file replays through
the launcher's own tee.

Every test writes into a temporary $TELLTALE_HOME (the `telltale_home` fixture), which
also asserts that nothing was written under $HOME.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence

# Three recorded stream-json lines from E01's S2 capture of Claude Code 2.1.257. Real
# bytes, so the tee is tested against what the provider really writes, including the
# trailing newline of each line.
FIXTURE_STREAM = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "sources"
    / "claude"
    / "2.1.257"
    / "S2"
    / "stream.jsonl"
)
STREAM_LINES = 3

# 128 + SIGTERM. The number a shell reports for a child that died by a signal, and the
# whole contract of `telltale run` is that the caller sees what it would have seen.
SIGTERM_CODE = 128 + int(signal.SIGTERM)

# The two names E01 finding 6 says must not reach the child: a VIRTUAL_ENV inherited
# from `uv run` stops the child running `uv run pytest` in its own repository.
ENV_REMOVED = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")

# How many lifecycle events a Claude Code settings block registers. The twelve names
# are in test_doctor_setup.py; the count is spelled here for the same reason they are
# spelled there, which is that a table compared against itself agrees with itself.
CLAUDE_HOOK_EVENTS = 12

# How many captures the concurrency test starts at the same instant. See its docstring:
# two is the case the brief names and four is the case that actually fails when the
# store's open is wrong.
CONCURRENT = 4


def _telltale() -> str:
    executable = shutil.which("telltale")
    assert executable is not None, "no `telltale` on PATH: run `uv sync` first"
    return executable


def _run(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    # check=False: the exit code is what is under test, in every one of these.
    return subprocess.run(
        [_telltale(), *args], capture_output=True, timeout=120, check=False, **kwargs
    )


def _text(*args: str) -> str:
    completed = _run(*args)
    assert completed.returncode == 0, completed.stderr.decode()
    return completed.stdout.decode()


def _rows(stdout: str) -> list[list[str]]:
    """The printed table as a list of cell lists, header and rule dropped."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    return [line.split() for line in lines[2:]]


def _observations(capture_id: str) -> list[dict[str, Any]]:
    """Read one capture back out of the database the command just wrote.

    Through the store's own reader, in this process, after the subprocess exited: the
    launcher closes the store as its last act, so everything it accepted is committed
    by the time the exit code arrives here.
    """
    from telltale import config
    from telltale.store import Store

    return Store(config.db_path()).observations(capture_id)


def _types(capture_id: str) -> list[str]:
    return [str(row["observation_type"]) for row in _observations(capture_id)]


def _payload(capture_id: str, obs_type: str) -> dict[str, Any]:
    for row in _observations(capture_id):
        if row["observation_type"] == obs_type:
            return dict(row["payload"])
    raise AssertionError(f"{capture_id} has no {obs_type}")


def _capture_ids() -> list[str]:
    return [row[0] for row in _rows(_text("sessions"))]


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_the_child_exit_code_is_the_launcher_exit_code() -> None:
    """Exit 3 in, exit 3 out, and a capture that says what it wrapped.

    The four Telltale observation types are the whole of what a capture with no
    provider surfaces holds: what repository it ran in, what environment, that it
    started, and that it ended. A wrapper around `bash` configures nothing else, and
    the coverage column says so by printing `-` rather than 0.
    """
    completed = _run("run", "--", "bash", "-c", "echo hi; exit 3")

    assert completed.returncode == 3, completed.stderr.decode()
    assert completed.stdout == b"hi\n"
    listed = _capture_ids()
    assert len(listed) == 1, _text("sessions")
    stored = _types(listed[0])
    assert "telltale.capture_started" in stored
    assert "telltale.capture_ended" in stored
    assert "telltale.repo.identity" in stored
    assert "telltale.environment" in stored
    started = _payload(listed[0], "telltale.capture_started")
    assert started["argv_shape"] == ["bash", "-c"], started
    assert _payload(listed[0], "telltale.capture_ended")["exit_code"] == 3


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_stream_json_output_is_teed_byte_for_byte() -> None:
    """Every line reaches both the terminal and the receiver, and the bytes are equal.

    The child is a python process printing three recorded Claude Code stream lines,
    with `--output-format stream-json` in its argv because that flag is what the plan
    reads to decide to tee at all. So this exercises the real decision, the real HTTP
    route and the real claude parser, and the assertion that matters is the equality:
    a recorder that reformatted its subject's output would be visible to every other
    consumer of that stream.
    """
    expected = b"".join(
        line.encode("utf-8") for line in _fixture_lines(FIXTURE_STREAM, STREAM_LINES)
    )
    script = "import sys, pathlib; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())"  # noqa: E501

    completed = _run(
        "run",
        "--provider",
        "claude",
        "--",
        "python",
        "-c",
        script,
        str(_written(expected)),
        "--output-format",
        "stream-json",
    )

    assert completed.returncode == 0, completed.stderr.decode()
    assert completed.stdout == expected
    capture = _capture_ids()[0]
    stream = [name for name in _types(capture) if name.startswith("claude.stream.")]
    assert len(stream) == STREAM_LINES, _types(capture)
    configured = _payload(capture, "telltale.capture_started")["surfaces_configured"]
    assert "stream" in configured, configured


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_sigterm_reaches_the_child_and_the_code_is_128_plus_the_signal() -> None:
    """A SIGTERM aimed at the launcher kills the child, and 143 comes back.

    The launcher must not die first: it has a capture to close, and the exit code it
    returns is the child's. The child prints a line before it sleeps so that the signal
    is sent when there IS a child, which is the difference between testing the
    forwarding and testing the launcher's own default signal disposition.
    """
    child = subprocess.Popen(
        [
            _telltale(),
            "run",
            "--",
            "python",
            "-c",
            "print('ready', flush=True)\nimport time; time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
    )
    assert child.stdout is not None
    assert child.stdout.readline() == b"ready\n"

    child.terminate()
    code = child.wait(timeout=60)
    child.stdout.close()

    assert code == SIGTERM_CODE
    capture = _capture_ids()[0]
    assert _payload(capture, "telltale.capture_ended")["exit_code"] == SIGTERM_CODE


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_captures_started_at_once_all_finish_and_are_all_listed() -> None:
    """Four launchers, one SQLite file, and no loser.

    An orchestrator runs several agents at once, so two `telltale run` processes
    writing into one database is the normal case rather than an edge one. Four rather
    than two because two is a coin flip: CI failed a two-way version of this test on a
    real bug that a four-way version reproduces in about a third of opens.

    The bug is worth naming, since this test is what stands between it and the next
    person. `PRAGMA journal_mode = WAL` needs an exclusive lock, and it is the one
    statement SQLite does not apply the busy timeout to, so on a database that does not
    exist yet every process tries to set it and all but one are told "database is
    locked" with no wait. Store.open reads the mode first and retries.
    """
    children = [
        subprocess.Popen([_telltale(), "run", "--", "python", "-c", "pass"])
        for _ in range(CONCURRENT)
    ]
    codes = [child.wait(timeout=120) for child in children]

    assert codes == [0] * CONCURRENT
    listed = _capture_ids()
    assert len(listed) == CONCURRENT, _text("sessions")
    for capture in listed:
        assert _payload(capture, "telltale.capture_ended")["exit_code"] == 0


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_the_child_keeps_stdin_and_loses_the_two_environment_names() -> None:
    """stdin passes through untouched, and VIRTUAL_ENV does not.

    Two promises in one child, because both are about the process the launcher starts
    rather than about anything it records. stdin is the terminal's: an agent that
    cannot read its own input is not being recorded, it is being broken. VIRTUAL_ENV
    and UV_PROJECT_ENVIRONMENT are E01 finding 6, and `env_removed` in capture_started
    is where a reader learns that the launcher changed the environment at all.
    """
    script = (
        "import os, sys;"
        "sys.stdout.write(sys.stdin.read());"
        "sys.stdout.write(' '.join(n for n in"
        f" {ENV_REMOVED!r} if n in os.environ) or 'none')"
    )

    completed = _run(
        "run", "--provider", "claude", "--", "python", "-c", script, input=b"echoed:"
    )

    assert completed.returncode == 0, completed.stderr.decode()
    assert completed.stdout == b"echoed:none"
    started = _payload(_capture_ids()[0], "telltale.capture_started")
    # Both names are removed when they are set. This process runs under `uv run`, so
    # VIRTUAL_ENV is set here and the child proved above that it did not inherit it.
    present = [name for name in ENV_REMOVED if name in os.environ]
    assert started["env_removed"] == present, started


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_settings_the_owner_passed_are_merged_and_not_replaced() -> None:
    """A `--settings` of your own survives, with ours added beside it.

    Claude Code takes its hook configuration on the command line, and so does
    Telltale, which means both want the same flag. Replacing what you passed would be
    capture changing the child's behaviour, which is the one thing it may never do
    (AGENTS.md invariant 8). So the launcher parses your JSON, appends its http hook to
    each event's list, and hands the result on: your `model`, your own PostToolUse
    hook, and ours, in that order.
    """
    my_hook = {"hooks": [{"type": "command", "command": "echo mine"}]}
    mine = {"model": "opus", "hooks": {"PostToolUse": [my_hook]}}
    script = "import sys; print(sys.argv[sys.argv.index('--settings') + 1])"

    completed = _run(
        "run",
        "--provider",
        "claude",
        "--",
        "python",
        "-c",
        script,
        "--settings",
        json.dumps(mine),
    )

    assert completed.returncode == 0, completed.stderr.decode()
    merged = json.loads(completed.stdout)
    assert merged["model"] == "opus", merged
    entries = merged["hooks"]["PostToolUse"]
    assert entries[0] == my_hook, entries
    assert entries[1]["hooks"][0]["type"] == "http", entries
    assert len(merged["hooks"]) == CLAUDE_HOOK_EVENTS, sorted(merged["hooks"])


# A child that behaves like the agent the launcher configured: it reads the hook URL
# out of the `--settings` JSON the plan put in its own argv, and POSTs one PostToolUse
# body naming a commit. Kept as text rather than as a function, because it runs in the
# child process and its argv is the thing under test.
HOOK_CHILD = """
import json, sys, urllib.request
settings = json.loads(sys.argv[sys.argv.index("--settings") + 1])
url = settings["hooks"]["PostToolUse"][0]["hooks"][0]["url"]
body = json.dumps({
    "hook_event_name": "PostToolUse",
    "session_id": "00000000-0000-4000-8000-00000000f00d",
    "tool_name": "Bash",
    "tool_response": {"gitOperation": {"commit": {"sha": sys.argv[1], "kind": "user"}}},
}).encode("utf-8")
request = urllib.request.Request(
    url, data=body, headers={"Content-Type": "application/json"}
)
sys.stdout.write(str(urllib.request.urlopen(request, timeout=10).status))
"""
# A sha this repository does not contain, so the linkage cannot succeed by accident:
# repo.commits_since still returns it, at the provider_reported rung, with every other
# field unknown. Spec 12.3: a claim that was made and could not be resolved is kept.
UNKNOWN_SHA = "0" * 40


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_a_hook_is_attributed_and_its_commit_is_linked_before_the_store_closes() -> (
    None
):
    """The whole loop, in one wrapped run: settings, hook, attribution, linkage.

    Four things have to hold at once and each one is a decision this task took. The
    `--settings` the plan handed the child names a URL that is really listening. The
    hook body carries a session id nobody registered, and it is still attributed,
    because a receiver serving one capture has a default_capture and nothing to guess.
    The commit id the hook reported is read back OUT of the store to link it, which is
    what makes `Store.flush` load-bearing rather than decorative: an empty queue is not
    a write barrier, and this read happens milliseconds after the POST. And the
    unresolvable sha comes back as a commit observation instead of being dropped.
    """
    script = _written(HOOK_CHILD.encode("utf-8"), "hook-child.py")

    completed = _run(
        "run", "--provider", "claude", "--", "python", str(script), UNKNOWN_SHA
    )

    assert completed.returncode == 0, completed.stderr.decode()
    assert completed.stdout == b"200"
    capture = _capture_ids()[0]
    assert "claude.hook.PostToolUse" in _types(capture)
    commit = _payload(capture, "telltale.repo.commit")
    assert commit["sha"] == UNKNOWN_SHA, commit
    assert commit["link_confidence"] == "provider_reported", commit


def _fixture_lines(path: Path, count: int) -> Sequence[str]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    assert len(lines) >= count, path
    # Every line must parse: a fixture that stopped being JSON would make the tee test
    # pass for the wrong reason, since equal bytes say nothing about what was stored.
    for line in lines[:count]:
        json.loads(line)
    return lines[:count]


def _written(payload: bytes, name: str = "stream-fixture.jsonl") -> Path:
    """Bytes on disk in the temporary home, for a child to read with no shell."""
    from telltale import config

    path = config.home() / name
    path.write_bytes(payload)
    return path
