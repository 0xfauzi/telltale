"""The round trip behind `telltale doctor`: one synthetic record per surface through a
temporary in-process receiver, read back from a temporary store, plus the daemon probe
and the tool checks. Design 6.13. This module prints nothing: cli.py renders the rows it
returns, so every line here is a measurement and none is a message.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from telltale import config
from telltale.receiver import Receiver, _post, _with_capture
from telltale.store import Store

# One capture id and one session id per surface. A real id, so the rows are queryable
# in the temporary database, and one nobody can mistake for a capture.
#
# Per surface rather than one for all seven, because that is what lets a single read at
# the end say which surface produced which type. The store's `surface` column cannot:
# /v1/correlations, /v1/outcomes and /v1/policy_interventions all record `external`
# (receiver._store_external), so one capture would leave three rows sharing one answer.
# And per-surface captures need per-surface sessions: one session id under a second
# capture is a `conflict` diagnostic (receiver._learn), and doctor prints the
# diagnostics its own round trip wrote, which stays empty on a healthy machine.
_DOCTOR_CAPTURE = "doctor"
_DOCTOR_SESSIONS = {
    "otel_logs": "00000000-0000-4000-8000-0000d0c70001",
    "otel_metrics": "00000000-0000-4000-8000-0000d0c70002",
    "hook": "00000000-0000-4000-8000-0000d0c70003",
    "stream": "00000000-0000-4000-8000-0000d0c70004",
}
# Long enough for a listener that accepts a connection and never answers (which is what
# a port held by something else looks like), short enough that doctor stays a command
# somebody runs. Nothing measured this: it is a decision.
_PROBE_TIMEOUT_S = 2.0

_TOOLS = ("git", "claude", "codex", "uv")
# The forecasting stack's import name is not settled: pyproject pins the distribution
# `timesfm` and the mypy override names the module `timesfm3`. Both are checked, and
# neither is imported: find_spec answers without running the package's __init__, which
# on this stack pulls torch.
_TIMESFM_MODULES = ("timesfm3", "timesfm")


def _attr(key: str, value: Any) -> dict[str, Any]:
    """One OTLP attribute in the encoding E01 measured: a typed one-key value object."""
    numeric = isinstance(value, int) and not isinstance(value, bool)
    return {"key": key, "value": {"intValue" if numeric else "stringValue": value}}


def _otlp_logs(session: str) -> dict[str, Any]:
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        _attr("service.name", "claude-code"),
                        _attr("service.version", "doctor"),
                    ]
                },
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": "1788293057178000000",
                                "body": {"stringValue": "claude_code.api_request"},
                                "attributes": [
                                    _attr("session.id", session),
                                    _attr("model", "doctor"),
                                    _attr("input_tokens", 1),
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }


def _otlp_metrics(session: str) -> dict[str, Any]:
    point = {
        "attributes": [_attr("session.id", session)],
        "timeUnixNano": "1788293057178000000",
        "asInt": 1,
    }
    return {
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


def _records() -> tuple[tuple[str, str, str, dict[str, Any]], ...]:
    """(surface, route, the type a healthy round trip produces, body) per endpoint.

    The bodies are the provider's real shapes as E01 recorded them, and they carry
    allowlisted fields only, so a healthy round trip writes no diagnostics at all.
    """
    return (
        (
            "otel_logs",
            "/v1/logs",
            "claude.otel.api_request",
            _otlp_logs(_DOCTOR_SESSIONS["otel_logs"]),
        ),
        (
            "otel_metrics",
            "/v1/metrics",
            "claude.otel.metric",
            _otlp_metrics(_DOCTOR_SESSIONS["otel_metrics"]),
        ),
        (
            "hook",
            "/hooks/claude",
            "claude.hook.SessionEnd",
            {
                "hook_event_name": "SessionEnd",
                "session_id": _DOCTOR_SESSIONS["hook"],
                "reason": "doctor",
            },
        ),
        (
            "stream",
            "/v1/stream/claude",
            "claude.stream.system.init",
            {
                "type": "system",
                "subtype": "init",
                "session_id": _DOCTOR_SESSIONS["stream"],
                "uuid": _DOCTOR_SESSIONS["stream"],
                "model": "doctor",
                "permission_mode": "default",
            },
        ),
        (
            "correlations",
            "/v1/correlations",
            "external.correlation",
            {"external_system": "doctor", "external_run_id": "1", "attempt": 1},
        ),
        (
            "outcomes",
            "/v1/outcomes",
            "external.outcome",
            {"kind": "doctor", "status": "ok", "external_run_id": "1", "attempt": 1},
        ),
        (
            "policy_interventions",
            "/v1/policy_interventions",
            "policy.intervention",
            {"advisory_id": "doctor", "action": "none", "policy_version": "0"},
        ),
    )


def roundtrip() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Post one record per endpoint into a throwaway store, then read them all back.

    Nothing is read until the store is CLOSED, and that is the whole point of the
    ordering. close() queues a sentinel behind the last record and joins the writer
    thread, so it is the one moment where "the writer has finished with everything sent"
    is a fact. An empty queue is not it: Store._serve_once takes its job off the
    queue BEFORE it opens the transaction, so /healthz reports queue_depth 0 while the
    batch is still in flight, and the drain the replay path uses returns there. Measured
    with pause_writer, which holds the writer at exactly that point: POST 200, drain
    returns with queue_depth 0, and the observation is not readable yet, with no drop.
    Read at that instant, doctor prints `failed` for a healthy surface, and it did once
    in a full suite run before this changed.
    """
    home = config.home()
    home.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="doctor-", dir=home))
    store = Store(workdir / "doctor.db").open()
    receiver = Receiver(store, level=1, provider="claude")
    port = receiver.start()
    try:
        for surface, route, _expected, body in _records():
            _post(port, _with_capture(route, _capture(surface)), _encode(body))
    finally:
        receiver.stop()
    store.close()
    rows = [
        _surface_row(surface, expected, _types(store, _capture(surface)))
        for surface, _route, expected, _body in _records()
    ]
    kinds = _diagnostic_kinds(store)
    shutil.rmtree(workdir, ignore_errors=True)
    return rows, kinds


def _capture(surface: str) -> str:
    return f"{_DOCTOR_CAPTURE}-{surface}"


def _encode(body: dict[str, Any]) -> bytes:
    return json.dumps(body).encode("utf-8")


def _types(store: Store, capture: str) -> set[str]:
    """Readable after close(): every reader here opens its own read-only connection."""
    return {str(row["observation_type"]) for row in store.observations(capture)}


def _diagnostic_kinds(store: Store) -> dict[str, int]:
    kinds: dict[str, int] = {}
    for row in store.diagnostics():
        kind = str(row["kind"])
        kinds[kind] = kinds.get(kind, 0) + 1
    return kinds


def _surface_row(surface: str, expected: str, arrived: set[str]) -> dict[str, Any]:
    return {
        "surface": surface,
        "result": "ok" if expected in arrived else "failed",
        # None, not "", for a surface that stored nothing: the table renders unknown as
        # `-` and there is no count here that could be reported as zero.
        "observed": ", ".join(sorted(arrived)) or None,
    }


def daemon_row(port: int) -> dict[str, Any]:
    """Whether the port `telltale daemon` would bind is free, ours, or somebody's.

    A port nothing listens on is the normal state, because the daemon is opt-in. A port
    that accepts a connection and does not answer /healthz is the failure this row
    exists for: the receiver would bind nothing and every hook would go nowhere.
    """
    result, detail = _probe_daemon(port)
    return {"surface": "daemon_port", "result": result, "observed": detail}


def _probe_daemon(port: int) -> tuple[str, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=_PROBE_TIMEOUT_S)
    try:
        connection.request("GET", "/healthz")
        body = json.loads(connection.getresponse().read() or b"{}")
    except ConnectionRefusedError:
        return "ok", f"port {port} free"
    except (OSError, ValueError) as error:
        return "failed", f"port {port} held, not answering ({type(error).__name__})"
    finally:
        connection.close()
    if isinstance(body, dict) and "store" in body:
        return "ok", f"port {port} telltale daemon"
    return "failed", f"port {port} answered by something else"


def tool_rows() -> list[dict[str, Any]]:
    """git, claude, codex, uv and timesfm. NEVER part of the exit code (design 6.13).

    Telltale records a session that some other program runs. A machine with no claude
    binary is a machine where the collector, the store and the reducers all still work,
    and CI is exactly that machine.
    """
    found_at = {name: shutil.which(name) for name in _TOOLS}
    rows: list[dict[str, Any]] = [
        {"tool": name, "result": _present(path), "detail": path}
        for name, path in found_at.items()
    ]
    found = next((name for name in _TIMESFM_MODULES if _importable(name)), None)
    rows.append({"tool": "timesfm", "result": _present(found), "detail": found})
    return rows


def _present(value: object) -> str:
    return "present" if value else "absent"


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False
