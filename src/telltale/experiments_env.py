"""`telltale experiment environment`: two arms, one factor, and what n can separate.

Design 6.12's H3 protocol. An ARM is one condition of `experiments.repeat`: N captures
of one task at one base commit under one environment fingerprint. Two arms that differ
in exactly one launch flag are the whole experiment, and the comparison between them is
the only thing here that `repeat` does not already do.

Its own module rather than more of experiments.py for the reason the statistics are
their own module: experiments.py is 576 lines and this is 406 more, so one file would
be 982 against an 800-line ratchet that is a gate rather than a preference. The seam is
real: experiments.py runs one condition and reports it, and this file only ever asks
whether two conditions differ in what they were declared to differ in.

Two assertions are the experiment, and each one is early or late on purpose:

  BEFORE anything runs, the arms' commands must differ in exactly the declared flag's
  value, token by token. A second difference makes the comparison a comparison of two
  things at once, and finding that out after ten captures have been made is finding it
  out too late.

  AFTER the runs, the two arms' fingerprint payloads must differ in exactly the declared
  field. The argv says what was asked for; the payload says what the environment turned
  out to be, and only the second is evidence. A spec that varies effort but also runs
  the arms at two content levels passes the first assertion and fails this one, which
  names content_level.

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
from telltale.experiments_measure import (
    MAD_SCALE,
    FingerprintMismatch,
    environment_payload,
)
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

# `level` is optional and defaults to the spec's; every other key is required.
ARM_KEYS = ("name", "command", "level")

# The fingerprint fields an arm may vary. Each one is a field of the payload env.py
# builds, which is what the post-run assertion compares; a factor that is not a field
# there could only be checked against the argv, and the argv is what the experiment is
# varying, so it would be checking the spec against itself.
FACTORS = ("model", "effort", "content_level")

# Design 6.12 varies one launch flag at a time, so an environment experiment is two
# arms exactly. Three arms is three pairwise comparisons and a multiplicity question
# this protocol does not answer.
ARMS = 2

# The pilot of design 6.12, printed with every report. Not enforced: a spec may run
# fewer while a harness is being checked, and the report says what it ran.
PILOT_PER_ARM = 5

# How each factor's value reaches the child's argv. These spellings are the ones
# env.fingerprint reads, duplicated here because the pre-run check happens before any
# fingerprint exists. A disagreement between the two tables cannot pass silently: the
# post-run assertion compares the payloads env.py actually built, and an argv
# difference this table misread would show up there as a field that is not the factor.
_FACTOR_FLAGS = {"model": ("--model", "-m"), "effort": ("--effort",)}
_FACTOR_CONFIG = {"effort": "model_reasoning_effort"}
_CONFIG_FLAGS = ("-c", "--config")


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
    _one_declared_difference(checked)
    arms = [
        {"name": str(arm["name"]), "report": repeat(_arm_spec(checked, arm), home, out)}
        for arm in checked["arms"]
    ]
    store = Store(home / "telltale.db")
    assertion = _assert_between(store, checked, arms)
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
        "base_sha": spec["base_sha"],
        "command": list(arm["command"]),
        "acceptance": list(spec["acceptance"]),
        "repetitions": spec["repetitions_per_arm"],
        "provider": spec["provider"],
        "level": arm.get("level", spec["level"]),
    }


# -- the assertion before anything runs -----------------------------------------------


def _one_declared_difference(spec: Mapping[str, Any]) -> list[int]:
    """The arms' commands differ in exactly the declared factor's value, or refuse.

    Token by token over the two argv lists. Anything else is refused with the tokens
    named, including no difference at all: two identical commands under a declared
    factor are one condition run twice and reported as a comparison.
    """
    first, second = spec["arms"]
    a = [str(word) for word in first["command"]]
    b = [str(word) for word in second["command"]]
    names = (first["name"], second["name"])
    if len(a) != len(b):
        raise SpecError(
            f"arms {names[0]} and {names[1]} have {len(a)} and {len(b)} tokens:"
            f" {_extra_tokens(a, b)}"
        )
    differing = [index for index in range(len(a)) if a[index] != b[index]]
    if spec["factor"] == "content_level":
        return _level_difference(spec, a, b, differing, names)
    factor = str(spec["factor"])
    stray = [
        index
        for index in differing
        if not (_declares(a, index, factor) and _declares(b, index, factor))
    ]
    if not differing:
        raise SpecError(
            f"arms {names[0]} and {names[1]} run the same command, so the declared"
            f" factor {factor!r} does not vary between them"
        )
    if stray:
        raise SpecError(
            f"arms {names[0]} and {names[1]} differ at {len(differing)} token(s)"
            f" [{_tokens(a, b, differing)}], and the declared factor {factor!r} is"
            f" the value of {', '.join(_FACTOR_FLAGS[factor])}:"
            f" refused [{_tokens(a, b, stray)}]"
        )
    return differing


def _level_difference(
    spec: Mapping[str, Any],
    a: Sequence[str],
    b: Sequence[str],
    differing: Sequence[int],
    names: tuple[Any, Any],
) -> list[int]:
    """content_level is a `level` difference, so the two commands must be identical."""
    if differing:
        raise SpecError(
            f"factor content_level is a level difference, but the commands of"
            f" {names[0]} and {names[1]} differ at [{_tokens(a, b, differing)}]"
        )
    levels = [arm.get("level", spec["level"]) for arm in spec["arms"]]
    if levels[0] == levels[1]:
        raise SpecError(
            f"factor content_level, and both arms run at level {levels[0]!r}"
        )
    return []


def _declares(argv: Sequence[str], index: int, factor: str) -> bool:
    """True when argv[index] is the value the declared factor is read from."""
    flags = _FACTOR_FLAGS[factor]
    token = argv[index]
    before = argv[index - 1] if index else ""
    if before in flags or any(token.startswith(f"{flag}=") for flag in flags):
        return True
    key = _FACTOR_CONFIG.get(factor)
    return key is not None and before in _CONFIG_FLAGS and token.startswith(f"{key}=")


def _tokens(a: Sequence[str], b: Sequence[str], indices: Sequence[int]) -> str:
    return "; ".join(f"{index}: {a[index]!r} vs {b[index]!r}" for index in indices)


def _extra_tokens(a: Sequence[str], b: Sequence[str]) -> str:
    """What one command has and the other does not, in the order it appears."""
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    extra = list(longer[len(shorter) :])
    return f"{extra} is in the longer one only"


# -- the assertion after the runs -----------------------------------------------------


def _assert_between(
    store: Store, spec: Mapping[str, Any], arms: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The two arms' fingerprints differ in exactly the declared field, or refuse.

    One capture per arm is read, because `repeat` has already refused the arm whose
    captures were not one environment: within-arm identity is its assertion and this
    one is only about the pair.
    """
    ids = [str(arm["report"]["environment_fingerprint_id"]) for arm in arms]
    names = [str(arm["name"]) for arm in arms]
    payloads = [
        environment_payload(store, str(arm["report"]["captures"][0])) for arm in arms
    ]
    differing = _differing_fields(payloads[0], payloads[1])
    if list(differing) != [spec["factor"]]:
        raise FingerprintMismatch(
            f"arms {names[0]} ({ids[0]}) and {names[1]} ({ids[1]}) differ in"
            f" {sorted(differing) or 'no field at all'}, and the declared factor is"
            f" {spec['factor']!r}: {_field_values(differing, names)}"
        )
    return {
        "factor": str(spec["factor"]),
        "fingerprint_ids": dict(zip(names, ids, strict=True)),
        "differing_fields": sorted(differing),
        "values": {
            field: dict(zip(names, values, strict=True))
            for field, values in differing.items()
        },
        "assertion": (
            f"the two arms differ in exactly the declared factor {spec['factor']!r}"
        ),
    }


def _differing_fields(
    a: Mapping[str, Any], b: Mapping[str, Any]
) -> dict[str, list[Any]]:
    """The payload fields whose value is not the same in both arms, in field order.

    Compared as canonical JSON, so instruction_hashes (a mapping) and capture_modes (a
    list) compare by content rather than by identity, and a field one payload does not
    carry at all counts as a difference rather than as a match against nothing.
    """
    return {
        name: [a.get(name), b.get(name)]
        for name in sorted(set(a) | set(b))
        if to_json(a.get(name)) != to_json(b.get(name))
    }


def _field_values(differing: Mapping[str, Sequence[Any]], names: Sequence[str]) -> str:
    return "; ".join(
        f"{field}: {names[0]}={values[0]!r}, {names[1]}={values[1]!r}"
        for field, values in differing.items()
    )


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
    "two arms of one task at one base commit, differing in exactly one declared"
    " factor: asserted on the argv before the runs and on the fingerprint payloads"
    " after them",
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
        "assumptions": list(_ASSUMPTIONS),
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
    """One row per metric, over the union of the two arms' metrics.

    A metric one arm never reported is compared with no values rather than skipped:
    `stats.compare` returns None for every number it cannot compute and a warning that
    says why, and a metric silently absent from this table would read as a metric that
    did not differ.
    """
    first, second = (dict(arm["report"]["stats"]) for arm in arms)
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
    path.write_text(to_json(report), encoding="utf-8")
    return path
