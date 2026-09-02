"""E12 answer key: ten facts per session, derived from the raw stream-json alone.

The key is the ground truth both reviewer arms are scored against, so it must not come
from Telltale (the summary arm would then be scored against itself). It comes from the
provider's own stream, which is what a person without Telltale would read, by rules
written here before any reviewer session runs. `--check` compares the key with what
`telltale show` says for the same capture: a disagreement is a bug in one of the two and
is resolved BEFORE the experiment, never scored.

Usage:
  uv run python experiments/E12/make_key.py experiments/E12/manifest.json
  uv run python experiments/E12/make_key.py experiments/E12/manifest.json --check
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SUBAGENT_TOOLS = {"Agent", "Task"}
FAILED = re.compile(r"\b[1-9]\d* failed\b|\bFAILED\b|\bExit code [1-9]")

QUESTIONS = {
    "Q1_pytest_runs": (
        "How many Bash tool calls executed pytest? A heredoc body that mentions pytest"
        " does not count; a pytest command after a script in the same call does."
    ),
    "Q2_failed_pytest_runs": (
        "How many of those pytest runs failed (a non-zero exit, or a failing test"
        " reported in the result text)?"
    ),
    "Q3_edit_after_last_pytest": (
        "Did the session edit or write a file after its last pytest run?"
        " (yes/no; 'no pytest run' if none)"
    ),
    "Q4_files_edited": (
        "How many distinct files did the session edit or write"
        " (Edit, Write, MultiEdit, NotebookEdit)?"
    ),
    "Q5_tool_errors": "How many tool results were errors (is_error true)?",
    "Q6_compactions": "How many context compactions happened?",
    "Q7_subagents": "How many subagent (Agent) tool calls were made?",
    "Q8_commits": "How many Bash tool calls ran 'git commit'?",
    "Q9_result": "What was the final result (success or error), after how many turns?",
    "Q10_output_tokens": "How many output tokens did the final result report?",
}


@dataclass
class Scan:
    """Everything one pass over the stream keeps: tool calls in order, with results."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: int = 0
    compactions: int = 0
    result: dict[str, Any] | None = None

    def add_uses(self, record: dict[str, Any]) -> None:
        for block in _blocks(record, "tool_use"):
            call = {"id": block.get("id"), "name": block.get("name"),
                    "input": block.get("input") or {}, "result": None}  # fmt: skip
            self.calls.append(call)
            self.by_id[str(call["id"])] = call

    def add_results(self, record: dict[str, Any]) -> None:
        for block in _blocks(record, "tool_result"):
            self.errors += bool(block.get("is_error"))
            call = self.by_id.get(str(block.get("tool_use_id")))
            if call is not None:
                call["result"] = block


def _records(path: Path) -> Iterator[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            yield record


def _blocks(record: dict[str, Any], kind: str) -> Iterator[dict[str, Any]]:
    for block in record.get("message", {}).get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == kind:
            yield block


def _scan(path: Path) -> Scan:
    scan = Scan()
    for record in _records(path):
        kind = record.get("type")
        if kind == "assistant":
            scan.add_uses(record)
        elif kind == "user":
            scan.add_results(record)
        elif kind == "system" and record.get("subtype") == "compact_boundary":
            scan.compactions += 1
        elif kind == "result":
            scan.result = record
    return scan


def _command(call: dict[str, Any]) -> str:
    return str(call["input"].get("command", "")) if call["name"] == "Bash" else ""


# A pytest EXECUTION: a command segment (split on newlines, `&&`, `||`, `;` and `|`)
# that starts with pytest, or with the three ways this repository invokes it. The word
# alone is not enough: a heredoc that writes a test file, a grep for the word and a
# python script that mentions it all contain "pytest" and run nothing. Measured on
# W4-T2/1 before this rule: 14 commands contained the word and 4 ran pytest, which is
# also what Telltale's verification_run rows say.
_PYTEST = re.compile(r"^\s*(uv run |python3? -m |\.venv/bin/)?pytest\b")
_SEGMENT = re.compile(r"\n|&&|\|\||;|\|")
# A heredoc body is data: `cat > report.md <<'EOF' ... EOF` and `python3 - <<'PY' ...
# PY` carry the words of a report or a script, and a line inside them that reads
# `uv run pytest` ran nothing. Measured on W4-T2/1 with the bodies kept: 12 matches for
# 4 executions; the other 8 were inside heredocs.
_HEREDOC = re.compile(
    r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n\1\s*$", re.DOTALL | re.MULTILINE
)


def _without_heredocs(command: str) -> str:
    return _HEREDOC.sub("", command)


def _runs_pytest(call: dict[str, Any]) -> bool:
    segments = _SEGMENT.split(_without_heredocs(_command(call)))
    return any(_PYTEST.match(segment) for segment in segments)


def _failed(result: dict[str, Any] | None) -> bool:
    if result is None:
        return False
    if result.get("is_error"):
        return True
    content = result.get("content")
    text = content if isinstance(content, str) else json.dumps(content)
    return bool(FAILED.search(text))


def _edit_after_last_pytest(calls: list[dict[str, Any]]) -> str:
    pytest_at = [i for i, c in enumerate(calls) if _runs_pytest(c)]
    if not pytest_at:
        return "no pytest run"
    last = pytest_at[-1]
    later = any(c["name"] in EDIT_TOOLS for c in calls[last + 1 :])
    return "yes" if later else "no"


def _files(calls: list[dict[str, Any]]) -> set[str]:
    named = {
        str(c["input"].get("file_path") or c["input"].get("notebook_path") or "")
        for c in calls
        if c["name"] in EDIT_TOOLS
    }
    named.discard("")
    return named


def facts(path: Path) -> dict[str, Any]:
    """The ten answers for one raw stream-json file."""
    scan = _scan(path)
    pytest_calls = [c for c in scan.calls if _runs_pytest(c)]
    result = scan.result or {}
    usage = result.get("usage") or {}
    return {
        "Q1_pytest_runs": len(pytest_calls),
        "Q2_failed_pytest_runs": sum(_failed(c["result"]) for c in pytest_calls),
        "Q3_edit_after_last_pytest": _edit_after_last_pytest(scan.calls),
        "Q4_files_edited": len(_files(scan.calls)),
        "Q5_tool_errors": scan.errors,
        "Q6_compactions": scan.compactions,
        "Q7_subagents": sum(c["name"] in SUBAGENT_TOOLS for c in scan.calls),
        "Q8_commits": sum("git commit" in _command(c) for c in scan.calls),
        "Q9_result": (
            None
            if scan.result is None
            else f"{result.get('subtype')} after {result.get('num_turns')} turns"
        ),
        "Q10_output_tokens": usage.get("output_tokens"),
        "_tool_calls": len(scan.calls),
        "_bytes": path.stat().st_size,
    }


# What `telltale show` calls the same facts, for --check. A path is a list of keys into
# the summary JSON; a question with no path is one Telltale does not carry as a number.
SHOW_PATHS: dict[str, list[str]] = {
    "Q1_pytest_runs": ["verification", "agent_test_runs"],
    "Q2_failed_pytest_runs": ["verification", "failed_test_runs"],
    "Q4_files_edited": ["work", "unique_files_changed"],
    "Q6_compactions": ["context", "compactions"],
    "Q7_subagents": ["delegation", "subagent_count"],
    "Q10_output_tokens": ["usage", "output_tokens"],
}


def _dig(summary: Any, path: list[str]) -> Any:
    node = summary
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return "<absent>"
        node = node[key]
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


def _shown(capture_id: str) -> Any:
    printed = subprocess.run(
        ["uv", "run", "telltale", "show", capture_id],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(printed[printed.index("{") :])


def _check(sessions: dict[str, dict[str, Any]]) -> None:
    rows = []
    for label, entry in sessions.items():
        summary = _shown(entry["capture_id"])
        for question, path in SHOW_PATHS.items():
            got = _dig(summary, path)
            rows.append((label, question, entry[question], got, entry[question] == got))
    width = max(len(r[0]) for r in rows)
    for label, question, ours, theirs, same in rows:
        verdict = "agree" if same else "DIFFER"
        print(f"{label:<{width}}  {question:<26} raw {ours!s:<22}"
              f" telltale {theirs!s:<12} {verdict}")  # fmt: skip
    print(f"{sum(r[4] for r in rows)} of {len(rows)} agree")


def main(argv: list[str]) -> int:
    manifest = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    sessions = {
        label: {"capture_id": entry["capture_id"], **facts(Path(entry["raw"]))}
        for label, entry in manifest["sessions"].items()
    }
    out = Path(argv[1]).with_name("key.json")
    out.write_text(
        json.dumps({"questions": QUESTIONS, "sessions": sessions}, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out}: {len(sessions)} sessions")
    if "--check" in argv:
        _check(sessions)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
