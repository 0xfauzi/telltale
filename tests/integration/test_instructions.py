"""The `instructions` factor: an AGENTS.md rewrite as a declared, checkable difference.

W4-T5, and E10 is what it is for: the same probes before and after an instruction
rewrite on a branch. Until this factor existed the intervention runner refused that
experiment, because a base_sha intervention asserts the two arms differ in NO
fingerprint field and `instruction_hashes` is one of them.

Nothing here is stubbed. Every test makes a real git repository with real commits, and
the two runner tests really launch the scripted agent through the real `telltale run`
console script into worktrees that really exist at the two commits. The pinning test at
the bottom is the one that does not launch anything: it asks `env.fingerprint` which
paths it hashes and compares that with the paths the pre-run diff check accepts, because
that check is a restatement of env.py's behaviour and only a comparison keeps the two
the same.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import pytest
from experiment_helpers import (
    ACCEPTANCE,
    ALPHA,
    arm_command,
    commit,
    git,
    intervention_spec,
    probe_repository,
    reader,
    run_telltale,
    task_repository,
)

from telltale import env, report, report_probe
from telltale.experiments import SpecError
from telltale.experiments_env import environment, intervention
from telltale.experiments_factor import _instruction_surface
from telltale.experiments_measure import FingerprintMismatch

if TYPE_CHECKING:
    from pathlib import Path

EXPERIMENT = "W4-T5-test"
REPETITIONS = 2

# The rewrite. Two AGENTS.md bodies of different lengths, so the fingerprint's `bytes`
# moves as well as its sha256 and a test can tell a rewrite from a re-hash.
BEFORE = "# Rules\n\nAnswer with the file path.\n"
AFTER = "# Rules\n\nAnswer with the file path, and then stop. Do not edit anything.\n"

# The stray source file that makes a commit two factors instead of one.
GAMMA = "src/gamma.py"


def _rewritten(root: Path) -> tuple[str, str]:
    """A repository whose two commits differ in AGENTS.md and in nothing else."""
    before = probe_repository(root, agents=BEFORE)
    return before, commit(root, "rewrite AGENTS.md", {"AGENTS.md": AFTER})


def _instructions_spec(
    root: Path, before: str, after: str, **changed: Any
) -> dict[str, Any]:
    return intervention_spec(
        root,
        before,
        after,
        EXPERIMENT,
        REPETITIONS,
        task_id="T-instructions",
        **{"factor": "instructions", **changed},
    )


@pytest.mark.integration
def test_one_probe_suite_across_an_agents_md_rewrite_is_paired_by_probe(
    telltale_home: Path, tmp_path: Path
) -> None:
    """E10's shape, on the scripted agent: the same probe at two instruction sets.

    Both arms run the byte-identical argv and both commits carry `src/alpha.py`, so the
    agent's answer and every score are the same on both sides. That is the point here:
    what moved is the environment, and the runner has to say which field moved and which
    file inside it. The two arms' fingerprint ids differ, which is what a base_sha
    intervention forbids and this factor requires.

    The digests are not asserted as literals. `bytes` is `len(BEFORE)` and `len(AFTER)`
    read off the constants above, which is the same number the fingerprint computed by a
    different route.
    """
    root = tmp_path / "repo"
    before, after = _rewritten(root)

    measured = intervention(
        _instructions_spec(root, before, after), telltale_home, tmp_path / "out"
    )

    assert measured["factor"] == "instructions"
    assert [arm["base_sha"] for arm in measured["arms"]] == [before, after]
    assertion = measured["fingerprint_assertion"]
    assert assertion["differing_fields"] == ["instruction_hashes"]
    assert assertion["expected_fields"] == ["instruction_hashes"]
    # Two environments, because the instruction surface IS part of the environment.
    assert len(set(assertion["fingerprint_ids"].values())) == 2
    # Which path inside the field moved, and not only that the field did. AGENTS.md is
    # the only one: the home files are hashed into the same field and are equal here.
    assert sorted(assertion["instruction_paths"]) == ["AGENTS.md"]
    digests = assertion["instruction_paths"]["AGENTS.md"]
    assert digests["before"]["bytes"] == len(BEFORE.encode("utf-8"))
    assert digests["after"]["bytes"] == len(AFTER.encode("utf-8"))
    assert digests["before"]["sha256"] != digests["after"]["sha256"]
    # Paired by probe, and the scores are the ones both commits can produce: the key is
    # {src/alpha.py}, the agent names it in both arms, and src/beta.py exists in
    # neither, so there is no wrong path on either side.
    assert sorted(measured["between"]) == ["P-narrow"]
    for arm in measured["arms"]:
        block = arm["report"]["probes"][0]
        assert block["scores"] == {"pass": REPETITIONS, "fail": 0, "unknown": 0}
        assert [one["score"]["precision"] for one in block["repetitions"]] == [1.0] * 2
    # The report names the file, its two hashes and its two sizes, and says what the
    # assertion proved rather than only which field it was about.
    printed = report_probe.intervention(measured)
    assert "factor instructions" in printed
    assert "instruction_hashes differ at AGENTS.md" in printed
    assert f"{len(BEFORE.encode('utf-8'))} bytes" in printed
    assert digests["after"]["sha256"][:12] in printed
    for forbidden in ("effect of", "impact", "cause", "no effect"):
        assert forbidden not in printed.replace("material environment effect", "")
    written = json.loads(
        (tmp_path / "out" / "T-instructions" / "intervention.json").read_text(
            encoding="utf-8"
        )
    )
    assert written == measured


@pytest.mark.integration
def test_the_same_two_commits_under_factor_base_sha_are_refused_naming_the_field(
    telltale_home: Path, tmp_path: Path
) -> None:
    """The refusal W4-T1 built, kept: an instruction rewrite is not a code refactor.

    Same repository, same two commits, same probes, one word of the spec different. The
    base_sha assertion is that the arms differ in NO fingerprint field, and this pair
    differs in one, so the runs happen and the comparison is refused afterwards with the
    field named. That refusal is why this task exists, and a factor that made it stop
    firing would have replaced a check rather than added one.
    """
    root = tmp_path / "repo"
    before, after = _rewritten(root)

    with pytest.raises(FingerprintMismatch) as refusal:
        intervention(
            _instructions_spec(root, before, after, factor="base_sha"), telltale_home
        )

    message = str(refusal.value)
    assert "instruction_hashes" in message, message
    assert "AGENTS.md" in message, message
    assert "expected to differ: []" in message, message
    # Refused AFTER the runs, so the captures are there: the two commits are a legal
    # experiment and it is the declared factor that is wrong.
    assert len(reader(telltale_home).captures()) == 2 * REPETITIONS


@pytest.mark.integration
def test_commits_that_move_code_as_well_are_refused_before_anything_runs(
    telltale_home: Path, tmp_path: Path
) -> None:
    """Two factors in one commit, and the fingerprint could never catch this pair.

    `instruction_hashes` moves whether or not the same commit edited a module, so the
    post-run assertion would pass and the report would say the instructions were the
    only difference. The diff between the two commits is where that can be seen, and it
    is read before any token is spent. Through the CLI, because "no database was
    created" is the assertion that says nothing ran.

    The second case is the mirror: two commits that differ in nothing at all. They pass
    the sha check (the shas differ) and would pass the fingerprint check (nothing moved,
    which is not what this factor expects), so an empty diff is refused here too.
    """
    root = tmp_path / "repo"
    before = probe_repository(root, agents=BEFORE)
    both = commit(
        root,
        "rewrite and edit",
        {"AGENTS.md": AFTER, GAMMA: "def gamma():\n    return 4\n"},
    )
    spec = _instructions_spec(root, before, both)
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")

    done = run_telltale(
        "experiment", "intervention", str(tmp_path / "spec.json"), home=telltale_home
    )

    assert done.returncode == 2, done.stdout
    assert GAMMA in done.stdout, done.stdout
    assert "two factors" in done.stdout, done.stdout
    # Named as the surfaces it is not one of, so the refusal says what would be allowed.
    assert "AGENTS.md" in done.stdout, done.stdout
    assert not (telltale_home / "telltale.db").exists()

    # An empty commit, which `commit` cannot make: two shas and one tree.
    git(root, "commit", "-q", "--allow-empty", "-m", "no change")
    empty = git(root, "rev-parse", "HEAD")
    with pytest.raises(SpecError) as refusal:
        intervention(_instructions_spec(root, both, empty), telltale_home)
    assert "differ in no path at all" in str(refusal.value)
    assert not (telltale_home / "telltale.db").exists()


@pytest.mark.integration
def test_the_environment_runner_compares_two_arms_across_an_instruction_rewrite(
    telltale_home: Path, tmp_path: Path
) -> None:
    """The same factor on the other runner: one task with an acceptance command.

    `experiment environment` takes the factor too, because an instruction rewrite is a
    difference a functional task can be run either side of just as well as a probe suite
    can. What is asserted is the pair of checks: identical argv, and payloads differing
    in the one field. The statistics are W2-T3's and are not touched by this task.
    """
    root = tmp_path / "repo"
    task_repository(root)
    before = commit(root, "instructions", {"AGENTS.md": BEFORE})
    after = commit(root, "rewrite AGENTS.md", {"AGENTS.md": AFTER})
    spec = {
        "task_id": "T-env-instructions",
        "experiment": EXPERIMENT,
        "repo": str(root),
        "base_sha": before,
        "acceptance": [sys.executable, "-c", ACCEPTANCE],
        "repetitions_per_arm": 1,
        "provider": "claude",
        "level": 1,
        "factor": "instructions",
        "arms": [
            {"name": "terse", "base_sha": before, "command": arm_command()},
            {"name": "explicit", "base_sha": after, "command": arm_command()},
        ],
    }

    measured = environment(spec, telltale_home)

    assertion = measured["fingerprint_assertion"]
    assert assertion["differing_fields"] == ["instruction_hashes"]
    assert sorted(assertion["instruction_paths"]) == ["AGENTS.md"]
    assert set(assertion["instruction_paths"]["AGENTS.md"]) == {"terse", "explicit"}
    assert "instruction_hashes" in assertion["assertion"]
    # Both arms ran the task and the harness accepted both: the rewrite did not change
    # what the acceptance command checks, which is what makes the arms comparable.
    for arm in measured["arms"]:
        assert arm["report"]["acceptance"] == {"pass": 1, "fail": 0}
    # The report's first assumption is this experiment's shape, and it says the diff
    # check happened rather than leaving the reader to assume the commits were scoped.
    assert "git diff" in measured["assumptions"][0]
    printed = report.environment(measured)
    assert "factor instructions" in printed
    assert "differing fields ['instruction_hashes']" in printed


# `telltale_home` for its other half: it sets $HOME to a temporary empty one, so the
# fingerprint below hashes no file of the operator's, and it asserts afterwards that
# nothing was written there.
@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_the_paths_the_diff_check_accepts_are_the_ones_the_fingerprint_hashes(
    tmp_path: Path,
) -> None:
    """The pre-run check is a restatement of env.py, so it is compared with env.py.

    The check reads a `git diff`, where nothing is checked out and no file exists to ask
    about, so it decides by the SHAPE of the path. That is a second statement of what
    env.fingerprint does with a worktree, and two statements drift. Here a directory is
    made with one file of every shape, the fingerprint is taken of it, and the paths it
    hashed are compared with the paths the check accepts.

    The direction matters. A path env.py hashes that the check refuses costs a refusal
    the owner can read; a path env.py does NOT hash that the check accepts would let a
    code change through as an instruction change, which is the failure this equality
    forbids.
    """
    root = tmp_path / "surfaces"
    shapes = [
        "AGENTS.md",
        "CLAUDE.md",
        ".claude/rules/style.md",
        ".claude/rules/deep/nested.md",
        "docs/AGENTS.md",
        "docs/CLAUDE.md",
        ".claude/settings.json",
        "src/alpha.py",
        "README.md",
    ]
    for name in shapes:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{name}\n", encoding="utf-8")

    payload = env.fingerprint("claude", ["claude", "-p"], root)

    # The home surfaces are hashed into the same field and no commit can move one, so
    # they are not what the diff check is about. $HOME is the fixture's empty temporary
    # one here, so this filter removes nothing today and is what keeps the test honest
    # on a machine where it would.
    hashed = {
        key for key in payload["instruction_hashes"] if not key.startswith("<home>")
    }
    accepted = {name for name in shapes if _instruction_surface(name)}

    assert hashed == accepted
    assert hashed == {"AGENTS.md", "CLAUDE.md", ".claude/rules/style.md"}
    # And the one the fixture repositories use is in it, so the tests above are testing
    # a path this check really accepts.
    assert "AGENTS.md" in accepted
    assert not _instruction_surface(ALPHA)
