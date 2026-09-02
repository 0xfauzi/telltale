"""What a linked commit's changed paths say. The change clock's last three columns.

Design 6.12 names `subsystems_touched`, `test_files_changed` and `dependency_delta`,
and all three are questions about paths rather than about counts. W3-T1 could answer
none of them because `telltale.repo.commit` carried no path list; W3-T4 put one on the
payload (repo.per_file, through repo_link._commit_stats), and this module is the rule
that reads it.

A separate file from series_lineage.py, which was at 769 lines against the 800-line
ratchet. The cut is also the right one on its own: everything here is a decision about
what a PATH means, taken from the string alone, while series_lineage.py folds a
repository into rows. Nothing here reads the store, and nothing here runs git: design
6.12 requires a series to be rebuildable from the store, so a rule that stat()ed a file
would make the answer depend on the checkout the build happened to run in.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# The three columns of design 6.12 this module fills, in its order.
PATH_COLUMNS = ("subsystems_touched", "test_files_changed", "dependency_delta")

# The one reason a cell of those three is unknown. Reported on the cohort rather than
# hidden, because "no surface carried it" and "this commit's paths are not in the
# store" are different sentences and the second one names what would fix it.
NO_PATHS = (
    "the commit carries no per_file list: recorded before W3-T4, or a merge, whose"
    " per-file numbers depend on which parent is picked, or a list the 8 KB payload"
    " bound truncated"
)

# What makes a changed path a test file. A path component of `tests` or `test`, or a
# basename matching one of these globs. Two directory words rather than one, because
# `tests/` is the Python convention and `test/` the Go and Java one and a change clock
# is not per-language; the cost is that a directory named `test` holding something else
# is counted, and it is the same cost the globs carry.
_TEST_DIRS = frozenset({"tests", "test"})
_TEST_GLOBS = ("test_*.py", "*_test.py", "*.test.*", "*.spec.*")

# What makes a changed path a dependency statement. Matched on the BASENAME, so a
# manifest inside a subdirectory of a monorepo counts as one; `requirements*.txt` is a
# glob because the convention names the environment in the file (requirements-dev.txt).
_MANIFESTS = frozenset({
    "Cargo.lock", "Cargo.toml", "Gemfile", "Gemfile.lock", "go.mod", "go.sum",
    "package-lock.json", "package.json", "pnpm-lock.yaml", "poetry.lock",
    "pyproject.toml", "uv.lock", "yarn.lock",
})  # fmt: skip
_MANIFEST_GLOBS = ("requirements*.txt",)

# What subsystems_touched calls the repository root, which is a place a change can
# touch: a commit that edits pyproject.toml alone has touched one thing, and 0 would
# read as a change that touched nothing at all.
_ROOT = "."


def columns(
    payload: Mapping[str, Any],
) -> tuple[float | None, float | None, float | None]:
    """The three cells for one commit, or three Nones. Design 6.12.

    One function for three columns because they have one answer between them: either
    the store holds what this commit changed or it does not, and no rule below can be
    applied to half a path list.

    subsystems_touched counts the distinct FIRST path components, with `.` standing for
    a file at the repository root. test_files_changed counts the paths that are under a
    test directory or named like a test. dependency_delta is 1 when any path is a
    lockfile or a manifest and 0 when none is: design 6.12 calls it a delta and this is
    a flag, because a path list says a dependency statement changed and cannot say
    which way a dependency moved.
    """
    paths = _paths(payload)
    if paths is None:
        return None, None, None
    return (
        len({one.split("/")[0] if "/" in one else _ROOT for one in paths}),
        sum(1 for one in paths if _is_test(one)),
        1 if any(_is_manifest(one) for one in paths) else 0,
    )


def _paths(payload: Mapping[str, Any]) -> list[str] | None:
    """Every path of the commit, or None when the store does not hold all of them.

    None rather than a short list, and the check is cardinality at the boundary: an
    entry that carries no path would otherwise drop out of a comprehension and turn "I
    cannot tell" into a smaller confident answer. `per_file_truncated` is the same
    refusal one level up: a prefix that happens to hold no test file does not mean the
    commit changed none, so a truncated list answers nothing rather than answering low.

    An empty list is an answer, not an absence: a commit that changed no file touched
    no subsystem, no test and no manifest.
    """
    entries = payload.get("per_file")
    if payload.get("per_file_truncated") or not isinstance(entries, list):
        return None
    found = [
        one.get("path") if isinstance(one, dict) else None
        for one in entries  # a non-dict entry is a shape nothing here may guess about
    ]
    return None if any(one is None for one in found) else [str(one) for one in found]


def _is_test(path: str) -> bool:
    parts = path.split("/")
    return bool(_TEST_DIRS.intersection(parts[:-1])) or _matches(parts[-1], _TEST_GLOBS)


def _is_manifest(path: str) -> bool:
    return path.split("/")[-1] in _MANIFESTS or _matches(
        path.split("/")[-1], _MANIFEST_GLOBS
    )


def _matches(name: str, globs: Sequence[str]) -> bool:
    """fnmatchcase, never fnmatch: fnmatch folds case on a case-insensitive filesystem,
    which would make `Test_x.py` a test file on macOS and not on Linux."""
    return any(fnmatchcase(name, glob) for glob in globs)


def unknown_columns(
    names: Sequence[str], rows: Sequence[Sequence[float | None]]
) -> dict[str, str]:
    """Which of the three columns hold an unknown cell, and why, for the cohort.

    On the cohort rather than in the ColumnSpec because a spec carries a coverage WORD
    and has no room for a sentence, and `series build` prints the cohort beside the
    column table: a reader who sees `partial` against subsystems_touched sees here what
    the partial is made of. A column with no unknown cell is absent from the map, so an
    empty map is the frame saying every linked commit carried its paths.
    """
    return {
        name: NO_PATHS
        for name in PATH_COLUMNS
        if any(row[names.index(name)] is None for row in rows)
    }
