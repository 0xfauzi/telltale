"""The probe runner and the intervention runner, against a real repository and agent.

Nothing here is stubbed. Every test makes a git repository with real commits, runs the
real `telltale experiment probe` machinery, which shells out to the real `telltale run`
console script, which wraps `tests/integration/fake_agent.py` as a real child process
that really reads the files it names, in a worktree that really exists at the commit the
spec pinned and is really removed afterwards.

The scored numbers are hand-computed in each docstring BEFORE the assertion, because a
scorer checked against its own output is not checked. The fake agent's `--answer` mode
names the paths it was given that exist in the checkout, so its answer is a function of
the commit: that is what makes the intervention a repository intervention rather than an
argv one, and the two arms below run byte-identical commands.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fake_agent
import pytest

from telltale import report_probe
from telltale.experiments import SpecError
from telltale.experiments_env import intervention
from telltale.experiments_measure import FingerprintMismatch
from telltale.experiments_probe import PRECISION, RECALL, probe, score
from telltale.stats import MATERIAL, UNRESOLVED
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Mapping

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"
EXPERIMENT = "W4-T1-test"
TASK = "T-probe"
REPETITIONS = 3

# What the agent is told to answer with. Both files exist at the second commit and only
# the first exists at the first, which is the whole of the intervention below.
ALPHA = "src/alpha.py"
BETA = "src/beta.py"
STRAY = "src/stray.py"
ANSWERED = f"{ALPHA},{BETA}"

# The seed bound both arms run under, for W2-T3's reason: every fabricated number in the
# fake agent is linear in its drawn seed, so an unbounded draw would move the token
# counts further than anything under test does.
SEED_MAX = 8


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _repository(root: Path) -> str:
    """A repository with one commit carrying alpha and stray. Returns its sha."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", ".")
    # On the REPOSITORY, never globally: the telltale_home fixture asserts that nothing
    # was written under $HOME.
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Telltale Test")
    (root / "README.md").write_text("a temporary repository\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / ALPHA).write_text("def load():\n    return 1\n", encoding="utf-8")
    (root / STRAY).write_text("def stray():\n    return 2\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "before")
    return _git(root, "rev-parse", "HEAD")


def _second_commit(root: Path) -> str:
    """The refactor: beta arrives. No instruction file is touched, on purpose.

    The fingerprint assertion of a base_sha experiment is that the two arms differ in NO
    fingerprint field, and `instruction_hashes` is one of them and is read out of the
    worktree. A commit that also added an AGENTS.md would be refused there, which is the
    check working rather than the check being wrong.
    """
    (root / BETA).write_text("def load():\n    return 3\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "after")
    return _git(root, "rev-parse", "HEAD")


def _command(answer: str = ANSWERED) -> list[str]:
    return [
        sys.executable, str(FAKE_AGENT), "-p", "{prompt}",
        "--output-format", "stream-json", "--seed-max", str(SEED_MAX),
        "--model", "sonnet", "--effort", "medium", "--answer", answer,
    ]  # fmt: skip


def _probes() -> list[dict[str, Any]]:
    """Two probes, one answered exactly and one the same answer overshoots.

    One agent, one answer, two keys. P-exact's key is both files the agent names, so it
    is answered exactly. P-narrow's key is only the first, so the second is a path the
    answer named that exists in the repository and is not in the key: a wrong path.
    """
    return [
        {
            "probe_id": "P-exact",
            "prompt": "locate the implementation behind load",
            "answer_key": {"paths": [ALPHA, BETA], "symbols": []},
        },
        {
            "probe_id": "P-narrow",
            "prompt": "identify the one module that defines load",
            "answer_key": {"paths": [ALPHA], "symbols": []},
        },
    ]


def _spec(
    root: Path,
    sha: str,
    probes: list[dict[str, Any]] | None = None,
    repetitions: int = REPETITIONS,
    task_id: str = TASK,
    command: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "experiment": EXPERIMENT,
        "repo": str(root),
        "base_sha": sha,
        "command": _command() if command is None else command,
        "probes": _probes() if probes is None else probes,
        "repetitions": repetitions,
        "provider": "claude",
        "level": 1,
    }


def _intervention_spec(
    root: Path, before: str, after: str, task_id: str = "T-refactor", **changed: Any
) -> dict[str, Any]:
    arms: list[dict[str, Any]] = [
        {"name": "before", "base_sha": before, "command": _command()},
        {"name": "after", "base_sha": after, "command": _command()},
    ]
    return {
        "task_id": task_id,
        "experiment": EXPERIMENT,
        "repo": str(root),
        "probes": [_probes()[1]],
        "repetitions_per_arm": REPETITIONS,
        "provider": "claude",
        "level": 1,
        "factor": "base_sha",
        "arms": arms,
        **changed,
    }


def _store(home: Path) -> Store:
    return Store(home / "telltale.db")


def _telltale(*args: str, home: Path) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("telltale")
    assert executable is not None, "no `telltale` on PATH: run `uv sync` first"
    return subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={
            "TELLTALE_HOME": str(home),
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ["HOME"],
        },
    )


def _block(report: Mapping[str, Any], probe_id: str) -> dict[str, Any]:
    found = [one for one in report["probes"] if one["probe_id"] == probe_id]
    assert len(found) == 1, [one["probe_id"] for one in report["probes"]]
    return dict(found[0])


def _outcomes(store: Store, capture_id: str) -> list[dict[str, Any]]:
    return [
        dict(row["payload"])
        for row in store.observations(capture_id)
        if row["observation_type"] == "external.outcome"
    ]


@pytest.mark.integration
def test_two_probes_are_scored_against_their_keys_at_three_repetitions(
    telltale_home: Path, tmp_path: Path
) -> None:
    """Spec 14.3, end to end, against numbers computed here before the run.

    The agent answers "the probe answer names these files: src/alpha.py, src/beta.py"
    every time, and the repository at this commit carries README.md, src/alpha.py and
    src/stray.py plus whatever the second commit adds. So, per repetition:

      P-exact, key {src/alpha.py, src/beta.py}: both key paths are named, so
      matched_keys = 2 and len(key) = 2. No other tracked path is named, so wrong_paths
      = 0. precision = 2 / (2 + 0) = 1.000 and recall = 2 / 2 = 1.000, status pass.

      P-narrow, key {src/alpha.py}: one key path is named, so matched_keys = 1 and
      len(key) = 1. src/beta.py is named, exists in the repository and is not in the
      key, so wrong_paths = 1. precision = 1 / (1 + 1) = 0.500 and recall = 1 / 1 =
      1.000, status fail. This is spec 14.3's own case: the two probes cost the agent
      the same three tool calls and one of the answers is wrong.

    The answer is deterministic, so at 3 repetitions each median is that value, the
    scaled MAD is 0, MDD is 0 and N_needed is 0, and the report warns that a spread of 0
    is what makes MDD 0.
    """
    root = tmp_path / "repo"
    sha = _repository(root)
    _second_commit(root)
    latest = _git(root, "rev-parse", "HEAD")

    report = probe(_spec(root, latest), telltale_home, tmp_path / "out")

    assert sha != latest
    assert len(report["captures"]) == 2 * REPETITIONS
    assert len(set(report["captures"])) == 2 * REPETITIONS
    assert report["environment_fingerprint_id"].startswith("env_")
    exact, narrow = _block(report, "P-exact"), _block(report, "P-narrow")
    assert exact["scores"] == {"pass": REPETITIONS, "fail": 0, "unknown": 0}
    assert narrow["scores"] == {"pass": 0, "fail": REPETITIONS, "unknown": 0}
    for run in exact["repetitions"]:
        assert run["score"]["precision"] == 1.0
        assert run["score"]["recall"] == 1.0
        assert run["score"]["matched_paths"] == [ALPHA, BETA]
        assert run["score"]["wrong_paths"] == []
    for run in narrow["repetitions"]:
        assert run["score"]["precision"] == 0.5
        assert run["score"]["recall"] == 1.0
        assert run["score"]["matched_paths"] == [ALPHA]
        assert run["score"]["wrong_paths"] == [BETA]
    # The two scores are columns of the vector, so one stats table covers them and the
    # 22 measures beside them.
    assert exact["stats"][PRECISION]["median"] == 1.0
    assert narrow["stats"][PRECISION]["median"] == 0.5
    for block in (exact, narrow):
        for name in (PRECISION, RECALL):
            row = block["stats"][name]
            assert row["n"] == REPETITIONS, name
            assert row["unknown"] == 0, name
            assert row["mad_scaled"] == 0.0, name
            assert row["mdd"] == 0.0, name
            assert row["n_needed"] == 0, name
        assert any("scaled MAD of 0" in one for one in block["warnings"])
    # Read-only: the probe agent edits nothing and runs nothing.
    assert exact["stats"]["tool_calls.Read"]["median"] == 2.0
    assert exact["stats"]["tool_calls"]["median"] == 2.0
    assert "tool_calls.Edit" not in exact["stats"]
    assert "tool_calls.Bash" not in exact["stats"]
    assert report["claim_class"] == {
        "vector": "derived",
        "score": "derived",
        "stats": "comparative",
    }
    # The written artefact is what a write-up cites, so it is checked as BYTES: exactly
    # one trailing newline, for the reason experiments._write states.
    raw = (tmp_path / "out" / TASK / "probe.json").read_text(encoding="utf-8")
    assert raw[-2:] == "}\n", raw[-40:]
    assert json.loads(raw) == report
    # Every worktree gone, and the repository back to one.
    assert list((telltale_home / "worktrees").iterdir()) == []


@pytest.mark.integration
def test_the_score_reaches_the_store_and_the_answer_text_does_not(
    telltale_home: Path, tmp_path: Path
) -> None:
    """The score is an external.outcome category. The answer it came from is nowhere.

    Both halves matter. The outcome allowlist carries `categories` as a bounded list of
    short symbolic strings, so a probe score needs no new observation type; and the
    answer text is read in the runner's own process and dropped, so the bytes of the
    database do not contain the sentence the agent wrote. The grep is over the raw file,
    not over what a reader returns, because a reader that hides a column would pass.
    """
    root = tmp_path / "repo"
    _repository(root)
    sha = _second_commit(root)

    report = probe(_spec(root, sha, repetitions=1), telltale_home)

    store = _store(telltale_home)
    narrow = _block(report, "P-narrow")
    outcomes = _outcomes(store, narrow["captures"][0])
    assert len(outcomes) == 1
    assert outcomes[0]["kind"] == "mechanical_verification"
    assert outcomes[0]["status"] == "fail"
    assert outcomes[0]["categories"] == [
        "probe:P-narrow",
        "precision:0.500",
        "recall:1.000",
    ]
    exact = _outcomes(store, _block(report, "P-exact")["captures"][0])
    assert exact[0]["categories"] == [
        "probe:P-exact",
        "precision:1.000",
        "recall:1.000",
    ]
    assert exact[0]["status"] == "pass"
    # The sentence the agent wrote is not in the database, on any surface, at any level.
    # The PATHS are (a Read tool_use carries file_path and the allowlist keeps it), so
    # the phrase grepped for is the frame around them and not a path.
    raw = (telltale_home / "telltale.db").read_bytes()
    assert fake_agent.ANSWER_PREFIX.encode("utf-8") not in raw
    assert b"the probe answer names" not in raw
    # And the file_path of a Read really is there, so the grep above proves something.
    assert ALPHA.encode("utf-8") in raw


@pytest.mark.integration
def test_a_repetition_with_no_result_text_scores_unknown_and_never_zero(
    telltale_home: Path, tmp_path: Path
) -> None:
    """No answer is not a wrong answer. Both numbers are None and n counts the rest.

    The fake agent emits a `result` field only in --answer mode, so a command without it
    is a session that finished and said nothing a scorer can read. Design invariant 5:
    a recall of 0 here would be a measurement of an agent that answered wrongly, and
    what happened is that nobody saw an answer.
    """
    root = tmp_path / "repo"
    sha = _repository(root)
    silent = [
        sys.executable, str(FAKE_AGENT), "-p", "{prompt}",
        "--output-format", "stream-json", "--seed", "1",
    ]  # fmt: skip

    report = probe(
        _spec(root, sha, probes=[_probes()[1]], repetitions=1, command=silent),
        telltale_home,
    )

    block = _block(report, "P-narrow")
    assert block["scores"] == {"pass": 0, "fail": 0, "unknown": 1}
    scored = block["repetitions"][0]["score"]
    assert scored["precision"] is None
    assert scored["recall"] is None
    assert scored["matched_keys"] is None
    assert block["stats"][PRECISION] == {
        **block["stats"][PRECISION],
        "n": 0,
        "unknown": 1,
        "median": None,
        "mad_scaled": None,
        "mdd": None,
        "n_needed": None,
    }
    assert any("printed no result message" in one for one in block["warnings"])
    # And the store says unknown in words rather than by leaving the category out.
    outcomes = _outcomes(_store(telltale_home), block["captures"][0])
    assert outcomes[0]["categories"] == [
        "probe:P-narrow",
        "precision:unknown",
        "recall:unknown",
    ]
    assert outcomes[0]["status"] == "unknown"


@pytest.mark.integration
def test_the_definitions_of_precision_and_recall_on_the_awkward_answers() -> None:
    """The four cases the end-to-end run cannot reach, against hand computation.

    `score` is the real scorer with real arguments; what is constructed here is the
    answer text, which in a run comes from a process rather than from a fixture.

      A basename alone is NOT a match. "alpha.py" names a file whose repository-relative
      path is src/alpha.py, and an answer that says only the basename has not located it
      in the repository. It is reported under basename_only and scores 0.

      A symbol is a whole word, dots included. `load` does not match `loader`, and
      `Store.open` matches only that literal.

      An answer that names nothing the key or the repository knows has an EMPTY
      precision denominator, so precision is None while recall is a measured 0.

      A wrongly named symbol is not counted anywhere: the repository enumerates its
      paths and not the symbols an answer could invent, which is why the report calls
      precision an upper bound.
    """
    tracked = ("README.md", ALPHA, BETA, STRAY)
    key = {"paths": [ALPHA], "symbols": ["load"]}

    basename = score("it is in alpha.py", key, tracked)
    assert basename["matched_paths"] == []
    assert basename["basename_only"] == [ALPHA]
    assert basename["matched_keys"] == 0
    assert basename["recall"] == 0.0
    assert basename["precision"] is None

    worded = score(f"{ALPHA} defines loader and Store.open", key, tracked)
    assert worded["matched_symbols"] == []
    assert worded["recall"] == 0.5
    assert worded["precision"] == 1.0

    astray = score(f"see {STRAY} and {BETA}", key, tracked)
    assert astray["wrong_paths"] == [BETA, STRAY]
    assert astray["matched_keys"] == 0
    assert astray["precision"] == 0.0
    assert astray["recall"] == 0.0

    invented = score(f"{ALPHA} defines load and save", key, tracked)
    assert invented["matched_keys"] == 2
    assert invented["wrong_paths"] == []
    assert invented["precision"] == 1.0
    assert invented["recall"] == 1.0


@pytest.mark.integration
def test_an_answer_key_naming_a_path_the_commit_does_not_carry_is_refused(
    telltale_home: Path, tmp_path: Path
) -> None:
    """A key path that is not in the tree at base_sha is a typo, not a hard probe.

    It would score recall 0 in every repetition for a reason that is nothing to do with
    the agent, so it is refused before any token is spent and the path is named. The
    first commit does not carry src/beta.py, which is exactly the case an intervention
    reusing one key across two commits runs into.
    """
    root = tmp_path / "repo"
    sha = _repository(root)
    _second_commit(root)

    with pytest.raises(SpecError) as refusal:
        probe(_spec(root, sha), telltale_home)

    message = str(refusal.value)
    assert "P-exact" in message, message
    assert BETA in message, message
    assert "not about the agent" in message, message
    # Refused before anything ran: the database the captures would go into is absent.
    assert not (telltale_home / "telltale.db").exists()


@pytest.mark.integration
def test_a_spec_that_is_not_a_probe_suite_is_refused_before_anything_runs(
    telltale_home: Path, tmp_path: Path
) -> None:
    """Four refusals, each naming what is wrong, and none of them opening a store."""
    root = tmp_path / "repo"
    _repository(root)
    sha = _second_commit(root)

    missing = _spec(root, sha)
    del missing["level"]
    missing["levels"] = 1
    with pytest.raises(SpecError) as refusal:
        probe(missing, telltale_home)
    assert "missing ['level']" in str(refusal.value)
    assert "unexpected ['levels']" in str(refusal.value)

    # A command with no placeholder runs one prompt N times and reports N probes.
    fixed = _spec(root, sha)
    fixed["command"] = [one.replace("{prompt}", "hello") for one in fixed["command"]]
    with pytest.raises(SpecError) as refusal:
        probe(fixed, telltale_home)
    assert "{prompt}" in str(refusal.value)

    # An empty key has no denominator for recall.
    empty = _spec(root, sha, probes=[{**_probes()[0], "answer_key": {"paths": []}}])
    with pytest.raises(SpecError) as refusal:
        probe(empty, telltale_home)
    assert "no path and no symbol" in str(refusal.value)

    # A probe_id long enough to be cut by the allowlist's enum bound is two probes
    # reported as one, so it is refused with the bound named.
    long = _spec(root, sha, probes=[{**_probes()[0], "probe_id": "P" * 80}])
    with pytest.raises(SpecError) as refusal:
        probe(long, telltale_home)
    assert "64-character bound" in str(refusal.value)

    assert not (telltale_home / "telltale.db").exists()


@pytest.mark.integration
def test_a_probe_condition_is_never_resumed_from_the_store(
    telltale_home: Path, tmp_path: Path
) -> None:
    """The repeat runner reads attempt k back; this one refuses to.

    A probe repetition IS its score, the score comes from the answer text, and the
    answer text is never stored. Reading the capture back would put a repetition with an
    unknown score into the statistics, which reads as a measurement rather than as a
    runner that could not do it.
    """
    root = tmp_path / "repo"
    _repository(root)
    sha = _second_commit(root)
    spec = _spec(root, sha, probes=[_probes()[0]], repetitions=1, task_id="T-again")

    probe(spec, telltale_home)
    before = len(_store(telltale_home).captures())
    with pytest.raises(SpecError) as refusal:
        probe(spec, telltale_home)

    message = str(refusal.value)
    assert "never resumed" in message, message
    assert "T-again-P-exact/1" in message, message
    # Refused rather than run: no second capture was made.
    assert len(_store(telltale_home).captures()) == before


@pytest.mark.integration
def test_one_probe_suite_at_two_commits_is_paired_by_probe(
    telltale_home: Path, tmp_path: Path
) -> None:
    """Spec 14.3's controlled intervention, against numbers computed before the run.

    Both arms run the SAME argv: `--answer src/alpha.py,src/beta.py`. The agent names
    the paths that exist in its checkout, so the answer is a function of the commit:

      Arm "before" (src/beta.py does not exist yet) answers src/alpha.py alone. The key
      is {src/alpha.py}, so matched_keys = 1, wrong_paths = 0, precision = 1.000,
      recall = 1.000, and the agent makes 1 Read.

      Arm "after" answers src/alpha.py and src/beta.py. matched_keys = 1, wrong_paths =
      1 (src/beta.py exists and is not in the key), precision = 0.500, recall = 1.000,
      and the agent makes 2 Reads.

    So over 3 repetitions per arm, a = [1.0, 1.0, 1.0] and b = [0.5, 0.5, 0.5] for
    precision. Hodges-Lehmann shift = median of the 9 differences = -0.500. Cliff's
    delta = (0 - 9) / 9 = -1.0. The exact Mann-Whitney: the pooled mid-ranks are 2, 2, 2
    for the 0.5s and 5, 5, 5 for the 1.0s, so R_a = 15, U_a = 15 - 3 (4) / 2 = 9, the
    null centre is 3 * 3 / 2 = 4.5, and exactly 2 of the C(6, 3) = 20 splits are 4.5 or
    further from it, so p = 2 / 20 = 0.100. Both MADs are 0 so MDD is 0 and the label is
    "material environment effect" for any nonzero shift, which the warning says.

    Recall does not move: a = b = [1.0, 1.0, 1.0], shift 0, delta 0, p 1.000, and the
    label is "not resolved at n". Read counts move the other way from precision: shift
    +1, delta +1, p 0.100. That pair is spec 14.3's sentence as a number. The "after"
    commit costs one more Read AND scores half the precision, so the cheaper arm is not
    the better one and nothing in this table may say it is.
    """
    root = tmp_path / "repo"
    before = _repository(root)
    after = _second_commit(root)

    report = intervention(
        _intervention_spec(root, before, after), telltale_home, tmp_path / "out"
    )

    assert report["factor"] == "base_sha"
    assert [arm["base_sha"] for arm in report["arms"]] == [before, after]
    # The fingerprint assertion, inverted: the payload carries no commit, so the two
    # arms must differ in NO field, and that is what catches a refactor that also moved
    # an instruction file.
    assertion = report["fingerprint_assertion"]
    assert assertion["differing_fields"] == []
    assert assertion["expected_fields"] == []
    assert len(set(assertion["fingerprint_ids"].values())) == 1
    assert "carries no commit" in assertion["assertion"]
    # Paired by probe: one table per probe, and no table pools the two.
    assert sorted(report["between"]) == ["P-narrow"]
    rows = report["between"]["P-narrow"]
    assert rows[PRECISION]["hl_shift"] == -0.5
    assert rows[PRECISION]["cliffs_delta"] == -1.0
    assert rows[PRECISION]["p"] == 0.1
    assert rows[PRECISION]["s"] == 0.0
    assert rows[PRECISION]["mdd"] == 0.0
    assert rows[PRECISION]["label"] == MATERIAL
    assert rows[RECALL]["hl_shift"] == 0.0
    assert rows[RECALL]["cliffs_delta"] == 0.0
    assert rows[RECALL]["p"] == 1.0
    assert rows[RECALL]["label"] == UNRESOLVED
    assert rows["tool_calls.Read"]["hl_shift"] == 1.0
    assert rows["tool_calls.Read"]["cliffs_delta"] == 1.0
    assert rows["tool_calls.Read"]["p"] == 0.1
    for metric, row in rows.items():
        assert row["claim_class"] == "comparative", metric
    # Not every metric: `compactions.pre_compaction_tokens` is unknown to this agent in
    # both arms, so its n is 0 on both sides and the row says so rather than being
    # dropped. The three the assertions above read are the ones that were measured.
    for metric in (PRECISION, RECALL, "tool_calls.Read"):
        assert rows[metric]["n_a"] == REPETITIONS, metric
        assert rows[metric]["n_b"] == REPETITIONS, metric
    # The printed report carries the constants, the assertion and the vocabulary, and
    # never the three words ADR-014 refuses.
    printed = report_probe.intervention(report)
    assert "pilot_repetitions_per_arm=5" in printed
    assert "factor base_sha" in printed
    assert "BETWEEN ARMS, probe P-narrow" in printed
    assert UNRESOLVED in printed
    assert "behaviour with a wrong answer is not an improvement" in printed
    for forbidden in ("effect of", "impact", "cause", "no effect"):
        assert forbidden not in printed.replace(MATERIAL, ""), forbidden
    raw = (tmp_path / "out" / "T-refactor" / "intervention.json").read_text(
        encoding="utf-8"
    )
    assert raw[-2:] == "}\n", raw[-40:]
    assert json.loads(raw) == report


@pytest.mark.integration
def test_arms_that_differ_in_base_sha_and_a_flag_are_refused_naming_both(
    telltale_home: Path, tmp_path: Path
) -> None:
    """Two differences are two experiments, and this pair could not be caught later.

    base_sha is not in the argv and the fingerprint carries no commit, so the post-run
    assertion would see the flag and never the commit: it would report a model
    difference and say nothing about the two repository versions the arms actually ran.
    The refusal names both tokens and both commits, before anything runs.
    """
    root = tmp_path / "repo"
    before = _repository(root)
    after = _second_commit(root)
    spec = _intervention_spec(root, before, after)
    spec["arms"][1]["command"] = [
        one.replace("sonnet", "opus") for one in spec["arms"][1]["command"]
    ]
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")

    done = _telltale(
        "experiment", "intervention", str(tmp_path / "spec.json"), home=telltale_home
    )

    assert done.returncode == 2, done.stdout
    for token in ("'sonnet'", "'opus'", before, after, "base_sha"):
        assert token in done.stdout, done.stdout
    assert not (telltale_home / "telltale.db").exists()


@pytest.mark.integration
def test_two_arms_at_one_commit_and_a_stray_commit_under_another_factor_are_refused(
    telltale_home: Path, tmp_path: Path
) -> None:
    """The two ways a declared factor and the repository can disagree.

    Both refusals are about the same hole: base_sha is invisible to the argv check and
    to the fingerprint check, so this is the only place either can be caught.
    """
    root = tmp_path / "repo"
    before = _repository(root)
    after = _second_commit(root)

    same = _intervention_spec(root, before, before)
    with pytest.raises(SpecError) as refusal:
        intervention(same, telltale_home)
    assert "both arms run at" in str(refusal.value)

    # An arm with no commit at all: an intervention arm IS a commit.
    nameless = _intervention_spec(root, before, after)
    del nameless["arms"][1]["base_sha"]
    with pytest.raises(SpecError) as refusal:
        intervention(nameless, telltale_home)
    assert "no base_sha" in str(refusal.value)

    # And a launch-flag experiment whose arms quietly moved the repository.
    from telltale.experiments_env import environment

    flagged = {
        "task_id": "T-effort",
        "experiment": EXPERIMENT,
        "repo": str(root),
        "base_sha": before,
        "acceptance": [sys.executable, "-c", "pass"],
        "repetitions_per_arm": 1,
        "provider": "claude",
        "level": 1,
        "factor": "effort",
        "arms": [
            {"name": "low", "command": _command(), "base_sha": before},
            {"name": "high", "command": _command(), "base_sha": after},
        ],
    }
    with pytest.raises(SpecError) as refusal:
        environment(flagged, telltale_home)
    message = str(refusal.value)
    assert before in message, message
    assert after in message, message
    assert "'effort'" in message, message
    assert not (telltale_home / "telltale.db").exists()


@pytest.mark.integration
def test_the_cli_prints_the_scores_and_refuses_a_broken_spec(
    telltale_home: Path, tmp_path: Path
) -> None:
    """`telltale experiment probe` end to end, through the console script.

    The printed table is what a write-up quotes, so the two score columns and the claim
    class on every row are asserted on the bytes the command wrote.
    """
    root = tmp_path / "repo"
    _repository(root)
    sha = _second_commit(root)
    spec = _spec(root, sha, repetitions=1, task_id="T-cli")
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")

    done = _telltale(
        "experiment",
        "probe",
        str(tmp_path / "spec.json"),
        "--out",
        str(tmp_path / "out"),
        home=telltale_home,
    )

    assert done.returncode == 0, done.stderr
    assert "PROBE P-exact" in done.stdout, done.stdout
    assert "PROBE P-narrow" in done.stdout, done.stdout
    assert "1.000" in done.stdout, done.stdout
    assert "0.500" in done.stdout, done.stdout
    assert "behaviour with a wrong answer" in done.stdout, done.stdout
    written = json.loads(
        (tmp_path / "out" / "T-cli" / "probe.json").read_text(encoding="utf-8")
    )
    assert written["task_id"] == "T-cli"
    assert len(written["probes"]) == 2
    # A spec that is not a suite is exit code 2 and one line, never a traceback.
    broken = _spec(root, sha)
    del broken["probes"]
    (tmp_path / "broken.json").write_text(json.dumps(broken), encoding="utf-8")
    refused = _telltale(
        "experiment", "probe", str(tmp_path / "broken.json"), home=telltale_home
    )
    assert refused.returncode == 2, refused.stdout
    assert "missing ['probes']" in refused.stdout, refused.stdout
    assert "Traceback" not in refused.stderr, refused.stderr


@pytest.mark.integration
def test_a_suite_whose_captures_are_two_environments_is_refused(
    telltale_home: Path, tmp_path: Path
) -> None:
    """Spec 14.3: the environment fingerprint is held constant across the probes.

    The assertion is over the whole suite rather than per probe, because two probes run
    under two models are two conditions however well each one scored. It is
    `experiments_measure.one_fingerprint`, the same one the repeat runner makes, and it
    names the field that differs.
    """
    root = tmp_path / "repo"
    _repository(root)
    sha = _second_commit(root)
    first = probe(
        _spec(root, sha, probes=[_probes()[0]], repetitions=1, task_id="T-haiku",
              command=[one.replace("sonnet", "haiku") for one in _command()]),
        telltale_home,
    )  # fmt: skip
    second = probe(
        _spec(root, sha, probes=[_probes()[0]], repetitions=1, task_id="T-opus",
              command=[one.replace("sonnet", "opus") for one in _command()]),
        telltale_home,
    )  # fmt: skip
    assert first["environment_fingerprint_id"] != second["environment_fingerprint_id"]

    from telltale.experiments_measure import one_fingerprint

    with pytest.raises(FingerprintMismatch) as refusal:
        one_fingerprint(
            _store(telltale_home),
            [first["captures"][0], second["captures"][0]],
        )
    message = str(refusal.value)
    assert "model=" in message, message
    assert "haiku" in message, message
    assert "opus" in message, message
