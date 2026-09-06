"""What is known about a candidate before it is merged: block A, off a git diff.

Split out of forecast/candidate.py when that file reached the 800-line ratchet (W8-T3).
It is the one job in there that the protocol does not touch: no window, no forecaster
and no registry, only git and the path rules the change clock already uses, so the
protocol module can be read without it and this one can be read without the protocol.
The names it defines are re-exported from `telltale.forecast.candidate`, because
`telltale advise` and `telltale forecast candidate` reach them through the protocol and
a split is not a rename.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telltale import repo, series_paths

if TYPE_CHECKING:
    from pathlib import Path


# The three provenance keys `features` returns beside the A block. Not part of the block
# and never covariates: they are what was diffed, so a stored advisory can be re-derived
# rather than trusted.
PROVENANCE = ("base_sha", "head_sha", "merge_base")

# `git diff base...head`. THREE dots, and this is the whole reason the constant exists
# rather than an f-string at the call site: `base..head` is main's drift plus the
# candidate's changes, and a candidate scored on somebody else's commits is not scored
# on anything. Three dots is `merge_base(base, head)..head`, which is the change the
# candidate would bring.
_SYMMETRIC = "..."
# -M so that a rename is one changed file here and one changed file in repo_link's
# diff-tree of the commit it becomes. Without it the same change measures 2 files
# before the merge and 1 after, and the A block would not be the row it predicts.
_DIFF_ARGS = (*repo.DIFF_SAFE, "-M")


class NotACandidate(Exception):
    """A (base, head) pair whose diff is not one candidate's own changes."""


def features(cwd: str | Path, base: str, head: str) -> dict[str, Any]:
    """The A block of a candidate, read off a git diff. Design 6.12's H8, block A.

    The six keys of ABLATION_A, each an int or None, plus the three of PROVENANCE, each
    a sha. Nothing else: this is what anybody deciding a merge can read off the diff,
    which is what makes it legitimate as a past-future covariate at the origin.

    Paths and numstat only. The rejected alternative was to read the file CONTENTS and
    say something about what the change does; that is a different claim class, it puts
    somebody's source into a recorder that promises never to persist diff text (design
    6.3), and none of the six columns needs it.

    A binary file leaves lines_added and lines_removed None and never 0: git prints
    "-\\t-" there because lines are not the unit, and `repo.totals` is where that rule
    already lives. files_changed still counts it.
    """
    base_sha = _rev(cwd, base)
    head_sha = _rev(cwd, head)
    merge_base = _merge_base(cwd, base_sha, head_sha)
    _not_a_merge(cwd, head, head_sha)
    span = f"{base_sha}{_SYMMETRIC}{head_sha}"
    changes = repo.git_numstat(cwd, "diff", *_DIFF_ARGS, span)
    if changes is None:
        raise NotACandidate(
            f"git diff {base}{_SYMMETRIC}{head} refused in {cwd}: there is no diff to"
            " read, and an empty A block is not the same answer as an empty diff"
        )
    added, removed = repo.totals(changes)
    subsystems, tests, dependency = series_paths.columns(
        {"per_file": repo.per_file(changes)}
    )
    return {
        "files_changed": len(changes),
        "lines_added": added,
        "lines_removed": removed,
        "subsystems_touched": _int(subsystems),
        "test_files_changed": _int(tests),
        "dependency_delta": _int(dependency),
        "base_sha": base_sha,
        "head_sha": head_sha,
        "merge_base": merge_base,
    }


def _rev(cwd: str | Path, ref: str) -> str:
    """One ref as a full sha, or the refusal naming the ref git could not resolve."""
    found = repo.git_line(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not found:
        raise NotACandidate(f"{ref}: no such commit in {cwd}")
    return found


def _merge_base(cwd: str | Path, base_sha: str, head_sha: str) -> str:
    """The commit `base...head` diffs from, or the refusal that there is not one.

    Two histories with no common ancestor have no candidate between them: `git diff`
    would still answer, by diffing one whole tree against the other, and every one of
    the six numbers would then be the size of the repository rather than of a change.
    """
    found = repo.git_line(cwd, "merge-base", base_sha, head_sha)
    if not found:
        raise NotACandidate(
            f"{base_sha[:12]} and {head_sha[:12]} share no ancestor, so there is no"
            " base this candidate is a change against"
        )
    return found


def _not_a_merge(cwd: str | Path, ref: str, head_sha: str) -> None:
    """Refuse a head that is a merge. Its own contribution is not one diff.

    repo_link._commit_stats already refuses a merge for this reason on the change clock,
    and the A block has to be the same measurement or a candidate's predicted row and
    its landed row are two different things. The parents are named so the caller can
    pick one and ask again.
    """
    listed = repo.git_line(cwd, "show", "-s", "--format=%P", head_sha)
    parents = (listed or "").split()
    if len(parents) > 1:
        raise NotACandidate(
            f"{ref} ({head_sha[:12]}) is a merge of {len(parents)} parents"
            f" ({', '.join(sha[:12] for sha in parents)}): its own changes are not one"
            " diff, because every per-file number depends on which parent is picked"
        )


def _int(value: float | None) -> int | None:
    """A path column as a whole count. None stays None and is never rounded."""
    return None if value is None else int(value)
