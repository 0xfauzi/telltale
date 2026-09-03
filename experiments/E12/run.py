"""E12: the blinded diagnosis protocol of `experiments/E12/protocol.md`, run.

One reviewer session answers ten fixed questions about one captured coding-agent
session, seeing either Telltale's summary of it (arm S) or the raw stream-json file
(arm R), and its answers are scored against `key.json`, which was written from the raw
streams before any reviewer ran. Nothing here may change the key, the questionnaire, the
session choice or the decision rule: they are in protocol.md and in key.json, and this
file reads them.

    uv run python experiments/E12/run.py --pilot
    uv run python experiments/E12/run.py
    uv run python experiments/E12/run.py --arm S
    uv run python experiments/E12/run.py --only W4-T1/1 --arm R
    uv run python experiments/E12/run.py --scan

Blinding is the experiment, so three things hold on every launch. The reviewer's working
directory is a fresh temporary directory OUTSIDE this repository holding the material
files and nothing else, which is also what stops `claude` walking up to this checkout's
CLAUDE.md and to the answer key beside it. The prompt is built from `key.json`'s
question text and names no task, no session, no arm and no date. And the reviewer's own
capture is read back afterwards for the paths it read; `blinding.py` is that check.

The reviewer's answer text is never written to disk. What is stored is the JSON object
parsed out of it, the score, and the numbers the launcher measured.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import blinding

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
OUT = HERE / "out"
STORE = OUT / "store"
TELLTALE = REPO_ROOT / ".venv" / "bin" / "telltale"

# The reviewer, from protocol.md "Sessions": one model, Opus, in both arms, and the
# turn bound the pilot was written to test the raw arm against.
MODEL = "opus"
MAX_TURNS = "60"

# protocol.md "STOP rules". Applied after a run, never during one: a reviewer killed
# mid-answer would be a missing measurement rather than a measured cost.
STOP_WALL_MS = 600_000
STOP_TOKENS = 2_000_000
STOP_MIN_ANSWERS = 5

# protocol.md's rule counts "the number of sessions (of 6)". Below six scored sessions
# in an arm the three labels are not defined, and printing "neither" for a pilot of one
# would report a rule's verdict where the rule does not apply.
SESSIONS_PER_ARM = 6

# The four counters that make a session's token total, under the names measures.py
# writes them. E05 and E06 use the same four and the gate's reference numbers are
# theirs. A counter with no value is NAMED rather than counted as zero.
TOKEN_KEYS = (
    "fresh_input_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "output_tokens",
)

VERDICTS = ("agree", "disagree", "unknown", "missing")

# Every pre-registered number this experiment runs under, in one place and written into
# out/summary.json, so the write-up quotes them out of the artefact rather than retyping
# them from protocol.md.
BOUNDS = {
    "stop_tokens": STOP_TOKENS,
    "stop_wall_ms": STOP_WALL_MS,
    "stop_min_answers": STOP_MIN_ANSWERS,
    "sessions_per_arm": SESSIONS_PER_ARM,
    "max_turns": int(MAX_TURNS),
    "reviewer_model": MODEL,
}

# A hard cap on one reviewer, well beyond the 10 minutes of the STOP rule, so a child
# that hangs cannot hold the harness for ever. Reaching it is recorded, not swallowed.
CHILD_TIMEOUT_S = 1500.0


def _key() -> dict[str, Any]:
    return dict(json.loads((HERE / "key.json").read_text(encoding="utf-8")))


def _manifest() -> dict[str, Any]:
    return dict(json.loads((HERE / "manifest.json").read_text(encoding="utf-8")))


def _slug(label: str) -> str:
    """`W4-T1/1` as a filename stem, the spelling the raw archive already uses."""
    return label.replace("/", "-")


# -- the prompt -----------------------------------------------------------------------


PROMPT_HEAD = (
    "The files in your current working directory are the record of one coding-agent"
    " session. Read them, and answer ten questions about that session.\n\n"
    "Read only files inside this directory. Do not read, list, search or open anything"
    " outside it, and do not use the network: everything available to you is here.\n\n"
    "The questions:\n\n"
)

PROMPT_RULES = (
    "\n\nHow to answer:\n"
    "- Answer from the record in this directory and from nothing else.\n"
    '- Where the record does not let you answer, the answer is the string "unknown".'
    ' "unknown" is a real answer here and is wanted in preference to a guess.\n'
    "- Q1, Q2, Q4, Q5, Q6, Q7, Q8 and Q10 are whole numbers: answer with a bare"
    " integer.\n"
    '- Q3 is "yes", "no", or "no pytest run".\n'
    '- Q9 is "success after N turns" or "error after N turns", N a whole number.\n\n'
    "Finish your reply with one JSON object and nothing after it, holding exactly these"
    " ten keys:\n"
)

RESTATED = (
    "\nA previous attempt at this read a file outside this directory, which is not"
    " allowed. Every file you may open is in this directory. Do not open, read, search"
    " or list any path outside it.\n"
)


def prompt(questions: dict[str, str], restate: bool) -> str:
    """The reviewer's prompt: the same ten questions and format in both arms.

    Built from key.json rather than retyped, so the questions the reviewer answers and
    the questions the key answers cannot drift apart.
    """
    asked = "\n".join(f"{name}: {text}" for name, text in questions.items())
    shape = json.dumps(dict.fromkeys(questions, "..."), indent=None)
    body = PROMPT_HEAD + asked + PROMPT_RULES + shape + "\n"
    return body + RESTATED if restate else body


# -- the material ---------------------------------------------------------------------


def _telltale(args: list[str], home: Path | None) -> str:
    env = dict(os.environ)
    if home is not None:
        env["TELLTALE_HOME"] = str(home)
    done = subprocess.run(
        [str(TELLTALE), *args], env=env, capture_output=True, text=True, check=True
    )
    return done.stdout


def material(label: str, arm: str, entry: dict[str, Any], into: Path) -> list[str]:
    """The one arm's material, under neutral names, in a directory holding nothing else.

    Arm S is `telltale show`, `telltale timeline` and `telltale vector` of the session's
    capture in the E12 material store, read-only. Arm R is the archived raw stream.
    """
    into.mkdir(parents=True, exist_ok=True)
    if arm == "R":
        source = REPO_ROOT / str(entry["raw"])
        if not source.exists():
            raise SystemExit(
                f"{label}: {source} is missing. Ask the orchestrator for the archive;"
                " it is not rebuilt from ~/.claude here."
            )
        shutil.copyfile(source, into / "session.jsonl")
        return ["session.jsonl"]
    capture = str(entry["material_capture_id"])
    for name, argv in (
        ("summary.txt", ["show", capture]),
        ("timeline.txt", ["timeline", capture]),
        ("vector.txt", ["vector", capture]),
    ):
        (into / name).write_text(_telltale(argv, STORE), encoding="utf-8")
    return ["summary.txt", "timeline.txt", "vector.txt"]


def _sizes(into: Path, names: list[str]) -> dict[str, int]:
    return {name: (into / name).stat().st_size for name in names}


def scan(manifest: dict[str, Any]) -> dict[str, Any]:
    """Both arms' material for all six sessions, measured and thrown away.

    No reviewer runs. This is what the write-up quotes when it says how big the material
    is and how often it names a task id, so those numbers come out of a file under out/
    rather than off somebody's screen.
    """
    found: dict[str, Any] = {}
    for label, entry in manifest["sessions"].items():
        row: dict[str, Any] = {}
        for arm in ("S", "R"):
            into = Path(tempfile.mkdtemp(prefix="e12-scan-")) / "material"
            names = material(label, arm, entry, into)
            leaks = blinding.leak_counts(into, names)
            sizes = _sizes(into, names)
            row[arm] = {
                "files": names,
                "bytes": sizes,
                "bytes_total": sum(sizes.values()),
                "task_id_mentions": leaks,
                "task_id_mentions_total": sum(leaks.values()),
            }
            shutil.rmtree(into.parent)
        found[label] = row
    return found


# -- the launch -----------------------------------------------------------------------


def _reviewer_argv(task_id: str, text: str) -> list[str]:
    return [
        str(TELLTALE), "run", "--provider", "claude", "--task-id", task_id,
        "--attempt", "1", "--experiment", "E12", "--",
        "claude", "-p", "--model", MODEL, "--permission-mode", "bypassPermissions",
        "--max-turns", MAX_TURNS, "--output-format", "stream-json", "--verbose", text,
    ]  # fmt: skip


def launch(task_id: str, text: str, cwd: Path) -> tuple[str | None, dict[str, Any]]:
    """One reviewer, in the foreground, and the capture it wrote."""
    if str(os.environ.get("TELLTALE_HOME") or "") == str(STORE):
        raise SystemExit("TELLTALE_HOME names the E12 material store; refusing to run")
    before = blinding.claiming(task_id)
    started = time.perf_counter()
    timed_out = False
    try:
        done = subprocess.run(
            _reviewer_argv(task_id, text), cwd=str(cwd), capture_output=True,
            text=True, timeout=CHILD_TIMEOUT_S, check=False,
        )  # fmt: skip
        stdout, code = done.stdout, done.returncode
    except subprocess.TimeoutExpired as expired:
        stdout = (expired.stdout or b"").decode("utf-8", "replace")
        code, timed_out = None, True
    harness_wall_ms = round((time.perf_counter() - started) * 1000)
    fresh = sorted(blinding.claiming(task_id) - before)
    return _answer(stdout), {
        "task_id": task_id,
        "exit_code": code,
        "harness_timed_out": timed_out,
        "harness_wall_ms": harness_wall_ms,
        "new_captures": fresh,
    }


def _answer(raw: str) -> str | None:
    """The text of the LAST `result` message on the child's stdout, or None.

    A line that is not JSON is skipped rather than refused: the child's stdout is its
    own, and a provider that prints a banner has not failed to answer.
    """
    found: str | None = None
    for line in raw.splitlines():
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


# -- the answers ----------------------------------------------------------------------


def parse_answers(text: str | None, names: list[str]) -> dict[str, Any] | None:
    """The LAST JSON object in the reply that carries at least one of the ten keys.

    Scanned rather than regexed: the object holds no nested braces, but the reply around
    it holds prose, fenced code and, in the raw arm, quoted stream lines.
    """
    if text is None:
        return None
    found: dict[str, Any] | None = None
    for start in range(len(text)):
        if text[start] != "{":
            continue
        candidate = _object_at(text, start)
        if candidate is not None and any(name in candidate for name in names):
            found = candidate
    return found


def _object_at(text: str, start: int) -> dict[str, Any] | None:
    depth, index = 0, start
    while index < len(text):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return _loads(text[start : index + 1])
        index += 1
    return None


def _loads(chunk: str) -> dict[str, Any] | None:
    try:
        value = json.loads(chunk)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _normalise(value: Any) -> str | None:
    """One answer as the string the comparison uses. None when nothing was said."""
    if value is None or isinstance(value, (list, dict)):
        return None
    text = " ".join(str(value).split()).strip().lower()
    return text or None


def _same(given: str, wanted: Any) -> bool:
    """Exact match, with an integer key also matching its own decimal spelling."""
    if isinstance(wanted, int) and not isinstance(wanted, bool):
        try:
            return int(given.replace(",", "")) == wanted
        except ValueError:
            return False
    return given == _normalise(wanted)


def _verdict(answers: dict[str, Any] | None, name: str, wanted: Any) -> str:
    """Four verdicts, and they are four different facts.

    `agree` is an exact match. `unknown` is the reviewer declaring it cannot tell, which
    the rule counts as disagreement and tallies separately. `missing` is a key the reply
    never carried, which is not the same statement as unknown. `disagree` is the rest.
    """
    if answers is None or name not in answers:
        return "missing"
    given = _normalise(answers[name])
    if given is None or given == "unknown":
        return "unknown"
    return "agree" if _same(given, wanted) else "disagree"


def score(
    answers: dict[str, Any] | None, key: dict[str, Any], names: list[str]
) -> dict[str, Any]:
    """One reply against one session's key: a verdict per question, and the counts."""
    verdicts = {name: _verdict(answers, name, key[name]) for name in names}
    tally = {
        one: sum(1 for found in verdicts.values() if found == one) for one in VERDICTS
    }
    return {
        "verdicts": verdicts,
        "counts": tally,
        "answered": tally["agree"] + tally["disagree"],
    }


# -- what the capture cost ------------------------------------------------------------


def capture_facts(capture_id: str) -> dict[str, Any]:
    """Wall and tokens from `telltale show` of the reviewer's own capture.

    Never from the reviewer's words: a session that reports its own cost is reporting a
    number it cannot see. A counter the summary does not carry is named, not zeroed.
    """
    shown = json.loads(_telltale(["show", capture_id], None))
    usage = dict(shown.get("usage") or {})
    session = dict(shown.get("session") or {})
    present = [int(usage[name]) for name in TOKEN_KEYS if usage.get(name) is not None]
    missing = [name for name in TOKEN_KEYS if usage.get(name) is None]
    duration = session.get("duration_ms")
    return {
        "capture_id": capture_id,
        "wall_ms": None if duration is None else round(float(duration)),
        "tokens": sum(present) if present else None,
        "tokens_missing": missing,
        "usage": {name: usage.get(name) for name in TOKEN_KEYS},
        "num_turns": session.get("num_turns"),
        "runtime_version": session.get("runtime_version"),
        "models": session.get("models"),
        "environment_fingerprint_id": shown.get("environment_fingerprint_id"),
    }


# -- one run --------------------------------------------------------------------------


def run_one(
    label: str,
    arm: str,
    entry: dict[str, Any],
    options: argparse.Namespace,
    questions: dict[str, str],
    key: dict[str, Any],
) -> dict[str, Any]:
    """One reviewer session, end to end, written to out/<session>-<arm>.json."""
    index = options.order.index(label) + 1
    task_id = f"E12-{index}-{arm}"
    cwd = Path(tempfile.mkdtemp(prefix=f"e12-{_slug(label)}-{arm}-")) / "material"
    names = material(label, arm, entry, cwd)
    text = prompt(questions, options.restate)
    if any(token in text for token in blinding.TASK_TOKENS):
        raise SystemExit("the prompt names a task id; the run would be invalid")
    print(f"{label} arm {arm}: {task_id} in {cwd}", flush=True)
    reply, launched = launch(task_id, text, cwd)
    answers = parse_answers(reply, list(questions))
    record: dict[str, Any] = {
        "session": label,
        "arm": arm,
        "task_id": task_id,
        "material_dir": str(cwd),
        "material_files": names,
        "material_bytes": _sizes(cwd, names),
        "material_task_id_mentions": blinding.leak_counts(cwd, names),
        "prompt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "restated": options.restate,
        "answers": answers,
        "score": score(answers, key, list(questions)),
        "launch": launched,
    }
    record.update(_measured(launched["new_captures"], cwd, names))
    _write(OUT / f"{_slug(label)}-{arm}.json", record)
    return record


def _measured(captures: list[str], cwd: Path, names: list[str]) -> dict[str, Any]:
    """The cost and the read paths, or a named refusal when the capture is not one."""
    if len(captures) != 1:
        return {
            "capture": None,
            "reads": None,
            "warning": f"{len(captures)} captures claim this task id: {captures}",
        }
    return {
        "capture": capture_facts(captures[0]),
        "reads": blinding.reads_outside(captures[0], cwd, names),
        "warning": None,
    }


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", "utf-8")


# -- the summary ----------------------------------------------------------------------


def _label(arm_s: dict[str, int], arm_r: dict[str, int]) -> str:
    """The pre-registered rule of protocol.md, applied to one question."""
    scored = min(sum(arm_s.values()), sum(arm_r.values()))
    if scored < SESSIONS_PER_ARM:
        return f"not applicable: {scored} scored per arm, the rule is written for 6"
    if arm_s["agree"] < 4 and arm_r["agree"] < 4:
        return "neither"
    return (
        "summary sufficient"
        if arm_s["agree"] >= arm_r["agree"]
        else "summary insufficient"
    )


def _column(runs: list[dict[str, Any]], arm: str, name: str) -> dict[str, int]:
    verdicts = [one["score"]["verdicts"][name] for one in runs if one["arm"] == arm]
    return {one: verdicts.count(one) for one in VERDICTS}


def _costs(runs: list[dict[str, Any]], arm: str, field: str) -> dict[str, Any]:
    values = [
        one["capture"][field]
        for one in runs
        if one["arm"] == arm and one.get("capture")
    ]
    known = sorted(one for one in values if one is not None)
    return {
        "n": len(known),
        "unknown": len(values) - len(known),
        "values": known,
        "median": statistics.median(known) if known else None,
    }


def _run_name(one: dict[str, Any]) -> str:
    return f"{one['session']}-{one['arm']}"


def summarise(runs: list[dict[str, Any]], questions: dict[str, str]) -> dict[str, Any]:
    """The agreement table, the cost lists, and the totals, from the run records."""
    table = {}
    for name in questions:
        arm_s, arm_r = _column(runs, "S", name), _column(runs, "R", name)
        table[name] = {"S": arm_s, "R": arm_r, "label": _label(arm_s, arm_r)}
    cost = {
        arm: {
            "tokens": _costs(runs, arm, "tokens"),
            "wall_ms": _costs(runs, arm, "wall_ms"),
        }
        for arm in ("S", "R")
    }
    return {
        "n_runs": len(runs),
        "sessions": sorted({one["session"] for one in runs}),
        "bounds": BOUNDS,
        "agreement": table,
        "cost": cost,
        "derived": _derived(runs, cost),
        "totals": _totals(runs),
        "stop_rules": _stops(runs),
        "material_task_id_mentions": {
            _run_name(one): sum(one["material_task_id_mentions"].values())
            for one in runs
        },
        "reads_outside_material": {
            _run_name(one): None
            if one["reads"] is None
            else one["reads"]["outside_count"]
            for one in runs
        },
    }


def _ratio(over: float | None, under: float | None) -> float | None:
    if over is None or not under:
        return None
    return round(over / under, 3)


def _cache_read(runs: list[dict[str, Any]], arm: str) -> int | None:
    """That arm's cache-read counter, from the first run that carries a capture."""
    for one in runs:
        if one["arm"] == arm and one.get("capture"):
            value = one["capture"]["usage"]["cache_read_tokens"]
            return None if value is None else int(value)
    return None


def _minus(whole: int | float | None, part: int | None) -> int | None:
    return None if whole is None or part is None else int(whole) - part


def _derived(runs: list[dict[str, Any]], cost: dict[str, Any]) -> dict[str, Any]:
    """The comparisons the write-up quotes, computed here so nobody types them.

    Every one is arithmetic over the medians above and is only as good as the n behind
    them, which is why `sessions_per_arm` is printed beside them.
    """
    arms = ("S", "R")
    tokens = {arm: cost[arm]["tokens"]["median"] for arm in arms}
    walls = {arm: cost[arm]["wall_ms"]["median"] for arm in arms}
    cache = {arm: _cache_read(runs, arm) for arm in arms}
    both = None not in tokens.values()
    return {
        "sessions_per_arm": {arm: cost[arm]["tokens"]["n"] for arm in arms},
        "tokens_excluding_cache_read": {
            arm: _minus(tokens[arm], cache[arm]) for arm in arms
        },
        "tokens_R_over_S": _ratio(tokens["R"], tokens["S"]),
        "wall_R_over_S": _ratio(walls["R"], walls["S"]),
        "tokens_over_stop_bound": {
            arm: _minus(tokens[arm], STOP_TOKENS) for arm in arms
        },
        "cache_read_share": {arm: _ratio(cache[arm], tokens[arm]) for arm in arms},
        "projected_full_run_tokens": SESSIONS_PER_ARM
        * (int(tokens["S"]) + int(tokens["R"]))
        if both
        else None,
    }


def _totals(runs: list[dict[str, Any]]) -> dict[str, Any]:
    tokens = [
        one["capture"]["tokens"]
        for one in runs
        if one.get("capture") and one["capture"]["tokens"] is not None
    ]
    walls = [
        one["capture"]["wall_ms"]
        for one in runs
        if one.get("capture") and one["capture"]["wall_ms"] is not None
    ]
    return {
        "sessions": len(runs),
        "tokens": sum(tokens),
        "tokens_from": len(tokens),
        "wall_ms": sum(walls),
        "wall_from": len(walls),
    }


def _stops(runs: list[dict[str, Any]]) -> list[str]:
    """Every STOP condition of protocol.md that the runs as recorded satisfy."""
    hit = []
    for one in runs:
        who = f"{one['session']} arm {one['arm']}"
        capture = one.get("capture") or {}
        if (capture.get("wall_ms") or 0) > STOP_WALL_MS:
            hit.append(f"{who}: {capture['wall_ms']} ms over {STOP_WALL_MS}")
        if (capture.get("tokens") or 0) > STOP_TOKENS:
            hit.append(f"{who}: {capture['tokens']} tokens over {STOP_TOKENS}")
        if one["score"]["answered"] < STOP_MIN_ANSWERS:
            hit.append(f"{who}: answered {one['score']['answered']} of 10")
        reads = one["reads"] or {}
        if reads.get("outside_count"):
            hit.append(f"{who}: {reads['outside_count']} reads outside material")
    return hit


# -- the command line -----------------------------------------------------------------


def _selected(options: argparse.Namespace, manifest: dict[str, Any]) -> list[str]:
    if options.only:
        if options.only not in manifest["sessions"]:
            raise SystemExit(f"{options.only} is not in manifest.json")
        return [options.only]
    return ["W4-T1/1"] if options.pilot else list(manifest["sessions"])


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="E12: the blinded diagnosis protocol")
    parser.add_argument("--pilot", action="store_true", help="W4-T1/1 in both arms")
    parser.add_argument("--arm", choices=("S", "R"), help="one arm only")
    parser.add_argument("--only", help="one session label from manifest.json")
    parser.add_argument(
        "--restate",
        action="store_true",
        help="restate the read restriction after a violation",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="rebuild out/summary.json from the run files",
    )
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="recompute the cost and read checks of every run file from the store",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip a session and arm whose out file is already written",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="measure both arms' material for all six sessions, running no reviewer",
    )
    return parser.parse_args(argv)


def _existing(order: list[str]) -> list[dict[str, Any]]:
    found = []
    for label in order:
        for arm in ("S", "R"):
            path = OUT / f"{_slug(label)}-{arm}.json"
            if path.exists():
                found.append(json.loads(path.read_text(encoding="utf-8")))
    return found


def _recheck(order: list[str]) -> None:
    """Recompute cost and reads for every run already written, from the store alone.

    Both are reads of the reviewer's own capture, so they can be re-derived at any time.
    The answers and the score are not touched and no reviewer is run again.
    """
    for run in _existing(order):
        capture = (run.get("capture") or {}).get("capture_id")
        if capture is None:
            continue
        into = Path(run["material_dir"])
        run["capture"] = capture_facts(capture)
        run["reads"] = blinding.reads_outside(
            capture, into, list(run["material_files"])
        )
        if into.exists():
            run["material_bytes"] = _sizes(into, list(run["material_files"]))
        _write(OUT / f"{_slug(run['session'])}-{run['arm']}.json", run)


def _report(runs: list[dict[str, Any]], questions: dict[str, str]) -> None:
    summary = summarise(runs, questions)
    _write(OUT / "summary.json", summary)
    _write(
        OUT / "answers.json",
        [
            {
                "session": one["session"],
                "arm": one["arm"],
                "capture_id": (one.get("capture") or {}).get("capture_id"),
                "answers": one["answers"],
                "score": one["score"],
            }
            for one in runs
        ],
    )
    for name, row in summary["agreement"].items():
        agreed = f"S {row['S']['agree']}/6  R {row['R']['agree']}/6"
        print(f"{name:<28} {agreed}  {row['label']}")
    for line in summary["stop_rules"]:
        print(f"STOP: {line}")


def _agree_on_captures(manifest: dict[str, Any], key: dict[str, Any]) -> None:
    """protocol.md's STOP: the key and the material must name the same two captures.

    A key written from one stream and material built from another would score a
    reviewer against a session it never saw, and the disagreement is not this task's to
    fix: it is reported and the manifest is the orchestrator's.
    """
    wrong = []
    for label, entry in manifest["sessions"].items():
        keyed = key["sessions"].get(label) or {}
        for field in ("capture_id", "material_capture_id"):
            if entry.get(field) != keyed.get(field):
                pair = f"{entry.get(field)} / {keyed.get(field)}"
                wrong.append(f"{label} {field}: {pair}")
    if wrong:
        raise SystemExit(
            "STOP: key.json and manifest.json disagree on a capture id, so the"
            " orchestrator fixes the manifest before any reviewer runs: "
            + "; ".join(wrong)
        )


def main(argv: list[str] | None = None) -> int:
    options = _parse(argv)
    manifest, key = _manifest(), _key()
    _agree_on_captures(manifest, key)
    questions = dict(key["questions"])
    options.order = list(manifest["sessions"])
    if options.scan:
        _write(OUT / "material_scan.json", scan(manifest))
        print(f"wrote {OUT / 'material_scan.json'}")
        return 0
    if options.recheck:
        _recheck(options.order)
    if options.summary_only or options.recheck:
        _report(_existing(options.order), questions)
        return 0
    arms = (options.arm,) if options.arm else ("S", "R")
    for label in _selected(options, manifest):
        for arm in arms:
            if options.skip_existing and (OUT / f"{_slug(label)}-{arm}.json").exists():
                print(f"{label} arm {arm}: already written, skipped", flush=True)
                continue
            one = run_one(
                label,
                arm,
                manifest["sessions"][label],
                options,
                questions,
                key["sessions"][label],
            )
            print(f"  {one['score']['counts']}", flush=True)
    _report(_existing(options.order), questions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
