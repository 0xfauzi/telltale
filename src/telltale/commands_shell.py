"""What a raw command line's own SHELL structure is, before shlex is allowed near it.

Split out of commands.py at the 800-line ratchet, and it is the block with the thinnest
interface in the file: `with_separators(text) -> str` is the whole of it, called once by
`commands.normalize`, and nothing here imports the rest of the module or knows what a
category is. It reads one thing off a command line: the boundary between one command
and the next, and the three places a newline is not one.

Why it exists at all. `shlex` is a lexer for words and treats a newline as whitespace,
so `cd /tmp/x` on one line and `uv run pytest` on the next reached the classifier
as one segment whose head was `cd`. A newline IS a `;` to the shell. The decision needs
the quoting and the heredocs, and shlex has thrown both away by the time it hands back
tokens, so it has to be made here, on the raw text, first.
"""

from __future__ import annotations

import re

# A line that ends on one of these is not finished, so the newline after it is not a
# separator: `uv run ruff check . &&\n uv run pytest` is one chain, and masking depends
# on that `&&` surviving.
_TAIL_OPERATOR = re.compile(r"(?:\|\||&&|[|;&(])$")
# `<<TAG`, `<<'TAG'`, `<<"TAG"` and the tab-stripping `<<-TAG`. A here-STRING (`<<<`)
# does not match, because `<` is not a tag character, so it needs no branch of its own.
_HEREDOC = re.compile(
    r"""<<(-?)[ \t]*(?:'([^'\n]*)'|"([^"\n]*)"|([A-Za-z_][A-Za-z0-9_]*))"""
)
_SEPARATOR = " ; "


def with_separators(text: str) -> str:
    """The command line with a `;` inserted after every newline the shell reads as one.

    A newline IS a `;` to the shell, and `_lex` cannot see that: shlex treats it as
    whitespace, so `cd /tmp/x\nuv run pytest` arrived as one segment whose head was `cd`
    and classified as unknown. The marking happens here, before shlex, because the
    decision needs the quoting and the heredocs, and shlex has already thrown both away
    by the time it hands back tokens.

    Three kinds of newline are NOT separators and this is the whole of the rule:
    one inside a quoted string (`git commit -m "<two lines>"` is one segment, W2-T1),
    one inside a heredoc body (that body is data: `python3 - <<'PY' ... PY` carries a
    script, and a line in it reading `uv run pytest` ran nothing), and one after a
    trailing `&&`, `||`, `|`, `&`, `;` or `(`, where the shell is still waiting for the
    rest of the command. An escaped newline is a line continuation, skipped for the
    same reason.

    The heredoc body is REPLACED by `_` here rather than merely stepped over, which is
    the same sentence as the one above read to its end: a body is data, and design 6.4's
    normal form is a command's shape and not its data. Measured on the six E12 streams
    before this: `python3 - <<'PY' <3.9 kB of script> PY` followed by `uv run pytest`
    lexed the script into 190 characters of `_`, `(` and `)` and hit the 200-character
    MAX_COMMAND bound, so the pytest at the end of the line was cut off the normal form
    and no rule downstream could see it. 14 of the 17 test runs still missing after the
    newline rule alone were that, and 2 more were bodies whose quoting made shlex refuse
    the whole line.

    This function only INSERTS and REPLACES BODIES. Measured on the 769 Bash commands of
    the six E12 streams: removing the inserted markers from the 259 it splits reproduces
    the original strings byte for byte outside the bodies, no split segment is left
    holding an unbalanced quote, and none of the 578 markers falls inside any of the 250
    heredoc bodies that experiments/E12/make_key.py's own regex finds.

    The residual, measured rather than guessed. A heredoc opened INSIDE a double-quoted
    command substitution is not seen, because the quote is read first and the whole
    substitution is one protected span: `git commit -m "$(cat <<'EOF' <message> EOF )"`
    keeps whatever of that message shlex hands back. Disabling the quote branch alone
    over the 769 commands moves exactly ONE normal form, and it is that shape; the
    normal form it gets is the one it had before W4-T3, so nothing regressed, and
    reading it properly means parsing `$(...)`, which this does not do.
    """
    protected, bodies = _scan(text)
    parts: list[str] = []
    start = 0
    for at, end, replacement in _edits(_breaks(text, protected), bodies):
        parts.append(text[start:at])
        parts.append(replacement)
        start = end
    parts.append(text[start:])
    return "".join(parts)


def _edits(
    breaks: list[int], bodies: list[tuple[int, int]]
) -> list[tuple[int, int, str]]:
    """The rewrites to apply to the raw line, in order and never overlapping.

    A body span ends where its terminator line ends, and the newline after it is a break
    one past that, so the two kinds cannot collide.
    """
    marks = [(at, at + 1, "\n" + _SEPARATOR) for at in breaks]
    return sorted(marks + [(at, end, " _ ") for at, end in bodies])


def _breaks(text: str, protected: set[int]) -> list[int]:
    """The offsets of the newlines that end a command, in order.

    `start` is where the current command began, and it moves only when one ends: after
    a trailing `&&` the next line belongs to the same command, so the operator test has
    to see the whole of it and not just the last line.
    """
    out: list[int] = []
    start = 0
    for at, char in enumerate(text):
        if char != "\n" or at in protected:
            continue
        command = text[start:at].rstrip()
        if command.strip() and not _TAIL_OPERATOR.search(command):
            out.append(at)
            start = at + 1
    return out


def _scan(text: str) -> tuple[set[int], list[tuple[int, int]]]:
    """The newlines that are not separators, and the spans the heredoc bodies occupy.

    A newline is not a separator when it is inside a quote, a comment, a heredoc body or
    an escape. A comment is read here because shlex's `commenters` is `#` and this must
    agree with it: `# it's fine\nls` is two commands to both, and a scanner reading that
    apostrophe as an opening quote would swallow the newline and report one.
    """
    found: set[int] = set()
    bodies: list[tuple[int, int]] = []
    pending: list[tuple[str, bool]] = []
    at = 0
    while at < len(text):
        char = text[at]
        if char == "\\":
            at = _protect(text, at, at + 2, found)
        elif char in "'\"":
            at = _protect(text, at, _quoted(text, at), found)
        elif char == "#":
            at = _protect(text, at, _line_end(text, at), found)
        elif (tag := _HEREDOC.match(text, at)) is not None:
            body = tag.group(2) or tag.group(3) or tag.group(4)
            pending.append((body, bool(tag.group(1))))
            at = tag.end()
        elif char == "\n" and pending:
            end = _bodies(text, at + 1, pending)
            bodies.append((at, end))
            at = _protect(text, at, end, found)
            pending = []
        else:
            at += 1
    return found, bodies


def _protect(text: str, start: int, end: int, found: set[int]) -> int:
    """Mark every newline in text[start:end] as not a separator, and skip past it."""
    found.update(start + at for at, char in enumerate(text[start:end]) if char == "\n")
    return max(end, start + 1)


def _quoted(text: str, at: int) -> int:
    """Where the string opened at `at` closes, or the end of what never closed it.

    An unterminated quote is exactly what makes shlex refuse a line, and `normalize`
    answers that with the fallback. Returning the end of the text here keeps the two in
    step: nothing after the opening quote is a separator, and nothing was parsed.
    """
    quote = text[at]
    scan = at + 1
    while scan < len(text):
        if quote == '"' and text[scan] == "\\":
            scan += 2
            continue
        if text[scan] == quote:
            return scan + 1
        scan += 1
    return len(text)


def _line_end(text: str, at: int) -> int:
    end = text.find("\n", at)
    return len(text) if end < 0 else end


def _bodies(text: str, at: int, pending: list[tuple[str, bool]]) -> int:
    """Past this line's heredoc bodies, short of the final terminator line's newline.

    That last newline is left exposed on purpose: the command carrying the heredoc is
    finished once its body is, so it is a separator like any other.
    """
    for tag, strip in pending:
        at = _body_end(text, at, tag, strip)
    return at - 1 if text[at - 1 : at] == "\n" else at


def _body_end(text: str, at: int, tag: str, strip: bool) -> int:
    """Past one heredoc body's terminator line, or the end of an unterminated one."""
    while at <= len(text):
        end = _line_end(text, at)
        line = text[at:end]
        if (line.lstrip("\t") if strip else line).rstrip("\r") == tag:
            return min(end + 1, len(text))
        if end >= len(text):
            return len(text)
        at = end + 1
    return len(text)
