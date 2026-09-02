"""What a condition MEASURES: the per-capture vector, the statistics over it, and
the one-environment check.

Split out of experiments.py, which RUNS a condition. The seam is the store: nothing in
this module starts a process, removes a worktree or posts to the receiver, and nothing
in it writes. It reads captures back and turns them into numbers, so it can be read
without knowing how a capture was made.

Nothing here writes an Evidence row. `vector()` READS the rows measures.py wrote at
capture end, so a repetition's numbers and a `telltale vector` of the same capture
cannot disagree; a row written here for a statistic over five captures would be deleted
by the next `store.rebuild()` anyway. Per-capture numbers are derived, and the
statistics over them are comparative WITHIN one condition. Both claim classes are
carried in the report experiments.py writes.
"""

from __future__ import annotations

from statistics import median, quantiles
from typing import TYPE_CHECKING, Any

from telltale.cohorts import VECTOR
from telltale.measures import value_of
from telltale.model import to_json

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.store import Store

# The scale that makes the median absolute deviation an estimator of the standard
# deviation of a normal sample. Design 6.12 fixes it; it is a constant, not a fit.
MAD_SCALE = 1.4826

# The result-message fields that are NOT measures. The token counts are gone from this
# tuple because spec 13.7's context_token_burden family carries them, read back out of
# the evidence table; these two the provider states about the session as a whole and no
# reducer computes.
_RESULT_KEYS = ("num_turns", "duration_ms")

_TOOL_PREFIX = "tool_calls."
_STREAM_PREFIX = "claude.stream."


class FingerprintMismatch(ValueError):
    """Two captures in one condition ran in two environments. Names the fields."""


class NotReduced(ValueError):
    """A capture with no evidence row at all. Names the capture."""


def vector(store: Store, capture_id: str) -> dict[str, float | None]:
    """The per-capture numbers: spec 13.7's evidence vector, and what only a stream has.

    The 22 metrics of `cohorts.VECTOR`, keyed "family.metric" and read back out of the
    evidence table rather than recomputed, so a repetition's numbers and a
    `telltale vector` of the same capture cannot disagree about one. Beside them the
    three facts a stream carries that no reducer measures: the child's own duration_ms,
    its turn count, and its tool calls counted by name.

    A capture with NO evidence row is REFUSED by id. Since W2-T6 every launcher capture
    reduces itself at capture end, so an empty evidence table means the reduction did
    not run, and 22 unknowns would enter the statistics as a measured absence.

    Unknown stays None everywhere else. A metric whose evidence row carries a null value
    is None here, and so is every stream fact of a capture whose stream surface
    delivered nothing: "the agent made no tool calls" and "no tool call was observable"
    are different facts.
    """
    measured = {str(row["metric"]): row for row in store.evidence(capture_id)}
    if not measured:
        raise NotReduced(
            f"{capture_id} has no evidence row: it was never reduced, and a vector of"
            " unknowns here would be a measurement of a missing reducer"
        )
    out: dict[str, float | None] = {
        f"{family}.{name}": value_of(measured.get(name))
        for family, names in VECTOR
        for name in names
    }
    out.update(_stream_facts(store, capture_id))
    return out


def _stream_facts(store: Store, capture_id: str) -> dict[str, float | None]:
    """duration_ms, num_turns and the tool calls by name. None when there was no stream.

    `tool_calls` is the total, and it is the field `_filled` reads to tell a capture
    that used no Edit from a capture whose stream nobody saw.
    """
    rows = store.observations(capture_id)
    streamed = any(
        str(row["observation_type"]).startswith(_STREAM_PREFIX) for row in rows
    )
    result = next(
        (
            row["payload"]
            for row in rows
            if row["observation_type"] == "claude.stream.result"
        ),
        None,
    )
    out: dict[str, float | None] = {
        key: None if result is None else _number(result.get(key))
        for key in _RESULT_KEYS
    }
    calls = [
        str(row["payload"]["tool_name"])
        for row in rows
        if row["observation_type"] == "claude.stream.assistant"
        and row["payload"].get("tool_name")
    ]
    out["tool_calls"] = float(len(calls)) if streamed else None
    for name in sorted(set(calls)):
        out[f"{_TOOL_PREFIX}{name}"] = float(calls.count(name))
    return out


def _filled(
    vectors: Sequence[Mapping[str, float | None]],
) -> list[dict[str, float | None]]:
    """Give every vector every metric, with the one distinction that matters.

    A tool name is a column only because some capture used it. In a capture whose
    stream WAS observed, a name that never appears is a real zero. In a capture with no
    stream at all it is unknown, and `tool_calls` being None is what says so.
    """
    columns = sorted({name for one in vectors for name in one})
    out: list[dict[str, float | None]] = []
    for one in vectors:
        seen = one.get("tool_calls") is not None
        filled = dict(one)
        for name in columns:
            if name in filled:
                continue
            filled[name] = 0.0 if (seen and name.startswith(_TOOL_PREFIX)) else None
        out.append(filled)
    return out


def stats(values: Sequence[float | None]) -> dict[str, Any]:
    """n, median, scaled MAD, IQR, min, max and the sorted values. Never a mean alone.

    `n` counts the values that are KNOWN and `unknown` counts the rest, so a metric
    three of five captures could not report is visibly a metric of three, not of five.
    Nothing is imputed and no gap is filled: design invariant 5.
    """
    known = sorted(value for value in values if value is not None)
    empty: dict[str, Any] = {
        "n": len(known),
        "unknown": len(values) - len(known),
        "median": None,
        "mad_scaled": None,
        "iqr": None,
        "min": None,
        "max": None,
        "values": known,
    }
    if not known:
        return empty
    middle = median(known)
    if len(known) < 2:
        # Both spreads stay None. One value has a median and no dispersion: a MAD of 0
        # here would read as "this measure does not vary", and it would make the
        # minimal detectable difference of design 6.12 zero, which says every
        # difference is resolvable at n=1.
        return {**empty, "median": middle, "min": known[0], "max": known[-1]}
    quarters = quantiles(known, n=4, method="inclusive")
    return {
        **empty,
        "median": middle,
        "mad_scaled": MAD_SCALE * median([abs(value - middle) for value in known]),
        "iqr": quarters[2] - quarters[0],
        "min": known[0],
        "max": known[-1],
    }


def one_fingerprint(store: Store, capture_ids: Sequence[str]) -> str:
    """The environment fingerprint these captures share, or refuse and name the fields.

    Design 6.12: the harness asserts fingerprints identical within a condition. This is
    that assertion, and it reports WHICH fields differ, because "these are two
    environments" is not actionable and "these are two environments and the model field
    is the difference" is.
    """
    seen = {capture_id: _environment(store, capture_id) for capture_id in capture_ids}
    ids = {found for found, _payload in seen.values()}
    if len(ids) == 1 and None not in ids:
        return str(next(iter(ids)))
    differing = _differing({name: payload for name, (_id, payload) in seen.items()})
    raise FingerprintMismatch(
        f"{len(ids)} environment fingerprints across {len(seen)} captures"
        f" ({', '.join(sorted(capture_ids))}); fields that differ: {differing}"
    )


def _environment(store: Store, capture_id: str) -> tuple[str | None, dict[str, Any]]:
    for row in store.observations(capture_id):
        if row["observation_type"] == "telltale.environment":
            found = row["environment_fingerprint_id"]
            return (str(found) if found else None, dict(row["payload"]))
    return None, {}


def environment_payload(store: Store, capture_id: str) -> dict[str, Any]:
    """The telltale.environment payload of one capture, {} when it recorded none.

    The reader experiments_env.py needs and this module already had. The between-arm
    assertion of design 6.12 compares payload FIELDS rather than the ids
    `one_fingerprint` returns: "these are two environments" is not the finding, and
    which field made them two is.
    """
    _id, payload = _environment(store, capture_id)
    return payload


def _differing(payloads: Mapping[str, Mapping[str, Any]]) -> str:
    """The fingerprint fields whose value is not the same in every capture."""
    names = sorted({name for payload in payloads.values() for name in payload})
    out = []
    for name in names:
        values = {to_json(payload.get(name)) for payload in payloads.values()}
        if len(values) > 1:
            out.append(f"{name}={sorted(values)}")
    return "; ".join(out) or "none (one capture recorded no environment at all)"


def _number(value: Any) -> float | None:
    """A payload value as a float, or None. A bool is not a number here."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
