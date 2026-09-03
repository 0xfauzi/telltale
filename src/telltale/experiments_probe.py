"""`telltale experiment probe`: fixed read-only probes, scored against an answer key.

Spec 14.3. A PROBE is a question with a deterministic answer that a person wrote down
before the run: "locate the implementation behind this interface", "identify the tests
covering this module". The answer key is a set of repository-relative paths and a set of
symbols, and the agent's final answer is scored against it. Spec 14.3's last sentence is
the reason the scoring exists at all: cheap behaviour with a wrong answer is not an
improvement, and a runner that reported only tool calls and tokens would call the cheap
wrong answer the better one.

Three decisions are worth stating, because each looks like an omission in the code:

  THE ANSWER TEXT IS READ AND THROWN AWAY. `experiments._launch` returns the finished
  `telltale run`, whose stdout is the child's own bytes (launch._tee echoes every line
  unchanged before recording it). This module parses the last `result` message out of
  those bytes, scores it here, in this process, and stores the SCORE. The text itself
  never reaches the store, and never could: `result`, `text` and `content` are in
  sanitize.NEVER_PERSIST and the claude.stream.result allowlist has no field for it.
  A harness reading its child's stdout is the same move the repeat runner makes when it
  reads the child's exit code.

  A PROBE CONDITION IS NEVER RESUMED. `experiments.repeat` reads attempt k back out of
  the store when a capture already claims it, because everything its report needs is on
  the capture. A probe's score is not: the answer it was computed from is gone by
  design. So a spec whose (task, attempt) pairs are already in the store is REFUSED by
  name rather than reported with unknown scores.

  AN ANSWER-KEY PATH THAT DOES NOT EXIST AT base_sha IS REFUSED. A key naming a file the
  commit does not carry would score recall 0 for every repetition and read as an agent
  that could not find it. That is a typo in the key, and it is caught before any token
  is spent.

Nothing here writes an Evidence row. `experiments_measure.vector` reads the numbers
measures.py already wrote, and the two score columns are added beside them so that one
`stats` call covers both: per-repetition numbers are derived and the statistics over
them are comparative WITHIN one probe.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import experiments_stop as stopping
from telltale import stats as between
from telltale.experiments import (
    SpecError,
    _approvable,
    _capture_ids,
    _correlate,
    _external,
    _git,
    _launch,
    _new_capture,
    _run_id,
    _session_of,
)
from telltale.experiments_measure import _filled, one_fingerprint, stats, vector
from telltale.facts import facts
from telltale.model import now_iso, to_json
from telltale.receiver import Receiver
from telltale.sanitize import MAX_ENUM
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

# The spec, in full. No `acceptance`: a probe is READ-ONLY (spec 14.3), so there is
# nothing in the worktree for a command to verify afterwards, and the answer key IS the
# acceptance criterion. `command` is the argv with the prompt of each probe substituted
# into it, so every probe of a suite runs the same agent under the same flags.
SPEC_KEYS = (
    "task_id", "experiment", "repo", "base_sha", "command", "probes",
    "repetitions", "provider", "level",
)  # fmt: skip

# What a spec MAY carry and need not. A suite without `stop` runs unbounded, which is
# what every suite before W4-E10 did, and its report says so rather than printing a
# bound it never had. experiments_stop.py is what the block means.
OPTIONAL_SPEC_KEYS = ("stop",)

PROBE_KEYS = ("probe_id", "prompt", "answer_key")
ANSWER_KEY_KEYS = ("paths", "symbols")

# What `command` must contain at least once, and what each occurrence becomes. A command
# that does not carry it would run one prompt N times and report N probes.
PLACEHOLDER = "{prompt}"

# The score columns, keyed the way `cohorts.VECTOR` keys a measure, so they travel
# through `stats` and `stats.compare` on the same path as every other number.
PRECISION = "probe_score.precision"
RECALL = "probe_score.recall"

# The categories the outcome carries. `external.outcome.categories` is Kind.ENUM, a
# bounded list of short symbolic strings, so a score reaches the store as text and no
# new observation type is needed. sanitize.MAX_ENUM is the bound each one is cut at,
# and `_checked` refuses a probe_id that would reach it rather than letting the
# sanitizer truncate one silently.
_CATEGORY = "probe:{probe_id}"
_UNKNOWN_SCORE = "unknown"

# A path-shaped run of characters. Everything outside it separates two tokens, so
# "in `src/a.py`, which" and "src/a.py:42" both yield "src/a.py".
_TOKEN = re.compile(r"[\w./-]+")

_ASSUMPTIONS = (
    "one probe suite: one task, one base commit, one environment fingerprint, one"
    " fresh worktree per repetition, and one answer key per probe written before the"
    " run",
    "the statistics are comparative WITHIN one probe and say nothing about any other"
    " probe, task or repository",
    "recall = matched_keys / len(answer_key), over the paths AND the symbols of the"
    " key. precision = matched_keys / (matched_keys + wrong_paths), where a wrong path"
    " is a path the answer named that exists at base_sha and is not in the key: the"
    " repository enumerates its own paths and does not enumerate the symbols an answer"
    " could name, so a wrongly named SYMBOL is not counted anywhere and precision is"
    " an upper bound",
    "a path counts as matched only on an exact repository-relative match; a match on"
    " the basename alone is reported beside it under basename_only and is not scored",
    "a repetition whose stdout carried no result message, or an empty one, scores None"
    " for both numbers and never 0: the agent answering nothing and the harness seeing"
    " nothing are the same absence here",
    "the agent's answer text is read in this process, scored, and dropped. Only the"
    " score reaches the store, as external.outcome categories",
)


# -- one probe suite ------------------------------------------------------------------


def probe(
    spec: Mapping[str, Any], home: Path, out: Path | None = None
) -> dict[str, Any]:
    """Run every probe `repetitions` times and return the scored report. Spec 14.3.

    One spec through the loop the intervention drives with two, so that the order and
    the stop bound are one implementation rather than two that can disagree.
    """
    return suite([spec], home, out)[0]


def suite(
    specs: Sequence[Mapping[str, Any]], home: Path, out: Path | None = None
) -> list[dict[str, Any]]:
    """Several probe specs, run REPETITION-MAJOR across all of them, one report each.

    `home` is the $TELLTALE_HOME the captures are written into, and it is passed to each
    child rather than inherited, for the reason `experiments.repeat` states.

    Every spec is checked, and every answer key checked against its own commit, before
    any session starts: a suite that refuses on its second arm once ten captures exist
    has already spent what the refusal was for. One receiver per spec, because the
    receiver carries the content level and two arms may declare two.
    """
    checked = [_checked(spec) for spec in specs]
    tracked = [_tracked(spec["repo"], spec["base_sha"]) for spec in checked]
    for spec, known in zip(checked, tracked, strict=True):
        _keys_present(spec, known)
        for probing in spec["probes"]:
            _approvable(_substituted(spec["command"], str(probing["prompt"])))
    store = Store(home / "telltale.db").open()
    try:
        for spec in checked:
            _unclaimed(store, spec)
        receivers = [Receiver(store, level=int(spec["level"])) for spec in checked]
        try:
            ports = [found.start() for found in receivers]
            runs, stopped = _sessions(checked, tracked, ports, home, store)
        finally:
            for found in receivers:
                found.stop()
            store.flush()
        return [
            _report(spec, store, found, stopped, out)
            for spec, found in zip(checked, runs, strict=True)
        ]
    finally:
        store.close()


def _sessions(
    checked: Sequence[Mapping[str, Any]],
    tracked: Sequence[Sequence[str]],
    ports: Sequence[int],
    home: Path,
    store: Store,
) -> tuple[list[dict[str, list[dict[str, Any]]]], dict[str, Any]]:
    """One session per (repetition, probe, spec) in `_order`, until a bound ends it."""
    runs: list[dict[str, list[dict[str, Any]]]] = [
        {str(probing["probe_id"]): [] for probing in spec["probes"]} for spec in checked
    ]
    stop = stopping.one_bound(checked)
    found: dict[str, Any] | None = None
    for attempt, index, position in _order(checked):
        spec = checked[index]
        probing = spec["probes"][position]
        run = _repetition(
            spec, probing, home, store, ports[index], tracked[index], attempt
        )
        runs[index][str(probing["probe_id"])].append(run)
        found = stopping.crossed(stop, spec, probing, run)
        if found is not None:
            break
    ran = [run for arm in runs for rows in arm.values() for run in rows]
    return runs, stopping.record(len(_order(checked)), ran, stop, found)


def _order(checked: Sequence[Mapping[str, Any]]) -> list[tuple[int, int, int]]:
    """(attempt, spec, probe), with the REPETITION outermost. W4-E10's first lesson.

    E09 ran every repetition of one probe before starting the next probe, and a two-arm
    suite ran every session of one arm before the other, so the first session over a
    bound was one of twenty and the crossing could only be read afterwards. With the
    repetition outermost, round 1 is one session of every probe of every spec: the pilot
    design 6.12 asks for, without a separate pilot spec, and the earliest point at which
    a bound can end a run.

    Inside a round the PROBE is outer and the spec inner, so the two arms' sessions for
    one probe are adjacent in time. Two arms compared across a gap filled with other
    sessions would carry whatever moved in that gap into the pairing, and the pairing is
    by probe.
    """
    rounds = max(int(spec["repetitions"]) for spec in checked)
    width = max(len(spec["probes"]) for spec in checked)
    return [
        (attempt, index, position)
        for attempt in range(1, rounds + 1)
        for position in range(width)
        for index, spec in enumerate(checked)
        if attempt <= int(spec["repetitions"]) and position < len(spec["probes"])
    ]


def _checked(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The spec, whole, before anything runs. Every refusal names what is wrong."""
    missing = sorted(set(SPEC_KEYS) - set(spec))
    unknown = sorted(set(spec) - set(SPEC_KEYS) - set(OPTIONAL_SPEC_KEYS))
    if missing or unknown:
        raise SpecError(f"spec: missing {missing}, unexpected {unknown}")
    checked = dict(spec)
    # Always present afterwards, None when the spec named no bound, so that every later
    # reader asks one question instead of two.
    checked["stop"] = stopping.checked(checked.get("stop"))
    if not isinstance(checked["command"], list) or not checked["command"]:
        raise SpecError("spec: command is a non-empty argv list")
    if not any(PLACEHOLDER in str(word) for word in checked["command"]):
        raise SpecError(
            f"spec: no {PLACEHOLDER} in the command, so every probe would run the same"
            " prompt and one session would be reported as several probes"
        )
    if not isinstance(checked["repetitions"], int) or checked["repetitions"] < 1:
        raise SpecError(f"spec: repetitions is {checked['repetitions']!r}, not a count")
    if not isinstance(checked["probes"], list) or not checked["probes"]:
        raise SpecError("spec: probes is a non-empty list")
    checked["probes"] = [_checked_probe(one) for one in checked["probes"]]
    ids = [str(one["probe_id"]) for one in checked["probes"]]
    if len(set(ids)) != len(ids):
        raise SpecError(f"spec: two probes share one probe_id ({sorted(ids)})")
    checked["repo"] = str(Path(str(checked["repo"])).expanduser().resolve())
    return checked


def _checked_probe(one: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(set(PROBE_KEYS) - set(one))
    unknown = sorted(set(one) - set(PROBE_KEYS))
    if missing or unknown:
        raise SpecError(f"probe: missing {missing}, unexpected {unknown}")
    probe_id = str(one["probe_id"])
    # Refused here rather than truncated by the sanitizer: a probe_id cut at 64
    # characters would put two probes' scores under one category string.
    if len(_CATEGORY.format(probe_id=probe_id)) > MAX_ENUM:
        raise SpecError(
            f"probe {probe_id!r}: the category {_CATEGORY.format(probe_id=probe_id)!r}"
            f" is longer than the {MAX_ENUM}-character bound the outcome allowlist cuts"
            " an enum at, and a cut probe_id is two probes reported as one"
        )
    if not str(one["prompt"]).strip():
        raise SpecError(f"probe {probe_id!r}: prompt is empty")
    return {**one, "probe_id": probe_id, "answer_key": _checked_key(probe_id, one)}


def _checked_key(probe_id: str, one: Mapping[str, Any]) -> dict[str, list[str]]:
    key = one["answer_key"]
    if not isinstance(key, dict):
        raise SpecError(f"probe {probe_id!r}: answer_key is an object")
    unknown = sorted(set(key) - set(ANSWER_KEY_KEYS))
    if unknown:
        raise SpecError(f"probe {probe_id!r}: answer_key has unexpected {unknown}")
    out = {name: [str(item) for item in key.get(name, [])] for name in ANSWER_KEY_KEYS}
    if not out["paths"] and not out["symbols"]:
        raise SpecError(
            f"probe {probe_id!r}: the answer key names no path and no symbol, so"
            " recall has no denominator and nothing about the answer is decidable"
        )
    for name in ANSWER_KEY_KEYS:
        if len(set(out[name])) != len(out[name]):
            raise SpecError(f"probe {probe_id!r}: answer_key {name} repeats an entry")
    return out


def _keys_present(spec: Mapping[str, Any], tracked: Sequence[str]) -> None:
    """Every answer-key path is in the tree at base_sha, or refuse and name the rest.

    A key path the commit does not carry scores recall 0 in every repetition and reads
    as an agent that could not find the file. It is a typo in the key, and the whole
    point of a pre-registered answer key is that it was checkable before the run.
    """
    known = set(tracked)
    for one in spec["probes"]:
        astray = sorted(set(one["answer_key"]["paths"]) - known)
        if astray:
            raise SpecError(
                f"probe {one['probe_id']!r}: {astray} not in {spec['repo']} at"
                f" {spec['base_sha']} ({len(known)} tracked paths there). An answer key"
                " naming a path the commit does not carry scores 0 recall for a reason"
                " that is not about the agent"
            )


def _unclaimed(store: Store, spec: Mapping[str, Any]) -> None:
    """Refuse when a capture already claims one of these (task, attempt) pairs.

    The repeat runner READS such a capture back and counts it as that repetition. This
    one cannot: a probe repetition is its score, the score comes from the answer text,
    and the answer text is never stored. Reporting the capture with an unknown score
    would put a hole in the condition that looks like a measurement.
    """
    wanted = {
        (f"{spec['task_id']}-{one['probe_id']}", attempt)
        for one in spec["probes"]
        for attempt in range(1, int(spec["repetitions"]) + 1)
    }
    found = sorted(
        f"{row['payload'].get('task_id')}/{row['payload'].get('attempt')}"
        f" ({row['capture_id']})"
        for row in store.observations_of_type("telltale.capture_started")
        if row["payload"].get("experiment") == spec["experiment"]
        and (row["payload"].get("task_id"), row["payload"].get("attempt")) in wanted
    )
    if found:
        raise SpecError(
            f"{len(found)} capture(s) already claim an attempt of this suite ({found})."
            " A probe condition is never resumed: the score is computed from the"
            " agent's answer, which is never stored, so a capture read back would enter"
            " the statistics with an unknown score. Use a new experiment id, a new"
            " task_id, or purge those captures"
        )


def _repetition(
    spec: Mapping[str, Any],
    one: Mapping[str, Any],
    home: Path,
    store: Store,
    port: int,
    tracked: Sequence[str],
    attempt: int,
) -> dict[str, Any]:
    """One worktree, one capture, one scored answer, and the worktree gone again."""
    condition = _condition(spec, one)
    name = f"{spec['experiment']}-{condition['task_id']}-{attempt}"
    worktree = home / "worktrees" / name
    _git(spec["repo"], "worktree", "add", "--detach", str(worktree), spec["base_sha"])
    try:
        before = _capture_ids(store)
        started = time.monotonic_ns()
        done = _launch(condition, home, worktree, attempt)
        wall_ms = (time.monotonic_ns() - started) // 1_000_000
        capture_id = _new_capture(store, before, condition, attempt)
        session = _session_of(store, capture_id)
        _correlate(port, capture_id, condition, attempt, session)
        scored = score(_answer(done.stdout), one["answer_key"], tracked)
        _outcome(port, capture_id, condition, attempt, str(one["probe_id"]), scored)
    finally:
        _git(spec["repo"], "worktree", "remove", "--force", str(worktree))
    known = facts(store, capture_id)
    # Read here rather than in the report, because the stop bound is compared with it
    # before the next session starts and the report is written after the last one.
    tokens, absent = stopping.session_tokens(store, capture_id)
    return {
        "attempt": attempt,
        "capture_id": capture_id,
        "exit_code": done.returncode,
        "wall_ms": wall_ms,
        "duration_ms": known.duration_ms,
        "coverage": known.coverage(),
        "provider_session_id": session,
        "session_tokens": tokens,
        "session_tokens_missing": absent,
        "score": scored,
    }


def _condition(spec: Mapping[str, Any], one: Mapping[str, Any]) -> dict[str, Any]:
    """One probe as the condition `experiments._launch` and `_new_capture` read.

    The probe_id is the task id's suffix, for the reason experiments_env gives about an
    arm's name: two probes are two conditions, and a correlation naming one task for
    both would say the captures were repetitions of one question.
    """
    return {
        "task_id": f"{spec['task_id']}-{one['probe_id']}",
        "experiment": spec["experiment"],
        "repo": spec["repo"],
        "base_sha": spec["base_sha"],
        "command": _substituted(spec["command"], str(one["prompt"])),
        "provider": spec["provider"],
        "level": spec["level"],
    }


def _substituted(command: Sequence[Any], prompt: str) -> list[str]:
    """The argv with every occurrence of the placeholder replaced by this prompt."""
    return [str(word).replace(PLACEHOLDER, prompt) for word in command]


def _tracked(repo: str, base_sha: str) -> tuple[str, ...]:
    """Every path the tree at base_sha carries, repository-relative and POSIX.

    From `git ls-tree` rather than from the worktree on disk, so the set is the COMMIT's
    and never picks up an untracked file a repetition happened to leave behind.
    """
    done = subprocess.run(
        ["git", "-C", repo, "ls-tree", "-r", "--name-only", "-z", base_sha],
        capture_output=True,
        check=True,
    )
    return tuple(
        name for name in done.stdout.decode("utf-8", "replace").split("\0") if name
    )


# -- the score ------------------------------------------------------------------------


def _answer(raw: bytes) -> str | None:
    """The text of the LAST `result` message on the child's stdout, or None.

    None when there is no result message and when its `result` field is absent or not a
    string. A line that is not JSON is skipped rather than refused: the child's stdout
    is its own, and a provider that prints a banner has not failed to answer.
    """
    found: str | None = None
    for line in raw.decode("utf-8", "replace").splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            message = json.loads(text)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("type") == "result":
            answer = message.get("result")
            found = answer if isinstance(answer, str) else found
    return found


def score(
    answer: str | None, key: Mapping[str, Sequence[str]], tracked: Sequence[str]
) -> dict[str, Any]:
    """One answer against one key. Two numbers, and the counts they were computed from.

    Both definitions are in the report's assumptions and both are stated here because
    neither is the textbook one:

      RECALL is `matched_keys / len(key)` over the paths and the symbols together. That
      one is the textbook definition and it is computable: the key is finite and known.

      PRECISION is `matched_keys / (matched_keys + wrong_paths)`. The textbook
      denominator is everything the answer asserted, and that is NOT computable from
      text: there is no way to enumerate what an arbitrary sentence claimed. So the
      denominator is the answer-key vocabulary plus what the REPOSITORY can recognize.
      A wrong path is one the answer named, that exists at base_sha, and that the key
      does not carry. A wrongly named symbol is not counted at all, because a repository
      does not enumerate the symbols an answer could invent, which makes this number an
      upper bound on precision rather than an estimate of it.

    Both are None when the answer is missing or blank, and precision is also None when
    the answer named nothing the key or the repository recognizes: a ratio with an empty
    denominator is unknown, not 0.
    """
    paths, symbols = list(key["paths"]), list(key["symbols"])
    size = len(paths) + len(symbols)
    if answer is None or not answer.strip():
        return _no_score(size)
    tokens = _tokens(answer)
    named = _named(tokens, {*tracked, *paths})
    matched_paths = [one for one in paths if one in named]
    basenames = _named(tokens, {_base(one) for one in paths})
    matched_symbols = [one for one in symbols if _worded(answer, one)]
    wrong = sorted(one for one in named if one not in set(paths))
    matched = len(matched_paths) + len(matched_symbols)
    claimed = matched + len(wrong)
    return {
        "status": "pass" if matched == size and not wrong else "fail",
        # Not 0 when nothing was claimed: a ratio with an empty denominator is unknown.
        "precision": None if claimed == 0 else matched / claimed,
        "recall": matched / size,
        "key_size": size,
        "matched_keys": matched,
        "matched_paths": matched_paths,
        "matched_symbols": matched_symbols,
        "basename_only": [
            one for one in paths if one not in named and _base(one) in basenames
        ],
        "wrong_paths": wrong,
    }


def _no_score(size: int) -> dict[str, Any]:
    return {
        "status": _UNKNOWN_SCORE,
        "precision": None,
        "recall": None,
        "key_size": size,
        "matched_keys": None,
        "matched_paths": [],
        "matched_symbols": [],
        "basename_only": [],
        "wrong_paths": [],
    }


def _tokens(answer: str) -> set[str]:
    """The path-shaped runs in the answer, with a trailing sentence dot removed."""
    found = set(_TOKEN.findall(answer))
    return found | {one.rstrip(".") for one in found}


def _named(tokens: Iterable[str], candidates: set[str]) -> set[str]:
    """The candidates the answer named: a token that IS one, or ends `/` plus one.

    The suffix rule is what lets "/tmp/w/src/a.py" and "./src/a.py" name `src/a.py`
    while "bsrc/a.py" and "src/a.pyx" do not: only a whole path component may precede
    the match. Walking the tokens rather than the candidates keeps this linear in the
    answer's length instead of in the size of the repository.
    """
    found = set()
    for token in tokens:
        if token in candidates:
            found.add(token)
        for index, character in enumerate(token):
            tail = token[index + 1 :]
            if character == "/" and tail in candidates:
                found.add(tail)
    return found


def _base(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _worded(answer: str, symbol: str) -> bool:
    """True when the symbol appears as a whole word, dots and all.

    Boundaries are identifier characters rather than `\\b`, so `Store.open` matches the
    literal and `open` does not match it, and `parse` does not match `parsed`.
    """
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])"
    return re.search(pattern, answer) is not None


def _outcome(
    port: int,
    capture_id: str,
    spec: Mapping[str, Any],
    attempt: int,
    probe_id: str,
    scored: Mapping[str, Any],
) -> None:
    """POST /v1/outcomes: the harness scored the answer against the key. Design 6.3.

    kind `mechanical_verification` because that is what this is: a deterministic key,
    checked by the harness after the agent exited, with no judgement in the loop. The
    two numbers travel in `categories`, which the allowlist carries as a bounded list of
    short symbolic strings, so no new observation type is needed and no free text
    reaches the store. A number that is unknown says so in words rather than being left
    out, because a missing category and a category that means unknown are different
    facts and only one of them is a measurement.
    """
    _external(port, "/v1/outcomes", capture_id, {
        "kind": "mechanical_verification",
        "status": str(scored["status"]),
        "categories": [
            _CATEGORY.format(probe_id=probe_id),
            f"precision:{_number(scored['precision'])}",
            f"recall:{_number(scored['recall'])}",
        ],
        "external_run_id": _run_id(spec, attempt),
        "component_id": str(spec["task_id"]),
        "attempt": attempt,
        "timestamp": now_iso(),
    })  # fmt: skip


def _number(value: float | None) -> str:
    return _UNKNOWN_SCORE if value is None else f"{value:.3f}"


# -- the report -----------------------------------------------------------------------


def _report(
    spec: Mapping[str, Any],
    store: Store,
    runs: Mapping[str, list[dict[str, Any]]],
    stopped: Mapping[str, Any],
    out: Path | None,
) -> dict[str, Any]:
    captures = [run["capture_id"] for found in runs.values() for run in found]
    # Spec 14.3: the environment fingerprint is held constant across the probes, so the
    # assertion is over the whole suite rather than per probe. None when a bound ended
    # the run before this spec had a session: a suite with no capture has no
    # environment, and "0 fingerprints across 0 captures" is a refusal about a run that
    # did not happen.
    fingerprint = one_fingerprint(store, captures) if captures else None
    blocks = [
        _block(store, spec, one, runs[str(one["probe_id"])]) for one in spec["probes"]
    ]
    report = {
        "experiment": spec["experiment"],
        "task_id": spec["task_id"],
        "created_at": now_iso(),
        "spec": dict(spec),
        "environment_fingerprint_id": fingerprint,
        "captures": captures,
        "stop": dict(stopped),
        "probes": blocks,
        "claim_class": {
            "vector": "derived",
            "score": "derived",
            "stats": "comparative",
        },
        "cohort": {
            "experiment": spec["experiment"],
            "task_id": spec["task_id"],
            "base_sha": spec["base_sha"],
            "provider": spec["provider"],
            "content_level": spec["level"],
            "environment_fingerprint_id": fingerprint,
            "probes": len(blocks),
            "n": int(spec["repetitions"]),
        },
        "assumptions": list(_ASSUMPTIONS),
        "warnings": [
            *_stop_warnings(spec, stopped),
            *(
                f"{block['probe_id']}: {warning}"
                for block in blocks
                for warning in block["warnings"]
            ),
        ],
    }
    if out is not None:
        _write(report, out / str(spec["task_id"]))
    return report


def _stop_warnings(spec: Mapping[str, Any], stopped: Mapping[str, Any]) -> list[str]:
    """What a reader must know before taking `cohort.n` for the sample size."""
    out = []
    if stopped["stopped"]:
        found = stopped["crossed"]
        out.append(
            f"the run was ENDED by a stop bound: {found['task_id']} attempt"
            f" {found['attempt']} ({found['capture_id']}) reached"
            f" {found['observed']} against {found['bound']} = {found['limit']}, and"
            f" nothing after it was started. {stopped['sessions_run']} of"
            f" {stopped['sessions_planned']} sessions ran, so the n of every table"
            f" below is smaller than the {spec['repetitions']} this spec declared"
        )
    if stopped["token_total_unknown"]:
        out.append(
            f"{len(stopped['token_total_unknown'])} session(s) carried no token total"
            " and so were compared with no token bound at all:"
            f" {stopped['token_total_unknown']}"
        )
    return out


def _write(report: Mapping[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "probe.json"
    # Trailing newline, for the reason experiments._write states about report.json:
    # without it the end-of-file-fixer hook rewrites the file on every commit and the
    # artefact in git stops matching what the runner writes.
    path.write_text(to_json(report) + "\n", encoding="utf-8")
    return path


def _block(
    store: Store,
    spec: Mapping[str, Any],
    one: Mapping[str, Any],
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """One probe: its key, its repetitions, and the statistics over them."""
    vectors = _filled([_vector(store, run) for run in runs])
    for run, found in zip(runs, vectors, strict=True):
        run["vector"] = found
    columns = sorted({name for found in vectors for name in found})
    return {
        "probe_id": str(one["probe_id"]),
        "task_id": f"{spec['task_id']}-{one['probe_id']}",
        "prompt": str(one["prompt"]),
        "answer_key": dict(one["answer_key"]),
        "key_size": len(one["answer_key"]["paths"]) + len(one["answer_key"]["symbols"]),
        "captures": [run["capture_id"] for run in runs],
        "repetitions": runs,
        "stats": {
            name: _statistics([found[name] for found in vectors]) for name in columns
        },
        "scores": _counts(runs),
        "warnings": _warnings(runs),
    }


def _vector(store: Store, run: Mapping[str, Any]) -> dict[str, float | None]:
    """The evidence vector of one capture with the two score columns beside it.

    Beside rather than inside: `cohorts.VECTOR` is spec 13.7's registry of measures and
    a probe score is not one of them. Giving the score the same "family.metric" shape
    is what lets one `stats` call, one `stats.compare` and one printed table cover a
    token count and a precision without either of them being a special case.
    """
    scored = run["score"]
    return {
        **vector(store, str(run["capture_id"])),
        PRECISION: scored["precision"],
        RECALL: scored["recall"],
    }


def _statistics(values: Sequence[float | None]) -> dict[str, Any]:
    """The repeat runner's row, plus what design 6.12 asks a pilot to print.

    MDD and N_needed are design 6.12's own formulas at this condition's own n and its
    own scaled MAD: the smallest median difference a second condition of this size
    could be separated from run-to-run variation, and the per-arm n at which that falls
    to a quarter of the median. Both are None when the spread is unknown, which is what
    a single repetition leaves them.
    """
    found = stats(values)
    return {
        **found,
        "mdd": between.mdd(found["mad_scaled"], found["n"]),
        "n_needed": between.n_needed(found["mad_scaled"], found["median"]),
    }


def _counts(runs: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, _UNKNOWN_SCORE: 0}
    for run in runs:
        status = str(run["score"]["status"])
        counts[status] = counts.get(status, 0) + 1
    return counts


def _warnings(runs: Sequence[Mapping[str, Any]]) -> list[str]:
    out = []
    quiet = [
        str(run["capture_id"])
        for run in runs
        if run["vector"].get("tool_calls") is None
    ]
    if quiet:
        out.append(
            f"{len(quiet)} capture(s) recorded no stream surface, so every per-capture"
            f" number for them is unknown rather than zero: {', '.join(quiet)}"
        )
    silent = [
        str(run["capture_id"])
        for run in runs
        if run["score"]["status"] == _UNKNOWN_SCORE
    ]
    if silent:
        out.append(
            f"{len(silent)} repetition(s) printed no result message, so their score is"
            f" unknown and not 0, and n below counts the rest: {', '.join(silent)}"
        )
    flat = sorted(
        name
        for name in (PRECISION, RECALL)
        for row in [stats([run["vector"].get(name) for run in runs])]
        if row["mad_scaled"] == 0
    )
    if flat:
        out.append(
            f"{flat} have a scaled MAD of 0, so MDD is 0 for them and any nonzero"
            " difference against another condition would read as resolvable. That is a"
            " statement about a spread of 0, which is what a deterministic answer gives"
        )
    return out
