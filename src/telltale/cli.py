"""The `telltale` command. Design 6.13; argparse, and report.py renders.

Two commands so far, and they are the two that answer questions about Telltale itself
rather than about a capture.

`doctor` round-trips one synthetic record through every endpoint of a receiver it starts
in-process, reads each one back out of a temporary database and prints what came back.
That is a different question from "does the code import": every surface has a route, a
parser, an allowlist entry and a column, and any one of the four can be missing while
the other three are fine. It also reports whether git, claude, codex, uv and timesfm are
present, and those lines never decide the exit code, because a machine without the
claude binary is a machine where Telltale still works.

`setup claude|codex --print` prints the snippet the owner may paste. It never writes
one: the owner decision of 2026-09-01 is launcher-only configuration, and `--apply`
prints a refusal that says so. AGENTS.md invariant 7.

`run` wraps one command and records it; launch.py does the work and this file parses the
argv and returns the child's exit code. `daemon` runs the same receiver in the
foreground on a fixed port, for the sessions an owner starts by hand: there the receiver
derives a capture from each provider session id, so a day-to-day session is captured
without a launcher and still without a line of global configuration. `sessions` lists
what either of them recorded.

`timeline`, `show` and `explain` read one capture and print what the reducer wrote.
They open no writer thread: every read in store.py takes its own read-only connection,
so a report runs while a capture is in flight without competing for it. `rebuild` is the
exception and the only one of the four that writes.

`experiment repeat` runs one condition of design 6.12: N captures of one task under one
environment, each in its own worktree, through `run` above. `purge` deletes one capture.

`series build` compiles one capture into the only shape a forecaster takes, `series
check` re-tests the no-look-ahead invariant against the stored rows, and `series list`
says what has been compiled. Every other command named in the design (compare, schema,
export, forecast) arrives with the task that implements the thing it prints.
"""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import (
    __version__,
    config,
    correlate,
    experiments,
    launch,
    measures,
    report,
    series,
)
from telltale.doctor import daemon_row, roundtrip, tool_rows
from telltale.facts import Facts, facts
from telltale.providers import claude
from telltale.receiver import Receiver
from telltale.report import render_table
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from telltale.model import Series

# Exit code for a refusal: the command exists, it ran, and it declined on purpose.
# Distinct from 1, which this file spends on a surface that did not round-trip.
_REFUSED = 2


def doctor(port: int) -> int:
    rows, kinds = roundtrip()
    rows.append(daemon_row(port))
    print(render_table(rows, ("surface", "result", "observed")))
    print()
    print(render_table(tool_rows(), ("tool", "result", "detail")))
    print()
    print(f"diagnostics written by the round trip: {kinds or 'none'}")
    failed = [row for row in rows if row["result"] != "ok"]
    if failed:
        print(f"doctor: {failed[0]['surface']} did not round-trip")
        return 1
    print(f"doctor: {len(rows)} surfaces round-trip")
    return 0


def _claude_snippet(port: int, level: int) -> dict[str, Any]:
    """The settings.json fragment for the daemon, taken from the real launch plan.

    Not written out by hand: `claude.launch` is what the launcher will use, so the hook
    list and the OTel variables here cannot drift from the ones a capture actually gets.
    """
    plan = claude.launch(["claude"], port, level, session_id=None)
    settings: dict[str, Any] = json.loads(plan.argv[plan.argv.index("--settings") + 1])
    settings["env"] = plan.env
    return settings


_CODEX_PENDING = """\
# telltale setup codex: SPELLING PENDING E02.
#
# What a codex session has to be told, which is known:
#   send OTLP logs and metrics to http://127.0.0.1:{port} over http/json
#   send hooks to http://127.0.0.1:{port}/hooks/codex
#
# How ~/.codex/config.toml spells those two settings is NOT known here, and this
# command will not invent a key name that nobody has run. E02 measures how `codex
# exec` takes OTel and hook configuration; this snippet becomes the real one when it
# lands. Until then there is nothing here to paste.
"""

_REFUSAL = """\
telltale setup --apply is refused, and the refusal is a decision rather than a gap.
Owner decision of 2026-09-01 (docs/design/02-protocol.md, "Global config"): capture is
launcher-only, and Telltale never edits ~/.claude/settings.json, ~/.codex/config.toml or
anything else outside this repository and $TELLTALE_HOME. AGENTS.md invariant 7.
Run `telltale setup {provider} --print` and paste what it prints, or run the agent under
`telltale run`, which configures the child process and leaves no file behind.\
"""


def setup(provider: str, apply: bool, port: int, level: int) -> int:
    if apply:
        print(_REFUSAL.format(provider=provider))
        return _REFUSED
    if provider == "codex":
        print(_CODEX_PENDING.format(port=port), end="")
        return 0
    print(json.dumps(_claude_snippet(port, level), indent=2, sort_keys=True))
    return 0


def _store() -> Store:
    """The database `timeline`, `show`, `explain` and `rebuild` read.

    Not opened: `Store.open()` starts the writer thread, and three of the four commands
    only read, which store.py does on its own read-only connection. A database that is
    not there is an error naming the path rather than an empty report, because "no
    captures" and "no database" are different answers to `telltale show`.
    """
    path = config.db_path()
    if not path.exists():
        raise SystemExit(f"{path}: no database. Run a capture, or set TELLTALE_HOME.")
    return Store(path)


def _known(store: Store, capture_id: str) -> str:
    ids = [str(row["capture_id"]) for row in store.captures()]
    if capture_id in ids:
        return capture_id
    listed = ", ".join(ids) or "none"
    raise SystemExit(f"{capture_id}: no such capture. Stored: {listed}")


def timeline(capture_id: str) -> int:
    store = _store()
    print(report.timeline(store.activities(_known(store, capture_id))))
    return 0


def show(capture_id: str) -> int:
    store = _store()
    print(report.show(measures.summary(store, _known(store, capture_id))))
    return 0


def explain(capture_id: str, metric: str) -> int:
    store = _store()
    print(report.explain(store, _known(store, capture_id), metric))
    return 0


def rebuild(capture_id: str | None) -> int:
    """Recompute the derived tables. Writes, so this one opens the store."""
    store = _store().open()
    try:
        count = store.rebuild(_known(store, capture_id) if capture_id else None)
    finally:
        store.close()
    print(f"rebuilt {count} capture(s) with {correlate.REDUCER_VERSION}")
    return 0


# What `series build` prints per column. `nulls` is what the exclude policy DOES: it
# counts and nothing else, so the reader can see how many windows a forecaster will
# drop before it runs (design 6.12).
_COLUMN_COLUMNS = ("column", "unit", "role", "coverage", "nulls")


def series_build(clock: str, capture_id: str, policy: str) -> int:
    """Compile one capture into a Series and store it. Design 6.12.

    A refusal (an unbuilt clock, a policy this capture cannot satisfy) is exit 2, the
    same code `setup --apply` spends: the command exists, it ran, and it declined.
    """
    store = _store().open()
    try:
        built = series.build(store, clock, _known(store, capture_id), policy)
        store.put_series(built)
    except series.Refused as refused:
        return _refuse(str(refused))
    finally:
        store.close()
    _print_series(built)
    return 0


def _print_series(built: Series) -> None:
    """The id, the shape, every column with its coverage and its holes, the policy."""
    print(f"{built.series_id}  clock {built.clock}  {len(built.rows)} rows")
    print(f"cohort {json.dumps(built.cohort, sort_keys=True)}")
    print(f"policy {built.missingness_policy}  reducer {built.reducer_version}")
    print(render_table(series.column_report(built), _COLUMN_COLUMNS))
    found = ", ".join(str(index) for index in built.changepoints)
    print(f"changepoints {found or 'none'}")


def series_check(series_id: str) -> int:
    """Print the invariant result for one stored series. Exit 1 on a violation."""
    store = _store()
    found = store.series(series_id)
    if found is None:
        stored = [str(row["series_id"]) for row in store.series_ids()]
        raise SystemExit(
            f"{series_id}: no such series. Stored: {', '.join(stored) or 'none'}"
        )
    violations = series.check(store, found)
    print("\n".join(violations) if violations else "ok")
    return 1 if violations else 0


def series_list() -> int:
    store = _store()
    rows = [
        {**row, "cohort": row["cohort"].get("capture_id")} for row in store.series_ids()
    ]
    print(render_table(rows, ("series_id", "clock", "cohort", "rows", "built_at")))
    return 0


def _refuse(reason: str) -> int:
    print(reason)
    return _REFUSED


def daemon(port: int, level: int) -> int:
    """One receiver, in the foreground, for the sessions the owner starts by hand.

    The launcher is still the only thing that CONFIGURES a capture; this is the other
    half of the same decision. A session an owner starts themselves was configured by
    the snippet `telltale setup claude --print` gave them, which points here, and the
    receiver derives one capture per provider session id (design 6.6). So a day-to-day
    session is recorded with no launcher, and Telltale has still written nothing outside
    $TELLTALE_HOME.

    Ctrl-C stops it: the receiver stops taking requests, everything already accepted is
    flushed, and the store closes. Nothing is timed out and nothing is waited for.
    """
    store = Store(config.db_path()).open()
    receiver = Receiver(store, level=level, port=port, derive_captures=True)
    # flush on every line: stdout is block-buffered when it is a pipe, and a daemon
    # whose whole output is one line per capture as it happens must not hold those
    # lines until it exits. Measured: without it, a reader of the pipe saw nothing.
    receiver.on_new_capture(lambda capture: print(f"capture {capture}", flush=True))
    bound = receiver.start()
    print(f"telltale daemon: http://127.0.0.1:{bound} -> {store.path}")
    print(
        f"content level {level}. Ctrl-C stops it. One line per capture follows.",
        flush=True,
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("")
    finally:
        receiver.stop()
        store.flush()
        store.close()
    return 0


# The sessions table, in the order design 6.13 names. `observations` is the count in
# the captures view, and `coverage` is delivered surfaces over configured ones.
_SESSION_COLUMNS = (
    "capture_id",
    "provider",
    # Beside the provider rather than in a column of its own next to model: it is a
    # property of the binary, and W1-T4 made it part of the environment fingerprint, so
    # two rows with one provider and two runtimes are two environments.
    "runtime",
    "model",
    "started",
    "duration_ms",
    "observations",
    "coverage",
    "commits",
)
_DEFAULT_LIMIT = 20
_DEFAULT_LEVEL = 1


def sessions(repo_id: str | None, limit: int, link_commits: bool) -> int:
    """The captures this database holds, newest first. Design 6.13."""
    store = Store(config.db_path()).open()
    try:
        rows, linked = _session_rows(store, repo_id, limit, link_commits)
    finally:
        store.close()
    print(render_table(rows, _SESSION_COLUMNS))
    if link_commits:
        print(f"\nlinked {linked} commits in this repository")
    return 0


def _session_rows(
    store: Store, repo_id: str | None, limit: int, link_commits: bool
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    linked = 0
    for capture in store.captures():
        if len(rows) >= limit:
            break
        if repo_id is not None and capture["repo_id"] != repo_id:
            continue
        capture_id = str(capture["capture_id"])
        known = facts(store, capture_id)
        # Only captures that ended with nothing linked: a capture that already has a
        # commit was linked by evidence this run cannot improve on.
        if link_commits and known.commits == 0:
            added = launch.link_commits(store, capture_id)
            linked += added
            known.commits += added
        rows.append(_session_row(capture, known))
    return rows, linked


def _session_row(capture: dict[str, Any], known: Facts) -> dict[str, Any]:
    return {
        "capture_id": capture["capture_id"],
        "provider": capture["provider"],
        "runtime": known.runtime_version,
        "model": known.model,
        # Seconds are enough to tell two captures apart in a list, and the microseconds
        # the store keeps make every column of this table twice as wide.
        "started": str(capture["first_ts"])[:19].replace("T", " "),
        "duration_ms": known.duration_ms,
        "observations": capture["observation_count"],
        "coverage": known.coverage(),
        "commits": known.commits,
    }


def experiment_repeat(spec_path: str, out: str | None) -> int:
    """Run one condition and print what it measured. Design 6.12.

    A refusal is exit code 2 and one line, not a traceback: a spec that is not a
    condition and a set of captures that is not one environment are both the runner
    declining on purpose, and report.py never sees a half-built report.
    """
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    try:
        measured = experiments.repeat(
            spec, config.home(), out=None if out is None else Path(out)
        )
    except (experiments.SpecError, experiments.FingerprintMismatch) as refusal:
        print(f"experiment repeat: {refusal}")
        return _REFUSED
    print(report.experiment(measured))
    return 0


def purge(capture_id: str) -> int:
    """Delete one capture's observations and diagnostics. Design 6.13."""
    store = Store(config.db_path()).open()
    try:
        if capture_id not in {str(row["capture_id"]) for row in store.captures()}:
            print(f"purge: no capture {capture_id} in {store.path}")
            return _REFUSED
        diagnostics = len(store.diagnostics(capture_id))
        observations = store.purge(capture_id)
    finally:
        store.close()
    print(
        f"purged {capture_id}: {observations} observations, {diagnostics} diagnostics"
    )
    return 0


def _configured(key: str, override: int | None, default: int) -> int:
    """The flag, then config.json's key, then the default. Never a guess.

    A configured value that is not a whole number stops the command rather than falling
    back to the default. Falling back would use a number the owner did not choose, and
    it would look right: a snippet naming the wrong port, or a capture recorded at a
    content level nobody asked for.
    """
    if override is not None:
        return override
    try:
        value = config.load().get(key)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    if value is None:
        return default
    try:
        return int(str(value))
    except ValueError:
        raise SystemExit(
            f"{config.home() / 'config.json'}: {key} is {value!r}, not a whole number"
        ) from None


def _daemon_port(override: int | None) -> int:
    return _configured("daemon_port", override, config.DEFAULT_DAEMON_PORT)


def _level(override: int | None) -> int:
    """The content level of a capture. Design 6.4 knows three, and no more."""
    level = _configured("content_level", override, _DEFAULT_LEVEL)
    if level not in (0, 1, 2):
        raise SystemExit(f"content level {level} is not 0, 1 or 2")
    return level


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telltale",
        description="A local flight recorder for coding-agent sessions.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command")
    check = subcommands.add_parser(
        "doctor", help="round-trip one record through every capture surface"
    )
    check.add_argument(
        "--port",
        type=int,
        default=None,
        help="the daemon port to check (default: config.json, then 47311)",
    )
    snippet = subcommands.add_parser(
        "setup", help="print the configuration snippet for a provider"
    )
    snippet.add_argument("provider", choices=("claude", "codex"))
    snippet.add_argument(
        "--print", action="store_true", help="print the snippet (the default)"
    )
    snippet.add_argument(
        "--apply", action="store_true", help="refused: Telltale never writes it"
    )
    snippet.add_argument("--port", type=int, default=None, help="the daemon port")
    snippet.add_argument("--level", type=int, default=1, choices=(0, 1, 2))
    _add_run(subcommands)
    watch = subcommands.add_parser(
        "daemon", help="serve the capture receiver in the foreground on a fixed port"
    )
    watch.add_argument("--port", type=int, default=None, help="default: 47311")
    watch.add_argument("--level", type=int, default=None, choices=(0, 1, 2))
    experiment = subcommands.add_parser(
        "experiment", help="run an experiment from a spec (design 6.12)"
    )
    kinds = experiment.add_subparsers(dest="kind", required=True)
    repeating = kinds.add_parser(
        "repeat", help="N captures of one task under one environment"
    )
    repeating.add_argument("spec", metavar="spec.json")
    repeating.add_argument(
        "--out",
        default=None,
        metavar="DIR",
        help="also write DIR/<task_id>/report.json (default: print only)",
    )
    removal = subcommands.add_parser("purge", help="delete one capture from this disk")
    removal.add_argument("capture_id", metavar="CAPTURE_ID")
    listing = subcommands.add_parser("sessions", help="list the captures on this disk")
    listing.add_argument("--repo", default=None, metavar="ID", help="one repo_id only")
    listing.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    listing.add_argument(
        "--link-commits",
        action="store_true",
        help="link commits for captures in the CURRENT repository that have none",
    )
    _reading_commands(subcommands)
    _series_commands(subcommands)
    return parser


def _reading_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """timeline, show, explain and rebuild: the four that read one capture."""
    rows = subcommands.add_parser("timeline", help="the activities of one capture")
    rows.add_argument("capture")
    summary = subcommands.add_parser("show", help="the session summary as JSON")
    summary.add_argument("capture")
    why = subcommands.add_parser("explain", help="one metric, back to its observations")
    why.add_argument("capture")
    why.add_argument("metric")
    again = subcommands.add_parser("rebuild", help="recompute activities and evidence")
    again.add_argument("capture", nargs="?", default=None)


def _series_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """`series build`, `series check` and `series list`. Design 6.12 and 6.13."""
    parent = subcommands.add_parser("series", help="compile and check forecast inputs")
    inner = parent.add_subparsers(dest="series_command", required=True)
    make = inner.add_parser("build", help="compile one capture into a Series")
    make.add_argument("--clock", required=True, choices=series.CLOCKS)
    make.add_argument("--capture", required=True, metavar="ID")
    make.add_argument("--policy", default="exclude", choices=series.POLICIES)
    verify = inner.add_parser("check", help="the no-look-ahead invariant, per row")
    verify.add_argument("series")
    inner.add_parser("list", help="the series snapshots on this disk")


def _add_run(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """`telltale run [options] -- <argv...>`.

    Everything after `--` is the child's, untouched: argparse stops reading flags at
    the first one, so `telltale run -- claude -p x --output-format stream-json` gives
    the child its own `--output-format` rather than refusing it here.
    """
    runner = subcommands.add_parser(
        "run", help="run a command and record it (argv after --)"
    )
    runner.add_argument(
        "--provider",
        default="auto",
        choices=("auto", "claude", "codex", "generic"),
        help="auto reads the command's own name (default)",
    )
    runner.add_argument("--level", type=int, default=None, choices=(0, 1, 2))
    runner.add_argument("--task-id", default=None, metavar="ID")
    runner.add_argument("--attempt", type=int, default=None, metavar="N")
    runner.add_argument("--experiment", default=None, metavar="E")
    runner.add_argument(
        "--commit",
        action="append",
        default=[],
        metavar="SHA",
        help="a commit this run produced; may be repeated (spec 12.3, explicit)",
    )
    runner.add_argument("argv", nargs="*", help="the command to run, after --")


def _series(args: argparse.Namespace) -> int:
    if args.series_command == "build":
        return series_build(args.clock, args.capture, args.policy)
    if args.series_command == "check":
        return series_check(args.series)
    return series_list()


def _run_command(args: argparse.Namespace) -> int:
    """`run` alone resolves its content level before the launcher sees it."""
    args.level = _level(args.level)
    return launch.run(args)


# One entry per subcommand, because an if/elif chain grows a branch per command and the
# cyclomatic ratchet counts them: eleven commands is eleven paths through one function.
# The table is the same statement made once. A name absent from it prints the help.
_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "doctor": lambda args: doctor(_daemon_port(args.port)),
    "setup": lambda args: setup(
        args.provider, args.apply, _daemon_port(args.port), args.level
    ),
    "run": _run_command,
    "daemon": lambda args: daemon(_daemon_port(args.port), _level(args.level)),
    "sessions": lambda args: sessions(args.repo, args.limit, args.link_commits),
    "timeline": lambda args: timeline(args.capture),
    "show": lambda args: show(args.capture),
    "explain": lambda args: explain(args.capture, args.metric),
    "rebuild": lambda args: rebuild(args.capture),
    # `experiment` has exactly one kind today and argparse requires it, so a bare
    # `telltale experiment` is argparse's own usage error rather than a branch here.
    "experiment": lambda args: experiment_repeat(args.spec, args.out),
    "purge": lambda args: purge(args.capture_id),
    "series": _series,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `telltale` console script; returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = _COMMANDS.get(args.command)
    if command is None:
        parser.print_help()
        return 0
    return command(args)
