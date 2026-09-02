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

`run` wraps one command and records it; launch.py does the work and this file parses the
argv and returns the child's exit code. `daemon` runs the same receiver in the
foreground on a fixed port, for the sessions an owner starts by hand: there the receiver
derives a capture from each provider session id, so a day-to-day session is captured
without a launcher and still without a line of global configuration. `sessions` lists
what either of them recorded.

Every other command named in the design (show, timeline, explain, compare, rebuild,
purge, schema, export, experiment, series, forecast) arrives with the task that
implements the thing it prints.
"""

from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import shutil
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import __version__, config, experiments, launch
from telltale.facts import Facts, facts
from telltale.providers import claude
from telltale.receiver import Receiver, _post, _with_capture
from telltale.report import render_table
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Sequence

# Exit code for a refusal: the command exists, it ran, and it declined on purpose.
# Distinct from 1, which this file spends on a surface that did not round-trip.
_REFUSED = 2

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


def _roundtrip() -> tuple[list[dict[str, Any]], dict[str, int]]:
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


def daemon(port: int, level: int) -> int:
    """One receiver, in the foreground, for the sessions the owner starts by hand.

    The launcher is still the only thing that CONFIGURES a capture; this is the other
    half of the same decision. A session an owner starts themselves was configured by
    the snippet `telltale setup claude --print` gave them, which points here, and the
    receiver derives one capture per provider session id (design 6.6). So a day-to-day
    session is recorded with no launcher, and Telltale has still written nothing outside
    $TELLTALE_HOME.

    Ctrl-C stops it: the receiver stops taking requests, everything already accepted is
    flushed, and the store closes. Nothing is timed out and nothing is waited for.
    """
    store = Store(config.db_path()).open()
    receiver = Receiver(store, level=level, port=port, derive_captures=True)
    # flush on every line: stdout is block-buffered when it is a pipe, and a daemon
    # whose whole output is one line per capture as it happens must not hold those
    # lines until it exits. Measured: without it, a reader of the pipe saw nothing.
    receiver.on_new_capture(lambda capture: print(f"capture {capture}", flush=True))
    bound = receiver.start()
    print(f"telltale daemon: http://127.0.0.1:{bound} -> {store.path}")
    print(
        f"content level {level}. Ctrl-C stops it. One line per capture follows.",
        flush=True,
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("")
    finally:
        receiver.stop()
        store.flush()
        store.close()
    return 0


# The sessions table, in the order design 6.13 names. `observations` is the count in
# the captures view, and `coverage` is delivered surfaces over configured ones.
_SESSION_COLUMNS = (
    "capture_id",
    "provider",
    # Beside the provider rather than in a column of its own next to model: it is a
    # property of the binary, and W1-T4 made it part of the environment fingerprint, so
    # two rows with one provider and two runtimes are two environments.
    "runtime",
    "model",
    "started",
    "duration_ms",
    "observations",
    "coverage",
    "commits",
)
_DEFAULT_LIMIT = 20
_DEFAULT_LEVEL = 1


def sessions(repo_id: str | None, limit: int, link_commits: bool) -> int:
    """The captures this database holds, newest first. Design 6.13."""
    store = Store(config.db_path()).open()
    try:
        rows, linked = _session_rows(store, repo_id, limit, link_commits)
    finally:
        store.close()
    print(render_table(rows, _SESSION_COLUMNS))
    if link_commits:
        print(f"\nlinked {linked} commits in this repository")
    return 0


def _session_rows(
    store: Store, repo_id: str | None, limit: int, link_commits: bool
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    linked = 0
    for capture in store.captures():
        if len(rows) >= limit:
            break
        if repo_id is not None and capture["repo_id"] != repo_id:
            continue
        capture_id = str(capture["capture_id"])
        known = facts(store, capture_id)
        # Only captures that ended with nothing linked: a capture that already has a
        # commit was linked by evidence this run cannot improve on.
        if link_commits and known.commits == 0:
            added = launch.link_commits(store, capture_id)
            linked += added
            known.commits += added
        rows.append(_session_row(capture, known))
    return rows, linked


def _session_row(capture: dict[str, Any], known: Facts) -> dict[str, Any]:
    return {
        "capture_id": capture["capture_id"],
        "provider": capture["provider"],
        "runtime": known.runtime_version,
        "model": known.model,
        # Seconds are enough to tell two captures apart in a list, and the microseconds
        # the store keeps make every column of this table twice as wide.
        "started": str(capture["first_ts"])[:19].replace("T", " "),
        "duration_ms": known.duration_ms,
        "observations": capture["observation_count"],
        "coverage": known.coverage(),
        "commits": known.commits,
    }


# The two tables `experiment repeat` prints. Design 6.13: the claim class column is
# never omitted, and here it is two different answers on one page, which is the point.
_RUN_COLUMNS = (
    "attempt",
    "capture_id",
    "exit_code",
    "acceptance",
    "wall_ms",
    "duration_ms",
    "coverage",
    "claim_class",
)
_STAT_COLUMNS = (
    "metric",
    "claim_class",
    "n",
    "unknown",
    "median",
    "mad_scaled",
    "iqr",
    "min",
    "max",
    "values",
)
_WITHIN = """\
Every row of the second table is COMPARATIVE WITHIN THIS CONDITION: one task, one base
commit, one environment fingerprint, {n} repetitions. It says how much a number moved
when nothing but the run changed. It is not a comparison with any other condition, and
the per-capture numbers it is built from are derived from one capture each."""


def experiment_repeat(spec_path: str, out: str | None) -> int:
    """Run one condition and print what it measured. Design 6.12."""
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    try:
        report = experiments.repeat(
            spec, config.home(), out=None if out is None else Path(out)
        )
    except (experiments.SpecError, experiments.FingerprintMismatch) as refusal:
        print(f"experiment repeat: {refusal}")
        return _REFUSED
    print(
        f"experiment {report['experiment']} task {report['task_id']}:"
        f" {len(report['captures'])} captures,"
        f" environment {report['environment_fingerprint_id']}"
    )
    print(f"acceptance: {report['acceptance']}")
    print()
    print(render_table(_run_rows(report), _RUN_COLUMNS))
    print()
    print(render_table(_stat_rows(report), _STAT_COLUMNS))
    print()
    print(_WITHIN.format(n=len(report["captures"])))
    for warning in report["warnings"]:
        print(f"warning: {warning}")
    return 0


def _run_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            **{name: run.get(name) for name in _RUN_COLUMNS},
            "acceptance": run["acceptance"]["status"],
            "claim_class": report["claim_class"]["vector"],
        }
        for run in report["repetitions"]
    ]


def _stat_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "metric": metric,
            "claim_class": report["claim_class"]["stats"],
            **{name: found.get(name) for name in _STAT_COLUMNS if name in found},
            **{
                name: _short(found[name]) for name in _SCALED if found[name] is not None
            },
            "values": ",".join(_short(value) for value in found["values"]),
        }
        for metric, found in sorted(report["stats"].items())
    ]


# Printed through _short, so a count prints as a count. The report.json keeps the full
# float: this rounding is for the terminal and never for the record.
_SCALED = ("median", "mad_scaled", "iqr", "min", "max")


def _short(value: float) -> str:
    """A float that is a whole number prints as one. Evidence.value is a REAL."""
    return str(int(value)) if float(value).is_integer() else f"{value:.3f}"


def purge(capture_id: str) -> int:
    """Delete one capture's observations and diagnostics. Design 6.13."""
    store = Store(config.db_path()).open()
    try:
        if capture_id not in {str(row["capture_id"]) for row in store.captures()}:
            print(f"purge: no capture {capture_id} in {store.path}")
            return _REFUSED
        diagnostics = len(store.diagnostics(capture_id))
        observations = store.purge(capture_id)
    finally:
        store.close()
    print(
        f"purged {capture_id}: {observations} observations, {diagnostics} diagnostics"
    )
    return 0


def _configured(key: str, override: int | None, default: int) -> int:
    """The flag, then config.json's key, then the default. Never a guess.

    A configured value that is not a whole number stops the command rather than falling
    back to the default. Falling back would use a number the owner did not choose, and
    it would look right: a snippet naming the wrong port, or a capture recorded at a
    content level nobody asked for.
    """
    if override is not None:
        return override
    try:
        value = config.load().get(key)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    if value is None:
        return default
    try:
        return int(str(value))
    except ValueError:
        raise SystemExit(
            f"{config.home() / 'config.json'}: {key} is {value!r}, not a whole number"
        ) from None


def _daemon_port(override: int | None) -> int:
    return _configured("daemon_port", override, config.DEFAULT_DAEMON_PORT)


def _level(override: int | None) -> int:
    """The content level of a capture. Design 6.4 knows three, and no more."""
    level = _configured("content_level", override, _DEFAULT_LEVEL)
    if level not in (0, 1, 2):
        raise SystemExit(f"content level {level} is not 0, 1 or 2")
    return level


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
    _add_run(subcommands)
    watch = subcommands.add_parser(
        "daemon", help="serve the capture receiver in the foreground on a fixed port"
    )
    watch.add_argument("--port", type=int, default=None, help="default: 47311")
    watch.add_argument("--level", type=int, default=None, choices=(0, 1, 2))
    experiment = subcommands.add_parser(
        "experiment", help="run an experiment from a spec (design 6.12)"
    )
    kinds = experiment.add_subparsers(dest="kind")
    repeating = kinds.add_parser(
        "repeat", help="N captures of one task under one environment"
    )
    repeating.add_argument("spec", metavar="spec.json")
    repeating.add_argument(
        "--out",
        default=None,
        metavar="DIR",
        help="also write DIR/<task_id>/report.json (default: print only)",
    )
    removal = subcommands.add_parser("purge", help="delete one capture from this disk")
    removal.add_argument("capture_id", metavar="CAPTURE_ID")
    listing = subcommands.add_parser("sessions", help="list the captures on this disk")
    listing.add_argument("--repo", default=None, metavar="ID", help="one repo_id only")
    listing.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    listing.add_argument(
        "--link-commits",
        action="store_true",
        help="link commits for captures in the CURRENT repository that have none",
    )
    return parser


def _add_run(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """`telltale run [options] -- <argv...>`.

    Everything after `--` is the child's, untouched: argparse stops reading flags at
    the first one, so `telltale run -- claude -p x --output-format stream-json` gives
    the child its own `--output-format` rather than refusing it here.
    """
    runner = subcommands.add_parser(
        "run", help="run a command and record it (argv after --)"
    )
    runner.add_argument(
        "--provider",
        default="auto",
        choices=("auto", "claude", "codex", "generic"),
        help="auto reads the command's own name (default)",
    )
    runner.add_argument("--level", type=int, default=None, choices=(0, 1, 2))
    runner.add_argument("--task-id", default=None, metavar="ID")
    runner.add_argument("--attempt", type=int, default=None, metavar="N")
    runner.add_argument("--experiment", default=None, metavar="E")
    runner.add_argument(
        "--commit",
        action="append",
        default=[],
        metavar="SHA",
        help="a commit this run produced; may be repeated (spec 12.3, explicit)",
    )
    runner.add_argument("argv", nargs="*", help="the command to run, after --")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `telltale` console script; returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor(_daemon_port(args.port))
    if args.command == "setup":
        return setup(args.provider, args.apply, _daemon_port(args.port), args.level)
    if args.command == "run":
        args.level = _level(args.level)
        return launch.run(args)
    if args.command == "daemon":
        return daemon(_daemon_port(args.port), _level(args.level))
    if args.command == "sessions":
        return sessions(args.repo, args.limit, args.link_commits)
    if args.command == "experiment" and args.kind == "repeat":
        return experiment_repeat(args.spec, args.out)
    if args.command == "purge":
        return purge(args.capture_id)
    parser.print_help()
    return 0
