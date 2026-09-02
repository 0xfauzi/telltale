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
from typing import Any

import pytest

from telltale.experiments import FingerprintMismatch, one_fingerprint, repeat, vector
from telltale.stats import TooManyValues, mann_whitney_exact
from telltale.store import Store

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

# What the fake agent's stream carries, so the placeholder vector has these columns.
USAGE_METRICS = ("input_tokens", "output_tokens", "num_turns", "duration_ms")
TOOL_METRICS = ("tool_calls", "tool_calls.Bash", "tool_calls.Edit", "tool_calls.Read")


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
    # Robust statistics per metric, never a mean alone.
    for metric in (*USAGE_METRICS, *TOOL_METRICS):
        found = report["stats"][metric]
        assert found["n"] == REPETITIONS, metric
        assert found["unknown"] == 0, metric
        assert found["median"] is not None, metric
        assert found["mad_scaled"] is not None, metric
        assert len(found["values"]) == REPETITIONS, metric
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
    """Unknown stays None. A child that is not an agent reports nothing, not zero.

    This is design invariant 5 at the one place the repeat runner could break it: a
    vector of zeros would enter the statistics as five real observations of nothing
    happening, and the median of that is a number nobody measured.
    """
    done = _telltale("run", "--", sys.executable, "-c", "pass", home=telltale_home)
    assert done.returncode == 0, done.stderr

    store = _store(telltale_home)
    capture_id = str(store.captures()[0]["capture_id"])
    found = vector(store, capture_id)

    assert found, "the vector has metrics"
    assert set(found.values()) == {None}, found


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


# -- the between-arm statistics (W2-T3) ------------------------------------


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
