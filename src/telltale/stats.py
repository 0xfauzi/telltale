"""The between-arm statistics of design 6.12's H2 and H3 protocol. Pure functions.

Nothing here touches the store, a capture or a spec: each function takes two lists of
numbers, one per arm, and returns numbers. That is what makes them checkable against
hand computation, and one of them is checked that way in the test suite.

Three decisions are worth stating, because each looks like an omission in the code:

  The exact test is exact at every n it answers at. The count of arrangements at least
  as extreme as the observed one comes from a subset-sum convolution over the pooled
  mid-ranks rather than from listing the arrangements. It counts the same set: every
  one of the C(n_a + n_b, n_a) splits contributes, none is sampled, and no normal
  approximation appears anywhere. Listing them is not an option at the protocol's own
  largest n: 20 per arm is 137846528820 arrangements, and literal enumeration was
  measured at 48.4 ms per 184756, which is 10.0 hours for that many. The convolution
  is 8.3 ms. The literal enumeration lives in the test, where it checks this one.

  Above 20 per arm the answer is refused rather than approximated. Design 6.12 scales
  to `N = min(20, N_needed)`, so 20 is the top of the protocol; a normal approximation
  past it would be a different test wearing this one's name.

  Unknown stays None. An arm with no known values has no shift, no delta and no
  spread, and an arm of one value has a median and no dispersion, so `mad_scaled` is
  None there and every number derived from it is None too. None of them is 0.

The label vocabulary is fixed here (ADR-014 and design 6.12): the two strings below
are the only two this system may put on a between-arm difference, and neither of them
says effect of, cause or impact.
"""

from __future__ import annotations

from math import ceil, comb, sqrt
from statistics import median
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

# z_{0.975} + z_{0.80}: the two-sample normal approximation at alpha 0.05 and power
# 0.80. Design 6.12 pre-registers it as a constant of the protocol, so it is written
# here once and printed with every report rather than recomputed per run.
POWER_Z = 2.8

# The relative resolution a measure has to reach to be compared across repositories.
# Design 6.12: demotion while `MDD > 0.25 median`.
RESOLUTION = 0.25

# The largest per-arm n the exact test is computed at. Design 6.12's own ceiling.
EXACT_MAX_N = 20

# The two labels, and the whole vocabulary. "not resolved at n" is never "no effect":
# the pilot measures what it can separate, and an unresolved difference is a statement
# about n, not about the world.
MATERIAL = "material environment effect"
UNRESOLVED = "not resolved at n"

CLAIM_CLASS = "comparative"


class TooManyValues(ValueError):
    """More values per arm than the exact test is computed at. Names the ceiling."""


def hodges_lehmann(a: Sequence[float], b: Sequence[float]) -> float | None:
    """The Hodges-Lehmann shift: the median of all n_a n_b differences b - a.

    Positive means arm b is the larger one. None when either arm is empty, because a
    median of no differences is not 0.
    """
    if not a or not b:
        return None
    return median([one_b - one_a for one_b in b for one_a in a])


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Cliff's delta: `(#(b > a) - #(b < a)) / (n_a n_b)`, over all pairs.

    +1 when every value of b exceeds every value of a, -1 when the reverse, and 0
    when the wins and the losses cancel. Ties count in the denominator and in neither
    numerator term, which is why a tied pair pulls the answer towards 0 rather than
    being dropped. None when either arm is empty.
    """
    if not a or not b:
        return None
    above = sum(1 for one_b in b for one_a in a if one_b > one_a)
    below = sum(1 for one_b in b for one_a in a if one_b < one_a)
    return (above - below) / (len(a) * len(b))


def mann_whitney_exact(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    """The exact two-sided Mann-Whitney test over the observed values. (U_a, p).

    `U_a = R_a - n_a (n_a + 1) / 2`, with R_a the sum of arm a's mid-ranks in the
    pooled sample: the number of pairs where a is the larger, counting a tie as half.
    Ties are handled by mid-ranks, so the test is computed over THESE values rather
    than over a tie-free rank table.

    `p` is the share of the `C(n_a + n_b, n_a)` ways of splitting the pooled mid-ranks
    between the two arms whose U is at least as far from `n_a n_b / 2` as the observed
    one. Every arrangement is counted (see the module docstring for how, and why not
    by listing them). No normal approximation and no continuity correction: at these n
    the exact answer is affordable, and design 6.12 asks for it by name.

    Mid-ranks are doubled to integers before anything is compared or summed, so every
    comparison below is exact integer arithmetic and no tie is decided by a floating
    point equality.
    """
    if not a or not b:
        raise ValueError("the exact test needs at least one value in each arm")
    if len(a) > EXACT_MAX_N or len(b) > EXACT_MAX_N:
        raise TooManyValues(
            f"exact test not computed above n = {EXACT_MAX_N} per arm:"
            f" n_a = {len(a)}, n_b = {len(b)}"
        )
    doubled = _doubled_midranks([*a, *b])
    n_a, n_b = len(a), len(b)
    observed = sum(doubled[:n_a])
    # Both doubled: R_a under the null centres on n_a (n + 1) / 2, so U centres on
    # n_a n_b / 2 and the sum it is read off centres on this.
    centre = n_a * (n_a + 1) + n_a * n_b
    counts = _subset_sums(doubled, n_a)
    total = sum(counts.values())
    if total != comb(n_a + n_b, n_a):
        raise ValueError(f"counted {total} arrangements, not {comb(n_a + n_b, n_a)}")
    extreme = sum(
        count
        for reached, count in counts.items()
        if abs(reached - centre) >= abs(observed - centre)
    )
    return (observed - n_a * (n_a + 1)) / 2.0, extreme / total


def _doubled_midranks(pooled: Sequence[float]) -> list[int]:
    """Twice each value's mid-rank in the pooled sample, as integers.

    A mid-rank is a whole or a half, so twice one is always a whole number. Equal
    values share the average of the ranks they span, which is what makes the test
    over tied data the test of these values rather than of an arbitrary order.
    """
    order = sorted(range(len(pooled)), key=lambda index: pooled[index])
    ranks = [0] * len(pooled)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and pooled[order[end + 1]] == pooled[order[start]]:
            end += 1
        shared = (start + 1) + (end + 1)  # twice the mean of the 1-based rank span
        for index in order[start : end + 1]:
            ranks[index] = shared
        start = end + 1
    return ranks


def _subset_sums(values: Sequence[int], size: int) -> dict[int, int]:
    """How many subsets of exactly `size` values reach each sum. One convolution.

    This is the enumeration: every subset of the pooled mid-ranks contributes exactly
    once, and the assertion on the total in the caller is what says so at runtime.
    """
    counts: list[dict[int, int]] = [{} for _ in range(size + 1)]
    counts[0][0] = 1
    for value in values:
        # Downwards, so a value is never counted twice into the same subset.
        for taken in range(min(size, len(values)) - 1, -1, -1):
            target = counts[taken + 1]
            for reached, count in counts[taken].items():
                target[reached + value] = target.get(reached + value, 0) + count
    return counts[size]


def pooled_spread(s_a: float | None, s_b: float | None) -> float | None:
    """The two arms' robust spread as one number: `sqrt((s_a^2 + s_b^2) / 2)`.

    The root mean square of the two scaled MADs, which is what design 6.12's `s` is
    for a two-arm comparison. None when either arm's spread is unknown: a spread
    estimated from one arm is not the spread of the comparison, and the report prints
    s_a and s_b beside this so the two are never confused.
    """
    if s_a is None or s_b is None:
        return None
    return sqrt((s_a**2 + s_b**2) / 2.0)


def mdd(s: float | None, n: int) -> float | None:
    """Minimal detectable median difference at this n: `MDD = 2.8 s sqrt(2 / n)`.

    `n` is the per-arm count. None when the spread is unknown or n is 0, because a
    detection limit computed from an unknown spread is not a limit.
    """
    if s is None or n < 1:
        return None
    return POWER_Z * s * sqrt(2.0 / n)


def n_needed(s: float | None, pooled_median: float | None) -> int | None:
    """`N_needed = ceil(2 (2.8 s / (0.25 median))^2)`: the per-arm n that resolves it.

    The n at which MDD would fall to a quarter of the pooled median, which is design
    6.12's demotion threshold. None when the spread is unknown or the pooled median is
    0: a quarter of zero is zero, and no n resolves a difference of zero.

    The median is taken as a magnitude. Every measure this runs on is a count or a
    duration and so is non-negative; a negative median would otherwise flip the
    inequality rather than scale it.
    """
    if s is None or pooled_median is None or pooled_median == 0:
        return None
    return ceil(2.0 * (POWER_Z * s / (RESOLUTION * abs(pooled_median))) ** 2)


def compare(
    a: Sequence[float], b: Sequence[float], s_a: float | None, s_b: float | None
) -> dict[str, Any]:
    """One metric across two arms: the shift, the separation, and what n allows.

    `a` and `b` are the KNOWN values of one metric in each arm and `s_a`, `s_b` their
    scaled MADs, both computed by the caller's per-arm statistics. Everything this
    returns is comparative and the claim class travels with it.

    `n` is the smaller of the two arm counts. Design 6.12 writes the MDD for equal
    arms; when a metric is unknown in some captures the arms are unequal, and the
    smaller n is the one that bounds what the pair can resolve.
    """
    warnings: list[str] = []
    n = min(len(a), len(b))
    spread = pooled_spread(s_a, s_b)
    pooled = median([*a, *b]) if a or b else None
    limit = mdd(spread, n)
    shift = hodges_lehmann(a, b)
    row: dict[str, Any] = {
        "claim_class": CLAIM_CLASS,
        "n_a": len(a),
        "n_b": len(b),
        "n": n,
        "hl_shift": shift,
        "cliffs_delta": cliffs_delta(a, b),
        "u": None,
        "p": None,
        "s_a": s_a,
        "s_b": s_b,
        "s": spread,
        "median": pooled,
        "mdd": limit,
        "n_needed": n_needed(spread, pooled),
        "demoted": _demoted(limit, pooled),
        "label": _label(shift, limit),
        "warnings": warnings,
    }
    _fill_test(row, a, b, warnings)
    if row["label"] is None:
        warnings.append(
            "no label: the shift or the spread is unknown at this n, so neither"
            f" {MATERIAL!r} nor {UNRESOLVED!r} would be a statement about the data"
        )
    if spread == 0:
        warnings.append(
            "both arms have a scaled MAD of 0, so MDD is 0 and every nonzero shift"
            " reads as material. That is a statement about a spread of 0, which"
            " happens when over half of each arm's values are identical, and design"
            " 6.12 does not distinguish it from a spread that is merely small"
        )
    if pooled == 0:
        warnings.append(
            "the pooled median is 0, so the 0.25 relative resolution is 0 and"
            " N_needed is undefined: no n resolves a quarter of zero"
        )
    return row


def _fill_test(
    row: dict[str, Any], a: Sequence[float], b: Sequence[float], warnings: list[str]
) -> None:
    """The exact test, or the reason there is no p. Never an approximated one."""
    if not a or not b:
        warnings.append("no exact test: one arm has no known value")
        return
    try:
        row["u"], row["p"] = mann_whitney_exact(a, b)
    except TooManyValues as refusal:
        warnings.append(str(refusal))


def _demoted(limit: float | None, pooled_median: float | None) -> bool | None:
    """Design 6.12: withheld from repository comparison while `MDD > 0.25 median`.

    None rather than False when either side is unknown: "not demoted" is a decision
    that this measure resolves, and an unknown spread has not earned it.
    """
    if limit is None or pooled_median is None:
        return None
    return limit > RESOLUTION * abs(pooled_median)


def _label(shift: float | None, limit: float | None) -> str | None:
    """Design 6.12: material iff `|HL shift| > MDD`. The other answer is about n."""
    if shift is None or limit is None:
        return None
    return MATERIAL if abs(shift) > limit else UNRESOLVED
