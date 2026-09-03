"""Activities in, Evidence out, and the Appendix B session summary. Design 6.11.

The second reducer. `activities.rebuild` writes the activities; `summarize` reads them
back and writes one Evidence per number a reader may see. Both are registered in
`Store.reducers` and run in registration order, and importing this module imports
activities.py first, which is what fixes that order.

Every neutral measure of spec 13 is here: verification cycles (13.1), edit turnover
(13.2), exploration scope (13.3), context and token burden (13.4), stable-state work
intervals (13.5) and delegation (13.6). The three that depend on ORDER rather than on a
count are in measures_intervals.py, which holds the walks and none of the evidence.

Two rules about zero. A count over an empty set is 0, and it is written with the
lifecycle activity as its source, because an Evidence with no source is refused and
"nothing happened" still has to be supported by the row that says the capability was
observable. A SUM over an empty set is null: with no compaction there is no
pre-compaction token count, and 0 would be a measurement nobody made.

One rule about coverage. A metric takes the WEAKEST word among the capabilities it
needed (`_weakest`), and `_honest` turns a value whose coverage is `unavailable` into
null whatever the arithmetic came to. That is the whole of design invariant 5 in two
functions: 0 compactions and "compaction was not observable here" are different
statements and this file will not let them share a spelling.

`created_at` on every Evidence here is the capture's last arrival, not the wall clock.
The numbers are a pure function of the capture, so two rebuilds must produce two
identical rows; a clock in this field would make one measurement look like two.

`value_of` is public because cohorts.py reads the same rows back: spec 13.7's vector is
the same numbers placed against a cohort, and two decoders of one REAL column would be
two answers about what a token count is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telltale import measures_intervals as walks
from telltale import measures_spec13 as spec13
from telltale.activities import rebuild
from telltale.correlate import (
    REDUCER_VERSION,
    as_activity,
    hashed_id,
    of_type,
)
from telltale.forecast import readiness
from telltale.model import Activity, Evidence
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from telltale.measures_spec13 import Metric

# What `show` prints, in Appendix B's order. A metric this file writes and this table
# does not name is still an Evidence and still reachable through `telltale explain`.
SUMMARY_BLOCKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "usage",
        (
            "model_requests",
            "fresh_input_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "output_tokens",
        ),
    ),
    (
        "work",
        (
            "unique_files_read",
            "unique_files_changed",
            "file_revisits",
            "max_diff_lines",
            "final_diff_lines",
            "reversions",
            "post_failure_revisits",
            "refused_tool_calls",
            "stable_state_work_intervals",
        ),
    ),
    (
        "verification",
        (
            "agent_test_runs",
            "failed_test_runs",
            "fail_to_pass_cycles",
            "edits_after_last_successful_test",
            "edit_epochs_with_verification",
            "edit_epochs_without_verification",
            "targeted_test_runs",
            "full_test_runs",
        ),
    ),
    (
        "exploration",
        (
            "unique_files_read_before_first_edit",
            "unique_files_read",
            "search_ops",
            "directories_traversed",
            "read_to_edit_ratio",
            "explored_to_final_ratio",
        ),
    ),
    (
        "context",
        (
            "compactions",
            "pre_compaction_tokens",
            "post_compaction_tokens",
            "occupancy_ratio",
        ),
    ),
    (
        "stable_state",
        (
            "stable_state_work_intervals",
            "stable_state_total_ms",
            "stable_state_max_ms",
            "stable_state_tokens",
            "stable_state_ended_by_edit",
            "stable_state_ended_by_verification",
            "stable_state_ended_by_capture_end",
        ),
    ),
    (
        "delegation",
        (
            "subagent_count",
            "subagent_tokens",
            "direct_tool_calls",
            "delegated_tool_calls",
        ),
    ),
)


def summarize(store: Store, capture_id: str) -> None:
    """Write one Evidence per number of this capture. Registered in Store.reducers."""
    activities = [as_activity(row) for row in store.activities(capture_id)]
    stamp = max(
        (str(row["ingest_ts"]) for row in store.observations(capture_id)), default=""
    )
    rows = [_evidence(capture_id, metric, stamp) for metric in _metrics(activities)]
    store.replace_evidence(capture_id, rows)


def summary(store: Store, capture_id: str) -> dict[str, Any]:
    """The Appendix B session summary: coverage first, diagnostics count last."""
    activities = [as_activity(row) for row in store.activities(capture_id)]
    evidence = {str(row["metric"]): row for row in store.evidence(capture_id)}
    capture = _capture_of(activities) if activities else None
    fields = dict(capture.fields) if capture else {}
    out: dict[str, Any] = {"coverage": _coverage_of(activities)}
    out["capture_id"] = capture_id
    out["provider_session_id"] = _session_of(activities)
    # Derived across resumes and continuations (spec 10.1), which needs the resume link
    # W1-T1 records. No replayed capture carries one, so nothing here can compute it.
    out["conversation_lineage_id"] = None
    out["repo_id"] = fields.get("repo_id")
    out["environment_fingerprint_id"] = fields.get("environment_fingerprint_id")
    out["session"] = _session_block(activities, capture)
    for block, names in SUMMARY_BLOCKS:
        out[block] = {name: value_of(evidence.get(name)) for name in names}
    out["context"]["denominator_source"] = fields.get("context_window_source")
    out["context"]["context_window"] = fields.get("context_window")
    # Not an Evidence: it is a repository fingerprint copied from the snapshot activity
    # that was current when the last verification ran, not a number derived from any.
    out["verification"]["last_verification_repo_hash"] = _last_repo_hash(activities)
    out["claim_class"] = "derived"
    out["forecast_readiness"] = readiness.summary_field(store, capture_id)
    out["reducer_version"] = _reducer_of(activities)
    out["warnings"] = _warnings(evidence)
    out["diagnostics"] = len(store.diagnostics(capture_id))
    return out


def _session_block(
    activities: Sequence[Activity], capture: Activity | None
) -> dict[str, Any]:
    """Who ran, on what, and how it ended. Values as stored, with no prose added.

    `is_error` and `stop_reason` come off the session_end lifecycle row that carries
    them, which is the one built from the stream result. W1-T6 measured a session ended
    by the subscription limit reporting subtype `success` WITH `is_error` true, so a
    reader who wants to know whether a capture ran to completion has to see both fields
    and neither may be turned into a word here.
    """
    ends = [
        item.fields
        for item in _lifecycle(activities, "session_end")
        if "is_error" in item.fields or "stop_reason" in item.fields
    ]
    starts = [item.fields for item in _lifecycle(activities, "session_start")]
    last = ends[-1] if ends else {}
    first = starts[0] if starts else {}
    fields = dict(capture.fields) if capture else {}
    return {
        "provider": fields.get("provider"),
        "models": fields.get("models") or _models(activities),
        "runtime_version": first.get("claude_code_version"),
        "permission_mode": first.get("permission_mode"),
        "is_error": last.get("is_error"),
        "stop_reason": last.get("stop_reason"),
        "num_turns": last.get("num_turns"),
        "duration_ms": _duration(capture),
    }


def _lifecycle(activities: Sequence[Activity], event: str) -> list[Activity]:
    return [
        item
        for item in walks.ordered(of_type(activities, ("lifecycle",)))
        if item.fields.get("event") == event
    ]


def _models(activities: Sequence[Activity]) -> list[str] | None:
    """The models the requests named, when the capture row did not state a set."""
    found = sorted(
        {
            str(item.fields["model"])
            for item in of_type(activities, ("model_request",))
            if item.fields.get("model")
        }
    )
    return found or None


def _duration(capture: Activity | None) -> float | None:
    """First observation to last, in milliseconds, or None when one end is missing.

    The capture activity's own span. It is the RECORDER's view of the session and not
    the agent's: `clock` on that row says which of the two clocks it is on, and a
    capture whose observations carried no provider time is timed by arrival.
    """
    if capture is None:
        return None
    started = walks._moment(capture.started_at)
    ended = walks._moment(capture.ended_at)
    if started is None or ended is None:
        return None
    # Three decimals, for the reason measures_intervals._span gives: the clocks report
    # microseconds, so a millisecond carries three digits of measurement and no more.
    return round((ended - started) * 1000.0, 3)


def _metrics(activities: Sequence[Activity]) -> list[Metric]:
    """Every number `show` may print, each with its coverage and its sources.

    The seven blocks of spec 13, in Appendix B's order. Each builder is passed the whole
    capture rather than its own slice, because several of them read a second type to
    decide what a first one means: a verification run is placed against the repository
    snapshots around it, and an exploration count against the first edit.
    """
    if not activities:
        return []
    capture = _capture_of(activities)
    coverage = dict(_coverage_of(activities))
    anchor = [capture.activity_id]
    rows = [
        *spec13.usage(
            of_type(activities, ("model_request",)),
            _session_totals(activities),
            coverage,
            anchor,
        ),
        *spec13.work(activities, capture, coverage, anchor),
        *spec13.verification(activities, coverage, anchor),
        *spec13.exploration(activities, coverage, anchor),
        *spec13.context(activities, capture, coverage, anchor),
        *spec13.stable_state(activities, coverage, anchor),
        *spec13.delegation(activities, coverage, anchor),
    ]
    return [spec13.honest(metric) for metric in rows]


def _session_totals(activities: Sequence[Activity]) -> dict[str, Activity]:
    """The lifecycle rows carrying a provider figure for a whole-session counter.

    Keyed by the summary metric the figure answers for, so that spec13.usage asks one
    question of it and this file holds the mapping. The LAST such row wins: a capture
    that resumed has more than one session_end and the last is the one that states the
    session as it finished.
    """
    found: dict[str, Activity] = {}
    for item in walks.ordered(of_type(activities, ("lifecycle",))):
        for metric, field in spec13.SESSION_FIELDS.items():
            if isinstance(item.fields.get(field), int):
                found[metric] = item
    return found


def _last_repo_hash(activities: Sequence[Activity]) -> str | None:
    """The dirty_tree_hash current when the last verification ran. Spec 13.1."""
    latest: str | None = None
    answer: str | None = None
    for item in walks.ordered(activities):
        if item.activity_type == "repo_snapshot":
            value = item.fields.get("dirty_tree_hash")
            latest = str(value) if isinstance(value, str) else latest
        elif item.activity_type == "verification_run":
            answer = latest
    return answer


def _evidence(capture_id: str, metric: Metric, stamp: str) -> Evidence:
    return Evidence.derived(
        evidence_id=hashed_id("ev", capture_id, "evidence", metric.name),
        metric=metric.name,
        value=metric.value,
        unit=metric.unit,
        coverage=metric.coverage,
        source=list(metric.source),
        capture_id=capture_id,
        reducer_version=REDUCER_VERSION,
        assumptions=list(metric.assumptions),
        warnings=list(metric.warnings),
        created_at=stamp,
    )


def _capture_of(activities: Sequence[Activity]) -> Activity:
    """The lifecycle row for the capture itself, which carries the measured coverage."""
    for item in activities:
        if item.fields.get("event") == "capture":
            return item
    raise ValueError("this capture has no lifecycle activity, so it has no coverage")


def _coverage_of(activities: Sequence[Activity]) -> dict[str, str]:
    if not activities:
        return {}
    return dict(_capture_of(activities).fields.get("coverage") or {})


def value_of(row: Mapping[str, Any] | None) -> float | int | None:
    """A REAL out of SQLite, back in the unit its Evidence declared it in."""
    if row is None or row["value"] is None:
        return None
    number = float(row["value"])
    return number if row["unit"] in ("ratio", "ms") else int(number)


def _warnings(evidence: Mapping[str, Mapping[str, Any]]) -> dict[str, list[str]]:
    return {
        name: list(row["warnings"])
        for name, row in sorted(evidence.items())
        if row["warnings"]
    }


def _session_of(activities: Sequence[Activity]) -> str | None:
    for item in activities:
        session = item.fields.get("provider_session_id")
        if session:
            return str(session)
    return None


def _reducer_of(activities: Sequence[Activity]) -> str:
    """The version that WROTE these rows, which is not always the one importing now.

    Read off the stored activities rather than from REDUCER_VERSION: a summary printed
    after the reducer changed and before `telltale rebuild` ran describes rows built by
    the old rules, and saying otherwise is the one lie this field exists to stop.
    """
    versions = sorted({item.reducer_version for item in activities})
    return ", ".join(versions) or REDUCER_VERSION


# Design 6.11. Registration order is run order (store.rebuild), and this loop is the
# only place it is stated: evidence is computed from activities that have already been
# written, so naming both reducers here means importing this module cannot get it wrong.
_REDUCERS: list[Callable[[Store, str], None]] = Store.reducers
for _reducer in (rebuild, summarize):
    if _reducer not in _REDUCERS:
        _REDUCERS.append(_reducer)
