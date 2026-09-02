"""What the recorder does when it cannot keep up, asked of a queue that is really full.

Spec 5.2 and AGENTS.md invariant 8: a Telltale failure never changes the behaviour of
the process being recorded. The client here is the agent. An OTLP exporter retries a
5xx and backs off; an http hook blocks a tool call until it gets an answer, inside a 5
second timeout. So every endpoint answers 200 whatever happened, no request thread waits
longer than `PUT_TIMEOUT_S`, and the loss is counted rather than hidden.

The queue is filled for real rather than simulated: `Store(..., queue_max=2)` is the
same code path as the default 10000 and fills in three POSTs, and `pause_writer` (which
says TEST-ONLY in its own docstring) holds the writer so the queue stays full. Nothing
in the receiver, the store or the provider is replaced.

Three claims, one per test:

  1. a full queue answers every POST fast, loses only what design 6.5 permits it to
     lose, and the arithmetic of what was posted, stored and dropped closes exactly;
  2. a record that is NOT droppable waits for room, and waits at most PUT_TIMEOUT_S,
     and its loss is written down;
  3. a string SQLite cannot encode does not kill the writer thread, which is the
     failure W0-T2 measured before the guard existed.
"""

from __future__ import annotations

import json
import sys
import time
from typing import TYPE_CHECKING, Any

import pytest

from telltale.store import DROPPABLE_TYPE_PREFIXES, PUT_TIMEOUT_S, Store

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from conftest import Live

CAPTURE = "cap_failopen"
SESSION = "00000000-0000-4000-8000-00000000fa11"

# Two, so three POSTs fill it: the writer takes one item off the queue before it blocks.
QUEUE_MAX = 2
# Design 6.5 promises the client sees at most PUT_TIMEOUT_S (250 ms). The extra 50 ms is
# for the loopback connection, the parse and the sanitizer, and the measured maximum is
# printed by every test here so the margin is a number rather than a hope.
ANSWER_MS = 300.0
DROPPABLE = 40
LIFECYCLE = 10
# More than the queue can hold plus the one the writer is holding, so that the last
# three POSTs are certain to meet a full queue.
WAITERS = 6

# How long a test waits for the writer to catch up before calling it wedged. A poll, not
# a sleep: the assertions never wait for a duration, they wait for a state.
SETTLE_S = 10.0


def _metric(value: int) -> bytes:
    """One OTLP metric data point, in the encoding E01 recorded. One observation."""
    point = {
        "attributes": [_attr("session.id", SESSION)],
        "timeUnixNano": "1788293057178000000",
        "asInt": value,
    }
    return _encode(
        {
            "resourceMetrics": [
                {
                    "resource": {"attributes": [_attr("service.name", "claude-code")]},
                    "scopeMetrics": [
                        {
                            "metrics": [
                                {
                                    "name": "claude_code.session.count",
                                    "sum": {"dataPoints": [point]},
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    )


def _hook(reason: str) -> bytes:
    """One SessionEnd hook body. Not droppable: a session ends once."""
    return _encode(
        {
            "hook_event_name": "SessionEnd",
            "session_id": SESSION,
            "reason": reason,
        }
    )


def _attr(key: str, value: Any) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _encode(body: dict[str, Any]) -> bytes:
    return json.dumps(body).encode("utf-8")


def _wait(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + SETTLE_S
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError(f"the store never reached the state: {what}")


def _report(label: str, times: list[float]) -> None:
    # sys.stdout.write, not print: ruff T20 keeps print() in cli.py and report.py, and
    # the measured latency is the point of this file, so it is written down.
    worst = max(times)
    top = " ".join(f"{value:.1f}" for value in sorted(times, reverse=True)[:3])
    sys.stdout.write(f"\n{label}: n={len(times)} max={worst:.1f} ms (top: {top})")


def _full_store(home: Path) -> Store:
    return Store(home / "telltale.db", queue_max=QUEUE_MAX).open()


@pytest.mark.integration
def test_a_full_queue_answers_every_post_and_keeps_every_lifecycle_record(
    telltale_home: Path, receiver: Callable[..., Live]
) -> None:
    """Droppable records are lost and counted; nothing else is lost at all."""
    store = _full_store(telltale_home)
    live = receiver(target=store)
    store.pause_writer()

    metrics = [
        live.timed_post("/v1/metrics", _metric(index), CAPTURE)
        for index in range(DROPPABLE)
    ]
    _report("40 otel_metrics POSTs against a full queue", [ms for _s, ms in metrics])
    assert {status for status, _ms in metrics} == {200}
    assert max(ms for _s, ms in metrics) < ANSWER_MS

    status, body = live.healthz()
    # Measured, and NOT what this task's brief expected. /healthz answers 503 only when
    # the writer is dead or the store is down (Receiver.health); pending drops are
    # reported in the BODY and leave the status at 200. The body is what design 6.5
    # fixes: 503 carrying writer state, per-surface last-received time and drop counts.
    # Whether the STATUS should follow the drop counts is a receiver decision, and
    # receiver.py is READS for W0-T5, so this asserts what the code does and says so.
    assert status == 200
    assert body["store"]["drops_unreported"] > 0
    assert body["store"]["drops_by_surface"]["otel_metrics"] > 0

    store.resume_writer()
    live.drain()
    _wait(lambda: _dropped_rows(store) != [], "the drops were written as a diagnostic")

    hooks = [
        live.timed_post("/hooks/claude", _hook(f"end-{index}"), CAPTURE)
        for index in range(LIFECYCLE)
    ]
    _report("10 hook POSTs against a running writer", [ms for _s, ms in hooks])
    assert {status for status, _ms in hooks} == {200}
    assert max(ms for _s, ms in hooks) < ANSWER_MS
    live.drain()
    _wait(lambda: len(_by_surface(store, "hook")) == LIFECYCLE, "every hook stored")

    stored_metrics = _by_surface(store, "otel_metrics")
    lost = DROPPABLE - len(stored_metrics)
    health = store.health()

    # The lifecycle records are posted AFTER the writer is released, and that ordering
    # is the whole reason this assertion is deterministic. A non-droppable batch offered
    # to a full queue waits PUT_TIMEOUT_S and is then dropped (the next test measures
    # exactly that), so posting them while the writer is held would make "every hook is
    # stored" depend on when the writer happened to be released. It would pass most
    # runs and fail some, which is worse than not testing it.
    assert len(_by_surface(store, "hook")) == LIFECYCLE
    assert 0 < len(stored_metrics) < DROPPABLE
    assert health["drops_by_surface"] == {"otel_metrics": lost}
    assert health["drops_total"] == lost
    assert health["drops_unreported"] == 0
    assert health["writer"] == "alive"
    assert not health["down"]

    # Only droppable types went missing, and the type that lost nothing is not one.
    assert {row["observation_type"] for row in stored_metrics} == {"claude.otel.metric"}
    assert "claude.otel.metric".startswith(DROPPABLE_TYPE_PREFIXES)
    assert not "claude.hook.SessionEnd".startswith(DROPPABLE_TYPE_PREFIXES)

    # One diagnostics row for the whole flood, not one per drop (design 6.5).
    rows = _dropped_rows(store)
    assert len(rows) == 1
    assert json.loads(str(rows[0]["detail"])) == {
        "per_surface": {"otel_metrics": lost},
        "total": lost,
    }


@pytest.mark.integration
def test_a_lifecycle_record_waits_at_most_the_put_timeout(
    telltale_home: Path, receiver: Callable[..., Live]
) -> None:
    """A record that may not be dropped waits for room, and not longer than promised.

    The first POSTs fit (one in the writer's hand, two in the queue). Every POST after
    them meets a full queue, and because a hook is not a droppable type it waits rather
    than being thrown away immediately. That wait is the number an agent feels: it
    happens inside a tool call, with the hook's 5 second timeout running.
    """
    store = _full_store(telltale_home)
    live = receiver(target=store)
    store.pause_writer()

    results = [
        live.timed_post("/hooks/claude", _hook(f"end-{index}"), CAPTURE)
        for index in range(WAITERS)
    ]
    waited = [ms for _status, ms in results[QUEUE_MAX + 1 :]]
    _report("hook POSTs that met a full queue", waited)

    assert {status for status, _ms in results} == {200}
    assert max(waited) < ANSWER_MS
    # Lower bound as well as upper: a POST that came back in a millisecond would mean
    # the record was thrown away without being offered room, which is what happens to a
    # metric and must not happen to a hook.
    assert min(waited) >= PUT_TIMEOUT_S * 1000.0

    store.resume_writer()
    live.drain()
    _wait(lambda: _dropped_rows(store) != [], "the drops were written as a diagnostic")

    stored = _by_surface(store, "hook")
    assert 0 < len(stored) < WAITERS
    assert store.health()["drops_by_surface"] == {"hook": WAITERS - len(stored)}


@pytest.mark.integration
def test_a_lone_surrogate_does_not_kill_the_writer(
    store: Store, receiver: Callable[..., Live]
) -> None:
    """The failure W0-T2 measured: one string, and the recorder goes quiet.

    `json.loads('"\\ud800"')` is legal and produces half a character that sqlite3
    refuses to encode. Before the guard, that exception left the writer loop, the thread
    died, and `append()` went on reporting success into a queue nobody drained. The
    store said it was fine and stored nothing.
    """
    live = receiver()
    surrogate = (
        b'{"hook_event_name":"SessionEnd","session_id":"' + SESSION.encode() + b'",'
        b'"reason":"end \\ud800"}'
    )

    status, milliseconds = live.timed_post("/hooks/claude", surrogate, CAPTURE)
    _report("the lone-surrogate POST", [milliseconds])
    live.drain()

    assert status == 200
    assert store.health()["writer"] == "alive"

    assert live.post("/hooks/claude", _hook("after-surrogate"), CAPTURE) == 200
    live.drain()
    _wait(
        lambda: any(
            row["payload"].get("reason") == "after-surrogate"
            for row in store.observations(CAPTURE)
        ),
        "the POST after the surrogate was stored",
    )

    kept = [row for row in store.observations(CAPTURE) if row["surface"] == "hook"]
    assert len(kept) == 2
    # The sanitizer replaced what it could not encode and said so, which is the guard
    # that keeps the row storable at all.
    assert any("reason:encoding" in row["redaction"]["redacted"] for row in kept)


def _by_surface(store: Store, surface: str) -> list[dict[str, Any]]:
    return [row for row in store.observations(CAPTURE) if row["surface"] == surface]


def _dropped_rows(store: Store) -> list[dict[str, Any]]:
    return [row for row in store.diagnostics() if row["kind"] == "dropped"]
