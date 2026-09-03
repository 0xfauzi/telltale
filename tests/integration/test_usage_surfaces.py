"""What each Claude surface says a session's output tokens were, through the recorder.

One file for output tokens because W4-T4 gave them a defect class of their own: a field
whose name matches the OTel one, whose type matches, whose magnitude looks plausible,
and which is not a count of anything. Every test here is a real `telltale run` around
the scripted agent or a real replay of a recorded fixture, so what is asserted is the
number a person would see on `telltale show`.

The measurement behind the file is in docs/log/W4-T4.md. In short: on the six E12
sessions, `telltale show` said 1902, 912, 636, 1620, 1421 and 1271 output tokens for
sessions whose own result records say 141206, 122438, 67988, 99305, 153943 and 89125,
and whose OTel captures say the latter exactly. The stream's per-message number is a
snapshot from before the message finished; joined by message id to the provider's own
transcript of the same session it is strictly smaller than the final count on all 761
messages, and never once equal.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from conftest import activity_fields, launched

from telltale import measures, series
from telltale.forecast import readiness
from telltale.measures_spec13 import SESSION_FIELDS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from conftest import Replayed

    from telltale.store import Store

# S1's seven api_request records, summed by hand from
# fixtures/sources/claude/2.1.257/S1/otel_logs.jsonl and checked in W1-T2.
S1_OUTPUT_TOKENS = 936


def _summary(store: Store, capture: str) -> dict[str, Any]:
    return measures.summary(store, capture)


def _requests(store: Store, capture: str) -> list[Mapping[str, Any]]:
    return activity_fields(store, capture, "model_request")


def _column(built: Any, name: str) -> list[float | None]:
    index = [spec.name for spec in built.columns].index(name)
    return [row[index] for row in built.rows]


def _spec(built: Any, name: str) -> Any:
    return next(spec for spec in built.columns if spec.name == name)


@pytest.mark.integration
def test_a_snapshot_and_a_count_of_one_session_report_one_number(
    tmp_path: Path,
) -> None:
    """Two captures of the same scripted session, differing only in how the stream
    states output_tokens on its assistant messages, report the same output_tokens.

    `--snapshot-usage` makes the assistant messages carry the shape a real Claude Code
    carries: a small number taken before the message finished, unrelated to its size.
    The result message's totals are identical in both runs, because the work is
    identical. A recorder whose answer depends on which of the two shapes the provider
    used to describe one session is reading the wrong field.

    Break it by reading the snapshot: restore `output_tokens` in
    `providers.claude._MESSAGE_USAGE` and drop the `_SESSION_FIELD` branch in
    `measures_spec13.usage`, and the snapshot run reports 15 output tokens against the
    plain run's 320, a factor of 21 on a session of 5 tool calls.
    """
    (plain_root := tmp_path / "plain").mkdir()
    (snapshot_root := tmp_path / "snapshot").mkdir()
    plain_store, plain = launched(plain_root)
    snapshot_store, snapshot = launched(snapshot_root, "--snapshot-usage")

    stated = _summary(plain_store, plain)["usage"]
    snapped = _summary(snapshot_store, snapshot)["usage"]

    assert stated["model_requests"] == snapped["model_requests"] > 0
    assert stated["output_tokens"] == snapped["output_tokens"]
    # Not vacuous: the number is the session's and is bigger than any one message.
    assert snapped["output_tokens"] > snapped["model_requests"] * 20
    # The three counters the stream DOES state finally are equal too, which is what
    # makes output_tokens the one field this task is about (W4-T4 measured the same
    # three agreeing exactly with OTel over 761 requests of the six E12 sessions).
    for name in ("fresh_input_tokens", "cache_read_tokens", "cache_creation_tokens"):
        assert stated[name] == snapped[name], name


@pytest.mark.integration
def test_a_stream_only_capture_says_the_split_per_request_is_unknown(
    tmp_path: Path,
) -> None:
    """The session total, coverage partial, and the warning that says why.

    The provider states one output figure for the whole session (the result message's
    modelUsage) and none per request. Both halves are reported: the number, because it
    is real and a reader may add it up across captures, and the coverage word and the
    warning, because placing it against a request would be arithmetic nobody measured.

    Break it by giving `_session_total` coverage `observed`: the summary then says a
    number nobody measured per request is as well seen as one that was.
    """
    store, capture = launched(tmp_path, "--snapshot-usage")
    summary = _summary(store, capture)
    evidence = {str(row["metric"]): row for row in store.evidence(capture)}
    row = evidence["output_tokens"]

    assert summary["usage"]["output_tokens"] == row["value"]
    assert row["coverage"] == "partial"
    assert row["claim_class"] == "derived"
    assert any("WHOLE session" in one for one in row["warnings"]), row["warnings"]
    assert "output_tokens" in summary["warnings"]
    # The source is the lifecycle row that carries the provider's figure, not a request.
    ends = [
        item
        for item in store.activities(capture)
        if item["activity_type"] == "lifecycle"
        and SESSION_FIELDS["output_tokens"] in dict(item["fields"])
    ]
    assert [str(item["activity_id"]) for item in ends] == list(row["source"])
    assert dict(ends[-1]["fields"])["session_output_tokens"] == row["value"]


@pytest.mark.integration
def test_a_stream_request_carries_the_snapshot_under_its_own_name(
    tmp_path: Path,
) -> None:
    """No model_request from the stream has an output_tokens field at all.

    The snapshot is not discarded: it is a thing the surface said and the activity keeps
    it, under a name that cannot be added to an OTel count by accident. What must not
    exist is `output_tokens` on a row where no surface counted one, because every reader
    downstream (the summary, the series, the vector) reads that name.

    Break it by putting the snapshot back under `output_tokens` in
    `activities._request`: every assertion below still passes except the first, and the
    series column in the next test fills up with snapshots.
    """
    store, capture = launched(tmp_path, "--snapshot-usage")
    requests = _requests(store, capture)

    assert requests, "the scripted agent made no model requests"
    assert [item for item in requests if "output_tokens" in item] == []
    assert all("output_tokens_snapshot" in item for item in requests)
    assert all(item["usage_source"] == "stream" for item in requests)
    # The three counters the stream states finally are on the row, so the absence above
    # is about one field and not about the surface having delivered nothing.
    for name in ("input_tokens", "cache_read_tokens", "cache_creation_tokens"):
        assert all(name in item for item in requests), name


@pytest.mark.integration
def test_the_request_clock_output_tokens_column_is_unknown_and_named(
    tmp_path: Path,
) -> None:
    """Every cell None, the column unavailable, and the readiness checklist naming it.

    Nothing imputes and nothing is dropped: the `exclude` policy leaves the None cells
    where they are and the forecaster drops the windows that read them. What this test
    pins is that a reader is told, by name, which column a stream-only capture cannot
    forecast on.

    Break it as in the previous test and the column fills with snapshots, coverage goes
    back to `observed`, and a forecast of output_tokens on a stream-only capture runs on
    numbers that are not output tokens.
    """
    store, capture = launched(tmp_path, "--snapshot-usage")
    built = series.build(store, "request", capture, "exclude")
    report = {row["column"]: row for row in series.column_report(built)}

    assert built.rows, "the capture produced no request-clock rows"
    assert _column(built, "output_tokens") == [None] * len(built.rows)
    assert _spec(built, "output_tokens").coverage == "unavailable"
    assert report["output_tokens"]["reason"] == series.EMPTY_COLUMN
    assert report["output_tokens"]["nulls"] == len(built.rows)
    # The columns the stream does state stay observed, so `unavailable` here is a fact
    # about one column and not about the capture.
    assert _spec(built, "fresh_input_tokens").coverage == "observed"
    # And the checklist names it, on a target that can be checked at all.
    checks = readiness.check(built, "fresh_input_tokens", 1)
    coverage = next(item for item in checks if item.name == "coverage")
    assert "output_tokens (unavailable)" in coverage.detail
    assert not coverage.passed


@pytest.mark.integration
def test_the_otel_surface_keeps_its_per_request_output_tokens(
    replay: Callable[..., Replayed], store: Store, settled: Callable[[Store], Store]
) -> None:
    """S1 replays both surfaces together, and the OTel numbers are untouched.

    The regression guard for the whole change: OTel is primary (design 6.10), so a
    capture that has it keeps a per-request output_tokens, coverage `observed`, no
    session-total warning and the same 936 the goldens carry.

    And no conflict diagnostic is written about output_tokens. The conflict rule is for
    two surfaces answering the SAME question differently; a snapshot from before a
    message finished is not a second measurement of that message's output, so recording
    a conflict would be a count of requests wearing the name of a defect.
    """
    replayed = replay("S1")
    store.rebuild(replayed.capture)
    settled(store)
    summary = _summary(store, replayed.capture)
    evidence = {str(row["metric"]): row for row in store.evidence(replayed.capture)}
    requests = _requests(store, replayed.capture)

    assert summary["usage"]["output_tokens"] == S1_OUTPUT_TOKENS
    assert evidence["output_tokens"]["coverage"] == "observed"
    assert evidence["output_tokens"]["warnings"] == []
    assert all(item["usage_source"] == "primary" for item in requests)
    assert sum(item["output_tokens"] for item in requests) == S1_OUTPUT_TOKENS
    mentions = [
        row
        for row in store.diagnostics(replayed.capture)
        if row["kind"] == "conflict" and "output_tokens" in json.dumps(dict(row))
    ]
    assert mentions == []


@pytest.mark.integration
def test_an_interval_with_an_unknown_counter_states_no_token_total(
    tmp_path: Path,
) -> None:
    """stable_state_tokens is null on a stream-only capture, not a sum of three of four.

    Spec 13.5 asks for the tokens spent inside an interval. With no per-request output
    figure that quantity is unknown, and a sum of the other three counters would be
    short by the whole of the session's output while carrying a name that says
    otherwise. The session total is still reported, in the usage block: what cannot be
    done is dividing it between intervals.

    Break it by restoring the `if isinstance(...)` filter inside the sum in
    `measures_intervals._tokens`: the metric comes back as a number that is missing
    every output token of the session.
    """
    store, capture = launched(tmp_path, "--snapshot-usage")
    summary = _summary(store, capture)
    evidence = {str(row["metric"]): row for row in store.evidence(capture)}

    assert summary["stable_state"]["stable_state_work_intervals"] > 0
    assert summary["stable_state"]["stable_state_tokens"] is None
    assert evidence["stable_state_tokens"]["value"] is None
    assert summary["usage"]["output_tokens"] is not None


@pytest.mark.integration
def test_the_result_usage_block_is_the_main_thread_and_is_named_so(
    replay: Callable[..., Replayed], store: Store, settled: Callable[[Store], Store]
) -> None:
    """S7's result message states 2714 output tokens for a session that made 18548.

    Measured on the fixture: the result's own `usage` block is the MAIN THREAD's totals.
    S7's eight api_request records include three with `query_source=compact` summing to
    15834, and 2714 is exactly the other five. S4 shows the same rule one subagent over:
    its `usage` says 350, the two `sdk` requests, leaving out the five
    `agent:builtin:Explore` ones that make up the rest of its 2368.

    So the session figure is modelUsage and never `usage`, and the four counters of that
    block are stored as `main_thread_*` so nothing can read one as a session total.

    Break it by pointing `activities._session_output` at `main_thread_output_tokens`:
    nothing here changes on S7, because OTel is primary and supplies every request, but
    a stream-only capture of a session that compacted or delegated would report a total
    missing exactly those requests.
    """
    replayed = replay("S7")
    store.rebuild(replayed.capture)
    settled(store)
    results = [
        dict(row["payload"])
        for row in store.observations(replayed.capture)
        if row["observation_type"] == "claude.stream.result"
    ]
    # S7 ends twice: the SessionEnd hook states one and the stream result the other,
    # and only the result carries a token figure. Filtering by the field rather than
    # taking the last row is what keeps this about the figure and not about the order.
    ends = [
        dict(item["fields"])
        for item in store.activities(replayed.capture)
        if item["activity_type"] == "lifecycle"
        and "session_output_tokens" in dict(item["fields"])
    ]

    assert len(results) == 1
    assert results[0]["main_thread_output_tokens"] == 2714
    assert "output_tokens" not in results[0]
    assert [item["session_output_tokens"] for item in ends] == [18548]
    # OTel delivered every request, so the summary reports the sum and not the total.
    assert _summary(store, replayed.capture)["usage"]["output_tokens"] == 18548


@pytest.mark.integration
def test_a_stream_assistant_message_states_no_output_token_count(
    replay: Callable[..., Replayed], store: Store, settled: Callable[[Store], Store]
) -> None:
    """The observation layer: the field is named for what it is, on every message.

    This is the one assertion about stored bytes rather than about a number, and it is
    where the rule is enforceable: `output_tokens` on a `claude.stream.assistant` row is
    now an unknown field, so a future parser that reintroduced it would be refused by
    the allowlist gate rather than believed.
    """
    replayed = replay("S1")
    store.rebuild(replayed.capture)
    settled(store)
    messages = [
        dict(row["payload"])
        for row in store.observations(replayed.capture)
        if row["observation_type"] == "claude.stream.assistant"
        and "input_tokens" in dict(row["payload"])
    ]
    snapshots = [item["output_tokens_snapshot"] for item in messages]

    # Ten records over seven message ids and seven request ids: Claude Code sends one
    # record per content block and repeats the message's usage on each, which is why
    # summing the records would multiply the snapshot as well as mistake it.
    assert len(messages) == 10
    assert all("output_tokens" not in item for item in messages)
    assert len(snapshots) == len(messages)
    # 20, against the 936 output tokens the same seven requests really produced.
    assert max(snapshots) == 20
    assert sum(snapshots) < S1_OUTPUT_TOKENS / 5
