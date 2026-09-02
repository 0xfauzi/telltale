"""The evidence vector, the cohort behind its percentile column, and compare.

Two halves, and they ask two different questions.

The first replays E01's recorded Claude Code sessions through a real receiver, exactly
as the rest of the suite does, and asks what a capture with no environment fingerprint
gets: a full vector of raw numbers, and a percentile column that says why there is no
percentile. A replayed capture carries no telltale.environment observation, so its
content level is unknown, so it is in no cohort. That is the right answer for it and not
a gap to patch, and `test_a_replayed_capture_carries_no_fingerprint` is the query that
shows the input really lacks the row rather than the reducer losing it.

The second builds a cohort the only way one can be built without spending tokens: ten
captures of `fake_agent.py` through the real `telltale run`, in one temporary
repository, under one temporary $TELLTALE_HOME. Ten because design 6.11 says ten. The
loop runs once for this module and the tests that change the store copy it first, since
each capture costs about a quarter of a second of wall clock and nine of the assertions
here do not need their own ten.

Nothing is stubbed. The vectors come out of `telltale.cohorts` over a real store, and
the printed rows come out of the installed `telltale` console script in a subprocess.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from conftest import FIXTURES

from telltale import cohorts, measures, report
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from conftest import Live, Replayed

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"
TRANSCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "sources"
    / "claude"
    / "2.1.257"
    / "transcript"
)

# Spec 13.7's six families, and how many metrics the table in cohorts.py gives them.
FAMILIES = 6
ENTRIES = 22

# The cohort the loop below builds, and the seed whose vector is read out of it. Ten is
# design 6.11's gate; seed 5 is the middle of the run and has captures on both sides.
SEEDS = range(1, 11)
READ_SEED = 5
MODEL = "sonnet"

# What a replayed capture is missing, and the two words that follow from it. Every
# fixture in this repository was recorded before the launcher existed.
NO_FINGERPRINT = "content level unknown"

# How many OTel log records `_bloat` posts into each of the ten captures, and the bound
# the scan has to stay under afterwards. A capture of the scripted agent holds about 25
# observations, so 500 makes the store roughly 20 times bigger in rows while leaving the
# number of captures, the cohort and every evidence row exactly as they were. Before
# W3-T0 the cohort scan read every one of those rows and the time went up with them;
# after it the scan reads two observations and one activity type per capture. The bound
# is 2.0 rather than 1.1 because this is wall clock on a shared machine: measured on
# macOS 25.6 with 225 rows growing to 5225, the ratio is 0.91 with the typed reads and
# 3.09 with the `types` argument removed from `cohorts._payloads`, so 2.0 sits between
# them with room on both sides.
BLOAT_RECORDS = 500
BLOAT_RATIO = 2.0


def _telltale() -> str:
    found = shutil.which("telltale")
    assert found is not None, "no `telltale` on PATH: run `uv sync` first"
    return found


def _cli(*args: str, home: Path, cwd: Path | None = None) -> str:
    """One `telltale` subcommand, in a named environment. Returns stdout.

    A named environment rather than the inherited one, for the reason
    test_experiments.py gives: the child must write into the home this test made, and
    HOME must be the temporary one so that nothing can reach the owner's files.
    """
    done = subprocess.run(
        [_telltale(), *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=None if cwd is None else str(cwd),
        env={
            "TELLTALE_HOME": str(home),
            "HOME": str(home.parent / "home"),
            "PATH": os.environ.get("PATH", ""),
        },
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def _repository(root: Path) -> None:
    """A git repository with one empty commit, which is what the launcher needs."""
    root.mkdir(parents=True, exist_ok=True)
    for args in (
        ("init", "-q", "."),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Telltale Test"),
        ("commit", "-q", "--allow-empty", "-m", "base"),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def cohort_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Ten captures of the scripted agent, in one repository, under one home.

    Module scope because the loop is ten real child processes. Every test that writes
    to this store copies it first, so the ten are built once and no test sees another
    test's edit. The fake HOME is listed before and after for the reason conftest's
    `telltale_home` fixture does it: a recorder that writes outside $TELLTALE_HOME has
    broken invariant 7, and nothing else in this file would notice.
    """
    root = tmp_path_factory.mktemp("cohort")
    home, repo, fake = root / "telltale-home", root / "repo", root / "home"
    home.mkdir()
    fake.mkdir()
    _repository(repo)
    before = sorted(str(path.relative_to(fake)) for path in fake.rglob("*"))
    for seed in SEEDS:
        _cli(
            "run", "--provider", "claude", "--",
            sys.executable, str(FAKE_AGENT),
            "--seed", str(seed), "--model", MODEL,
            "--output-format", "stream-json",
            home=home, cwd=repo,
        )  # fmt: skip
    # W2-T6 makes the launcher run the reducers at capture end. Until it lands this is
    # what writes the activities and the evidence the vector reads, and afterwards it
    # is a no-op that rewrites identical rows.
    _cli("rebuild", home=home)
    yield home
    assert before == sorted(str(path.relative_to(fake)) for path in fake.rglob("*"))


@pytest.fixture(scope="module")
def denied_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One capture of the scripted agent whose added Bash call was REFUSED.

    The cheapest reproduction of what W2-E05 spent five real sessions measuring: a
    headless agent asks to run `uv run pytest`, nobody is there to approve it, and the
    provider answers with a permission_denied system message and a tool_result carrying
    is_error true. No tokens, and the same stream lines.
    """
    root = tmp_path_factory.mktemp("denied")
    home, repo, fake = root / "telltale-home", root / "repo", root / "home"
    home.mkdir()
    fake.mkdir()
    _repository(repo)
    _cli(
        "run", "--provider", "claude", "--",
        sys.executable, str(FAKE_AGENT),
        "--seed", str(READ_SEED), "--model", MODEL,
        "--output-format", "stream-json", "--deny",
        home=home, cwd=repo,
    )  # fmt: skip
    _cli("rebuild", home=home)
    return home


@pytest.fixture
def copied(cohort_home: Path, tmp_path: Path) -> Path:
    """The ten-capture home, copied, for a test that purges or imports into it."""
    into = tmp_path / "telltale-home"
    shutil.copytree(cohort_home, into)
    (tmp_path / "home").mkdir(exist_ok=True)
    return into


def _store(home: Path) -> Store:
    """A reader over a store the CLI wrote. Read methods open their own connection."""
    return Store(home / "telltale.db")


def _captures(store: Store) -> list[str]:
    return sorted(str(row["capture_id"]) for row in store.captures())


def _cells(built: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    return [
        (family, name, cell)
        for family, metrics in built.items()
        for name, cell in metrics.items()
    ]


def _values(store: Store, captures: Sequence[str], metric: str) -> list[float | None]:
    out = []
    for capture in captures:
        rows = [row for row in store.evidence(capture) if row["metric"] == metric]
        out.append(measures.value_of(rows[0]) if rows else None)
    return out


def _midrank(values: Sequence[float | None], own: float) -> float:
    """The percentile the module under test should print, computed here from scratch.

    Written out rather than imported so that the test and the code are two statements
    of one rule. A test that called `cohorts.percentile` would agree with any rule.
    """
    seen = [value for value in values if value is not None]
    below = len([value for value in seen if value < own])
    equal = len([value for value in seen if value == own])
    return round(100.0 * (below + 0.5 * equal) / len(seen), 1)


def _row(printed: str, metric: str) -> list[str]:
    """One row of a printed table, by the metric in its second column."""
    for line in printed.splitlines():
        cells = line.split()
        if len(cells) > 1 and cells[1] == metric:
            return cells
    raise AssertionError(f"no row for {metric} in\n{printed}")


def _replayed(replay: Callable[..., Replayed], store: Store, scenario: str) -> Replayed:
    done = replay(scenario)
    store.rebuild(done.capture)
    return done


def _bloat(live: Live, captures: Sequence[str], records: int) -> None:
    """Make every capture much bigger, in recorded provider bytes, over the real route.

    One `/v1/logs` POST per capture, carrying E01's own S1 OTLP body with its
    logRecords list repeated up to `records`. Nothing is synthesised: these are the
    bytes Claude Code sent, and the only edit is how many of them there are, which is
    what a longer session would have changed too. Posting them through the receiver
    means the observations are parsed, sanitized and written by the code under test.

    The store is CLOSED at the end and not drained. conftest's `settled` says why: an
    empty queue is not a write barrier, and a caller that reads at that instant sees
    fewer rows than it posted. Measured here first: with `drain` alone this function
    delivered 50 of the 500 rows per capture.
    """
    line = (FIXTURES / "S1" / "otel_logs.jsonl").read_text(encoding="utf-8")
    body = json.loads(line.splitlines()[0])["body_json"]
    recorded = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    for capture in captures:
        grown = json.loads(json.dumps(body))
        block = grown["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        block.clear()
        while len(block) < records:
            block.extend(json.loads(json.dumps(recorded))[: records - len(block)])
        assert live.post("/v1/logs", json.dumps(grown).encode(), capture=capture) == 200
    live.store.close()


def _vector_seconds(store: Store, capture: str) -> float:
    """The best of three, because this is wall clock and the machine is shared."""
    best = None
    for _ in range(3):
        started = time.perf_counter()
        cohorts.vector(store, capture)
        taken = time.perf_counter() - started
        best = taken if best is None else min(best, taken)
    assert best is not None
    return best


# -- a capture with no fingerprint ----------------------------------------------------


@pytest.mark.integration
def test_a_replayed_capture_carries_no_fingerprint(
    replay: Callable[..., Replayed], store: Store, settled: Callable[[Store], Store]
) -> None:
    """The input really lacks the row, so "no cohort" is the answer and not a loss.

    The equivalent of the sqlite3 query in docs/log/W2-T4.md, run against the store the
    other tests here read. content_level lives in the telltale.environment payload and
    nowhere else, and a replay posts recorded provider bytes with no launcher behind
    them, so there is nothing for the cohort to key on.
    """
    capture = _replayed(replay, store, "S1").capture
    settled(store)

    kinds = {str(row["observation_type"]) for row in store.observations(capture)}

    assert "telltale.environment" not in kinds
    assert "telltale.capture_started" not in kinds
    assert cohorts.cohort_keys(store, capture)["content_level"] is None
    assert cohorts.cohort(store, capture) == []


@pytest.mark.integration
def test_the_vector_is_22_entries_read_back_out_of_the_evidence_table(
    replay: Callable[..., Replayed], store: Store, settled: Callable[[Store], Store]
) -> None:
    """Spec 13.7's six families, and not one number recomputed on the way out.

    Every cell is checked against the evidence row `telltale show` reads, because a
    vector that recomputed would be a second opinion about a capture, and two numbers
    for one metric is the failure the evidence table exists to prevent.
    """
    capture = _replayed(replay, store, "S1").capture
    settled(store)

    built = cohorts.vector(store, capture)

    rows = {str(row["metric"]): row for row in store.evidence(capture)}
    assert len(built) == FAMILIES
    assert len(_cells(built)) == ENTRIES
    for _family, name, cell in _cells(built):
        assert cell["value"] == measures.value_of(rows[name]), name
        assert cell["coverage"] == rows[name]["coverage"], name
        assert cell["evidence_id"] == rows[name]["evidence_id"], name
        assert cell["percentile"] == {"no_cohort": NO_FINGERPRINT}, name


@pytest.mark.integration
def test_a_row_with_unavailable_coverage_prints_a_dash_and_not_a_zero(
    replay: Callable[..., Replayed], store: Store, settled: Callable[[Store], Store]
) -> None:
    """Design invariant 5 in the one column where it would be invisible.

    S1 has no repository snapshot, so max_diff_lines is unavailable rather than 0. The
    printed cell has to say that, and the coverage column beside it is what separates
    "nothing changed" from "nobody looked".
    """
    capture = _replayed(replay, store, "S1").capture
    settled(store)

    built = cohorts.vector(store, capture)
    cell = built["edit_turnover"]["max_diff_lines"]
    printed = _row(report.vector(built), "max_diff_lines")

    assert cell["value"] is None
    assert cell["coverage"] == "unavailable"
    assert printed[2] == "-"
    assert printed[4] == "unavailable"


@pytest.mark.integration
def test_compare_shows_both_coverages_and_a_difference_only_where_both_exist(
    replay: Callable[..., Replayed], store: Store, settled: Callable[[Store], Store]
) -> None:
    """The two coverage columns on every row, and no difference invented for a null.

    S2 is the capture that changed nothing and still explored, so the pair carries both
    cases: numbers both captures measured, and numbers neither could.
    """
    first = _replayed(replay, store, "S1").capture
    second = _replayed(replay, store, "S2").capture
    settled(store)

    left, right = cohorts.sides(store, first, second)

    for family, name, cell in _cells(left["vector"]):
        other = right["vector"][family][name]
        difference = right["difference"][family][name]
        assert cell["coverage"] is not None, name
        assert other["coverage"] is not None, name
        if cell["value"] is None or other["value"] is None:
            assert difference is None, name
        else:
            assert difference["value"] == other["value"] - cell["value"], name
            assert difference["claim_class"] == "comparative", name
            assert difference["source"] == [cell["evidence_id"], other["evidence_id"]]
    # S2 changed no file and read one anyway, which is what makes it the second arm.
    assert right["vector"]["edit_turnover"]["unique_files_changed"]["value"] == 0
    assert right["vector"]["exploration_scope"]["unique_files_read"]["value"] == 1


@pytest.mark.integration
def test_the_printed_compare_row_carries_both_coverages(
    replay: Callable[..., Replayed], store: Store, settled: Callable[[Store], Store]
) -> None:
    """One printed row, cell by cell. The dict above is not what a reader sees."""
    first = _replayed(replay, store, "S1").capture
    second = _replayed(replay, store, "S2").capture
    settled(store)

    left, right = cohorts.sides(store, first, second)
    printed = _row(report.compare(left, right), "cache_read_tokens")

    # The columns are family, metric, a, b, a_coverage, b_coverage, then the difference
    # split over two cells by the space inside "-144111 (comparative)".
    assert printed[:6] == [
        "context_token_burden",
        "cache_read_tokens",
        "227790",
        "83679",
        "observed",
        "observed",
    ]
    assert printed[6:8] == ["-144111", "(comparative)"]
    assert printed[8] == "derived"


@pytest.mark.integration
def test_a_null_has_no_rank_and_is_never_counted_as_a_zero() -> None:
    """The rule that decides what a cohort's n_metric is, on the function that holds it.

    A pure function over a list of numbers, so it is asserted directly: the cohort the
    ten captures below make has the same coverage on every member, so no store in this
    repository can produce a cohort where one member measured a metric and another did
    not. The third line is what zero-filling would print, and it is here so that the
    difference between the two rules is a number in this file rather than a claim.
    """
    assert cohorts.percentile([1.0, None, 3.0], 3.0) == 75.0
    assert cohorts.percentile([1.0, 3.0], 3.0) == 75.0
    assert cohorts.percentile([1.0, 0.0, 3.0], 3.0) == 83.3
    with pytest.raises(ValueError, match="at least one measured value"):
        cohorts.percentile([None, None], 1.0)


# -- a cohort of ten ------------------------------------------------------------------


@pytest.mark.integration
def test_ten_captures_of_one_agent_are_one_cohort(cohort_home: Path) -> None:
    """Design 6.11's four keys, measured on ten real captures of one scripted agent.

    runtime_major is "fake-agent" and not a number: the launcher probes `--version` for
    a known agent binary only, so this child's runtime version is the one its own init
    message states, and a version that does not start with a digit has no major.
    """
    store = _store(cohort_home)
    captures = _captures(store)

    keys = [cohorts.cohort_keys(store, capture) for capture in captures]

    assert len(captures) == len(SEEDS)
    assert all(key["reason"] is None for key in keys)
    assert {key["provider"] for key in keys} == {"claude"}
    assert {key["runtime_major"] for key in keys} == {"fake-agent"}
    assert {key["model"] for key in keys} == {MODEL}
    assert {key["content_level"] for key in keys} == {1}
    assert cohorts.cohort(store, captures[READ_SEED - 1]) == captures


@pytest.mark.integration
def test_the_percentile_is_the_midrank_this_test_computes_by_hand(
    cohort_home: Path,
) -> None:
    """The number in the column, against the same number worked out from the ten rows.

    fresh_input_tokens because it is the one metric the seed moves on every capture:
    the fake agent's input count is a function of the seed, so the ten values are ten
    distinct numbers and a rank among them is not a statement about ties.
    """
    store = _store(cohort_home)
    captures = _captures(store)
    mine = captures[READ_SEED - 1]

    built = cohorts.vector(store, mine)

    values = _values(store, captures, "fresh_input_tokens")
    cell = built["context_token_burden"]["fresh_input_tokens"]
    assert cell["value"] is not None
    assert cell["percentile"]["value"] == _midrank(values, cell["value"])
    assert cell["percentile"]["n"] == len(SEEDS)
    assert cell["percentile"]["n_metric"] == len(SEEDS)
    assert cell["percentile"]["cohort"]["captures"] == captures


@pytest.mark.integration
def test_every_percentile_is_a_comparative_evidence_with_sources(
    cohort_home: Path,
) -> None:
    """Not a bare float anywhere: every rank is an Evidence, with the rows it read.

    The two compactions rows are the exception spec 13.7 names, and they say so in
    words rather than by leaving the column blank.
    """
    store = _store(cohort_home)
    mine = _captures(store)[READ_SEED - 1]

    built = cohorts.vector(store, mine)

    ranked = 0
    for family, name, cell in _cells(built):
        found = cell["percentile"]
        if family == "compactions":
            assert found == {"no_cohort": cohorts.NO_PERCENTILE}, name
            continue
        if cell["value"] is None:
            assert found == {"no_cohort": "value unknown"}, name
            continue
        ranked += 1
        assert found["claim_class"] == "comparative", name
        assert found["source"], name
        assert len(found["source"]) == len(SEEDS), name
        assert found["unit"] == "percentile", name
        assert found["assumptions"], name
    assert ranked


@pytest.mark.integration
def test_a_percentile_is_not_written_to_the_store(cohort_home: Path) -> None:
    """The whole reason cohorts.py returns them instead of storing them.

    Every evidence row in the database is derived and belongs to one capture. A
    comparative row here would be a comparison frozen on the day it was printed.
    """
    store = _store(cohort_home)
    mine = _captures(store)[READ_SEED - 1]
    before = len(store.evidence(mine))

    cohorts.vector(store, mine)
    cohorts.sides(store, mine, _captures(store)[0])

    rows = [row for capture in _captures(store) for row in store.evidence(capture)]
    assert len(store.evidence(mine)) == before
    assert {str(row["claim_class"]) for row in rows} == {"derived"}
    assert not [row for row in rows if str(row["metric"]).endswith("_percentile")]


@pytest.mark.integration
def test_nine_captures_are_below_the_gate(copied: Path) -> None:
    """One capture short, and the column says the count rather than ranking anyway.

    Nine is the case the gate exists for. A rank among nine is arithmetically fine and
    design 6.11 refuses it, because the size of the comparison group is what decides
    whether a rank means anything.
    """
    store = _store(copied)
    captures = _captures(store)
    _cli("purge", captures[-1], home=copied)

    built = cohorts.vector(_store(copied), captures[READ_SEED - 1])

    assert len(_captures(_store(copied))) == len(SEEDS) - 1
    for _family, name, cell in _cells(built):
        assert cell["percentile"] == {"no_cohort": len(SEEDS) - 1}, name
    printed = _cli("vector", captures[READ_SEED - 1], home=copied)
    assert "no cohort (n=9)" in printed


@pytest.mark.integration
def test_a_second_model_is_a_second_cohort(copied: Path) -> None:
    """The model key, measured rather than asserted: one flag, two cohorts.

    The eleventh capture differs from the other ten in `--model` alone, which the
    launcher reads out of argv into the environment fingerprint. It is in no cohort of
    its own and it does not join theirs.
    """
    store = _store(copied)
    sonnets = _captures(store)
    repo = copied.parent / "repo"
    _repository(repo)
    _cli(
        "run", "--provider", "claude", "--",
        sys.executable, str(FAKE_AGENT),
        "--seed", str(READ_SEED), "--model", "opus",
        "--output-format", "stream-json",
        home=copied, cwd=repo,
    )  # fmt: skip
    _cli("rebuild", home=copied)

    fresh = _store(copied)
    opus = [one for one in _captures(fresh) if one not in sonnets]

    assert len(opus) == 1
    assert cohorts.cohort_keys(fresh, opus[0])["model"] == "opus"
    assert cohorts.cohort(fresh, opus[0]) == opus
    assert cohorts.cohort(fresh, sonnets[READ_SEED - 1]) == sonnets


@pytest.mark.integration
def test_an_imported_capture_is_outside_every_cohort_with_or_without_the_flag(
    copied: Path,
) -> None:
    """--include-backfill admits imported captures, and this one still has no cohort.

    Not a contradiction: the flag decides whether a backfill capture may be a member,
    and the four keys decide whether it has a cohort at all. An imported session was
    read off a file the provider wrote, so no launcher recorded a content level for it
    and none of its numbers can be ranked. The flag is exercised on both sides of that.
    """
    _cli("import", "claude-transcripts", "--root", str(TRANSCRIPTS), home=copied)
    _cli("rebuild", home=copied)
    store = _store(copied)
    imported = [one for one in _captures(store) if one.startswith("imp_")]
    ten = [one for one in _captures(store) if one.startswith("cap_")]

    assert imported
    for capture in imported:
        keys = cohorts.cohort_keys(store, capture)
        assert keys["backfill"] is True
        assert NO_FINGERPRINT in keys["reason"]
        assert cohorts.cohort(store, capture) == []
        assert cohorts.cohort(store, capture, include_backfill=True) == []
    # The ten are unaffected either way: an imported capture cannot join a cohort it
    # has no key for, so the flag changes nothing here rather than changing n.
    assert cohorts.cohort(store, ten[READ_SEED - 1]) == ten
    assert cohorts.cohort(store, ten[READ_SEED - 1], include_backfill=True) == ten


@pytest.mark.integration
def test_the_cohort_scan_does_not_slow_down_when_the_captures_get_bigger(
    cohort_home: Path, tmp_path: Path, receiver: Callable[..., Live]
) -> None:
    """Design 6.5 and W3-T0: a read scales with the capture, not with the store.

    The cohort has to look at every capture that could share the four keys, so the scan
    is linear in the NUMBER of captures and design 6.11 makes it so. What it must not be
    is linear in how BIG they are, and it was: `cohort_keys` read each capture's whole
    observation list and whole activity list to find four fields. On the owner's
    3200-capture store that made `telltale vector` take 11.13 s.

    Here the ten captures are grown about twentyfold in rows and nothing else about
    them changes: same ten captures, same four keys, same cohort of ten, same evidence.
    If the scan still read whole captures the time would go up with the rows. Break it
    by dropping the `types` argument in `cohorts._payloads` and this fails; measured,
    the ratio goes from 0.91 to 3.09 while the bound stays 2.0.
    """
    # Its own copy, not the `copied` fixture: `receiver` brings the temporary HOME with
    # it, and both would make the same directory.
    into = tmp_path / "grown-home"
    shutil.copytree(cohort_home, into)
    store = _store(into)
    captures = _captures(store)
    mine = captures[READ_SEED - 1]
    before_rows = sum(len(store.observations(one)) for one in captures)
    before = _vector_seconds(store, mine)

    _bloat(receiver(target=Store(into / "telltale.db").open()), captures, BLOAT_RECORDS)

    grown = _store(into)
    after_rows = sum(len(grown.observations(one)) for one in captures)
    after = _vector_seconds(grown, mine)
    assert after_rows > before_rows * 10, (before_rows, after_rows)
    assert _captures(grown) == captures
    assert cohorts.cohort(grown, mine) == captures
    assert after < before * BLOAT_RATIO, (before, after, before_rows, after_rows)


@pytest.mark.integration
def test_a_refused_call_is_not_a_test_run(denied_home: Path) -> None:
    """W2-E05 finding 1, on a capture that costs nothing. Design 6.10 and 6.11.

    The refused call is still a row and still classified: the agent DID ask to run a
    test, and dropping that would lose the fact. What it is not is a verification_run,
    because nothing ran. Before this, the denial arrived as a tool_result with
    `is_error` true, `_tool_outcome` read that field, and the capture reported one
    failed test run for a test that never started. All five W2-E05 pilot captures said
    agent_test_runs 3 and failed_test_runs 3 with no test ever executed.

    Break it by deleting the `denied is not None` branch in
    `activities_tools._tool_outcome` and the four numbers below go to 1, 1, 1 and 0.
    """
    store = _store(denied_home)
    capture = _captures(store)[0]
    rows = [dict(row["fields"]) for row in store.activities(capture)]
    refused = [row for row in rows if row.get("executed") is False]
    summary = measures.summary(store, capture)

    assert len(refused) == 1
    assert refused[0]["outcome"] == "refused"
    assert refused[0]["tool_name"] == "Bash"
    # Still classified. "0 test runs and 1 refused call" needs both halves to be said.
    assert refused[0]["category"] == "test"
    assert [row["activity_type"] for row in store.activities(capture)].count(
        "verification_run"
    ) == 0
    assert summary["verification"]["agent_test_runs"] == 0
    assert summary["verification"]["failed_test_runs"] == 0
    assert summary["work"]["refused_tool_calls"] == 1


@pytest.mark.integration
def test_the_refused_count_is_unavailable_where_no_surface_states_one(
    replay: Callable[..., Replayed], store: Store, settled: Callable[[Store], Store]
) -> None:
    """Absence is not zero, on the newest number in the summary.

    A Codex capture delivers a surface called `stream` too, and nothing has ever
    measured a Codex permission denial on it. So `refused_tool_calls` is null with a
    warning naming the gap, rather than 0, which would say the session was refused
    nothing.
    """
    replayed = replay("S1", provider="codex")
    store.rebuild(replayed.capture)
    settled(store)

    summary = measures.summary(store, replayed.capture)

    assert summary["work"]["refused_tool_calls"] is None
    assert any(
        "states whether a tool call was refused" in note
        for note in summary["warnings"]["refused_tool_calls"]
    )


@pytest.mark.integration
def test_a_stream_only_capture_reports_its_cache_counters(cohort_home: Path) -> None:
    """W2-E05 finding 2: two of the four token counters were null for no good reason.

    The scripted agent is stream-only, so its requests carry the counters under the
    stream's spellings (`cache_read_input_tokens`, `cache_creation_input_tokens`) and
    the reducer read only the OTel ones. The numbers were in the capture the whole time.
    The coverage word is `observed`, not a weaker one: spec 9.1 gives request_usage
    `observed` on the stream surface, and this capture delivered that surface, so the
    counter is as well seen as the two that were never broken.
    """
    store = _store(cohort_home)
    mine = _captures(store)[READ_SEED - 1]

    rows = {str(row["metric"]): row for row in store.evidence(mine)}

    for name in ("cache_read_tokens", "cache_creation_tokens"):
        assert measures.value_of(rows[name]) is not None, name
        assert rows[name]["coverage"] == "observed", name
    # Not a tautology: the fake agent emits a non-zero cache read on every turn but the
    # first, so a reducer that read the wrong key would report null and not 0.
    read = measures.value_of(rows["cache_read_tokens"])
    assert read is not None
    assert read > 0


@pytest.mark.integration
def test_the_printed_vector_names_the_cohort_and_its_size(cohort_home: Path) -> None:
    """What a reader sees: the four keys above the table, and a rank in the column."""
    mine = _captures(_store(cohort_home))[READ_SEED - 1]

    printed = _cli("vector", mine, home=cohort_home)

    assert "cohort: provider=claude, runtime_major=fake-agent" in printed
    assert f"model={MODEL}, content_level=1, n={len(SEEDS)}" in printed
    assert "(comparative)" in _row(printed, "fresh_input_tokens")


@pytest.mark.integration
def test_the_json_output_says_null_and_never_zero(cohort_home: Path) -> None:
    """Unknown stays None all the way out. The one place it would become 0 quietly."""
    mine = _captures(_store(cohort_home))[READ_SEED - 1]
    store = _store(cohort_home)
    unknown = [
        name
        for _family, name, cell in _cells(cohorts.vector(store, mine))
        if cell["value"] is None
    ]

    printed = _cli("vector", mine, "--json", home=cohort_home)

    assert unknown, "this capture measured everything, so the test proves nothing"
    for name in unknown:
        assert f'"{name}"' in printed
    assert '"value": null' in printed
