"""A session that recorded a change and never numbered itself. W9-T2.

Two captures of one repository through the receiver in the daemon's mode, which is what
a session the owner starts by hand is: nothing launched it, so there is no argv to
fingerprint and no `--task-id` to read. One of them posts a correlation and so IS an
attempt of this lineage; the other posts none and is the shape W9-T1 measured on the
owner's own first evening of day-to-day capture. Both commit, and both report the commit
id the way Claude Code 2.1.263 reports it.

Nothing here is a fake in the sense that matters. Every POST is one Claude Code really
makes, against a real `Receiver` in derive-captures mode with the real `Binder` behind
it, over a real git repository; the commits are real commits and the linkage is
`launch_commits`'s own. What the file asserts is which of those two sessions' numbers
reach which row.

A separate file from test_series_lineage.py, which is at 588 lines against the 800-line
ratchet, and it imports that file's repository and build helpers the way
test_series_backfill.py does. The split is the same one W8-T2 made and for the same
reason.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from test_series_lineage import _built, _cell, _git, _repository, _store

from telltale import repo, series

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from telltale.store import Store

pytestmark = pytest.mark.integration

ATTEMPT_SESSION = "00000000-0000-4000-8000-00000000a77e"
RECORDER_SESSION = "00000000-0000-4000-8000-00000000dae0"

# The attempt ordinal the correlation states. Not 1, so `attempts_to_land` on the row
# it landed cannot pass by being the number a counter would have reached anyway.
LANDED_ATTEMPT = 2

# What each session's one api_request reports. Two different numbers, so a row folding
# the wrong session's requests reads as the other one's total rather than as a tie.
ATTEMPT_TOKENS = 41
RECORDER_TOKENS = 17

# Long enough for the 2 s snapshot debounce and the linkage worker a SessionEnd starts,
# both bounded by git rather than by a timer. test_daemon_capture.py measures the same
# two waits and this is its ceiling; the poll below stops early.
LINK_TIMEOUT_S = 20.0
LINK_POLL_S = 0.1


@dataclass(frozen=True)
class _Daemon:
    """The receiver `telltale daemon` starts, its Binder, and the port it bound."""

    receiver: Any
    binder: Any
    store: Store
    port: int

    def post(self, route: str, body: dict[str, Any]) -> int:
        from telltale.receiver import _post

        return _post(self.port, route, json.dumps(body).encode("utf-8"))

    def drain(self) -> None:
        from telltale.receiver import _drain

        _drain(self.port)

    def committed(self, capture_id: str) -> list[dict[str, Any]]:
        """Poll until the linkage worker has written the commit, or give up.

        A poll rather than a sleep for the reason test_daemon_capture.py gives: what
        this waits for is a git call finishing, and nothing here can measure that in
        advance.
        """
        deadline = time.monotonic() + LINK_TIMEOUT_S
        while time.monotonic() < deadline:
            self.store.flush()
            found = [
                row
                for row in self.store.observations(capture_id)
                if row["observation_type"] == "telltale.repo.commit"
            ]
            if found:
                return found
            time.sleep(LINK_POLL_S)
        return []


@pytest.fixture
def daemon(store: Store) -> Iterator[Callable[[], _Daemon]]:
    """A real receiver in derive-captures mode with the Binder the daemon wires in."""
    from telltale.daemon_capture import Binder
    from telltale.receiver import Receiver

    started: list[tuple[Any, Any]] = []

    def start() -> _Daemon:
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
        return _Daemon(receiver=receiver, binder=binder, store=store, port=port)

    yield start
    for receiver, binder in started:
        receiver.stop()
        binder.close()


def _attr(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _otel(session: str, attributes: list[dict[str, Any]], body: str) -> dict[str, Any]:
    """One OTLP log record from Claude Code, in the encoding E01 measured."""
    record = {
        "timeUnixNano": "1788293057178000000",
        "body": {"stringValue": body},
        "attributes": [_attr("session.id", session), *attributes],
    }
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": [_attr("service.name", "claude-code")]},
                "scopeLogs": [{"logRecords": [record]}],
            }
        ]
    }


def _api_request(session: str, tokens: int) -> dict[str, Any]:
    """The record `fresh_input_tokens_total` is a sum over."""
    return _otel(
        session,
        [
            _attr("event.name", "api_request"),
            _attr("model", "claude-haiku-4-5"),
            _attr("input_tokens", str(tokens)),
        ],
        "claude_code.api_request",
    )


def _tool_result(session: str, sha: str) -> dict[str, Any]:
    """The `git commit` that has run, reporting the SHORT sha 2.1.263 reports."""
    return _otel(
        session,
        [
            _attr("event.name", "tool_result"),
            _attr("tool_name", "Bash"),
            _attr("success", "true"),
            _attr(
                "tool_parameters",
                json.dumps({"full_command": "git commit", "git_commit_id": sha}),
            ),
        ],
        "claude_code.tool_result",
    )


def _session(live: _Daemon, root: Path, session: str, tokens: int) -> tuple[str, str]:
    """One daemon session that reads the repository, commits, and reports the commit.

    Returns (capture_id, sha). Every POST is one an agent really makes: a PreToolUse
    hook binds the capture to the checkout (W9-T1), the api_request carries the tokens,
    the tool_result carries the commit id, and SessionEnd starts linkage.
    """
    from telltale.receiver import derived_capture_id

    capture_id = derived_capture_id(session)
    assert (
        live.post(
            "/hooks/claude",
            {
                "session_id": session,
                "cwd": str(root),
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": str(root / f"{session[-4:]}.txt")},
            },
        )
        == 200
    )
    live.drain()
    assert live.post("/v1/logs", _api_request(session, tokens)) == 200
    (root / f"{session[-4:]}.txt").write_text("x\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", session[-4:])
    sha = _git(root, "rev-parse", "HEAD")
    assert live.post("/v1/logs", _tool_result(session, sha[:7])) == 200
    live.drain()
    assert (
        live.post(
            "/hooks/claude",
            {"session_id": session, "cwd": str(root), "hook_event_name": "SessionEnd"},
        )
        == 200
    )
    linked = live.committed(capture_id)
    assert [row["payload"]["link_confidence"] for row in linked] == [
        "provider_reported"
    ], [row["payload"] for row in linked]
    assert linked[0]["payload"]["sha"] == sha
    return capture_id, sha


@pytest.mark.integration
def test_a_change_row_folds_the_process_columns_of_the_session_that_recorded_it(
    daemon: Callable[[], _Daemon], tmp_path: Path
) -> None:
    """Two daemon sessions, two commits, and only one of them an attempt. W9-T2.

    Before this task the second row held its six repository columns and seven Nones:
    `_change_row` folded over `landed_by` alone, so a capture that recorded a commit
    and never said which attempt it was contributed nothing to the row holding the
    commit it had made. Measured against the parent commit on 2026-09-07, this test
    failed on the second row's `fresh_input_tokens_total`, which was None.

    What the two rows must NOT share is what makes the fold a claim rather than a
    coincidence: each row's token total is its own session's, the attempt's row carries
    its attempt ordinal and the recorder's row carries None, and `series check` still
    resolves every provenance id the recorder brought in against the row's end.
    """
    root = _repository(tmp_path / "repo")
    live = daemon()
    landed, landed_sha = _session(live, root, ATTEMPT_SESSION, ATTEMPT_TOKENS)
    assert (
        live.post(
            "/v1/correlations",
            {
                "capture_id": landed,
                "external_system": "test",
                "task_id": "T-landed",
                "attempt": LANDED_ATTEMPT,
            },
        )
        == 200
    )
    live.drain()
    live.store.rebuild(landed)
    recorder, recorded_sha = _session(live, root, RECORDER_SESSION, RECORDER_TOKENS)
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)
    live.store.flush()

    built = _built(repo_id, clock="change")

    assert sorted(str(meta.row_key) for meta in built.row_meta) == sorted(
        [landed_sha, recorded_sha]
    )
    assert _cell(built, landed_sha, "attempts_to_land") == LANDED_ATTEMPT
    assert _cell(built, landed_sha, "fresh_input_tokens_total") == ATTEMPT_TOKENS
    # The row nothing numbered. Its tokens are its own session's, and the two unknowns
    # stay unknown: nothing says how many sessions tried this change before, and a
    # session nothing launched has no environment fingerprint to differ from.
    assert _cell(built, recorded_sha, "fresh_input_tokens_total") == RECORDER_TOKENS
    assert _cell(built, recorded_sha, "attempts_to_land") is None
    assert _cell(built, recorded_sha, "env_changed") is None
    assert built.cohort["process_from_recording_captures"] == [recorded_sha]
    assert series.check(_store(), built) == []
    # The cohort says what the recording capture did instead of calling it something
    # this clock knows to be false.
    dropped = {one["key"]: one["reason"] for one in built.cohort["dropped"]}
    assert dropped[recorder] == (
        f"recorded {recorded_sha} at provider_reported:"
        " process columns folded, attempts_to_land unknown"
    )
    assert landed not in dropped
