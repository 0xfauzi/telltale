"""Series compilation and forecast commands, including policy regime checks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from telltale import cli_common as common
from telltale import series as compiler
from telltale import series_lineage as lineage
from telltale.forecast import (
    BASELINE_NAMES,
    DEFAULT_FORECASTERS,
    DEVICES,
    FORECASTERS,
    HORIZONS,
    ORDERING_BLOCK,
    ORDERING_ROW,
    ORDERING_TRUE,
    SCENARIO_HORIZONS,
    TARGETS,
    make,
    readiness,
)
from telltale.forecast import ablate as ablator
from telltale.forecast import backtest as backtester
from telltale.forecast import candidate as protocol
from telltale.forecast import decide as decider
from telltale.forecast import placebo as placebos
from telltale.forecast import regime as regimes
from telltale.forecast import scenario as scenarios
from telltale.forecast import scenario_report as scenario_page
from telltale.report import render_table

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Mapping, Sequence

    from telltale.model import Series
    from telltale.store import Store


# What `series build` prints per column. `nulls` is what the exclude policy DOES: it
# counts and nothing else, so the reader can see how many windows a forecaster will
# drop before it runs (design 6.12).
_COLUMN_COLUMNS = ("column", "unit", "role", "coverage", "nulls")


def series_build(args: argparse.Namespace) -> int:
    """Compile one history into a Series and store it. Design 6.12.

    The key is a capture id on the request clock and a repo_id on the other two, so
    only the first is checked against the captures table: a repo_id that names no
    capture is refused by the compiler, with the count of captures it read.

    """
    key = args.capture or args.repo
    store = common.store().open()
    try:
        resolved = common.known(store, key) if args.clock == "request" else key
        built = compiler.build(
            store, args.clock, resolved, args.policy, args.regime, args.intervention
        )
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
    print(f"cohort {json.dumps(_headline(built.cohort), sort_keys=True)}")
    print(f"policy {built.missingness_policy}  reducer {built.reducer_version}")
    print(render_table(compiler.column_report(built), _COLUMN_COLUMNS))
    found = ", ".join(str(index) for index in built.changepoints)
    print(f"changepoints {found or 'none'}")
    low = [
        meta.row_key for meta in built.row_meta if lineage.LOW_CONFIDENCE in meta.flags
    ]
    print(f"low_confidence rows {len(low)}{_named(low)}")
    _print_dropped(built.cohort.get("dropped"))


def _headline(cohort: Mapping[str, Any]) -> dict[str, Any]:
    """The cohort without its two lists, which are printed as counts and a table.

    Nothing is hidden: `captures` is `len(captures)` on the same line and `dropped` is
    the table below it. A cohort holding 37 capture ids on one line is a line nobody
    reads, and the two lists are what the reader has to be able to check.
    """
    listed = cohort.get("captures")
    trimmed = {name: value for name, value in cohort.items()
               if name not in ("captures", "dropped")}  # fmt: skip
    if isinstance(listed, list):
        trimmed["captures"] = len(listed)
    return trimmed


def _named(keys: Sequence[str]) -> str:
    return f": {', '.join(keys)}" if keys else ""


def _print_dropped(dropped: Any) -> None:
    """Every capture or commit the frame refused, and why. Never a silent skip."""
    if not isinstance(dropped, list) or not dropped:
        return
    print(f"\ndropped {len(dropped)}")
    print(render_table([dict(row) for row in dropped], ("key", "reason")))


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
    series_id: str,
    target: str,
    horizon: int,
    names: Sequence[str],
    device: str,
    model: str | None,
    pooled: bool,
) -> int:
    """Roll an origin through one stored series and store the run. Design 6.12.

    The forecasters are built BEFORE the store is opened, because building the timesfm
    one loads a 1.32 GB checkpoint and a refusal (an unknown name, a missing extra)
    should not have a writer thread waiting behind it.

    The row is labelled through the one decision rule, with no placebo (W3-V finding
    1): the label is baseline sufficient or not assessable, never one of the two that
    read a chronology control, and the inequalities are printed and stored with it.
    A placebo already stored for the pair is named rather than denied.
    """
    try:
        forecasters = {name: make(name, device) for name in names}
        chosen = _model(names, model)
    except KeyError as unknown:
        return common.refuse(
            f"{unknown.args[0]}: no such forecaster. {_forecaster_help()}"
        )
    except ImportError as missing:
        return common.refuse(f"timesfm needs the forecast extra: {missing}")
    except ValueError as ambiguous:
        return common.refuse(str(ambiguous))
    store = common.store().open()
    try:
        found = _resolve(store, series_id, pooled)
        run = backtester.run(found, target, horizon, forecasters)
        decision = placebos.unpaired(run, chosen)
        stored = _stored(store, found, target, horizon, names, _PLACEBO_ORDERINGS)
        # Render before persisting to enforce ADR-014.
        printed = "\n\n".join((
            backtester.report(run),
            decider.report(decision, placebos.constants(run)),
        ))  # fmt: skip
        run_id = backtester.persist(
            store, run, pooled_across=regimes.pooled_across(found, pooled)
        )
    except backtester.Refused as refused:
        return common.refuse(str(refused))
    finally:
        store.close()
    print(printed)
    print()
    if stored:
        print(
            f"placebo rows stored for this pair: {len(stored)} (newest"
            f" {stored[-1]['forecast_run_id']}). Their label rides on the true-order"
            " row they were paired with; this run took none, because a placebo is a"
            " control for the run it was made for."
        )
    if decision.reason == decider.NO_PLACEBO:
        print(
            f"run `telltale forecast placebo --series {series_id} --target {target}"
            f" --horizon {horizon}` to pair one with this run"
        )
    print(f"\nforecast_run_id {run_id}")
    return 0


_PLACEBO_ORDERINGS = (ORDERING_BLOCK, ORDERING_ROW)


def forecast_placebo(
    series_id: str,
    target: str,
    horizon: int,
    names: Sequence[str],
    device: str,
    model: str | None,
    pooled: bool,
) -> int:
    """The chronology placebo and design 6.12's decision rule. Design 6.12.

    The true-order run is reused from the store when one matches on every field that
    could move a number, and re-run otherwise; the placebos are always run, because a
    placebo is a control for the run it is paired with and for no other.
    """
    try:
        forecasters = {name: make(name, device) for name in names}
        chosen = _model(names, model)
    except KeyError as unknown:
        return common.refuse(
            f"{unknown.args[0]}: no such forecaster. {_forecaster_help()}"
        )
    except ImportError as missing:
        return common.refuse(f"timesfm needs the forecast extra: {missing}")
    except ValueError as ambiguous:
        return common.refuse(str(ambiguous))
    store = common.store().open()
    try:
        found = _resolve(store, series_id, pooled)
        paired = placebos.paired(
            found, target, horizon, forecasters, chosen,
            truth=_stored_true(store, found, target, horizon, names),
        )  # fmt: skip
        printed = placebos.report(paired)
        run_ids = placebos.store_all(
            store, paired, pooled_across=regimes.pooled_across(found, pooled)
        )
    except backtester.Refused as refused:
        return common.refuse(str(refused))
    finally:
        store.close()
    print(printed)
    print(f"\nforecast_run_ids {' '.join(run_ids)}")
    return 0


def forecast_ablate(
    series_id: str,
    target: str,
    horizon: int,
    names: Sequence[str],
    device: str,
    model: str | None,
    pooled: bool,
) -> int:
    """The A/B/C ablation on a change-clock series. Design 6.12.

    A FACTORY rather than a dict of forecasters: three variants means three runs, and
    one instance shared between them carries whatever the first run left on it.
    """
    try:
        chosen = _model(names, model)
        factory = _factory(names, device)
        factory()
    except KeyError as unknown:
        return common.refuse(
            f"{unknown.args[0]}: no such forecaster. {_forecaster_help()}"
        )
    except ImportError as missing:
        return common.refuse(f"timesfm needs the forecast extra: {missing}")
    except ValueError as ambiguous:
        return common.refuse(str(ambiguous))
    store = common.store().open()
    try:
        found = _resolve(store, series_id, pooled)
        ablation = ablator.run(found, target, horizon, factory, chosen)
        printed = ablator.report(ablation)
        run_ids = ablator.store_all(
            store, ablation, pooled_across=regimes.pooled_across(found, pooled)
        )
    except backtester.Refused as refused:
        return common.refuse(str(refused))
    finally:
        store.close()
    print(printed)
    print(f"\nforecast_run_ids {' '.join(run_ids)}")
    return 0


def forecast_candidate(
    series_id: str,
    target: str,
    names: Sequence[str],
    device: str,
    model: str | None,
    base: str | None,
    head: str | None,
    pooled: bool,
) -> int:
    """The one-step candidate protocol of design 6.12, H8. Exit 2 on any refusal.

    Two runs over the same origins and the paired difference between them, then the
    readiness checklist and the decision label for the conditioned run, so that a
    number and what may be said about it are on one page. With --base and --head the
    candidate's own A block is read off a git diff and the pair is run once more at
    origin N, the change that has not landed.

    The readiness lines print AFTER the runs rather than gating them. A checklist that
    refused before printing would hide the pair that a reader has to see to know what
    the refusal is about, and `forecast readiness` already exists for the preflight.
    """
    if (base is None) != (head is None):
        return common.refuse(
            "forecast candidate: --base and --head are given together or not at all;"
            " an A block is a diff between two commits"
        )
    try:
        protocol.check_target(target)
        forecasters = {name: make(name, device) for name in names}
        chosen = _candidate_model(names, model)
    except KeyError as unknown:
        return common.refuse(
            f"{unknown.args[0]}: no such forecaster. {_forecaster_help()}"
        )
    except ImportError as missing:
        return common.refuse(f"timesfm needs the forecast extra: {missing}")
    except (ValueError, backtester.Refused) as refused:
        return common.refuse(str(refused))
    store = common.store().open()
    try:
        found = _resolve(store, series_id, pooled)
        conditioned = protocol.conditioned(
            store,
            found,
            target,
            forecasters,
            chosen,
            pooled_across=regimes.pooled_across(found, pooled),
        )
        printed = protocol.report(conditioned)
    except backtester.Refused as refused:
        return common.refuse(str(refused))
    finally:
        store.close()
    if model is None:
        print(
            f"--model not given: the paired difference below is about {chosen}, the"
            f" first of --forecasters {list(names)}"
        )
    print(printed)
    print()
    print(_candidate_verdict(found, target, conditioned, chosen))
    print(f"\nforecast_run_ids {' '.join(conditioned['forecast_run_ids'])}")
    if base is None or head is None:
        return 0
    return _candidate_at_n(found, target, forecasters, base, head)


def _candidate_at_n(
    series: Series,
    target: str,
    forecasters: Mapping[str, Any],
    base: str,
    head: str,
) -> int:
    """The A block of one candidate and the pair of forecasts of the row it becomes."""
    try:
        block = protocol.features(".", base, head)
        ahead = protocol.one_step(series, target, forecasters, block)
    except protocol.NotACandidate as refused:
        return common.refuse(f"forecast candidate: {refused}")
    except backtester.Refused as refused:
        return common.refuse(str(refused))
    print()
    print(protocol.one_step_report(ahead))
    return 0


def forecast_scenario(
    series_id: str,
    target: str,
    horizon: int,
    futures: Sequence[str],
    name: str,
    names: Sequence[str],
    device: str,
    model: str | None,
    against: str | None,
    pooled: bool,
) -> int:
    """A conditional forecast at the end of one stored series. Design 6.12, W6-T1.

    The paths are parsed and the horizon checked BEFORE a forecaster is built, because
    building the timesfm one loads a 1.32 GB checkpoint and a refusal should not have
    that behind it. `check` inside `scenarios.run` asks the same questions again against
    the series, which is the half of them that needs one.
    """
    try:
        scenarios.check_horizon(horizon)
        declared = scenarios.Scenario(
            name=name,
            horizon=horizon,
            paths=scenarios.parse_paths(futures, horizon),
        )
        forecasters = {one: make(one, device) for one in names}
        chosen = _candidate_model(names, model)
    except KeyError as unknown:
        return common.refuse(
            f"{unknown.args[0]}: no such forecaster. {_forecaster_help()}"
        )
    except ImportError as missing:
        return common.refuse(f"timesfm needs the forecast extra: {missing}")
    except (ValueError, backtester.Refused) as refused:
        return common.refuse(str(refused))
    store = common.store().open()
    try:
        found = _resolve(store, series_id, pooled)
        run = scenarios.run(found, target, declared, forecasters, chosen)
        run["calibration"] = scenarios.calibration(store, run)
        # Rendered BEFORE the row is written: ADR-014's word refusal raises here, and a
        # report that may not be printed is a report that may not be stored either.
        pages = [scenario_page.report(run)]
        if against is not None:
            beside = scenarios.stored(store, series_id, against)
            pages.append(scenario_page.compare(beside, scenarios.record(run)))
        run_id = scenarios.persist(
            store, run, pooled_across=regimes.pooled_across(found, pooled)
        )
    except backtester.Refused as refused:
        return common.refuse(str(refused))
    finally:
        store.close()
    if model is None:
        print(
            f"--model not given: this scenario is about {chosen}, the first of"
            f" --forecasters {list(names)}"
        )
    print("\n\n".join(pages))
    print(f"\nforecast_run_id {run_id}")
    return 0


def _candidate_verdict(
    series: Series, target: str, found: Mapping[str, Any], model: str
) -> str:
    """The readiness checklist and the label, for the conditioned run.

    The label comes through `placebo.unpaired`, which is the same route `forecast
    backtest` takes (W3-V finding 1): with no chronology control the only two labels
    reachable are baseline sufficient and not assessable, and the inequalities that
    produced whichever one it is are printed with it.
    """
    run = found["runs"][protocol.CONDITIONED]
    checks = readiness.check(series, target, found["horizon"])
    decision = placebos.unpaired(dict(run), model)
    return "\n\n".join((
        readiness.report(series, target, found["horizon"], checks),
        decider.report(decision, placebos.constants(run)),
    ))  # fmt: skip


def _candidate_model(names: Sequence[str], model: str | None) -> str:
    """Which forecaster the paired difference is about. Named, never silent.

    Not `_model` below, which requires exactly one non-baseline because the DECISION
    rule is the model against the baselines. This protocol makes no such comparison:
    it scores one forecaster twice on the same origins, so every named forecaster is a
    legitimate subject and a run of baselines alone is a legitimate run. The default is
    the first name given, the report prints it on its first line, and the command says
    on stdout that it defaulted.
    """
    if not names:
        raise ValueError("--forecasters named none")
    if model is not None and model not in names:
        raise ValueError(f"--model {model} is not among --forecasters {list(names)}")
    return model or names[0]


def _factory(names: Sequence[str], device: str) -> Callable[[], dict[str, Any]]:
    return lambda: {name: make(name, device) for name in names}


def _model(names: Sequence[str], model: str | None) -> str:
    """Which forecaster the decision is about. Never guessed when there is a choice."""
    if model is not None:
        if model not in names:
            raise ValueError(f"--model {model} is not among --forecasters {names}")
        return model
    others = [name for name in names if name not in BASELINE_NAMES]
    if len(others) != 1:
        raise ValueError(
            f"--model is required: {len(others)} of --forecasters {list(names)} are not"
            f" baselines ({list(BASELINE_NAMES)}), and the decision is about one"
        )
    return others[0]


def _stored_true(
    store: Any, series: Series, target: str, horizon: int, names: Sequence[str]
) -> dict[str, Any] | None:
    """The stored true-order twin of the run about to be placeboed, or None.

    Reuse saves the expensive half of the pair (a TimesFM run is a loaded checkpoint
    and one call per window), and it is what lets a placebo be paired with a run an
    experiment already stored. The newest match wins: `forecast_runs` is ordered by
    creation and a re-run of the same pair is a correction of the older one.
    """
    rows = _stored(store, series, target, horizon, names, (ORDERING_TRUE,))
    if not rows:
        return None
    spec = backtester.registered(series, target, horizon)
    return placebos.restored(rows[-1], series, spec.unit)


def _stored(
    store: Any,
    series: Series,
    target: str,
    horizon: int,
    names: Sequence[str],
    orderings: Sequence[str],
) -> list[dict[str, Any]]:
    """The stored rows of one pair in the given orderings, oldest first."""
    spec = backtester.registered(series, target, horizon)
    wanted = {
        "target": target,
        "variant": spec.variant,
        "horizon": horizon,
        "c_min": spec.c_min,
        "stride": horizon,
    }
    return [
        row
        for row in store.forecast_runs(series.series_id)
        if placebos.matches(row, wanted, names, orderings)
    ]


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
    back.add_argument("--model", default=None, help="see `forecast placebo --model`")
    back.add_argument("--device", default="cpu", choices=DEVICES)
    regimes.add_pooled(back)
    ready = inner.add_parser("readiness", help="the eight-line preflight, design 6.12")
    ready.add_argument("--series", required=True, metavar="ID")
    ready.add_argument("--target", required=True, choices=sorted(TARGETS))
    ready.add_argument("--horizon", type=int, default=1, choices=HORIZONS)
    shuffle = inner.add_parser(
        "placebo", help="the chronology placebo and the decision rule, design 6.12"
    )
    shuffle.add_argument("--series", required=True, metavar="ID")
    shuffle.add_argument("--target", required=True, choices=sorted(TARGETS))
    shuffle.add_argument("--horizon", type=int, default=1, choices=HORIZONS)
    shuffle.add_argument(
        "--forecasters",
        default=",".join(DEFAULT_FORECASTERS),
        metavar="A,B,C",
        help=f"default: {','.join(DEFAULT_FORECASTERS)}. timesfm needs the extra",
    )
    shuffle.add_argument(
        "--model",
        default=None,
        help="which forecaster the decision is about. Default: the one that is not a"
        " baseline, and required when there is more than one",
    )
    shuffle.add_argument("--device", default="cpu", choices=DEVICES)
    regimes.add_pooled(shuffle)
    cut = inner.add_parser(
        "ablate", help="the A/B/C ablation on a change-clock series, design 6.12"
    )
    cut.add_argument("--series", required=True, metavar="ID")
    cut.add_argument("--target", required=True, choices=sorted(TARGETS))
    cut.add_argument("--horizon", type=int, default=1, choices=HORIZONS)
    cut.add_argument(
        "--forecasters",
        default=",".join(DEFAULT_FORECASTERS),
        metavar="A,B,C",
        help=f"default: {','.join(DEFAULT_FORECASTERS)}. timesfm needs the extra",
    )
    cut.add_argument("--model", default=None, help="see `forecast placebo --model`")
    cut.add_argument("--device", default="cpu", choices=DEVICES)
    regimes.add_pooled(cut)
    _candidate_command(inner)
    _scenario_command(inner)


def _candidate_command(inner: argparse._SubParsersAction[Any]) -> None:
    """`forecast candidate`. Design 6.12's H8; no --horizon, because H = 1 is the whole
    protocol: it conditions on one candidate and forecasts the row it becomes."""
    one = inner.add_parser(
        "candidate", help="the one-step candidate protocol, design 6.12"
    )
    one.add_argument("--series", required=True, metavar="ID")
    # Every registered target, so that a target the protocol forbids reaches the
    # refusal that names WHY rather than an argparse list that does not.
    one.add_argument("--target", required=True, choices=sorted(TARGETS))
    one.add_argument(
        "--forecasters",
        default=",".join(DEFAULT_FORECASTERS),
        metavar="A,B,C",
        help=f"default: {','.join(DEFAULT_FORECASTERS)}. timesfm needs the extra",
    )
    one.add_argument(
        "--model",
        default=None,
        help="which forecaster the paired difference is about."
        " Default: the first of --forecasters, printed on the report",
    )
    one.add_argument("--device", default="cpu", choices=DEVICES)
    one.add_argument(
        "--base",
        default=None,
        metavar="REF",
        help="with --head, read one candidate's A block off `git diff base...head`"
        " and forecast the row it would become",
    )
    one.add_argument("--head", default=None, metavar="REF", help="see --base")
    regimes.add_pooled(one)


def _scenario_command(inner: argparse._SubParsersAction[Any]) -> None:
    """`forecast scenario`. Design 6.12 and W6-T1's amendment.

    `--horizon` is a plain int and NOT `choices=SCENARIO_HORIZONS`, for the reason
    `--target` is every registered target on `forecast candidate`: an argparse list
    refuses without saying why, and the refusal that names the cost of a horizon is the
    one a reader has to see.
    """
    story = inner.add_parser(
        "scenario", help="a conditional forecast at the end of a series, design 6.12"
    )
    story.add_argument("--series", required=True, metavar="ID")
    story.add_argument("--target", required=True, choices=sorted(TARGETS))
    story.add_argument(
        "--horizon", type=int, default=1, metavar="H",
        help=f"one of {', '.join(str(one) for one in SCENARIO_HORIZONS)}",
    )  # fmt: skip
    story.add_argument(
        "--future",
        action="append",
        default=[],
        metavar="COL=V1,...,VH",
        help="one declared path per flag, one value per step. Only a column the"
        " registry marks declarable on this clock may carry one",
    )
    story.add_argument(
        "--name", default="scenario", metavar="NAME", help="what to print it under"
    )
    story.add_argument(
        "--forecasters",
        default=",".join(DEFAULT_FORECASTERS),
        metavar="A,B,C",
        help=f"default: {','.join(DEFAULT_FORECASTERS)}. timesfm needs the extra",
    )
    story.add_argument(
        "--model",
        default=None,
        help="which forecaster this scenario is about."
        " Default: the first of --forecasters, printed on the report",
    )
    story.add_argument("--device", default="cpu", choices=DEVICES)
    story.add_argument(
        "--compare-with",
        default=None,
        metavar="RUN_ID",
        help="a stored scenario of the same series, target and horizon. Prints the two"
        " point paths side by side with the difference per step",
    )
    regimes.add_pooled(story)


def _series_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """`series build`, `series check` and `series list`. Design 6.12 and 6.13."""
    parent = subcommands.add_parser("series", help="compile and check forecast inputs")
    inner = parent.add_subparsers(dest="series_command", required=True)
    make = inner.add_parser("build", help="compile one history into a Series")
    make.add_argument("--clock", required=True, choices=compiler.CLOCKS)
    # Exclusive and required together: the request clock takes one capture and the two
    # lineage clocks take one repository, and a build with neither has no history to
    # fold. argparse enforces it, so no branch below has to.
    which = make.add_mutually_exclusive_group(required=True)
    which.add_argument("--capture", default=None, metavar="ID", help="request clock")
    which.add_argument("--repo", default=None, metavar="REPO_ID", help="the other two")
    make.add_argument("--policy", default="exclude", choices=compiler.POLICIES)
    make.add_argument(
        "--regime", default=None, choices=regimes.REGIMES,
        help="keep one side of a policy intervention (spec 14.6): `pre` the rows"
        " before the boundary, `post` the rows at or after it. Neither pads",
    )  # fmt: skip
    make.add_argument(
        "--intervention", default=None, metavar="ID",
        help="the advisory id --regime is a side of. Optional when the repository has"
        " exactly one intervention, required when it has more",
    )  # fmt: skip
    verify = inner.add_parser("check", help="the no-look-ahead invariant, per row")
    verify.add_argument("series")
    inner.add_parser("list", help="the series snapshots on this disk")


def series(args: argparse.Namespace) -> int:
    if args.series_command == "build":
        return series_build(args)
    if args.series_command == "check":
        return series_check(args.series)
    return series_list()


def forecast(args: argparse.Namespace) -> int:
    if args.forecast_command == "readiness":
        return forecast_readiness(args.series, args.target, args.horizon)
    names = [name for name in args.forecasters.split(",") if name]
    if args.forecast_command == "candidate":
        return forecast_candidate(
            args.series, args.target, names, args.device, args.model,
            args.base, args.head, args.pooled,
        )  # fmt: skip
    if args.forecast_command == "scenario":
        return forecast_scenario(
            args.series, args.target, args.horizon, args.future, args.name,
            names, args.device, args.model, args.compare_with, args.pooled,
        )  # fmt: skip
    if args.forecast_command == "placebo":
        return forecast_placebo(
            args.series, args.target, args.horizon, names, args.device, args.model,
            args.pooled,
        )  # fmt: skip
    if args.forecast_command == "ablate":
        return forecast_ablate(
            args.series, args.target, args.horizon, names, args.device, args.model,
            args.pooled,
        )  # fmt: skip
    return forecast_backtest(
        args.series, args.target, args.horizon, names, args.device, args.model,
        args.pooled,
    )  # fmt: skip


def add_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    _series_commands(subcommands)
    _forecast_commands(subcommands)


def _resolve(store: Store, series_id: str, pooled: bool) -> Series:
    found = regimes.resolve(store, series_id, pooled)
    heading = regimes.banner(found, pooled)
    if heading:
        print(heading, end="")
    return found
