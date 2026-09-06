"""E14: does a transcript's own timestamps reproduce the request duration OTel states?

`request_duration_ms` is the one request-clock column with no value on the transcript
surface. Every imported Claude capture therefore carries it as `unavailable`, readiness
check 1 counts it as not forecastable, and E13 had to pre-register a narrower named
variant before it could score an import at all. A transcript does carry a clock: every
line has a `timestamp`, and the assistant lines of one request share a `requestId`. The
question is whether the gap between the line that precedes a request and the last line
the request wrote is the same number the OTel `api_request` record states.

This script answers it on the sessions that have BOTH surfaces: the build's own launcher
captures, whose OTel records are in the store and whose transcripts Claude Code wrote
under `~/.claude/projects` at the same time. Nothing here writes: the store is opened
`mode=ro` with `query_only`, and the transcripts are read line by line.

Three definitions are measured rather than one, because the choice between them is the
decision this experiment exists to make.

  `last_raw`   end at the LAST assistant line of the request; start at the nearest
               earlier line of any other type that carries a timestamp.
  `last_obs`   the same, but the start line must be one the transcript PARSER stores as
               an observation: a user line with a prompt or a tool_result, or a
               `system` line whose subtype is `compact_boundary`. This is the rule
               `activities._request` can actually implement, because a reducer sees
               observations and not lines.
  `first_raw`  end at the FIRST assistant line of the request, start as in `last_raw`.
               The obvious definition, and the one this run is here to reject or keep.

Decision rule, written before the run: adopt the last-line derivation if at least 90
percent of matched requests agree with the OTel duration within 10 percent relative.

Usage:
    uv run python experiments/E14/measure_transcript_duration.py
    uv run python experiments/E14/measure_transcript_duration.py --store PATH
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import time
from bisect import bisect_left
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
# E13's store copy, which is a `.backup` of the owner's store and is never written here.
STORE = Path("experiments/E13/out/home/telltale.db")
PROJECTS = Path.home() / ".claude" / "projects"
TOLERANCE = 0.10
DEFINITIONS = ("last_raw", "last_obs", "first_raw")

_REQUESTS = """
select capture_id, provider_session_id,
       json_extract(payload, '$.request_id'),
       json_extract(payload, '$.duration_ms')
from observations
where observation_type = 'claude.otel.api_request'
order by capture_id, observation_id
"""


def store_path(given: str | None) -> Path:
    """E13's copy, found here or in the checkout this worktree branched off.

    A git worktree has its own tree and shares one object store, so
    `experiments/E13/out` exists in the checkout the experiment was run in and nowhere
    else. The `.git` file of a worktree names that checkout, so the fallback is a
    lookup rather than a guess.
    """
    if given is not None:
        return Path(given)
    root = HERE.parent.parent
    candidates = [root / STORE]
    marker = root / ".git"
    if marker.is_file():
        named = marker.read_text(encoding="utf-8").split("gitdir:", 1)[-1].strip()
        candidates.append(Path(named).parent.parent.parent / STORE)
    for path in candidates:
        if path.exists():
            return path
    raise SystemExit(
        "no store found at "
        + ", ".join(str(path) for path in candidates)
        + ". Pass --store PATH."
    )


def otel_requests(db: Path) -> list[tuple[str, str, str, int]]:
    """Every api_request record that names a request id, a session and a duration."""
    reader = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        reader.execute("pragma query_only = 1")
        rows = reader.execute(_REQUESTS).fetchall()
    finally:
        reader.close()
    return [
        (str(capture), str(session), str(request), int(duration))
        for capture, session, request, duration in rows
        if session and request and isinstance(duration, int | float)
    ]


def stamp(value: Any) -> float | None:
    """One transcript timestamp as epoch milliseconds, or None when it is not one."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def stored_non_assistant(line: dict[str, Any]) -> bool:
    """Whether claude_transcript.py would store an observation for this line.

    Mirrors `_user` and `_system` in src/telltale/providers/claude_transcript.py: a user
    line becomes one observation per tool_result block plus one for its prompt length,
    and the only `system` subtype that becomes an observation is compact_boundary. Every
    other line kind becomes a counted note and no row, so a reducer cannot see it.
    """
    kind = line.get("type")
    if kind == "system":
        return line.get("subtype") == "compact_boundary"
    if kind != "user":
        return False
    content = (
        line.get("message", {}).get("content")
        if isinstance(line.get("message"), dict)
        else None
    )
    if isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") in ("tool_result", "text")
        for block in content
    )


class Transcript:
    """One session file, as the three lists this measurement reads it through."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.groups: dict[str, list[float]] = {}
        self.unreadable = 0
        raw: list[float] = []
        obs: list[float] = []
        for line in self._objects(path):
            self._read(line, raw, obs)
        self.raw = sorted(raw)
        self.obs = sorted(obs)

    def _objects(self, path: Path) -> Iterator[dict[str, Any]]:
        """Every line of the file that is a JSON object, counting the ones that are not.

        A transcript killed mid-write ends in a partial line, which the parser turns
        into one parse_failure row rather than refusing the file.
        """
        for text in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not text.strip():
                continue
            try:
                line = json.loads(text)
            except ValueError:
                line = None
            if isinstance(line, dict):
                yield line
            else:
                self.unreadable += 1

    def _read(self, line: dict[str, Any], raw: list[float], obs: list[float]) -> None:
        """One line into the group it belongs to, or into one of the two start lists."""
        when = stamp(line.get("timestamp"))
        if when is None:
            return
        if line.get("type") == "assistant":
            request = line.get("requestId")
            if isinstance(request, str):
                self.groups.setdefault(request, []).append(when)
            return
        raw.append(when)
        if stored_non_assistant(line):
            obs.append(when)

    def derived(self, request: str) -> dict[str, float | None]:
        """The three candidate durations for one request id, in milliseconds."""
        stamps = sorted(self.groups[request])
        return {
            "last_raw": _gap(self.raw, stamps[0], stamps[-1]),
            "last_obs": _gap(self.obs, stamps[0], stamps[-1]),
            "first_raw": _gap(self.raw, stamps[0], stamps[0]),
        }


def _gap(starts: list[float], first: float, end: float) -> float | None:
    """end minus the nearest start candidate strictly before the request, or None.

    None rather than a number when nothing precedes the request in the file (the first
    request of a transcript, whose start line is a kind the parser does not read) and
    when the gap is negative, which a file written out of timestamp order can produce.
    Absence is not zero: design invariant 5.
    """
    index = bisect_left(starts, first)
    if index == 0:
        return None
    span = end - starts[index - 1]
    return span if span >= 0 else None


def compare(store: Path, projects: Path) -> dict[str, Any]:
    """Every OTel request matched to its transcript, and the differences per rule."""
    records = otel_requests(store)
    files: dict[str, Transcript | None] = {}
    matched: list[dict[str, Any]] = []
    unmatched_session = 0
    unmatched_request = 0
    for _capture, session, request, duration in records:
        if session not in files:
            found = sorted(projects.glob(f"*/{session}.jsonl"))
            files[session] = Transcript(found[0]) if found else None
        transcript = files[session]
        if transcript is None:
            unmatched_session += 1
            continue
        if request not in transcript.groups:
            unmatched_request += 1
            continue
        matched.append({
            "session": session,
            "request": request,
            "otel_ms": duration,
            "lines": len(transcript.groups[request]),
            **transcript.derived(request),
        })  # fmt: skip
    return {
        "store": str(store),
        "projects": str(projects),
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "otel_records": len(records),
        "otel_sessions": len({session for _c, session, _r, _d in records}),
        "sessions_with_transcript": sum(one is not None for one in files.values()),
        "matched": len(matched),
        "unmatched_session": unmatched_session,
        "unmatched_request": unmatched_request,
        "assistant_lines_per_request": _histogram(matched),
        "rules": {name: _rule(matched, name) for name in DEFINITIONS},
        "records": matched,
    }


def _histogram(matched: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in matched:
        key = str(row["lines"])
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: int(item[0])))


def _rule(matched: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """One definition's agreement with OTel, over the requests it could compute."""
    pairs = [(row["otel_ms"], row[name]) for row in matched if row[name] is not None]
    absolute = [abs(derived - otel) for otel, derived in pairs]
    relative = [(derived - otel) / otel for otel, derived in pairs if otel > 0]
    within = [one for one in relative if abs(one) <= TOLERANCE]
    return {
        "n": len(pairs),
        "no_value": len(matched) - len(pairs),
        "median_otel_ms": _median([otel for otel, _d in pairs]),
        "median_derived_ms": _median([derived for _o, derived in pairs]),
        "median_abs_diff_ms": _median(absolute),
        "median_rel_diff": _median(relative),
        "p90_rel_diff": _quantile(relative, 0.90),
        "within_10pc": len(within),
        "share_within_10pc": (len(within) / len(relative)) if relative else None,
    }


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def _quantile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(share * len(ordered)))
    return round(ordered[index], 4)


def render(summary: dict[str, Any]) -> None:
    print(f"store    {summary['store']}")
    print(f"projects {summary['projects']}")
    print(
        f"otel api_request records {summary['otel_records']}"
        f"  sessions {summary['otel_sessions']}"
        f"  with a transcript file {summary['sessions_with_transcript']}"
    )
    print(
        f"matched to a requestId in the transcript {summary['matched']}"
        f"  (session not found {summary['unmatched_session']},"
        f" requestId not in the file {summary['unmatched_request']})"
    )
    print(f"assistant lines per request {summary['assistant_lines_per_request']}")
    header = (
        f"{'rule':10} {'n':>6} {'none':>5} {'med otel':>9} {'med derived':>12}"
        f" {'med |diff|':>11} {'med rel':>9} {'p90 rel':>9} {'within 10pc':>12}"
    )
    print("")
    print(header)
    for name in DEFINITIONS:
        one = summary["rules"][name]
        share = one["share_within_10pc"]
        print(
            f"{name:10} {one['n']:>6} {one['no_value']:>5}"
            f" {_show(one['median_otel_ms']):>9} {_show(one['median_derived_ms']):>12}"
            f" {_show(one['median_abs_diff_ms']):>11}"
            f" {_show(one['median_rel_diff']):>9} {_show(one['p90_rel_diff']):>9}"
            f" {'n/a' if share is None else f'{share * 100:.1f}%':>12}"
        )
    print("")
    for name in DEFINITIONS:
        share = summary["rules"][name]["share_within_10pc"] or 0.0
        verdict = "meets" if share >= 0.90 else "does NOT meet"
        print(
            f"{name}: {share * 100:.1f} percent within 10 percent, {verdict} the rule"
        )


def _show(value: float | None) -> str:
    return "none" if value is None else f"{value:g}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default=None)
    parser.add_argument("--projects", default=str(PROJECTS))
    options = parser.parse_args()
    started = time.perf_counter()
    summary = compare(store_path(options.store), Path(options.projects))
    summary["wall_s"] = round(time.perf_counter() - started, 3)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    render(summary)
    print(f"\nwall {summary['wall_s']} s, records in {OUT / 'summary.json'}")


if __name__ == "__main__":
    main()
