"""Spec 16's repository work profile: the natural history of one repository.

What this module computes and what it refuses are the same decision. For each group of
captures of one repository it reports an observed DISTRIBUTION per metric (n, median,
scaled MAD, min, max) and, beside it, one comparative number: the group's median over
the median of a matched cohort outside the group. Spec 16 allows exactly that sentence
("sessions touching payments used 2.1x the matched-cohort median fresh token burden,
n=31, same model/runtime family") and refuses the single latent number per path that a
table of this shape invites. There is no composite here, no weight and no total, and
report_profile.py refuses the four words such a number would be spelled with.

Nothing here computes a metric and nothing here writes. Every raw number is READ BACK
out of the evidence table measures.py wrote, as cohorts.py reads it, so a profile row
and a `telltale show` of one of its captures cannot disagree about a number. Every
number the profile itself produces is an Evidence built through the comparative
constructor, returned, printed and never stored, for the reason cohorts.py gives at
length about percentiles: a median over "the captures in this database at this moment"
changes the moment a capture arrives, and a stored row would freeze one comparison and
then present it as a property of a path.

The cohort rule is design 6.11's and is not restated here: `cohorts.cohort` decides who
is comparable, and this module only decides who is OUTSIDE the group being described.
The gate is that outside set. A group compared against a cohort containing itself is
compared partly against itself, and at these sample sizes that is most of the answer.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from statistics import median
from typing import TYPE_CHECKING, Any

from telltale.cohorts import COHORT_KEYS, COHORT_MIN, VECTOR, cohort, cohort_keys
from telltale.correlate import REDUCER_VERSION, hashed_id
from telltale.experiments_measure import stats as distribution
from telltale.measures import value_of
from telltale.model import COVERAGE, Evidence

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from telltale.store import Store

# The three groupings. `week` is the ISO week a capture's first observation arrived in;
# `subsystem` is the top-level directory rule series_paths.py uses, applied to the paths
# a capture EDITED; `path` is the one group a `--path` prefix names, which is the shape
# spec 16's allowed sentence has ("sessions touching payments ...").
GROUPINGS = ("week", "subsystem", "path")

# What a group of imported captures is called. They are outside every cohort (design
# 6.11), so they are never mixed into a group whose ratio column could be filled: a
# group holding one is a group with no cohort, and the suffix says so before the ratio
# column has to.
BACKFILL = " (backfill)"

# A file at the repository root is in no directory. `subsystems_touched` spells it this
# way in series_paths.py and the two must agree: counting such a capture into no group
# would say a session that edited pyproject.toml alone touched nothing.
ROOT = "."

# The four spread numbers a row carries beside the Evidence's median value. They
# describe the SAME distribution the median came from, under the same claim class,
# the same coverage word and the same source list.
SPREAD = ("mad_scaled", "min", "max")

# Why a capture is in no group. Each is a sentence and never a silent drop: a profile
# whose n is smaller than the repository's capture count has to say where the rest went.
EXCLUDED = "imported (backfill), excluded"
NO_PATHS = "no file_edit activity named a path"
NO_EDIT = "edited no file"
NOTHING_UNDER = "edited nothing under the prefix"

# How many repositories a refusal names when the given repo_id is not one of them. A
# display bound, not a measurement: an id is 64 characters and the owner's store holds
# 79, so the whole list is a screen of hashes instead of a message.
BUSIEST = 5

_ASSUMPTIONS = (
    "a natural history: these captures were not assigned to their group, they arrived"
    " in it, so the group differs from every other group in more than its name",
    "cohort: same provider, runtime major version, model and content level"
    f" (design 6.11), and at least {COHORT_MIN} captures OUTSIDE this group",
    "imported (backfill) captures are excluded unless --include-backfill was given,"
    " and are then grouped on their own because they are outside every cohort",
)
_PARTIAL = (
    "at least one capture in this group measured this partially, so the distribution is"
    " over values that were not all seen the same way"
)


class Refused(ValueError):
    """A profile that cannot be built, naming what would let it be. Never a blank."""


def build(
    store: Store,
    repo_id: str,
    path: str | None = None,
    by: str | None = None,
    include_backfill: bool = False,
) -> dict[str, Any]:
    """The profile: the frame, the groups, and 22 rows in each. Spec 16.

    `path` selects the captures that edited under a prefix and, when `by` was not
    given, also names the one group they form. `by` overrides that: `--path X --by week`
    is the natural history of the sessions that touched X, week by week.
    """
    grouping = _grouping(path, by)
    rows = _captures(store, repo_id)
    keys = {
        str(row["capture_id"]): cohort_keys(store, str(row["capture_id"]))
        for row in rows
    }
    placed, skipped = _place(store, rows, keys, grouping, path, include_backfill)
    pools: dict[tuple[Any, ...], _Pool] = {}
    seen: dict[str, dict[str, Mapping[str, Any]]] = {}
    return {
        "repo_id": repo_id,
        "by": grouping,
        "path": path,
        "include_backfill": include_backfill,
        "captures": len(rows),
        "grouped": len({one for members in placed.values() for one in members}),
        # Never dropped silently and never rolled into one number: each reason is the
        # sentence that says what would put those captures in a group.
        "skipped": skipped,
        "groups": [
            _group(store, name, members, keys, include_backfill, pools, seen)
            for name, members in sorted(placed.items())
        ],
    }


def _grouping(path: str | None, by: str | None) -> str:
    """`week` by default, the prefix itself when `--path` was given and `--by` was not.

    Spec 16 says "for each path/subsystem", so a named prefix IS a group and not only a
    filter: `--path payments` alone answers "sessions touching payments", which is the
    sentence the spec allows, while `--path payments --by week` answers it week by week.
    """
    if by is None:
        return "path" if path is not None else "week"
    if by not in GROUPINGS:
        raise Refused(f"--by {by} is not one of {', '.join(GROUPINGS)}")
    if by == "path" and path is None:
        raise Refused(
            "--by path needs --path PREFIX: the prefix is what names the group"
        )
    return by


def _captures(store: Store, repo_id: str) -> list[Mapping[str, Any]]:
    """This repository's captures, oldest first. A repo_id nobody stored is a refusal.

    Named rather than answered with an empty profile, because "this repository has no
    captures" and "that is not a repo_id in this database" are different answers and
    only the second one is fixed by looking up the id.
    """
    every = store.captures()
    rows = [row for row in every if row["repo_id"] == repo_id]
    if not rows:
        raise Refused(f"{repo_id}: no captures of this repository. {_stored(every)}")
    return sorted(rows, key=lambda row: str(row["first_ts"]))


def _stored(every: Sequence[Mapping[str, Any]]) -> str:
    """What repositories the store does hold: the busiest few, and how many there are.

    Not the whole list, unlike `cli_common.known` for captures: a repo_id is 64
    characters and the owner's store holds 79 of them, which is a screen of hashes
    rather than a message. The count is the fact a reader needs (the id is not among
    them) and the busiest few are the ones they probably meant.
    """
    counts = Counter(str(row["repo_id"]) for row in every if row["repo_id"])
    listed = ", ".join(f"{one} ({n})" for one, n in counts.most_common(BUSIEST))
    if not counts:
        return "This database holds no capture with a repository at all."
    return f"{len(counts)} stored, the busiest {min(len(counts), BUSIEST)}: {listed}"


def _place(
    store: Store,
    rows: Sequence[Mapping[str, Any]],
    keys: Mapping[str, Mapping[str, Any]],
    grouping: str,
    path: str | None,
    include_backfill: bool,
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Which groups each capture belongs to, and why one belongs to none.

    A capture belongs to MORE THAN ONE subsystem group when it edited more than one
    top-level directory, which is what "sessions touching payments" means: the groups
    overlap and their capture counts do not sum to the repository's.
    """
    skipped: Counter[str] = Counter()
    placed: dict[str, list[str]] = {}
    for row in rows:
        one = str(row["capture_id"])
        backfill = bool(keys[one]["backfill"])
        if backfill and not include_backfill:
            skipped[EXCLUDED] += 1
            continue
        names, reason = _names(store, row, grouping, path)
        if reason is not None:
            skipped[reason] += 1
            continue
        for name in names:
            placed.setdefault(name + (BACKFILL if backfill else ""), []).append(one)
    return placed, dict(skipped)


def _names(
    store: Store, row: Mapping[str, Any], grouping: str, path: str | None
) -> tuple[list[str], str | None]:
    """The groups one capture belongs to, or the reason it belongs to none.

    The paths are read only when a grouping or a filter needs them: the week grouping
    with no `--path` is a question about arrival time alone, and reading every capture's
    file_edit activities to answer it would be a read nothing uses.
    """
    if grouping == "week" and path is None:
        return [_week(str(row["first_ts"]))], None
    edited, reason = _selected(store, str(row["capture_id"]), path)
    if reason is not None:
        return [], reason
    if grouping == "week":
        return [_week(str(row["first_ts"]))], None
    if grouping == "path":
        return [str(path)], None
    return sorted({one.split("/")[0] if "/" in one else ROOT for one in edited}), None


def _selected(
    store: Store, capture_id: str, path: str | None
) -> tuple[list[str], str | None]:
    """The edited paths this profile is about, or the reason there are none.

    Three refusals and not one, because they are three different facts about a capture
    and only the first is fixed by widening the prefix: nothing was edited, nothing was
    edited under the prefix, and edits happened whose paths no surface named.
    """
    edited = _edited(store, capture_id)
    if edited is None:
        return [], NO_PATHS
    if not edited:
        return [], NO_EDIT
    if path is None:
        return edited, None
    under = [one for one in edited if _under(one, path)]
    return (under, None) if under else ([], NOTHING_UNDER)


def _edited(store: Store, capture_id: str) -> list[str] | None:
    """The distinct repo-relative paths this capture edited. Three answers, not two.

    `[]` is "this capture edited no file", `None` is "it made edits and no surface
    named the path", and a list is the paths. `measures_spec13._distinct` separates the
    same three for `unique_files_changed`, and the two have to agree: a capture that
    counts 4 files changed and lands in no subsystem group would be a profile losing
    work the summary reports.
    """
    rows = store.activities(capture_id, ("file_edit",))
    if not rows:
        return []
    found = sorted(
        {
            str(fields["file_path"])
            for row in rows
            if (fields := dict(row["fields"])).get("file_path")
        }
    )
    return found or None


def _under(one: str, prefix: str) -> bool:
    """Path components, never characters: `src/telltale` does not hold `src/telltale2`.

    A prefix is a place in the tree, and a character prefix would put a file in a
    directory it is not in.
    """
    trimmed = prefix.rstrip("/")
    return one == trimmed or one.startswith(trimmed + "/")


def _week(stamp: str) -> str:
    """The ISO week the capture's first observation arrived in, `2026-W36`.

    ISO rather than calendar month: `%G` is the ISO year and differs from the calendar
    year in the days around New Year, so a group is a week that never spans two names.
    """
    found = datetime.fromisoformat(stamp).isocalendar()
    return f"{found.year:04d}-W{found.week:02d}"


@dataclass(frozen=True)
class _Pool:
    """One matched cohort, read once and shared by every group that keys to it."""

    members: list[str]
    rows: dict[str, dict[str, Mapping[str, Any]]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Matched:
    """What one group may be placed against: the cohort MINUS the group itself."""

    outside: list[str]
    keys: dict[str, Any]
    total: int
    rows: dict[str, dict[str, Mapping[str, Any]]]
    # The {"no_ratio": ...} cell every metric gets when there is no comparison to make.
    absent: dict[str, Any] | None

    def published(self) -> dict[str, Any]:
        """What the frame prints about the cohort. Never the member evidence rows."""
        if self.absent is not None:
            return dict(self.absent)
        return {**self.keys, "n": self.total, "outside": len(self.outside)}


def _group(
    store: Store,
    name: str,
    members: Sequence[str],
    keys: Mapping[str, Mapping[str, Any]],
    include_backfill: bool,
    pools: dict[tuple[Any, ...], _Pool],
    seen: dict[str, dict[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    mine = {one: _by_metric(store, one, seen) for one in members}
    matched = _matched(store, members, keys, include_backfill, pools, seen)
    return {
        "group": name,
        "composition": _composition(members, keys),
        "cohort": matched.published(),
        "rows": [
            _row(name, family, metric, members, mine, matched)
            for family, metrics in VECTOR
            for metric in metrics
        ],
    }


def _matched(
    store: Store,
    members: Sequence[str],
    keys: Mapping[str, Mapping[str, Any]],
    include_backfill: bool,
    pools: dict[tuple[Any, ...], _Pool],
    seen: dict[str, dict[str, Mapping[str, Any]]],
) -> _Matched:
    """The cohort outside this group, or what stopped there being one.

    Three gates in order, and each names itself rather than leaving the column blank.
    A group holding one capture whose keys are not all known has no cohort at all; a
    group spanning two cohorts is not one comparison; and a cohort with fewer than
    design 6.11's ten members outside the group is a rank among the group's neighbours.
    """
    absent = _no_cohort(members, keys)
    if absent is not None:
        return _Matched([], {}, 0, {}, absent)
    found = tuple(keys[members[0]][key] for key in COHORT_KEYS)
    pool = _pool(store, members[0], found, include_backfill, pools, seen)
    outside = [one for one in pool.members if one not in set(members)]
    named: dict[str, Any] = dict(zip(COHORT_KEYS, found, strict=True))
    return _Matched(
        outside,
        named,
        len(pool.members),
        pool.rows,
        # Both counts, because they answer two questions and neither implies the other.
        # `n` is the cohort, which is what spec 16's allowed sentence quotes; the number
        # after it is the gate, and a group that IS its whole cohort has a cohort of 31
        # and nothing to be placed against.
        None
        if len(outside) >= COHORT_MIN
        else {
            "no_ratio": f"no cohort (n={len(pool.members)},"
            f" {len(outside)} outside this group)",
            "n": len(pool.members),
            "outside": len(outside),
        },
    )


def _no_cohort(
    members: Sequence[str], keys: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any] | None:
    """Why this group has no cohort at all, or None when it has one.

    Two gates. A group holding one capture whose four keys are not all known has no
    cohort, because an unknown key excludes a capture from every cohort including one
    made of captures exactly like it (cohorts.cohort_keys). A group spanning two key
    sets is not one comparison: it would be placed against a cohort half of it is not
    in, which is the "same model/runtime family" clause of spec 16's allowed sentence.
    """
    without = [one for one in members if keys[one]["reason"]]
    if without:
        # A COUNT in the cell and the sentences beside it, because the cell is repeated
        # on all 22 rows of the group and reads the same on every one. What they say is
        # also in the composition already, key by key: a group whose runtime_majors
        # column reads "2 31, unknown 12" has named the missing key more precisely.
        return {
            "no_ratio": f"{len(without)} of {len(members)} captures have an"
            " unknown cohort key",
            "reasons": sorted({str(keys[one]["reason"]) for one in without}),
        }
    spanned = {tuple(keys[one][key] for key in COHORT_KEYS) for one in members}
    if len(spanned) > 1:
        return {"no_ratio": f"the group spans {len(spanned)} cohorts"}
    return None


def _pool(
    store: Store,
    member: str,
    found: tuple[Any, ...],
    include_backfill: bool,
    pools: dict[tuple[Any, ...], _Pool],
    seen: dict[str, dict[str, Mapping[str, Any]]],
) -> _Pool:
    """The cohort for one key set, scanned once however many groups share it."""
    if found not in pools:
        listed = cohort(store, member, include_backfill)
        pools[found] = _Pool(
            listed, {one: _by_metric(store, one, seen) for one in listed}
        )
    return pools[found]


def _row(
    group: str,
    family: str,
    metric: str,
    members: Sequence[str],
    mine: Mapping[str, Mapping[str, Mapping[str, Any]]],
    matched: _Matched,
) -> dict[str, Any]:
    """One (group, metric) row: a comparative Evidence, its spread, and the ratio cell.

    The Evidence's value is the median and its source is every evidence row the median
    was taken over, so the scaled MAD, the minimum and the maximum beside it describe
    the same distribution under the same claim and the same provenance. `n` counts the
    captures that MEASURED the metric and `unknown` the rest: nothing is imputed, so a
    metric three of eight captures could not report is visibly a metric of five.
    """
    found = [mine[one].get(metric) for one in members]
    rows = [row for row in found if row is not None]
    numbers = distribution([value_of(row) for row in found])
    built = Evidence.comparative(
        evidence_id=hashed_id(
            "ev", ",".join(members), "profile_median", f"{group}|{metric}"
        ),
        metric=f"{metric}_group_median",
        value=numbers["median"],
        unit=_one_unit(rows),
        coverage=_weakest(str(row["coverage"]) for row in rows),
        source=[str(row["evidence_id"]) for row in rows],
        cohort={"group": group, "captures": list(members), "n": len(members)},
        reducer_version=REDUCER_VERSION,
        assumptions=list(_ASSUMPTIONS),
        warnings=_row_warnings(rows, numbers["unknown"], len(members)),
    )
    return {
        "family": family,
        **asdict(built),
        "n": numbers["n"],
        "unknown": numbers["unknown"],
        **{name: numbers[name] for name in SPREAD},
        "ratio": _ratio(group, metric, numbers["median"], built, matched),
    }


def _row_warnings(
    rows: Sequence[Mapping[str, Any]], unknown: int, members: int
) -> list[str]:
    out = [_PARTIAL] if any(row["coverage"] == "partial" for row in rows) else []
    if unknown:
        out.append(f"{unknown} of {members} captures in this group measured no value")
    return out


def _ratio(
    group: str, metric: str, own: float | None, built: Evidence, matched: _Matched
) -> dict[str, Any]:
    """The one cohort-relative number spec 16 allows, or what stopped it. Never blank.

    The denominator is the median over the cohort members OUTSIDE this group, and the
    per-metric gate is the same ten: a cohort of thirty-one that measured this metric
    nine times is nine values, whatever the membership count says. A median of zero
    refuses rather than dividing, because the ratio would be undefined and 0 or 1 in
    that cell would both be answers nobody measured.
    """
    if matched.absent is not None:
        return dict(matched.absent)
    if own is None:
        return {"no_ratio": "no capture in this group measured it"}
    found = [matched.rows[one].get(metric) for one in matched.outside]
    seen = [value for value in (value_of(row) for row in found) if value is not None]
    if len(seen) < COHORT_MIN:
        return {
            "no_ratio": f"no cohort (n={len(matched.outside)} outside this group,"
            f" {len(seen)} measured this metric)",
            "outside": len(matched.outside),
            "n_metric": len(seen),
        }
    middle = median(seen)
    if middle == 0:
        return {"no_ratio": "the matched-cohort median of this metric is 0"}
    rows = [row for row in found if row is not None]
    evidence = Evidence.comparative(
        evidence_id=hashed_id(
            "ev", ",".join(matched.outside), "profile_ratio", f"{group}|{metric}"
        ),
        metric=f"{metric}_ratio_to_cohort_median",
        value=round(own / middle, 3),
        unit="ratio",
        coverage=_weakest([built.coverage, *(str(row["coverage"]) for row in rows)]),
        source=[built.evidence_id, *(str(row["evidence_id"]) for row in rows)],
        cohort={
            **matched.keys,
            "n": matched.total,
            "outside": len(matched.outside),
            "n_metric": len(seen),
            "captures": list(matched.outside),
        },
        reducer_version=REDUCER_VERSION,
        assumptions=list(_ASSUMPTIONS),
        warnings=_row_warnings(
            rows, len(matched.outside) - len(seen), len(matched.outside)
        ),
    )
    return {**asdict(evidence), "cohort_median": middle, "n_metric": len(seen)}


def _composition(
    members: Sequence[str], keys: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Spec 16's sample composition: what this group is MADE of, before any number.

    A key no surface stated is counted under the word `unknown` rather than dropped or
    defaulted: a group of eight whose provider column sums to six is a group whose
    composition nobody can read.
    """
    return {
        "captures": len(members),
        **{
            f"{name}s": dict(
                Counter(
                    str(keys[one][name] if keys[one][name] is not None else "unknown")
                    for one in members
                ).most_common()
            )
            for name in COHORT_KEYS
        },
        "imported": sum(1 for one in members if keys[one]["backfill"]),
    }


def _by_metric(
    store: Store, capture_id: str, seen: dict[str, dict[str, Mapping[str, Any]]]
) -> dict[str, Mapping[str, Any]]:
    """One capture's evidence rows by metric, read once per profile.

    Memoized because one capture is in more than one subsystem group and is usually in
    the cohort those groups are placed against too. The evidence table has no index on
    capture_id, so each read is a scan: measured on a copy of the owner's store on
    2026-09-02, 49 reads take 1.09 s, and the subsystem profile of this repository asks
    for 25 captures spread over 5 groups plus a 31-member cohort.
    """
    if capture_id not in seen:
        seen[capture_id] = {
            str(row["metric"]): row for row in store.evidence(capture_id)
        }
    return seen[capture_id]


def _one_unit(rows: Sequence[Mapping[str, Any]]) -> str:
    """The unit these rows agree on, or the word that says they do not state one.

    `unknown` is a word about the unit and not a number: it appears only where no
    capture in the group carried a row for the metric, and there the value is None and
    the coverage is `unavailable` beside it. Two units for one metric is the same
    refusal `cohorts._difference` makes: two reducer versions that disagree about a
    unit have no distribution between them.
    """
    units = {str(row["unit"]) for row in rows}
    return units.pop() if len(units) == 1 else "unknown"


def _weakest(words: Iterable[str]) -> str:
    """The weakest coverage word among the rows a number was read from.

    The same rule as `cohorts._weakest` and deliberately a second copy of two lines:
    that one is private and cohorts.py is not this task's file to change. If a third
    caller appears, make it public there and delete this.
    """
    found = list(words)
    return max(found, key=COVERAGE.index) if found else "unavailable"
