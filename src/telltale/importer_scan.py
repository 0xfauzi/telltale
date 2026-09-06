"""What is on the disk: the session files a backfill would read, surveyed and counted.

The half of the importer that writes nothing. `importer.py` is the half that writes, and
the cut is the one the owner decision of 2026-09-01 already draws: a dry run reports the
counts, the owner sees them, and only then does an import append anything. Splitting on
that line keeps the shapes and the reading primitives on the side that cannot write.

Two rules shape this file.

  A file is streamed, never loaded. The owner's ~/.claude/projects is 1.7 GB over 1806
  files and ~/.codex/sessions is 2.9 GB over 1452 (measured 2026-09-02), so every read
  here is line by line. Measured over the six largest transcripts, 233 MB: 0.07 s to
  count the lines and 0.31 s to json.loads every one of them, which is about 2 s for
  the whole tree and is why `_survey` parses rather than pattern-matching.

  A slug is not a path. `~/.claude/projects/<slug>/` encodes the project directory it
  came from; nothing here reconstructs it, stores it or prints it. What is stored is
  the sha256 of the file's path, and what is grouped and printed is the sha256 of the
  slug.

`loads`, `text` and `timestamp` are public because `importer.py` reads the same files
with them. A second spelling of "which lines parse" would be a second answer to the one
question both halves have to agree on: what line 400 of this file is.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from telltale.store import Store


@dataclass(frozen=True)
class Kind:
    """One backfill source: which provider wrote it, where, and what a file is called.

    `surface` is the provider's own parse() surface name, so the importer never learns
    a second vocabulary for the same thing.
    """

    name: str
    provider: str
    surface: str
    root: str
    glob: str
    grouping: str  # what a dry run counts by: "project" or "day"


KINDS: dict[str, Kind] = {
    "claude-transcripts": Kind(
        name="claude-transcripts",
        provider="claude",
        surface="transcript",
        root="~/.claude/projects",
        glob="*.jsonl",
        grouping="project",
    ),
    "codex-rollouts": Kind(
        name="codex-rollouts",
        provider="codex",
        surface="rollout",
        root="~/.codex/sessions",
        glob="rollout-*.jsonl",
        grouping="day",
    ),
}


@dataclass(frozen=True)
class Source:
    """One file on disk, surveyed. The unit both halves of this module work in."""

    path: Path
    bytes: int
    lines: int
    session_id: str | None
    first_ts: str | None
    last_ts: str | None
    readable: bool
    kind: str
    group: str
    # The file's own name inside a session, set only when a session was written to more
    # than one file. See `_part`: it is what separates a subagent's transcript from its
    # parent's, and it is not a provider identifier, so it never leaves this module
    # except inside the capture id.
    part: str | None = None
    reason: str | None = None
    cwd: str | None = None

    @property
    def capture_id(self) -> str:
        """`imp_<24 hex>` of provider and session, so a second import is a no-op."""
        session = self.session_id or str(self.path)
        if self.part is not None:
            session = f"{session}/{self.part}"
        return capture_id(KINDS[self.kind].provider, session)


def capture_id(provider: str, session: str) -> str:
    digest = hashlib.sha256(f"{provider}:{session}".encode()).hexdigest()
    return f"imp_{digest[:24]}"


def path_hash(path: Path) -> str:
    return hashlib.sha256(str(path).encode()).hexdigest()


def default_root(kind: str) -> Path:
    return Path(KINDS[kind].root).expanduser()


# -- scanning -------------------------------------------------------------------------
def scan(
    root: Path,
    kind: str,
    since: str | None = None,
    project: str | None = None,
) -> Iterator[Source]:
    """Every file under `root` this importer would read, surveyed but not imported.

    `since` compares the file's FIRST PROVIDER timestamp, not its mtime: a transcript
    is appended to whenever the session is resumed, so mtime is the last time the owner
    touched it and says nothing about when the session happened.
    """
    cutoff = _cutoff(since)
    settings = KINDS[kind]
    for path in _files(root, settings.glob):
        if project is not None and _slug(path, root) != project:
            continue
        source = _survey(path, root, settings)
        # A file with no provider timestamp at all is excluded by --since rather than
        # kept: the flag asks for sessions after a date, and this one names no date.
        if cutoff is not None and (source.first_ts or "")[:10] < cutoff:
            continue
        yield source


def _cutoff(since: str | None) -> str | None:
    """`since` as YYYY-MM-DD, or ValueError. A date: a session is a day's work."""
    if since is None:
        return None
    return date.fromisoformat(since).isoformat()


def _files(root: Path, pattern: str) -> Iterator[Path]:
    """Every matching file under root, following no symlink out of it.

    os.walk does not descend into a symlinked directory unless it is told to, and a
    symlinked FILE is skipped here: either could point outside the root the owner
    named, and this command reads whatever it is pointed at.
    """
    base = root.expanduser().resolve()
    for parent, directories, names in os.walk(base, followlinks=False):
        directories[:] = [
            name for name in directories if not Path(parent, name).is_symlink()
        ]
        for name in sorted(names):
            path = Path(parent, name)
            if path.is_symlink() or not path.match(pattern):
                continue
            yield path


def _slug(path: Path, root: Path) -> str:
    """The first directory component under root, which is the project directory name.

    Returned for `--project` to compare against and for nothing else: it encodes the
    absolute path of the project it came from, so `group()` hashes it before anybody
    sees it.
    """
    parts = path.relative_to(root.expanduser().resolve()).parts
    return parts[0] if len(parts) > 1 else ""


def _group(path: Path, root: Path, grouping: str) -> str:
    """What a dry run counts by: a hashed project, or the day the rollout was written.

    A day is a date and safe to print. A slug is the project's path with the separators
    changed, so it is hashed: the owner can still see that one project holds 210 files
    without the report naming the directory.
    """
    if grouping == "day":
        parts = path.relative_to(root.expanduser().resolve()).parts
        return "/".join(parts[:3]) if len(parts) > 3 else "(undated)"
    slug = _slug(path, root)
    return f"proj_{hashlib.sha256(slug.encode()).hexdigest()[:8]}" if slug else "(root)"


def _survey(path: Path, root: Path, settings: Kind) -> Source:
    """One pass over a file: how big it is, when it ran, and whose session it was.

    Every line is json.loads'd. Measured: 0.31 s over 233 MB, so about 2 s for the
    owner's whole transcript tree, which is cheaper than a rule for guessing which
    lines are worth parsing and wrong in a way nobody would notice.
    """
    lines = 0
    parsed = 0
    size = 0
    session: str | None = None
    cwd: str | None = None
    first: str | None = None
    last: str | None = None
    try:
        size = path.stat().st_size
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                lines += 1
                record = loads(line)
                if record is None:
                    continue
                parsed += 1
                stamp = timestamp(record)
                if stamp is not None:
                    first = first or stamp
                    last = stamp
                session = session or _session_of(record, settings.provider)
                cwd = cwd or _cwd_of(record, settings.provider)
    except OSError as error:
        return _unreadable(path, root, settings, f"{type(error).__name__}: {error}")
    if session is None:
        return _unreadable(
            path,
            root,
            settings,
            "no session id in any line" if parsed else "no line parsed as JSON",
            lines=lines,
            size=size,
        )
    return Source(
        path=path,
        bytes=size,
        lines=lines,
        session_id=session,
        part=_part(session, path),
        first_ts=first,
        last_ts=last,
        readable=True,
        kind=settings.name,
        group=_group(path, root, settings.grouping),
        cwd=cwd,
    )


def _unreadable(
    path: Path, root: Path, settings: Kind, reason: str, lines: int = 0, size: int = 0
) -> Source:
    return Source(
        path=path,
        bytes=size,
        lines=lines,
        session_id=None,
        first_ts=None,
        last_ts=None,
        readable=False,
        kind=settings.name,
        group=_group(path, root, settings.grouping),
        reason=reason,
    )


def _part(session: str, path: Path) -> str | None:
    """Which part of a session this file is, when a session was written to several.

    A file whose name ends in the session id is the session's own file and has no part.
    Both providers write files that are not, and each was measured on the owner's disk.

    Claude: a subagent file (`<slug>/<session>/subagents/agent-<id>.jsonl`) carries the
    PARENT session's sessionId on every line, every line has isSidechain true, and it
    shares no uuid with the parent file. 852 of 1817 files.

    Codex: a resumed thread writes a NEW rollout file whose name carries a new uuid
    while session_meta still names the original session. 27 session ids over 50 files.

    They become one capture each, and the file name is what separates them. One capture
    holding several would sum a subagent's tokens into the main thread's total, which
    spec 13.6 forbids and which nothing downstream separates; and skipping the second
    file as already imported would lose it silently, which is worse. The link is kept:
    every row of both carries the same provider_session_id.
    """
    return None if path.stem.endswith(session) else path.stem


def loads(line: str) -> Mapping[str, Any] | None:
    """One line as a JSON OBJECT, or None. Both halves of the importer read with it."""
    try:
        record = json.loads(line)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def timestamp(record: Mapping[str, Any]) -> str | None:
    value = record.get("timestamp")
    return value if isinstance(value, str) and value else None


def _session_of(record: Mapping[str, Any], provider: str) -> str | None:
    """The provider's own session id, from wherever that provider puts it.

    Codex spells it `id` on the session_meta line and `session_id` on some versions
    (measured: 25 of 25 files carry `id`, 1 of 25 carries both), so both are read.
    """
    if provider == "claude":
        return text(record.get("sessionId"))
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    return text(payload.get("session_id")) or text(payload.get("id"))


def _cwd_of(record: Mapping[str, Any], provider: str) -> str | None:
    if provider == "claude":
        return text(record.get("cwd"))
    payload = record.get("payload")
    return text(payload.get("cwd")) if isinstance(payload, dict) else None


def text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


# -- the dry run ----------------------------------------------------------------------
def dry_run(
    root: Path,
    kind: str,
    since: str | None = None,
    project: str | None = None,
    store: Store | None = None,
) -> dict[str, Any]:
    """What an import would read, counted. Writes nothing, anywhere.

    `already_imported` is None rather than 0 when there is no database yet: "no capture
    of this session is stored" and "there is nowhere to look" are different answers,
    and this command must not create the database to find out.
    """
    stored = _stored_captures(store)
    counted = _Counts()
    for source in scan(root, kind, since, project):
        counted.add(source, stored)
    return counted.as_dict(kind, root, since, project, stored is not None)


def _stored_captures(store: Store | None) -> set[str] | None:
    if store is None or not Path(store.path).exists():
        return None
    return {str(row["capture_id"]) for row in store.captures()}


@dataclass
class _Counts:
    """The running totals of one dry run, in the shape the CLI prints."""

    files: int = 0
    bytes: int = 0
    lines: int = 0
    sessions: set[str] = field(default_factory=set)
    imported: set[str] = field(default_factory=set)
    first_ts: str | None = None
    last_ts: str | None = None
    unreadable: list[dict[str, Any]] = field(default_factory=list)
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, source: Source, stored: set[str] | None) -> None:
        self.files += 1
        self.bytes += source.bytes
        self.lines += source.lines
        if not source.readable:
            self.unreadable.append({"file": path_hash(source.path)[:12],
                                    "reason": source.reason})  # fmt: skip
            return
        self.sessions.add(source.capture_id)
        if stored is not None and source.capture_id in stored:
            self.imported.add(source.capture_id)
        if source.first_ts:
            self.first_ts = min(self.first_ts or source.first_ts, source.first_ts)
        if source.last_ts:
            self.last_ts = max(self.last_ts or source.last_ts, source.last_ts)
        row = self.groups.setdefault(
            source.group, {"group": source.group, "files": 0, "lines": 0, "bytes": 0}
        )
        row["files"] += 1
        row["lines"] += source.lines
        row["bytes"] += source.bytes

    def as_dict(
        self,
        kind: str,
        root: Path,
        since: str | None,
        project: str | None,
        checked: bool,
    ) -> dict[str, Any]:
        return {
            "kind": kind,
            "root": str(root),
            "since": since,
            "project": project,
            "files": self.files,
            "sessions": len(self.sessions),
            "bytes": self.bytes,
            "lines": self.lines,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "already_imported": len(self.imported) if checked else None,
            "unreadable": self.unreadable,
            "groups": sorted(
                self.groups.values(), key=lambda row: (-int(row["files"]), row["group"])
            ),
        }
