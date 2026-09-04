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

  **The import validates every row before writing to the destination.**
  export_import.py stages validated rows and restores missing identities.
  export_validation.py validates payloads and the documented legacy forms.

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
from telltale.export_import import import_export as import_export
from telltale.model import Activity, now_iso, to_json

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
        conn.execute("BEGIN")
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
