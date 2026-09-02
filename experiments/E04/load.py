"""E04 load generator: OTLP JSON log batches at a Telltale receiver, as fast as it can.

The question this answers is not "how fast is the receiver". It is whether a receiver
that cannot keep up still answers, and answers quickly, while an agent is being
recorded through it. Design 6.5 bounds the wait a request thread pays at 250 ms
(`PUT_TIMEOUT_S` in store.py) and spec 5.2 says every POST is answered 200 whatever
happened, so what is measured here is the tail of the answer time and the number of
answers that were not 200.

A batch is 100 `claude_code.api_request` log records shaped like the E01 fixtures
(`fixtures/sources/claude/2.1.257/S2/otel_logs.jsonl`), carrying no
`telltale.capture_id`: attribution then falls to the receiver's default capture, which
is the capture under way. That is the point. The load lands INSIDE the capture it is
trying to disturb.

The batch bytes are built once and re-sent unchanged. Every record is therefore a
duplicate of every other, which the store does not deduplicate (a fresh observation id
per parse), so the row count is real. What this does NOT measure is parse-time variety:
one record shape, one size.

Usage:
    uv run python experiments/E04/load.py --port 51234 --seconds 20 --threads 4
"""

from __future__ import annotations

import argparse
import http.client
import json
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any

RECORDS_PER_BATCH = 100

# A POST that takes longer than this is a failure rather than a wait. 30 s is 120 times
# the 250 ms bound store.py holds a non-droppable batch for, so nothing inside the
# design's own budget can hit it.
TIMEOUT_S = 30.0

# The threshold rule (2) of docs/experiments/E04.md judges against. Recorded here so the
# generator's own summary says how many POSTs crossed it, rather than leaving the
# question to a reader with a list of latencies.
SLOW_S = 0.300

# How many of the slowest POSTs are kept individually. The full list of every latency is
# tens of thousands of numbers; these are the ones a violation would be found in.
SLOWEST_KEPT = 20

SESSION_ID = "e04load00-0000-4000-8000-000000000001"


def _attribute(key: str, value: Any) -> dict[str, Any]:
    """One OTLP attribute. bool before int: bool is an int subclass in Python."""
    if isinstance(value, bool):
        kind = "boolValue"
    elif isinstance(value, int):
        kind = "intValue"
    elif isinstance(value, float):
        kind = "doubleValue"
    else:
        kind = "stringValue"
    return {"key": key, "value": {kind: value}}


def _record(index: int) -> dict[str, Any]:
    """One synthetic api_request, with the attributes E01 measured on a real one."""
    nanos = str(1788293015914000000 + index)
    fields: tuple[tuple[str, Any], ...] = (
        ("user.id", "telltale-fake-user-id"),
        ("session.id", SESSION_ID),
        ("organization.id", "00000000-0000-4000-8000-000000000002"),
        ("terminal.type", "ghostty"),
        ("event.name", "api_request"),
        ("event.timestamp", "2026-09-02T00:00:00.000Z"),
        ("event.sequence", index),
        ("model", "claude-sonnet-5"),
        ("input_tokens", 2),
        ("output_tokens", 124),
        ("cache_read_tokens", 18549),
        ("cache_creation_tokens", 12895),
        ("cost_usd", 0.0565338),
        ("duration_ms", 2296),
        ("request_id", f"req_E04load{index:06d}"),
        ("speed", "normal"),
        ("query_source", "sdk"),
    )
    return {
        "timeUnixNano": nanos,
        "observedTimeUnixNano": nanos,
        "body": {"stringValue": "claude_code.api_request"},
        "attributes": [_attribute(key, value) for key, value in fields],
        "droppedAttributesCount": 0,
    }


def batch() -> bytes:
    """The one body every thread posts. service.name routes it to the claude parser."""
    resource = (
        ("host.arch", "arm64"),
        ("os.type", "darwin"),
        ("os.version", "25.6.0"),
        ("service.name", "claude-code"),
        ("service.version", "2.1.257"),
    )
    payload = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [_attribute(k, v) for k, v in resource],
                    "droppedAttributesCount": 0,
                },
                "scopeLogs": [
                    {
                        "scope": {
                            "name": "com.anthropic.claude_code.events",
                            "version": "2.1.257",
                        },
                        "logRecords": [
                            _record(index) for index in range(RECORDS_PER_BATCH)
                        ],
                    }
                ],
            }
        ]
    }
    return json.dumps(payload).encode("utf-8")


def flood(
    port: int, body: bytes, deadline: float, out: list[tuple[int | None, float, str]]
) -> None:
    """POST until the deadline on one keep-alive connection. Never raises.

    A connection that breaks is reopened on the next iteration rather than retried in
    place: a retry would hide the failed POST, and a failed POST is the finding.
    """
    connection: http.client.HTTPConnection | None = None
    mine: list[tuple[int | None, float, str]] = []
    while time.monotonic() < deadline:
        start = time.perf_counter()
        try:
            if connection is None:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", port, timeout=TIMEOUT_S
                )
            connection.request("POST", "/v1/logs", body=body, headers=_JSON_HEADERS)
            response = connection.getresponse()
            response.read()
            mine.append((response.status, time.perf_counter() - start, ""))
        except (OSError, http.client.HTTPException) as error:
            mine.append((None, time.perf_counter() - start, repr(error)))
            if connection is not None:
                connection.close()
            connection = None
    if connection is not None:
        connection.close()
    out.extend(mine)


_JSON_HEADERS = {"Content-Type": "application/json"}


def healthz(port: int) -> dict[str, Any] | str:
    """The /healthz body, or the reason there is none. Never raises."""
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT_S)
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        raw = response.read()
        connection.close()
    except (OSError, http.client.HTTPException) as error:
        return repr(error)
    try:
        loaded = json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", errors="replace")[:2000]
    return loaded if isinstance(loaded, dict) else {"body": loaded}


def summary(
    results: list[tuple[int | None, float, str]],
    port: int,
    seconds: float,
    threads: int,
) -> dict[str, Any]:
    """Every number this generator has, and none it does not."""
    latencies = sorted(latency for _status, latency, _why in results)
    slow = sorted(results, key=lambda row: row[1], reverse=True)[:SLOWEST_KEPT]
    median = statistics.median(latencies) if latencies else None
    mad = (
        statistics.median([abs(one - median) for one in latencies])
        if median is not None and len(latencies) > 1
        else None
    )
    return {
        "port": port,
        "seconds": seconds,
        "threads": threads,
        "records_per_batch": RECORDS_PER_BATCH,
        "batch_bytes": len(batch()),
        "posts": len(results),
        "records_posted": len(results) * RECORDS_PER_BATCH,
        "status_counts": _statuses(results),
        "non_200": sum(1 for status, _l, _w in results if status != 200),
        "errors": sorted({why for _s, _l, why in results if why})[:10],
        "latency_s": {
            "median": median,
            "mad_scaled": None if mad is None else 1.4826 * mad,
            "min": latencies[0] if latencies else None,
            "max": latencies[-1] if latencies else None,
        },
        "over_300ms": sum(1 for one in latencies if one > SLOW_S),
        "slowest_s": [
            {"status": status, "latency_s": latency, "error": why}
            for status, latency, why in slow
        ],
        "healthz": healthz(port),
    }


def _statuses(results: list[tuple[int | None, float, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for status, _latency, _why in results:
        name = "connection_error" if status is None else str(status)
        counts[name] = counts.get(name, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--out", default=None, metavar="PATH", help="write the summary")
    args = parser.parse_args()

    body = batch()
    # One list per thread, joined after every thread has finished: no lock, because no
    # two threads ever touch the same list and nothing reads them while they run.
    collected: list[list[tuple[int | None, float, str]]] = [
        [] for _ in range(args.threads)
    ]
    deadline = time.monotonic() + args.seconds
    workers = [
        threading.Thread(target=flood, args=(args.port, body, deadline, mine))
        for mine in collected
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    results = [row for mine in collected for row in mine]

    found = summary(results, args.port, args.seconds, args.threads)
    print(
        f"posts {found['posts']}, max latency {found['latency_s']['max']} s, "
        f"non-200 {found['non_200']}, over 300 ms {found['over_300ms']}"
    )
    print(json.dumps(found["healthz"], indent=2, sort_keys=True))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
