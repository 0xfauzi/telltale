"""`telltale import git-history`, against a repository the test builds commit by commit.

The fixture is `git init` and eight commits, not a checked-in `.git` directory: what is
under test is a walk over real git output, and a recorded walk would be a test of the
recording. Every commit here is written for one assertion, and the docstring of
`_history` names which.

The rework label is the part a fixture can pin down that a real repository cannot. Its
rule is "a later commit within the next three removed, in the same file, a line this
one added, ignoring lines of 3 characters or fewer after stripping", and the eight
commits below make three statements about it at once: two commits are reworked and are
named, one overlap falls outside the window and must produce nothing, and one overlap
exists ONLY on the un-stripped comparison, so a reader who drops the normalisation gets
a third outcome the fixture never designed.

No network: every test here passes `--no-checks`, so `gh` is never run and the check-run
half is exercised by the VERIFY commands in docs/log/W8-T1.md against this repository's
own GitHub check runs.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from telltale import importer_git, repo

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from telltale.store import Store

# Content strings that must never reach the store. The same three shapes E01 planted in
# its fixture repository (tests/integration/test_privacy.py PROBES), put here inside a
# committed FILE's text rather than in its name: a path is data this importer stores on
# purpose, and file contents are what it must never read out of a repository.
PROBES = (
    "sk-ant-api03-TELLTALEFAKE0000000000000000000000000000000000000",
    "AKIATELLTALEFAKE00001",
    "BEGIN TELLTALEFAKE PRIVATE KEY",
)

COMMITS = 8
# Design 6.12's window: the last three commits of any history have no label yet.
UNDECIDED = 3
DECIDED = COMMITS - UNDECIDED


def _git(cwd: Path, *args: str) -> None:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr.decode()


def _write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    done = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, timeout=30,
        check=True,
    )  # fmt: skip
    return done.stdout.decode().strip()


# The four states of pkg/core.py, in commit order. Each is the whole file, so what the
# diff holds is what git computes rather than what this test asserts about it.
_CORE_0 = """ALPHA = "alpha configuration value"
PLACEHOLDER = "placeholder line to be replaced"


def load(name):
    return ALPHA + name
"""
_CORE_1 = (
    _CORE_0
    + """BETA = "beta configuration value"
"""
)
_CORE_2 = _CORE_1.replace('PLACEHOLDER = "placeholder line to be replaced"\n', "")
_CORE_3 = _CORE_2.replace(
    'BETA = "beta configuration value"\n', 'DELTA = "delta configuration value"\n'
)

# The three states of tests/test_core.py. The two wrapped calls are deliberately spelled
# differently (`compute(` against `load(`) so that the only line c2 added and c4 removed
# is the indented `)`, which the stripped rule drops and the un-stripped rule keeps.
_TEST_0 = """from pkg.core import load


def test_first():
    assert compute(
        "first argument",
    )
"""
_TEST_2 = (
    _TEST_0
    + """

def test_wrapped():
    assert load(
        "wrapped argument",
    )
"""
)
_TEST_4 = """from pkg.core import load


def test_wrapped():
    assert load(
        "wrapped argument",
    )
"""

_LOCK = """version = 1
requires-python = ">=3.12"

[[package]]
name = "example-dependency"
version = "1.2.3"
"""

# One committed file's CONTENT holds the three credential probes. Its path does not:
# per_file carries paths, and a path this test planted a probe in would be a probe the
# store is supposed to keep.
_NOTES = "notes for the operator\n" + "\n".join(PROBES) + "\n"


def _history(root: Path) -> list[str]:
    """Eight commits, oldest first, each written for the assertion named beside it.

    c0 the package, its test and the file holding the probes.
    c1 adds BETA to pkg/core.py.                     Reworked by c3.
    c2 removes PLACEHOLDER, adds a wrapped test.     Reworks c0; reworked by nothing.
    c3 removes BETA, adds DELTA.                     Reworks c1.
    c4 the lockfile, and deletes test_first.         Reworks c2 ONLY un-stripped.
    c5 adds pkg/extra.py with GAMMA.                 Undecided: no window yet.
    c6 adds a line to the test.                      Undecided.
    c7 removes GAMMA from pkg/extra.py.              Undecided; would rework c5.
    """
    _git(root, "init", "--quiet", "-b", "main")
    _git(root, "config", "user.email", "fixture@telltale.invalid")
    _git(root, "config", "user.name", "Fixture")
    shas = []
    _write(root, "pkg/core.py", _CORE_0)
    _write(root, "tests/test_core.py", _TEST_0)
    _write(root, "pkg/notes.txt", _NOTES)
    shas.append(_commit(root, "c0 the package and its test"))
    _write(root, "pkg/core.py", _CORE_1)
    shas.append(_commit(root, "c1 beta"))
    _write(root, "pkg/core.py", _CORE_2)
    _write(root, "tests/test_core.py", _TEST_2)
    shas.append(_commit(root, "c2 drop the placeholder, wrap a call"))
    _write(root, "pkg/core.py", _CORE_3)
    shas.append(_commit(root, "c3 beta becomes delta"))
    _write(root, "uv.lock", _LOCK)
    _write(root, "tests/test_core.py", _TEST_4)
    shas.append(_commit(root, "c4 the lockfile, and the first test goes"))
    _write(root, "pkg/extra.py", 'GAMMA = "gamma configuration value"\n')
    shas.append(_commit(root, "c5 extra"))
    _write(root, "tests/test_core.py", _TEST_4 + "\n\ndef test_extra():\n    pass\n")
    shas.append(_commit(root, "c6 one more test"))
    _write(root, "pkg/extra.py", 'EPSILON = "epsilon configuration value"\n')
    shas.append(_commit(root, "c7 gamma goes"))
    return shas


def _telltale() -> str:
    found = shutil.which("telltale")
    assert found is not None, "no `telltale` on PATH: run `uv sync` first"
    return found


def _cli(*args: str) -> str:
    done = subprocess.run(
        [_telltale(), *args], capture_output=True, timeout=300, check=False
    )
    assert done.returncode == 0, done.stderr.decode()
    return done.stdout.decode()


def _import(root: Path, *extra: str) -> str:
    return _cli("import", "git-history", "--repo", str(root), "--no-checks", *extra)


def _rows(store: Store, capture_id: str, obs_type: str) -> list[dict[str, Any]]:
    return [dict(row["payload"]) for row in store.observations(capture_id, (obs_type,))]


def _reworks(store: Store, capture_id: str) -> set[tuple[str, str]]:
    """(the commit that was reworked, the commit that reworked it), as shas."""
    return {
        (str(row["component_id"]), str(row["external_run_id"]))
        for row in _rows(store, capture_id, "external.outcome")
        if row.get("kind") == "revert_or_repair"
    }


def _capture(root: Path) -> str:
    return importer_git.capture_of(importer_git.resolve(root)[1])


def _tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def _count(store: Store, capture_id: str) -> int:
    return len(store.observations(capture_id))


@pytest.mark.integration
def test_a_history_becomes_one_capture_of_unlinked_change_rows(
    store: Store, tmp_path: Path
) -> None:
    """Eight commits, one capture, eight change rows, and every one of them unlinked.

    `unlinked` rather than a rung of spec 12.3's ladder: the ladder answers "did this
    capture make that commit", and no capture was in the question. per_file is on every
    row, because the three path columns of the change clock are built from it.
    """
    root = tmp_path / "fixture"
    root.mkdir()
    shas = _history(root)

    printed = _import(root)

    capture_id = _capture(root)
    commits = _rows(store, capture_id, "telltale.repo.commit")
    assert len(commits) == COMMITS
    assert [row["sha"] for row in commits] == shas
    assert {row["link_confidence"] for row in commits} == {importer_git.UNLINKED}
    assert all(row.get("per_file") for row in commits)
    # The root commit has no parent and still reports what it changed: `--root` on the
    # diff-tree repo_link runs is what makes it its whole tree rather than nothing.
    assert commits[0]["parents"] == []
    assert commits[0]["files_changed"] == 3
    assert f"imported {COMMITS} new commit(s)" in printed
    assert len(store.captures()) == 1


@pytest.mark.integration
def test_the_rework_label_names_which_commit_reworked_which(
    store: Store, tmp_path: Path
) -> None:
    """Exactly the two reworks the fixture designs, and nothing for the last three.

    c2 removed the PLACEHOLDER line c0 added, and c3 removed the BETA line c1 added.
    Both are inside the three-commit window, so both are decided and both are here.

    c7 removes the GAMMA line c5 added, which is the same overlap. It produces NO
    outcome, because c5 is one of the last three commits and design 6.12's label is not
    decidable until three more have landed. That absence is the assertion: a 0 there
    would be this import claiming that nothing went wrong yet.
    """
    root = tmp_path / "fixture"
    root.mkdir()
    shas = _history(root)

    _import(root)

    capture_id = _capture(root)
    assert _reworks(store, capture_id) == {(shas[0], shas[2]), (shas[1], shas[3])}
    labelled = {pair[0] for pair in _reworks(store, capture_id)}
    assert not labelled & set(shas[DECIDED:]), "a commit with no window was labelled"


@pytest.mark.integration
def test_a_second_import_appends_nothing_and_a_dry_run_writes_nothing(
    store: Store, telltale_home: Path, tmp_path: Path
) -> None:
    """The one importer whose capture is appended to still appends each commit once.

    A repository's history grows, so this kind reads back the shas it already recorded
    instead of skipping the capture whole the way every other kind does. Running it
    twice over an unchanged repository must therefore find nothing, and say so.
    """
    root = tmp_path / "fixture"
    root.mkdir()
    _history(root)
    _import(root)
    capture_id = _capture(root)
    before = _count(store, capture_id)

    printed = _import(root)
    listing = _tree(telltale_home)
    dry = _cli("import", "git-history", "--repo", str(root), "--no-checks", "--dry-run")

    assert "imported 0 new commit(s)" in printed
    assert _count(store, capture_id) == before
    assert f"commits found {COMMITS}  commits new 0" in dry
    assert f"already recorded {COMMITS}" in dry
    assert "dry run: nothing was written" in dry
    assert _tree(telltale_home) == listing, "the dry run wrote into $TELLTALE_HOME"


@pytest.mark.integration
def test_a_window_that_closes_between_two_imports_is_labelled_on_the_second(
    store: Store, tmp_path: Path
) -> None:
    """c5's label is undecidable at the first import and decided at the second.

    The commit that reworked it (c7) was already stored, so nothing about the evidence
    changed. What changed is that three more commits have landed, which is the whole
    content of design 6.12's delayed label, and it is why this kind re-evaluates the
    last three recorded commits on every run instead of only the new ones.
    """
    root = tmp_path / "fixture"
    root.mkdir()
    shas = _history(root)
    _import(root)
    capture_id = _capture(root)
    assert (shas[5], shas[7]) not in _reworks(store, capture_id)

    _write(root, "pkg/late.py", "LATE = 1\n")
    later = _commit(root, "c8 one more, which closes c5's window")
    printed = _import(root)

    assert "imported 1 new commit(s)" in printed
    assert (shas[5], shas[7]) in _reworks(store, capture_id)
    assert later in {
        str(row["sha"]) for row in _rows(store, capture_id, "telltale.repo.commit")
    }


@pytest.mark.integration
def test_no_file_content_reaches_the_store(
    store: Store, tmp_path: Path, db_after_close: Any
) -> None:
    """Paths and counts, never a line. The probes are in a committed file's text.

    This importer reads two things out of a repository that no other reader does: a
    commit's numstat, and its diff. The numstat is counted and the diff is compared line
    against line inside this process and dropped. So a credential committed to the
    repository must be absent from every byte SQLite wrote, and the file it is in must
    still be named in per_file: the path is what the change clock's path columns read.
    """
    root = tmp_path / "fixture"
    root.mkdir()
    _history(root)

    _import(root)
    blob = db_after_close(store)

    for probe in PROBES:
        assert probe.encode() not in blob, f"{probe[:12]}... reached the database"
    assert b"TELLTALEFAKE" not in blob
    assert b"pkg/notes.txt" in blob, "the path was dropped, so the test proves nothing"


@pytest.mark.integration
def test_a_directory_that_is_not_a_repository_is_refused_by_name(
    telltale_home: Path, tmp_path: Path
) -> None:
    """Two refusals, each naming the condition it hit rather than exiting non-zero."""
    plain = tmp_path / "plain"
    plain.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "--quiet", "-b", "main")

    outside = subprocess.run(
        [_telltale(), "import", "git-history", "--repo", str(plain)],
        capture_output=True, timeout=120, check=False,
    )  # fmt: skip
    unborn = subprocess.run(
        [_telltale(), "import", "git-history", "--repo", str(empty)],
        capture_output=True, timeout=120, check=False,
    )  # fmt: skip

    assert b"is not inside a git working tree" in outside.stdout
    assert b"has no commits" in unborn.stdout
    assert outside.returncode != 0
    assert unborn.returncode != 0
    # A refusal must not have created the database on the way to refusing.
    assert not (telltale_home / "telltale.db").exists()


@pytest.mark.integration
def test_the_stripping_rule_is_what_keeps_c2_unlabelled(tmp_path: Path) -> None:
    """The normalisation is load-bearing, and this is the commit pair that shows it.

    c2 added an indented `)` closing a wrapped call, and c4 removed an indented `)`
    closing a DIFFERENT wrapped call, in the same file. Stripped, both are `)`, one
    character, which `_keep` drops as evidence of nothing. Un-stripped they are `    )`,
    five characters, equal to each other and to nothing else, so a reader that skipped
    the normalisation would report that c4 reworked c2.

    Asserted against `_parse_diff` directly rather than by editing the module, because
    the assertion is about the two line sets and not about the store: with stripping the
    intersection is empty, and the same two diffs intersect on `    )` without it.
    """
    root = tmp_path / "fixture"
    root.mkdir()
    shas = _history(root)

    added = importer_git._diff_of(str(root), shas[2])
    removed = importer_git._diff_of(str(root), shas[4])

    assert added is not None
    assert removed is not None
    path = "tests/test_core.py"
    assert not added.added[path] & removed.removed[path]
    raw_added = _raw_lines(str(root), shas[2], "+")
    raw_removed = _raw_lines(str(root), shas[4], "-")
    assert "    )" in raw_added & raw_removed


def _raw_lines(root: str, sha: str, marker: str) -> set[str]:
    """Every changed line of one commit's diff, with no normalisation applied at all.

    The un-stripped comparison the rule refuses, built here rather than by breaking the
    module, so the test states both answers in one place.
    """
    out = repo.git_stdout(
        root, "show", "--first-parent", "--format=", "--unified=0", sha
    )
    assert out is not None
    text = out.decode("utf-8", "replace")
    return _marked(text.splitlines(), marker)


def _marked(lines: Sequence[str], marker: str) -> set[str]:
    keep: set[str] = set()
    in_hunk = False
    for line in lines:
        if line.startswith("diff --git "):
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith(marker):
            keep.add(line[1:])
    return keep
