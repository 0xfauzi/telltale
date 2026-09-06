"""Every E02 Codex fixture through the real receiver: requests, dropped, conflicts.

W7-T1. The brief measured S1, S3 and S6; this runs all seven so that a token_count that
pairs with nothing is found rather than assumed absent. Temporary stores only.
"""

from __future__ import annotations

import json
import pathlib
import tempfile

from telltale import measures  # noqa: F401  - registers the reducers
from telltale.receiver import Receiver, _drain, _post, _replay_records, _with_capture
from telltale.sanitize import Ctx
from telltale.store import Store

ROOT = pathlib.Path("fixtures/sources/codex/0.150.1")
FILES = (
    "otel_logs.jsonl",
    "otel_metrics.jsonl",
    "hooks.jsonl",
    "exec.jsonl",
    "rollout.jsonl",
)
SCENARIOS = ("S1", "S2", "S3", "S4", "S5", "S6", "S7")


def stage(
    scen: str, base: pathlib.Path
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    repo, home, staged = base / "repo", base / "home", base / "in"
    for path in (repo, home, staged):
        path.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        src = ROOT / scen / name
        if src.exists():
            text = src.read_text(encoding="utf-8")
            text = text.replace("<repo>", str(repo)).replace("<home>", str(home))
            (staged / name).write_text(text, encoding="utf-8")
    return repo, home, staged


def run(scen: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        repo, home, staged = stage(scen, base)
        store = Store(base / "telltale.db").open()
        live = Receiver(
            store,
            level=1,
            ctx_for_capture=lambda _c: Ctx(repo_root=repo, home=home),
            provider="codex",
        )
        port = live.start()
        for route, body in _replay_records(staged):
            _post(port, _with_capture(route, scen), body)
        _drain(port)
        live.stop()
        store.rebuild(scen)
        requests = [
            row
            for row in store.activities(scen)
            if row["activity_type"] == "model_request"
        ]
        sources = {dict(row["fields"])["usage_source"] for row in requests}
        rows = [
            (str(row["kind"]), json.loads(str(row["detail"])))
            for row in store.diagnostics(scen)
            if row["kind"] in ("dropped", "conflict")
            and str(row["detail"]).startswith("{")
        ]
        mine = [
            (kind, body)
            for kind, body in rows
            if str(body.get("metric", "")).startswith("model_request")
        ]
        print(f"{scen}: {len(requests)} requests from {sorted(sources)}")
        for kind, body in mine:
            print(f"   {kind}: {json.dumps(body)[:300]}")
        store.close()


for name in SCENARIOS:
    run(name)
