"""Spec 13.7's evidence vector, and the cohort a percentile may be taken over.

Split out of measures.py rather than added to it: that file stood at 799 lines against
the 800-line ratchet with this code in it, which is the ratchet doing its job. Nothing
here writes to the store and nothing here computes a metric. Every raw number in a
vector is READ BACK out of the evidence table that measures.py wrote, so a vector and a
`telltale show` of one capture cannot disagree about a number.

A percentile is built through `Evidence.comparative`, printed and returned, and it is
NEVER written to the store. A percentile is a statement about a cohort on a date: it
changes the moment a capture sharing the four cohort keys is added, so a stored row
would freeze one comparison and then present it as a property of the capture. The
invariant that decides it is the reducer's idempotence. Two rebuilds of one capture must
write byte-identical rows, and a number that depends on which other captures happen to
be in the database cannot promise that. The difference `compare` prints is the same kind
of number for the same reason, and it is not stored either.

Design 6.11's last paragraph is the cohort rule: same provider, runtime major version,
model and content level, at least ten captures, imported captures excluded unless asked.
It is a GATE and not a statistic. Below ten the column says why there is no percentile
rather than printing a rank nobody should read, because a rank among three captures is
mostly a statement about which three.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from telltale.correlate import REDUCER_VERSION, hashed_id
from telltale.facts import text
from telltale.measures import value_of
from telltale.model import COVERAGE, Evidence

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from telltale.store import Store

# The six families of spec 13.7 and the metrics each is read from. A constant rather
# than an argument: a family whose membership changes per call is a family two readers
# cannot compare. 22 rows, and every value in them is read back out of the evidence
# table rather than recomputed here.
VECTOR: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "context_token_burden",
        ("model_requests", "fresh_input_tokens", "cache_read_tokens", "output_tokens"),
    ),
    (
        "edit_turnover",
        (
            "unique_files_changed",
            "file_revisits",
            "max_diff_lines",
            "final_diff_lines",
            "reversions",
        ),
    ),
    (
        "verification_cycles",
        (
            "agent_test_runs",
            "failed_test_runs",
            "fail_to_pass_cycles",
            "edit_epochs_without_verification",
        ),
    ),
    (
        "exploration_scope",
        (
            "unique_files_read",
            "search_ops",
            "directories_traversed",
            "read_to_edit_ratio",
        ),
    ),
    (
        "stable_state_work",
        ("stable_state_work_intervals", "stable_state_total_ms", "stable_state_tokens"),
    ),
    ("compactions", ("compactions", "pre_compaction_tokens")),
)

# Spec 13.7 gives the last family a raw count and a coverage word and NO percentile, so
# its rows say that in the percentile column rather than leaving it blank.
_RAW_ONLY = "compactions"
NO_PERCENTILE = "spec 13.7: raw count and coverage, no percentile"

# Design 6.11's cohort: these four keys equal, and at least this many members.
COHORT_KEYS = ("provider", "runtime_major", "model", "content_level")
COHORT_MIN = 10

# What one vector row carries beside its percentile. `value` first because the rest are
# strings read off the same evidence row, and the value is the one that may be null.
_CELL = ("value", "unit", "coverage", "claim_class", "evidence_id")

_ASSUMPTIONS = (
    "cohort: same provider, runtime major version, model and content level"
    f" (design 6.11), and at least {COHORT_MIN} captures",
    "imported (backfill) captures are excluded unless --include-backfill was given",
)
_PARTIAL = (
    "at least one cohort member measured this partially, so the rank is over values"
    " that were not all seen the same way"
)
_DIFFERENCE = (
    "b minus a, two captures compared directly and not against any cohort: they may"
    " differ in provider, runtime, model, content level and task"
)


def cohort_keys(store: Store, capture_id: str) -> dict[str, Any]:
    """The four cohort keys of design 6.11, the backfill flag, and what is unknown.

    Each key is read where the capture actually carries it, and that is three places
    rather than one. provider is on the capture lifecycle activity. runtime_version is
    the session_start lifecycle field `claude_code_version` and NOT the environment
    fingerprint's runtime_version: the launcher probes `<binary> --version` only for a
    binary it has a provider module for, so the fingerprint's field is null for every
    other child. model is the fingerprint's when the launcher parsed one out of argv,
    else the one model the session named. content_level is the fingerprint's alone, so
    a capture with no telltale.environment observation, which is every replayed and
    every imported one, has no cohort and says so.

    `reason` names EVERY key that is unknown and is None when all four are known. It is
    never an empty string and no key is ever defaulted: an unknown key excludes the
    capture from every cohort, including one made of captures exactly like it.
    """
    rows = store.activities(capture_id)
    fingerprint, started = _payloads(store, capture_id)
    models = _model_names(rows)
    keys: dict[str, Any] = {
        "provider": text(_capture_fields(rows).get("provider")),
        "runtime_major": _major(_runtime_version(rows)),
        "model": text(fingerprint.get("model")) or _one_model(models),
        "content_level": _whole(fingerprint.get("content_level")),
        "backfill": started.get("argv_shape") == "backfill",
    }
    keys["reason"] = _reason(keys, models)
    return keys


def cohort(store: Store, capture_id: str, include_backfill: bool = False) -> list[str]:
    """The captures this one may be ranked against, itself included, in id order.

    Empty when this capture has no key of its own: a cohort of one capture whose keys
    nobody knows is not a smaller cohort, it is a comparison that cannot be stated.
    """
    return _members(store, cohort_keys(store, capture_id), include_backfill)


def _members(store: Store, own: Mapping[str, Any], include_backfill: bool) -> list[str]:
    """The scan, given the keys already read once. Design 6.11.

    One `cohort_keys` per capture and no more. Each of them reads that capture's whole
    observation list, because content_level lives in a payload and there is no reader
    for one observation type. Measured on the owner's 36-capture, 30715-observation
    store: 225 ms of `vector`'s 245 ms is this scan, and it grows with the total size of
    the database rather than with the size of the capture being reported on.
    """
    if own["reason"] is not None or (own["backfill"] and not include_backfill):
        return []
    found = []
    for row in store.captures():
        capture = str(row["capture_id"])
        keys = cohort_keys(store, capture)
        if (include_backfill or not keys["backfill"]) and _matches(keys, own):
            found.append(capture)
    return sorted(found)


def percentile(values: Sequence[float | None], own: float) -> float:
    """Mid-rank of `own` among `values`, in per cent, to one decimal.

    100 * (below + 0.5 * equal) / n over the values that were MEASURED. Mid-rank rather
    than "strictly below" because ties are the common case: ten captures of one task
    share a value for every metric the task does not move, and counting a tie as below
    would report 0.0 for all ten of them.
    """
    seen = _present(values)
    if not seen:
        raise ValueError("a percentile needs at least one measured value")
    below = sum(1 for value in seen if value < own)
    equal = sum(1 for value in seen if value == own)
    return round(100.0 * (below + 0.5 * equal) / len(seen), 1)


def vector(
    store: Store, capture_id: str, include_backfill: bool = False
) -> dict[str, Any]:
    """Spec 13.7's evidence vector: {family: {metric: cell}}, 22 cells.

    The value and the coverage of a cell come off the SAME evidence row, so a metric
    whose coverage is unavailable carries a null value beside the word that says why.
    `percentile` is either a comparative Evidence or a statement of what stopped it.
    """
    mine = _by_metric(store, capture_id)
    keys = cohort_keys(store, capture_id)
    members = _members(store, keys, include_backfill)
    pool = _Cohort(
        keys=keys,
        members=members,
        rows={one: _by_metric(store, one) for one in members}
        if len(members) >= COHORT_MIN
        else {},
        absent=_absent(keys, members, include_backfill),
    )
    return {
        family: {
            name: _cell(mine.get(name), _placed(pool, family, name, mine.get(name)))
            for name in names
        }
        for family, names in VECTOR
    }


def sides(
    store: Store, a: str, b: str, include_backfill: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The two halves `report.compare` prints: two vectors, and b minus a.

    The difference sits on b because it IS b minus a. Each one is an Evidence built
    through the comparative constructor with both evidence ids as its source, and none
    of them is written: a difference is a statement about two captures rather than a
    property of either.
    """
    first = vector(store, a, include_backfill)
    second = vector(store, b, include_backfill)
    left: dict[str, Any] = {"capture_id": a, "vector": first}
    right: dict[str, Any] = {
        "capture_id": b,
        "vector": second,
        "difference": _differences(first, second),
    }
    return left, right


@dataclass(frozen=True)
class _Cohort:
    """The members a percentile is taken over, and their evidence rows by metric."""

    keys: dict[str, Any]
    members: list[str]
    rows: dict[str, dict[str, Mapping[str, Any]]]
    # The {"no_cohort": ...} cell every metric gets when there is no cohort at all.
    absent: dict[str, Any] | None


def _absent(
    keys: Mapping[str, Any], members: Sequence[str], include_backfill: bool
) -> dict[str, Any] | None:
    """Why this capture has no cohort, or None when it has one. Never a blank."""
    if keys["reason"] is not None:
        return {"no_cohort": keys["reason"]}
    if keys["backfill"] and not include_backfill:
        return {"no_cohort": "backfill excluded"}
    if len(members) < COHORT_MIN:
        return {"no_cohort": len(members)}
    return None


def _placed(
    pool: _Cohort, family: str, name: str, own: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The percentile cell: a comparative Evidence, or what stopped it being one.

    The no-cohort answer comes first even for the family spec 13.7 gives no percentile,
    because it is the more general fact and it is true of the whole vector.
    """
    if pool.absent is not None:
        return dict(pool.absent)
    if family == _RAW_ONLY:
        return {"no_cohort": NO_PERCENTILE}
    value = None if own is None else value_of(own)
    if value is None:
        return {"no_cohort": "value unknown"}
    found = [pool.rows[one].get(name) for one in pool.members]
    values = [None if row is None else value_of(row) for row in found]
    if len(_present(values)) < COHORT_MIN:
        return {"no_cohort": len(_present(values))}
    return _ranked(pool, name, value, found)


def _ranked(
    pool: _Cohort, name: str, own: float, found: Sequence[Mapping[str, Any] | None]
) -> dict[str, Any]:
    """One percentile, as the Evidence it is. Built, printed and never stored."""
    rows = [row for row in found if row is not None]
    values = [None if row is None else value_of(row) for row in found]
    measured = len(_present(values))
    evidence = Evidence.comparative(
        evidence_id=hashed_id("ev", ",".join(pool.members), "percentile", name),
        metric=f"{name}_percentile",
        value=percentile(values, own),
        unit="percentile",
        coverage=_weakest(str(row["coverage"]) for row in rows),
        source=[str(row["evidence_id"]) for row in rows],
        cohort={
            **{key: pool.keys[key] for key in COHORT_KEYS},
            "n": len(pool.members),
            "n_metric": measured,
            "captures": list(pool.members),
        },
        reducer_version=REDUCER_VERSION,
        assumptions=list(_ASSUMPTIONS),
        warnings=_member_warnings(rows, len(pool.members)),
        # The wall clock, unlike every Evidence `summarize` writes: this number is a
        # statement about the captures that were in the database at this moment.
    )
    return {**asdict(evidence), "n": len(pool.members), "n_metric": measured}


def _member_warnings(rows: Sequence[Mapping[str, Any]], members: int) -> list[str]:
    out = [_PARTIAL] if any(row["coverage"] == "partial" for row in rows) else []
    missing = members - len(rows)
    if missing:
        out.append(f"{missing} of {members} cohort members have no row for this metric")
    return out


def _cell(row: Mapping[str, Any] | None, placed: dict[str, Any]) -> dict[str, Any]:
    """One vector row. The value and the coverage come off the same evidence row.

    A metric with no row at all is not a zero either: every column is null and the
    percentile cell says what stopped the comparison.
    """
    if row is None:
        return {**dict.fromkeys(_CELL), "percentile": placed}
    return {
        "value": value_of(row),
        **{name: str(row[name]) for name in _CELL[1:]},
        "percentile": placed,
    }


def _differences(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    return {
        family: {
            name: _difference(name, a[family][name], b[family][name]) for name in names
        }
        for family, names in VECTOR
    }


def _difference(
    name: str, a: Mapping[str, Any], b: Mapping[str, Any]
) -> dict[str, Any] | None:
    """b minus a as an Evidence, or None when either side has no value to subtract.

    None rather than 0: "the two agreed" and "one of them could not be seen" are the
    two statements design invariant 5 exists to keep apart, and a difference column is
    where they would otherwise share a spelling. The unit check is the same rule about
    cardinality: two captures reduced by two reducer versions may not report one metric
    in one unit, and then there is no difference to state.
    """
    if a["value"] is None or b["value"] is None or a["unit"] != b["unit"]:
        return None
    evidence = Evidence.comparative(
        evidence_id=hashed_id(
            "ev", f"{a['evidence_id']}|{b['evidence_id']}", "difference", name
        ),
        metric=f"{name}_difference",
        value=_amount(b["value"] - a["value"]),
        unit=str(a["unit"]),
        coverage=_weakest((str(a["coverage"]), str(b["coverage"]))),
        source=[str(a["evidence_id"]), str(b["evidence_id"])],
        reducer_version=REDUCER_VERSION,
        assumptions=[_DIFFERENCE],
    )
    return asdict(evidence)


def _amount(value: float) -> float | int:
    """Six decimals on a float. The stored numbers carry no more, and 0.1 + 0.2 does."""
    return value if isinstance(value, int) else round(value, 6)


def _by_metric(store: Store, capture_id: str) -> dict[str, Mapping[str, Any]]:
    return {str(row["metric"]): row for row in store.evidence(capture_id)}


def _present(values: Sequence[float | None]) -> list[float]:
    """The values that were measured. A None has no place in the order.

    Design invariant 5 at the one point where it decides a comparison: a capture whose
    coverage made a metric null has no rank, and counting it as 0 would put it below
    every capture that measured one.
    """
    return [value for value in values if value is not None]


def _weakest(words: Iterable[str]) -> str:
    """The weakest coverage word among the rows a number was read from."""
    found = list(words)
    return max(found, key=COVERAGE.index) if found else "unavailable"


def _matches(keys: Mapping[str, Any], own: Mapping[str, Any]) -> bool:
    return all(keys[name] == own[name] for name in COHORT_KEYS)


def _payloads(store: Store, capture_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The telltale.environment and telltale.capture_started payloads, or empty dicts.

    Read off the observations because neither becomes an activity of its own. An empty
    dict is "the capture has no such observation", which is what a replayed capture has
    and is why it is outside every cohort.
    """
    found: dict[str, dict[str, Any]] = {}
    for row in store.observations(capture_id):
        kind = str(row["observation_type"])
        if kind in ("telltale.environment", "telltale.capture_started"):
            found.setdefault(kind, dict(row["payload"]))
    return found.get("telltale.environment", {}), found.get(
        "telltale.capture_started", {}
    )


def _capture_fields(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for row in rows:
        fields = dict(row["fields"])
        if fields.get("event") == "capture":
            return fields
    return {}


def _runtime_version(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for row in rows:
        fields = dict(row["fields"])
        if fields.get("event") == "session_start":
            found = text(fields.get("claude_code_version"))
            if found is not None:
                return found
    return None


def _model_names(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """The models this session named, the same way the summary's session block does."""
    listed = _capture_fields(rows).get("models")
    if isinstance(listed, list) and listed:
        return sorted({str(name) for name in listed})
    return sorted(
        {
            str(fields["model"])
            for row in rows
            if row["activity_type"] == "model_request"
            and (fields := dict(row["fields"])).get("model")
        }
    )


def _one_model(models: Sequence[str]) -> str | None:
    """The model, when the session named exactly one. Two models are not one cohort."""
    return models[0] if len(models) == 1 else None


def _major(version: str | None) -> str | None:
    """The text before the first dot when the version starts with a digit, else all.

    "2.1.258" is major "2". "fake-agent" is "fake-agent": a runtime whose version is
    not a number has no major version, and splitting it on the first dot would put
    every unnumbered runtime in one cohort named after a prefix nobody chose.
    """
    if version is None:
        return None
    return version.split(".")[0] if version[0].isdigit() else version


def _reason(keys: Mapping[str, Any], models: Sequence[str]) -> str | None:
    """Every cohort key that is unknown, named. None when all four are known.

    Every one, not the first: an imported transcript is missing the runtime version AND
    the content level, and a reader told only about the first would fix the fingerprint
    and find the capture still outside every cohort.
    """
    found = []
    if keys["provider"] is None:
        found.append("provider unknown")
    if keys["runtime_major"] is None:
        found.append("runtime version unknown")
    if keys["model"] is None:
        found.append("mixed models" if len(models) > 1 else "model unknown")
    if keys["content_level"] is None:
        found.append("content level unknown")
    return ", ".join(found) or None


def _whole(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
