"""Rendering for the CLI. Design 6.13: report.py renders what cli.py decides to print.

One function so far. The commands that print evidence (`show`, `timeline`, `explain`,
`compare`) arrive with the tasks that compute it, and each brings its own renderer here.

Two rules this file holds for all of them. A value that is None or absent prints as `-`
and never as blank or as 0: design invariant 5 says unknown stays unknown, and a report
is the last place where "no compactions were observed" could turn into "0 compactions".
And a number is formatted by the caller, from its unit, because `Evidence.value` is a
SQLite REAL and float(2.0) is not the integer 2 that a request count is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

UNKNOWN = "-"


def render_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    """A fixed-width table of `columns` taken from `rows`, header first.

    `columns` is the order and the selection: a key a row carries and `columns` does not
    name is not printed, and a column no row carries prints as a column of `-`.
    """
    if not columns:
        return ""
    header = [column.upper() for column in columns]
    cells = [[_cell(row.get(column)) for column in columns] for row in rows]
    widths = [
        max(len(line[index]) for line in [header, *cells])
        for index in range(len(columns))
    ]
    rule = ["-" * width for width in widths]
    return "\n".join(_line(line, widths) for line in [header, rule, *cells])


def _line(cells: Sequence[str], widths: Sequence[int]) -> str:
    # rstrip because the last column's padding is invisible in a terminal and visible
    # in every diff of a captured output.
    pairs = zip(cells, widths, strict=True)
    return "  ".join(cell.ljust(width) for cell, width in pairs).rstrip()


def _cell(value: Any) -> str:
    return UNKNOWN if value is None else str(value)
