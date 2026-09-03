"""The three ways a capture fails, provoked through the real launcher. W6-T4.

Each of these was measured before it was made legible, and the measurement is in
docs/log/W6-T4.md. None of them is a hypothetical: a port a stale daemon still holds, a
session somebody Ctrl-C'd or a scheduler killed, and a second `telltale` writing to the
same database are what a recorder meets on a laptop that is also doing work.

The three properties every test here asserts, and they are the same three:

  the child's exit code is what the caller sees, whatever happened to the recorder
    (AGENTS.md invariant 8), and for a child a signal killed that is 128 + N;
  the failure ends up in the store as a `launcher` diagnostic, or on stderr when the
    store is the thing that failed;
  nothing is invented: a lost batch is reported as lost and never as a zero.

Real processes throughout. The launcher runs as the installed console script, the child
is fake_agent.py, the port is a real bound socket and the lock is a real second process
holding BEGIN IMMEDIATE on the real database. There is nothing here to stub, and a stub
of any of the four would be a test of the stub.

The locked-database test takes about 47 s, and the cost is the store's own retry ladder:
store.BUSY_TIMEOUT_MS is 5000 and store.RETRY_DELAYS_S has five entries, so a batch that
cannot be committed is given up 39.3 s after its first blocked attempt (measured), and
the lock has to outlive that for anything to be lost at all. It is the slowest test in
the suite by a factor of four. The alternative was not testing the failure that loses
data.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from telltale import launch_health
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Sequence

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"

# How long the child idles, and how long the lock is held. The sleep is long enough
# that a signal arrives while the launcher is teeing and the child is doing nothing;
# the hold is 5.7 s past the 39.3 s the writer takes to give a batch up, which is the
# margin between "the batch is lost" and "the batch commits late and nothing is lost".
_CHILD_SLEEP_S = 20.0
_LOCK_HOLD_S = 45.0
# A launcher that has been signalled exits in about 0.13 s (measured); a locked run
# takes about 47 s. One bound for both, well past either.
_WAIT_S = 120.0


def _repo(root: Path) -> tuple[Path, Path]:
    """A temporary $TELLTALE_HOME and a git repository to run in, as conftest does.

    The repository is real and has a commit, because the writes a locked database loses
    include the repository snapshot, and a capture with no repository would not queue
    one.
    """
    home, repo = root / "telltale-home", root / "repo"
    for made in (home, repo, root / "home"):
        made.mkdir(parents=True, exist_ok=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    for args in (
        ("init", "-q", "."),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Telltale Test"),
        ("add", "README.md"),
        ("commit", "-q", "-m", "base"),
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return home, repo


def _telltale() -> str:
    found = shutil.which("telltale")
    assert found is not None, "no `telltale` on PATH: run `uv sync` first"
    return found


def _env(home: Path) -> dict[str, str]:
    return {
        "TELLTALE_HOME": str(home),
        "HOME": str(home.parent / "home"),
        "PATH": os.environ.get("PATH", ""),
    }


def _cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_telltale(), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=_WAIT_S,
        env=_env(home),
    )


def _launch(
    home: Path, repo: Path, *flags: str, capture_output: bool = True
) -> subprocess.Popen[str]:
    """`telltale run` around the fake agent, still running when this returns."""
    argv = [_telltale(), "run", "--provider", "claude", "--"]
    child = [sys.executable, str(FAKE_AGENT), "--seed", "7"]
    return subprocess.Popen(
        [*argv, *child, "--output-format", "stream-json", *flags],
        cwd=str(repo),
        env=_env(home),
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=True,
        bufsize=1,
    )


def _started(proc: subprocess.Popen[str]) -> dict[str, Any]:
    """Block until the child has printed its init line, and return it.

    A readiness signal rather than a sleep: the line is the child's own first byte of
    stdout through the launcher's tee, so once it is here the child is running, the
    receiver has taken a record, and the child is inside its `--sleep`.
    """
    assert proc.stdout is not None
    line = proc.stdout.readline()
    parsed: dict[str, Any] = json.loads(line)
    assert parsed["subtype"] == "init", line
    return parsed


def _children(parent: int) -> list[int]:
    """The pids whose parent is `parent`, from ps alone.

    `ps -Ao pid=,ppid=` and not `pgrep -P` or /proc: this is the POSIX spelling, and it
    is the same command on the owner's macOS and on CI's ubuntu.
    """
    listed = subprocess.run(
        ["ps", "-Ao", "pid=,ppid="], capture_output=True, text=True, check=True
    )
    found = []
    for line in listed.stdout.splitlines():
        cells = line.split()
        if len(cells) == 2 and int(cells[1]) == parent:
            found.append(int(cells[0]))
    return found


def _alive(pids: Sequence[int]) -> list[int]:
    """Which of these processes still exist. Signal 0 asks and does nothing."""
    living = []
    for pid in pids:
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        living.append(pid)
    return living


def _ended(home: Path) -> dict[str, Any]:
    """The one telltale.capture_ended payload in this home's store."""
    store = Store(home / "telltale.db")
    payloads = [
        dict(row["payload"])
        for capture in store.captures()
        for row in store.observations(
            str(capture["capture_id"]), ("telltale.capture_ended",)
        )
    ]
    assert len(payloads) == 1, payloads
    return payloads[0]


def _held_port(home: Path) -> tuple[socket.socket, int]:
    """A bound and listening socket nobody answers on, named as the daemon's port.

    The port goes into this home's config.json, which is where `telltale daemon` and
    `telltale doctor` read it from (cli._configured). That is what makes this a test
    about the daemon's port rather than about some port: no fixed number is claimed,
    and nothing outside the temporary home is touched.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = int(listener.getsockname()[1])
    (home / "config.json").write_text(json.dumps({"daemon_port": port}), "utf-8")
    return listener, port


@pytest.mark.integration
def test_a_run_records_while_the_daemon_port_is_held(tmp_path: Path) -> None:
    """A held daemon port cannot stop `telltale run`, because run never uses it.

    The receiver a run starts is built with no port and the OS picks a free one
    (launch._open), so the failure `telltale daemon` can have is one `telltale run`
    cannot. Asserted rather than assumed: the run below has the daemon's port held
    for its whole life and still delivers the stream surface, which is the surface
    that could only have arrived over the port the OS chose.
    """
    home, repo = _repo(tmp_path)
    listener, port = _held_port(home)
    try:
        proc = _launch(home, repo, "--sleep", "0")
        out, err = proc.communicate(timeout=_WAIT_S)
        # Inside the `try`, so this asks about the port while it is still held: the
        # run above proves nothing if the socket was already closed by then.
        probed = _cli(home, "doctor", "--port", str(port)).stdout
    finally:
        listener.close()

    assert proc.returncode == 0, out + err
    ended = _ended(home)
    assert ended["exit_code"] == 0, ended
    assert ended["terminal"] == "exit", ended
    assert ended["surfaces_received"].get("stream"), ended
    assert f"port {port} held, not answering" in probed, probed


@pytest.mark.integration
def test_the_daemon_refuses_a_held_port_in_one_line(tmp_path: Path) -> None:
    """One line naming the port and what holds it, exit 1, and no traceback.

    Measured before W6-T4: 23 lines of traceback ending in `OSError: [Errno 48]
    Address already in use`, and the store the command had already opened was left
    open. The port here accepts a connection and never answers, which is what a wedged
    process on the daemon port looks like and what doctor's probe reports as `held, not
    answering`.
    """
    home, _repo_path = _repo(tmp_path)
    listener, port = _held_port(home)
    try:
        completed = _cli(home, "daemon")
    finally:
        listener.close()

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    lines = completed.stdout.splitlines()
    assert len(lines) == 1, completed.stdout
    assert str(port) in lines[0], lines
    assert "held, not answering" in lines[0], lines


@pytest.mark.integration
def test_a_sigterm_to_the_launcher_kills_the_child_and_is_recorded(
    tmp_path: Path,
) -> None:
    """The launcher relays SIGTERM, exits 143, and the capture says which signal.

    143 is 128 + 15, which is what a shell reports for a child killed by SIGTERM, and
    it is also what a child that returned 143 itself reports. `terminal` is the field
    that separates them, and it is read off the negative code Popen gives and nowhere
    else (launch._child).
    """
    home, repo = _repo(tmp_path)
    proc = _launch(home, repo, "--sleep", str(_CHILD_SLEEP_S))
    _started(proc)
    children = _children(proc.pid)
    assert len(children) == 1, children

    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=_WAIT_S)

    assert proc.returncode == 143, (proc.returncode, out, err)
    assert _alive(children) == [], "the child outlived the launcher"
    ended = _ended(home)
    assert ended["exit_code"] == 143, ended
    assert ended["terminal"] == "signal 15", ended


@pytest.mark.integration
def test_a_child_killed_outright_is_recorded_as_signal_nine(tmp_path: Path) -> None:
    """SIGKILL to the CHILD: the launcher reports 137 and records signal 9.

    The other half of the signal contract, and the half no relay is involved in: the
    launcher was not signalled at all here, it only waited on a child that stopped
    existing. Nothing about the recorder changes the code the caller sees.
    """
    home, repo = _repo(tmp_path)
    proc = _launch(home, repo, "--sleep", str(_CHILD_SLEEP_S))
    _started(proc)
    children = _children(proc.pid)
    assert len(children) == 1, children

    os.kill(children[0], signal.SIGKILL)
    out, err = proc.communicate(timeout=_WAIT_S)

    assert proc.returncode == 137, (proc.returncode, out, err)
    ended = _ended(home)
    assert ended["exit_code"] == 137, ended
    assert ended["terminal"] == "signal 9", ended


@pytest.mark.integration
def test_a_locked_database_costs_a_batch_and_the_capture_says_so(
    tmp_path: Path,
) -> None:
    """A second process holding the write lock loses a batch, and the store says which.

    Measured before W6-T4, with the lock held for 75 s: the run returned the child's 0,
    three observations reached the disk, there was no capture_ended row, no activities
    and ZERO diagnostics anywhere. A capture that lost its ending was indistinguishable
    from one nobody had reduced yet.

    The first run is a warm-up, and it is load-bearing: `Store._prepare` needs the write
    lock to create a schema, so a lock taken before the FIRST run in a home is a
    different failure (the store never opens, the run fails open and says so on stderr).
    The lock here is taken against a database that already has its schema, which is
    what a second `telltale` on a machine that has recorded before actually does.

    See `_LOCK_HOLD_S` for the timing, which is the store's own retry ladder and not a
    number this test chose.
    """
    home, repo = _repo(tmp_path)
    warm = _launch(home, repo, "--sleep", "0")
    warm.communicate(timeout=_WAIT_S)
    assert warm.returncode == 0

    holder = _lock_holder(home / "telltale.db", _LOCK_HOLD_S)
    try:
        proc = _launch(home, repo, "--sleep", "1")
        out, err = proc.communicate(timeout=_WAIT_S)
    finally:
        holder.wait(timeout=_WAIT_S)

    assert proc.returncode == 0, (proc.returncode, out, err)
    said = [
        str(row["detail"])
        for row in Store(home / "telltale.db").diagnostics()
        if row["kind"] == "launcher" and "the store lost part of this capture" in
        str(row["detail"])
    ]  # fmt: skip
    assert len(said) == 1, said
    assert "batch(es) the writer gave up on" in said[0], said
    assert "1 batch(es)" in said[0], said
    assert str(home / "telltale.db") in said[0], said
    assert "lock" in said[0], said
    # And doctor is where an owner who was not watching finds it afterwards. The
    # line is the LAST launcher diagnostic, and this one is written after the
    # reducer's, so a capture that lost a batch is what doctor reports about.
    printed = _cli(home, "doctor").stdout
    line = next(one for one in printed.splitlines() if one.startswith("last launcher "))
    assert said[0] in line, printed


def _lock_holder(db: Path, seconds: float) -> subprocess.Popen[bytes]:
    """A second process holding BEGIN IMMEDIATE on the store, and nothing else.

    BEGIN IMMEDIATE with no statement inside it: measured, that alone takes the write
    lock in WAL mode, so the holder adds no row of its own to the database the
    assertions then read. It returns once the lock is HELD, so the caller does not race
    it.
    """
    script = (
        "import sqlite3, sys, time;"
        "conn = sqlite3.connect(sys.argv[1], timeout=60);"
        "conn.execute('BEGIN IMMEDIATE');"
        "print('held', flush=True);"
        "time.sleep(float(sys.argv[2]));"
        "conn.rollback()"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", script, str(db), str(seconds)], stdout=subprocess.PIPE
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == b"held"
    return holder


@pytest.mark.integration
def test_the_lock_probe_tells_a_held_lock_from_a_free_one(tmp_path: Path) -> None:
    """The measurement the locked-capture diagnostic names the lock with.

    Both answers, on one real database, because a probe that always said "held" would
    make every diagnostic name a lock that was not there. It is checked here rather
    than only inside the 47 s test above so that the two answers cost nothing.
    """
    db = tmp_path / "telltale.db"
    Store(db).open().close()

    assert "is free now" in launch_health.lock_state(db)
    holder = _lock_holder(db, 5.0)
    try:
        held = launch_health.lock_state(db)
    finally:
        holder.kill()
        holder.wait(timeout=_WAIT_S)
    assert "another process holds the write lock" in held, held
    assert "database is locked" in held, held


@pytest.mark.integration
def test_the_probe_does_not_wait_for_a_lock_it_finds(tmp_path: Path) -> None:
    """It reports in milliseconds, not in the store's five seconds of busy_timeout.

    The probe runs at the end of a capture, after the child has exited, so a probe that
    waited would add its wait to every locked run. store.BUSY_TIMEOUT_MS is 5000, and
    the bound below is a tenth of it: enough to say the busy timeout is not being paid,
    loose enough not to fail on a loaded machine.
    """
    db = tmp_path / "telltale.db"
    Store(db).open().close()
    holder = _lock_holder(db, 10.0)
    try:
        started = time.monotonic()
        launch_health.lock_state(db)
        elapsed = time.monotonic() - started
    finally:
        holder.kill()
        holder.wait(timeout=_WAIT_S)
    assert elapsed < 0.5, elapsed


@pytest.mark.integration
def test_a_run_that_lost_nothing_writes_no_health_diagnostic(tmp_path: Path) -> None:
    """The other side of the locked test: an ordinary run says nothing about the store.

    Without this, a diagnostic that fired on every capture would pass the test above
    and mean nothing. `sqlite3` is imported here to prove the database this run wrote
    is the same file the lock tests hold: same path, same schema, no lock.
    """
    home, repo = _repo(tmp_path)
    proc = _launch(home, repo, "--sleep", "0")
    out, err = proc.communicate(timeout=_WAIT_S)

    assert proc.returncode == 0, out + err
    assert "the store lost" not in err, err
    details = [str(row["detail"]) for row in Store(home / "telltale.db").diagnostics()]
    assert not [one for one in details if "the store lost part" in one], details
    with sqlite3.connect(f"file:{home / 'telltale.db'}?mode=ro", uri=True) as conn:
        counted = conn.execute("SELECT count(*) FROM observations").fetchone()[0]
    assert counted > 0
