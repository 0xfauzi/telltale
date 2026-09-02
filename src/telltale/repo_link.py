"""Which commits belong to a capture, and how sure we are. Spec 12.3, design 6.8.

Split out of repo.py, which was at the 800-line ratchet. The split follows the
question rather than the mechanism: repo.py observes a repository at one moment and
knows nothing about captures, while everything here reads a capture's window and its
snapshots and answers "did this session make that commit".

The answer is a RUNG, never a boolean. Spec 12.3 orders them explicit,
provider_reported, tree_match_during, tree_match_after, heuristic, and says temporal
proximity alone is insufficient: a commit whose only evidence is its timestamp reaches
no rung at all unless a caller asks for the heuristic one. A miss costs confidence; it
never produces a wrong link.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from telltale import repo

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from telltale.store import Store

# Design 6.8: a commit made within ten minutes after a capture ends is close enough
# to be worth comparing. It bounds the search; on its own it is never a link.
_AFTER_WINDOW = timedelta(seconds=600)

_FACT_FIELDS = ("sha", "parents", "tree", "committed_ts")
_STAT_FIELDS = ("files_changed", "additions", "deletions")


def _commit_facts(cwd: str | Path, sha: str) -> dict[str, Any] | None:
    """sha, parents, tree and commit time for one commit, or None when it is unknown."""
    raw = repo.git_line(cwd, "show", "-s", "--format=%H%x00%P%x00%T%x00%ct", sha)
    if raw is None:
        return None
    full, parents, tree, when = raw.split("\0")
    # %ct is epoch seconds; design 6.2 spells a timestamp ISO 8601 UTC with Z.
    stamp = datetime.fromtimestamp(int(when), tz=UTC).isoformat().replace("+00:00", "Z")
    return {
        "sha": full,
        "parents": parents.split(),
        "tree": tree,
        "committed_ts": stamp,
    }


def _commit_stats(cwd: str | Path, sha: str, parents: list[str]) -> dict[str, Any]:
    """What one commit changed.

    A merge's numbers depend on which parent you pick, so a merge reports unknown
    rather than its diff against the first. --root makes a root commit report its whole
    tree instead of nothing; -M matches the rename detection `git diff` does by
    default, so a rename is one file here and in a snapshot alike.
    """
    unknown = dict.fromkeys(_STAT_FIELDS)
    if len(parents) > 1:
        return unknown
    changes = repo.git_numstat(
        cwd, "diff-tree", *repo.DIFF_SAFE, "-r", "-M", "--no-commit-id", "--root", sha
    )
    if changes is None:
        return unknown
    additions, deletions = repo.totals(changes)
    return {
        "files_changed": len(changes),
        "additions": additions,
        "deletions": deletions,
    }


def _tree_match(
    cwd: str | Path, facts: dict[str, Any], states: set[tuple[str, str]]
) -> bool:
    """Does this commit make exactly the change a snapshot recorded, on the same base?

    The rule, in full. A snapshot holds head H and diff_hash D, the sha256 of `git diff
    HEAD`. A commit C with the single parent P matches when P equals H and the sha256
    of `git diff P C` equals D. Same base, byte-identical patch, so the same resulting
    tree: spec 12.3's "matching tree during capture", from two values a snapshot
    already carries. Measured, not assumed: with an edit, a rename and a staged new
    file in the tree, the two patches were the same 665 bytes with the same sha256
    (git 2.47.1, 2026-09-01).

    What it cannot see. A file the agent never staged is absent from `git diff HEAD`
    and present in the commit, so committing an untracked file does not match. Nor
    does a commit of part of the tree. An amend or a rebase moves the parent away from
    every snapshot head, and a merge has two parents and is never matched. Each falls
    to a lower rung, which is the point of a ladder: a miss costs confidence, never a
    wrong link.
    """
    parents = facts["parents"]
    if len(parents) != 1:
        return False
    parent = parents[0]
    if not any(head == parent for head, _ in states):
        return False
    patch = repo.git_stdout(cwd, "diff", *repo.DIFF_SAFE, parent, facts["sha"])
    return patch is not None and (parent, repo.sha256(patch)) in states


def _confidence(
    facts: dict[str, Any],
    matched: bool,
    window: tuple[datetime, datetime],
    heuristic: bool,
) -> str | None:
    """The highest rung of the spec 12.3 ladder a commit reaches, or None for no link.

    Temporal proximity alone reaches no rung unless heuristic was asked for, which is
    spec 12.3's "insufficient" made mechanical.
    """
    start, end = window
    when = repo.parse_ts(facts["committed_ts"])
    if matched and start <= when <= end:
        return "tree_match_during"
    if matched and end < when <= end + _AFTER_WINDOW:
        return "tree_match_after"
    if heuristic and start <= when <= end + _AFTER_WINDOW:
        return "heuristic"
    return None


def _unresolved(sha: str, rung: str) -> dict[str, Any]:
    """A sha somebody named that this repository does not contain.

    Returned rather than dropped: the claim was made, and losing it silently would hide
    that it was wrong. Every field but the sha and the rung is unknown, because it is.
    """
    return {
        **dict.fromkeys(_FACT_FIELDS),
        **dict.fromkeys(_STAT_FIELDS),
        "sha": sha,
        "link_confidence": rung,
    }


def _link(
    cwd: str | Path,
    sha: str,
    stated: Mapping[str, str],
    states: set[tuple[str, str]],
    window: tuple[datetime, datetime],
    heuristic: bool,
) -> dict[str, Any] | None:
    """One telltale.repo.commit payload, or None when this commit reaches no rung."""
    facts = _commit_facts(cwd, sha)
    if facts is None:
        return _unresolved(sha, stated[sha])
    rung = stated.get(facts["sha"]) or stated.get(sha)
    if rung is None:
        rung = _confidence(facts, _tree_match(cwd, facts, states), window, heuristic)
    if rung is None:
        return None
    return {
        **facts,
        **_commit_stats(cwd, facts["sha"], facts["parents"]),
        "link_confidence": rung,
    }


def _candidates(
    cwd: str | Path, start: datetime, stated: Mapping[str, str]
) -> list[str]:
    """Commits worth examining: everything on a local ref since start, plus the stated.

    Local refs only: a `git fetch` during a capture brings other people's commits into
    remote-tracking refs, and heuristic linking would make them candidates. Git's own
    limit applies too, in that --since prunes the walk by commit date, so a commit
    dated before start hides the commits behind it.
    """
    listed = repo.git_stdout(
        cwd, "rev-list", f"--since={start.isoformat()}", "--branches", "--tags", "HEAD"
    )
    seen = [] if listed is None else listed.decode("utf-8", "replace").split()
    return list(dict.fromkeys([*seen, *stated]))


def commits_since(
    cwd: str | Path,
    since_ts: str,
    snapshots: Iterable[Mapping[str, Any]],
    provider_reported: Iterable[str] = (),
    explicit: Iterable[str] = (),
    *,
    until_ts: str | None = None,
    heuristic: bool = False,
) -> list[dict[str, Any]]:
    """Commits linked to a capture, one telltale.repo.commit payload each (spec 12.3).

    since_ts and until_ts are ISO 8601 timestamps carrying an offset. until_ts
    defaults to now, right at capture end and wrong for a re-link days later, so
    `sessions --link-commits` passes the capture's real end.

    snapshots are the payloads snapshot() returned; only head and diff_hash are read.
    explicit and provider_reported are shas the orchestrator stated and the provider
    reported, kept even outside the window, because a statement outranks a clock.

    heuristic must be asked for. Without it a commit whose only evidence is its
    timestamp is not returned at all, because "inside the window" is the one thing
    spec 12.3 says is never a link on its own.
    """
    start = repo.parse_ts(since_ts)
    end = repo.parse_ts(until_ts) if until_ts else datetime.now(UTC)
    if end < start:
        raise ValueError(f"capture ends before it starts: {since_ts!r} to {until_ts!r}")
    # explicit is applied second, so it overwrites provider_reported for one sha.
    stated = dict.fromkeys(provider_reported, "provider_reported")
    stated.update(dict.fromkeys(explicit, "explicit"))
    states = {
        (str(s["head"]), str(s["diff_hash"]))
        for s in snapshots
        if s["head"] is not None and s["diff_hash"] is not None
    }
    linked = (
        _link(cwd, sha, stated, states, (start, end), heuristic)
        for sha in _candidates(cwd, start, stated)
    )
    return [row for row in linked if row is not None]


def reported_commits(store: Store, capture_id: str) -> list[str]:
    """Every git_commit_id a provider reported inside this capture, in arrival order.

    Read back out of the store rather than counted on the way in, because the field
    arrives on a provider's own records (design 6.3: a git_commit_id lifted out of
    tool_parameters) and the launcher never sees one. The caller flushes first.
    """
    seen = [
        str(row["payload"]["git_commit_id"])
        for row in store.observations(capture_id)
        if row["payload"].get("git_commit_id")
    ]
    return list(dict.fromkeys(seen))
