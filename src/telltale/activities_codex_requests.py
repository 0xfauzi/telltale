"""What one Codex model request is, on the two surfaces that see it. Design 6.10.

Split out of activities_codex.py, which the 800-line limit closed when this was written:
that file decides WHICH observations describe one thing, and this one holds the whole
answer to a single question, what one model request is and how long it took. It is a
rule module, so its name is in correlate._RULE_MODULES and editing it moves
REDUCER_VERSION.

Two measurements shape everything below, both made on fixtures/sources/codex/0.150.1 and
on the owner's own rollout files.

  A TOKEN_COUNT IS ONE REQUEST; A TURN IS NOT. `codex.exec.turn_completed` and the
  rollout's `info.total_token_usage` are TURN totals, and one turn holds many model
  responses: S1 has 1 turn and 7 responses. The rollout's `info.last_token_usage` is
  the other quantity on the same record and it is ONE request's usage. Measured on the
  owner's largest rollout of 2026-09 (1 task_started, 2 turn_context, 69 token_count
  records in one turn, each with its own last_token_usage and a total_token_usage that
  only grows: 24062, 50668, 81746, 113264) and on the three fixtures that carry both
  surfaces, where each last_token_usage equals one codex.sse_event response.completed
  record field for field on all three counters both surfaces state (S1 6 of 6, S3 2 of
  2, S6 9 of 9 that carry a usage mapping). So the rollout is a SECOND view of the same
  requests, never a second set of them: where both surfaces are present the sse record
  is the request and the rollout row is attached to it as provenance, and where only
  the rollout is present the rollout row IS the request. The turn totals stay on the
  `turn` activity, which no summary metric reads, and nothing sums one into the other.

  NOTHING TIMES A MODEL REQUEST. No Codex surface carries a per-request duration field:
  `codex.api_request.duration_ms` times the /models HTTP call, `codex.turn_ttft` times
  a turn's first token, and `codex.websocket_request.duration_ms` is 2, 0, 0, 0, 0, 0
  and 0 ms on S1 against responses that took 490 to 5812 ms, so it times writing the
  request onto the socket. The duration is derived instead, from the send record and
  the completion record that bracket one request, and only on the OTel surface: the
  same derivation from rollout timestamps was measured and rejected (`_durations`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from telltale import correlate
from telltale.correlate import Fields
from telltale.model import to_json
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from telltale.correlate import Obs
    from telltale.model import Activity

# One model response, with its own token counts. The other event_kinds of
# codex.otel.sse_event carry no usage at all (E02: response.created, response.output_*).
RESPONSE = "codex.otel.sse_event"
_RESPONSE_KIND = "response.completed"

# The rollout's view of the same request, and when the request went out. The three
# types `_pairing` and `_durations` read, named together because `diagnose` asks the
# store for exactly these and nothing else.
_TOKEN_COUNT = "codex.rollout.event_msg.token_count"
_LAST_USAGE = "last_token_usage"
_SENT = "codex.otel.websocket_request"
_REQUEST_TYPES = (RESPONSE, _SENT, _TOKEN_COUNT)

# The three counters BOTH surfaces state about one response, under each spelling. Three
# and not five: these are the three that are equal field for field on every paired
# record of S1, S3 and S6, and cache_write and reasoning are not compared because a
# pairing rule is only as strong as its weakest agreed field.
_SSE_TRIPLE = ("input_token_count", "cached_token_count", "output_token_count")
_ROLLOUT_TRIPLE = ("input_tokens", "cached_input_tokens", "output_tokens")

# What `duration_ms` on a model_request means when this module put it there.
_DURATION_SOURCE = "otel:websocket_request->sse_event"

# How many observation ids one diagnostic names. store.MAX_DETAIL bounds detail at 2048
# bytes and a truncated JSON object is readable by nothing, so the COUNT in the row is
# exact and the id list is a sample once the count passes this. Measured: a ULID id
# costs 30 bytes inside a JSON array, so 40 of them leave room for the rest of the row.
_NAMED_IDS = 40

# Why a token_count became no request, in the words the diagnostic carries.
_UNUSABLE = (
    "no last_token_usage mapping, so no model_request at all: a request nobody"
    " counted is unknown and never a zero row"
)
_UNPAIRED = (
    "equals no unpaired codex.sse_event response.completed record on input_tokens,"
    " cached_input_tokens and output_tokens. A rollout file is per THREAD and a"
    " capture is per RUN, so a resumed session's rollout carries token_count records"
    " of turns this capture's OTel surface never saw"
)

# The four counters under the summary's name for each, from the sse_event spelling.
_USAGE = (
    ("cache_read_tokens", "cached_token_count"),
    ("cache_creation_tokens", "cache_write_token_count"),
    ("output_tokens", "output_token_count"),
    ("reasoning_output_tokens", "reasoning_token_count"),
)
# The same four from the exec stream's turn.completed spelling.
_TURN_USAGE = (
    ("cache_read_tokens", "cached_input_tokens"),
    ("cache_creation_tokens", "cache_write_input_tokens"),
    ("output_tokens", "output_tokens"),
    ("reasoning_output_tokens", "reasoning_output_tokens"),
)


# -- model requests -------------------------------------------------------------------
def model_requests(capture_id: str, observed: Sequence[Obs]) -> list[Activity]:
    """One activity per model REQUEST, from whichever surface saw the request.

    Two surfaces see the same requests and neither sees all of them. `codex.sse_event`
    with event.kind response.completed carries the six counters of one response and is
    primary wherever it is present: it is the record the model wrote, it carries the
    model name, and S1 has 7 of them against 1 turn. The rollout carries the same usage
    on `event_msg/token_count.info.last_token_usage`, and it is the only surface an
    IMPORT has: measured on the owner's store, 1459 imported Codex captures held
    149560 token_count observations and 0 model_request activities, so every one of
    them built a request-clock series of 0 rows.

    So the rollout is read as a request only when the OTel surface is absent, and where
    both are present each rollout row is attached to the sse request it equals as
    provenance (`_pairing`). The pre-turn response (11437 input, 0 cached, 0 output on
    S1, S3 and S6 alike) has no rollout twin by construction and is a request from the
    sse record alone.
    """
    responses = _sorted(observed, RESPONSE, _RESPONSE_KIND)
    paired = _pairing(observed, responses)
    if not responses:
        return [
            _rollout_request(capture_id, index, item, _turn_context(observed, item))
            for index, item in enumerate(paired.usable, start=1)
        ]
    took, _ = _durations(responses, _sorted(observed, _SENT))
    return [
        _request(
            capture_id, index, item, paired.twins.get(item.id, ()), took.get(item.id)
        )
        for index, item in enumerate(responses, start=1)
    ]


def _request(
    capture_id: str,
    index: int,
    item: Obs,
    twins: Sequence[str],
    took: tuple[int, str] | None,
) -> Activity:
    """One request as the OTel surface saw it, with its rollout twin behind the numbers.

    `twins` names the rollout token_count records that state the same usage. They are
    sources and never values: the sse record is what this row says, and the rollout id
    is there so that `telltale explain` reaches the second surface that agreed.
    """
    built = Fields()
    built.put("request_index", index)
    built.put("usage_source", RESPONSE, item.id, *twins)
    built.put("model", item.payload.get("model"), item.id)
    fresh(
        built,
        item.payload,
        (item.id, *twins),
        "input_token_count",
        "cached_token_count",
    )
    for name, key in _USAGE:
        built.put(name, item.payload.get(key), item.id, *twins)
    if took is not None:
        built.put("duration_ms", took[0], item.id, took[1])
        built.put("duration_source", _DURATION_SOURCE)
    return correlate.activity(
        capture_id,
        "model_request",
        item.id,
        actor="agent",
        started_at=item.ts,
        arrival=item.ingest,
        ended_at=None,
        built=built,
    )


def _rollout_request(
    capture_id: str, index: int, item: Obs, context: Obs | None
) -> Activity:
    """One request as the rollout alone saw it. The import path, and only that path.

    Every counter comes from `last_token_usage`, which is this response's own usage;
    `total_token_usage` on the same record is the turn's running total and is read by
    `_turn_totals` and by nothing here. The model and the effort come from the latest
    turn_context before this row on the provider clock, because token_count carries no
    turn id (DRIFT) and there is no id to join on.
    """
    usage = item.payload[_LAST_USAGE]
    built = Fields()
    built.put("request_index", index)
    built.put("usage_source", f"{_TOKEN_COUNT}.{_LAST_USAGE}", item.id)
    if context is not None:
        built.put("model", context.payload.get("model"), context.id)
        built.put("effort", context.payload.get("effort"), context.id)
    fresh(built, usage, (item.id,), "input_tokens", "cached_input_tokens")
    for name, key in _TURN_USAGE:
        built.put(name, usage.get(key), item.id)
    return correlate.activity(
        capture_id,
        "model_request",
        item.id,
        actor="agent",
        # When the usage was RECORDED, which is not when the response completed: E02
        # measured the token_count timestamp equal to the FOLLOWING tool output's (S1,
        # custom_tool_call_output and token_count both at 22:54:30.241Z). It is this
        # row's position on the request clock and nothing more, and the duration it
        # would imply was measured and rejected (`_durations`).
        started_at=item.ts,
        arrival=item.ingest,
        ended_at=None,
        built=built,
    )


def _turn_context(observed: Sequence[Obs], item: Obs) -> Obs | None:
    """The turn_context in force when this token_count was written, or None.

    By the provider clock, because turn_context carries a turn id and token_count does
    not. A rollout that begins mid-session has token_count records with no turn_context
    before them, and those rows state no model rather than borrowing a later turn's.
    """
    if item.ts is None:
        return None
    before = [
        one
        for one in observed
        if one.type == "codex.rollout.turn_context" and one.ts and one.ts <= item.ts
    ]
    return max(before, key=lambda one: (one.ts or "", one.id)) if before else None


def fresh(
    built: Fields,
    usage: Mapping[str, Any],
    sources: tuple[str, ...],
    total: str,
    cached: str,
) -> None:
    """Input tokens as the two quantities Codex reports in one field.

    `sources` is a TUPLE and not a Sequence, so that mypy refuses a bare string here: a
    str is a Sequence[str], and passing one spreads its characters into the provenance
    list. Measured, before this annotation: provenance.input_tokens held '0', 'S' and
    the rest of an observation id, and the golden test that resolves every provenance
    id caught it.

    `input_tokens` is the fresh part, which is what design 6.11 means by fresh_input and
    what the summary prints. `total_input_tokens` is the wire value. The subtraction is
    skipped, and `input_tokens` stays absent, unless both are integers and the cached
    part is not larger: an unknown stays unknown and is never a negative token count.
    """
    whole, reused = usage.get(total), usage.get(cached)
    built.put("total_input_tokens", whole, *sources)
    if isinstance(whole, int) and isinstance(reused, int) and whole >= reused:
        built.put("input_tokens", whole - reused, *sources)


def _sorted(
    observed: Sequence[Obs], obs_type: str, event_kind: str | None = None
) -> list[Obs]:
    """The observations of one type on the provider clock, ties broken by arrival."""
    rows = [
        item
        for item in observed
        if item.type == obs_type
        and (event_kind is None or item.payload.get("event_kind") == event_kind)
    ]
    return sorted(rows, key=lambda item: (item.ts or "", item.id))


# -- the two surfaces agreeing about one request --------------------------------------
@dataclass(frozen=True)
class _Pairing:
    """Which rollout token_count is which OTel response, inside one capture.

    A pure function of the capture (`_pairing`), so `activities` and `diagnose` compute
    it separately and get the same answer rather than passing state between reducers.
    """

    usable: list[Obs] = field(default_factory=list)
    twins: dict[str, list[str]] = field(default_factory=dict)
    unusable: list[str] = field(default_factory=list)
    unpaired: list[tuple[str, list[str]]] = field(default_factory=list)

    def diagnostics(self) -> list[tuple[str, str]]:
        """What this pairing could not do, as (kind, detail) rows. Spec 7.2."""
        rows = [
            ("conflict", _conflict([rollout_id], candidates, _UNPAIRED))
            for rollout_id, candidates in self.unpaired
        ]
        if not self.unusable:
            return rows
        dropped = to_json({
            "metric": "model_request usage",
            "surface": _TOKEN_COUNT,
            "reason": _UNUSABLE,
            "dropped": len(self.unusable),
            "observations": self.unusable[:_NAMED_IDS],
        })  # fmt: skip
        return [("dropped", dropped), *rows]


def _conflict(secondary: Sequence[str], primary: Sequence[str], why: str) -> str:
    """One spec 7.2 conflict row: what the two surfaces are, and which one was kept.

    The same five keys correlate.conflicts writes for Claude, so a reader of the
    diagnostics table sees one shape whatever found the disagreement.
    """
    return to_json({
        "metric": "model_request usage",
        "primary": list(primary[:_NAMED_IDS]),
        "secondary": list(secondary),
        "kept": "primary",
        "unpaired_responses_remaining": len(primary),
        "fields": {f"{_TOKEN_COUNT}.{_LAST_USAGE}": why},
    })  # fmt: skip


def _pairing(observed: Sequence[Obs], responses: Sequence[Obs]) -> _Pairing:
    """Pair each rollout token_count with the sse response that states the same usage.

    IN ORDER, and by equality on all three counters both surfaces state. In order
    because neither surface carries a request id: the rollout has no sse id and the sse
    record has no rollout id, so position within the capture is the only join, and a
    scan that never goes backwards cannot pair one response with two rollout rows.
    Equality because a pairing by position alone would attach a rollout row to whatever
    response happened to be next, which is a guess with the shape of a fact.

    Measured on the three fixtures that carry both surfaces: S1 6 of 6, S3 2 of 2, S6 9
    of 9 rollout rows that carry a usage mapping. The sse records left over are the
    pre-turn one on all three (11437, 0, 0) and, on S6, one response.completed that
    carries no counters at all; S6's tenth token_count is the one whose `info` is null,
    which is the `dropped` row above and never a zero request.

    S2 and S4 are the case that does NOT pair cleanly, and they are why the leftovers
    are a diagnostic rather than an assumption. They are two runs against ONE Codex
    thread (S4 is a resume of S2), so E02 copied the same 38-line rollout into both
    fixture directories: byte for byte identical, same session id, five token_count
    records spanning 22:53:36 to 22:55:54. Each scenario's OTel capture covers only its
    own run, four responses for S2 and one for S4, so S2 has one leftover rollout row
    and S4 has three. Neither is a disagreement about one request; the two surfaces
    cover different spans, and the conflict row says which records were compared.
    """
    usable: list[Obs] = []
    unusable: list[str] = []
    for item in _sorted(observed, _TOKEN_COUNT):
        if isinstance(item.payload.get(_LAST_USAGE), dict):
            usable.append(item)
        else:
            unusable.append(item.id)
    twins: dict[str, list[str]] = {}
    unpaired: list[tuple[str, list[str]]] = []
    at = 0
    for item in usable if responses else ():
        mine = _triple(item.payload[_LAST_USAGE], _ROLLOUT_TRIPLE)
        found = next(
            (
                index
                for index in range(at, len(responses))
                if mine is not None
                and _triple(responses[index].payload, _SSE_TRIPLE) == mine
            ),
            None,
        )
        if found is None:
            unpaired.append((item.id, [one.id for one in responses[at:]]))
            continue
        twins.setdefault(responses[found].id, []).append(item.id)
        at = found + 1
    return _Pairing(usable, twins, unusable, unpaired)


def _triple(usage: Mapping[str, Any], keys: Sequence[str]) -> tuple[int, ...] | None:
    """The three counters as integers, or None when any of them is not one.

    None rather than a tuple holding a None, so that two records neither of which
    states its input tokens can never be read as the same request.
    """
    values = [usage.get(key) for key in keys]
    if not all(isinstance(value, int) for value in values):
        return None
    return tuple(value for value in values if isinstance(value, int))


# -- how long a request took ----------------------------------------------------------
def _durations(
    responses: Sequence[Obs], sent: Sequence[Obs]
) -> tuple[dict[str, tuple[int, str]], list[tuple[str, str]]]:
    """Each request's duration, derived, as {response id: (milliseconds, send id)}.

    `codex.websocket_request.event.timestamp` is when the request went out and
    `codex.sse_event response.completed .event.timestamp` is when it came back, and the
    two counts are equal in every capture that carries both (S1 7 and 7, S3 3 and 3, S6
    11 and 11). They are paired by position, which on all 21 pairs of the three
    fixtures gives the same answer as pairing each response with the nearest preceding
    send. Counts that differ produce NO duration and one diagnostic: an unequal pairing
    would date one request from another one's send.

    THE ROLLOUT CANNOT DO THIS, and the rule was written before the run. Derive each
    response's span from the rollout as (the last response_item of the response) minus
    (the last tool output, user_message or task_started before its first response_item)
    and compare with the OTel span of its twin; adopt it only if 90 percent of the
    paired responses agree within 10 percent relative. Measured over S1, S3 and S6
    pooled (experiments/W7-T1/duration_probe.py, n = 17): 14 agree, 82.4 percent, so
    the rule rejects it. The three that fail are the first response of a turn, where
    the only start marker in the rollout is task_started and the span picks up the
    prompt assembly as well: S1 6134 ms against 3487, S3 6086 against 3462, S6 11060
    against 4121. So a rollout-only capture has no request duration, `request_duration`
    is `unavailable` on the rollout surface, and the cells stay None.
    """
    if len(sent) != len(responses):
        return {}, [("conflict", _mismatch(len(sent), len(responses)))]
    out: dict[str, tuple[int, str]] = {}
    for request, response in zip(sent, responses, strict=True):
        if request.ts is None or response.ts is None:
            continue
        span = _span_ms(request.ts, response.ts)
        if span is None:
            continue
        if span < 0:
            # A response that predates the send it was paired with means the pairing is
            # wrong, and a negative duration is not a shorter one. Nothing is kept.
            return {}, [("conflict", _backwards(request, response))]
        out[response.id] = (span, request.id)
    return out, []


def _mismatch(sends: int, responses: int) -> str:
    return to_json({
        "metric": "model_request duration",
        "primary": [_SENT, sends],
        "secondary": [f"{RESPONSE} {_RESPONSE_KIND}", responses],
        "kept": "no duration",
        "fields": {"counts": [sends, responses]},
    })  # fmt: skip


def _backwards(request: Obs, response: Obs) -> str:
    return to_json({
        "metric": "model_request duration",
        "primary": [response.id],
        "secondary": [request.id],
        "kept": "no duration",
        "fields": {"timestamps": [request.ts, response.ts]},
    })  # fmt: skip


def _span_ms(start: str, end: str) -> int | None:
    """Two provider timestamps as whole milliseconds between them, or None.

    None for a stamp this cannot read and for a pair where one carries a zone and the
    other does not: a span between two clocks that were never compared is not a number.
    """
    try:
        began = datetime.fromisoformat(start.replace("Z", "+00:00"))
        done = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    if (began.tzinfo is None) != (done.tzinfo is None):
        return None
    return (done - began) // timedelta(milliseconds=1)


# -- what the reduction could not do --------------------------------------------------
def diagnose(store: Store, capture_id: str) -> None:
    """The `dropped` and `conflict` rows this reduction found. Design 6.10, spec 7.2.

    A second reducer rather than a return value, because `activities()` is handed
    observations and never the store. It recomputes the pairing rather than carrying it
    over from the run before: a rebuild is a pure function of the capture, and the read
    is by observation type, so a capture with none of the three types costs one indexed
    lookup that returns nothing.

    Only rows not already there, exactly as activities.py does it. `rebuild` deletes a
    capture's `conflict` rows before the reducers run, so those are rewritten every
    time; a `dropped` row survives, so writing it unconditionally would grow the count
    on a capture where nothing changed. `launcher` rows are already handled this way.
    """
    observed = [
        correlate.read(row) for row in store.observations(capture_id, _REQUEST_TYPES)
    ]
    if not observed:
        return
    responses = _sorted(observed, RESPONSE, _RESPONSE_KIND)
    found = _pairing(observed, responses).diagnostics()
    found += _durations(responses, _sorted(observed, _SENT))[1] if responses else []
    already = {str(row["detail"]) for row in store.diagnostics(capture_id)}
    for kind, detail in found:
        if detail not in already:
            store.diagnose(kind, detail, capture_id)


# Design 6.10: the reducer registry is how `telltale rebuild` finds this. Registered on
# import, and this module is imported by activities.py from inside the run of the first
# reducer, so the append lands in the list `store.rebuild` is iterating and this reducer
# runs for that capture too (verified: a list append during `for x in list` is reached).
_REDUCERS: list[Callable[[Store, str], None]] = Store.reducers
if diagnose not in _REDUCERS:
    _REDUCERS.append(diagnose)
