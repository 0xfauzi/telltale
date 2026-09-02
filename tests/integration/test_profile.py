"""Spec 16's repository work profile, over captures a real launcher really made.

One repository, seventeen captures of `fake_agent.py` through `telltale run`, and the
whole point of the arrangement is the cohort gate. Twelve captures edit under `src/`
and two under `docs/`, all with `--model sonnet`; three edit under `experiments/` with
`--model opus`. Design 6.11's cohort is the four keys and nothing about a path, so the
fourteen sonnet captures are one cohort and the three opus ones are another, while the
subsystem grouping cuts across both. That gives all three answers the ratio column can
have in one store: `docs` has twelve cohort members outside it and gets a ratio, `src`
has two outside it and says so, and `experiments` is its whole cohort and says that.

Seventeen real child processes, once for the module. Nothing is stubbed: the captures
come out of the launcher, the numbers come out of the evidence table the reducer wrote,
and the printed rows come out of the installed `telltale` console script.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any

import pytest

from telltale import profile, report_profile
from telltale.measures import value_of
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"

# The arrangement, as (subsystem, model, how many). The numbers are design 6.11's gate
# read backwards: `docs` needs at least COHORT_MIN sonnet captures outside it, so the
# sonnet cohort is 14 and `docs` holds 2 of them.
ARRANGEMENT = (("src", "sonnet", 12), ("docs", "sonnet", 2), ("experiments", "opus", 3))
SONNET = 14
OPUS = 3

# A metric every capture of the scripted agent measures, and one no capture of it can:
# the fake agent never compacts, and nothing on its surface states a pre-compaction
# token count. The second is where "unknown stays unknown" is checked.
MEASURED = "model_requests"
UNMEASURED = "pre_compaction_tokens"


def _telltale() -> str:
    found = shutil.which("telltale")
    assert found is not None, "no `telltale` on PATH: run `uv sync` first"
    return found


def _cli(*args: str, home: Path, cwd: Path | None = None) -> str:
    """One `telltale` subcommand in a named environment. Returns stdout.

    The environment is named rather than inherited for the reason test_compare.py gives:
    the child writes into the home this test made, and HOME is the temporary one so that
    nothing here can reach the owner's files.
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
    assert done.returncode == 0, done.stderr or done.stdout
    return done.stdout


def _repository(root: Path) -> None:
    """A git repository with one empty commit and the directories the runs edit."""
    root.mkdir(parents=True, exist_ok=True)
    for args in (
        ("init", "-q", "."),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Telltale Test"),
        ("commit", "-q", "--allow-empty", "-m", "base"),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    for name, _model, _count in ARRANGEMENT:
        (root / name).mkdir()


@pytest.fixture(scope="module")
def profiled(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, str]]:
    """The seventeen captures, the home they live in, and the repository they are of.

    Module scope because this is seventeen child processes. Every test here reads; none
    writes, so the store is built once. The fake HOME is listed before and after, as
    conftest's `telltale_home` does it: a recorder that writes outside $TELLTALE_HOME
    has broken invariant 7 and nothing else in this file would notice.
    """
    root = tmp_path_factory.mktemp("profile")
    home, repo, fake = root / "telltale-home", root / "repo", root / "home"
    home.mkdir()
    fake.mkdir()
    _repository(repo)
    seed = 0
    for name, model, count in ARRANGEMENT:
        for _ in range(count):
            seed += 1
            _cli(
                "run", "--provider", "claude", "--",
                sys.executable, str(FAKE_AGENT),
                "--seed", str(seed), "--model", model,
                "--target", f"{name}/answer.txt",
                "--output-format", "stream-json",
                home=home, cwd=repo,
            )  # fmt: skip
    _cli("rebuild", home=home)
    before = sorted(str(path.relative_to(fake)) for path in fake.rglob("*"))
    yield home, _repo_id(home)
    assert before == sorted(str(path.relative_to(fake)) for path in fake.rglob("*"))


def _repo_id(home: Path) -> str:
    ids = {str(row["repo_id"]) for row in Store(home / "telltale.db").captures()}
    assert len(ids) == 1, f"the runs should be of one repository, not {ids}"
    return ids.pop()


def _built(profiled: tuple[Path, str], **kwargs: Any) -> dict[str, Any]:
    home, repo_id = profiled
    return profile.build(Store(home / "telltale.db"), repo_id, **kwargs)


def _group(built: dict[str, Any], name: str) -> dict[str, Any]:
    found: list[dict[str, Any]] = [
        one for one in built["groups"] if one["group"] == name
    ]
    assert found, f"no group {name} in {[one['group'] for one in built['groups']]}"
    return found[0]


def _cell(group: dict[str, Any], metric: str) -> dict[str, Any]:
    found: list[dict[str, Any]] = [
        one for one in group["rows"] if one["metric"] == f"{metric}_group_median"
    ]
    assert len(found) == 1, f"{metric} appears {len(found)} times in the group"
    return found[0]


def _values(home: Path, captures: Sequence[str], metric: str) -> list[float]:
    """The metric's values, read out of the evidence table by hand.

    Written out rather than taken from `profile`, so the test and the code are two
    statements of one rule: a test that reused the module's own reader would agree with
    any reader.
    """
    store = Store(home / "telltale.db")
    out = []
    for capture in captures:
        rows = [row for row in store.evidence(capture) if row["metric"] == metric]
        found = value_of(rows[0]) if rows else None
        if found is not None:
            out.append(found)
    return out


def _row(printed: str, table: str, group: str) -> list[str]:
    """One row of one metric table, by the heading above it and the group column."""
    blocks = printed.split("\n\n")
    for block in blocks:
        lines = block.splitlines()
        if lines and lines[0].strip() == table:
            for line in lines:
                if line.split(maxsplit=1)[:1] == [group]:
                    return line.split()
    raise AssertionError(f"no {group} row under {table} in\n{printed}")


# -- the groupings --------------------------------------------------------------------


@pytest.mark.integration
def test_the_default_grouping_is_the_iso_week(profiled: tuple[Path, str]) -> None:
    """Every capture is placed, in the ISO week its first observation arrived in."""
    home, _repo_id = profiled
    built = _built(profiled)

    assert built["by"] == "week"
    assert built["captures"] == SONNET + OPUS
    assert built["grouped"] == SONNET + OPUS
    assert built["skipped"] == {}
    names = [group["group"] for group in built["groups"]]
    assert all(re.fullmatch(r"\d{4}-W\d{2}", name) for name in names), names
    weeks = {
        datetime.fromisoformat(str(row["first_ts"])).isocalendar()
        for row in Store(home / "telltale.db").captures()
    }
    assert set(names) == {f"{found.year:04d}-W{found.week:02d}" for found in weeks}


@pytest.mark.integration
def test_a_subsystem_is_the_top_level_directory_a_capture_edited(
    profiled: tuple[Path, str],
) -> None:
    """The three groups are the three directories, with the counts the runs made.

    From `file_edit` activities and nothing else: no git call, no path list on a commit,
    and no read of the working tree at profile time.
    """
    built = _built(profiled, by="subsystem")

    assert {
        group["group"]: group["composition"]["captures"] for group in built["groups"]
    } == {name: count for name, _model, count in ARRANGEMENT}
    assert built["grouped"] == SONNET + OPUS


@pytest.mark.integration
def test_a_path_prefix_matches_components_and_names_the_group(
    profiled: tuple[Path, str],
) -> None:
    """`--path src` is the twelve captures that edited under it, in one group named it.

    `--path s` selects none: a prefix is a place in the tree, so `s` does not hold
    `src/answer.txt`, and the profile says so rather than answering with a group of 12.
    """
    built = _built(profiled, path="src")
    assert built["by"] == "path"
    assert [group["group"] for group in built["groups"]] == ["src"]
    assert built["grouped"] == 12

    narrow = _built(profiled, path="s")
    assert narrow["groups"] == []
    assert narrow["skipped"] == {profile.NOTHING_UNDER: SONNET + OPUS}


@pytest.mark.integration
def test_a_path_and_a_by_are_a_filter_and_a_grouping(
    profiled: tuple[Path, str],
) -> None:
    """`--path src --by week` is the sessions that touched src, week by week."""
    built = _built(profiled, path="src", by="week")

    assert built["by"] == "week"
    assert built["grouped"] == 12
    assert sum(group["composition"]["captures"] for group in built["groups"]) == 12


@pytest.mark.integration
def test_by_path_without_a_prefix_is_refused(profiled: tuple[Path, str]) -> None:
    with pytest.raises(profile.Refused, match="needs --path"):
        _built(profiled, by="path")


@pytest.mark.integration
def test_an_unknown_repo_id_is_refused_and_names_what_is_stored(
    profiled: tuple[Path, str],
) -> None:
    """A repo_id nobody stored is not an empty profile. Two different answers."""
    home, repo_id = profiled
    with pytest.raises(profile.Refused, match="no captures of this repository"):
        profile.build(Store(home / "telltale.db"), "not-a-repo-id")
    assert repo_id in profile._stored(Store(home / "telltale.db").captures())


# -- the cohort gate ------------------------------------------------------------------


@pytest.mark.integration
def test_a_group_with_ten_cohort_members_outside_it_gets_a_ratio(
    profiled: tuple[Path, str],
) -> None:
    """docs: 2 captures, 12 sonnet captures outside it, so spec 16's sentence stands.

    The ratio is recomputed here from the evidence rows, so the test states the rule
    rather than agreeing with the module.
    """
    home, _repo_id = profiled
    built = _built(profiled, by="subsystem")
    group = _group(built, "docs")
    cell = _cell(group, MEASURED)["ratio"]

    assert group["cohort"]["n"] == SONNET
    assert group["cohort"]["outside"] == SONNET - 2
    assert cell["claim_class"] == "comparative"
    assert cell["unit"] == "ratio"
    # The cohort's member ids sit on the ratio Evidence and not on the published frame,
    # which is 12 ULIDs a composition table has no room for.
    mine = _values(home, cell["cohort"]["captures"], MEASURED)
    assert len(mine) == SONNET - 2
    assert cell["value"] == round(_cell(group, MEASURED)["value"] / median(mine), 3)
    assert cell["cohort"]["model"] == "sonnet"
    assert cell["cohort"]["n_metric"] == SONNET - 2
    assert "captures" not in group["cohort"]


@pytest.mark.integration
def test_a_cohort_with_fewer_than_ten_outside_the_group_is_refused(
    profiled: tuple[Path, str],
) -> None:
    """The gate, and the test to break: src has 2 outside it and experiments has 0.

    Design 6.11's ten is a GATE and not a statistic. A group placed against two other
    captures is placed against those two, and the cell says the two counts that decide
    it: the cohort, and how much of it is outside the group.
    """
    built = _built(profiled, by="subsystem")

    assert _cell(_group(built, "src"), MEASURED)["ratio"]["no_ratio"] == (
        f"no cohort (n={SONNET}, {SONNET - 12} outside this group)"
    )
    assert _cell(_group(built, "experiments"), MEASURED)["ratio"]["no_ratio"] == (
        f"no cohort (n={OPUS}, 0 outside this group)"
    )
    for name in ("src", "experiments"):
        for row in _group(built, name)["rows"]:
            assert "value" not in row["ratio"], f"{name} was ranked against too few"


@pytest.mark.integration
def test_a_group_that_spans_two_cohorts_is_not_one_comparison(
    profiled: tuple[Path, str],
) -> None:
    """The week group holds both models, so no single cohort matches all of it."""
    built = _built(profiled)
    group = built["groups"][0]

    assert group["cohort"]["no_ratio"] == "the group spans 2 cohorts"
    assert _cell(group, MEASURED)["ratio"]["no_ratio"] == "the group spans 2 cohorts"


@pytest.mark.integration
def test_nothing_is_written(profiled: tuple[Path, str]) -> None:
    """A profile is a statement about the store, so it may not become a row in it.

    The same rule cohorts.py keeps for a percentile, checked the same way: the evidence
    table is counted before and after, and the ids the profile hands back are not in it.
    """
    home, _repo_id = profiled
    connection = sqlite3.connect(f"file:{home / 'telltale.db'}?mode=ro", uri=True)
    before = connection.execute("SELECT count(*) FROM evidence").fetchone()[0]
    built = _built(profiled, by="subsystem")
    after = connection.execute("SELECT count(*) FROM evidence").fetchone()[0]
    ids = [row["evidence_id"] for row in _group(built, "docs")["rows"]]
    found = connection.execute(
        "SELECT count(*) FROM evidence WHERE evidence_id IN"
        " (SELECT value FROM json_each(?))",
        (json.dumps(ids),),
    ).fetchone()[0]
    connection.close()

    assert before == after
    assert found == 0


# -- unknown stays unknown ------------------------------------------------------------


@pytest.mark.integration
def test_a_metric_no_capture_measured_is_none_and_never_zero(
    profiled: tuple[Path, str],
) -> None:
    """No value, no spread, no ratio, and the coverage word beside the hole."""
    built = _built(profiled, by="subsystem")
    cell = _cell(_group(built, "docs"), UNMEASURED)

    assert cell["value"] is None
    assert cell["n"] == 0
    assert cell["unknown"] == 2
    assert (cell["mad_scaled"], cell["min"], cell["max"]) == (None, None, None)
    assert cell["ratio"]["no_ratio"] == "no capture in this group measured it"
    printed = report_profile.render(built)
    row = _row(printed, f"compactions / {UNMEASURED}", "docs")
    assert row[1:7] == ["0", "2", "-", "-", "-", "-"]


@pytest.mark.integration
def test_the_group_median_carries_the_rows_it_was_taken_over(
    profiled: tuple[Path, str],
) -> None:
    """Design invariant 4's provenance, on a number that is never stored.

    Every source id is the evidence row of a capture in the group, and there are as many
    of them as there are captures: a median over 12 values that named 3 sources would be
    a number a reader cannot check.
    """
    home, _repo_id = profiled
    built = _built(profiled, by="subsystem")
    cell = _cell(_group(built, "src"), MEASURED)
    store = Store(home / "telltale.db")

    assert cell["claim_class"] == "comparative"
    assert len(cell["source"]) == 12
    ids = {
        str(row["evidence_id"])
        for capture in cell["cohort"]["captures"]
        for row in store.evidence(capture)
        if row["metric"] == MEASURED
    }
    assert set(cell["source"]) == ids
    assert cell["value"] == median(_values(home, cell["cohort"]["captures"], MEASURED))


# -- imported captures ----------------------------------------------------------------


@pytest.mark.integration
def test_imported_captures_are_excluded_and_then_grouped_on_their_own(
    tmp_path: Path,
) -> None:
    """Design 6.11: an imported capture is outside every cohort, so it groups alone.

    A repository with both kinds of capture in it, made the only way that exists: the
    launcher for the live ones, and `telltale import` over the transcript fixtures for
    the backfill ones, with both pointed at one git repository. `materialise` comes from
    test_import.py rather than being copied, because it is the substitution that makes a
    recorded transcript refer to THIS machine's paths and two copies of it would drift.
    """
    from test_import import materialise

    root, repo, _fixture_home = materialise("claude-transcripts", tmp_path)
    home = tmp_path / "telltale-home"
    home.mkdir()
    (tmp_path / "home").mkdir(exist_ok=True)
    (repo / "src").mkdir(exist_ok=True)
    for seed in (1, 2):
        _cli(
            "run", "--provider", "claude", "--",
            sys.executable, str(FAKE_AGENT),
            "--seed", str(seed), "--model", "sonnet",
            "--target", "src/answer.txt",
            "--output-format", "stream-json",
            home=home, cwd=repo,
        )  # fmt: skip
    _cli("import", "claude-transcripts", "--root", str(root), home=home)
    _cli("rebuild", home=home)
    store = Store(home / "telltale.db")
    repo_id = _one_repo_with_both(store)

    excluded = profile.build(store, repo_id)
    admitted = profile.build(store, repo_id, include_backfill=True)

    assert excluded["skipped"][profile.EXCLUDED] > 0
    assert all(profile.BACKFILL not in one["group"] for one in excluded["groups"])
    alone = [one for one in admitted["groups"] if profile.BACKFILL in one["group"]]
    assert alone, [one["group"] for one in admitted["groups"]]
    for group in alone:
        assert group["composition"]["imported"] == group["composition"]["captures"]
        assert "no_ratio" in group["cohort"]
    for group in admitted["groups"]:
        if profile.BACKFILL not in group["group"]:
            assert group["composition"]["imported"] == 0


def _one_repo_with_both(store: Store) -> str:
    """The repo_id both the launched captures and the imported ones carry."""
    counts: dict[str, int] = {}
    for row in store.captures():
        counts[str(row["repo_id"])] = counts.get(str(row["repo_id"]), 0) + 1
    found = max(counts, key=lambda one: counts[one])
    assert counts[found] > 2, f"the import did not land in the launched repo: {counts}"
    return found


# -- the word refusal (spec 16, spec 17.2) --------------------------------------------


@pytest.mark.integration
def test_a_rendered_string_holding_one_of_the_four_words_raises() -> None:
    for word in ("maintainability 0.17", "a latent SCORE", "difficulty", "qualities"):
        with pytest.raises(report_profile.ForbiddenWord, match="may not use"):
            report_profile.refuse_words(f"payments has {word}")
    assert report_profile.refuse_words("a natural history") == "a natural history"


@pytest.mark.integration
def test_a_group_named_after_a_refused_word_cannot_be_printed(
    profiled: tuple[Path, str],
) -> None:
    """The check reads the DATA, not only the headings.

    A repository with a top-level directory called `quality` refuses to render by
    subsystem, and the refusal says so. That is the intended trade, stated in
    report_profile.py: a check over the headings alone would pass a table whose group
    column carried the very word the rule exists to keep out.
    """
    built = _built(profiled, by="subsystem")
    built["groups"][0]["group"] = "quality"

    with pytest.raises(report_profile.ForbiddenWord, match="quality"):
        report_profile.render(built)


@pytest.mark.integration
def test_the_printed_profile_holds_none_of_the_four_words(
    profiled: tuple[Path, str],
) -> None:
    """The byte scan, over the command as an owner runs it."""
    home, repo_id = profiled
    for extra in ([], ["--by", "subsystem"], ["--path", "src"], ["--include-backfill"]):
        printed = _cli("profile", repo_id, *extra, home=home).lower()
        for word in report_profile.REFUSED_WORDS:
            assert word not in printed, f"{word} in `telltale profile {extra}`"


# -- the command ----------------------------------------------------------------------


@pytest.mark.integration
def test_the_command_prints_one_table_per_metric_with_the_cohort_column(
    profiled: tuple[Path, str],
) -> None:
    """What an owner sees: composition, 22 tables, and the ratio where it qualifies."""
    home, repo_id = profiled
    printed = _cli("profile", repo_id, "--by", "subsystem", home=home)

    assert "SAMPLE COMPOSITION" in printed
    assert printed.count(" / ") >= 22
    docs = _row(printed, f"context_token_burden / {MEASURED}", "docs")
    assert docs[-1].endswith(f"n={SONNET - 2})")
    assert "comparative" in docs
    src = _row(printed, f"context_token_burden / {MEASURED}", "src")
    assert " ".join(src).endswith(
        f"no cohort (n={SONNET}, {SONNET - 12} outside this group)"
    )


@pytest.mark.integration
def test_the_json_form_carries_the_evidence_the_table_prints(
    profiled: tuple[Path, str],
) -> None:
    home, repo_id = profiled
    built = json.loads(
        _cli("profile", repo_id, "--by", "subsystem", "--json", home=home)
    )

    assert built["by"] == "subsystem"
    docs = next(one for one in built["groups"] if one["group"] == "docs")
    cell = next(
        one for one in docs["rows"] if one["metric"] == f"{MEASURED}_group_median"
    )
    assert cell["claim_class"] == "comparative"
    assert cell["ratio"]["claim_class"] == "comparative"
    assert cell["ratio"]["cohort"]["n"] == SONNET
