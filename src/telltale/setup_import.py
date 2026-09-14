"""The launchd plist that reruns a transcript backfill on an interval, as text. W11-T1.

Some agents write their sessions to disk and export no live telemetry: the Claude
desktop app's local-agent-mode is one, and the daemon has nothing to receive from it.
What reaches the store instead is `telltale import`, rerun. `telltale setup <provider>
--print --import-schedule ROOT` prints the launchd agent that reruns it, and prints it
only, for the reason setup_daemon.py gives at length: a job that outlives the command
that asked for it is the owner's to install (AGENTS.md invariant 7). `--apply` refuses
before anything here runs.

The same shape as setup_daemon.plist, built with plistlib for the same reason: the root
this exists for is under `~/Library/Application Support`, and a path with a space or an
`&` in it is exactly what a formatted string gets wrong and plistlib escapes.

Rerunning is safe because `importer.import_files` skips a capture that is already
stored. That skip is whole-session, which is also the limit INSTRUCTIONS states: a
session still being written when a run fires is stored as far as it had got, and every
later run skips it. Measured in W11-T1 on a copy of the real local-agent-mode root: a
transcript truncated to half, imported, restored to full and imported again, stayed at
14744 observations where a fresh store holds 15248.
"""

from __future__ import annotations

import argparse
import hashlib
import plistlib
import re
import shlex
from pathlib import Path

from telltale import setup_daemon

LABEL_PREFIX = "com.telltale.import"
DEFAULT_INTERVAL_S = 3600

# `telltale import` names its backfills by what the files are; `telltale setup` names
# its providers. The one backfill each provider writes.
KINDS = {"claude": "claude-transcripts", "codex": "codex-rollouts"}

# The sed quits at the first `</plist>`: the line below that names the sed contains
# `<?xml` itself, so a range alone reopens there and saves every comment after it.
INSTRUCTIONS = """\
# The plist above is PRINTED and nothing else. To run
# `telltale import {kind}` over the root it names once at login and then
# every {interval} seconds, the owner may save the XML and load it. The XML alone,
# because everything else this command prints is a comment or the provider snippet
# below:
#
#   {command} | \\
#     sed -n '/<?xml/,/<\\/plist>/p;/<\\/plist>/q' \\
#     > {install_dir}/{label}.plist
#   launchctl load {install_dir}/{label}.plist
#
# and to stop it: launchctl unload {install_dir}/{label}.plist
#
# A rerun adds nothing for a session already stored, and that is also its limit: a
# session is imported whole or skipped whole, so one still being written when a run
# fires is stored as far as it had got, and no later run adds the rest.
#
# Telltale never writes that file and never calls launchctl. {install_dir} is outside
# this repository and outside $TELLTALE_HOME, which Telltale treats as read-only
# (AGENTS.md invariant 7); `telltale setup --apply` prints a refusal that says so.
"""


def label(root: Path, kind: str) -> str:
    """`com.telltale.import.<root's own name>.<8 hex>`, stable for one kind and root.

    The digest is of the kind and the whole path, so two roots that share a last
    component get two labels, two plist files and two pairs of logs rather than one
    job that overwrites the other.
    """
    name = re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-") or "root"
    digest = hashlib.sha256(f"{kind}:{root}".encode()).hexdigest()[:8]
    return f"{LABEL_PREFIX}.{name}.{digest}"


def plist(
    root: Path, interval_s: int, home: Path, binary: str, kind: str, level: int
) -> str:
    """The launchd agent that runs `telltale import KIND --root ROOT --level LEVEL`.

    StartInterval rather than KeepAlive: the daemon is one process that must stay up,
    and this is a job that runs to completion and exits, which KeepAlive would restart
    at once. launchd.plist(5): a firing is missed while the job is still running or
    the machine is asleep, and a missed firing costs nothing here because the next run
    reads the same files. RunAtLoad gives the one catch-up run at login.

    `root`, `binary` and `TELLTALE_HOME` are all absolute and all in the plist, for the
    reason setup_daemon.plist gives: launchd starts the job with no login shell, no
    `~` expansion and a PATH of /usr/bin:/bin:/usr/sbin:/sbin.
    """
    name = label(root, kind)
    agent = {
        "Label": name,
        "ProgramArguments": [
            binary,
            "import",
            kind,
            "--root",
            str(root),
            "--level",
            str(level),
        ],
        "EnvironmentVariables": {"TELLTALE_HOME": str(home)},
        "StartInterval": interval_s,
        "RunAtLoad": True,
        # Named after the label so that two scheduled roots do not share a log. Each
        # run prints the importer's one summary line to the out file.
        "StandardOutPath": str(home / f"{name}.log"),
        "StandardErrorPath": str(home / f"{name}.err"),
    }
    return plistlib.dumps(agent, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")


def add_flags(parser: argparse.ArgumentParser) -> None:
    """The two `setup` flags this module answers. Here, to keep cli.py from growing."""
    parser.add_argument(
        "--import-schedule",
        default=None,
        metavar="ROOT",
        help="also print a launchd plist that reruns `telltale import` over ROOT",
    )
    parser.add_argument(
        "--import-interval",
        type=_seconds,
        default=None,
        metavar="SECONDS",
        help=f"how often that plist reruns it (default: {DEFAULT_INTERVAL_S})",
    )


def text(args: argparse.Namespace, home: Path) -> str:
    """The plist and INSTRUCTIONS for `--import-schedule`, or "" when it was not given.

    Refused rather than ignored: `--import-interval` alone, which would otherwise read
    as a schedule having been set, and a ROOT that is not a directory, which would be a
    job that fails on every firing. SystemExit, as cli._level refuses a bad level.
    """
    if args.import_schedule is None:
        if args.import_interval is not None:
            raise SystemExit(
                "telltale setup: --import-interval needs --import-schedule"
            )
        return ""
    root = Path(args.import_schedule).expanduser().absolute()
    if not root.is_dir():
        raise SystemExit(f"telltale setup: {root} is not a directory")
    interval = (
        DEFAULT_INTERVAL_S if args.import_interval is None else args.import_interval
    )
    kind = KINDS[args.provider]
    home = home.absolute()
    command = [
        "telltale", "setup", args.provider, "--print", "--import-schedule", str(root),
        "--import-interval", str(interval), "--level", str(args.level),
    ]  # fmt: skip
    instructions = INSTRUCTIONS.format(
        kind=kind,
        interval=interval,
        command=shlex.join(command),
        install_dir=setup_daemon.INSTALL_DIR,
        label=label(root, kind),
    )
    xml = plist(root, interval, home, setup_daemon.binary(), kind, args.level)
    # The blank line after the comments is the one the daemon's `print` leaves.
    return f"{xml}{instructions}\n"


def _seconds(text: str) -> int:
    """A whole number of seconds above zero: launchd has no meaning for the rest."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{value} is not a positive number of seconds")
    return value
