"""The launchd plist for `telltale daemon`, as text. W6-T4.

Printed, never written. `telltale setup <provider> --print --daemon` prints this plist
and then the provider snippet that points at the port it names, and the owner is the
one who decides whether either of them lands on their disk. That is the owner decision
of 2026-09-01 and AGENTS.md invariant 7, and it is the same rule the settings snippet
already follows: a receiver that outlives the session that asked for it is exactly the
kind of thing a recorder must not install behind somebody's back.

~/Library/LaunchAgents is outside this repository and outside $TELLTALE_HOME, so
nothing here or in cli.py opens it, and `--apply` still prints the refusal.

plistlib rather than a formatted string: a plist is XML, a home directory may contain
`&`, and one un-escaped path would be a file launchd refuses to parse. plistlib escapes
and emits the DOCTYPE Apple's parser expects, so the output is `plutil -lint` clean by
construction rather than by proofreading.
"""

from __future__ import annotations

import plistlib
import shutil
import sys
from pathlib import Path

LABEL = "com.telltale.daemon"
PLIST_NAME = f"{LABEL}.plist"
INSTALL_DIR = "~/Library/LaunchAgents"

# What the owner may do with what this prints, and what Telltale will not do. The
# `launchctl load` line is the whole reason the label and the file name are constants:
# the three have to agree or the instruction does not work.
INSTRUCTIONS = f"""\
# The plist above is PRINTED and nothing else. To run the daemon at login, the owner
# may save the XML and load it. The XML alone, because everything else this command
# prints is a comment or the provider snippet below:
#
#   telltale setup {{provider}} --print --daemon --port {{port}} --level {{level}} | \\
#     sed -n '/<?xml/,/<\\/plist>/p' \\
#     > {INSTALL_DIR}/{PLIST_NAME}
#   launchctl load {INSTALL_DIR}/{PLIST_NAME}
#
# and to stop it: launchctl unload {INSTALL_DIR}/{PLIST_NAME}
#
# Telltale never writes that file and never calls launchctl. {INSTALL_DIR} is outside
# this repository and outside $TELLTALE_HOME, which Telltale treats as read-only
# (AGENTS.md invariant 7); `telltale setup --apply` prints a refusal that says so.
"""


def plist(port: int, level: int, home: Path, binary: str) -> str:
    """The launchd agent that runs `telltale daemon --port PORT --level LEVEL`.

    `binary` is an absolute path because launchd's environment is not a login shell's:
    a bare `telltale` in ProgramArguments is a job that fails to spawn on a machine
    where the tool is installed. See `binary()`.

    `TELLTALE_HOME` is in EnvironmentVariables rather than left out, because the same
    reasoning applies to it: launchd starts the job with almost no environment, so a
    daemon loaded from this plist would otherwise record into `~/.telltale` whatever
    the owner's shell says, and the port they were told to paste into their settings
    would point at a different database from the one their reports read.

    KeepAlive true, which launchd documents as implying RunAtLoad. A daemon whose port
    is held exits 1 with one line (cli.daemon), so launchd will restart it, and
    launchd.plist(5) states the default throttle: a job is not spawned more than once
    every 10 seconds. Both the line and the retries land in the log files below, which
    is why they are named at all.
    """
    agent = {
        "Label": LABEL,
        "ProgramArguments": [
            binary,
            "daemon",
            "--port",
            str(port),
            "--level",
            str(level),
        ],
        "EnvironmentVariables": {"TELLTALE_HOME": str(home)},
        "RunAtLoad": True,
        "KeepAlive": True,
        # Under $TELLTALE_HOME, which is the one directory Telltale writes to. launchd
        # creates both files; the daemon prints one line per capture as it happens
        # (cli.daemon flushes for exactly this reason), so the out file is the log of
        # what was recorded without a launcher.
        "StandardOutPath": str(home / "daemon.log"),
        "StandardErrorPath": str(home / "daemon.err"),
    }
    return plistlib.dumps(agent, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")


def binary() -> str:
    """The absolute path of the `telltale` that is running, for ProgramArguments.

    `sys.argv[0]` resolved, which for the console script uv installs is the path in
    `.venv/bin`. A run through `python -m` or a relative argv[0] that no longer exists
    falls back to `which telltale`, and to the bare name when there is none: launchd
    cannot spawn that, and printing it is how the owner finds out, rather than printing
    a path that happens to exist on this machine and is not the tool they ran.
    """
    argv0 = Path(sys.argv[0] or "telltale")
    if argv0.name and argv0.exists():
        return str(argv0.resolve())
    return shutil.which("telltale") or "telltale"
