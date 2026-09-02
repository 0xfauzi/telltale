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

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from telltale import cohorts, measures, report
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from conftest import Replayed

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
