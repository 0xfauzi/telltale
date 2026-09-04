"""Turning a command line into something safe to store and something safe to compare.

Two jobs, design 6.4 and 6.10.

`normalize` produces the NORMAL FORM: the executable, up to two subcommand words, the
flag-SHAPED tokens with their values stripped, the paths made repo-relative, and an
underscore for everything else. `uv run pytest tests/test_calc.py -k zero` becomes
`uv run pytest tests/test_calc.py -k _`. Two runs of the same command then produce the
same string, while a filename, a search string or a token in an argument does not
survive.

`classify` reads that normal form and says what kind of work it was: test, typecheck,
lint, format, build, benchmark, security_scan, package_op, git, process_mgmt, shell, or
unknown. Nothing here judges the command; the names are deliberately neutral (spec 13).

A chain is read whole, in both directions. A newline is a `;` to the shell, so the
normal form carries one (W4-T3), and a verification command ANYWHERE in the chain makes
the call a verification run: `uv sync -q | tail -2; uv run pytest` is a test run and not
a package operation. `categories` names every kind of work the chain held, so the one
category a row carries hides none of the others.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from pathlib import Path
from typing import Any

from telltale import commands_shell
from telltale.allowlist import ALLOWLIST, Kind
from telltale.model import to_json
from telltale.sanitize import Ctx, relativize, scrub

# Design 6.4 said 200. W4-T3 measured on the six E12 streams that 2 of their 51 pytest
# runs sat past the 200th character of a chain's normal form and were cut off it, and
# the owner raised the bound on 2026-09-03 to the one every other kept string already
# has. With the bound lifted, the same 769 commands give 100 normal forms of 200 or
# more, 7 of 512 or more, none of 768 or more (the longest is 717), and no pytest
# segment that starts past character 300. What a longer stored form can carry is more
# flags, more placeholders and more paths already made repo-relative or hashed: never a
# value.
MAX_COMMAND = 512

# v3 (W2-T8): a token beginning with `-` survives only when it is FLAG-SHAPED, and the
# secret scrub runs on the normal form before the bound. v4 (W4-T3): a newline outside
# quotes and heredoc bodies is a `;`, so a two-line command stores two segments where it
# stored one run-on. All three change stored strings, so all three change the version: a
# row normalized by v1, one by v3 and one by v4 are not comparable and must not share a
# label. Measured on the six E12 streams: 259 of 769 Bash commands normalize differently
# under v4, and 578 separators are inserted across them. v5 (W4-F2): the bound is 512,
# so 100 of those 769 store a longer form than v4 did; a v4 row cut at 200 cannot be
# extended by a rewrite, which is why the version moves rather than the rows.
NORMALIZATION_VERSION = "cmdnorm-v5"
# A separate version for the path shlex could not take. A command normalized by the
# fallback is not comparable with one that was parsed, so they share no label.
FALLBACK_VERSION = "cmdnorm-v5-fallback"

# The operators a pipeline is cut on. Kept in the normal form: `pytest && git` and
# `pytest ; git status` are different commands and the difference costs two characters.
_SEPARATORS = frozenset({"|", "||", "&&", ";", "&"})
_STRUCTURAL = "<>&|;()"

# A subcommand word: `run`, `commit`, `check`. Bounded and lowercase, so a bare argument
# that happens to be a filename or a search term does not slip in as one.
_BARE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}$")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_BARE_BUDGET = 2  # design 6.4: the first two bare tokens

# What a flag looks like, once any `=value` is cut off. Design 6.4 said "every token
# starting with `-`", which is a rule about a token's FIRST CHARACTER and not about its
# shape, and a shell token is whatever the quoting says it is: `echo "--- sk-ant not X
# ---"` is ONE token, it begins with `-`, and the whole quoted string was therefore
# stored verbatim. Measured on the owner's store on 2026-09-02, after the backfill
# import: ten observation rows held a credential probe and every one of them was inside
# such a token, on five surfaces and in launcher and imported captures alike.
#
# The shape keeps `-k`, `-rho`, `--max-turns` and `--output-format`. It refuses a token
# with a space in it, `-----BEGIN ...`, a bare `-` and a bare `--`. What it cannot
# refuse is a short quoted argument that happens to be flag-shaped: `git commit -m
# "-TELLTALEFAKE"` stores `-TELLTALEFAKE`, because nothing distinguishes it from `-m`.
# That residual is named in design 6.4 rather than papered over.
_FLAG = re.compile(r"^--?[A-Za-z0-9][A-Za-z0-9_.:+-]{0,31}$")

# What a row rewritten by `store.resanitize` gains in `redaction.redacted`, so a reader
# can tell a normal form these rules PRODUCED from one they were applied to afterwards.
# `normalization_version` cannot carry that difference on its own: re-running the token
# rules over a v1 string cannot restore what v1 never recorded, so a rewritten v1 row
# carries the current version AND this marker, and the capture's diagnostics row names
# the version it came from.
RESANITIZE_MARKER = "resanitize"

_SOURCE_SUFFIXES = (".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb")


def normalize(command: str, ctx: Ctx, level: int) -> tuple[str, str, int]:
    """The normal form of a command line, the version of the rules, and its redactions.

    The third value is how many secrets the scrub of design 6.4 replaced, which the
    caller records in `redaction.redacted`.
    """
    text = command.strip()
    if not text:
        return "", NORMALIZATION_VERSION, 0
    try:
        tokens = _lex(commands_shell.with_separators(text))
    except ValueError:
        # The fallback reads the RAW line and not the marked one, so W2-T1's rule for a
        # command shlex refuses is exactly what it was. Measured on the six E12 streams:
        # shlex refuses 40 of 769 commands, the same 40 before and after the marking, so
        # the marking neither creates nor repairs a fallback.
        scrubbed, hits = _finish([_fallback(text, level)])
        return scrubbed, FALLBACK_VERSION, hits
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
    scrubbed, hits = _finish(parts)
    return scrubbed, NORMALIZATION_VERSION, hits


def _finish(parts: list[str]) -> tuple[str, int]:
    """Join, scrub, bound: the last three things that happen to every normal form.

    The scrub runs BEFORE the bound, as it does for every other kept string, so a
    credential the 200-character bound would cut in half is replaced whole rather than
    half-stored. It runs at all because the shape rules above are shapes: `-ghp_` and
    sixteen characters is a flag by every test this module can make, and it is also a
    GitHub token. W2-T8 measured that no scrub had ever seen a normal form: the COMMAND
    branch of sanitize._clean_str returned before the one that scrubs, so the private
    key header design 6.4 names survived in four stored rows.
    """
    text, hits = scrub(" ".join(part for part in parts if part))
    return text[:MAX_COMMAND], hits


def renormalize(command_norm: str) -> tuple[str, int]:
    """Apply the current token rules to a NORMAL FORM an older version wrote.

    The input is a stored normal form and not a command line: its tokens are already
    separated by single spaces and its `_` placeholders are already placeholders. So
    there is no lexing here, and the raw command is not consulted, because it no longer
    exists. Every branch below keeps a token, cuts it (an assignment loses its value, an
    absolute path becomes its `<outside>` placeholder) or replaces it with `_`, which is
    what makes this safe to run on an already sanitized string: it can only remove.

    The assignment and path cuts are the rules `_normalize_token` applies to a raw
    token, repeated here because cmdnorm-v3 kept both whole: measured on the owner's
    export on 2026-09-04, 3 of 110,655 stored commands carried an assignment value and
    37 an absolute path (`/usr/local`, `/`, `//`), and the import gate of
    export_validation, which requires a fixed point of this function, refused every
    one of them while `resanitize` left them standing. One definition of the normal
    form, applied by both.

    The bare-token budget is deliberately not re-applied. It was spent by the original
    normalization over the original tokenization, and a multi-word token that survived
    as one "flag" arrives here as several tokens, so re-spending it would drop words
    that have nothing to do with the leak. `store.resanitize` rewrites with it and
    `export_validation` refuses whatever it would rewrite.
    """
    parts: list[str] = []
    head = True
    for token in command_norm.split():
        parts.append(_renormalize_token(token, head))
        head = _next_head(token, head)
    return _finish(parts)


def resanitize_row(
    observation_type: str,
    payload: dict[str, Any],
    redaction: Any,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """One stored row under the current rules: (payload, redaction, fields rewritten).

    Zero fields means nothing changed and the caller writes nothing. Which fields are
    commands is read from the ALLOWLIST rather than named here, so a provider module
    that adds one is covered by the entry it already has to add.

    `normalization_version` moves only when a field moved. A version is a claim about
    which rules produced a string, and no claim is made about a string nobody rewrote.
    A rewritten row keeps the fallback suffix if it had one: the fallback is what shlex
    could not parse, and a rewrite of its output did not parse it either.
    """
    fields = [
        name
        for name, kind in ALLOWLIST.get(observation_type, {}).items()
        if kind is Kind.COMMAND
    ]
    out = dict(payload)
    changed = 0
    for name in fields:
        value = payload.get(name)
        if not isinstance(value, str):
            continue
        rewritten, _hits = renormalize(value)
        if rewritten != value:
            out[name] = rewritten
            changed += 1
    if not changed:
        return payload, redaction, 0
    stored = str(payload.get("normalization_version", ""))
    out["normalization_version"] = (
        FALLBACK_VERSION if stored.endswith("-fallback") else NORMALIZATION_VERSION
    )
    return out, _marked(redaction), changed


def _marked(redaction: Any) -> dict[str, Any]:
    """`redaction` with the rewrite named in `redacted`, so the row says it happened."""
    out = dict(redaction) if isinstance(redaction, dict) else {}
    entry = f"{RESANITIZE_MARKER}:{NORMALIZATION_VERSION}"
    listed = [str(item) for item in out.get("redacted", [])]
    out["redacted"] = listed if entry in listed else [*listed, entry]
    return out


def _renormalize_token(token: str, head: bool) -> str:
    if _is_structural(token):
        return token
    if token.startswith("-"):
        return _flag_token(token)
    if _ENV_ASSIGN.match(token):
        return token.split("=", 1)[0] + "="
    if _is_path_like(token):
        # Level 1, because a level 0 normal form holds no path at all, so there is
        # nothing here for the level to change.
        return _path_token(token, Ctx(), 1)
    if head or _BARE.match(token):
        return token
    return "_"


def _next_head(token: str, head: bool) -> bool:
    """Whether the NEXT token starts a segment.

    A segment's head is its executable: the first token after a separator that is not
    an environment assignment. It is kept whatever its shape, because a basename is not
    required to look like a subcommand (`7z`, `Python3`) and the head was already
    reduced to a basename when it was first normalized.
    """
    if token in _SEPARATORS:
        return True
    return head and bool(_ENV_ASSIGN.match(token))


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

    The executable, the bare subcommand words the budget allows, and an underscore if
    anything followed them. The other reading of design 6.4's "fallback: one token" is
    to treat the WHOLE line as the token, and that is rejected here because the line is
    then stored verbatim, which is the one thing the normal form exists to prevent.

    Keeping the subcommand is W2-T1. `git commit -m "<a message with a newline>"` is a
    line shlex refuses, so before this every such commit was stored as `git _` and no
    stored normal form in the whole build contained "git commit": a reader could not
    tell a commit from a `git status`. The words are only kept while they match _BARE,
    so a filename or a search string still cannot reach the store through here.
    """
    words = text.split()
    prefix, head = _env_prefix(words)
    if head >= len(words):
        return " ".join(prefix)
    executable = _basename(words[head])
    if level <= 0:
        return executable
    out = [*prefix, executable]
    rest = words[head + 1 :]
    kept = 0
    while kept < _BARE_BUDGET and kept < len(rest) and _BARE.match(rest[kept]):
        out.append(rest[kept])
        kept += 1
    return " ".join([*out, "_"] if len(rest) > kept else out)


def _env_prefix(tokens: list[str]) -> tuple[list[str], int]:
    """The leading `NAME=value` assignments as `NAME=`, and where the command starts.

    The `=` is kept and the value is not. Without it the normal form says `UV_CACHE_DIR
    uv run pytest` and classify() cannot tell that first word from an executable, which
    is exactly what made `UV_CACHE_DIR=/x uv run pytest && git status` classify as git
    (measured on Codex S1 and S6). The NAME is a fact about the run; the value is not.
    """
    head = 0
    prefix: list[str] = []
    while head < len(tokens) and _ENV_ASSIGN.match(tokens[head]):
        prefix.append(tokens[head].split("=", 1)[0] + "=")
        head += 1
    return prefix, head


def _segment(tokens: list[str], ctx: Ctx, level: int) -> list[str]:
    prefix, head = _env_prefix(tokens)
    if head >= len(tokens):
        return prefix
    executable = _basename(tokens[head])
    if level <= 0:
        # Design 6.4: level 0 keeps only the basenames.
        return [executable]
    out = [*prefix, executable]
    rest = tokens[head + 1 :]
    # A RUNNER word is not a subcommand and does not spend the budget. `uv run ruff
    # format .` has three words before its first argument and design 6.4's budget is
    # two, so `run` and `ruff` were kept and `format` became `_`: `uv run ruff _ .`, on
    # which the table matches neither ("ruff", "format") nor ("ruff", "check"), so it
    # was classified `unknown` and was not a verification run at all. Measured on the
    # six E12 streams: 81 of 769 Bash calls hold `uv run ruff _`, and lint and format
    # were the category of NONE of the 769. The words freed here are members of
    # _RUNNERS, which is a fixed table, so nothing a person typed reaches the store
    # through this: `run` is stored because it equals a constant.
    free = max(_runner_words([executable, *rest]) - 1, 0)
    budget = _BARE_BUDGET
    for index, token in enumerate(rest):
        if index < free:
            out.append(token)
            continue
        text, budget = _normalize_token(token, budget, ctx, level)
        out.append(text)
    return out


def _normalize_token(token: str, budget: int, ctx: Ctx, level: int) -> tuple[str, int]:
    if _is_structural(token):
        return token, budget
    if token.startswith("-"):
        return _flag_token(token), budget
    if _ENV_ASSIGN.match(token):
        return token.split("=", 1)[0] + "=", budget
    if _is_path_like(token):
        return _path_token(token, ctx, level), budget
    if budget > 0 and _BARE.match(token):
        return token, budget - 1
    return "_", budget


def _flag_token(token: str) -> str:
    """A dash-leading token, cut at `=`, if what is left is shaped like a flag."""
    flag = token.split("=", 1)[0]
    return flag if _FLAG.match(flag) else "_"


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

# The verification categories of design 6.10, in the order a chain that holds several of
# them is named by. It lives here rather than in the reducer because the priority IS a
# classification rule and belongs to CLASSIFIER_VERSION; activities_tools re-exports it.
#
# The order is by how much of the work the category stands for. `uv run ruff format . &&
# uv run ruff check . && uv run mypy . && uv run pytest` is one tool call and gets one
# category: calling it a format run would describe the cheapest thing in it. A `test`
# beats a `benchmark` only because pytest-benchmark is already resolved one rule further
# down, so the two can only meet in a chain that really ran both.
_VERIFICATION_ORDER = (
    "test",
    "benchmark",
    "typecheck",
    "lint",
    "format",
    "build",
    "security_scan",
)
VERIFICATION = frozenset(_VERIFICATION_ORDER)

# The rules classify() applies around the table, named so that they are part of the
# version below. A rule that changes which category a stored command lands in changes
# the version even when the table itself did not move.
_CLASSIFY_RULES = (
    "skip-leading-env-assignments",
    "strip-runner-prefix",
    "a-runner-word-does-not-spend-the-bare-token-budget",
    "a-newline-is-a-separator",
    "highest-priority-verification-category-in-the-chain-wins",
    "otherwise-the-first-segment-with-a-known-category-wins",
    "pytest-with-a-benchmark-flag-is-benchmark",
)

# The version is the hash of the tables above, not a number somebody remembers to bump.
# Two captures classified by different tables must not compare as though they agreed,
# and hashing the SOURCE FILE instead would change the version when a comment changes.
CLASSIFIER_VERSION = (
    "commands-v1-"
    + hashlib.sha256(
        to_json([_RULES, _RUNNERS, _VERIFICATION_ORDER, _CLASSIFY_RULES]).encode(
            "utf-8"
        )
    ).hexdigest()[:8]
)


def classify(command_norm: str) -> tuple[str, str]:
    """Return (category, scope) for a normal form. Design 6.10.

    A chain can hold two kinds of work, and which one names it depends on whether any of
    them is a VERIFICATION. A verification command anywhere in the chain ran: the shell
    reached it, and spec 13 exists to say the agent checked its work. So the highest
    priority verification category present wins wherever it sits, and the scope comes
    from that same segment. A chain with none of them keeps the old rule, the first
    segment with a known category, because there the leading command is the work and the
    rest is what was done with its output.

    What forced it, measured by the orchestrator over the six E12 sessions: their raw
    streams hold 52 pytest executions and this reducer summed `agent_test_runs` to 14.
    `uv sync -q | tail -2; uv run pytest` was a package_op, and `uv run ruff format . &&
    uv run ruff check . && uv run mypy . && uv run pytest` was a format run.
    CLASSIFIER_VERSION records that these rules were the ones in force.
    """
    found = _classified(command_norm.split())
    index = _chosen(found)
    return found[index] if index is not None else ("unknown", "unknown")


def categories(command_norm: str) -> list[str]:
    """Every category this chain held, in segment order, each named once.

    The chain's one `category` is a choice among these, and this is what keeps the
    choice from hiding the rest: a row that says `test` for a chain that also ran ruff
    and mypy can still say so. `unknown` is not a category and is left out; a chain of
    nothing but unknown segments returns an empty list rather than `["unknown"]`, which
    would read as a category nobody found.
    """
    out: list[str] = []
    for category, _scope in _classified(command_norm.split()):
        if category != "unknown" and category not in out:
            out.append(category)
    return out


def _classified(tokens: list[str]) -> list[tuple[str, str]]:
    """(category, scope) for every segment of the chain, in order.

    One function so that `classify`, `categories` and `exit_masked` read the same
    segments in the same order. `exit_masked` is a statement about the segment
    `classify` chose, and the two drifting apart is the defect it would produce.
    """
    return [_classify_segment(segment) for segment in _split_segments(tokens)]


def _chosen(found: list[tuple[str, str]]) -> int | None:
    """Which segment names the chain, or None when no segment has a known category."""
    for wanted in _VERIFICATION_ORDER:
        for index, (category, _scope) in enumerate(found):
            if category == wanted:
                return index
    for index, (category, _scope) in enumerate(found):
        if category != "unknown":
            return index
    return None


def exit_masked(command_norm: str) -> bool:
    """Whether this chain's exit status belongs to a program `classify` did not name.

    A shell reports ONE status for a whole chain, and it is the last command's. So the
    status stands for the classified segment only when that segment is last, or when
    everything after it is joined by `&&`: a non-zero there stops the chain and becomes
    the chain's own status, so a failure cannot hide. After a `|`, a `;`, a `||` or a
    `&` the status is another program's and this segment's is unknown.

    Measured on E01's S1 and on all five W2-E05 re-run captures: every one of them ran
    `uv run pytest 2>&1 | tail -50`, whose normal form keeps the operator
    (`uv run pytest _ >& _ | tail -50`), the tests failed, and `tail` exited 0. Codex S6
    is the other side of the rule: `uv run pytest && git diff --check && git diff _
    pkg/calc.py` is not masked, because a failing pytest ends that chain with its own
    code.

    A redirection is not a separator and does not mask anything: `pytest > _` and
    `pytest 2>& _` are still pytest's status, which is why `_STRUCTURAL` tokens are
    passed over here and only `_SEPARATORS` are read.

    The segment judged is the one `classify` CHOSE, which since W4-T3 is not always the
    first one with a category. `uv sync -q | tail -2 ; uv run pytest` is a test run
    whose pytest is last, so nothing masks it; reading the `uv sync` at the front
    would call it masked by the `|` that is not in front of the test at all. A newline
    counts as the `;` it is: `commands_shell` has already written it as one.
    """
    tokens = command_norm.split()
    separators = [token for token in tokens if token in _SEPARATORS]
    index = _chosen(_classified(tokens))
    if index is None:
        return False
    return any(token != "&&" for token in separators[index:])


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
    # `UV_CACHE_DIR= uv run pytest`: an assignment is not the command, so the table is
    # consulted from the word after it. Without this the segment matched nothing and
    # classify() fell through to the NEXT segment, which is why
    # `UV_CACHE_DIR=/x uv run pytest && git status` was recorded as git on Codex S1.
    words = words[len(_env_prefix(words)[0]) :]
    words = _strip_runner(words)
    category, matched = _match(words)
    if category == "test" and any(flag.startswith("--benchmark") for flag in flags):
        # pytest-benchmark is pytest plus a flag, and design 6.10 counts it as a
        # benchmark rather than a test. The flag survives normalization, so this is
        # readable from the normal form alone.
        category = "benchmark"
    return category, _scope(category, words[matched:], flags)


def _strip_runner(words: list[str]) -> list[str]:
    return words[_runner_words(words) :]


def _runner_words(words: list[str]) -> int:
    """How many leading words are a runner prefix. `uv run pytest` answers 2.

    Read twice, from both ends of the pipeline: `_segment` uses it to decide which words
    do not spend the bare-token budget, and `_strip_runner` to decide which to drop
    before the table is consulted. One function, so the two cannot disagree about where
    a command starts.
    """
    for runner in _RUNNERS:
        if tuple(words[: len(runner)]) == runner:
            return len(runner)
    return 0


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
