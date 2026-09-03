"""Where the checkpoint lands. `TELLTALE_HF_CACHE` and the empty string.

An environment variable set to the empty string is not an unset one, and the difference
costs 1.29 GB. `cache_dir or os.environ.get(CACHE_ENV)` hands `""` straight through,
huggingface_hub resolves `""` against the current directory, and the checkpoint
downloads into whatever directory the command was run from: W6-T1's implementer did
that into a worktree root while verifying (docs/log/W6-T1.md). `hf_cache_dir` is the
one rule that decides it, and the rule lives in `telltale.forecast` rather than in
`telltale.forecast.timesfm` for the reason this file can be run at all.

NOTHING HERE IMPORTS `telltale.forecast.timesfm`. That module imports numpy at module
scope and torch inside `TimesFM.__init__`, both of which live behind the `forecast`
extra, and CI runs the default environment where a plain `uv sync` installs neither. A
test of the cache rule that needed the extra would be a test CI never runs, which is
why the rule is stdlib and lives one module up.

`monkeypatch.setenv` and `delenv` are the process environment, not anything of
Telltale's, and pytest restores both after each test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from telltale.forecast import CACHE_ENV, hf_cache_dir

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def test_an_unset_variable_leaves_the_loader_its_own_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None, and not the empty string: None is what says "you choose"."""
    monkeypatch.delenv(CACHE_ENV, raising=False)

    assert hf_cache_dir(None) is None


def test_a_variable_set_to_the_empty_string_is_not_a_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect W6-T1 hit. `export TELLTALE_HF_CACHE=` is not a path to cache into.

    huggingface_hub takes a `cache_dir` of "" as a relative path and resolves it against
    the process's current directory, so this one falsy string is the difference between
    the shared cache and 1.29 GB of checkpoint in the working directory.
    """
    monkeypatch.setenv(CACHE_ENV, "")

    assert hf_cache_dir(None) is None


def test_a_variable_holding_a_path_is_the_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The intended use: E03 points the variable at a directory it keeps."""
    monkeypatch.setenv(CACHE_ENV, str(tmp_path))

    assert hf_cache_dir(None) == str(tmp_path)


def test_the_explicit_argument_wins_over_the_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`TimesFM(cache_dir=...)` is a caller saying where, and it outranks the shell."""
    monkeypatch.setenv(CACHE_ENV, str(tmp_path / "from_the_environment"))

    assert hf_cache_dir(str(tmp_path / "asked_for")) == str(tmp_path / "asked_for")
