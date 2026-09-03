"""The per-session stop bound and the repetition-major order, against a real agent.

Nothing here is stubbed. Every test makes a real git repository with real commits and
runs the real intervention runner, which shells out to the real `telltale run` console
script wrapping `tests/integration/fake_agent.py` as a real child process. The bound is
compared against evidence rows a real reduction wrote, not against a number this file
supplies.

Both mechanisms are W4-E09's first lesson. That experiment's brief carried a
200,000-token stop bound and the runner could not act on it: the pilot ran one probe
three times, the suite ran every remaining repetition in one invocation, and four
sessions crossed the bound before anybody could see one. Two things had to change
together. A bound has to be compared after every session and be able to end the run, and
the sessions have to be ordered so that the first round is one session of every probe of
every arm rather than every session of one arm.

Its own file rather than more of test_probe.py: that file is at 670 lines against an
800-line ratchet that is a gate rather than a preference, and it holds the SCORING
tests, which is a different subject from the run's bound.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from experiment_helpers import (
    STRAY,
    beta_commit,
    intervention_spec,
    probe_repository,
    probes,
    reader,
    run_telltale,
)

from telltale import report_probe
from telltale.experiments import SpecError
from telltale.experiments_env import intervention

if TYPE_CHECKING:
    from pathlib import Path

    from telltale.store import Store

EXPERIMENT = "W4-E10-test"

# What the scripted agent spends on one probe session of this fixture, from its own
# constants rather than from a recorded run: one Read turn, `_usage(seed, 1, "sonnet",
# 0)` = input 156 + 13 seed, output 50, cache_read 0, cache_creation 300. So the total
# `telltale show` sums is 506 + 13 seed, and 506 is its floor at any seed. A bound of 1
# is under the floor, which is what makes the first session's crossing deterministic.
FLOOR_TOKENS = 506

# A bound in seconds that no session of this fixture reaches, so the token bound is the
# only one under test. W4-T5 measured six of these sessions end to end at 2.30 s.
LOOSE_SECONDS = 3600


def _spec(root: Path, before: str, after: str, **changed: Any) -> dict[str, Any]:
    return intervention_spec(root, before, after, EXPERIMENT, 3, **changed)


def _probes() -> list[dict[str, Any]]:
    """Two probes whose answer keys both exist at BOTH commits of this fixture.

    `experiment_helpers.probes()` has one key naming src/beta.py, which the first commit
    does not carry, and a key naming a path its commit lacks is refused before any
    session runs. That refusal is test_probe.py's subject; what is under test here is
    the ORDER two probes run in, so both keys have to be answerable on both arms.
    """
    return [
        probes()[1],
        {
            "probe_id": "P-stray",
            "prompt": "identify the module nothing calls",
            "answer_key": {"paths": [STRAY], "symbols": []},
        },
    ]


def _started(store: Store) -> list[tuple[str, int]]:
    """(task_id, attempt) of every session of this experiment, in the order they ran.

    Sorted by observation_id, which design 6.2 fixes as arrival order: a bare ULID whose
    48-bit millisecond prefix and in-millisecond counter make its sort order its
    generation order. `observations_of_type` orders by capture first, so the sort here
    is what turns a set of captures back into a sequence of sessions.
    """
    rows = [
        row
        for row in store.observations_of_type("telltale.capture_started")
        if row["payload"].get("experiment") == EXPERIMENT
    ]
    return [
        (str(row["payload"]["task_id"]), int(row["payload"]["attempt"]))
        for row in sorted(rows, key=lambda row: str(row["observation_id"]))
    ]


@pytest.mark.integration
def test_a_bound_under_the_first_session_ends_the_run_and_the_report_names_it(
    telltale_home: Path, tmp_path: Path
) -> None:
    """Two probes, two arms, three repetitions: 12 sessions planned, 1 run.

    The bound is 1 token and the cheapest session this fixture can produce spends 506,
    so the first session crosses it whatever seed it drew. What the run then owes a
    reader is four things, and each is asserted below: it stopped, it stopped THERE, no
    thirteenth-of-a-run capture reached the store, and the report says which session and
    which bound rather than presenting eleven missing sessions as a smaller experiment.
    """
    root = tmp_path / "repo"
    before = probe_repository(root)
    after = beta_commit(root)
    spec = _spec(
        root,
        before,
        after,
        probes=_probes(),
        stop={
            "max_tokens_per_session": 1,
            "max_seconds_per_session": LOOSE_SECONDS,
            "basis": "one token, which is under the floor of this fixture",
        },
    )

    report = intervention(spec, telltale_home)

    stop = report["stop"]
    assert stop["stopped"] is True, stop
    assert stop["sessions_planned"] == 12, stop
    assert stop["sessions_run"] == 1, stop
    crossed = stop["crossed"]
    assert crossed["bound"] == "max_tokens_per_session", crossed
    assert crossed["limit"] == 1, crossed
    assert crossed["observed"] >= FLOOR_TOKENS, crossed
    assert crossed["attempt"] == 1, crossed
    assert crossed["task_id"] == f"{spec['task_id']}-before-P-narrow", crossed
    # Nothing after it was started: one capture of this experiment exists, and it is the
    # one the bound named.
    ran = _started(reader(telltale_home))
    assert ran == [(f"{spec['task_id']}-before-P-narrow", 1)], ran
    assert report["arms"][1]["report"]["captures"] == []
    # A run stopped before an arm had a capture has not FAILED the between-arm
    # assertion, it has not made one, and the report may not read as the first.
    assert report["fingerprint_assertion"] is None
    warnings = report["warnings"]
    assert any("NO between-arm" in one for one in warnings), warnings
    printed = report_probe.intervention(report)
    assert "ENDED by" in printed, printed
    assert crossed["capture_id"] in printed, printed
    assert "max_tokens_per_session = 1" in printed, printed
    assert "1 of 12 sessions ran" in printed, printed


@pytest.mark.integration
def test_the_sessions_run_repetition_major_across_both_arms_and_both_probes(
    telltale_home: Path, tmp_path: Path
) -> None:
    """Round 1 is one session of every probe of every arm, then round 2. W4-E10's DO 1.

    The order is written out here rather than derived, because deriving it from the same
    comprehension the runner uses would assert that the code equals itself. Inside a
    round the probe is outer and the arm inner, so the two arms' sessions for one probe
    are adjacent in time and the pairing, which is by probe, spans the smallest gap
    available.
    """
    root = tmp_path / "repo"
    before = probe_repository(root)
    after = beta_commit(root)
    spec = _spec(root, before, after, probes=_probes(), repetitions_per_arm=2)
    task = spec["task_id"]

    intervention(spec, telltale_home)

    assert _started(reader(telltale_home)) == [
        (f"{task}-before-P-narrow", 1),
        (f"{task}-after-P-narrow", 1),
        (f"{task}-before-P-stray", 1),
        (f"{task}-after-P-stray", 1),
        (f"{task}-before-P-narrow", 2),
        (f"{task}-after-P-narrow", 2),
        (f"{task}-before-P-stray", 2),
        (f"{task}-after-P-stray", 2),
    ]


@pytest.mark.integration
def test_a_spec_with_no_stop_block_runs_unbounded_and_the_report_says_so(
    telltale_home: Path, tmp_path: Path
) -> None:
    """The default is what every suite before W4-E10 did, and it is now stated.

    An unbounded run and a bounded one that nothing crossed are different facts, and a
    report that printed a bound line only when there was a bound would leave a reader
    who does not see one guessing which they hold.
    """
    root = tmp_path / "repo"
    before = probe_repository(root)
    after = beta_commit(root)
    spec = _spec(root, before, after, repetitions_per_arm=1)

    report = intervention(spec, telltale_home)

    assert report["stop"] == {
        "bounds": None,
        "stopped": False,
        "sessions_planned": 2,
        "sessions_run": 2,
        "crossed": None,
        "token_total_unknown": [],
    }
    assert report["fingerprint_assertion"] is not None
    printed = report_probe.intervention(report)
    assert "no bound in the spec, so the suite ran unbounded" in printed, printed
    assert "2 of 2 sessions ran" in printed, printed


@pytest.mark.integration
def test_a_stop_block_that_is_not_two_positive_whole_numbers_is_refused(
    telltale_home: Path, tmp_path: Path
) -> None:
    """Refused before any session, with the key named. No capture, no database.

    A bound of 0 would end the run at its first session whatever that session cost, and
    a fractional one is not a count of tokens or of seconds. Both are typos, and a typo
    caught after twenty sessions is caught after the tokens are gone.
    """
    root = tmp_path / "repo"
    before = probe_repository(root)
    after = beta_commit(root)
    broken = {
        "max_tokens_per_session": 0,
        "max_seconds_per_session": 1.5,
        "max_tokens_per_session_typo": 1,
    }
    for name, value in broken.items():
        stop = {
            "max_tokens_per_session": 10,
            "max_seconds_per_session": LOOSE_SECONDS,
            name: value,
        }
        with pytest.raises(SpecError) as refusal:
            intervention(_spec(root, before, after, stop=stop), telltale_home)
        assert name.replace("_typo", "") in str(refusal.value), str(refusal.value)
    with pytest.raises(SpecError) as refusal:
        intervention(_spec(root, before, after, stop=1300000), telltale_home)
    assert "stop is an object" in str(refusal.value), str(refusal.value)
    assert not (telltale_home / "telltale.db").exists()


@pytest.mark.integration
def test_the_cli_prints_the_bound_and_the_session_that_crossed_it(
    telltale_home: Path, tmp_path: Path
) -> None:
    """End to end through the console script, which is what an experiment runs.

    The written intervention.json is what a write-up's numbers are checked against, so
    the stop record has to be in the file and not only on the terminal.
    """
    root = tmp_path / "repo"
    before = probe_repository(root)
    after = beta_commit(root)
    spec = _spec(
        root,
        before,
        after,
        task_id="T-cli-stop",
        stop={
            "max_tokens_per_session": 1,
            "max_seconds_per_session": LOOSE_SECONDS,
            "basis": "one token, under this fixture's floor",
        },
    )
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")

    done = run_telltale(
        "experiment",
        "intervention",
        str(tmp_path / "spec.json"),
        "--out",
        str(tmp_path / "out"),
        home=telltale_home,
    )

    assert done.returncode == 0, done.stderr
    assert "ENDED by" in done.stdout, done.stdout
    assert "1 of 6 sessions ran" in done.stdout, done.stdout
    written = json.loads(
        (tmp_path / "out" / "T-cli-stop" / "intervention.json").read_text(
            encoding="utf-8"
        )
    )
    assert written["stop"]["stopped"] is True
    assert written["stop"]["bounds"]["max_tokens_per_session"] == 1
    assert written["stop"]["crossed"]["observed"] >= FLOOR_TOKENS
