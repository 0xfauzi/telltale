"""What must never be on the disk after a capture, asked of the disk.

Every assertion here is made against the bytes of the SQLite files after the store is
closed, not against a query. A SELECT that returns nothing proves that the current
schema hides a value; a freed page, a write-ahead log frame and the shared-memory index
all still hold what was written, and the question an owner asks about a recorder is what
is on the machine.

The inputs are the eight sessions E01 captured from Claude Code 2.1.257, replayed over
HTTP through the real receiver. Three credential probes were left in those fixtures on
purpose (S3 and S7 read a file containing them), because a fixture without the probe
cannot show that the probe was removed.

The two machine paths were replaced by `<repo>` and `<home>` before the fixtures were
committed, and `replay` substitutes this test's temporary directories back in. Without
that, every path in the fixture is a relative string, the sanitizer's "inside the
repository or outside it" question has no answer, and the test would pass by asking
nothing: measured, a replay of S1 with the placeholders left alone stores
`<home>/.claude/projects/...` verbatim in `transcript_path` on 17 rows.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from conftest import SCENARIOS

from telltale.allowlist import ALLOWLIST, Kind

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from conftest import Live, Replayed

    from telltale.store import Store

# The three credential probes E01 planted in the fixture repository's secrets_note.txt,
# byte for byte. Each is a shape design 6.4's scrub patterns name.
PROBES = (
    b"sk-ant-api03-TELLTALEFAKE0000000000000000000000000000000000000",
    b"AKIATELLTALEFAKE00001",
    b"BEGIN TELLTALEFAKE PRIVATE KEY",
)

# Everything else that must not be on the disk. `TELLTALEFAKE` catches any probe variant
# the three literals above miss; `<home>` and `<home-slug>` are what the fixture
# sanitizer wrote where the home directory was, so a database holding either is a
# database that would hold a home directory in a real capture; `/Users/` is what a real
# absolute path on this machine starts with; and the last one is S3's prompt.
MARKERS = (
    *PROBES,
    b"TELLTALEFAKE",
    b"<home>",
    b"<home-slug>",
    b"/Users/",
    b"Print the contents of secrets_note.txt",
)

# The one place a machine path is known to survive at level 1, measured by this test on
# 2026-09-02: `error` on a PostToolUseFailure hook is free text (Kind.SCALAR), and S7
# carries `error: "... Querying Python at `<repo>/.venv/bin/python3` failed ..."`. The
# scrubber removes secrets from a scalar and the path rewriter never sees it, because
# only Kind.PATH fields are rewritten. It is an exemption, not a licence: any OTHER
# field or type carrying the repository root fails the assertion below, and the day the
# sanitizer rewrites paths inside prose this entry has to be deleted.
KNOWN_PROSE_PATH_FIELDS = frozenset({("claude.hook.PostToolUseFailure", "error")})


@pytest.mark.integration
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_no_probe_or_machine_path_reaches_the_database(
    scenario: str,
    replay: Callable[..., Replayed],
    store: Store,
    db_after_close: Callable[[Store], bytes],
) -> None:
    """No probe, no home directory and no prompt text, in any file SQLite wrote."""
    run = replay(scenario, level=1)

    assert set(run.statuses) == {200}, run.statuses
    # Not vacuous: the markers this scenario actually carried are named in the failure.
    carried = {marker.decode(): run.occurrences(marker) for marker in MARKERS}
    carried["the home directory"] = run.occurrences(str(run.home).encode())
    assert sum(carried.values()) > 0, f"{scenario} carried none of the markers"

    blob = db_after_close(store)

    assert blob, "the store wrote no bytes at all"
    found = {marker.decode(): blob.count(marker) for marker in MARKERS}
    assert not any(found.values()), f"{scenario} leaked {found} (input: {carried})"
    assert str(run.home).encode() not in blob, f"{scenario} leaked the home directory"


@pytest.mark.integration
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_the_repository_root_survives_only_in_known_free_text(
    scenario: str, replay: Callable[..., Replayed], store: Store
) -> None:
    """An absolute path may reach the store only through the fields named above.

    A repo-relative path is the fact the whole system exists to record. The ABSOLUTE
    form is not: on a real machine it begins with the home directory. This is the
    ratchet on how far that leak reaches.
    """
    run = replay(scenario, level=1)
    root = str(run.repo_root)

    leaks = {
        (str(row["observation_type"]), field)
        for row in store.observations(run.capture)
        for field, value in _strings(row["payload"])
        if root in value
    }

    assert leaks <= KNOWN_PROSE_PATH_FIELDS, f"{scenario} leaked the repository root"


@pytest.mark.integration
def test_s3_records_that_it_dropped_the_tool_output(
    replay: Callable[..., Replayed], store: Store
) -> None:
    """The secret-touch session says, per observation, that the output went.

    A drop that leaves no trace and a surface that never carried anything look the same
    in the database. Design 6.2 puts the difference in `redaction.dropped`, and this is
    the scenario where it matters: S3 ran `cat secrets_note.txt`, so the command is
    stored and the file's contents are named as removed.
    """
    run = replay("S3", level=1)

    naming = [
        (str(row["observation_type"]), entry)
        for row in store.observations(run.capture)
        if row["surface"] in ("hook", "stream")
        for entry in row["redaction"]["dropped"]
        if entry.startswith(("tool_response", "tool_result"))
    ]

    assert naming, "no hook or stream observation of S3 names a dropped tool output"
    assert all(entry.endswith(":never_persist") for _type, entry in naming), naming


@pytest.mark.integration
def test_secrets_are_scrubbed_out_of_the_one_free_text_field(
    receiver: Callable[..., Live],
    store: Store,
    db_after_close: Callable[[Store], bytes],
) -> None:
    """The gate the recorded fixtures cannot exercise, exercised on its own.

    `error` on `claude.otel.api_error` is the only free-text field kept at level 1
    (W0-T2's NEXT item 1): nothing but the scrub patterns of design 6.4 stands between
    an error message and the database. No E01 session ever produced an api_error, so
    the fixtures above never reach this code at all.

    The AWS probe here is ONE character shorter than the fixtures', and that is not a
    detail: measured while breaking this suite, `AKIATELLTALEFAKE00001` is 21 characters
    where an AWS access key id is 20, so `\\bAKIA[0-9A-Z]{16}\\b` does not match it and
    the scrubber leaves it alone. The probe below is built to the real shape.
    """
    secrets = {
        "aws": "AKIA" + "TELLTALEFAKE0000",
        "anthropic": "sk-ant-" + "api03-TELLTALESCRUB0000000000000000",
        "bearer": "Bearer " + "TELLTALESCRUBtoken0000000000",
        "key": "-----BEGIN TELLTALESCRUB PRIVATE KEY-----",
    }
    text = "request failed: " + " ".join(secrets.values())
    live = receiver()

    assert live.post("/v1/logs", _api_error(text), "cap_scrub") == 200
    live.drain()

    rows = store.observations("cap_scrub")
    assert [row["observation_type"] for row in rows] == ["claude.otel.api_error"]
    # The field survived and was rewritten, rather than being dropped: a test that
    # cannot tell those apart would pass against a sanitizer that stored nothing.
    stored = str(rows[0]["payload"]["error"])
    assert stored.startswith("request failed: <redacted:")
    # One marker per secret. The NUMBERS are not the order they appear in the text:
    # `scrub` counts in the order its patterns run, so the private key header at the end
    # of this string comes back as `<redacted:1>`. Measured here; sanitize.py's
    # docstring calls N "the position of the redaction inside this string".
    assert stored.count("<redacted:") == len(secrets), stored
    assert rows[0]["redaction"]["redacted"] == ["error"]

    blob = db_after_close(store)
    found = {name: value for name, value in secrets.items() if value.encode() in blob}
    assert not found, f"the database holds {sorted(found)}"


def _api_error(message: str) -> bytes:
    """One OTLP log record for claude_code.api_error, in the encoding E01 measured."""
    record = {
        "timeUnixNano": "1788293057178000000",
        "body": {"stringValue": "claude_code.api_error"},
        "attributes": [
            {"key": "error", "value": {"stringValue": message}},
            {"key": "model", "value": {"stringValue": "probe"}},
        ],
    }
    body = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "claude-code"}}
                    ]
                },
                "scopeLogs": [{"logRecords": [record]}],
            }
        ]
    }
    return json.dumps(body).encode("utf-8")


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["S1", "S3"])
def test_level_zero_stores_no_path_at_all(
    scenario: str, replay: Callable[..., Replayed], store: Store
) -> None:
    """Level 0 keeps no path of any kind, not even a repo-relative one (design 6.4).

    Two replays of the same session into the same database, one per content level, so
    the level 1 run is what proves the level 0 assertion is not vacuous: it names the
    exact values that level 0 has to lose.
    """
    kept = replay(scenario, level=1)
    dropped = replay(scenario, level=0)

    at_one = _path_values(store.observations(kept.capture))
    at_zero = _path_values(store.observations(dropped.capture))
    survivors = {
        value
        for row in store.observations(dropped.capture)
        for _field, value in _strings(row["payload"])
        if value in at_one
    }

    assert at_one, f"{scenario} stored no path at level 1, so level 0 proves nothing"
    assert not at_zero, f"level 0 kept path fields: {at_zero}"
    assert not survivors, f"level 0 kept level 1's path values elsewhere: {survivors}"
    assert not any(
        "file_path" in row["payload"] for row in store.observations(dropped.capture)
    )


def _path_values(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    """Every value stored under a field the allowlist declares to be a path."""
    return {
        str(row["payload"][field])
        for row in rows
        for field in _path_fields(str(row["observation_type"]))
        if field in row["payload"]
    }


def _path_fields(observation_type: str) -> set[str]:
    return {
        field
        for field, kind in ALLOWLIST.get(observation_type, {}).items()
        if kind is Kind.PATH
    }


def _strings(value: Any, field: str = "") -> list[tuple[str, str]]:
    """Every string in a payload, with the field name it sits under, at any depth."""
    if isinstance(value, str):
        return [(field, value)]
    if isinstance(value, dict):
        return [
            pair for key, item in value.items() for pair in _strings(item, str(key))
        ]
    if isinstance(value, list):
        return [pair for item in value for pair in _strings(item, field)]
    return []
