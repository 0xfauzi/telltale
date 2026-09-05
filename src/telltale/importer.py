"""Backfill: the session files the provider already wrote, read through the same gates.

Design 6.3 calls `claude.transcript.*` and `codex.rollout.*` backfill surfaces, and the
owner decision of 2026-09-01 (docs/design/02-protocol.md) allows the import after a dry
run whose counts the owner sees first. This is the half that WRITES; `importer_scan.py`
is the half that counts, and it holds the shapes, the walk and the dry run. The names
this module re-exports are the ones `cli_import.py` and the tests already call, so the
split is not visible from outside.

Three rules shape it.

  The receiver is not involved. A backfill has no HTTP client, no capture to attribute
  and no live session to fail open for; it parses, sanitizes and appends through the
  store directly, in batches, from the thread that is reading the file.

  A file is streamed, never loaded, and read three times at that price: `_survey` for
  its session and its clock, `regimes` for the environments it ran under, and `_replay`
  for the observations. Measured over the six largest transcripts, 233 MB: 0.31 s to
  json.loads every line, so about 2 s per pass over the owner's whole tree.

  A slug is not a path. What is stored is the sha256 of the file's path, never the
  path. The repository is identified only when a `cwd` line names a directory that
  still exists on this machine, and then by repo_id, which is a hash of the git dir.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import env, providers, repo
from telltale.importer_scan import (
    KINDS,
    Kind,
    Source,
    capture_id,
    default_root,
    dry_run,
    loads,
    path_hash,
    scan,
    text,
    timestamp,
)
from telltale.model import Observation, now_iso, ulid
from telltale.providers import ParseCtx
from telltale.sanitize import Ctx, sanitize

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from telltale.store import Store

# What a caller of this module may use. The eight re-exported names are what the CLI and
# the tests called before importer_scan.py existed, and they keep calling them here.
__all__ = [
    "ADAPTER",
    "BATCH",
    "KINDS",
    "Kind",
    "Source",
    "capture_id",
    "default_root",
    "dry_run",
    "import_files",
    "path_hash",
    "regimes",
    "scan",
]

ADAPTER = "telltale.import@1"

# How many observations go into one store.append. Design 6.5: the writer takes one job
# and drains up to 500 more into one transaction, so this is the size of the batch it
# was built to commit.
BATCH = 500


# -- the environment ------------------------------------------------------------------
@dataclass(frozen=True)
class _Regime:
    """One stretch of a file that ran under one environment, and that environment.

    `line` is the first line number the id governs and `payload` is the
    telltale.environment body without its id, ready for `_emit`. A regime is recorded
    only where the id CHANGES, so a session that goes A, B, A is three regimes over two
    ids and two environment observations.
    """

    line: int
    stamp: str | None
    fingerprint_id: str
    payload: dict[str, Any]


def regimes(source: Source, settings: Kind, level: int) -> list[_Regime]:
    """The environments one file ran under, in file order. A second read of the file.

    A pre-pass rather than work done inside `_replay`, because the count of distinct
    ids goes on the capture_started payload, which is written before the first line is
    parsed. It costs one more json.loads per line: 0.31 s over the 233 MB `_survey` was
    measured on, so about 2 s over the owner's whole transcript tree.

    A file whose lines record no runtime version, model or effort at all yields nothing
    and the capture stays `unavailable` in the series. A fingerprint built out of three
    Nones would be one id shared by every such file, and a changepoint between two of
    them would be a claim about an environment nobody observed.
    """
    reader = _claude_regimes if settings.provider == "claude" else _codex_regimes
    out: list[_Regime] = []
    for line, stamp, observed in reader(source):
        if observed == (None, None, None):
            continue
        runtime_version, model, effort = observed
        built = env.backfill_fingerprint(
            settings.provider, runtime_version, model, effort, settings.surface, level
        )
        fingerprint_id = str(built["fingerprint_id"])
        if out and out[-1].fingerprint_id == fingerprint_id:
            continue
        out.append(
            _Regime(
                # The first regime governs from line 1: the lines before the first
                # assistant message or the first turn_context ran in the same
                # environment as the first one that named it, and leaving them unstamped
                # would make an otherwise complete capture `partial`.
                line=1 if not out else line,
                stamp=source.first_ts if not out else stamp,
                fingerprint_id=fingerprint_id,
                payload={
                    name: value
                    for name, value in built.items()
                    if name != "fingerprint_id"
                },
            )
        )
    return out


_Observed = tuple[str | None, str | None, str | None]

# What Claude Code writes in `message.model` on an assistant line no model produced.
# Measured over the 1841 imported Claude captures of the E13 store: 105 assistant rows
# in 64 captures carry it, and every one of the 105 has input_tokens 0 and
# output_tokens 0. On the transcript it was first seen in, all three are
# `isApiErrorMessage: true`. It is a sentinel and not a model name, and reading it as
# one made a session that ran on one model report two changepoints, into it and back
# out (measured before this line existed: imp_81e36bfa24c2f58530f6acee, 1278 request
# rows, changepoints 1016 and 1017).
_NOT_A_MODEL = "<synthetic>"


def _claude_regimes(source: Source) -> Iterator[tuple[int, str | None, _Observed]]:
    """(line, timestamp, (version, model, effort)) for every assistant line.

    Measured on the owner's largest telltale transcript, 2262 assistant lines: the
    top-level `version` is on all 2262, `message.model` on all 2262 and the top-level
    `effort` on 2245. A line that is not an assistant line carries none of the three,
    so it names no regime and belongs to the one already running.

    A line whose model is `<synthetic>` is skipped for the same reason a user line is:
    no model produced it, so it says nothing about which model the session was using.
    It is not read as "model not observed" either, because that is a fourth value and
    would be the same changepoint under a different name.
    """
    for number, record in _records(source):
        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        model = text(message.get("model")) if isinstance(message, dict) else None
        if model == _NOT_A_MODEL:
            continue
        yield (
            number,
            timestamp(record),
            (text(record.get("version")), model, text(record.get("effort"))),
        )


def _codex_regimes(source: Source) -> Iterator[tuple[int, str | None, _Observed]]:
    """The same, from the turn_context records, with the file's one cli_version.

    A rollout names its CLI version once, on the session_meta record it opens with
    (measured, W7-T2 brief), so within a file it is a constant. It is applied to every
    turn rather than as it is met: a turn_context that arrived before its session_meta
    would otherwise become a changepoint on a field that did not change.

    That is why the turns are held and yielded after the read, and it is the only thing
    this module keeps in memory: one small tuple per TURN, not per line, on a file that
    is otherwise streamed.
    """
    runtime_version: str | None = None
    turns: list[tuple[int, str | None, str | None, str | None]] = []
    for number, record in _records(source):
        payload = _payload(record)
        runtime_version = runtime_version or text(payload.get("cli_version"))
        if record.get("type") == "turn_context":
            turns.append((
                number,
                timestamp(record),
                text(payload.get("model")),
                text(payload.get("effort")),
            ))  # fmt: skip
    for number, stamp, model, effort in turns:
        yield number, stamp, (runtime_version, model, effort)


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("payload")
    return value if isinstance(value, dict) else {}


def _records(source: Source) -> Iterator[tuple[int, Mapping[str, Any]]]:
    """Every line of the file that parses as a JSON object, numbered as `_replay` does.

    The same reader and the same `_loads`, so a line this pass skips is a line that
    pass reports as a parse failure, and the two agree on what line 400 is.
    """
    with source.path.open(encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = loads(line)
            if record is not None:
                yield number, record


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
    """One file, one capture: start, every line, end, then the reducers.

    The two lifecycle rows carry the first and the last environment the file recorded,
    which is what the launcher does with its one fingerprint: it is bound before
    capture_started is emitted and still bound at capture_ended. A capture whose file
    named no environment leaves both unstamped, and every row of it stays unknown.
    """
    settings = KINDS[source.kind]
    repo_root, repo_id = _repository(source.cwd, roots)
    ctx = ParseCtx(
        capture_id=source.capture_id,
        level=level,
        paths=Ctx(repo_root=None if repo_root is None else Path(repo_root)),
        repo_id=repo_id,
    )
    found = regimes(source, settings, level)
    started = _started(source, level, len({item.fingerprint_id for item in found}))
    unknown: set[str] = set()
    written = _emit(
        store, source, _at(ctx, found[0] if found else None),
        "telltale.capture_started", started,
    )  # fmt: skip
    written += _replay(store, source, ctx, settings, found, unknown)
    written += _emit(
        store, source, _at(ctx, found[-1] if found else None),
        "telltale.capture_ended", _ended(source),
    )  # fmt: skip
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
        answer = (root, text(found.get("repo_id")))
    roots[cwd] = answer
    return answer


def _at(ctx: ParseCtx, regime: _Regime | None) -> ParseCtx:
    """`ctx` bound to one regime's environment, or left unknown when there is none."""
    if regime is None:
        return ctx
    return replace(ctx, environment_fingerprint_id=regime.fingerprint_id)


def _started(source: Source, level: int, fingerprints: int) -> dict[str, Any]:
    """The capture_started payload of an imported session. Design 6.3, plus the file.

    `argv_shape` is "backfill" and not a command line, because there was no launch:
    this capture is a file that was already on the disk, and `telltale sessions` reads
    that word to mark the row.

    `fingerprints` is how many DISTINCT environments the file recorded. Zero is a
    count, not a gap: the importer always knows how many it built, and zero says the
    file named no runtime version, model or effort anywhere. One is the ordinary
    session; more than one is a mid-session model, effort or version switch, and the
    request series reports a changepoint at each.
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
        "fingerprints": fingerprints,
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
    found: Sequence[_Regime],
    unknown: set[str],
) -> int:
    """Every line of one file, parsed, sanitized and appended in batches.

    A line the parser refuses is one diagnostics row and the import continues: a
    transcript is 4000 lines of which one may be a shape this parser has never seen,
    and refusing the file would lose the other 3999.

    A regime boundary flushes the batch before its telltale.environment row is written,
    so the row is stored, and ordered, ahead of every line it governs. From the
    boundary on the ctx handed to the parser carries that id, and the parser stamps it
    on each Observation it builds (claude._observe, codex._observe).
    """
    module = providers.get(settings.provider)
    pending = list(found)
    emitted: set[str] = set()
    batch: list[Observation] = []
    written = 0
    with source.path.open(encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            while pending and pending[0].line <= number:
                regime = pending.pop(0)
                unknown |= _unknown_of(batch)
                written += store.append(batch)
                batch.clear()
                written += _environment(store, source, ctx, regime, emitted)
                ctx = _at(ctx, regime)
            _line(store, source, ctx, module, settings, line, number, batch)
            if len(batch) >= BATCH:
                unknown |= _unknown_of(batch)
                written += store.append(batch)
                batch.clear()
    unknown |= _unknown_of(batch)
    return written + store.append(batch)


def _environment(
    store: Store,
    source: Source,
    ctx: ParseCtx,
    regime: _Regime,
    emitted: set[str],
) -> int:
    """One telltale.environment row, the first time an id appears in this file.

    A session that returns to an environment it has already been in gets no second row:
    the id is the environment, so the row would repeat a payload already stored, while
    the observations from here on carry the id again and the changepoint is read off
    those.
    """
    if regime.fingerprint_id in emitted:
        return 0
    emitted.add(regime.fingerprint_id)
    return _emit(
        store,
        source,
        _at(ctx, regime),
        "telltale.environment",
        regime.payload,
        stamp=regime.stamp,
    )


def _line(
    store: Store,
    source: Source,
    ctx: ParseCtx,
    module: providers.Provider,
    settings: Kind,
    line: str,
    number: int,
    batch: list[Observation],
) -> None:
    """One line into `batch`, or one parse_failure diagnostic. Never raises."""
    record = loads(line)
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
    stamp: str | None = None,
) -> int:
    """One telltale.* observation, through the same three gates a provider record takes.

    The provider timestamps are the file's own: capture_started is stamped with the
    first line that carried a clock and capture_ended with the last, so the `captures`
    view reports when the SESSION happened rather than when it was imported. A caller
    that knows the line its row belongs to passes that line's clock instead; an
    environment row stamped with the file's last timestamp would sort after every line
    it governs.
    """
    if stamp is None:
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
            environment_fingerprint_id=ctx.environment_fingerprint_id,
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
