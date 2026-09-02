"""Activities in, Evidence out, and the Appendix B session summary. Design 6.11.

The second reducer. `activities.rebuild` writes the activities; `summarize` reads them
back and writes one Evidence per number a reader may see. Both are registered in
`Store.reducers` and run in registration order, and importing this module imports
activities.py first, which is what fixes that order.

This is W1-T2's half of design 6.11: the numbers that are a count, a distinct count
or a sum over the activities the reducer wrote. The rest of spec 13 (fail-to-pass
cycles, edit epochs, revisits, exploration ratios, stable-state intervals, occupancy)
needs rules this task has not measured, and each of those appears in the summary with a
null value, coverage `unavailable` and a warning naming W2-T1. A field that needs a
surface no capture carried says that instead, and names the surface.

Two rules about zero. A count over an empty set is 0, and it is written with the
lifecycle activity as its source, because an Evidence with no source is refused and
"nothing happened" still has to be supported by the row that says the capability was
observable. A SUM over an empty set is null: with no compaction there is no
pre-compaction token count, and 0 would be a measurement nobody made.

`created_at` on every Evidence here is the capture's last arrival, not the wall clock.
The numbers are a pure function of the capture, so two rebuilds must produce two
identical rows; a clock in this field would make one measurement look like two.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from telltale.activities import rebuild
from telltale.correlate import (
    REDUCER_VERSION,
    as_activity,
    hashed_id,
    of_type,
)
from telltale.model import Activity, Evidence
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

# What `show` prints, in Appendix B's order. A metric this file writes and this table
# does not name is still an Evidence and still reachable through `telltale explain`.
SUMMARY_BLOCKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "usage",
        ("model_requests", "fresh_input_tokens", "cache_read_tokens", "output_tokens"),
    ),
    (
        "work",
        (
            "unique_files_read",
            "unique_files_changed",
            "file_revisits",
            "max_diff_lines",
            "final_diff_lines",
            "stable_state_work_intervals",
        ),
    ),
    (
        "verification",
        ("agent_test_runs", "failed_test_runs", "edits_after_last_successful_test"),
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
)

# The four token counters, under the summary's name for each.
_TOKENS = (
    ("fresh_input_tokens", "input_tokens"),
    ("cache_read_tokens", "cache_read_tokens"),
    ("output_tokens", "output_tokens"),
    ("cache_creation_tokens", "cache_creation_tokens"),
)

# The one sentence a field carries instead of a number it has no rule for yet.
PENDING = "measures.py (W2-T1) defines this; the activities it reads are written here"
NO_SNAPSHOT = "no telltale.repo.snapshot observation in this capture"

# What a failed test run IS, and the one way that reading goes wrong. Spec 13 defines a
# verification activity by its exit status, and the exit status of a pipeline is the
# last program's. Measured on S1: all three runs are `uv run pytest 2>&1 | tail -50`,
# every surface reported the call as successful, and the captured output of the first
# one says "1 failed, 1 passed". The command exited 0 and the tests did not pass, so a
# capture with no failing run is not a capture where nothing failed.
_EXIT_STATUS = (
    "counted from the exit status the surfaces reported for the command, which for a"
    " pipeline is the last program's and not the test runner's"
)
_UNSEEN = (
    "no surface in this capture could show this, so the count is null rather than 0:"
    " see the coverage block for which capability was missing"
)
_NONE_FAILED = (
    "no test run reported a failing exit status, which is not the same as every test"
    " passing: a run whose command pipes the test runner into another program reports"
    " that program's status (S1 measured exactly this)"
)


@dataclass(frozen=True)
class _Metric:
    """One number the summary may print, with the claim it supports attached."""

    name: str
    unit: str
    value: float | int | None
    coverage: str
    source: list[str]
    warnings: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


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
    fields = dict(_capture_of(activities).fields) if activities else {}
    out: dict[str, Any] = {"coverage": fields.get("coverage") or {}}
    out["capture_id"] = capture_id
    out["provider_session_id"] = _session_of(activities)
    # Derived across resumes and continuations (spec 10.1), which needs the resume link
    # W1-T1 records. No replayed capture carries one, so nothing here can compute it.
    out["conversation_lineage_id"] = None
    out["repo_id"] = fields.get("repo_id")
    out["environment_fingerprint_id"] = fields.get("environment_fingerprint_id")
    for block, names in SUMMARY_BLOCKS:
        out[block] = {name: _value(evidence.get(name)) for name in names}
    out["context"]["denominator_source"] = fields.get("context_window_source")
    out["claim_class"] = "derived"
    out["forecast_readiness"] = _readiness(activities)
    out["reducer_version"] = _reducer_of(activities)
    out["warnings"] = _warnings(evidence)
    out["diagnostics"] = len(store.diagnostics(capture_id))
    return out


def _metrics(activities: Sequence[Activity]) -> list[_Metric]:
    """Every number `show` may print, each with its coverage and its sources."""
    if not activities:
        return []
    capture = _capture_of(activities)
    coverage = dict(capture.fields.get("coverage") or {})
    anchor = [capture.activity_id]
    rows = [
        *_usage(of_type(activities, ("model_request",)), coverage, anchor),
        *_work(activities, coverage, anchor),
        *_context(of_type(activities, ("compaction",)), coverage, anchor),
    ]
    return [_honest(metric) for metric in rows]


def _honest(metric: _Metric) -> _Metric:
    """A number nobody could have seen is null, whatever the arithmetic came to.

    A count over an empty set is 0 only when the capability was observable. With
    coverage `unavailable` the same 0 says "this did not happen" about a capture where
    it could not have been seen, and those are the two statements design invariant 5
    exists to keep apart. Measured on a capture the launcher made around
    `bash -c 'echo hello'`: three surfaces configured, none delivered, every capability
    unavailable, and before this the summary reported 0 model requests, 0 test runs and
    0 compactions as though the session had made none.
    """
    if metric.coverage != "unavailable" or metric.value is None:
        return metric
    return replace(metric, value=None, warnings=(*metric.warnings, _UNSEEN))


def _usage(
    requests: Sequence[Activity], coverage: Mapping[str, str], anchor: list[str]
) -> list[_Metric]:
    state = coverage.get("request_usage", "unavailable")
    seen = _ids(requests, anchor)
    rows = [_Metric("model_requests", "requests", len(requests), state, seen)]
    for metric, name in _TOKENS:
        carried = [item for item in requests if name in item.fields]
        rows.append(
            _Metric(
                metric,
                "tokens",
                sum(item.fields[name] for item in carried) if carried else None,
                state if len(carried) == len(requests) else "partial",
                _ids(carried, anchor),
            )
        )
    return rows


def _work(
    activities: Sequence[Activity], coverage: Mapping[str, str], anchor: list[str]
) -> list[_Metric]:
    reads = of_type(activities, ("file_read",))
    edits = of_type(activities, ("file_edit",))
    runs = of_type(activities, ("verification_run",))
    tests = [item for item in runs if item.fields.get("category") == "test"]
    failed = [item for item in tests if item.fields.get("success") is False]
    paths = coverage.get("file_paths", "unavailable")
    return [
        _Metric(
            "unique_files_read", "files", _distinct(reads), paths, _ids(reads, anchor)
        ),
        _Metric(
            "unique_files_changed",
            "files",
            _distinct(edits),
            paths,
            _ids(edits, anchor),
        ),
        *_pending(("file_revisits", "stable_state_work_intervals"), "count"),
        *_pending(("max_diff_lines", "final_diff_lines"), "lines", NO_SNAPSHOT),
        _Metric(
            "agent_test_runs",
            "runs",
            len(tests),
            coverage.get("commands", "unavailable"),
            _ids(tests, anchor),
        ),
        _Metric(
            "failed_test_runs",
            "runs",
            len(failed),
            _stated(tests, coverage),
            _ids(failed, anchor),
            () if failed or not tests else (_NONE_FAILED,),
            (_EXIT_STATUS,),
        ),
        *_pending(("edits_after_last_successful_test",), "edits"),
    ]


def _context(
    rows: Sequence[Activity], coverage: Mapping[str, str], anchor: list[str]
) -> list[_Metric]:
    state = coverage.get("compaction", "unavailable")
    out = [_Metric("compactions", "compactions", len(rows), state, _ids(rows, anchor))]
    for metric, name in (
        ("pre_compaction_tokens", "pre_tokens"),
        ("post_compaction_tokens", "post_tokens"),
    ):
        carried = [item for item in rows if name in item.fields]
        missing = len(rows) - len(carried)
        out.append(
            _Metric(
                metric,
                "tokens",
                sum(item.fields[name] for item in carried) if carried else None,
                state if not missing else "partial",
                _ids(carried, anchor),
                ()
                if not missing
                else (
                    f"{missing} of {len(rows)} compactions carried no {name}: E01"
                    " measured that a failed compaction reports none",
                ),
            )
        )
    out += _pending(("occupancy_ratio",), "ratio")
    return out


def _stated(tests: Sequence[Activity], coverage: Mapping[str, str]) -> str:
    """observed only when every run in scope stated an outcome, partial otherwise.

    A count of failures over runs whose success nobody saw is a count of the failures
    that happened to be visible, and printing that as `observed` would say more than the
    surfaces did. E01: only the OTel tool_result states success outright.
    """
    stated = [item for item in tests if isinstance(item.fields.get("success"), bool)]
    if len(stated) == len(tests):
        return coverage.get("tool_calls", "unavailable")
    return "partial"


def _ids(rows: Sequence[Activity], anchor: list[str]) -> list[str]:
    """The activity ids a number was read from, or the capture row when there are none.

    Never empty: an Evidence with no source is refused by the model unless its coverage
    is `unavailable`, and a count of zero that WAS observable is supported by the
    lifecycle row carrying the coverage that says so.
    """
    return [item.activity_id for item in rows] or anchor


def _distinct(rows: Sequence[Activity]) -> int | None:
    """How many distinct paths these activities name. Three answers, not two.

    No activities is 0 files. Activities that name paths is the number of distinct ones.
    Activities where no path was observable is None, because "the agent read nothing"
    and "what it read could not be seen" is the distinction coverage exists for.
    """
    paths = {item.fields["file_path"] for item in rows if "file_path" in item.fields}
    if not rows:
        return 0
    return len(paths) or None


def _pending(names: Sequence[str], unit: str, why: str = PENDING) -> list[_Metric]:
    return [_Metric(name, unit, None, "unavailable", [], (why,)) for name in names]


def _evidence(capture_id: str, metric: _Metric, stamp: str) -> Evidence:
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


def _value(row: Mapping[str, Any] | None) -> float | int | None:
    """A REAL out of SQLite, back in the unit its Evidence declared it in."""
    if row is None or row["value"] is None:
        return None
    number = float(row["value"])
    return number if row["unit"] == "ratio" else int(number)


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


def _readiness(activities: Sequence[Activity]) -> dict[str, bool]:
    """Which logical clocks this capture can index (spec 15.2), as a fact about rows."""
    return {
        "request_clock": bool(of_type(activities, ("model_request",))),
        "attempt_clock": bool(of_type(activities, ("correlation",))),
        "change_clock": bool(of_type(activities, ("repo_commit",))),
    }


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
