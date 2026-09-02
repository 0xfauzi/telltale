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

# The trigger launch.py gives the last snapshot of a capture. It is the boundary: a
# tree it holds and nothing else holds is evidence gathered as the capture ended, so
# spec 12.3's "shortly after" is the honest rung for it.
CAPTURE_END = "capture_end"

# sha256 of no bytes, which is what `git diff HEAD` prints for a clean tree. Named
# because a snapshot carrying it says something specific: the working tree IS its head
# commit, with nothing staged and nothing modified.
_EMPTY_DIFF = repo.sha256(b"")

# One snapshot's claim about the repository: (head, diff_hash, trigger). The trigger is
# in the key because it is what separates a tree seen while the child ran from the one
# seen after it exited, and those are two different rungs.
_States = set[tuple[str, str, str]]


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


def _holders(states: _States, head: str, diff_hash: str) -> set[str]:
    """The triggers of every snapshot that recorded exactly this (head, diff) state."""
    return {trigger for h, d, trigger in states if (h, d) == (head, diff_hash)}


def _tree_match(cwd: str | Path, facts: dict[str, Any], states: _States) -> str | None:
    """The tree-match rung this commit reaches from the snapshots, or None for neither.

    Two shapes of evidence, and both are a tree this recorder photographed itself.

    BEFORE the commit. A snapshot holds head H and diff_hash D, the sha256 of `git diff
    HEAD`. A commit C with the single parent P matches when P equals H and the sha256
    of `git diff P C` equals D. Same base, byte-identical patch, so the same resulting
    tree. Measured, not assumed: with an edit, a rename and a staged new file in the
    tree, the two patches were the same 665 bytes with the same sha256 (git 2.47.1,
    2026-09-01).

    AT the commit. A snapshot whose head IS C and whose diff is empty is the working
    tree at C, taken inside the capture by Telltale. That is a tree match by
    definition and it is what W2-T6 adds: the wave 1 gate capture moved HEAD from
    e2aa6a95 to 3512c938 and ended clean, and the empty-diff snapshot at capture end
    said so while the ladder had no rung that could read it.

    Which rung a match reaches is the trigger of the snapshot that holds the state:
    capture_end is the boundary of the capture, so alone it is tree_match_after, and
    any snapshot taken while the child was running is tree_match_during. `_confidence`
    then weakens it further if the commit itself is later than the capture.

    What it still cannot see. A file the agent never staged is absent from `git diff
    HEAD` and present in the commit, so committing an untracked file does not match
    the first shape; a second commit in one capture is not the tree any later snapshot
    holds, so only the last one matches the second shape. A merge has two parents and
    is never matched by the first shape, and a rebase or an amend moves the parent
    away from every snapshot head, so both reach the ladder only through the tree they
    leave behind. Each miss costs confidence, never a wrong link.
    """
    sha = str(facts["sha"])
    triggers = _holders(states, sha, _EMPTY_DIFF)
    parents = facts["parents"]
    if len(parents) == 1 and any(head == parents[0] for head, _, _ in states):
        patch = repo.git_stdout(cwd, "diff", *repo.DIFF_SAFE, parents[0], sha)
        if patch is not None:
            triggers |= _holders(states, parents[0], repo.sha256(patch))
    if not triggers:
        return None
    return "tree_match_after" if triggers == {CAPTURE_END} else "tree_match_during"


def _confidence(
    facts: dict[str, Any],
    matched: str | None,
    window: tuple[datetime, datetime],
    heuristic: bool,
) -> str | None:
    """The highest rung of the spec 12.3 ladder a commit reaches, or None for no link.

    Temporal proximity alone reaches no rung unless heuristic was asked for, which is
    spec 12.3's "insufficient" made mechanical. Where the snapshot and the clock
    disagree the weaker of the two wins: a snapshot taken during the capture that
    matches a commit made after it is tree_match_after, because the commit is what the
    rung is about.

    A commit dated before the window reaches no rung at all, which is what keeps the
    empty-diff match honest: a capture that starts and ends clean on the same HEAD
    holds a snapshot of the tree at a commit somebody else made yesterday, and nothing
    here may claim it.

    The start of the window is floored to the whole second, and that is a measurement
    rather than a slack allowance. Git records a committer date in whole seconds, so a
    commit dated 16:30:25 was made somewhere in that second; a capture that started at
    16:30:25.129544 would otherwise exclude every commit made in the second it began.
    Measured: `telltale run -- bash -c '... git commit ...'` around a bash child
    finishes in 95 ms, so ALL of its commits fall in that second. What it costs is
    stated: a commit made up to one second before the capture can now reach a rung if a
    snapshot holds its tree.
    """
    start, end = window
    when = repo.parse_ts(facts["committed_ts"])
    if not start.replace(microsecond=0) <= when <= end + _AFTER_WINDOW:
        return None
    if matched is not None:
        return matched if when <= end else "tree_match_after"
    return "heuristic" if heuristic else None


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
    states: _States,
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

    snapshots are the payloads snapshot() returned; head, diff_hash and trigger are
    read. explicit and provider_reported are shas the orchestrator stated and the
    provider reported, kept even outside the window, because a statement outranks a
    clock.

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
    # A snapshot that recorded no trigger is read as the capture_end one, which is the
    # weaker of the two rungs: "I cannot tell when this tree was seen" must not buy the
    # stronger claim.
    states = {
        (str(s["head"]), str(s["diff_hash"]), str(s.get("trigger") or CAPTURE_END))
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
