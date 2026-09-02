"""The two tests that drive a real agent binary and spend real tokens.

NEVER run by CI, and not run by W1-T4, whose brief forbids starting any session that
spends tokens. `uv run pytest -m live` runs them by hand, and only with TELLTALE_LIVE=1
set, so the marker alone cannot spend money by accident: a marker is a selection and an
environment variable is a decision.

What they are for. Every other test in this tree drives the launcher with a scripted
child, which proves the mechanism and cannot prove the shape: whether Claude Code still
accepts the flags the launch plan appends, whether it still exports what the receiver
parses, and whether `codex exec` does either. Those are facts about somebody else's
binary, they change without notice, and the only way to know is to run one.

Each test is one short prompt and asserts the smallest thing that could only be true if
the whole path worked: a capture exists, and something in it reports a model request.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# The prompt. One sentence, one word of output, no tools: the cheapest thing that still
# makes a model request, because what is under test is capture and not the answer.
PROMPT = "Reply with the single word: ok"

# Claude Code's own limit on the session, so a run that misunderstands the prompt still
# ends. Three is the smallest number that leaves room for a tool call the model decides
# to make anyway.
MAX_TURNS = "3"

_LIVE = "TELLTALE_LIVE"
_REASON = f"live: set {_LIVE}=1 to run a real agent and spend real tokens"

# Observation types that mean a model was called. One per provider, because the two
# report a request on different surfaces: Claude on OTel and the stream, Codex on its
# exec stream (design 6.3).
_CLAUDE_REQUEST = ("claude.otel.api_request", "claude.stream.assistant")
_CODEX_REQUEST = ("codex.exec.turn_completed", "codex.exec.item")


def _enabled() -> bool:
    return os.environ.get(_LIVE) == "1"


def _binary(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        pytest.skip(f"live: no {name} binary on this machine")
    return found


def _run(home: Path, argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    executable = shutil.which("telltale")
    assert executable is not None, "no `telltale` on PATH: run `uv sync` first"
    return subprocess.run(
        [executable, "run", "--", *argv],
        capture_output=True,
        timeout=600,
        check=False,
        env={**os.environ, "TELLTALE_HOME": str(home)},
    )


def _types(home: Path) -> dict[str, list[str]]:
    """Every capture in this home, and the observation types it holds."""
    store = Store(home / "telltale.db")
    return {
        str(row["capture_id"]): [
            str(one["observation_type"])
            for one in store.observations(str(row["capture_id"]))
        ]
        for row in store.captures()
    }


def _assert_one_capture_with(
    found: dict[str, list[str]], wanted: Sequence[str]
) -> None:
    assert len(found) == 1, found
    types = next(iter(found.values()))
    assert any(name in types for name in wanted), sorted(set(types))


@pytest.mark.live
@pytest.mark.skipif(not _enabled(), reason=_REASON)
def test_a_real_claude_session_is_captured(telltale_home: Path) -> None:
    """One `claude -p --model haiku --max-turns 3`, wrapped, and its model request."""
    binary = _binary("claude")

    done = _run(
        telltale_home,
        [binary, "-p", PROMPT, "--model", "haiku", "--max-turns", MAX_TURNS],
    )

    assert done.returncode == 0, done.stderr.decode()
    _assert_one_capture_with(_types(telltale_home), _CLAUDE_REQUEST)


@pytest.mark.live
@pytest.mark.skipif(not _enabled(), reason=_REASON)
def test_a_real_codex_session_is_captured(telltale_home: Path) -> None:
    """One `codex exec`, wrapped, and its model request.

    W1-T3 owns the codex launch plan and the codex provider module. Until both land a
    capture here still exists (the launcher records a child it has no plan for), so a
    failure of this test names which half is missing.
    """
    binary = _binary("codex")

    done = _run(telltale_home, [binary, "exec", PROMPT])

    assert done.returncode == 0, done.stderr.decode()
    _assert_one_capture_with(_types(telltale_home), _CODEX_REQUEST)
