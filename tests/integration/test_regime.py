"""Six real attempts, one policy intervention, and the regimes it cuts them into.

Nothing here writes an observation by hand. Every attempt is a real `telltale run` of
the fake agent in a real git repository, every outcome is a real `telltale outcome`,
and the intervention is a real `telltale intervention`: the boundary this file measures
is the one the shipped commands produce.

The one thing that is not a compiled lineage is the series the POOLED run is measured
on. There is no attempt-clock target in the registry and the six real attempts are
short of the change clock's c_min of 16, so a run that pooled across the boundary would
refuse on window count before it reached a forecaster. `_pooled_series` writes
synthetic_series' 60-row change-clock shape instead and puts the REAL boundary on it:
`series_regime.marks` reads the observation the CLI wrote, and `segment` is the same
function series_lineage calls. What is synthetic there is the rows, which is what
synthetic_series.py exists to say.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import synthetic_series

from telltale import config, repo, series, series_regime
from telltale.forecast import regime as pooling
from telltale.store import Store

if TYPE_CHECKING:
    from telltale.model import Series

pytestmark = pytest.mark.integration

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"

# Two task ids and three attempts each, interleaved, as test_series_lineage.py runs
# them: six captures whose order is the order they RAN. The intervention lands between
# the third and the fourth, so `pre` and `post` are three rows each and neither is the
# whole lineage.
ATTEMPTS = (("T-alpha", 1), ("T-beta", 1), ("T-alpha", 2), ("T-beta", 2),
            ("T-alpha", 3), ("T-beta", 3))  # fmt: skip
BOUNDARY = 3

ADVISORY = "adv_w6t2_test"
CHILD = ("python", str(FAKE_AGENT), "--output-format", "stream-json")

# The pooled run: the change clock's shortest target, two baselines and the stub,
# which need no extra and no checkpoint. `echo` is the non-baseline the decision rule
# is about. 60 rows is what c_min 16 and a horizon of 1 can score.
POOLED_TARGET = "attempts_to_land"
POOLED_ROWS = 60
BASELINES = "persistence,rolling_median,echo"
MODEL = "echo"


def _telltale() -> str:
    executable = shutil.which("telltale")
    assert executable is not None, "no `telltale` on PATH: run `uv sync` first"
    return executable


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, timeout=30, check=True
    )
    return done.stdout.decode().strip()


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_telltale(), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _ok(cwd: Path, *args: str) -> str:
    done = _run(cwd, *args)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def _repository(root: Path) -> Path:
    """A repository with one commit and an identity that is not the operator's."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", ".")
    for name, value in (
        ("user.email", "telltale-test@example.invalid"),
        ("user.name", "Telltale Test"),
        ("commit.gpgsign", "false"),
    ):
        _git(root, "config", name, value)
    (root / "f").write_text("base\n", encoding="utf-8")
    _git(root, "add", "f")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _lineage(root: Path) -> tuple[Path, str]:
    """Six attempts over two task ids, each with a mechanical verification posted."""
    _repository(root)
    for index, (task_id, attempt) in enumerate(ATTEMPTS):
        _ok(
            root, "run", "--provider", "claude", "--task-id", task_id,
            "--attempt", str(attempt), "--", *CHILD, "--seed", str(index),
        )  # fmt: skip
        _ok(
            root, "outcome", "--kind", "mechanical_verification", "--status", "passed",
            "--task-id", task_id, "--attempt", str(attempt), "--duration-ms", "1000",
        )  # fmt: skip
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)
    return root, repo_id


def _store() -> Store:
    return Store(config.db_path())


def _ends(built: Series) -> list[str]:
    return [meta.row_end_ts for meta in built.row_meta]


def _between(ends: list[str], index: int) -> str:
    """An instant strictly between two rows' ends, so the boundary is not a tie.

    The midpoint rather than one row's end: the boundary rule is "at or after", and an
    intervention posted at exactly `ends[index]` would pass whether the comparison were
    `>=` or `>`. This one only lands on row `index` if the rule is the one documented.
    """
    before, after = (datetime.fromisoformat(ends[index - 1]), datetime.fromisoformat(
        ends[index]))  # fmt: skip
    assert before < after, f"rows {index - 1} and {index} share an end: {ends}"
    middle = before + (after - before) / 2
    return middle.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _intervened(root: Path, at: str, advisory: str = ADVISORY) -> str:
    """One `telltale intervention`, returned as its stdout."""
    return _ok(
        root, "intervention", "--advisory-id", advisory, "--action", "shadow",
        "--policy-version", "w6t2", "--external-system", "telltale", "--at", at,
    )  # fmt: skip


def _prepared(root: Path) -> tuple[str, str]:
    """The six-attempt lineage with an intervention between rows 2 and 3.

    The `--at` is read off the UNSEGMENTED series that already exists, so the boundary
    is a property of when the attempts ran rather than of how long this test slept.
    """
    repo_id = _lineage(root)[1]
    ends = _ends(series.build(_store(), "attempt", repo_id))
    at = _between(ends, BOUNDARY)
    _intervened(root, at)
    return repo_id, at


def _built(
    repo_id: str, regime: str | None = None, intervention: str | None = None
) -> Series:
    return series.build(_store(), "attempt", repo_id, "exclude", regime, intervention)


def _stored(repo_id: str) -> Series:
    """The unsegmented attempt clock, compiled and put where the CLI can find it."""
    store = Store(config.db_path()).open()
    try:
        built = series.build(store, "attempt", repo_id)
        store.put_series(built)
    finally:
        store.close()
    return built


@pytest.mark.usefixtures("telltale_home")
@pytest.mark.parametrize("field", ["changepoints", "cohort"])
def test_the_boundary_is_a_changepoint_and_a_cohort_entry(
    tmp_path: Path, field: str
) -> None:
    """`series build --clock attempt` over a lineage a policy has acted on.

    Six rows still, because a boundary is not a filter: the unsegmented frame keeps
    every attempt and says where the policy started acting, in two places that a reader
    and a backtester each look at.
    """
    repo_id, at = _prepared(tmp_path / "repo")

    built = _built(repo_id)
    assert len(built.rows) == len(ATTEMPTS)
    if field == "changepoints":
        assert BOUNDARY in built.changepoints
    else:
        assert built.cohort[series_regime.COHORT_KEY] == [
            {"advisory_id": ADVISORY, "boundary_index": BOUNDARY, "ts": at}
        ]
    assert series_regime.REGIME_KEY not in built.cohort
    assert series.check(_store(), built) == []


@pytest.mark.usefixtures("telltale_home")
def test_pre_and_post_are_three_rows_each_and_three_different_series(
    tmp_path: Path,
) -> None:
    """The two sides of one boundary, and the frame they were cut out of.

    The row counts add up to the unsegmented count and neither side is padded, and the
    three series ids differ because the cohort each was read under differs. That is the
    point of putting the boundary in the cohort: one lineage under a policy and the same
    lineage before it are two frames, and design 6.12 hashes the cohort into the id.
    """
    repo_id = _prepared(tmp_path / "repo")[0]

    whole = _built(repo_id)
    before = _built(repo_id, regime="pre", intervention=ADVISORY)
    after = _built(repo_id, regime="post", intervention=ADVISORY)

    assert len(before.rows) == BOUNDARY
    assert len(after.rows) == len(ATTEMPTS) - BOUNDARY
    assert len(before.rows) + len(after.rows) == len(whole.rows)
    assert before.cohort[series_regime.REGIME_KEY] == "pre"
    assert after.cohort[series_regime.REGIME_KEY] == "post"
    assert before.cohort[series_regime.CHOSEN_KEY] == ADVISORY
    # The boundary index is the position in the LINEAGE and does not move when the
    # frame is cut: both sides say the policy began acting at row 3 of six.
    for one in (before, after):
        assert one.cohort[series_regime.COHORT_KEY][0]["boundary_index"] == BOUNDARY
        # A segmented frame carries no boundary changepoint: `pre` ends before it and
        # `post` begins at it, so there is no row inside either where it sits.
        assert BOUNDARY not in one.changepoints
    assert len({whole.series_id, before.series_id, after.series_id}) == 3
    # A prefix of a running maximum is the running maximum of the prefix, so `pre` is
    # the same three timestamps the unsegmented frame printed. `post` restarts from its
    # own first row, which is what "neither pads" means at the boundary.
    assert _ends(before) == _ends(whole)[:BOUNDARY]
    assert _ends(after) == sorted(_ends(after))
    assert series.check(_store(), before) == []
    assert series.check(_store(), after) == []


@pytest.mark.usefixtures("telltale_home")
def test_the_cli_prints_the_boundary_and_the_two_regimes(tmp_path: Path) -> None:
    """The three `series build` invocations of the brief, through the real CLI."""
    root = tmp_path / "repo"
    repo_id = _prepared(root)[0]

    whole = _ok(root, "series", "build", "--clock", "attempt", "--repo", repo_id)
    assert "clock attempt  6 rows" in whole
    assert f"changepoints {BOUNDARY}" in whole
    assert ADVISORY in whole

    # `post` without --intervention: the repository has exactly one, so naming it is
    # optional. `pre` names it, which is what a repository with two would require.
    for word, rows, named in (
        ("pre", BOUNDARY, [ADVISORY]),
        ("post", len(ATTEMPTS) - BOUNDARY, []),
    ):
        page = _ok(
            root, "series", "build", "--clock", "attempt", "--repo", repo_id,
            "--regime", word, *(["--intervention", *named] if named else []),
        )  # fmt: skip
        assert f"clock attempt  {rows} rows" in page
        assert f'"regime": "{word}"' in page


@pytest.mark.usefixtures("telltale_home")
def test_a_forecast_of_the_unsplit_series_is_refused_by_the_boundary(
    tmp_path: Path,
) -> None:
    """Exit 2, naming the intervention, the row and what to do about it."""
    root = tmp_path / "repo"
    built = _stored(_prepared(root)[0])

    refused = _run(
        root, "forecast", "backtest", "--series", built.series_id,
        "--target", POOLED_TARGET,
    )  # fmt: skip

    assert refused.returncode == 2, refused.stdout
    assert f"intervention {ADVISORY} at row {BOUNDARY} of 6" in refused.stdout
    assert "--regime pre" in refused.stdout
    assert "--pooled" in refused.stdout


@pytest.mark.usefixtures("telltale_home")
def test_an_intervention_on_the_request_clock_is_refused(tmp_path: Path) -> None:
    """A regime is a lineage-clock statement, and the refusal says the words."""
    root = tmp_path / "repo"
    capture = _built(_prepared(root)[0]).row_meta[0].row_key

    refused = _run(
        root, "series", "build", "--clock", "request", "--capture", capture,
        "--regime", "post", "--intervention", ADVISORY,
    )  # fmt: skip

    assert refused.returncode == 2, refused.stdout
    assert "regimes are lineage-clock statements" in refused.stdout


@pytest.mark.usefixtures("telltale_home")
def test_the_intervention_stores_four_fields_and_no_unknown_field(
    tmp_path: Path,
) -> None:
    """The observation the command wrote, read back out of the store.

    Four fields and four diagnostics of zero: an `unknown_field` row here would mean
    this file's payload and the allowlist disagree, which is a bug in the writer rather
    than a provider that moved.
    """
    root = _repository(tmp_path / "repo")
    printed = _intervened(root, "2026-09-03T12:00:00Z")

    store = _store()
    rows = store.observations_of_type(series_regime.INTERVENTION_TYPE)
    assert len(rows) == 1
    assert dict(rows[0]["payload"]) == {
        "advisory_id": ADVISORY,
        "action": "shadow",
        "policy_version": "w6t2",
        "external_system": "telltale",
    }
    # Normalized to the store's fixed-width spelling: `...00Z` and `...00.000000Z` are
    # the same instant and do not compare equal as TEXT, and the boundary rule is a
    # string comparison against row_end_ts.
    assert rows[0]["provider_ts"] == "2026-09-03T12:00:00.000000Z"
    assert rows[0]["capture_id"].startswith("pol_")
    assert rows[0]["capture_id"] in printed
    assert rows[0]["observation_id"] in printed
    assert [row for row in store.diagnostics() if row["kind"] == "unknown_field"] == []


@pytest.mark.usefixtures("telltale_home")
def test_a_second_intervention_makes_the_regime_flag_name_both(tmp_path: Path) -> None:
    """Two boundaries and one regime word is a question with two answers."""
    root = tmp_path / "repo"
    repo_id = _prepared(root)[0]
    ends = _ends(_built(repo_id))
    _intervened(root, _between(ends, 5), advisory="adv_w6t2_second")

    built = _built(repo_id)
    assert {BOUNDARY, 5} <= set(built.changepoints)
    refused = _run(
        root, "series", "build", "--clock", "attempt", "--repo", repo_id,
        "--regime", "pre",
    )  # fmt: skip
    assert refused.returncode == 2, refused.stdout
    assert ADVISORY in refused.stdout
    assert "adv_w6t2_second" in refused.stdout


@pytest.mark.usefixtures("telltale_home")
def test_a_naive_timestamp_is_refused_rather_than_read_as_utc(tmp_path: Path) -> None:
    """A machine's zone setting must not decide which side of a boundary a row is."""
    root = _repository(tmp_path / "repo")

    refused = _run(
        root, "intervention", "--advisory-id", ADVISORY, "--action", "shadow",
        "--policy-version", "w6t2", "--external-system", "telltale",
        "--at", "2026-09-03T12:00:00",
    )  # fmt: skip

    assert refused.returncode == 2, refused.stdout
    assert "carries no timezone" in refused.stdout


# -- the pooled run -------------------------------------------------------------------


def _pooled_series(store: Store, repo_id: str) -> Series:
    """A 60-row change-clock series of this repository, with the real boundary on it.

    The rows are synthetic_series' shape and say so in `reducer_version`. Everything
    about the BOUNDARY is the shipped code: `marks` reads the observation the CLI
    wrote, and `segment` is the function series_lineage calls after it assembles a
    frame. Only the frame is written here, because the real lineage of this file is six
    rows and the change clock's c_min is 16.
    """
    built = synthetic_series.make_change(rows=POOLED_ROWS)
    cohort = {**built.cohort, "repo_id": repo_id}
    frame = _Frame(
        rows=[list(row) for row in built.rows],
        keys=[meta.row_key for meta in built.row_meta],
        ends=_ends(built),
        fingerprints=[meta.env_fingerprint_id for meta in built.row_meta],
        provenance=[list(meta.provenance) for meta in built.row_meta],
        flags=[list(meta.flags) for meta in built.row_meta],
    )
    marks = series_regime.marks(store, repo_id, None, None)
    series_regime.segment(marks, frame, cohort, frame.ends)
    pooled = dataclasses.replace(
        built,
        cohort=cohort,
        changepoints=sorted(series_regime.boundaries(cohort)),
        series_id=series.series_id(
            "change", cohort, built.columns, built.reducer_version, built.rows
        ),
    )
    store.put_series(pooled)
    return pooled


@dataclasses.dataclass
class _Frame:
    """The six parallel lists `segment` cuts. See `series_regime.Frame`."""

    rows: list[list[float | None]]
    keys: list[str]
    ends: list[str]
    fingerprints: list[str | None]
    provenance: list[list[str]]
    flags: list[list[str]]


@pytest.mark.usefixtures("telltale_home")
def test_a_pooled_run_names_the_boundary_first_and_stores_it(tmp_path: Path) -> None:
    """`--pooled` past the refusal: the banner is the first line and the row says so.

    Both halves matter. The line is for the reader of the terminal, and
    `scenario.pooled_across` is for every later reader of the stored number, who has no
    terminal to have read.
    """
    root = _repository(tmp_path / "repo")
    _intervened(root, "2026-01-01T10:00:00Z")
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)
    store = Store(config.db_path()).open()
    try:
        pooled = _pooled_series(store, repo_id)
    finally:
        store.close()
    # 20 minutes a row from 2026-01-01T00:00:00Z, so 10:00 is row 30 of 60.
    assert pooled.changepoints == [30]

    refused = _run(
        root, "forecast", "backtest", "--series", pooled.series_id,
        "--target", POOLED_TARGET, "--forecasters", BASELINES, "--model", MODEL,
    )  # fmt: skip
    assert refused.returncode == 2, refused.stdout
    assert f"intervention {ADVISORY} at row 30 of 60" in refused.stdout

    done = _run(
        root, "forecast", "backtest", "--series", pooled.series_id,
        "--target", POOLED_TARGET, "--forecasters", BASELINES, "--model", MODEL,
        "--pooled",
    )  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    first = done.stdout.splitlines()[0]
    assert first == f"pooled across intervention {ADVISORY} at row 30 of 60", first

    rows = _store().forecast_runs(pooled.series_id)
    assert len(rows) == 1
    assert rows[0]["scenario"]["pooled_across"] == [ADVISORY]


@pytest.mark.usefixtures("telltale_home")
def test_a_regime_series_forecasts_without_the_flag(tmp_path: Path) -> None:
    """The other answer to the refusal: one regime needs no --pooled and stores none.

    `post` of the synthetic frame is 30 rows, which c_min 16 can still score, and the
    stored row carries no `pooled_across` because it pooled across nothing.
    """
    root = _repository(tmp_path / "repo")
    _intervened(root, "2026-01-01T10:00:00Z")
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)
    store = Store(config.db_path()).open()
    try:
        whole = _pooled_series(store, repo_id)
        one = _segmented(store, whole, repo_id, "post")
    finally:
        store.close()
    assert len(one.rows) == POOLED_ROWS - 30

    done = _run(
        root, "forecast", "backtest", "--series", one.series_id,
        "--target", POOLED_TARGET, "--forecasters", BASELINES, "--model", MODEL,
    )  # fmt: skip

    assert done.returncode == 0, done.stdout + done.stderr
    assert not done.stdout.startswith("pooled across")
    rows = _store().forecast_runs(one.series_id)
    assert "pooled_across" not in rows[0]["scenario"]


def _segmented(store: Store, built: Series, repo_id: str, word: str) -> Series:
    """One side of a `_pooled_series` boundary, through the same two functions."""
    cohort: dict[str, Any] = {**built.cohort}
    cohort.pop(series_regime.COHORT_KEY, None)
    frame = _Frame(
        rows=[list(row) for row in built.rows],
        keys=[meta.row_key for meta in built.row_meta],
        ends=_ends(built),
        fingerprints=[meta.env_fingerprint_id for meta in built.row_meta],
        provenance=[list(meta.provenance) for meta in built.row_meta],
        flags=[list(meta.flags) for meta in built.row_meta],
    )
    marks = series_regime.marks(store, repo_id, word, ADVISORY)
    series_regime.segment(marks, frame, cohort, list(frame.ends))
    # By row_key rather than by position: `segment` cut six parallel lists, and the
    # keys are what says which rows came out of it.
    by_key = {meta.row_key: meta for meta in built.row_meta}
    one = dataclasses.replace(
        built,
        cohort=cohort,
        rows=frame.rows,
        row_meta=[by_key[key] for key in frame.keys],
        changepoints=sorted(series_regime.boundaries(cohort)),
        series_id=series.series_id(
            "change", cohort, built.columns, built.reducer_version, frame.rows
        ),
    )
    store.put_series(one)
    return one


@pytest.mark.usefixtures("telltale_home")
def test_the_pooled_flag_alone_is_not_a_licence_to_pool_nothing(
    tmp_path: Path,
) -> None:
    """`--pooled` on a series with no boundary prints no banner and stores no key.

    The flag says what a run crossed, and a run that crossed nothing says nothing: a
    `pooled_across: []` on every row would make the key mean "this command was typed"
    rather than "this number is an average of two regimes".
    """
    root = _repository(tmp_path / "repo")
    store = Store(config.db_path()).open()
    try:
        built = synthetic_series.write_change(store, rows=POOLED_ROWS)
    finally:
        store.close()
    assert pooling.check(built, pooled=False) == []

    done = _run(
        root, "forecast", "backtest", "--series", built.series_id,
        "--target", POOLED_TARGET, "--forecasters", BASELINES, "--model", MODEL,
        "--pooled",
    )  # fmt: skip

    assert done.returncode == 0, done.stdout + done.stderr
    assert not done.stdout.startswith("pooled across")
    rows = _store().forecast_runs(built.series_id)
    assert "pooled_across" not in json.dumps(rows[0]["scenario"])


@pytest.mark.usefixtures("telltale_home")
@pytest.mark.parametrize(
    ("command", "target", "extra"),
    [
        ("placebo", POOLED_TARGET, []),
        ("ablate", POOLED_TARGET, []),
        ("candidate", "merge_verification_ms", []),
        (
            "scenario",
            POOLED_TARGET,
            ["--name", "policy", "--future", "files_changed=2"],
        ),
    ],
)
def test_each_forecast_command_checks_and_records_pooling(
    tmp_path: Path, command: str, target: str, extra: list[str]
) -> None:
    root = _repository(tmp_path / "repo")
    _intervened(root, "2026-01-01T10:00:00Z")
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)
    store = _store().open()
    try:
        built = _pooled_series(store, repo_id)
    finally:
        store.close()
    args = [
        "forecast",
        command,
        "--series",
        built.series_id,
        "--target",
        target,
        "--forecasters",
        BASELINES,
        "--model",
        MODEL,
        *extra,
    ]
    refused = _run(root, *args)
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert f"intervention {ADVISORY} at row 30" in refused.stdout
    done = _run(root, *args, "--pooled")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines()[0] == (
        f"pooled across intervention {ADVISORY} at row 30 of 60"
    )
    records = _store().forecast_runs(built.series_id)
    assert records
    assert all(row["scenario"]["pooled_across"] == [ADVISORY] for row in records)


@pytest.mark.usefixtures("telltale_home")
def test_duplicate_advisory_ids_refuse_regime_selection(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    _intervened(root, "2026-01-01T10:00:00Z")
    _intervened(root, "2026-01-01T11:00:00Z")
    repo_id = repo.identity(root)["repo_id"]
    assert isinstance(repo_id, str)
    done = _run(
        root,
        "series",
        "build",
        "--clock",
        "attempt",
        "--repo",
        repo_id,
        "--regime",
        "post",
        "--intervention",
        ADVISORY,
    )
    assert done.returncode == 2, done.stdout + done.stderr
    assert "2 observations" in done.stdout
    assert "ambiguous boundary" in done.stdout


@pytest.mark.usefixtures("telltale_home")
def test_request_clock_refuses_only_its_repository_intervention(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    _ok(root, "run", "--provider", "claude", "--", *CHILD)
    captures = _store().captures()
    assert len(captures) == 1
    capture = captures[0]
    stamp = _between([capture["first_ts"], capture["last_ts"]], 1)
    unrelated = _repository(tmp_path / "unrelated")
    _git(unrelated, "remote", "add", "origin", "https://example.invalid/unrelated.git")
    assert repo.identity(root)["repo_id"] != repo.identity(unrelated)["repo_id"]
    _intervened(unrelated, stamp)
    args = ["series", "build", "--clock", "request", "--capture", capture["capture_id"]]
    _ok(root, *args)
    _intervened(root, stamp)
    done = _run(root, *args)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "regimes are lineage-clock statements" in done.stdout


@pytest.mark.usefixtures("telltale_home")
def test_selected_regimes_keep_other_interior_boundaries(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    repo_id = _prepared(root)[0]
    _intervened(root, _between(_ends(_built(repo_id)), 5), "adv_later")
    before = _built(repo_id, "pre", "adv_later")
    after = _built(repo_id, "post", ADVISORY)
    assert before.changepoints == [BOUNDARY]
    assert after.changepoints == [2]
    for built, advisory, index in (
        (before, ADVISORY, BOUNDARY),
        (after, "adv_later", 2),
    ):
        refused = pooling.check(built, pooled=False)
        assert f"intervention {advisory} at row {index}" in "\n".join(refused)
        assert pooling.pooled_across(built, pooled=True) == [advisory]
        assert pooling.check(built, pooled=True) == []


@pytest.mark.usefixtures("telltale_home")
def test_imported_request_ignores_an_unrelated_intervention(tmp_path: Path) -> None:
    from test_import import materialise

    sources, root, _ = materialise("claude-transcripts", tmp_path)
    _repository(root)
    _ok(root, "import", "claude-transcripts", "--root", str(sources))
    captures = _store().captures()
    capture = max(captures, key=lambda one: one["n_observations"])
    assert not _store().observations(
        capture["capture_id"], ("telltale.capture_started",)
    )
    unrelated = _repository(tmp_path / "unrelated")
    _git(unrelated, "remote", "add", "origin", "https://example.invalid/unrelated.git")
    _intervened(unrelated, "2026-09-02T12:00:00Z")
    _ok(
        root,
        "series",
        "build",
        "--clock",
        "request",
        "--capture",
        capture["capture_id"],
    )
