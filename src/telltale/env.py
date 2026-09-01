"""The environment a capture ran in, as one content-safe fingerprint.

Spec 9.2 asks for this so that a change over time is not casually blamed on the
repository: a model swap, a new CLAUDE.md or a different permission mode all change what
an agent does, and none of them show up in the diff. The payload is the fields of design
6.3's telltale.environment, plus the id derived from them, and the launcher wraps it
into an Observation.

Nothing here stores content. An instruction file becomes a sha256 and a byte count; a
settings mapping and a tool list become one sha256 each. The id is "env_" plus the
sha256 of the canonical JSON of the rest of the payload, so two captures share an id
when every field above is equal, and a changed field is visible as a changed id.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale.repo import git_root

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

# Instruction surfaces under the working directory and the repository root (design 6.8).
_REPO_FILES = ("CLAUDE.md", "AGENTS.md")
_RULES_DIR = (".claude", "rules")

# Instruction surfaces under the operator's home. The key keeps the placeholder rather
# than the real path: the fingerprint travels with a report, and a home directory
# carries the operator's account name.
_HOME_KEY = "<home>"
_HOME_FILES = (".claude/CLAUDE.md", ".codex/AGENTS.md")

# What a caller may put in `extra`. A key outside this set is refused rather than
# ignored: a misspelled "capture_mode" would otherwise fingerprint as "no capture modes
# given", and two different environments would share an id.
_EXTRA_KEYS = frozenset(
    {
        "tool_set",
        "mcp_names",
        "settings",
        "sandbox_posture",
        "capture_modes",
        "content_level",
    }
)

# Flags that name a value. A repeated flag is read as its last occurrence, which is what
# argparse and clap both do with a single-valued option.
_MODEL_FLAGS = ("--model", "-m")
_EFFORT_FLAGS = ("--effort",)
_POSTURE_FLAGS = ("--permission-mode", "--sandbox")
_CONFIG_FLAGS = ("-c", "--config")

# Flags that set a posture without naming a value. The flag itself is recorded: turning
# two providers' vocabularies into one word would be inventing a vocabulary, and this
# field only has to be equal for equal environments.
_POSTURE_SWITCHES = (
    "--dangerously-skip-permissions",
    "--dangerously-bypass-approvals-and-sandbox",
)

_DEMO_ARGV = (
    "claude",
    "-p",
    "--model",
    "sonnet",
    "-c",
    "model_reasoning_effort=high",
    "--permission-mode",
    "acceptEdits",
)
_DEMO_EXTRA: dict[str, Any] = {
    "tool_set": ["Bash", "Edit", "Read", "Write"],
    "mcp_names": ["telltale"],
    "settings": {
        "permission_mode": "acceptEdits",
        "hook_events": ["PostToolUse", "PreToolUse"],
        "allowed_tools": ["Bash(git status:*)"],
    },
    "capture_modes": ["hook", "otel_logs", "otel_metrics", "stream"],
    "content_level": 1,
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    """The one JSON spelling a hash may be taken of: sorted keys, no whitespace.

    ensure_ascii stays on, so the bytes are pure ASCII whatever a path contains, and
    allow_nan stays off, because NaN is not JSON and a value that reached here as one
    would hash differently in another reader.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _hash_names(names: Iterable[str] | None) -> str | None:
    """sha256 of a name list, sorted. None when the caller did not supply one.

    Sorted because the order a provider happens to serialize its tools in is not a
    property of the environment. Not deduplicated: a list holding a name twice is a
    different list, and collapsing it would be answering a question nobody asked.
    """
    return None if names is None else _sha256(_canonical(sorted(names)))


def _last_flag_value(argv: Sequence[str], flags: Iterable[str]) -> str | None:
    """The value of `--flag value` or `--flag=value`, or None when it is absent."""
    found: str | None = None
    for index, arg in enumerate(argv):
        for flag in flags:
            if arg == flag and index + 1 < len(argv):
                found = argv[index + 1]
            elif arg.startswith(f"{flag}="):
                found = arg[len(flag) + 1 :]
    return found


def _unquote(value: str) -> str:
    """Drop one pair of TOML quotes, so `-c key="high"` and `-c key=high` agree."""
    for quote in ('"', "'"):
        if len(value) >= 2 and value.startswith(quote) and value.endswith(quote):
            return value[1:-1]
    return value


def _config_value(argv: Sequence[str], key: str) -> str | None:
    """The value of a `-c key=value` override, or None when that key is not set."""
    prefix = f"{key}="
    found: str | None = None
    for index, arg in enumerate(argv):
        preceded = index > 0 and argv[index - 1] in _CONFIG_FLAGS
        if preceded and arg.startswith(prefix):
            found = _unquote(arg[len(prefix) :])
    return found


def _sandbox_posture(argv: Sequence[str]) -> str | None:
    """The sandbox or permission posture the argv asks for, None when it names none."""
    for switch in _POSTURE_SWITCHES:
        if switch in argv:
            return switch
    named = _last_flag_value(argv, _POSTURE_FLAGS)
    return named if named is not None else _config_value(argv, "sandbox_mode")


def _digest(path: Path) -> dict[str, Any]:
    """sha256 and byte size of one instruction file. Its content is read and dropped.

    A file that exists and cannot be read is recorded as present with both values
    unknown. Leaving it out instead would say the instruction surface is not there, and
    an instruction the agent read is exactly what this field exists to notice.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return {"sha256": None, "bytes": None}
    return {"sha256": _sha256(data), "bytes": len(data)}


def _offer(found: dict[str, Path], base: Path, path: Path) -> None:
    """Record a file under the key it will be reported by, when it is there."""
    if path.is_file():
        found[os.path.relpath(path, base)] = path


def _instruction_files(cwd: Path) -> dict[str, Path]:
    """The instruction surfaces in play, keyed by the path each is reported under.

    Repository files are keyed relative to the repository root, so a capture started in
    a subdirectory and one started at the root name the same file the same way, and no
    absolute path reaches the payload. Outside a repository the keys are relative to the
    working directory.
    """
    root = git_root(cwd)
    base = Path(root).resolve() if root else cwd
    found: dict[str, Path] = {}
    for directory in dict.fromkeys((base, cwd)):
        for name in _REPO_FILES:
            _offer(found, base, directory / name)
        for path in sorted(directory.joinpath(*_RULES_DIR).glob("*.md")):
            _offer(found, base, path)
    keyed = {key: path for key, path in found.items() if not key.startswith("..")}
    home = Path.home()
    for relative in _HOME_FILES:
        path = home / relative
        if path.is_file():
            keyed[f"{_HOME_KEY}/{relative}"] = path
    return keyed


def _checked_extra(extra: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Refuse an unknown key here, where the caller can still be told which one."""
    if extra is None:
        return {}
    unknown = sorted(set(extra) - _EXTRA_KEYS)
    if unknown:
        raise ValueError(f"unknown fingerprint inputs: {unknown}")
    return extra


def fingerprint(
    provider: str,
    argv: Sequence[str],
    cwd: str | Path = ".",
    runtime_version: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The telltale.environment payload for one capture, id included.

    provider and runtime_version are what the launcher knows about the binary it is
    about to run; runtime_version is None until `claude --version` or an event was read,
    and a None here is "not yet observed", never "no version".

    model and effort come from argv when the flags are there. `extra` carries what only
    the launcher knows, all of it optional: tool_set and mcp_names (hashed as sorted
    name lists), settings (permission mode, hook event names, allowed tools, hashed),
    sandbox_posture, capture_modes and content_level. A key absent from `extra` is
    reported as None, which is the truth about an input nobody supplied; a key that is
    not one of the six is refused.
    """
    values = _checked_extra(extra)
    modes = values.get("capture_modes")
    settings = values.get("settings")
    payload: dict[str, Any] = {
        "provider": provider,
        "runtime_version": runtime_version,
        "model": _last_flag_value(argv, _MODEL_FLAGS),
        "effort": _last_flag_value(argv, _EFFORT_FLAGS)
        or _config_value(argv, "model_reasoning_effort"),
        "tool_set_hash": _hash_names(values.get("tool_set")),
        "mcp_names_hash": _hash_names(values.get("mcp_names")),
        "instruction_hashes": {
            key: _digest(path)
            for key, path in sorted(_instruction_files(Path(cwd).resolve()).items())
        },
        "settings_hash": None if settings is None else _sha256(_canonical(settings)),
        "sandbox_posture": values.get("sandbox_posture") or _sandbox_posture(argv),
        # Sorted for the same reason as the tool set: the order surfaces were configured
        # in is not a fact about the environment.
        "capture_modes": None if modes is None else sorted(modes),
        "content_level": values.get("content_level"),
    }
    # Over the payload without the id, because the id cannot contain itself. Any later
    # reader recomputes it by removing this one key.
    return {**payload, "fingerprint_id": f"env_{_sha256(_canonical(payload))}"}


def _write(obj: object) -> None:
    """stdout, without print: ruff T20 keeps print in cli.py and report.py alone."""
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def _demo(path: str) -> int:
    """Fingerprint `path` with fixed demo inputs, and show what those inputs were.

    The inputs are constants so that two runs in one directory differ only if the
    directory did. runtime_version stays None: no provider binary was run here, and
    filling it in would be inventing the one field this module cannot observe.
    """
    _write(
        {
            "inputs": {
                "provider": "claude",
                "argv": list(_DEMO_ARGV),
                "runtime_version": None,
                "extra": _DEMO_EXTRA,
            },
            "payload": fingerprint(
                "claude", _DEMO_ARGV, path, runtime_version=None, extra=_DEMO_EXTRA
            ),
        }
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m telltale.env",
        description="Environment fingerprint of a directory.",
    )
    parser.add_argument("--demo", action="store_true", help="print one fingerprint")
    parser.add_argument("path", nargs="?", default=".", help="working directory")
    args = parser.parse_args(argv)
    if not args.demo:
        parser.print_help()
        return 2
    return _demo(args.path)


if __name__ == "__main__":
    sys.exit(main())
