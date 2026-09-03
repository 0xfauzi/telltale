"""What an attempt's outcomes say about the change it landed. Three post-merge columns.

Design 6.12's one-step candidate protocol forecasts what is NOT known when a merge
decision is taken: how long the merge verification took, whether it failed, and whether
the change was reverted or repaired soon after. All three are statements about a change
AFTER it landed, and the only surface that carries one is an `external.outcome` an
orchestrator posted. So this module reads outcomes and nothing else.

A separate file from series_lineage.py, which was at 772 lines against the 800-line
ratchet, and the cut is the same one series_paths.py made: everything here is a decision
about what an OUTCOME means, while series_lineage.py folds a repository into rows.
Nothing here reads the store and nothing here runs git.

Three rules, and each of them is a way of saying that an unknown stays unknown.

  A verification nobody posted is not a passing verification.
  `merge_verification_failed` is 1 for a fail, 0 for a pass and None for no outcome at
  all, and None again for a status word outside the two vocabularies below: "the
  harness said nothing" and "the harness said it passed" are different facts and a 0
  would merge them.

  A duration nobody stated is not zero milliseconds. `telltale outcome --duration-ms N`
  is the one way a duration reaches an outcome, and an outcome posted without it leaves
  the cell None however long the verification really took.

  `rework_within_3` is a DELAYED label. It is not decidable on the last three changes of
  a lineage, whatever the outcomes say, because the window it asks about has not
  happened yet. Those rows are None and the cohort says why.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telltale import repo
from telltale import series as compiler

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# The three columns of design 6.12 this module fills, in the order the change clock
# lists them. They sit immediately before env_changed, which is appended last on both
# clocks because it is a function of the row fingerprints.
POST_MERGE_COLUMNS = (
    "merge_verification_ms",
    "merge_verification_failed",
    "rework_within_3",
)

# Two of the five outcome kinds of design 6.3. The other three (adversarial_review,
# merge_decision, runtime_signal) are read by the attempt clock and say nothing about
# what happened to a change after it landed.
VERIFICATION_KIND = "mechanical_verification"
REWORK_KIND = "revert_or_repair"

# Design 6.12: row o is labelled once three more changes have landed. The same constant
# is `forecast.REWORK_TAIL`, which limits the protocol's origins; it is spelled again
# here rather than imported because `telltale.forecast` is the laboratory and this is
# the collector, and a build must not depend on the package behind the extras.
REWORK_TAIL = 3

# What an outcome STATUS means. `telltale outcome --status` takes free text, because an
# orchestrator's vocabulary is its own, so this table is the one place a word becomes a
# number. A word it does not carry leaves the cell None: an unrecognised status is not a
# pass, and it is not a fail either. Read by the attempt clock too (series_lineage), so
# there is one table rather than two that can drift apart.
PASSED = frozenset(
    {"pass", "passed", "ok", "success", "succeeded", "merged", "accepted", "approved"}
)
FAILED = frozenset(
    {"fail", "failed", "error", "rejected", "reverted", "abandoned", "blocked"}
)

# One sentence per reason a cell above is unknown, for the cohort's `unknown_columns`.
# Each names what would fix it, which is the whole reason a sentence is carried rather
# than a coverage word: `partial` says a column has holes and says nothing about them.
NO_VERIFICATION = (
    f"no {VERIFICATION_KIND} outcome on the attempt that landed the change:"
    f" `telltale outcome --kind {VERIFICATION_KIND} --status S --task-id X --attempt N`"
    " is what records one"
)
UNREADABLE_STATUS = (
    f"the {VERIFICATION_KIND} outcome carries a status word that is neither a pass"
    f" ({', '.join(sorted(PASSED))}) nor a fail ({', '.join(sorted(FAILED))})"
)
NO_DURATION = (
    f"the {VERIFICATION_KIND} outcome states no duration_ms: it was posted without"
    " `--duration-ms N`, or by a writer older than W5-T1"
)
NO_TAIL = (
    f"fewer than {REWORK_TAIL} later changes in this lineage, so the delayed label of"
    " design 6.12 is not decidable on this row yet"
)
NO_IDENTITY = (
    "the commit is linked to no capture that named a task_id and an attempt, so there"
    f" is no identity a {REWORK_KIND} outcome could name"
)
UNREADABLE_TIMESTAMP = (
    f"a {REWORK_KIND} outcome or a commit carries a timestamp with no UTC offset, so"
    " whether the rework fell inside the window cannot be told"
)


def passed(status: Any) -> int | None:
    """1 for a pass, 0 for a fail, None for a word neither table carries."""
    word = str(status or "").strip().lower()
    if word in PASSED:
        return 1
    if word in FAILED:
        return 0
    return None


def of_kind(rows: Sequence[Mapping[str, Any]], kind: str) -> list[Mapping[str, Any]]:
    """The outcome activities of one kind, in clock order."""
    return sorted(
        (
            row
            for row in rows
            if row["activity_type"] == "outcome" and row["fields"].get("kind") == kind
        ),
        key=compiler.order,
    )


def columns(
    landed: Sequence[Mapping[str, Any]],
    identities: Sequence[tuple[str, int]],
    deadline: str | None,
) -> tuple[list[float | None], dict[str, str]]:
    """The three cells for one change, and one sentence per cell that is unknown.

    `landed` is every activity of every attempt that landed this change, `identities`
    the (task_id, attempt) pairs of those attempts, and `deadline` the commit time of
    the third following change, or None when this lineage has fewer than three left.

    The values and the reasons come out together because they are one decision: a cell
    is None exactly when a reason applies, and computing the two separately is how the
    two drift into disagreeing about the same row.
    """
    ms, failed, verdict = _verification(landed)
    rework, tail = _rework(landed, identities, deadline)
    reasons = {
        POST_MERGE_COLUMNS[0]: verdict[0],
        POST_MERGE_COLUMNS[1]: verdict[1],
        POST_MERGE_COLUMNS[2]: tail,
    }
    return (
        [ms, failed, rework],
        {name: why for name, why in reasons.items() if why is not None},
    )


def _verification(
    landed: Sequence[Mapping[str, Any]],
) -> tuple[float | None, float | None, tuple[str | None, str | None]]:
    """(duration, failed flag) off the LAST mechanical_verification, with the reasons.

    The last one rather than any one, and rather than a sum over all of them: the
    attempt clock's `verification_passed` reads the last status for the reason that an
    outcome revising an earlier one is the answer, and a duration taken from a
    superseded outcome would be the duration of a verification that was corrected.
    """
    found = of_kind(landed, VERIFICATION_KIND)
    if not found:
        return None, None, (NO_VERIFICATION, NO_VERIFICATION)
    fields = found[-1]["fields"]
    stated = compiler.number(fields.get("duration_ms"))
    state = passed(fields.get("status"))
    return (
        stated,
        None if state is None else float(1 - state),
        (
            None if stated is not None else NO_DURATION,
            None if state is not None else UNREADABLE_STATUS,
        ),
    )


def _rework(
    landed: Sequence[Mapping[str, Any]],
    identities: Sequence[tuple[str, int]],
    deadline: str | None,
) -> tuple[float | None, str | None]:
    """1 when a rework naming this change landed inside the window, 0 when none did."""
    if deadline is None:
        return None, NO_TAIL
    if not identities:
        return None, NO_IDENTITY
    wanted = set(identities)
    events = [
        row for row in of_kind(landed, REWORK_KIND) if _names(row["fields"], wanted)
    ]
    inside = [_within(compiler.position(row), deadline) for row in events]
    if any(cell is None for cell in inside):
        return None, UNREADABLE_TIMESTAMP
    return (1.0 if any(inside) else 0.0), None


def _names(fields: Mapping[str, Any], wanted: set[tuple[str, int]]) -> bool:
    """Whether an outcome names one of the attempts that landed this change.

    `component_id` rather than a task_id field: external.outcome has no task_id in the
    allowlist of design 6.3, so `telltale outcome` and `experiments._outcome` both put
    the task there, and this is the same fact read back.
    """
    task = fields.get("component_id")
    attempt = fields.get("attempt")
    if not isinstance(task, str) or isinstance(attempt, bool):
        return False
    return isinstance(attempt, int) and (task, attempt) in wanted


def _within(stamp: str, deadline: str) -> bool | None:
    """Whether one timestamp is at or before another, or None when one cannot be read.

    Parsed rather than compared as text. A commit time is whole seconds
    (`2026-09-03T12:34:56Z`) and an activity position carries microseconds
    (`2026-09-03T12:34:56.123456Z`), and `"Z" > "."`, so string order puts an event a
    tenth of a second BEFORE the deadline after it. Measured, not assumed: that is a
    real pair of formats in this store.
    """
    try:
        return repo.parse_ts(stamp) <= repo.parse_ts(deadline)
    except ValueError:
        return None
