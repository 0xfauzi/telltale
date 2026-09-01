"""tests/integration/ is the only place a test may live, and this is the enforcement.

Why by construction rather than by convention: the owner's rule is that Telltale is
verified by running the real system, so a unit test that stubs the store, the receiver
or a provider would be a test of the stub. A directory is the cheapest boundary that a
reader and a hook can both see.

TWO hooks, because one of them cannot fire on the command everybody actually runs.
[tool.pytest.ini_options] sets `testpaths = ["tests/integration"]`, so a bare `uv run
pytest` never descends into tests/ at all and `pytest_collect_file` is never called
for a file outside this directory. Measured: with a stray tests/test_probe.py present,
the collect hook alone lets the run finish green. `pytest_configure` runs once per
session whatever the collection roots are, so the filesystem scan is what makes the
default command refuse. The collect hook stays for the explicit invocations
(`pytest tests/`), where it names the file at the moment pytest opens it.

The third copy of this rule is the tests-live-under-integration pre-commit hook, the
one that runs on a server where --no-verify cannot reach.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parent
_TESTS_ROOT = _INTEGRATION_ROOT.parent


def _is_stray(path: Path) -> bool:
    """True for a pytest-collectable test file that sits outside tests/integration/."""
    return not path.resolve().is_relative_to(_INTEGRATION_ROOT)


def _stray_test_files() -> list[Path]:
    # Both of pytest's default python_files patterns, so the scan and the pre-commit
    # hook answer the same question about the same set of files.
    candidates = [*_TESTS_ROOT.rglob("test_*.py"), *_TESTS_ROOT.rglob("*_test.py")]
    return sorted({path for path in candidates if _is_stray(path)})


def _refuse(paths: list[Path], root: Path) -> None:
    listed = ", ".join(str(path.relative_to(root)) for path in paths)
    raise pytest.UsageError(
        f"tests live under tests/integration/ only; move or delete: {listed}"
    )


def pytest_configure(config: pytest.Config) -> None:
    stray = _stray_test_files()
    if stray:
        _refuse(stray, Path(config.rootpath))


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> None:
    if _is_stray(file_path):
        _refuse([file_path], Path(parent.config.rootpath))
    return None
