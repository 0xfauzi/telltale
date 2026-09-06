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

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import repo, series_paths

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from telltale.store import Store

# Design 6.8: a commit made within ten minutes after a capture ends is close enough
# to be worth comparing. It bounds the search; on its own it is never a link.
_AFTER_WINDOW = timedelta(seconds=600)

_FACT_FIELDS = ("sha", "parents", "tree", "committed_ts")
# What _commit_stats answers. The three path cells and the version of the rule that
# produced them are stat fields like the counts, and not a separate group: they are
# folded from the same numstat in the same call, and a commit whose diff is unknown has
# all seven unknown together.
_STAT_FIELDS = (
    "files_changed",
    "additions",
    "deletions",
    "per_file",
    *series_paths.PATH_COLUMNS,
    "path_rules_version",
)


def _path_rules_version() -> str:
    """A short hash of series_paths.py's source, stored beside the cells it produced.

    The idiom of correlate.REDUCER_VERSION and series.REDUCER_VERSION: a hash of the
    rule's source rather than a number somebody remembers to bump, so an edit to the
    test-path or manifest rule is visible on every row recorded after it. Short because
    this is a field on every commit payload rather than one string per series, and
    because the allowlist cleans it as a Kind.ENUM, which sanitize bounds at 64
    characters.

    Truncated to 12 hex characters, which is a collision claim this file has to own:
    the question the field answers is "were these cells folded by the rule I am reading
    now", so what it must survive is the number of DISTINCT versions of one small module
    a store ever holds, not an adversary. 48 bits against the tens of versions a
    repository's history has is not a number worth measuring further.
    """
    try:
        source = Path(series_paths.__file__).read_bytes()
    except OSError:
        return "paths-source-unavailable"
    return f"paths-{hashlib.sha256(source).hexdigest()[:12]}"


PATH_RULES_VERSION = _path_rules_version()

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


def _commit_stats(
    cwd: str | Path,
    sha: str,
    parents: list[str],
    *,
    first_parent: bool = False,
) -> dict[str, Any]:
    """What one commit changed: the three counts, and the paths behind them.

    A merge's numbers depend on which parent you pick, so by default a merge reports
    unknown rather than its diff against the first. That covers per_file too: a path
    list picked from one parent's diff is the same invented answer as a line count
    picked from it. --root makes a root commit report its whole tree instead of nothing;
    -M matches the rename detection `git diff` does by default, so a rename is one file
    here and in a snapshot alike.

    `first_parent` says the CALLER already picked the parent, and only a caller walking
    `git log --first-parent` may say so. The two cases differ because the question
    differs. On that walk a merge is one row of a branch's history and the reader is
    asking what the branch took when the merge landed, which is exactly `diff-tree
    parents[0] sha` and is no more a pick than the walk itself was. A merge inside a
    launcher capture - a `git merge main` into a task branch, say - was reached with no
    parent chosen: nobody said which side of it the row is about, so it stays unknown
    (W8-T4). The default is the honest answer for a caller that did not choose.

    per_file comes from the numstat this function already reads, so the paths cost no
    second git call and no patch body. W3-T4 added it because the change clock's
    subsystems_touched, test_files_changed and dependency_delta are all questions about
    paths, and a count of files answers none of them. files_changed stays the true
    count whatever the launcher's payload bound does to the list.

    W8-T5 makes those three cells the same kind of survivor. They are folded HERE, from
    the whole numstat, before any caller fits the payload to design 6.4's 8 KB bound, so
    a commit whose list is later cut to a prefix still reports what its paths said. That
    is what the bound cost before: measured on the owner's repositories after W8-T4, the
    only remaining unknowns in those three columns were 5 deckgen rows and 4 kstrl rows
    whose per_file the bound had truncated, and a `partial` covariate is excluded by
    name from every forecast variant. The rule is series_paths, applied to a payload
    holding just the list, so this module owns no path rule of its own; the version of
    that rule goes on the row beside the cells it produced.
    """
    unknown = dict.fromkeys(_STAT_FIELDS)
    merge = len(parents) > 1
    if merge and not first_parent:
        return unknown
    # A merge names both endpoints, so --root has nothing to do and is left off; a
    # single-parent or root commit names one revision and --root is what makes the root
    # report its whole tree.
    walk = [parents[0], sha] if merge else ["--root", sha]
    changes = repo.git_numstat(
        cwd, "diff-tree", *repo.DIFF_SAFE, "-r", "-M", "--no-commit-id", *walk
    )
    if changes is None:
        return unknown
    additions, deletions = repo.totals(changes)
    entries = repo.per_file(changes)
    cells = series_paths.columns({"per_file": entries})
    return {
        "files_changed": len(changes),
        "additions": additions,
        "deletions": deletions,
        "per_file": entries,
        **dict(zip(series_paths.PATH_COLUMNS, cells, strict=True)),
        "path_rules_version": PATH_RULES_VERSION,
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


def _resolved(cwd: str | Path, stated: Mapping[str, str]) -> dict[str, str]:
    """Stated shas, keyed by the full sha this repository resolves each one to.

    A provider reports an ABBREVIATED sha. Measured on 2026-09-06, Claude Code 2.1.263,
    capture cap_b0be0e0da48a5a1a78bdb8fa: `gitOperation.commit.sha` was "0881c21",
    seven characters. git resolves an abbreviation, so `_commit_facts` was already
    returning the full commit for one; what it could not do is stop `_candidates` from
    holding both spellings of a single commit and `_link` from answering twice, once at
    the stated rung and once at whatever rung the tree match reached. Measured before
    this function existed: one commit, one snapshot and one short sha gave two
    `telltale.repo.commit` payloads with the same full sha, one tree_match_during and
    one provider_reported. Duplicate is not one, so the abbreviation is resolved here,
    at the one place in the call that holds the repository.

    `^{commit}` refuses a tag or a tree that resolves to the right object type by
    accident, and a sha this repository does not contain keeps its own spelling so that
    `_unresolved` still reports the claim somebody made about it.
    """
    out: dict[str, str] = {}
    for sha, rung in stated.items():
        args = ("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
        out[repo.git_line(cwd, *args) or sha] = rung
    return out


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
    stated = _resolved(cwd, stated)
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
