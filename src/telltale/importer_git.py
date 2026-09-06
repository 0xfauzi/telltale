"""A repository's own history as change rows, with no capture behind them.

The change clock (design 6.12) is built from `telltale.repo.commit` observations, and
until now the only writer of one was the launcher: a commit reached the clock because a
capture was linked to it. E11 measured the cost. This build's store held 54 linked
commits and got 0 forecastable windows on every target, because the rule needs 36 rows
in one regime and no repository here has 36 CAPTURED commits. The rows exist anyway:
they are in git. This module walks a repository's first-parent history and writes one
change row per commit, with two outcomes read off evidence already on disk or already on
GitHub - the commit's own diff, for design 6.12's delayed rework label, and the commit's
GitHub check runs, for the mechanical verification.

Three rules shape it.

  `link_confidence` is a NEW word, `unlinked`. Spec 12.3's ladder ranks how sure we are
  that a capture made a commit, and these commits were read out of git with no capture
  in the question. `heuristic`, the neighbouring word, means a link somebody attempted
  on weak evidence. `unlinked` says none was ever attempted, by construction.

  A repository's history GROWS, so this is the one importer whose capture is appended
  to. Every other kind skips a stored capture whole (importer.import_files); here the
  shas already recorded are read back and only the new commits are appended. Each
  appending run writes its own capture_started/capture_ended bracket, and every count on
  that bracket is that run's own: a number scoped to the run that measured it cannot go
  stale, and re-stating a total nobody recounted would be inventing one.

  No content leaves this module. A commit message is never read; a diff line is read in,
  compared with another and dropped. What is stored is what `repo_link` already stores
  for the launcher: shas, counts and paths.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from telltale import importer, repo, repo_link
from telltale.launch import PER_FILE_MAX
from telltale.model import Observation, now_iso, to_json, ulid
from telltale.sanitize import MAX_PAYLOAD_BYTES, Ctx, sanitize

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.store import Store

KIND = "git-history"
PROVIDER = "git"
SURFACE = "git_history"
ADAPTER = "telltale.import.git@1"

COMMIT_TYPE = "telltale.repo.commit"
OUTCOME_TYPE = "external.outcome"
STARTED_TYPE = "telltale.capture_started"
ENDED_TYPE = "telltale.capture_ended"
IDENTITY_TYPE = "telltale.repo.identity"

# Spec 12.3's ladder answers "did this capture make that commit". A commit read out of
# git was never a candidate for it, so it reaches no rung. series_lineage.LADDER does
# not carry this word yet: that is W8-T2's half of this wave, and until it lands these
# rows are invisible to the change clock.
UNLINKED = "unlinked"

# Design 6.12's delayed label, and the same constant as series_outcomes.REWORK_TAIL: a
# commit is labelled once three more have landed after it.
REWORK_WINDOW = 3

# A line of three characters or fewer is not evidence that a change was undone: `}` and
# `);` appear in and disappear from every commit of every repository. The bound is the
# orchestrator's, pre-registered with the rule it belongs to.
MIN_LINE = 3

VERIFICATION_KIND = "mechanical_verification"
REWORK_KIND = "revert_or_repair"
CHECKS_SYSTEM = "github:check-runs"
OVERLAP_SYSTEM = "git:line-overlap"
REWORKED = "reworked"

# `gh api` reaches the network, so its cap is wall-clock and much longer than the 5 s
# git gets (repo._TIMEOUT_S). Measured over ten commits here on 2026-09-06: 345 ms
# minimum, 410 ms median, 432 ms maximum. A 157-commit history is about a minute of
# network, which is why `--no-checks` exists and why the dry run prints the call count.
CHECKS_TIMEOUT_S = 30.0

# One page, compared against the API's own total_count. `gh api --paginate` on an object
# endpoint concatenates JSON objects into something no parser reads, and a silently
# truncated list would turn "one of these failed" into "all of these passed". The
# largest repository the orchestrator measured runs 18 check runs per commit.
CHECKS_PAGE = 100

# GitHub's conclusion vocabulary, split into the two statements it makes. A word in
# neither set decides nothing: a run still going carries a null conclusion, and `stale`
# says the answer was thrown away. Neither is a pass and neither is a fail.
PASS_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
FAIL_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
)

_GH_ENV = {"GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat", "NO_COLOR": "1"}


class Refused(ValueError):
    """The import cannot run, and the message says which condition it hit."""


@dataclass(frozen=True)
class Commit:
    """One first-parent commit. committed_ts is repo_link's own `%ct`."""

    sha: str
    committed_ts: str


@dataclass(frozen=True)
class _Diff:
    """One commit's diff against its first parent, as line sets per file.

    Two dicts because the rule is asymmetric: a commit's ADDED lines are what a later
    commit can undo, and its REMOVED lines are what does the undoing.
    """

    added: dict[str, set[str]]
    removed: dict[str, set[str]]


@dataclass
class _Counts:
    """What one import run did, in the shape the CLI prints and the end row carries."""

    commits_found: int = 0
    commits_new: int = 0
    observations: int = 0
    diagnostics: int = 0
    verifications: int = 0
    reworks: int = 0
    no_check_runs: int = 0
    check_run_incomplete: int = 0
    checks_fetched: int = 0
    conclusions_unread: dict[str, int] = field(default_factory=dict)


# -- reading the repository -----------------------------------------------------------
def resolve(repo_path: str | Path) -> tuple[str, str, dict[str, Any]]:
    """(working tree root, repo_id, identity payload), or Refused naming what is wrong.

    repo_id is `repo.identity`'s: the sha256 of the ROOT COMMIT, not of the remote.
    Measured 2026-09-06 - this worktree, a fresh `git clone` of it and the main checkout
    all report d9f783b1d318c0d6... while their remote fingerprints differ, so one
    history lands in one capture however it was cloned.
    """
    root = check(repo_path)
    found = repo.identity(root)
    repo_id = found.get("repo_id")
    if not isinstance(repo_id, str) or not repo_id:
        raise Refused(f"{root} has no repo_id: git named no common directory for it")
    return root, repo_id, found


def check(repo_path: str | Path) -> str:
    """The working tree root, or Refused. Two git calls and no database.

    Run by the CLI BEFORE it opens the store, so a refusal leaves no database behind.
    """
    root = repo.git_root(repo_path)
    if root is None:
        raise Refused(f"{repo_path} is not inside a git working tree")
    if repo.git_line(root, "rev-parse", "--quiet", "--verify", "HEAD") is None:
        raise Refused(f"{root} has no commits, so there is no history to import")
    return root


def capture_of(repo_id: str) -> str:
    """The one capture id this repository's history is imported into, forever."""
    return importer.capture_id(PROVIDER, repo_id)


def history(root: str, branch: str | None, since: str | None) -> list[Commit]:
    """Every first-parent commit of the branch, oldest first.

    `--first-parent` is what makes this a list of CHANGES rather than of commits: on a
    merge workflow the second parent holds a branch's own history, which landed here as
    one row. `since` is git's own, which PRUNES the walk by commit date.
    """
    args = ["log", "--first-parent", "--format=%H%x00%ct"]
    if since is not None:
        args.append(f"--since={date.fromisoformat(since).isoformat()}")
    args.append(branch if branch is not None else "HEAD")
    listed = repo.git_stdout(root, *args)
    if listed is None:
        named = branch if branch is not None else "HEAD"
        raise Refused(f"git refused to walk {named!r} in {root}")
    found = [_commit(line) for line in listed.decode("utf-8", "replace").splitlines()]
    return [one for one in reversed(found) if one is not None]


def _commit(line: str) -> Commit | None:
    sha, _, when = line.partition("\0")
    if not sha or not when.isdigit():
        return None
    return Commit(sha=sha, committed_ts=_stamp(int(when)))


def _stamp(epoch: int) -> str:
    """Epoch seconds as ISO 8601 UTC with an offset: design 6.2's spelling."""
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")


# -- the rework label -----------------------------------------------------------------
def _diff_of(root: str, sha: str) -> _Diff | None:
    """One commit's diff against its first parent, as line sets. None when git refused.

    `--unified=0` because a context line is evidence of nothing; `--format=` drops the
    commit message, which this module never reads. errors="replace" because a diff is
    not text: a commit in the owner's chopin checkout carries a non-UTF-8 byte at offset
    80691, and the orchestrator's first scan of it died there.
    """
    out = repo.git_stdout(
        root, "show", "--first-parent", "--format=", "--unified=0", *repo.DIFF_SAFE, sha
    )
    if out is None:
        return None
    return _parse_diff(out.decode("utf-8", "replace"))


@dataclass
class _Sides:
    """Which file the reader is inside, and whether it has reached that file's hunks."""

    old: str | None = None
    new: str | None = None
    in_hunk: bool = False

    def header(self, line: str) -> bool:
        """Read one line of diff metadata. True when it was metadata, not a change.

        `---` and `+++` are headers only between `diff --git` and the first hunk.
        Inside a hunk they are content: a diff of a diff holds lines beginning `++ `,
        which at column 0 behind their own marker read exactly like a header.
        """
        if line.startswith("diff --git "):
            self.old, self.new, self.in_hunk = None, None, False
            return True
        if line.startswith("@@"):
            self.in_hunk = True
            return True
        if self.in_hunk:
            return False
        if line.startswith("--- "):
            self.old = _header_path(line[4:])
        elif line.startswith("+++ "):
            self.new = _header_path(line[4:])
        return True


def _parse_diff(text: str) -> _Diff:
    """A unified diff as added and removed line sets, keyed by the file each belongs to.

    Added lines are keyed by the `+++` path and removed lines by the `---` path: one
    path for a file that was not renamed, the right pair for one that was. A binary file
    contributes nothing, because git prints one sentence there and no lines.
    """
    added: dict[str, set[str]] = {}
    removed: dict[str, set[str]] = {}
    sides = _Sides()
    for line in text.splitlines():
        if sides.header(line):
            continue
        if line.startswith("+"):
            _keep(added, sides.new, line[1:])
        elif line.startswith("-"):
            _keep(removed, sides.old, line[1:])
    return _Diff(added=added, removed=removed)


def _header_path(text: str) -> str | None:
    """The path in a `--- a/x` or `+++ b/x` header, or None for /dev/null."""
    if text == "/dev/null":
        return None
    return text[2:] if text[:2] in ("a/", "b/") else text


def _keep(into: dict[str, set[str]], path: str | None, body: str) -> None:
    """One diff line under its file, if it is long enough to be evidence of anything."""
    stripped = body.strip()
    if path is None or len(stripped) <= MIN_LINE:
        return
    into.setdefault(path, set()).add(stripped)


def _overlaps(earlier: _Diff, later: _Diff) -> bool:
    """Whether the later commit removed, in one file, a line the earlier one added."""
    return any(
        lines & later.removed.get(path, frozenset())
        for path, lines in earlier.added.items()
    )


class _Reworks:
    """The rework rule over one first-parent walk, with a four-commit diff cache.

    Every diff is read once and dropped as soon as the last commit it could have undone
    is decided: without the cache each is read four times, and without the eviction one
    lockfile commit is held for the whole walk.
    """

    def __init__(self, root: str, commits: Sequence[Commit]) -> None:
        self.root = root
        self.commits = commits
        self.cache: dict[int, _Diff | None] = {}

    def decide(self, index: int) -> Commit | None:
        """The first of the next three commits that reworked this one, or None.

        None covers both "none of the three did" and "there are not three yet"; the
        caller tells them apart with `decidable`, because the second is not a 0.
        """
        mine = self._diff(index)
        if mine is None:
            return None
        last = min(index + 1 + REWORK_WINDOW, len(self.commits))
        for ahead in range(index + 1, last):
            later = self._diff(ahead)
            if later is not None and _overlaps(mine, later):
                return self.commits[ahead]
        return None

    def decidable(self, index: int) -> bool:
        """Whether three more commits have landed, so the window has closed."""
        return index + REWORK_WINDOW < len(self.commits)

    def done_through(self, index: int) -> None:
        """Drop the diffs no later decision can need."""
        for key in [key for key in self.cache if key <= index]:
            del self.cache[key]

    def _diff(self, index: int) -> _Diff | None:
        if index not in self.cache:
            self.cache[index] = _diff_of(self.root, self.commits[index].sha)
        return self.cache[index]


# -- GitHub check runs ----------------------------------------------------------------
def github_slug(root: str) -> str | None:
    """`owner/repo` when origin is a GitHub URL, None otherwise.

    Read here to build one API path and never stored: an outcome carries the words, the
    sha and the suite id, and no address.
    """
    remote = repo.git_line(root, "remote", "get-url", "origin")
    if not remote:
        return None
    text = remote.removesuffix(".git")
    if text.startswith("git@"):
        host, _, path = text.partition(":")
        return path if host.endswith("github.com") else None
    parsed = urlsplit(text)
    if (parsed.hostname or "") != "github.com":
        return None
    trimmed = parsed.path.strip("/")
    return trimmed if trimmed.count("/") == 1 else None


def checks_available(root: str) -> str | None:
    """The slug to ask about, or None when no check run can be fetched at all."""
    return None if shutil.which("gh") is None else github_slug(root)


def _fetch_checks(slug: str, sha: str) -> list[Mapping[str, Any]]:
    """The commit's check runs. Raises Refused with the reason nothing could be read.

    One process per commit, no shell, a 30 s cap. A non-zero exit, a timeout, a body
    that is not JSON and a page short of the API's own total_count are one refusal each.
    """
    route = f"repos/{slug}/commits/{sha}/check-runs?per_page={CHECKS_PAGE}"
    try:
        done = subprocess.run(
            ["gh", "api", route],
            capture_output=True,
            timeout=CHECKS_TIMEOUT_S,
            check=False,
            env={**os.environ, **_GH_ENV},
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Refused(f"gh api failed: {type(error).__name__}") from error
    if done.returncode != 0:
        raise Refused(f"gh api exited {done.returncode}")
    try:
        body = json.loads(done.stdout.decode("utf-8", "replace"))
    except ValueError as error:
        raise Refused("gh api answered something that is not JSON") from error
    runs = body.get("check_runs") if isinstance(body, dict) else None
    total = body.get("total_count") if isinstance(body, dict) else None
    if not isinstance(runs, list) or not isinstance(total, int):
        raise Refused("gh api answered a body with no check_runs list and total_count")
    if len(runs) != total:
        raise Refused(f"gh api returned {len(runs)} of {total} check runs")
    return [run for run in runs if isinstance(run, dict)]


def _verdict(runs: Sequence[Mapping[str, Any]]) -> tuple[str | None, str | None]:
    """(status, the conclusion word that could not be read), for one commit's runs.

    A fail outranks everything and a pass needs EVERY run to say so. Anything else is
    neither, and the word comes back so the caller can name it.
    """
    words = [str(run.get("conclusion") or "") for run in runs]
    if any(word in FAIL_CONCLUSIONS for word in words):
        return "fail", None
    if all(word in PASS_CONCLUSIONS for word in words):
        return "pass", None
    unread = next(word for word in words if word not in PASS_CONCLUSIONS)
    return None, unread or "(no conclusion)"


def _span(runs: Sequence[Mapping[str, Any]]) -> tuple[int, str] | None:
    """(whole ms from the first start to the last finish, the last finish), or None.

    None when any run has not stated both ends: the timestamps that ARE there would
    time whichever part happened to have finished.
    """
    try:
        starts = [repo.parse_ts(str(run["started_at"])) for run in runs]
        ends = [repo.parse_ts(str(run["completed_at"])) for run in runs]
    except (KeyError, TypeError, ValueError):
        return None
    last = max(ends)
    span = round((last - min(starts)).total_seconds() * 1000)
    return span, _stamp(int(last.timestamp()))


def _suite(runs: Sequence[Mapping[str, Any]], sha: str) -> str:
    """The check suite the runs belong to, or the sha when they do not agree on one.

    Duplicate is not one: picking either of two suite ids would attribute the outcome
    to a suite that produced half of it.
    """
    ids = {
        str(run["check_suite"]["id"])
        for run in runs
        if isinstance(run.get("check_suite"), dict)
        and run["check_suite"].get("id") is not None
    }
    return ids.pop() if len(ids) == 1 else sha


# -- writing --------------------------------------------------------------------------
class _Writer:
    """One capture's append path: sanitize, batch, and count what reached the store."""

    def __init__(self, store: Store, capture_id: str, repo_id: str, root: str,
                 level: int) -> None:  # fmt: skip
        self.store = store
        self.capture_id = capture_id
        self.repo_id = repo_id
        self.level = level
        self.ctx = Ctx(repo_root=Path(root).resolve())
        self.batch: list[Observation] = []
        self.written = 0
        self.diagnostics = 0

    def emit(
        self,
        obs_type: str,
        payload: Mapping[str, Any],
        ts: str | None,
        surface: str,
        provider: str = PROVIDER,
        adapter: str = ADAPTER,
    ) -> None:
        body, redaction, unknown = sanitize(
            obs_type, dict(payload), self.level, self.ctx
        )
        self.batch.append(
            Observation(
                observation_id=ulid(),
                capture_id=self.capture_id,
                observation_type=obs_type,
                surface=surface,
                provider=provider,
                adapter=adapter,
                ingest_ts=now_iso(),
                provider_ts=ts,
                repo_id=self.repo_id,
                payload=body,
                redaction=redaction,
            )
        )
        if unknown:
            self.diagnose("unknown_field", f"{obs_type}: {' '.join(sorted(unknown))}")
        if len(self.batch) >= importer.BATCH:
            self.drain()

    def outcome(self, payload: Mapping[str, Any], stamp: str) -> None:
        """One external.outcome, attributed the way `telltale outcome` attributes one.

        provider_ts is the outcome's own timestamp, not the import's: series_outcomes
        compares an activity's position against the rework window's deadline.
        """
        self.emit(OUTCOME_TYPE, payload, stamp, "external", "external", "external@1")

    def diagnose(self, kind: str, detail: str) -> None:
        self.store.diagnose(kind, detail, capture_id=self.capture_id)
        self.diagnostics += 1

    def drain(self) -> None:
        self.written += self.store.append(self.batch)
        self.batch.clear()


def _capped(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Cut per_file until the payload fits, rather than letting it be dropped whole.

    The twin of launch._capped, on W0-T2's measurement: design 6.4's 8 KB bound drops
    the LARGEST FIELD whole, which on a commit is per_file, so a 300-file commit would
    store none of it. PER_FILE_MAX is imported so it cannot drift.

    per_file is the only field the cut touches, and after W8-T5 that is what makes the
    cut cheap. `files_changed` was already the true count whatever happened to the list;
    `subsystems_touched`, `test_files_changed`, `dependency_delta` and
    `path_rules_version` are now beside it, folded by repo_link._commit_stats from the
    WHOLE list before this function ever saw the payload, and they pass through
    untouched. What a truncated list costs is now the list, not the columns built from
    it: measured after W8-T4, the bound had left those three columns unknown on 5 of
    deckgen's 118 rows and 4 of kstrl's 252, which was enough to mark each column
    `partial` and drop it by name from every forecast variant of E16.
    """
    entries = payload.get("per_file")
    if not isinstance(entries, list):
        return dict(payload)
    kept = list(entries[:PER_FILE_MAX])
    while True:
        out = {**payload, "per_file": kept}
        if len(kept) < len(entries):
            out["per_file_truncated"] = True
        if not kept or len(to_json(out).encode("utf-8")) <= MAX_PAYLOAD_BYTES:
            return out
        kept.pop()


def commit_payload(root: str, commit: Commit) -> dict[str, Any] | None:
    """One telltale.repo.commit payload at the `unlinked` rung, or None when git
    refuses.

    Built by repo_link's own two readers, so it carries the same thirteen fields in the
    same shapes as a linked commit. The rung is the only difference.

    `first_parent=True` because `history` walked `--first-parent`: the parent was picked
    by the walk before this function ran, so a merge here reports the diff the branch
    took when it landed rather than unknown. Without it the A block went unknown on
    every merge of a merge-based workflow, which measured 79 of 118 rows on deckgen and
    100 of 252 on kstrl (W8-T4), and took subsystems_touched, test_files_changed and
    dependency_delta with it.
    """
    facts = repo_link._commit_facts(root, commit.sha)
    if facts is None:
        return None
    stats = repo_link._commit_stats(
        root, str(facts["sha"]), list(facts["parents"]), first_parent=True
    )
    return _capped({**facts, **stats, "link_confidence": UNLINKED})


def _stored(store: Store, capture_id: str) -> tuple[set[str], set[tuple[str, str]]]:
    """(shas already recorded, the (kind, component_id) pairs already outcome'd).

    Both in one query. The second is what makes the delayed label safe to re-evaluate.
    A database that is not there answers with two empty sets, and the dry run then
    reports `already_recorded` as None rather than 0.
    """
    if not Path(store.path).exists():
        return set(), set()
    rows = store.observations(capture_id, (COMMIT_TYPE, OUTCOME_TYPE))
    shas = {
        str(row["payload"]["sha"])
        for row in rows
        if row["observation_type"] == COMMIT_TYPE and row["payload"].get("sha")
    }
    outcomes = {
        (str(row["payload"].get("kind")), str(row["payload"].get("component_id")))
        for row in rows
        if row["observation_type"] == OUTCOME_TYPE
    }
    return shas, outcomes


def dry_run(
    store: Store,
    repo_path: str | Path,
    *,
    branch: str | None = None,
    since: str | None = None,
    checks: bool = True,
) -> dict[str, Any]:
    """What an import would read, counted. Opens no database and creates none."""
    root, repo_id, _identity = resolve(repo_path)
    capture_id = capture_of(repo_id)
    commits = history(root, branch, since)
    known = Path(store.path).exists()
    shas = _stored(store, capture_id)[0]
    new = [one for one in commits if one.sha not in shas]
    slug = checks_available(root) if checks else None
    return {
        "root": root,
        "repo_id": repo_id,
        "capture_id": capture_id,
        "branch": branch,
        "since": since,
        "commits_found": len(commits),
        "commits_new": len(new),
        "already_recorded": len(shas) if known else None,
        "check_runs_to_fetch": len(new) if slug else 0,
        "checks": None if not checks else slug,
        "first_ts": commits[0].committed_ts if commits else None,
        "last_ts": commits[-1].committed_ts if commits else None,
    }


def import_history(
    store: Store,
    repo_path: str | Path,
    *,
    branch: str | None = None,
    since: str | None = None,
    level: int = 1,
    checks: bool = True,
) -> dict[str, Any]:
    """A repository's first-parent history into its own capture. Returns what it wrote.

    A run that finds nothing new writes nothing at all, not even a lifecycle row.
    """
    root, repo_id, identity = resolve(repo_path)
    capture_id = capture_of(repo_id)
    commits = history(root, branch, since)
    shas, outcomes = _stored(store, capture_id)
    counted = _Counts(commits_found=len(commits))
    todo = _todo(commits, shas)
    if not todo:
        return _result(capture_id, counted, root)
    writer = _Writer(store, capture_id, repo_id, root, level)
    slug = checks_available(root) if checks else None
    writer.emit(STARTED_TYPE, _started(level), commits[0].committed_ts, "import")
    writer.emit(IDENTITY_TYPE, identity, commits[0].committed_ts, "import")
    _walk(writer, root, commits, todo, shas, outcomes, slug, counted)
    writer.emit(ENDED_TYPE, _ended(counted), commits[-1].committed_ts, "import")
    writer.drain()
    store.flush()
    store.rebuild(capture_id)
    counted.observations = writer.written
    counted.diagnostics = writer.diagnostics
    return _result(capture_id, counted, root)


def _todo(commits: Sequence[Commit], shas: set[str]) -> list[int]:
    """The indexes this run has work for: every new commit, and the last three old ones.

    The last three, because a commit recorded while it was the tip had no window yet.
    """
    new = [index for index, one in enumerate(commits) if one.sha not in shas]
    if not new:
        return []
    recorded = [index for index, one in enumerate(commits) if one.sha in shas]
    return sorted(set(new) | set(recorded[-REWORK_WINDOW:]))


def _walk(
    writer: _Writer,
    root: str,
    commits: Sequence[Commit],
    todo: Sequence[int],
    shas: set[str],
    outcomes: set[tuple[str, str]],
    slug: str | None,
    counted: _Counts,
) -> None:
    """Every commit this run has work for, in first-parent order, oldest first."""
    reworks = _Reworks(root, commits)
    for index in todo:
        commit = commits[index]
        fresh = commit.sha not in shas
        payload = commit_payload(root, commit) if fresh else None
        if payload is not None:
            writer.emit(COMMIT_TYPE, payload, commit.committed_ts, SURFACE)
            counted.commits_new += 1
            if slug is not None:
                _write_verification(writer, slug, commit, counted)
        elif fresh:
            writer.diagnose("parse_failure", f"git says nothing about {commit.sha}")
        if (REWORK_KIND, commit.sha) not in outcomes:
            _write_rework(writer, reworks, index, commit, counted)
        reworks.done_through(index)
    _report_conclusions(writer, counted)


def _write_verification(
    writer: _Writer, slug: str, commit: Commit, counted: _Counts
) -> None:
    """One mechanical_verification outcome from the commit's check runs, or none.

    Four ways to get none, each counted, never turned into a status: no check runs, an
    unfinished run, an unreadable conclusion, an unreadable `gh`.
    """
    counted.checks_fetched += 1
    try:
        runs = _fetch_checks(slug, commit.sha)
    except Refused as refused:
        writer.diagnose("parse_failure", f"check runs for {commit.sha}: {refused}")
        return
    if not runs:
        counted.no_check_runs += 1
        return
    span = _span(runs)
    status, unread = _verdict(runs)
    if span is None or status is None:
        counted.check_run_incomplete += 1
        if unread is not None:
            counted.conclusions_unread[unread] = (
                counted.conclusions_unread.get(unread, 0) + 1
            )
        return
    duration_ms, finished = span
    writer.outcome(
        {
            "kind": VERIFICATION_KIND,
            "status": status,
            "external_system": CHECKS_SYSTEM,
            "external_run_id": _suite(runs, commit.sha),
            "component_id": commit.sha,
            "duration_ms": duration_ms,
            "timestamp": finished,
        },
        finished,
    )
    counted.verifications += 1


def _write_rework(
    writer: _Writer, reworks: _Reworks, index: int, commit: Commit, counted: _Counts
) -> None:
    """One revert_or_repair outcome when a later commit undid this one, or none.

    Nothing is written when the window has not closed: nobody knows yet, and that is
    not the statement 0 makes.
    """
    if not reworks.decidable(index):
        return
    later = reworks.decide(index)
    if later is None:
        return
    writer.outcome(
        {
            "kind": REWORK_KIND,
            "status": REWORKED,
            "external_system": OVERLAP_SYSTEM,
            "external_run_id": later.sha,
            "component_id": commit.sha,
            "timestamp": later.committed_ts,
        },
        later.committed_ts,
    )
    counted.reworks += 1


def _report_conclusions(writer: _Writer, counted: _Counts) -> None:
    """One diagnostic naming every check-run conclusion this reader could not read.

    A count with no names is a rumour: the row says which word, and how often.
    """
    if not counted.conclusions_unread:
        return
    listed = " ".join(
        f"{word}: {n}" for word, n in sorted(counted.conclusions_unread.items())
    )
    writer.diagnose("dropped", f"check run conclusions neither pass nor fail: {listed}")


def _started(level: int) -> dict[str, Any]:
    """The capture_started payload. There was no launch, so argv_shape is `backfill`.

    `--branch` and `--since` are not on it: capture_started has no field for either.
    """
    return {
        "provider": PROVIDER,
        "argv_shape": "backfill",
        "content_level": level,
        "surfaces_configured": [SURFACE],
        "source_kind": KIND,
    }


def _ended(counted: _Counts) -> dict[str, Any]:
    """The capture_ended payload. Every count on it is THIS RUN's own.

    exit_code and duration_ms are None because there was no process. The two check-run
    counters are absent rather than 0 on a run that fetched none: "none were missing"
    and "nobody looked" are different.
    """
    return {
        "exit_code": None,
        "duration_ms": None,
        "surfaces_received": {SURFACE: counted.commits_new},
        "no_check_runs": counted.no_check_runs if counted.checks_fetched else None,
        "check_run_incomplete": (
            counted.check_run_incomplete if counted.checks_fetched else None
        ),
    }


def _result(capture_id: str, counted: _Counts, root: str) -> dict[str, Any]:
    """What the CLI prints: where it read, and every counter this run kept."""
    return {"capture_id": capture_id, "root": root, **asdict(counted)}
