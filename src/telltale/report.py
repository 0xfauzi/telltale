"""Rendering for the CLI. Design 6.13: report.py renders what cli.py decides to print.

Seven renderers: a fixed-width table, the activity timeline, the Appendix B summary,
`explain`, which walks one number back to the bytes it came from, the two the experiment
runners print (one condition, and two arms of one factor), and the one spec 13.7 asks
for (one capture's evidence vector).

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

from telltale.stats import UNRESOLVED

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
    """ok, failed, or unknown. Absence of a failure is not success (design 6.3).

    The exit status comes first, for the reason spec 13.1 gives: a verification activity
    is defined by it. Measured on Codex S1, where the rollout records exit code 1 and
    status "failed" for a pytest run that `codex.otel.tool_result.success` calls true.
    Reading `success` first printed `ok` on the row the summary counts as a failure, and
    a timeline that disagrees with the summary about one call is worse than either.
    """
    if isinstance(fields.get("exit_code"), int):
        return "ok" if fields["exit_code"] == 0 else "failed"
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


# -- experiment repeat (design 6.12) ------------------------------------------------

# The two tables `experiment repeat` prints. Design 6.13: the claim class column is
# never omitted, and here it is two different answers on one page, which is the point.
RUN_COLUMNS = (
    "attempt",
    "capture_id",
    "exit_code",
    "acceptance",
    "wall_ms",
    "duration_ms",
    "coverage",
    "claim_class",
)
STAT_COLUMNS = (
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

# Rounded for the terminal through _amount, so a count prints as a count. The
# report.json the runner writes keeps the full float: this rounding is for reading.
_SCALED = ("median", "mad_scaled", "iqr", "min", "max")

_WITHIN = """\
Every row of the second table is COMPARATIVE WITHIN THIS CONDITION: one task, one base
commit, one environment fingerprint, {n} repetitions. It says how much a number moved
when nothing but the run changed. It is not a comparison with any other condition, and
the per-capture numbers it is built from are derived from one capture each."""


def experiment(measured: Mapping[str, Any]) -> str:
    """One repeat report as the two tables and the sentence that bounds them."""
    lines = [
        f"experiment {measured['experiment']} task {measured['task_id']}:"
        f" {len(measured['captures'])} captures,"
        f" environment {measured['environment_fingerprint_id']}",
        f"acceptance: {measured['acceptance']}",
        "",
        render_table(_repetition_rows(measured), RUN_COLUMNS),
        "",
        render_table(_stat_rows(measured), STAT_COLUMNS),
        "",
        _WITHIN.format(n=len(measured["captures"])),
    ]
    lines += [f"warning: {one}" for one in measured["warnings"]]
    return "\n".join(lines)


def _repetition_rows(measured: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            **{name: run.get(name) for name in RUN_COLUMNS},
            "acceptance": run["acceptance"]["status"],
            "claim_class": measured["claim_class"]["vector"],
        }
        for run in measured["repetitions"]
    ]


def _stat_rows(measured: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "metric": metric,
            "claim_class": measured["claim_class"]["stats"],
            **{name: found.get(name) for name in STAT_COLUMNS if name in found},
            **{
                name: _amount(found[name])
                for name in _SCALED
                if found[name] is not None
            },
            "values": ",".join(_amount(value) for value in found["values"]),
        }
        for metric, found in sorted(measured["stats"].items())
    ]


def _amount(value: float) -> str:
    """A float that is a whole number prints as one. Evidence.value is a REAL."""
    return str(int(value)) if float(value).is_integer() else f"{value:.3f}"


# -- experiment environment (design 6.12's H3) ---------------------------------------

# The between-arm table. `s` is the pooled spread the MDD is computed from and s_a and
# s_b are the two arms' own, printed beside it because a pooled number hides which arm
# was the noisy one. claim_class is never omitted: every row here is comparative.
BETWEEN_COLUMNS = (
    "metric",
    "claim_class",
    "n_a",
    "n_b",
    "hl_shift",
    "cliffs_delta",
    "p",
    "s_a",
    "s_b",
    "s",
    "median",
    "mdd",
    "n_needed",
    "demoted",
    "label",
)

_ROUNDED = ("hl_shift", "cliffs_delta", "p", "s_a", "s_b", "s", "median", "mdd")

# The paragraph avoids the three words ADR-014 refuses, including in the sentence that
# says they do not apply: a reader grepping this output for them must find nothing.
_NOT_RESOLVED = """\
"{unresolved}" is a statement about n, not about the two arms. It says the shift this
pilot measured is smaller than the smallest one {n} repetitions per arm can separate
from run-to-run variation, so this experiment cannot tell such a shift from run-to-run
noise for that measure. It does not say the two arms behave alike, and no row here may
be read as more than a difference measured between two arms of one task at one base
commit. N_NEEDED is the per-arm repetitions at which MDD falls to a quarter of the
pooled median, which is what it takes to settle the question. DEMOTED is design 6.12's
rule that a measure still above that quarter is withheld from repository comparison."""


def environment(measured: Mapping[str, Any]) -> str:
    """One environment experiment: the constants, the assertion, the arms, the table."""
    arms = measured["arms"]
    lines = [
        f"experiment {measured['experiment']} task {measured['task_id']}:"
        f" factor {measured['factor']}, {len(arms)} arms",
        f"pre-registered constants: {_constants(measured['constants'])}",
        _assertion_line(measured["fingerprint_assertion"]),
    ]
    for arm in arms:
        lines += [
            "",
            f"ARM {arm['name']} (task {arm['task_id']})",
            "",
            experiment(arm["report"]),
        ]
    lines += [
        "",
        render_table(_between_rows(measured), BETWEEN_COLUMNS),
        "",
        _NOT_RESOLVED.format(unresolved=UNRESOLVED, n=_per_arm(arms)),
    ]
    lines += [f"warning: {one}" for one in measured["warnings"]]
    return "\n".join(lines)


def _constants(constants: Mapping[str, Any]) -> str:
    return ", ".join(f"{name}={value}" for name, value in constants.items())


def _per_arm(arms: Sequence[Mapping[str, Any]]) -> str:
    counts = sorted({len(arm["report"]["captures"]) for arm in arms})
    return " and ".join(str(count) for count in counts)


def _assertion_line(assertion: Mapping[str, Any]) -> str:
    """Both fingerprint ids, the field that differs, and the two values it takes."""
    ids = ", ".join(
        f"{name}={one}" for name, one in assertion["fingerprint_ids"].items()
    )
    values = "; ".join(
        f"{field}: " + ", ".join(f"{name}={value!r}" for name, value in arms.items())
        for field, arms in assertion["values"].items()
    )
    return (
        f"fingerprints: {ids}; differing fields {assertion['differing_fields']}"
        f" ({values}); {assertion['assertion']}"
    )


def _between_rows(measured: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "metric": metric,
            **{name: row.get(name) for name in BETWEEN_COLUMNS if name in row},
            **{
                name: _amount(row[name])
                for name in _ROUNDED
                if row.get(name) is not None
            },
        }
        for metric, row in sorted(measured["between"].items())
    ]


# -- the evidence vector and compare (spec 13.7, design 6.13) -------------------------

VECTOR_COLUMNS = (
    "family",
    "metric",
    "value",
    "unit",
    "coverage",
    "claim_class",
    "percentile",
)

# The coverage word is printed beside every value on both sides for the reason design
# invariant 5 gives: `-` in a value column means either "the capture did not do this" or
# "no surface here could have shown it", and only the coverage word separates them.
_VECTOR_NOTE = """\
PERCENTILE is a rank inside the cohort named above, computed from this database as it
stands at this moment. Adding a capture that shares the four cohort keys changes it, so
two runs of this command on two days may print two numbers for one capture. That is what
a percentile is, and it is why none of them is stored. A cell reading "no cohort" is not
a zero and not a missing number: it names what stopped the comparison, and the raw value
beside it is unaffected by it."""


def vector(built: Mapping[str, Any]) -> str:
    """Spec 13.7's evidence vector of one capture, as a table. Design 6.13."""
    return "\n".join(
        [
            _cohort_line(built),
            "",
            render_table(_vector_rows(built), VECTOR_COLUMNS),
            "",
            _VECTOR_NOTE,
        ]
    )


def _vector_rows(built: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"family": family, "metric": name, **_side(cell)}
        for family, name, cell in _walk(built)
    ]


def _side(cell: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "value": cell["value"],
        "unit": cell["unit"],
        "coverage": cell["coverage"],
        "claim_class": cell["claim_class"],
        "percentile": _percentile(cell["percentile"]),
    }


def _walk(built: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    return [
        (family, name, cell)
        for family, metrics in built.items()
        for name, cell in metrics.items()
    ]


def _percentile(cell: Mapping[str, Any]) -> str:
    """The percentile column. Never blank, never 0, never n/a: always a statement."""
    if "no_cohort" in cell:
        found = cell["no_cohort"]
        inside = f"n={found}" if isinstance(found, int) else str(found)
        return f"no cohort ({inside})"
    return _claimed(cell)


def _claimed(cell: Mapping[str, Any]) -> str:
    """A number with the claim class it was built under, in one cell.

    The difference and the percentiles sit in a table whose CLAIM_CLASS column is about
    the raw values, so each of them says its own here. Design 6.13: the claim class is
    never omitted, and a comparative number printed in a row headed `derived` would be
    exactly that omission.
    """
    return f"{cell['value']} ({cell['claim_class']})"


# What the cohort line leaves out: the member ids, which are 26 characters each, and
# the per-metric count, which differs down the column and so cannot head the table.
# Everything else in the cohort dict is printed, rather than a list of key names kept
# here, so a key added to a cohort cannot go missing from the line that describes it.
_COHORT_UNPRINTED = ("captures", "n_metric")


def _cohort_line(built: Mapping[str, Any]) -> str:
    """The cohort every percentile in this vector was taken over, or why there is none.

    Read off the first percentile cell that carries one rather than passed in: the
    cohort is a property of the Evidence, so a header that disagreed with the column
    under it would be prose nothing checked.
    """
    for _family, _name, cell in _walk(built):
        found = cell["percentile"]
        if "cohort" in found:
            named = ", ".join(
                f"{key}={value}"
                for key, value in found["cohort"].items()
                if key not in _COHORT_UNPRINTED
            )
            return f"cohort: {named}"
    return f"cohort: none, {_first_reason(built)}"


def _first_reason(built: Mapping[str, Any]) -> str:
    for _family, _name, cell in _walk(built):
        found = cell["percentile"]["no_cohort"]
        return f"n={found}" if isinstance(found, int) else str(found)
    return UNKNOWN
