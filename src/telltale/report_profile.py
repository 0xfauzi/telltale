"""Rendering for `telltale profile`, and the four words the output may not contain.

A separate file from report.py, which was at 635 lines: the cut is also the right one on
its own, because everything here is about ONE command and report.py's eight renderers
are shared. What is not separate is the shape: `render_table` is imported rather than
copied, so a profile table and a vector table are the same table.

The word refusal is the load-bearing part. Spec 16 allows "sessions touching payments
used 2.1x the matched-cohort median fresh token burden (n=31; same model/runtime
family)" and refuses "payments has maintainability 0.17", and spec 17.2 says no
`quality_score` or `difficulty_score` is exposed anywhere. A table of per-path
distributions is exactly the shape somebody would add such a column to, so the finished
string is checked before it is printed, the way ADR-014's `forecast.refuse_words`
checks a forecast report. The check reads DATA as well as headings: a group named after
a directory whose name is one of the four words refuses to print, and the refusal says
which word and that the profile can be taken by week instead. That is the intended
trade. A renderer that skipped the data would pass a table whose rows carried the very
number the rule exists to prevent.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from telltale import cli_common as common
from telltale import profile
from telltale.report import UNKNOWN, render_table

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping, Sequence

# Spec 16 and spec 17.2. `maintainability` and `difficulty` name the latent scalar
# ADR-008 refuses, `quality` names the other half of it, and `score` is what any of them
# would be called in a column heading. Inflections go with the stem, as ADR-014's list
# does: "scored" and "scoring" make the same claim as "score".
REFUSED_WORDS = ("maintainability", "quality", "difficulty", "score")
# Every inflection spelled out, rather than a shared stem with an alternation after it.
# The shorter pattern puts a truncated word in the source, and the codespell hook reads
# a truncated word as a misspelling of the whole one, which is what it is.
_REFUSED = re.compile(
    r"\b(maintainability|quality|qualities|difficulty|difficulties"
    r"|score|scores|scored|scoring)\b",
    re.IGNORECASE,
)

COMPOSITION_COLUMNS = (
    "group",
    "captures",
    "providers",
    "models",
    "runtime_majors",
    "content_levels",
    "imported",
    "cohort",
)
ROW_COLUMNS = (
    "group",
    "n",
    "unknown",
    "median",
    "mad_scaled",
    "min",
    "max",
    "unit",
    "coverage",
    "claim_class",
    "ratio_to_cohort_median",
)

_NOTE = """\
This is a natural history and nothing else: observed distributions over the captures of
one repository, grouped by when they ran or by what they edited, and placed against a
cohort design 6.11 would accept. The captures were not assigned to their groups, they
arrived in them, so a group differs from every other group in more than its name.

RATIO_TO_COHORT_MEDIAN is the group's median over the median of the matched cohort
OUTSIDE the group: same provider, runtime major version, model and content level, at
least ten captures outside the group, and at least ten of them measuring this metric. A
cell reading "no cohort (n=k)" names what stopped the comparison; it is not a 1.0 and
not a missing number, and the n inside a FILLED cell is how many of the cohort outside
the group measured this metric. Every number here is comparative, is built from the
evidence rows the reducer wrote, and is stored nowhere: it is a statement about the
captures in this database at this moment, and it changes when the next one arrives.

A row whose MEDIAN is "-" reports that no capture in the group carried a value, and the
COVERAGE word beside it says whether the work did not happen or no surface could have
shown it. N counts the captures that measured the metric and UNKNOWN the rest; nothing
is imputed and no gap is filled.

Spec 16 permits the comparative sentence this table supports and refuses the single
latent number per path that a table of this shape invites. The renderer refuses the
four words such a number would be spelled with, so a column carrying one cannot be
printed."""


class ForbiddenWord(Exception):
    """A profile naming the latent number spec 16 refuses. Raised before printing."""


def refuse_words(text: str) -> str:
    """The text back, or the refusal naming every forbidden word it holds.

    The whole finished string, headings and data together, and BEFORE anything reaches
    stdout. A check that ran over the headings alone would pass a table whose group
    column carried the word, and a check that ran after the print would be a check of
    bytes a reader has already seen.
    """
    found = sorted({match.group(0).lower() for match in _REFUSED.finditer(text)})
    if found:
        raise ForbiddenWord(
            f"a work profile may not use {', '.join(found)}:"
            f" spec 16 and spec 17.2 refuse {', '.join(REFUSED_WORDS)},"
            " and nothing here may present a path as carrying a latent number."
            " A group named after one of those words has to be profiled by week"
        )
    return text


def render(built: Mapping[str, Any]) -> str:
    """The frame, the sample composition, one table per metric, and the note.

    One table per metric rather than one table of 22 times the group count rows: the
    reader is comparing groups against each other and against the cohort, and that
    comparison is a column, not a page. Every row of every table is one (group, metric).
    """
    lines = [_frame(built), "", _composition(built)]
    for family, metric in _metrics(built):
        lines += ["", f"{family} / {metric}", _metric_table(built, family, metric)]
    return refuse_words("\n".join([*lines, "", _NOTE]))


def command(args: argparse.Namespace) -> int:
    """`telltale profile <repo_id>`. Design 6.13: profile.py decides, this prints."""
    store = common.store()
    try:
        built = profile.build(
            store, args.repo_id, args.path, args.by, args.include_backfill
        )
    except profile.Refused as error:
        return common.refuse(str(error))
    # The one print outside cli*.py and report.py, and it is here because the string it
    # prints is the one `refuse_words` has just checked: a caller that received the text
    # and printed it elsewhere could print an unchecked one just as easily.
    print(json.dumps(built, indent=2) if args.json else render(built))  # noqa: T201
    return 0


def add_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """`profile` registers itself here, as cli_forecast and cli_import do."""
    parser = subcommands.add_parser(
        "profile", help="one repository's natural history, by week or subsystem"
    )
    parser.add_argument("repo_id", metavar="REPO_ID")
    parser.add_argument(
        "--path",
        default=None,
        metavar="PREFIX",
        help="only captures that edited under PREFIX; also names the group",
    )
    parser.add_argument(
        "--by",
        default=None,
        choices=profile.GROUPINGS,
        help="week (the default), subsystem, or the --path prefix itself",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the dicts, unknown as null"
    )
    parser.add_argument(
        "--include-backfill",
        action="store_true",
        help="admit imported captures, in cohort-less groups of their own",
    )


def _frame(built: Mapping[str, Any]) -> str:
    """What was profiled, over how many captures, and where the rest of them went."""
    skipped = built["skipped"]
    lines = [
        f"repository {built['repo_id']}",
        f"grouping: {built['by']}    path: {built['path'] or UNKNOWN}"
        f"    captures: {built['grouped']} of {built['captures']} grouped",
    ]
    lines += [
        f"  not grouped: {reason} ({count})"
        for reason, count in sorted(skipped.items())
    ]
    return "\n".join(lines)


def _composition(built: Mapping[str, Any]) -> str:
    """Spec 16's sample composition: what each group is made of, before any number."""
    rows = [
        {
            "group": group["group"],
            **{
                name: _counts(group["composition"].get(name))
                for name in COMPOSITION_COLUMNS[1:-1]
            },
            "cohort": _cohort(group["cohort"]),
        }
        for group in built["groups"]
    ]
    return "SAMPLE COMPOSITION\n" + render_table(rows, COMPOSITION_COLUMNS)


def _counts(found: Any) -> str:
    """`claude 8` or `opus 6, sonnet 2`. A count of captures by a stated cohort key."""
    if isinstance(found, dict):
        return ", ".join(f"{key} {count}" for key, count in found.items())
    return UNKNOWN if found is None else str(found)


def _cohort(found: Mapping[str, Any]) -> str:
    """The cohort this group may be placed against, or why there is none."""
    if "no_ratio" in found:
        return _blank(found)
    named = ", ".join(f"{key}={value}" for key, value in found.items())
    return named


def _metrics(built: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every (family, metric) the groups carry, in the order cohorts.VECTOR gives.

    Read off the first group rather than off VECTOR, so a family added there cannot go
    missing from the tables while the rows behind it are being built.
    """
    groups: Sequence[Mapping[str, Any]] = built["groups"]
    if not groups:
        return []
    return [(str(row["family"]), _metric_of(row)) for row in groups[0]["rows"]]


def _metric_of(row: Mapping[str, Any]) -> str:
    """The metric a row is about. Its Evidence names itself `<metric>_group_median`."""
    return str(row["metric"]).removesuffix("_group_median")


def _metric_table(built: Mapping[str, Any], family: str, metric: str) -> str:
    rows = []
    for group in built["groups"]:
        for row in group["rows"]:
            if row["family"] == family and _metric_of(row) == metric:
                rows.append({"group": group["group"], **_row(row)})
    return render_table(rows, ROW_COLUMNS)


def _row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "n": row["n"],
        "unknown": row["unknown"],
        "median": row["value"],
        "mad_scaled": _round(row["mad_scaled"]),
        "min": row["min"],
        "max": row["max"],
        "unit": row["unit"],
        "coverage": row["coverage"],
        # Design 6.13: never omitted. A distribution over several captures is
        # comparative wherever it is printed, and the ratio beside it says its own.
        "claim_class": row["claim_class"],
        "ratio_to_cohort_median": _ratio(row["ratio"]),
    }


def _ratio(cell: Mapping[str, Any]) -> str:
    """The one cohort-relative column. Never blank, never 1.0, always a statement."""
    if "no_ratio" in cell:
        return _blank(cell)
    return f"{cell['value']}x ({cell['claim_class']}, n={cell['n_metric']})"


def _blank(cell: Mapping[str, Any]) -> str:
    """Why there is no ratio here, as profile.py stated it. Never blank and never 1.0.

    The sentence is built where the counts are, not here, because the reasons that are
    not counts ("the group spans 2 cohorts") are decisions profile.py takes and a
    renderer that re-worded half of them would be a second statement of the rule.
    """
    return str(cell["no_ratio"])


def _round(value: float | None) -> float | None:
    """Six decimals on a scaled MAD. 1.4826 times a median of integers is not one."""
    return None if value is None else round(value, 6)
