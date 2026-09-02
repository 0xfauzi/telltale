"""What must never be on the disk after a capture, asked of the disk.

Every assertion here is made against the bytes of the SQLite files after the store is
closed, not against a query. A SELECT that returns nothing proves that the current
schema hides a value; a freed page, a write-ahead log frame and the shared-memory index
all still hold what was written, and the question an owner asks about a recorder is what
is on the machine.

The inputs are the eight sessions E01 captured from Claude Code 2.1.257 and the seven
E02 captured from Codex CLI 0.150.1, replayed over HTTP through the real receiver. The
same three credential probes were left in both sets of fixtures on purpose (each
experiment's S3 reads a file containing them), because a fixture without the probe
cannot show that the probe was removed.

W2-T2 adds the backfill half, and it is the harder one: a transcript and a rollout are
files nobody configured, so nothing was withheld from them. The Edit strings, the Write
contents, the whole of what a command printed and the model's reasoning are all in
there, at full length, and the only thing between them and the database is the parser
refusing to hand a container on. Those fixtures are synthetic and hand-written for
exactly that reason (fixtures/sources/.../transcript and .../rollout-import), with the
same probes planted in the places the owner's own files carry content.

The two experiments hide different things in different places, which is why both are
here. Claude carried the probes in a hook's `tool_response`, in a stream `tool_result`
block and in the assistant's own text. Codex carried them on four surfaces including
the OTel `output` attribute of `codex.tool_result`, with no content switch turned on,
and its rollout carries a unified diff of the file the agent edited.

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
from conftest import CODEX_SCENARIOS, SCENARIOS
from test_import import materialise

from telltale import importer
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

# Every recorded session of both experiments, as (provider, scenario). One list rather
# than two tests, because the question the two assertions below ask is about the
# recorder and not about a provider: a second provider that leaks is the same failure.
CASES = [("claude", name) for name in SCENARIOS] + [
    ("codex", name) for name in CODEX_SCENARIOS
]

# Where each provider puts what a tool printed. Every name here is in
# sanitize.NEVER_PERSIST, and the test below is what says that the drop was RECORDED
# rather than merely happening: a surface that carried nothing and a surface whose
# output was removed look identical in a payload.
OUTPUT_CONTAINERS = {
    "claude": ("tool_response", "tool_result"),
    "codex": ("tool_response", "output"),
}


@pytest.mark.integration
@pytest.mark.parametrize(("provider", "scenario"), CASES)
def test_no_probe_or_machine_path_reaches_the_database(
    provider: str,
    scenario: str,
    replay: Callable[..., Replayed],
    store: Store,
    db_after_close: Callable[[Store], bytes],
) -> None:
    """No probe, no home directory and no prompt text, in any file SQLite wrote."""
    run = replay(scenario, level=1, provider=provider)

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
@pytest.mark.parametrize(("provider", "scenario"), CASES)
def test_the_repository_root_survives_only_in_known_free_text(
    provider: str,
    scenario: str,
    replay: Callable[..., Replayed],
    store: Store,
    settled: Callable[[Store], Store],
) -> None:
    """An absolute path may reach the store only through the fields named above.

    A repo-relative path is the fact the whole system exists to record. The ABSOLUTE
    form is not: on a real machine it begins with the home directory. This is the
    ratchet on how far that leak reaches.

    It is also the assertion that catches a path which is not shaped like one. Codex
    spells a rollout command's cwd as `file://<absolute path>`, which the rewriter does
    not recognise as absolute: without the scheme stripped first it joins the URL to
    the repository root and hands back `file:/Users/...` intact. Broken deliberately,
    this test fails on codex S1, S2, S3, S4, S5 and S6.
    """
    run = replay(scenario, level=1, provider=provider)
    root = str(run.repo_root)

    leaks = {
        (str(row["observation_type"]), field)
        for row in settled(store).observations(run.capture)
        for field, value in _strings(row["payload"])
        if root in value
    }

    assert leaks <= KNOWN_PROSE_PATH_FIELDS, f"{scenario} leaked the repository root"


@pytest.mark.integration
@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_s3_records_that_it_dropped_the_tool_output(
    provider: str,
    replay: Callable[..., Replayed],
    store: Store,
    settled: Callable[[Store], Store],
) -> None:
    """The secret-touch session says, per observation, that the output went.

    A drop that leaves no trace and a surface that never carried anything look the same
    in the database. Design 6.2 puts the difference in `redaction.dropped`, and this is
    the scenario where it matters: both S3s ran `cat secrets_note.txt`, so the command
    is stored and the file's contents are named as removed.

    Codex is the harder half. E02 measured its command output reaching the OTel logs in
    the `output` attribute of `codex.tool_result` with no content switch enabled, so
    the surface a reader would assume is safe is the one that carried the file.
    """
    run = replay("S3", level=1, provider=provider)

    naming = [
        (str(row["observation_type"]), entry)
        for row in settled(store).observations(run.capture)
        for entry in row["redaction"]["dropped"]
        if entry.startswith(OUTPUT_CONTAINERS[provider])
    ]

    assert naming, f"no {provider} S3 observation names a dropped tool output"
    assert all(entry.endswith(":never_persist") for _type, entry in naming), naming
    # Not vacuous: the drop is named on the surface E02 was surprised by.
    if provider == "codex":
        assert any(kind == "codex.otel.tool_result" for kind, _entry in naming)


@pytest.mark.integration
def test_secrets_are_scrubbed_out_of_the_one_free_text_field(
    receiver: Callable[..., Live],
    store: Store,
    settled: Callable[[Store], Store],
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

    rows = settled(store).observations("cap_scrub")
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
def test_a_codex_reasoning_item_becomes_no_observation_and_one_diagnostic(
    receiver: Callable[..., Live],
    store: Store,
    settled: Callable[[Store], Store],
    db_after_close: Callable[[Store], bytes],
) -> None:
    """Reasoning is never persisted, and how much of it there was still is.

    Design 6.3 puts reasoning in the never-persisted list, so a reasoning item becomes
    no observation at all and there is no `redaction` to carry the fact that one
    arrived. `ParseCtx.notes` is the carrier instead, and the receiver turns it into one
    `dropped` diagnostic per request.

    The fixtures cannot reach this code: E02's sanitizer removed the 22 reasoning rows
    from the S1 and S6 rollouts before they were committed, which is why the exec line
    below is built here. The three spellings are W0-E02 finding 11, where a rule that
    matched only `reasoning` missed 442 of 884 rows.
    """
    live = receiver()
    lines = [
        b'{"type": "item.completed", "item": {"id": "i1", "type": "reasoning",'
        b' "text": "TELLTALEREASON secret plan"}}',
        b'{"type": "item.completed", "item": {"id": "i2", "type": "Reasoning",'
        b' "summary": "TELLTALEREASON again"}}',
        b'{"type": "item.completed", "item": {"id": "i3", "type": "agent_reasoning",'
        b' "text": "TELLTALEREASON third"}}',
        b'{"type": "thread.started", "thread_id": "th_probe"}',
    ]
    for line in lines:
        assert live.post("/v1/stream/codex", line, "cap_reason") == 200
    live.drain()

    rows = settled(store).observations("cap_reason")
    kinds = [str(row["kind"]) for row in store.diagnostics()]

    # The thread line survives, so the three that did not are a drop and not a dead
    # endpoint: a test where nothing parsed would pass for the wrong reason.
    assert [row["observation_type"] for row in rows] == ["codex.exec.thread_started"]
    assert kinds.count("dropped") == 3, kinds
    details = [str(row["detail"]) for row in store.diagnostics() if row["kind"]]
    assert "codex.exec.item:reasoning x1" in details, details
    assert b"TELLTALEREASON" not in db_after_close(store)


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["S1", "S3"])
def test_level_zero_stores_no_path_at_all(
    scenario: str,
    replay: Callable[..., Replayed],
    store: Store,
    settled: Callable[[Store], Store],
) -> None:
    """Level 0 keeps no path of any kind, not even a repo-relative one (design 6.4).

    Two replays of the same session into the same database, one per content level, so
    the level 1 run is what proves the level 0 assertion is not vacuous: it names the
    exact values that level 0 has to lose. Both replays finish before the store is
    settled, so one close covers both captures.
    """
    kept = replay(scenario, level=1)
    dropped = replay(scenario, level=0)

    at_one = _path_values(settled(store).observations(kept.capture))
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


# The two backfill fixtures, at the two content levels the import can run at. Level 0 is
# not a weaker version of the same question: it drops every path, so a leak that level 1
# hides inside a repo-relative path has nowhere left to hide.
IMPORT_CASES = [
    (kind, level)
    for kind in ("claude-transcripts", "codex-rollouts")
    for level in (1, 0)
]

# Where each backfill file puts what a tool printed and what the model wrote, spelled as
# the exact `field:reason` entry design 6.2 puts in `redaction.dropped`. The reason is
# part of the assertion: `never_persist` is the hard stop that an allowlist entry cannot
# undo, and `unknown` is only the first gate.
IMPORT_CONTAINERS = {
    "claude-transcripts": (
        "message:never_persist",
        "tool_use_result:never_persist",
        "compact_metadata:unknown",
    ),
    "codex-rollouts": (
        "output:never_persist",
        "arguments:never_persist",
        "input:never_persist",
    ),
}


@pytest.mark.integration
@pytest.mark.parametrize(("kind", "level"), IMPORT_CASES)
def test_no_probe_or_machine_path_survives_a_backfill_import(
    kind: str,
    level: int,
    store: Store,
    tmp_path: Any,
    db_after_close: Callable[[Store], bytes],
) -> None:
    """The same question the replays ask, of the files an import reads off the disk.

    A backfill is where a leak would be worst: the transcript holds the prompt, the
    answer, every Edit string and everything a command printed, and unlike a capture
    nobody could have turned any of it off. So this asserts against the bytes of the
    database files after close, and the input is checked for the same markers first, so
    that a fixture which stopped carrying them cannot make the test pass by being empty.
    """
    root, _repo_root, home = materialise(kind, tmp_path)
    sources = list(importer.scan(root, kind))
    carried = _in_files(root, [*MARKERS, str(home).encode()])

    result = importer.import_files(store, sources, level)

    assert result["captures"] > 0, f"{kind} imported nothing, so it proves nothing"
    assert sum(carried.values()) > 0, f"{kind} carried none of the markers"
    blob = db_after_close(store)
    found = {marker.decode(): blob.count(marker) for marker in MARKERS}
    assert blob, "the store wrote no bytes at all"
    assert not any(found.values()), f"{kind} at level {level} leaked {found}"
    assert str(home).encode() not in blob, f"{kind} leaked the home directory"


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["claude-transcripts", "codex-rollouts"])
def test_a_backfill_records_that_it_dropped_the_containers(
    kind: str,
    store: Store,
    tmp_path: Any,
    settled: Callable[[Store], Store],
) -> None:
    """An imported capture says, per observation, that the content went.

    The distinction this keeps is the one design 6.2 puts in `redaction.dropped`: a
    file that carried nothing and a file whose contents were removed look identical in
    a payload. On a backfill it is the only evidence that the parser read the container
    rather than never meeting one.
    """
    root, _repo_root, _home = materialise(kind, tmp_path)

    importer.import_files(store, list(importer.scan(root, kind)), 1)

    named = {
        entry
        for capture in settled(store).captures()
        for row in store.observations(str(capture["capture_id"]))
        for entry in row["redaction"]["dropped"]
    }

    assert set(IMPORT_CONTAINERS[kind]) <= named, f"{kind} named only {sorted(named)}"


def _in_files(root: Any, markers: list[bytes]) -> dict[str, int]:
    """How often each marker appears in the files an import is about to read."""
    blob = b"".join(path.read_bytes() for path in sorted(root.rglob("*.jsonl")))
    return {marker.decode(): blob.count(marker) for marker in markers}
