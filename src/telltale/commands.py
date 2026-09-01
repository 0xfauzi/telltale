"""Turning a command line into something safe to store and something safe to compare.

Two jobs, design 6.4 and 6.10.

`normalize` produces the NORMAL FORM: the executable, up to two subcommand words, the
flags with their values stripped, the paths made repo-relative, and an underscore for
everything else. `uv run pytest tests/test_calc.py -k zero` becomes `uv run pytest
tests/test_calc.py -k _`. Two runs of the same command then produce the same string,
while a filename, a search string or a token in an argument does not survive.

`classify` reads that normal form and says what kind of work it was: test, typecheck,
lint, format, build, benchmark, security_scan, package_op, git, process_mgmt, shell, or
unknown. Nothing here judges the command; the names are deliberately neutral (spec 13).
"""

from __future__ import annotations

import hashlib
import re
import shlex
from pathlib import Path

from telltale.model import to_json
from telltale.sanitize import Ctx, relativize

MAX_COMMAND = 200  # design 6.4

NORMALIZATION_VERSION = "cmdnorm-v1"
# A separate version for the path shlex could not take. A command normalized by the
# fallback is not comparable with one that was parsed, so they share no label.
FALLBACK_VERSION = "cmdnorm-v1-fallback"

# The operators a pipeline is cut on. Kept in the normal form: `pytest && git` and
# `pytest ; git status` are different commands and the difference costs two characters.
_SEPARATORS = frozenset({"|", "||", "&&", ";", "&"})
_STRUCTURAL = "<>&|;()"

# A subcommand word: `run`, `commit`, `check`. Bounded and lowercase, so a bare argument
# that happens to be a filename or a search term does not slip in as one.
_BARE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}$")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_BARE_BUDGET = 2  # design 6.4: the first two bare tokens

_SOURCE_SUFFIXES = (".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb")


def normalize(command: str, ctx: Ctx, level: int) -> tuple[str, str]:
    """Return the normal form of a command line, and the version of the rules used."""
    text = command.strip()
    if not text:
        return "", NORMALIZATION_VERSION
    try:
        tokens = _lex(text)
    except ValueError:
        return _fallback(text, level), FALLBACK_VERSION
    parts: list[str] = []
    segment: list[str] = []
    for token in tokens:
        if token in _SEPARATORS:
            parts.extend(_segment(segment, ctx, level))
            parts.append(token)
            segment = []
        else:
            segment.append(token)
    parts.extend(_segment(segment, ctx, level))
    return " ".join(part for part in parts if part)[:MAX_COMMAND], NORMALIZATION_VERSION


def _lex(text: str) -> list[str]:
    """Split on whitespace, respecting quotes, with `&&`, `||`, `;` and `|` as tokens.

    `shlex.split` alone is not enough: it leaves `ls;pwd` as one token, so a second
    command hides inside the first. `punctuation_chars` is what separates them, and it
    still does not split a `;` inside quotes, which `str.split(";")` would.
    """
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _fallback(text: str, level: int) -> str:
    """What to record when shlex refuses the line, which an unbalanced quote does.

    One token: the executable, and an underscore if anything followed it. The other
    reading of design 6.4's "fallback: one token" is to treat the WHOLE line as the
    token, and that is rejected here because the line is then stored verbatim, which is
    the one thing the normal form exists to prevent.
    """
    words = text.split()
    head = _basename(words[0]) if words else ""
    return f"{head} _" if len(words) > 1 and level > 0 else head


def _segment(tokens: list[str], ctx: Ctx, level: int) -> list[str]:
    head = 0
    prefix: list[str] = []
    while head < len(tokens) and _ENV_ASSIGN.match(tokens[head]):
        # `FOO=bar cmd`: the variable NAME is a fact about the run, its value is not.
        prefix.append(tokens[head].split("=", 1)[0])
        head += 1
    if head >= len(tokens):
        return prefix
    executable = _basename(tokens[head])
    if level <= 0:
        # Design 6.4: level 0 keeps only the basenames.
        return [executable]
    out = [*prefix, executable]
    budget = _BARE_BUDGET
    for token in tokens[head + 1 :]:
        text, budget = _normalize_token(token, budget, ctx, level)
        out.append(text)
    return out


def _normalize_token(token: str, budget: int, ctx: Ctx, level: int) -> tuple[str, int]:
    if _is_structural(token):
        return token, budget
    if token.startswith("-"):
        return token.split("=", 1)[0], budget
    if _ENV_ASSIGN.match(token):
        return token.split("=", 1)[0], budget
    if _is_path_like(token):
        return _path_token(token, ctx, level), budget
    if budget > 0 and _BARE.match(token):
        return token, budget - 1
    return "_", budget


def _path_token(token: str, ctx: Ctx, level: int) -> str:
    relative = relativize(token, ctx, level)
    if relative is None or any(char.isspace() for char in relative):
        # The normal form is space-joined, so classify() can split it back into tokens.
        # A path containing whitespace cannot survive that and becomes a placeholder.
        return "_"
    # `pytest tests/` and `pytest tests` are different commands: the first names a
    # directory. Path normalization drops the trailing slash, and classify() reads the
    # normal form alone, so scope would silently become `full` for a targeted run.
    return (
        relative + "/"
        if token.endswith("/") and not relative.endswith("/")
        else relative
    )


def _basename(token: str) -> str:
    return Path(token).name or token


def _is_structural(token: str) -> bool:
    return bool(token) and all(char in _STRUCTURAL for char in token)


def _is_path_like(token: str) -> bool:
    return "/" in token or token.startswith("~") or token in {".", ".."}


# The classification table of design 6.10, keyed by the leading non-flag words of a
# segment. Longest prefix wins, so `ruff check` is lint while `ruff format` is format.
_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("pytest",), "test"),
    (("vitest",), "test"),
    (("jest",), "test"),
    (("go", "test"), "test"),
    (("cargo", "test"), "test"),
    (("npm", "test"), "test"),
    (("mypy",), "typecheck"),
    (("pyright",), "typecheck"),
    (("tsc",), "typecheck"),
    (("ty",), "typecheck"),
    (("ruff", "check"), "lint"),
    (("eslint",), "lint"),
    (("flake8",), "lint"),
    (("pylint",), "lint"),
    (("ruff", "format"), "format"),
    (("black",), "format"),
    (("prettier",), "format"),
    (("uv", "build"), "build"),
    (("npm", "run", "build"), "build"),
    (("make",), "build"),
    (("cargo", "build"), "build"),
    (("docker", "build"), "build"),
    (("hyperfine",), "benchmark"),
    (("gitleaks",), "security_scan"),
    (("bandit",), "security_scan"),
    (("uv", "audit"), "security_scan"),
    (("npm", "audit"), "security_scan"),
    (("uv", "sync"), "package_op"),
    (("uv", "add"), "package_op"),
    (("uv", "lock"), "package_op"),
    (("uv", "pip"), "package_op"),
    (("pip",), "package_op"),
    (("pip3",), "package_op"),
    (("npm", "install"), "package_op"),
    (("npm", "ci"), "package_op"),
    (("git",), "git"),
    (("kill",), "process_mgmt"),
    (("pkill",), "process_mgmt"),
    (("ps",), "process_mgmt"),
    (("sleep",), "process_mgmt"),
    (("wait",), "process_mgmt"),
    (("ls",), "shell"),
    (("cat",), "shell"),
    (("sed",), "shell"),
    (("grep",), "shell"),
    (("rg",), "shell"),
    (("find",), "shell"),
    (("head",), "shell"),
    (("tail",), "shell"),
    (("echo",), "shell"),
    (("wc",), "shell"),
    (("which",), "shell"),
)

# Words that run something else and say nothing about what kind of work it is. Stripped
# before the table is consulted, so `uv run pytest` and `pytest` land on one rule.
# `uv` alone is NOT here: `uv sync` is a package operation, `uv run pytest` a test.
_RUNNERS: tuple[tuple[str, ...], ...] = (
    ("uv", "run"),
    ("uvx",),
    ("npx",),
    ("poetry", "run"),
    ("pdm", "run"),
    ("hatch", "run"),
    ("python",),
    ("python3",),
)

# The version is the hash of the tables above, not a number somebody remembers to bump.
# Two captures classified by different tables must not compare as though they agreed,
# and hashing the SOURCE FILE instead would change the version when a comment changes.
CLASSIFIER_VERSION = (
    "commands-v1-"
    + hashlib.sha256(to_json([_RULES, _RUNNERS]).encode("utf-8")).hexdigest()[:8]
)


def classify(command_norm: str) -> tuple[str, str]:
    """Return (category, scope) for a normal form. Design 6.10.

    A pipeline can hold two kinds of work (`pytest && git status`). The FIRST segment
    with a known category wins, because that is the command the person ran and the rest
    is what they did with the result. CLASSIFIER_VERSION records that this rule was the
    one in force.
    """
    for segment in _split_segments(command_norm.split()):
        category, scope = _classify_segment(segment)
        if category != "unknown":
            return category, scope
    return "unknown", "unknown"


def _split_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return segments


def _classify_segment(tokens: list[str]) -> tuple[str, str]:
    flags = [token for token in tokens if token.startswith("-")]
    words = [
        token
        for token in tokens
        if not token.startswith("-") and not _is_structural(token)
    ]
    words = _strip_runner(words)
    category, matched = _match(words)
    if category == "test" and any(flag.startswith("--benchmark") for flag in flags):
        # pytest-benchmark is pytest plus a flag, and design 6.10 counts it as a
        # benchmark rather than a test. The flag survives normalization, so this is
        # readable from the normal form alone.
        category = "benchmark"
    return category, _scope(category, words[matched:], flags)


def _strip_runner(words: list[str]) -> list[str]:
    for runner in _RUNNERS:
        if tuple(words[: len(runner)]) == runner:
            return words[len(runner) :]
    return words


def _match(words: list[str]) -> tuple[str, int]:
    best_category = "unknown"
    best_length = 0
    for prefix, category in _RULES:
        if len(prefix) > best_length and tuple(words[: len(prefix)]) == prefix:
            best_category, best_length = category, len(prefix)
    return best_category, best_length


def _scope(category: str, rest: list[str], flags: list[str]) -> str:
    """targeted, full or unknown. Design 6.10 defines scope for test commands only.

    Everything else is `unknown` rather than `full`: `ruff check src/` is narrower than
    `ruff check`, but nothing here has established what the two mean, and "full" would
    be a claim about coverage that no measurement supports yet.
    """
    if category != "test":
        return "unknown"
    if "-k" in flags or any(_is_target(word) for word in rest):
        return "targeted"
    return "full"


def _is_target(word: str) -> bool:
    return "/" in word or word.endswith(_SOURCE_SUFFIXES)
