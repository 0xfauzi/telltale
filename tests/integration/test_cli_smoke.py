"""The installed console script starts and reports the installed version.

This is the smallest end-to-end fact there is: packaging, entry point, import chain and
argument parsing all have to hold for it to pass. It runs the real binary in a real
subprocess rather than calling main() in-process, because calling main() would prove
nothing about `[project.scripts]`, which is the part that breaks.

The expected string is NOT spelled here. The subprocess prints what argparse got from
importlib.metadata; the test compares it against the same metadata read in this process.
Spelling "0.0.1" in both places would only prove that the test author can copy a number.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from telltale import __version__


@pytest.mark.integration
def test_console_script_reports_the_installed_version() -> None:
    executable = shutil.which("telltale")
    assert executable is not None, "no `telltale` on PATH: run `uv sync` first"
    # Not the import fallback: if this fires, the distribution metadata is missing
    # and the comparison below would be trivially true on both sides.
    assert __version__ != "0+unknown"

    completed = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == __version__
