"""Six real attempts in a real repository, and the two clocks folded from them.

Nothing here writes an activity or an observation by hand. Every attempt is a real
`telltale run` of the fake agent in a real git repository, which is what makes the
attempt clock's row count a measurement: the launcher decides what a capture is, the
reducer decides what an activity is, and the compiler reads only what those two wrote.

The fake agent is the child for the reason test_commit_link.py gives: a real agent
would spend tokens to tell us nothing about a clock whose rows are one per capture.

`telltale outcome`, which is what fills the three outcome columns of the attempt clock,
is exercised in test_outcome.py, which reuses the lineage this file builds.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from telltale import config, repo, series
from telltale.launch import PER_FILE_MAX
from telltale.series_paths import NO_PATHS, PATH_COLUMNS
from telltale.store import Store

pytestmark = pytest.mark.integration

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"

# Two task ids and three attempts each: six captures, which is what the brief asks the
# attempt clock to be measured against. Six is also enough for the ORDER to be a claim:
# the two tasks interleave in time, so a row order that keyed on the task id rather
# than on the capture start would come out differently.
ATTEMPTS = (("T-alpha", 1), ("T-beta", 1), ("T-alpha", 2), ("T-beta", 2),
            ("T-alpha", 3), ("T-beta", 3))  # fmt: skip

# What `telltale run` gives the child. `--output-format stream-json` is what makes the
# launcher tee stdout into /v1/stream, which is the surface the fake agent writes on.
CHILD = ("python", str(FAKE_AGENT), "--output-format", "stream-json")


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
    """A repository with one commit and an identity that is not the operator's."""
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
    return subprocess.run(
        [_telltale(), *args], cwd=cwd, capture_output=True, timeout=180, check=False
    )


def _attempt(root: Path, task_id: str, attempt: int, seed: int) -> None:
    done = _run(
        root,
        "run",
        "--provider",
        "claude",
        "--task-id",
        task_id,
        "--attempt",
        str(attempt),
        "--experiment",
        "test",
        "--",
        *CHILD,
        "--seed",
        str(seed),
    )
    assert done.returncode == 0, done.stderr.decode()


def _lineage(root: Path) -> tuple[Path, str]:
    """Six attempts over two task ids, in the order ATTEMPTS lists them."""
    _repository(root)
    for index, (task_id, attempt) in enumerate(ATTEMPTS):
        _attempt(root, task_id, attempt, seed=index)
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)
    return root, repo_id


def _store() -> Store:
    return Store(config.db_path())


def _built(repo_id: str, clock: str = "attempt", policy: str = "exclude") -> Any:
    store = _store()
    return series.build(store, clock, repo_id, policy)


def _column(built: Any, name: str) -> list[float | None]:
    index = [spec.name for spec in built.columns].index(name)
    return [row[index] for row in built.rows]


def _spec(built: Any, name: str) -> Any:
    return next(spec for spec in built.columns if spec.name == name)


def _identities(built: Any, root: Path) -> list[tuple[str, int]]:
    """Each row's (task_id, attempt), read back out of the capture it names."""
    store = _store()
    out = []
    for meta in built.row_meta:
        payload = _started(store, str(meta.row_key))
        out.append((str(payload["task_id"]), int(payload["attempt"])))
    assert root.exists()
    return out


def _started(store: Store, capture_id: str) -> dict[str, Any]:
    for row in store.observations(capture_id):
        if row["observation_type"] == "telltale.capture_started":
            return dict(row["payload"])
    raise AssertionError(f"{capture_id} has no capture_started observation")


@pytest.mark.usefixtures("telltale_home")
def test_six_attempts_become_six_rows_in_start_order(tmp_path: Path) -> None:
    """The row count and the row ORDER, against the launcher's own captures.

    Six captures with a task id and an attempt, and the two tasks interleave, so the
    order below is the order they RAN and not the order either task numbers them in.
    Nothing else in the store is an attempt: the assertion is an equality.
    """
    root, repo_id = _lineage(tmp_path / "repo")
    built = _built(repo_id)

    assert len(built.rows) == len(ATTEMPTS)
    assert _identities(built, root) == list(ATTEMPTS)
    assert _column(built, "attempt_of_component") == [
        float(attempt) for _task, attempt in ATTEMPTS
    ]
    ends = [meta.row_end_ts for meta in built.row_meta]
    assert ends == sorted(ends), "row_end_ts must not go backwards"
    assert series.check(_store(), built) == []


@pytest.mark.usefixtures("telltale_home")
def test_the_cli_builds_the_attempt_clock_and_checks_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`series build --clock attempt --repo <id>`, then `series check <id>`."""
    from telltale import cli

    _root, repo_id = _lineage(tmp_path / "repo")
    assert cli.main(["series", "build", "--clock", "attempt", "--repo", repo_id]) == 0
    printed = capsys.readouterr().out
    series_id = printed.split()[0]

    assert "clock attempt  6 rows" in printed
    assert "attempt_of_component" in printed
    assert "verification_passed" in printed
    assert "low_confidence rows 0" in printed
    assert cli.main(["series", "check", series_id]) == 0
    assert capsys.readouterr().out.strip() == "ok"


@pytest.mark.usefixtures("telltale_home")
def test_a_repository_with_no_attempt_is_refused_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A capture with no --task-id is not an attempt, and the refusal says so."""
    from telltale import cli

    root = _repository(tmp_path / "repo")
    assert _run(root, "run", "--", *CHILD).returncode == 0
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)

    assert cli.main(["series", "build", "--clock", "attempt", "--repo", repo_id]) == 2
    printed = capsys.readouterr().out
    assert "no capture carries a task_id and an attempt" in printed


# -- the change clock -----------------------------------------------------------------

# What the two children run, and how long the hook child stays alive. Both are copied
# from test_commit_link.py, which measured them: the 2 s snapshot debounce is what
# separates a tree photographed DURING the capture from the one taken at capture end,
# and a child that exits sooner tests the flush instead of the trigger.
COMMIT_COMMAND = "echo x >> f && git add f && git commit -q -m one"
CHILD_LIFETIME_S = 3.0

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


def _during(root: Path, script: Path, task_id: str, attempt: int) -> None:
    """One attempt that commits and REPORTS the command, so the rung is during."""
    done = _run(
        root, "run", "--provider", "claude",
        "--task-id", task_id, "--attempt", str(attempt),
        "--", "python", str(script), COMMIT_COMMAND, str(CHILD_LIFETIME_S),
    )  # fmt: skip
    assert done.returncode == 0, done.stderr.decode()


def _after(
    root: Path, task_id: str, attempt: int, command: str = COMMIT_COMMAND
) -> None:
    """One attempt that commits and reports nothing, so the rung is after.

    The rung is not what the path-column tests below are about, and this child is the
    cheap one: it exits as soon as the commit is made, where the hook child has to
    outlive the 2 s snapshot debounce to reach the stronger rung.
    """
    done = _run(
        root, "run", "--provider", "claude",
        "--task-id", task_id, "--attempt", str(attempt),
        "--", "bash", "-c", command,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr.decode()


@pytest.mark.usefixtures("telltale_home")
def test_the_change_clock_flags_the_link_it_is_least_sure_of(tmp_path: Path) -> None:
    """Four real commits, three at tree_match_during and one at tree_match_after.

    The rung is not asserted from a constant: each commit is made by a real child in a
    real repository and the ladder of spec 12.3 decides, exactly as test_commit_link.py
    measures it. What this test adds is that the ladder reaches the SERIES: the three
    strong links are plain rows and the weak one carries `low_confidence` in its
    row_meta, which is the flag the backtester excludes by default.
    """
    root = _repository(tmp_path / "repo")
    script = tmp_path / "hook-child.py"
    script.write_text(HOOK_CHILD, encoding="utf-8")
    for attempt in (1, 2, 3):
        _during(root, script, "T-land", attempt)
    _after(root, "T-land", 4)
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)

    built = _built(repo_id, clock="change")

    rungs = sorted(one["link_confidence"] for one in _commits(repo_id))
    assert rungs == ["tree_match_after"] + ["tree_match_during"] * 3, rungs
    assert len(built.rows) == 4
    flagged = [
        meta.row_key for meta in built.row_meta if "low_confidence" in meta.flags
    ]
    assert len(flagged) == 1, [meta.flags for meta in built.row_meta]
    assert flagged[0] == _git(root, "rev-parse", "HEAD")
    assert series.check(_store(), built) == []


@pytest.mark.usefixtures("telltale_home")
def test_a_change_row_carries_the_commit_numbers_and_the_attempt_that_landed_it(
    tmp_path: Path,
) -> None:
    """Every column a stored repo.commit payload fills, from one real commit.

    The three path columns are the ones W3-T1 could not build and W3-T4 does. This
    commit appends to `f` at the repository root, so the values are computable by
    reading the commit: one subsystem (the root, which `.` names, because a change to
    pyproject.toml alone has touched one thing and 0 would say it touched nothing), no
    test file, and no manifest.
    """
    root = _repository(tmp_path / "repo")
    script = tmp_path / "hook-child.py"
    script.write_text(HOOK_CHILD, encoding="utf-8")
    _during(root, script, "T-land", 2)
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)

    built = _built(repo_id, clock="change")
    stored = _commits(repo_id)

    assert len(built.rows) == len(stored) == 1
    assert _column(built, "files_changed") == [stored[0]["files_changed"]]
    assert _column(built, "lines_added") == [stored[0]["additions"]]
    assert _column(built, "lines_removed") == [stored[0]["deletions"]]
    # The attempt ordinal the launcher was given, arriving through the commit's own
    # capture: 2, not 1, because attempts_to_land counts attempts and not commits.
    assert _column(built, "attempts_to_land") == [2]
    assert [one["path"] for one in stored[0]["per_file"]] == ["f"], stored[0]
    assert _column(built, "subsystems_touched") == [1]
    assert _column(built, "test_files_changed") == [0]
    assert _column(built, "dependency_delta") == [0]
    for name in PATH_COLUMNS:
        assert _spec(built, name).coverage == "observed", name
    assert built.cohort["unknown_columns"] == {}, built.cohort
    assert series.check(_store(), built) == []


# Three commits whose paths answer the three columns differently, and the answers, by
# hand from the paths themselves. `src/a.py` is one subsystem and nothing else;
# `tests/test_a.py` is one subsystem and a test file twice over (the directory and the
# `test_*.py` glob); the third touches the root and `src`, which is two subsystems, and
# pyproject.toml is a manifest, which is what dependency_delta is 1 for.
PATH_COMMITS = (
    ("mkdir -p src && echo x > src/a.py", (1.0, 0.0, 0.0)),
    ("mkdir -p tests && echo x > tests/test_a.py", (1.0, 1.0, 0.0)),
    ("echo 'x = 1' > pyproject.toml && echo y >> src/a.py", (2.0, 0.0, 1.0)),
)

# One more file than the launcher keeps, so the list stored is a prefix and carries
# per_file_truncated. See test_commit_link.py, which measures the same commit's payload.
WIDE_COMMIT = (
    f"mkdir -p wide && for i in $(seq 1 {PER_FILE_MAX + 1}); do echo x > wide/f$i.txt;"
    " done"
)


def _committing(command: str) -> str:
    return f"{command} && git add -A && git commit -q -m change"


@pytest.mark.usefixtures("telltale_home")
def test_the_three_path_columns_are_read_off_the_commits_own_paths(
    tmp_path: Path,
) -> None:
    """subsystems_touched, test_files_changed and dependency_delta, on real commits.

    Three captures, three commits, and the expected cells are in PATH_COMMITS beside
    the command that makes each one, computed by reading the paths rather than by
    running the code. The rows come back in committed_ts order, which is the order the
    captures ran.

    Every cell is known, so all three columns read `observed` and the cohort's
    `unknown_columns` is empty. That map is where a reader learns WHY a column is
    partial, and an empty one is the frame saying every linked commit carried its paths.
    """
    root = _repository(tmp_path / "repo")
    expected = {}
    for attempt, (command, cells) in enumerate(PATH_COMMITS, start=1):
        _after(root, "T-paths", attempt, _committing(command))
        expected[_git(root, "rev-parse", "HEAD")] = list(cells)
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)

    built = _built(repo_id, clock="change")

    assert len(built.rows) == len(PATH_COMMITS)
    assert _by_sha(built) == expected
    for name in PATH_COLUMNS:
        assert _spec(built, name).coverage == "observed", name
    assert built.cohort["unknown_columns"] == {}, built.cohort
    assert series.check(_store(), built) == []


@pytest.mark.usefixtures("telltale_home")
def test_a_truncated_path_list_leaves_the_three_columns_unknown(
    tmp_path: Path,
) -> None:
    """A commit whose list the payload bound cut answers none of the three questions.

    A prefix is not the change. A truncated list holding no test file does not mean the
    commit changed no test file, so all three cells go None rather than reading low, the
    columns go `partial` because the other row knows its paths, and the cohort names the
    reason. files_changed on the same row is still the true count, which is the pair
    that makes the truncation legible rather than invisible.
    """
    root = _repository(tmp_path / "repo")
    _after(root, "T-wide", 1, _committing(PATH_COMMITS[0][0]))
    narrow = _git(root, "rev-parse", "HEAD")
    _after(root, "T-wide", 2, _committing(WIDE_COMMIT))
    wide = _git(root, "rev-parse", "HEAD")
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)

    built = _built(repo_id, clock="change")

    assert len(built.rows) == 2
    assert _by_sha(built) == {narrow: [1, 0, 0], wide: [None, None, None]}
    assert _cell(built, wide, "files_changed") == PER_FILE_MAX + 1
    for name in PATH_COLUMNS:
        assert _spec(built, name).coverage == "partial", name
    assert sorted(built.cohort["unknown_columns"]) == sorted(PATH_COLUMNS)
    assert set(built.cohort["unknown_columns"].values()) == {NO_PATHS}
    assert series.check(_store(), built) == []


def _by_sha(built: Any) -> dict[str, list[float | None]]:
    """Each row's three path columns, keyed by the commit. Design 6.12's order.

    Keyed rather than indexed because rows are ordered by committed_ts and git dates a
    commit in whole seconds: two of these captures finish inside one second, so their
    order is decided by the sha tiebreak and an index would make the assertion depend
    on how fast the machine is.
    """
    return {
        str(meta.row_key): [
            _cell(built, str(meta.row_key), name) for name in PATH_COLUMNS
        ]
        for meta in built.row_meta
    }


def _cell(built: Any, sha: str, name: str) -> float | None:
    row = [meta.row_key for meta in built.row_meta].index(sha)
    return _column(built, name)[row]


def _commits(repo_id: str) -> list[dict[str, Any]]:
    store = _store()
    return [
        dict(row["payload"])
        for capture in store.captures()
        if capture["repo_id"] == repo_id
        for row in store.observations(str(capture["capture_id"]))
        if row["observation_type"] == "telltale.repo.commit"
    ]


@pytest.mark.usefixtures("telltale_home")
def test_a_change_row_reading_a_later_activity_is_caught_by_check(
    tmp_path: Path,
) -> None:
    """Break-and-restore: move a change row's end back and `check` has to name it.

    The row is built from real activities, so its provenance is real ids with real
    positions; moving `row_end_ts` earlier is the same edit test_series.py makes on the
    request clock, and it has to fail on this clock for the same reason. Without it,
    "check() covers both clocks" would be a claim resting on the clocks agreeing.
    """
    from dataclasses import replace

    root = _repository(tmp_path / "repo")
    script = tmp_path / "hook-child.py"
    script.write_text(HOOK_CHILD, encoding="utf-8")
    _during(root, script, "T-land", 1)
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)
    built = _built(repo_id, clock="change")

    assert series.check(_store(), built) == []
    meta = list(built.row_meta)
    meta[0] = replace(meta[0], row_end_ts="1970-01-01T00:00:00.000000Z")
    violations = series.check(_store(), replace(built, row_meta=meta))

    assert violations
    assert all(one.startswith("row 0 ") for one in violations)
    assert any("look-ahead" in one for one in violations)
