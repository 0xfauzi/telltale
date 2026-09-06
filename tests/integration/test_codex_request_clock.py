"""Where a Codex request comes from, and what happens when only the rollout saw it.

Before W7-T1 the Codex reducer read one surface for per-request usage,
`codex.otel.sse_event` with event.kind response.completed. An IMPORT has no OTel
surface at all, so on the owner's store 1459 imported Codex captures held 149560
`codex.rollout.event_msg.token_count` observations, 0 model_request activities and a
request-clock series of 0 rows each.

The rollout carries the same usage: `info.last_token_usage` is one model request's
usage and `info.total_token_usage`, on the same record, is the turn's running total.
The tests below run the real receiver over the real fixture bytes and assert the two
halves of that: a rollout-only capture builds one request per token_count record, and a
capture that has both surfaces still builds exactly the responses the OTel surface saw,
with the rollout rows attached to them as provenance rather than counted twice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from conftest import activity_fields

from telltale import cli, series

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from conftest import Live, Replayed

    from telltale.store import Store

_REPO_ROOT = Path(__file__).resolve().parents[2]
ROLLOUT_IMPORT = (
    _REPO_ROOT
    / "fixtures"
    / "sources"
    / "codex"
    / "0.150.1"
    / "rollout-import"
    / "2026"
    / "09"
    / "02"
    / "rollout-2026-09-02T10-00-00-019adada-1111-7282-acf2-a7be9046cc69.jsonl"
)
GOLDEN = _REPO_ROOT / "fixtures" / "golden" / "codex" / "rollout-import"

# The route conftest's replay helper posts a rollout.jsonl line to. Named again here
# because rollout-import is a directory tree in the layout `telltale import
# codex-rollouts` reads (YYYY/MM/DD/rollout-<stamp>-<id>.jsonl), not the flat
# <scenario>/rollout.jsonl the replay fixture materialises, so this file posts the
# lines itself. It is the same receiver, the same route and the same bytes.
ROLLOUT_ROUTE = "/v1/stream/codex?surface=rollout"

# What the sse surface says S1 was: 7 response.completed records against 1 turn and 6
# rollout token_count records. The count that must not move when the rollout becomes
# readable as a request.
S1_REQUESTS = 7

# The usage_source a request built from the rollout carries.
ROLLOUT_SOURCE = "codex.rollout.event_msg.token_count.last_token_usage"

STRIPPED = "<reducer_version>"


def _token_count_lines(path: Path) -> int:
    """How many token_count records the fixture holds, counted from the file.

    From the file and not from a constant, so that this asserts about the fixture that
    was replayed rather than about a number somebody typed twice.
    """
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        and (json.loads(line).get("payload") or {}).get("type") == "token_count"
    )


def _post_rollout(live: Live, capture: str, repo_root: Path, home: Path) -> int:
    """Post every line of the rollout-import fixture over HTTP. Returns the lines."""
    text = ROLLOUT_IMPORT.read_text(encoding="utf-8")
    text = text.replace("<repo>", str(repo_root)).replace("<home>", str(home))
    lines = [line for line in text.splitlines() if line.strip()]
    for line in lines:
        assert live.post(ROLLOUT_ROUTE, line.encode("utf-8"), capture=capture) == 200
    live.drain()
    return len(lines)


@pytest.fixture
def rollout_capture(
    receiver: Callable[..., Live], store: Store, tmp_path: Path
) -> tuple[str, Live]:
    """rollout-import through the real receiver, reduced. The import path, replayed."""
    from telltale.sanitize import Ctx

    repo_root, home = tmp_path / "repo", tmp_path / "home"
    repo_root.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    live = receiver(ctx=Ctx(repo_root=repo_root, home=home))
    capture = "rollout-import-L1"
    _post_rollout(live, capture, repo_root, home)
    store.rebuild(capture)
    return capture, live


@pytest.mark.integration
def test_a_rollout_only_capture_builds_one_request_per_token_count(
    rollout_capture: tuple[str, Live],
    store: Store,
    settled: Callable[[Store], Store],
) -> None:
    """One model_request per token_count record, from last_token_usage and nothing else.

    The hand-checked row is the fixture's first token_count, whose last_token_usage is
    input_tokens 12400, cached_input_tokens 11000, output_tokens 320. Fresh input is
    12400 minus 11000 = 1400, which is what the summary prints, and the wire value
    12400 stays under `total_input_tokens`. Storing 12400 as fresh input would call
    11000 reused tokens new ones.
    """
    capture, _ = rollout_capture
    settled(store)
    requests = activity_fields(store, capture, "model_request")
    assert len(requests) == _token_count_lines(ROLLOUT_IMPORT) == 2
    assert [row["usage_source"] for row in requests] == [ROLLOUT_SOURCE] * 2
    first = min(requests, key=lambda row: int(row["request_index"]))
    assert first["total_input_tokens"] == 12400
    assert first["cache_read_tokens"] == 11000
    assert first["input_tokens"] == 12400 - 11000 == 1400
    assert first["output_tokens"] == 320
    # The model and the effort come from the turn_context in force, which token_count
    # itself does not carry.
    assert first["model"] == "gpt-5.6-sol"
    assert first["effort"] == "high"


@pytest.mark.integration
def test_the_second_row_states_no_fresh_input_rather_than_a_negative_one(
    rollout_capture: tuple[str, Live],
    store: Store,
    settled: Callable[[Store], Store],
) -> None:
    """cached larger than input is unknown fresh input, never a negative token count.

    The fixture's second token_count reports input_tokens 12700 with
    cached_input_tokens 12800. The subtraction would be -100. Both wire values are
    stored and `input_tokens` is absent, which is what "unknown stays unknown" means
    here: the row still exists, and the one field nobody can compute is missing.
    """
    capture, _ = rollout_capture
    settled(store)
    requests = activity_fields(store, capture, "model_request")
    second = max(requests, key=lambda row: int(row["request_index"]))
    assert second["total_input_tokens"] == 12700
    assert second["cache_read_tokens"] == 12800
    assert "input_tokens" not in second


@pytest.mark.integration
def test_the_request_clock_of_a_rollout_only_capture_has_rows(
    rollout_capture: tuple[str, Live],
    store: Store,
    settled: Callable[[Store], Store],
) -> None:
    """`series build --clock request` gives one row per token_count, not zero.

    This is the whole point of W7-T1: measured on the owner's store before it, every
    one of 1459 imported Codex captures compiled to a 0-row series.
    """
    capture, _ = rollout_capture
    settled(store)
    built = series.build(store, "request", capture)
    assert len(built.rows) == _token_count_lines(ROLLOUT_IMPORT)
    named = {spec.name: spec for spec in built.columns}
    assert named["fresh_input_tokens"].coverage == "observed"
    assert named["cache_read_tokens"].coverage == "observed"
    assert named["output_tokens"].coverage == "observed"
    # No OTel surface, so nothing brackets a request and the rollout derivation was
    # measured and rejected (activities_codex._durations).
    assert named["request_duration_ms"].coverage == "unavailable"
    assert all(
        row[named_index(built, "request_duration_ms")] is None for row in built.rows
    )


def named_index(built: Any, name: str) -> int:
    return [spec.name for spec in built.columns].index(name)


@pytest.mark.integration
def test_a_capture_with_both_surfaces_counts_the_responses_once(
    replay: Callable[..., Replayed],
    store: Store,
    settled: Callable[[Store], Store],
) -> None:
    """S1 has 7 responses and 6 rollout token_count records, and builds 7 requests.

    The rollout row is a second view of a request, not a second request: each one
    equals exactly one response on input, cached and output, and is attached to it as
    provenance. Attaching every rollout row as its own request instead would build 13,
    which is the assertion that fails when the pairing rule is broken.

    Zero conflict diagnostics, because every one of S1's 6 rollout rows found its twin.
    """
    replayed = replay("S1", provider="codex")
    store.rebuild(replayed.capture)
    settled(store)
    requests = activity_fields(store, replayed.capture, "model_request")
    assert len(requests) == S1_REQUESTS
    assert {row["usage_source"] for row in requests} == {"codex.otel.sse_event"}
    conflicts = [
        row
        for row in store.diagnostics(replayed.capture)
        if row["kind"] == "conflict" and "model_request" in str(row["detail"])
    ]
    assert conflicts == []


@pytest.mark.integration
def test_the_rollout_twin_is_provenance_on_the_request_it_agrees_with(
    replay: Callable[..., Replayed],
    store: Store,
    settled: Callable[[Store], Store],
) -> None:
    """Six of S1's seven requests name a rollout observation behind their counters.

    The seventh is the pre-turn response (input 11437, cached 0, output 0), which the
    rollout never records: it is a request from the sse record alone, and its
    provenance names one observation and not two.
    """
    replayed = replay("S1", provider="codex")
    store.rebuild(replayed.capture)
    settled(store)
    rows = [
        row
        for row in store.activities(replayed.capture)
        if row["activity_type"] == "model_request"
    ]
    types = _observation_types(store, replayed.capture)
    twinned = [
        row
        for row in rows
        if any(
            types.get(one) == "codex.rollout.event_msg.token_count"
            for one in dict(row["provenance"]).get("cache_read_tokens", [])
        )
    ]
    assert len(rows) == S1_REQUESTS
    assert len(twinned) == S1_REQUESTS - 1


@pytest.mark.integration
def test_a_token_count_with_no_usage_is_a_dropped_row_and_never_a_zero_one(
    replay: Callable[..., Replayed],
    store: Store,
    settled: Callable[[Store], Store],
) -> None:
    """S6's 10 token_count records include one whose `info` is null.

    It becomes no model_request and one `dropped` diagnostic naming it. S6 keeps its 11
    requests, which is what the sse surface saw: 11 response.completed records, one of
    which carries no counters at all and is the twin of this token_count.
    """
    replayed = replay("S6", provider="codex")
    store.rebuild(replayed.capture)
    settled(store)
    assert len(activity_fields(store, replayed.capture, "model_request")) == 11
    dropped = [
        json.loads(str(row["detail"]))
        for row in store.diagnostics(replayed.capture)
        if row["kind"] == "dropped"
    ]
    mine = [row for row in dropped if row.get("metric") == "model_request usage"]
    assert len(mine) == 1
    assert mine[0]["dropped"] == 1
    assert len(mine[0]["observations"]) == 1


@pytest.mark.integration
def test_a_resumed_thread_reports_the_leftover_rollout_rows_and_adds_no_request(
    replay: Callable[..., Replayed],
    store: Store,
    settled: Callable[[Store], Store],
) -> None:
    """S4 resumes S2's thread, so one rollout file covers both runs.

    A Codex rollout is written per THREAD and a capture is per RUN. E02 copied the same
    38-line rollout into both fixture directories, byte for byte, same session id, five
    token_count records spanning 22:53:36 to 22:55:54. S4's own OTel capture saw three
    responses, so three of its four usable rollout rows belong to S2's turn and pair
    with nothing here.

    The reducer keeps the OTel count and writes one conflict row per leftover. It never
    adds a second request: three extra requests on S4 would be S2's turn counted twice
    across two captures of one thread.
    """
    replayed = replay("S4", provider="codex")
    store.rebuild(replayed.capture)
    settled(store)
    requests = activity_fields(store, replayed.capture, "model_request")
    assert len(requests) == 3
    conflicts = [
        json.loads(str(row["detail"]))
        for row in store.diagnostics(replayed.capture)
        if row["kind"] == "conflict" and str(row["detail"]).startswith("{")
    ]
    mine = [row for row in conflicts if row.get("metric") == "model_request usage"]
    assert len(mine) == 3
    assert {row["kept"] for row in mine} == {"primary"}
    assert all(len(row["secondary"]) == 1 for row in mine)


@pytest.mark.integration
def test_a_rebuild_does_not_grow_the_dropped_count(
    replay: Callable[..., Replayed],
    store: Store,
    settled: Callable[[Store], Store],
) -> None:
    """`dropped` rows survive a rebuild, so the reducer writes each fact once.

    `store.rebuild` deletes a capture's `conflict` rows and nothing else, which is why
    the reducer compares against what is already there. Without that the count grows by
    one on every `telltale rebuild` of a capture where nothing changed.
    """
    replayed = replay("S6", provider="codex")
    store.rebuild(replayed.capture)
    store.rebuild(replayed.capture)
    store.rebuild(replayed.capture)
    settled(store)
    dropped = [
        row
        for row in store.diagnostics(replayed.capture)
        if row["kind"] == "dropped"
        and json.loads(str(row["detail"])).get("metric") == "model_request usage"
    ]
    assert len(dropped) == 1


@pytest.mark.integration
def test_every_request_duration_names_the_derivation_that_produced_it(
    replay: Callable[..., Replayed],
    store: Store,
    settled: Callable[[Store], Store],
) -> None:
    """A duration is derived from two timestamps, and the row says so.

    No Codex surface carries a per-request duration field, so every `duration_ms` on a
    Codex model_request is the span between `codex.websocket_request` going out and
    `codex.sse_event response.completed` coming back. S1's seven, in order: 490, 3487,
    5812, 4266, 5587, 4507 and 3269 ms.
    """
    replayed = replay("S1", provider="codex")
    store.rebuild(replayed.capture)
    settled(store)
    requests = sorted(
        activity_fields(store, replayed.capture, "model_request"),
        key=lambda row: int(row["request_index"]),
    )
    assert [row["duration_ms"] for row in requests] == [
        490,
        3487,
        5812,
        4266,
        5587,
        4507,
        3269,
    ]
    assert {row["duration_source"] for row in requests} == {
        "otel:websocket_request->sse_event"
    }


@pytest.mark.integration
def test_show_and_timeline_match_the_rollout_import_goldens(
    rollout_capture: tuple[str, Live],
    store: Store,
    settled: Callable[[Store], Store],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The import path end to end, through the two commands a person actually runs.

    Regenerate with TELLTALE_GOLDEN=write, the same switch the other goldens use.
    """
    import os

    capture, _ = rollout_capture
    settled(store)
    printed = _stripped(_run(capsys, ["show", capture]))
    _golden(GOLDEN / "show.json", printed, write=os.environ.get("TELLTALE_GOLDEN"))
    _golden(
        GOLDEN / "timeline.txt",
        _run(capsys, ["timeline", capture]),
        write=os.environ.get("TELLTALE_GOLDEN"),
    )


def _observation_types(store: Store, capture: str) -> Mapping[str, str]:
    return {
        str(row["observation_id"]): str(row["observation_type"])
        for row in store.observations(capture)
    }


def _run(capsys: pytest.CaptureFixture[str], argv: Sequence[str]) -> str:
    assert cli.main(list(argv)) == 0
    return capsys.readouterr().out


def _stripped(printed: str) -> str:
    summary = json.loads(printed)
    assert summary["reducer_version"].startswith("act-"), summary["reducer_version"]
    summary["reducer_version"] = STRIPPED
    return json.dumps(summary, indent=2, ensure_ascii=False) + "\n"


def _golden(path: Path, actual: str, write: str | None) -> None:
    if write == "write":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
    assert path.exists(), f"{path} is missing; regenerate with TELLTALE_GOLDEN=write"
    assert actual == path.read_text(encoding="utf-8")
