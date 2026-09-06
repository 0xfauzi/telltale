"""`telltale advise`: the shadow advisory, end to end. Spec 14.6, design 6.12.

Four questions, and they are four different things.

  What the page SAYS. The A block off a real `git diff base...head`, the decision label
  of a real stored backtest, the readiness word of the real eight-line checklist, and
  the mandatory sentence under every target. Each number in the A block is asserted
  against the arithmetic of what this test wrote, never against a string copied from an
  earlier run.

  What the store HOLDS. One `policy.advisory` observation, in a capture of its own, and
  a payload that survives the real sanitizer with no `unknown_field` diagnostic. An
  allowlist entry that misspells a field would drop it silently, so the assertion is
  that the six features and the forecast_run_ids are in the row that came BACK out of
  the database.

  What it says when it cannot say anything. With no stored backtest for a target the
  label is `no assessable forecast`, no forecast numbers are printed, and the A block
  is stored anyway: what is known about the change is known whether or not anything
  can be forecast from it. A stored SCENARIO (W6-T1) is that case and not another one:
  it is a true-order row of the same pair carrying no score, so the two tests at the
  end of this file store one with a real `telltale forecast scenario` and read what the
  page says with a backtest beside it and with nothing beside it.

  What it refuses. ADR-014's word check runs over the whole rendered page BEFORE the
  observation is appended, so a record that claims a cause leaves nothing on the disk.

Everything below runs against the real system: a real git repository, the real CLI
through subprocess, the real store, the real backtester and the real sanitizer. The
change series is `synthetic_series.write_change`, written through the real store past
the real CHECK constraints, because this repository's own change clock cannot forecast
any candidate target today (W5-T1 measured why, and the amendment records it).
"""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING, Any

import pytest
from synthetic_series import write_change
from test_series_lineage import _git, _repository, _run, _store

from telltale import cli_advise, report_advise
from telltale.forecast import (
    ABLATION_A,
    C_MIN_SHORT,
    CANDIDATE_TARGETS,
    K_MIN,
    REWORK_TAIL,
    ForbiddenWord,
)
from telltale.store import Store

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


# What the candidate commit writes, and with how many lines. Every expected number
# below is arithmetic on THIS, so a fixture that stated its own answer cannot pass.
_FILES = {"pkg/a.py": 7, "tests/test_x.py": 4, "pyproject.toml": 2}
# The target that gets a stored backtest, and the two that deliberately do not.
_SCORED = "merge_verification_failed"
# The one candidate target of this series whose column has holes, and the horizon the
# registry gives it. Its frame is three rows shorter than the series, so its run is
# stored under `<series>:<target>` and its checklist may only be asked at H = 4.
_HOLED = "rework_within_3_lag3"
_HOLED_HORIZON = 4
_MODEL = "persistence"
_FORECASTERS = "persistence,rolling_median"
# What the scenario declares a path for. ABLATION_A, and so declarable on the change
# clock; every other column of this series is refused a path by name.
_DECLARED = "files_changed"


def _candidate(root: Path) -> tuple[str, str]:
    """A repository with a base commit and one candidate commit on top of it."""
    base = _git(root, "rev-parse", "HEAD")
    for name, lines in _FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"line {i}\n" for i in range(lines)), "utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "candidate")
    return base, _git(root, "rev-parse", "HEAD")


# The rows the change series is written with. Named because two tests below compute a
# window count off it rather than copying one out of a run.
_SERIES_ROWS = 60


def _series(home: Path) -> str:
    """One change-clock series in this test's own store, and its id."""
    store = Store(home / "telltale.db").open()
    try:
        return write_change(store, rows=_SERIES_ROWS, seed=3).series_id
    finally:
        store.close()


def _backtest(root: Path, series_id: str) -> str:
    """A real `telltale forecast backtest`, and the forecast_run_id it stored."""
    done = _run(
        root, "forecast", "backtest", "--series", series_id, "--target", _SCORED,
        "--forecasters", _FORECASTERS, "--model", _MODEL,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr.decode()
    printed = done.stdout.decode()
    return printed.rsplit("forecast_run_id ", 1)[1].strip()


def _backtest_holed(root: Path, series_id: str) -> str:
    """A real backtest of the target with holes, and the forecast_run_id it stored.

    Stored under the FRAME's id (`<series>:<target>`), not the series id, because a
    target with holes is forecast over the rows where it is known (forecast/frame.py).
    Nothing here says so: what the test asserts is what came back out of the store.
    """
    done = _run(
        root, "forecast", "backtest", "--series", series_id, "--target", _HOLED,
        "--horizon", str(_HOLED_HORIZON), "--forecasters", _FORECASTERS,
        "--model", _MODEL,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr.decode()
    return done.stdout.decode().rsplit("forecast_run_id ", 1)[1].strip()


def _block(printed: str, target: str) -> str:
    """One target's section of the page, from its heading to the next one."""
    after = printed.split(f"target {target} (", 1)[1]
    return after.split("\ntarget ", 1)[0]


def _scenario(root: Path, series_id: str) -> str:
    """A real `telltale forecast scenario` on the same series and target as _backtest.

    The real command rather than a `store.put_forecast_run` shaped like one: what is
    under test is that the row this command writes is not read as a score, and a row
    this test built itself would prove only that this test can shape one. Horizon 1 is
    what every candidate target is registered for, and the declared column is the A
    block's, which is what the change clock allows a path for.
    """
    done = _run(
        root, "forecast", "scenario", "--series", series_id, "--target", _SCORED,
        "--horizon", "1", "--future", f"{_DECLARED}=9", "--forecasters", _MODEL,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr.decode()
    return done.stdout.decode().rsplit("forecast_run_id ", 1)[1].strip()


def _advise(root: Path, base: str, head: str, series_id: str) -> str:
    done = _run(
        root, "advise", "--base", base, "--head", head, "--series", series_id
    )  # fmt: skip
    assert done.returncode == 0, done.stderr.decode()
    return done.stdout.decode()


def _advisories() -> list[dict[str, Any]]:
    """Every stored advisory, read back out of the database it was written to."""
    return [
        dict(row) for row in _store().observations_of_type(cli_advise.ADVISORY_TYPE)
    ]


@pytest.mark.usefixtures("telltale_home")
def test_the_advisory_prints_the_block_the_label_and_the_sentence(
    tmp_path: Path, telltale_home: Path
) -> None:
    """The page, and the one observation under it. Design 6.12, spec 14.6.

    The A block is asserted against the files this test wrote. The label is asserted
    against the run `forecast backtest` stored a moment earlier, by id, so a label the
    advisory derived on its own would fail here rather than agree by luck.
    """
    root = _repository(tmp_path / "repo")
    base, head = _candidate(root)
    series_id = _series(telltale_home)
    run_id = _backtest(root, series_id)

    printed = _advise(root, base, head, series_id)

    assert f"files_changed       {len(_FILES)}" in printed
    assert f"lines_added         {sum(_FILES.values())}" in printed
    assert "lines_removed       0" in printed
    assert f"target {_SCORED} (flag)" in printed
    assert "label: baseline sufficient" in printed
    assert run_id in printed
    # The mandatory sentence of design 6.12, once under every target block.
    assert printed.count(_SENTENCE) == len(CANDIDATE_TARGETS)
    assert printed.endswith(_tail(printed))
    assert report_advise.SHADOW in printed
    # "Confidence" is the label plus the readiness word, beside every forecast number.
    # `ready` and not `variation`: check 7 measures the minority share of a flag since
    # W8-F1, and this column takes both of its values on the rows the run scores.
    assert "baseline sufficient / ready" in printed

    stored = _advisories()
    assert len(stored) == 1, [row["capture_id"] for row in stored]
    payload = dict(stored[0]["payload"])
    assert stored[0]["provider"] == cli_advise.PROVIDER
    assert str(stored[0]["capture_id"]).startswith(cli_advise.PREFIX)
    assert payload["action"] == cli_advise.ACTION
    assert payload["series_id"] == series_id
    assert payload["features"] == {
        "files_changed": len(_FILES),
        "lines_added": sum(_FILES.values()),
        "lines_removed": 0,
        "subsystems_touched": len(_FILES),
        "test_files_changed": 1,
        "dependency_delta": 1,
    }
    assert sorted(payload["features"]) == sorted(ABLATION_A)
    assert payload["forecast_run_ids"][_SCORED] == [run_id]
    assert payload["label"][_SCORED] == "baseline sufficient"
    assert sorted(payload["target"]) == sorted(CANDIDATE_TARGETS)


@pytest.mark.usefixtures("telltale_home")
def test_show_lists_the_advisory_and_sessions_names_the_provider(
    tmp_path: Path, telltale_home: Path
) -> None:
    """`show <adv_...>` prints the payload with the coverage line reading advisory."""
    root = _repository(tmp_path / "repo")
    base, head = _candidate(root)
    series_id = _series(telltale_home)
    _backtest(root, series_id)
    _advise(root, base, head, series_id)
    capture = str(_advisories()[0]["capture_id"])

    done = _run(root, "show", capture)
    assert done.returncode == 0, done.stderr.decode()
    shown = json.loads(done.stdout.decode())

    assert shown["coverage"] == "advisory"
    assert shown["capture_id"] == capture
    assert shown["observation_type"] == cli_advise.ADVISORY_TYPE
    assert shown["payload"]["advisory_id"] == capture
    assert report_advise.SHADOW in shown["warnings"]

    listed = _run(root, "sessions")
    assert listed.returncode == 0, listed.stderr.decode()
    rows = [line for line in listed.stdout.decode().splitlines() if capture in line]
    assert len(rows) == 1, listed.stdout.decode()
    assert cli_advise.PROVIDER in rows[0]


@pytest.mark.usefixtures("telltale_home")
def test_no_stored_backtest_is_no_assessable_forecast_and_the_block_is_stored_anyway(
    tmp_path: Path, telltale_home: Path
) -> None:
    """Absence is a result. What is known about the change is stored either way.

    No `forecast backtest` runs here, so nothing has scored any of the three targets on
    this series. The advisory still reads the A block off git and still stores it: the
    six numbers are facts about the change, and they do not stop being facts because
    nothing can be forecast from them.
    """
    root = _repository(tmp_path / "repo")
    base, head = _candidate(root)
    series_id = _series(telltale_home)

    printed = _advise(root, base, head, series_id)

    assert printed.count(cli_advise.NO_FORECAST) == len(CANDIDATE_TARGETS)
    assert "forecast: not run." in printed
    # The distinction the two constants keep apart: nothing scored this pair, which is
    # not the decision rule looking at a run and writing no label.
    assert "not assessable" not in printed

    payload = dict(_advisories()[0]["payload"])
    assert payload["label"] == dict.fromkeys(CANDIDATE_TARGETS, cli_advise.NO_FORECAST)
    assert payload["forecast_run_ids"] == {name: [] for name in CANDIDATE_TARGETS}
    assert payload["features"]["files_changed"] == len(_FILES)


@pytest.mark.usefixtures("telltale_home")
def test_the_stored_payload_raises_no_unknown_field_diagnostic(
    tmp_path: Path, telltale_home: Path
) -> None:
    """Every field this command writes is in the allowlist, and none was dropped.

    Two assertions rather than one, because they fail differently: a field the
    allowlist does not carry raises the diagnostic, and a field whose Kind refuses its
    value is dropped from the payload with no diagnostic at all.
    """
    root = _repository(tmp_path / "repo")
    base, head = _candidate(root)
    series_id = _series(telltale_home)
    _backtest(root, series_id)
    _advise(root, base, head, series_id)

    stored = _advisories()[0]
    unknown = [
        row
        for row in _store().diagnostics()
        if row["kind"] == "unknown_field"
        and cli_advise.ADVISORY_TYPE in str(row["detail"])
    ]
    assert unknown == []
    assert dict(stored["redaction"])["dropped"] == []
    assert sorted(dict(stored["payload"])) == sorted(_EXPECTED_FIELDS)


@pytest.mark.usefixtures("telltale_home")
def test_a_record_that_claims_a_cause_is_refused_before_anything_is_stored(
    tmp_path: Path, telltale_home: Path
) -> None:
    """ADR-014 at the row and not only at the page. Design 6.12.

    `emit` renders first and appends second, so a page that may not be printed leaves
    nothing on the disk. The forbidden word is put on a real record built by the real
    path, so what is under test is the ORDER of the two steps rather than a string.
    """
    root = _repository(tmp_path / "repo")
    base, head = _candidate(root)
    series_id = _series(telltale_home)
    store = Store(telltale_home / "telltale.db").open()
    try:
        record = cli_advise.build(
            store,
            str(root),
            argparse.Namespace(base=base, head=head, series=series_id),
        )
        record["targets"][0]["forecast_note"] = (
            "a merge of this candidate would take longer"
        )
        with pytest.raises(ForbiddenWord) as refused:
            cli_advise.emit(store, record)
    finally:
        store.close()

    assert "would" in str(refused.value)
    assert _advisories() == []


@pytest.mark.usefixtures("telltale_home")
def test_a_scenario_of_the_same_pair_does_not_displace_the_scored_backtest(
    tmp_path: Path, telltale_home: Path
) -> None:
    """The newest true-order row is not the newest SCORED one. W6-T1, W6-F1.

    A scenario is stored after the backtest, on the same series and the same target,
    with `ordering` true and `metrics` `{}`: by created_at it IS the newest true-order
    row of this pair. What the advisory must print is still the backtest's own label,
    by id, because the backtest is the only run here that scored anything.
    """
    root = _repository(tmp_path / "repo")
    base, head = _candidate(root)
    series_id = _series(telltale_home)
    run_id = _backtest(root, series_id)
    scenario_id = _scenario(root, series_id)

    printed = _advise(root, base, head, series_id)

    assert scenario_id != run_id
    assert "label: baseline sufficient" in printed
    assert run_id in printed
    assert scenario_id not in printed
    assert "not assessable" not in printed
    payload = dict(_advisories()[0]["payload"])
    assert payload["forecast_run_ids"][_SCORED] == [run_id]
    assert payload["label"][_SCORED] == "baseline sufficient"


@pytest.mark.usefixtures("telltale_home")
def test_a_scenario_and_nothing_else_is_no_assessable_forecast(
    tmp_path: Path, telltale_home: Path
) -> None:
    """The honest sentence for a pair nothing scored, with a scenario row on the disk.

    `not assessable` is the decision rule's own word for a run it looked at and could
    not label. Nothing looked at anything here: the one row of this pair holds a
    conditional forecast made at origin N, where there is no actual to score against.
    The two sentences are different claims and the page prints the one that is true.
    """
    root = _repository(tmp_path / "repo")
    base, head = _candidate(root)
    series_id = _series(telltale_home)
    scenario_id = _scenario(root, series_id)

    printed = _advise(root, base, head, series_id)

    assert printed.count(cli_advise.NO_FORECAST) == len(CANDIDATE_TARGETS)
    assert "forecast: not run." in printed
    assert "not assessable" not in printed
    assert scenario_id not in printed
    payload = dict(_advisories()[0]["payload"])
    assert payload["label"] == dict.fromkeys(CANDIDATE_TARGETS, cli_advise.NO_FORECAST)
    assert payload["forecast_run_ids"] == {name: [] for name in CANDIDATE_TARGETS}


@pytest.mark.usefixtures("telltale_home")
def test_a_run_stored_under_the_frames_own_id_is_found_and_named(
    tmp_path: Path, telltale_home: Path
) -> None:
    """The lookup W8-T3 left broken: a holed target's run is not under the series id.

    Break it by looking the run up under the parent id alone and this fails: the page
    prints `no stored true-order backtest of this target on this series` about a row
    `forecast backtest` put on the disk a moment earlier.

    The parent id is still tried FIRST, and the hole-free target scored above keeps its
    own run, so this is one more lookup rather than a different one.
    """
    root = _repository(tmp_path / "repo")
    base, head = _candidate(root)
    series_id = _series(telltale_home)
    whole_id = _backtest(root, series_id)
    holed_id = _backtest_holed(root, series_id)

    printed = _advise(root, base, head, series_id)

    holed = _block(printed, _HOLED)
    assert holed_id in holed
    assert cli_advise.NO_FORECAST not in holed
    assert "no stored true-order backtest" not in holed
    # The hole-free target still finds its own run under the parent id.
    assert whole_id in _block(printed, _SCORED)
    payload = dict(_advisories()[0]["payload"])
    assert payload["forecast_run_ids"][_HOLED] == [holed_id]
    assert payload["forecast_run_ids"][_SCORED] == [whole_id]

    # Which id answered is a fact about the store, so it is read back out of one.
    store = Store(telltale_home / "telltale.db").open()
    try:
        found, under = cli_advise._latest(store, series_id, _HOLED)
        whole, whole_under = cli_advise._latest(store, series_id, _SCORED)
    finally:
        store.close()
    assert found is not None
    assert str(found["forecast_run_id"]) == holed_id
    assert under == f"{series_id}:{_HOLED}"
    assert whole is not None
    assert whole_under == series_id


@pytest.mark.usefixtures("telltale_home")
def test_the_checklist_of_a_lagged_target_is_asked_at_the_registrys_horizon(
    tmp_path: Path, telltale_home: Path
) -> None:
    """H comes from `candidate.horizon`, not from a constant in the command.

    Break it by asking the checklist at 1 again and this fails: `rework_within_3_lag3`
    is registered for H = 4 alone, `backtest.registered` refuses with `horizon 1 is not
    one of [4]`, and the page reports `refused` about the advisory's own constant
    rather than about the series.

    What it reads instead is the first line of the checklist that fails AT H = 4, and
    that line's numbers are arithmetic on this fixture: 60 rows less the three the
    lagged label has no source change for, at c_min 16 and stride 4.
    """
    root = _repository(tmp_path / "repo")
    base, head = _candidate(root)
    series_id = _series(telltale_home)

    printed = _advise(root, base, head, series_id)

    holed = _block(printed, _HOLED)
    assert "horizon 1 is not one of" not in printed
    assert cli_advise.READINESS_REFUSED not in holed
    retained = _SERIES_ROWS - REWORK_TAIL
    windows = (retained - C_MIN_SHORT - _HOLED_HORIZON) // _HOLED_HORIZON + 1
    assert windows == 10
    assert f"readiness: windows  (windows: measured {windows}, needed {K_MIN})" in holed
    # And the three targets at H = 1 are unaffected: this one is the only entry in
    # CANDIDATE_HORIZON whose horizon is not 1.
    assert f"readiness: {cli_advise.READY}" in _block(printed, _SCORED)


_SENTENCE = (
    "The difference between the conditioned and unconditioned forecast measures how"
    " much the candidate's known features change the forecast; it is not the effect of"
    " merging the candidate, because only one future is observed."
)

# Every field name the payload carries, so a field added without an allowlist entry
# fails here rather than being dropped into silence.
_EXPECTED_FIELDS = (
    "advisory_id",
    "action",
    "policy_version",
    "base_sha",
    "head_sha",
    "series_id",
    "target",
    "forecast_run_ids",
    "label",
    "readiness",
    "features",
)


def _tail(printed: str) -> str:
    """The last two lines the command prints: the id, and how to read it back.

    Spelled as a function of what was printed rather than as a literal, because the
    advisory id is a content hash and a literal would be a number nobody measured.
    """
    capture = printed.rsplit("advisory ", 1)[1].splitlines()[0]
    return f"advisory {capture}\nrun `telltale show {capture}` for the stored payload\n"
