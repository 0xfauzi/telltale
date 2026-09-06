"""The one-step candidate protocol end to end. Design 6.12's H8, spec 15.8.

Four things are under test and they are four different questions.

  What is KNOWN about a candidate before it is merged. `candidate.features` runs git
  against a real repository and reads the A block off `git diff base...head`: six
  numbers, none of them from a file's contents. The traps are in the test because they
  are what the function is for: three dots and not two, so a candidate is not charged
  with main's drift; a merge head refused by name, because its own changes are not one
  diff; a binary file leaving the line counts None and never 0.

  What is NOT known about it. The three post-merge columns of the change clock come
  from outcomes an orchestrator posted, and every one of them has a case where the
  honest answer is None: nobody posted a verification, nobody stated a duration, and
  the delayed label of a change with fewer than three later changes.

  Whether a forecaster can READ the candidate. The conditioned run hangs the A block on
  the window as a past-future covariate, and until something reads it the two runs of
  the protocol are the same run. `FutureEcho` below is the reader, so the protocol is
  exercised without the model stack; the baselines are the control, and the run says
  NO_FUTURE_READER when they are all there is.

  Everything above runs against the real system: real `telltale run` captures, the real
  store, the real compiler and the real backtester. The only thing written for the test
  is a forecaster, which is what a Forecaster is: a signature, not a stub of anything.
"""

from __future__ import annotations

import random
import statistics
import time
from typing import TYPE_CHECKING, Any

import pytest
from synthetic_series import write_change
from test_series_lineage import (
    _built,
    _column,
    _git,
    _repository,
    _run,
    _spec,
)

from telltale import repo
from telltale.forecast import (
    ABLATION_A,
    ABLATION_C,
    BASELINE_NAMES,
    CANDIDATE_FORBIDDEN,
    CANDIDATE_SENTENCE,
    CANDIDATE_TARGETS,
    QUANTILE_LEVELS,
    REWORK_TAIL,
    EchoStub,
    Window,
    make,
)
from telltale.forecast import candidate as protocol
from telltale.forecast.backtest import Refused
from telltale.forecast.frame import retained as frame_of
from telltale.model import ColumnSpec, ForecastResult, RowMeta, Series
from telltale.series_lineage import UNCAPTURED
from telltale.series_outcomes import (
    NO_DURATION,
    NO_TAIL,
    NO_VERIFICATION,
    POST_MERGE_COLUMNS,
)

if TYPE_CHECKING:
    from pathlib import Path

    from telltale.store import Store

pytestmark = pytest.mark.integration


# -- (a) the A block of a candidate, off a real git diff -------------------------------

# What the candidate commit writes, and the line counts it writes them with. The
# expected numstat is arithmetic on THESE, computed in the assertions rather than
# copied from a run: a fixture that stated its own answer would pass against any diff.
_CANDIDATE_FILES = {
    "pkg/a.py": 7,
    "tests/test_x.py": 4,
    "pyproject.toml": 2,
}
# One file whose numstat is "-\t-". Under pkg/ on purpose, so the subsystem count of
# the diff is the same with and without it and the binary file changes exactly one
# thing: whether lines are a unit at all.
_BINARY = "pkg/blob.bin"


def _write(root: Path, name: str, lines: int) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"line {index}\n" for index in range(lines)), "utf-8")


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def test_the_a_block_is_read_off_the_diff_and_nothing_else(tmp_path: Path) -> None:
    """The six numbers of block A, each computable by hand from what the test wrote.

    The line counts are asserted against the sum of the files this test created, so the
    assertion is arithmetic on the fixture and not a number copied out of an earlier
    run. The three path columns are the same rule the change clock uses on a landed
    commit (series_paths), which is what makes the A block of a candidate comparable
    with the row it becomes.
    """
    root = _repository(tmp_path / "repo")
    base = _git(root, "rev-parse", "HEAD")
    for name, lines in _CANDIDATE_FILES.items():
        _write(root, name, lines)
    text = _commit(root, "candidate")

    found = protocol.features(root, base, text)

    assert found["files_changed"] == len(_CANDIDATE_FILES)
    assert found["lines_added"] == sum(_CANDIDATE_FILES.values())
    # Every file is new, so nothing was deleted. 0 here is a measurement and not a
    # stand-in for an unknown, which is the distinction the binary case below draws.
    assert found["lines_removed"] == 0
    # tests, pkg and the repository root, which `.` names.
    assert found["subsystems_touched"] == 3
    assert found["test_files_changed"] == 1
    assert found["dependency_delta"] == 1
    assert found["base_sha"] == base
    assert found["head_sha"] == text
    assert found["merge_base"] == base
    assert set(found) == {*ABLATION_A, *protocol.PROVENANCE}


def test_a_binary_file_leaves_the_line_counts_unknown_and_counts_the_file(
    tmp_path: Path,
) -> None:
    """git prints "-\\t-" for a binary file: not zero lines, but lines are not the unit.

    files_changed still counts it, because a file changed. Summing the text files and
    saying so nowhere is the answer this refuses, and it is `repo.totals`' rule reached
    through the extractor rather than a second copy of it.
    """
    root = _repository(tmp_path / "repo")
    base = _git(root, "rev-parse", "HEAD")
    for name, lines in _CANDIDATE_FILES.items():
        _write(root, name, lines)
    _commit(root, "candidate")
    (root / _BINARY).write_bytes(bytes(range(256)) * 4)
    head = _commit(root, "binary")

    found = protocol.features(root, base, head)

    assert found["files_changed"] == len(_CANDIDATE_FILES) + 1
    assert found["lines_added"] is None
    assert found["lines_removed"] is None
    # The path columns are still answerable: they are questions about paths, and a
    # binary file has one. Unknown spreads exactly as far as the reason for it does.
    assert found["subsystems_touched"] == 3
    assert found["test_files_changed"] == 1
    assert found["dependency_delta"] == 1


def test_the_diff_is_symmetric_so_the_base_branchs_own_commits_are_not_charged(
    tmp_path: Path,
) -> None:
    """`base...head` and not `base..head`. The trap, made into a measurement.

    main gains a commit of its own after the candidate branches. Two dots would report
    that commit as part of the candidate, which is a forecast conditioned on somebody
    else's work; three dots diff from the merge base, which is the change the candidate
    would bring. The assertion is that the two spellings DIFFER and that the extractor
    took the second, so it fails if the dots are ever changed.
    """
    root = _repository(tmp_path / "repo")
    base_start = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "-b", "candidate")
    for name, lines in _CANDIDATE_FILES.items():
        _write(root, name, lines)
    head = _commit(root, "candidate")
    _git(root, "checkout", "-q", "-")
    _write(root, "drift/other.py", 30)
    drifted = _commit(root, "someone else")

    found = protocol.features(root, drifted, head)

    assert found["merge_base"] == base_start
    assert found["files_changed"] == len(_CANDIDATE_FILES)
    # The other spelling, run here so the difference is measured rather than asserted:
    # two dots would have counted the deletion of drift/other.py as the candidate's.
    two_dots = _git(root, "diff", "--numstat", f"{drifted}..{head}").splitlines()
    assert len(two_dots) == len(_CANDIDATE_FILES) + 1
    assert any("drift/other.py" in line for line in two_dots)


def test_a_merge_head_is_refused_by_name(tmp_path: Path) -> None:
    """A merge commit's own changes are not one diff, so it is not a candidate.

    The same refusal repo_link._commit_stats makes on the change clock, and it has to
    be the same one: if the A block of a candidate were measured against one parent and
    the row it becomes were measured as unknown, the protocol would be conditioning on
    a number the history it is scored against does not contain.
    """
    root = _repository(tmp_path / "repo")
    base = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "-b", "side")
    _write(root, "pkg/a.py", 7)
    _commit(root, "side")
    _git(root, "checkout", "-q", "-")
    _write(root, "other.py", 3)
    _commit(root, "main moves")
    _git(root, "merge", "--no-ff", "-q", "-m", "merge", "side")
    merged = _git(root, "rev-parse", "HEAD")

    with pytest.raises(protocol.NotACandidate) as refused:
        protocol.features(root, base, merged)

    assert "is a merge of 2 parents" in str(refused.value)
    assert merged[:12] in str(refused.value)


def test_a_ref_that_does_not_resolve_is_refused_rather_than_diffed(
    tmp_path: Path,
) -> None:
    """An unknown ref names itself. `git diff` would have answered on a path instead."""
    root = _repository(tmp_path / "repo")
    with pytest.raises(protocol.NotACandidate) as refused:
        protocol.features(root, "HEAD", "no-such-ref")
    assert "no-such-ref: no such commit" in str(refused.value)


# -- (b) the three post-merge columns, from real captures and real outcomes -----------

# Six attempts over two task ids, each committing once. One second apart, because git
# records a committer date in WHOLE SECONDS and the change clock sorts on it: two
# commits inside one second are ordered by their sha, and a test whose row order
# depended on how fast the machine ran would be a test of the machine.
_ATTEMPTS = (
    ("T-alpha", 1),
    ("T-alpha", 2),
    ("T-beta", 1),
    ("T-beta", 2),
    ("T-beta", 3),
    ("T-gamma", 1),
)
_SECOND = 1.05

# What each attempt's child does: append a line and commit it. Every attempt makes
# exactly one commit, so attempt i lands change i.
_COMMIT = "printf 'x\\n' >> f && git add -A && git commit -q -m change"


def _land(root: Path, task_id: str, attempt: int) -> str:
    """One `telltale run` whose child commits, and the sha it produced."""
    done = _run(
        root, "run", "--provider", "claude",
        "--task-id", task_id, "--attempt", str(attempt),
        "--", "bash", "-c", _COMMIT,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr.decode()
    return _git(root, "rev-parse", "HEAD")


def _outcome(root: Path, *args: str) -> None:
    done = _run(root, "outcome", *args)
    assert done.returncode == 0, done.stdout.decode() + done.stderr.decode()


@pytest.mark.usefixtures("telltale_home")
def test_the_three_post_merge_columns_come_from_the_outcomes_of_the_landing_attempt(
    tmp_path: Path,
) -> None:
    """merge_verification_ms, merge_verification_failed and rework_within_3, on six
    real captures with real outcomes posted through `telltale outcome`.

    Every branch of every rule is on this one lineage, which is why it is one test:
    a duration stated and a duration not stated, a pass and a fail and no outcome at
    all, a rework inside the window, a rework recorded after the window closed, three
    changes with three later ones and no rework, and three changes the delayed label
    cannot decide yet.

    The rework recorded LATE is the assertion that costs the most to arrange and is
    worth it: the second `revert_or_repair` names a real attempt of a real change and
    the answer is still 0, because it was recorded after the third following change had
    landed. Without it the temporal half of the rule would never run.
    """
    root = _repository(tmp_path / "repo")
    shas = []
    for index, (task_id, attempt) in enumerate(_ATTEMPTS):
        if index:
            time.sleep(_SECOND)
        shas.append(_land(root, task_id, attempt))
        if index == 2:
            # Posted while three changes exist, so it is at or before the third
            # following change of row 0, which has not been made yet.
            _outcome(
                root, "--kind", "revert_or_repair", "--status", "reverted",
                "--task-id", "T-alpha", "--attempt", "1",
            )  # fmt: skip
    # Posted after every change, so it is AFTER row 1's third following change.
    _outcome(
        root, "--kind", "revert_or_repair", "--status", "reverted",
        "--task-id", "T-alpha", "--attempt", "2",
    )  # fmt: skip
    _outcome(
        root, "--kind", "mechanical_verification", "--status", "pass",
        "--task-id", "T-alpha", "--attempt", "1", "--duration-ms", "1234",
    )  # fmt: skip
    _outcome(
        root, "--kind", "mechanical_verification", "--status", "fail",
        "--task-id", "T-alpha", "--attempt", "2", "--duration-ms", "4321",
    )  # fmt: skip
    _outcome(
        root, "--kind", "mechanical_verification", "--status", "pass",
        "--task-id", "T-beta", "--attempt", "1",
    )  # fmt: skip
    repo_id = _repo_id(root)

    built = _built(repo_id, clock="change")

    assert [meta.row_key for meta in built.row_meta] == shas
    # Row 0 stated a duration, row 1 stated one, row 2 posted an outcome without one,
    # and rows 3 to 5 posted no verification at all. Three different reasons for a
    # cell to be unknown and one of them is a number.
    stated = [1234, 4321, None, None, None, None]
    assert _column(built, "merge_verification_ms") == stated
    # 0 is a pass, 1 is a fail, None is nobody said. "Nobody said" is not a pass.
    assert _column(built, "merge_verification_failed") == [0, 1, 0, None, None, None]
    # Row 0: reverted, and the revert was recorded before row 3 landed.
    # Row 1: reverted, and the revert was recorded after row 4 landed, so it is outside
    #        the window design 6.12 asks about and the answer is 0, not 1.
    # Row 2: three later changes and no revert naming it.
    # Rows 3 to 5: fewer than three later changes, so the label is not decidable.
    assert _column(built, "rework_within_3") == [1, 0, 0, None, None, None]
    for name in POST_MERGE_COLUMNS:
        assert _spec(built, name).coverage == "partial", name
    # Two different reasons for one column's unknown cells, and both are in the map:
    # row 2 posted a verification and stated no duration, rows 3 to 5 posted none at
    # all, and telling a reader only the second would send them to add --duration-ms to
    # an outcome that does not exist.
    unknown = built.cohort["unknown_columns"]
    assert NO_DURATION in unknown["merge_verification_ms"]
    assert NO_VERIFICATION in unknown["merge_verification_ms"]
    assert unknown["merge_verification_failed"] == NO_VERIFICATION
    assert unknown["rework_within_3"] == NO_TAIL


def _repo_id(root: Path) -> str:
    found = repo.identity(root)["repo_id"]
    assert isinstance(found, str)
    return found


# -- (c) and (d) a reader of the covariate, and the baselines that do not read one ----

_ECHO_SPREAD = 2.0
_READS = ABLATION_A[0]


class FutureEcho:
    """Persistence plus the candidate's own `files_changed`. A reader, not a stub.

    It is here rather than in `telltale.forecast` on purpose. What the protocol needs
    to be tested end to end is A forecaster that reads `Window.future`, and the one in
    src/ that does is the TimesFM adapter, which needs an extra CI does not install.
    So the reader lives in the test and is handed to `conditioned` like any other: it
    stands in for nothing, and nothing in src/ changes shape to accommodate it.

    Without a future block it is exactly persistence, which is what makes the pair
    meaningful: the unconditioned run and the conditioned run differ in the covariate
    and in nothing else.
    """

    name = "future_echo"

    def forecast(self, window: Window, horizon: int) -> ForecastResult:
        last = window.column(window.target)[-1]
        block = (window.future or {}).get(_READS)
        point = [last + (0.0 if block is None else block[-1])] * horizon
        return ForecastResult(
            forecaster=self.name,
            horizon=horizon,
            point=[point],
            quantile_levels=list(QUANTILE_LEVELS),
            targets=[window.target],
            covariates=window.covariates,
            missingness_policy="exclude",
            quantiles=[
                [
                    [value + (level - 0.5) * _ECHO_SPREAD for level in QUANTILE_LEVELS]
                    for value in point
                ]
            ],
        )


_TARGET = "merge_verification_ms"


def _point(record: dict[str, Any]) -> float:
    return float(record["forecasts"][FutureEcho.name]["point"][0])


def _mae(found: dict[str, Any], run: str, model: str) -> float | None:
    scored = found["runs"][run]["metrics"]["forecasters"][model]
    value = scored["mae_mean"]
    return None if value is None else float(value)


def test_a_forecaster_that_reads_the_future_block_scores_differently(
    store: Store,
) -> None:
    """The protocol's whole claim: the conditioned run knows one thing more.

    Break it by making `candidate._attach` return the window unchanged and this fails:
    the two runs score identically, the paired difference is 0 and NO_FUTURE_READER
    appears, which is the run correctly reporting that nothing read the covariate.
    """
    built = write_change(store, rows=60, seed=3)
    forecasters: dict[str, Any] = {FutureEcho.name: FutureEcho()}

    found = protocol.conditioned(None, built, _TARGET, forecasters, FutureEcho.name)

    plain = _mae(found, protocol.UNCONDITIONED, FutureEcho.name)
    fitted = _mae(found, protocol.CONDITIONED, FutureEcho.name)
    assert plain is not None
    assert fitted is not None
    assert plain != fitted
    assert found["paired"]["n_paired"] > 0
    # Window by window, and by exactly the number the reader read: the conditioned
    # point is the unconditioned point plus the candidate row's own files_changed.
    # The MEDIAN of the paired MAE differences is not asserted, because each of them is
    # plus or minus that number and a median over an even count of them can be 0 while
    # every single window moved.
    index = [column.name for column in built.columns].index(_READS)
    for before, after in zip(
        found["runs"][protocol.UNCONDITIONED]["windows"],
        found["runs"][protocol.CONDITIONED]["windows"],
        strict=True,
    ):
        moved = _point(after) - _point(before)
        assert moved == built.rows[int(before["origin"])][index]
    assert found["warnings"] == []
    assert protocol.NO_FUTURE_READER not in found["warnings"]
    for run in (protocol.UNCONDITIONED, protocol.CONDITIONED):
        assert CANDIDATE_SENTENCE in found["runs"][run]["assumptions"]
    printed = protocol.report(found)
    assert CANDIDATE_SENTENCE in printed
    # No checkpoint was involved, and the report says that rather than saying nothing.
    assert protocol.NO_LICENCE in printed


def test_the_baselines_read_no_covariate_and_the_run_says_so(store: Store) -> None:
    """The control. Four baselines, two identical runs, and the warning that says why.

    0 here is a fact about the forecasters and the run refuses to let it read as a fact
    about the candidate. That refusal is the reason the warning exists.
    """
    built = write_change(store, rows=60, seed=3)
    model = BASELINE_NAMES[0]
    forecasters = {name: make(name) for name in BASELINE_NAMES}

    found = protocol.conditioned(None, built, _TARGET, forecasters, model)

    for name in BASELINE_NAMES:
        assert _mae(found, protocol.UNCONDITIONED, name) == _mae(
            found, protocol.CONDITIONED, name
        )
    assert found["paired"]["mae_median_difference"] == 0.0
    assert found["warnings"] == [protocol.NO_FUTURE_READER]


def test_the_target_known_at_merge_time_stays_refused(store: Store) -> None:
    """`attempts_to_land` is a lookup at the moment the decision is taken."""
    built = write_change(store, rows=60, seed=3)
    forecasters: dict[str, Any] = {FutureEcho.name: FutureEcho()}

    with pytest.raises(Refused) as refused:
        protocol.conditioned(
            None, built, CANDIDATE_FORBIDDEN, forecasters, FutureEcho.name
        )
    assert "known at merge time" in str(refused.value)

    # And a target that is neither forbidden nor a candidate target is refused too,
    # naming the three that are: `forecast candidate --target verification_cycles`
    # would otherwise run a protocol whose meaning is about post-merge quantities on a
    # column that is known before the merge.
    with pytest.raises(Refused) as wrong:
        protocol.conditioned(
            None, built, "verification_cycles", forecasters, FutureEcho.name
        )
    assert "is not a candidate target" in str(wrong.value)


def test_the_forecast_of_the_next_change_conditions_on_a_real_candidate(
    store: Store, tmp_path: Path
) -> None:
    """`one_step`: the A block of a real diff, on the row that has not happened.

    Origin N is one past the last row, so there is no actual and nothing is scored.
    What is asserted is the shape of the claim: the conditioned forecast saw a block
    that is n_ctx + H long and ends on the candidate, the unconditioned one saw no
    block at all, and the two points differ by exactly what the reader read.
    """
    built = write_change(store, rows=60, seed=3)
    root = _repository(tmp_path / "repo")
    base = _git(root, "rev-parse", "HEAD")
    for name, lines in _CANDIDATE_FILES.items():
        _write(root, name, lines)
    head = _commit(root, "candidate")
    block = protocol.features(root, base, head)

    ahead = protocol.one_step(built, _TARGET, {FutureEcho.name: FutureEcho()}, block)

    assert ahead["origin"] == len(built.rows)
    assert ahead["a_block"] == {name: block[name] for name in ABLATION_A}
    plain = ahead["runs"][protocol.UNCONDITIONED][FutureEcho.name]["point"][0]
    fitted = ahead["runs"][protocol.CONDITIONED][FutureEcho.name]["point"][0]
    # FutureEcho adds the last column of the block it read, which is the candidate's
    # own files_changed. So the difference between the two forecasts IS that number,
    # and this is the assertion that the block reached the forecaster intact.
    assert fitted - plain == float(block[_READS])
    printed = protocol.one_step_report(ahead)
    assert CANDIDATE_SENTENCE in printed
    assert str(block["files_changed"]) in printed
    assert head[:12] in printed
    # The weights licence, on a run that used none. Design 6.12 says every forecast
    # command prints it, so "no licensed weights" is a line rather than an absence.
    assert protocol.NO_LICENCE in printed


def test_an_unknown_in_the_a_block_refuses_the_conditioned_forecast(
    store: Store, tmp_path: Path
) -> None:
    """A candidate whose own features cannot be read is not a candidate.

    The binary file makes lines_added and lines_removed None, and nothing here imputes
    one: a conditioned forecast that quietly dropped two of the six columns would be
    the unconditioned forecast wearing the conditioned one's name.
    """
    built = write_change(store, rows=60, seed=3)
    root = _repository(tmp_path / "repo")
    base = _git(root, "rev-parse", "HEAD")
    (root / _BINARY).parent.mkdir(parents=True, exist_ok=True)
    (root / _BINARY).write_bytes(bytes(range(256)) * 4)
    head = _commit(root, "binary")
    block = protocol.features(root, base, head)

    with pytest.raises(Refused) as refused:
        protocol.one_step(built, _TARGET, {FutureEcho.name: FutureEcho()}, block)

    assert "lines_added" in str(refused.value)
    assert "Nothing here imputes one" in str(refused.value)


# -- (e) the lagged rework label at H = 4, scored on step 4 ---------------------------

# The lineage the H = 4 protocol runs on: 60 uncaptured change rows, the six A-block
# columns filled and every process column None, which is the shape `import git-history`
# produces and the shape E16 will run on. `rework_within_3` is a seeded 0/1 column and
# `rework_within_3_lag3` is that column three rows later, so rows 0 to 2 have no source
# change and the frame this target is forecast over is 57 rows long.
LAG_TARGET = "rework_within_3_lag3"
LAG_ROWS = 60
LAG_C_MIN = 16
LAG_HORIZON = 4
LAG_RETAINED = LAG_ROWS - 3
# `_plan`'s origins: range(c_min, N' - H + 1, H) at stride H.
LAG_ORIGINS = list(range(LAG_C_MIN, LAG_RETAINED - LAG_HORIZON + 1, LAG_HORIZON))
_UNAVAILABLE = (
    *[name for name in ABLATION_C if name not in ABLATION_A],
    "merge_verification_ms",
    "merge_verification_failed",
)


def _lag_series(rows: int = LAG_ROWS, seed: int = 5) -> Series:
    """A change-clock lineage written by hand: the A block, the label and its lag.

    Written here rather than taken from `synthetic_series.make_change` because what is
    under test is the lag and the exclusion it forces, and a fixture whose target had no
    holes would exercise neither. Every number is a draw from a seeded generator and
    nothing here is evidence about any repository.
    """
    dice = random.Random(seed)
    labels = [float(dice.random() < 0.4) for _ in range(rows)]
    values: dict[str, list[float | None]] = {
        name: [float(dice.randint(1, 40)) for _ in range(rows)] for name in ABLATION_A
    }
    values |= {name: [None] * rows for name in _UNAVAILABLE}
    values["rework_within_3"] = list(labels)
    values["rework_within_3_lag3"] = [
        labels[at - REWORK_TAIL] if at >= REWORK_TAIL else None for at in range(rows)
    ]
    specs = [
        ColumnSpec(
            name=name,
            unit="flag" if name.startswith("rework") else "count",
            role="past_covariate",
            coverage=_lag_coverage(values[name]),
        )
        for name in _LAG_COLUMNS
    ]
    return Series(
        series_id=f"syn_lag_{seed}_{rows}",
        clock="change",
        cohort={"capture_id": f"synthetic-lag-{seed}", "provider": "git"},
        columns=specs,
        rows=[[values[name][at] for name in _LAG_COLUMNS] for at in range(rows)],
        row_meta=[
            RowMeta(
                row_key=f"lag_{at:04d}",
                row_end_ts=f"2026-08-01T{at // 60:02d}:{at % 60:02d}:00.000000Z",
                provenance=[f"lag_{at:04d}"],
                flags=[UNCAPTURED],
            )
            for at in range(rows)
        ],
        changepoints=[],
        missingness_policy="exclude",
        reducer_version="syn-lag",
    )


_LAG_COLUMNS = (*ABLATION_C, *CANDIDATE_TARGETS)


def _lag_coverage(cells: list[float | None]) -> str:
    """`unavailable` when no row could carry it, `partial` when some rows do not.

    The two words the frame turns on: an unavailable column is excluded from the
    variant by name (W7-T3) and a partial TARGET is forecast over the rows that carry
    it (W8-T3). Read off the cells so the fixture cannot claim a cell it does not hold.
    """
    if all(cell is None for cell in cells):
        return "unavailable"
    return "partial" if any(cell is None for cell in cells) else "observed"


def _lag_forecasters() -> dict[str, Any]:
    return {"echo": EchoStub(), **{name: make(name) for name in BASELINE_NAMES}}


def test_the_lagged_label_runs_at_four_steps_and_is_scored_on_the_fourth(
    store: Store,
) -> None:
    """E16's protocol for H8 on `rework_within_3_lag3`, end to end.

    Four claims, and each of them is a different way the run could have been wrong.

      H is 4 and comes from the registry, so both runs of the pair ask for four steps.

      The A block spans n_ctx + 4 and its last four values are the CANDIDATE's, because
      steps 2 to 4 are edge-replicated from row o. A block that read rows o + 1 to
      o + 3 would be conditioning on the three changes after the candidate, which is
      the look-ahead this whole column exists to avoid.

      The metrics are computed over step 4 alone, recomputed here by hand from the
      window records the run stored. Step 4 at origin o is change o's own label; steps
      1 to 3 are changes o - 3 to o - 1, which the merge decision is not about.

      The origins stop at N' - H over the RETAINED frame, and the three rows with no
      source change are excluded by name with their keys in the run.
    """
    built = _lag_series()
    store.put_series(built)
    forecasters = _lag_forecasters()
    stub = forecasters["echo"]
    assert isinstance(stub, EchoStub)

    found = protocol.conditioned(None, built, LAG_TARGET, forecasters, "echo")

    assert found["horizon"] == LAG_HORIZON == 4
    assert found["scored_steps"] == [4]
    for name, one in found["runs"].items():
        _assert_lag_run(name, one)
    assert max(LAG_ORIGINS) <= LAG_RETAINED - LAG_HORIZON
    _assert_blocks(stub, frame_of(built, LAG_TARGET))
    for name, one in found["runs"].items():
        _assert_step_four(name, one)

    printed = protocol.report(found)
    assert "scored on step 4 of 4" in printed
    assert CANDIDATE_SENTENCE in printed


def _assert_lag_run(name: str, one: dict[str, Any]) -> None:
    """One run of the pair: four steps, step 4 scored, the origins and the exclusion."""
    assert one["horizon"] == LAG_HORIZON, name
    assert one["scored_steps"] == [4], name
    assert CANDIDATE_SENTENCE in one["assumptions"], name
    assert [record["origin"] for record in one["windows"]] == LAG_ORIGINS, name
    assert one["excluded_rows"]["count"] == REWORK_TAIL, name
    assert one["excluded_rows"]["row_keys"] == [
        f"lag_{at:04d}" for at in range(REWORK_TAIL)
    ], name
    assert one["n_rows"] == LAG_RETAINED, name


def _assert_blocks(stub: EchoStub, frame: Series) -> None:
    """Every future block: n_ctx + 4 long, ending in four copies of the candidate row.

    Read off the windows the stub kept, which is the only place a test can see what a
    forecaster was handed. The unconditioned half of the pair carries no block at all,
    and that count is asserted too: it is the other half of what the difference means.
    """
    fitted = [window for window in stub.seen if window.future is not None]
    assert len(fitted) == len(LAG_ORIGINS)
    assert len([one for one in stub.seen if one.future is None]) == len(fitted)
    files = [column.name for column in frame.columns].index(_READS)
    known = [row[files] for row in frame.rows]
    assert all(value is not None for value in known)
    for window in fitted:
        block = (window.future or {})[_READS]
        assert len(block) == window.n_ctx + LAG_HORIZON
        assert block[-LAG_HORIZON:] == [known[window.origin]] * LAG_HORIZON
        assert block[: window.n_ctx] == known[window.ctx_start : window.origin]


def _assert_step_four(name: str, one: dict[str, Any]) -> None:
    """The MAE the run reports, recomputed by hand from the records over step 4 alone,
    and shown to differ from the number every step would have given."""
    errors = [_error(record, 3) for record in one["windows"]]
    every = [
        statistics.fmean([_error(record, at) for at in range(LAG_HORIZON)])
        for record in one["windows"]
    ]
    scored = one["metrics"]["forecasters"]["echo"]
    assert scored["mae_mean"] == pytest.approx(statistics.fmean(errors)), name
    assert scored["mae_median"] == pytest.approx(statistics.median(errors)), name
    assert statistics.fmean(every) != pytest.approx(statistics.fmean(errors)), name


def _error(record: dict[str, Any], at: int) -> float:
    """|actual - point| at one step of one window, off the stored record."""
    actual = float(record["actual"][at])
    return abs(actual - float(record["forecasts"]["echo"]["point"][at]))
