"""The bound a probe suite runs under, and the one number it is compared with.

W4-E09's first lesson, turned into a mechanism. That experiment's brief carried a
200,000-token stop bound and the runner could not act on it: the pilot ran one probe
three times and the suite ran every remaining repetition in one invocation, so four
sessions crossed the bound and the crossing was only visible once the last had
finished. A bound nobody can act on is a note, not a bound.

What makes it one is here and in `experiments_probe._sessions`, which compares every
finished session with it before starting the next. This module owns the three things
that comparison needs and the runners do not: what a `stop` block may say, what a
session's token total IS, and what the report records when a bound ends a run.

Its own module rather than more of experiments_probe.py for the reason
experiments_factor.py exists: that file was at 684 lines against an 800-line ratchet
that is a gate rather than a preference, and this is a seam it does not cross. Nothing
here launches a process, removes a worktree or writes anything: it reads one finished
capture back into one number and answers one yes-or-no question about it.

Two rules run through all of it. A bound is a property of the SUITE and never of one
arm, because a bound that held for one arm would end the run at a session the other arm
never had and the two would be compared at two sample sizes for a reason no reader could
see. And a session whose token total is unknown crosses nothing: "this session was under
the bound" and "nobody could tell" are different statements, only one of them is a
measurement, and `record` names every session of the second kind rather than letting it
pass as the first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telltale.experiments import SpecError
from telltale.measures import value_of
from telltale.model import to_json

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.store import Store

# The `stop` block: the two bounds, and the prose that says where they came from. The
# basis is carried into the report unread. A bound whose derivation is not written down
# beside it is a number nobody can argue with, and this file cannot check prose.
STOP_KEYS = ("max_tokens_per_session", "max_seconds_per_session", "basis")
BOUNDS = ("max_tokens_per_session", "max_seconds_per_session")

# The four counters `telltale show` prints under `usage`, which is the total a token
# bound is compared with: the summary's usage block minus model_requests, which counts
# requests and not tokens. `cohorts.VECTOR` carries three of the four and NOT
# cache_creation_tokens, so the evidence vector is not the sum a reader of `show` adds
# up, and a bound stated against `show` is read off the evidence rows directly.
STOP_TOKENS = (
    "fresh_input_tokens", "cache_read_tokens", "cache_creation_tokens", "output_tokens",
)  # fmt: skip


def checked(stop: Any) -> dict[str, Any] | None:
    """One `stop` block, or None for a suite that runs unbounded. Refuse and name why.

    Two positive whole numbers. A bound of 0 or below would end the run at its first
    session whatever that session cost, and a fractional one is not a count of tokens or
    of seconds; neither is a bound anybody meant to write.
    """
    if stop is None:
        return None
    if not isinstance(stop, dict):
        raise SpecError(
            f"spec: stop is an object carrying {list(STOP_KEYS)}, not {stop!r}"
        )
    missing = sorted(set(BOUNDS) - set(stop))
    unknown = sorted(set(stop) - set(STOP_KEYS))
    if missing or unknown:
        raise SpecError(f"spec stop: missing {missing}, unexpected {unknown}")
    for name in BOUNDS:
        value = stop[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise SpecError(
                f"spec stop: {name} is {value!r} and a bound is a positive whole"
                " number. A bound at or below 0 ends the run at its first session"
                " whatever that session cost, and a fraction is not a count"
            )
    return dict(stop)


def one_bound(specs: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The suite's one bound, or refuse and print the blocks that disagree."""
    bounds = [spec.get("stop") for spec in specs]
    if any(to_json(found) != to_json(bounds[0]) for found in bounds):
        raise SpecError(
            f"the specs of one suite carry different stop blocks ({bounds}): a bound is"
            " a property of the suite, and one that held for a single arm would end the"
            " run at a session the other arm never had"
        )
    return bounds[0]


def session_tokens(store: Store, capture_id: str) -> tuple[int | None, list[str]]:
    """The token total the bound is compared with, and the counters that were missing.

    (None, names) when any of the four is unknown, never a sum over the rest: a total
    that silently left out cache_read would be under every bound for a reason the number
    does not show. The caller records the names, and the session is compared with
    nothing.
    """
    measured = {str(row["metric"]): row for row in store.evidence(capture_id)}
    values = {name: value_of(measured.get(name)) for name in STOP_TOKENS}
    absent = sorted(name for name, value in values.items() if value is None)
    if absent:
        return None, absent
    return int(sum(float(value or 0) for value in values.values())), []


def crossed(
    stop: Mapping[str, Any] | None,
    spec: Mapping[str, Any],
    probe: Mapping[str, Any],
    run: Mapping[str, Any],
) -> dict[str, Any] | None:
    """The first bound this finished session is over, or None.

    Strictly over: a session that lands exactly on the bound spent what the bound
    allows. The bounds are tried in `BOUNDS` order and the first one wins, so a session
    over both is reported under the token bound; the report carries the observed value
    of the one that fired, and both are in `bounds`.
    """
    if stop is None:
        return None
    seconds = None if run["wall_ms"] is None else run["wall_ms"] / 1000
    observed = {BOUNDS[0]: run["session_tokens"], BOUNDS[1]: seconds}
    for name in BOUNDS:
        value = observed[name]
        if value is not None and value > stop[name]:
            return {
                "task_id": f"{spec['task_id']}-{probe['probe_id']}",
                "probe_id": str(probe["probe_id"]),
                "attempt": run["attempt"],
                "capture_id": run["capture_id"],
                "bound": name,
                "limit": stop[name],
                "observed": value,
            }
    return None


def record(
    planned: int,
    runs: Sequence[Mapping[str, Any]],
    stop: Mapping[str, Any] | None,
    found: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """What the bound was, which session ended the run, and what never started."""
    return {
        "bounds": None if stop is None else dict(stop),
        "stopped": found is not None,
        "sessions_planned": planned,
        "sessions_run": len(runs),
        "crossed": None if found is None else dict(found),
        # The sessions whose token total no evidence row carried, so the token bound
        # could not be evaluated for them at all. Empty is the ordinary case and the key
        # is always present: a key that is only sometimes there makes a reader who does
        # not see it guess which case they are in.
        "token_total_unknown": [
            {
                "capture_id": run["capture_id"],
                "missing": list(run["session_tokens_missing"]),
            }
            for run in runs
            if run["session_tokens"] is None
        ],
    }
