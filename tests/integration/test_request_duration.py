"""How long one model request took, on the two Claude surfaces that can answer.

W7-T3. `request_duration_ms` was the one request-clock column no imported capture could
carry: an OTel `api_request` record states `duration_ms` and a transcript line states
nothing of the kind, so every backfilled capture wrote the column as all None and every
reader was told `unavailable`. A transcript does date every line, and E14 measured that
the gap from the line before a request to the LAST assistant line of it reproduces the
OTel number on 6518 requests of 128 of the build's own sessions, 99.7 percent of them
within 10 percent relative (docs/experiments/E14.md).

What the three tests here pin is the difference between the two surfaces rather than
the arithmetic alone: an OTel capture keeps the number the provider stated and says
`otel_api_request`, an imported transcript carries a number this system computed and
says `transcript_timestamps`, and a request with nothing before it carries no number at
all and is counted in a diagnostic. Nothing anywhere mixes the two, and nothing fills a
missing one with a zero.

Break it in `activities._gap` by ending at the group's FIRST assistant observation
rather than its last and the third test falls from 4000 ms to 2000 ms on the request
that wrote two lines; delete the `stated` branch of `_duration` and the second test
loses both the provider's number and the word that says it was the provider's.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from conftest import activity_fields
from test_import import _import, _main_capture, materialise

from telltale import series

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from conftest import Replayed

    from telltale.store import Store

pytestmark = pytest.mark.integration

# The request the arithmetic is asserted on, and the two lines it is computed from, both
# read out of fixtures/sources/claude/2.1.257/transcript/-telltale-fake-project/
# 11111111-2222-4333-8444-555555555555.jsonl:
#
#   user      2026-09-02T09:00:09.000Z   the line before the request
#   assistant 2026-09-02T09:01:00.000Z   requestId req_aaa5, the last line of it
#
# 09:01:00.000 minus 09:00:09.000 is 51 seconds, so 51000 ms.
DERIVED_REQUEST = "req_aaa5"
DERIVED_MS = 51000

# The request whose preceding line is a system compact_boundary rather than a user line:
# 09:01:02.000 minus 09:01:01.000. Both kinds open a request, and only these two are
# observations at all, so this is the whole of the "or system" half of the rule.
AFTER_COMPACTION = "req_aaa6"
AFTER_COMPACTION_MS = 1000

# Every request of the main transcript, in request_index order. Requests 7 to 11 share
# one opening line: the fixture writes those assistant lines at 09:02:0X and the user
# lines that follow them at 09:03:0X, so the nearest PRECEDING line by the provider
# clock is the 09:01:05 user line for all five. Nearest by clock, never by file order.
MAIN_DURATIONS = [
    1000, 1000, 1000, 1000, 51000, 1000, 56000, 57000, 58000, 59000, 60000, 61000,
]  # fmt: skip

DURATION_COLUMN = "request_duration_ms"

# The hand-written transcript of the third test: a request whose two assistant lines
# close at 10:00:06.000, 4000 ms after the 10:00:02.000 user line before it.
LAST_LINE_MS = 4000


def _requests(store: Store, capture: str) -> list[dict[str, Any]]:
    rows = [dict(row) for row in activity_fields(store, capture, "model_request")]
    return sorted(rows, key=lambda row: int(row["request_index"]))


def _spec(built: Any, name: str) -> Any:
    return next(spec for spec in built.columns if spec.name == name)


def _column(built: Any, name: str) -> list[float | None]:
    index = [spec.name for spec in built.columns].index(name)
    return [row[index] for row in built.rows]


def test_an_imported_transcript_derives_its_request_durations(
    store: Store, tmp_path: Path
) -> None:
    """The gap between two timestamps, on the row, in the column and in its coverage.

    The number is hand-computed in DERIVED_MS above from the two lines named there, and
    the provenance is both observations, so `telltale explain` can print the two rows
    the subtraction was made from. `derived` and not `observed`: no line of the file
    states this number, and design 6.3's word for a number read out of a surface rather
    than stated by it is the weaker of the two.
    """
    root, _repo, _home = materialise("claude-transcripts", tmp_path)

    _import(store, "claude-transcripts", root)
    store.flush()
    capture = _main_capture(store)
    requests = _requests(store, capture)
    found = {row["request_id"]: row for row in requests}

    assert [row["duration_ms"] for row in requests] == MAIN_DURATIONS
    assert found[DERIVED_REQUEST]["duration_ms"] == DERIVED_MS
    assert found[AFTER_COMPACTION]["duration_ms"] == AFTER_COMPACTION_MS
    assert {row["duration_source"] for row in requests} == {"transcript_timestamps"}

    provenance = {
        str(row["activity_id"]): dict(row["provenance"])
        for row in store.activities(capture)
        if row["activity_type"] == "model_request"
    }
    sources = [value["duration_ms"] for value in provenance.values()]
    assert all(len(one) == 2 for one in sources), sources

    built = series.build(store, "request", capture, "exclude")
    assert _spec(built, DURATION_COLUMN).coverage == "derived"
    assert _column(built, DURATION_COLUMN) == MAIN_DURATIONS


def test_an_otel_capture_keeps_the_duration_the_provider_stated(
    replay: Callable[..., Replayed], store: Store, settled: Callable[[Store], Store]
) -> None:
    """S1 replayed: the api_request number, named as the provider's, coverage observed.

    The regression guard for the derivation: a request the OTel surface saw never
    reaches the transcript rule, because `_requests` heads it with the api_request
    record and keeps its request_id out of the stream groups. Two surfaces are two
    measurements and this system does not average them (design 6.10).
    """
    replayed = replay("S1")
    store.rebuild(replayed.capture)
    settled(store)
    requests = _requests(store, replayed.capture)

    assert requests, "the replay produced no model requests"
    assert {row["duration_source"] for row in requests} == {"otel_api_request"}
    assert all(row["duration_ms"] > 0 for row in requests)

    built = series.build(store, "request", replayed.capture, "exclude")
    assert _spec(built, DURATION_COLUMN).coverage == "observed"
    assert _column(built, DURATION_COLUMN) == [
        float(row["duration_ms"]) for row in requests
    ]


def test_the_last_assistant_line_closes_the_request_and_a_first_one_is_none(
    store: Store, tmp_path: Path
) -> None:
    """The two cases the tracked fixture cannot show, in one hand-written transcript.

    Its first request follows a `queue-operation`, one of the fifteen line kinds this
    parser counts and does not read, so nothing stored precedes it: None, never zero,
    and a `dropped` diagnostic that counts it. A column of 0 ms would be a claim that
    the request was instant.

    Its second request writes TWO assistant lines under one requestId, which is what
    3314 of E14's 6518 matched requests do and what the tracked fixture has none of.
    The request closes at the LAST of them: 10:00:06.000 minus the 10:00:02.000 user
    line is 4000 ms, and ending at the first line instead would say 2000 ms. E14
    measured that choice as 99.7 percent against 54.7 percent within 10 percent of the
    OTel duration, and this is the test that holds it.
    """
    root = tmp_path / "in-transcript"
    session = "22222222-3333-4444-8555-666666666666"
    path = root / "-telltale-fake-project" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_no_opening_line(session), encoding="utf-8")

    _import(store, "claude-transcripts", root)
    store.flush()
    listed = [str(row["capture_id"]) for row in store.captures()]
    assert len(listed) == 1, listed
    capture = listed[0]
    requests = _requests(store, capture)
    dropped = [
        str(row["detail"])
        for row in store.diagnostics(capture)
        if row["kind"] == "dropped"
    ]

    assert [row.get("duration_ms") for row in requests] == [None, LAST_LINE_MS]
    assert [row.get("duration_source") for row in requests] == [
        None,
        "transcript_timestamps",
    ]
    assert any("1 transcript requests have no derived" in one for one in dropped)

    built = series.build(store, "request", capture, "exclude")
    assert _column(built, DURATION_COLUMN) == [None, LAST_LINE_MS]
    # A hole is not an absence of the column: one row carries a number, so `derived`
    # stands and `blank_unobservable` leaves it alone.
    assert _spec(built, DURATION_COLUMN).coverage == "derived"


def _no_opening_line(session: str) -> str:
    """A transcript whose first request follows a line kind the parser does not read."""
    usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0}
    lines = [
        {
            "type": "queue-operation",
            "timestamp": "2026-09-02T10:00:00.000Z",
            "sessionId": session,
            "uuid": "q-0001",
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-02T10:00:01.000Z",
            "sessionId": session,
            "uuid": "a-0002",
            "requestId": "req_none1",
            "message": {"model": "claude-opus-5", "usage": usage, "content": []},
        },
        {
            "type": "user",
            "timestamp": "2026-09-02T10:00:02.000Z",
            "sessionId": session,
            "uuid": "u-0003",
            "message": {"content": "the next prompt"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-02T10:00:04.000Z",
            "sessionId": session,
            "uuid": "a-0004",
            "requestId": "req_have1",
            "message": {"model": "claude-opus-5", "usage": usage, "content": []},
        },
        # The same requestId, one content block later. Measured on the owner's files:
        # a request is 1 to 5 assistant lines and 3314 of E14's 6518 are two.
        {
            "type": "assistant",
            "timestamp": "2026-09-02T10:00:06.000Z",
            "sessionId": session,
            "uuid": "a-0005",
            "requestId": "req_have1",
            "message": {"model": "claude-opus-5", "usage": usage, "content": []},
        },
    ]
    return "\n".join(json.dumps(line) for line in lines) + "\n"
