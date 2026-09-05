"""The `telltale` command. Design 6.13; argparse, and report.py renders.

Two commands so far, and they are the two that answer questions about Telltale itself
rather than about a capture.

`doctor` round-trips one synthetic record through every endpoint of a receiver it starts
in-process, reads each one back out of a temporary database and prints what came back.
That is a different question from "does the code import": every surface has a route, a
parser, an allowlist entry and a column, and any one of the four can be missing while
the other three are fine. It also reports whether git, claude, codex, uv and timesfm are
present, and those lines never decide the exit code, because a machine without the
claude binary is a machine where Telltale still works. `--matrix` adds what the store
already knows about the agent versions it holds captures of (doctor_matrix.py), and it
does not decide the exit code either.

`setup claude|codex --print` prints the snippet the owner may paste. It never writes
one: the owner decision of 2026-09-01 is launcher-only configuration, and `--apply`
prints a refusal that says so. AGENTS.md invariant 7. `--daemon` adds the launchd plist
that would run `telltale daemon` on the port the snippet names (setup_daemon.py), which
is printed and never installed for exactly the same reason.

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

`resanitize [capture]` rewrites the stored commands an older normalization version
wrote, in place. It is the one command that changes an observation rather than adding or
deleting one, and store.py holds its single UPDATE for the reason it holds the INSERTs.

`experiment repeat` runs one condition of design 6.12: N captures of one task under one
environment, each in its own worktree, through `run` above. `experiment environment`
runs two of those conditions as the arms of one factor and compares them, and lives in
experiments_env.py for the same reason the statistics live in stats.py: the 800-line
ratchet. `purge` deletes one capture.

`series` and `forecast` live in cli_forecast.py, which registers its own subcommands
here through `add_commands`; the helpers both files share (the store, the capture
lookup, the refusal exit code) are cli_common.py.

`import` lives in cli_import.py, which registers its own subcommand here the way
cli_forecast.py does: it reads the session files a provider has already written into
captures of their own, through the same parsers and the same sanitizer.

`advise` lives in cli_advise.py and registers itself here too. It prints the shadow
advisory for one candidate and stores it as a `policy.advisory` observation in a capture
of its own. Nothing is posted anywhere and nothing acts on it: spec 14.6 keeps forecasts
shadow-only, and the observation a policy writes when it ACTS is `policy.intervention`,
which `advise` never writes: `intervention` below is the command that does.
`show <adv_...>` prints the stored payload, which is
the one branch below that does not go through the session summary: an advisory has no
activities, so `measures.summary` would report a capture of nulls for it.

`intervention` lives in cli_intervention.py and registers itself here too. It is the
other half of the sentence the advisory paragraph above ends on: spec 14.6's
`policy.intervention`, written when a policy ACTS, after which the lineage is evaluated
as a new regime (series_regime.py).

`outcome` lives in cli_outcome.py and registers itself here too. It records what
happened to one attempt: a verification, a review, a merge decision, a revert or a
runtime signal. It is the only CLI write path besides `run` and `import`, and it exists
because three columns of the attempt clock are outcomes and nothing but the experiment
runner could post one before it.

`export` and `schema` live in cli_export.py and register themselves here too. `export`
writes one file per table under a directory that may not be inside $TELLTALE_HOME, and
`telltale import export --root DIR` reads the observations back; the derived rows are
never re-imported, they are rebuilt, because store.py is the only file that may write
one. `schema` prints the allowlist and the four durable shapes as JSON.

`purge` deletes one capture, or, with `--diagnostics-older-than N`, every diagnostics
row older than N days. The two are mutually exclusive because they are two different
deletions: one is a capture leaving this disk, the other is retention.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import (
    __version__,
    cli_advise,
    cli_export,
    cli_forecast,
    cli_import,
    cli_intervention,
    cli_outcome,
    cli_probe,
    cli_purge,
    cohorts,
    config,
    correlate,
    doctor_matrix,
    experiments,
    experiments_env,
    experiments_measure,
    launch,
    launch_commits,
    measures,
    report,
    report_profile,
    setup_daemon,
)
from telltale import cli_common as common
from telltale.doctor import daemon_row, last_launcher, retention, roundtrip, tool_rows
from telltale.facts import Facts, facts
from telltale.providers import claude, codex
from telltale.receiver import Receiver
from telltale.report import render_table
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def doctor(port: int, show_matrix: bool = False) -> int:
    rows, kinds = roundtrip()
    rows.append(daemon_row(port))
    print(render_table(rows, ("surface", "result", "observed")))
    print()
    print(render_table(tool_rows(), ("tool", "result", "detail")))
    print()
    print(f"diagnostics written by the round trip: {kinds or 'none'}")
    print(f"last launcher diagnostic: {last_launcher()}")
    if show_matrix:
        # After the round trip and never part of the exit code, for the reason the
        # tool rows are not: this reads captures that already exist, and a store with
        # no capture of a provider is not a broken machine.
        print()
        print(_matrix_block())
    print()
    print(retention())
    failed = [row for row in rows if row["result"] != "ok"]
    if failed:
        print(f"doctor: {failed[0]['surface']} did not round-trip")
        return 1
    print(f"doctor: {len(rows)} surfaces round-trip")
    return 0


def _matrix_block() -> str:
    """The compatibility matrix, or a line saying there is nothing to make one from.

    Not `cli_common.store()`, which raises SystemExit for a missing database: that is
    the right answer for `telltale show`, and the wrong one here. doctor's own round
    trip makes and deletes a database of its own, so a machine that has recorded
    nothing still passes every other check, and `--matrix` may not be the line that
    turns that into an exit code.
    """
    path = config.db_path()
    try:
        if not path.exists():
            return f"no store at {path} yet, so there is no compatibility matrix"
        return doctor_matrix.render(doctor_matrix.matrix(Store(path)))
    except (OSError, sqlite3.Error, ValueError) as error:
        return f"compatibility matrix unavailable at {path}: {error}"


def _claude_snippet(port: int, level: int) -> dict[str, Any]:
    """The settings.json fragment for the daemon, taken from the real launch plan.

    Not written out by hand: `claude.launch` is what the launcher will use, so the hook
    list and the OTel variables here cannot drift from the ones a capture actually gets.
    """
    plan = claude.launch(["claude"], port, level, session_id=None)
    settings: dict[str, Any] = json.loads(plan.argv[plan.argv.index("--settings") + 1])
    settings["env"] = plan.env
    return settings


def _codex_snippet(port: int, level: int) -> str:
    """The config.toml lines for the daemon, taken from the real launch plan.

    `codex.launch` puts its `-c` overrides after the subcommand; each is
    `dotted.key=value` with an inline-table or string value, which is one valid TOML
    line, so the snippet is those values joined by newlines and nothing else. One
    spelling, measured once (E02), used by both capture modes. No capture id: a daemon
    capture is named from the session id the receiver sees.
    """
    plan = codex.launch(["codex", "exec", "-"], port, level, session_id=None)
    values = [plan.argv[at + 1] for at, item in enumerate(plan.argv) if item == "-c"]
    return "\n".join(values) + "\n"


_CODEX_HEADER = """\
# telltale setup codex: paste into ~/.codex/config.toml yourself; Telltale never writes
# it. Every line below is one of the `-c` overrides `telltale run` passes to codex exec,
# in the spelling E02 measured on codex 0.150.1; both endpoints are used verbatim, so
# each carries its full signal path. The daemon tells a Codex batch from a Claude one by
# service.name. Hooks are not configured here: Codex takes command hooks from
# <repo>/.codex/hooks.json only, so daemon capture is OTel logs and metrics, and
# `telltale import codex-rollouts` backfills the rollout surface from ~/.codex/sessions.
# Content level {level}: tool result bodies are never persisted at any level, and
# max_bytes = 0 stops codex sending them at all.
"""

_REFUSAL = """\
telltale setup --apply is refused, and the refusal is a decision rather than a gap.
Owner decision of 2026-09-01 (docs/design/02-protocol.md, "Global config"): capture is
launcher-only, and Telltale never edits ~/.claude/settings.json, ~/.codex/config.toml or
anything else outside this repository and $TELLTALE_HOME. AGENTS.md invariant 7.
Run `telltale setup {provider} --print` and paste what it prints, or run the agent under
`telltale run`, which configures the child process and leaves no file behind.\
"""


def setup(
    provider: str, apply: bool, port: int, level: int, daemon: bool = False
) -> int:
    if apply:
        print(_REFUSAL.format(provider=provider))
        return common.REFUSED
    if daemon:
        # The plist first, then what the owner may do with it, then the same snippet
        # they would get without --daemon: the snippet already names `port`, so the
        # agent it configures posts to the daemon this plist would start.
        print(
            setup_daemon.plist(port, level, config.home(), setup_daemon.binary()),
            end="",
        )
        print(
            setup_daemon.INSTRUCTIONS.format(provider=provider, port=port, level=level)
        )
    if provider == "codex":
        print(_CODEX_HEADER.format(level=level), end="")
        print(_codex_snippet(port, level), end="")
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
    if capture_id.startswith(cli_advise.PREFIX):
        return cli_advise.show(store, capture_id)
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
    try:
        bound = receiver.start()
    except OSError as error:
        # The one failure `telltale run` cannot have: there the receiver is built with
        # no port and the OS picks a free one, and here the owner named the port so
        # that a snippet can point at it. Measured before W6-T4: 23 lines of traceback
        # ending in `OSError: [Errno 48] Address already in use`, and the store this
        # function had already opened was never closed.
        store.close()
        held = daemon_row(port)["observed"]
        print(
            f"telltale daemon: cannot bind: {held}. {error.strerror}."
            " Free it, or name another port with --port."
        )
        return 1
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
            added = launch_commits.link_commits(store, capture_id)
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
    except (experiments.SpecError, experiments_measure.FingerprintMismatch) as refusal:
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
    except (experiments.SpecError, experiments_measure.FingerprintMismatch) as refusal:
        print(f"experiment environment: {refusal}")
        return common.REFUSED
    print(report.environment(measured))
    return 0


def resanitize(capture_id: str | None) -> int:
    """Rewrite stored commands through the current normalization rules. Design 6.5.

    The one command that changes an observation already on the disk. It removes and
    never adds: every token it touches becomes `_` or a `<redacted:N>` marker, and a
    row it does not change is not written at all, so a second run reports nothing.
    """
    store = Store(config.db_path()).open()
    try:
        target = None if capture_id is None else common.known(store, capture_id)
        counts = store.resanitize(target)
    finally:
        store.close()
    for capture, fields in sorted(counts.items()):
        print(f"{capture}: {fields} field(s) rewritten")
    print(f"resanitized {len(counts)} capture(s), {sum(counts.values())} field(s)")
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
    check.add_argument(
        "--matrix",
        action="store_true",
        help="also print the runtimes this store has captured and their coverage",
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
    snippet.add_argument(
        "--daemon",
        action="store_true",
        help="also print the launchd plist that runs `telltale daemon` on that port",
    )
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
    cli_probe.add_kinds(kinds)
    cli_purge.add_commands(subcommands)
    rewrite = subcommands.add_parser(
        "resanitize", help="rewrite stored commands through the current rules"
    )
    rewrite.add_argument(
        "capture_id", nargs="?", default=None, metavar="CAPTURE_ID",
        help="one capture; the whole store when it is left out",
    )  # fmt: skip
    cli_import.add_commands(subcommands)
    listing = subcommands.add_parser("sessions", help="list the captures on this disk")
    listing.add_argument("--repo", default=None, metavar="ID", help="one repo_id only")
    listing.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    listing.add_argument(
        "--link-commits",
        action="store_true",
        help="link commits for captures in the CURRENT repository that have none",
    )
    _reading_commands(subcommands)
    cli_outcome.add_commands(subcommands)
    cli_intervention.add_commands(subcommands)
    cli_advise.add_commands(subcommands)
    cli_export.add_commands(subcommands)
    cli_forecast.add_commands(subcommands)
    report_profile.add_commands(subcommands)
    return parser


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
    "doctor": lambda args: doctor(_daemon_port(args.port), args.matrix),
    "setup": lambda args: setup(
        args.provider, args.apply, _daemon_port(args.port), args.level, args.daemon
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
    "purge": cli_purge.command,
    "resanitize": lambda args: resanitize(args.capture_id),
    "import": lambda args: cli_import.command(args, _level(args.level)),
    "outcome": cli_outcome.outcome,
    "intervention": cli_intervention.intervention,
    "advise": cli_advise.advise,
    "export": cli_export.command,
    "schema": lambda _args: cli_export.schema_command(),
    "series": cli_forecast.series,
    "forecast": cli_forecast.forecast,
    "profile": report_profile.command,
}


# The kinds of `telltale experiment`, for the reason _COMMANDS is a table.
_EXPERIMENTS: dict[str, Callable[[argparse.Namespace], int]] = {
    "repeat": lambda args: experiment_repeat(args.spec, args.out),
    "environment": lambda args: experiment_environment(args.spec, args.out),
    **cli_probe.KINDS,
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
