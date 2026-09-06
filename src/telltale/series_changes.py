"""The change clock: one row per commit of a repository's lineage. Design 6.12.

series_lineage.py reads a repository into attempts and folds the attempt clock; this
file folds the other one. The cut is the third the 800-line ratchet has forced out of
that module, after series_paths.py and series_outcomes.py, and it is the right cut on
its own: everything here is a decision about what a COMMIT is a row of, while
series_lineage.py decides what a capture is and what both clocks count.

Five rules hold this clock in place, and each is a mechanism rather than a convention.

  A git-history import defines the TRACKED BASE, and the rows are its commits. W8-T4.
  A capture's own commit is a row only where it landed on that base, by its sha or, on
  a squash-merge workflow, by its tree; anything else is dropped by name and counted,
  never ordered in among the commits that did land. Without such an import there is no
  base and the rows are the captured commits, as they were. See
  series_lineage.tracked_base for what the alternative measured.

  One sha is one row, at the best rung any recorder gave it. Two captures may each
  record one commit and W8-T2 added a third recorder that records every commit of the
  history, so a sha the launcher linked AND the git backfill wrote reaches `_rung` as
  two activities and leaves it as one row at the launcher's rung. Duplicate is not one.

  A commit no capture landed is still a row. Its six repository columns are read off the
  payload exactly as any other row's are, its seven process columns are None with the
  sentence NO_CAPTURE against each, and it carries the row flag `uncaptured`. The
  alternative is a lineage that reports only the commits somebody happened to record a
  session for, which is a sample of the repository chosen by the tooling.

  A row's process columns come from whoever RECORDED it. The attempts that landed the
  change when there are any, and otherwise the captures that recorded it at a rung of
  spec 12.3's ladder, attempt or not: a day-to-day session run behind the daemon names
  no task and no attempt, and before W9-T2 it contributed nothing to the row holding
  the commit it had made. `attempts_to_land` stays the attempt ordinal and stays None
  for such a row, because how many sessions tried this change before is not something
  a session that never numbered itself can be read as answering. See
  docs/design/amendments/W9-T2.md.

  A delayed label is carried where it is decidable. `rework_within_3` is decided three
  commits after the row it is about, so a backtest context that ends at origin o holds
  cells that commits at or after o decided. `rework_within_3_lag3` is the same label on
  the row whose own commit time closed its window, and that is the column E16 forecasts.
  See docs/design/amendments/W8-T2.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from telltale import measures_intervals as walks
from telltale import series as compiler
from telltale import series_lineage as lineages
from telltale import series_outcomes as outcomes
from telltale import series_paths
from telltale import series_regime as regimes
from telltale.correlate import as_activity
from telltale.series_lineage import (
    CAPTURED_SHA,
    LADDER,
    LOW_CONFIDENCE,
    LOW_CONFIDENCE_RUNGS,
    NO_CAPTURE,
    NOT_AN_ATTEMPT,
    PROCESS_COLUMNS,
    READ_TYPES,
    UNCAPTURED,
    UNLINKED,
    Attempt,
    Frame,
    Lineage,
    Refused,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.model import Series
    from telltale.series_regime import Marks
    from telltale.store import Store

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
    # The three post-merge columns of design 6.12's candidate protocol (W5-T1). Their
    # rule is series_outcomes.columns; like the path columns they rest on no provider
    # capability, so `_measured` takes their coverage word from their own cells.
    ("merge_verification_ms", "ms", ()),
    ("merge_verification_failed", "flag", ()),
    ("rework_within_3", "flag", ()),
    # W8-T2. Appended by `change_series` after every row exists, because row j reads
    # change j - 3's label; see series_outcomes.lagged for why that is the cell E16
    # forecasts and the source cell is not.
    ("rework_within_3_lag3", "flag", ()),
    ("env_changed", "flag", ()),
)

# The three change columns built from a commit's paths come from series_paths.columns,
# off the per_file list W3-T4 put on the telltale.repo.commit payload. A commit
# recorded before W3-T4 carries no list and its three cells stay None.


@dataclass(frozen=True)
class Recorder:
    """One capture that recorded a commit at a rung of the ladder and is not an attempt.

    The five fields an Attempt carries about the CAPTURE behind it, read off the same
    lifecycle row (`series_lineage.capture_row`), and none of the four an attempt
    identity supplies. That split is the point: a recorder can fill the seven process
    columns, because they are measurements of a session, and it can fill neither
    `attempts_to_land` nor the post-merge columns, because both are keyed on an
    identity it never stated.
    """

    capture_id: str
    started_at: str
    provider: str
    activities: list[Mapping[str, Any]]
    coverage: dict[str, str]
    fingerprint: str | None


@dataclass(frozen=True)
class Change:
    """One commit of the lineage, its rung, and the captures that recorded it."""

    sha: str
    committed_ts: str
    payload: dict[str, Any]
    rung: str
    commits: list[Mapping[str, Any]]
    landed_by: list[Attempt]
    # The captures that recorded this commit, or its tree twin, at a rung of spec 12.3's
    # ladder and named no attempt, in start order. A git history import is not among
    # them: it writes UNLINKED, which is not a rung, and folding its activities into a
    # row would turn `uncaptured` into a process measurement of the importer. W9-T2.
    recorded_by: list[Recorder] = field(default_factory=list)
    # Every activity of the capture(s) that RECORDED the commit, attempt or not. Read
    # only when nothing landed the change: a git history backfill writes the commit and
    # the outcomes about it into one capture, and the sha is the only name they share.
    own: list[Mapping[str, Any]] = field(default_factory=list)
    # The sha a capture recorded, when the row was reached through its TREE rather than
    # through its sha. `sha` above is the base commit's and is the row's key; this is
    # the branch commit the session actually made, and without it a reader has no way
    # back from the row to the session's own history. W8-T4.
    captured_sha: str | None = None

    @property
    def uncaptured(self) -> bool:
        """No capture landed this change: it is a row of the repository's history.

        Both halves are required. The rung says the recorder made no link, and the
        empty `landed_by` says no attempt recorded the sha either; a commit the
        launcher linked and the backfill also wrote is ONE row, at the launcher's rung,
        with the launcher's process columns and no flag. Duplicate is not one.
        """
        return self.rung == UNLINKED and not self.landed_by

    @property
    def process_from(self) -> Sequence[Attempt] | Sequence[Recorder]:
        """Whose session the seven process columns are folded over. W9-T2.

        The attempts that landed the change where there are any, so the five rows E16
        built from an attempt fold exactly what they folded. Otherwise the captures
        that recorded it, which is the whole of what day-to-day capture can offer: the
        commit is on the tracked base and a real session made it, and the alternative
        is a row that reports the repository half of a change whose process half is
        sitting in the store unread.

        Empty on an uncaptured row, and that is the same emptiness as before: nothing
        recorded that commit at a rung, so there is no session to fold.
        """
        return self.landed_by or self.recorded_by

    @property
    def activities(self) -> list[Mapping[str, Any]]:
        """Everything this row reads: the commit rows and the sessions folded into it.

        And, on an uncaptured row, the outcomes of its own capture that name its sha.
        Those cells are read from those activities, so the row has to say so: `end_of`
        takes the row's end from this list and `series check` resolves the provenance
        against that end, and a cell built from an activity the row does not name is a
        claim no check can test. A rework outcome is stamped at the REWORKING commit's
        time, which is later than this row's own commit, and the row's end moves with
        it: the label was not decidable before then, and row_end_ts says when a row
        closed rather than when its commit landed.
        """
        return [
            *self.commits,
            *(row for one in self.process_from for row in one.activities),
            *(outcomes.about(self.own, self.sha) if self.uncaptured else ()),
        ]

    @property
    def fingerprint(self) -> str | None:
        """The environment it landed in, or None when the sessions folded disagree.

        None also when the sessions folded carry none at all, which is what a daemon
        capture is: the fingerprint is of the process the launcher is about to start,
        and nothing launched a session the owner started themselves. So `env_changed`
        stays None on such a row rather than reading 0, which would be a claim that the
        environment did not move across a boundary nobody observed.
        """
        found = {one.fingerprint for one in self.process_from if one.fingerprint}
        return found.pop() if len(found) == 1 else None


def change_series(store: Store, repo_id: str, policy: str, marks: Marks) -> Series:
    """One row per commit any capture of this repository recorded. Design 6.12."""
    found = lineages.lineage(store, repo_id)
    changes, dropped, counts = _changes(found)
    found.dropped += dropped
    _renamed(found, changes)
    if not changes:
        raise Refused(
            f"{repo_id}: no commit is linked to any capture of this repository."
            " Run `telltale sessions --link-commits` inside the repository first."
        )
    # Over whatever each row folded, which is the attempts where there are any and the
    # recording captures otherwise: a column is one coverage word for the whole frame,
    # and reading it off the attempts alone would put an attempt's word on a column
    # filled by a capture the map does not describe. W9-T2.
    built = Frame(
        coverage=lineages.worst_coverage(
            one.coverage for change in changes for one in change.process_from
        )
    )
    unknown = _fill(built, changes)
    _lag(built, unknown)
    captures = sorted(
        {str(row["capture_id"]) for change in changes for row in change.activities}
    )
    attempts = [one for change in changes for one in change.landed_by]
    cohort = lineages.cohort_of(
        found,
        captures,
        attempts,
        [found.providers[one] for one in captures if found.providers.get(one)],
    )
    # Beside `dropped` and counted before the regime cut, because both are facts about
    # the LINEAGE rather than about the retained slice: `off_base_commits` is how many
    # commits a capture recorded that never landed on the tracked base, and a slice of
    # the rows did not make any of them land. `collapsed_links` is how many shas more
    # than one repo_commit activity named, which is the deduplication of W8-T2 made
    # countable: without it a reader cannot tell one row from two collapsed into one.
    cohort.update(counts)
    regimes.segment(marks, built, cohort, lineages.running_max(built.ends))
    _retained(cohort, built, changes)
    # Only on this clock: the path and post-merge columns are the ones whose unknown
    # cells have a cause a reader can act on, and `series build` prints the cohort
    # beside the coverage word each of them takes from those cells.
    cohort["unknown_columns"] = {
        **series_paths.unknown_columns(
            [name for name, _unit, _caps in _CHANGE_COLUMNS], built.rows
        ),
        **{name: _REASON_JOIN.join(why) for name, why in unknown.items()},
    }
    # The seven process columns are floored by their own cells once the frame holds a
    # row no capture landed: see lineages._specs. Only when it does, so that on a frame
    # of landed changes alone the `refuse` policy keeps its teeth against a hole in a
    # column a capture could see.
    floor = PROCESS_COLUMNS if cohort["uncaptured_rows"] else ()
    return lineages.assemble("change", cohort, _CHANGE_COLUMNS, built, policy, floor)


def _renamed(found: Lineage, changes: Sequence[Change]) -> None:
    """Say what a capture whose process columns were folded actually did. W9-T2.

    `lineage` drops every capture that named no attempt by NOT_AN_ATTEMPT, which on
    this clock is true and misleading in one sentence: the capture is not a row of the
    attempt clock AND it filled seven columns of a row of this one. The sentence is
    replaced only for a capture something was taken from, so a capture that recorded
    nothing keeps today's, and so does one whose change an attempt landed: the row
    folded the attempt, and nothing of that capture reached the frame.

    A fact about the LINEAGE and not about the retained slice, like `dropped` itself:
    it is computed before the regime cut, and a `--regime pre` build says the same
    thing about a capture as the whole-frame build does.
    """
    folded: dict[str, list[str]] = {}
    for change in changes:
        if change.landed_by:
            continue
        said = f"{change.sha} at {change.rung}"
        for one in change.recorded_by:
            folded.setdefault(one.capture_id, []).append(said)
    for row in found.dropped:
        what = folded.get(row["key"])
        if what is not None and row["reason"] == NOT_AN_ATTEMPT:
            row["reason"] = FOLDED.format(what=", ".join(what))


def _retained(cohort: dict[str, Any], built: Frame, changes: Sequence[Change]) -> None:
    """The three facts about the rows the frame KEPT, written into the cohort.

    Counted after the regime cut and off the row flags and keys rather than off
    `changes`: `--regime pre` keeps a slice of the rows, and a count taken before the
    cut would describe a frame the reader was not handed.

    `process_from_recording_captures` names its shas where the other two count rows,
    because it is what a reader has to go and look at: `attempts_to_land` is None on
    exactly these rows and on the uncaptured ones, and those are two different
    unknowns. W9-T2.
    """
    uncaptured = sum(1 for one in built.flags if UNCAPTURED in one)
    cohort["captured_rows"] = len(built.flags) - uncaptured
    cohort["uncaptured_rows"] = uncaptured
    recorded = {one.sha for one in changes if not one.landed_by and one.recorded_by}
    cohort["process_from_recording_captures"] = [
        key for key in built.keys if key in recorded
    ]


# Between two reasons one column's unknown cells have. A separator a sentence cannot
# contain, so a reader can split the field back apart.
_REASON_JOIN = " | "


def _fill(built: Frame, changes: Sequence[Change]) -> dict[str, list[str]]:
    """One row per change, and every DISTINCT reason a column went unknown.

    Distinct and in the order the rows gave them, never the last row's reason alone:
    one column's cells go unknown for different reasons on different rows, and a map
    that overwrote would tell a reader to go and post a duration for a verification
    nobody ran.
    """
    unknown: dict[str, list[str]] = {}
    times = [one.committed_ts for one in changes]
    for index, change in enumerate(changes):
        cells, reasons = _change_row(change, outcomes.deadline(times, index))
        for name, why in reasons.items():
            listed = unknown.setdefault(name, [])
            if why not in listed:
                listed.append(why)
        built.rows.append(cells)
        built.keys.append(change.sha)
        built.ends.append(lineages.end_of(change.activities))
        built.fingerprints.append(change.fingerprint)
        built.provenance.append(lineages.ids_of(change.activities))
        built.flags.append(_flags(change))
    return unknown


def _flags(change: Change) -> list[str]:
    """The words about a row that are not numbers a forecaster may read.

    Exclusive, and `uncaptured` first: a change at the UNLINKED rung is at no rung of
    spec 12.3's ladder at all, so it can never also be low_confidence, and a reader
    seeing both would be reading a guess that was never made.

    `captured_sha=<sha>` is not exclusive with either, and it is spelled `name=value`
    because RowMeta has no field for it and a row keyed on a base commit has to name
    the branch commit the session made. W8-T4.
    """
    if change.uncaptured:
        return [UNCAPTURED]
    flags = [LOW_CONFIDENCE] if change.rung in LOW_CONFIDENCE_RUNGS else []
    if change.captured_sha is not None:
        flags.append(f"{CAPTURED_SHA}={change.captured_sha}")
    return flags


def _lag(built: Frame, unknown: dict[str, list[str]]) -> None:
    """Append `rework_within_3_lag3`, the last column before env_changed. W8-T2.

    Row j's cell is change j - 3's label, which `outcomes.deadline` decided at change
    j's own commit time, so it is known when row j closes. Row j - 3's provenance is
    added to row j's for exactly that reason: the cell IS built from those activities,
    and `series check` resolves every provenance id against the row's end, so stating
    them is what makes the no-look-ahead claim of this column falsifiable rather than
    asserted. The base list is snapshotted first, so a row three further on inherits
    one row's ids rather than every earlier row's.
    """
    labels = [row[-1] for row in built.rows]
    base = [list(one) for one in built.provenance]
    for index, cell in enumerate(outcomes.lagged(labels)):
        built.rows[index].append(cell)
        if cell is None:
            continue
        source = base[index - outcomes.REWORK_TAIL]
        built.provenance[index] = sorted(set(base[index]) | set(source))
    if any(row[-1] is None for row in built.rows):
        unknown.setdefault(outcomes.POST_MERGE_COLUMNS[3], []).append(outcomes.NO_LAG)


def _changes(
    found: Lineage,
) -> tuple[list[Change], list[dict[str, str]], dict[str, int]]:
    """Every commit of the lineage, one row per sha, at the best rung it reached.

    Grouped by sha because two captures may each record one commit: duplicate is not
    one, and a change that landed once is one row whatever recorded it. The payload the
    row reads is the earliest one recorded AT THE WINNING RUNG rather than whichever
    the capture iteration reached first, so a sha the launcher linked and the git
    backfill also wrote reads the launcher's numbers on every build of the same store.

    With a tracked base (W8-T4, lineages.tracked_base) the rows are the base's commits
    and nothing else, and a captured commit joins the row its work landed as. What each
    half of such a row reads is the point of the split below:

      `named` is the activities that name the ROW's sha, and the payload comes from it.
      A tree twin is a different commit with the same tree, so its own diff against its
      own parent is not what landed on the base, and the six repository columns have to
      read the base commit's numbers. Where the twin and the row are the same sha there
      is no twin, `named` is every activity, and the rule is W8-T2's unchanged.

      `rows` is every activity that reaches the row, twin included, and the rung, the
      landing attempts and the provenance come from it. That is what carries a session
      to the change it landed.

    The third return is the two lineage-level counts the cohort states.
    """
    by_sha: dict[str, list[Mapping[str, Any]]] = {}
    for rows in found.activities.values():
        for row in lineages.of_type(rows, "repo_commit"):
            by_sha.setdefault(str(row["fields"].get("sha") or ""), []).append(row)
    dropped: list[dict[str, str]] = []
    ranked: dict[str, list[Mapping[str, Any]]] = {}
    for sha, rows in by_sha.items():
        if not sha or _rung(rows) is None:
            dropped.append({"key": sha or "(no sha)", "reason": _NO_RUNG})
            continue
        ranked[sha] = rows
    counts = {"collapsed_links": sum(1 for rows in ranked.values() if len(rows) > 1)}
    joined, base_dropped, counted = _joined(found, ranked)
    dropped += base_dropped
    counts.update(counted)
    recorders, refused = _recorders(found)
    dropped += refused
    changes = [
        _change(found, recorders, found_sha, rows, twin)
        for found_sha, rows, twin in _grouped(joined, ranked)
    ]
    changes.sort(key=lambda one: (one.committed_ts, one.sha))
    return changes, dropped, counts


def _joined(
    found: Lineage, ranked: Mapping[str, list[Mapping[str, Any]]]
) -> tuple[dict[str, str], list[dict[str, str]], dict[str, int]]:
    """sha -> the row it joins, the refusals, and what the cohort counts about them.

    Without a tracked base every sha is its own row and there is nothing to refuse,
    which is the whole of "a store with no git-history import builds what it built".
    """
    base = lineages.tracked_base(found)
    if base is None:
        return {sha: sha for sha in ranked}, [], {}
    joined, refused = lineages.attach(base, ranked)
    off_base = sum(1 for one in refused if one["reason"] == lineages.OFF_BASE)
    return joined, refused, {"off_base_commits": off_base}


def _grouped(
    joined: Mapping[str, str], ranked: Mapping[str, list[Mapping[str, Any]]]
) -> list[tuple[str, list[Mapping[str, Any]], str | None]]:
    """One (row sha, every activity that reaches it, the twin's sha) per row."""
    rows: dict[str, list[Mapping[str, Any]]] = {}
    twins: dict[str, str] = {}
    for sha in sorted(joined):
        target = joined[sha]
        rows.setdefault(target, []).extend(ranked[sha])
        if sha != target:
            twins[target] = sha
    return [(sha, found, twins.get(sha)) for sha, found in rows.items()]


def _change(
    found: Lineage,
    recorders: Mapping[str, Recorder],
    sha: str,
    rows: Sequence[Mapping[str, Any]],
    twin: str | None,
) -> Change:
    """One row: the base commit's payload, and the rung and capture that reached it."""
    named = [row for row in rows if str(row["fields"].get("sha") or "") == sha]
    payload = dict(_at_rung(named, _best(named, sha))["fields"])
    return Change(
        sha=sha,
        committed_ts=str(payload.get("committed_ts") or ""),
        payload=payload,
        rung=_best(rows, sha),
        commits=list(rows),
        landed_by=_landed_by(found, rows),
        recorded_by=_recorded_by(recorders, rows),
        own=_own(found, rows),
        captured_sha=twin,
    )


_NO_RUNG = (
    f"no sha, or a link_confidence that is neither {UNLINKED} nor on spec 12.3's ladder"
)


def _best(rows: Sequence[Mapping[str, Any]], sha: str) -> str:
    """The rung of a group `_changes` has already proved reaches one.

    A refusal rather than a fallback, and it is unreachable by construction: `ranked`
    holds only shas `_rung` answered for, and every group below is built out of it. If
    a caller ever reaches this, a row was about to be built at a rung nobody recorded,
    and a lineage that invented one rung would invent every number resting on it.
    """
    rung = _rung(rows)
    if rung is None:
        raise Refused(f"{sha}: {_NO_RUNG}")
    return rung


def _rung(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """The best rung any of these activities recorded, or None for no rung at all.

    A word on the ladder beats UNLINKED whoever wrote it, and that is the whole of the
    deduplication: a sha the launcher linked AND the git backfill recorded groups into
    one Change here, and comes out at the launcher's rung with the launcher's attempts.
    UNLINKED is returned only when it is the only word any recorder gave.
    """
    words = [str(row["fields"].get("link_confidence") or "") for row in rows]
    ranked = [word for word in words if word in LADDER]
    if ranked:
        return min(ranked, key=LADDER.index)
    return UNLINKED if UNLINKED in words else None


# What a capture that recorded a commit at a rung is refused by when the lifecycle row
# holding its coverage map is missing. Its process columns are not folded and no
# coverage word is assumed for it: a capability map is a measurement of one capture,
# and defaulting one would put a word on a column that nobody measured. Measured on the
# E16 store copy on 2026-09-07: 33 captures recorded a commit at a rung and all 33 have
# the row, as do all 1459 codex captures, so this refusal has never fired.
NO_COVERAGE = (
    "recorded a commit at a rung but has no capture lifecycle activity:"
    " its process columns cannot be read and no coverage is assumed for it"
)

# What the change clock says instead of NOT_AN_ATTEMPT about a capture whose process
# columns it folded. `{what}` is one `<sha> at <rung>` per row it filled.
FOLDED = "recorded {what}: process columns folded, attempts_to_land unknown"


def _recorders(found: Lineage) -> tuple[dict[str, Recorder], list[dict[str, str]]]:
    """Every capture of this lineage that recorded a commit at a rung and is no attempt.

    Once per capture rather than once per (capture, commit): the five fields are read
    off one lifecycle row, and a capture that recorded three commits would otherwise be
    refused three times over the same missing row.

    An attempt is left out because `landed_by` already carries it, with the identity a
    recorder does not have; nothing here changes what an attempt contributes.
    """
    attempts = {one.capture_id for one in found.attempts}
    carriers = sorted({
        capture_id
        for capture_id, rows in found.activities.items()
        if capture_id not in attempts
        for row in lineages.of_type(rows, "repo_commit")
        if str(row["fields"].get("link_confidence") or "") in LADDER
    })  # fmt: skip
    out: dict[str, Recorder] = {}
    refused: list[dict[str, str]] = []
    for capture_id in carriers:
        rows = found.activities[capture_id]
        lifecycle = lineages.capture_row(rows)
        if lifecycle is None:
            refused.append({"key": capture_id, "reason": NO_COVERAGE})
            continue
        fields = dict(lifecycle["fields"])
        out[capture_id] = Recorder(
            capture_id=capture_id,
            started_at=str(lifecycle["started_at"]),
            provider=str(fields.get("provider") or found.providers.get(capture_id, "")),
            activities=[one for one in rows if one["activity_type"] in READ_TYPES],
            coverage=dict(fields.get("coverage") or {}),
            fingerprint=fields.get("environment_fingerprint_id"),
        )
    return out, refused


def _recorded_by(
    recorders: Mapping[str, Recorder], rows: Sequence[Mapping[str, Any]]
) -> list[Recorder]:
    """The recorders that recorded THIS commit at a rung, in start order.

    The rung is read off the row's own activities rather than taken from the capture,
    which is what makes the uncaptured rule of W8-T2 hold by construction: an
    uncaptured row's activities carry UNLINKED and nothing else, so this list is empty
    there and `process_from` has nothing to fold, whatever else the capture recorded.

    Start order, and the capture id after it, for the reason `lineage` sorts attempts
    that way: two captures of one commit have to come out in one order on every build
    of one store, and the store's iteration order is not one.
    """
    carriers = {
        str(row["capture_id"])
        for row in rows
        if str(row["fields"].get("link_confidence") or "") in LADDER
    }
    found = [one for one in recorders.values() if one.capture_id in carriers]
    return sorted(found, key=lambda one: (one.started_at, one.capture_id))


def _landed_by(found: Lineage, rows: Sequence[Mapping[str, Any]]) -> list[Attempt]:
    """The attempts whose captures recorded this commit, in start order."""
    carriers = {str(row["capture_id"]) for row in rows}
    return [one for one in found.attempts if one.capture_id in carriers]


def _own(found: Lineage, rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Every activity of the capture(s) that recorded this commit, attempt or not."""
    carriers = sorted({str(row["capture_id"]) for row in rows})
    return [row for one in carriers for row in found.activities.get(one, [])]


def _at_rung(rows: Sequence[Mapping[str, Any]], rung: str) -> Mapping[str, Any]:
    """The first repo_commit activity, in clock order, that recorded this rung."""
    return min(
        (row for row in rows if row["fields"].get("link_confidence") == rung),
        key=compiler.order,
    )


def _change_row(
    change: Change, closes: str | None
) -> tuple[list[float | None], dict[str, str]]:
    """The seventeen cells `_lag` appends the eighteenth to, and the unknowns.

    The three path columns come from the commit's own per_file list and are all None
    together when it has none: see series_paths, and `_measured` then makes the column
    partial or unavailable from the cells rather than from a constant. The post-merge
    columns come from the landing attempts' outcomes, or from the recording capture's
    own outcomes when nothing landed the change: see series_outcomes, which returns a
    sentence for every cell it left unknown, and `outcomes.deadline` for the window the
    delayed label is decided in.

    An uncaptured row needs no arithmetic of its own. `folded` is empty, so every
    process column below is already None by the rules that make a sum over no rows
    unknown and a ratio over no edits undefined; what this adds is the SENTENCE, so
    that a reader of the cohort is told the row is a repository fact rather than a
    session in which nothing happened.
    """
    folded = [row for one in change.process_from for row in one.activities]
    landed = [row for one in change.landed_by for row in one.activities]
    attempts = [one.attempt for one in change.landed_by]
    requests = lineages.of_type(folded, "model_request")
    subsystems, tests, dependency = series_paths.columns(change.payload)
    # The post-merge columns keep the ATTEMPTS as their input where the process columns
    # above now take whoever recorded the change (W9-T2). An outcome reaches a change
    # through the task id and the attempt ordinal it names, and a recorder that stated
    # neither cannot be found by one; the other route into these three cells is the sha
    # an outcome carries, and that is the `own` argument an uncaptured row passes.
    post, unknown = outcomes.columns(
        landed,
        [(one.task_id, one.attempt) for one in change.landed_by],
        closes,
        change.own if change.uncaptured else None,
        change.sha,
    )
    if change.uncaptured:
        unknown = {**unknown, **dict.fromkeys(PROCESS_COLUMNS, NO_CAPTURE)}
    return [
        compiler.number(change.payload.get("files_changed")),
        compiler.number(change.payload.get("additions")),
        compiler.number(change.payload.get("deletions")),
        subsystems,
        tests,
        dependency,
        # The maximum attempt ordinal among the attempts that landed it, and None where
        # none did. Never 1 for a session that named no attempt: how many sessions tried
        # this change before it is exactly what such a session does not say, and a 1
        # there would be this fold answering a question nobody asked it. W9-T2.
        max(attempts) if attempts else None,
        lineages.total(requests, "input_tokens"),
        lineages.total(requests, "cache_read_tokens"),
        len(lineages.of_type(folded, "compaction")) if folded else None,
        len(lineages.of_type(folded, "verification_run")) if folded else None,
        _turnover(lineages.of_type(folded, "file_edit")),
        _intervals(folded),
        lineages.distinct(lineages.of_type(folded, "file_read"), "file_path")
        if folded
        else None,
        *post,
    ], unknown


def _turnover(edits: Sequence[Mapping[str, Any]]) -> float | None:
    """Edits per distinct file edited. Design 6.12 names the column, not its formula.

    W3-T1 chose this one and says so: the count of file_edit activities over the count
    of distinct paths they name, which is 1.0 when every file was written once and
    grows as a file is rewritten. None when no edit named a path, for the reason
    `_distinct` gives, and None when there was no edit at all: a ratio over no edits is
    not 0, it is undefined.
    """
    files = lineages.distinct(edits, "file_path")
    return len(edits) / files if files else None


def _intervals(folded: Sequence[Mapping[str, Any]]) -> float | None:
    """Stable-state work intervals, spec 13.5, over the sessions folded into a change.

    measures_intervals holds the one definition of an interval in this codebase and it
    is called rather than copied. Without a repo_snapshot there is no diff fingerprint,
    so "the fingerprint did not move" is not something these captures could have seen,
    and the answer is None rather than one interval covering the whole of them.
    """
    if not lineages.of_type(folded, "repo_snapshot"):
        return None
    return len(walks.intervals([as_activity(row) for row in folded]))
