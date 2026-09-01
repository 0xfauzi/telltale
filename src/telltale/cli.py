"""The `telltale` command.

A placeholder with two behaviours: report the version, and refuse to pretend the
self-check works. Every other command named in the design (run, daemon, sessions,
show, timeline, explain, compare, rebuild, purge, schema, export, experiment, series,
forecast) arrives with the task that implements the thing it prints.

`doctor` exits 2 rather than 0 on purpose. A self-check that has not been written
must not report success: a caller wiring Telltale into a launcher script would read
exit 0 as "every surface round-trips" and would only find out otherwise from missing
data.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from telltale import __version__

if TYPE_CHECKING:
    from collections.abc import Sequence

# Exit code for a command that exists but has no implementation yet. Distinct from 1
# (a real failure the caller should read) and from 0.
_NOT_IMPLEMENTED = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telltale",
        description="A local flight recorder for coding-agent sessions.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser(
        "doctor",
        help="check that every capture surface round-trips (not implemented yet)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `telltale` console script; returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        print("doctor: not implemented")
        return _NOT_IMPLEMENTED
    parser.print_help()
    return 0
