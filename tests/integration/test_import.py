"""Backfill, against synthetic transcripts and rollouts on a real disk.

The inputs are hand-written files under fixtures/sources/, not captured ones: an import
reads the owner's own sessions, and the owner's own sessions are the one thing that
must never be copied into this repository. Every shape in them was measured first
against ~/.claude/projects and ~/.codex/sessions (W2-T2, docs/log/W2-T2.md, and
claude.DRIFT), and the probe strings E01 planted in its S3 fixture are left in place,
because a fixture without the probe cannot show that the probe was removed.

The two machine paths are `<repo>` and `<home>`, the same placeholders the replay
fixtures use, and `materialise` substitutes this test's temporary directories back in.
The repository is a real `git init`, because the sanitizer's question is "inside the
repository or outside it" and a directory that is not a working tree has no answer.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from conftest import HOME_PLACEHOLDER, REPO_PLACEHOLDER

from telltale import importer, measures

if TYPE_CHECKING:
    from collections.abc import Sequence

    from telltale.store import Store

_REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = {
    "claude-transcripts": _REPO_ROOT
    / "fixtures"
    / "sources"
    / "claude"
    / "2.1.257"
    / "transcript",
    "codex-rollouts": _REPO_ROOT
    / "fixtures"
    / "sources"
    / "codex"
    / "0.150.1"
    / "rollout-import",
}

# What the transcript fixture is, counted from the files rather than from the parser.
TRANSCRIPT_FILES = 3
TRANSCRIPT_SESSIONS = 2  # the main transcript, and the subagent's own capture
TRANSCRIPT_UNREADABLE = 1  # vercel-plugin/skill-injections.jsonl names no session
MAIN_REQUESTS = 12  # assistant lines carrying message.usage in the main transcript
SIDECHAIN_REQUESTS = 6  # the same count in the subagent file
POST_COMPACTION_TOKENS = 13317
PRE_COMPACTION_TOKENS = 410334
ROLLOUT_CONTEXT_WINDOW = 258400
# The dates the two fixtures carry, and a day after each, for --since.
FIXTURE_DAY = "2026-09-02"
DAY_AFTER = "2026-09-03"


def materialise(kind: str, tmp_path: Path) -> tuple[Path, Path, Path]:
    """Copy one fixture tree into tmp_path with the two machine paths substituted.

    Returns (root the importer reads, the repository the session ran in, the home).
    The repository is initialised as a git working tree: `repo_id` and every
    repo-relative path in the store depend on git answering about that directory.
    """
    root = tmp_path / f"in-{kind}"
    repo_root = tmp_path / "repo"
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    _git_repo(repo_root)
    for source in sorted(FIXTURES[kind].rglob("*.jsonl")):
        target = root / source.relative_to(FIXTURES[kind])
        target.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding="utf-8")
        text = text.replace(REPO_PLACEHOLDER, str(repo_root))
        text = text.replace(HOME_PLACEHOLDER, str(home))
        target.write_text(text, encoding="utf-8")
    return root, repo_root, home


def _git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--quiet"], cwd=path, check=True, capture_output=True
    )


def _sources(kind: str, root: Path, **kwargs: Any) -> list[importer.Source]:
    return list(importer.scan(root, kind, **kwargs))


def _import(store: Store, kind: str, root: Path, **kwargs: Any) -> dict[str, Any]:
    return importer.import_files(store, _sources(kind, root, **kwargs), 1)


def _summary(store: Store, capture_id: str) -> dict[str, Any]:
    return measures.summary(store, capture_id)


def _captures(store: Store) -> list[str]:
    return [str(row["capture_id"]) for row in store.captures()]


def _tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def _telltale() -> str:
    found = shutil.which("telltale")
    assert found is not None, "no `telltale` on PATH: run `uv sync` first"
    return found


def _cli(*args: str) -> str:
    done = subprocess.run(
        [_telltale(), *args], capture_output=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr.decode()
    return done.stdout.decode()


def _rows(stdout: str) -> list[list[str]]:
    """A printed table as a list of cell lists, header and rule dropped."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    return [line.split() for line in lines[2:]]


@pytest.mark.integration
def test_the_dry_run_counts_the_files_and_writes_nothing(
    telltale_home: Path, tmp_path: Path
) -> None:
    """The half of the import the owner sees before deciding. It creates no database.

    The listing of $TELLTALE_HOME before and after is the assertion, not a check that
    telltale.db is absent: the owner decision of 2026-09-01 is that the counts are
    reported first, and a command that made ANY file to report them would have written
    before it was allowed to.
    """
    root, _repo, _home = materialise("claude-transcripts", tmp_path)
    before = _tree(telltale_home)

    counts = importer.dry_run(root, "claude-transcripts")
    printed = _cli("import", "claude-transcripts", "--root", str(root), "--dry-run")

    assert _tree(telltale_home) == before, "the dry run wrote into $TELLTALE_HOME"
    assert counts["files"] == TRANSCRIPT_FILES
    assert counts["sessions"] == TRANSCRIPT_SESSIONS
    assert len(counts["unreadable"]) == TRANSCRIPT_UNREADABLE
    assert counts["lines"] == sum(
        len([line for line in path.read_text().splitlines() if line.strip()])
        for path in sorted(root.rglob("*.jsonl"))
    )
    assert counts["first_ts"] is not None
    assert str(counts["first_ts"]).startswith(FIXTURE_DAY)
    # No database, so the question "is this already imported" has no answer here, and
    # the count is None rather than 0.
    assert counts["already_imported"] is None
    assert "dry run: nothing was written" in printed
    # The printed group is a HASH of the project directory, never the directory itself:
    # a transcript slug is the project's absolute path with the separators changed. The
    # root the owner typed is echoed, because the owner typed it.
    slug = next(path.name for path in root.iterdir() if path.is_dir())
    assert "proj_" in printed
    assert slug not in printed


@pytest.mark.integration
def test_an_imported_transcript_carries_its_usage_and_its_compaction(
    store: Store, tmp_path: Path
) -> None:
    """One capture per transcript file, with the numbers the session really had.

    post_compaction_tokens is the reason backfill is worth having at all: the
    transcript is the only Claude surface that carries it after the fact, so its
    coverage here is `observed` and not `partial`.
    """
    root, _repo, _home = materialise("claude-transcripts", tmp_path)

    result = _import(store, "claude-transcripts", root)
    store.flush()
    main = _main_capture(store)
    summary = _summary(store, main)

    assert result["captures"] == TRANSCRIPT_SESSIONS
    assert result["unreadable"] == TRANSCRIPT_UNREADABLE
    assert summary["usage"]["model_requests"] == MAIN_REQUESTS
    assert summary["usage"]["output_tokens"] > 0
    assert summary["coverage"]["compaction"] == "observed"
    assert summary["context"]["compactions"] == 1
    assert summary["context"]["post_compaction_tokens"] == POST_COMPACTION_TOKENS
    assert summary["context"]["pre_compaction_tokens"] == PRE_COMPACTION_TOKENS
    # Exit codes are derived from the text of a failed result, exactly as on the stream.
    assert summary["verification"]["failed_test_runs"] == 1


@pytest.mark.integration
def test_a_subagent_transcript_is_its_own_capture(store: Store, tmp_path: Path) -> None:
    """The sidechain file's tokens are never inside the main thread's total.

    Measured on the owner's own files: a subagent transcript carries the PARENT
    session's sessionId on every line and shares no uuid with the parent file. Two
    captures keep the two token totals apart, which is spec 13.6, and the link between
    them survives as the provider_session_id both carry.
    """
    root, _repo, _home = materialise("claude-transcripts", tmp_path)

    _import(store, "claude-transcripts", root)
    store.flush()
    main, side = _main_capture(store), _sidechain_capture(store)

    assert main != side
    assert _summary(store, main)["usage"]["model_requests"] == MAIN_REQUESTS
    assert _summary(store, side)["usage"]["model_requests"] == SIDECHAIN_REQUESTS
    assert (
        _summary(store, main)["provider_session_id"]
        == (_summary(store, side)["provider_session_id"])
    )
    # Every line of the subagent file is marked, and no line of the parent's is. The
    # rows read are the ones carrying usage: a tool_use block becomes an observation of
    # the same type holding the call's own scalars and nothing about the message.
    assert _sidechain_flags(store, side) == [True] * SIDECHAIN_REQUESTS
    assert _sidechain_flags(store, main) == [False] * MAIN_REQUESTS


@pytest.mark.integration
def test_a_second_import_of_the_same_files_creates_nothing(
    store: Store, tmp_path: Path
) -> None:
    """The capture id is a hash of the session, so a repeat is a skip and not a double.

    Observations are append-only (design 6.5): an import that ran twice without this
    would double every count in the capture, which is the failure that looks most like
    data.
    """
    root, _repo, _home = materialise("claude-transcripts", tmp_path)

    first = _import(store, "claude-transcripts", root)
    again = _import(store, "claude-transcripts", root)
    store.flush()

    assert first["captures"] == TRANSCRIPT_SESSIONS
    assert again["captures"] == 0
    assert again["skipped"] == TRANSCRIPT_SESSIONS
    assert again["observations"] == 0
    assert len(_captures(store)) == TRANSCRIPT_SESSIONS
    assert _summary(store, _main_capture(store))["usage"]["model_requests"] == (
        MAIN_REQUESTS
    )


@pytest.mark.integration
def test_since_compares_the_session_clock_and_not_the_files_mtime(
    tmp_path: Path,
) -> None:
    """A file touched today whose session ran in 2026-09-02 is excluded by 2026-09-03.

    The mtime is when the owner's disk was written, which for a resumed session is
    long after the session, and for a fixture copied by this test is now. So `--since`
    reads the first provider timestamp INSIDE the file.
    """
    root, _repo, _home = materialise("claude-transcripts", tmp_path)
    fresh = [path.stat().st_mtime for path in root.rglob("*.jsonl")]

    kept = _sources("claude-transcripts", root, since=FIXTURE_DAY)
    dropped = _sources("claude-transcripts", root, since=DAY_AFTER)

    assert min(fresh) > 0
    assert [source.readable for source in kept].count(True) == TRANSCRIPT_SESSIONS
    assert dropped == []


@pytest.mark.integration
def test_project_selects_one_directory_and_nothing_else(tmp_path: Path) -> None:
    """`--project` is the directory NAME, which is the only form the owner has."""
    root, _repo, _home = materialise("claude-transcripts", tmp_path)
    slug = next(path.name for path in root.iterdir() if path.is_dir())

    mine = _sources("claude-transcripts", root, project=slug)
    other = _sources("claude-transcripts", root, project="-no-such-project")

    assert len(mine) == TRANSCRIPT_FILES
    assert other == []


@pytest.mark.integration
def test_a_file_that_names_no_session_is_one_diagnostic_and_stops_nothing(
    store: Store, tmp_path: Path
) -> None:
    """115 of the owner's 1806 files are not sessions. They are counted, not imported.

    A truncated line inside a file that IS a session is the other half: one
    parse_failure row, and the 33 lines around it are still imported.
    """
    root, _repo, _home = materialise("claude-transcripts", tmp_path)

    result = _import(store, "claude-transcripts", root)
    store.flush()
    kinds = [str(row["kind"]) for row in store.diagnostics()]
    details = [str(row["detail"]) for row in store.diagnostics()]

    assert result["captures"] == TRANSCRIPT_SESSIONS
    assert result["unreadable"] == TRANSCRIPT_UNREADABLE
    assert kinds.count("parse_failure") == 2, details
    assert any("no session id in any line" in detail for detail in details)
    assert any("not a JSON object" in detail for detail in details)
    # The line kinds this parser does not read are counted rather than stored, and the
    # fields it has never seen are named rather than dropped in silence.
    assert any("claude.transcript:queue-operation x1" in detail for detail in details)
    assert any("claude.transcript.user.user_email" in detail for detail in details)


@pytest.mark.integration
def test_an_imported_rollout_carries_the_context_window_and_its_turn_usage(
    store: Store, tmp_path: Path
) -> None:
    """The Codex half, through the parser E02 and W1-T3 already wrote.

    Cached tokens are on the TURN, not on a request: W1-T3 measured that a Codex turn
    is not a model request and that `codex.rollout` carries usage per turn only, so
    `show` reports request usage as partial and the numbers live on the turn rows the
    timeline prints.
    """
    root, _repo, _home = materialise("codex-rollouts", tmp_path)

    result = _import(store, "codex-rollouts", root)
    store.flush()
    capture = _captures(store)[0]
    summary = _summary(store, capture)
    turns = [row for row in store.activities(capture) if row["activity_type"] == "turn"]
    cached = [
        row["fields"]["cache_read_tokens"]
        for row in turns
        if "cache_read_tokens" in row["fields"]
    ]

    assert result["captures"] == 1
    assert summary["coverage"]["context_window"] == "observed"
    assert summary["context"]["denominator_source"] == (
        "codex.rollout.event_msg.task_started.model_context_window"
    )
    assert cached == [11000, 23800]
    assert _window(store, capture) == ROLLOUT_CONTEXT_WINDOW


@pytest.mark.integration
def test_two_files_keying_on_one_capture_are_a_conflict_and_not_a_merge(
    store: Store, tmp_path: Path
) -> None:
    """Duplicate is not one. The second file is refused, loudly, rather than appended.

    The case is real: before the file name entered the capture id, 50 of the owner's
    1452 Codex rollouts collided, because a resumed thread writes a new file that still
    names the original session. Appending would have doubled the first session's counts
    while looking like one session, and skipping would have lost the file in silence.
    """
    root, _repo, _home = materialise("claude-transcripts", tmp_path)
    original = next(root.rglob("subagents/*.jsonl"))
    twin = root / "other-session" / "subagents" / original.name
    twin.parent.mkdir(parents=True)
    twin.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")

    result = _import(store, "claude-transcripts", root)
    store.flush()
    conflicts = [
        str(row["detail"])
        for row in store.diagnostics()
        if str(row["kind"]) == "conflict"
    ]

    assert result["collisions"] == 1
    assert result["captures"] == TRANSCRIPT_SESSIONS
    assert len(conflicts) == 1
    assert "was not imported" in conflicts[0]
    # And the capture it collided with still holds one file's worth of requests.
    assert _summary(store, _sidechain_capture(store))["usage"]["model_requests"] == (
        SIDECHAIN_REQUESTS
    )


@pytest.mark.integration
def test_the_walk_reads_no_file_a_symlink_points_at(tmp_path: Path) -> None:
    """`--root` is a boundary. A link inside it may name anything on the machine."""
    root, _repo, _home = materialise("claude-transcripts", tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "elsewhere.jsonl").write_text('{"type": "mode"}\n', encoding="utf-8")
    (root / "linked.jsonl").symlink_to(outside / "elsewhere.jsonl")
    (root / "linked-dir").symlink_to(outside, target_is_directory=True)

    found = [source.path.name for source in _sources("claude-transcripts", root)]

    assert "elsewhere.jsonl" not in found
    assert "linked.jsonl" not in found
    assert len(found) == TRANSCRIPT_FILES


@pytest.mark.integration
def test_the_sessions_table_says_which_captures_are_backfill(
    telltale_home: Path, tmp_path: Path
) -> None:
    """`telltale sessions` through the installed console script, end to end.

    The BACKFILL column reads argv_shape, which the importer writes as the word
    "backfill" because there was no launch: nothing else can put that word in a
    capture_started payload.
    """
    root, _repo, _home = materialise("claude-transcripts", tmp_path)

    printed = _cli("import", "claude-transcripts", "--root", str(root))
    listed = _cli("sessions")
    rows = _rows(listed)

    assert telltale_home.exists()
    assert f"imported {TRANSCRIPT_SESSIONS} capture(s)" in printed
    assert "0 colliding" in printed
    assert len(rows) == TRANSCRIPT_SESSIONS
    assert {row[0][:4] for row in rows} == {"imp_"}
    assert [row[-1] for row in rows] == ["yes"] * TRANSCRIPT_SESSIONS
    # The started column is the session's own clock, not the moment of the import.
    assert all(FIXTURE_DAY in row for row in listed.splitlines()[2:] if row.strip())


def _sidechain_flags(store: Store, capture_id: str) -> list[bool]:
    """`is_sidechain` on every assistant observation that carries usage."""
    return [
        bool(row["payload"].get("is_sidechain"))
        for row in store.observations(capture_id)
        if row["observation_type"] == "claude.transcript.assistant"
        and "input_tokens" in row["payload"]
    ]


def _main_capture(store: Store) -> str:
    return _capture_with(store, sidechain=False)


def _sidechain_capture(store: Store) -> str:
    return _capture_with(store, sidechain=True)


def _capture_with(store: Store, sidechain: bool) -> str:
    """The capture whose assistant lines are (or are not) the subagent's.

    Found by reading the stored rows rather than by recomputing the id, so the test
    does not restate the id rule it is checking.
    """
    for capture in _captures(store):
        flags = _sidechain_flags(store, capture)
        if flags and flags[0] is sidechain:
            return capture
    raise AssertionError(f"no capture with is_sidechain {sidechain}")


def _window(store: Store, capture_id: str) -> Any:
    rows: Sequence[dict[str, Any]] = store.activities(capture_id)
    for row in rows:
        if "context_window" in row["fields"]:
            return row["fields"]["context_window"]
    raise AssertionError(f"{capture_id} has no context window")
