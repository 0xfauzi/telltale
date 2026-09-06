"""E13 census: which captures on the whole store are ready for request-clock backtests?

E07 (wave 2) assessed the build's own launcher captures and nothing else, because its
cohort rule refused imported transcripts and rollouts. The owner's day-to-day sessions
have since been imported (1,645 Claude and 1,454 Codex captures at wave 2, 142 and 5
more on 2026-09-05), so the question this census answers before any model runs is: how
many (capture, target, horizon) triples pass design 6.12's readiness checklist, by
cohort, and how many windows they carry. No forecaster runs here; the count decides
whether a model run is worth its compute and at what scale.

Reuses E07's copy, census, series build and readiness functions unchanged, so the
numbers are comparable with E07's; only the cohort rule is different, and it is
different by being absent: every capture with at least c_min model requests is built,
and the cohort keys (provider, surface, content level) are RECORDED per row so a later
run can pool inside them and never across them.
"""

from __future__ import annotations

import collections
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "E07"))
import run as e07  # noqa: E402

from telltale import series as compiler  # noqa: E402
from telltale.forecast import TARGETS  # noqa: E402
from telltale.store import Store  # noqa: E402

OUT = HERE / "out"
C_MIN = min(TARGETS[target].c_min for target in TARGETS)


def cohort_of(row: dict[str, Any]) -> dict[str, Any]:
    """provider, surface and content level of one capture.

    Imported captures carry a `telltale.capture_started` row too (the importer writes
    one, surface `import`), so `launched` does not separate them from launcher
    captures; the capture id prefix does: `imp_` is the importer's, `cap_` the
    launcher's, `adv_` and `pol_` are Telltale's own.
    """
    prefix = row["capture_id"][:4]
    surface = {"imp_": "import", "cap_": "launcher"}.get(prefix, prefix.rstrip("_"))
    return {
        "provider": row["provider"],
        "surface": surface,
        "content_level": row["content_level"],
    }


def request_pairs() -> list[tuple[str, int]]:
    """E07's pairs, restricted to the request clock: the registry has grown change-clock
    targets since E07 ran, and readiness refuses those on a request series."""
    return [(t, h) for t, h in e07.pairs() if TARGETS[t].clock == "request"]


def main() -> int:
    started = time.perf_counter()
    e07.OUT, e07.HOME = OUT, OUT / "home"
    home = e07.point_home_at_the_copy()
    copied = e07.copy_store(e07.SOURCE_DB, home / "telltale.db")
    store = Store(home / "telltale.db").open()
    rows_out: list[dict[str, Any]] = []
    by_cohort: dict[str, dict[str, Any]] = collections.defaultdict(
        lambda: {
            "captures": 0,
            "built": 0,
            "ready_triples": 0,
            "windows": 0,
            "unbuilt": 0,
        }
    )
    try:
        rows = e07.census(home / "telltale.db")
        short = [one for one in rows if one["requests"] < C_MIN]
        kept = [one for one in rows if one["requests"] >= C_MIN]
        for capture in kept:
            cohort = cohort_of(capture)
            key = (
                f"{cohort['provider']}/{cohort['surface']}"
                f"/level {cohort['content_level']}"
            )
            tally = by_cohort[key]
            tally["captures"] += 1
            try:
                built = e07.build_series(store, capture["capture_id"])
            except compiler.Refused as refused:
                tally["unbuilt"] += 1
                rows_out.append({**capture, **cohort, "unbuilt": str(refused)})
                continue
            tally["built"] += 1
            for target, horizon in request_pairs():
                found = e07.readiness_of(built, target, horizon)
                windows = next(
                    (c["measured"] for c in found["checks"] if "window" in c["check"]),
                    None,
                )
                if found["ready"]:
                    tally["ready_triples"] += 1
                    tally["windows"] += int(windows or 0)
                    pair = f"{target} H{horizon}"
                    tally.setdefault("by_pair", {}).setdefault(pair, 0)
                    tally["by_pair"][pair] += 1
                rows_out.append(
                    {
                        "capture": capture["capture_id"],
                        "task": capture["task_id"],
                        "requests": capture["requests"],
                        **cohort,
                        "target": target,
                        "h": horizon,
                        "ready": found["ready"],
                        "windows": windows,
                        "first_failure": found["first_failure"],
                    }
                )
    finally:
        store.close()
    summary = {
        "experiment": "E13-census",
        "copy": copied,
        "captures_on_disk": len(rows),
        "below_c_min": len(short),
        "c_min": C_MIN,
        "pairs": request_pairs(),
        "by_cohort": dict(by_cohort),
        "rows": rows_out,
        "wall_s": round(time.perf_counter() - started, 3),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "census.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(
        f"captures on disk {len(rows)}, below c_min {C_MIN}: {len(short)},"
        f" wall {summary['wall_s']} s"
    )
    print(
        f"{'COHORT':44} {'CAPTURES':>8} {'BUILT':>6} {'READY TRIPLES':>13}"
        f" {'WINDOWS':>8} {'UNBUILT':>7}"
    )
    for key, tally in sorted(by_cohort.items()):
        print(
            f"{key:44} {tally['captures']:>8} {tally['built']:>6}"
            f" {tally['ready_triples']:>13} {tally['windows']:>8} {tally['unbuilt']:>7}"
        )
        for pair, count in sorted(tally.get("by_pair", {}).items()):
            print(f"    {pair:40} ready captures {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
