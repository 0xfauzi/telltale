"""Two arms, one factor, and what n can separate: `environment` and `intervention`.

Design 6.12's H3 protocol and spec 14.3's controlled repository intervention, which are
one mechanism read two ways. An ARM is one condition: `experiments.repeat` for the
environment experiment, one arm of `experiments_probe.suite` for the intervention. Two
arms that differ in exactly one declared factor are the whole experiment, and the
comparison between them is the only thing here that the two runners do not already do.

The factor is a launch flag (model, effort, content_level) or the repository itself,
read one of two ways: `base_sha` is a scoped code refactor and `instructions` is a
rewrite of the files the agent is told to read. What each factor asserts, before the
runs and after them, is `experiments_factor.py`; this file runs the arms, calls those
two assertions at the two moments they can be made, and reports. An intervention pairs
BY PROBE: two probes are two questions, and a shift computed across them would be a
shift between questions rather than between commits.

Its own module rather than more of experiments.py for the reason the statistics are
their own module: experiments.py is 629 lines and this was more, so one file would be
past an 800-line ratchet that is a gate rather than a preference. The seam is real:
experiments.py and experiments_probe.py each run one condition and report it, and this
file only ever asks whether two conditions differ in what they were declared to differ
in. W4-T5 split the same file again at the same seam when the `instructions` factor
took it to 910 lines: the question moved to experiments_factor.py and the two runners
stayed here.

Every between-arm number is COMPARATIVE and carries that claim class. The label
vocabulary is stats.py's two strings and nothing else: "not resolved at n" is a
statement about n, and this file never writes "no effect", "effect of", "impact" or
"cause".
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import stats as between
from telltale.experiments import SpecError, repeat
from telltale.experiments_factor import (
    COMMIT_FACTORS,
    FACTORS,
    REPOSITORY_FACTOR,
    SHAPE,
    arm_sha,
    assert_between,
    assert_declared,
)
from telltale.experiments_measure import MAD_SCALE
from telltale.model import now_iso, to_json
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


# The spec of an environment experiment. `repetitions_per_arm` rather than
# `repetitions` and `arms` rather than `command`: a spec that names one command is a
# spec for `repeat`, and reading it here would run one arm and report a comparison.
ENVIRONMENT_SPEC_KEYS = (
    "task_id", "experiment", "repo", "base_sha", "acceptance",
    "repetitions_per_arm", "provider", "level", "factor", "arms",
)  # fmt: skip

# `level` and `base_sha` are optional and default to the spec's; the other two are
# required.
ARM_KEYS = ("name", "command", "level", "base_sha")

# Design 6.12 varies one launch flag at a time, so an environment experiment is two
# arms exactly. Three arms is three pairwise comparisons and a multiplicity question
# this protocol does not answer.
ARMS = 2

# The pilot of design 6.12, printed with every report. Not enforced: a spec may run
# fewer while a harness is being checked, and the report says what it ran.
PILOT_PER_ARM = 5


def environment(
    spec: Mapping[str, Any], home: Path, out: Path | None = None
) -> dict[str, Any]:
    """Run two arms that differ in one declared factor and compare them. Design 6.12.

    The two assertions are the module docstring's. Each arm runs through `repeat`
    unchanged, so the per-arm tables are its tables and the within-arm fingerprint
    assertion is its assertion; `home` is the $TELLTALE_HOME every capture is written
    into, and `out` writes this report beside the two `repeat` reports.
    """
    checked = _checked_environment(spec)
    assert_declared(checked)
    arms = [
        {"name": str(arm["name"]), "report": repeat(_arm_spec(checked, arm), home, out)}
        for arm in checked["arms"]
    ]
    store = Store(home / "telltale.db")
    assertion = assert_between(store, checked, arms)
    return _environment_report(checked, arms, assertion, out)


def _checked_environment(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The spec, whole, before anything runs. Every refusal names what is wrong."""
    missing = sorted(set(ENVIRONMENT_SPEC_KEYS) - set(spec))
    unknown = sorted(set(spec) - set(ENVIRONMENT_SPEC_KEYS))
    if missing or unknown:
        raise SpecError(f"spec: missing {missing}, unexpected {unknown}")
    checked = dict(spec)
    if checked["factor"] not in FACTORS:
        raise SpecError(f"spec: factor {checked['factor']!r} is not one of {FACTORS}")
    count = len(checked["arms"]) if isinstance(checked["arms"], list) else 0
    if count != ARMS:
        raise SpecError(f"spec: {count} arms, and an environment experiment has {ARMS}")
    if not isinstance(checked["repetitions_per_arm"], int) or (
        checked["repetitions_per_arm"] < 1
    ):
        raise SpecError(
            f"spec: repetitions_per_arm is {checked['repetitions_per_arm']!r},"
            " not a count"
        )
    checked["arms"] = [_checked_arm(arm) for arm in checked["arms"]]
    if len({arm["name"] for arm in checked["arms"]}) != ARMS:
        raise SpecError("spec: the two arms share one name")
    checked["repo"] = str(Path(str(checked["repo"])).expanduser().resolve())
    return checked


def _checked_arm(arm: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(arm) - set(ARM_KEYS))
    missing = sorted({"name", "command"} - set(arm))
    if missing or unknown:
        raise SpecError(f"arm: missing {missing}, unexpected {unknown}")
    if not isinstance(arm["command"], list) or not arm["command"]:
        raise SpecError(f"arm {arm['name']!r}: command is a non-empty argv list")
    return dict(arm)


def _arm_spec(spec: Mapping[str, Any], arm: Mapping[str, Any]) -> dict[str, Any]:
    """One arm as a `repeat` spec. The arm's name is the task id's suffix.

    A suffix rather than a shared task id: the two arms are two conditions, and a
    correlation that named one task for both would say the ten captures were ten
    attempts at the same thing under the same environment.
    """
    return {
        "task_id": f"{spec['task_id']}-{arm['name']}",
        "experiment": spec["experiment"],
        "repo": spec["repo"],
        "base_sha": arm_sha(spec, arm),
        "command": list(arm["command"]),
        "acceptance": list(spec["acceptance"]),
        "repetitions": spec["repetitions_per_arm"],
        "provider": spec["provider"],
        "level": arm.get("level", spec["level"]),
    }


# -- the report -----------------------------------------------------------------------

# Design 6.12 pre-registers these and asks that every report print them: changing one
# after seeing a result is a new experiment id, and a constant that is not printed is
# a constant nobody can check was not changed.
CONSTANTS: dict[str, Any] = {
    "arms": ARMS,
    "pilot_repetitions_per_arm": PILOT_PER_ARM,
    "mad_scale": MAD_SCALE,
    "power_z": between.POWER_Z,
    "relative_resolution": between.RESOLUTION,
    "exact_test_max_n_per_arm": between.EXACT_MAX_N,
}


_ASSUMPTIONS = (
    "the between-arm rows are COMPARATIVE between these two arms and say nothing"
    " about any other task, repository or provider",
    "'not resolved at n' is a statement about n. It is never a statement that the"
    " two arms are the same, and this report never says effect, impact or cause",
    "the per-capture vector is spec 13.7's 22 metrics read back out of the evidence"
    " table, plus duration_ms, num_turns and the tool calls by name, which are stream"
    " facts and not measures",
)


def _environment_report(
    spec: Mapping[str, Any],
    arms: Sequence[Mapping[str, Any]],
    assertion: Mapping[str, Any],
    out: Path | None,
) -> dict[str, Any]:
    rows = _between_rows(arms)
    report = {
        "experiment": spec["experiment"],
        "task_id": spec["task_id"],
        "created_at": now_iso(),
        "spec": dict(spec),
        "factor": spec["factor"],
        "constants": CONSTANTS,
        "arms": [
            {
                "name": arm["name"],
                "task_id": arm["report"]["task_id"],
                "report": arm["report"],
            }
            for arm in arms
        ],
        "fingerprint_assertion": dict(assertion),
        "between": rows,
        "claim_class": {
            "vector": "derived",
            "stats": "comparative",
            "between": between.CLAIM_CLASS,
        },
        "assumptions": [*SHAPE[str(spec["factor"])], *_ASSUMPTIONS],
        "warnings": [
            f"{metric}: {warning}"
            for metric, row in sorted(rows.items())
            for warning in row["warnings"]
        ],
    }
    if out is not None:
        _write_environment(report, out / str(spec["task_id"]))
    return report


def _between_rows(arms: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """One row per metric, over the union of the two arms' metrics."""
    first, second = (dict(arm["report"]["stats"]) for arm in arms)
    return _between_stats(first, second)


def _between_stats(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Two per-condition stats tables, compared metric by metric.

    A metric one side never reported is compared with no values rather than skipped:
    `stats.compare` returns None for every number it cannot compute and a warning that
    says why, and a metric silently absent from this table would read as a metric that
    did not differ.
    """
    empty: dict[str, Any] = {"values": [], "mad_scaled": None}
    return {
        metric: between.compare(
            first.get(metric, empty)["values"],
            second.get(metric, empty)["values"],
            first.get(metric, empty)["mad_scaled"],
            second.get(metric, empty)["mad_scaled"],
        )
        for metric in sorted(set(first) | set(second))
    }


def _write_environment(report: Mapping[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "environment.json"
    # Trailing newline, for the reason experiments._write states about report.json:
    # without it the end-of-file-fixer hook rewrites the file on every commit and the
    # artefact in git stops matching what the runner writes. Measured by W2-E06, which
    # is the first task to commit one.
    path.write_text(to_json(report) + "\n", encoding="utf-8")
    return path


# -- the controlled repository intervention (spec 14.3) --------------------------------

# The spec of an intervention. It is an environment spec with `probes` in place of
# `acceptance`: the two arms run the SAME probe suite before and after a scoped refactor
# or an instruction rewrite, so the answer key is the acceptance criterion and there is
# nothing in a read-only worktree for a command to verify.
INTERVENTION_SPEC_KEYS = (
    "task_id", "experiment", "repo", "probes",
    "repetitions_per_arm", "provider", "level", "factor", "arms",
)  # fmt: skip

# What an intervention MAY carry and need not. `stop` is the per-session bound
# experiments_stop.py owns: it belongs to the SUITE and not to an arm, so it is read
# here and copied to both arm specs, and an intervention without one runs unbounded the
# way every intervention before W4-E10 did.
OPTIONAL_INTERVENTION_KEYS = ("stop",)

_INTERVENTION_ASSUMPTIONS = (
    "the between-arm rows are PAIRED BY PROBE. A probe's values are compared only with"
    " the same probe's values in the other arm, and no row pools two probes: two probes"
    " are two questions and a shift between them would be a shift between questions",
    "the between-arm rows are COMPARATIVE between these two arms of this suite and say"
    " nothing about any other task, repository or provider",
    "'not resolved at n' is a statement about n. It is never a statement that the"
    " two arms are the same, and this report never says effect, impact or cause",
)


def intervention(
    spec: Mapping[str, Any], home: Path, out: Path | None = None
) -> dict[str, Any]:
    """Run one probe suite at two commits and compare them, paired by probe. Spec 14.3.

    The before-and-after half of spec 14.3: the same fixed read-only probes on two
    repository versions. Under `base_sha` the environment fingerprint is held constant;
    under `instructions` it is held constant except for the field the rewrite moves, and
    which of the two applies is the spec's declared factor. Both arms run through ONE
    `experiments_probe.suite` call, so the per-probe tables, the scoring and the stop
    bound are its, and the only thing this adds is the pairing.

    Imported inside the function on purpose. experiments_probe.py reads
    `experiments.py`'s runner helpers and this module reads `experiments_probe.suite`;
    a module-level import here would be the third side of a cycle at import time, and
    the intervention is the one entry point that needs it.
    """
    from telltale.experiments_probe import suite

    checked = _checked_intervention(spec)
    assert_declared(checked)
    # ONE call rather than one per arm, because the order is repetition-major ACROSS the
    # arms: round 1 is one session of every probe of both arms. Two `probe` calls would
    # run every session of the first arm before the second started, which is what left
    # W4-E09's stop bound unable to act.
    reports = suite(
        [_arm_probe_spec(checked, arm) for arm in checked["arms"]], home, out
    )
    arms = [
        {
            "name": str(arm["name"]),
            "base_sha": arm_sha(checked, arm),
            "report": report,
        }
        for arm, report in zip(checked["arms"], reports, strict=True)
    ]
    store = Store(home / "telltale.db")
    assertion = _asserted(store, checked, arms)
    return _intervention_report(checked, arms, assertion, out)


def _asserted(
    store: Store, spec: Mapping[str, Any], arms: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    """The between-arm fingerprint assertion, or None when an arm has no capture.

    `assert_between` reads one capture per arm, and a stop bound that ended the run
    inside the first round can leave the second arm with none. None rather than a
    refusal: a run stopped early has not FAILED the assertion, it has not made one, and
    a report that turned the second into the first would name a mismatch nobody
    measured.
    """
    if all(arm["report"]["captures"] for arm in arms):
        return assert_between(store, spec, arms)
    return None


def _checked_intervention(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The spec, whole, before anything runs. Every refusal names what is wrong."""
    missing = sorted(set(INTERVENTION_SPEC_KEYS) - set(spec))
    unknown = sorted(
        set(spec) - set(INTERVENTION_SPEC_KEYS) - set(OPTIONAL_INTERVENTION_KEYS)
    )
    if missing or unknown:
        raise SpecError(f"spec: missing {missing}, unexpected {unknown}")
    checked = dict(spec)
    if checked["factor"] not in COMMIT_FACTORS:
        raise SpecError(
            f"spec: factor {checked['factor']!r}, and an intervention is one of"
            f" {COMMIT_FACTORS} (spec 14.3), because an arm IS a commit. Use"
            " `experiment environment` for a launch flag"
        )
    count = len(checked["arms"]) if isinstance(checked["arms"], list) else 0
    if count != ARMS:
        raise SpecError(f"spec: {count} arms, and an intervention has {ARMS}")
    if not isinstance(checked["repetitions_per_arm"], int) or (
        checked["repetitions_per_arm"] < 1
    ):
        raise SpecError(
            f"spec: repetitions_per_arm is {checked['repetitions_per_arm']!r},"
            " not a count"
        )
    checked["arms"] = [_checked_intervention_arm(arm) for arm in checked["arms"]]
    if len({arm["name"] for arm in checked["arms"]}) != ARMS:
        raise SpecError("spec: the two arms share one name")
    checked["repo"] = str(Path(str(checked["repo"])).expanduser().resolve())
    return checked


def _checked_intervention_arm(arm: Mapping[str, Any]) -> dict[str, Any]:
    """An arm names its own commit. There is no spec-level default to fall back on."""
    checked = _checked_arm(arm)
    if not str(checked.get(REPOSITORY_FACTOR, "")).strip():
        raise SpecError(
            f"arm {checked['name']!r}: no {REPOSITORY_FACTOR}, and an intervention arm"
            " IS a commit"
        )
    return checked


def _arm_probe_spec(spec: Mapping[str, Any], arm: Mapping[str, Any]) -> dict[str, Any]:
    """One arm as an `experiments_probe.suite` spec. The name suffixes the task id."""
    return {
        "task_id": f"{spec['task_id']}-{arm['name']}",
        "experiment": spec["experiment"],
        "repo": spec["repo"],
        "base_sha": arm_sha(spec, arm),
        "command": list(arm["command"]),
        "probes": [dict(one) for one in spec["probes"]],
        "repetitions": spec["repetitions_per_arm"],
        "provider": spec["provider"],
        "level": arm.get("level", spec["level"]),
        # The same block on both arms, which is what `experiments_stop.one_bound`
        # requires: the bound is the suite's and never an arm's.
        "stop": spec.get("stop"),
    }


def _intervention_report(
    spec: Mapping[str, Any],
    arms: Sequence[Mapping[str, Any]],
    assertion: Mapping[str, Any] | None,
    out: Path | None,
) -> dict[str, Any]:
    paired = _paired(arms)
    # One record for the suite, taken off the first arm's report because both arms ran
    # under one bound in one loop and both carry the same one.
    stopped = dict(arms[0]["report"]["stop"])
    report = {
        "experiment": spec["experiment"],
        "task_id": spec["task_id"],
        "created_at": now_iso(),
        "spec": dict(spec),
        "factor": spec["factor"],
        "constants": CONSTANTS,
        "arms": [
            {
                "name": arm["name"],
                "base_sha": arm["base_sha"],
                "task_id": arm["report"]["task_id"],
                "report": arm["report"],
            }
            for arm in arms
        ],
        "fingerprint_assertion": None if assertion is None else dict(assertion),
        "stop": stopped,
        "between": paired,
        "claim_class": {
            "vector": "derived",
            "score": "derived",
            "stats": "comparative",
            "between": between.CLAIM_CLASS,
        },
        "assumptions": [
            *SHAPE[str(spec["factor"])],
            *_INTERVENTION_ASSUMPTIONS,
        ],
        "warnings": [
            *_unasserted(assertion, stopped),
            *(
                f"{probe_id} {metric}: {warning}"
                for probe_id, rows in sorted(paired.items())
                for metric, row in sorted(rows.items())
                for warning in row["warnings"]
            ),
        ],
    }
    if out is not None:
        _write_intervention(report, out / str(spec["task_id"]))
    return report


def _unasserted(
    assertion: Mapping[str, Any] | None, stopped: Mapping[str, Any]
) -> list[str]:
    """Said once, at the top of the warnings, when no fingerprint assertion was made."""
    if assertion is not None:
        return []
    return [
        "NO between-arm fingerprint assertion was made: the stop bound ended the run"
        f" after {stopped['sessions_run']} of {stopped['sessions_planned']} sessions"
        " and an arm has no capture to read an environment off. Nothing below is a"
        " controlled comparison, and the arms were not shown to differ in the declared"
        " factor alone"
    ]


def _paired(
    arms: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """One between-arm table per probe. Refuses when the two arms ran different probes.

    Both arms run the same `probes` list by construction, so a disagreement here is a
    runner defect rather than a spec one, and reporting the intersection would hide it.
    """
    blocks = [
        {str(block["probe_id"]): block for block in arm["report"]["probes"]}
        for arm in arms
    ]
    if set(blocks[0]) != set(blocks[1]):
        raise SpecError(
            f"arm {arms[0]['name']} ran probes {sorted(blocks[0])} and arm"
            f" {arms[1]['name']} ran {sorted(blocks[1])}: a paired table needs the"
            " same probe on both sides"
        )
    return {
        probe_id: _between_stats(
            blocks[0][probe_id]["stats"], blocks[1][probe_id]["stats"]
        )
        for probe_id in sorted(blocks[0])
    }


def _write_intervention(report: Mapping[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "intervention.json"
    # Trailing newline, for the reason `_write_environment` states.
    path.write_text(to_json(report) + "\n", encoding="utf-8")
    return path
