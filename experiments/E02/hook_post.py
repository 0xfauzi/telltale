"""One Codex hook command: read the hook JSON on stdin, POST it to the throwaway sink.

Codex 0.150.1 has no `http` hook type (Claude Code does), so reaching a receiver from a
Codex hook needs a `command` hook that does the POST itself. This is that command,
and it is also the measurement instrument for the fail-open question: it records what
it was given and what happened to the delivery attempt in a LOCAL file first, so a
scenario whose sink is down still proves the hook fired.

Contract with the agent it is recording, which is the whole point of the experiment:
  - stdout stays empty. Codex parses a non-empty stdout as a hook decision document and
    says "hook returned invalid ... JSON output" if it is not one.
  - the exit code is always 0. Exit 2 is Codex's blocking signal on several events.
  - every exception is caught and written to the local log. A recorder that can
    change the recorded process is not a recorder.

Usage (from .codex/hooks.json): python3 hook_post.py <port> <local-log-path>
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Bounded so a hung sink cannot stall the agent for longer than the delay it is
# measuring.
# A refused connection returns far faster than this; the timeout only binds a sink that
# accepts the connection and then goes quiet.
POST_TIMEOUT_SECONDS = 5.0


def main(argv: list[str]) -> int:
    started = time.time()
    port = argv[1] if len(argv) > 1 else "0"
    log_path = Path(argv[2]) if len(argv) > 2 else Path("hooks_local.jsonl")

    raw = ""
    try:
        raw = sys.stdin.read()
    except OSError as exc:  # stdin closed by the parent
        raw = f"<stdin unreadable: {exc}>"

    record: dict[str, object] = {"hook_received_ts": started, "raw_len": len(raw)}
    try:
        record["payload"] = json.loads(raw)
    except json.JSONDecodeError:
        record["payload_text"] = raw

    body = json.dumps(record, ensure_ascii=False).encode("utf-8")
    delivery: dict[str, object] = {"port": port}
    attempt_started = time.time()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/hooks/codex",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=POST_TIMEOUT_SECONDS) as response:
            delivery["status"] = response.status
    except (urllib.error.URLError, OSError, ValueError) as exc:
        delivery["error"] = f"{type(exc).__name__}: {exc}"
    delivery["elapsed_s"] = round(time.time() - attempt_started, 6)

    record["delivery"] = delivery
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # One write of one line: O_APPEND makes concurrent hook processes interleave
        # whole lines rather than fragments, which a lock across processes could not.
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # the child's behaviour must not change because our own log failed

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
