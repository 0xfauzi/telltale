"""`telltale export`, and the round trip back: one directory of files per table.

Design 6.13 names `export --format jsonl|parquet --out DIR` and never says what a row
in it looks like. Four decisions do, and each one is here rather than in the CLI because
the reader below has to agree with the writer above about all four.

  **A column is exported as it is stored, and no column is invented.** The column list
  of every table is read out of the database with `PRAGMA table_info`, so it is the
  schema itself and cannot drift from schema.py. A JSON column (`payload`, `source`,
  `rows`, ...) is written as the TEXT the store holds, which is a JSON string inside the
  JSON object of a jsonl line and a string column in parquet. Decoding it would put two
  different things in the two formats and would make the round trip depend on this
  module re-encoding a structure the same way the store did.

  **`claim_class` travels with every derived row.** evidence and forecast_runs carry the
  column; series_snapshots carries `reducer_version`, which is what design 6.5 gives it.
  activities has no such column, because model.py refuses any Activity that is not
  derived, so the export writes the constant off `model.Activity` itself. It is a
  property of the shape rather than a number this module computed, and a file of
  activity rows with no claim class beside them is a file a reader may take for
  observations (invariant 6).

  **The round trip carries observations only.** `telltale import export --root DIR`
  reads `observations.jsonl`, appends through `store.append` and calls `store.rebuild`
  on every capture it created. It never writes an activity, an evidence row, a series
  snapshot or a forecast run: those are derived, the `derived-writes-only-in-store` hook
  puts their INSERT statements in store.py alone, and a rebuild is the one way to get
  them that keeps the reducer version honest.

  **The re-import re-runs the sanitizer as a gate, and appends the exported payload.**
  What it refuses and what it merely counts is `_check_payload` below, and both halves
  were measured on the owner's 1138974-observation store rather than assumed.

`pyarrow` is imported inside the parquet branch and nowhere else in this package: it is
the whole content of the `export` extra, a plain `uv sync` does not install it, and
`telltale export --format jsonl` must work on a machine that never had it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import __version__, config
from telltale.allowlist import ALLOWLIST, Kind
from telltale.model import Activity, Observation, from_json, now_iso, to_json
from telltale.sanitize import Ctx, sanitize

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator, Mapping, Sequence

    from telltale.store import Store

FORMATS = ("jsonl", "parquet")
MANIFEST = "MANIFEST.json"

# The six tables of design 6.5. The `captures` view is not one of them: it is a GROUP BY
# over observations and re-deriving it on the far side is the same statement.
TABLES = (
    "observations",
    "activities",
    "evidence",
    "series_snapshots",
    "forecast_runs",
    "diagnostics",
)

# What `--capture` can restrict. A series snapshot spans captures by construction and a
# forecast run is about a series, so neither has a capture to filter on and both are
# exported whole.
PER_CAPTURE = frozenset({"observations", "activities", "evidence", "diagnostics"})

# Each table in the order an index already answers, so a whole-table export is a scan of
# one index and never a sort. observations is ordered by (capture_id, observation_id)
# rather than by its primary key because the round trip reads it one capture at a time:
# obs_by_capture is exactly that pair. Measured on the owner's store, 1138974 rows:
# 1.25 s in this order, 1.53 s unordered, 1.22 s by observation_id.
_ORDER = {
    "observations": ("capture_id", "observation_id"),
    "activities": ("activity_id",),
    "evidence": ("evidence_id",),
    "series_snapshots": ("series_id",),
    "forecast_runs": ("forecast_run_id",),
    "diagnostics": ("diagnostic_id",),
}

# The claim class of an activity, off the dataclass rather than spelled here: model.py
# refuses any other value, so this is the shape's constant and not a second copy of it.
ACTIVITY_CLAIM_CLASS = str(
    next(spec.default for spec in fields(Activity) if spec.name == "claim_class")
)

# The three columns of an observation that hold JSON text, in the order the dataclass
# wants them back. Any other column is a scalar and needs no decoding.
_JSON_FIELDS = ("correlation_ids", "payload", "redaction")

# sanitize() writes this INTO the payload it returns (design 6.4), so on a second pass
# it arrives as an input field that no allowlist lists. It is the sanitizer's own
# output, not a field a provider sent, and it is excluded from the drift count for that
# reason. The stored value is kept: it records which rules produced the stored command.
_SANITIZER_FIELD = "normalization_version"

# Rows per parquet record batch, and the unit this module streams in. A table is never
# read into memory whole; 1000 rows is one batch of a few megabytes at the widest
# payload the store bounds (sanitize.MAX_PAYLOAD_BYTES is 8 KB).
CHUNK = 1000

# How many observations go into one store.append on the way back in, which is design
# 6.5's batch: the writer takes one job and drains up to 500 more into one transaction.
BATCH = 500


@dataclass(frozen=True)
class Column:
    """One exported column: its name, its SQLite type, and its constant when it has one.

    `constant` is set for exactly one column in the system, `activities.claim_class`,
    which is a property of the shape rather than a stored value. Every other column
    comes off `PRAGMA table_info`, so the export cannot name a column the schema does
    not have and cannot miss one it does.
    """

    name: str
    sql_type: str
    constant: str | None = None


# -- the export -----------------------------------------------------------------------
def export(
    store: Store, out_dir: str | Path, fmt: str, capture_id: str | None = None
) -> dict[str, int]:
    """Write one file per table under `out_dir`, plus MANIFEST.json. Returns row counts.

    One read-only connection for the whole export, and one cursor per table: `Store`
    opens a read-only connection per read by design, and six of those would each pay
    the WAL index setup again while this one has to stay open across a table anyway.
    Nothing here writes to the database.
    """
    if fmt not in FORMATS:
        raise ValueError(f"export format {fmt!r} is not one of {FORMATS}")
    out = _out_dir(out_dir)
    conn = store._connect(readonly=True)
    try:
        counts, versions = _write_tables(conn, out, fmt, capture_id)
    finally:
        conn.close()
    _write_manifest(out, fmt, capture_id, counts, versions)
    return counts


def _out_dir(out_dir: str | Path) -> Path:
    """The directory, created. Never inside $TELLTALE_HOME.

    An export under the home would put a copy of every payload beside the database that
    holds it, where `purge` and `purge --diagnostics-older-than` cannot reach it: a
    capture deleted from the store would still be on the disk, in full, and nothing
    would say so.
    """
    out = Path(out_dir).expanduser().resolve()
    home = config.home().resolve()
    if out.is_relative_to(home):
        raise ValueError(
            f"export --out {out} is inside TELLTALE_HOME {home}. An export there"
            " outlives `telltale purge`, which deletes from the database only."
        )
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write_tables(
    conn: sqlite3.Connection, out: Path, fmt: str, capture_id: str | None
) -> tuple[dict[str, int], dict[str, list[str]]]:
    counts: dict[str, int] = {}
    versions: dict[str, list[str]] = {}
    for table in TABLES:
        columns = table_columns(conn, table)
        seen: set[str] = set()
        rows = _read_rows(conn, table, columns, capture_id, seen)
        path = out / f"{table}.{fmt}"
        counts[table] = (
            _write_jsonl(path, columns, rows)
            if fmt == "jsonl"
            else _write_parquet(path, columns, rows)
        )
        if seen:
            versions[table] = sorted(seen)
    return counts, versions


def table_columns(conn: sqlite3.Connection, table: str) -> tuple[Column, ...]:
    """The stored columns of one table, in schema order, off the database itself."""
    stored = tuple(
        Column(name=str(row[1]), sql_type=str(row[2]).upper())
        for row in conn.execute(f'PRAGMA table_info("{table}")')
    )
    if not stored:
        raise ValueError(f"{table}: no such table in this database")
    if table != "activities":
        return stored
    return (*stored, Column("claim_class", "TEXT", ACTIVITY_CLAIM_CLASS))


def _read_rows(
    conn: sqlite3.Connection,
    table: str,
    columns: Sequence[Column],
    capture_id: str | None,
    versions: set[str],
) -> Iterator[tuple[Any, ...]]:
    """Every row of one table, streamed, with the constant columns appended."""
    stored = [column for column in columns if column.constant is None]
    constants = tuple(
        column.constant for column in columns if column.constant is not None
    )
    names = ", ".join(f'"{column.name}"' for column in stored)
    where = " WHERE capture_id = ?" if capture_id and table in PER_CAPTURE else ""
    order = ", ".join(f'"{name}"' for name in _ORDER[table])
    sql = f"SELECT {names} FROM {table}{where} ORDER BY {order}"  # noqa: S608 - fixed names
    version_at = next(
        (
            index
            for index, column in enumerate(stored)
            if column.name == "reducer_version"
        ),
        None,
    )
    for row in conn.execute(sql, (capture_id,) if where else ()):
        if version_at is not None:
            versions.add(str(row[version_at]))
        yield (*row, *constants)


def _write_jsonl(
    path: Path, columns: Sequence[Column], rows: Iterator[tuple[Any, ...]]
) -> int:
    names = [column.name for column in columns]
    written = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(to_json(dict(zip(names, row, strict=True))))
            handle.write("\n")
            written += 1
    return written


def _write_parquet(
    path: Path, columns: Sequence[Column], rows: Iterator[tuple[Any, ...]]
) -> int:
    """The one place in this package that imports pyarrow, inside the branch that needs
    it. A plain `uv sync` installs no extra, so the jsonl half must not pay for this."""
    import pyarrow
    import pyarrow.parquet

    types = {
        "TEXT": pyarrow.string(),
        "INTEGER": pyarrow.int64(),
        "REAL": pyarrow.float64(),
    }
    schema = pyarrow.schema([
        pyarrow.field(column.name, types[column.sql_type]) for column in columns
    ])  # fmt: skip
    written = 0
    writer = pyarrow.parquet.ParquetWriter(path, schema)
    try:
        for chunk in _chunks(rows):
            columnwise = list(zip(*chunk, strict=True))
            writer.write_batch(
                pyarrow.RecordBatch.from_arrays(
                    [
                        pyarrow.array(values, type=field.type)
                        for values, field in zip(columnwise, schema, strict=True)
                    ],
                    schema=schema,
                )
            )
            written += len(chunk)
    finally:
        writer.close()
    return written


def _chunks(rows: Iterator[tuple[Any, ...]]) -> Iterator[list[tuple[Any, ...]]]:
    batch: list[tuple[Any, ...]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= CHUNK:
            yield batch
            batch = []
    if batch:
        yield batch


def _write_manifest(
    out: Path,
    fmt: str,
    capture_id: str | None,
    counts: Mapping[str, int],
    versions: Mapping[str, list[str]],
) -> None:
    manifest = {
        "telltale_version": __version__,
        "format": fmt,
        "exported_at": now_iso(),
        "capture": capture_id,
        "tables": dict(counts),
        "reducer_versions": dict(versions),
    }
    (out / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# -- the round trip -------------------------------------------------------------------
#
# What the gate refuses and what it merely counts, and why the two lists are not one.
#
# The rule a first attempt reaches for is "a row that was sanitized once must pass
# again, unchanged". Measured against the owner's store on 2026-09-03, over all
# 1138974 observations re-sanitized at each capture's own content level, that rule
# refuses 19 per cent of the rows, and not one of the three causes is a leak:
#
#   `normalization_version` (on every row carrying a command). sanitize() WRITES this
#   field into the payload it returns, so on a second pass it arrives as an input field
#   that no allowlist lists, is dropped as unknown, and is written back holding the
#   CURRENT rules' version. It is the sanitizer's own output. Excluded by name.
#
#   Allowlist drift, 6521 rows. W4-T4 renamed `claude.stream.assistant.output_tokens` to
#   `output_tokens_snapshot` and moved `claude.stream.result`'s four usage fields under
#   `main_thread_*`. Rows written before that carry the old names, which the current
#   table does not know. The gate they went through is the gate that ran; the table
#   moved afterwards. Counted and named, never refused.
#
#   `commands.normalize` is not idempotent on its own output, 37955 rows. A path already
#   reduced to `<outside>/<hex>` is re-read on the next pass as a shell redirection:
#   `cat <outside>/8c1075f0` becomes `cat < outside > <outside>/69f5ca73`, and a third
#   pass adds another pair. 40 rows re-truncate at MAX_COMMAND for the same reason.
#   That is a defect in commands.py (docs/log/W6-T3.md reports it), not evidence about
#   this file, and the stored value is the cleaner of the two. Counted and named.
#
# So the gate refuses what the CURRENT rules would remove for a privacy reason and
# nothing else:
#
#   any redaction at all (a secret pattern matched inside the file, or a string UTF-8
#   cannot encode). This fires inside the COMMAND branch too, so a credential in a
#   stored command is still caught;
#   any drop whose reason is not `unknown` (never_persist, type, depth, not_a_number,
#   level0_path, empty_path);
#   any truncation except `command_bound` on a field whose Kind is COMMAND;
#   any changed value except on a field whose Kind is COMMAND. Kind.PATH is the one that
#   matters and it is idempotent: 0 of 1138974 stored path fields moved.
#
# What that leaves uncaught is named rather than papered over: an ABSOLUTE PATH written
# by hand into a `command` field of an export file would be normalized to
# `<outside>/<hex>` by the gate, which is a changed COMMAND value and therefore not a
# refusal, and the file's own text would be appended. Closing it needs normalize() to be
# idempotent, which is the defect above.

_STARTED = "telltale.capture_started"
# The content levels sanitize() distinguishes. It branches on `level <= 0` and nowhere
# else, so 1 and 2 are the same function and these two cover all three.
#
# Used only for a capture whose export carries no `telltale.capture_started` row to read
# a level off, and then the row is accepted if ANY of them accepts it. Requiring all of
# them was written first and is wrong: measured on a replay of E01's S1 through the
# receiver, which records no capture_started, 80 rows carrying a repo-relative `cwd` and
# `transcript_path` were refused at level 0 for `level0_path` while being exactly what
# level 1 stores. Accepting on any is not the weaker check it looks like, because every
# signal the gate refuses on except `level0_path` is level-independent: a never_persist
# field, a matched secret, an exceeded bound and a rewritten PATH are refused at 0 and
# at 1 alike, and an absolute path planted in a PATH field is DROPPED at level 0 and
# REWRITTEN at level 1, so no level accepts it.
_EITHER_LEVEL = (0, 1)
_SHOWN = 10  # refusals printed before the count of the rest


@dataclass
class Notes:
    """What the gate saw, allowed, and counted. Never a reason to refuse."""

    drift: dict[str, int]
    commands: dict[str, int]

    @classmethod
    def empty(cls) -> Notes:
        return cls(drift={}, commands={})

    def bump(self, counter: dict[str, int], key: str) -> None:
        counter[key] = counter.get(key, 0) + 1

    def merge(self, other: Notes) -> None:
        """Take the counts of the one content level that accepted a row, and no other.

        A row checked at two levels is one row: counting the drift of both would report
        twice what the file holds, which is the cardinality defect AGENTS.md names.
        """
        pairs = ((self.drift, other.drift), (self.commands, other.commands))
        for mine, theirs in pairs:
            for key, count in theirs.items():
                mine[key] = mine.get(key, 0) + count


@dataclass
class Counted:
    """The captures in an export file, split by whether the store already holds them."""

    fresh: dict[str, int]
    known: dict[str, int]

    @property
    def fresh_rows(self) -> int:
        return sum(self.fresh.values())

    @property
    def known_rows(self) -> int:
        return sum(self.known.values())


def read_manifest(root: Path) -> dict[str, Any]:
    """The manifest of an export directory, or a refusal naming what is wrong."""
    path = root / MANIFEST
    if not path.is_file():
        raise ValueError(f"{path}: not an export directory, no {MANIFEST} in it")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"{path}: {error}") from None
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: holds a {type(loaded).__name__}, not an object")
    if loaded.get("format") != "jsonl":
        raise ValueError(
            f"{path}: format {loaded.get('format')!r}. The round trip reads jsonl;"
            " re-export the same store with --format jsonl."
        )
    return loaded


def import_export(
    store: Store, root: str | Path, dry_run: bool = False
) -> dict[str, Any]:
    """Read `observations.jsonl` back into `store`. Design 6.13, and the trip back.

    Two passes over the file, and the split is what makes `--dry-run` mean something.
    The first sanitizes every row and writes nothing; the second appends the rows of the
    captures this store does not hold, and appends the FILE's payload rather than the
    sanitizer's, because what the first pass proved is that the payload holds nothing
    the current rules would remove, not that the current rules would write it back
    byte for byte. A refusal in the first pass stops the whole import, so the store is
    never left holding half a file.

    A capture the store already has is skipped WHOLE, which is `importer.import_files`'
    rule and the reason a second import adds 0. The cost is that a capture whose import
    was interrupted cannot be completed by a second run; `telltale purge` it first.
    """
    where = Path(root).expanduser()
    manifest = read_manifest(where)
    path = where / "observations.jsonl"
    if not path.is_file():
        raise ValueError(f"{path}: no observations.jsonl under {where}")
    known = _known(store)
    notes = Notes.empty()
    counted = _gate(path, known, notes)
    result: dict[str, Any] = {
        "root": str(where),
        "exported_at": manifest.get("exported_at"),
        "captures": len(counted.fresh),
        "skipped_captures": len(counted.known),
        "observations": counted.fresh_rows,
        "skipped": counted.known_rows,
        "drift": dict(notes.drift),
        "commands": dict(notes.commands),
        "added": 0,
        "diagnostics": 0,
        "dropped": 0,
        "rebuilt": 0,
    }
    if dry_run:
        return result
    dropped_before = _dropped(store)
    result["added"] = _append(store, path, counted.fresh)
    result["diagnostics"] = _append_diagnostics(store, where, counted.fresh)
    store.flush()
    result["dropped"] = _dropped(store) - dropped_before
    for capture in sorted(counted.fresh):
        store.rebuild(capture)
    result["rebuilt"] = len(counted.fresh)
    return result


def _dropped(store: Store) -> int:
    """How many rows this store has lost to a full queue, over its whole life.

    Read before and after the copy, because `store.diagnose` never blocks and never
    says: it counts a drop and returns. Measured before `_append_diagnostics` flushed,
    importing the owner's 18817 attributable diagnostics lost 31 of them this way, and
    the import reported 18817 written. A count of rows OFFERED printed as rows written
    is exactly the defect AGENTS.md names, so the difference is read off the store's own
    counters and printed beside it.
    """
    counted = store.health()["drops_by_surface"]
    return int(sum(counted.values()))


def _known(store: Store) -> set[str]:
    """The capture ids already on this disk, or none when there is no disk yet.

    `importer._stored_captures`' rule, and for its reason: a dry run must create no
    database to find out that it holds nothing, and a read-only connection to a file
    that is not there is an error rather than an empty answer.
    """
    if not Path(store.path).exists():
        return set()
    return {str(row["capture_id"]) for row in store.captures()}


def _gate(path: Path, known: set[str], notes: Notes) -> Counted:
    """Sanitize every row in the file. Writes nothing, and raises with every refusal.

    Grouped by capture because the content level is a property of the capture and is
    read off its `telltale.capture_started` row, which is NOT always the first row of
    the group: measured on the owner's store, it is the lowest observation_id in 3153 of
    the 3312 captures that have one and not in the other 159. One capture's rows are
    held in memory at a time, bounded by the biggest capture there (22201 rows,
    6.1 MB of payload).
    """
    counted = Counted(fresh={}, known={})
    refusals: list[str] = []
    for capture, rows in _captures(path):
        target = counted.known if capture in known else counted.fresh
        target[capture] = len(rows)
        levels = _levels_of(capture, rows)
        for row in rows:
            refusals.extend(
                f"{row['observation_id']} ({row['observation_type']}): {reason}"
                for reason in _check_row(row, levels, notes)
            )
    if refusals:
        shown = "\n  ".join(refusals[:_SHOWN])
        rest = len(refusals) - _SHOWN
        more = f"\n  ... {rest} more" if rest > 0 else ""
        raise ValueError(
            f"{path}: {len(refusals)} row(s) pass the sanitizer at no content level"
            f" their capture could have been recorded at, so this file holds something"
            f" the store would not have accepted:\n  {shown}{more}"
        )
    return counted


def _captures(path: Path) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """One capture's rows at a time, from a file grouped by capture. Never a whole file.

    A capture id that comes back after its group closed is refused rather than merged:
    the export writes ORDER BY capture_id, and a file where one capture appears twice is
    a file two exports were concatenated into, where the second copy's rows would be
    counted as a capture of their own.
    """
    closed: set[str] = set()
    current: str | None = None
    rows: list[dict[str, Any]] = []
    for row in _lines(path):
        capture = str(row["capture_id"])
        if capture != current:
            if current is not None:
                closed.add(current)
                yield current, rows
            if capture in closed:
                raise ValueError(
                    f"{path}: capture {capture} appears in two separate blocks;"
                    " this file is not one export"
                )
            current, rows = capture, []
        rows.append(row)
    if current is not None:
        yield current, rows


def _lines(path: Path) -> Iterator[dict[str, Any]]:
    """Every line of one jsonl file as a row, with its JSON columns decoded."""
    with path.open(encoding="utf-8") as handle:
        for number, text in enumerate(handle, start=1):
            if not text.strip():
                continue
            try:
                row = json.loads(text)
            except ValueError as error:
                raise ValueError(f"{path} line {number}: {error}") from None
            if not isinstance(row, dict):
                raise ValueError(f"{path} line {number}: not a JSON object")
            yield {
                name: from_json(value)
                if name in _JSON_FIELDS and isinstance(value, str)
                else value
                for name, value in row.items()
            }


def _levels_of(capture: str, rows: Sequence[Mapping[str, Any]]) -> tuple[int, ...]:
    """The content level this capture was recorded at, or both that could apply."""
    found = {
        int(level)
        for row in rows
        if row["observation_type"] == _STARTED
        for level in [dict(row["payload"]).get("content_level")]
        if isinstance(level, int)
    }
    if len(found) > 1:
        raise ValueError(
            f"capture {capture}: two content levels, {sorted(found)}."
            " One capture is recorded at one level (design 6.4)."
        )
    return (found.pop(),) if found else _EITHER_LEVEL


def _check_row(
    row: Mapping[str, Any], levels: Sequence[int], notes: Notes
) -> list[str]:
    """Why no content level accepts this row. Empty when one does, and then its counts.

    One level when the capture recorded one, and both otherwise. The counts of the level
    that accepted are the ones kept: a row is one row whatever it took to check it.
    """
    attempts = [_check_payload(row, level) for level in levels]
    for refusals, seen in attempts:
        if not refusals:
            notes.merge(seen)
            return []
    return list(
        dict.fromkeys(reason for refusals, _ in attempts for reason in refusals)
    )


def _check_payload(row: Mapping[str, Any], level: int) -> tuple[list[str], Notes]:
    """What the current rules would remove from this payload, and what they only moved.

    The long comment above this section is the argument for every line of it, and both
    halves were measured before they were written. One helper per list the sanitizer
    returns, because each answers a different question about the same row.
    """
    obs_type = str(row["observation_type"])
    payload = dict(row["payload"])
    cleaned, redaction, _unknown = sanitize(obs_type, payload, level, Ctx())
    notes = Notes.empty()
    refusals = [
        *_dropped_reasons(obs_type, redaction["dropped"], notes),
        # No exception and no Kind: a redaction is the scrubber saying it found one of
        # design 6.4's secret patterns, or a string UTF-8 cannot encode. Neither can be
        # in a row this store wrote, at any content level.
        *(
            f"{entry} holds something the scrubber redacts"
            for entry in redaction["redacted"]
        ),
        *_truncated_reasons(obs_type, redaction["truncated"], notes),
        *_changed_reasons(obs_type, payload, cleaned, notes),
    ]
    return refusals, notes


def _dropped_reasons(
    obs_type: str, dropped: Sequence[str], notes: Notes
) -> Iterator[str]:
    """A drop for any reason but `unknown`. An unknown field is the allowlist moving."""
    for entry in dropped:
        name, _, reason = entry.rpartition(":")
        if reason != "unknown":
            yield f"{entry} would be dropped"
        elif name != _SANITIZER_FIELD:
            notes.bump(notes.drift, f"{obs_type}.{name}")


def _truncated_reasons(
    obs_type: str, truncated: Sequence[str], notes: Notes
) -> Iterator[str]:
    """A bound exceeded, except the command bound on a command already at it."""
    for entry in truncated:
        name, _, reason = entry.rpartition(":")
        if reason == "command_bound" and _kind(obs_type, name) is Kind.COMMAND:
            notes.bump(notes.commands, f"{obs_type}.{name}")
        else:
            yield f"{entry} exceeds a bound"


def _changed_reasons(
    obs_type: str, payload: Mapping[str, Any], cleaned: Mapping[str, Any], notes: Notes
) -> Iterator[str]:
    """A field the rules rewrote. Only a COMMAND may differ, and only because of the
    non-idempotence measured above; Kind.PATH did not move on any of 1138974 rows."""
    for name, value in cleaned.items():
        if name == _SANITIZER_FIELD or name not in payload or payload[name] == value:
            continue
        if _kind(obs_type, name) is Kind.COMMAND:
            notes.bump(notes.commands, f"{obs_type}.{name}")
        else:
            yield f"{name} is not what the current rules produce"


def _kind(obs_type: str, field: str) -> Kind | None:
    return ALLOWLIST.get(obs_type, {}).get(field)


def _append(store: Store, path: Path, fresh: Mapping[str, int]) -> int:
    """The second pass: append the rows of the captures this store does not hold.

    The payload appended is the FILE's, not the sanitizer's output. Re-sanitizing here
    would delete every field the allowlist has since renamed (6521 rows on the owner's
    store) and rewrite every command holding a path outside the repository (37955), so a
    round trip would change what the store holds. The first pass is the gate; this is
    the copy.
    """
    batch: list[Observation] = []
    written = 0
    for row in _lines(path):
        if row["capture_id"] not in fresh:
            continue
        batch.append(_observation(row))
        if len(batch) >= BATCH:
            written += store.append(batch)
            batch.clear()
    return written + store.append(batch)


def _observation(row: Mapping[str, Any]) -> Observation:
    """One exported row as an Observation. The dataclass is the check on the columns."""
    expected = {spec.name for spec in fields(Observation)}
    if set(row) != expected:
        missing = sorted(expected - set(row))
        extra = sorted(set(row) - expected)
        raise ValueError(
            f"observations.jsonl: a row is missing {missing} and carries {extra};"
            " it was not written by this version of `telltale export`"
        )
    return Observation(**row)


def _append_diagnostics(store: Store, where: Path, fresh: Mapping[str, int]) -> int:
    """Re-record the diagnostics of the captures this run created, and only those.

    Two things this cannot do, and both are named rather than worked around.
    `store.diagnose` is the only public write path and it stamps its own `ingest_ts`, so
    a re-imported diagnostic carries the time of the IMPORT and not of the event: the
    export FILE holds the true one, and `purge --diagnostics-older-than` on the far side
    sees these rows as fresh. And a row with no capture_id (232 of 19819 on the owner's
    store, all of them `parse_failure` from a file the backfill could not read) belongs
    to the process that wrote it, not to a capture, so nothing here can decide whether a
    second import would be recording it again; it is skipped and counted.
    """
    from telltale.store import DIAGNOSTIC_KINDS

    path = where / "diagnostics.jsonl"
    if not path.is_file():
        return 0
    written = 0
    for row in _lines(path):
        capture = row.get("capture_id")
        if capture not in fresh or str(row.get("kind")) not in DIAGNOSTIC_KINDS:
            continue
        store.diagnose(
            str(row["kind"]),
            str(row.get("detail") or ""),
            capture_id=str(capture),
            observation_id=_text(row.get("observation_id")),
        )
        written += 1
        # A barrier every BATCH rows, because `diagnose` puts one row on the queue and
        # returns whether or not there was room. `append` says how many it took and this
        # does not, so the only way to copy 18817 of them without losing any is to let
        # the writer catch up. Measured without it: 31 lost.
        if written % BATCH == 0:
            store.flush()
    return written


def _text(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None
