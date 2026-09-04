"""Where a policy intervention cuts a lineage clock. Spec 14.6, design 6.12, W6-T2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from telltale import series as compiler

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from telltale.store import Store

# The two halves of a lineage split at an intervention. `pre` is strictly before the
# boundary and `post` is at or after it, so the two partition the frame and no row is
# in both or in neither.
REGIMES = ("pre", "post")

INTERVENTION_TYPE = "policy.intervention"

# The cohort key the boundary is recorded under, and the key a segmented frame records
# which half it is. Read back by `boundaries` below and by forecast/regime.py, which is
# why they are constants rather than three string literals in three files.
COHORT_KEY = "interventions"
REGIME_KEY = "regime"
CHOSEN_KEY = "intervention"


@dataclass(frozen=True)
class Intervention:
    """One `policy.intervention` observation, reduced to what a boundary needs."""

    advisory_id: str
    ts: str
    observation_id: str


@dataclass(frozen=True)
class Marks:
    """Every intervention of one repository, and which regime this build asked for."""

    found: tuple[Intervention, ...] = ()
    regime: str | None = None
    chosen: Intervention | None = None


class Frame(Protocol):
    """The parallel lists a lineage build fills, in row order."""

    rows: list[list[float | None]]
    keys: list[str]
    ends: list[str]
    fingerprints: list[str | None]
    provenance: list[list[str]]
    flags: list[list[str]]


def interventions(store: Store, repo_id: str) -> list[Intervention]:
    """Every intervention recorded against one repository, oldest first."""
    found = []
    for row in store.observations_of_type(INTERVENTION_TYPE):
        if row["repo_id"] != repo_id:
            continue
        advisory_id = row["payload"].get("advisory_id")
        if not isinstance(advisory_id, str) or not advisory_id:
            raise compiler.Refused(
                f"intervention observation {row['observation_id']} has no advisory id"
            )
        ts = str(row["provider_ts"] or row["ingest_ts"])
        _instant(ts)
        found.append(Intervention(advisory_id, ts, str(row["observation_id"])))
    return sorted(found, key=lambda one: (_instant(one.ts), one.observation_id))


def marks(
    store: Store, repo_id: str, regime: str | None, intervention: str | None
) -> Marks:
    """What `series build` asked for, resolved against what the store holds."""
    found = interventions(store, repo_id)
    if regime is None and intervention is None:
        return Marks(tuple(found))
    if regime is None:
        raise compiler.Refused(
            f"--intervention {intervention} without --regime: an intervention is where"
            f" a lineage is cut, and --regime says which side. One of {REGIMES}"
        )
    if regime not in REGIMES:
        raise compiler.Refused(f"--regime {regime!r} is not one of {REGIMES}")
    return Marks(tuple(found), regime, _chosen(found, intervention))


def _chosen(found: Sequence[Intervention], intervention: str | None) -> Intervention:
    """The one intervention a regime is measured against. Never guessed."""
    listed = ", ".join(one.advisory_id for one in found) or "none"
    if intervention is not None:
        matched = [one for one in found if one.advisory_id == intervention]
        if len(matched) > 1:
            raise compiler.Refused(
                f"intervention {intervention}: {len(matched)} observations"
                " name this advisory;"
                " ambiguous boundary"
            )
        if matched:
            return matched[0]
        raise compiler.Refused(
            f"--intervention {intervention}: no policy.intervention of this repository"
            f" carries that advisory id. Recorded: {listed}"
        )
    if len(found) != 1:
        raise compiler.Refused(
            f"--regime needs --intervention: {len(found)} interventions are recorded"
            f" against this repository ({listed}), and a regime is a side of ONE"
        )
    return found[0]


def boundary(rows_end_ts: Sequence[str], ts: str) -> int | None:
    """The first row that closed at or after `ts`, or None when no row did."""
    instant = _instant(ts)
    for index, end in enumerate(rows_end_ts):
        if _instant(end) >= instant:
            return index
    return None


def cohort_entry(one: Intervention, at: int) -> dict[str, Any]:
    """What the cohort records about one landed intervention."""
    return {"advisory_id": one.advisory_id, "boundary_index": at, "ts": one.ts}


def active_entries(cohort: dict[str, Any]) -> list[dict[str, Any]]:
    """The interior interventions, their indices moved into the chosen frame."""
    listed = cohort.get(COHORT_KEY, [])
    if cohort.get(REGIME_KEY) is None:
        return [dict(one) for one in listed]
    start, end = cohort["regime_bounds"]
    return [
        {**one, "boundary_index": one["boundary_index"] - start}
        for one in listed
        if start < one["boundary_index"] < end
    ]


def boundaries(cohort: dict[str, Any]) -> set[int]:
    """The policy changepoints retained inside this frame."""
    return {int(one["boundary_index"]) for one in active_entries(cohort)}


def segment(
    found: Marks, frame: Frame, cohort: dict[str, Any], ends: Sequence[str]
) -> None:
    """Record the landed boundaries in the cohort, and cut the frame to one regime."""
    landed = [
        (one, at) for one in found.found if (at := boundary(ends, one.ts)) is not None
    ]
    if landed:
        cohort[COHORT_KEY] = [cohort_entry(one, at) for one, at in landed]
    if found.chosen is None:
        return
    cohort[REGIME_KEY] = found.regime
    cohort[CHOSEN_KEY] = found.chosen.advisory_id
    kept = _kept(found.regime, found.chosen, ends)
    cohort["regime_bounds"] = [kept.start, kept.stop]
    frame.ends = list(ends)
    _cut(found.regime, frame, kept)


def _kept(regime: str | None, chosen: Intervention, ends: Sequence[str]) -> range:
    """The row indices one regime keeps, refused by name when it keeps none."""
    at = boundary(ends, chosen.ts)
    if at is None:
        raise compiler.Refused(
            f"intervention {chosen.advisory_id} is later ({chosen.ts}) than every row"
            " of this lineage, so there is no boundary here to take a regime side of"
        )
    kept = range(at) if regime == "pre" else range(at, len(ends))
    if not kept:
        raise compiler.Refused(
            f"--regime {regime} of intervention {chosen.advisory_id} keeps no row: its"
            f" boundary is row {at} of {len(ends)}, and a regime is never padded"
        )
    return kept


def _cut(regime: str | None, frame: Frame, kept: range) -> None:
    """Slice every list of the frame to the same rows, or leave the frame alone."""
    if len(kept) == len(frame.rows):
        return
    lengths = _lengths(frame)
    if len(lengths) != 1:
        raise compiler.Refused(
            f"--regime {regime}: the frame's columns are {sorted(lengths)} rows"
            " long, so there is no one set of rows to cut"
        )
    frame.rows = [frame.rows[index] for index in kept]
    frame.keys = [frame.keys[index] for index in kept]
    frame.ends = [frame.ends[index] for index in kept]
    frame.fingerprints = [frame.fingerprints[index] for index in kept]
    frame.provenance = [frame.provenance[index] for index in kept]
    frame.flags = [frame.flags[index] for index in kept]


def _lengths(frame: Frame) -> set[int]:
    """How long each of the frame's six lists is. One number, or the frame is broken."""
    listed: Iterable[list[Any]] = (
        frame.rows,
        frame.keys,
        frame.ends,
        frame.fingerprints,
        frame.provenance,
        frame.flags,
    )
    return {len(one) for one in listed}


def _instant(ts: str) -> datetime:
    try:
        instant = datetime.fromisoformat(ts)
    except ValueError as invalid:
        raise compiler.Refused(
            f"intervention boundary timestamp {ts!r} is invalid"
        ) from invalid
    if instant.tzinfo is None:
        raise compiler.Refused(
            f"intervention boundary timestamp {ts!r} needs a timezone"
        )
    return instant


def refuse_request(store: Store, capture_id: str) -> None:
    """A policy boundary inside one capture needs a lineage clock."""
    if not store.observations_of_type(INTERVENTION_TYPE):
        return
    rows = store.observations(capture_id)
    repo_ids = {row["repo_id"] for row in rows if row["repo_id"] is not None}
    if len(repo_ids) != 1:
        raise compiler.Refused(
            f"{capture_id}: intervention repository identity is unknown or ambiguous"
        )
    found = interventions(store, str(next(iter(repo_ids))))
    if not found:
        return
    start, end = _capture_bounds(rows, capture_id)
    for one in found:
        if start <= _instant(one.ts) <= end:
            raise compiler.Refused(
                f"regimes are lineage-clock statements: intervention {one.advisory_id}"
                f" falls inside capture {capture_id}"
            )


def _capture_bounds(
    rows: Sequence[dict[str, Any]], capture_id: str
) -> tuple[datetime, datetime]:
    """Require one lifecycle start and end before locating an intervention."""
    starts = [
        row for row in rows if row["observation_type"] == "telltale.capture_started"
    ]
    ends = [row for row in rows if row["observation_type"] == "telltale.capture_ended"]
    if len(starts) != 1 or len(ends) != 1:
        raise compiler.Refused(
            f"{capture_id}: intervention position needs"
            " one capture start and one capture end"
        )
    start, end = (
        _instant(str(row["provider_ts"] or row["ingest_ts"]))
        for row in (starts[0], ends[0])
    )
    return start, end
