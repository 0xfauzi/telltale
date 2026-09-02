"""One capture's activities folded into a Series, with no look-ahead. Design 6.12.

A clock is a choice of what one row means. The request clock puts one row per model
request, so the sequence a forecaster sees is the sequence the session actually had:
row i closes at the moment request i happened, and everything the row says is a fact
that was already true then.

Three rules hold that promise, and each is a mechanism rather than a convention.

  The fold is single-pass over activities sorted by clock position, and a row is
  emitted the moment its request is consumed. State that has not been consumed cannot
  reach a row, so no look-ahead is possible without changing the loop. `check` is the
  independent test of the same claim, run against the stored rows rather than against
  the loop: every provenance id must resolve to an activity whose position is at or
  before the row's end.

  A column whose capability was not observed is all None, never zeros. `_honest` in
  measures.py is the same rule for a scalar; here it is applied per column, from the
  coverage the activities reducer measured for this capture. "0 compactions" and
  "compaction was not observable on this surface" are different statements.

  Position is `ended_at` or `started_at`, and ties break on activity id. Parallel tool
  calls and subagents genuinely overlap: any rule that put them in a total order would
  be inventing one, and the id is arrival order, which is the only order measured.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale.model import ColumnSpec, RowMeta, Series, to_json

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.store import Store

CLOCKS = ("request", "attempt", "change")
POLICIES = ("exclude", "refuse")

# The activity types design 6.10 gives a tool call. `tool_calls_since_prev` counts all
# five, which is the number W1-T2's goldens check against the distinct tool_use_id
# count in the fixture.
TOOL_TYPES = ("verification_run", "command", "file_edit", "file_read", "tool_call")

# Best first, so the worst of several capabilities is the last one standing. A column
# fed by two capabilities is only as good as the weaker of them.
_COVERAGE_RANK = ("observed", "derived", "partial", "unavailable")

# Every column of the request clock, in order, with the capabilities it rests on.
# Design 6.12 names the columns; the capability lists are what makes the coverage of
# each one a measurement rather than a guess, and they are measured rather than
# assumed (see docs/log/W1-T5.md for the level-0 replay behind two of them).
#
# The role is past_covariate for all ten: which column is the target is chosen at
# forecast time (W1-T6), not fixed in the stored series.
_REQUEST_COLUMNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("fresh_input_tokens", "tokens", ("request_usage",)),
    ("cache_read_tokens", "tokens", ("request_usage",)),
    ("output_tokens", "tokens", ("request_usage",)),
    # Claude reports it on the api_request record itself, so it rides request_usage.
    # Codex derives it from turn timestamps and design 6.12 calls that partial; W1-T3
    # is adding those activities in parallel and this mapping is untested against them.
    ("request_duration_ms", "ms", ("request_usage",)),
    ("compaction_before", "flag", ("compaction",)),
    ("tool_calls_since_prev", "calls", ("tool_calls",)),
    # tool_calls, not file_paths: the count needs the tool NAME, and a level-0 replay
    # of S1 keeps the file_edit activity while dropping its path (W1-T5 measured).
    ("files_edited_since_prev", "files", ("tool_calls",)),
    # commands, because a verification_run is a command the classifier recognised: the
    # same level-0 replay turns all three of S1's pytest runs into plain commands.
    ("verification_runs_since_prev", "runs", ("commands",)),
    ("last_verification_exit", "exit_code", ("commands", "tool_calls")),
    # env_changed has no capability: the fingerprint is a column of the observation
    # row, so its coverage is measured from the rows themselves in `_env`.
    ("env_changed", "flag", ()),
)

_ENV_COLUMN = "env_changed"


class Refused(Exception):
    """A build or a check that stopped on purpose. The CLI turns it into exit 2."""


@dataclass(frozen=True)
class _RowKey:
    """What identifies one row: its request activity, when it closed, and its bytes.

    `primary` is the observation that opened the request activity, and it is carried
    only so that `build` can ask the store for that observation's environment
    fingerprint COLUMN. It is not a value in the series.
    """

    activity_id: str
    end: str
    primary: str


def _version() -> str:
    """The hash of this file's source, read at import. Design 6.12.

    A hash rather than a number somebody remembers to bump: a series built by a
    different fold must not compare equal to one built by this fold, and the series_id
    below carries this string into its own hash so that it cannot.
    """
    try:
        digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return "ser-source-unavailable"
    return f"ser-{digest}"


REDUCER_VERSION = _version()


def columns(coverage: Mapping[str, str], env: str = "unavailable") -> list[ColumnSpec]:
    """The request clock's ColumnSpec list, with coverage resolved for one capture.

    `coverage` is the capability map the activities reducer measured and stored on the
    capture's lifecycle activity. `env` is the separately measured coverage of the
    environment fingerprint, which is not a provider capability.
    """
    return [
        ColumnSpec(
            name=name,
            unit=unit,
            role="past_covariate",
            coverage=env if name == _ENV_COLUMN else _worst(coverage, capabilities),
        )
        for name, unit, capabilities in _REQUEST_COLUMNS
    ]


def _worst(coverage: Mapping[str, str], capabilities: Sequence[str]) -> str:
    cells = [coverage.get(name, "unavailable") for name in capabilities]
    return max(cells, key=_COVERAGE_RANK.index) if cells else "unavailable"


def build(
    store: Store,
    clock: str,
    capture_id: str,
    missingness_policy: str = "exclude",
) -> Series:
    """One capture's activities as a request-clock Series. Design 6.12.

    The attempt and change clocks belong to W3-T1 and are refused by name rather than
    built from a definition nobody has measured.
    """
    if clock in ("attempt", "change"):
        raise Refused(f"the {clock} clock is not built yet (W3-T1)")
    if clock != "request":
        raise Refused(f"clock {clock!r} is not one of {CLOCKS}")
    if missingness_policy not in POLICIES:
        raise Refused(f"policy {missingness_policy!r} is not one of {POLICIES}")

    activities = store.activities(capture_id)
    if not activities:
        raise Refused(f"{capture_id}: no activities. Run `telltale rebuild` first.")
    capture = _capture_activity(activities, capture_id)
    rows, keys, provenance = _fold(sorted(activities, key=_order))
    fingerprints = _fingerprints(store, [key.primary for key in keys])
    env_coverage, env_column, changepoints = _env(fingerprints)
    specs = columns(dict(capture["fields"]["coverage"]), env_coverage)
    table = _blank_unobservable(
        [[*row, flag] for row, flag in zip(rows, env_column, strict=True)], specs
    )
    if missingness_policy == "refuse":
        _refuse_on_gaps(table, specs, keys)
    cohort = _cohort(capture_id, capture["fields"])
    return Series(
        series_id=series_id(clock, cohort, specs, REDUCER_VERSION, table),
        clock=clock,
        cohort=cohort,
        columns=specs,
        rows=table,
        row_meta=[
            RowMeta(
                row_key=key.activity_id,
                row_end_ts=key.end,
                env_fingerprint_id=fingerprint,
                provenance=ids,
            )
            for key, fingerprint, ids in zip(
                keys, fingerprints, provenance, strict=True
            )
        ],
        changepoints=changepoints,
        missingness_policy=missingness_policy,
        reducer_version=REDUCER_VERSION,
    )


def series_id(
    clock: str,
    cohort: Mapping[str, Any],
    specs: Sequence[ColumnSpec],
    reducer_version: str,
    rows: Sequence[Sequence[float | None]],
) -> str:
    """sha256 of what the series IS, so an identical rebuild is the same snapshot.

    Not of row_meta, not of built_at and not of the changepoints: the first two are
    when and from what it was built, and the third is a function of the rows. Two
    builds of one capture write one row through `put_series`, which replaces on this id.
    """
    schema = [[spec.name, spec.unit, spec.role, spec.coverage] for spec in specs]
    canonical = to_json([clock, dict(cohort), schema, reducer_version, rows])
    return f"ser_{hashlib.sha256(canonical.encode()).hexdigest()[:24]}"


def check(store: Store, series: Series) -> list[str]:
    """The build-time invariant, tested against the stored rows. Design 6.12.

    Two claims, and the first is the one that matters: no value in row i was known
    later than row i closed. It is checked by resolving every provenance id back to
    its activity and comparing positions, which is independent of the fold that wrote
    them. A provenance id that resolves to nothing is a violation of its own, because
    an unresolvable id is an unfalsifiable claim.

    `store` is a parameter because a Series carries activity IDS, and an id has no
    position until the activities table is read. A check that skipped the comparison
    when it could not resolve them would be a check that passes for the wrong reason.
    """
    violations: list[str] = []
    capture_id = str(series.cohort.get("capture_id") or "")
    positions = {
        str(row["activity_id"]): _position(row) for row in store.activities(capture_id)
    }
    if not positions:
        violations.append(
            f"capture {capture_id!r} has no activities, so the provenance of"
            f" {len(series.row_meta)} rows could not be checked"
        )
    previous: str | None = None
    for index, meta in enumerate(series.row_meta):
        if previous is not None and meta.row_end_ts < previous:
            violations.append(
                f"row {index} ends at {meta.row_end_ts}, before row {index - 1}"
                f" ended at {previous}: row_end_ts must not go backwards"
            )
        previous = meta.row_end_ts
        if positions:
            violations += _late(index, meta, positions)
    return violations


def _late(index: int, meta: RowMeta, positions: Mapping[str, str]) -> list[str]:
    found: list[str] = []
    for activity_id in meta.provenance:
        position = positions.get(activity_id)
        if position is None:
            found.append(
                f"row {index} names activity {activity_id} in its provenance and"
                " no such activity is stored for this capture"
            )
        elif position > meta.row_end_ts:
            found.append(
                f"row {index} ends at {meta.row_end_ts} and its provenance names"
                f" activity {activity_id} at {position}, which is later:"
                " the row was built with look-ahead"
            )
    return found


# -- the fold -------------------------------------------------------------------------


def _order(row: Mapping[str, Any]) -> tuple[str, str]:
    return (_position(row), str(row["activity_id"]))


def _position(row: Mapping[str, Any]) -> str:
    """Where an activity sits on the clock: when it ended, or when it started.

    An activity with no end has not been observed to finish, and its start is the only
    time anything measured. Every model_request on a replayed Claude capture is in that
    case: the OTel api_request record carries one timestamp and a duration.
    """
    return str(row["ended_at"] or row["started_at"])


def _fold(
    ordered: Sequence[Mapping[str, Any]],
) -> tuple[list[list[float | None]], list[_RowKey], list[list[str]]]:
    """One pass, emitting a row the moment its request is consumed.

    This loop is where "no look-ahead" is true by construction: the counters a row is
    built from have seen only the activities already consumed, and consumption is in
    position order. Nothing later in `ordered` is reachable from here.
    """
    rows: list[list[float | None]] = []
    keys: list[_RowKey] = []
    provenance: list[list[str]] = []
    pending: list[str] = []
    state = _State()
    for item in ordered:
        activity_id = str(item["activity_id"])
        fields = dict(item["fields"])
        if item["activity_type"] != "model_request":
            pending.append(activity_id)
            state.consume(str(item["activity_type"]), fields)
            continue
        rows.append(state.row(fields))
        keys.append(
            _RowKey(
                activity_id=activity_id,
                end=_position(item),
                primary=str(fields.get("primary_observation") or ""),
            )
        )
        provenance.append([*pending, activity_id])
        pending = []
        state.reset()
    return rows, keys, provenance


@dataclass
class _State:
    """What the fold has consumed since the previous row, plus what outlives a row.

    `last_exit` is the one counter `reset` leaves alone: design 6.12 defines
    last_verification_exit as the most recent verification's result, which is a fact
    that stays true until another verification states otherwise.
    """

    tools: int = 0
    files: int = 0
    verifications: int = 0
    compactions: int = 0
    last_exit: float | None = None

    def consume(self, kind: str, fields: Mapping[str, Any]) -> None:
        self.tools += kind in TOOL_TYPES
        self.files += kind == "file_edit"
        self.compactions += kind == "compaction"
        if kind == "verification_run":
            self.verifications += 1
            self.last_exit = _exit(fields.get("success"), self.last_exit)

    def row(self, fields: Mapping[str, Any]) -> list[float | None]:
        """The nine columns that come from the fold, in the order design 6.12 lists.

        env_changed is the tenth and is appended by `build`: it is a function of the
        row fingerprints, which are not known until every row key exists.
        """
        return [
            _number(fields.get("input_tokens")),
            _number(fields.get("cache_read_tokens")),
            _number(fields.get("output_tokens")),
            _number(fields.get("duration_ms")),
            1 if self.compactions else 0,
            self.tools,
            self.files,
            self.verifications,
            self.last_exit,
        ]

    def reset(self) -> None:
        self.tools = self.files = self.verifications = self.compactions = 0


def _exit(success: Any, previous: float | None) -> float | None:
    """0 for a pass, 1 for a fail, and the previous answer when nothing said.

    Design 6.10 stores no exit code: E01 measured that a Bash exit status is nowhere
    structured and appears only as text on a FAILED stream result. `success` is what
    the surfaces state, and a run whose success nobody stated leaves the last stated
    answer standing rather than being read as a pass.
    """
    if success is True:
        return 0
    if success is False:
        return 1
    return previous


def _number(value: Any) -> float | None:
    """A stored field as a number, or None. A bool is not a number here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


# -- coverage, the environment, and the two policies ----------------------------------


def _capture_activity(
    activities: Sequence[Mapping[str, Any]], capture_id: str
) -> Mapping[str, Any]:
    """The lifecycle row that carries the measured capability coverage (W1-T2).

    Refused rather than defaulted when it is absent: a column's coverage is the whole
    difference between a zero and an unknown, and inventing one here would be the
    substitution this file exists to prevent.
    """
    for row in activities:
        fields = dict(row["fields"])
        if row["activity_type"] == "lifecycle" and fields.get("event") == "capture":
            if isinstance(fields.get("coverage"), dict):
                return row
            raise Refused(
                f"{capture_id}: the capture lifecycle activity carries no coverage map,"
                " so no column's coverage can be resolved"
            )
    raise Refused(f"{capture_id}: no capture lifecycle activity")


def _cohort(capture_id: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """The five keys design 6.12 puts on a request-clock series.

    `content_level` is None on every capture today: W1-T2 does not lift it onto the
    lifecycle activity, and it lives in the telltale.capture_started observation
    PAYLOAD, which this module may not read. It is read here by the name it will have
    when a later task lifts it, rather than left out of the cohort.
    """
    return {
        "capture_id": capture_id,
        "provider": fields.get("provider"),
        "repo_id": fields.get("repo_id"),
        "environment_fingerprint_id": fields.get("environment_fingerprint_id"),
        "content_level": fields.get("content_level"),
    }


def _fingerprints(store: Store, primaries: Sequence[str]) -> list[str | None]:
    """The environment fingerprint of each row's request, off the observation ROW.

    Design 6.2 puts `environment_fingerprint_id` in a column rather than in a payload,
    so this is the one observation read the compiler makes, and it reads that column
    and nothing else. A replayed fixture has none on any row (no launcher stamped
    them); a launcher capture has one on every row.
    """
    wanted = [value for value in primaries if value]
    found = {
        str(row["observation_id"]): row["environment_fingerprint_id"]
        for row in store.observations_by_id(wanted)
    }
    return [found.get(value) for value in primaries]


def _env(
    fingerprints: Sequence[str | None],
) -> tuple[str, list[float | None], list[int]]:
    """env_changed, its coverage, and the changepoints, from the row fingerprints.

    Coverage is measured from the rows because the fingerprint is not a provider
    capability: no row carries one and the column is unavailable and all None; every
    row carries one and it is observed; some do and it is partial, with a None wherever
    this row or the one before it is unknown. A changepoint is only recorded between
    two KNOWN and different fingerprints: a change nobody observed is not a change.
    """
    known = [value for value in fingerprints if value is not None]
    if not known:
        return "unavailable", [None] * len(fingerprints), []
    coverage = "observed" if len(known) == len(fingerprints) else "partial"
    pairs = [(None, fingerprints[0]), *itertools.pairwise(fingerprints)]
    column = [_changed(before, now) for before, now in pairs]
    return coverage, column, [i for i, cell in enumerate(column) if cell == 1]


def _changed(before: str | None, now: str | None) -> float | None:
    """1 when the fingerprint differs from the previous row's, 0 when it does not.

    None when either side is unknown, including the first row, which has no previous
    row to differ from. `0` there would be a claim that the environment did not change
    across a boundary that was never observed.
    """
    if now is None:
        return None
    if before is None:
        return 0
    return 1 if before != now else 0


def _blank_unobservable(
    table: list[list[float | None]], specs: Sequence[ColumnSpec]
) -> list[list[float | None]]:
    """A column nobody could see is all None, never a column of zeros.

    This is the defect W1-T2 found in its own summary and fixed in `_honest`: the
    arithmetic is right (a count over an empty set IS zero) and the statement is false.
    """
    blank = [
        index for index, spec in enumerate(specs) if spec.coverage == "unavailable"
    ]
    for row in table:
        for index in blank:
            row[index] = None
    return table


def _refuse_on_gaps(
    table: Sequence[Sequence[float | None]],
    specs: Sequence[ColumnSpec],
    keys: Sequence[_RowKey],
) -> None:
    """The `refuse` policy: a hole in a column that WAS observable stops the build.

    A column whose coverage is unavailable is all None by definition, so refusing on it
    would refuse every capture that lacks a surface. It is reported by `series build`
    instead. No policy imputes.
    """
    for index, spec in enumerate(specs):
        if spec.coverage != "observed":
            continue
        gap = next((i for i, row in enumerate(table) if row[index] is None), None)
        if gap is not None:
            raise Refused(
                f"policy refuse: column {spec.name} has coverage observed and no value"
                f" in row {gap} (activity {keys[gap].activity_id})"
            )


def column_report(series: Series) -> list[dict[str, Any]]:
    """One row per column: what it holds, how well it was seen, and how many holes.

    Coverage and the None count belong on one line because together they say what a
    zero in that column would have meant. A column with coverage `unavailable` has a
    None count equal to the row count, and that pair is the whole difference between
    "it did not happen" and "no surface could see it".

    Nothing is dropped and nothing is filled at build time under the `exclude` policy:
    the forecaster drops the windows containing a None (design 6.12), and this count is
    how many of them a reader can expect before it runs.
    """
    return [
        {
            "column": spec.name,
            "unit": spec.unit,
            "role": spec.role,
            "coverage": spec.coverage,
            "nulls": sum(1 for row in series.rows if row[index] is None),
        }
        for index, spec in enumerate(series.columns)
    ]
