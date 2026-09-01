"""The `telltale` command. Design 6.13; argparse, and report.py renders.

Two commands so far, and they are the two that answer questions about Telltale itself
rather than about a capture.

`doctor` round-trips one synthetic record through every endpoint of a receiver it starts
in-process, reads each one back out of a temporary database and prints what came back.
That is a different question from "does the code import": every surface has a route, a
parser, an allowlist entry and a column, and any one of the four can be missing while
the other three are fine. It also reports whether git, claude, codex, uv and timesfm are
present, and those lines never decide the exit code, because a machine without the
claude binary is a machine where Telltale still works.

`setup claude|codex --print` prints the snippet the owner may paste. It never writes
one: the owner decision of 2026-09-01 is launcher-only configuration, and `--apply`
prints a refusal that says so. AGENTS.md invariant 7.

Every other command named in the design (run, daemon, sessions, show, timeline, explain,
compare, rebuild, purge, schema, export, experiment, series, forecast) arrives with the
task that implements the thing it prints.
"""

from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import __version__, config
from telltale.providers import claude
from telltale.receiver import Receiver, _drain, _post, _with_capture
from telltale.report import render_table
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Sequence

# Exit code for a refusal: the command exists, it ran, and it declined on purpose.
# Distinct from 1, which this file spends on a surface that did not round-trip.
_REFUSED = 2

# The capture id doctor's synthetic records carry. A real id, so the rows are queryable
# in the temporary database, and one nobody can mistake for a capture.
_DOCTOR_CAPTURE = "doctor"
# One fixed session id for every synthetic record, so they correlate as one session.
_DOCTOR_SESSION = "00000000-0000-4000-8000-0000d0c70000"
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


def _otlp_logs() -> dict[str, Any]:
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
                                    _attr("session.id", _DOCTOR_SESSION),
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


def _otlp_metrics() -> dict[str, Any]:
    point = {
        "attributes": [_attr("session.id", _DOCTOR_SESSION)],
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
        ("otel_logs", "/v1/logs", "claude.otel.api_request", _otlp_logs()),
        ("otel_metrics", "/v1/metrics", "claude.otel.metric", _otlp_metrics()),
        (
            "hook",
            "/hooks/claude",
            "claude.hook.SessionEnd",
            {
                "hook_event_name": "SessionEnd",
                "session_id": _DOCTOR_SESSION,
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
                "session_id": _DOCTOR_SESSION,
                "uuid": _DOCTOR_SESSION,
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


def _roundtrip() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Post one record per endpoint into a throwaway store and read each one back.

    One POST and one drain per surface, rather than all seven and one drain: it is what
    lets the table say which type came back on WHICH surface, and a surface whose parser
    produces the wrong type then reads as that type rather than as silence.
    """
    home = config.home()
    home.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="doctor-", dir=home))
    store = Store(workdir / "doctor.db").open()
    receiver = Receiver(store, level=1, provider="claude")
    port = receiver.start()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for surface, route, expected, body in _records():
            _post(port, _with_capture(route, _DOCTOR_CAPTURE), _encode(body))
            _drain(port)
            arrived = _types(store) - seen
            seen |= arrived
            rows.append(_surface_row(surface, expected, arrived))
    finally:
        receiver.stop()
    kinds = _diagnostic_kinds(store)
    store.close()
    shutil.rmtree(workdir, ignore_errors=True)
    return rows, kinds


def _encode(body: dict[str, Any]) -> bytes:
    return json.dumps(body).encode("utf-8")


def _types(store: Store) -> set[str]:
    return {str(row["observation_type"]) for row in store.observations(_DOCTOR_CAPTURE)}


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


def _daemon_row(port: int) -> dict[str, Any]:
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


def _tool_rows() -> list[dict[str, Any]]:
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


def doctor(port: int) -> int:
    rows, kinds = _roundtrip()
    rows.append(_daemon_row(port))
    print(render_table(rows, ("surface", "result", "observed")))
    print()
    print(render_table(_tool_rows(), ("tool", "result", "detail")))
    print()
    print(f"diagnostics written by the round trip: {kinds or 'none'}")
    failed = [row for row in rows if row["result"] != "ok"]
    if failed:
        print(f"doctor: {failed[0]['surface']} did not round-trip")
        return 1
    print(f"doctor: {len(rows)} surfaces round-trip")
    return 0


def _claude_snippet(port: int, level: int) -> dict[str, Any]:
    """The settings.json fragment for the daemon, taken from the real launch plan.

    Not written out by hand: `claude.launch` is what the launcher will use, so the hook
    list and the OTel variables here cannot drift from the ones a capture actually gets.
    """
    plan = claude.launch(["claude"], port, level, session_id=None)
    settings: dict[str, Any] = json.loads(plan.argv[plan.argv.index("--settings") + 1])
    settings["env"] = plan.env
    return settings


_CODEX_PENDING = """\
# telltale setup codex: SPELLING PENDING E02.
#
# What a codex session has to be told, which is known:
#   send OTLP logs and metrics to http://127.0.0.1:{port} over http/json
#   send hooks to http://127.0.0.1:{port}/hooks/codex
#
# How ~/.codex/config.toml spells those two settings is NOT known here, and this
# command will not invent a key name that nobody has run. E02 measures how `codex
# exec` takes OTel and hook configuration; this snippet becomes the real one when it
# lands. Until then there is nothing here to paste.
"""

_REFUSAL = """\
telltale setup --apply is refused, and the refusal is a decision rather than a gap.
Owner decision of 2026-09-01 (docs/design/02-protocol.md, "Global config"): capture is
launcher-only, and Telltale never edits ~/.claude/settings.json, ~/.codex/config.toml or
anything else outside this repository and $TELLTALE_HOME. AGENTS.md invariant 7.
Run `telltale setup {provider} --print` and paste what it prints, or run the agent under
`telltale run`, which configures the child process and leaves no file behind.\
"""


def setup(provider: str, apply: bool, port: int, level: int) -> int:
    if apply:
        print(_REFUSAL.format(provider=provider))
        return _REFUSED
    if provider == "codex":
        print(_CODEX_PENDING.format(port=port), end="")
        return 0
    print(json.dumps(_claude_snippet(port, level), indent=2, sort_keys=True))
    return 0


def _daemon_port(override: int | None) -> int:
    """--port, then config.json's daemon_port, then the default. Never a guess."""
    if override is not None:
        return override
    value = config.load().get("daemon_port")
    return int(value) if isinstance(value, int | str) else config.DEFAULT_DAEMON_PORT


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telltale",
        description="A local flight recorder for coding-agent sessions.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command")
    check = subcommands.add_parser(
        "doctor", help="round-trip one record through every capture surface"
    )
    check.add_argument(
        "--port",
        type=int,
        default=None,
        help="the daemon port to check (default: config.json, then 47311)",
    )
    snippet = subcommands.add_parser(
        "setup", help="print the configuration snippet for a provider"
    )
    snippet.add_argument("provider", choices=("claude", "codex"))
    snippet.add_argument(
        "--print", action="store_true", help="print the snippet (the default)"
    )
    snippet.add_argument(
        "--apply", action="store_true", help="refused: Telltale never writes it"
    )
    snippet.add_argument("--port", type=int, default=None, help="the daemon port")
    snippet.add_argument("--level", type=int, default=1, choices=(0, 1, 2))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `telltale` console script; returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor(_daemon_port(args.port))
    if args.command == "setup":
        return setup(args.provider, args.apply, _daemon_port(args.port), args.level)
    parser.print_help()
    return 0
