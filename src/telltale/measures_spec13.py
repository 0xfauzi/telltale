"""One `Metric` per neutral measure of spec 13, with its coverage and its sources.

The arithmetic half of design 6.11. measures.py owns `summarize` and the Appendix B
summary and turns each `Metric` below into an Evidence row; this file decides what the
numbers ARE, and measures_intervals.py holds the three that depend on order.

Split out of measures.py at the 800-line ratchet, and the split is where the file
divides anyway: nothing here touches the store, writes an Evidence or prints, so a
reader checking a definition against spec 13 reads one file and no plumbing.

Two rules shape every builder. A metric takes the WEAKEST coverage word among the
capabilities it needed (`weakest`), because a number is only as trustworthy as its least
visible input. And `honest` turns a value whose coverage is `unavailable` into null
whatever the arithmetic came to, because 0 test runs and "commands were not observable
here" are different statements and design invariant 5 exists to keep them apart.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from telltale import measures_intervals as walks
from telltale.correlate import TOOL_TYPES, of_type
from telltale.model import COVERAGE

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from telltale.model import Activity

# The four token counters, under the summary's name for each.
_TOKENS = (
    ("fresh_input_tokens", "input_tokens"),
    ("cache_read_tokens", "cache_read_tokens"),
    ("output_tokens", "output_tokens"),
    ("cache_creation_tokens", "cache_creation_tokens"),
)

# The tools that search rather than read one named file (spec 13.3). The shell names are
# here because a Codex read is a shell command and `rg pattern` is a search on both.
_SEARCH_TOOLS = frozenset({"Grep", "Glob"})
_SEARCH_HEADS = frozenset({"rg", "grep", "find"})

# Capability names off the coverage block, so a typo is a name in one place.
_PATHS = "file_paths"
_COMMANDS = "commands"
_TOOLS = "tool_calls"
_USAGE = "request_usage"
_WINDOW = "context_window"
_COMPACTION = "compaction"
_SUBAGENTS = "subagents"
_EXIT_CODES = "exit_codes"

# Not a provider capability: whether the LAUNCHER wrote repo snapshots for this capture.
# Design 6.8 makes them Telltale's own observation, so no provider CAPABILITIES table
# mentions them and a replayed fixture has none. `_snapshot_coverage` computes it.
_SNAPSHOTS = "repo_snapshots"

# Not a provider capability either, and for a sharper reason than snapshots: no
# CAPABILITIES row describes a permission denial, so this table is what says which
# surface states one. W2-E05 measured both shapes on Claude's stream, a
# `permission_denied` system message per refused call and a `permission_denials` list
# on the session result, and nothing on the other three surfaces. Nothing has measured
# a Codex refusal on any surface, so a Codex capture reports this unavailable rather
# than 0: the exec stream is called `stream` too, and a table keyed on the surface name
# alone would claim it had looked. This belongs in claude_drift.CAPABILITIES the day
# somebody owns that file; until then the measurement lives beside the number it
# decides.
_REFUSALS = "permission_denials"
_REFUSAL_SURFACES = {"claude": "stream"}

NO_REFUSAL_SURFACE = (
    "no surface in this capture states whether a tool call was refused, so this is"
    " unavailable and not 0: a refused call is one the agent asked for and never ran"
)

NO_SNAPSHOT = (
    "no telltale.repo.snapshot activity in this capture, so nothing observed the"
    " repository between one operation and the next: this is unavailable, not zero"
)

# What a failed test run IS, and the one way that reading goes wrong. Spec 13 defines a
# verification activity by its exit status, and the exit status of a chain is the last
# program's. Measured on S1: all three runs are `uv run pytest 2>&1 | tail -50`, every
# surface reported the call as successful, and the captured output of the first one says
# "1 failed, 1 passed". W3-T3 stopped that status being read as the test runner's: the
# reducer marks such a run `exit_masked` and states no outcome for it. W4-F3 finished
# the sentence: one such run in scope makes the number NULL, because a count over the
# rest is a count over a session nobody ran.
_EXIT_STATUS = (
    "counted from the exit status the surfaces reported, and only where that status is"
    " the classified command's own: one run in scope whose chain took its status from"
    " another program carries exit_masked and makes this null, never a smaller count"
)
_UNSEEN = (
    "no surface in this capture could show this, so the count is null rather than 0:"
    " see the coverage block for which capability was missing"
)
_NONE_FAILED = (
    "no test run reported a failing exit status, which is not the same as every test"
    " passing: a run whose outcome no surface stated makes this null, not a smaller"
    " count"
)
_MASKED_WHY = (
    "the classified command is followed by |, ; or ||, so the status the shell"
    " reported is another program's"
)

# W2-T1 measured the disagreement over 333 requests across seven 2.1.257 fixtures and
# eight 2.1.258 captures; W4-T4 measured WHAT the other surface is stating, by joining
# 761 stream messages to the provider's own transcript of the same six sessions. It is
# an assumption and not a warning: the number is the primary surface's and is not in
# doubt, and what a reader must know is that the stream's figure is not a smaller
# measurement of the same thing but a count taken before the message finished.
_OUTPUT_SURFACES = (
    "output_tokens is the OTel api_request total for the request; what the stream calls"
    " output_tokens on an assistant message is a snapshot from before that message"
    " finished, smaller than its final count on all 761 messages measured, so it is"
    " stored as output_tokens_snapshot and is never compared, added or substituted"
)

# Which summary counters have a provider session figure to fall back on, and the
# lifecycle field that holds it. One entry, because output_tokens is the one counter
# whose per-request value a stream-only Claude capture does not carry: W4-T4 measured
# the other three against the OTel capture of the same six sessions, 761 requests, and
# input_tokens, cache_read_tokens and cache_creation_tokens agree exactly on every one.
SESSION_FIELDS = {"output_tokens": "session_output_tokens"}

_SESSION_TOTAL = (
    "per-request output tokens were not observable on the surfaces this capture has, so"
    " this is the provider's figure for the WHOLE session (the stream result's"
    " modelUsage) and not a sum over the requests: the split across requests is unknown"
)
_RATIO_FILES = "files read over files changed, both distinct paths"
_RATIO_DIRS = "distinct parent directories read over distinct parent directories edited"
_OCCUPANCY = (
    "the largest single request's input plus cache_read plus cache_creation over the"
    " context window the provider stated; requests are not summed, because the window"
    " bounds one request and not a session"
)


@dataclass(frozen=True)
class Metric:
    """One number the summary may print, with the claim it supports attached."""

    name: str
    unit: str
    value: float | int | None
    coverage: str
    source: list[str]
    warnings: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


def honest(metric: Metric) -> Metric:
    """A number nobody could have seen is null, whatever the arithmetic came to.

    A count over an empty set is 0 only when the capability was observable. With
    coverage `unavailable` the same 0 says "this did not happen" about a capture where
    it could not have been seen, and those are the two statements design invariant 5
    exists to keep apart. Measured on a capture the launcher made around
    `bash -c 'echo hello'`: three surfaces configured, none delivered, every capability
    unavailable, and before this the summary reported 0 model requests, 0 test runs and
    0 compactions as though the session had made none.
    """
    if metric.coverage != "unavailable" or metric.value is None:
        return metric
    return replace(metric, value=None, warnings=(*metric.warnings, _UNSEEN))


def usage(
    requests: Sequence[Activity],
    session_totals: Mapping[str, Activity],
    coverage: Mapping[str, str],
    anchor: list[str],
) -> list[Metric]:
    state = coverage.get(_USAGE, "unavailable")
    seen = _ids(requests, anchor)
    rows = [Metric("model_requests", "requests", len(requests), state, seen)]
    for metric, name in _TOKENS:
        carried = [item for item in requests if name in item.fields]
        if len(carried) < len(requests) and metric in session_totals:
            rows.append(_session_total(metric, session_totals[metric]))
            continue
        rows.append(
            Metric(
                metric,
                "tokens",
                sum(item.fields[name] for item in carried) if carried else None,
                state if len(carried) == len(requests) else "partial",
                _ids(carried, anchor),
                assumptions=(_OUTPUT_SURFACES,) if name == "output_tokens" else (),
            )
        )
    return rows


def _session_total(metric: str, activity: Activity) -> Metric:
    """The provider's own session figure, when no per-request count was observable.

    Used only for the counters where the requests themselves have none. The number is
    real and the split across requests is not, so this is `partial` however complete the
    provider's figure is: what a reader may do with it is add it up over captures, and
    what they may not do is place it against a request. W4-T4 measured both halves of
    that on the six E12 sessions.
    """
    return Metric(
        metric,
        "tokens",
        int(activity.fields[SESSION_FIELDS[metric]]),
        "partial",
        [activity.activity_id],
        warnings=(_SESSION_TOTAL,),
    )


def work(
    activities: Sequence[Activity],
    capture: Activity,
    coverage: Mapping[str, str],
    anchor: list[str],
) -> list[Metric]:
    """Edit turnover, spec 13.2. The diff sizes are the launcher's snapshots or null."""
    edits = of_type(activities, ("file_edit",))
    checks = of_type(activities, ("verification_run",))
    snapshots = walks.ordered(of_type(activities, ("repo_snapshot",)))
    paths = weakest(coverage, _PATHS, _TOOLS)
    shots = _snapshot_coverage(snapshots, coverage)
    lines = [_diff_lines(item) for item in snapshots]
    measured = [value for value in lines if value is not None]
    shot_ids = _ids(snapshots, anchor)
    return [
        Metric(
            "unique_files_changed",
            "files",
            _distinct(edits),
            paths,
            _ids(edits, anchor),
        ),
        Metric(
            "file_revisits", "edits", walks.revisits(edits), paths, _ids(edits, anchor)
        ),
        Metric(
            "max_diff_lines",
            "lines",
            max(measured) if measured else None,
            shots,
            shot_ids,
            () if shots != "unavailable" else (NO_SNAPSHOT,),
        ),
        Metric(
            "final_diff_lines",
            "lines",
            measured[-1] if measured else None,
            shots,
            shot_ids,
            () if shots != "unavailable" else (NO_SNAPSHOT,),
        ),
        Metric(
            "reversions",
            "snapshots",
            walks.reversions(snapshots),
            shots,
            shot_ids,
            () if shots != "unavailable" else (NO_SNAPSHOT,),
        ),
        Metric(
            "post_failure_revisits",
            "edits",
            walks.post_failure_revisits(activities),
            _stated(checks, coverage, _PATHS, _COMMANDS),
            _ids(edits, anchor),
            _masked(checks),
            assumptions=(
                f"an edit within {walks.POST_FAILURE_WINDOW} edits of a verification"
                " run that reported a failing exit status",
            ),
        ),
        _refused(activities, capture, coverage, anchor),
    ]


def _refused(
    activities: Sequence[Activity],
    capture: Activity,
    coverage: Mapping[str, str],
    anchor: list[str],
) -> Metric:
    """Tool calls the agent asked for and was refused. Design 6.11, W3-T0.

    A refused call is not a failure of the work and not a unit of it: it is the session
    asking for something nobody was there to approve. W2-E05 ran five headless sessions
    that each made three Bash calls, were refused all three, and reported three failed
    test runs for tests that never ran. This is the count that says what happened, and
    activities.py is what keeps those calls out of `agent_test_runs`.
    """
    state = _refusal_coverage(capture, coverage)
    refused = [
        item
        for item in of_type(activities, TOOL_TYPES)
        if item.fields.get("executed") is False
    ]
    return Metric(
        "refused_tool_calls",
        "calls",
        len(refused),
        state,
        _ids(refused, anchor),
        () if state != "unavailable" else (NO_REFUSAL_SURFACE,),
    )


def _refusal_coverage(capture: Activity, coverage: Mapping[str, str]) -> str:
    """observed when a surface that STATES a refusal delivered, unavailable when not.

    Read through the coverage block first, so that a `permission_denials` capability
    added to a provider's CAPABILITIES table wins over the measurement table above.
    """
    stated = coverage.get(_REFUSALS)
    if stated:
        return stated
    wanted = _REFUSAL_SURFACES.get(str(capture.fields.get("provider")))
    delivered = capture.fields.get("surfaces_delivered")
    if wanted is None or not isinstance(delivered, list):
        return "unavailable"
    return "observed" if wanted in delivered else "unavailable"


def _diff_lines(snapshot: Activity) -> int | None:
    """additions plus deletions, or None when the snapshot carried neither."""
    parts = [snapshot.fields.get("additions"), snapshot.fields.get("deletions")]
    found = [value for value in parts if isinstance(value, int)]
    return sum(found) if found else None


def verification(
    activities: Sequence[Activity], coverage: Mapping[str, str], anchor: list[str]
) -> list[Metric]:
    """Verification cycles, spec 13.1. W4-F3: a number that needs an outcome is null
    when any run in ITS OWN scope states none, and `agent_test_runs` is not one of
    those, because the agent ran the tests however they ended."""
    runs = of_type(activities, ("verification_run",))
    tests = [item for item in runs if item.fields.get("category") == "test"]
    known = walks.outcomes_known(tests)
    failed = [item for item in tests if walks.failed(item)]
    epochs = walks.epochs(activities)
    outcome = weakest(coverage, _COMMANDS, _TOOLS)
    scoped = {
        name: [item for item in tests if item.fields.get("scope") == name]
        for name in ("targeted", "full")
    }
    none_failed = () if failed or not tests else (_NONE_FAILED,)
    return [
        Metric(
            "agent_test_runs",
            "runs",
            len(tests),
            coverage.get(_COMMANDS, "unavailable"),
            _ids(tests, anchor),
        ),
        Metric(
            "failed_test_runs",
            "runs",
            len(failed) if known else None,
            _stated(tests, coverage),
            # A null is sourced from the whole scope: "nobody could tell" is not a
            # fact about the failures.
            _ids(failed if known else tests, anchor),
            (*_masked(tests), *none_failed),
            (_EXIT_STATUS,),
        ),
        Metric(
            "fail_to_pass_cycles",
            "cycles",
            walks.fail_to_pass(runs),
            _stated(runs, coverage),
            _ids(runs, anchor),
            (*_masked(runs), *none_failed),
            (_EXIT_STATUS,),
        ),
        Metric(
            "edits_after_last_successful_test",
            "edits",
            epochs.edits_after_last_success,
            _stated(runs, coverage, _COMMANDS, _PATHS),
            epochs.sources or anchor,
            _masked(runs),
            (_EXIT_STATUS,),
        ),
        Metric(
            "edit_epochs_with_verification",
            "epochs",
            epochs.with_verification,
            outcome,
            epochs.sources or anchor,
        ),
        Metric(
            "edit_epochs_without_verification",
            "epochs",
            epochs.without_verification,
            outcome,
            epochs.sources or anchor,
        ),
        *[
            Metric(
                f"{name}_test_runs",
                "runs",
                len(rows),
                coverage.get(_COMMANDS, "unavailable"),
                _ids(rows, anchor),
            )
            for name, rows in scoped.items()
        ],
    ]


def _masked(runs: Sequence[Activity]) -> tuple[str, ...]:
    """The warning a count carries when a run's exit status was another program's.

    An agent_test_runs of 2 beside a failed_test_runs of 0 is a true pair of statements
    only if somebody saw how those two runs ended. When one of them piped its test
    runner into `tail`, nobody did, and W4-F3 made the value null rather than that 0.
    The count is over the runs THIS metric is taken over, so failed_test_runs names the
    test runs and fail_to_pass_cycles names all of them.

    The K and J sentence is what the null no longer prints: told only "null", a reader
    cannot tell 11 masked runs from one masked among eleven legible."""
    found = [item for item in runs if item.fields.get("exit_masked")]
    if not found:
        return ()
    stated = [item for item in runs if walks.failed(item) is not None]
    failing = sum(1 for item in stated if walks.failed(item))
    verb = "has" if len(found) == 1 else "have"
    noun = "run" if len(stated) == 1 else "runs"
    return (
        f"{len(found)} of {len(runs)} verification runs {verb} a masked exit status, so"
        " the value is null and not a count over what was left:"
        f" {_MASKED_WHY}."
        f" Of the {len(stated)} {noun} whose outcome a surface stated, {failing}"
        " failed",
    )


def exploration(
    activities: Sequence[Activity], coverage: Mapping[str, str], anchor: list[str]
) -> list[Metric]:
    """Exploration scope, spec 13.3. Wide reading may be prudent or costly:
    no valence is attached to any number here."""
    reads = of_type(activities, ("file_read",))
    edits = of_type(activities, ("file_edit",))
    paths = weakest(coverage, _PATHS, _TOOLS)
    before = _before_first_edit(activities)
    read_dirs = _directories(reads)
    edit_dirs = _directories(edits)
    traversed = None if read_dirs is None else len(read_dirs)
    return [
        Metric(
            "unique_files_read", "files", _distinct(reads), paths, _ids(reads, anchor)
        ),
        Metric(
            "unique_files_read_before_first_edit",
            "files",
            _distinct(before) if before or not edits else 0,
            paths,
            _ids(before, anchor),
        ),
        Metric(
            "search_ops",
            "operations",
            len(_searches(activities)),
            weakest(coverage, _TOOLS, _COMMANDS),
            _ids(_searches(activities), anchor),
        ),
        Metric(
            "directories_traversed",
            "directories",
            traversed,
            paths,
            _ids(reads, anchor),
        ),
        Metric(
            "read_to_edit_ratio",
            "ratio",
            _ratio(_distinct(reads), _distinct(edits)),
            paths,
            _ids([*reads, *edits], anchor),
            assumptions=(_RATIO_FILES,),
        ),
        Metric(
            "explored_to_final_ratio",
            "ratio",
            _ratio(traversed, None if edit_dirs is None else len(edit_dirs)),
            paths,
            _ids([*reads, *edits], anchor),
            assumptions=(_RATIO_DIRS,),
        ),
    ]


def _before_first_edit(activities: Sequence[Activity]) -> list[Activity]:
    """The file_reads that happened before the capture's first file_edit."""
    out: list[Activity] = []
    for item in walks.ordered(activities):
        if item.activity_type == "file_edit":
            return out
        if item.activity_type == "file_read":
            out.append(item)
    return out


def _searches(activities: Sequence[Activity]) -> list[Activity]:
    """Grep, Glob, and the shell commands that do the same job (spec 13.3)."""
    return [
        item
        for item in activities
        if item.fields.get("tool_name") in _SEARCH_TOOLS
        or _SEARCH_HEADS.intersection(
            str(item.fields.get("command_norm") or "").split()
        )
    ]


def _directories(rows: Sequence[Activity]) -> set[str] | None:
    """The distinct parent directories of the paths these activities name.

    None, not the empty set, when there were activities and none of them named a path.
    Measured on Codex, where a file read is a shell command and nothing extracts a path
    from a command string (W1-T3): an empty set there would report
    `directories_traversed 0` about a session that read four files.
    """
    named = [item for item in rows if item.fields.get("file_path")]
    if rows and not named:
        return None
    return {
        str(item.fields["file_path"]).rsplit("/", 1)[0]
        if "/" in str(item.fields["file_path"])
        else "."
        for item in named
    }


def _ratio(top: int | None, bottom: int | None) -> float | None:
    """None when the denominator is zero or unknown. Never a ratio against nothing."""
    if top is None or not bottom:
        return None
    return top / bottom


def context(
    activities: Sequence[Activity],
    capture: Activity,
    coverage: Mapping[str, str],
    anchor: list[str],
) -> list[Metric]:
    """Context and token burden, spec 13.4. The occupancy denominator or null."""
    rows = of_type(activities, ("compaction",))
    state = coverage.get(_COMPACTION, "unavailable")
    out = [Metric("compactions", "compactions", len(rows), state, _ids(rows, anchor))]
    for metric, name in (
        ("pre_compaction_tokens", "pre_tokens"),
        ("post_compaction_tokens", "post_tokens"),
    ):
        carried = [item for item in rows if name in item.fields]
        missing = len(rows) - len(carried)
        out.append(
            Metric(
                metric,
                "tokens",
                sum(item.fields[name] for item in carried) if carried else None,
                state if not missing else "partial",
                _ids(carried, anchor),
                ()
                if not missing
                else (
                    f"{missing} of {len(rows)} compactions carried no {name}: E01"
                    " measured that a failed compaction reports none",
                ),
            )
        )
    out.append(_occupancy(activities, capture, coverage, anchor))
    return out


def _occupancy(
    activities: Sequence[Activity],
    capture: Activity,
    coverage: Mapping[str, str],
    anchor: list[str],
) -> Metric:
    """The fullest single request over the stated window, or null. Spec 13.4.

    Only with a denominator the provider stated: the capture row carries
    `context_window` and `context_window_source` or it carries neither, and a window
    guessed from a model name would be a number nobody measured.
    """
    window = capture.fields.get("context_window")
    requests = of_type(activities, ("model_request",))
    occupied = [
        (value, item) for item in requests if (value := _occupied(item)) is not None
    ]
    if not isinstance(window, int) or not window or not occupied:
        return Metric(
            "occupancy_ratio",
            "ratio",
            None,
            "unavailable",
            [],
            (
                "no context window was stated by any surface of this capture"
                if not isinstance(window, int) or not window
                else "no request carried the counters an occupancy ratio is made of",
            ),
        )
    top, item = max(occupied, key=lambda pair: pair[0])
    return Metric(
        "occupancy_ratio",
        "ratio",
        top / window,
        weakest(coverage, _USAGE, _WINDOW),
        [item.activity_id, *anchor],
        assumptions=(
            _OCCUPANCY,
            f"denominator {window} from {capture.fields.get('context_window_source')}",
        ),
    )


def _occupied(request: Activity) -> int | None:
    """One request's occupancy of the window: fresh plus cache read plus cache write."""
    parts = [
        request.fields.get(name)
        for name in ("input_tokens", "cache_read_tokens", "cache_creation_tokens")
    ]
    found = [value for value in parts if isinstance(value, int)]
    return sum(found) if found else None


def stable_state(
    activities: Sequence[Activity], coverage: Mapping[str, str], anchor: list[str]
) -> list[Metric]:
    """Stable-state work intervals, spec 13.5. Unavailable without repository snapshots.

    Without a snapshot there is no diff fingerprint, so "the fingerprint did not move"
    is not something this capture could have seen, and every number below is null rather
    than a count over one interval that happens to be the whole session.
    """
    snapshots = of_type(activities, ("repo_snapshot",))
    state = _snapshot_coverage(snapshots, coverage)
    found = walks.intervals(activities) if state != "unavailable" else []
    # The snapshots are sources even though no interval CONTAINS one: they are what the
    # walk read to decide where an interval ended, so `telltale explain` has to reach
    # them. Without this a reader asking why there are five intervals is shown the work
    # inside them and nothing about the boundaries.
    inside = [one for interval in found for one in interval.sources]
    sources = (inside + _ids(snapshots, [])) if found else anchor
    note = () if state != "unavailable" else (NO_SNAPSHOT,)

    def one(name: str, unit: str, value: float | None, ids: list[str]) -> Metric:
        return Metric(name, unit, value, state, ids, note)

    spans = [item.duration_ms for item in found if item.duration_ms is not None]
    tokens = [item.tokens for item in found if item.tokens is not None]
    return [
        one("stable_state_work_intervals", "intervals", len(found), sources),
        one("stable_state_total_ms", "ms", sum(spans) if spans else None, sources),
        one("stable_state_max_ms", "ms", max(spans) if spans else None, sources),
        one("stable_state_tokens", "tokens", sum(tokens) if tokens else None, sources),
        *[
            one(
                f"stable_state_ended_by_{ending}",
                "intervals",
                len(ended),
                [item for interval in ended for item in interval.sources] or sources,
            )
            for ending, ended in _by_ending(found)
        ],
    ]


def _by_ending(
    found: Sequence[walks.Interval],
) -> list[tuple[str, list[walks.Interval]]]:
    """Every ending in walks.ENDINGS with its intervals. A pair list, not a dict.

    Every ending gets a row even when no interval ended that way, so a reader sees three
    numbers that sum to the interval count rather than a block whose missing key they
    have to read as zero.
    """
    return [
        (ending, [item for item in found if item.ended_by == ending])
        for ending in walks.ENDINGS
    ]


def delegation(
    activities: Sequence[Activity], coverage: Mapping[str, str], anchor: list[str]
) -> list[Metric]:
    """Delegation, spec 13.6. Attributed, never summed into the capture's own totals."""
    children = of_type(activities, ("subagent",))
    state = coverage.get(_SUBAGENTS, "unavailable")
    attributed = [
        item.fields["attributed_total_tokens"]
        for item in children
        if isinstance(item.fields.get("attributed_total_tokens"), int)
    ]
    delegated = {
        one
        for item in children
        for one in (item.fields.get("delegated_tool_calls") or [])
    }
    calls = [item for item in activities if item.activity_type in walks.WORK_TYPES]
    tools = [item for item in calls if item.activity_type != "model_request"]
    return [
        Metric("subagent_count", "subagents", len(children), state,
                _ids(children, anchor)),
        Metric(
            "subagent_tokens",
            "tokens",
            sum(attributed) if attributed else None,
            state if len(attributed) == len(children) else "partial",
            _ids(children, anchor),
            assumptions=(
                "already inside the capture's own usage totals: an attribution of part"
                " of that total and never an addition to it (spec 13.6)",
            ),
        ),
        Metric("delegated_tool_calls", "calls", len(delegated),
                weakest(coverage, _SUBAGENTS, _TOOLS), sorted(delegated) or anchor),
        Metric("direct_tool_calls", "calls", len(tools) - len(delegated),
                weakest(coverage, _SUBAGENTS, _TOOLS), _ids(tools, anchor)),
    ]  # fmt: skip


def _snapshot_coverage(
    snapshots: Sequence[Activity], coverage: Mapping[str, str]
) -> str:
    """observed when the launcher wrote snapshots, unavailable when it wrote none.

    Not a provider capability: a repository snapshot is Telltale's own observation, so
    the answer is whether any arrived rather than what a CAPABILITIES table says. It is
    read through the coverage block first so that a future capability of that name wins.
    """
    stated = coverage.get(_SNAPSHOTS)
    if stated:
        return stated
    return "observed" if snapshots else "unavailable"


def weakest(coverage: Mapping[str, str], *names: str) -> str:
    """The weakest coverage word among the capabilities a number needed.

    Design invariant 5: a metric is only as trustworthy as its least visible input, and
    a metric that reads paths AND exit statuses cannot be `observed` because one of them
    was.
    """
    words = [coverage.get(name, "unavailable") for name in names]
    return max(words, key=COVERAGE.index) if words else "unavailable"


def _stated(runs: Sequence[Activity], coverage: Mapping[str, str], *names: str) -> str:
    """observed only when every run in scope stated an outcome, partial otherwise.

    A count of failures over runs whose outcome nobody saw is a count of the failures
    that happened to be visible, and printing that as `observed` would say more than the
    surfaces did. E01: only the OTel tool_result states success outright, and on Claude
    an exit code is DERIVED, which is why `exit_codes` is in the weakest set: reading a
    run's outcome now consults it (see measures_intervals.failed).

    Asked of `walks.outcomes_known` and never of the field names: since W4-F3 a masked
    row carries the CALL's `success` (design 6.10), and reading the field would print
    `observed` over a value that just became null."""
    if walks.outcomes_known(runs):
        return weakest(coverage, _TOOLS, _EXIT_CODES, *names)
    return "partial"


def _ids(rows: Sequence[Activity], anchor: list[str]) -> list[str]:
    """The activity ids a number was read from, or the capture row when there are none.

    Never empty: an Evidence with no source is refused by the model unless its coverage
    is `unavailable`, and a count of zero that WAS observable is supported by the
    lifecycle row carrying the coverage that says so.
    """
    return [item.activity_id for item in rows] or anchor


def _distinct(rows: Sequence[Activity]) -> int | None:
    """How many distinct paths these activities name. Three answers, not two.

    No activities is 0 files. Activities that name paths is the number of distinct ones.
    Activities where no path was observable is None, because "the agent read nothing"
    and "what it read could not be seen" is the distinction coverage exists for.
    """
    paths = {item.fields["file_path"] for item in rows if "file_path" in item.fields}
    if not rows:
        return 0
    return len(paths) or None
