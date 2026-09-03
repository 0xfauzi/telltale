"""The three walks over an ordered capture: epochs, revisits and stable-state intervals.

Split out of measures.py because that file is at the 800-line ratchet and because these
three are the only measures that depend on ORDER rather than on a count or a sum. Every
function here takes activities and returns plain numbers; nothing reads the store,
nothing writes an Evidence, and nothing prints. measures.py wraps each result with the
coverage and the source list that make it evidence.

The order is the timeline's order (report._order): started_at, then the primary
observation, which is arrival. It is stated once, in `ordered`, because three different
walks would otherwise each have their own idea of what "later" means and two of them
would be wrong on the parallel tool calls that genuinely share a start.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from telltale.model import Activity

# What counts as work continuing inside a stable-state interval (spec 13.5: "model/tool
# activity"). A repo_snapshot is not work: it is Telltale looking, not the agent acting.
WORK_TYPES = (
    "model_request",
    "verification_run",
    "file_read",
    "file_edit",
    "command",
    "tool_call",
)

# Spec 13.2: an edit to a file within N edits after a failed verification.
POST_FAILURE_WINDOW = 3

# The four counters that make up "tokens inside" an interval.
_TOKEN_FIELDS = (
    "input_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "output_tokens",
)


def ordered(activities: Sequence[Activity]) -> list[Activity]:
    """The capture in timeline order. The one definition of "later" in this module."""
    return sorted(
        activities,
        key=lambda item: (
            item.started_at,
            str(item.fields.get("primary_observation") or ""),
            item.activity_type,
        ),
    )


# -- edit epochs and revisits ---------------------------------------------------------
@dataclass(frozen=True)
class Epochs:
    """Maximal runs of file_edits with no verification_run between them. Spec 13.1."""

    with_verification: int
    without_verification: int
    edits_after_last_success: int | None
    sources: list[str]


def epochs(activities: Sequence[Activity]) -> Epochs:
    """Count the edit epochs, and the edits that came after the last passing run.

    An epoch is closed by the verification_run that follows it, so every epoch except
    possibly the last one is `with_verification` by construction. The last is `without`
    exactly when the capture ended before anything verified those edits, which is the
    statement spec 13.1 is asking for. It is not a judgement: an epoch without
    verification may be a capture that ended mid-thought.

    `edits_after_last_success` is None when a run whose outcome nobody stated sits after
    the last KNOWN success, or when no success is known and some run's outcome is not.
    Either way the position of the last passing run is unknown, so the edits after it
    cannot be counted: the answer would be a count from a place that may not be where
    the last success was. The 0 survives only where every run in the capture stated an
    outcome, which is the sentence below it.
    """
    closed = 0
    open_epoch = False
    since_success = 0
    seen_success = False
    unknown_since_success = False
    sources: list[str] = []
    for item in ordered(activities):
        if item.activity_type == "file_edit":
            sources.append(item.activity_id)
            open_epoch = True
            since_success += 1
        elif item.activity_type == "verification_run":
            sources.append(item.activity_id)
            if open_epoch:
                closed += 1
                open_epoch = False
            outcome = failed(item)
            if outcome is None:
                unknown_since_success = True
            elif outcome is False:
                seen_success = True
                since_success = 0
                unknown_since_success = False
    return Epochs(
        with_verification=closed,
        without_verification=1 if open_epoch else 0,
        # Zero edits after a test that never passed is not "the tree was verified": with
        # no successful run there is no "last successful test" to count after.
        edits_after_last_success=(
            None if unknown_since_success else since_success if seen_success else 0
        ),
        sources=sources,
    )


def revisits(edits: Sequence[Activity]) -> int | None:
    """Edits to a file this capture had already edited. Three answers, not two.

    No edits at all is 0 revisits. Edits that name paths is the count. Edits where no
    path was observable is None, for the reason measures_spec13._distinct gives: "it
    edited nothing" and "what it edited could not be seen" are different statements.
    """
    paths = [item.fields["file_path"] for item in edits if "file_path" in item.fields]
    if not edits:
        return 0
    if not paths:
        return None
    seen: set[str] = set()
    count = 0
    for path in paths:
        count += path in seen
        seen.add(path)
    return count


def post_failure_revisits(activities: Sequence[Activity]) -> int | None:
    """Edits made within POST_FAILURE_WINDOW edits of a failed verification. Spec 13.2.

    The window is counted in EDITS and not in seconds, because a capture's wall clock
    includes the agent thinking and a capture on a slower machine would otherwise report
    a different number for the same work.

    None when any verification run in the capture states no outcome: a run nobody saw
    the end of may have been the failure that opened a window, so the edits after it are
    inside a window or outside it and this walk cannot tell which.
    """
    runs = [item for item in activities if item.activity_type == "verification_run"]
    if not outcomes_known(runs):
        return None
    budget = 0
    count = 0
    for item in ordered(activities):
        if item.activity_type == "verification_run":
            if failed(item):
                budget = POST_FAILURE_WINDOW
        elif item.activity_type == "file_edit" and budget:
            count += 1
            budget -= 1
    return count


def failed(run: Activity) -> bool | None:
    """Did this verification run fail. Three answers, and the exit status comes first.

    Spec 13.1 defines a verification activity by its EXIT STATUS, and where a surface
    supplies one it is the answer. Measured on Codex S1: the run of
    `UV_CACHE_DIR= uv run pytest` recorded by rollout item exec-7984baec carries exit
    code 1 and status "failed" while `codex.otel.tool_result.success` is true, so a
    reader of `success` alone counted a failing test run as a passing one.

    On Claude no successful call carries an exit code at all (E01: one exists only on a
    failed stream result), so `success` is the only answer there and absence of an exit
    code is not absence of an outcome.

    A MASKED run is None before either field is read, and the order matters. Since
    W4-F3 the row of a masked chain carries the CALL's own success (design 6.10), so
    reading `success` here would answer "did the check pass" with "did `tail` exit 0".
    The exit code is not on such a row at all (W4-T3), and the guard does not depend on
    that staying true.
    """
    if run.fields.get("exit_masked"):
        return None
    code = run.fields.get("exit_code")
    if isinstance(code, int):
        return code != 0
    success = run.fields.get("success")
    return not success if isinstance(success, bool) else None


def outcomes_known(runs: Sequence[Activity]) -> bool:
    """True when every run in scope states an outcome. Vacuously true for none.

    What the callers need is not "how many failed" but "may a count of failures be
    printed at all", and that is one question over the whole scope: a count over the
    runs whose outcome happened to be visible is a count of the visible failures, which
    is not what any of the four metrics that ask this claim to be.
    """
    return all(failed(run) is not None for run in runs)


def fail_to_pass(runs: Sequence[Activity]) -> int | None:
    """Failed verifications followed by a passing one of the same category. Spec 13.1.

    Counted per category, so a failing test followed by a passing lint is not a cycle.
    The pairing is the FIRST pass after a fail: a category that failed once and passed
    three times is one cycle, not three.

    None when any run in scope states no outcome: an unknown run may be the failure a
    later pass closes, or the pass that closes an earlier failure, so a cycle count over
    the rest is a count over a sequence that is not the one the session ran.
    """
    if not outcomes_known(runs):
        return None
    failing: set[str] = set()
    cycles = 0
    for item in ordered(runs):
        category = str(item.fields.get("category") or "")
        outcome = failed(item)
        if outcome is True:
            failing.add(category)
        elif outcome is False and category in failing:
            cycles += 1
            failing.discard(category)
    return cycles


def reversions(snapshots: Sequence[Activity]) -> int:
    """Snapshot diff fingerprints equal to one seen earlier here. Spec 13.2."""
    seen: set[str] = set()
    count = 0
    previous: str | None = None
    for item in ordered(snapshots):
        current = item.fields.get("diff_hash")
        if not isinstance(current, str):
            continue
        # Only a CHANGE back counts. Two consecutive snapshots with one fingerprint are
        # one state observed twice, which is what the snapshot trigger produces when a
        # tool call touched nothing.
        if current != previous and current in seen:
            count += 1
        seen.add(current)
        previous = current
    return count


# -- stable-state work intervals ------------------------------------------------------
@dataclass(frozen=True)
class Interval:
    """One maximal interval where neither fingerprint moved. Spec 13.5."""

    operations: int
    duration_ms: float | None
    tokens: int | None
    ended_by: str
    sources: list[str]


# What ended an interval, in the words spec 13.5 uses. `edit` is the diff fingerprint
# moving, which is a snapshot observing what an edit did; `verification` is the
# verification signature moving; `capture_end` is neither, and the capture stopped.
ENDINGS = ("edit", "verification", "capture_end")


def intervals(activities: Sequence[Activity]) -> list[Interval]:
    """The stable-state work intervals of one capture, in order. Spec 13.5.

    The state is the pair (latest snapshot diff_hash, latest verification signature),
    and the signature is (category, success, repository hash at the time). Work
    activities are grouped into maximal runs that share one state; the run ends at
    whichever half of the pair moved next.

    It is deliberately not called "no progress": every interval here is an interval in
    which the agent was reading, running commands and calling the model. What it says is
    that the tree and the last verification result looked the same the whole time.
    """
    runs = _runs(activities)
    if not runs:
        return []
    endings = [
        "edit" if runs[index + 1][0][0] != state[0] else "verification"
        for index, (state, _group) in enumerate(runs[:-1])
    ]
    return [
        Interval(
            operations=len(group),
            duration_ms=_span(group),
            tokens=_tokens(group),
            ended_by=ending,
            sources=[item.activity_id for item in group],
        )
        for (_key, group), ending in zip(runs, [*endings, "capture_end"], strict=True)
    ]


def _runs(
    activities: Sequence[Activity],
) -> list[tuple[tuple[Any, Any], list[Activity]]]:
    """Work activities cut into maximal runs that share one state."""
    runs: list[tuple[tuple[Any, Any], list[Activity]]] = []
    for state, item in _walk(activities):
        if not runs or runs[-1][0] != state:
            runs.append((state, []))
        runs[-1][1].append(item)
    return runs


def _walk(activities: Sequence[Activity]) -> Iterator[tuple[tuple[Any, Any], Activity]]:
    """Each work activity paired with the state that was in force when it happened."""
    diff: Any = None
    verified: tuple[Any, ...] = ()
    for item in ordered(activities):
        if item.activity_type == "repo_snapshot":
            diff = item.fields.get("diff_hash")
        elif item.activity_type == "verification_run":
            verified = (item.fields.get("category"), failed(item), diff)
        if item.activity_type in WORK_TYPES:
            yield (diff, verified), item


def _span(group: Sequence[Activity]) -> float | None:
    """Milliseconds from the first start to the last end, on the PROVIDER clock only.

    An activity whose `clock` is `arrival` is timestamped by the receiver, so a span
    that mixed the two would be partly a measurement of Telltale. None when fewer than
    two rows in the interval carry a provider clock: one instant is not a duration.
    """
    stamps = [
        moment
        for item in group
        if item.fields.get("clock") == "provider"
        for moment in (_moment(item.started_at), _moment(item.ended_at))
        if moment is not None
    ]
    if len(stamps) < 2:
        return None
    # Three decimals: `model.now_iso` and every provider clock measured so far report
    # microseconds, so a millisecond carries three digits of measurement and no more.
    return round((max(stamps) - min(stamps)) * 1000.0, 3)


def _moment(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return None


def _tokens(group: Sequence[Activity]) -> int | None:
    """Every token the model requests inside this interval accounted for. None if none.

    None rather than 0 when the interval holds no model request: an interval of tool
    calls between two requests spent no tokens the reducer can see, and 0 would claim
    the model was called and returned nothing.

    None too when a request in the interval is missing one of the four counters. The
    docstring's first word is `every`, and skipping an absent counter would keep the
    word while dropping the tokens: on a Claude capture with no OTel surface, W4-T4
    leaves output_tokens unset on every request (the stream states no output count), and
    a sum over the other three would report a session's spend short by the whole of its
    output. An unknown addend makes the sum unknown; measures_spec13.SESSION_FIELDS is
    where a provider's session figure is read instead, and it cannot be split per
    interval.
    """
    requests = [item for item in group if item.activity_type == "model_request"]
    if not requests:
        return None
    counts = [item.fields.get(name) for item in requests for name in _TOKEN_FIELDS]
    if not all(isinstance(value, int) for value in counts):
        return None
    return sum(int(value) for value in counts if isinstance(value, int))
