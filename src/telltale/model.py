"""The four durable shapes, plus the identifiers and timestamps everything else uses.

Design 6.1 and 6.2. `Observation` is the only thing a provider module produces,
`Activity` the only thing measures read, `Evidence` the only way a derived number is
written or returned, and `Series` the only input to a forecaster.

Two invariants live here rather than in a comment somewhere downstream:

  A claim class is never upgraded. There is no `Evidence.observed` and no
  `Evidence.causal` constructor, and `__post_init__` refuses either word, so a module
  that wants to present a computed number as an observation has to change this file
  and say so in a diff.

  Unknown stays unknown. Every optional field is `None`, never 0 and never "". A
  reducer that cannot see a value must leave it out; the coverage field is what says
  whether the absence means "did not happen" or "could not be seen".
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Both closed vocabularies, and both are also CHECK constraints in store.py. Two copies
# of one list is a real cost; the alternative is a database that accepts a claim class
# the model refuses, which is the failure this system exists to prevent.
CLAIM_CLASSES: tuple[str, ...] = ("derived", "comparative", "associative", "predictive")
COVERAGE: tuple[str, ...] = ("observed", "partial", "derived", "unavailable")

SCHEMA_VERSION = 1

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_RANDOM_BITS = 80
_ULID_CHARS = 26

_ulid_lock = threading.Lock()
_last_ms = -1
_last_random = 0


def _fresh_random() -> int:
    return int.from_bytes(os.urandom(_RANDOM_BITS // 8), "big")


def _crockford(value: int) -> str:
    out = bytearray(_ULID_CHARS)
    for index in range(_ULID_CHARS - 1, -1, -1):
        out[index] = ord(_CROCKFORD[value & 0x1F])
        value >>= 5
    return out.decode("ascii")


def ulid() -> str:
    """A 26-character Crockford base32 id whose sort order is generation order.

    48 bits of millisecond time and 80 bits of randomness. The randomness is a COUNTER
    within one millisecond rather than a fresh draw: two observations recorded in the
    same millisecond by two request threads would otherwise sort in random order, and
    the observation_id IS the arrival order (design 6.2). `max(now, _last_ms)` is the
    other half of that promise: a clock step backwards must not produce an id that
    sorts before one already handed out.
    """
    global _last_ms, _last_random
    with _ulid_lock:
        now_ms = max(time.time_ns() // 1_000_000, _last_ms)
        if now_ms == _last_ms:
            _last_random += 1
            if _last_random >> _RANDOM_BITS:
                # 2**80 ids inside one millisecond is not reachable on this hardware.
                # The branch exists so that the counter can never wrap to a smaller id.
                now_ms = _last_ms + 1
                _last_random = _fresh_random()
        else:
            _last_random = _fresh_random()
        _last_ms = now_ms
        value = (now_ms << _RANDOM_BITS) | _last_random
    return _crockford(value)


def new_id(prefix: str) -> str:
    """`act_01J...`: a ULID behind a prefix that says which table the id belongs to.

    Evidence.source mixes activity ids and observation ids (design 6.2), so `explain`
    has to know which table to look in from the id alone. Observation ids carry no
    prefix: design 6.2 fixes them as bare ULIDs.
    """
    return f"{prefix}_{ulid()}"


def now_iso() -> str:
    """The current time as ISO 8601 UTC with a Z suffix, to microseconds.

    Timestamps are TEXT in SQLite (design 6.5), so they are compared as strings: a
    fixed-width UTC spelling is what makes `ORDER BY ingest_ts` and `WHERE ingest_ts <
    cutoff` mean what they say. `+00:00` is spelled `Z` for the same reason, since the
    two spellings of the same instant do not compare equal as text.
    """
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def to_json(value: Any) -> str:
    """Canonical JSON: sorted keys, no incidental whitespace.

    Sorted because ids are hashes of payloads elsewhere (the environment fingerprint of
    design 6.3), and two spellings of one dict would be two fingerprints.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def from_json(text: str) -> Any:
    return json.loads(text)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class Observation:
    """One sanitized provider fact, append-only. Design 6.2."""

    observation_id: str
    capture_id: str
    observation_type: str
    surface: str
    provider: str
    adapter: str
    ingest_ts: str
    provider_ts: str | None = None
    provider_session_id: str | None = None
    environment_fingerprint_id: str | None = None
    repo_id: str | None = None
    schema_version: int = SCHEMA_VERSION
    correlation_ids: dict[str, str] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    redaction: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Activity:
    """A rebuildable projection of observations. Design 6.2 and 6.10."""

    activity_id: str
    capture_id: str
    activity_type: str
    actor: str
    started_at: str
    ended_at: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, list[str]] = field(default_factory=dict)
    reducer_version: str = ""
    claim_class: str = "derived"

    def __post_init__(self) -> None:
        # The activities table of design 6.5 has no claim_class column, so any other
        # value would be silently lost on the way back out. An activity is derived by
        # definition: it is recomputed from observations by a named reducer.
        _require(
            self.claim_class == "derived",
            f"an activity is always derived, not {self.claim_class!r}",
        )


@dataclass(frozen=True)
class Evidence:
    """A number a reader will see, with the claim it supports attached. Design 6.2.

    Build one with `Evidence.derived`, `.comparative`, `.associative` or `.predictive`.
    There is deliberately no constructor for `observed` (a computed number is not an
    observation) and none for `causal` (spec 2.1: ordinary observational data cannot
    support it).
    """

    evidence_id: str
    metric: str
    value: float | int | None
    unit: str
    claim_class: str
    coverage: str
    source: list[str]
    reducer_version: str
    capture_id: str | None = None
    cohort: dict[str, Any] | None = None
    environment_fingerprint_id: str | None = None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Set when the number was computed, not when it was written: the two differ by a
    # queue, and the first is the one a reader wants beside the value.
    created_at: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        _require(
            self.claim_class in CLAIM_CLASSES,
            f"claim_class {self.claim_class!r} is not one of {CLAIM_CLASSES}",
        )
        _require(
            self.coverage in COVERAGE,
            f"coverage {self.coverage!r} is not one of {COVERAGE}",
        )
        # A number with no source is a number nobody can check. `unavailable` is the one
        # exception because there is nothing to point at: the measure could not be seen.
        _require(
            bool(self.source) or self.coverage == "unavailable",
            f"{self.metric}: source is empty and coverage is {self.coverage!r},"
            " so nothing supports this number",
        )

    @classmethod
    def _make(cls, claim_class: str, **kwargs: Any) -> Evidence:
        kwargs.setdefault("evidence_id", new_id("ev"))
        return cls(claim_class=claim_class, **kwargs)

    @classmethod
    def derived(cls, **kwargs: Any) -> Evidence:
        """A number computed from this capture's own activities."""
        return cls._make("derived", **kwargs)

    @classmethod
    def comparative(cls, **kwargs: Any) -> Evidence:
        """A number placed against a stated cohort. `cohort` records which one."""
        return cls._make("comparative", **kwargs)

    @classmethod
    def associative(cls, **kwargs: Any) -> Evidence:
        """A co-occurrence. The permitted verb is "accompanies", never "causes"."""
        return cls._make("associative", **kwargs)

    @classmethod
    def predictive(cls, **kwargs: Any) -> Evidence:
        """A forecast. The permitted verb is "forecasts", never "will"."""
        return cls._make("predictive", **kwargs)


@dataclass(frozen=True)
class ColumnSpec:
    """One column of a Series: what it holds and how well it was seen."""

    name: str
    unit: str
    role: str  # target | past_covariate | future_covariate
    coverage: str

    def __post_init__(self) -> None:
        _require(
            self.coverage in COVERAGE,
            f"coverage {self.coverage!r} is not one of {COVERAGE}",
        )


@dataclass(frozen=True)
class RowMeta:
    """What a Series row was built from, and when it closed.

    `row_end_ts` is the invariant the no-look-ahead check of design 6.12 tests: every
    provenance observation's provider_ts must be at or before it.
    """

    row_key: str
    row_end_ts: str
    env_fingerprint_id: str | None = None
    provenance: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Series:
    """The only input to a forecaster. Design 6.2 and 6.12.

    `rows` holds `None` for unknown and is never zero-filled: a gap is excluded or
    refuses the build by the named missingness policy, and no policy imputes.
    """

    series_id: str
    clock: str  # request | attempt | change
    cohort: dict[str, Any]
    columns: list[ColumnSpec]
    rows: list[list[float | None]]
    row_meta: list[RowMeta]
    changepoints: list[int]
    missingness_policy: str  # exclude | refuse
    reducer_version: str
    built_at: str = field(default_factory=now_iso)


@dataclass(frozen=True)
class ForecastResult:
    """What a forecaster returns. Design 6.2. Its claim class is fixed."""

    forecaster: str
    horizon: int
    point: list[list[float]]
    quantile_levels: list[float]
    targets: list[str]
    covariates: list[str]
    missingness_policy: str
    checkpoint: str | None = None
    quantiles: list[list[list[float]]] | None = None
    warnings: list[str] = field(default_factory=list)
    claim_class: str = "predictive"

    def __post_init__(self) -> None:
        _require(
            self.claim_class == "predictive",
            f"a forecast is predictive, not {self.claim_class!r}",
        )
