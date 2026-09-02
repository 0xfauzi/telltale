"""Commits made during a capture, and the rung each one reaches. Spec 12.3.

Every test here makes a REAL commit in a real repository the test creates, through a
real `telltale run`, and reads the answer out of the database the launcher wrote. There
is no fake git and no stubbed store: the whole question is whether two values Telltale
recorded about a working tree can be matched against a commit git made, and a test that
supplied either value would be testing itself.

The child is `bash` or `python`. What Claude Code contributes to linkage is one hook
body and one stream line, and both are POSTed here by a child process exactly as the
agent would POST them, which is the same trade test_launcher.py makes: a real agent
would spend tokens to tell us nothing about the ladder.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from telltale import repo, repo_link
from telltale.launch import PER_FILE_MAX

if TYPE_CHECKING:
    from collections.abc import Sequence

# The child of the two trigger tests outlives the 2 s snapshot debounce, so the
# debounced snapshot is taken while the capture is still running rather than by the
# flush at capture end. Anything under SNAPSHOT_DEBOUNCE_S would test the flush.
CHILD_LIFETIME_S = 3.0

# One second of separation, so "before the capture" is unambiguous in git's units. A
# committer date is whole seconds (repo_link._confidence), so a commit made 40 ms before
# a capture starts is dated in the same second and is INSIDE the window by design.
BEFORE_GAP_S = 1.1

# What the two children POST and print. Both are the shapes E01 recorded, cut to the
# fields the linkage path reads.
COMMIT_COMMAND = "echo x >> f && git add f && git commit -q -m one"

# What the per_file test commits. The first touches two subsystems and one of them is a
# test file, so the same two commits exercise the change clock's path columns in
# test_series_lineage.py. The second is one file past launch.PER_FILE_MAX, which is what
# makes the cut happen at the count rather than at the 8 KB bound: measured, the whole
# payload with 101 entries of these short names is 5538 bytes, so the size rule alone
# would not have fired and the flag under test is PER_FILE_MAX's.
TWO_FILE_COMMIT = (
    "mkdir -p src tests && echo x > src/a.py && echo x > tests/test_a.py"
    " && git add -A && git commit -q -m two"
)
MANY_FILES = PER_FILE_MAX + 1
MANY_FILE_COMMIT = (
    f"mkdir -p wide && for i in $(seq 1 {MANY_FILES}); do echo x > wide/f$i.txt; done"
    " && git add -A && git commit -q -m many"
)

# What `fake_agent.py --seed 1 --model sonnet` reports, measured by running it. The
# W2-T6 brief expected 2; the file fabricates one assistant message per tool call and
# that seed makes five of them.
FAKE_AGENT_REQUESTS = 5

HOOK_CHILD = """
import json, subprocess, sys, time, urllib.request
settings = json.loads(sys.argv[sys.argv.index("--settings") + 1])
url = settings["hooks"]["PostToolUse"][0]["hooks"][0]["url"]
command = sys.argv[1]
subprocess.run(["bash", "-c", command], check=True)
body = json.dumps({
    "hook_event_name": "PostToolUse",
    "session_id": "00000000-0000-4000-8000-0000000c0mm1",
    "tool_name": "Bash",
    "tool_input": {"command": command},
}).encode("utf-8")
request = urllib.request.Request(
    url, data=body, headers={"Content-Type": "application/json"}
)
urllib.request.urlopen(request, timeout=10).read()
time.sleep(float(sys.argv[2]))
"""

STREAM_CHILD = """
import json, subprocess, sys, time
subprocess.run(["bash", "-c", sys.argv[1]], check=True)
sys.stdout.write(json.dumps({
    "type": "system",
    "subtype": "vcs_state_changed",
    "uuid": "0197f2d0-0000-7000-8000-0000000c0mm1",
    "session_id": "00000000-0000-4000-8000-0000000c0mm1",
    "kind": "commit",
    "branch": "main",
}) + "\\n")
sys.stdout.flush()
time.sleep(float(sys.argv[2]))
"""


def _telltale() -> str:
    executable = shutil.which("telltale")
    assert executable is not None, "no `telltale` on PATH: run `uv sync` first"
    return executable


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, timeout=30, check=True
    )
    return done.stdout.decode().strip()


def _repository(root: Path) -> Path:
    """A repository with one commit, and an identity that is not the operator's.

    `commit.gpgsign=false` and the two names are set on this repository alone: a global
    signing key would make every commit here wait for a passphrase, and Telltale may
    not edit an operator's configuration to find out (AGENTS.md invariant 7).
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


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    # check=False: a launcher that changed the child's exit code is a failure this file
    # wants to report as an assertion rather than as an exception.
    return subprocess.run(
        [_telltale(), *args], cwd=cwd, capture_output=True, timeout=120, check=False
    )


def _observations(capture_id: str | None = None) -> list[dict[str, Any]]:
    from telltale import config
    from telltale.store import Store

    store = Store(config.db_path())
    ids = (
        [capture_id]
        if capture_id is not None
        else [str(row["capture_id"]) for row in store.captures()]
    )
    return [row for one in ids for row in store.observations(one)]


def _payloads(obs_type: str, capture_id: str | None = None) -> list[dict[str, Any]]:
    return [
        dict(row["payload"])
        for row in _observations(capture_id)
        if row["observation_type"] == obs_type
    ]


def _commits(capture_id: str | None = None) -> list[dict[str, Any]]:
    return _payloads("telltale.repo.commit", capture_id)


def _written(text: str, tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _child(script: Path, seconds: float, *extra: str) -> Sequence[str]:
    return (
        "run",
        "--provider",
        "claude",
        "--",
        "python",
        str(script),
        COMMIT_COMMAND,
        str(seconds),
        *extra,
    )


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_a_commit_the_child_made_links_at_the_tree_it_left(tmp_path: Path) -> None:
    """One commit, made by the child inside the capture, linked with no provider help.

    The rung is tree_match_after, and asserting which one it is IS the test. A `bash`
    child posts nothing to the receiver, so no file mutation and no command ever reaches
    Telltale and the only snapshot of this capture is the one taken at capture end.
    That snapshot's head IS the commit and its diff is empty, which is the working tree
    at that commit; spec 12.3 calls evidence gathered at the boundary of a capture
    "matching tree shortly after", so the rung is tree_match_after rather than during.
    A capture that DOES report its commands reaches the stronger rung, which is the
    next test.

    Before W2-T6 this commit reached no rung at all and the capture recorded no
    telltale.repo.commit: the wave 1 gate measured exactly this on a real session.
    """
    root = _repository(tmp_path / "repo")

    completed = _run(root, "run", "--", "bash", "-c", COMMIT_COMMAND)

    assert completed.returncode == 0, completed.stderr.decode()
    head = _git(root, "rev-parse", "HEAD")
    linked = _commits()
    assert len(linked) == 1, linked
    assert linked[0]["sha"] == head
    assert linked[0]["link_confidence"] == "tree_match_after", linked[0]
    assert linked[0]["files_changed"] == 1, linked[0]
    triggers = [payload["trigger"] for payload in _payloads("telltale.repo.snapshot")]
    assert triggers == [repo_link.CAPTURE_END], triggers


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_a_commit_carries_its_paths_and_says_when_it_cut_them(tmp_path: Path) -> None:
    """The per_file list W3-T4 puts on a commit: what it holds, and where it stops.

    Two commits in one repository, each through its own `telltale run`. The first
    changes two files and the list is both of them, with the three keys design 6.3 now
    gives a commit and no patch_hash: repo.per_file measured that a fourth key takes
    this repository's own largest commit past the 8 KB payload bound. The second
    changes MANY_FILES, one more than the launcher's PER_FILE_MAX, so the list is a
    prefix, `per_file_truncated` says so, and files_changed still carries the true
    count. That last pair is the whole reason the flag exists: a reader must be able to
    tell a short list from a small change.

    The paths are asserted to be repo-relative, and the DATABASE is then searched for
    the absolute path of the temporary repository. Design 6.4 relativizes a PATH; this
    is the assertion that the commit payload went through that gate like every other.
    """
    root = _repository(tmp_path / "repo")

    assert _run(root, "run", "--", "bash", "-c", TWO_FILE_COMMIT).returncode == 0
    assert _run(root, "run", "--", "bash", "-c", MANY_FILE_COMMIT).returncode == 0

    linked = sorted(_commits(), key=lambda one: one["files_changed"])
    assert [one["files_changed"] for one in linked] == [2, MANY_FILES], linked
    small, large = linked
    assert small["per_file"] == [
        {"path": "src/a.py", "additions": 1, "deletions": 0},
        {"path": "tests/test_a.py", "additions": 1, "deletions": 0},
    ], small
    assert "per_file_truncated" not in small, small
    assert len(large["per_file"]) == PER_FILE_MAX, len(large["per_file"])
    assert large["per_file_truncated"] is True, large
    from telltale import config

    stored = config.db_path().read_bytes()
    assert stored.count(str(root).encode()) == 0, "an absolute path reached the store"
    assert stored.count(b'"path":"src/a.py"') > 0, "the relative path did not"


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_sessions_link_commits_counts_the_commit(tmp_path: Path) -> None:
    """`telltale sessions --link-commits` shows COMMITS 1 for the capture above.

    It links nothing further, and that is the point of the number beside the table: the
    capture already carries the commit, so this run has nothing to add. The column is
    read out of the capture's own observations either way (facts.py).
    """
    root = _repository(tmp_path / "repo")
    assert _run(root, "run", "--", "bash", "-c", COMMIT_COMMAND).returncode == 0

    completed = _run(root, "sessions", "--link-commits", "--limit", "8")

    assert completed.returncode == 0, completed.stderr.decode()
    printed = completed.stdout.decode()
    row = printed.splitlines()[2]
    # From the right: STARTED holds a space, so the columns before it cannot be counted
    # by splitting, and COMMITS and BACKFILL are the last two.
    commits = row.split()[-2]
    assert commits == "1", printed
    assert "linked 0 commits in this repository" in printed, printed


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_a_reported_commit_command_reaches_the_during_rung(tmp_path: Path) -> None:
    """A PostToolUse naming a `git commit` takes a snapshot while the child still runs.

    This is the rung the wave 1 gate could not reach. The child commits, POSTs the hook
    body Claude Code POSTs for a Bash call that has finished, and stays alive past the
    2 s debounce, so the snapshot is taken DURING the capture and carries the trigger
    that asked for it. The tree it photographs is the commit with nothing outstanding,
    so the commit links at tree_match_during.

    The command is matched in its NORMAL form (`echo x >> f && git add f && git commit
    -q -m one`), which is what the store holds; nothing here reads the raw command line.
    """
    root = _repository(tmp_path / "repo")
    script = _written(HOOK_CHILD, tmp_path, "hook-child.py")

    completed = _run(root, *_child(script, CHILD_LIFETIME_S))

    assert completed.returncode == 0, completed.stderr.decode()
    snapshots = _payloads("telltale.repo.snapshot")
    triggers = [payload["trigger"] for payload in snapshots]
    assert "commit_command" in triggers, triggers
    during = next(one for one in snapshots if one["trigger"] == "commit_command")
    assert during["head"] == _git(root, "rev-parse", "HEAD"), during
    assert during["files_changed"] == 0, during
    linked = _commits()
    assert len(linked) == 1, linked
    assert linked[0]["link_confidence"] == "tree_match_during", linked[0]


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_a_vcs_state_changed_message_takes_a_snapshot(tmp_path: Path) -> None:
    """The other trigger: Claude Code 2.1.258 announces a commit on the stream.

    The message names no sha, which is why it is a snapshot trigger and not a link: it
    says the repository moved, Telltale photographs the tree, and the ladder does the
    rest. Same rung as the hook path, from a different surface.
    """
    root = _repository(tmp_path / "repo")
    script = _written(STREAM_CHILD, tmp_path, "stream-child.py")

    completed = _run(
        root, *_child(script, CHILD_LIFETIME_S, "--output-format", "stream-json")
    )

    assert completed.returncode == 0, completed.stderr.decode()
    triggers = [payload["trigger"] for payload in _payloads("telltale.repo.snapshot")]
    assert "vcs_state_changed" in triggers, triggers
    linked = _commits()
    assert len(linked) == 1, linked
    assert linked[0]["link_confidence"] == "tree_match_during", linked[0]


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_a_commit_made_before_the_capture_does_not_link(tmp_path: Path) -> None:
    """The capture photographs that commit's tree, and still does not link it.

    This is the case the empty-diff rung has to refuse. A capture that starts and ends
    on a clean checkout takes a capture-end snapshot whose head IS the previous commit
    and whose diff is empty, which matches the tree exactly; the window is what refuses
    it. The gap is 1.1 s because git dates a commit in whole seconds.
    """
    root = _repository(tmp_path / "repo")
    (root / "f").write_text("edited\n", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "before")
    before = _git(root, "rev-parse", "HEAD")
    time.sleep(BEFORE_GAP_S)

    assert _run(root, "run", "--", "bash", "-c", "true").returncode == 0

    snapshots = _payloads("telltale.repo.snapshot")
    assert [one["head"] for one in snapshots] == [before], snapshots
    assert snapshots[0]["files_changed"] == 0, snapshots
    assert _commits() == []


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_a_commit_another_process_made_links_and_says_only_what_it_saw(
    tmp_path: Path,
) -> None:
    """A commit this capture did not make, in the worktree it is watching, links.

    Asserted because it is true and not because it is wanted. The child sleeps and
    changes nothing; a second process commits in the same worktree meanwhile; the
    capture-end snapshot photographs a tree that IS that commit, and the ladder reports
    tree_match_after. The claim it makes is exactly what was observed, "the tree at the
    end of this capture was this commit", and no field of it says the session did the
    work.

    Spec 12.4 is the missing half and it is not built here: `repo.overlaps` marks two
    CAPTURES on one worktree ambiguous, and an uncaptured `git commit` in another
    terminal is invisible to it. The heuristic rung is not what admits this commit;
    a tree match is, so asking for heuristic would change nothing.
    """
    root = _repository(tmp_path / "repo")
    launcher = subprocess.Popen(
        [_telltale(), "run", "--", "bash", "-c", f"sleep {CHILD_LIFETIME_S}"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(1.0)
    (root / "other").write_text("someone else\n", encoding="utf-8")
    _git(root, "add", "other")
    _git(root, "commit", "-q", "-m", "not this session")
    outside = _git(root, "rev-parse", "HEAD")
    assert launcher.wait(timeout=60) == 0, launcher.communicate()[1].decode()

    linked = _commits()
    assert [one["sha"] for one in linked] == [outside], linked
    assert linked[0]["link_confidence"] == "tree_match_after", linked[0]


@pytest.mark.integration
def test_a_provider_reported_commit_id_outranks_every_tree(
    replay: Any, settled: Any, store: Any, tmp_path: Path
) -> None:
    """Claude S8 on 2.1.257 reported a commit id, and it still links as reported.

    2.1.258 reports none anywhere (claude_drift.DRIFT), so this fixture is the only
    exercise the rung has. The sha is short and belongs to another repository, so it
    resolves to nothing here and comes back as spec 12.3 asks: the claim was made, it
    could not be checked, and every other field is unknown rather than invented.
    """
    replayed = replay("S8")
    settled(store)

    reported = repo_link.reported_commits(store, replayed.capture)
    assert reported, "S8 carries a git_commit_id on three surfaces"
    linked = repo_link.commits_since(
        _repository(tmp_path / "repo"),
        "2026-01-01T00:00:00+00:00",
        snapshots=[],
        provider_reported=reported,
        until_ts="2026-01-01T00:10:00+00:00",
    )

    assert [one["link_confidence"] for one in linked] == ["provider_reported"], linked
    assert linked[0]["sha"] == reported[0]
    assert linked[0]["parents"] is None, linked[0]


@pytest.mark.integration
def test_temporal_proximity_alone_is_not_a_rung(tmp_path: Path) -> None:
    """The rule spec 12.3 states in one sentence, as the only test that can state it.

    A commit inside the window with no snapshot to match reaches no rung and is not
    returned at all. It comes back only when a caller asks for the heuristic rung, and
    then it says heuristic, which is the ladder admitting what it is doing.
    """
    root = _repository(tmp_path / "repo")
    (root / "f").write_text("later\n", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "inside the window")
    window = repo.git_line(root, "show", "-s", "--format=%cI", "HEAD") or ""
    assert window, "the repository has a HEAD"

    silent = repo_link.commits_since(root, window, snapshots=[])
    asked = repo_link.commits_since(root, window, snapshots=[], heuristic=True)

    assert silent == []
    assert {one["link_confidence"] for one in asked} == {"heuristic"}, asked
    assert _git(root, "rev-parse", "HEAD") in [one["sha"] for one in asked], asked


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_the_capture_reduces_itself(tmp_path: Path) -> None:
    """`telltale show` answers straight after a capture, with no rebuild in between.

    Until W2-T6 the launcher ran no reducer, so a capture that had just been recorded
    printed a summary of nulls and read exactly like a capture where nothing happened.
    The fake agent reports two model requests; the number is fabricated by that file
    and fixed by --seed, and what is under test is that it survives to `show`.
    """
    root = _repository(tmp_path / "repo")
    agent = Path(__file__).with_name("fake_agent.py")

    completed = _run(
        root,
        "run",
        "--provider",
        "claude",
        "--",
        "python",
        str(agent),
        "--seed",
        "1",
        "--model",
        "sonnet",
        "--output-format",
        "stream-json",
    )

    assert completed.returncode == 0, completed.stderr.decode()
    capture = _payloads("telltale.capture_started")
    assert len(capture) == 1, capture
    capture_id = next(
        str(row["capture_id"])
        for row in _observations()
        if row["observation_type"] == "telltale.capture_started"
    )
    shown = _run(root, "show", capture_id)
    assert shown.returncode == 0, shown.stderr.decode()
    summary = json.loads(shown.stdout.decode())
    # 5 is what `--seed 1 --model sonnet` fabricates: three Read calls, one Edit and
    # one Bash, each its own assistant message. Fixed by the seed, and pinned here so
    # that a reduction which silently stopped counting is a failure rather than a null.
    assert summary["usage"]["model_requests"] == FAKE_AGENT_REQUESTS, summary["usage"]
    assert summary["session"]["runtime_version"] == "fake-agent", summary["session"]


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_a_capture_with_no_activities_is_refused_rather_than_reported(
    tmp_path: Path,
) -> None:
    """`show` on an unreduced capture refuses and exits 2 rather than printing nulls.

    Invariant 5 in the one place a reader meets it: a capture nothing has reduced and a
    capture where nothing happened must not print the same report. The activities are
    deleted here by hand, which is what a capture recorded before W2-T6 looks like.
    """
    from telltale import config
    from telltale.store import Store

    root = _repository(tmp_path / "repo")
    assert _run(root, "run", "--", "bash", "-c", "true").returncode == 0
    capture_id = next(
        str(row["capture_id"])
        for row in _observations()
        if row["observation_type"] == "telltale.capture_started"
    )
    store = Store(config.db_path()).open()
    try:
        assert store.activities(capture_id), "the launcher reduced this capture"
        store.replace_activities(capture_id, [])
    finally:
        store.close()

    refused = _run(root, "show", capture_id)

    assert refused.returncode == 2, refused.stdout.decode()
    assert f"capture {capture_id} has no activities" in refused.stdout.decode()
    assert "telltale rebuild" in refused.stdout.decode()


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_link_commits_reduces_the_capture_it_appends_to(tmp_path: Path) -> None:
    """A commit linked after the fact reaches the activities with no rebuild in between.

    The child leaves the tree dirty and commits nothing; the commit is made after the
    capture ended, so only `sessions --link-commits` can link it (tree_match_after).
    A reader of the change clock reads activities, not observations, so the linked
    commit must be reduced the way a capture reduces itself at its end (W2-T6). Found
    on the owner's store on 2026-09-02: four re-linked commits carried their per_file
    lists and the change clock still reported every path column unknown until the
    captures were rebuilt by hand.
    """
    root = _repository(tmp_path / "repo")
    assert _run(root, "run", "--", "bash", "-c", "echo x >> f").returncode == 0
    _git(root, "add", "f")
    _git(root, "commit", "-q", "-m", "after the capture")

    completed = _run(root, "sessions", "--link-commits", "--limit", "8")

    printed = completed.stdout.decode()
    assert completed.returncode == 0, completed.stderr.decode()
    assert "linked 1 commits in this repository" in printed, printed
    capture_id = printed.splitlines()[2].split()[0]
    timeline = _run(root, "timeline", capture_id).stdout.decode()
    assert "repo_commit" in timeline, timeline
