"""`telltale experiment repeat`: N independent captures of one task, one environment.

Design 6.12, "H2 and H3 protocol". One run is one `telltale run` of a task spec in a
disposable worktree of the target repository at a pinned base commit; a condition is N
of those under one environment fingerprint. What this module produces is the pilot
measurement everything else in the H2 and H3 protocol is built on: how much a measure
varies when NOTHING varies except the run.

Four decisions are worth stating here, because each looks like an omission in the code:

  The launcher is the only way a capture is made. This module shells out to
  `telltale run` rather than calling launch.run(), because a capture assembled any
  other way is a capture of a different process tree, and the thing being measured is
  run-to-run variation of the real one.

  A repetition is a fresh detached worktree, removed afterwards. Repetitions that share
  a checkout are not independent: the second one starts from what the first one left.

  The acceptance command is run by the HARNESS, after the agent has exited, in the
  worktree, and never by the agent. An agent that runs its own verification reports its
  own result.

  The runner REFUSES to compute statistics when two captures in one condition have
  different environment fingerprints, and names the fields that differ. Statistics over
  a condition that was not one condition are the failure this whole protocol exists to
  prevent.

Nothing here writes an Evidence row. `vector()` below READS the rows measures.py wrote
at capture end, so a repetition's numbers and a `telltale vector` of the same capture
cannot disagree; a row written here for a statistic over five captures would be deleted
by the next `store.rebuild()` anyway. The claim class of every number is carried in the
report and printed in the table instead: per-capture numbers are derived, and the
statistics over them are comparative WITHIN this condition.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from statistics import median, quantiles
from typing import TYPE_CHECKING, Any

from telltale.cohorts import VECTOR
from telltale.facts import facts
from telltale.measures import value_of
from telltale.model import now_iso, to_json
from telltale.receiver import Receiver, _post, _with_capture
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

# The scale that makes the median absolute deviation an estimator of the standard
# deviation of a normal sample. Design 6.12 fixes it; it is a constant, not a fit.
MAD_SCALE = 1.4826

# `external_system` on every correlation this runner posts. It is what says an
# orchestrator claimed these captures, as opposed to a person running one by hand.
EXTERNAL_SYSTEM = "telltale-experiment"

# The spec, in full. Every key is required and an unknown key is refused: a spec that
# does not name its content level is a spec whose captures cannot be compared, and a
# misspelled key that defaults quietly is two conditions reported as one.
SPEC_KEYS = (
    "task_id", "experiment", "repo", "base_sha", "command", "acceptance",
    "repetitions", "provider", "level",
)  # fmt: skip

# The result-message fields that are NOT measures. The token counts are gone from this
# tuple because spec 13.7's context_token_burden family carries them, read back out of
# the evidence table; these two the provider states about the session as a whole and no
# reducer computes.
_RESULT_KEYS = ("num_turns", "duration_ms")

_TOOL_PREFIX = "tool_calls."
_STREAM_PREFIX = "claude.stream."

# What a report rebuilt by `from_store` has to say about itself, beside the assumptions
# every report carries. The wrapper's wall time is the one field that cannot come back.
_RECOVERED_ASSUMPTIONS = (
    "this report was rebuilt from captures already in the store, not from a run:"
    " wall_ms is None because the wrapper that measured it is gone, and capture_span_ms"
    " (capture_started to capture_ended, on the arrival clock) is beside it under its"
    " own name",
    "an acceptance status of 'unknown' means the harness recorded no outcome for that"
    " repetition, which is not a pass and not a fail",
)


class SpecError(ValueError):
    """The spec does not describe a condition. Raised before anything is run."""


class FingerprintMismatch(ValueError):
    """Two captures in one condition ran in two environments. Names the fields."""


class NotReduced(ValueError):
    """A capture with no evidence row at all. Names the capture."""


# -- one condition --------------------------------------------------------------------


def repeat(
    spec: Mapping[str, Any], home: Path, out: Path | None = None
) -> dict[str, Any]:
    """Run one condition and return its report. Writes the report only if `out` is set.

    `home` is the $TELLTALE_HOME the captures are written into, and it is passed to
    each child rather than inherited, so a caller cannot end up measuring one database
    while writing into another.
    """
    checked = _checked(spec)
    # Only here, and not in `_checked`: this is a rule about STARTING a session, and a
    # report rebuilt by `from_store` from sessions that already ran must not be blocked
    # by it. Refusing to recover the record of a mistake deletes the evidence of it.
    _approvable(checked["command"])
    store = Store(home / "telltale.db").open()
    receiver = Receiver(store, level=int(checked["level"]))
    port = receiver.start()
    try:
        runs = [
            _repetition(checked, home, store, port, attempt)
            for attempt in range(1, int(checked["repetitions"]) + 1)
        ]
    finally:
        receiver.stop()
        store.flush()
    try:
        return _report(checked, store, runs, out)
    finally:
        store.close()


def _checked(spec: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(set(SPEC_KEYS) - set(spec))
    unknown = sorted(set(spec) - set(SPEC_KEYS))
    if missing or unknown:
        raise SpecError(f"spec: missing {missing}, unexpected {unknown}")
    checked = dict(spec)
    for key in ("command", "acceptance"):
        if not isinstance(checked[key], list) or not checked[key]:
            raise SpecError(f"spec: {key} is a non-empty argv list")
    if not isinstance(checked["repetitions"], int) or checked["repetitions"] < 1:
        raise SpecError(f"spec: repetitions is {checked['repetitions']!r}, not a count")
    checked["repo"] = str(Path(str(checked["repo"])).expanduser().resolve())
    return checked


def _approvable(command: Sequence[Any]) -> None:
    """Refuse a headless claude session that cannot approve its own tool calls.

    Measured on 2026-09-02 by E05's five sessions, and the reason this check exists.
    `claude -p --permission-mode acceptEdits` auto-accepts file edits and nothing else,
    so each session's first Bash call raised a permission request with nobody at the
    keyboard to answer it. All five sessions are the same four turns: three Bash calls,
    three `claude.stream.system.permission_denied` observations, no Read, no Edit, and
    a result message listing all three tool_use ids under `permission_denials`. The
    condition ran to completion and measured a permission failure five times.

    E01's S1 ran the same prompt with `--permission-mode bypassPermissions` and fixed
    the test in 7 turns, so the flag is the whole difference. This refuses before any
    token is spent rather than after five captures have been written.
    """
    words = [str(word) for word in command]
    if not words or Path(words[0]).name != "claude":
        return
    if not {"-p", "--print"} & set(words):
        return
    mode = _flag(words, "--permission-mode")
    if mode != "bypassPermissions":
        raise SpecError(
            f"spec: command is headless claude with --permission-mode {mode!r}."
            " A tool call that needs approval in `claude -p` has nobody to approve it,"
            " so the call is denied and the session ends having done nothing."
            " Use bypassPermissions, or drop -p"
        )


def _flag(words: Sequence[str], name: str) -> str | None:
    """The value of `--name value`, or of `--name=value`. None when the flag is absent.

    None and "the flag is there with an empty value" are both refused by the caller, so
    they are not distinguished here.
    """
    for index, word in enumerate(words):
        if word == name and index + 1 < len(words):
            return words[index + 1]
        if word.startswith(f"{name}="):
            return word.split("=", 1)[1]
    return None


def from_store(
    spec: Mapping[str, Any], home: Path, out: Path | None = None
) -> dict[str, Any]:
    """The report of a condition already captured in the store. Launches nothing.

    `repeat` writes nothing until every repetition is done, so a runner killed at
    repetition N leaves N captures and no report. This rebuilds the report from what
    the captures themselves carry, which is every field the report needs except one.

    The exception is `wall_ms`, the wrapper's perf_counter around `telltale run`. That
    was measured in a process that no longer exists and it is None here rather than
    replaced: `capture_span_ms`, capture_started to capture_ended, is a different
    measurement and carries a different name. Nothing else is substituted, and a
    repetition whose acceptance was never recorded gets the status "unknown" rather
    than a pass or a fail nobody ran.
    """
    checked = _checked(spec)
    store = Store(home / "telltale.db")
    try:
        runs = [
            _recovered(checked, store, attempt)
            for attempt in range(1, int(checked["repetitions"]) + 1)
        ]
        return _report(checked, store, runs, out, recovered=True)
    finally:
        store.close()


def _recovered(spec: Mapping[str, Any], store: Store, attempt: int) -> dict[str, Any]:
    """One repetition read back out of its capture. Refuses when there is no capture."""
    capture_id = _claimed_capture(store, spec, attempt)
    known = facts(store, capture_id)
    return {
        "attempt": attempt,
        "capture_id": capture_id,
        "exit_code": known.exit_code,
        "wall_ms": None,
        "capture_span_ms": _span_ms(known.started_at, known.ended_at),
        "duration_ms": known.duration_ms,
        "coverage": known.coverage(),
        "provider_session_id": _session_of(store, capture_id),
        "acceptance": _recorded_acceptance(store, capture_id, spec),
    }


def _claimed_capture(store: Store, spec: Mapping[str, Any], attempt: int) -> str:
    """The one capture that says it is this attempt of this task. Never a guess."""
    matched = [
        str(row["capture_id"])
        for row in store.captures()
        if _claims(store, str(row["capture_id"]), spec, attempt)
    ]
    if len(matched) != 1:
        raise SpecError(
            f"attempt {attempt} of {spec['task_id']}: {len(matched)} captures in the"
            f" store claim it ({matched}), and a report needs exactly one"
        )
    return matched[0]


def _recorded_acceptance(
    store: Store, capture_id: str, spec: Mapping[str, Any]
) -> dict[str, Any]:
    """The acceptance outcome the harness recorded, or "unknown" when it recorded none.

    Never "fail". A repetition whose runner died before the acceptance step and one
    whose acceptance command exited non-zero are different facts, and the second is the
    only one that is a result about the agent.
    """
    for row in store.observations(capture_id):
        if row["observation_type"] == "external.outcome":
            return {
                "command": [str(word) for word in spec["acceptance"]],
                "exit_code": None,
                "status": str(row["payload"]["status"]),
            }
    return {
        "command": [str(word) for word in spec["acceptance"]],
        "exit_code": None,
        "status": "unknown",
    }


def _span_ms(started_at: str | None, ended_at: str | None) -> int | None:
    """capture_started to capture_ended in milliseconds, from the arrival clock."""
    if started_at is None or ended_at is None:
        return None
    first = datetime.fromisoformat(started_at)
    last = datetime.fromisoformat(ended_at)
    return int((last - first).total_seconds() * 1000)


def finish(
    spec: Mapping[str, Any], home: Path, attempt: int, worktree: Path
) -> dict[str, Any]:
    """Record the correlation and acceptance of a repetition the runner never closed.

    The recovery half of `_repetition`, for the case where the runner was killed
    between the capture ending and the two records that close it out. The acceptance
    command is RUN HERE, in the worktree the killed runner left behind, so the recorded
    outcome is still one the harness measured rather than one a person decided. The
    worktree is removed afterwards, as `_repetition` would have removed it.
    """
    checked = _checked(spec)
    store = Store(home / "telltale.db").open()
    receiver = Receiver(store, level=int(checked["level"]))
    port = receiver.start()
    try:
        capture_id = _claimed_capture(store, checked, attempt)
        session = _session_of(store, capture_id)
        _correlate(port, capture_id, checked, attempt, session)
        acceptance = _accept(checked, worktree)
        _outcome(port, capture_id, checked, attempt, acceptance)
    finally:
        receiver.stop()
        store.flush()
        store.close()
    _git(checked["repo"], "worktree", "remove", "--force", str(worktree))
    _git(checked["repo"], "worktree", "prune")
    return {"attempt": attempt, "capture_id": capture_id, "acceptance": acceptance}


def _repetition(
    spec: Mapping[str, Any], home: Path, store: Store, port: int, attempt: int
) -> dict[str, Any]:
    """One worktree, one capture, one acceptance run, and the worktree gone again."""
    worktree = home / "worktrees" / f"{spec['experiment']}-{spec['task_id']}-{attempt}"
    _git(spec["repo"], "worktree", "add", "--detach", str(worktree), spec["base_sha"])
    try:
        before = _capture_ids(store)
        started = time.monotonic_ns()
        exit_code = _launch(spec, home, worktree, attempt)
        wall_ms = (time.monotonic_ns() - started) // 1_000_000
        capture_id = _new_capture(store, before, spec, attempt)
        session = _session_of(store, capture_id)
        _correlate(port, capture_id, spec, attempt, session)
        acceptance = _accept(spec, worktree)
        _outcome(port, capture_id, spec, attempt, acceptance)
    finally:
        _git(spec["repo"], "worktree", "remove", "--force", str(worktree))
    known = facts(store, capture_id)
    return {
        "attempt": attempt,
        "capture_id": capture_id,
        "exit_code": exit_code,
        "wall_ms": wall_ms,
        "duration_ms": known.duration_ms,
        "coverage": known.coverage(),
        "provider_session_id": session,
        "acceptance": acceptance,
    }


def _launch(spec: Mapping[str, Any], home: Path, worktree: Path, attempt: int) -> int:
    """`telltale run` in the worktree. Its exit code is the agent's own."""
    done = subprocess.run(
        [
            _telltale(),
            "run",
            "--provider",
            str(spec["provider"]),
            "--level",
            str(spec["level"]),
            "--task-id",
            str(spec["task_id"]),
            "--attempt",
            str(attempt),
            "--experiment",
            str(spec["experiment"]),
            "--",
            *[str(word) for word in spec["command"]],
        ],
        cwd=worktree,
        # TELLTALE_HOME is passed rather than inherited: `repeat` was given a home and
        # every capture it counts has to be in that one.
        env={**os.environ, "TELLTALE_HOME": str(home)},
        capture_output=True,
        check=False,
    )
    return done.returncode


def _accept(spec: Mapping[str, Any], worktree: Path) -> dict[str, Any]:
    """The acceptance command, in the worktree, after the agent has gone.

    Design 6.12: deterministic, and executed by the harness rather than by the agent. A
    command that cannot be executed at all stops the experiment: it is a broken spec,
    and every later repetition would break the same way.
    """
    argv = [str(word) for word in spec["acceptance"]]
    try:
        done = subprocess.run(argv, cwd=worktree, capture_output=True, check=False)
    except OSError as error:
        raise SpecError(f"acceptance command {argv} could not run: {error}") from None
    return {
        "command": argv,
        "exit_code": done.returncode,
        "status": "pass" if done.returncode == 0 else "fail",
    }


# -- the external API the orchestrator writes through ---------------------------------


def _correlate(
    port: int,
    capture_id: str,
    spec: Mapping[str, Any],
    attempt: int,
    session: str | None,
) -> None:
    """POST /v1/correlations: this capture is attempt N of this task. Design 6.3."""
    _external(port, "/v1/correlations", capture_id, {
        "external_system": EXTERNAL_SYSTEM,
        "external_run_id": _run_id(spec, attempt),
        "component_id": str(spec["task_id"]),
        "task_id": str(spec["task_id"]),
        "attempt": attempt,
        "provider_session_id": session,
    })  # fmt: skip


def _outcome(
    port: int,
    capture_id: str,
    spec: Mapping[str, Any],
    attempt: int,
    acceptance: Mapping[str, Any],
) -> None:
    """POST /v1/outcomes: the harness ran the acceptance command and it passed or not.

    external.outcome has no task_id field in the allowlist, so the task travels in
    component_id and in external_run_id, which are the two the schema does have. A
    field this body does not carry is a field the store would drop unrecorded.
    """
    _external(port, "/v1/outcomes", capture_id, {
        "kind": "mechanical_verification",
        "status": acceptance["status"],
        "external_run_id": _run_id(spec, attempt),
        "component_id": str(spec["task_id"]),
        "attempt": attempt,
        "timestamp": now_iso(),
    })  # fmt: skip


def _external(port: int, route: str, capture_id: str, body: Mapping[str, Any]) -> None:
    """One POST to the runner's own receiver, attributed by the capture query.

    The receiver of the capture itself is gone: it lived inside the `telltale run`
    process, which has exited. This one is in this process, over the same database, and
    `?capture=` is how design 6.6 lets an orchestrator attribute a record to a capture
    it did not serve.
    """
    payload = {name: value for name, value in body.items() if value is not None}
    _post(port, _with_capture(route, capture_id), to_json(payload).encode("utf-8"))


def _run_id(spec: Mapping[str, Any], attempt: int) -> str:
    return f"{spec['experiment']}/{spec['task_id']}/{attempt}"


# -- reading the captures back --------------------------------------------------------


def vector(store: Store, capture_id: str) -> dict[str, float | None]:
    """The per-capture numbers: spec 13.7's evidence vector, and what only a stream has.

    The 22 metrics of `cohorts.VECTOR`, keyed "family.metric" and read back out of the
    evidence table rather than recomputed, so a repetition's numbers and a
    `telltale vector` of the same capture cannot disagree about one. Beside them the
    three facts a stream carries that no reducer measures: the child's own duration_ms,
    its turn count, and its tool calls counted by name.

    A capture with NO evidence row is REFUSED by id. Since W2-T6 every launcher capture
    reduces itself at capture end, so an empty evidence table means the reduction did
    not run, and 22 unknowns would enter the statistics as a measured absence.

    Unknown stays None everywhere else. A metric whose evidence row carries a null value
    is None here, and so is every stream fact of a capture whose stream surface
    delivered nothing: "the agent made no tool calls" and "no tool call was observable"
    are different facts.
    """
    measured = {str(row["metric"]): row for row in store.evidence(capture_id)}
    if not measured:
        raise NotReduced(
            f"{capture_id} has no evidence row: it was never reduced, and a vector of"
            " unknowns here would be a measurement of a missing reducer"
        )
    out: dict[str, float | None] = {
        f"{family}.{name}": value_of(measured.get(name))
        for family, names in VECTOR
        for name in names
    }
    out.update(_stream_facts(store, capture_id))
    return out


def _stream_facts(store: Store, capture_id: str) -> dict[str, float | None]:
    """duration_ms, num_turns and the tool calls by name. None when there was no stream.

    `tool_calls` is the total, and it is the field `_filled` reads to tell a capture
    that used no Edit from a capture whose stream nobody saw.
    """
    rows = store.observations(capture_id)
    streamed = any(
        str(row["observation_type"]).startswith(_STREAM_PREFIX) for row in rows
    )
    result = next(
        (
            row["payload"]
            for row in rows
            if row["observation_type"] == "claude.stream.result"
        ),
        None,
    )
    out: dict[str, float | None] = {
        key: None if result is None else _number(result.get(key))
        for key in _RESULT_KEYS
    }
    calls = [
        str(row["payload"]["tool_name"])
        for row in rows
        if row["observation_type"] == "claude.stream.assistant"
        and row["payload"].get("tool_name")
    ]
    out["tool_calls"] = float(len(calls)) if streamed else None
    for name in sorted(set(calls)):
        out[f"{_TOOL_PREFIX}{name}"] = float(calls.count(name))
    return out


def _filled(
    vectors: Sequence[Mapping[str, float | None]],
) -> list[dict[str, float | None]]:
    """Give every vector every metric, with the one distinction that matters.

    A tool name is a column only because some capture used it. In a capture whose
    stream WAS observed, a name that never appears is a real zero. In a capture with no
    stream at all it is unknown, and `tool_calls` being None is what says so.
    """
    columns = sorted({name for one in vectors for name in one})
    out: list[dict[str, float | None]] = []
    for one in vectors:
        seen = one.get("tool_calls") is not None
        filled = dict(one)
        for name in columns:
            if name in filled:
                continue
            filled[name] = 0.0 if (seen and name.startswith(_TOOL_PREFIX)) else None
        out.append(filled)
    return out


def stats(values: Sequence[float | None]) -> dict[str, Any]:
    """n, median, scaled MAD, IQR, min, max and the sorted values. Never a mean alone.

    `n` counts the values that are KNOWN and `unknown` counts the rest, so a metric
    three of five captures could not report is visibly a metric of three, not of five.
    Nothing is imputed and no gap is filled: design invariant 5.
    """
    known = sorted(value for value in values if value is not None)
    empty: dict[str, Any] = {
        "n": len(known),
        "unknown": len(values) - len(known),
        "median": None,
        "mad_scaled": None,
        "iqr": None,
        "min": None,
        "max": None,
        "values": known,
    }
    if not known:
        return empty
    middle = median(known)
    if len(known) < 2:
        # Both spreads stay None. One value has a median and no dispersion: a MAD of 0
        # here would read as "this measure does not vary", and it would make the
        # minimal detectable difference of design 6.12 zero, which says every
        # difference is resolvable at n=1.
        return {**empty, "median": middle, "min": known[0], "max": known[-1]}
    quarters = quantiles(known, n=4, method="inclusive")
    return {
        **empty,
        "median": middle,
        "mad_scaled": MAD_SCALE * median([abs(value - middle) for value in known]),
        "iqr": quarters[2] - quarters[0],
        "min": known[0],
        "max": known[-1],
    }


def one_fingerprint(store: Store, capture_ids: Sequence[str]) -> str:
    """The environment fingerprint these captures share, or refuse and name the fields.

    Design 6.12: the harness asserts fingerprints identical within a condition. This is
    that assertion, and it reports WHICH fields differ, because "these are two
    environments" is not actionable and "these are two environments and the model field
    is the difference" is.
    """
    seen = {capture_id: _environment(store, capture_id) for capture_id in capture_ids}
    ids = {found for found, _payload in seen.values()}
    if len(ids) == 1 and None not in ids:
        return str(next(iter(ids)))
    differing = _differing({name: payload for name, (_id, payload) in seen.items()})
    raise FingerprintMismatch(
        f"{len(ids)} environment fingerprints across {len(seen)} captures"
        f" ({', '.join(sorted(capture_ids))}); fields that differ: {differing}"
    )


def _environment(store: Store, capture_id: str) -> tuple[str | None, dict[str, Any]]:
    for row in store.observations(capture_id):
        if row["observation_type"] == "telltale.environment":
            found = row["environment_fingerprint_id"]
            return (str(found) if found else None, dict(row["payload"]))
    return None, {}


def environment_payload(store: Store, capture_id: str) -> dict[str, Any]:
    """The telltale.environment payload of one capture, {} when it recorded none.

    The reader experiments_env.py needs and this module already had. The between-arm
    assertion of design 6.12 compares payload FIELDS rather than the ids
    `one_fingerprint` returns: "these are two environments" is not the finding, and
    which field made them two is.
    """
    _id, payload = _environment(store, capture_id)
    return payload


def _differing(payloads: Mapping[str, Mapping[str, Any]]) -> str:
    """The fingerprint fields whose value is not the same in every capture."""
    names = sorted({name for payload in payloads.values() for name in payload})
    out = []
    for name in names:
        values = {to_json(payload.get(name)) for payload in payloads.values()}
        if len(values) > 1:
            out.append(f"{name}={sorted(values)}")
    return "; ".join(out) or "none (one capture recorded no environment at all)"


# -- the report -----------------------------------------------------------------------


def _report(
    spec: Mapping[str, Any],
    store: Store,
    runs: list[dict[str, Any]],
    out: Path | None,
    recovered: bool = False,
) -> dict[str, Any]:
    captures = [str(run["capture_id"]) for run in runs]
    fingerprint = one_fingerprint(store, captures)
    vectors = _filled([vector(store, capture_id) for capture_id in captures])
    for run, one in zip(runs, vectors, strict=True):
        run["vector"] = one
    columns = sorted({name for one in vectors for name in one})
    report = {
        "experiment": spec["experiment"],
        "task_id": spec["task_id"],
        "created_at": now_iso(),
        "spec": dict(spec),
        "environment_fingerprint_id": fingerprint,
        "captures": captures,
        "repetitions": runs,
        "stats": {name: stats([one[name] for one in vectors]) for name in columns},
        "acceptance": _acceptance_counts(runs),
        # Design 6.13: a printed number carries its claim class. Per-capture numbers are
        # derived from one capture's own observations; the statistics are comparative
        # and the cohort below is the whole of what they may be compared within.
        "claim_class": {"vector": "derived", "stats": "comparative"},
        "cohort": {
            "experiment": spec["experiment"],
            "task_id": spec["task_id"],
            "base_sha": spec["base_sha"],
            "provider": spec["provider"],
            "content_level": spec["level"],
            "environment_fingerprint_id": fingerprint,
            "n": len(runs),
        },
        "assumptions": [
            "one condition: one task, one base commit, one environment fingerprint,"
            " one fresh worktree per repetition",
            "the statistics are comparative WITHIN this condition and say nothing"
            " about any other condition",
            "the per-capture vector is spec 13.7's 22 metrics read back out of the"
            " evidence table, plus duration_ms, num_turns and the tool calls by name,"
            " which are stream facts and not measures",
            *(_RECOVERED_ASSUMPTIONS if recovered else ()),
        ],
        "recovered_from_store": recovered,
        "warnings": _warnings(runs),
    }
    if out is not None:
        _write(report, out / str(spec["task_id"]))
    return report


def _acceptance_counts(runs: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0}
    for run in runs:
        status = str(run["acceptance"]["status"])
        counts[status] = counts.get(status, 0) + 1
    return counts


def _warnings(runs: Iterable[Mapping[str, Any]]) -> list[str]:
    quiet = [
        str(run["capture_id"])
        for run in runs
        if run["vector"].get("tool_calls") is None
    ]
    if not quiet:
        return []
    return [
        f"{len(quiet)} capture(s) recorded no stream surface, so every per-capture"
        f" number for them is unknown rather than zero: {', '.join(quiet)}"
    ]


def _write(report: Mapping[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "report.json"
    # Trailing newline, so a committed report and a regenerated one are the same bytes:
    # without it the end-of-file-fixer hook rewrites the file on every commit and the
    # artefact in git stops matching what the runner writes.
    path.write_text(to_json(report) + "\n", encoding="utf-8")
    return path


# -- the pieces --------------------------------------------------------------------


def _telltale() -> str:
    found = shutil.which("telltale")
    if found is None:
        raise SpecError("no `telltale` on PATH: run `uv sync` first")
    return found


def _git(repo: str, *args: str) -> None:
    # Never shell=True: a path or a branch name out of a repository under observation
    # must not become a command (design 6.8).
    subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True)


def _capture_ids(store: Store) -> set[str]:
    return {str(row["capture_id"]) for row in store.captures()}


def _new_capture(
    store: Store, before: set[str], spec: Mapping[str, Any], attempt: int
) -> str:
    """The capture this repetition wrote, proved rather than assumed.

    New ids alone would be a guess: a daemon or another launcher can write into the
    same database while this runs. So the new ids are filtered by what the capture
    SAYS about itself, and anything other than exactly one is refused here rather than
    carried into the statistics.
    """
    fresh = sorted(_capture_ids(store) - before)
    matched = [
        capture_id for capture_id in fresh if _claims(store, capture_id, spec, attempt)
    ]
    if len(matched) != 1:
        raise SpecError(
            f"attempt {attempt} of {spec['task_id']}: {len(matched)} captures claim it"
            f" (new captures: {fresh})"
        )
    return matched[0]


def _claims(
    store: Store, capture_id: str, spec: Mapping[str, Any], attempt: int
) -> bool:
    for row in store.observations(capture_id):
        if row["observation_type"] != "telltale.capture_started":
            continue
        payload = row["payload"]
        claimed = (
            payload.get("task_id"),
            payload.get("attempt"),
            payload.get("experiment"),
        )
        return claimed == (spec["task_id"], attempt, spec["experiment"])
    return False


def _session_of(store: Store, capture_id: str) -> str | None:
    """The provider session id this capture carries, or None when it named none."""
    for row in store.observations(capture_id):
        found = row["provider_session_id"]
        if found:
            return str(found)
    return None


def _number(value: Any) -> float | None:
    """A payload value as a float, or None. A bool is not a number here."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
