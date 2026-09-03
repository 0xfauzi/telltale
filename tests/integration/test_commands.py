"""What `commands.py` says one command line was, measured through the whole recorder.

One file for the classifier because W4-T3 gave it a defect class of its own: the rules
that read a chain. Everything here is a real `telltale run` around the scripted agent,
so what is asserted is the row a person would see and never a call into `commands.py`:
a classifier tested on strings it was handed cannot catch a normalizer that never
handed it the second half of the line, which is exactly what happened.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from conftest import activity_fields, launched

from telltale import measures

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.integration
def test_a_verification_anywhere_in_a_chain_is_a_verification_run(
    tmp_path: Path,
) -> None:
    r"""Four chains whose verification is not the first thing in them, and one that is
    not a chain at all. W4-T3.

    The scripted agent's `--chains` mode reports the five command lines of
    `fake_agent.CHAIN_COMMANDS` and runs none of them, so what is under test is what the
    recorder does with a command line and not what this machine does with pytest. Every
    one was recorded as something other than a test run before this:

    - `uv sync -q | tail -2; uv run pytest -m integration` was a package_op: the first
      segment with a category won and `uv sync` is the first segment.
    - `uv run ruff format . && ... && uv run pytest -q` was a format run, by the same
      rule. It is also the chain the second defect was measured on: `uv run ruff format`
      normalized to `uv run ruff _`, because design 6.4's two bare words went to `run`
      and `ruff`, so lint and format were the category of NONE of the 769 Bash calls in
      the six E12 streams while 81 of them ran ruff.
    - `cd /tmp/x` then `uv run pytest -q 2>&1 | tail -5` was shell: a newline was
      whitespace to shlex, so `cd ... uv run pytest` was ONE segment whose head was
      `cd`, and the `tail` at the end of it was the next one.
    - a `python3 - <<'PY'` script then `uv run pytest -q` was unknown, the same way, one
      segment headed `python3`.
    - `git commit -m "one<newline>two"` was git and still is: this is the case a newline
      rule must NOT split, and W2-T1 measured it.

    `agent_test_runs` is 4 and not 5: the commit is a `command`. It is not 6 either,
    which is what the heredoc chain is for. That body says `uv run pytest` and ran
    nothing, so the chain holds ONE test segment; the `command_norm` asserted below is
    where that is visible, and `exit_masked` is the second place. Were the body split
    into segments, the chosen segment would be the one inside it and the `;` the
    terminator line puts after it would mask the run.

    Three breaks, each run and each with the number it produced. Restore
    `first-segment-with-a-known-category-wins` in `commands.classify` (drop the
    `_VERIFICATION_ORDER` loop in `_chosen`): the first two chains stop being test runs
    and `agent_test_runs` is 2. Delete the `commands_shell.with_separators` call in
    `commands.normalize` instead: the third and fourth stop being test runs and it is 2
    again, the other two surviving on their `;` and `&&`. Write the newline rule the
    naive way, `text.replace("\n", " ; ")` with no heredoc and no quoting: the count
    holds at 4 and the heredoc's normal form becomes
    `python3 _ << _ ; print ( _ ) ; PY ; uv run pytest -q`, three segments cut out of a
    script that ran as one.
    """
    store, capture = launched(tmp_path, "--chains")
    runs = activity_fields(store, capture, "verification_run")
    summary = measures.summary(store, capture)

    # Keyed by the normal form rather than positional: the tool calls of one capture are
    # correlated by tool_use_id, so their row order is that id's and not the order the
    # agent made them in.
    # The digest in `<outside>/...` is a function of an absolute path outside the
    # repository and is not a fact this test is about; `test_privacy.py` owns it.
    by_norm = {
        re.sub(r"<outside>/[0-9a-f]+", "<outside>/_", str(row["command_norm"])): row
        for row in runs
    }

    assert summary["verification"]["agent_test_runs"] == 4
    assert len(by_norm) == 4, sorted(by_norm)
    assert [row["category"] for row in runs] == ["test"] * 4
    assert [row["scope"] for row in runs] == ["full"] * 4
    # One row per chain, with the VERIFICATION categories it held so that the one word
    # the row carries hides none of the checking the call did, and `exit_masked` judged
    # for the CHOSEN segment: a `|` in front of the test masks nothing and a `|` after
    # it masks everything.
    expected = {
        "uv sync -q | tail -2 ; uv run pytest -m integration": (["test"], None),
        "uv run ruff format . && uv run ruff check . && uv run mypy ."
        " && uv run pytest -q": (["format", "lint", "typecheck", "test"], None),
        "cd <outside>/_ ; uv run pytest -q _ >& _ | tail -5": (["test"], True),
        "python3 _ << _ _ ; uv run pytest -q": (["test"], None),
    }
    assert {
        norm: (row["categories"], row.get("exit_masked"))
        for norm, row in by_norm.items()
    } == expected
    # The commit is one segment and one category, and its message never became a
    # command: a newline inside quotes is not a separator.
    commits = [
        row
        for row in activity_fields(store, capture, "command")
        if row.get("category") == "git"
    ]
    assert [str(row["command_norm"]) for row in commits] == ["git commit -m _"]
    assert "categories" not in commits[0]


def test_a_chain_longer_than_the_old_bound_keeps_its_last_segment(
    tmp_path: Path,
) -> None:
    """One 369-character chain with pytest at its end is one test run. W4-F2.

    Design 6.4 bounded a stored normal form at 200 characters. W4-T3 measured the
    consequence on the six E12 streams: two of the 51 pytest runs sat past the 200th
    character of a chain's normal form and were cut off it, so the recorder called one
    chain a typecheck and the other a lint. The owner raised the bound on 2026-09-03 to
    512, the bound every other kept string already has (design 6.4). Measured with the
    bound lifted on the same 769 commands: 100 normal forms reach 200, 7 reach 512, none
    reaches 768, the longest is 717, and no pytest segment starts past character 300.

    The chain here is W3-E08b/1's shape. Its normal form is 299 characters and its
    pytest starts at character 251. Break it by putting `MAX_COMMAND = 200` back: the
    stored form ends at the fourth `echo _`, the call is a typecheck, and
    `agent_test_runs` is 0.
    """
    store, capture = launched(tmp_path, "--long-chain")
    runs = activity_fields(store, capture, "verification_run")
    summary = measures.summary(store, capture)

    assert summary["verification"]["agent_test_runs"] == 1
    assert len(runs) == 1, [row.get("command_norm") for row in runs]
    (row,) = runs
    assert row["category"] == "test"
    assert row["scope"] == "full"
    assert row["categories"] == ["typecheck", "lint", "format", "test"]
    assert row["exit_masked"] is True
    norm = str(row["command_norm"])
    assert norm.endswith("; uv run pytest -m integration -q _ >& _ | tail -3"), norm
    assert len(norm) == 299, len(norm)
