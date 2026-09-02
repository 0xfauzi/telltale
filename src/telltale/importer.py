"""Backfill: the session files the provider already wrote, read through the same gates.

Design 6.3 calls `claude.transcript.*` and `codex.rollout.*` backfill surfaces, and the
owner decision of 2026-09-01 (docs/design/02-protocol.md) allows the import after a dry
run whose counts the owner sees first. So this module has two halves: `dry_run` counts
and writes nothing, and `import_files` writes.

Three rules shape it.

  The receiver is not involved. A backfill has no HTTP client, no capture to attribute
  and no live session to fail open for; it parses, sanitizes and appends through the
  store directly, in batches, from the thread that is reading the file.

  A file is streamed, never loaded. The owner's ~/.claude/projects is 1.7 GB over 1806
  files and ~/.codex/sessions is 2.9 GB over 1452 (measured 2026-09-02), so every read
  here is line by line. Measured over the six largest transcripts, 233 MB: 0.07 s to
  count the lines and 0.31 s to json.loads every one of them, which is about 2 s for
  the whole tree and is why the scan parses rather than pattern-matching.

  A slug is not a path. `~/.claude/projects/<slug>/` encodes the project directory it
  came from; nothing here reconstructs it, stores it or prints it. What is stored is
  the sha256 of the file's path, and what is grouped and printed is the sha256 of the
  slug. The repository is identified only when a `cwd` line names a directory that
  still exists on this machine, and then by repo_id, which is a hash of the git dir.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import providers, repo
from telltale.model import Observation, now_iso, ulid
from telltale.providers import ParseCtx
from telltale.sanitize import Ctx, sanitize

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from telltale.store import Store

ADAPTER = "telltale.import@1"

# How many observations go into one store.append. Design 6.5: the writer takes one job
# and drains up to 500 more into one transaction, so this is the size of the batch it
# was built to commit.
BATCH = 500


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
            for text in handle:
                if not text.strip():
                    continue
                lines += 1
                record = _loads(text)
                if record is None:
                    continue
                parsed += 1
                stamp = _timestamp(record)
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


def _loads(text: str) -> Mapping[str, Any] | None:
    try:
        record = json.loads(text)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _timestamp(record: Mapping[str, Any]) -> str | None:
    value = record.get("timestamp")
    return value if isinstance(value, str) and value else None


def _session_of(record: Mapping[str, Any], provider: str) -> str | None:
    """The provider's own session id, from wherever that provider puts it.

    Codex spells it `id` on the session_meta line and `session_id` on some versions
    (measured: 25 of 25 files carry `id`, 1 of 25 carries both), so both are read.
    """
    if provider == "claude":
        return _text(record.get("sessionId"))
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    return _text(payload.get("session_id")) or _text(payload.get("id"))


def _cwd_of(record: Mapping[str, Any], provider: str) -> str | None:
    if provider == "claude":
        return _text(record.get("cwd"))
    payload = record.get("payload")
    return _text(payload.get("cwd")) if isinstance(payload, dict) else None


def _text(value: Any) -> str | None:
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


# -- the import -----------------------------------------------------------------------
def import_files(
    store: Store, sources: Iterable[Source], level: int = 1
) -> dict[str, Any]:
    """Import each source into its own capture. Returns what it wrote.

    A capture that is already in the store is skipped whole rather than appended to:
    observations are append-only (design 6.5), so importing a file twice would double
    every count in it, and the capture id is a hash of the session precisely so that
    this check is possible.
    """
    stored = {str(row["capture_id"]) for row in store.captures()}
    created: dict[str, Path] = {}
    result = {
        "captures": 0, "skipped": 0, "unreadable": 0, "observations": 0,
        "diagnostics": 0, "collisions": 0,
    }  # fmt: skip
    roots: dict[str, tuple[str | None, str | None]] = {}
    for source in sources:
        if not source.readable:
            store.diagnose(
                "parse_failure",
                f"backfill {source.kind}: {path_hash(source.path)[:12]}:"
                f" {source.reason}",
            )
            result["unreadable"] += 1
            result["diagnostics"] += 1
            continue
        if source.capture_id in stored:
            result["skipped"] += 1
            continue
        # Two files of one run reaching one capture id is not the idempotent skip
        # above: it is two sessions this rule cannot tell apart, and appending the
        # second would double the first's counts while looking like one session.
        # Measured before `_part` existed: 50 Codex rollouts collided this way.
        if source.capture_id in created:
            store.diagnose(
                "conflict",
                f"{source.kind}: {path_hash(source.path)[:12]} and"
                f" {path_hash(created[source.capture_id])[:12]} both key on"
                f" {source.capture_id}; the second was not imported",
                capture_id=source.capture_id,
            )
            result["collisions"] += 1
            result["diagnostics"] += 1
            continue
        created[source.capture_id] = source.path
        written = _import_one(store, source, level, roots)
        result["captures"] += 1
        result["observations"] += written["observations"]
        result["diagnostics"] += written["diagnostics"]
    return result


def _import_one(
    store: Store,
    source: Source,
    level: int,
    roots: dict[str, tuple[str | None, str | None]],
) -> dict[str, int]:
    """One file, one capture: start, every line, end, then the reducers."""
    settings = KINDS[source.kind]
    repo_root, repo_id = _repository(source.cwd, roots)
    ctx = ParseCtx(
        capture_id=source.capture_id,
        level=level,
        paths=Ctx(repo_root=None if repo_root is None else Path(repo_root)),
        repo_id=repo_id,
    )
    started = _started(source, level)
    unknown: set[str] = set()
    written = _emit(store, source, ctx, "telltale.capture_started", started)
    written += _replay(store, source, ctx, settings, unknown)
    written += _emit(store, source, ctx, "telltale.capture_ended", _ended(source))
    diagnostics = _report_notes(store, source, ctx) + _report_unknown(
        store, source, unknown
    )
    store.flush()
    store.rebuild(source.capture_id)
    return {"observations": written, "diagnostics": diagnostics}


def _repository(
    cwd: str | None, roots: dict[str, tuple[str | None, str | None]]
) -> tuple[str | None, str | None]:
    """(working tree root, repo_id) for a recorded cwd, or (None, None).

    Only when the directory still exists: git is asked about a place, and a place that
    is gone cannot answer. Cached by cwd because hundreds of sessions share a checkout
    and each answer costs several git subprocesses.

    No telltale.repo.identity observation is written from this. identity() reports the
    repository as it is NOW, and the session it would be attached to ran in the past;
    the only field taken is repo_id, which is a hash of the git directory and does not
    change with the working tree's state.
    """
    if cwd is None:
        return None, None
    if cwd in roots:
        return roots[cwd]
    answer: tuple[str | None, str | None] = (None, None)
    if Path(cwd).is_dir():
        root = repo.git_root(cwd)
        found = repo.identity(cwd) if root is not None else {}
        answer = (root, _text(found.get("repo_id")))
    roots[cwd] = answer
    return answer


def _started(source: Source, level: int) -> dict[str, Any]:
    """The capture_started payload of an imported session. Design 6.3, plus the file.

    `argv_shape` is "backfill" and not a command line, because there was no launch:
    this capture is a file that was already on the disk, and `telltale sessions` reads
    that word to mark the row.
    """
    return {
        "provider": KINDS[source.kind].provider,
        "argv_shape": "backfill",
        "content_level": level,
        "surfaces_configured": [KINDS[source.kind].surface],
        "provider_session_id_requested": source.session_id,
        "source_kind": source.kind,
        "source_path_hash": path_hash(source.path),
        "source_bytes": source.bytes,
        "source_lines": source.lines,
    }


def _ended(source: Source) -> dict[str, Any]:
    """The capture_ended payload. Three fields of design 6.3 are unknowable here.

    exit_code and duration_ms are None: a transcript records what the session did, not
    how the process ended or how long it was alive. The span between the first and the
    last provider timestamp is a different quantity and is on the observations already.
    """
    return {
        "exit_code": None,
        "duration_ms": None,
        "surfaces_received": {KINDS[source.kind].surface: source.lines},
    }


def _replay(
    store: Store,
    source: Source,
    ctx: ParseCtx,
    settings: Kind,
    unknown: set[str],
) -> int:
    """Every line of one file, parsed, sanitized and appended in batches.

    A line the parser refuses is one diagnostics row and the import continues: a
    transcript is 4000 lines of which one may be a shape this parser has never seen,
    and refusing the file would lose the other 3999.
    """
    module = providers.get(settings.provider)
    batch: list[Observation] = []
    written = 0
    with source.path.open(encoding="utf-8", errors="replace") as handle:
        for number, text in enumerate(handle, start=1):
            if not text.strip():
                continue
            _line(store, source, ctx, module, settings, text, number, batch)
            if len(batch) >= BATCH:
                unknown |= _unknown_of(batch)
                written += store.append(batch)
                batch.clear()
    unknown |= _unknown_of(batch)
    return written + store.append(batch)


def _line(
    store: Store,
    source: Source,
    ctx: ParseCtx,
    module: providers.Provider,
    settings: Kind,
    text: str,
    number: int,
    batch: list[Observation],
) -> None:
    """One line into `batch`, or one parse_failure diagnostic. Never raises."""
    record = _loads(text)
    if record is None:
        store.diagnose(
            "parse_failure",
            f"{source.kind} line {number}: not a JSON object",
            capture_id=source.capture_id,
        )
        return
    try:
        batch.extend(module.parse(settings.surface, record, ctx))
    # One bad line never stops a file: a 4000-line transcript may hold one shape this
    # parser has never seen, and refusing the file would lose the other 3999.
    except Exception as error:
        store.diagnose(
            "parse_failure",
            f"{source.kind} line {number}: {type(error).__name__}: {error}",
            capture_id=source.capture_id,
        )


def _unknown_of(batch: Sequence[Observation]) -> set[str]:
    """Every `<type>.<field>` in this batch that no allowlist entry knows."""
    return {
        f"{observation.observation_type}.{entry.removesuffix(':unknown')}"
        for observation in batch
        for entry in observation.redaction.get("dropped", ())
        if entry.endswith(":unknown")
    }


def _report_unknown(store: Store, source: Source, unknown: set[str]) -> int:
    """One diagnostic per capture naming every field this parser is behind on.

    The receiver writes one of these per request (design 6.6); a file is the request
    here, so a transcript that carries the same new field on 4000 lines is one row and
    not four thousand.
    """
    if not unknown:
        return 0
    store.diagnose(
        "unknown_field", " ".join(sorted(unknown)), capture_id=source.capture_id
    )
    return 1


def _emit(
    store: Store,
    source: Source,
    ctx: ParseCtx,
    obs_type: str,
    payload: Mapping[str, Any],
) -> int:
    """One telltale.* observation, through the same three gates a provider record takes.

    The provider timestamps are the file's own: capture_started is stamped with the
    first line that carried a clock and capture_ended with the last, so the `captures`
    view reports when the SESSION happened rather than when it was imported.
    """
    stamp = source.first_ts if obs_type.endswith("started") else source.last_ts
    body, redaction, unknown = sanitize(obs_type, dict(payload), ctx.level, ctx.paths)
    written = store.append([
        Observation(
            observation_id=ulid(),
            capture_id=source.capture_id,
            observation_type=obs_type,
            surface="import",
            provider=KINDS[source.kind].provider,
            adapter=ADAPTER,
            ingest_ts=now_iso(),
            provider_ts=stamp,
            provider_session_id=source.session_id,
            repo_id=ctx.repo_id,
            payload=body,
            redaction=redaction,
        )
    ])  # fmt: skip
    if unknown:
        store.diagnose(
            "unknown_field",
            f"{obs_type}: {' '.join(sorted(unknown))}",
            capture_id=source.capture_id,
        )
    return written


def _report_notes(store: Store, source: Source, ctx: ParseCtx) -> int:
    """One `dropped` diagnostic per capture for the lines that became no observation.

    The receiver writes one of these per request; a file is the request here. The count
    is the point: a transcript holds 18 line kinds and this parser reads 3, so the row
    says how much of the file was skipped and by which name.

    The `kind` is `dropped` because the vocabulary is a CHECK constraint and a seventh
    word is a migration. The DETAIL says which drop this is, and that is not cosmetic:
    the receiver's `dropped` rows are records lost to a full queue, which is data the
    recorder was handed and failed to keep, and these are line kinds this parser was
    never written to read. A reader counting queue loss over the owner's store would
    have found 1696 of these rows and read them as 1696 lost records.
    """
    if not ctx.notes:
        return 0
    counted: dict[str, int] = {}
    for note in ctx.notes:
        counted[note] = counted.get(note, 0) + 1
    detail = " ".join(
        f"importer: unparsed line kind {name}: {number}"
        for name, number in sorted(counted.items())
    )
    store.diagnose("dropped", detail, capture_id=source.capture_id)
    return 1
