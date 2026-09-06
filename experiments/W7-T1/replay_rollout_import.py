"""Post the rollout-import fixture through a real receiver into $TELLTALE_HOME.

W7-T1 VERIFY step 1. Nothing is stubbed: a real Receiver on a real port, the fixture's
own bytes on the route a rollout is delivered on, then the reducers.
"""

from __future__ import annotations

import pathlib
import sys

from telltale.receiver import Receiver, _drain, _post, _with_capture
from telltale.sanitize import Ctx
from telltale.store import Store

HOME = pathlib.Path(sys.argv[1])
CAPTURE = sys.argv[2]
FIXTURE = pathlib.Path(
    "fixtures/sources/codex/0.150.1/rollout-import/2026/09/02"
    "/rollout-2026-09-02T10-00-00-019adada-1111-7282-acf2-a7be9046cc69.jsonl"
)
ROUTE = "/v1/stream/codex?surface=rollout"

repo = HOME / "repo"
fake_home = HOME / "fakehome"
repo.mkdir(parents=True, exist_ok=True)
fake_home.mkdir(parents=True, exist_ok=True)

store = Store(HOME / "telltale.db").open()
live = Receiver(
    store,
    level=1,
    ctx_for_capture=lambda _c: Ctx(repo_root=repo, home=fake_home),
    provider="claude",
)
port = live.start()
text = FIXTURE.read_text(encoding="utf-8")
text = text.replace("<repo>", str(repo)).replace("<home>", str(fake_home))
lines = [line for line in text.splitlines() if line.strip()]
statuses = {
    _post(port, _with_capture(ROUTE, CAPTURE), line.encode("utf-8")) for line in lines
}
_drain(port)
live.stop()
from telltale import measures  # noqa: E402, F401  - registers the reducers

store.rebuild(CAPTURE)
store.close()
print(
    f"posted {len(lines)} rollout lines, statuses {sorted(statuses)}, capture {CAPTURE}"
)
