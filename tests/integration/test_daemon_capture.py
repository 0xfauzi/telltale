"""A day-to-day session, its repository, and the commit it made. W9-T1.

Every test here posts over HTTP to a real receiver running in derive-captures mode,
which is the daemon's mode, with a real Binder over a real temporary git repository.
There is no fake hook body in the sense that matters: the shapes below are the shapes
measured on Claude Code 2.1.263 through the running daemon on 2026-09-06 (capture
cap_b0be0e0da48a5a1a78bdb8fa), cut to the fields the binding path reads.

What that capture stored before this module existed, and what these tests would have
found on main:

  PreToolUse payload   "cwd":"<outside>/14fee89e", "file_path":"<outside>/0cc7d242"
  every observation    repo_id NULL
  tool_result payload  "git_commit_id":"0881c21", seven characters
  telltale.repo.commit none, and `sessions --link-commits` skipped the capture

Two of those numbers were re-measured against this branch's parent before the change,
with the same POSTs these tests make: 3 observations, repo_id None on all three, both
paths hashed, 0 repo.commit rows and "linked 0 commits in this repository".

One assertion the brief asked for is not written the way it was asked. "the string
'hi' is absent from the db bytes" cannot be asserted against the FILE: measured, a
freshly opened store already contains b"hi" twice, both times inside the word "this"
in the schema's own comments, which SQLite keeps in sqlite_master. So the content
assertion is made where a leak would actually be, over every stored payload, and the
db-bytes assertion is made about the key that would carry it.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from telltale import repo
from telltale.daemon_capture import Binder
from telltale.receiver import Receiver, _drain, _post, derived_capture_id

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from telltale.store import Store

# A session id per test, because the capture id is a function of it alone
# (receiver.derived_capture_id) and two tests sharing one would share a capture.
INSIDE = "00000000-0000-4000-8000-00000000in51"
COMMIT = "00000000-0000-4000-8000-000000c0mm17"
OUTSIDE = "00000000-0000-4000-8000-0000000u7514"
RELINK = "00000000-0000-4000-8000-00000072l1nk"

# What the Write hook says it is writing. `content` never reaches the store (design
# 6.3), and the file name is what has to come out repo-relative.
WRITTEN = "hello.txt"
CONTENT = "hi"

# Long enough for the 2 s snapshot debounce to fire and its git calls to finish, and
# for the linkage worker a SessionEnd starts. Both are bounded by git rather than by a
# timer, so this is a ceiling and not a measurement: the loops below stop early.
SETTLE_S = 20.0
POLL_S = 0.1


@dataclass(frozen=True)
class Daemon:
    """A receiver in the daemon's mode, with the Binder the daemon wires into it."""

    receiver: Receiver
    binder: Binder
    store: Store
    port: int

    def hook(self, body: dict[str, Any]) -> int:
        return _post(self.port, "/hooks/claude", json.dumps(body).encode("utf-8"))

    def logs(self, body: dict[str, Any]) -> int:
        return _post(self.port, "/v1/logs", json.dumps(body).encode("utf-8"))

    def drain(self) -> dict[str, Any]:
        return _drain(self.port)

    def observations(self, capture_id: str) -> list[dict[str, Any]]:
        self.store.flush()
        return self.store.observations(capture_id)

    def wait_for(self, capture_id: str, obs_type: str) -> list[dict[str, Any]]:
        """Poll until an observation of this type exists, or SETTLE_S goes by.

        A poll rather than a sleep because the two things this file waits for are a
        debounced snapshot and a linkage worker, and the honest bound on either is
        "when git finishes", which nothing here can measure in advance.
        """
        deadline = time.monotonic() + SETTLE_S
        while time.monotonic() < deadline:
            found = [
                row
                for row in self.observations(capture_id)
                if row["observation_type"] == obs_type
            ]
            if found:
                return found
            time.sleep(POLL_S)
        return []


@pytest.fixture
def daemon(store: Store) -> Iterator[Callable[[], Daemon]]:
    """The receiver `telltale daemon` starts, with its Binder, on a free port."""
    started: list[tuple[Receiver, Binder]] = []

    def start() -> Daemon:
        receiver = Receiver(
            store,
            level=1,
            derive_captures=True,
            ctx_for_capture=lambda capture: binder.ctx(capture),
        )
        binder = Binder(store, receiver, 1)
        receiver.on_record(binder.observe)
        receiver.on_repo_change(binder.repo_changed)
        port = receiver.start()
        started.append((receiver, binder))
        return Daemon(receiver=receiver, binder=binder, store=store, port=port)

    yield start
    for receiver, binder in started:
        receiver.stop()
        binder.close()


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, timeout=30, check=True
    )
    return done.stdout.decode().strip()


def _repository(root: Path) -> Path:
    """A repository with one commit and an identity that is not the operator's.

    The three config values are set on this repository alone: a global signing key
    would make every commit here wait for a passphrase, and Telltale may not edit an
    operator's configuration to find out (AGENTS.md invariant 7).
    """
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", ".")
    for name, value in (
        ("user.email", "telltale-test@example.invalid"),
        ("user.name", "Telltale Test"),
        ("commit.gpgsign", "false"),
    ):
        _git(root, "config", name, value)
    (root / "f").write_text("base\n", encoding="utf-8")
    _git(root, "add", "f")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _write_hook(session: str, cwd: Path) -> dict[str, Any]:
    """The PreToolUse body Claude Code posts before a Write. 2.1.263's shape."""
    return {
        "session_id": session,
        "cwd": str(cwd),
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(cwd / WRITTEN), "content": CONTENT},
    }


def _attr(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _tool_result(session: str, sha: str) -> dict[str, Any]:
    """One OTLP tool_result log record for a `git commit` that has run.

    `git_commit_id` rides in `tool_parameters`, which is where claude._lifted reads it
    from, and it is the SHORT sha because that is what 2.1.263 reports.
    """
    record = {
        "timeUnixNano": "1788293016298000000",
        "body": {"stringValue": "claude_code.tool_result"},
        "attributes": [
            _attr("session.id", session),
            _attr("event.name", "tool_result"),
            _attr("tool_name", "Bash"),
            _attr("success", "true"),
            _attr(
                "tool_parameters",
                json.dumps(
                    {
                        "full_command": f"git add {WRITTEN} && git commit -m W9-smoke",
                        "git_commit_id": sha,
                    }
                ),
            ),
        ],
    }
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [_attr("service.name", "claude-code")],
                },
                "scopeLogs": [{"logRecords": [record]}],
            }
        ]
    }


def _payload(rows: list[dict[str, Any]], obs_type: str) -> dict[str, Any]:
    found = [row for row in rows if row["observation_type"] == obs_type]
    assert len(found) == 1, f"{obs_type}: {len(found)} rows, expected one"
    return dict(found[0]["payload"])


@pytest.mark.integration
def test_a_daemon_capture_binds_its_repository_from_the_first_hook_cwd(
    daemon: Callable[[], Daemon], tmp_path: Path
) -> None:
    """One PreToolUse, and the capture has a repository and repo-relative paths.

    Four claims, and each one is a column or a field the change clock later reads:
    every observation carries the repo_id `repo.identity` gives that checkout, the
    identity itself is recorded once, the written file is `hello.txt` rather than a
    hash of somewhere, and nothing anywhere in the capture says `<outside>`.
    """
    root = _repository(tmp_path / "repo")
    live = daemon()
    capture = derived_capture_id(INSIDE)

    assert live.hook(_write_hook(INSIDE, root)) == 200
    live.drain()

    rows = live.observations(capture)
    expected = repo.identity(root)["repo_id"]
    assert expected is not None
    assert {row["repo_id"] for row in rows} == {expected}
    assert _payload(rows, "telltale.repo.identity")["repo_id"] == expected

    hook = _payload(rows, "claude.hook.PreToolUse")
    assert hook["file_path"] == WRITTEN
    assert hook["cwd"] == "."
    blob = json.dumps([row["payload"] for row in rows])
    assert "<outside>" not in blob, blob
    # design 6.3: the bytes a Write would have written are never persisted. Asserted
    # over the payloads because the schema's own comments put b"hi" in the file: see
    # the module docstring.
    assert "content" not in hook
    assert CONTENT not in blob, blob


@pytest.mark.integration
def test_a_daemon_capture_links_its_commit_from_the_short_sha_it_reported(
    daemon: Callable[[], Daemon],
    tmp_path: Path,
    db_after_close: Callable[[Store], bytes],
) -> None:
    """A session that commits gets one repo_commit, with the FULL sha, at rung
    provider_reported.

    The provider reports seven characters. `_candidates` would otherwise hold both
    spellings of that one commit and link it twice, once at the stated rung and once
    off the snapshot the commit_command trigger took, so repo_link._resolved is what
    makes this assertion "one row" rather than "at least one".
    """
    root = _repository(tmp_path / "repo")
    live = daemon()
    capture = derived_capture_id(COMMIT)

    assert live.hook(_write_hook(COMMIT, root)) == 200
    live.drain()

    (root / WRITTEN).write_text(f"{CONTENT}\n", encoding="utf-8")
    _git(root, "add", WRITTEN)
    _git(root, "commit", "-q", "-m", "W9-smoke")
    full = _git(root, "rev-parse", "HEAD")
    short = _git(root, "rev-parse", "--short", "HEAD")
    assert short != full
    assert full.startswith(short)

    assert live.logs(_tool_result(COMMIT, short)) == 200
    live.drain()
    assert live.hook({
        "session_id": COMMIT,
        "cwd": str(root),
        "hook_event_name": "SessionEnd",
        "reason": "other",
    }) == 200  # fmt: skip

    linked = live.wait_for(capture, "telltale.repo.commit")
    assert len(linked) == 1, [row["payload"] for row in linked]
    assert linked[0]["payload"]["sha"] == full
    assert linked[0]["payload"]["link_confidence"] == "provider_reported"
    assert linked[0]["repo_id"] == repo.identity(root)["repo_id"]
    # The reducer ran, which is what puts the commit on the change clock at all.
    assert live.store.activities(capture, ("repo_commit",))

    live.receiver.stop()
    live.binder.close()
    assert b'"content"' not in db_after_close(live.store)


@pytest.mark.integration
def test_a_session_outside_a_checkout_binds_nothing_and_says_nothing(
    daemon: Callable[[], Daemon], tmp_path: Path
) -> None:
    """repo_id None is the truth about that session, not an error about it.

    No identity observation, because there is no repository to describe; no diagnostic,
    because nothing went wrong; and the paths go back to being hashed, because outside
    a checkout that is what the sanitizer can honestly say about them.
    """
    outside = tmp_path / "not-a-checkout"
    outside.mkdir()
    live = daemon()
    capture = derived_capture_id(OUTSIDE)

    assert live.hook(_write_hook(OUTSIDE, outside)) == 200
    live.drain()

    rows = live.observations(capture)
    assert rows, "the record was not stored at all"
    assert {row["repo_id"] for row in rows} == {None}
    assert [row["observation_type"] for row in rows] == ["claude.hook.PreToolUse"]
    hook = _payload(rows, "claude.hook.PreToolUse")
    assert str(hook["file_path"]).startswith("<outside>/")
    assert live.store.diagnostics(capture) == []


@pytest.mark.integration
def test_sessions_link_commits_finds_a_daemon_capture_and_adds_nothing_to_it(
    daemon: Callable[[], Daemon], tmp_path: Path, telltale_home: Path
) -> None:
    """The command that used to skip every daemon capture now reaches this one.

    Two facts in one run, and they are two because either alone would pass for the
    wrong reason: `--repo` filters on the repo_id the binding wrote, so the capture
    printing at all is proof the column is set; and "linked 0" is proof the session end
    already did the work rather than proof that the command skipped the capture again.
    """
    root = _repository(tmp_path / "repo")
    live = daemon()
    capture = derived_capture_id(RELINK)

    assert live.hook(_write_hook(RELINK, root)) == 200
    live.drain()
    (root / WRITTEN).write_text(f"{CONTENT}\n", encoding="utf-8")
    _git(root, "add", WRITTEN)
    _git(root, "commit", "-q", "-m", "W9-smoke")
    short = _git(root, "rev-parse", "--short", "HEAD")
    assert live.logs(_tool_result(RELINK, short)) == 200
    live.drain()
    assert live.hook({
        "session_id": RELINK,
        "cwd": str(root),
        "hook_event_name": "SessionEnd",
    }) == 200  # fmt: skip
    assert live.wait_for(capture, "telltale.repo.commit")

    live.receiver.stop()
    live.binder.close()
    live.store.close()

    repo_id = repo.identity(root)["repo_id"]
    done = subprocess.run(
        ["telltale", "sessions", "--link-commits", "--repo", str(repo_id)],
        cwd=root,
        capture_output=True,
        timeout=120,
        check=False,
        env={**os.environ, "TELLTALE_HOME": str(telltale_home)},
    )
    out = done.stdout.decode()
    assert done.returncode == 0, done.stderr.decode()
    assert capture in out, out
    assert "linked 0 commits in this repository" in out, out
