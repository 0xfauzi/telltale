"""The load arm's child: name the receiver, wait, run the scripted agent, wait again.

Why a wrapper rather than `bash -c`. The launcher decides what to configure by SCANNING
the child's argv (`src/telltale/providers/claude_launch.py`): it tees stdout only when
it sees `--output-format` and `stream-json` as separate tokens, and it inserts
`--session-id` only when it sees `-p`. Flags hidden inside a `bash -c` string are not
tokens of the child's argv, so a `bash -c` child configures no stream surface at all and
the load arm would have nothing to compare. Measured, not reasoned: see the
`E04-argv` capture in experiments/E04/out/load/results.json.

The two waits are what make the load arm a controlled comparison. The scripted agent
finishes in well under a second, and the load runs for 20 s, so without a wait the
capture would be over before the flood started. The wait before lets the flood fill the
queue, the agent then runs while it is full, and the wait after keeps the receiver
alive to the end of the flood. Both captures wait the same, so the only difference
between them is whether the load generator ran.

Everything it needs comes from the environment, because argv belongs to the agent: the
launcher appends `--settings` and `--session-id` to it and this wrapper passes it
through untouched.

    E04_ENDPOINT_FILE   where OTEL_EXPORTER_OTLP_ENDPOINT is written (endpoint.txt)
    E04_HOLD_BEFORE_S   seconds to wait before the agent runs (default 0)
    E04_HOLD_AFTER_S    seconds to wait after it exits (default 0)
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

FAKE_AGENT = (
    Path(__file__).resolve().parents[2] / "tests" / "integration" / "fake_agent.py"
)


def main() -> int:
    name = os.environ.get("E04_ENDPOINT_FILE", "endpoint.txt")
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    Path(name).write_text(endpoint + "\n", encoding="utf-8")
    time.sleep(float(os.environ.get("E04_HOLD_BEFORE_S", "0")))
    # stdout is inherited, so the agent's lines reach the launcher's tee unchanged.
    done = subprocess.run([sys.executable, str(FAKE_AGENT), *sys.argv[1:]], check=False)
    time.sleep(float(os.environ.get("E04_HOLD_AFTER_S", "0")))
    return done.returncode


if __name__ == "__main__":
    sys.exit(main())
