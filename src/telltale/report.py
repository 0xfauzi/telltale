"""Rendering for the CLI. Design 6.13: report.py renders what cli.py decides to print.

Four renderers: a fixed-width table, the activity timeline, the Appendix B summary and
`explain`, which walks one number back to the bytes it came from.

Three rules this file holds for all of them. A value that is None or absent prints as
`-` and never as blank or as 0: design invariant 5 says unknown stays unknown, and a
is the last place where "no compactions were observed" could turn into "0 compactions".
A number is formatted by the caller, from its unit, because `Evidence.value` is a
SQLite REAL and float(2.0) is not the integer 2 that a request count is. And nothing
here computes: every number printed is already a field of an Activity or an Evidence,
so a column a reader doubts is walked back with `explain` rather than reread here.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.store import Store

UNKNOWN = "-"

TIMELINE_COLUMNS = ("time", "type", "actor", "name", "duration_ms", "outcome", "claim")

# What the `name` column shows, first match wins. The order is what makes one column
# readable across seven activity types: a Bash call is its command, a Read is its path,
# and a lifecycle row is the event, not the model it happened to name.
_NAME_FIELDS = (
    "file_path",
    "command_norm",
    "event",
    "model",
    "agent_type",
    "trigger",
    "tool_name",
)

# How wide the name column may get. A normalized command is bounded at 200 characters
# (commands.MAX_COMMAND) and a table 200 columns wide is not a table. The full value is
# on the activity, one `telltale explain` or `show` away, and the cut is marked.
NAME_WIDTH = 56

# How wide a payload may print inside `explain` before it is cut. `explain` exists to
# show the bytes behind a number, so this is generous; a stream init message carries the
# whole tool list and would otherwise be the only thing on the screen.
MAX_PAYLOAD = 2000


def render_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    """A fixed-width table of `columns` taken from `rows`, header first.

    `columns` is the order and the selection: a key a row carries and `columns` does not
    name is not printed, and a column no row carries prints as a column of `-`.
    """
    if not columns:
        return ""
    header = [column.upper() for column in columns]
    cells = [[_cell(row.get(column)) for column in columns] for row in rows]
    widths = [
        max(len(line[index]) for line in [header, *cells])
        for index in range(len(columns))
    ]
    rule = ["-" * width for width in widths]
    return "\n".join(_line(line, widths) for line in [header, rule, *cells])


def timeline(rows: Sequence[Mapping[str, Any]]) -> str:
    """The activities of one capture as a table, in the order they started.

    Ties are broken by the primary observation id, which is arrival order (design 6.2),
    and never by anything that would impose a total order on work that overlapped:
    parallel tool calls and a subagent's calls genuinely share a start.
    """
    ordered = sorted(rows, key=_order)
    return render_table([_timeline_row(row) for row in ordered], TIMELINE_COLUMNS)


def _order(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """Start, then arrival, then the two fields that separate a tie inside one arrival.

    The primary observation is arrival order (design 6.2) and orders everything that
    started at the same instant. It is not unique: the capture's own lifecycle row opens
    on the capture's FIRST observation, and when that observation is also a session
    start (Codex S3: codex.otel.conversation_starts) two rows share a start and a tie
    break. An observation id is a ULID minted at ingest, so falling through to the
    database's order made those two rows swap between runs and a golden unreproducible.
    Type and event are properties of the reduction and are the same on every rebuild.
    """
    fields = dict(row.get("fields") or {})
    tie = str(fields.get("primary_observation") or row.get("activity_id") or "")
    return (
        str(row.get("started_at") or ""),
        tie,
        str(row.get("activity_type") or ""),
        str(fields.get("event") or ""),
    )


def _timeline_row(row: Mapping[str, Any]) -> dict[str, Any]:
    fields = dict(row.get("fields") or {})
    return {
        "time": _clock(str(row.get("started_at") or ""), str(fields.get("clock", ""))),
        "type": row.get("activity_type"),
        "actor": row.get("actor"),
        "name": _name(fields),
        "duration_ms": fields.get("duration_ms"),
        "outcome": _outcome(fields),
        # Never omitted (design 6.13). An activity is derived by definition, and the
        # column is here so that no reader has to remember that.
        "claim": "derived",
    }


def _clock(stamp: str, clock: str) -> str:
    """`HH:MM:SS.mmm` when the provider gave a time, `-` when only the receiver did.

    An activity built from hooks, stream system messages or the stream result has no
    provider timestamp anywhere (E01), and its `started_at` is the moment the record
    reached the receiver. That is a fact about Telltale, not about the session, so the
    row keeps its POSITION in the table and claims no time. The arrival stamp is still
    on the activity for anything that needs it.
    """
    if clock != "provider":
        return UNKNOWN
    if len(stamp) >= 23 and stamp[10:11] == "T":
        return stamp[11:23]
    return stamp or UNKNOWN


def _name(fields: Mapping[str, Any]) -> Any:
    # Truthiness, not `is not None`: S7's SubagentStop hook reports `agent_type` as the
    # empty string, and an empty cell in this column reads as a rendering fault rather
    # than as what it is. The field keeps the empty string; the column says unknown.
    for key in _NAME_FIELDS:
        if fields.get(key):
            return _short(str(fields[key]))
    return None


def _short(text: str) -> str:
    return text if len(text) <= NAME_WIDTH else text[: NAME_WIDTH - 3] + "..."


def _outcome(fields: Mapping[str, Any]) -> str | None:
    """ok, failed, or unknown. Absence of a failure is not success (design 6.3)."""
    if isinstance(fields.get("success"), bool):
        return "ok" if fields["success"] else "failed"
    if isinstance(fields.get("is_error"), bool):
        return "failed" if fields["is_error"] else "ok"
    return None


def show(summary: Mapping[str, Any]) -> str:
    """The Appendix B summary as JSON, in the order the summary built it.

    Not sorted: design 6.13 puts coverage first and the diagnostics count last, and
    sorting the keys would move both into the middle of the alphabet.
    """
    return json.dumps(summary, indent=2, ensure_ascii=False)


def explain(store: Store, capture_id: str, metric: str) -> str:
    """One number, the activities it was computed from, and their observations.

    The chain is the whole point: a reader who doubts a number follows source ids from
    the Evidence to the Activity to the observation payload the provider sent, and every
    id in between resolves or the reducer that wrote it has a bug.
    """
    rows = [row for row in store.evidence(capture_id) if row["metric"] == metric]
    if not rows:
        known = sorted({str(row["metric"]) for row in store.evidence(capture_id)})
        listed = ", ".join(known) or UNKNOWN
        return f"{metric}: no evidence in {capture_id}. Known: {listed}"
    evidence = rows[0]
    activities = {str(row["activity_id"]): row for row in store.activities(capture_id)}
    lines = _evidence_lines(evidence)
    for source in evidence["source"]:
        lines += _source_lines(store, source, activities)
    return "\n".join(lines)


def _evidence_lines(evidence: Mapping[str, Any]) -> list[str]:
    value = evidence["value"]
    shown = UNKNOWN if value is None else f"{_number(value, evidence['unit'])}"
    out = [
        f"metric           {evidence['metric']}",
        f"value            {shown} {evidence['unit']}",
        f"claim_class      {evidence['claim_class']}",
        f"coverage         {evidence['coverage']}",
        f"reducer_version  {evidence['reducer_version']}",
        f"created_at       {evidence['created_at']}",
    ]
    for name in ("assumptions", "warnings"):
        for text in evidence[name] or []:
            out.append(f"{name:<16} {text}")
    out.append(f"sources          {len(evidence['source'])}")
    return out


def _number(value: float, unit: str) -> str:
    return str(value) if unit == "ratio" else str(int(value))


def _source_lines(
    store: Store, source: str, activities: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    activity = activities.get(source)
    if activity is None:
        return ["", f"  {source}  (not an activity of this capture)"]
    fields = dict(activity["fields"])
    out = [
        "",
        f"  {source}  {activity['activity_type']}  {activity['actor']}"
        f"  {activity['started_at']}",
        f"    fields  {json.dumps(fields, sort_keys=True)[:MAX_PAYLOAD]}",
    ]
    provenance = dict(activity["provenance"])
    ids = sorted({one for many in provenance.values() for one in many})
    for name, sources in sorted(provenance.items()):
        out.append(f"    {name} <- {', '.join(sources)}")
    out += _observation_lines(store, ids)
    return out


def _observation_lines(store: Store, ids: Sequence[str]) -> list[str]:
    found = store.observations_by_id(ids)
    out = []
    for row in found:
        stamp = row["provider_ts"] or f"ingest {row['ingest_ts']}"
        out.append(
            f"    {row['observation_id']}  {row['observation_type']}"
            f"  {row['surface']}  {stamp}"
        )
        payload = json.dumps(row["payload"], sort_keys=True)
        out.append(f"      {payload[:MAX_PAYLOAD]}")
    # A provenance id that resolves to nothing is a bug in the reducer, so it is named
    # here rather than skipped: a missing row is exactly what a silent `continue` hides.
    missing = sorted(set(ids) - {str(row["observation_id"]) for row in found})
    out += [f"    {one}  (not a stored observation)" for one in missing]
    return out


def _line(cells: Sequence[str], widths: Sequence[int]) -> str:
    # rstrip because the last column's padding is invisible in a terminal and visible
    # in every diff of a captured output.
    pairs = zip(cells, widths, strict=True)
    return "  ".join(cell.ljust(width) for cell, width in pairs).rstrip()


def _cell(value: Any) -> str:
    return UNKNOWN if value is None else str(value)
