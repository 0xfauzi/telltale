"""The repeat runner, against a real git repository and a real scripted agent.

Nothing here is stubbed. Every test makes a git repository with one commit, runs the
real `telltale experiment repeat` machinery, which shells out to the real `telltale run`
console script, which wraps `tests/integration/fake_agent.py` as a real child process
that really reads files, really rewrites one and really runs a command in a worktree
that really exists and is really removed afterwards. The only thing the fake agent
fakes is the token counts, and the point of these tests is the mechanism around them.

The agent is scripted rather than real because the runner is what is under test. A test
that spent tokens on Claude Code would learn nothing about the worktree lifecycle, the
correlation records or the fingerprint assertion, and it would learn it slowly.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fake_agent
import pytest

from telltale import report as report_module
from telltale.cohorts import VECTOR
from telltale.experiments import SpecError, from_store, repeat
from telltale.experiments_env import environment
from telltale.experiments_measure import FingerprintMismatch, one_fingerprint, vector
from telltale.stats import MATERIAL, UNRESOLVED, TooManyValues, mann_whitney_exact
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Mapping

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"
TASK = "T-repeat"
EXPERIMENT = "W1-T4-test"
REPETITIONS = 5

# The acceptance command: deterministic, run by the harness after the agent exits, and
# the only thing that decides pass or fail. `--fail` makes the agent write 41.
ACCEPTANCE = (
    "import pathlib,sys;"
    "sys.exit(0 if pathlib.Path('answer.txt').read_text().strip()=='42' else 1)"
)

# The vector's three stream facts, which no reducer measures, and the tool calls the
# fake agent really makes.
STREAM_METRICS = ("num_turns", "duration_ms")
TOOL_METRICS = ("tool_calls", "tool_calls.Bash", "tool_calls.Edit", "tool_calls.Read")

# The one metric of spec 13.7's vector a fake-agent capture leaves unknown. Measured
# rather than reasoned: the agent never compacts, so `pre_compaction_tokens` is a sum
# over an empty set and stays null (measures.py's second rule about zero).
#
# `context_token_burden.cache_read_tokens` was the second entry here until W3-T0. It was
# null because `activities._request` read `correlate.USAGE_KEYS`, the OTel spelling,
# and a stream-only request spells the same counter `cache_read_input_tokens`. The
# fake agent emitted the number all along; the reducer did not read it. It is now in
# MEASURE_METRICS, and this comment is what stops it being put back.
UNKNOWN_TO_THE_FAKE_AGENT = ("compactions.pre_compaction_tokens",)

# The other 20, keyed as the vector keys them.
MEASURE_METRICS = tuple(
    key
    for family, names in VECTOR
    for name in names
    if (key := f"{family}.{name}") not in UNKNOWN_TO_THE_FAKE_AGENT
)


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _repository(root: Path) -> str:
    """A git repository with one commit. Returns its sha.

    user.email and user.name are set on the REPOSITORY, not globally: the
    `telltale_home` fixture asserts that nothing was written under $HOME.
    """
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Telltale Test")
    (root / "README.md").write_text("a temporary repository\n", encoding="utf-8")
    (root / "answer.txt").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return _git(root, "rev-parse", "HEAD")


def _spec(
    root: Path,
    sha: str,
    repetitions: int = REPETITIONS,
    model: str = "sonnet",
    failing: bool = False,
    task_id: str = TASK,
) -> dict[str, Any]:
    command = [
        sys.executable, str(FAKE_AGENT), "-p", "make the answer 42",
        "--output-format", "stream-json", "--model", model, "--effort", "medium",
    ]  # fmt: skip
    if failing:
        command.append("--fail")
    return {
        "task_id": task_id,
        "experiment": EXPERIMENT,
        "repo": str(root),
        "base_sha": sha,
        "command": command,
        "acceptance": [sys.executable, "-c", ACCEPTANCE],
        "repetitions": repetitions,
        "provider": "claude",
        "level": 1,
    }


def _store(home: Path) -> Store:
    """A reader over the database the runner wrote. Read methods need no open()."""
    return Store(home / "telltale.db")


def _payloads(store: Store, capture_id: str, obs_type: str) -> list[dict[str, Any]]:
    return [
        dict(row["payload"])
        for row in store.observations(capture_id)
        if row["observation_type"] == obs_type
    ]


def _telltale(*args: str, home: Path) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("telltale")
    assert executable is not None, "no `telltale` on PATH: run `uv sync` first"
    return subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        # A named environment rather than the inherited one: the child must write into
        # the home this test was given, and HOME is the temporary one the fixture set.
        env={
            "TELLTALE_HOME": str(home),
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ["HOME"],
        },
    )


def _assert_statistics(found: Mapping[str, Any]) -> None:
    """Robust statistics per metric, never a mean alone, and no unknown made a zero.

    The vector is spec 13.7's own 22 metrics read back out of the evidence table, so
    these rows and a `telltale vector` of any of the same captures are one set of
    numbers rather than two.
    """
    for metric in (*MEASURE_METRICS, *STREAM_METRICS, *TOOL_METRICS):
        row = found[metric]
        assert row["n"] == REPETITIONS, metric
        assert row["unknown"] == 0, metric
        assert row["median"] is not None, metric
        assert row["mad_scaled"] is not None, metric
        assert len(row["values"]) == REPETITIONS, metric
    for metric in UNKNOWN_TO_THE_FAKE_AGENT:
        row = found[metric]
        assert row["n"] == 0, metric
        assert row["unknown"] == REPETITIONS, metric
        assert row["median"] is None, metric


def _rows(stdout: str, header: str) -> list[list[str]]:
    """The table under `header`, as cell lists. Blank line ends a table."""
    lines = stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(header))
    out: list[list[str]] = []
    for line in lines[start + 2 :]:
        if not line.strip():
            break
        out.append(line.split())
    return out


@pytest.mark.integration
def test_five_repetitions_are_five_captures_in_one_environment(
    telltale_home: Path, tmp_path: Path
) -> None:
    """The pilot of design 6.12: five runs of one task, and what varied.

    Everything the H2 protocol asks to be recorded per run is asserted here: the
    capture, the attempt correlated to the task, the acceptance outcome, and one
    environment fingerprint across the condition. The worktrees are checked afterwards
    because a runner that leaks them makes the second experiment on a repository
    measure a repository the first one left behind.
    """
    root = tmp_path / "repo"
    sha = _repository(root)

    report = repeat(_spec(root, sha), telltale_home)

    store = _store(telltale_home)
    assert len(report["captures"]) == REPETITIONS
    assert len(set(report["captures"])) == REPETITIONS
    assert {str(row["capture_id"]) for row in store.captures()} == set(
        report["captures"]
    )
    # One environment, and every capture's own column agrees with the report. Only the
    # rows that HAVE the column: repo.identity is emitted before the fingerprint is
    # computed, and the external API rows are written by a receiver that was never told
    # which environment the capture ran in (see docs/log/W1-T4.md, UNSURE).
    assert report["environment_fingerprint_id"].startswith("env_")
    for capture_id in report["captures"]:
        found = {
            row["environment_fingerprint_id"]
            for row in store.observations(capture_id)
            if row["environment_fingerprint_id"] is not None
        }
        assert found == {report["environment_fingerprint_id"]}
    # Attempts 1..5, correlated to the task through /v1/correlations.
    correlations = [
        one
        for capture_id in report["captures"]
        for one in _payloads(store, capture_id, "external.correlation")
    ]
    assert {one["attempt"] for one in correlations} == set(range(1, REPETITIONS + 1))
    assert {one["task_id"] for one in correlations} == {TASK}
    assert {one["external_system"] for one in correlations} == {"telltale-experiment"}
    # The acceptance outcome the HARNESS recorded, once per repetition.
    outcomes = [
        one
        for capture_id in report["captures"]
        for one in _payloads(store, capture_id, "external.outcome")
    ]
    assert len(outcomes) == REPETITIONS
    assert {one["kind"] for one in outcomes} == {"mechanical_verification"}
    assert {one["status"] for one in outcomes} == {"pass"}
    assert report["acceptance"] == {"pass": REPETITIONS, "fail": 0}
    _assert_statistics(report["stats"])
    assert report["claim_class"] == {"vector": "derived", "stats": "comparative"}
    # Every worktree gone, and the repository back to one.
    assert _git(root, "worktree", "list").splitlines() == [
        _git(root, "worktree", "list").splitlines()[0]
    ]
    leftover = list((telltale_home / "worktrees").iterdir())
    assert leftover == [], leftover


@pytest.mark.integration
def test_the_fail_flag_records_a_failed_acceptance_and_writes_the_report(
    telltale_home: Path, tmp_path: Path
) -> None:
    """`--fail` makes the ACCEPTANCE command fail while the agent still exits 0.

    That separation is the point of design 6.12's rule that the harness runs the
    acceptance command: an agent that reported its own success would report success
    here. This one goes through the CLI, so the printed table and the written
    report.json are exercised too.
    """
    root = tmp_path / "repo"
    sha = _repository(root)
    spec = _spec(root, sha, repetitions=1, failing=True, task_id="T-fail")
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")

    done = _telltale(
        "experiment",
        "repeat",
        str(tmp_path / "spec.json"),
        "--out",
        str(tmp_path / "out"),
        home=telltale_home,
    )

    assert done.returncode == 0, done.stderr
    assert "'fail': 1" in done.stdout, done.stdout
    written = json.loads(
        (tmp_path / "out" / "T-fail" / "report.json").read_text(encoding="utf-8")
    )
    assert written["acceptance"] == {"pass": 0, "fail": 1}
    assert written["repetitions"][0]["exit_code"] == 0
    assert written["repetitions"][0]["acceptance"]["status"] == "fail"
    store = _store(telltale_home)
    outcomes = _payloads(store, written["captures"][0], "external.outcome")
    assert [one["status"] for one in outcomes] == ["fail"]
    # The stats table prints with a claim class on every row.
    rows = _rows(done.stdout, "METRIC")
    assert rows, done.stdout
    assert {row[1] for row in rows} == {"comparative"}


@pytest.mark.integration
def test_two_conditions_that_differ_by_model_are_refused_by_name(
    telltale_home: Path, tmp_path: Path
) -> None:
    """Two runs under two --model values are two environments, and the runner says so.

    Run separately, because within one condition the command is fixed. What is under
    test is the assertion design 6.12 asks the harness to make: fingerprints identical
    within a condition, and when they are not, WHICH field is the difference.
    """
    root = tmp_path / "repo"
    sha = _repository(root)
    first = repeat(_spec(root, sha, repetitions=1, model="haiku"), telltale_home)
    second = repeat(
        _spec(root, sha, repetitions=1, model="opus", task_id="T-other"), telltale_home
    )
    assert first["environment_fingerprint_id"] != second["environment_fingerprint_id"]

    together = [first["captures"][0], second["captures"][0]]
    with pytest.raises(FingerprintMismatch) as refusal:
        one_fingerprint(_store(telltale_home), together)

    message = str(refusal.value)
    assert "model=" in message, message
    assert "haiku" in message, message
    assert "opus" in message, message
    # Only the declared factor differs: the rest of the fingerprint is unchanged.
    assert message.count("=[") == 1, message


@pytest.mark.integration
def test_a_capture_with_no_stream_has_unknown_numbers_and_not_zeros(
    telltale_home: Path,
) -> None:
    """Unknown stays None, and the two zeros this capture really did see stay zeros.

    This is design invariant 5 at the one place the repeat runner could break it: a
    vector of zeros would enter the statistics as real observations of nothing
    happening, and the median of that is a number nobody measured. It cuts the other
    way too. The launcher takes its own repository snapshots whatever the child is, so
    the four numbers those snapshots answer were observed here and are 0; everything the
    child would have had to be an agent to show is None. Two of the four need the
    working directory to be a git repository, which under pytest it is.
    """
    done = _telltale("run", "--", sys.executable, "-c", "pass", home=telltale_home)
    assert done.returncode == 0, done.stderr

    store = _store(telltale_home)
    capture_id = str(store.captures()[0]["capture_id"])
    found = vector(store, capture_id)

    assert set(found) == {*MEASURE_METRICS, *UNKNOWN_TO_THE_FAKE_AGENT, *STREAM_METRICS,
                          "tool_calls"}  # fmt: skip
    seen = {name: value for name, value in found.items() if value is not None}
    assert set(seen) == {
        "edit_turnover.max_diff_lines",
        "edit_turnover.final_diff_lines",
        "edit_turnover.reversions",
        "stable_state_work.stable_state_work_intervals",
    }, found
    # The two diff-line numbers are the size of whatever this checkout was carrying when
    # the test ran, so only their presence is asserted. The other two are counts over
    # what happened DURING the capture, and nothing happened.
    assert seen["edit_turnover.reversions"] == 0
    assert seen["stable_state_work.stable_state_work_intervals"] == 0
    for name in (*STREAM_METRICS, "tool_calls"):
        assert found[name] is None, name


@pytest.mark.integration
def test_a_headless_claude_that_cannot_approve_a_tool_call_is_refused(
    telltale_home: Path, tmp_path: Path
) -> None:
    """E05's defect, turned into a refusal. `claude -p` plus acceptEdits does nothing.

    Measured on 2026-09-02: five sessions under that pair each made three Bash calls,
    had all three denied because nobody in a headless run can approve one, and ended
    having read nothing and edited nothing. The condition completed and measured a
    permission failure. Refused before the store is even opened, which is the assertion
    on the last line.
    """
    root = tmp_path / "repo"
    sha = _repository(root)
    broken = _spec(root, sha, repetitions=1)
    broken["command"] = [
        "claude", "-p", "--model", "sonnet", "--permission-mode", "acceptEdits",
    ]  # fmt: skip

    with pytest.raises(SpecError) as refusal:
        repeat(broken, telltale_home)

    message = str(refusal.value)
    assert "acceptEdits" in message, message
    assert "nobody to approve" in message, message
    assert "bypassPermissions" in message, message
    assert not (telltale_home / "telltale.db").exists()


@pytest.mark.integration
def test_a_report_is_rebuilt_from_the_store_without_running_anything(
    telltale_home: Path, tmp_path: Path
) -> None:
    """The recovery path: the same report, from captures alone, launching nothing.

    `repeat` writes nothing until every repetition is done, so a runner killed at
    repetition N leaves N captures and no report. What is asserted is that the rebuilt
    report agrees with the one the run produced on everything a capture carries, and
    that the one field it cannot carry is None rather than substituted.
    """
    root = tmp_path / "repo"
    sha = _repository(root)
    spec = _spec(root, sha, repetitions=2, task_id="T-recover")

    ran = repeat(spec, telltale_home)
    rebuilt = from_store(spec, telltale_home)

    assert rebuilt["captures"] == ran["captures"]
    assert rebuilt["environment_fingerprint_id"] == ran["environment_fingerprint_id"]
    assert rebuilt["acceptance"] == ran["acceptance"]
    assert rebuilt["stats"] == ran["stats"]
    assert rebuilt["recovered_from_store"] is True
    assert ran["recovered_from_store"] is False
    for before, after in zip(ran["repetitions"], rebuilt["repetitions"], strict=True):
        assert after["attempt"] == before["attempt"]
        assert after["duration_ms"] == before["duration_ms"]
        assert after["acceptance"]["status"] == before["acceptance"]["status"]
        # The wrapper's perf_counter cannot come back, and the capture's own span is
        # beside it under a different name rather than in its place.
        assert after["wall_ms"] is None
        assert isinstance(before["wall_ms"], int)
        assert after["capture_span_ms"] >= 0
    # A repetition the store has no capture for is refused by number, not skipped.
    with pytest.raises(SpecError) as refusal:
        from_store({**spec, "repetitions": 3}, telltale_home)
    assert "attempt 3 of T-recover: 0 captures" in str(refusal.value)


@pytest.mark.integration
def test_a_condition_resumes_the_attempts_the_store_already_holds(
    telltale_home: Path, tmp_path: Path
) -> None:
    """A restarted runner reads attempt 1 back and only launches attempt 2.

    A condition is N independent captures of one task, and a capture that already
    claims attempt k IS the kth of them. Re-running it spends a second session to
    produce a sixth capture and leaves the condition holding two captures of one
    attempt. What is asserted is that the store gains exactly one capture on the second
    call, that attempt 1 keeps its id, and that the resumed row carries `source`
    "store" and no wall time rather than a made-up one.
    """
    root = tmp_path / "repo"
    sha = _repository(root)
    spec = _spec(root, sha, repetitions=1, task_id="T-resume")

    first = repeat(spec, telltale_home)
    both = repeat({**spec, "repetitions": 2}, telltale_home)

    store = _store(telltale_home)
    assert len(store.captures()) == 2
    assert both["captures"][0] == first["captures"][0]
    assert both["captures"][1] != first["captures"][0]
    resumed, fresh = both["repetitions"]
    assert resumed["source"] == "store"
    assert resumed["wall_ms"] is None
    assert resumed["capture_span_ms"] >= 0
    assert resumed["acceptance"]["status"] == "pass"
    assert fresh["source"] == "run"
    assert isinstance(fresh["wall_ms"], int)
    # One repetition read back is enough to make the whole report a recovered one,
    # because that is when wall_ms starts being None for part of the table.
    assert both["recovered_from_store"] is True
    assert first["recovered_from_store"] is False


@pytest.mark.integration
def test_purge_removes_one_capture_and_leaves_the_rest(telltale_home: Path) -> None:
    """`telltale purge <id>` deletes one capture's rows and nothing else's."""
    for _ in range(2):
        assert (
            _telltale(
                "run", "--", sys.executable, "-c", "pass", home=telltale_home
            ).returncode
            == 0
        )
    store = _store(telltale_home)
    captures = sorted(str(row["capture_id"]) for row in store.captures())
    assert len(captures) == 2
    doomed, kept = captures
    before = len(store.observations(kept))

    done = _telltale("purge", doomed, home=telltale_home)

    assert done.returncode == 0, done.stderr
    assert doomed in done.stdout, done.stdout
    assert "observations" in done.stdout, done.stdout
    assert store.observations(doomed) == []
    assert len(store.observations(kept)) == before
    assert {str(row["capture_id"]) for row in store.captures()} == {kept}
    # An id that is not there is a refusal, not a silent zero.
    missing = _telltale("purge", "cap_nothing", home=telltale_home)
    assert missing.returncode == 2, missing.stdout


@pytest.mark.integration
def test_a_spec_that_is_not_a_condition_is_refused_before_anything_runs(
    telltale_home: Path, tmp_path: Path
) -> None:
    """A missing or misspelled key stops the experiment, and names the key.

    A spec that does not state its content level is a spec whose captures cannot be
    compared with anything, and a defaulted one would be two conditions reported as one.
    `levels` for `level` is the shape of the mistake: a key that is nearly right.
    """
    root = tmp_path / "repo"
    sha = _repository(root)
    broken = _spec(root, sha, repetitions=1)
    del broken["level"]
    broken["levels"] = 1
    (tmp_path / "spec.json").write_text(json.dumps(broken), encoding="utf-8")

    done = _telltale(
        "experiment", "repeat", str(tmp_path / "spec.json"), home=telltale_home
    )

    assert done.returncode == 2, done.stdout
    assert "missing ['level']" in done.stdout, done.stdout
    assert "unexpected ['levels']" in done.stdout, done.stdout
    # Refused before anything ran: not even the database was opened.
    assert not (telltale_home / "telltale.db").exists()


# -- the environment runner (W2-T3) ---------------------------------------------------

ENV_EXPERIMENT = "W2-T3-test"
ENV_TASK = "T-env"

# The seed bound both arms run under. Every fabricated number in the fake agent is
# linear in its seed, so at the default bound of 100 the DRAW moves the token counts
# further than --effort does and the sign of a between-arm shift would be a property of
# the draw. At 3 the arms' value ranges are disjoint by construction, which is what
# lets a test assert a direction rather than a number from one run. 8 rather than 3
# because the scaled MAD of five draws from three values is often 0, and a spread of 0
# makes MDD 0 and labels every nonzero shift material.
SEED_MAX = 8

# What the fake agent does per run beyond its Read calls: one Edit and one Bash, so one
# turn each. `_usage` is called once per turn, which is what makes the token total a
# function of the read count.
TURNS_BESIDE_READS = 2


def _arm_command(effort: str = "medium", model: str = "sonnet") -> list[str]:
    return [
        sys.executable, str(FAKE_AGENT), "-p", "make the answer 42",
        "--output-format", "stream-json", "--seed-max", str(SEED_MAX),
        "--model", model, "--effort", effort,
    ]  # fmt: skip


def _environment_spec(
    root: Path,
    sha: str,
    arms: list[dict[str, Any]],
    factor: str = "effort",
    repetitions: int = REPETITIONS,
    task_id: str = ENV_TASK,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "experiment": ENV_EXPERIMENT,
        "repo": str(root),
        "base_sha": sha,
        "acceptance": [sys.executable, "-c", ACCEPTANCE],
        "repetitions_per_arm": repetitions,
        "provider": "claude",
        "level": 1,
        "factor": factor,
        "arms": arms,
    }


def _effort_arms() -> list[dict[str, Any]]:
    return [
        {"name": "low", "command": _arm_command(effort="low")},
        {"name": "high", "command": _arm_command(effort="high")},
    ]


def _reads_range(effort: str) -> list[int]:
    """Every Read count the fake agent can produce in this arm, from its constants."""
    rank = fake_agent.EFFORTS.index(effort)
    return [fake_agent._reads(seed, rank) for seed in range(SEED_MAX)]


def _fresh_input_tokens_range(effort: str, model: str = "sonnet") -> list[int]:
    """Every fresh_input_tokens total this arm can produce, from the agent's formula.

    Computed rather than recorded: a number copied out of a run would make this test a
    record of that run, and the claim under test is that the RUNNER reports the
    direction the agent's constants put there.

    spec 13.7's fresh_input_tokens is the sum of the stream's per-request
    `input_tokens`, which is what this sums: `measures_spec13._TOKENS` maps the one to
    the other and `activities._request` copies the field across unrenamed.
    """
    rank = fake_agent.EFFORTS.index(effort)
    totals = []
    for seed in range(SEED_MAX):
        turns = fake_agent._reads(seed, rank) + TURNS_BESIDE_READS
        totals.append(
            sum(
                fake_agent._usage(seed, rank, model, turn)["input_tokens"]
                for turn in range(turns)
            )
        )
    return totals


def _by_enumeration(a: list[float], b: list[float]) -> tuple[float, float]:
    """The exact test again, by listing every arrangement. The check on stats.py.

    Written out here, independently of stats.py: mid-ranks from first principles, all
    `C(n_a + n_b, n_a)` splits listed with itertools, and the two-sided p as the share
    of them at least as far from the null centre as the observed one. If the
    convolution in stats.py counted a subset twice or missed one, these disagree.
    """
    pooled = [*a, *b]
    ranks = [
        1.0
        + sum(1 for other in pooled if other < value)
        + (sum(1 for other in pooled if other == value) - 1) / 2.0
        for value in pooled
    ]
    n_a, n_b = len(a), len(b)
    observed = sum(ranks[:n_a])
    centre = (n_a * (n_a + 1) / 2.0) + (n_a * n_b / 2.0)
    splits = list(itertools.combinations(range(n_a + n_b), n_a))
    extreme = [
        split
        for split in splits
        if abs(sum(ranks[index] for index in split) - centre) >= abs(observed - centre)
    ]
    return observed - n_a * (n_a + 1) / 2.0, len(extreme) / len(splits)


@pytest.mark.integration
def test_two_arms_differing_only_in_effort_are_compared_between_arms(
    telltale_home: Path, tmp_path: Path
) -> None:
    """The H3 pilot of design 6.12: one factor, two arms, five repetitions each.

    Both assertions the runner exists to make are exercised: the arms' commands differ
    in exactly the declared flag's value, and the two fingerprint payloads differ in
    exactly the declared field. What is asserted about the numbers is their DIRECTION,
    computed here from the fake agent's own constants, because a number copied from a
    run would be a test of that run.
    """
    root = tmp_path / "repo"
    sha = _repository(root)
    # The fake agent's constants, before anything runs. Read counts: the two arms'
    # ranges touch at one value, so every high value is at least every low value and
    # the shift cannot be negative. fresh_input_tokens: the ranges are disjoint, so
    # it is strictly positive whatever the five seeds turn out to be.
    assert min(_reads_range("high")) >= max(_reads_range("low"))
    assert min(_fresh_input_tokens_range("high")) > max(
        _fresh_input_tokens_range("low")
    )

    report = environment(
        _environment_spec(root, sha, _effort_arms()), telltale_home, tmp_path / "out"
    )

    # The written artefact is what a write-up cites, so it is checked as BYTES: exactly
    # one trailing newline, so a committed environment.json and a regenerated one are
    # the same file rather than two the end-of-file-fixer hook keeps rewriting.
    raw = (tmp_path / "out" / ENV_TASK / "environment.json").read_text(encoding="utf-8")
    assert raw[-2:] == "}\n", raw[-40:]
    assert json.loads(raw) == report
    assertion = report["fingerprint_assertion"]
    assert assertion["differing_fields"] == ["effort"]
    assert assertion["values"]["effort"] == {"low": "low", "high": "high"}
    ids = assertion["fingerprint_ids"]
    assert set(ids) == {"low", "high"}
    assert ids["low"] != ids["high"]
    assert all(one.startswith("env_") for one in ids.values())
    # Five captures per arm, each arm's own task id, and the acceptance the harness ran.
    for arm in report["arms"]:
        assert len(arm["report"]["captures"]) == REPETITIONS
        assert arm["report"]["task_id"] == f"{ENV_TASK}-{arm['name']}"
        assert arm["report"]["acceptance"] == {"pass": REPETITIONS, "fail": 0}
    between = report["between"]
    assert between["tool_calls.Read"]["hl_shift"] >= 0
    assert between["tool_calls.Read"]["cliffs_delta"] >= 0
    tokens = between["context_token_burden.fresh_input_tokens"]
    assert tokens["hl_shift"] > 0
    assert tokens["cliffs_delta"] > 0
    for metric in (*MEASURE_METRICS, *STREAM_METRICS, *TOOL_METRICS):
        row = between[metric]
        assert row["claim_class"] == "comparative", metric
        assert row["n_a"] == row["n_b"] == REPETITIONS, metric
        assert row["mdd"] is not None, metric
        assert row["s_a"] is not None, metric
        assert row["s_b"] is not None, metric
        assert 0.0 < row["p"] <= 1.0, metric
        assert row["label"] in {MATERIAL, UNRESOLVED}, metric
        assert isinstance(row["demoted"], bool), metric
        # N_needed is None only where the pooled median is 0, and then the row says so.
        assert (row["n_needed"] is not None) == (row["median"] != 0), metric
    # The printed report carries the constants, the assertion and the vocabulary.
    printed = report_module.environment(report)
    assert "pilot_repetitions_per_arm=5" in printed
    assert "exact_test_max_n_per_arm=20" in printed
    assert "differing fields ['effort']" in printed
    assert UNRESOLVED in printed
    for forbidden in ("effect of", "impact", "cause", "no effect"):
        assert forbidden not in printed.replace(MATERIAL, ""), forbidden


@pytest.mark.integration
def test_arms_that_differ_in_two_flags_are_refused_before_any_capture(
    telltale_home: Path, tmp_path: Path
) -> None:
    """Two differences are two experiments, and the refusal names both tokens.

    Before anything runs, because the alternative is finding out after ten captures
    that the comparison was of effort and model at once.
    """
    root = tmp_path / "repo"
    sha = _repository(root)
    arms = [
        {"name": "a", "command": _arm_command(effort="low", model="haiku")},
        {"name": "b", "command": _arm_command(effort="high", model="opus")},
    ]
    spec = _environment_spec(root, sha, arms, factor="model", repetitions=1)
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")

    done = _telltale(
        "experiment", "environment", str(tmp_path / "spec.json"), home=telltale_home
    )

    assert done.returncode == 2, done.stdout
    assert "differ at 2 token(s)" in done.stdout, done.stdout
    for token in ("'haiku'", "'opus'", "'low'", "'high'"):
        assert token in done.stdout, done.stdout
    # Nothing ran: the database the captures would be written into does not exist.
    assert not (telltale_home / "telltale.db").exists()


@pytest.mark.integration
def test_arms_that_differ_in_level_are_refused_after_the_runs(
    telltale_home: Path, tmp_path: Path
) -> None:
    """A level difference is invisible in the argv and shows up in the fingerprint.

    This is why the second assertion exists. The commands differ in exactly the
    declared factor, so the pre-run check passes and both arms run; the payloads then
    differ in content_level as well as effort, and the refusal names it.
    """
    root = tmp_path / "repo"
    sha = _repository(root)
    arms = [
        {"name": "low", "command": _arm_command(effort="low"), "level": 1},
        {"name": "high", "command": _arm_command(effort="high"), "level": 0},
    ]
    spec = _environment_spec(root, sha, arms, factor="effort", repetitions=1)

    with pytest.raises(FingerprintMismatch) as refusal:
        environment(spec, telltale_home)

    message = str(refusal.value)
    assert "content_level" in message, message
    assert "effort" in message, message
    # Refused AFTER the runs: both captures exist and neither is thrown away.
    assert len(_store(telltale_home).captures()) == 2


@pytest.mark.integration
def test_the_exact_mann_whitney_reproduces_a_hand_computed_p() -> None:
    """Two 3-element lists, and a p a person can count on paper.

    a = [1, 2, 3] and b = [4, 5, 6] are completely separated, so R_a = 1 + 2 + 3 = 6,
    U_a = 6 - 3 (3 + 1) / 2 = 0, and the null centre is n_a n_b / 2 = 4.5. There are
    C(6, 3) = 20 ways to split the six mid-ranks between the arms. Exactly two of them
    are at least 4.5 from the centre: the one where a takes the three smallest ranks
    (U = 0) and the one where it takes the three largest (U = 9). p = 2 / 20 = 0.1.

    The tied cases below are checked against the literal enumeration in this file,
    which is the thing stats.py's convolution is a faster spelling of.
    """
    assert mann_whitney_exact([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == (0.0, 0.1)
    tied = [
        ([1.0, 2.0, 2.0], [2.0, 3.0, 4.0]),
        ([5.0, 5.0, 5.0], [5.0, 5.0, 5.0]),
        ([1.0, 4.0], [2.0, 2.0, 3.0, 9.0]),
    ]
    for a, b in [([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]), *tied]:
        assert mann_whitney_exact(a, b) == _by_enumeration(a, b), (a, b)
    # Above the protocol's own ceiling the answer is refused, never approximated.
    with pytest.raises(TooManyValues) as refusal:
        mann_whitney_exact([0.0] * 21, [1.0] * 5)
    assert "exact test not computed above n = 20 per arm" in str(refusal.value)
