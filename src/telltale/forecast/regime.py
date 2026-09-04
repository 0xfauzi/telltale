"""The refusal to forecast across a policy intervention. Spec 14.6, W6-T2."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telltale.forecast.backtest import Refused
from telltale.series_regime import REGIMES, active_entries

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping

    from telltale.model import Series
    from telltale.store import Store

__all__ = ["REGIMES", "banner", "check", "resolve"]


def entries(series: Series) -> list[dict[str, Any]]:
    """The intervention boundaries the series cohort carries, in boundary order."""
    return sorted(
        active_entries(series.cohort), key=lambda one: int(one["boundary_index"])
    )


def check(series: Series, pooled: bool) -> list[str]:
    """The refusal lines for one series, and an empty list when there is no refusal."""
    if pooled:
        return []
    found = entries(series)
    if not found:
        return []
    return [
        f"{series.series_id}: this series pools across a policy intervention, so a"
        " forecast of it scores two regimes as one history (spec 14.6).",
        *(_named(series, one) for one in found),
        "Build one side of the boundary and forecast that:",
        *_commands(series, found[0]),
        "or pass --pooled to forecast across it anyway, which names the boundary on the"
        " first line of the report and stores it on the run.",
    ]


def _named(series: Series, one: Mapping[str, Any]) -> str:
    return (
        f"  intervention {one['advisory_id']} at row {one['boundary_index']}"
        f" of {len(series.rows)} ({one['ts']})"
    )


def _commands(series: Series, one: Mapping[str, Any]) -> list[str]:
    """The two builds that answer the refusal, spelled for this series."""
    repo_id = series.cohort.get("repo_id") or "<repo_id>"
    return [
        f"  telltale series build --clock {series.clock} --repo {repo_id}"
        f" --regime {word} --intervention {one['advisory_id']}"
        for word in REGIMES
    ]


def resolve(store: Store, series_id: str, pooled: bool) -> Series:
    """The stored series, refused when it is not there or may not be pooled."""
    found = store.series(series_id)
    if found is None:
        raise Refused(f"{series_id}: no such series. Run `telltale series list`.")
    lines = check(found, pooled)
    if lines:
        raise Refused("\n".join(lines))
    return found


def banner(series: Series, pooled: bool) -> str:
    """The first lines of a pooled report, or "" when the run pooled across nothing."""
    found = entries(series) if pooled else []
    if not found:
        return ""
    named = "\n".join(
        f"pooled across intervention {one['advisory_id']} at row"
        f" {one['boundary_index']} of {len(series.rows)}"
        for one in found
    )
    return f"{named}\n\n"


def pooled_across(series: Series, pooled: bool) -> list[str]:
    """The advisory ids a pooled run crossed. Empty when it crossed none."""
    return [str(one["advisory_id"]) for one in (entries(series) if pooled else [])]


def add_pooled(parser: argparse.ArgumentParser) -> None:
    """`--pooled`, on every forecast subcommand that runs a forecaster."""
    parser.add_argument(
        "--pooled",
        action="store_true",
        help="forecast across a policy intervention anyway (spec 14.6). The boundary"
        " is the report's first line and is stored on the run",
    )
