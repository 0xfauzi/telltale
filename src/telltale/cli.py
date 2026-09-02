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

`vector <capture>` prints spec 13.7's evidence vector: the raw numbers of six
families, each beside a cohort percentile that is filled only from a cohort design 6.11
would accept. `compare <a> <b>` prints two of those vectors side by side with both
coverage columns and a difference where both captures measured the number. Neither
writes: a percentile and a difference are statements about a comparison rather than
properties of a capture, and cohorts.py says at length why they are not stored.

`timeline`, `show` and `explain` read one capture and print what the reducer wrote.
They open no writer thread: every read in store.py takes its own read-only connection,
so a report runs while a capture is in flight without competing for it. `rebuild` is the
exception and the only one of the four that writes.

`experiment repeat` runs one condition of design 6.12: N captures of one task under one
environment, each in its own worktree, through `run` above. `experiment environment`
runs two of those conditions as the arms of one factor and compares them, and lives in
experiments_env.py for the same reason the statistics live in stats.py: the 800-line
ratchet. `purge` deletes one capture.

`series` and `forecast` live in cli_forecast.py, which registers its own subcommands
here through `add_commands`; the helpers both files share (the store, the capture
lookup, the refusal exit code) are cli_common.py.

`import` reads the session files a provider has already written into captures of their
own, through the same parsers and the same sanitizer. `--dry-run` counts and writes
nothing, which is the half the owner sees first (docs/design/02-protocol.md).

Every other command named in the design (schema, export) arrives with the task that
implements the thing it prints.
"""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import (
    __version__,
    cli_forecast,
    cohorts,
    config,
    correlate,
    experiments,
    experiments_env,
    importer,
    launch,
    measures,
    report,
)
from telltale import cli_common as common
from telltale.doctor import daemon_row, roundtrip, tool_rows
from telltale.facts import Facts, facts
from telltale.providers import claude
from telltale.receiver import Receiver
from telltale.report import render_table
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


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
        return common.REFUSED
    if provider == "codex":
        print(_CODEX_PENDING.format(port=port), end="")
        return 0
    print(json.dumps(_claude_snippet(port, level), indent=2, sort_keys=True))
    return 0


def _reduced(store: Store, capture_id: str) -> str | None:
    """The capture id, or None when nothing has reduced it yet.

    A capture with no activities has no derived number either, and every reader below
    would print a report of nulls for it. That is the confusion invariant 5 forbids: a
    capture that was recorded and never reduced would read exactly like one where
    nothing happened. `telltale run` reduces at capture end, so this is a capture
    imported, replayed or recorded before that existed.
    """
    known = common.known(store, capture_id)
    return known if store.activities(known) else None


def _unreduced(capture_id: str) -> int:
    return common.refuse(
        f"capture {capture_id} has no activities: run `telltale rebuild {capture_id}`"
    )


def timeline(capture_id: str) -> int:
    store = common.store()
    known = _reduced(store, capture_id)
    if known is None:
        return _unreduced(capture_id)
    print(report.timeline(store.activities(known)))
    return 0


def show(capture_id: str) -> int:
    store = common.store()
    known = _reduced(store, capture_id)
    if known is None:
        return _unreduced(capture_id)
    print(report.show(measures.summary(store, known)))
    return 0


def explain(capture_id: str, metric: str) -> int:
    store = common.store()
    known = _reduced(store, capture_id)
    if known is None:
        return _unreduced(capture_id)
    print(report.explain(store, known, metric))
    return 0


def vector(capture_id: str, as_json: bool, include_backfill: bool) -> int:
    """Spec 13.7's evidence vector of one capture. Design 6.13."""
    store = common.store()
    built = cohorts.vector(store, common.known(store, capture_id), include_backfill)
    print(json.dumps(built, indent=2) if as_json else report.vector(built))
    return 0


def compare(a: str, b: str, as_json: bool, include_backfill: bool) -> int:
    """Two evidence vectors side by side, and b minus a where both were measured."""
    store = common.store()
    left, right = cohorts.sides(
        store, common.known(store, a), common.known(store, b), include_backfill
    )
    printed = {"a": left, "b": right}
    print(json.dumps(printed, indent=2) if as_json else report.compare(left, right))
    return 0


def rebuild(capture_id: str | None) -> int:
    """Recompute the derived tables. Writes, so this one opens the store."""
    store = common.store().open()
    try:
        count = store.rebuild(common.known(store, capture_id) if capture_id else None)
    finally:
        store.close()
    print(f"rebuilt {count} capture(s) with {correlate.REDUCER_VERSION}")
    return 0


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
    # yes when this capture was read out of a file the provider had already written
    # (`telltale import`), rather than recorded around a running child.
    "backfill",
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
        # The provider's clock when the capture has one, which for an imported session
        # is when the session RAN rather than when it was read off the disk. Seconds
        # are enough to tell two captures apart, and the microseconds the store keeps
        # make every column of this table twice as wide.
        "started": str(known.session_started_at or capture["first_ts"])[:19].replace(
            "T", " "
        ),
        "duration_ms": known.duration_ms,
        "observations": capture["observation_count"],
        "coverage": known.coverage(),
        "commits": known.commits,
        "backfill": "yes" if known.backfill else "no",
    }


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
        return common.REFUSED
    print(report.experiment(measured))
    return 0


def experiment_environment(spec_path: str, out: str | None) -> int:
    """Run two arms of one factor and print the comparison. Design 6.12's H3.

    Both refusals are exit code 2 and one line: a spec whose arms differ in more than
    the declared flag is refused before any capture is made, and two arms whose
    environments differ in more than the declared field are refused after the runs,
    with the fields named. Neither prints a between-arm number.
    """
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    try:
        measured = experiments_env.environment(
            spec, config.home(), out=None if out is None else Path(out)
        )
    except (experiments.SpecError, experiments.FingerprintMismatch) as refusal:
        print(f"experiment environment: {refusal}")
        return common.REFUSED
    print(report.environment(measured))
    return 0


def purge(capture_id: str) -> int:
    """Delete one capture's observations and diagnostics. Design 6.13."""
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
    varying = kinds.add_parser(
        "environment", help="two arms that differ in one launch flag"
    )
    varying.add_argument("spec", metavar="spec.json")
    varying.add_argument(
        "--out",
        default=None,
        metavar="DIR",
        help="also write DIR/<task_id>/environment.json (default: print only)",
    )
    removal = subcommands.add_parser("purge", help="delete one capture from this disk")
    removal.add_argument("capture_id", metavar="CAPTURE_ID")
    _add_import(subcommands)
    listing = subcommands.add_parser("sessions", help="list the captures on this disk")
    listing.add_argument("--repo", default=None, metavar="ID", help="one repo_id only")
    listing.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    listing.add_argument(
        "--link-commits",
        action="store_true",
        help="link commits for captures in the CURRENT repository that have none",
    )
    _reading_commands(subcommands)
    cli_forecast.add_commands(subcommands)
    return parser


def _add_import(subcommands: argparse._SubParsersAction[Any]) -> None:
    """`telltale import <kind> [--root] [--since] [--project] [--dry-run]`.

    One subcommand with a positional kind rather than two: the two backfills differ in
    the directory they read and the parser they hand a line to, and nothing else.
    """
    backfill = subcommands.add_parser(
        "import", help="read sessions the provider already wrote (design 6.3)"
    )
    backfill.add_argument("kind", choices=tuple(importer.KINDS))
    backfill.add_argument(
        "--root", default=None, metavar="DIR", help="default: the provider's own"
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


def _reading_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """timeline, show, explain, rebuild, vector and compare: the reading commands."""
    rows = subcommands.add_parser("timeline", help="the activities of one capture")
    rows.add_argument("capture")
    summary = subcommands.add_parser("show", help="the session summary as JSON")
    summary.add_argument("capture")
    why = subcommands.add_parser("explain", help="one metric, back to its observations")
    why.add_argument("capture")
    why.add_argument("metric")
    again = subcommands.add_parser("rebuild", help="recompute activities and evidence")
    again.add_argument("capture", nargs="?", default=None)
    shape = subcommands.add_parser("vector", help="the spec 13.7 evidence vector")
    shape.add_argument("capture")
    _cohort_flags(shape)
    both = subcommands.add_parser("compare", help="two evidence vectors side by side")
    both.add_argument("a", metavar="A")
    both.add_argument("b", metavar="B")
    _cohort_flags(both)


def _cohort_flags(parser: argparse.ArgumentParser) -> None:
    """The two flags `vector` and `compare` share. Design 6.11's cohort rule."""
    parser.add_argument(
        "--json", action="store_true", help="print the dicts, unknown as null"
    )
    parser.add_argument(
        "--include-backfill",
        action="store_true",
        help="admit imported captures to the cohort (excluded by default)",
    )


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
    "vector": lambda args: vector(args.capture, args.json, args.include_backfill),
    "compare": lambda args: compare(args.a, args.b, args.json, args.include_backfill),
    # argparse requires the kind, so a bare `telltale experiment` is its usage error
    # rather than a branch here. The two kinds are two runners and one table below.
    "experiment": lambda args: _EXPERIMENTS[args.kind](args),
    "purge": lambda args: purge(args.capture_id),
    "import": lambda args: import_command(
        args.kind, args.root, args.since, args.project, args.dry_run, _level(args.level)
    ),
    "series": cli_forecast.series,
    "forecast": cli_forecast.forecast,
}


# The kinds of `telltale experiment`, for the reason _COMMANDS is a table.
_EXPERIMENTS: dict[str, Callable[[argparse.Namespace], int]] = {
    "repeat": lambda args: experiment_repeat(args.spec, args.out),
    "environment": lambda args: experiment_environment(args.spec, args.out),
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
