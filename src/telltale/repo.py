"""What the repository was, and what changed in it while an agent ran.

Four observers, one payload dict each, in the field names of design 6.3: identity at
capture start, a debounced diff snapshot while the agent works, and the overlap marker
for two captures on one worktree at once. The launcher wraps a payload into an
Observation; nothing here imports the model, opens the store or writes anything
anywhere. Linking a commit to a capture reads these payloads and lives in repo_link.py.

The git primitives below are public because that module runs the same git calls: one
argv builder, one timeout and one environment, rather than two.

Every value that leaves a patch is a count or a sha256. The body is read into this
process and dropped: design 6.3 lists diff text among the things never persisted.

Git is reached with an argv list and a 5 s timeout, never a shell. A branch name, a
remote URL and a path in a repository under observation are text somebody else wrote,
and `shell=True` would make each of them a command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

# Design 6.8 fixes the timeout. It is a wall-clock cap on a child process, not a budget:
# git answers in single-digit milliseconds on a healthy repository (measured 4.6 to
# 5.8 ms for status, ls-files and diff here on a clean tree, 2026-09-01), so a call
# that reaches 5 s has hit a lock, a network remote or a filesystem that is stuck.
_TIMEOUT_S = 5.0

# LC_ALL=C: git localizes "Binary files a/x and b/y differ", and that sentence sits
#   inside the bytes that become diff_hash. Without it the hash depends on the
#   operator's locale.
# GIT_OPTIONAL_LOCKS=0: `status` would otherwise take the index lock to write a
#   refreshed index. This process runs beside an agent that is running git itself,
#   and a lock taken here is a lock the agent waits for.
# GIT_TERMINAL_PROMPT=0: no git call here may stop and ask a human for a credential.
_GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}

# --no-ext-diff and --no-textconv on every diff: both are configured inside the
# repository under observation (.gitattributes, .git/config) and both name a program
# for git to run.
# A recorder must not execute what it is recording.
DIFF_SAFE = ("--no-ext-diff", "--no-textconv")

# Tried in order for base_sha. origin/HEAD is the only one that is a statement by the
# remote; the rest are conventions, and a repository that uses neither name gets None.
_DEFAULT_BRANCH_REFS = ("origin/main", "origin/master", "main", "master")

# Named once because it is written twice: as real values, and as dict.fromkeys(...)
# where the answer is unknown. These are the snapshot fields that need a HEAD to diff
# against.
_DIFF_FIELDS = (
    "diff_hash",
    "files_changed",
    "additions",
    "deletions",
    "renames",
    "per_file",
)


class _Change(NamedTuple):
    """One record of `git diff --numstat -z`. Paths stay bytes, as git gave them."""

    # additions and deletions are None for a binary file: git prints "-" there, and
    # means that lines are not the unit, rather than that no lines changed.
    additions: int | None
    deletions: int | None
    old_path: bytes | None  # set only for a rename
    path: bytes


class _Window(NamedTuple):
    """One capture's claim on one worktree. end None means it is still running."""

    place: tuple[str, str]  # repo_id and worktree_id: see _window on why both
    capture_id: str
    start: datetime
    end: datetime | None


def _git(cwd: str | Path, *args: str | bytes) -> tuple[int, bytes]:
    """Run one git command and return its exit code and stdout bytes.

    stderr is captured and dropped. Callers read the exit code; git's message would
    otherwise reach the recorded agent's stderr, which capture may never change.
    """
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        timeout=_TIMEOUT_S,
        check=False,
        env={**os.environ, **_GIT_ENV},
    )
    return completed.returncode, completed.stdout


def git_stdout(cwd: str | Path, *args: str | bytes) -> bytes | None:
    """stdout bytes, or None when git refused. None is "cannot tell", never "empty"."""
    code, out = _git(cwd, *args)
    return None if code != 0 else out


def git_line(cwd: str | Path, *args: str | bytes) -> str | None:
    """Stripped stdout, or None when git refused.

    The exit code is what decides. `git rev-parse HEAD` in a repository with no commits
    prints the string "HEAD" on stdout and exits 128, so reading stdout alone would
    record the word HEAD as a commit sha.
    """
    out = git_stdout(cwd, *args)
    return None if out is None else out.decode("utf-8", "replace").strip()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_ts(value: str) -> datetime:
    """Parse an ISO 8601 timestamp that carries an offset; refuse one that does not.

    A naive timestamp has no meaning a comparison can use, and picking a zone for it
    would be inventing the answer to the question the caller is asking.
    """
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"timestamp needs a UTC offset: {value!r}")
    return moment.astimezone(UTC)


def git_root(cwd: str | Path) -> str | None:
    """Absolute path of the working tree root, or None when cwd is not inside one."""
    return git_line(cwd, "rev-parse", "--show-toplevel")


def _take(data: bytes, pos: int) -> tuple[bytes | None, int]:
    """One NUL-terminated field at pos, or (None, pos) when it is unterminated."""
    end = data.find(b"\0", pos)
    if end == -1:
        return None, pos
    return data[pos:end], end + 1


def _record(data: bytes, pos: int) -> tuple[_Change | None, int]:
    """One numstat record starting at pos, or (None, pos) where the block ends.

    Measured shape (git 2.47.1): "adds\\tdels\\tpath\\0", and for a rename
    "adds\\tdels\\t\\0old\\0new\\0". With --patch, one empty record closes the block and
    the patch follows; with --numstat alone the data simply ends. A count of "-" is
    None: git means that lines are not this file's unit, not that none changed.
    """
    chunk, after = _take(data, pos)
    if not chunk:
        return None, after
    fields = chunk.split(b"\t", 2)
    if len(fields) != 3:
        return None, pos
    counts = [None if field == b"-" else int(field) for field in fields[:2]]
    if fields[2] != b"":
        return _Change(counts[0], counts[1], None, fields[2]), after
    old_path, after = _take(data, after)
    path, after = _take(data, after)
    if old_path is None or path is None:
        return None, pos
    return _Change(counts[0], counts[1], old_path, path), after


def _parse_numstat(data: bytes) -> tuple[list[_Change], bytes]:
    """Split `--numstat [--patch] -z` output into records and the patch that follows.

    The NUL form is used rather than the human one because it is the only form in
    which a path with a space, a quote or a newline comes back as git holds it.
    """
    changes: list[_Change] = []
    pos = 0
    while pos < len(data):
        change, after = _record(data, pos)
        if change is None:
            return changes, data[after:]
        changes.append(change)
        pos = after
    return changes, b""


def _patch_sections(patch: bytes) -> list[bytes]:
    """Cut a patch into one section per file, splitting only at a header line start.

    Every line inside a unified diff body starts with a space, a plus, a minus or a
    backslash, so "diff --git " at column 0 is always a header and never content. The
    sections concatenate back to the input exactly, which is what makes one section's
    hash a hash of that file's share of the whole-diff hash beside it.
    """
    if not patch:
        return []
    # Section 0 starts at byte 0 whatever is there. Git always opens with a header,
    # and _per_file's count check turns any other shape into unknown, not a wrong hash.
    starts = [0]
    found = patch.find(b"\ndiff --git ")
    while found != -1:
        starts.append(found + 1)
        found = patch.find(b"\ndiff --git ", found + 1)
    bounds = [*starts, len(patch)]
    return [patch[bounds[i] : bounds[i + 1]] for i in range(len(starts))]


def _diff_state(
    cwd: str | Path, head: str | None
) -> tuple[bytes, list[_Change], bytes]:
    """The working tree against HEAD, from ONE git call: numstat bytes, records, patch.

    One call rather than two because the agent is editing the tree as this runs: two
    calls are two moments, and the counts would describe a state the patch does not.
    With no HEAD there is nothing to diff against, so all three come back empty and
    every field derived from them is reported unknown rather than zero.
    """
    if head is None:
        return b"", [], b""
    out = git_stdout(cwd, "diff", *DIFF_SAFE, "HEAD", "--numstat", "--patch", "-z")
    if out is None:
        return b"", [], b""
    changes, patch = _parse_numstat(out)
    return out[: len(out) - len(patch)], changes, patch


def _dirty_tree_hash(
    head: str | None, numstat: bytes, status: bytes | None
) -> str | None:
    """sha256 of the numstat block and the porcelain status, NUL-separated.

    Design 6.8 names the human-readable `git diff HEAD --numstat` and `git status
    --porcelain`; this hashes their -z forms, the same content with paths unquoted.
    Deliberate: two fewer git calls, no quoting rules inside the hash, and identity()
    and snapshot() hash the same bytes for one state, which is what the field is for.
    The separator stops a byte moving across the boundary leaving the hash equal.

    With no HEAD the numstat half does not exist, and empty bytes would give an empty
    repository the hash of a clean checkout: "nothing to compare against" and "nothing
    differs" are the two statements this system exists to keep apart. No numstat can
    equal the marker, because a record always carries tabs and a NUL.
    """
    if status is None:
        return None
    measured = b"<no-head>" if head is None else numstat
    return sha256(measured + b"\0" + status)


def _repo_id(cwd: str | Path, common_dir: str) -> str:
    """sha256 of the root commit sha, falling back to the common git dir path.

    The root commit is the same object in every clone and worktree, which is what makes
    this identity portable where an absolute path is not (spec 12.1). Two real limits:
    several root commits means the smallest sha is used, so a clone holding a different
    set of refs can disagree, and a shallow clone's boundary commit is parentless, so
    shallow and full clones do not share a repo_id. With no commits there is nothing
    portable to hash and the local path is used, which says as much as can be said.
    """
    roots = git_line(cwd, "rev-list", "--max-parents=0", "HEAD")
    if roots:
        return sha256(min(roots.split()).encode())
    return sha256(common_dir.encode())


def _base_sha(cwd: str | Path) -> str | None:
    """merge-base of HEAD with the default branch, or None when it does not resolve."""
    remote_head = git_line(
        cwd, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"
    )
    for ref in [*([remote_head] if remote_head else []), *_DEFAULT_BRANCH_REFS]:
        base = git_line(cwd, "merge-base", "HEAD", ref)
        if base:
            return base
    return None


def identity(cwd: str | Path = ".") -> dict[str, Any]:
    """Repository identity at capture start, as a telltale.repo.identity payload.

    Every field is None when cwd is not inside a working tree, and head and base_sha
    are None in a repository with no commits: an unborn HEAD is a state git answers
    128 to, not a state to guess at.
    """
    dirs = git_line(
        cwd, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"
    )
    # Two lines when cwd is inside a working tree, nothing at all when it is not. Every
    # other field below reaches None on its own, through the git call that refused.
    git_dir, common_dir = dirs.splitlines() if dirs else (None, None)
    head = git_line(cwd, "rev-parse", "HEAD")
    root = git_root(cwd)
    remote = git_line(cwd, "remote", "get-url", "origin")
    numstat, _changes, _patch = _diff_state(cwd, head)
    outside = git_dir is None or common_dir is None
    return {
        "repo_id": None if common_dir is None else _repo_id(cwd, common_dir),
        # The git dir relative to the common dir: "." in the main worktree and
        # "worktrees/<name>" in a linked one. Relative, so two clones of one repository
        # at different absolute paths still agree.
        "worktree_id": None
        if outside
        else sha256(os.path.relpath(str(git_dir), str(common_dir)).encode()),
        # The checkout's absolute path, hashed rather than stored: spec 12.1 says the
        # path is not the identity, and a hash still answers "the same checkout?".
        "root_hash": None if root is None else sha256(root.encode()),
        "head": head,
        # symbolic-ref rather than `rev-parse --abbrev-ref`, which prints the literal
        # "HEAD" when detached. Here a detached HEAD is None, and a branch with no
        # commits yet still has its name.
        "branch": git_line(cwd, "symbolic-ref", "--quiet", "--short", "HEAD"),
        "base_sha": _base_sha(cwd),
        # The URL as configured, so an https and an ssh remote for one repository
        # fingerprint differently. It identifies the address, not the project.
        "remote_fingerprint": None if remote is None else sha256(remote.encode()),
        "dirty_tree_hash": _dirty_tree_hash(
            head, numstat, git_stdout(cwd, "status", "--porcelain", "-z")
        ),
    }


def git_numstat(cwd: str | Path, *args: str) -> list[_Change] | None:
    """One numstat view, parsed. None when git refused.

    --numstat and -z are appended here so no caller can leave -z off. Without it git
    separates records with newlines, the parser finds no NUL and stops, and the answer
    is a confident empty list: measured, a three-file commit reported files_changed 0.
    """
    out = git_stdout(cwd, *args, "--numstat", "-z")
    return None if out is None else _parse_numstat(out)[0]


def totals(changes: list[_Change]) -> tuple[int | None, int | None]:
    """Added and deleted line counts, or None when any changed file has no line counts.

    A binary file's numstat is "-\\t-": not zero lines, but a file where the question
    does not apply. Summing the rest would print a confident number that silently
    excludes a file, so the totals go unknown and per_file keeps every known count.
    The rejected alternative was to sum the text files and say so nowhere.
    """
    additions = [change.additions for change in changes]
    deletions = [change.deletions for change in changes]
    if None in additions or None in deletions:
        return None, None
    return sum(a or 0 for a in additions), sum(d or 0 for d in deletions)


def _per_file(changes: list[_Change], sections: list[bytes]) -> list[dict[str, Any]]:
    """One entry per changed file: repo-relative path, counts, and its patch's sha256.

    Records and sections come from one git call in one diffcore ordering, so they line
    up by index; when the counts disagree that assumption is broken and every
    patch_hash goes None rather than being attached to the wrong file. Paths decode
    with replacement, so a path that is not UTF-8 is still valid text in the payload:
    nothing downstream opens a file, and the hashes come from the bytes git gave.
    """
    aligned = len(sections) == len(changes)
    return [
        {
            "path": change.path.decode("utf-8", "replace"),
            "additions": change.additions,
            "deletions": change.deletions,
            "patch_hash": sha256(sections[index]) if aligned else None,
        }
        for index, change in enumerate(changes)
    ]


def _diff_fields(
    head: str | None, changes: list[_Change], patch: bytes
) -> dict[str, Any]:
    """The half of a snapshot that only exists when there is a HEAD to diff against."""
    if head is None:
        return dict.fromkeys(_DIFF_FIELDS)
    additions, deletions = totals(changes)
    return {
        # sha256 of exactly the bytes `git diff HEAD` prints. A binary file contributes
        # its "Binary files a/x and b/y differ" line: the hash is of the diff, whatever
        # the diff says, which is what makes it comparable with a commit's diff later.
        "diff_hash": sha256(patch),
        "files_changed": len(changes),
        "additions": additions,
        "deletions": deletions,
        "renames": sum(1 for change in changes if change.old_path is not None),
        "per_file": _per_file(changes, _patch_sections(patch)),
    }


def snapshot(cwd: str | Path, trigger: str) -> dict[str, Any]:
    """A debounced diff snapshot, as a telltale.repo.snapshot payload (spec 12.2).

    trigger is the caller's word for what asked for it (a file mutation, capture end),
    recorded as given so a snapshot can be read back to its cause.

    files_changed, additions, deletions, renames, diff_hash and per_file describe the
    tree against HEAD and are None when HEAD is unborn. staged_files, unstaged_files
    and untracked_count need no HEAD and are measured regardless; untracked_count
    counts files rather than directories, and excludes ignored files.
    """
    head = git_line(cwd, "rev-parse", "HEAD")
    numstat, changes, patch = _diff_state(cwd, head)
    untracked = git_stdout(cwd, "ls-files", "--others", "--exclude-standard", "-z")
    staged = git_numstat(cwd, "diff", *DIFF_SAFE, "--cached")
    unstaged = git_numstat(cwd, "diff", *DIFF_SAFE)
    return {
        "trigger": trigger,
        "head": head,
        "dirty_tree_hash": _dirty_tree_hash(
            head, numstat, git_stdout(cwd, "status", "--porcelain", "-z")
        ),
        **_diff_fields(head, changes, patch),
        "staged_files": None if staged is None else len(staged),
        "unstaged_files": None if unstaged is None else len(unstaged),
        "untracked_count": None
        if untracked is None
        else sum(1 for p in untracked.split(b"\0") if p),
    }


class Debouncer:
    """Turn a burst of triggers into one call of fn, once the burst stops.

    Spec 12.2 asks for snapshots outside hooks and debounced: an agent writing six
    files in a second is one piece of work, and six `git diff` runs of a moving tree
    describe six states that never mattered. Each trigger() restarts the clock; fn runs
    once, on a timer thread, after `seconds` of quiet. flush() runs a pending call at
    once, on the caller's thread, for shutdown. The argument order is (fn, seconds)
    because Python cannot put a defaulted parameter first.

    fn takes the reason of the LAST trigger of the burst. A burst is one call, so one
    of its reasons has to be the one recorded, and the last is the one nearest in time
    to the state fn is about to read. An exception it raises on the timer thread is kept
    in
    last_error rather than reaching stderr, where threading would print it into the
    stream the recorded agent is writing to, and capture may never change the child's
    output (AGENTS.md invariant 8). flush() lets it propagate to Telltale's own
    shutdown path, which can record it.

    Not solved here: a trigger every second forever never falls quiet, so fn never
    runs. A maximum delay would fix it and is not built, because nothing has yet
    measured how a real session's edits are spaced.
    """

    def __init__(self, fn: Callable[[str], None], seconds: float = 2.0) -> None:
        self._fn = fn
        self._seconds = seconds
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._pending: str | None = None
        self.last_error: BaseException | None = None

    def trigger(self, reason: str) -> None:
        """Ask for a call of fn(reason) once the triggers stop for `seconds`."""
        with self._lock:
            self._pending = reason
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._seconds, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _claim(self) -> str | None:
        """Take the pending call if there is one. One burst of triggers, one fn call."""
        with self._lock:
            reason, self._pending = self._pending, None
            if reason is None:
                return None
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            return reason

    def _fire(self) -> None:
        reason = self._claim()
        if reason is None:
            return
        try:
            self._fn(reason)
        except BaseException as error:
            self.last_error = error

    def flush(self) -> None:
        """Run a pending call now, on this thread. Safe to call when none is pending."""
        reason = self._claim()
        if reason is not None:
            self._fn(reason)


def _window(capture: Mapping[str, Any]) -> _Window:
    """Read one capture's window. Subscripts, not .get(): a missing key raises here.

    A key that is silently absent turns "I cannot tell whether these overlapped" into
    "they did not", the substitution this system treats as its defect class.

    repo_id is read as well as worktree_id, a deviation from the brief worth saying out
    loud. worktree_id is the sha256 of the git dir relative to the common dir (design
    6.8), so EVERY main worktree of EVERY repository shares one, the hash of ".":
    measured, a fresh `git init` and a clone of this repository both give
    cdb4ee2aea69cc6a8. Grouping on it alone would call two captures in unrelated
    repositories concurrent on one worktree, which is an invented attribution in the
    one function whose job is to refuse to invent attribution (spec 12.4).
    """
    return _Window(
        (str(capture["repo_id"]), str(capture["worktree_id"])),
        str(capture["capture_id"]),
        parse_ts(capture["started_at"]),
        None if capture["ended_at"] is None else parse_ts(capture["ended_at"]),
    )


def _concurrent(left: _Window, right: _Window) -> bool:
    """Two captures on one worktree whose windows intersect. Touching ends do not."""
    left_open = left.end is None or right.start < left.end
    right_open = right.end is None or left.start < right.end
    return left.place == right.place and left_open and right_open


def overlaps(captures: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """Pairs of captures that were touching one worktree at the same time (spec 12.4).

    Each capture is a mapping with capture_id, repo_id, worktree_id, started_at and
    ended_at; ended_at None means still running, and an open capture overlaps anything
    that had not finished before it began. Both ids are required: see _window. The
    caller marks the repo_snapshot activities of a returned pair ambiguous; this
    function invents no attribution, which is all spec 12.4 asks for.
    """
    windows = [_window(capture) for capture in captures]
    pairs = {
        (left.capture_id, right.capture_id)
        if left.capture_id <= right.capture_id
        else (right.capture_id, left.capture_id)
        for index, left in enumerate(windows)
        for right in windows[index + 1 :]
        if _concurrent(left, right)
    }
    return sorted(pairs)


def _write(obj: object) -> None:
    """stdout, without print: ruff T20 keeps print in cli.py and report.py alone."""
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def _demo_edit(clone: Path) -> str | None:
    """Append a line to the first tracked text file, so there is exactly one change."""
    listed = git_stdout(clone, "ls-files", "-z")
    if listed is None:
        return None
    for raw in listed.split(b"\0"):
        name = raw.decode("utf-8", "replace")
        if name.endswith((".md", ".py", ".toml", ".txt", ".yaml")):
            with (clone / name).open("a", encoding="utf-8") as handle:
                handle.write("telltale demo edit\n")
            return name
    return None


def _demo(path: str) -> int:
    """Show identity and a snapshot, inside a throwaway clone of `path`.

    The clone is the point: an observer that writes into the repository it observes is
    a different tool. The edit is made there, reverted there, and the clone deleted.
    """
    source = str(Path(path).resolve())
    tmp = tempfile.mkdtemp(prefix="telltale-repo-demo-")
    try:
        clone = Path(tmp) / "clone"
        code, _ = _git(tmp, "clone", "--quiet", "--no-hardlinks", source, str(clone))
        if code != 0:
            sys.stderr.write(f"could not clone {source}\n")
            return 1
        _write({"step": "identity", "payload": identity(clone)})
        edited = _demo_edit(clone)
        if edited is None:
            sys.stderr.write("no tracked text file to edit: identity only\n")
            return 0
        _write({"step": "snapshot after edit", "payload": snapshot(clone, "demo_edit")})
        _git(clone, "checkout", "--", edited)
        _write({"step": "identity after revert", "payload": identity(clone)})
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="telltale.repo", description=__doc__)
    parser.add_argument("--demo", action="store_true", help="identity and a snapshot")
    parser.add_argument("path", nargs="?", default=".", help="a repository")
    args = parser.parse_args(argv)
    if not args.demo:
        parser.print_help()
        return 2
    return _demo(args.path)


if __name__ == "__main__":
    sys.exit(main())
