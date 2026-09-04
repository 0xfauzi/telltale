"""`telltale export`, the trip back into an empty store, retention and `schema`.

Every store here is a real Store with its writer thread, every capture is a real replay
of E01's and E02's recorded bytes through a real receiver on a real port, and the round
trip goes through `export.export` and `export.import_export` rather than through a
fixture that hands one the other's dict. What is being tested is that a directory of
files on the disk is enough to rebuild a store, so the file has to be on the disk.

Two things this file cannot test, and both are named rather than left as a gap.

  A diagnostics row older than the retention window. `store.diagnose` stamps
  `now_iso()` and the store offers no clock a test may set, so the only way to make a
  row two days old is to wait two days. `test_purge_diagnostics_window` therefore
  proves the cutoff arithmetic (a fresh row survives `--diagnostics-older-than 1`, and
  the printed cutoff is a day back), the refusal of 0, of a negative N and of both or
  neither argument, and stops there. Deleting an old row is exercised by hand on a copy
  of the owner's store; docs/log/W6-T3.md holds the counts.

  An absolute path written by hand into a `command` field of an export file. The gate
  cannot refuse it, because `commands.normalize` is not idempotent on its own output
  and a changed COMMAND value is therefore not evidence of anything. The comment above
  the round-trip section of export.py measures that and says so. Every other leak IS
  tested: `test_a_planted_prompt_refuses_the_file` plants a never-persist field and
  asserts the text never reaches the far store's disk.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from telltale import export, measures, report
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from conftest import Replayed

pytestmark = pytest.mark.integration

# The two recorded sessions this file replays, one per provider. S1 is the scenario both
# experiments recorded first and the one every other test file reaches for.
SCENARIOS = (("claude", "S1"), ("codex", "S1"))
# The tables whose rows are derived and must therefore carry a claim class out of the
# export, and the one whose rows carry a reducer version instead (design 6.5 gives
# series_snapshots no claim_class column: a snapshot is not a claim, it is an input).
DERIVED = ("activities", "evidence", "forecast_runs")


def _replay_both(replay: Callable[..., Replayed], level: int = 1) -> list[str]:
    """Both scenarios, each into a capture of its own.

    The capture id is named here rather than left to the fixture's default, which is
    `<scenario>-L<level>` and is therefore the SAME id for both providers' S1: the two
    replays would land in one capture and every count below would be of the pair.
    """
    return [
        replay(
            scenario,
            level=level,
            provider=provider,
            capture=f"{provider}-{scenario}-L{level}",
        ).capture
        for provider, scenario in SCENARIOS
    ]


def _reduce(store: Store, captures: list[str]) -> None:
    """Settle the writer and build the derived rows, the way `telltale rebuild` does."""
    store.flush()
    for capture in captures:
        store.rebuild(capture)


def _counts(store: Store, capture: str) -> dict[str, int]:
    counted: dict[str, int] = {}
    for row in store.observations(capture):
        obs_type = str(row["observation_type"])
        counted[obs_type] = counted.get(obs_type, 0) + 1
    return counted


def _empty_store(tmp_path: Path, name: str = "far-side") -> Store:
    """A second store in a directory of its own, with nothing in it."""
    return Store(tmp_path / name / "telltale.db").open()


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_export_writes_every_table_and_a_manifest(
    store: Store, replay: Callable[..., Replayed], tmp_path: Path
) -> None:
    """One file per table, one line per row, and a manifest that counts them."""
    captures = _replay_both(replay)
    _reduce(store, captures)
    out = tmp_path / "out"

    counts = export.export(store, out, "jsonl")

    assert set(counts) == set(export.TABLES)
    for table, count in counts.items():
        lines = (out / f"{table}.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == count, table
    manifest = json.loads((out / export.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["tables"] == counts
    assert manifest["format"] == "jsonl"
    assert manifest["capture"] is None
    assert manifest["exported_at"].endswith("Z")
    # The reducer version of the rows that were just built, read off the file rather
    # than off the reducer: a manifest naming a version no row carries is worse than
    # none, because it is the field a later reader would trust to compare two exports.
    assert manifest["reducer_versions"]["activities"] == sorted(
        {str(row["reducer_version"]) for row in _rows(out / "activities.jsonl")}
    )
    assert counts["observations"] == sum(
        len(store.observations(capture)) for capture in captures
    )


def test_every_derived_row_carries_its_claim_class(
    store: Store, replay: Callable[..., Replayed], tmp_path: Path
) -> None:
    """Invariant 6 travels with the file. A derived row with no claim class beside it
    is a row a reader may take for an observation."""
    captures = _replay_both(replay)
    _reduce(store, captures)
    out = tmp_path / "out"

    export.export(store, out, "jsonl")

    for table in DERIVED:
        rows = _rows(out / f"{table}.jsonl")
        assert rows or table == "forecast_runs", f"{table} exported nothing"
        assert all(row.get("claim_class") for row in rows), table
    assert {row["claim_class"] for row in _rows(out / "activities.jsonl")} == {
        "derived"
    }
    assert all(
        row.get("reducer_version") for row in _rows(out / "series_snapshots.jsonl")
    )


def test_round_trip_into_an_empty_store(
    store: Store,
    replay: Callable[..., Replayed],
    tmp_path: Path,
    settled: Callable[[Store], Store],
) -> None:
    """Export both captures, import them into a store that has never seen one, and get
    the same observations, the same summary and, on a second run, nothing."""
    captures = _replay_both(replay)
    _reduce(store, captures)
    before = {capture: _counts(store, capture) for capture in captures}
    summaries = {
        capture: report.show(measures.summary(store, capture)) for capture in captures
    }
    out = tmp_path / "out"
    export.export(settled(store), out, "jsonl")

    far = _empty_store(tmp_path)
    try:
        result = export.import_export(far, out)
        assert result["captures"] == len(captures)
        assert result["added"] == result["observations"]
        assert result["dropped"] == 0
        far.flush()
        assert {str(row["capture_id"]) for row in far.captures()} == set(captures)
        for capture in captures:
            assert _counts(far, capture) == before[capture], capture
            assert report.show(measures.summary(far, capture)) == summaries[capture]
        # A second import of the same directory. observation_id is the primary key and
        # a capture already stored is skipped whole, so this adds nothing at all.
        again = export.import_export(far, out)
        assert again["captures"] == 0
        assert again["added"] == 0
        assert again["skipped"] == result["observations"]
        far.flush()
        assert sum(len(far.observations(c)) for c in captures) == result["observations"]
    finally:
        far.close()


def test_round_trip_of_a_level_0_capture(
    store: Store,
    replay: Callable[..., Replayed],
    tmp_path: Path,
    settled: Callable[[Store], Store],
) -> None:
    """A payload sanitized at level 0 passes the sanitizer at level 0 again.

    The gate is what asserts it. A capture replayed through the receiver carries no
    `telltale.capture_started` row to read a level off, so `export._levels_of` checks
    every row at both levels the sanitizer distinguishes and the import refuses unless
    both agree. Break the gate and this test still passes; plant a `prompt` field in the
    file and it fails, which is the pair docs/log/W6-T3.md pastes.
    """
    captures = _replay_both(replay, level=0)
    _reduce(store, captures)
    out = tmp_path / "out"
    export.export(settled(store), out, "jsonl")

    far = _empty_store(tmp_path)
    try:
        result = export.import_export(far, out)
        assert result["added"] == result["observations"]
        assert result["added"] > 0
        far.flush()
        assert {str(row["capture_id"]) for row in far.captures()} == set(captures)
    finally:
        far.close()


def test_a_planted_prompt_refuses_the_file(
    store: Store,
    replay: Callable[..., Replayed],
    tmp_path: Path,
    settled: Callable[[Store], Store],
) -> None:
    """One never-persist field written into the export by hand, and nothing is stored.

    `prompt` is in `sanitize.NEVER_PERSIST`, so the store could not have held it and the
    gate says so before a row is appended. The refusal names the observation and the
    field, and the far store is left empty rather than half filled.
    """
    captures = _replay_both(replay)
    _reduce(store, captures)
    out = tmp_path / "out"
    export.export(settled(store), out, "jsonl")
    path = out / "observations.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    planted = json.loads(lines[0])
    payload = json.loads(planted["payload"])
    payload["prompt"] = "refactor the auth module and do not tell anyone"
    planted["payload"] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    lines[0] = json.dumps(planted)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    far = _empty_store(tmp_path)
    try:
        with pytest.raises(ValueError, match="prompt:never_persist") as refusal:
            export.import_export(far, out)
        assert str(planted["observation_id"]) in str(refusal.value)
        far.flush()
        assert far.captures() == []
    finally:
        far.close()
    # The database FILE, not a query: the question is what is on the disk of the
    # machine, and a value a SELECT no longer returns can still sit in a freelist page.
    assert b"do not tell anyone" not in far.path.read_bytes()


def test_purging_one_capture_leaves_the_other_byte_identical(
    store: Store, replay: Callable[..., Replayed]
) -> None:
    """`purge` takes one capture's rows and nothing else. Design 6.5 and 6.13."""
    first, second = _replay_both(replay)
    _reduce(store, [first, second])
    kept = {
        "activities": [json.dumps(row, sort_keys=True, default=str)
                       for row in store.activities(second)],
        "evidence": [json.dumps(row, sort_keys=True, default=str)
                     for row in store.evidence(second)],
    }  # fmt: skip
    assert kept["activities"]
    assert kept["evidence"]

    store.purge(first)
    store.rebuild(second)

    assert store.observations(first) == []
    assert store.activities(first) == []
    assert [
        json.dumps(row, sort_keys=True, default=str) for row in store.activities(second)
    ] == kept["activities"]
    assert [
        json.dumps(row, sort_keys=True, default=str) for row in store.evidence(second)
    ] == kept["evidence"]


def test_export_refuses_a_directory_inside_telltale_home(
    store: Store, telltale_home: Path
) -> None:
    """An export under $TELLTALE_HOME outlives `purge`, which deletes rows and not
    files, so a purged capture would still be on the disk in full."""
    with pytest.raises(ValueError, match="inside TELLTALE_HOME"):
        export.export(store, telltale_home / "out", "jsonl")
    assert not (telltale_home / "out").exists()


def test_export_of_one_capture_carries_only_that_capture(
    store: Store,
    replay: Callable[..., Replayed],
    tmp_path: Path,
    settled: Callable[[Store], Store],
) -> None:
    """`--capture` restricts the four tables that have a capture, and no others."""
    first, second = _replay_both(replay)
    _reduce(store, [first, second])
    out = tmp_path / "one"

    counts = export.export(settled(store), out, "jsonl", first)

    assert counts["observations"] == len(store.observations(first))
    assert counts["activities"] == len(store.activities(first))
    for table in ("observations", "activities", "evidence", "diagnostics"):
        rows = _rows(out / f"{table}.jsonl")
        assert {row["capture_id"] for row in rows} <= {first}, table
    assert json.loads((out / export.MANIFEST).read_text())["capture"] == first


def test_parquet_holds_the_same_rows(
    store: Store,
    replay: Callable[..., Replayed],
    tmp_path: Path,
    settled: Callable[[Store], Store],
) -> None:
    """The parquet branch, under `uv run --extra export`. Skipped where it is
    absent, which is CI: a plain `uv sync` installs no extra, and the jsonl half of
    this command must not care whether pyarrow is there."""
    parquet = pytest.importorskip(
        "pyarrow.parquet", reason="pyarrow is the `export` extra, never synced"
    )
    captures = _replay_both(replay)
    _reduce(store, captures)
    settled(store)
    as_jsonl = export.export(store, tmp_path / "j", "jsonl")

    as_parquet = export.export(store, tmp_path / "p", "parquet")

    assert as_parquet == as_jsonl
    for table, count in as_parquet.items():
        path = tmp_path / "p" / f"{table}.parquet"
        assert path.is_file(), table
        assert parquet.ParquetFile(path).metadata.num_rows == count, table
    activities = parquet.read_table(tmp_path / "p" / "activities.parquet")
    assert "claim_class" in activities.schema.names
    assert json.loads((tmp_path / "p" / export.MANIFEST).read_text())["format"] == (
        "parquet"
    )


def test_the_round_trip_refuses_a_parquet_directory(
    store: Store,
    replay: Callable[..., Replayed],
    tmp_path: Path,
    settled: Callable[[Store], Store],
) -> None:
    """`import export` reads jsonl. A parquet directory is refused by name, not by a
    stack trace from a JSON parser handed a parquet file."""
    pytest.importorskip("pyarrow.parquet", reason="pyarrow is the `export` extra")
    _reduce(store, _replay_both(replay))
    out = tmp_path / "p"
    export.export(settled(store), out, "parquet")

    far = _empty_store(tmp_path)
    try:
        with pytest.raises(ValueError, match="re-export the same store with --format"):
            export.import_export(far, out)
    finally:
        far.close()


def test_purge_diagnostics_window(
    store: Store, telltale_home: Path, tmp_path: Path
) -> None:
    """`purge --diagnostics-older-than N` and the four ways it refuses.

    The removal of a genuinely old row is NOT here: see this file's docstring. What is
    here is that a row written now survives a one-day window, which is the half that
    says the cutoff is `now - N` and not `now`.
    """
    from conftest import telltale_cli

    store.diagnose("parse_failure", "a fresh row", capture_id="cap_export_test")
    store.flush()
    store.close()

    printed = telltale_cli(
        "purge", "--diagnostics-older-than", "1", home=telltale_home, cwd=tmp_path
    )

    assert "purged 0 diagnostics row(s) with ingest_ts before " in printed
    assert len(Store(telltale_home / "telltale.db").diagnostics()) == 1
    from telltale.cli import main

    assert main(["purge", "--diagnostics-older-than", "0"]) == 2
    assert main(["purge", "--diagnostics-older-than", "-1"]) == 2
    assert main(["purge"]) == 2
    assert main(["purge", "cap_export_test", "--diagnostics-older-than", "3"]) == 2
    assert len(Store(telltale_home / "telltale.db").diagnostics()) == 1


def test_doctor_prints_retention_and_the_exit_code_ignores_it(
    store: Store, telltale_home: Path, tmp_path: Path
) -> None:
    """A fourth block, and never a reason to fail. Design 6.13's rule for the tool rows
    is the rule here: a store holding old diagnostics is a store doing its job."""
    from conftest import telltale_cli

    store.diagnose("unknown_field", "one field nobody listed", capture_id="cap_doc")
    store.flush()
    store.close()

    printed = telltale_cli("doctor", home=telltale_home, cwd=tmp_path)

    assert "retention: 1 diagnostics in " in printed
    assert "unknown_field" in printed.split("retention:")[1]
    assert "purge --diagnostics-older-than N removes rows older than N days" in printed
    assert "doctor: 8 surfaces round-trip" in printed
    # doctor exits 0 (telltale_cli asserts it) and wrote nothing to the real store.
    assert len(Store(telltale_home / "telltale.db").diagnostics()) == 1


def test_schema_prints_the_allowlist_and_the_shapes(
    telltale_home: Path, tmp_path: Path
) -> None:
    """`telltale schema` is read off the tables and the dataclasses, never restated.

    Through the console script, and with no database in $TELLTALE_HOME: `schema` is a
    statement about the code rather than about a store, so it must answer on a machine
    that has never recorded anything.
    """
    from conftest import telltale_cli

    from telltale.allowlist import ALLOWLIST
    from telltale.model import CLAIM_CLASSES, COVERAGE

    printed = json.loads(telltale_cli("schema", home=telltale_home, cwd=tmp_path))

    assert not (telltale_home / "telltale.db").exists()
    assert set(printed["observation_types"]) == set(ALLOWLIST)
    assert printed["observation_types"]["claude.otel.api_request"]["model"] == "enum"
    assert printed["claim_classes"] == list(CLAIM_CLASSES)
    assert printed["coverage"] == list(COVERAGE)
    assert printed["shapes"]["Observation"][0] == "observation_id"
    assert set(printed["shapes"]) == {"Observation", "Activity", "Evidence", "Series"}
    assert "prompt" in printed["never_persist"]
    assert "command" in printed["kinds"]
