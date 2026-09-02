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
on the replayed Codex S1 instead: its `request_duration_ms` is observed and empty on
every row, because Codex states no per-response duration on any surface (W1-T3). Before
W2-T7 the refusal came from `last_verification_exit` row 0, which was a None standing
for a state the capture had observed; the two flag columns that replaced it carry that
state as a number.
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
    # one 0 in verification_seen, and it is a measurement rather than a gap. All three
    # runs carry `success` true on this fixture, so nothing ever sets the second flag.
    # (`telltale timeline` prints started_at, which is 21.401, 25.498 and 31.809: the
    # first run STARTS before request 0 and ENDS after it, and the fold reads the end.)
    assert _column(built, "verification_runs_since_prev") == [0, 1, 0, 1, 0, 0, 1]
    assert _column(built, "verification_seen") == [0, 1, 1, 1, 1, 1, 1]
    assert _column(built, "last_verification_failed") == [0] * 7
    assert [row["fields"]["success"] for row in verifications] == [True] * 3


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
    """The Codex S1 replay has a real hole in an observed column, found not punched.

    `request_duration_ms` rides the request_usage capability, which Codex reports, so
    the column's coverage is observed; and no Codex surface states a duration per
    response, so every cell of it is None. Under `exclude` those None cells survive to
    the forecaster, which drops the windows containing them; under `refuse` the build
    stops and says which column and which row.

    Claude S1 was the case W1-T5 and W2-T5 used, through `last_verification_exit` row 0.
    That column is gone (W2-T7) and Claude S1 now has no hole in any observed column, so
    the same policy builds it: the assertion at the end is that refuse refuses a gap and
    not a capture.
    """
    codex = replay("S1", provider="codex")
    store.rebuild(codex.capture)

    with pytest.raises(series.Refused) as refusal:
        series.build(store, "request", codex.capture, "refuse")

    message = str(refusal.value)
    assert "request_duration_ms" in message
    assert "row 0" in message
    built = series.build(store, "request", codex.capture, "exclude")
    assert _column(built, "request_duration_ms") == [None] * len(built.rows)
    assert _spec(built, "request_duration_ms").coverage == "observed"

    claude = replay("S1")
    store.rebuild(claude.capture)
    assert series.build(store, "request", claude.capture, "refuse").rows


def test_the_unbuilt_clocks_are_refused_by_name(
    replay: Callable[..., Replayed], store: Store
) -> None:
    replayed = replay("S1")
    store.rebuild(replayed.capture)

    for clock in ("attempt", "change"):
        with pytest.raises(series.Refused, match=f"the {clock} clock is not built yet"):
            series.build(store, clock, replayed.capture)


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

    unbuilt = ["series", "build", "--clock", "attempt", "--capture", replayed.capture]
    assert cli.main(unbuilt) == 2
    assert "not built yet" in capsys.readouterr().out
    # Every observed column of S1 is filled, so `refuse` has nothing to refuse and
    # builds. The refusal itself is exercised on the Codex replay above.
    assert cli.main([*build, "--policy", "refuse"]) == 0
    assert "policy refuse" in capsys.readouterr().out
