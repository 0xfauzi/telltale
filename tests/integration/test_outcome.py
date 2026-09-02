"""`telltale outcome`: the only CLI write path besides `run` and `import`.

Design 6.12 gives the attempt clock three columns that no provider surface can fill:
verification_passed, review_fail_count and accepted. They come from external.outcome
records, and until this command only the experiment runner could post one, so the
build's own merge protocol had no way to record a decision it had taken.

The lineage under test is the six real `telltale run` attempts test_series_lineage.py
builds, imported rather than rebuilt: the two files are asking about one system and a
second scaffolding would be a second definition of what an attempt is.

Both doors are exercised. `--receiver` posts to a receiver somebody else is running,
and without it the record is appended through the store of $TELLTALE_HOME and the
capture is rebuilt. A test that used one door would leave the other untested and any
difference between them invisible.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import pytest
from test_series_lineage import (
    _attempt,
    _built,
    _column,
    _lineage,
    _repository,
    _run,
    _spec,
    _store,
)

from telltale import config, repo, series
from telltale.receiver import Receiver
from telltale.store import Store

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


@pytest.mark.usefixtures("telltale_home")
def test_an_outcome_nobody_carries_is_refused_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """Absence is not zero: an unknown (task_id, attempt) is exit 2 and no row.

    The refusal names what it searched, so an operator who mistyped a task id can see
    the six it did find rather than being told only that something was wrong.
    """
    root, repo_id = _lineage(tmp_path / "repo")
    before = len(_outcomes(repo_id))

    done = _run(
        root, "outcome", "--kind", "merge_decision", "--status", "merged",
        "--task-id", "T-gamma", "--attempt", "1",
    )  # fmt: skip

    printed = done.stdout.decode()
    assert done.returncode == 2, printed + done.stderr.decode()
    assert "no capture of this repository carries T-gamma attempt 1" in printed
    assert "T-alpha/1" in printed
    assert "T-beta/3" in printed
    assert len(_outcomes(repo_id)) == before


@pytest.mark.usefixtures("telltale_home")
def test_an_attempt_two_captures_carry_is_refused_naming_both(tmp_path: Path) -> None:
    """Duplicate is not one: two captures of one (task_id, attempt) refuse the write.

    Both captures are real `telltale run`s given the same `--task-id` and `--attempt`,
    which is the mistake an orchestrator makes when it retries a dispatch. There is no
    right answer to "which capture is attempt 1", so there is no answer at all, and
    both capture ids are printed because a refusal nobody can locate is not actionable.
    """
    root = _repository(tmp_path / "repo")
    _attempt(root, "T-dup", 1, seed=1)
    _attempt(root, "T-dup", 1, seed=2)
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)

    done = _run(
        root, "outcome", "--kind", "mechanical_verification", "--status", "pass",
        "--task-id", "T-dup", "--attempt", "1",
    )  # fmt: skip

    printed = done.stdout.decode()
    assert done.returncode == 2, printed + done.stderr.decode()
    assert "2 captures carry T-dup attempt 1" in printed
    ids = [str(row["capture_id"]) for row in _store().captures()]
    assert sum(one in printed for one in ids) == 2, printed


def _outcomes(repo_id: str) -> list[dict[str, Any]]:
    store = _store()
    return [
        dict(row["payload"])
        for capture in store.captures()
        if capture["repo_id"] == repo_id
        for row in store.observations(str(capture["capture_id"]))
        if row["observation_type"] == "external.outcome"
    ]


@pytest.mark.usefixtures("telltale_home")
def test_outcomes_fill_three_columns_where_posted_and_none_where_not(
    tmp_path: Path,
) -> None:
    """The whole point of `telltale outcome`, through both of its doors.

    Four outcomes over six attempts: two through a receiver this test runs, two through
    the store. What is asserted is the pair of statements design invariant 5 keeps
    apart. Where an outcome was posted the column carries the number it stated; where
    none was posted the cell is None and the coverage says `partial`, which is the
    difference between "this attempt was not accepted" and "nobody has said".
    """
    root, repo_id = _lineage(tmp_path / "repo")
    live = _Live()
    try:
        _outcome(root, "mechanical_verification", "pass", "T-alpha", 1, live.port)
        _outcome(root, "merge_decision", "merged", "T-alpha", 1, live.port)
    finally:
        live.stop()
    _outcome(root, "mechanical_verification", "fail", "T-beta", 2, None)
    _outcome(root, "adversarial_review", "rejected", "T-beta", 2, None)
    for capture in _capture_ids(repo_id):
        assert _run(root, "rebuild", capture).returncode == 0
    built = _built(repo_id)

    assert _column(built, "verification_passed") == [1, None, None, 0, None, None]
    assert _column(built, "accepted") == [1, None, None, None, None, None]
    assert _column(built, "review_fail_count") == [None, None, None, 1, None, None]
    for name in ("verification_passed", "accepted", "review_fail_count"):
        assert _spec(built, name).coverage == "partial", name
    assert series.check(_store(), built) == []


class _Live:
    """A receiver over the store of $TELLTALE_HOME, and the port it bound.

    Started here rather than through the `receiver` fixture because these tests run
    `telltale` as a CHILD PROCESS against $TELLTALE_HOME's own database, and the
    fixture's receiver serves a store the fixture opened. One writer per file.
    """

    def __init__(self) -> None:
        self.store = Store(config.db_path()).open()
        self.receiver = Receiver(self.store, level=1, provider="claude")
        self.port = self.receiver.start()

    def stop(self) -> None:
        self.receiver.stop()
        self.store.flush()
        self.store.close()


def _outcome(
    root: Path, kind: str, status: str, task_id: str, attempt: int, port: int | None
) -> None:
    where = ["--receiver", f"http://127.0.0.1:{port}"] if port else []
    done = _run(
        root, "outcome", "--kind", kind, "--status", status,
        "--task-id", task_id, "--attempt", str(attempt), *where,
    )  # fmt: skip
    assert done.returncode == 0, done.stdout.decode() + done.stderr.decode()
    if port:
        # The receiver answers 200 whatever happened (spec 5.2), so the record is
        # waited for rather than assumed: the writer thread is in another object.
        _settle(port)


def _settle(port: int) -> None:
    from telltale.receiver import _drain

    _drain(port)
    time.sleep(0.05)


def _capture_ids(repo_id: str) -> list[str]:
    return [
        str(row["capture_id"])
        for row in _store().captures()
        if row["repo_id"] == repo_id
    ]


@pytest.mark.usefixtures("telltale_home")
def test_the_receiver_and_the_store_write_the_same_observation(tmp_path: Path) -> None:
    """Two doors, one shape. The payloads differ only in the fields that must differ.

    If they did not, one of the two paths would be writing a record the allowlist and
    the reducer treat differently, and the attempt clock would report a column that
    depends on which door an operator happened to use.
    """
    root, repo_id = _lineage(tmp_path / "repo")
    live = _Live()
    try:
        _outcome(root, "merge_decision", "merged", "T-alpha", 1, live.port)
    finally:
        live.stop()
    _outcome(root, "merge_decision", "merged", "T-beta", 1, None)

    posted = sorted(_outcomes(repo_id), key=lambda one: str(one["component_id"]))
    assert len(posted) == 2, posted
    through_receiver, through_store = posted
    assert through_receiver["component_id"] == "T-alpha"
    assert through_store["component_id"] == "T-beta"
    for one in posted:
        assert one["kind"] == "merge_decision"
        assert one["status"] == "merged"
        assert one["attempt"] == 1
        assert one["timestamp"].endswith("Z")
    differing = {
        name
        for name in set(through_receiver) | set(through_store)
        if through_receiver.get(name) != through_store.get(name)
    }
    assert differing == {"component_id", "timestamp"}, differing


@pytest.mark.usefixtures("telltale_home")
def test_the_status_words_a_table_does_not_carry_stay_unknown(tmp_path: Path) -> None:
    """A status nobody has a rule for is None, not a failure and not a pass.

    `--status` is free text on purpose: an orchestrator's vocabulary is its own. The
    cost of that is a word the table has never seen, and the answer to it is the same
    answer as to a missing surface, because in both cases nothing here knows.
    """
    root, repo_id = _lineage(tmp_path / "repo")
    _outcome(root, "merge_decision", "deferred-to-thursday", "T-alpha", 1, None)
    for capture in _capture_ids(repo_id):
        assert _run(root, "rebuild", capture).returncode == 0
    built = _built(repo_id)

    stored = [one for one in _outcomes(repo_id) if one["kind"] == "merge_decision"]
    assert [one["status"] for one in stored] == ["deferred-to-thursday"]
    assert _column(built, "accepted") == [None] * 6
    assert _spec(built, "accepted").coverage == "unavailable"
