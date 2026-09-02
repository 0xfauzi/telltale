"""The `series` and `forecast` subcommands of `telltale`. Design 6.12 and 6.13.

`series build` compiles one capture into the only shape a forecaster takes, `series
check` re-tests the no-look-ahead invariant against the stored rows, and `series list`
says what has been compiled. `forecast backtest` rolls an origin through one stored
series, runs every named forecaster on the identical window and stores the result;
`--forecasters timesfm` is the one spelling that needs the `forecast` extra, and the
adapter is imported inside that branch so every other command runs without torch.
`forecast readiness` is the preflight that says whether that backtest is worth running,
and it is the same eight checks the session summary carries.

cli.py registers these through `add_commands` and dispatches `series` and `forecast`
to the two functions of those names at the bottom of this file.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from telltale import cli_common as common
from telltale import series as compiler
from telltale.forecast import (
    DEFAULT_FORECASTERS,
    DEVICES,
    FORECASTERS,
    HORIZONS,
    TARGETS,
    make,
    readiness,
)
from telltale.forecast import backtest as backtester
from telltale.report import render_table

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence

    from telltale.model import Series


# What `series build` prints per column. `nulls` is what the exclude policy DOES: it
# counts and nothing else, so the reader can see how many windows a forecaster will
# drop before it runs (design 6.12).
_COLUMN_COLUMNS = ("column", "unit", "role", "coverage", "nulls")


def series_build(clock: str, capture_id: str, policy: str) -> int:
    """Compile one capture into a Series and store it. Design 6.12.

    A refusal (an unbuilt clock, a policy this capture cannot satisfy) is exit 2, the
    same code `setup --apply` spends: the command exists, it ran, and it declined.
    """
    store = common.store().open()
    try:
        built = compiler.build(store, clock, common.known(store, capture_id), policy)
        store.put_series(built)
    except compiler.Refused as refused:
        return common.refuse(str(refused))
    finally:
        store.close()
    _print_series(built)
    return 0


def _print_series(built: Series) -> None:
    """The id, the shape, every column with its coverage and its holes, the policy."""
    print(f"{built.series_id}  clock {built.clock}  {len(built.rows)} rows")
    print(f"cohort {json.dumps(built.cohort, sort_keys=True)}")
    print(f"policy {built.missingness_policy}  reducer {built.reducer_version}")
    print(render_table(compiler.column_report(built), _COLUMN_COLUMNS))
    found = ", ".join(str(index) for index in built.changepoints)
    print(f"changepoints {found or 'none'}")


def series_check(series_id: str) -> int:
    """Print the invariant result for one stored series. Exit 1 on a violation."""
    store = common.store()
    found = store.series(series_id)
    if found is None:
        stored = [str(row["series_id"]) for row in store.series_ids()]
        raise SystemExit(
            f"{series_id}: no such series. Stored: {', '.join(stored) or 'none'}"
        )
    violations = compiler.check(store, found)
    print("\n".join(violations) if violations else "ok")
    return 1 if violations else 0


def series_list() -> int:
    store = common.store()
    rows = [
        {**row, "cohort": row["cohort"].get("capture_id")} for row in store.series_ids()
    ]
    print(render_table(rows, ("series_id", "clock", "cohort", "rows", "built_at")))
    return 0


def forecast_backtest(
    series_id: str, target: str, horizon: int, names: Sequence[str], device: str
) -> int:
    """Roll an origin through one stored series and store the run. Design 6.12.

    The forecasters are built BEFORE the store is opened, because building the timesfm
    one loads a 1.32 GB checkpoint and a refusal (an unknown name, a missing extra)
    should not have a writer thread waiting behind it.
    """
    try:
        forecasters = {name: make(name, device) for name in names}
    except KeyError as unknown:
        return common.refuse(
            f"{unknown.args[0]}: no such forecaster. {_forecaster_help()}"
        )
    except ImportError as missing:
        return common.refuse(f"timesfm needs the forecast extra: {missing}")
    store = common.store().open()
    try:
        found = store.series(series_id)
        if found is None:
            return common.refuse(
                f"{series_id}: no such series. Run `telltale series list`."
            )
        run = backtester.run(found, target, horizon, forecasters)
        run_id = backtester.persist(store, run)
    except backtester.Refused as refused:
        return common.refuse(str(refused))
    finally:
        store.close()
    print(backtester.report(run))
    print(f"\nforecast_run_id {run_id}")
    return 0


def forecast_readiness(series_id: str, target: str, horizon: int) -> int:
    """The eight-line preflight of design 6.12. Exit 1 when any line failed.

    Exit 1 rather than 2: the checklist ran and answered, and the answer is that this
    series is not ready. A refusal (an unknown series, a target the registry does not
    carry) is exit 2, as everywhere else.
    """
    store = common.store()
    found = store.series(series_id)
    if found is None:
        return common.refuse(
            f"{series_id}: no such series. Run `telltale series list`."
        )
    try:
        checks = readiness.check(found, target, horizon)
    except backtester.Refused as refused:
        return common.refuse(str(refused))
    print(readiness.report(found, target, horizon, checks))
    return 0 if readiness.ready(checks) else 1


def _forecaster_help() -> str:
    return f"Known: {', '.join(sorted(FORECASTERS))}"


def _forecast_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """`forecast backtest`. Design 6.12 and 6.13; the store is $TELLTALE_HOME's."""
    parent = subcommands.add_parser("forecast", help="backtest a stored series")
    inner = parent.add_subparsers(dest="forecast_command", required=True)
    back = inner.add_parser("backtest", help="rolling-origin backtest, true order")
    back.add_argument("--series", required=True, metavar="ID")
    back.add_argument("--target", required=True, choices=sorted(TARGETS))
    back.add_argument("--horizon", type=int, default=1, choices=HORIZONS)
    back.add_argument(
        "--forecasters",
        default=",".join(DEFAULT_FORECASTERS),
        metavar="A,B,C",
        help=f"default: {','.join(DEFAULT_FORECASTERS)}. timesfm needs the extra",
    )
    back.add_argument("--device", default="cpu", choices=DEVICES)
    ready = inner.add_parser("readiness", help="the eight-line preflight, design 6.12")
    ready.add_argument("--series", required=True, metavar="ID")
    ready.add_argument("--target", required=True, choices=sorted(TARGETS))
    ready.add_argument("--horizon", type=int, default=1, choices=HORIZONS)


def _series_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """`series build`, `series check` and `series list`. Design 6.12 and 6.13."""
    parent = subcommands.add_parser("series", help="compile and check forecast inputs")
    inner = parent.add_subparsers(dest="series_command", required=True)
    make = inner.add_parser("build", help="compile one capture into a Series")
    make.add_argument("--clock", required=True, choices=compiler.CLOCKS)
    make.add_argument("--capture", required=True, metavar="ID")
    make.add_argument("--policy", default="exclude", choices=compiler.POLICIES)
    verify = inner.add_parser("check", help="the no-look-ahead invariant, per row")
    verify.add_argument("series")
    inner.add_parser("list", help="the series snapshots on this disk")


def series(args: argparse.Namespace) -> int:
    if args.series_command == "build":
        return series_build(args.clock, args.capture, args.policy)
    if args.series_command == "check":
        return series_check(args.series)
    return series_list()


def forecast(args: argparse.Namespace) -> int:
    if args.forecast_command == "readiness":
        return forecast_readiness(args.series, args.target, args.horizon)
    names = [name for name in args.forecasters.split(",") if name]
    return forecast_backtest(args.series, args.target, args.horizon, names, args.device)


def add_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    _series_commands(subcommands)
    _forecast_commands(subcommands)
