"""`telltale purge`: one capture, or every diagnostics row older than N days.

Moved out of cli.py at the wave 6 gate so that file stays under the length ratchet; the
two deletions and their refusals are exactly what cli.py held, and this module registers
its own subcommand the way cli_export.py does.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from telltale import cli_common as common
from telltale import config
from telltale.store import Store

if TYPE_CHECKING:
    import argparse


def purge(capture_id: str | None, older_than_days: int | None) -> int:
    """Delete one capture, or every diagnostics row older than N days. Design 6.13.

    Two deletions, never both in one run: a capture leaving this disk takes its
    diagnostics with it and rebuilds what is left, and retention takes rows from every
    capture and rebuilds nothing. A command that did both would report one count for two
    different questions.
    """
    if (capture_id is None) == (older_than_days is None):
        return common.refuse(
            "purge takes either a capture id or --diagnostics-older-than N, not both"
            " and not neither"
        )
    if older_than_days is not None:
        return _purge_diagnostics(older_than_days)
    return _purge_capture(str(capture_id))


def _purge_diagnostics(older_than_days: int) -> int:
    """`purge --diagnostics-older-than N`. store.purge_diagnostics is the one age-based
    deletion in the system (design 6.5), and this is its only caller.

    0 and negatives are refused. `now - 0 days` is now, so `--diagnostics-older-than 0`
    would delete every diagnostics row in the store while reading as a retention window,
    and a negative one would delete rows from the future as well.
    """
    if older_than_days < 1:
        return common.refuse(
            f"purge --diagnostics-older-than {older_than_days}: N is a number of days"
            " and must be 1 or more. 0 is `now`, so it would delete every diagnostics"
            " row in the store, and a negative N would take rows stamped ahead of now"
            " with them."
        )
    store = Store(config.db_path()).open()
    try:
        cutoff = _cutoff(older_than_days)
        deleted = store.purge_diagnostics(older_than_days, cutoff=cutoff)
    finally:
        store.close()
    print(f"purged {deleted} diagnostics row(s) with ingest_ts before {cutoff}")
    return 0


def _cutoff(older_than_days: int) -> str:
    """The exact cutoff passed to the store and printed with the deletion count."""
    stamp = datetime.now(UTC) - timedelta(days=older_than_days)
    return stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _purge_capture(capture_id: str) -> int:
    store = Store(config.db_path()).open()
    try:
        if capture_id not in {str(row["capture_id"]) for row in store.captures()}:
            print(f"purge: no capture {capture_id} in {store.path}")
            return common.REFUSED
        diagnostics = len(store.diagnostics(capture_id))
        observations = store.purge(capture_id)
    finally:
        store.close()
    print(
        f"purged {capture_id}: {observations} observations, {diagnostics} diagnostics"
    )
    return 0


def add_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    removal = subcommands.add_parser(
        "purge", help="delete one capture, or diagnostics older than N days"
    )
    removal.add_argument("capture_id", metavar="CAPTURE_ID", nargs="?", default=None)
    removal.add_argument(
        "--diagnostics-older-than",
        type=int,
        default=None,
        metavar="N",
        help="delete every diagnostics row older than N days instead of a capture",
    )


def command(args: argparse.Namespace) -> int:
    return purge(args.capture_id, args.diagnostics_older_than)
