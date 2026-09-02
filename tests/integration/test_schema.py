"""What `Store.open` does to a database that already exists. Design 6.5.

Every other statement in schema.py is `CREATE ... IF NOT EXISTS`, which is its own
proof: any test that opens a store exercises it, and a missing table fails everything.
A DROP is not like that. Its whole effect is on a database somebody ALREADY has, and no
other test in this suite has one: the fixtures all start from an empty file, so a
migration could be deleted tomorrow and the suite would stay green while every existing
store kept paying for the index.

A new file rather than a case inside another, because this asks a question none of the
others do: not what the store records, but what it does to the file it opens.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from telltale.store import Store

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

# The index W3-T3 dropped. A strict prefix of obs_by_type_capture, and no SELECT in src/
# filters on observation_type alone, so it cost the writer 6.1 per cent of the write
# path to answer nothing (schema.py has the measurement).
DROPPED = "obs_by_type"
KEPT = ("obs_by_capture", "obs_by_session", "obs_by_type_capture")


def _indexes(path: Path) -> set[str]:
    """The named indexes on `observations`, read with sqlite3 and not through Store."""
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
            " AND tbl_name = 'observations' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def test_an_existing_store_loses_the_index_nobody_reads(telltale_home: Path) -> None:
    """An old store carries obs_by_type; one open removes it and keeps the others.

    The index is put back by hand first, which is what a database written before this
    change looks like. Then the store is opened, which is the only thing a person has to
    do: there is no migrate command and there is no version column, because the schema
    is brought up to date by the DDL that every open already runs.

    Break it by deleting the `DROP INDEX IF EXISTS obs_by_type` line from schema.py and
    the index survives the open, which is the state every store on disk is in today.
    """
    path = telltale_home / "telltale.db"
    Store(path).open().close()
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE INDEX {DROPPED} ON observations (observation_type)")
    conn.commit()
    conn.close()
    assert DROPPED in _indexes(path)

    Store(path).open().close()

    found = _indexes(path)
    assert DROPPED not in found
    assert set(KEPT) <= found, found
