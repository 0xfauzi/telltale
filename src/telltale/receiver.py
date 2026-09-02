"""The loopback receiver: every surface arrives here, and nothing here can fail loudly.

Design 6.6. A ThreadingHTTPServer bound to 127.0.0.1 with seven POST routes and one GET.
Each POST reads a body, decides which capture it belongs to, asks a provider module to
parse it, and hands the observations to the store. Then it answers 200 with `{}`,
whatever happened, because the client is the agent being recorded: an OTLP exporter
retries a 5xx and an http hook waits for its answer inside a 5 second timeout, so an
error returned here changes the behaviour of the process under observation. AGENTS.md
invariant 8. `/healthz` is the one endpoint that tells the truth.

Attribution is the other half of the job, and it is where a recorder silently lies if it
guesses. A record belongs to the capture named by, in order: the `capture` query
parameter, the telltale.capture_id resource attribute the launcher set, or the provider
session id looked up in the map. If none of those answer, what happens next depends on
what this receiver is serving, and there are three cases rather than one:

  `telltale run` serves ONE capture, so it passes `default_capture` and every record
  that reaches this server belongs to it. There is nothing to guess.

  `telltale daemon` serves the sessions an owner starts by hand, and no launcher named
  any of them. It passes `derive_captures`, and an unknown provider session id becomes
  its own capture, `cap_<sha256(session id)[:24]>`: derived from the id, so the same
  session always lands in the same capture, including after a restart.

  Anything else (a replay, `doctor`) stores the record under `unattributed` with a
  `launcher` diagnostic. Dropping it would turn a wiring mistake into a quiet session.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import sys
import threading
import time
from collections import Counter
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlsplit

from telltale import providers
from telltale.model import Observation, now_iso, ulid
from telltale.sanitize import Ctx, sanitize
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# The capture id an observation gets when nothing in the request says which capture it
# belongs to. A real id, so the rows are queryable, and one nobody can mistake for one.
UNATTRIBUTED = "unattributed"

# How often the serving thread looks for a shutdown request. socketserver's default is
# 0.5 s and `shutdown()` waits for the next look: measured on this machine, that made
# `Receiver.stop()` cost 391 ms of the 659 ms a whole `telltale run -- true` took, which
# is latency the owner pays on every capture for nothing. At 0.05 s the same stop costs
# under 50 ms, and the cost of the change is one select() wakeup 20 times a second.
SERVE_POLL_S = 0.05

# A body larger than this is refused rather than read. 16 MB is about 16 times the
# largest OTLP batch E01 recorded (61 KB); it exists so one request cannot take the
# memory of the machine the agent is working on, not because anything approached it.
MAX_BODY_BYTES = 16 * 1024 * 1024

# Routes. Design 6.6 fixes them; the two prefixes take the provider name from the path.
_OTLP_SURFACES = {"/v1/logs": "otel_logs", "/v1/metrics": "otel_metrics"}
_EXTERNAL_TYPES = {
    "/v1/correlations": "external.correlation",
    "/v1/outcomes": "external.outcome",
    "/v1/policy_interventions": "policy.intervention",
}
_HOOKS_PREFIX = "/hooks/"
_STREAM_PREFIX = "/v1/stream/"

_EXTERNAL_ADAPTER = "external@1"

# The prefix of a capture id derived from a provider session id, and how much of the
# hash is kept. 24 hex characters is 96 bits, which is not a collision anyone will meet
# and is short enough to type; the prefix keeps it apart from a launched `cap_<ULID>`
# only by its alphabet, so nothing downstream may parse a capture id.
_DERIVED_PREFIX = "cap_"
_DERIVED_CHARS = 24


def derived_capture_id(provider_session_id: str) -> str:
    """The capture a session with no launcher belongs to. Design 6.6, daemon mode.

    A function of the session id alone, so a session that spans two daemon runs keeps
    one capture id, and two daemons watching one machine agree without talking.
    """
    digest = hashlib.sha256(provider_session_id.encode("utf-8")).hexdigest()
    return f"{_DERIVED_PREFIX}{digest[:_DERIVED_CHARS]}"


class Receiver:
    """One HTTP server, one store, and the map from session ids to capture ids."""

    def __init__(
        self,
        store: Store,
        level: int = 1,
        ctx_for_capture: Callable[[str], Ctx] | None = None,
        port: int = 0,
        provider: str = "claude",
        default_capture: str | None = None,
        derive_captures: bool = False,
    ) -> None:
        self.store = store
        self.level = level
        self.default_provider = provider
        # The two attribution modes, and they are exclusive by construction: a receiver
        # serving one capture has no unattributable record to derive an id for.
        self.default_capture = default_capture
        self.derive_captures = derive_captures and default_capture is None
        self._ctx_for = ctx_for_capture or (lambda _capture: Ctx())
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._sessions: dict[str, str] = {}
        self._capture_provider: dict[str, str] = {}
        self._capture_ids: dict[str, tuple[str | None, str | None]] = {}
        self._seen: set[str] = set()
        self._received: Counter[str] = Counter()
        self._mutation: Callable[[Observation], None] | None = None
        self._new_capture: Callable[[str], None] | None = None

    def start(self) -> int:
        """Bind, serve in a daemon thread, and return the port that was bound."""
        if self._server is not None:
            raise RuntimeError("receiver is already started")
        server = _Server(("127.0.0.1", self._port), _Handler)
        server.receiver = self
        self._server = server
        self._thread = threading.Thread(
            target=partial(server.serve_forever, poll_interval=SERVE_POLL_S),
            name="telltale-receiver",
            daemon=True,
        )
        self._thread.start()
        return int(server.server_address[1])

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server, self._thread = None, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5.0)

    def register_session(
        self, provider_session_id: str, capture_id: str, provider: str | None = None
    ) -> None:
        """Bind a provider session id to a capture, so hooks can be attributed.

        The launcher calls this before the child starts, with the id it passed as
        `--session-id`. The receiver also learns bindings on its own from any record
        that carries both, which is what makes hooks attributable when the launcher
        could not name the session in advance.
        """
        with self._lock:
            self._sessions[provider_session_id] = capture_id
            if provider:
                self._capture_provider[capture_id] = provider

    def bind_capture(
        self,
        capture_id: str,
        repo_id: str | None = None,
        environment_fingerprint_id: str | None = None,
    ) -> None:
        """Give a capture the two ids the launcher observed, for every later record.

        Design 6.2 puts repo_id and environment_fingerprint_id on every observation,
        and only the launcher can know them: the repository is on the launcher's disk
        and the fingerprint is of the process it is about to start. A capture nobody
        bound keeps None in both columns, which is what a daemon capture really is.
        """
        with self._lock:
            self._capture_ids[capture_id] = (repo_id, environment_fingerprint_id)

    def on_new_capture(self, callback: Callable[[str], None]) -> None:
        """Called once with each capture id this receiver attributes a record to.

        On a request thread, like on_file_mutation, and for the same reason: the
        daemon's one line per capture is printed while an agent's hook is waiting.
        """
        self._new_capture = callback

    def on_file_mutation(self, callback: Callable[[Observation], None]) -> None:
        """Called when an observation names a tool that changes a file. Design 6.6.

        Called, not awaited: the callback runs on the request thread that is holding an
        agent's hook open, so it schedules work and returns. An exception from it is
        swallowed into a diagnostic, because a repository snapshot failing is not a
        reason for a hook to fail.
        """
        self._mutation = callback

    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._received)

    def health(self) -> tuple[bool, dict[str, Any]]:
        """(ok, body) for /healthz. False means 503: the store is not writing."""
        store = self.store.health()
        ok = store["writer"] == "alive" and not store["down"]
        body = {
            "ok": ok,
            "store": store,
            "received": self.counts(),
            "sessions_known": len(self._sessions),
        }
        return ok, body

    # -- the request path ------------------------------------------------------------

    def handle(self, path: str, body: bytes) -> None:
        """One POST, from bytes to stored observations. Raises only for the handler."""
        route = urlsplit(path)
        query = parse_qs(route.query)
        raw = json.loads(body) if body.strip() else None
        if raw is None:
            raise ValueError("empty request body")
        obs_type = _EXTERNAL_TYPES.get(route.path)
        if obs_type is not None:
            self._store_external(obs_type, raw, query)
            return
        surface, provider_name = self._route(route.path, raw)
        module = providers.get(provider_name)
        capture, _session = self._attribute(module, surface, raw, query)
        self._deliver(surface, capture, module.parse(surface, raw, self._ctx(capture)))

    def _ctx(self, capture: str) -> providers.ParseCtx:
        with self._lock:
            repo_id, fingerprint = self._capture_ids.get(capture, (None, None))
        return providers.ParseCtx(
            capture, self.level, self._ctx_for(capture), repo_id, fingerprint
        )

    def _route(self, path: str, raw: Any) -> tuple[str, str]:
        """(surface, provider) for a path, or ValueError for one we do not serve."""
        surface = _OTLP_SURFACES.get(path)
        if surface is not None:
            return surface, self._otlp_provider(raw)
        if path.startswith(_HOOKS_PREFIX):
            return "hook", path[len(_HOOKS_PREFIX) :]
        if path.startswith(_STREAM_PREFIX):
            return "stream", path[len(_STREAM_PREFIX) :]
        raise ValueError(f"no route for {path!r}")

    def _otlp_provider(self, raw: Any) -> str:
        """Which provider sent an OTLP batch: `service.name` says, or the default.

        /v1/logs and /v1/metrics are one endpoint for every provider, so the name has to
        come out of the body. Codex and Claude both write service.name.
        """
        for key in ("resourceLogs", "resourceMetrics"):
            for block in _blocks(raw, key):
                resource = block.get("resource")
                attrs = providers.otlp_attrs(
                    resource.get("attributes") if isinstance(resource, dict) else None
                )
                name = providers.SERVICE_NAMES.get(str(attrs.get("service.name")))
                if name:
                    return name
        return self.default_provider

    def _attribute(
        self,
        module: providers.Provider,
        surface: str,
        raw: Any,
        query: dict[str, list[str]],
    ) -> tuple[str, str | None]:
        """Which capture this record belongs to, and the session id it named."""
        explicit = (query.get("capture") or [""])[0]
        from_body, session = module.hints(surface, raw)
        capture = explicit or from_body
        if capture is None and session is not None:
            with self._lock:
                capture = self._sessions.get(session)
        if capture is None:
            capture = self._fallback(surface, session)
        self._learn(session, capture)
        self._announce(capture)
        return capture, session

    def _fallback(self, surface: str, session: str | None) -> str:
        """The capture id for a record nothing in the request could attribute."""
        if self.default_capture is not None:
            return self.default_capture
        if self.derive_captures and session is not None:
            return derived_capture_id(session)
        # Kind launcher, not parse_failure: the record parsed, and all three ways a
        # capture id can arrive are things the launcher sets (AGENTS.md invariant 8),
        # so an unattributable record is a fact about the launched process's wiring.
        # parse_failure stays for design 6.6's "any exception".
        self.store.diagnose(
            "launcher",
            f"unattributed {surface} record, session "
            f"{session or 'absent'}: no capture query, no attribute, no binding",
            capture_id=UNATTRIBUTED,
        )
        return UNATTRIBUTED

    def _announce(self, capture: str) -> None:
        callback = self._new_capture
        with self._lock:
            first = capture not in self._seen
            self._seen.add(capture)
        if callback is None or not first:
            return
        try:
            callback(capture)
        except Exception as error:
            self.store.diagnose(
                "launcher", f"new capture callback: {error!r}", capture_id=capture
            )

    def _learn(self, session: str | None, capture: str) -> None:
        """Remember session -> capture, and say so when the answer changes.

        A session bound to two captures is the resume case (E01 S5 resumes S2's
        session), and it is a conflict rather than an update to make quietly: the newer
        binding wins, and the diagnostic keeps the older one visible.
        """
        if session is None or capture == UNATTRIBUTED:
            return
        with self._lock:
            previous = self._sessions.get(session)
            self._sessions[session] = capture
        if previous is not None and previous != capture:
            self.store.diagnose(
                "conflict",
                f"session {session} was bound to capture {previous}, now {capture}",
                capture_id=capture,
            )

    def _deliver(
        self, surface: str, capture: str, observations: Sequence[Observation]
    ) -> None:
        with self._lock:
            self._received[surface] += 1
        if not observations:
            return
        self.store.append(list(observations))
        self._report_unknown(capture, observations)
        for observation in observations:
            self._maybe_mutation(observation, capture)

    def _report_unknown(
        self, capture: str, observations: Sequence[Observation]
    ) -> None:
        """One diagnostic per request naming every field no allowlist entry knows.

        One row per request rather than per observation: a batch of 85 log records with
        the same five unknown attributes is one fact about the parser, and 85 rows of it
        would bury the next one.
        """
        unknown = {
            f"{observation.observation_type}.{entry.removesuffix(':unknown')}"
            for observation in observations
            for entry in observation.redaction.get("dropped", ())
            if entry.endswith(":unknown")
        }
        if unknown:
            self.store.diagnose(
                "unknown_field", " ".join(sorted(unknown)), capture_id=capture
            )

    def _maybe_mutation(self, observation: Observation, capture: str) -> None:
        callback = self._mutation
        if callback is None or not _names_mutation(observation):
            return
        try:
            callback(observation)
        except Exception as error:
            # A snapshot that fails is a diagnostic. It is never a failed hook.
            self.store.diagnose(
                "launcher", f"file mutation callback: {error!r}", capture_id=capture
            )

    def _store_external(
        self, obs_type: str, raw: Any, query: dict[str, list[str]]
    ) -> None:
        """The three external API routes. No provider module owns these bodies."""
        if not isinstance(raw, dict):
            raise ValueError(f"{obs_type} body is not a JSON object")
        capture = (query.get("capture") or [""])[0] or str(raw.get("capture_id") or "")
        session = str(raw.get("provider_session_id") or "") or None
        if not capture and session:
            with self._lock:
                capture = self._sessions.get(session, "")
        if not capture:
            capture = self._fallback(obs_type, session)
        self._announce(capture)
        payload = {key: value for key, value in raw.items() if key != "capture_id"}
        body, redaction, _unknown = sanitize(
            obs_type, payload, self.level, self._ctx_for(capture)
        )
        observation = Observation(
            observation_id=ulid(),
            capture_id=capture,
            observation_type=obs_type,
            surface="external",
            provider="external",
            adapter=_EXTERNAL_ADAPTER,
            ingest_ts=now_iso(),
            provider_session_id=session,
            payload=body,
            redaction=redaction,
        )
        self._deliver("external", capture, [observation])


def _names_mutation(observation: Observation) -> bool:
    """True when this observation is an agent changing a file. Design 6.6."""
    if observation.provider == "claude":
        from telltale.providers.claude import names_file_mutation

        return names_file_mutation(observation)
    return str(observation.payload.get("item_type", "")) == "file_change"


def _blocks(raw: Any, key: str) -> list[dict[str, Any]]:
    inner = raw.get(key) if isinstance(raw, dict) else None
    inner = inner if isinstance(inner, list) else []
    return [item for item in inner if isinstance(item, dict)]


class _Server(ThreadingHTTPServer):
    """The server, and the promise that it never writes to the terminal.

    ThreadingHTTPServer's default handle_error prints a traceback to stderr, and stderr
    belongs to the agent being recorded: E01 measured that one line on stderr is the
    whole visible cost of a receiver that is not there, and a traceback per dropped
    connection would be worse than the failure it reports. A transport failure has no
    body to parse and no capture to attribute, so it is recorded as a launcher
    diagnostic, which is design 6.5's kind for Telltale's own plumbing.
    """

    daemon_threads = True
    receiver: Receiver

    def handle_error(self, _request: Any, _client_address: Any) -> None:
        # The two arguments are socketserver's signature, which calls this
        # positionally; the error itself is on the stack, not in an argument.
        error = sys.exc_info()[1]
        with contextlib.suppress(Exception):
            self.receiver.store.diagnose(
                "launcher", f"receiver connection: {type(error).__name__}: {error}"
            )


class _Handler(BaseHTTPRequestHandler):
    """One request. Every path through this class ends in a response."""

    protocol_version = "HTTP/1.1"
    server_version = "telltale"
    sys_version = ""

    @property
    def receiver(self) -> Receiver:
        """The Receiver this server belongs to, attached by Receiver.start()."""
        return cast("_Server", self.server).receiver

    def do_GET(self) -> None:
        if urlsplit(self.path).path != "/healthz":
            self._respond(404, {"error": "not found"})
            return
        ok, body = self.receiver.health()
        self._respond(200 if ok else 503, body)

    def do_POST(self) -> None:
        try:
            body = self._read()
            self.receiver.handle(self.path, body)
        except Exception as error:
            # Design 6.6 and spec 5.2: the client is the process being recorded, so
            # every failure is a diagnostic here and a 200 there.
            self._diagnose(error)
        self._respond(200, {})

    def _read(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise ValueError(f"body of {length} bytes is over the {MAX_BODY_BYTES} cap")
        body = self.rfile.read(length) if length > 0 else b""
        if "gzip" in (self.headers.get("Content-Encoding") or ""):
            body = gzip.decompress(body)
        return body

    def _diagnose(self, error: Exception) -> None:
        detail = f"{urlsplit(self.path).path}: {type(error).__name__}: {error}"
        # A store that cannot even take a diagnostic has nowhere else to say so, and
        # raising here would turn a bad body into a failed hook. health() still tells.
        with contextlib.suppress(Exception):
            self.receiver.store.diagnose("parse_failure", detail)

    def _respond(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *args: Any) -> None:
        """Silence. stderr belongs to the agent being recorded, not to the recorder."""


# -- replay ---------------------------------------------------------------------------


def _post(port: int, path: str, body: bytes) -> int:
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        headers = {"Content-Type": "application/json", "Connection": "close"}
        connection.request("POST", path, body=body, headers=headers)
        return connection.getresponse().status
    finally:
        connection.close()


def _get(port: int, path: str) -> dict[str, Any]:
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return dict(json.loads(response.read()))
    finally:
        connection.close()


def _replay_records(directory: Path) -> list[tuple[str, bytes]]:
    """Every sink record in ingest order, then every stream line in file order.

    The sink files hold one JSON record per line: ingest_ts, path, headers, body_json.
    They are posted to the path they were recorded on, so the replay exercises the same
    routing a live capture does. parse() is never called from here on purpose.
    """
    sink: list[tuple[float, str, bytes]] = []
    for name in ("otel_logs.jsonl", "otel_metrics.jsonl", "hooks.jsonl"):
        sink += _sink_records(directory / name)
    out = [(path, body) for _ts, path, body in sorted(sink, key=lambda row: row[0])]
    out += [
        ("/v1/stream/claude", line.encode("utf-8"))
        for line in _lines(directory / "stream.jsonl")
    ]
    return out


def _sink_records(path: Path) -> list[tuple[float, str, bytes]]:
    out = []
    for line in _lines(path):
        record = json.loads(line)
        body = json.dumps(record["body_json"]).encode("utf-8")
        out.append((float(record.get("ingest_ts") or 0), str(record["path"]), body))
    return out


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip()]


def _drain(port: int, timeout_s: float = 30.0) -> dict[str, Any]:
    """Wait for the writer to empty its queue, then answer with the health body."""
    deadline = time.monotonic() + timeout_s
    body = _get(port, "/healthz")
    while time.monotonic() < deadline and body.get("store", {}).get("queue_depth"):
        time.sleep(0.05)
        body = _get(port, "/healthz")
    return body


def _replay(args: argparse.Namespace) -> int:
    write = sys.stdout.write  # ruff T20: print() lives in cli.py and report.py only
    directory = Path(args.replay)
    store = Store(args.db).open()
    receiver = Receiver(store, level=args.level)
    port = receiver.start()
    if args.capture and args.session:
        receiver.register_session(args.session, args.capture, "claude")
    statuses: Counter[int] = Counter()
    for path, body in _replay_records(directory):
        statuses[
            _post(
                port,
                path if not args.capture else _with_capture(path, args.capture),
                body,
            )
        ] += 1
    # store.flush(), not _drain(): an empty queue is not a write barrier, and _report
    # below reads the database back through its own connections. W0-T5 measured a
    # drain returning with queue_depth 0 while the batch was still in flight, which
    # here would print a count of the rows that happened to be committed in time.
    store.flush()
    health = _get(port, "/healthz")
    receiver.stop()
    _report(write, store, receiver, statuses, health)
    store.close()
    return 0


def _with_capture(path: str, capture: str) -> str:
    joiner = "&" if "?" in path else "?"
    return f"{path}{joiner}capture={capture}"


def _report(
    write: Callable[[str], int],
    store: Store,
    receiver: Receiver,
    statuses: Counter[int],
    health: dict[str, Any],
) -> None:
    types: Counter[str] = Counter()
    surfaces: Counter[str] = Counter()
    captures = [str(row["capture_id"]) for row in store.captures()]
    for capture in captures:
        for row in store.observations(capture):
            types[str(row["observation_type"])] += 1
            surfaces[str(row["surface"])] += 1
    kinds: Counter[str] = Counter(str(row["kind"]) for row in store.diagnostics())
    write(f"captures {captures}\n")
    write(f"responses {dict(statuses)}\n")
    write(f"requests per surface {receiver.counts()}\n")
    write("observations per type\n")
    for name, number in sorted(types.items()):
        write(f"  {number:5d}  {name}\n")
    write(f"observations per surface {dict(surfaces)}\n")
    write(f"observations total {sum(types.values())}\n")
    write(f"diagnostics by kind {dict(kinds)}\n")
    write(f"health {json.dumps(health['store'], sort_keys=True)}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m telltale.receiver")
    parser.add_argument(
        "--replay", metavar="DIR", required=True, help="a fixture directory"
    )
    parser.add_argument(
        "--db", metavar="DB", required=True, help="the database to write"
    )
    parser.add_argument("--level", type=int, default=1, choices=(0, 1, 2))
    parser.add_argument(
        "--capture",
        metavar="ID",
        default="",
        help="attribute every record to this capture",
    )
    parser.add_argument(
        "--session",
        metavar="ID",
        default="",
        help="bind this provider session id to --capture",
    )
    return _replay(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
