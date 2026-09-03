"""Rendering for `experiment probe` and `experiment intervention`. Spec 14.3.

Its own module rather than two more renderers in report.py, for the reason stats.py and
experiments_env.py exist: report.py was at 635 lines against an 800-line ratchet and two
more renderers plus their paragraphs are 200 more. Nothing here computes: every number
printed is already a field of the report the runner built, and the two paragraphs below
are what stops a reader taking the tables for more than they are.

report.py's three rules hold here unchanged, and one is added. A value that is None
prints as `-` and never as 0 or blank. A number is formatted by the caller. And the
SCORE columns are printed beside the work quantities on the same page on purpose: spec
14.3's "cheap behaviour with a wrong answer is not an improvement" is a rule about
reading, and it can only be followed if both halves are in front of the reader at once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telltale.experiments_factor import INSTRUCTION_FIELD
from telltale.experiments_stop import BOUNDS
from telltale.report import UNKNOWN, _amount, render_table
from telltale.stats import UNRESOLVED

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

# The between-arm table of an intervention, one per probe. Same columns as the
# environment runner's, because it is the same comparison over a different factor.
BETWEEN_COLUMNS = (
    "metric",
    "claim_class",
    "n_a",
    "n_b",
    "hl_shift",
    "cliffs_delta",
    "p",
    "s_a",
    "s_b",
    "s",
    "median",
    "mdd",
    "n_needed",
    "demoted",
    "label",
)

_SCALED = ("median", "mad_scaled", "iqr", "min", "max", "mdd")
_ROUNDED = ("hl_shift", "cliffs_delta", "p", "s_a", "s_b", "s", "median", "mdd")

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

_PAIRED = """\
Each table above is ONE PROBE compared across the two arms, and no row pools two
probes. "{unresolved}" is a statement about n, not about the two commits. It says the
shift this run measured is smaller than the smallest one {n} repetitions per arm can
separate from run-to-run variation for that measure. It does not say the two commits
behave alike, and no row here may be read as more than a difference measured between
two versions of one repository under one probe. N_NEEDED is the per-arm repetitions at
which MDD falls to a quarter of the pooled median. DEMOTED is design 6.12's rule that a
measure still above that quarter is withheld from repository comparison."""


def probe(measured: Mapping[str, Any]) -> str:
    """One probe suite: a block per probe, then the sentence that bounds them all."""
    lines = [
        f"experiment {measured['experiment']} task {measured['task_id']}:"
        f" {len(measured['probes'])} probe(s), {len(measured['captures'])} captures,"
        f" environment {_cell(measured['environment_fingerprint_id'])}",
        _stop_line(measured["stop"]),
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


def intervention(measured: Mapping[str, Any]) -> str:
    """One intervention: the constants, the assertion, both arms, a table per probe."""
    arms = measured["arms"]
    lines = [
        f"experiment {measured['experiment']} task {measured['task_id']}:"
        f" factor {measured['factor']}, {len(arms)} arms",
        f"pre-registered constants: {_constants(measured['constants'])}",
        "arms: " + ", ".join(f"{arm['name']}={arm['base_sha']}" for arm in arms),
        _stop_line(measured["stop"]),
        _assertion_line(measured["fingerprint_assertion"]),
    ]
    for arm in arms:
        header = f"ARM {arm['name']} at {arm['base_sha']}"
        lines += ["", header, "", probe(arm["report"])]
    for probe_id, rows in sorted(measured["between"].items()):
        lines += ["", f"BETWEEN ARMS, probe {probe_id}", "",
                  render_table(_between_rows(rows), BETWEEN_COLUMNS)]  # fmt: skip
    lines += ["", _PAIRED.format(unresolved=UNRESOLVED, n=_per_arm(arms))]
    lines += [f"warning: {one}" for one in measured["warnings"]]
    return "\n".join(lines)


def _stop_line(stop: Mapping[str, Any]) -> str:
    """What bounded the run, and which session ended it. On every probe report.

    A report of an UNBOUNDED suite says so in the same place a bounded one prints its
    numbers. W4-E09's bound lived in a brief and not in the runner, so its report could
    not say either thing, and a reader had to know which of the two they were holding.
    """
    ran = f"{stop['sessions_run']} of {stop['sessions_planned']} sessions ran"
    unknown = stop["token_total_unknown"]
    tail = (
        f"; {len(unknown)} session(s) carry no token total and were compared with no"
        f" token bound: {[one['capture_id'] for one in unknown]}"
        if unknown
        else ""
    )
    if stop["bounds"] is None:
        return f"stop: no bound in the spec, so the suite ran unbounded; {ran}{tail}"
    bounds = ", ".join(f"{name}={stop['bounds'][name]}" for name in BOUNDS)
    crossed = stop["crossed"]
    if crossed is None:
        return f"stop: {bounds}; no session crossed a bound, {ran}{tail}"
    return (
        f"stop: {bounds}; ENDED by {crossed['task_id']} attempt {crossed['attempt']}"
        f" ({crossed['capture_id']}), which reached {crossed['observed']} against"
        f" {crossed['bound']} = {crossed['limit']}, and nothing after it was started;"
        f" {ran}{tail}"
    )


def _constants(constants: Mapping[str, Any]) -> str:
    return ", ".join(f"{name}={value}" for name, value in constants.items())


def _assertion_line(assertion: Mapping[str, Any] | None) -> str:
    """Both fingerprint ids, what differed, what was expected to, and the sentence.

    `instruction_hashes` is printed as the PATHS inside it that differ and not as the
    field's two values. The field is one mapping over every instruction surface in play,
    the operator's home files are in it and are the same for both arms, and a reader
    told that the field moved still does not know which file the two commits rewrote.

    None when the run was stopped before an arm had a capture. The line says that no
    assertion was made, which is not the same as one that passed and not the same as one
    that failed, and a report that printed nothing here would read as the first.
    """
    if assertion is None:
        return (
            "fingerprints: NO assertion was made, because the run was stopped before an"
            " arm had a capture to read an environment off. The two arms were not shown"
            " to differ in the declared factor alone"
        )
    ids = ", ".join(
        f"{name}={one}" for name, one in assertion["fingerprint_ids"].items()
    )
    values = "; ".join(
        f"{field}: " + ", ".join(f"{name}={value!r}" for name, value in fields.items())
        for field, fields in assertion["values"].items()
        if field != INSTRUCTION_FIELD
    )
    paths = "; ".join(
        f"{path}: " + ", ".join(f"{name}={_digest(one)}" for name, one in arms.items())
        for path, arms in assertion["instruction_paths"].items()
    )
    return (
        f"fingerprints: {ids}; differing fields {assertion['differing_fields']},"
        f" expected {assertion['expected_fields']}"
        f"{f' ({values})' if values else ''}"
        f"{f'; {INSTRUCTION_FIELD} differ at {paths}' if paths else ''}"
        f"; {assertion['assertion']}"
    )


def _digest(value: Any) -> str:
    """One instruction file, as the fingerprint holds it: a hash prefix and a size.

    `-` when the arm does not carry the file at all, which is a difference and not a
    zero. The sha256 is cut to 12 characters because what a reader does with it is
    compare two of them; the whole hash is in the report's `values` field.
    """
    if value is None:
        return UNKNOWN
    return f"sha256 {_cell(value['sha256'])[:12]}, {_cell(value['bytes'])} bytes"


def _between_rows(rows: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "metric": metric,
            **{name: row.get(name) for name in BETWEEN_COLUMNS if name in row},
            **{
                name: _amount(row[name])
                for name in _ROUNDED
                if row.get(name) is not None
            },
        }
        for metric, row in sorted(rows.items())
    ]


def _per_probe(blocks: Sequence[Mapping[str, Any]]) -> str:
    counts = sorted({len(block["captures"]) for block in blocks})
    return " and ".join(str(count) for count in counts)


def _per_arm(arms: Sequence[Mapping[str, Any]]) -> str:
    return " and ".join(sorted({_per_probe(arm["report"]["probes"]) for arm in arms}))


def _score(value: float | None) -> str:
    """A ratio to three places, or `-`. Never to a whole number: 0.999 is not 1."""
    return UNKNOWN if value is None else f"{value:.3f}"


def _cell(value: Any) -> str:
    return UNKNOWN if value is None else str(value)
