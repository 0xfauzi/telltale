"""Restore validated export identities through the store's public write methods.

The temporary database holds only validated rows. Its primary key proves cardinality.
Destination writes start after all files, counts, payloads and existing identities pass.
A retry adds missing identities and refuses any conflicting existing row.
"""

from __future__ import annotations

import itertools
import sqlite3
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

from telltale import export_validation as validation
from telltale.model import to_json
from telltale.store import DIAGNOSTIC_KINDS, MAX_DETAIL

if TYPE_CHECKING:
    from collections.abc import Iterator

    from telltale.store import Store

TABLES = (
    "observations",
    "activities",
    "evidence",
    "series_snapshots",
    "forecast_runs",
    "diagnostics",
)
BATCH = 500
_DIAGNOSTIC_COLUMNS = {
    "diagnostic_id",
    "capture_id",
    "ingest_ts",
    "kind",
    "observation_id",
    "detail",
}


def import_export(
    store: Store, root: str | Path, dry_run: bool = False
) -> dict[str, Any]:
    where = Path(root).expanduser()
    manifest = _manifest(where)
    with TemporaryDirectory(prefix="telltale-import-") as temporary:
        staged = sqlite3.connect(Path(temporary) / "validated.db")
        try:
            staged.execute(
                "CREATE TABLE incoming (kind TEXT, id TEXT, capture TEXT, data TEXT,"
                " fresh INTEGER, PRIMARY KEY (kind, id))"
            )
            drift = _stage(store, where, manifest, staged)
            result = _result(where, manifest, staged, drift)
            if not dry_run:
                _restore(store, staged, result)
            return result
        finally:
            staged.close()


def _manifest(root: Path) -> dict[str, Any]:
    path = root / "MANIFEST.json"
    try:
        manifest = validation.decode(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"{path}: {error}") from None
    if not isinstance(manifest, dict) or manifest.get("format") != "jsonl":
        raise ValueError(f"{path}: re-export the same store with --format jsonl")
    counts = manifest.get("tables")
    if not isinstance(counts, dict) or set(counts) != set(TABLES):
        raise ValueError(f"{path}: tables must name all six exported tables")
    if any(type(count) is not int or count < 0 for count in counts.values()):
        raise ValueError(f"{path}: table counts must be nonnegative integers")
    return manifest


def _lines(path: Path, observation: bool = False) -> Iterator[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as handle:
            for number, text in enumerate(handle, 1):
                if not text.strip():
                    raise ValueError(f"{path} line {number}: blank row")
                row = validation.decode(text)
                if not isinstance(row, dict):
                    raise ValueError(f"{path} line {number}: row must be an object")
                yield _decoded(row) if observation else row
    except (OSError, UnicodeError) as error:
        raise ValueError(f"{path}: {error}") from None


def _decoded(row: dict[str, Any]) -> dict[str, Any]:
    """One observation row with its JSON columns decoded and its shape checked."""
    for name in validation.JSON_FIELDS:
        if name in row and isinstance(row[name], str):
            row[name] = validation.decode(row[name])
    validation.observation(row)
    return row


def _stage(
    store: Store,
    root: Path,
    manifest: dict[str, Any],
    staged: sqlite3.Connection,
) -> Counter[str]:
    drift = _stage_observations(store, root, manifest, staged)
    for table in TABLES[1:]:
        _stage_table(store, root, manifest, staged, table)
    staged.commit()
    return drift


def _stage_observations(
    store: Store, root: Path, manifest: dict[str, Any], staged: sqlite3.Connection
) -> Counter[str]:
    """Every observation row, one capture group at a time, validated then staged."""
    drift: Counter[str] = Counter()
    closed: set[str] = set()
    count = 0
    rows = _lines(root / "observations.jsonl", observation=True)
    for capture, group in itertools.groupby(rows, key=lambda row: row["capture_id"]):
        if capture in closed:
            raise ValueError(f"capture {capture!r} appears in separate blocks")
        closed.add(capture)
        captured = list(group)
        levels = validation.levels(captured)
        for row in captured:
            drift.update(validation.payload(row, levels))
        count += _stage_rows(store, staged, "observations", captured)
    _count(manifest, "observations", count)
    return drift


def _stage_table(
    store: Store,
    root: Path,
    manifest: dict[str, Any],
    staged: sqlite3.Connection,
    table: str,
) -> None:
    """Count one derived table's rows against the manifest; stage diagnostics only."""
    count = 0
    pending: list[dict[str, Any]] = []
    for row in _lines(root / f"{table}.jsonl"):
        count += 1
        if table != "diagnostics":
            continue
        _diagnostic(row)
        pending.append(row)
        if len(pending) == BATCH:
            _stage_rows(store, staged, table, pending)
            pending.clear()
    if pending:
        _stage_rows(store, staged, table, pending)
    _count(manifest, table, count)


def _count(manifest: dict[str, Any], table: str, count: int) -> None:
    expected = manifest["tables"][table]
    if count != expected:
        raise ValueError(
            f"{table}: manifest declares {expected} rows, file contains {count}"
        )


def _diagnostic(row: dict[str, Any]) -> None:
    if set(row) != _DIAGNOSTIC_COLUMNS:
        raise ValueError("diagnostic columns do not match the stored schema")
    for name, value in row.items():
        if name in {"capture_id", "observation_id"} and value is None:
            continue
        validation._text(name, value)
    if row["kind"] not in DIAGNOSTIC_KINDS:
        raise ValueError(f"unknown diagnostic kind {row['kind']!r}")
    if len(row["detail"]) > MAX_DETAIL:
        raise ValueError("diagnostic detail exceeds the stored bound")


def _existing(store: Store, kind: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not store.path.exists():
        return {}
    rows = (
        store.observations_by_id(ids)
        if kind == "observations"
        else store.diagnostics_by_id(ids)
    )
    key = "observation_id" if kind == "observations" else "diagnostic_id"
    return {row[key]: row for row in rows}


def _stage_rows(
    store: Store,
    staged: sqlite3.Connection,
    kind: str,
    rows: list[dict[str, Any]],
) -> int:
    key = "observation_id" if kind == "observations" else "diagnostic_id"
    for offset in range(0, len(rows), BATCH):
        batch = rows[offset : offset + BATCH]
        existing = _existing(store, kind, [row[key] for row in batch])
        for row in batch:
            identity = row[key]
            previous = existing.get(identity)
            if previous is not None and previous != row:
                raise ValueError(f"{kind}: conflicting existing identity {identity!r}")
            try:
                staged.execute(
                    "INSERT INTO incoming VALUES (?, ?, ?, ?, ?)",
                    (
                        kind,
                        identity,
                        row["capture_id"],
                        to_json(row),
                        int(previous is None),
                    ),
                )
            except sqlite3.IntegrityError:
                raise ValueError(f"{kind}: duplicate identity {identity!r}") from None
    return len(rows)


def _result(
    root: Path,
    manifest: dict[str, Any],
    staged: sqlite3.Connection,
    drift: Counter[str],
) -> dict[str, Any]:
    groups = dict(
        staged.execute(
            "SELECT fresh, count(*) FROM incoming"
            " WHERE kind = 'observations' GROUP BY fresh"
        )
    )
    captures = dict(
        staged.execute(
            "SELECT fresh, count(DISTINCT capture) FROM incoming"
            " WHERE kind = 'observations' GROUP BY fresh"
        )
    )
    return {
        "root": str(root),
        "exported_at": manifest.get("exported_at"),
        "captures": captures.get(1, 0),
        "skipped_captures": captures.get(0, 0),
        "observations": groups.get(1, 0),
        "skipped": groups.get(0, 0),
        "drift": dict(drift),
        "commands": {},
        "added": 0,
        "diagnostics": 0,
        "diagnostics_skipped": 0,
        "diagnostics_generated": 0,
        "dropped": 0,
        "rebuilt": 0,
    }


def _batches(
    staged: sqlite3.Connection, kind: str, fresh_only: bool = True
) -> Iterator[list[dict[str, Any]]]:
    cursor = staged.execute(
        "SELECT data FROM incoming WHERE kind = ? AND (fresh = 1 OR ? = 0)",
        (kind, int(fresh_only)),
    )
    while rows := cursor.fetchmany(BATCH):
        yield [validation.decode(row[0]) for row in rows]


def _restore(store: Store, staged: sqlite3.Connection, result: dict[str, Any]) -> None:
    for batch in _batches(staged, "observations"):
        accepted = store.append([validation.observation(row) for row in batch])
        _committed(store, "observations", batch, accepted)
        result["added"] += len(batch)
    before = {row["diagnostic_id"] for row in store.diagnostics()}
    captures = staged.execute(
        "SELECT DISTINCT capture FROM incoming"
        " WHERE kind = 'observations' AND fresh = 1"
    )
    for (capture,) in captures:
        store.rebuild(capture)
        result["rebuilt"] += 1
    after = {row["diagnostic_id"] for row in store.diagnostics()}
    result["diagnostics_generated"] = len(after - before)
    for batch in _batches(staged, "diagnostics", fresh_only=False):
        existing = _existing(
            store, "diagnostics", [row["diagnostic_id"] for row in batch]
        )
        pending = [row for row in batch if row["diagnostic_id"] not in existing]
        result["diagnostics_skipped"] += len(batch) - len(pending)
        for row in pending:
            store.diagnose(
                row["kind"],
                row["detail"],
                capture_id=row["capture_id"],
                observation_id=row["observation_id"],
                diagnostic_id=row["diagnostic_id"],
                ingest_ts=row["ingest_ts"],
            )
        _committed(store, "diagnostics", batch, len(batch))
        result["diagnostics"] += len(pending)


def _committed(
    store: Store, kind: str, batch: list[dict[str, Any]], accepted: int
) -> None:
    if accepted != len(batch) or not store.flush():
        raise ValueError(
            f"{kind}: store did not commit the import batch; retry the import"
        )
    key = "observation_id" if kind == "observations" else "diagnostic_id"
    actual = _existing(store, kind, [row[key] for row in batch])
    if len(actual) != len(batch) or any(actual[row[key]] != row for row in batch):
        raise ValueError(
            f"{kind}: committed rows differ from the validated batch; retry the import"
        )
