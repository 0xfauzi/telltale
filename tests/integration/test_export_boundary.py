"""Exercise export trust boundaries through real temporary stores and files."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from telltale import export
from telltale.model import Observation, now_iso, to_json
from telltale.sanitize import Ctx, sanitize
from telltale.store import Store

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def _seed(store: Store, count: int = 1) -> list[Observation]:
    payload, redaction, _ = sanitize(
        "claude.hook.PreToolUse", {"tool_name": "Bash", "command": "pwd"}, 1, Ctx()
    )
    original = Observation(
        observation_id="obs_0",
        capture_id="cap_boundary",
        observation_type="claude.hook.PreToolUse",
        surface="hook",
        provider="claude",
        adapter="test",
        ingest_ts=now_iso(),
        payload=payload,
        redaction=redaction,
    )
    rows = [replace(original, observation_id=f"obs_{i:04d}") for i in range(count)]
    assert store.append(rows) == count
    assert store.flush()
    return rows


def _rows(folder: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (folder / "observations.jsonl").read_text().splitlines()
    ]


def _write(folder: Path, rows: list[dict[str, Any]]) -> None:
    (folder / "observations.jsonl").write_text(
        "".join(to_json(row) + "\n" for row in rows)
    )
    path = folder / "MANIFEST.json"
    manifest = json.loads(path.read_text())
    manifest["tables"]["observations"] = len(rows)
    path.write_text(to_json(manifest))


@pytest.mark.parametrize(
    "attack",
    [
        {"extra": {"prompt": "private request", "key": "sk-" + "A" * 45}},
        {"command": "cat /Users/alice/private.txt"},
        {"command": "TOKEN=private_value python"},
        {"command": "echo " + "x" * 600},
        {"normalization_version": "private version text"},
    ],
)
def test_import_refuses_unsanitized_fields_before_writes(
    store: Store,
    tmp_path: Path,
    attack: dict[str, Any],
) -> None:
    _seed(store)
    folder = tmp_path / "export"
    export.export(store, folder, "jsonl")
    rows = _rows(folder)
    payload = json.loads(rows[0]["payload"])
    payload.update(attack)
    rows[0]["payload"] = to_json(payload)
    _write(folder, rows)
    far = Store(tmp_path / "far.db").open()
    try:
        for dry_run in (True, False):
            with pytest.raises(
                ValueError, match=r"obs_|columns|schema_version|manifest|duplicate"
            ):
                export.import_export(far, folder, dry_run=dry_run)
            assert far.flush()
            assert far.captures() == []
    finally:
        far.close()


@pytest.mark.parametrize(
    "damage", ["duplicate", "late_column", "late_type", "manifest", "json_key"]
)
def test_import_validates_all_rows_before_destination_writes(
    store: Store,
    tmp_path: Path,
    damage: str,
) -> None:
    _seed(store, 501)
    folder = tmp_path / "export"
    export.export(store, folder, "jsonl")
    rows = _rows(folder)
    if damage == "duplicate":
        rows[-1] = rows[0]
    elif damage == "late_column":
        del rows[-1]["surface"]
    elif damage == "late_type":
        rows[-1]["schema_version"] = "invalid"
    elif damage == "manifest":
        rows.pop()
    elif damage == "json_key":
        rows[-1]["payload"] = '{"tool_name":"Bash","tool_name":"hidden"}'
    if damage == "manifest":
        (folder / "observations.jsonl").write_text(
            "".join(to_json(row) + "\n" for row in rows)
        )
    else:
        _write(folder, rows)
    far = Store(tmp_path / "far.db").open()
    try:
        for dry_run in (True, False):
            with pytest.raises(
                ValueError, match=r"duplicate|columns|schema_version|manifest"
            ):
                export.import_export(far, folder, dry_run=dry_run)
            assert far.flush()
            assert far.captures() == []
    finally:
        far.close()


def test_import_resumes_observations_and_preserves_diagnostic_identities(
    store: Store,
    tmp_path: Path,
) -> None:
    observations = _seed(store, 2)
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    for identity in ("diag_one", "diag_two"):
        store.diagnose(
            "parse_failure", "same detail", diagnostic_id=identity, ingest_ts=old
        )
    assert store.flush()
    folder = tmp_path / "export"
    export.export(store, folder, "jsonl")
    far = Store(tmp_path / "far.db").open()
    try:
        far.append(observations[:1])
        assert far.flush()
        result = export.import_export(far, folder)
        assert result["added"] == 1
        assert result["skipped"] == 1
        assert result["diagnostics"] == 2
        assert far.observations("cap_boundary") == store.observations("cap_boundary")
        assert far.diagnostics_by_id(
            ["diag_one", "diag_two"]
        ) == store.diagnostics_by_id(["diag_one", "diag_two"])
        again = export.import_export(far, folder)
        assert again["added"] == again["diagnostics"] == again["rebuilt"] == 0
        assert again["diagnostics_skipped"] == 2
        assert far.purge_diagnostics(1) == 2
    finally:
        far.close()


def test_conflicting_existing_observation_refuses_before_new_rows(
    store: Store,
    tmp_path: Path,
) -> None:
    observations = _seed(store, 2)
    folder = tmp_path / "export"
    export.export(store, folder, "jsonl")
    far = Store(tmp_path / "far.db").open()
    try:
        far.append([replace(observations[-1], adapter="different")])
        assert far.flush()
        with pytest.raises(ValueError, match="conflicting existing identity"):
            export.import_export(far, folder)
        assert len(far.observations("cap_boundary")) == 1
    finally:
        far.close()


def test_export_uses_one_snapshot_for_all_tables(store: Store, tmp_path: Path) -> None:
    observations = _seed(store)
    folder = tmp_path / "export"
    folder.mkdir()
    fifo = folder / "activities.jsonl"
    os.mkfifo(fifo)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(export.export, store, folder, "jsonl")
        path = folder / "observations.jsonl"
        deadline = time.monotonic() + 5
        while (
            not path.exists() or not path.stat().st_size
        ) and time.monotonic() < deadline:
            time.sleep(0.001)
        store.append([replace(observations[0], observation_id="obs_after")])
        store.diagnose("launcher", "later", observation_id="obs_after")
        assert store.flush()
        with fifo.open() as reader:
            reader.read()
        counts = future.result(timeout=5)
    assert counts["observations"] == 1
    assert counts["diagnostics"] == 0


def test_retention_reports_corrupt_store(telltale_home: Path) -> None:
    from telltale.doctor import retention

    (telltale_home / "telltale.db").write_bytes(b"not a database")
    assert "could not be read" in retention()


def test_legacy_fields_and_bounded_stored_commands_roundtrip(
    store: Store,
    tmp_path: Path,
) -> None:
    rows = _seed(store)
    legacy = replace(
        rows[0],
        observation_id="obs_legacy",
        observation_type="claude.stream.assistant",
        payload={"output_tokens": 12},
    )
    bounded = ("echo " + "_ " * 96 + "<outside>/abcdef12")[:200]
    command = replace(
        rows[0],
        observation_id="obs_bounded",
        payload={"command": bounded, "normalization_version": "cmdnorm-v3"},
    )
    store.append([legacy, command])
    assert store.flush()
    folder = tmp_path / "export"
    export.export(store, folder, "jsonl")
    far = Store(tmp_path / "far.db").open()
    try:
        result = export.import_export(far, folder)
        assert result["added"] == 3
        assert result["drift"] == {"claude.stream.assistant.output_tokens": 1}
        assert far.observations("cap_boundary") == store.observations("cap_boundary")
    finally:
        far.close()
