"""The `telltale import` subcommand. Design 6.3 and 6.13, wave 2.

`import` reads the session files a provider has already written into captures of their
own, through the same parsers and the same sanitizer a live capture uses. `--dry-run`
counts and writes nothing, which is the half the owner sees first
(docs/design/02-protocol.md).

`import export` is the third kind and the only one whose source Telltale itself wrote:
it reads a directory `telltale export --format jsonl` produced and puts the observations
back. It shares this subcommand rather than getting one of its own because the shape is
the same shape - a root, a dry run that counts, and idempotence by capture id - and
export.py holds everything that is different, because the reader and the writer of one
file format have to agree.

Split out of cli.py on 2026-09-02, when it stood at 764 lines against the 800-line
ratchet and W3-T1 had to add `outcome` and two clock options. Nothing here changed in
the move: the same functions, the same parser, the same printed bytes. cli.py registers
the subcommand through `add_commands` and dispatches `import` to `command` below, the
way it already does for cli_forecast.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import cli_common as common
from telltale import config, export, importer
from telltale.report import render_table
from telltale.store import Store

if TYPE_CHECKING:
    import argparse


# The kind whose root is an export directory rather than a provider's own. It has no
# default root, no project and no date to select on, and its content level is a property
# of every capture in the file rather than of this command.
EXPORT_KIND = "export"

# What the dry-run table prints per group. A group is a HASHED project slug or a day,
# never a directory name: design 12.1, and docs/log/W2-T2.md.
_GROUP_COLUMNS = ("group", "files", "lines", "bytes")
_GROUP_ROWS = 20


def import_command(
    kind: str,
    root: str | None,
    since: str | None,
    project: str | None,
    dry_run: bool,
    level: int,
) -> int:
    """`telltale import claude-transcripts|codex-rollouts`. Design 6.3, wave 2.

    The dry run opens no database and creates none: the owner decision of 2026-09-01
    is that the counts are reported before the import runs, and a command that made a
    file in order to report them would have written before it was allowed to.
    """
    if kind == EXPORT_KIND:
        return _export_kind(root, since, project, dry_run)
    where = Path(root).expanduser() if root else importer.default_root(kind)
    if not where.is_dir():
        return common.refuse(f"telltale import: {where} is not a directory")
    try:
        if dry_run:
            counts = importer.dry_run(where, kind, since, project, _quiet())
            return _print_dry_run(counts)
        return _import(where, kind, since, project, level)
    except ValueError as refusal:
        return common.refuse(f"telltale import: {refusal}")


def _quiet() -> Store | None:
    """The store to check for captures already imported, or None when there is none."""
    path = config.db_path()
    return Store(path) if path.exists() else None


def _print_dry_run(counts: dict[str, Any]) -> int:
    print(f"{counts['kind']} under {counts['root']}")
    print(
        f"files {counts['files']}  sessions {counts['sessions']}"
        f"  lines {counts['lines']}  bytes {counts['bytes']}"
    )
    print(f"first {counts['first_ts'] or 'none'}  last {counts['last_ts'] or 'none'}")
    already = counts["already_imported"]
    print(f"already imported {'no database yet' if already is None else already}")
    unreadable = counts["unreadable"]
    print(f"unreadable {len(unreadable)}")
    for row in unreadable[:_GROUP_ROWS]:
        print(f"  {row['file']} {row['reason']}")
    _more(len(unreadable))
    groups = counts["groups"]
    print(render_table(groups[:_GROUP_ROWS], _GROUP_COLUMNS))
    _more(len(groups))
    print("dry run: nothing was written")
    return 0


def _more(total: int) -> None:
    """The tail of a list this table cut. A cut nobody names is a wrong count."""
    if total > _GROUP_ROWS:
        print(f"... {total - _GROUP_ROWS} more")


def _import(
    where: Path, kind: str, since: str | None, project: str | None, level: int
) -> int:
    found = list(importer.scan(where, kind, since, project))
    store = Store(config.db_path()).open()
    try:
        result = importer.import_files(store, found, level)
    finally:
        store.close()
    print(
        f"imported {result['captures']} capture(s) from {len(found)} file(s):"
        f" {result['observations']} observations, {result['skipped']} already stored,"
        f" {result['unreadable']} unreadable, {result['collisions']} colliding,"
        f" {result['diagnostics']} diagnostics"
    )
    return 0


def _export_kind(
    root: str | None, since: str | None, project: str | None, dry_run: bool
) -> int:
    """`telltale import export --root DIR [--dry-run]`. export.py does the reading.

    `--since` and `--project` are refused rather than ignored. They select a provider's
    files by the date the session ran and by the project directory it ran in, and an
    export directory has neither, so a run that silently did nothing with them would
    report a count for a filter it never applied.
    """
    unused = [
        name
        for name, value in (("--since", since), ("--project", project))
        if value is not None
    ]
    if unused:
        return common.refuse(
            f"telltale import export does not take {' or '.join(unused)}."
            " A provider's own files are selected by the date the session ran and by"
            " the project directory it ran in, and an export directory has neither."
        )
    if root is None:
        return common.refuse(
            "telltale import export: --root DIR is required. An export is written"
            " wherever `telltale export --out` put it, so there is no default."
        )
    where = Path(root).expanduser()
    if not where.is_dir():
        return common.refuse(f"telltale import: {where} is not a directory")
    try:
        return _read_export(where, dry_run)
    except ValueError as refusal:
        return common.refuse(f"telltale import export: {refusal}")


def _read_export(where: Path, dry_run: bool) -> int:
    """The dry run opens no database and creates none, as the backfill dry run does not.

    `Store(path)` starts no writer thread and touches no file, and
    `export.import_export` asks it only which captures exist, which is `set()` when the
    database is not there.
    """
    if dry_run:
        counted = export.import_export(Store(config.db_path()), where, True)
        return _print_export(counted, dry_run=True)
    store = Store(config.db_path()).open()
    try:
        result = export.import_export(store, where, False)
    finally:
        store.close()
    return _print_export(result, dry_run=False)


def _print_export(result: dict[str, Any], dry_run: bool) -> int:
    print(f"export at {result['root']}, written {result['exported_at']}")
    print(
        f"captures {result['captures']} new, {result['skipped_captures']} already"
        f" stored; observations {result['observations']} new,"
        f" {result['skipped']} skipped"
    )
    _print_notes("allowlist drift", result["drift"])
    _print_notes("commands the current rules re-normalize", result["commands"])
    if dry_run:
        print("dry run: nothing was written")
        return 0
    lost = result["dropped"]
    print(
        f"added {result['added']} observations and {result['diagnostics']}"
        f" diagnostics, rebuilt {result['rebuilt']} capture(s)"
        + (f", LOST {lost} row(s) to a full queue" if lost else "")
    )
    return 0


def _print_notes(what: str, counted: dict[str, int]) -> None:
    """The gate's two allowed differences, named. A count with no names is a rumour."""
    if not counted:
        return
    listed = ", ".join(
        f"{name} {rows}" for name, rows in sorted(counted.items(), key=_by_rows)
    )
    print(
        f"{what}: {sum(counted.values())} row(s) over {len(counted)} field(s): {listed}"
    )


def _by_rows(item: tuple[str, int]) -> tuple[int, str]:
    return -item[1], item[0]


def add_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """`telltale import <kind> [--root] [--since] [--project] [--dry-run]`.

    One subcommand with a positional kind rather than two: the two backfills differ in
    the directory they read and the parser they hand a line to, and nothing else.
    """
    backfill = subcommands.add_parser(
        "import", help="read sessions the provider already wrote (design 6.3)"
    )
    backfill.add_argument("kind", choices=(*importer.KINDS, EXPORT_KIND))
    backfill.add_argument(
        "--root",
        default=None,
        metavar="DIR",
        help="default: the provider's own; REQUIRED for the export kind, which has no"
        " default location",
    )
    backfill.add_argument(
        "--since",
        default=None,
        metavar="DATE",
        help="YYYY-MM-DD, compared against the file's first provider timestamp",
    )
    backfill.add_argument(
        "--project",
        default=None,
        metavar="SLUG",
        help="one ~/.claude/projects directory only (claude-transcripts)",
    )
    backfill.add_argument(
        "--dry-run", action="store_true", help="count and write nothing"
    )
    backfill.add_argument("--level", type=int, default=None, choices=(0, 1, 2))


def command(args: argparse.Namespace, level: int) -> int:
    """The dispatch cli.py's table calls. The content level is resolved by cli.py.

    `--level` is refused for the export kind rather than ignored: the level of an
    exported capture is the one it was RECORDED at, which export.py reads off each
    capture's own `telltale.capture_started` row, and a flag that silently did nothing
    would read as the import having honoured it.
    """
    if args.kind == EXPORT_KIND and args.level is not None:
        return common.refuse(
            "telltale import export: --level is refused. An exported capture keeps the"
            " content level it was recorded at, read off its capture_started row."
        )
    return import_command(
        args.kind, args.root, args.since, args.project, args.dry_run, level
    )
