"""Rendering for `experiment probe`. Spec 14.3.

Its own module rather than another renderer in report.py, for the reason stats.py and
experiments_env.py exist: report.py was at 635 lines against an 800-line ratchet and
another wave 4 task is adding to it. Nothing here computes: every number printed is
already a field of the report the runner built, and the paragraph below is what stops a
reader taking the tables for more than they are.

report.py's three rules hold here unchanged, and one is added. A value that is None
prints as `-` and never as 0 or blank. A number is formatted by the caller. And the
SCORE columns are printed beside the work quantities on the same page on purpose: spec
14.3's "cheap behaviour with a wrong answer is not an improvement" is a rule about
reading, and it can only be followed if both halves are in front of the reader at once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telltale.report import UNKNOWN, _amount, render_table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# The per-repetition table. `status`, `precision` and `recall` sit next to `wall_ms` and
# `duration_ms` because that adjacency is the whole point of spec 14.3.
RUN_COLUMNS = (
    "attempt",
    "capture_id",
    "exit_code",
    "status",
    "precision",
    "recall",
    "matched",
    "wrong",
    "wall_ms",
    "duration_ms",
    "coverage",
    "claim_class",
)

# The per-probe statistics. The repeat runner's columns, plus design 6.12's two: MDD is
# the smallest median difference this n and this spread could separate, and N_needed is
# the per-arm n at which that falls to a quarter of the median.
STAT_COLUMNS = (
    "metric",
    "claim_class",
    "n",
    "unknown",
    "median",
    "mad_scaled",
    "iqr",
    "min",
    "max",
    "mdd",
    "n_needed",
    "values",
)

_SCALED = ("median", "mad_scaled", "iqr", "min", "max", "mdd")

_WITHIN = """\
Every row of a STATS table is COMPARATIVE WITHIN ONE PROBE: one question, one base
commit, one environment fingerprint, {n} repetitions. It says how much a number moved
when nothing but the run changed. PRECISION and RECALL are scores of the agent's final
answer against a key written before the run, and they are DERIVED per repetition like
every other per-capture number. The answer text itself was read to compute them and
then dropped: it is not in the store and cannot be recovered from one.

Spec 14.3: measure correctness of the probe answer as well as work quantities. Cheap
behaviour with a wrong answer is not an improvement, so a row of this table that shows
fewer tool calls says nothing on its own until the two score rows are read beside it."""


def probe(measured: Mapping[str, Any]) -> str:
    """One probe suite: a block per probe, then the sentence that bounds them all."""
    lines = [
        f"experiment {measured['experiment']} task {measured['task_id']}:"
        f" {len(measured['probes'])} probe(s), {len(measured['captures'])} captures,"
        f" environment {measured['environment_fingerprint_id']}"
    ]
    for block in measured["probes"]:
        lines += ["", *_probe_block(measured, block)]
    lines += ["", _WITHIN.format(n=_per_probe(measured["probes"]))]
    lines += [f"warning: {one}" for one in measured["warnings"]]
    return "\n".join(lines)


def _probe_block(measured: Mapping[str, Any], block: Mapping[str, Any]) -> list[str]:
    key = block["answer_key"]
    return [
        f"PROBE {block['probe_id']} (task {block['task_id']}): scores"
        f" {block['scores']}, key {block['key_size']} entries"
        f" (paths {key['paths']}, symbols {key['symbols']})",
        "",
        render_table(_repetition_rows(measured, block), RUN_COLUMNS),
        "",
        render_table(_stat_rows(measured, block), STAT_COLUMNS),
    ]


def _repetition_rows(
    measured: Mapping[str, Any], block: Mapping[str, Any]
) -> list[dict[str, Any]]:
    return [
        {
            **{name: run.get(name) for name in RUN_COLUMNS},
            "status": run["score"]["status"],
            "precision": _score(run["score"]["precision"]),
            "recall": _score(run["score"]["recall"]),
            # The counts the two ratios were computed from, so a reader can check one
            # by hand without the report.json.
            "matched": f"{_cell(run['score']['matched_keys'])}/{block['key_size']}",
            "wrong": len(run["score"]["wrong_paths"]),
            "claim_class": measured["claim_class"]["vector"],
        }
        for run in block["repetitions"]
    ]


def _stat_rows(
    measured: Mapping[str, Any], block: Mapping[str, Any]
) -> list[dict[str, Any]]:
    return [
        {
            "metric": metric,
            "claim_class": measured["claim_class"]["stats"],
            **{name: found.get(name) for name in STAT_COLUMNS if name in found},
            **{
                name: _amount(found[name])
                for name in _SCALED
                if found.get(name) is not None
            },
            "values": ",".join(_amount(value) for value in found["values"]),
        }
        for metric, found in sorted(block["stats"].items())
    ]


def _per_probe(blocks: Sequence[Mapping[str, Any]]) -> str:
    counts = sorted({len(block["captures"]) for block in blocks})
    return " and ".join(str(count) for count in counts)


def _score(value: float | None) -> str:
    """A ratio to three places, or `-`. Never to a whole number: 0.999 is not 1."""
    return UNKNOWN if value is None else f"{value:.3f}"


def _cell(value: Any) -> str:
    return UNKNOWN if value is None else str(value)
