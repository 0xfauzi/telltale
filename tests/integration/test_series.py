"""From recorded bytes to a Series, through the real compiler and the real store.

Every test here replays E01's S1 capture over HTTP into a real receiver, runs the real
activity reducer, and compiles the result with `telltale.series`. Nothing is stubbed:
the numbers asserted below are compared against the fixture's own `api_request` records
read by a second path that does not go through the compiler.

S1 is the scenario because it is the shape the product exists for (a failing test, an
edit, a passing test) and because it exercises three of the four counters on its own:
seven model requests, six tool calls, three of them verification runs, and one file
edit. Its `env_changed` column is unavailable and all None, because a replayed fixture
is provider bytes and no launcher stamped an environment onto them; that None is the
absence the whole coverage vocabulary is for, and it is not a zero.

Every other column of Claude S1 is filled, which is why the `refuse` policy is exercised
on a replayed Codex capture instead. It is S6, whose second model response carries no
token counts at all: one hole in three columns a surface really does deliver, found in
the recorded bytes rather than punched into them. Before W2-T7 the refusal came from
Claude S1's `last_verification_exit` row 0, a None standing for a state the capture had
observed; the two flag columns that replaced it carry that state as a number. Between
W2-T7 and W3-T3 it came from Codex S1's `request_duration_ms`, a column labelled
observed with every cell None, which is now `unavailable` and is the subject of its own
test below.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import synthetic_series

from telltale import cli, series

if TYPE_CHECKING:
    from collections.abc import Callable

    from conftest import Replayed

    from telltale.store import Store

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "sources"
    / "claude"
    / "2.1.257"
    / "S1"
    / "otel_logs.jsonl"
)

pytestmark = pytest.mark.integration


def _compiled(
    replay: Callable[..., Replayed],
    store: Store,
    scenario: str = "S1",
    policy: str = "exclude",
) -> tuple[str, Any]:
    """Replay, reduce, compile. Returns the capture id and the Series.

    The store is left OPEN, because `series.build` reads through it and `check` reads
    through it again. `Store.rebuild` is the barrier that matters here: it submits its
    work to the writer thread and waits, so every activity it wrote is readable by the
    time it returns.
    """
    replayed = replay(scenario)
    store.rebuild(replayed.capture)
    return replayed.capture, series.build(store, "request", replayed.capture, policy)


def _api_requests() -> list[dict[str, int]]:
    """S1's api_request usage, read straight out of the fixture.

    A second path on purpose: it opens the recorded OTLP bytes and walks them, so it
    shares no code with the receiver, the parser, the reducer or the compiler. An
    assertion against numbers this produced is an assertion against the provider.
    """
    found: list[dict[str, int]] = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)["body_json"]
        for resource in record.get("resourceLogs", []):
            for scope in resource.get("scopeLogs", []):
                logs = scope.get("logRecords", [])
                found += [_usage(log) for log in logs if _is(log)]
    return found


def _is(log: dict[str, Any]) -> bool:
    return bool(log.get("body", {}).get("stringValue") == "claude_code.api_request")


def _usage(log: dict[str, Any]) -> dict[str, int]:
    return {
        attribute["key"]: int(attribute["value"]["intValue"])
        for attribute in log.get("attributes", [])
        if "intValue" in attribute["value"]
    }


def _column(built: Any, name: str) -> list[float | None]:
    index = [spec.name for spec in built.columns].index(name)
    return [row[index] for row in built.rows]


def _spec(built: Any, name: str) -> Any:
    return next(spec for spec in built.columns if spec.name == name)


def _of_type(store: Store, capture_id: str, kind: str) -> list[dict[str, Any]]:
    return [row for row in store.activities(capture_id) if row["activity_type"] == kind]


def test_one_row_per_model_request_with_the_provider_s_own_numbers(
    replay: Callable[..., Replayed], store: Store
) -> None:
    """Row count, and every usage cell, against the fixture's api_request records."""
    capture_id, built = _compiled(replay, store)
    requests = _of_type(store, capture_id, "model_request")
    recorded = _api_requests()

    assert len(recorded) == 7, "the fixture should hold seven api_request records"
    assert len(built.rows) == len(requests) == len(recorded)
    assert _column(built, "fresh_input_tokens") == [
        usage["input_tokens"] for usage in recorded
    ]
    assert _column(built, "cache_read_tokens") == [
        usage["cache_read_tokens"] for usage in recorded
    ]
    assert _column(built, "output_tokens") == [
        usage["output_tokens"] for usage in recorded
    ]
    assert _column(built, "request_duration_ms") == [
        usage["duration_ms"] for usage in recorded
    ]


def test_the_counters_add_up_to_the_activities_they_count(
    replay: Callable[..., Replayed], store: Store
) -> None:
    """A `_since_prev` column is a partition of the capture, so its sum is the total.

    It holds only because every one of those activities sits before the last row's end.
    An activity after it would be counted by nothing, which is correct and is why this
    is an equality rather than an invariant: it is a fact about S1, asserted as one.
    """
    capture_id, built = _compiled(replay, store)
    verifications = _of_type(store, capture_id, "verification_run")
    edits = _of_type(store, capture_id, "file_edit")

    assert len(verifications) == 3
    assert _total(built, "verification_runs_since_prev") == len(verifications)
    assert _total(built, "files_edited_since_prev") == len(edits)
    assert _total(built, "tool_calls_since_prev") == 6
    # The two flags, derived by hand from S1's own timeline. A run is consumed at the
    # position the fold orders by, which is `ended_at`: the three runs end at
    # 20:04:21.995, 20:04:25.967 and 20:04:31.939, and the requests are at 21.422,
    # 24.172, 25.665, 27.534, 30.479, 31.843 and 33.145. So the runs fall in the gaps
    # closing rows 1, 3 and 6, and row 0 is the only row no run precedes: that is the
    # one 0 in verification_seen, and it is a measurement rather than a gap.
    # (`telltale timeline` prints started_at, which is 21.401, 25.498 and 31.809: the
    # first run STARTS before request 0 and ENDS after it, and the fold reads the end.)
    assert _column(built, "verification_runs_since_prev") == [0, 1, 0, 1, 0, 0, 1]
    assert _column(built, "verification_seen") == [0, 1, 1, 1, 1, 1, 1]
    # All three runs pipe pytest into `tail`, so NONE of them states an outcome
    # (W3-T3): `exit_masked` is on every one and `success` is on none. The second flag
    # stays 0 for the reason W2-T7 gives, which is not "they passed": a run whose
    # result nobody stated leaves the last stated answer standing, and none was ever
    # stated here. `verification_seen` is the column that says a run happened.
    assert [row["fields"].get("success") for row in verifications] == [None] * 3
    assert [row["fields"].get("exit_masked") for row in verifications] == [True] * 3
    assert _column(built, "last_verification_failed") == [0] * 7


def _total(built: Any, name: str) -> float:
    """The sum of one column, refusing a None rather than reading it as a zero."""
    cells = _column(built, name)
    assert None not in cells, f"{name} has a hole, so it has no total"
    return sum(cell for cell in cells if cell is not None)


def test_a_failed_verification_sets_the_second_flag_and_it_stays_set(
    replay: Callable[..., Replayed], store: Store
) -> None:
    """S7 is the fixture where a verification fails, so the 1 branch is a real 1.

    Both flags outlive the row that set them: `reset` clears the four `_since_prev`
    counters and leaves these two alone, so the last stated result stands until another
    verification states otherwise. S7 makes eight requests and runs one test, which
    fails in the gap closing row 6, so the flags turn together there and hold at row 7.
    """
    capture_id, built = _compiled(replay, store, "S7")
    runs = _of_type(store, capture_id, "verification_run")

    assert [row["fields"]["success"] for row in runs] == [False]
    assert _column(built, "verification_runs_since_prev") == [0, 0, 0, 0, 0, 0, 1, 0]
    assert _column(built, "verification_seen") == [0, 0, 0, 0, 0, 0, 1, 1]
    assert _column(built, "last_verification_failed") == [0, 0, 0, 0, 0, 0, 1, 1]


def test_a_capability_nobody_observed_is_none_and_never_zero(
    replay: Callable[..., Replayed], store: Store
) -> None:
    """`env_changed` on a replayed fixture: unavailable, all None, no changepoints.

    This is the defect W1-T2 found in its own summary, in the one place a Series can
    still make it: a column of zeros here would say the environment was observed and
    did not change, and what happened is that no launcher stamped an environment onto
    these bytes at all.
    """
    built = _compiled(replay, store)[1]

    assert _spec(built, "env_changed").coverage == "unavailable"
    assert _column(built, "env_changed") == [None] * len(built.rows)
    assert built.changepoints == []
    assert all(meta.env_fingerprint_id is None for meta in built.row_meta)
    assert _spec(built, "compaction_before").coverage == "observed"
    assert _column(built, "compaction_before") == [0] * len(built.rows)


def test_every_provenance_id_resolves_and_check_finds_nothing(
    replay: Callable[..., Replayed], store: Store
) -> None:
    capture_id, built = _compiled(replay, store)
    stored = {str(row["activity_id"]) for row in store.activities(capture_id)}

    assert series.check(store, built) == []
    for index, meta in enumerate(built.row_meta):
        assert meta.provenance, f"row {index} has no provenance"
        assert set(meta.provenance) <= stored
        assert meta.row_key == meta.provenance[-1]


def test_building_twice_gives_the_same_series_id(
    replay: Callable[..., Replayed], store: Store
) -> None:
    """The id is a content hash, so `put_series` replaces rather than accumulating."""
    capture_id, first = _compiled(replay, store)
    second = series.build(store, "request", capture_id)

    assert first.series_id == second.series_id
    assert first.rows == second.rows
    store.put_series(first)
    store.put_series(second)
    assert [row["series_id"] for row in store.series_ids()] == [first.series_id]


def test_a_row_that_ends_earlier_than_its_provenance_fails_check(
    replay: Callable[..., Replayed], store: Store
) -> None:
    """Move one row's end backwards and the invariant has to see it.

    Both halves of design 6.12's check fire on this edit: `row_end_ts` now goes
    backwards, and the activities the row was folded from are now later than it. The
    row is named in every sentence, because a violation nobody can locate is a
    violation nobody will fix.
    """
    built = _compiled(replay, store)[1]
    meta = list(built.row_meta)
    meta[3] = replace(meta[3], row_end_ts=meta[0].row_end_ts)
    violations = series.check(store, replace(built, row_meta=meta))

    assert violations
    assert all(violation.startswith("row 3 ") for violation in violations)
    assert any("look-ahead" in violation for violation in violations)
    assert any("must not go backwards" in violation for violation in violations)


def test_refuse_names_the_column_and_the_first_row_with_a_hole(
    replay: Callable[..., Replayed], store: Store
) -> None:
    """The Codex S6 replay has a real hole in an observed column, found not punched.

    Its eleven `response.completed` records carry the six token counters, except the
    second, which carries none of them: measured in the fixture bytes, the eleven
    `input_token_count` values are 11437, absent, 20006, 20291, 20687, 20896, 21090,
    21748, 22241, 22554 and 22894. Codex reports request usage on otel_logs, so the
    three usage columns are observed; row 1 of each is None because one record did not
    say. Under `exclude` those None cells survive to the forecaster, which drops the
    windows containing them; under `refuse` the build stops and says which column and
    which row.

    Claude S1 was the case W1-T5 and W2-T5 used, through `last_verification_exit` row 0.
    That column is gone (W2-T7) and Claude S1 now has no hole in any observed column, so
    the same policy builds it: the assertion at the end is that refuse refuses a gap and
    not a capture.
    """
    codex = replay("S6", provider="codex")
    store.rebuild(codex.capture)

    with pytest.raises(series.Refused) as refusal:
        series.build(store, "request", codex.capture, "refuse")

    message = str(refusal.value)
    assert "fresh_input_tokens" in message
    assert "row 1" in message
    built = series.build(store, "request", codex.capture, "exclude")
    assert _spec(built, "fresh_input_tokens").coverage == "observed"
    assert _column(built, "fresh_input_tokens").count(None) == 1
    assert _column(built, "fresh_input_tokens")[1] is None

    claude = replay("S1")
    store.rebuild(claude.capture)
    assert series.build(store, "request", claude.capture, "refuse").rows


def test_a_column_with_no_value_in_any_row_is_never_observed(
    replay: Callable[..., Replayed], store: Store
) -> None:
    """Codex `request_duration_ms`: observed and empty was a claim nobody could support.

    Design 6.12 said Codex derives a request duration from turn timestamps. W3-T3
    measured that it cannot. The exec stream's `turn.started` and `turn.completed`
    carry no clock at all, and the rollout's `task_started` and `task_complete` do
    carry one but bracket a TURN: S1 is one turn holding seven model responses, so the
    turn's 31584 ms is not any request's duration and dividing it by seven would be a
    number nobody measured. The cells are therefore None, and the coverage word has to
    follow them: `observed` there says a surface delivered this and none did.

    `column_report` names the rule that fixed the word: `no value in any row` for this
    column, and `measured for this column` for the ones a surface filled. It does not
    say whether an empty column's capability ALSO said unavailable, which is a
    different question and is answered by the coverage block `telltale show` prints.

    Break it by deleting the `elif` branch of `series.blank_unobservable`: the column
    goes back to `observed` with seven None cells, and `refuse` stops the build over a
    hole no Codex capture can ever fill.
    """
    codex = replay("S1", provider="codex")
    store.rebuild(codex.capture)
    built = series.build(store, "request", codex.capture, "exclude")
    requests = _of_type(store, codex.capture, "model_request")
    turns = _of_type(store, codex.capture, "turn")
    report = {row["column"]: row for row in series.column_report(built)}

    assert len(requests) == 7
    assert len(turns) == 1
    assert [dict(row["fields"]).get("duration_ms") for row in requests] == [None] * 7
    assert dict(turns[0]["fields"])["duration_ms"] == 31584
    assert _column(built, "request_duration_ms") == [None] * 7
    assert _spec(built, "request_duration_ms").coverage == "unavailable"
    assert report["request_duration_ms"]["reason"] == series.EMPTY_COLUMN
    assert report["output_tokens"]["reason"] == series.MEASURED_COLUMN
    # The hole this capture used to refuse on is gone, so the policy builds it.
    assert series.build(store, "request", codex.capture, "refuse").rows


def test_the_lineage_clocks_take_a_repo_id_and_never_a_capture(
    replay: Callable[..., Replayed], store: Store
) -> None:
    """W3-T1 built the two clocks, so what is refused now is the KEY, not the clock.

    A replayed fixture is provider bytes with no launcher, so no observation of it
    carries a repo_id (W1-T2) and its capture id is not one either. Handing either
    clock a capture id therefore has to refuse by name rather than fold the capture,
    which is the mistake the old refusal used to make impossible for free.

    That the two clocks BUILD is measured in test_outcome.py, over six real
    `telltale run` attempts in a real repository: a replayed fixture has no lineage to
    be a row of.
    """
    replayed = replay("S1")
    store.rebuild(replayed.capture)

    for clock in ("attempt", "change"):
        with pytest.raises(series.Refused, match="carries this repo_id"):
            series.build(store, clock, replayed.capture)
        with pytest.raises(series.Refused, match="carries this repo_id"):
            series.build(store, clock, "no-such-repository")


def test_the_synthetic_writer_round_trips_through_the_store(store: Store) -> None:
    """The 200-row series W1-T6 backtests, written and read through the real table."""
    written = synthetic_series.write(store, rows=200, seed=1)
    store.flush()
    read = store.series(written.series_id)

    assert read is not None
    assert read == written
    assert len(read.rows) == 200
    assert read.changepoints == [synthetic_series.CHANGEPOINT]
    assert read.row_meta[synthetic_series.CHANGEPOINT].env_fingerprint_id != (
        read.row_meta[synthetic_series.CHANGEPOINT - 1].env_fingerprint_id
    )
    assert synthetic_series.make(rows=200, seed=1).series_id == written.series_id
    assert synthetic_series.make(rows=200, seed=2).series_id != written.series_id
    assert store.forecast_runs(written.series_id) == []


def test_the_cli_builds_checks_and_lists(
    replay: Callable[..., Replayed],
    store: Store,
    settled: Callable[[Store], Store],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The three commands a person runs, in the order they run them.

    The store is closed first: `telltale series build` opens its own, and two writer
    threads on one SQLite file is the one thing store.py's design does not allow.
    """
    replayed = replay("S1")
    store.rebuild(replayed.capture)
    settled(store)
    build = ["series", "build", "--clock", "request", "--capture", replayed.capture]

    assert cli.main(build) == 0
    printed = capsys.readouterr().out
    series_id = printed.split()[0]
    assert "7 rows" in printed
    assert "env_changed" in printed
    assert "unavailable" in printed
    assert "changepoints none" in printed

    assert cli.main(["series", "check", series_id]) == 0
    assert capsys.readouterr().out.strip() == "ok"

    assert cli.main(["series", "list"]) == 0
    assert series_id in capsys.readouterr().out

    # The two lineage clocks are keyed on a repository, and argparse refuses the
    # combination before the compiler is reached: `--capture` and `--repo` are one
    # required, mutually exclusive group, so a build with neither has no history.
    with pytest.raises(SystemExit):
        cli.main(["series", "build", "--clock", "attempt"])
    lineage = ["series", "build", "--clock", "attempt", "--repo", "no-such-repository"]
    assert cli.main(lineage) == 2
    assert "carries this repo_id" in capsys.readouterr().out
    # Every observed column of S1 is filled, so `refuse` has nothing to refuse and
    # builds. The refusal itself is exercised on the Codex replay above.
    assert cli.main([*build, "--policy", "refuse"]) == 0
    assert "policy refuse" in capsys.readouterr().out
