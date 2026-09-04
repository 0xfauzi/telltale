"""The `telltale export` and `telltale schema` subcommands. Design 6.13.

    telltale export --format jsonl|parquet --out DIR [--capture ID]
    telltale schema

Both print and neither writes to the database: `export` opens the store the way every
reading command does, without a writer thread, and `schema` opens nothing at all.

In its own file rather than in cli.py for the reason cli_advise.py and cli_import.py
are: cli.py stands at 754 lines against the 800-line ratchet, and these are two
commands with their own vocabulary. cli.py registers them through `add_commands` below.

`schema` prints the allowlist and the four durable shapes as JSON, and every value in it
is read out of the tables and the dataclasses rather than restated here. A schema
command that carried its own copy of the field list would be a second answer to the
question it exists to answer, and the first time the two disagreed the printed one would
be the one somebody believed.
"""

from __future__ import annotations

import json
from dataclasses import fields
from typing import TYPE_CHECKING, Any

from telltale import __version__, export
from telltale import cli_common as common
from telltale.allowlist import ALLOWLIST, Kind
from telltale.model import (
    CLAIM_CLASSES,
    COVERAGE,
    Activity,
    Evidence,
    Observation,
    Series,
)
from telltale.report import render_table
from telltale.sanitize import NEVER_PERSIST

if TYPE_CHECKING:
    import argparse

# The four durable shapes of design 6.1, in the order that file names them.
_SHAPES = (Observation, Activity, Evidence, Series)
_TABLE_COLUMNS = ("table", "rows")


def export_command(fmt: str, out: str, capture_id: str | None) -> int:
    """Write the store to `out` and print what went into each file."""
    store = common.store()
    known = None if capture_id is None else common.known(store, capture_id)
    try:
        counts = export.export(store, out, fmt, known)
    except ValueError as refusal:
        return common.refuse(f"telltale export: {refusal}")
    rows = [{"table": name, "rows": count} for name, count in counts.items()]
    print(render_table(rows, _TABLE_COLUMNS))
    scope = "the whole store" if known is None else f"capture {known}"
    print(
        f"\nexported {scope} as {fmt}: {sum(counts.values())} rows in"
        f" {len(counts)} file(s) plus {export.MANIFEST}"
    )
    return 0


def schema_command() -> int:
    """The allowlist and the four durable shapes, as JSON on stdout. Design 6.13."""
    print(json.dumps(_schema(), indent=2, sort_keys=True))
    return 0


def _schema() -> dict[str, Any]:
    return {
        "telltale_version": __version__,
        # {observation type: {field: the Kind that cleans it}}. This IS the allowlist:
        # a field absent from it is dropped by sanitize.py and raises a diagnostic.
        "observation_types": {
            obs_type: {name: kind.value for name, kind in sorted(allowed.items())}
            for obs_type, allowed in sorted(ALLOWLIST.items())
        },
        # The closed vocabulary the values above come from. sanitize.py cleans a
        # field by its Kind and by nothing else, so this list is the whole set of
        # ways a stored value can have been treated.
        "kinds": [kind.value for kind in Kind],
        # Removed at every depth and at every content level, whatever the allowlist
        # says. Printed because "this field is not listed" and "this field can never be
        # stored" are different answers and only one of them can change.
        "never_persist": sorted(NEVER_PERSIST),
        "shapes": {
            shape.__name__: [spec.name for spec in fields(shape)] for shape in _SHAPES
        },
        "claim_classes": list(CLAIM_CLASSES),
        "coverage": list(COVERAGE),
    }


def add_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """`telltale export --format F --out DIR [--capture ID]` and `telltale schema`."""
    writing = subcommands.add_parser(
        "export", help="write every table to DIR (design 6.13)"
    )
    writing.add_argument(
        "--format", default="jsonl", choices=export.FORMATS, dest="fmt"
    )
    writing.add_argument("--out", required=True, metavar="DIR")
    writing.add_argument(
        "--capture",
        default=None,
        metavar="ID",
        help="restrict observations, activities, evidence and diagnostics to one"
        " capture; series snapshots and forecast runs are not per capture and are"
        " exported whole",
    )
    subcommands.add_parser(
        "schema", help="the allowlist and the durable shapes as JSON"
    )


def command(args: argparse.Namespace) -> int:
    return export_command(args.fmt, args.out, args.capture)
