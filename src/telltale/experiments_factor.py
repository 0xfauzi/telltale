"""One declared factor, and the two assertions that make it the only difference.

Design 6.12's H3 protocol and spec 14.3's controlled repository intervention both come
down to one question: did these two arms differ in what they were declared to differ in,
and in nothing else. `experiments_env.py` runs the arms and reports them; this module
owns the question, and the two runners there call it at the two moments it can be
answered.

  BEFORE anything runs, `assert_declared` compares the two arms' argv token by token and
  their commits, and for the `instructions` factor also the paths their two commits
  differ in. A second difference makes the comparison a comparison of two things at
  once, and finding that out after ten captures have been made is finding it out too
  late.

  AFTER the runs, `assert_between` compares the two arms' fingerprint payloads. The argv
  says what was asked for; the payload says what the environment turned out to be, and
  only the second is evidence. A spec that varies effort but also runs the arms at two
  content levels passes the first assertion and fails this one, which names
  content_level.

Its own module rather than more of experiments_env.py because that file reached the
800-line ratchet when the `instructions` factor arrived, and this is the seam its own
docstring names: the runners run and report, and the factor is what they are checked
against. Three functions and four tables are the whole interface, and FACTORS is the
keys of one of the tables so that a factor cannot exist without an assertion.
"""

from __future__ import annotations

import subprocess
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from telltale import env
from telltale.experiments import SpecError
from telltale.experiments_measure import FingerprintMismatch, environment_payload
from telltale.model import to_json

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.store import Store


# The fingerprint fields an arm may vary, plus the two things an arm may vary that the
# fingerprint deliberately does not carry. `model`, `effort` and `content_level` are
# fields of the payload env.py builds, which is what the post-run assertion compares.
#
# `base_sha` is spec 14.3's controlled repository intervention: the two arms are the
# same task or the same probes before and after a scoped refactor. It is not a
# fingerprint field and cannot become one, because a fingerprint that carried the
# commit would make every repository comparison a comparison of two environments. So
# the post-run assertion for this factor is INVERTED: the payloads must differ in NO
# field at all, which is spec 14.3's "the environment fingerprint is held constant"
# stated as a check. It is not vacuous. `instruction_hashes` is a fingerprint field
# and it is read out of the worktree, so a refactor that also touched AGENTS.md or
# CLAUDE.md is caught there and named.
#
# `instructions` is the intervention that refusal named: the same probes before and
# after an AGENTS.md rewrite (spec 14.2's instruction surface, run on the mechanism of
# 14.3). Its arm value is a commit too, and its post-run assertion is the MIRROR of
# base_sha's rather than a relaxation of it. The arms must differ in
# `instruction_hashes` and in NOTHING else, so the one thing base_sha forbids is the
# one thing this factor requires, and everything base_sha holds constant this factor
# holds constant too. What makes that more than a hope is a pre-run check base_sha does
# not need: `_instructions_difference` refuses unless every path the two commits differ
# in is a surface env.py hashes. A commit that rewrote AGENTS.md and edited a module
# would satisfy the post-run assertion while being two factors, because the payload
# cannot see the module.
REPOSITORY_FACTOR = "base_sha"
INSTRUCTIONS_FACTOR = "instructions"

# The one payload field an instruction rewrite moves (design 6.8: env.fingerprint hashes
# each surface's bytes and size under its repository-relative path).
INSTRUCTION_FIELD = "instruction_hashes"

# What each factor's post-run assertion expects the two payloads to differ in. FACTORS
# is this table's keys, so a factor cannot be added without an assertion: the two would
# otherwise disagree silently and the new factor would assert whatever its name matched.
_EXPECTED_FIELDS: dict[str, list[str]] = {
    "model": ["model"],
    "effort": ["effort"],
    "content_level": ["content_level"],
    REPOSITORY_FACTOR: [],
    INSTRUCTIONS_FACTOR: [INSTRUCTION_FIELD],
}
FACTORS = tuple(_EXPECTED_FIELDS)

# The factors whose arm value is a COMMIT, carried by the arm key `base_sha` rather than
# by a token of the arm's argv. Both require the two commands to be identical.
COMMIT_FACTORS = (REPOSITORY_FACTOR, INSTRUCTIONS_FACTOR)

# The instruction surfaces, taken from env.py's own constants rather than written out
# again here: env.py owns the list (design 6.8) and a second copy would drift the day a
# surface is added. `_HOME_FILES` is deliberately not among them. Those are hashed into
# the same payload field and are the same for both arms, and no commit of the repository
# under test can change one.
_REPO_FILES = env._REPO_FILES
_RULES_DIR = PurePosixPath(*env._RULES_DIR)
_SURFACES = (*_REPO_FILES, f"{_RULES_DIR}/*.md")


# How each factor's value reaches the child's argv. These spellings are the ones
# env.fingerprint reads, duplicated here because the pre-run check happens before any
# fingerprint exists. A disagreement between the two tables cannot pass silently: the
# post-run assertion compares the payloads env.py actually built, and an argv
# difference this table misread would show up there as a field that is not the factor.
_FACTOR_FLAGS = {"model": ("--model", "-m"), "effort": ("--effort",)}
_FACTOR_CONFIG = {"effort": "model_reasoning_effort"}
_CONFIG_FLAGS = ("-c", "--config")


# -- the assertion before anything runs -----------------------------------------------


def assert_declared(spec: Mapping[str, Any]) -> list[int]:
    """The two arms differ in the declared factor and in nothing else, or refuse.

    Token by token over the two argv lists, and what that comes to depends on the
    factor. A launch flag must be the ONLY differing token, and no difference at all is
    refused too: two identical commands under a declared factor are one condition run
    twice and reported as a comparison. `content_level` and the two commit-valued
    factors are not in the argv at all, so for them the commands must be identical and
    the difference is asserted where it lives. Every refusal names the tokens.
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
    if spec["factor"] == REPOSITORY_FACTOR:
        return _sha_difference(spec, a, b, differing, names)
    if spec["factor"] == INSTRUCTIONS_FACTOR:
        return _instructions_difference(spec, a, b, differing, names)
    _one_base_sha(spec, names)
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


def arm_sha(spec: Mapping[str, Any], arm: Mapping[str, Any]) -> str:
    """The commit this arm runs at: its own when it names one, the spec's otherwise.

    An intervention spec carries no spec-level commit at all (an arm IS a commit there),
    so the fallback is looked up only when the arm has none.
    """
    if REPOSITORY_FACTOR in arm:
        return str(arm[REPOSITORY_FACTOR])
    return str(spec["base_sha"])


def _one_base_sha(spec: Mapping[str, Any], names: tuple[Any, Any]) -> None:
    """Under any other factor the two arms run at ONE commit, or refuse and name both.

    The mirror of `_sha_difference`. A spec that declares `effort` and also moves the
    repository is two experiments, and the argv check would never see it: base_sha is
    not in the argv, and the fingerprint carries no commit either, so this is the only
    place where that pair can be caught at all.
    """
    shas = [arm_sha(spec, arm) for arm in spec["arms"]]
    if shas[0] != shas[1]:
        raise SpecError(
            f"arms {names[0]} and {names[1]} run at {shas[0]} and {shas[1]}, and the"
            f" declared factor is {spec['factor']!r}: a repository difference is the"
            f" factor {REPOSITORY_FACTOR!r} or {INSTRUCTIONS_FACTOR!r} (spec 14.3)"
            " and never a second one"
        )


def _sha_difference(
    spec: Mapping[str, Any],
    a: Sequence[str],
    b: Sequence[str],
    differing: Sequence[int],
    names: tuple[Any, Any],
) -> list[int]:
    """A commit-valued factor is a repository difference: the commands are identical.

    Spec 14.3: the same functional task template, or the same probes, run before and
    after a deliberately scoped refactor. A spec whose arms move the repository AND
    change a flag is refused with both differences named, because the fingerprint
    assertion afterwards cannot separate them: it sees the flag and never the commit.
    Both commit-valued factors come through here, and the refusal names the declared
    one rather than base_sha, which is the arm KEY under either.
    """
    factor = str(spec["factor"])
    shas = [arm_sha(spec, arm) for arm in spec["arms"]]
    if differing:
        raise SpecError(
            f"factor {factor} is a repository difference, and the"
            f" commands of {names[0]} and {names[1]} also differ at"
            f" [{_tokens(a, b, differing)}]:"
            f" {REPOSITORY_FACTOR} {names[0]}={shas[0]!r}, {names[1]}={shas[1]!r}"
        )
    if shas[0] == shas[1]:
        raise SpecError(f"factor {factor}, and both arms run at {shas[0]!r}")
    return []


def _instructions_difference(
    spec: Mapping[str, Any],
    a: Sequence[str],
    b: Sequence[str],
    differing: Sequence[int],
    names: tuple[Any, Any],
) -> list[int]:
    """`instructions` is base_sha plus one check, and the check is the whole factor.

    The commands must be identical and the commits must differ, exactly as for
    base_sha. Then `git diff --name-only` between the two commits must name at least
    one path, and every path it names must be a surface the fingerprint hashes. The
    post-run assertion says `instruction_hashes` moved and nothing else in the payload
    did; that is a statement about the ENVIRONMENT and not about the repository, and a
    commit that rewrote AGENTS.md and edited a module would pass it while being two
    factors. This is where that spec is refused, before any token is spent, with the
    path named.
    """
    indices = _sha_difference(spec, a, b, differing, names)
    shas = [arm_sha(spec, arm) for arm in spec["arms"]]
    changed = _changed_paths(str(spec["repo"]), shas[0], shas[1])
    if not changed:
        raise SpecError(
            f"factor {INSTRUCTIONS_FACTOR}, and {names[0]}={shas[0]} and"
            f" {names[1]}={shas[1]} differ in no path at all: an instruction rewrite is"
            " a difference in a file, and two commits with the same tree are one arm"
        )
    stray = [path for path in changed if not _instruction_surface(path)]
    if stray:
        raise SpecError(
            f"factor {INSTRUCTIONS_FACTOR}, and {names[0]}={shas[0]} and"
            f" {names[1]}={shas[1]} differ in {len(changed)} path(s) of which"
            f" {stray} {'is' if len(stray) == 1 else 'are'} not an instruction surface"
            f" the fingerprint hashes ({', '.join(_SURFACES)}): a commit that changes"
            " code AND an instruction file is two factors"
        )
    return indices


def _changed_paths(repo: str, before: str, after: str) -> list[str]:
    """Every path the two commits differ in. Never shell=True (design 6.8).

    `-z`, so the paths arrive verbatim: git quotes a name carrying a space or a
    non-ASCII byte in its default output, and a quoted path compared against a surface
    name is a path that fails to match for a reason nobody can see.
    """
    done = subprocess.run(
        ["git", "-C", repo, "diff", "--name-only", "-z", before, after],
        capture_output=True,
        check=True,
    )
    return [name for name in done.stdout.decode("utf-8", "replace").split("\0") if name]


def _instruction_surface(path: str) -> bool:
    """True when env.fingerprint would hash this path out of an arm's worktree.

    The surfaces are env.py's list, read from its constants rather than restated here.
    What this file adds is the shape: a git diff names a path in a commit, nothing is
    checked out while this runs, and an arm runs with the worktree ROOT as its working
    directory (experiments._launch), so `docs/AGENTS.md` and `.claude/rules/deep/x.md`
    are not surfaces even though `AGENTS.md` and `.claude/rules/x.md` are.

    A path env.py hashes that this refuses costs a refusal the owner can read. A path
    env.py does NOT hash that this accepts would let a code change through as an
    instruction change, so the one direction that must not drift is pinned against
    env.fingerprint itself by tests/integration/test_instructions.py.
    """
    posix = PurePosixPath(path)
    if posix.parent == PurePosixPath("."):
        return posix.name in _REPO_FILES
    return posix.parent == _RULES_DIR and posix.suffix == ".md"


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


def assert_between(
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
    # The declared factor decides what must differ, and for two factors it is not the
    # factor's own name: the fingerprint carries no commit, so base_sha asserts that
    # NOTHING moved and instructions that `instruction_hashes` alone did. See FACTORS
    # for why the fingerprint cannot carry a commit.
    expected = _EXPECTED_FIELDS[str(spec["factor"])]
    paths = _instruction_paths(differing, names)
    if sorted(differing) != expected:
        raise FingerprintMismatch(
            f"arms {names[0]} ({ids[0]}) and {names[1]} ({ids[1]}) differ in"
            f" {sorted(differing) or 'no field at all'}, and the declared factor is"
            f" {spec['factor']!r} (fingerprint fields expected to differ: {expected}):"
            f" {_field_values(differing, names)}{_paths_clause(paths)}"
        )
    return {
        "factor": str(spec["factor"]),
        "fingerprint_ids": dict(zip(names, ids, strict=True)),
        "differing_fields": sorted(differing),
        "expected_fields": expected,
        "values": {
            field: dict(zip(names, values, strict=True))
            for field, values in differing.items()
        },
        # Which paths inside instruction_hashes differ. Empty for every factor that did
        # not move the field, and never absent: a report that carried the key only
        # sometimes would make a reader who does not see it guess which case it is.
        "instruction_paths": paths,
        "assertion": _ASSERTED[str(spec["factor"])].format(factor=spec["factor"]),
    }


# What the assertion says it proved, one entry per factor so that a factor added to
# FACTORS without a sentence here raises rather than printing another factor's.
_FLAG_ASSERTED = "the two arms differ in exactly the declared factor {factor!r}"
_ASSERTED = {
    "model": _FLAG_ASSERTED,
    "effort": _FLAG_ASSERTED,
    "content_level": _FLAG_ASSERTED,
    REPOSITORY_FACTOR: (
        "the two arms differ in the declared factor {factor!r} and in no field of the"
        " environment fingerprint, which carries no commit and so cannot show a"
        " repository difference at all: what this asserts is that nothing ELSE moved,"
        " including the instruction files, which are read out of each arm's worktree"
    ),
    INSTRUCTIONS_FACTOR: (
        "the two arms differ in the declared factor {factor!r} and in exactly one field"
        f" of the environment fingerprint, {INSTRUCTION_FIELD}, which is read out of"
        " each arm's worktree: what this asserts is that the instruction surfaces moved"
        " and that nothing else in the environment did. That the instruction surfaces"
        " are ALL the two commits moved is the pre-run check on their diff, not this"
        " one, since the fingerprint carries no commit and cannot see a source file"
    ),
}


def _instruction_paths(
    differing: Mapping[str, Sequence[Any]], names: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """WHICH paths inside instruction_hashes differ, by arm. Empty when none do.

    The payload field is one mapping over every instruction surface in play, and the
    home files are hashed into it too and are the same for both arms, so "the field
    differs" does not say what moved and a report that stopped there would be naming a
    field rather than a file. A path one arm does not carry at all is recorded as None
    rather than left out: a deleted AGENTS.md is a difference, not a match.
    """
    if INSTRUCTION_FIELD not in differing:
        return {}
    first, second = (
        one if isinstance(one, dict) else {} for one in differing[INSTRUCTION_FIELD]
    )
    return {
        path: dict(zip(names, [first.get(path), second.get(path)], strict=True))
        for path in sorted(set(first) | set(second))
        if to_json(first.get(path)) != to_json(second.get(path))
    }


def _paths_clause(paths: Mapping[str, Any]) -> str:
    return f"; inside {INSTRUCTION_FIELD}: {sorted(paths)}" if paths else ""


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


# The shape of the experiment, which is the one assumption that depends on the factor
# and the first line of both reports. A launch-flag experiment holds the repository
# still; the two commit-valued factors move it and say what they asserted instead. One
# entry per factor, for `_ASSERTED`'s reason.
_FLAG_SHAPE = (
    "two arms of one task at ONE base commit, differing in exactly one declared launch"
    " factor: asserted on the argv before the runs and on the fingerprint payloads"
    " after them",
)
SHAPE: dict[str, tuple[str, ...]] = {
    "model": _FLAG_SHAPE,
    "effort": _FLAG_SHAPE,
    "content_level": _FLAG_SHAPE,
    REPOSITORY_FACTOR: (
        "two arms at TWO base commits, differing in the repository and in nothing else:"
        " asserted on the argv before the runs, and on the fingerprint payloads after"
        " them, where the arms must differ in NO field",
        "the environment fingerprint carries no commit, so it cannot show a repository"
        " difference. Holding it constant is what makes the commit the only declared"
        " difference; it is not evidence that the commit is the only difference there"
        " is",
    ),
    INSTRUCTIONS_FACTOR: (
        "two arms at TWO base commits that differ ONLY in the instruction surfaces the"
        " fingerprint hashes: asserted on the argv before the runs, on every path"
        " `git diff` names between the two commits before the runs, and on the"
        " fingerprint payloads after them, where the arms must differ in"
        f" {INSTRUCTION_FIELD} and in no other field",
        "the environment fingerprint carries no commit, so the pre-run diff check is"
        " the only thing that makes the instruction surfaces the WHOLE of the"
        " difference between the two commits. The payload could not tell an instruction"
        " rewrite from an instruction rewrite that also edited a module",
    ),
}
