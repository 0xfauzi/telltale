"""The attempt and change clocks: one repository lineage, not one capture. Design 6.12.

series.py folds ONE capture into one row per model request. These two clocks fold a
repository: every capture that ran against one repo_id, in the order they started. A
row is an attempt at a task, or a change that landed. Both are keyed on a repo_id
rather than on a capture id, which is the whole reason they live in their own file:
nothing here can be answered by reading one capture's activities.

Four rules, and each is a mechanism rather than a convention.

  An attempt is a capture that NAMES which attempt it is. Two surfaces say so and both
  are read: an `external.correlation` written through `/v1/correlations`, and the
  `telltale.capture_started` payload the launcher writes from `--task-id X --attempt
  N`. Measured on the owner's store on 2026-09-02: of 37 captures of this repository,
  22 carry task_id and attempt in capture_started and NONE carries a correlation, so a
  reader of correlations alone would report an empty lineage for the build that
  produced it. A capture carrying two different identities is refused by name rather
  than resolved: duplicate is not one.

  A row reads its own capture and nothing else. Attempts of one repository overlap
  (measured: W1-T4 attempt 2 spans 01:21 to 01:48 and W1-T5 attempt 1 sits inside it),
  so any rule that partitioned the lineage by wall-clock time would put one session's
  requests in another session's row.

  `row_end_ts` is a RUNNING maximum. A row closes when the last thing it read was
  observed, and an outcome about an attempt arrives after that attempt ended, often
  after a later attempt has run. Design 6.12 requires row_end_ts to be non-decreasing,
  and on this clock the row's own last observation is not: measured, W2-T8 attempt 1
  ended at 18:24 and W2-E05 attempt 2, which started later, ended at 18:11. So the
  running maximum is what the field holds, and it says "everything in rows 0..i had
  been observed by here", which is the claim `check` tests and the one a reader needs.

  A column with no capability list takes its coverage from its own cells: all None is
  `unavailable`, no None is `observed`, and anything between is `partial`. The outcome
  columns are the case that matters. No outcome anywhere makes the column unavailable
  and all None, never a column of zeros, because "the merge protocol has posted no
  outcome" and "the attempt was not accepted" are different statements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from telltale import series as compiler
from telltale import series_outcomes as outcomes
from telltale import series_regime as regimes
from telltale.model import ColumnSpec, RowMeta, Series

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from telltale.series_regime import Marks
    from telltale.store import Store

Refused = compiler.Refused

# Spec 12.3's confidence ranking, best first. A commit reaches the best rung any of its
# repo_commit activities recorded; a word outside this tuple is not a rung, and the
# commit is dropped by name rather than ranked last.
LADDER = (
    "explicit",
    "provider_reported",
    "tree_match_during",
    "tree_match_after",
    "heuristic",
)

# The two rungs that are evidence gathered AFTER the capture. A row built on one of
# them is flagged rather than dropped: it is a real commit and a reader may want it.
# Nothing excludes the flagged row; `telltale series build` counts and names them, and
# that is the whole of what reads this flag today (measured 2026-09-06, W8-T2).
LOW_CONFIDENCE_RUNGS = ("tree_match_after", "heuristic")
LOW_CONFIDENCE = "low_confidence"

# What a git history backfill records: this commit is a fact about the repository and
# the recorder made no link to any session. Not on LADDER, because it is not a link,
# and not a low_confidence rung, because nothing was guessed. A change at this rung
# that no attempt landed is flagged `uncaptured`, and its process columns are None by
# name rather than by arithmetic over an empty set. W8-T2.
UNLINKED = "unlinked"
UNCAPTURED = "uncaptured"

# The seven columns of a change row that only a capture of the session could fill. On
# an uncaptured row every one of them is None, and NO_CAPTURE is the sentence the
# cohort carries for each: `unavailable` says a column is empty and says nothing about
# which half of the row it is empty in.
PROCESS_COLUMNS = (
    "fresh_input_tokens_total", "cache_read_tokens_total", "compactions",
    "verification_cycles", "edit_turnover_ratio", "stable_state_intervals",
    "unique_files_read",
)  # fmt: skip
NO_CAPTURE = (
    "no capture landed this change: the row is a repository fact and the process that"
    " produced it was not observed"
)


def uncaptured(series: Series) -> list[str]:
    """The one assumption a run over a frame holding uncaptured rows has to state.

    A list of zero or one sentence, so a caller can splice it into an assumption list
    without a branch. Empty when no row carries the flag, because an assumption nothing
    in the frame triggers is noise on every other run.

    An uncaptured row is NOT excluded from a backtest, and this sentence is not an
    exclusion notice. The row's seven process columns are None, so each of those columns
    is `unavailable` or `partial` on the frame, and `backtest._variant` already drops a
    column on that word and names it in the run's warnings. That is the whole mechanism.
    What it does not say is how much of the frame it describes, and a reader of a stored
    forecast_runs row cannot work that out from the warnings alone: `forecast readiness`
    prints the same count beside the row count for the same reason.
    """
    rows = uncaptured_keys(series)
    if not rows:
        return []
    return [
        f"{len(rows)} of {len(series.row_meta)} rows are uncaptured: their process"
        " columns are unavailable and were excluded by name."
    ]


def uncaptured_keys(series: Series) -> list[str]:
    """The row_key of every row no capture landed. The count `uncaptured` states.

    Separate from the sentence above because two callers want two different things out
    of one fact, and a caller that took `len` of the sentence list would report 1.
    """
    return [meta.row_key for meta in series.row_meta if UNCAPTURED in meta.flags]


# The activity types a lineage row reads: everything carrying a number a column below
# is built from, plus the rows that say what the capture was. tool_call and subagent
# are left out because no column of either clock reads one.
_READ_TYPES = (
    "lifecycle",
    "model_request",
    "compaction",
    "verification_run",
    "command",
    "file_edit",
    "file_read",
    "repo_snapshot",
    "repo_commit",
    "correlation",
    "outcome",
)

# Design 6.12 names these columns and this order. An empty capability tuple means the
# column rests on no provider surface, so its coverage is measured from its own cells
# (`_measured`), exactly as the request clock measures `env_changed`. `env_changed` is
# last on both clocks and is appended by `assemble` from the row fingerprints.
_ATTEMPT_COLUMNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("attempt_of_component", "attempts", ()),
    ("model_requests", "requests", ("request_usage",)),
    ("fresh_input_tokens_total", "tokens", ("request_usage",)),
    ("compactions", "compactions", ("compaction",)),
    ("duration_ms", "ms", ()),
    ("verification_passed", "flag", ()),
    ("review_fail_count", "reviews", ()),
    ("accepted", "flag", ()),
    ("env_changed", "flag", ()),
)


@dataclass(frozen=True)
class Attempt:
    """One capture that named which attempt of which task it was."""

    capture_id: str
    task_id: str
    attempt: int
    started_at: str
    provider: str
    activities: list[Mapping[str, Any]]
    coverage: dict[str, str]
    fingerprint: str | None
    duration_ms: int | None


@dataclass
class Lineage:
    """Every attempt of one repository, and every capture that was not one."""

    repo_id: str
    attempts: list[Attempt] = field(default_factory=list)
    dropped: list[dict[str, str]] = field(default_factory=list)
    # capture_id -> its activities, for every capture of the repository, so the change
    # clock can read a repo_commit written by a capture that carried no attempt id.
    activities: dict[str, list[Mapping[str, Any]]] = field(default_factory=dict)
    # capture_id -> its provider, for the same captures. An Attempt carries its own,
    # but a git history backfill is not an attempt and "git" still has to reach the
    # cohort: two frames of one repository that read different providers are two
    # different frames (W8-T2).
    providers: dict[str, str] = field(default_factory=dict)


def build(store: Store, clock: str, repo_id: str, policy: str, marks: Marks) -> Series:
    """The entry point series.build dispatches to. Design 6.12.

    `marks` is spec 14.6's policy boundary (W6-T2), read once before any row exists.

    series_changes is imported here rather than at the top for the reason series.py
    imports this module inside its own `build`: series_changes reads this file's
    vocabulary, so importing it at the top would be a cycle.
    """
    if clock == "attempt":
        return attempt_series(store, repo_id, policy, marks)
    from telltale import series_changes

    return series_changes.change_series(store, repo_id, policy, marks)


# -- reading the lineage --------------------------------------------------------------


def lineage(store: Store, repo_id: str) -> Lineage:
    """Every capture of one repository, split into attempts and named refusals.

    The identity read is one observation per capture, resolved through the
    `primary_observation` the capture_start and capture_end lifecycle activities
    already carry, and every one of them is fetched in a single `observations_by_id`
    call: a lineage of 37 captures makes one query rather than 37 payload scans of a
    table holding a million rows.
    """
    captures = [row for row in store.captures() if row["repo_id"] == repo_id]
    if not captures:
        raise Refused(f"{repo_id}: no capture in this store carries this repo_id")
    found = Lineage(repo_id=repo_id)
    pending: list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]] = []
    wanted: list[str] = []
    for capture in captures:
        capture_id = str(capture["capture_id"])
        rows: list[Mapping[str, Any]] = list(store.activities(capture_id))
        if not rows:
            found.dropped.append(
                {"key": capture_id, "reason": "no activities: run `telltale rebuild`"}
            )
            continue
        found.activities[capture_id] = rows
        found.providers[capture_id] = str(capture["provider"] or "")
        pending.append((capture, rows))
        wanted += _primaries(rows)
    payloads = {
        str(row["observation_id"]): dict(row["payload"])
        for row in store.observations_by_id(wanted)
    }
    for owner, rows in pending:
        _add(found, owner, rows, payloads)
    found.attempts.sort(key=lambda one: (one.started_at, one.capture_id))
    return found


def _primaries(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """The observation ids of the capture_start and capture_end lifecycle activities."""
    return [
        str(row["fields"]["primary_observation"])
        for row in rows
        if row["activity_type"] == "lifecycle"
        and row["fields"].get("event") in ("capture_start", "capture_end")
        and row["fields"].get("primary_observation")
    ]


def _add(
    found: Lineage,
    capture: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    payloads: Mapping[str, Mapping[str, Any]],
) -> None:
    """Turn one capture into an Attempt, or record why it is not one."""
    capture_id = str(capture["capture_id"])
    named = _identity(rows, payloads)
    if isinstance(named, str):
        found.dropped.append({"key": capture_id, "reason": named})
        return
    lifecycle = _capture_row(rows)
    if lifecycle is None:
        found.dropped.append(
            {"key": capture_id, "reason": "no capture lifecycle activity"}
        )
        return
    fields = dict(lifecycle["fields"])
    task_id, attempt = named
    found.attempts.append(
        Attempt(
            capture_id=capture_id,
            task_id=task_id,
            attempt=attempt,
            started_at=str(lifecycle["started_at"]),
            provider=str(fields.get("provider") or capture["provider"] or ""),
            activities=[row for row in rows if row["activity_type"] in _READ_TYPES],
            coverage=dict(fields.get("coverage") or {}),
            fingerprint=fields.get("environment_fingerprint_id"),
            duration_ms=_duration(rows, payloads),
        )
    )


def _capture_row(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The lifecycle row carrying the measured coverage map. See series._capture."""
    for row in rows:
        fields = dict(row["fields"])
        if (
            row["activity_type"] == "lifecycle"
            and fields.get("event") == "capture"
            and isinstance(fields.get("coverage"), dict)
        ):
            return row
    return None


def _identity(
    rows: Sequence[Mapping[str, Any]],
    payloads: Mapping[str, Mapping[str, Any]],
) -> tuple[str, int] | str:
    """(task_id, attempt) for this capture, or the sentence saying why it is not one.

    Both surfaces are read and both have to agree. A capture carrying two different
    identities is refused by name: the whole point of an attempt clock is that a row is
    ONE attempt, and picking the first of two would invent that.
    """
    stated = [
        *(
            _pair(dict(row["fields"]))
            for row in rows
            if row["activity_type"] == "correlation"
        ),
        *(_pair(payloads.get(one, {})) for one in _primaries(rows)),
    ]
    named = {pair for pair in stated if pair is not None}
    if not named:
        return "no task_id and attempt: not an attempt of this lineage"
    if len(named) > 1:
        listed = ", ".join(f"{task}/{index}" for task, index in sorted(named))
        return f"two attempt identities on one capture ({listed}): refusing to pick"
    return named.pop()


def _pair(fields: Mapping[str, Any]) -> tuple[str, int] | None:
    task = fields.get("task_id")
    attempt = fields.get("attempt")
    if not isinstance(task, str) or not task:
        return None
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        return None
    return (task, attempt)


def _duration(
    rows: Sequence[Mapping[str, Any]], payloads: Mapping[str, Mapping[str, Any]]
) -> int | None:
    """The launcher's own measurement, off the telltale.capture_ended payload.

    Not the difference of two activity timestamps: a capture still in flight has an
    ended_at that is only its last arrival, and subtracting it would report a running
    session's elapsed time so far as its duration. A capture that has not ended has
    none, and None is what that says.
    """
    for row in rows:
        if row["activity_type"] != "lifecycle":
            continue
        if row["fields"].get("event") != "capture_end":
            continue
        payload = payloads.get(str(row["fields"].get("primary_observation") or ""), {})
        found = payload.get("duration_ms")
        if isinstance(found, int) and not isinstance(found, bool):
            return found
    return None


def find(found: Lineage, task_id: str, attempt: int) -> list[Attempt]:
    """Every attempt of this lineage that IS (task_id, attempt). A list, not one.

    A list because the caller has to see the cardinality: `telltale outcome` refuses on
    zero and refuses on two, and a function that returned the first of two would have
    made that decision silently.
    """
    return [
        one
        for one in found.attempts
        if one.task_id == task_id and one.attempt == attempt
    ]


# -- what both clocks count -----------------------------------------------------------


def of_type(rows: Iterable[Mapping[str, Any]], *kinds: str) -> list[Mapping[str, Any]]:
    return [row for row in rows if row["activity_type"] in kinds]


def total(rows: Sequence[Mapping[str, Any]], name: str) -> float | None:
    """The total over the rows that stated the field, or None when none did.

    A SUM over an empty set is null, which is measures.py's rule and the same one here:
    with no request stating a token count there is no token count, and 0 would be a
    measurement nobody made. A COUNT over an empty set is 0, and is spelled `len`.
    """
    measured = [
        value
        for value in (compiler.number(row["fields"].get(name)) for row in rows)
        if value is not None
    ]
    return sum(measured) if measured else None


def distinct(rows: Sequence[Mapping[str, Any]], name: str) -> int | None:
    """Distinct values of one field, with the three-answer rule of spec 13.3.

    No activities at all is 0. Activities that name the field is the count. Activities
    where the field was never observable is None: "it read nothing" and "what it read
    could not be seen" are different statements.
    """
    if not rows:
        return 0
    values = {str(row["fields"][name]) for row in rows if name in row["fields"]}
    return len(values) if values else None


def _last_status(rows: Sequence[Mapping[str, Any]], kind: str) -> float | None:
    """The most recent outcome of one kind as 0/1, or None when there is none.

    Most recent by clock position, which is the order everything here reads in: an
    outcome that revises an earlier one is the answer, and a kind nobody posted leaves
    the cell unknown rather than reading as a failure.
    """
    found = outcomes.of_kind(rows, kind)
    return outcomes.passed(found[-1]["fields"].get("status")) if found else None


def _reviews(rows: Sequence[Mapping[str, Any]]) -> float | None:
    """How many adversarial reviews failed, or None when no review was posted."""
    found = outcomes.of_kind(rows, "adversarial_review")
    if not found:
        return None
    return sum(1 for row in found if outcomes.passed(row["fields"].get("status")) == 0)


def worst_coverage(maps: Iterable[Mapping[str, str]]) -> dict[str, str]:
    """Per capability, the weakest cell any capture in this frame recorded.

    One column is one word for a whole lineage, so a frame holding a capture that could
    not see commands has a commands-backed column no stronger than that capture.
    """
    rank = compiler.COVERAGE_RANK.index
    combined: dict[str, str] = {}
    for one in maps:
        for name, cell in one.items():
            current = combined.get(name)
            if current is None or rank(cell) > rank(current):
                combined[name] = cell
    return combined


def _measured(cells: Sequence[float | None]) -> str:
    """A column with no capability behind it, judged by its own cells.

    See the module docstring: unavailable, observed or partial, and never a zero
    standing in for an unknown.
    """
    known = [cell for cell in cells if cell is not None]
    if not known:
        return "unavailable"
    return "observed" if len(known) == len(cells) else "partial"


# -- the frame both clocks fill -------------------------------------------------------


@dataclass
class Frame:
    """One clock's rows before coverage, the two policies and the id are applied.

    `rows` holds every column but the last: `env_changed` is a function of the row
    fingerprints, which are not known until every row exists, exactly as on the
    request clock.
    """

    rows: list[list[float | None]] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    ends: list[str] = field(default_factory=list)
    fingerprints: list[str | None] = field(default_factory=list)
    provenance: list[list[str]] = field(default_factory=list)
    flags: list[list[str]] = field(default_factory=list)
    coverage: dict[str, str] = field(default_factory=dict)


def _specs(
    table: Sequence[tuple[str, str, tuple[str, ...]]],
    coverage: Mapping[str, str],
    rows: Sequence[Sequence[float | None]],
    env: str,
    floor: Sequence[str] = (),
) -> list[ColumnSpec]:
    """One ColumnSpec per column, with the coverage word each of them earned.

    `floor` names columns whose capability word must additionally be weakened by their
    own cells, and only the change clock passes any: a capability map is measured PER
    CAPTURE, and a change no capture landed (W8-T2) belongs to no capture the map
    describes, so on a frame holding one the capability word overstates the column. The
    rule only ever weakens, never upgrades, which is why it is a floor and not a
    replacement: a column built from `partial` usage does not become `observed` because
    every row happened to get a number.
    """
    out: list[ColumnSpec] = []
    for index, (name, unit, capabilities) in enumerate(table):
        cells = [row[index] for row in rows]
        if name == "env_changed":
            word = env
        elif capabilities:
            word = compiler.worst(coverage, capabilities)
            if name in floor:
                word = max(word, _measured(cells), key=compiler.COVERAGE_RANK.index)
        else:
            word = _measured(cells)
        out.append(
            ColumnSpec(name=name, unit=unit, role="past_covariate", coverage=word)
        )
    return out


def running_max(ends: Sequence[str]) -> list[str]:
    """Each row's end, never before the row's own predecessor. See the docstring."""
    out: list[str] = []
    for end in ends:
        out.append(max(end, out[-1]) if out else end)
    return out


def end_of(rows: Iterable[Mapping[str, Any]]) -> str:
    return max((compiler.position(row) for row in rows), default="")


def ids_of(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    return sorted({str(row["activity_id"]) for row in rows})


def assemble(
    clock: str,
    cohort: dict[str, Any],
    table: Sequence[Any],
    built: Frame,
    policy: str,
    floor: Sequence[str] = (),
) -> Series:
    """The last third of both builds: env_changed, coverage, the policies and the id."""
    env, cells, changepoints = compiler.env_column(built.fingerprints)
    table_rows = [[*row, cell] for row, cell in zip(built.rows, cells, strict=True)]
    specs = _specs(table, built.coverage, table_rows, env, floor)
    rows = compiler.blank_unobservable(table_rows, specs)
    if policy == "refuse":
        compiler.refuse_on_gaps(rows, specs, built.keys)
    return Series(
        series_id=compiler.series_id(
            clock, cohort, specs, compiler.REDUCER_VERSION, rows
        ),
        clock=clock,
        cohort=cohort,
        columns=specs,
        rows=rows,
        row_meta=[
            RowMeta(
                row_key=key,
                row_end_ts=end,
                env_fingerprint_id=fingerprint,
                provenance=ids,
                flags=flags,
            )
            for key, end, fingerprint, ids, flags in zip(
                built.keys,
                running_max(built.ends),
                built.fingerprints,
                built.provenance,
                built.flags,
                strict=True,
            )
        ],
        changepoints=sorted(set(changepoints) | regimes.boundaries(cohort)),
        missingness_policy=policy,
        reducer_version=compiler.REDUCER_VERSION,
    )


def cohort_of(
    found: Lineage,
    captures: Sequence[str],
    attempts: Sequence[Attempt],
    providers: Sequence[str] = (),
) -> dict[str, Any]:
    """What identifies a lineage frame, and what it refused to read.

    `captures` is in the cohort because `check` has to resolve a provenance id back to
    an activity, and an activity has no position until the capture that owns it is
    read. `dropped` is in it because a frame that silently skipped a capture is a frame
    nobody can audit, and because a drop that later resolves (a capture nobody has
    rebuilt yet) SHOULD give a different series id.

    `providers` names the providers of captures that are NOT attempts but did carry a
    row. Only the change clock has any: a git history backfill lands rows and lands no
    attempt, and a cohort saying `["claude"]` over a frame half of which came from
    `git` would be a cohort nobody can compare against the next one.
    """
    return {
        "repo_id": found.repo_id,
        "providers": sorted(
            {one.provider for one in attempts if one.provider} | set(providers)
        ),
        "captures": list(captures),
        "dropped": sorted(found.dropped, key=lambda one: one["key"]),
    }


# -- the attempt clock ----------------------------------------------------------------


def attempt_series(store: Store, repo_id: str, policy: str, marks: Marks) -> Series:
    """One row per attempt of one repository, in start order. Design 6.12."""
    found = lineage(store, repo_id)
    if not found.attempts:
        raise Refused(
            f"{repo_id}: no capture carries a task_id and an attempt;"
            f" {len(found.dropped)} capture(s) were read and none was an attempt"
        )
    built = Frame(coverage=worst_coverage(one.coverage for one in found.attempts))
    for one in found.attempts:
        built.rows.append(_attempt_row(one))
        built.keys.append(one.capture_id)
        built.ends.append(end_of(one.activities))
        built.fingerprints.append(one.fingerprint)
        built.provenance.append(ids_of(one.activities))
        built.flags.append([])
    cohort = cohort_of(
        found, [one.capture_id for one in found.attempts], found.attempts
    )
    regimes.segment(marks, built, cohort, running_max(built.ends))
    return assemble("attempt", cohort, _ATTEMPT_COLUMNS, built, policy)


def _attempt_row(one: Attempt) -> list[float | None]:
    """The eight columns design 6.12 lists before env_changed, in its order."""
    requests = of_type(one.activities, "model_request")
    return [
        one.attempt,
        len(requests),
        total(requests, "input_tokens"),
        len(of_type(one.activities, "compaction")),
        one.duration_ms,
        _last_status(one.activities, "mechanical_verification"),
        _reviews(one.activities),
        _last_status(one.activities, "merge_decision"),
    ]
