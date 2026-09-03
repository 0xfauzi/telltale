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

# Imported for the side effect the reducers are registered by, which is how every
# entry point that rebuilds gets them: `telltale resanitize` reaches them through
# cli.py's import of measures.py, and a test that skipped it would assert the rebuild
# below against a store with no reducers at all.
from telltale import importer, measures  # noqa: F401  (measures registers a reducer)
from telltale.allowlist import ALLOWLIST, Kind
from telltale.model import Observation
from telltale.sanitize import Ctx

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


# The commands W2-T8 measured reaching the disk with a credential in them, as a shell
# would lex them. The first four are the defect the owner's store held: a QUOTED
# ARGUMENT OR HEREDOC LINE THAT BEGINS WITH A DASH, which is the one shape design 6.4's
# "every token starting with -" rule kept verbatim, because shlex hands the whole
# quoted string over as one token and the rule looked no further than its first
# character. The last two are the other half of the same fix and no dash is involved:
# `relativize` returns a repo-relative path unscrubbed, so a credential inside a
# FILENAME or a URL survived, and until W2-T8 no scrub had ever seen a normal form.
# The probes are the same fake strings E01 and E02 planted, so a leak here reads
# exactly like the ten rows the owner's store held on 2026-09-02.
LEAKY_COMMANDS = {
    "echo": 'echo "--- ' + PROBES[0].decode() + ' ---"',
    "heredoc": "python3 - <<EOF\n-----BEGIN TELLTALEFAKE PRIVATE KEY-----\nEOF",
    "grep": "grep -r " + PROBES[1].decode() + " .",
    # The boundary, and it is asserted below rather than hidden: a SHORT quoted
    # argument that begins with a dash and holds no spaces is flag-shaped, and nothing
    # in a normal form distinguishes it from `-m`. So `-TELLTALEFAKE` is stored. The
    # rule is a shape, and this is what the shape cannot do.
    "commit": 'git commit -m "-TELLTALEFAKE"',
    "clone": "git clone https://x-access-token:ghp_TELLTALEFAKE0000@github.com/o/r .",
    "cat": "cat config/" + PROBES[0].decode() + ".env",
}


@pytest.mark.integration
def test_a_credential_in_a_command_does_not_reach_the_disk(
    receiver: Callable[..., Live],
    store: Store,
    tmp_path: Any,
    settled: Callable[[Store], Store],
    db_after_close: Callable[[Store], bytes],
) -> None:
    """A token beginning with `-` survives only if it is flag-shaped, and then it is
    scrubbed.

    Posted on both surfaces a Bash command reaches at level 1, because the defect was
    in the normalizer and not in a parser: the same command leaked through the stream,
    both hooks, the OTel tool_decision and the transcript, and one shared rule is what
    fixed all five.

    What is asserted is the stored command as well as the bytes. An assertion that only
    said "the probe is gone" would pass against a normalizer that stored `_` for every
    argument, and that would destroy the thing commands are recorded for.
    """
    # A capture with a repository root, which is what makes the last two cases real:
    # `relativize` hands a path INSIDE the repository back unchanged, so the scrub in
    # the normal form is the only gate left. Without a root it would scrub the path
    # itself and the assertion below would pass for the wrong reason.
    live = receiver(ctx=Ctx(repo_root=tmp_path / "repo", home=tmp_path / "home"))
    for name, command in LEAKY_COMMANDS.items():
        posted = _tool_use(name, command)
        assert live.post("/v1/stream/claude", posted, "cap_dash") == 200
        assert live.post("/hooks/claude", _hook(name, command), "cap_dash") == 200
        # Not vacuous: the probe was in the bytes that went over the wire.
        assert PROBES[0] in posted or b"TELLTALEFAKE" in posted
    live.drain()

    stored = {
        (str(row["observation_type"]), str(row["payload"]["tool_use_id"])): str(
            row["payload"]["command"]
        )
        for row in settled(store).observations("cap_dash")
        if "command" in row["payload"]
    }
    expected = {
        "echo": "echo _",
        # One `_` for the whole heredoc body since W4-T3, where there were six before,
        # one per word of the private key header. A body is data and the normal form is
        # a command's shape, so the body is replaced before it is ever lexed: what the
        # token rules used to reduce word by word is now never tokenized at all.
        "heredoc": "python3 _ << _ _",
        # The flag survives with its name: `-r` is the fact that a search was
        # recursive, and it is not a secret. The search term next to it is not a fact
        # about the work, and it is where a credential ends up.
        "grep": "grep -r _ .",
        "commit": "git commit -m -TELLTALEFAKE",
        # No dash and no flag: the scrub is the only thing between the token in this
        # URL and the disk, and `git clone <url>` is how a token gets into one.
        "clone": "git clone https:/x-access-token:<redacted:1>@github.com/o/r .",
        "cat": "cat config/<redacted:1>.env",
    }
    surfaces = {"claude.stream.assistant", "claude.hook.PreToolUse"}

    assert {kind for kind, _name in stored} == surfaces, sorted(stored)
    assert stored == {
        (kind, name): value for kind in surfaces for name, value in expected.items()
    }, stored
    # Design 6.4: a string the scrub rewrote is listed in `redaction.redacted`, and a
    # command was the one kept string that never was, because it never reached a scrub.
    named = {
        (str(row["observation_type"]), str(row["payload"]["tool_use_id"]))
        for row in store.observations("cap_dash")
        if "command" in row["redaction"]["redacted"]
    }
    assert named == {(kind, name) for kind in surfaces for name in ("clone", "cat")}

    blob = db_after_close(store)
    found = {probe.decode(): blob.count(probe) for probe in PROBES}
    assert not any(found.values()), f"the database holds {found}"
    # `-TELLTALEFAKE` is on the disk, by the boundary named above. Every occurrence of
    # the probe is that one, on the two surfaces posted, and no other: this is what
    # keeps the boundary honest and stops it from growing.
    assert blob.count(b"-TELLTALEFAKE") == len(surfaces)
    assert blob.count(b"TELLTALEFAKE") == blob.count(b"-TELLTALEFAKE")


def _tool_use(tool_use_id: str, command: str) -> bytes:
    """One stream assistant message holding a Bash tool_use, as E01 recorded them."""
    block = {
        "type": "tool_use",
        "id": tool_use_id,
        "name": "Bash",
        "input": {"command": command},
    }
    line = {
        "type": "assistant",
        "message": {
            "model": "probe",
            "id": "msg_probe",
            "type": "message",
            "role": "assistant",
            "content": [block],
        },
        "session_id": "sess-dash",
        "uuid": f"uuid-{tool_use_id}",
        "timestamp": "2026-09-02T10:00:00.000Z",
    }
    return json.dumps(line).encode("utf-8")


def _hook(tool_use_id: str, command: str) -> bytes:
    """One PreToolUse hook body, in the shape Claude Code hands a hook command."""
    body = {
        "session_id": "sess-dash",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": tool_use_id,
    }
    return json.dumps(body).encode("utf-8")


# Two normal forms this build's own store held on 2026-09-02, byte for byte, with the
# normalization version each was written under. Both are what the OLD rule made of a
# command that quoted an argument beginning with a dash: the first is a heredoc that
# wrote a fixture, the second an `echo` whose whole argument survived as one "flag".
# They are copied rather than invented because a remediation has to be shown working on
# the thing it was written for, and neither can be produced any more: the current rules
# refuse both at capture time.
LEAKED = (
    (
        "cap_leak_a",
        "claude.stream.assistant",
        "cmdnorm-v1",
        "cd <outside>/e9671acd && cat > mk_rollout.py << _ import _ _ _ _ _ _ _ _ ("
        " fixtures/sources/codex/0.150.1/rollout-import/2026/09/02 ) _ _ _ _ _ _ _ _ _"
        " _ _ _ _ _ -----BEGIN TELLTALEFAKE PRIVATE KEY---",
    ),
    (
        "cap_leak_b",
        "claude.transcript.assistant",
        "cmdnorm-v2",
        "echo --- sk-ant not TELLTALEFAKE --- ; grep -rho _ fixtures/sources/claude |"
        " sort -u | head ; echo --- api key env leak --- ; grep -rlo _"
        " fixtures/sources/claude | head -3 ; echo --- messaging token -",
    ),
    # The control: a v2 normal form the current rules agree with. It must come back
    # untouched, version and all, or `resanitize` is rewriting rows for the sake of the
    # label rather than because a token was wrong.
    (
        "cap_leak_b",
        "claude.hook.PreToolUse",
        "cmdnorm-v2",
        "uv run pytest tests/test_calc.py -k _",
    ),
)


@pytest.mark.integration
def test_resanitize_rewrites_a_leaked_command_and_nothing_else(
    store: Store,
    db_after_close: Callable[[Store], bytes],
) -> None:
    """The one sanctioned rewrite of an observation, on the rows that forced it.

    `purge` was the wrong remedy and this is why the store has an UPDATE at all: three
    of the ten captures that held a probe are this build's own launcher captures with
    hundreds of model requests each, and the leak is one token in one field of each.

    The rows are written through the real store, at the versions that wrote them, and
    then read back through it. Nothing is monkeypatched: an older rule set is a fact
    about rows already on the disk, so a row is the honest way to express one.
    """
    store.append([_stored(index, *row) for index, row in enumerate(LEAKED)])
    assert store.flush(), "the writer did not drain"

    counts = store.resanitize()

    assert counts == {"cap_leak_a": 1, "cap_leak_b": 1}, counts
    rows = {
        str(row["observation_id"]): row
        for capture in ("cap_leak_a", "cap_leak_b")
        for row in store.observations(capture)
    }
    rewritten = rows["obs_probe_0"]["payload"]
    untouched = rows["obs_probe_2"]["payload"]

    assert "TELLTALEFAKE" not in str(rewritten["command"])
    assert str(rewritten["command"]).endswith("_ _ _ _"), rewritten["command"]
    assert rewritten["normalization_version"] == "cmdnorm-v4"
    # The row says it was rewritten rather than produced. `normalization_version` alone
    # cannot: re-running the token rules over a v1 string does not restore the `=` that
    # v1 never recorded, so the marker is what stops a reader reading v4 as v4.
    assert rows["obs_probe_0"]["redaction"]["redacted"] == ["resanitize:cmdnorm-v4"]
    assert untouched == {
        "command": "uv run pytest tests/test_calc.py -k _",
        "normalization_version": "cmdnorm-v2",
        "tool_name": "Bash",
    }
    assert rows["obs_probe_2"]["redaction"]["redacted"] == []
    # The capture was rebuilt too, because an activity COPIES the normal form into
    # `fields.command_norm`: rewriting the observation alone leaves the token standing
    # one table over, which is what a copy of the owner's store showed on six rows.
    assert [
        str(activity["fields"]["command_norm"])
        for activity in store.activities("cap_leak_a")
        if "command_norm" in activity["fields"]
    ] == [str(rewritten["command"])]

    details = [
        str(row["detail"])
        for row in store.diagnostics("cap_leak_a")
        if row["kind"] == "dropped"
    ]
    assert details == ["resanitize cmdnorm-v1 to cmdnorm-v4: 1 field(s) rewritten"]
    # Idempotent, and that is the claim the version label alone would not support: the
    # rules are applied to their own output and find nothing to change.
    assert store.resanitize() == {}

    blob = db_after_close(store)
    found = {marker.decode(): blob.count(marker) for marker in PROBES}
    found["TELLTALEFAKE"] = blob.count(b"TELLTALEFAKE")
    assert not any(found.values()), f"the database still holds {found}"


def _stored(
    index: int, capture_id: str, observation_type: str, version: str, command: str
) -> Observation:
    """One observation as an older normalization version left it on the disk."""
    return Observation(
        observation_id=f"obs_probe_{index}",
        capture_id=capture_id,
        observation_type=observation_type,
        surface="stream",
        provider="claude",
        adapter="claude@probe",
        ingest_ts="2026-09-02T10:00:00.000000Z",
        # The reducer groups a tool call by this, and the activity it builds COPIES the
        # normal form into `fields.command_norm`. Without it there is no activity and
        # the rebuild half of the remediation would go unasserted.
        correlation_ids={"tool_use_id": f"toolu_{index}"},
        payload={
            "command": command,
            "normalization_version": version,
            "tool_name": "Bash",
        },
        redaction={"dropped": [], "truncated": [], "redacted": []},
    )
