"""The fixtures the experiment-runner tests share: repositories, argv, and constants.

Not a test file and not collected as one. It exists because three test files drive the
same three runners (`experiments.repeat`, `experiments_env.environment`,
`experiments_env.intervention`) against the same scripted agent, and each of them had
its own copy of `_git`, `_store` and `_telltale`. W4-T4 left
tests/integration/test_experiments.py at exactly 800 lines, the file-length ratchet's
limit, and said the next task to touch it had to split it; this is that split, and the
duplicated helpers came with the constants because two copies of a repository builder
are two fixtures that can disagree.

Nothing here is a stub. Every function below makes a real git repository, a real commit
or a real subprocess, which is the rule tests/integration/conftest.py exists to keep.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fake_agent

from telltale.cohorts import VECTOR
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Mapping

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"

# The acceptance command: deterministic, run by the harness after the agent exits, and
# the only thing that decides pass or fail. `--fail` makes the agent write 41.
ACCEPTANCE = (
    "import pathlib,sys;"
    "sys.exit(0 if pathlib.Path('answer.txt').read_text().strip()=='42' else 1)"
)

# The vector's stream facts, which no reducer measures, and the tool calls the fake
# agent really makes.
STREAM_METRICS = ("num_turns", "duration_ms")
TOOL_METRICS = ("tool_calls", "tool_calls.Bash", "tool_calls.Edit", "tool_calls.Read")

# The metrics of spec 13.7's vector a fake-agent capture leaves unknown, measured and
# not reasoned. It never compacts, so `pre_compaction_tokens` sums an empty set and is
# null; and it is a stream-only capture, whose per-request output count Claude does not
# state (W4-T4), so no interval has one. The session total is in MEASURE_METRICS below.
#
# `context_token_burden.cache_read_tokens` was an entry here until W3-T0: `_request`
# read `correlate.USAGE_KEYS`, the OTel spelling, while a stream-only request spells
# that counter `cache_read_input_tokens`, so the fake agent emitted it all along and
# the reducer never read it. This comment is what stops it being put back.
UNKNOWN_TO_THE_FAKE_AGENT = ("compactions.pre_compaction_tokens",
                             "stable_state_work.stable_state_tokens")  # fmt: skip

# The other 19, keyed as the vector keys them.
MEASURE_METRICS = tuple(
    key
    for family, names in VECTOR
    for name in names
    if (key := f"{family}.{name}") not in UNKNOWN_TO_THE_FAKE_AGENT
)

# The seed bound both arms of a two-arm experiment run under. Every fabricated number in
# the fake agent is linear in its seed, so at the default bound of 100 the DRAW moves
# the token counts further than --effort does and the sign of a between-arm shift would
# be a property of the draw. At 3 the arms' value ranges are disjoint by construction,
# which is what lets a test assert a direction rather than a number from one run. 8
# rather than 3 because the scaled MAD of five draws from three values is often 0, and a
# spread of 0 makes MDD 0 and labels every nonzero shift material.
SEED_MAX = 8

# What the fake agent does per run beyond its Read calls: one Edit and one Bash, so one
# turn each. `_usage` is called once per turn, which is what makes the token total a
# function of the read count.
TURNS_BESIDE_READS = 2

# The probe fixture's files. Both exist at the second commit and only the first exists
# at the first, which is the whole of W4-T1's intervention.
ALPHA = "src/alpha.py"
BETA = "src/beta.py"
STRAY = "src/stray.py"
ANSWERED = f"{ALPHA},{BETA}"


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _init(root: Path) -> None:
    """A git repository with an identity set on the REPOSITORY, never globally: the
    `telltale_home` fixture asserts that nothing was written under $HOME."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", ".")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Telltale Test")


def commit(root: Path, message: str, files: Mapping[str, str]) -> str:
    """Write these paths, commit everything, and return the sha.

    Parent directories are made, so a path can name a file the repository does not have
    a directory for yet. `git add -A` rather than the named paths, because a test that
    deletes a file is committing that deletion too.
    """
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def task_repository(root: Path) -> str:
    """One commit, and a file the fake agent rewrites. Returns its sha."""
    _init(root)
    return commit(
        root,
        "base",
        {"README.md": "a temporary repository\n", "answer.txt": "seed\n"},
    )


def probe_repository(root: Path, agents: str | None = None) -> str:
    """A repository with one commit carrying alpha and stray. Returns its sha.

    `agents` writes an AGENTS.md into that first commit. It is None for the base_sha
    tests, whose fixture must carry no instruction file at all: a commit that also
    touched one is refused by the fingerprint assertion, which is that check working.
    """
    _init(root)
    files = {
        "README.md": "a temporary repository\n",
        ALPHA: "def load():\n    return 1\n",
        STRAY: "def stray():\n    return 2\n",
    }
    return commit(
        root, "before", files if agents is None else {**files, "AGENTS.md": agents}
    )


def beta_commit(root: Path) -> str:
    """The refactor: beta arrives. No instruction file is touched, on purpose.

    The fingerprint assertion of a base_sha experiment is that the two arms differ in NO
    fingerprint field, and `instruction_hashes` is one of them and is read out of the
    worktree. A commit that also added an AGENTS.md would be refused there, which is the
    check working rather than the check being wrong.
    """
    return commit(root, "after", {BETA: "def load():\n    return 3\n"})


def arm_command(effort: str = "medium", model: str = "sonnet") -> list[str]:
    """The environment runner's argv: one task, and the two flags an arm may vary."""
    return [
        sys.executable, str(FAKE_AGENT), "-p", "make the answer 42",
        "--output-format", "stream-json", "--seed-max", str(SEED_MAX),
        "--model", model, "--effort", effort,
    ]  # fmt: skip


def probe_command(answer: str = ANSWERED) -> list[str]:
    """The probe runner's argv. `{prompt}` is what the runner substitutes per probe."""
    return [
        sys.executable, str(FAKE_AGENT), "-p", "{prompt}",
        "--output-format", "stream-json", "--seed-max", str(SEED_MAX),
        "--model", "sonnet", "--effort", "medium", "--answer", answer,
    ]  # fmt: skip


def probes() -> list[dict[str, Any]]:
    """Two probes, one answered exactly and one the same answer overshoots.

    One agent, one answer, two keys. P-exact's key is both files the agent names, so it
    is answered exactly. P-narrow's key is only the first, so the second is a path the
    answer named that exists in the repository and is not in the key: a wrong path.
    """
    return [
        {
            "probe_id": "P-exact",
            "prompt": "locate the implementation behind load",
            "answer_key": {"paths": [ALPHA, BETA], "symbols": []},
        },
        {
            "probe_id": "P-narrow",
            "prompt": "identify the one module that defines load",
            "answer_key": {"paths": [ALPHA], "symbols": []},
        },
    ]


def intervention_spec(
    root: Path,
    before: str,
    after: str,
    experiment: str,
    repetitions: int,
    task_id: str = "T-refactor",
    **changed: Any,
) -> dict[str, Any]:
    """One probe suite at two commits, with byte-identical argv on both arms."""
    arms: list[dict[str, Any]] = [
        {"name": "before", "base_sha": before, "command": probe_command()},
        {"name": "after", "base_sha": after, "command": probe_command()},
    ]
    return {
        "task_id": task_id,
        "experiment": experiment,
        "repo": str(root),
        "probes": [probes()[1]],
        "repetitions_per_arm": repetitions,
        "provider": "claude",
        "level": 1,
        "factor": "base_sha",
        "arms": arms,
        **changed,
    }


def reader(home: Path) -> Store:
    """A reader over the store the runner wrote. Read methods need no open()."""
    return Store(home / "telltale.db")


def payloads(store: Store, capture_id: str, obs_type: str) -> list[dict[str, Any]]:
    return [
        dict(row["payload"])
        for row in store.observations(capture_id)
        if row["observation_type"] == obs_type
    ]


def run_telltale(*args: str, home: Path) -> subprocess.CompletedProcess[str]:
    """One `telltale` subcommand as a real child process, in a named environment.

    Named rather than inherited: the child must write into the home this test was given,
    and HOME is the temporary one the `telltale_home` fixture set.
    """
    executable = shutil.which("telltale")
    assert executable is not None, "no `telltale` on PATH: run `uv sync` first"
    return subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={
            "TELLTALE_HOME": str(home),
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ["HOME"],
        },
    )


def rows(stdout: str, header: str) -> list[list[str]]:
    """The table under `header`, as cell lists. Blank line ends a table."""
    lines = stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(header))
    out: list[list[str]] = []
    for line in lines[start + 2 :]:
        if not line.strip():
            break
        out.append(line.split())
    return out


def assert_statistics(found: Mapping[str, Any], repetitions: int) -> None:
    """Robust statistics per metric, never a mean alone, and no unknown made a zero.

    The vector is spec 13.7's own 22 metrics read back out of the evidence table, so
    these rows and a `telltale vector` of any of the same captures are one set of
    numbers rather than two.
    """
    for metric in (*MEASURE_METRICS, *STREAM_METRICS, *TOOL_METRICS):
        row = found[metric]
        assert row["n"] == repetitions, metric
        assert row["unknown"] == 0, metric
        assert row["median"] is not None, metric
        assert row["mad_scaled"] is not None, metric
        assert len(row["values"]) == repetitions, metric
    for metric in UNKNOWN_TO_THE_FAKE_AGENT:
        row = found[metric]
        assert row["n"] == 0, metric
        assert row["unknown"] == repetitions, metric
        assert row["median"] is None, metric


def reads_range(effort: str) -> list[int]:
    """Every Read count the fake agent can produce in this arm, from its constants."""
    rank = fake_agent.EFFORTS.index(effort)
    return [fake_agent._reads(seed, rank) for seed in range(SEED_MAX)]


def fresh_input_tokens_range(effort: str, model: str = "sonnet") -> list[int]:
    """Every fresh_input_tokens total this arm can produce, from the agent's formula.

    Computed rather than recorded: a number copied out of a run would make the test a
    record of that run, and the claim under test is that the RUNNER reports the
    direction the agent's constants put there.

    spec 13.7's fresh_input_tokens is the sum of the stream's per-request
    `input_tokens`, which is what this sums: `measures_spec13._TOKENS` maps the one to
    the other and `activities._request` copies the field across unrenamed.
    """
    rank = fake_agent.EFFORTS.index(effort)
    totals = []
    for seed in range(SEED_MAX):
        turns = fake_agent._reads(seed, rank) + TURNS_BESIDE_READS
        totals.append(
            sum(
                fake_agent._usage(seed, rank, model, turn)["input_tokens"]
                for turn in range(turns)
            )
        )
    return totals
