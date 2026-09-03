"""What a shadow advisory looks like on the page. Design 6.12, spec 14.6 and 17.2.

One page per candidate: what is known about the change before it lands, and, for each
post-merge target, how strongly anything may be said about it. The order is deliberate
and it is the opposite of what a reader wants: the label and the readiness word come
BEFORE the two forecast numbers, and they are repeated in a column of every forecast
table, because a number read without them is the failure this whole page exists to
prevent.

Three rules hold the page together.

  "Confidence" is not a number this file invents. It is the decision label of the
  stored backtest plus the readiness word of the checklist, and both travel in the
  `confidence` column beside every point and every quantile. A separate percentage
  would be a fifth quantity nothing measured.

  A target with no stored backtest prints `no assessable forecast` and no numbers. That
  is a result rather than a gap: the strongest thing that may be said about a pair
  nobody scored is that it was not scored.

  Everything below goes through `forecast.refuse_words` before it leaves this module,
  and `cli_advise.emit` renders before it stores. A sentence this file may not print is
  a sentence the store may not hold either (ADR-014).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telltale.forecast import QUANTILE_LEVELS, refuse_words
from telltale.forecast.candidate import (
    CONDITIONED,
    POINT_AT,
    UNCONDITIONED,
)
from telltale.report import render_table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# The last line of every advisory, and the whole of spec 14.6 that is true today:
# forecasts are shadow-only during primary evaluation. A policy that ACTS on one writes
# a `policy.intervention` observation; `telltale advise` never writes one.
SHADOW = "This advisory is shadow: it is stored and printed and nothing acts on it."

# What the A block prints where a column has no value. Never 0: a binary file leaves
# lines_added unknown because lines are not the unit there, and 0 lines added is a
# different statement about a different change.
UNKNOWN = "unknown"

_BLOCK = ("column", "value")
_TESTS = ("test", "lhs", "rhs", "holds")
_FORECAST = ("run", "forecaster", "point", "q10", "q50", "q90", "confidence")
_DIFFERENCE = (
    "statistic",
    "forecaster",
    "conditioned minus unconditioned",
    "confidence",
)
_SHORT = 12
_INDENT = "  "

# The three quantiles of the nine that a reader of a merge decision is offered. The
# indexes are read off QUANTILE_LEVELS rather than written as 0, 4 and 8, so a registry
# that ever adds a level does not silently move which number this prints.
_Q10 = QUANTILE_LEVELS.index(0.1)
_Q90 = QUANTILE_LEVELS.index(0.9)
_QUANTILES = (("q10", _Q10), ("q50", POINT_AT), ("q90", _Q90))
_BAND_NAMES = tuple(pair[0] for pair in _QUANTILES)


def render(record: Mapping[str, Any]) -> str:
    """The whole advisory on one page. Raises ForbiddenWord before it returns.

    Called by `cli_advise.emit` BEFORE the observation is appended, which is the order
    design 6.12 fixes: the word refusal runs before anything is printed and before
    anything is stored, and a check that ran after the INSERT would be a check of a row
    already on the disk.
    """
    lines = [
        *_header(record),
        "",
        render_table(_block_rows(record["features"]), _BLOCK),
    ]
    for entry in record["targets"]:
        lines += ["", *_target(entry, str(record["sentence"]))]
    lines += ["", SHADOW]
    return refuse_words("\n".join(lines))


def stored(row: Mapping[str, Any]) -> dict[str, Any]:
    """One stored advisory as `telltale show` prints it. Spec 17.2.

    `coverage` reads `advisory` and leads, as it does on a session summary: this record
    is neither a capture nor a measure, and a reader has to know that before reading
    the fields under it.

    `claim_class` says `observed` because every value in the payload was READ rather
    than computed here: the A block off a git diff, the label copied off the stored
    decision of the run named in forecast_run_ids. The warning underneath says where
    the strength of that label actually comes from, since nothing may present a number
    as stronger evidence than the layer that produced it.
    """
    payload = dict(row["payload"])
    return {
        "coverage": "advisory",
        "capture_id": row["capture_id"],
        "observation_type": row["observation_type"],
        "provider": row["provider"],
        "surface": row["surface"],
        "adapter": row["adapter"],
        "ingest_ts": row["ingest_ts"],
        "repo_id": row["repo_id"],
        "claim_class": "observed",
        "payload": payload,
        "redaction": dict(row["redaction"]),
        "warnings": [
            SHADOW,
            "every label in this payload carries the claim class of the forecast_runs"
            " row named beside it in forecast_run_ids, which is predictive; the"
            " advisory copied the word and computed no forecast of its own",
            "readiness is the one word this advisory derived: the first line of design"
            " 6.12's eight-line checklist that failed on this series, or `ready` when"
            " none did. It is a property of the series, not of the candidate",
        ],
    }


# -- the page -------------------------------------------------------------------------


def _header(record: Mapping[str, Any]) -> list[str]:
    return [
        f"telltale advise  {record['advisory_id']}  action {record['action']}"
        f"  policy_version {record['policy_version']}",
        f"base {_short(record['base_sha'])} ({record['base_ref']})"
        f"..head {_short(record['head_sha'])} ({record['head_ref']})"
        f"  merge_base {_short(record['merge_base'])}",
        f"series {record['series_id']}  clock {record['clock']}"
        f"  {record['rows']} rows  built {record['built_at']}",
        f"created_at {record['created_at']}  repo {_short(record['repo_id'])}",
        "",
        "A block: what is known about this change before it lands",
    ]


def _block_rows(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"column": name, "value": UNKNOWN if value is None else value}
        for name, value in block.items()
    ]


def _target(entry: Mapping[str, Any], sentence: str) -> list[str]:
    """One target: readiness, label, the inequalities, the pair, and the sentence."""
    lines = [
        f"target {entry['target']} ({entry['unit']})",
        f"  readiness: {entry['readiness']}{_detail(entry['readiness_detail'])}",
        f"  label: {entry['label']}{_source(entry['run'])}",
    ]
    if entry["decision_reason"]:
        lines.append(f"  reason: {entry['decision_reason']}")
    lines += ["", *_indent(_tests(entry["inequalities"]))]
    lines += ["", *_forecast(entry)]
    lines += ["", f"  {sentence}"]
    lines += [f"  {line}" for line in entry["licences"]]
    return lines


def _tests(inequalities: Sequence[Mapping[str, Any]]) -> str:
    """Every comparison the stored decision evaluated, with both sides of it."""
    rows = [
        {
            "test": item["test"],
            "lhs": _round(item["lhs"]),
            "rhs": _round(item["rhs"]),
            "holds": "yes" if item["holds"] else "no",
        }
        for item in inequalities
    ]
    return render_table(rows or [{"test": "none evaluated"}], _TESTS)


def _forecast(entry: Mapping[str, Any]) -> list[str]:
    """Both forecasts of the row this change becomes, or the reason there are none."""
    found = entry["forecast"]
    if found is None:
        return [f"  forecast: not run. {entry['forecast_note']}"]
    confidence = f"{entry['label']} / {entry['readiness']}"
    head = (
        f"  forecast of row {found['origin']} (the row this change becomes),"
        f" context [{found['ctx_start']}, {found['origin']}) = {found['n_ctx']} rows,"
        f" padding_mode {found['padding_mode']}"
    )
    return [
        head,
        "",
        *_indent(render_table(_forecast_rows(found, confidence), _FORECAST)),
        "",
        *_indent(render_table(_difference_rows(found, confidence), _DIFFERENCE)),
    ]


def _forecast_rows(found: Mapping[str, Any], confidence: str) -> list[dict[str, Any]]:
    """One row per (run, forecaster). `confidence` rides on every one of them.

    Beside every number rather than once above the table, because the label and the
    readiness word are the only thing that says how strongly the number may be read,
    and a reader who copies one row out of this table has to carry them with it.
    """
    return [
        {
            "run": run,
            "forecaster": name,
            "point": _round(one["point"][0]),
            **_band(one),
            "confidence": confidence,
        }
        for run in (UNCONDITIONED, CONDITIONED)
        for name, one in found["runs"][run].items()
    ]


def _band(one: Mapping[str, Any]) -> dict[str, Any]:
    """The three quantiles of step 1, or three unknowns for a point-only forecaster."""
    quantiles = one["quantiles"]
    step = quantiles[0] if quantiles else None
    return {
        name: None if step is None else _round(step[index])
        for name, index in _QUANTILES
    }


def _difference_rows(found: Mapping[str, Any], confidence: str) -> list[dict[str, Any]]:
    """Conditioned minus unconditioned, per forecaster, on the point and the band.

    The paired difference of design 6.12 at origin N. There is no actual here and so no
    MAE: row N does not exist yet, and the sentence under this table is what the number
    means and what it does not.
    """
    plain = found["runs"][UNCONDITIONED]
    fitted = found["runs"][CONDITIONED]
    rows = []
    for name in fitted:
        before, after = plain.get(name), fitted[name]
        if before is None:
            continue
        rows.append({
            "statistic": "point",
            "forecaster": name,
            "conditioned minus unconditioned": _round(
                after["point"][0] - before["point"][0]
            ),
            "confidence": confidence,
        })  # fmt: skip
        rows += _band_differences(name, before, after, confidence)
    return rows


def _band_differences(
    name: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    confidence: str,
) -> list[dict[str, Any]]:
    left, right = _band(before), _band(after)
    return [
        {
            "statistic": label,
            "forecaster": name,
            "conditioned minus unconditioned": None
            if left[label] is None or right[label] is None
            else _round(right[label] - left[label]),
            "confidence": confidence,
        }
        for label in _BAND_NAMES
    ]


# -- small things ---------------------------------------------------------------------


def _source(run: Mapping[str, Any] | None) -> str:
    if run is None:
        return " (no stored true-order backtest of this target on this series)"
    return (
        f" (from {run['forecast_run_id']}, variant {run['variant']},"
        f" run {run['created_at']}, model {run['model']})"
    )


def _detail(detail: str | None) -> str:
    return "" if not detail else f"  ({detail})"


def _indent(text: str) -> list[str]:
    return [f"{_INDENT}{line}" for line in text.splitlines()]


def _short(value: Any) -> str:
    return UNKNOWN if value is None else str(value)[:_SHORT]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
