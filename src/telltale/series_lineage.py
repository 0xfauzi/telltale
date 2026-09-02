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

from telltale import measures_intervals as walks
from telltale import series as compiler
from telltale.correlate import as_activity
from telltale.model import ColumnSpec, RowMeta, Series

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

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

# The two rungs that are evidence gathered AFTER the capture, or no evidence at all.
# A row built on one of them is flagged rather than dropped: it is a real commit and a
# reader may want it, and the backtester excludes the flag by default (W3-T2).
LOW_CONFIDENCE_RUNGS = ("tree_match_after", "heuristic")
LOW_CONFIDENCE = "low_confidence"

# What an outcome STATUS means. `telltale outcome --status` takes free text, because
# an orchestrator's vocabulary is its own, so this table is the one place a word
# becomes a number. A word it does not carry leaves the cell None: an unrecognised
# status is not a pass, and it is not a fail either.
_PASSED = frozenset(
    {"pass", "passed", "ok", "success", "succeeded", "merged", "accepted", "approved"}
)
_FAILED = frozenset(
    {"fail", "failed", "error", "rejected", "reverted", "abandoned", "blocked"}
)

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
# last on both clocks and is appended by `_assemble` from the row fingerprints.
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

_CHANGE_COLUMNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("files_changed", "files", ()),
    ("lines_added", "lines", ()),
    ("lines_removed", "lines", ()),
    ("subsystems_touched", "directories", ()),
    ("test_files_changed", "files", ()),
    ("dependency_delta", "flag", ()),
    ("attempts_to_land", "attempts", ()),
    ("fresh_input_tokens_total", "tokens", ("request_usage",)),
    ("cache_read_tokens_total", "tokens", ("request_usage",)),
    ("compactions", "compactions", ("compaction",)),
    ("verification_cycles", "runs", ("commands",)),
    ("edit_turnover_ratio", "edits_per_file", ("file_paths",)),
    ("stable_state_intervals", "intervals", ()),
    ("unique_files_read", "files", ("file_paths",)),
    ("env_changed", "flag", ()),
)

# The three change columns no stored field can fill, and what each one needs.
# telltale.repo.commit carries sha, parents, tree, committed_ts, files_changed,
# additions, deletions and link_confidence (design 6.3, and measured on the owner's
# store on 2026-09-02: those eight keys and no others, on all four stored commits).
# None of them is a path, and design 6.12 forbids reading git at build time, so these
# three are None with coverage unavailable until a repo.commit payload carries paths.
MISSING_FIELD = {
    "subsystems_touched": "telltale.repo.commit carries no per-file paths",
    "test_files_changed": "telltale.repo.commit carries no per-file paths",
    "dependency_delta": "telltale.repo.commit carries no per-file paths",
}


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


def build(store: Store, clock: str, repo_id: str, policy: str) -> Series:
    """The entry point series.build dispatches to. Design 6.12."""
    if clock == "attempt":
        return attempt_series(store, repo_id, policy)
    return change_series(store, repo_id, policy)


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


def _of_type(rows: Iterable[Mapping[str, Any]], *kinds: str) -> list[Mapping[str, Any]]:
    return [row for row in rows if row["activity_type"] in kinds]


def _sum(rows: Sequence[Mapping[str, Any]], name: str) -> float | None:
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


def _distinct(rows: Sequence[Mapping[str, Any]], name: str) -> int | None:
    """Distinct values of one field, with the three-answer rule of spec 13.3.

    No activities at all is 0. Activities that name the field is the count. Activities
    where the field was never observable is None: "it read nothing" and "what it read
    could not be seen" are different statements.
    """
    if not rows:
        return 0
    values = {str(row["fields"][name]) for row in rows if name in row["fields"]}
    return len(values) if values else None


def _passed(status: Any) -> int | None:
    word = str(status or "").strip().lower()
    if word in _PASSED:
        return 1
    if word in _FAILED:
        return 0
    return None


def _outcomes(rows: Sequence[Mapping[str, Any]], kind: str) -> list[Mapping[str, Any]]:
    return [
        row for row in _of_type(rows, "outcome") if row["fields"].get("kind") == kind
    ]


def _last_status(rows: Sequence[Mapping[str, Any]], kind: str) -> float | None:
    """The most recent outcome of one kind as 0/1, or None when there is none.

    Most recent by clock position, which is the order everything here reads in: an
    outcome that revises an earlier one is the answer, and a kind nobody posted leaves
    the cell unknown rather than reading as a failure.
    """
    found = sorted(_outcomes(rows, kind), key=compiler.order)
    return _passed(found[-1]["fields"].get("status")) if found else None


def _reviews(rows: Sequence[Mapping[str, Any]]) -> float | None:
    """How many adversarial reviews failed, or None when no review was posted."""
    found = _outcomes(rows, "adversarial_review")
    if not found:
        return None
    return sum(1 for row in found if _passed(row["fields"].get("status")) == 0)


def _worst_coverage(maps: Iterable[Mapping[str, str]]) -> dict[str, str]:
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
class _Frame:
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
) -> list[ColumnSpec]:
    out: list[ColumnSpec] = []
    for index, (name, unit, capabilities) in enumerate(table):
        if name == "env_changed":
            word = env
        elif capabilities:
            word = compiler.worst(coverage, capabilities)
        else:
            word = _measured([row[index] for row in rows])
        out.append(
            ColumnSpec(name=name, unit=unit, role="past_covariate", coverage=word)
        )
    return out


def _running_max(ends: Sequence[str]) -> list[str]:
    """Each row's end, never before the row's own predecessor. See the docstring."""
    out: list[str] = []
    for end in ends:
        out.append(max(end, out[-1]) if out else end)
    return out


def _end(rows: Iterable[Mapping[str, Any]]) -> str:
    return max((compiler.position(row) for row in rows), default="")


def _ids(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    return sorted({str(row["activity_id"]) for row in rows})


def _assemble(
    clock: str, cohort: dict[str, Any], table: Sequence[Any], built: _Frame, policy: str
) -> Series:
    """The last third of both builds: env_changed, coverage, the policies and the id."""
    env, cells, changepoints = compiler.env_column(built.fingerprints)
    table_rows = [[*row, cell] for row, cell in zip(built.rows, cells, strict=True)]
    specs = _specs(table, built.coverage, table_rows, env)
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
                _running_max(built.ends),
                built.fingerprints,
                built.provenance,
                built.flags,
                strict=True,
            )
        ],
        changepoints=changepoints,
        missingness_policy=policy,
        reducer_version=compiler.REDUCER_VERSION,
    )


def _cohort(
    found: Lineage, captures: Sequence[str], attempts: Sequence[Attempt]
) -> dict[str, Any]:
    """What identifies a lineage frame, and what it refused to read.

    `captures` is in the cohort because `check` has to resolve a provenance id back to
    an activity, and an activity has no position until the capture that owns it is
    read. `dropped` is in it because a frame that silently skipped a capture is a frame
    nobody can audit, and because a drop that later resolves (a capture nobody has
    rebuilt yet) SHOULD give a different series id.
    """
    return {
        "repo_id": found.repo_id,
        "providers": sorted({one.provider for one in attempts if one.provider}),
        "captures": list(captures),
        "dropped": sorted(found.dropped, key=lambda one: one["key"]),
    }


# -- the attempt clock ----------------------------------------------------------------


def attempt_series(store: Store, repo_id: str, policy: str) -> Series:
    """One row per attempt of one repository, in start order. Design 6.12."""
    found = lineage(store, repo_id)
    if not found.attempts:
        raise Refused(
            f"{repo_id}: no capture carries a task_id and an attempt;"
            f" {len(found.dropped)} capture(s) were read and none was an attempt"
        )
    built = _Frame(coverage=_worst_coverage(one.coverage for one in found.attempts))
    for one in found.attempts:
        built.rows.append(_attempt_row(one))
        built.keys.append(one.capture_id)
        built.ends.append(_end(one.activities))
        built.fingerprints.append(one.fingerprint)
        built.provenance.append(_ids(one.activities))
        built.flags.append([])
    cohort = _cohort(found, [one.capture_id for one in found.attempts], found.attempts)
    return _assemble("attempt", cohort, _ATTEMPT_COLUMNS, built, policy)


def _attempt_row(one: Attempt) -> list[float | None]:
    """The eight columns design 6.12 lists before env_changed, in its order."""
    requests = _of_type(one.activities, "model_request")
    return [
        one.attempt,
        len(requests),
        _sum(requests, "input_tokens"),
        len(_of_type(one.activities, "compaction")),
        one.duration_ms,
        _last_status(one.activities, "mechanical_verification"),
        _reviews(one.activities),
        _last_status(one.activities, "merge_decision"),
    ]


# -- the change clock -----------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One commit of the lineage, its rung, and the attempts that landed it."""

    sha: str
    committed_ts: str
    payload: dict[str, Any]
    rung: str
    commits: list[Mapping[str, Any]]
    landed_by: list[Attempt]

    @property
    def activities(self) -> list[Mapping[str, Any]]:
        """Everything this row reads: the commit rows and the landing attempts."""
        return [
            *self.commits,
            *(row for one in self.landed_by for row in one.activities),
        ]

    @property
    def fingerprint(self) -> str | None:
        """The environment it landed in, or None when the landing attempts disagree."""
        found = {one.fingerprint for one in self.landed_by if one.fingerprint}
        return found.pop() if len(found) == 1 else None


def change_series(store: Store, repo_id: str, policy: str) -> Series:
    """One row per commit linked to a capture of this repository. Design 6.12."""
    found = lineage(store, repo_id)
    changes, dropped = _changes(found)
    found.dropped += dropped
    if not changes:
        raise Refused(
            f"{repo_id}: no commit is linked to any capture of this repository."
            " Run `telltale sessions --link-commits` inside the repository first."
        )
    built = _Frame(
        coverage=_worst_coverage(
            one.coverage for change in changes for one in change.landed_by
        )
    )
    for change in changes:
        built.rows.append(_change_row(change))
        built.keys.append(change.sha)
        built.ends.append(_end(change.activities))
        built.fingerprints.append(change.fingerprint)
        built.provenance.append(_ids(change.activities))
        built.flags.append(
            [LOW_CONFIDENCE] if change.rung in LOW_CONFIDENCE_RUNGS else []
        )
    captures = sorted(
        {str(row["capture_id"]) for change in changes for row in change.activities}
    )
    attempts = [one for change in changes for one in change.landed_by]
    cohort = _cohort(found, captures, attempts)
    return _assemble("change", cohort, _CHANGE_COLUMNS, built, policy)


def _changes(found: Lineage) -> tuple[list[Change], list[dict[str, str]]]:
    """Every commit of the lineage, one row per sha, at the best rung it reached.

    Grouped by sha because two captures may each record one commit: duplicate is not
    one, and a change that landed once is one row whatever recorded it.
    """
    by_sha: dict[str, list[Mapping[str, Any]]] = {}
    for rows in found.activities.values():
        for row in _of_type(rows, "repo_commit"):
            by_sha.setdefault(str(row["fields"].get("sha") or ""), []).append(row)
    changes: list[Change] = []
    dropped: list[dict[str, str]] = []
    for sha, rows in by_sha.items():
        rung = _rung(rows)
        if not sha or rung is None:
            dropped.append({"key": sha or "(no sha)", "reason": _NO_RUNG})
            continue
        payload = dict(rows[0]["fields"])
        changes.append(
            Change(
                sha=sha,
                committed_ts=str(payload.get("committed_ts") or ""),
                payload=payload,
                rung=rung,
                commits=rows,
                landed_by=_landed_by(found, rows),
            )
        )
    changes.sort(key=lambda one: (one.committed_ts, one.sha))
    return changes, dropped


_NO_RUNG = "no sha, or a link_confidence that is not on spec 12.3's ladder"


def _rung(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """The best rung any of these activities recorded, or None for no rung at all."""
    words = [
        str(row["fields"].get("link_confidence") or "")
        for row in rows
        if str(row["fields"].get("link_confidence") or "") in LADDER
    ]
    return min(words, key=LADDER.index) if words else None


def _landed_by(found: Lineage, rows: Sequence[Mapping[str, Any]]) -> list[Attempt]:
    """The attempts whose captures recorded this commit, in start order."""
    carriers = {str(row["capture_id"]) for row in rows}
    return [one for one in found.attempts if one.capture_id in carriers]


def _change_row(change: Change) -> list[float | None]:
    """The fourteen columns design 6.12 lists before env_changed, in its order.

    The three None literals are the columns of MISSING_FIELD, and they are None here
    rather than absent so that the row has design 6.12's shape: `_measured` then makes
    the whole column unavailable, which is the statement "no surface carried it".
    """
    landed = [row for one in change.landed_by for row in one.activities]
    attempts = [one.attempt for one in change.landed_by]
    requests = _of_type(landed, "model_request")
    return [
        compiler.number(change.payload.get("files_changed")),
        compiler.number(change.payload.get("additions")),
        compiler.number(change.payload.get("deletions")),
        None,
        None,
        None,
        max(attempts) if attempts else None,
        _sum(requests, "input_tokens"),
        _sum(requests, "cache_read_tokens"),
        len(_of_type(landed, "compaction")) if landed else None,
        len(_of_type(landed, "verification_run")) if landed else None,
        _turnover(_of_type(landed, "file_edit")),
        _intervals(landed),
        _distinct(_of_type(landed, "file_read"), "file_path") if landed else None,
    ]


def _turnover(edits: Sequence[Mapping[str, Any]]) -> float | None:
    """Edits per distinct file edited. Design 6.12 names the column, not its formula.

    W3-T1 chose this one and says so: the count of file_edit activities over the count
    of distinct paths they name, which is 1.0 when every file was written once and
    grows as a file is rewritten. None when no edit named a path, for the reason
    `_distinct` gives, and None when there was no edit at all: a ratio over no edits is
    not 0, it is undefined.
    """
    files = _distinct(edits, "file_path")
    return len(edits) / files if files else None


def _intervals(landed: Sequence[Mapping[str, Any]]) -> float | None:
    """Stable-state work intervals, spec 13.5, over the attempts that landed a change.

    measures_intervals holds the one definition of an interval in this codebase and it
    is called rather than copied. Without a repo_snapshot there is no diff fingerprint,
    so "the fingerprint did not move" is not something these captures could have seen,
    and the answer is None rather than one interval covering the whole of them.
    """
    if not _of_type(landed, "repo_snapshot"):
        return None
    return len(walks.intervals([as_activity(row) for row in landed]))
