"""Turn one scenario's raw capture into a committable fixture, and say what it removed.

This is NOT the Telltale sanitizer. It is the smaller thing a fixture needs: enough
rewriting that the raw capture of a real account on a real machine can live in a public
repository, and nothing more, because a fixture that has already been cleaned cannot
demonstrate that the real sanitizer cleans anything.

What it rewrites, and why each one is here rather than in a general rule:
  - the scenario's repo copy path -> `<repo>`. It contains the machine's home directory
    twice over and a worktree name that means nothing outside this run.
  - the home directory -> `<home>`, after the repo path, so the longer match wins.
  - `user.account_id` and `user.email` -> fixed fakes. Codex puts BOTH on every single
    OTel log record; they are the reason these files cannot be committed raw.
  - `host.name` -> `<host>`.
  - the owner's own instruction files. `world_state.state.agents_md.text` in a rollout
    is whatever AGENTS.md was in scope, which here is the owner's personal one.
    `session_meta.base_instructions.text` is the Codex system prompt: not private, but
    50 KB of provider boilerplate per scenario that says nothing about the surface.
    Both are replaced by a one-line placeholder that records the length that was there.

What it strips: every reasoning item, on every surface. Codex emits them in the exec
stream and persists them in rollouts; Telltale never stores them, so neither does a
fixture. The count of what was dropped goes in the MANIFEST, because "0 reasoning items"
and "reasoning items were removed" are different statements.

What it deliberately KEEPS:
  - the TELLTALEFAKE probes. A fixture without them cannot show they were removed.
  - `command_execution.aggregated_output`. It is command output, so it is exactly the
    kind of thing the real sanitizer must drop; the fixture shows what the SURFACE
    carries, and the MANIFEST says so.

Usage: uv run python experiments/E02/sanitize_fixture.py --scenario S2
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_ROOT = HERE / "out"
FIXTURES = HERE.parent.parent / "fixtures" / "sources" / "codex"

SURFACE_FILES = (
    "exec.jsonl",
    "otel_logs.jsonl",
    "otel_metrics.jsonl",
    "otel_traces.jsonl",
    "other.jsonl",
    "hooks.jsonl",
    "hooks_local.jsonl",
    "rollout.jsonl",
)

# With a base endpoint Codex posts every OTLP signal to `/`, so the receiver files all
# three under `other.jsonl`. The fixture is organised by SIGNAL instead, because that is
# how a parser will meet them, and the signal is unambiguous from the payload's one
# top-level key.
SIGNAL_ROUTES = {
    "resourceLogs": "otel_logs.jsonl",
    "resourceMetrics": "otel_metrics.jsonl",
    "resourceSpans": "otel_traces.jsonl",
}

# How many spans, and how many metric series, survive per OTLP request. The envelope,
# the resource attributes and the scope are what a parser has to read; the thousandth
# span teaches it nothing the first twenty did not. The numbers are the reason: S6 sent
# 5707 spans in 72 seconds, and ONE such request is 536 KB. `otel.trace_exporter`
# on Codex 0.150.1 is the CLI's internal Rust tracing, function by function, not a
# session-shaped trace. Every MANIFEST records what was captured against what was kept.
SPANS_PER_REQUEST = 20
METRICS_PER_REQUEST = 20

FAKE_ACCOUNT_ID = "00000000-0000-4000-8000-000000000000"
FAKE_EMAIL = "capture@example.invalid"
FAKE_HOST = "<host>"

# A reasoning item is spelled differently on each surface, so the test is on the TYPE
# field and not on the text. A regex over the whole line would also delete a model
# message that happens to say the word, and an experiment about what a surface carries
# must not edit the messages.
#
# The names come from counting, not from guessing: over the six largest rollouts on this
# machine, `payload.type` is `reasoning` 5282 times under `response_item` and
# `agent_reasoning` 4139 times under `event_msg`, and no other type contains the string.
# `agent_reasoning` was missing from the first version of this rule, which would have
# put 4139 rows of reasoning text into a fixture that claimed to have none.
REASONING_MARK = "reasoning"


def load_meta(scenario: str) -> dict[str, object]:
    loaded = json.loads((OUT_ROOT / scenario / "meta.json").read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"{scenario}/meta.json is not an object")
    return loaded


def text_rules(meta: dict[str, object]) -> list[tuple[str, str]]:
    """Longest first: the repo path contains the home directory, which contains the
    login name.

    The bare login name is a rule of its own because command OUTPUT is not a path.
    Measured: S6 ran `ls -la`, and `drwxr-xr-x@ 13 <name>  staff` reached four surfaces
    (exec, both hook files, and the OTel `codex.tool_result` output attribute) with the
    home path nowhere near it. Scrubbing paths alone leaves the owner's identity in the
    fixture, which is the same defect the OTel finding is about.
    """
    repo = str(meta.get("child_cwd", ""))
    home = str(Path.home())
    rules = [(repo, "<repo>")] if repo else []
    rules.append((home, "<home>"))
    rules.append((Path.home().name, "<user>"))
    return rules


def rewrite_text(value: str, rules: list[tuple[str, str]], counts: Counter[str]) -> str:
    for needle, replacement in rules:
        if needle and needle in value:
            counts[replacement] += value.count(needle)
            value = value.replace(needle, replacement)
    if "@" in value and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        counts["<email>"] += 1
        return FAKE_EMAIL
    return value


def rewrite_key(key: str, rules: list[tuple[str, str]], counts: Counter[str]) -> str:
    """A KEY can be a file path.

    `item.changes` in the exec stream and the rollout is keyed BY the absolute path of
    each changed file, so a sanitizer that rewrites only values leaves the home
    directory in the fixture. Measured: with values-only scrubbing, S1 and S6 still
    carried `/Users/<name>/...` after a clean run. Only the path rules apply here; the
    email rule is about a value's shape and a key is not a value.
    """
    for needle, replacement in rules:
        if needle and needle in key:
            counts[f"{replacement} (key)"] += key.count(needle)
            key = key.replace(needle, replacement)
    return key


def scrub(node: object, rules: list[tuple[str, str]], counts: Counter[str]) -> object:
    """Walk the parsed JSON. Keys go by NAME and by path, values by content."""
    if isinstance(node, dict):
        out: dict[str, object] = {}
        for key, value in node.items():
            rewritten = rewrite_key(key, rules, counts)
            if rewritten in out:
                # Two distinct keys collapsing into one would silently lose a file.
                raise ValueError(f"key collision on rewrite: {key!r}")
            out[rewritten] = scrub_value(key, value, rules, counts)
        return out
    if isinstance(node, list):
        return [scrub(v, rules, counts) for v in node]
    if isinstance(node, str):
        return rewrite_text(node, rules, counts)
    return node


def scrub_value(
    key: str, value: object, rules: list[tuple[str, str]], counts: Counter[str]
) -> object:
    if key in {"user.account_id", "account_id"} and isinstance(value, str):
        counts["<account_id>"] += 1
        return FAKE_ACCOUNT_ID
    if key in {"user.email", "email"} and isinstance(value, str):
        counts["<email>"] += 1
        return FAKE_EMAIL
    if key in {"host.name", "hostname"} and isinstance(value, str):
        counts["<host>"] += 1
        return FAKE_HOST
    return scrub(value, rules, counts)


def otel_attribute_scrub(node: object, counts: Counter[str]) -> object:
    """OTLP puts names in a `key` field and values in a sibling `value` object.

    A generic walk never sees `user.email` as a key, so this pass runs over the same
    tree looking for the OTLP shape. Without it the owner's address is in the fixture.
    """
    if isinstance(node, list):
        return [otel_attribute_scrub(v, counts) for v in node]
    if not isinstance(node, dict):
        return node
    key = node.get("key")
    value = node.get("value")
    if isinstance(key, str) and isinstance(value, dict) and "stringValue" in value:
        replacement = {
            "user.account_id": FAKE_ACCOUNT_ID,
            "user.email": FAKE_EMAIL,
            "host.name": FAKE_HOST,
        }.get(key)
        if replacement is not None:
            counts[f"<{key}>"] += 1
            return {**node, "value": {"stringValue": replacement}}
    return {k: otel_attribute_scrub(v, counts) for k, v in node.items()}


def placeholder(text: str, label: str) -> str:
    return f"<{label} removed by sanitize_fixture.py: {len(text)} characters>"


# Bulk provider and machine boilerplate. Each entry is replaced by a placeholder that
# keeps the SHAPE (same key, a string value) and the character count, because a parser
# tested against this fixture needs the structure and not the megabyte. Measured on S2:
# these five are 111 KB of a 123 KB rollout, and none of them is a fact about the
# session. `state.permissions` alone is 58 KB of sandbox path enumeration that
# `turn_context.sandbox_policy` already states in one line.
BULK_TEXT_KEYS = ("base_instructions", "agents_md")
BULK_STATE_KEYS = ("host_skills", "permissions")
BOILERPLATE_ROLES = {"developer", "system"}


def _replace_text_field(holder: object, label: str, counts: Counter[str]) -> None:
    if isinstance(holder, dict) and isinstance(holder.get("text"), str):
        counts[label] += 1
        holder["text"] = placeholder(holder["text"], label)


def _strip_message_boilerplate(
    payload: dict[str, object], counts: Counter[str]
) -> None:
    """Developer and system messages are the CLI's own prompt, not the session."""
    role = payload.get("role")
    if payload.get("type") != "message" or role not in BOILERPLATE_ROLES:
        return
    content = payload.get("content")
    if not isinstance(content, list):
        return
    for element in content:
        _replace_text_field(element, f"{role} message", counts)


def strip_bulk_text(row: dict[str, object], counts: Counter[str]) -> dict[str, object]:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return row
    _replace_text_field(payload.get("base_instructions"), "base_instructions", counts)
    _strip_message_boilerplate(payload, counts)
    state = payload.get("state")
    if not isinstance(state, dict):
        return row
    _replace_text_field(state.get("agents_md"), "agents_md", counts)
    for key in BULK_STATE_KEYS:
        if key in state:
            counts[key] += 1
            state[key] = placeholder(json.dumps(state[key]), key)
    return row


def type_is_reasoning(node: object) -> bool:
    kind = node.get("type") if isinstance(node, dict) else None
    return isinstance(kind, str) and REASONING_MARK in kind.lower()


def is_reasoning(row: dict[str, object]) -> bool:
    """One row, three surfaces, one question: is this row a reasoning item."""
    # exec stream: item.started, item.updated and item.completed all carry `item.type`.
    if type_is_reasoning(row.get("item")):
        return True
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return False
    # rollout: response_item/reasoning and event_msg/agent_reasoning, plus the item
    # envelope event_msg/item_completed wraps.
    return type_is_reasoning(payload) or type_is_reasoning(payload.get("item"))


def route(name: str, row: object) -> str:
    """Which fixture file this row belongs in."""
    if name != "other.jsonl":
        return name
    body = row.get("body_json") if isinstance(row, dict) else None
    if isinstance(body, dict):
        for key, target in SIGNAL_ROUTES.items():
            if key in body:
                return target
    return "other.jsonl"


def trim_list(
    holder: dict[str, object], key: str, cap: int, counts: Counter[str]
) -> None:
    values = holder.get(key)
    if isinstance(values, list) and len(values) > cap:
        counts[f"{key} trimmed"] += len(values) - cap
        holder[key] = values[:cap]
        holder[f"_e02_{key}_total"] = len(values)


# resource key, scope key, leaf key, cap. One row per OTLP signal that is worth
# trimming; logs are not here because a log record IS the fact.
TRIM_SHAPES = (
    ("resourceSpans", "scopeSpans", "spans", SPANS_PER_REQUEST),
    ("resourceMetrics", "scopeMetrics", "metrics", METRICS_PER_REQUEST),
)


def members(holder: dict[str, object], key: str) -> list[dict[str, object]]:
    """The dicts under `key`, and nothing else. A malformed envelope yields none."""
    values = holder.get(key)
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def trim_shape(
    body: dict[str, object], shape: tuple[str, str, str, int], counts: Counter[str]
) -> None:
    resource_key, inner_key, leaf, cap = shape
    for resource in members(body, resource_key):
        for scope in members(resource, inner_key):
            trim_list(scope, leaf, cap, counts)


def trim_otlp(row: dict[str, object], counts: Counter[str]) -> dict[str, object]:
    """Bound one OTLP request without changing its shape."""
    body = row.get("body_json")
    if isinstance(body, dict):
        for shape in TRIM_SHAPES:
            trim_shape(body, shape, counts)
    return row


def sanitize_rows(
    rows: list[object], dst: Path, rules: list[tuple[str, str]]
) -> dict[str, object]:
    counts: Counter[str] = Counter()
    kept: list[str] = []
    dropped = 0
    kinds: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if is_reasoning(row):
            dropped += 1
            continue
        row = trim_otlp(strip_bulk_text(row, counts), counts)
        scrubbed = otel_attribute_scrub(scrub(row, rules, counts), counts)
        assert isinstance(scrubbed, dict)
        kinds[kind_of(scrubbed)] += 1
        kept.append(json.dumps(scrubbed, ensure_ascii=False))
    dst.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return {
        "rows_in": len(kept) + dropped,
        "rows_out": len(kept),
        "reasoning_rows_dropped": dropped,
        "kinds": dict(kinds),
        "replacements": dict(counts),
        "bytes_out": dst.stat().st_size,
    }


def read_jsonl(path: Path) -> list[object]:
    rows: list[object] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def kind_of(row: dict[str, object]) -> str:
    if "type" in row and "payload" in row:  # rollout
        payload = row.get("payload")
        sub = payload.get("type") if isinstance(payload, dict) else None
        return f"{row['type']}/{sub}" if isinstance(sub, str) else str(row["type"])
    if "type" in row:  # exec stream
        item = row.get("item")
        if isinstance(item, dict) and isinstance(item.get("type"), str):
            return f"{row['type']}:{item['type']}"
        return str(row["type"])
    if "path" in row and "ingest_ts" in row:  # a sink line
        return f"request {row['path']}"
    if "payload" in row:  # hook_post local log
        payload = row.get("payload")
        name = payload.get("hook_event_name") if isinstance(payload, dict) else None
        return f"hook {name}"
    return "_unknown"


MANIFEST_HEAD = """# {scenario} fixture manifest

Codex {version}. Captured by `experiments/E02/run.py --scenario {scenario}`, rewritten
by `experiments/E02/sanitize_fixture.py`. Raw capture: `experiments/E02/out/{scenario}/`
(gitignored). Command line and every timing number: `out/{scenario}/meta.json`.

Prompt: {prompt}

Exit code {exit_code}, wall time {wall}s.
"""

MANIFEST_TAIL = """
## What was replaced

Counts are of individual substitutions, not of rows.

{replacements}

`<repo>` is the scenario's throwaway repository copy, `<home>` the machine's home
directory. `user.account_id` and `user.email` are on EVERY OTel log record and are
replaced with `{fake_account}` and `{fake_email}`. `host.name` becomes `{fake_host}`.
`base_instructions` (the Codex system prompt) and `agents_md` (whatever AGENTS.md was in
scope, which on the capture machine is the owner's own) are replaced by a placeholder
that keeps the character count.

## What was stripped

{reasoning} model-thinking rows, across every surface. Telltale never persists them, so
the fixture does not carry them either.

The assertion is a test for the ITEM:
`grep -rlE '"(agent_)?[Rr]easoning"' fixtures/sources/codex | wc -l` = 0.
A bare `grep -c reasoning` over this tree is NOT 0 and cannot be:
`reasoning_effort`, `reasoning_output_tokens`, `reasoning_token_count`,
`reasoning_summary` and the metric name `codex.turn.token_usage.reasoning_output_tokens`
are field names the matrix depends on, and deleting them would delete two of its rows.

## What was deliberately kept

- The TELLTALEFAKE probes from `secrets_note.txt`, wherever a surface carried them. A
  fixture without the probe cannot show the probe was removed.
- **`command_execution.aggregated_output` is command output and it is still here.** The
  fixture's job is to show what the surface carries. THE SANITIZER WRITTEN LATER MUST
  DROP IT: it is the stdout of an arbitrary command run in the owner's workspace, and
  nothing about the exec stream bounds what ends up in it.
"""


def write_manifest(
    dst_dir: Path, scenario: str, meta: dict[str, object], per_file: dict[str, object]
) -> None:
    timing = meta.get("timing", {})
    assert isinstance(timing, dict)
    replacements: Counter[str] = Counter()
    reasoning = 0
    rows = [
        "| file | rows captured | rows kept | reasoning dropped | bytes |",
        "|---|---|---|---|---|",
    ]
    kinds_block: list[str] = []
    for name, stats in sorted(per_file.items()):
        assert isinstance(stats, dict)
        rows.append(
            f"| `{name}` | {stats['rows_in']} | {stats['rows_out']} |"
            f" {stats['reasoning_rows_dropped']} | {stats['bytes_out']} |"
        )
        dropped = stats["reasoning_rows_dropped"]
        reasoning += dropped if isinstance(dropped, int) else 0
        replaced = stats["replacements"]
        if isinstance(replaced, dict):
            replacements.update(replaced)
        kinds = stats["kinds"]
        assert isinstance(kinds, dict)
        if kinds:
            listed = ", ".join(f"`{k}` {v}" for k, v in sorted(kinds.items()))
            kinds_block.append(f"- `{name}`: {listed}")
    body = MANIFEST_HEAD.format(
        scenario=scenario,
        version=meta.get("codex_version"),
        prompt=meta.get("prompt"),
        exit_code=timing.get("exit_code"),
        wall=timing.get("wall_s"),
    )
    body += "\n## Requests and rows per surface\n\n" + "\n".join(rows) + "\n"
    body += "\n## Record kinds\n\n" + "\n".join(kinds_block) + "\n"
    replacement_lines = "\n".join(
        f"- `{k}`: {v}" for k, v in sorted(replacements.items())
    )
    body += MANIFEST_TAIL.format(
        replacements=replacement_lines or "- nothing matched",
        reasoning=reasoning,
        fake_account=FAKE_ACCOUNT_ID,
        fake_email=FAKE_EMAIL,
        fake_host=FAKE_HOST,
    )
    (dst_dir / "MANIFEST.md").write_text(body, encoding="utf-8")


def sanitize(scenario: str) -> dict[str, object]:
    meta = load_meta(scenario)
    version = str(meta.get("codex_version", "unknown"))
    src_dir = OUT_ROOT / scenario
    dst_dir = FIXTURES / version / scenario
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True)
    rules = text_rules(meta)

    routed: dict[str, list[object]] = {}
    for name in SURFACE_FILES:
        src = src_dir / name
        if not src.exists():
            continue
        for row in read_jsonl(src):
            routed.setdefault(route(name, row), []).append(row)

    per_file: dict[str, object] = {}
    for name, rows in sorted(routed.items()):
        per_file[name] = sanitize_rows(rows, dst_dir / name, rules)
    write_manifest(dst_dir, scenario, meta, per_file)
    return {"scenario": scenario, "fixture_dir": str(dst_dir), "files": per_file}


def scenario_dirs(include_labelled: bool = False) -> list[str]:
    """Scenario runs in out/, newest run per name.

    A LABELLED run (`run.py --label`) is a rehearsal against a Codex that could not
    reach the model. It is a real measurement of everything before the model call and
    it is not one of the seven scenarios, so it stays out of the fixture tree and out
    of the matrix unless asked for by name.
    """
    names: list[str] = []
    for path in sorted(OUT_ROOT.iterdir()):
        meta = path / "meta.json"
        if not meta.exists():
            continue
        label = json.loads(meta.read_text(encoding="utf-8")).get("label") or ""
        if label and not include_labelled:
            continue
        names.append(path.name)
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--scenario")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    names = scenario_dirs() if args.all else [args.scenario]
    if not names or names == [None]:
        parser.error("pass --scenario NAME or --all")
    for name in names:
        result = sanitize(name)
        print(f"{name} -> {result['fixture_dir']}")
        files = result["files"]
        assert isinstance(files, dict)
        for fname, stats in sorted(files.items()):
            assert isinstance(stats, dict)
            print(
                f"    {fname}: {stats['rows_out']} rows,"
                f" {stats['reasoning_rows_dropped']} reasoning dropped,"
                f" {stats['bytes_out']} bytes"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
