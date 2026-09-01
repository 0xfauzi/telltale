"""Throwaway HTTP sink for the capture-feasibility experiments (E01, E02).

Not the Telltale receiver. This one keeps EVERYTHING it is sent, byte for byte,
so the experiments can see what each provider surface really emits before any
allowlist exists. Its output must never be committed unsanitized: the
experiments' sanitize_fixture.py scripts rewrite it into fixtures/sources/.

One JSON line per request, appended to <out>/<surface>.jsonl, where the surface
is derived from the request path:
    /v1/logs           -> otel_logs
    /v1/metrics        -> otel_metrics
    /v1/traces         -> otel_traces
    /hooks/<provider>  -> hooks
    anything else      -> other
Each line: {"ingest_ts": <unix seconds float>, "path", "headers": {Content-Type,
Content-Encoding}, "body_json" | "body_text"}. ingest_ts is what the late-export
window is measured from, so it is taken before the body is parsed.

Always answers 200 with "{}" (hooks expect a JSON body; OTLP accepts any 2xx).
`--down` binds nothing and exits immediately after printing PORT=0: the
experiments use it to measure fail-open behaviour against a closed port.

Usage: python capture_receiver.py --out DIR [--port 0]
Prints "PORT=<n>" on stdout once bound, then serves until SIGTERM or Ctrl-C.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SURFACES = {
    "/v1/logs": "otel_logs",
    "/v1/metrics": "otel_metrics",
    "/v1/traces": "otel_traces",
}


def surface_for(path: str) -> str:
    bare = path.split("?", 1)[0]
    if bare in SURFACES:
        return SURFACES[bare]
    if bare.startswith("/hooks/"):
        return "hooks"
    return "other"


class Sink(BaseHTTPRequestHandler):
    out_dir: Path
    lock = threading.Lock()

    def do_POST(self) -> None:
        ingest_ts = time.time()
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
        record: dict[str, object] = {
            "ingest_ts": ingest_ts,
            "path": self.path,
            "headers": {
                "Content-Type": self.headers.get("Content-Type"),
                "Content-Encoding": self.headers.get("Content-Encoding"),
            },
        }
        try:
            record["body_json"] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            record["body_text"] = raw.decode("utf-8", errors="replace")
        line = json.dumps(record, ensure_ascii=False) + "\n"
        target = self.out_dir / f"{surface_for(self.path)}.jsonl"
        with self.lock, target.open("a", encoding="utf-8") as fh:
            fh.write(line)
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return  # stdout carries only the PORT line; request logs would corrupt it


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", required=True, help="directory for <surface>.jsonl")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--down", action="store_true", help="bind nothing; exit")
    args = parser.parse_args()
    if args.down:
        print("PORT=0", flush=True)
        return 0
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    Sink.out_dir = out_dir
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Sink)
    server.daemon_threads = True
    print(f"PORT={server.server_address[1]}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
