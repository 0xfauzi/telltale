"""What a run projects a Series through before it scores anything. Design 6.12 step (2).

Three projections live here, and each is a rule about what a number may be computed
over rather than a convenience.

  `retained` is the frame a target is forecast on: the rows where that target is KNOWN,
  in lineage order. Design 6.12 step (2) reads "keep only rows where the target's
  coverage is observed", which is row-level, and W8-T3 implements it because every
  post-merge column of a change lineage has holes by construction. A commit with no
  check run has no merge_verification_ms, the last three rows of a lineage have no
  rework_within_3 and the first three have no rework_within_3_lag3. Dropping a WINDOW
  for such a hole, which is what the backtester does for a covariate, kept 16 of 146
  windows for merge_verification_ms on this repository's own history and 0 of 143 for
  the lagged label at H = 4, because every context of a lineage shorter than
  MAX_CONTEXT starts at row 0. Excluding the ROW is an exclusion by a named policy,
  which AGENTS.md invariant 5 allows; nothing here imputes, and the count and the row
  keys ride in the run so that a reader is told which rows were not there.

  `retained` takes a second set of columns, and W8-F1 added it for the candidate
  protocol. A hole in the conditioning block used to refuse the whole run: `lines_added`
  and `lines_removed` are unknown on a commit that touched a binary file, git numstat
  says `-` there, and two such commits in 169 refused every origin above the lower of
  them. Naming the block to `retained` makes that hole cost the ROWS it sits on, the
  same policy the target already ran under, and both runs of a pair are built from the
  one frame so their origins stay identical. The block itself is never narrowed: a run
  that dropped two of six columns would be the unconditioned run wearing the
  conditioned run's name.

  `scored` cuts a window record down to the steps a protocol scores. The candidate
  protocol at H = 4 scores step 4 alone: at origin o the fourth step of
  `rework_within_3_lag3` is change o's own label, and steps 1 to 3 are the labels of
  changes o - 3 .. o - 1, which are not the question that was asked.

  `edge_padded` extends a past-future covariate block past step 1 by replicating the
  candidate row's value. The model does the same thing internally to a horizon
  covariate shorter than n_ctx + H; doing it here means a stored run can say what the
  forecaster saw instead of leaving a reader to know it.

`threshold` is here for the same reason the first projection is: tau is q80 of the
first c_min rows OF THE FRAME A RUN SCORES, and a tau read off the parent series would
be computed over rows that run never saw.

The identity is the point of `retained`: it returns the parent unchanged when no row
was excluded, so every run over a target with no holes is the run it was before this
module existed, down to the series id.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from telltale.forecast import TARGETS
from telltale.forecast.baselines import quantile

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.model import Series

# The cohort keys `retained` adds, and the word a run records when it added none.
# In the cohort rather than beside the rows because a cohort is what a series id and a
# report are made of: two frames of one lineage that excluded different rows are two
# frames, and a reader of either is told which rows are not in it.
RETAINED_ROWS = "retained_rows"
EXCLUDED_ROWS = "excluded_rows"
EXCLUDED_ROW_KEYS = "excluded_row_keys"
EXCLUDED_REASON = "excluded_reason"
# The row keys each NAMED column of `retained` cost, keyed by column. Per column and
# not one merged list, because "which column cost a row" and "how many rows a column
# cost" are two questions and a run that answered only the first would leave a reader
# unable to tell one binary commit from twenty. Absent when no named column cost a row.
EXCLUDED_BY_COLUMN = "excluded_by_column"
REASON = "target unknown"


def retained(series: Series, target: str, columns: Sequence[str] = ()) -> Series:
    """`series` over the rows where `target` and every column of `columns` is known.

    `columns` is the conditioning block of a paired protocol. Named here rather than
    handled at the window, so a hole in it costs the ROW it sits on under the same
    policy the target runs under, and so both runs of a pair are cut from one frame and
    keep identical origins.

    The parent object is returned, not a copy of it, when nothing was excluded. That
    identity is what the E13 invariant rests on: a request-clock run over an observed
    target hashes to the same series id and stores the same row it stored before, and
    a block with no hole in it changes nothing about the frame either.
    """
    named = [name for name in dict.fromkeys(columns) if name != target]
    at = {name: _index(series, name) for name in (target, *named)}
    keep = _keep(series, at)
    if len(keep) == len(series.rows):
        return series
    if len(series.row_meta) != len(series.rows):
        raise ValueError(
            f"series {series.series_id} has {len(series.rows)} rows and"
            f" {len(series.row_meta)} row_meta entries: a row that cannot be named"
            " cannot be reported as excluded"
        )
    gone = sorted(set(range(len(series.rows))) - set(keep))
    dropped = [series.row_meta[row_at].row_key for row_at in gone]
    cost = _cost(series, at, gone)
    block = {name: keys for name, keys in cost.items() if name in named and keys}
    return replace(
        series,
        series_id=f"{series.series_id}:{target}",
        cohort={
            **series.cohort,
            RETAINED_ROWS: len(keep),
            EXCLUDED_ROWS: len(dropped),
            EXCLUDED_ROW_KEYS: dropped,
            EXCLUDED_REASON: _reason(target, len(cost[target]), block),
            **({EXCLUDED_BY_COLUMN: block} if block else {}),
        },
        rows=[series.rows[row_at] for row_at in keep],
        row_meta=[series.row_meta[row_at] for row_at in keep],
        changepoints=_moved(series.changepoints, keep),
    )


def _keep(series: Series, at: Mapping[str, int]) -> list[int]:
    """The rows where every column of `at` is known, in lineage order."""
    return [
        row_at
        for row_at, row in enumerate(series.rows)
        if all(row[position] is not None for position in at.values())
    ]


def _cost(
    series: Series, at: Mapping[str, int], gone: Sequence[int]
) -> dict[str, list[str]]:
    """The row keys each column of `at` is unknown on, over the excluded rows.

    A row missing two of them is ONE row in `EXCLUDED_ROW_KEYS` and appears under each
    column here: how many rows went and which column cost a row are two questions.
    """
    return {
        name: [
            series.row_meta[row_at].row_key
            for row_at in gone
            if series.rows[row_at][position] is None
        ]
        for name, position in at.items()
    }


def _reason(target: str, target_cost: int, cost: Mapping[str, Sequence[str]]) -> str:
    """Which columns cost rows and how many each cost, in one sentence.

    The target alone reads exactly as it did before the block was nameable, so a run
    over a target with no block keeps the reason W8-T3 stored. With a block the target
    gets its own count too, because "26 rows went and lines_added cost 2 of them" and
    "26 rows went, 2 more for lines_added" are different frames.
    """
    if not cost:
        return f"target {target} unknown"
    parts = [f"target {target} unknown on {target_cost} rows"] if target_cost else []
    parts += [f"{name} unknown on {len(keys)} rows" for name, keys in cost.items()]
    return "; ".join(parts)


def excluded(frame: Series) -> dict[str, Any]:
    """The exclusion record a run carries: how many rows, which ones, and why.

    Absence of the cohort key is the identity case and nothing else, because `retained`
    writes it whenever it excludes anything: 0 here is a measurement rather than a
    default taken over structure this module did not write. The reason is the frame's
    own, so a run says which columns cost it rows rather than repeating one constant.
    """
    keys = [str(key) for key in frame.cohort.get(EXCLUDED_ROW_KEYS, [])]
    return {
        "count": len(keys),
        "row_keys": keys,
        "reason": str(frame.cohort.get(EXCLUDED_REASON) or REASON),
    }


def for_columns(frame: Series) -> dict[str, Any]:
    """The rows the NAMED columns of `retained` cost, deduplicated, and the per-column
    counts. Empty on a frame built without a block, and on one whose block had no hole.

    A run records this beside `excluded` rather than instead of it: the rows a block
    cost are a fact about the conditioning and the rows the target cost are a fact
    about the outcome, and adding them would be a number about neither.
    """
    by_column = frame.cohort.get(EXCLUDED_BY_COLUMN) or {}
    keys = list(dict.fromkeys(str(key) for one in by_column.values() for key in one))
    return {
        "count": len(keys),
        "row_keys": keys,
        "by_column": {name: len(one) for name, one in by_column.items()},
    }


def assumption(frame: Series) -> list[str]:
    """The one sentence a run over a frame with excluded rows has to state.

    A list of zero or one, so a caller splices it into an assumption list without a
    branch, exactly as `series_lineage.uncaptured` is spliced. What it has to say that
    the count alone does not is what the context now MEANS: the row before an origin is
    the previous change with a known target, and not the previous change.

    A frame built with a conditioning block says the same thing about one more set of
    columns, and names them: a reader told only that the target was unknown would have
    the wrong count of commits with a check run.
    """
    found = excluded(frame)
    if not found["count"]:
        return []
    total = found["count"] + len(frame.rows)
    if not frame.cohort.get(EXCLUDED_BY_COLUMN):
        return [
            f"{found['count']} of {total} rows were excluded because the target was"
            " unknown on them; the context is the lineage without those rows, so the"
            " previous row means the previous change with a known target."
        ]
    return [
        f"{found['count']} of {total} rows were excluded because the target or a"
        " column of the conditioning block was unknown on them"
        f" ({found['reason']}); the context is the lineage without those rows, so the"
        " previous row means the previous change with a known target and a readable"
        " block."
    ]


def scored(record: Mapping[str, Any], steps: Sequence[int] | None) -> Mapping[str, Any]:
    """One window record cut to `steps`, which are 1-based. `None` is every step.

    The record is not mutated and the untouched keys ride along, so an origin, a
    y_{o-1} and a flag list still pair a projected record with its twin in the other
    run of a paired protocol.
    """
    if steps is None:
        return record
    at = [step - 1 for step in steps]
    if any(one < 0 or one >= len(record["actual"]) for one in at):
        raise ValueError(
            f"steps {list(steps)} are not inside a horizon of"
            f" {len(record['actual'])}: a step nobody forecast cannot be scored"
        )
    return {
        **record,
        "actual": [record["actual"][one] for one in at],
        "forecasts": {
            name: {
                **entry,
                "point": [entry["point"][one] for one in at],
                "quantiles": None
                if entry["quantiles"] is None
                else [entry["quantiles"][one] for one in at],
            }
            for name, entry in record["forecasts"].items()
        },
    }


def edge_padded(values: list[float], horizon: int) -> list[float]:
    """`values` extended to length len(values) + horizon - 1 by repeating the last.

    The last value is the candidate row's own, so the padding says "and nothing changes
    after the candidate". That is a statement about the covariate and not about the
    target, and it is recorded in the run because it is the model's own rule made
    visible rather than a number anybody measured.
    """
    if not values:
        raise ValueError("an empty covariate block has no last value to replicate")
    return [*values, *[values[-1]] * (horizon - 1)]


def threshold(series: Series, target: str, c_min: int | None = None) -> float | None:
    """tau under the registry's rule, or None when it is not computable.

    q80 of the first c_min rows of the target, over the frame the caller handed in.
    The readiness checklist asks this of the same frame the backtester will score, so
    check 8 answers about the tau the lead-time metric will use.
    """
    floor = TARGETS[target].c_min if c_min is None else c_min
    index = _index(series, target)
    head = [row[index] for row in series.rows[:floor]]
    if len(head) < floor or any(value is None for value in head):
        return None
    return quantile([float(value) for value in head if value is not None], 0.8)


def _index(series: Series, target: str) -> int:
    return [column.name for column in series.columns].index(target)


def _moved(changepoints: Sequence[int], keep: Sequence[int]) -> list[int]:
    """Each changepoint at the number of retained rows before it, deduplicated.

    A changepoint is a boundary BETWEEN rows, so its new position is how many retained
    rows sit below it. 0 and len(keep) are dropped: a boundary at either end of the
    frame divides nothing, and `regime_start` already treats row 0 as one.
    """
    moved = {bisect_left(keep, point) for point in changepoints}
    return sorted(point for point in moved if 0 < point < len(keep))
