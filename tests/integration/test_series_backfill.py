"""A git-backfilled lineage on the change clock: rows for commits nobody watched.

W8-T2. `telltale import git-history` is W8-T1's and has not landed, so the capture the
change clock reads here is written by hand, through the real allowlist, the real store
and the real activities reducer. What is hand-written is the PAYLOADS, and they are the
shapes the coordinator fixed on 2026-09-06 (quoted in briefs/W8-T2.md). The launcher
capture beside them is a real `telltale run` of a real child that commits, so the rung
its commit reaches, the model requests it makes and the payload it records are all the
real linker's work.

A separate file from test_series_lineage.py, which was at the 800-line ratchet, and it
imports that file's repository and launcher helpers the way test_advise.py and
test_outcome.py already do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from test_series_lineage import (
    CHILD_LIFETIME_S,
    FAKE_AGENT,
    _built,
    _cell,
    _column,
    _commits,
    _repository,
    _run,
    _spec,
    _store,
)

from telltale import config, repo, series
from telltale import series_lineage as lineage
from telltale import series_outcomes as outcomes
from telltale.series_paths import PATH_COLUMNS
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

pytestmark = pytest.mark.integration


# What W8-T1's `telltale import git-history` will write, written by hand here because
# it does not exist yet: one capture per repository, provider `git`, one
# telltale.repo.commit per first-parent commit at the rung `unlinked`, and zero or one
# external.outcome of each post-merge kind per commit, naming the commit by sha. The
# coordinator fixed these shapes on 2026-09-06 and they are quoted in briefs/W8-T2.md.
# Nothing here parses a git history: that is W8-T1's half, and this file is the
# compiler's half of the same contract.
BACKFILL_PROVIDER = "git"
BACKFILL_SURFACE = "git_history"
BACKFILL_ADAPTER = "telltale.import@1"

# Seven commits nothing recorded a session for, one hour apart, well before the
# launcher capture below. The eighth row is the real commit that capture links, so the
# frame holds both kinds and every column's coverage word is measured over the pair.
HISTORY_HOURS = 7
HISTORY_START = "2026-08-01T10:00:00Z"


def _history_ts(index: int) -> str:
    return f"2026-08-01T{10 + index:02d}:00:00Z"


def _history_sha(index: int) -> str:
    """A 40-hex sha for a commit no repository here contains, by construction.

    The seven backfilled rows do not have to be commits of the test repository: the
    change clock reads the payload the recorder wrote and never runs git, which is the
    rule series_paths.py states and the reason a series is rebuildable from the store.
    """
    return f"{index:040x}"


def _commit_payload(index: int) -> dict[str, Any]:
    """One telltale.repo.commit of a git history backfill. Design 6.3 plus W3-T4."""
    return {
        "sha": _history_sha(index),
        "parents": [_history_sha(index - 1)] if index else [],
        "tree": _history_sha(index + 100),
        "committed_ts": _history_ts(index),
        "files_changed": 1,
        "additions": index + 1,
        "deletions": index,
        "link_confidence": "unlinked",
        "per_file": [{"path": f"src/f{index}.py", "additions": index + 1,
                      "deletions": index}],
    }  # fmt: skip


# The four outcomes of the fixture, by the index of the commit each names.
#   0: a check run that passed, with the 90 s the brief asks for.
#   1: a check run that failed.
#   2: a rework INSIDE the window: its timestamp is the hour of commit 4, and commit 2's
#      deadline is commit 5, so it counts.
#   3: a rework OUTSIDE the window: its timestamp is the hour of commit 7, which is the
#      real commit below, and commit 3's deadline is commit 6.
PASS_MS = 90000
FAIL_MS = 12000


def _outcome_payloads(last_ts: str) -> list[dict[str, Any]]:
    return [
        {
            "kind": "mechanical_verification", "status": "pass",
            "external_system": "github:check-runs", "component_id": _history_sha(0),
            "duration_ms": PASS_MS, "timestamp": _history_ts(0),
        },
        {
            "kind": "mechanical_verification", "status": "fail",
            "external_system": "github:check-runs", "component_id": _history_sha(1),
            "duration_ms": FAIL_MS, "timestamp": _history_ts(1),
        },
        {
            "kind": "revert_or_repair", "status": "reworked",
            "external_system": "git:line-overlap", "component_id": _history_sha(2),
            "external_run_id": _history_sha(4), "timestamp": _history_ts(4),
        },
        {
            "kind": "revert_or_repair", "status": "reworked",
            "external_system": "git:line-overlap", "component_id": _history_sha(3),
            "external_run_id": _history_sha(7), "timestamp": last_ts,
        },
    ]  # fmt: skip


def _observation(
    capture: str, repo_id: str, root: Path, obs_type: str, payload: dict[str, Any],
    stamp: str, provider: str = BACKFILL_PROVIDER, surface: str = BACKFILL_SURFACE,
) -> Any:  # fmt: skip
    """One backfill observation through the real allowlist, exactly as importer._emit.

    Sanitized rather than written raw: the allowlist is what decides which fields of a
    payload survive, and a fixture that skipped it would be a fixture of a payload the
    store can never hold.

    `provider` is a parameter because W8-T4 needs a SECOND recorder in one lineage: the
    tracked base is defined by the captures whose provider is `git`, so a fixture that
    could only write that provider could not write a commit that has to attach to the
    base rather than define it.
    """
    from telltale.model import Observation, now_iso, ulid
    from telltale.sanitize import Ctx, sanitize

    ctx = Ctx(repo_root=root)
    body, redaction, _unknown = sanitize(obs_type, dict(payload), 1, ctx)
    return Observation(
        observation_id=ulid(),
        capture_id=capture,
        observation_type=obs_type,
        surface=surface,
        provider=provider,
        adapter=BACKFILL_ADAPTER,
        ingest_ts=now_iso(),
        provider_ts=stamp,
        repo_id=repo_id,
        payload=body,
        redaction=redaction,
    )


def _backfill(
    root: Path,
    repo_id: str,
    commits: Sequence[dict[str, Any]],
    posted: Sequence[dict[str, Any]] | None = None,
) -> str:
    """Write one git-history capture of these commits and their outcomes, and reduce it.

    Through the real store, the real allowlist and the real activities reducer: what is
    hand-written is the PAYLOADS, because the writer that will produce them is W8-T1's
    and is not on this branch.
    """
    from telltale import importer, measures  # noqa: F401  (registers the reducers)

    # Importing measures is what registers the two reducers `store.rebuild` runs, and
    # it is the same import `telltale.cli` makes: a rebuild against an unregistered
    # reducer writes nothing and says nothing, which is not a fixture, it is a silence.
    assert Store.reducers, "no reducer is registered: `store.rebuild` would be a no-op"

    capture = importer.capture_id(BACKFILL_PROVIDER, repo_id)
    last = commits[-1]["committed_ts"]
    rows = [
        _observation(capture, repo_id, root, "telltale.capture_started", {
            "provider": BACKFILL_PROVIDER, "argv_shape": "backfill", "content_level": 1,
            "surfaces_configured": [BACKFILL_SURFACE], "source_kind": "git-history",
        }, commits[0]["committed_ts"]),
        *[
            _observation(capture, repo_id, root, "telltale.repo.commit", payload,
                         str(payload["committed_ts"]))
            for payload in commits
        ],
        *[
            _observation(capture, repo_id, root, "external.outcome", payload,
                         str(payload["timestamp"]))
            for payload in (_outcome_payloads(last) if posted is None else posted)
        ],
        _observation(capture, repo_id, root, "telltale.capture_ended", {
            "exit_code": None, "duration_ms": None,
            "surfaces_received": {BACKFILL_SURFACE: len(commits)},
        }, last),
    ]  # fmt: skip
    store = Store(config.db_path()).open()
    try:
        assert store.append(rows) == len(rows)
        store.flush()
        store.rebuild(capture)
        store.flush()
    finally:
        store.close()
    return capture


# A child that is an agent AND a committer: it re-emits the fake agent's stream-json on
# its own stdout, so the launcher's tee sees real model requests, then commits and
# reports the Bash call through the PostToolUse hook the launcher configured, which is
# what puts the link on spec 12.3's `tree_match_during` rung. `--output-format
# stream-json` is in the launcher's argv because that flag is what the launcher reads to
# decide to tee at all (providers/claude_launch.py).
LINKING_CHILD = """
import json, subprocess, sys, time, urllib.request
agent, command, lifetime = sys.argv[1], sys.argv[2], float(sys.argv[3])
settings = json.loads(sys.argv[sys.argv.index("--settings") + 1])
url = settings["hooks"]["PostToolUse"][0]["hooks"][0]["url"]
done = subprocess.run(
    [sys.executable, agent, "--output-format", "stream-json", "--seed", "3"],
    check=True, capture_output=True,
)
sys.stdout.write(done.stdout.decode())
sys.stdout.flush()
subprocess.run(["bash", "-c", command], check=True)
body = json.dumps({
    "hook_event_name": "PostToolUse",
    "session_id": "00000000-0000-4000-8000-0000000c0mm2",
    "tool_name": "Bash",
    "tool_input": {"command": command},
}).encode("utf-8")
request = urllib.request.Request(
    url, data=body, headers={"Content-Type": "application/json"}
)
urllib.request.urlopen(request, timeout=10).read()
time.sleep(lifetime)
"""

LINKING_COMMAND = "echo linked >> f && git add -A && git commit -q -m linked"


def _linked(tmp_path: Path) -> tuple[Path, str, dict[str, Any]]:
    """A repository, a real launcher capture that commits, and the commit's payload."""
    root = _repository(tmp_path / "repo")
    script = tmp_path / "linking-child.py"
    script.write_text(LINKING_CHILD, encoding="utf-8")
    done = _run(
        root, "run", "--provider", "claude", "--task-id", "T-linked", "--attempt", "1",
        "--", "python", str(script), str(FAKE_AGENT), LINKING_COMMAND,
        str(CHILD_LIFETIME_S), "--output-format", "stream-json",
    )  # fmt: skip
    assert done.returncode == 0, done.stderr.decode()
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)
    stored = _commits(repo_id)
    assert len(stored) == 1, stored
    return root, repo_id, stored[0]


def _mixed(tmp_path: Path) -> tuple[Path, str, dict[str, Any]]:
    """Eight rows: seven a git backfill wrote and one a launcher capture also linked.

    The backfill records the linked commit too, at the `unlinked` rung and with a
    files_changed nothing else in the repository agrees with, which is how the row-count
    assertion below is a claim about deduplication rather than about arithmetic.
    """
    root, repo_id, landed = _linked(tmp_path)
    commits = [_commit_payload(index) for index in range(HISTORY_HOURS)]
    commits.append(
        {
            **_commit_payload(HISTORY_HOURS),
            "sha": str(landed["sha"]),
            "committed_ts": str(landed["committed_ts"]),
            "files_changed": 99,
        }
    )
    _backfill(root, repo_id, commits)
    return root, repo_id, landed


def _flags(built: Any, sha: str) -> list[str]:
    return next(meta.flags for meta in built.row_meta if meta.row_key == sha)


@pytest.mark.usefixtures("telltale_home")
def test_a_commit_no_capture_landed_is_a_row_with_its_process_columns_unknown(
    tmp_path: Path,
) -> None:
    """The eight rows of a git-backfilled lineage, and what each half of one holds.

    The seven backfilled rows carry the repository columns off the payload and None in
    every process column, each with NO_CAPTURE against it in the cohort. The eighth is
    the commit a launcher capture linked: it is ONE row at that capture's rung, with
    that capture's numbers, and no `uncaptured` flag. Duplicate is not one.
    """
    root, repo_id, landed = _mixed(tmp_path)
    linked = str(landed["sha"])

    built = _built(repo_id, clock="change")

    assert len(built.rows) == HISTORY_HOURS + 1
    assert [meta.row_key for meta in built.row_meta] == [
        *(_history_sha(index) for index in range(HISTORY_HOURS)),
        linked,
    ]
    assert _flags(built, linked) == []
    assert all(
        _flags(built, _history_sha(index)) == [lineage.UNCAPTURED]
        for index in range(HISTORY_HOURS)
    )
    # The launcher's payload, not the backfill's 99: one sha is one row at the best rung
    # any recorder gave it, and the payload it reads is the one recorded AT that rung.
    assert _cell(built, linked, "files_changed") == landed["files_changed"] != 99
    assert _cell(built, linked, "attempts_to_land") == 1
    assert _cell(built, linked, "fresh_input_tokens_total") is not None
    for name in lineage.PROCESS_COLUMNS:
        assert _column(built, name)[:HISTORY_HOURS] == [None] * HISTORY_HOURS, name
        assert _spec(built, name).coverage == "partial", name
        assert lineage.NO_CAPTURE in built.cohort["unknown_columns"][name], name
    # The six repository columns are read off the payload on a backfilled row exactly as
    # on a landed one, which is the whole reason the row exists.
    assert _column(built, "lines_added")[:HISTORY_HOURS] == [1, 2, 3, 4, 5, 6, 7]
    assert _column(built, "subsystems_touched")[:HISTORY_HOURS] == [1] * HISTORY_HOURS
    assert series.check(_store(), built) == []
    assert root.exists()


@pytest.mark.usefixtures("telltale_home")
def test_the_post_merge_columns_read_outcomes_that_name_the_sha(
    tmp_path: Path,
) -> None:
    """A check run and a rework reach a row nothing landed, by component_id alone.

    The deadline rule is unchanged and is what separates rows 2 and 3: both carry a
    revert_or_repair naming them, row 2's is timestamped inside its three-commit window
    and row 3's is not. The last three rows of any lineage stay None with NO_TAIL,
    whatever the outcomes say, because the window has not happened.
    """
    _root, repo_id, _landed = _mixed(tmp_path)

    built = _built(repo_id, clock="change")

    assert _column(built, "merge_verification_ms") == [PASS_MS, FAIL_MS, *[None] * 6]
    assert _column(built, "merge_verification_failed") == [0, 1, *[None] * 6]
    assert _column(built, "rework_within_3") == [0, 0, 1, 0, 0, None, None, None]
    assert outcomes.NO_TAIL in built.cohort["unknown_columns"]["rework_within_3"]
    # The lagged label: row j holds change j - 3's, which change j's own commit time
    # decided. Row 5 is row 2's 1, and the first three rows have no source change.
    lag = _column(built, "rework_within_3_lag3")
    assert lag[5] == _column(built, "rework_within_3")[2] == 1
    assert lag[:3] == [None, None, None]
    assert lag == [None, None, None, 0, 0, 1, 0, 0]
    assert series.check(_store(), built) == []


@pytest.mark.usefixtures("telltale_home")
def test_a_frame_of_only_backfilled_rows_reads_unavailable_by_name(
    tmp_path: Path,
) -> None:
    """No capture landed any of these eight, so the seven process columns are empty.

    `unavailable` rather than `partial` is what `_measured` says over a column with no
    value in any row, and it is the word the backtester excludes a column by. The six
    repository columns are `observed` on the same frame, which is the pair that makes
    the row worth building: the diff is known and the session is not.
    """
    root = _repository(tmp_path / "history")
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)
    _backfill(root, repo_id, [_commit_payload(index) for index in range(8)])

    built = _built(repo_id, clock="change")

    assert len(built.rows) == 8
    assert [meta.flags for meta in built.row_meta] == [[lineage.UNCAPTURED]] * 8
    for name in lineage.PROCESS_COLUMNS:
        assert _spec(built, name).coverage == "unavailable", name
    for name in ("files_changed", "lines_added", "lines_removed", *PATH_COLUMNS):
        assert _spec(built, name).coverage == "observed", name
    # No attempt landed any row, so no row carries an environment fingerprint and
    # env_changed is unavailable and all None. That is `series.env_column`'s rule over
    # the row fingerprints, not `_measured`: the column is exempted from the cell rule
    # in `_specs` and takes the word env_column returned.
    assert _spec(built, "env_changed").coverage == "unavailable"
    assert _column(built, "env_changed") == [None] * 8
    assert built.cohort["providers"] == [BACKFILL_PROVIDER]
    assert built.cohort["captured_rows"] == 0
    assert built.cohort["uncaptured_rows"] == 8
    assert series.check(_store(), built) == []


@pytest.mark.usefixtures("telltale_home")
def test_the_cohort_counts_both_kinds_of_row_and_names_both_providers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`series build`, `series check` and `forecast readiness` over a mixed frame.

    The two counts are in the cohort so that two frames differing only in how many rows
    are backfilled are two series ids. The readiness checklist prints the uncaptured
    count beside the row count, because a reader who sees the seven process columns
    named as not forecastable has been told the symptom and not the cause.
    """
    from telltale import cli

    _root, repo_id, _landed = _mixed(tmp_path)

    assert cli.main(["series", "build", "--clock", "change", "--repo", repo_id]) == 0
    printed = capsys.readouterr().out
    series_id = printed.split()[0]
    assert "clock change  8 rows" in printed
    assert '"captured_rows": 1' in printed
    assert '"uncaptured_rows": 7' in printed
    assert '"providers": ["claude", "git"]' in printed
    assert "low_confidence rows 0" in printed
    assert cli.main(["series", "check", series_id]) == 0
    assert capsys.readouterr().out.strip() == "ok"

    # And what a registered target does on THIS frame: merge_verification_ms is known
    # on two rows of eight, so the column is `partial` and the backtester refuses it as
    # a target before any check runs. That refusal is the pre-registered rule of design
    # 6.12 (backtest.FORECASTABLE) and W8-T2 does not touch it; the checklist over a
    # frame whose target IS observed is the test below.
    assert (
        cli.main([
            "forecast", "readiness", "--series", series_id,
            "--target", "merge_verification_ms",
        ])
        == 2
    )  # fmt: skip
    assert "coverage partial: a target must be one of" in capsys.readouterr().out


@pytest.mark.usefixtures("telltale_home")
def test_readiness_names_the_unavailable_columns_and_counts_the_uncaptured_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The checklist over a lineage that is backfilled end to end. W8-T2 (4).

    Every commit carries a check run here, so merge_verification_ms is `observed` and
    the backtester will take it as a target. Check 1 then names the seven process
    columns as excluded by name with the word `unavailable` beside each, which is
    W7-T3's rule doing its job on a row whose process was never observed, and the
    header line says how many rows that is: an uncaptured row is not excluded, its
    unavailable COLUMNS are. The two rework columns are `partial` by construction on
    a lineage (the last three and the first three rows have no label yet), and W7-T3's
    rule fails check 1 on a partial column; what that means for the change clock is
    W8-T3's question, not this test's, which pins the words for the process columns.
    """
    from telltale import cli

    root = _repository(tmp_path / "history")
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)
    _backfill(
        root,
        repo_id,
        [_commit_payload(index) for index in range(8)],
        [
            {
                "kind": "mechanical_verification",
                "status": "pass",
                "external_system": "github:check-runs",
                "component_id": _history_sha(index),
                "duration_ms": PASS_MS + index,
                "timestamp": _history_ts(index),
            }
            for index in range(8)
        ],
    )

    assert cli.main(["series", "build", "--clock", "change", "--repo", repo_id]) == 0
    series_id = capsys.readouterr().out.split()[0]
    cli.main([
        "forecast", "readiness", "--series", series_id,
        "--target", "merge_verification_ms",
    ])  # fmt: skip
    checklist = capsys.readouterr().out

    assert "rows 8  uncaptured 8" in checklist
    assert "excluded by name:" in checklist
    for name in lineage.PROCESS_COLUMNS:
        assert f"{name} (unavailable)" in checklist, name


# -- the tracked base -----------------------------------------------------------------
#
# W8-T4. Once a git-history import is in the store, the rows of the change clock are its
# commits and nothing else. What the four captures below put in front of that rule is
# every way a captured commit can meet a base: as itself, as a tree that landed under
# another sha, and as neither.

# The commit no capture recorded and no base commit twins: it is on a task branch that
# was squash-merged, so it never landed as itself. Distinct from every _history_sha and
# from every base tree by construction.
OFF_BASE_SHA = f"{0xC0FFEE:040x}"
OFF_BASE_TREE = f"{0xDECAF:040x}"

# The base commit the launcher's real commit twins: a different sha carrying the tree
# the branch tip left behind. `files_changed` is deliberately not the launcher's, so the
# assertion below is a claim about WHICH payload the row reads.
TWIN_INDEX = HISTORY_HOURS
TWIN_FILES = 42

# The rung the session capture records twice for one base sha, best first. `explicit`
# carries no low_confidence flag and `tree_match_after` does, so the flag on the built
# row is what says which of the two won.
TWICE_LINKED = ("tree_match_after", "explicit")
TWICE_INDEX = 2
TWICE_FILES = 77

SESSION_PROVIDER = "claude"
SESSION_SURFACE = "claude_hooks"


def _session(root: Path, repo_id: str, commits: Sequence[dict[str, Any]]) -> str:
    """One non-git capture recording these commits, and nothing else about itself.

    Provider `claude`, so `tracked_base` does not read its commits as base commits. It
    names no task_id, so it is not an attempt and lands no process column; what it is
    here for is the LINK, which is the thing the base rule decides about.
    """
    from telltale import importer, measures  # noqa: F401  (registers the reducers)

    capture = importer.capture_id(SESSION_PROVIDER, f"{repo_id}-session")
    stamp = str(commits[0]["committed_ts"])
    rows = [
        _observation(capture, repo_id, root, "telltale.capture_started", {
            "provider": SESSION_PROVIDER, "argv_shape": "session", "content_level": 1,
            "surfaces_configured": [SESSION_SURFACE], "source_kind": "launcher",
        }, stamp, SESSION_PROVIDER, SESSION_SURFACE),
        *[
            _observation(capture, repo_id, root, "telltale.repo.commit", payload,
                         str(payload["committed_ts"]), SESSION_PROVIDER,
                         SESSION_SURFACE)
            for payload in commits
        ],
    ]  # fmt: skip
    store = Store(config.db_path()).open()
    try:
        assert store.append(rows) == len(rows)
        store.flush()
        store.rebuild(capture)
        store.flush()
    finally:
        store.close()
    return capture


def _based(tmp_path: Path) -> tuple[str, dict[str, Any], list[str]]:
    """A tracked base of eight commits, and three captured commits against it.

    (repo_id, the launcher's own commit payload, the base's shas in committed order).

      the launcher's commit  a real `telltale run` that commits. Its sha is on no base
                             commit and its TREE is the last one's: the branch tip after
                             the gate protocol merged main in has exactly the tree the
                             squash commit put on main, and that is case (b).
      _history_sha(2)        recorded twice by one session capture, at two rungs, and it
                             IS a base commit: cases (a) and (d).
      OFF_BASE_SHA           recorded by the same session capture, on neither the base's
                             shas nor its trees: case (c).
    """
    root, repo_id, landed = _linked(tmp_path)
    base = [_commit_payload(index) for index in range(HISTORY_HOURS)]
    base.append({
        **_commit_payload(TWIN_INDEX),
        "tree": str(landed["tree"]),
        "committed_ts": str(landed["committed_ts"]),
        "files_changed": TWIN_FILES,
    })  # fmt: skip
    _backfill(root, repo_id, base)
    _session(root, repo_id, [
        *[
            {
                **_commit_payload(TWICE_INDEX), "link_confidence": rung,
                "files_changed": TWICE_FILES,
            }
            for rung in TWICE_LINKED
        ],
        {
            **_commit_payload(0), "sha": OFF_BASE_SHA, "tree": OFF_BASE_TREE,
            "committed_ts": _history_ts(1), "link_confidence": "tree_match_during",
        },
    ])  # fmt: skip
    return repo_id, landed, [str(one["sha"]) for one in base]


@pytest.mark.usefixtures("telltale_home")
def test_the_tracked_base_defines_the_rows_and_a_tree_twin_joins_the_one_it_landed_as(
    tmp_path: Path,
) -> None:
    """Eight rows, because the base has eight commits. W8-T4 (2) and (3).

    Before this rule the frame held eleven: the base's eight plus the three commits a
    capture recorded, ordered among them by their own commit times. Measured on the
    owner's store on 2026-09-06, that was 51 such commits among 168, and NONE of the 51
    is on main's first-parent history - every one is a task-branch commit that was
    squash-merged, so the lineage a reader was handed was two histories interleaved.
    """
    repo_id, landed, shas = _based(tmp_path)
    twin = shas[TWIN_INDEX]

    built = _built(repo_id, clock="change")

    assert [meta.row_key for meta in built.row_meta] == shas
    assert len(built.rows) == len(shas) == HISTORY_HOURS + 1
    assert str(landed["sha"]) not in shas, "the fixture's twin shares a sha, not a tree"
    assert built.cohort["captured_rows"] == 2
    assert built.cohort["uncaptured_rows"] == HISTORY_HOURS - 1
    assert built.cohort["off_base_commits"] == 1
    assert built.cohort["collapsed_links"] == 1
    # (b) The twin row is keyed on the BASE commit and carries the capture that made the
    # branch commit: its process columns, its attempt, and the branch sha in row_meta.
    assert _flags(built, twin) == [f"{lineage.CAPTURED_SHA}={landed['sha']}"]
    assert _cell(built, twin, "fresh_input_tokens_total") is not None
    assert _cell(built, twin, "attempts_to_land") == 1
    # And the base's numbers, not the branch commit's: the row is the commit that landed
    # on the base, and a branch tip's diff against its own parent is not that commit's.
    assert _cell(built, twin, "files_changed") == TWIN_FILES != landed["files_changed"]
    # (a) and (d) One sha, one row, at the better of the two rungs one capture recorded.
    # `tree_match_after` is a low_confidence rung and `explicit` is not, so an empty
    # flag list says the higher rung won, and the payload read is that rung's.
    twice = shas[TWICE_INDEX]
    assert _flags(built, twice) == []
    assert _cell(built, twice, "files_changed") == TWICE_FILES
    # (c) Not a row at all, and named rather than dropped in silence.
    assert {one["key"]: one["reason"] for one in built.cohort["dropped"]}[
        OFF_BASE_SHA
    ] == lineage.OFF_BASE
    assert series.check(_store(), built) == []


@pytest.mark.usefixtures("telltale_home")
def test_without_a_git_history_capture_the_rows_are_the_captured_commits(
    tmp_path: Path,
) -> None:
    """The same three captured commits, no import, and every one of them a row.

    The rule is reached through a tracked base and there is none here, so nothing is
    dropped and nothing is re-keyed: this is the frame W8-T2 built. Without it a reader
    could not tell "the base rule kept these" from "the base rule never ran".
    """
    root, repo_id, landed = _linked(tmp_path)
    _session(root, repo_id, [
        *[
            {
                **_commit_payload(TWICE_INDEX), "link_confidence": rung,
                "files_changed": TWICE_FILES,
            }
            for rung in TWICE_LINKED
        ],
        {
            **_commit_payload(0), "sha": OFF_BASE_SHA, "tree": OFF_BASE_TREE,
            "committed_ts": _history_ts(1), "link_confidence": "tree_match_during",
        },
    ])  # fmt: skip

    built = _built(repo_id, clock="change")

    assert sorted(meta.row_key for meta in built.row_meta) == sorted(
        [_history_sha(TWICE_INDEX), OFF_BASE_SHA, str(landed["sha"])]
    )
    assert "off_base_commits" not in built.cohort
    assert built.cohort["collapsed_links"] == 1
    assert all(
        lineage.CAPTURED_SHA not in "".join(meta.flags) for meta in built.row_meta
    )
    assert series.check(_store(), built) == []
