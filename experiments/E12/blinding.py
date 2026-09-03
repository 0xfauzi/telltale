"""Did the blinding hold: what the material said, and what the reviewer read.

protocol.md makes two claims that only a measurement can settle, and this module is that
measurement. The first is that the reviewer's working directory holds one arm's material
and nothing else, so the answer key in this repository is out of reach. The second is
that the material itself does not tell the reviewer which session it is looking at.
Both are checked per run and both are recorded whatever they say: a leak that is
measured is a fact about the material, and scrubbing it after the material was defined
would be a different experiment under a new id.

Everything here reads. Nothing writes to the store, and nothing rewrites the material.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

# The task ids of the six sessions, as VERIFY greps for them. Counted in the material
# of every arm and recorded: arm R carries them in the brief text of the raw stream and
# that is not scrubbed, and arm S carries whichever of them the session typed into a
# command that the timeline prints.
TASK_TOKENS = ("W4-T2", "W3-V", "W3-E08", "W3-T4", "W4-T1")

# Where a `<outside>/<digest>` may be decoded back to a path. The repository is in the
# list because the answer key is in it: a digest that resolves under this root is the
# violation the protocol is written against, and one that resolves to a scratch file the
# reviewer wrote out of its own material is not. A digest that resolves nowhere stays
# undecoded and is reported as such.
DECODE_ROOTS = (Path("/tmp"), Path("/private/tmp"), REPO_ROOT)


def db_path() -> Path:
    """The owner's store, the one the reviewer sessions are captured in."""
    return Path(os.environ.get("TELLTALE_HOME") or "~/.telltale").expanduser() / (
        "telltale.db"
    )


def query(sql: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    try:
        return list(conn.execute(sql, args))
    finally:
        conn.close()


def claiming(task_id: str) -> set[str]:
    """The captures that say they are this task, from their own capture_started row.

    By what the capture SAYS about itself rather than by being the newest row: another
    launcher may write into this database while a reviewer runs.
    """
    rows = query(
        "SELECT capture_id FROM observations WHERE observation_type ="
        " 'telltale.capture_started' AND json_extract(payload, '$.task_id') = ?",
        (task_id,),
    )
    return {str(row[0]) for row in rows}


def leak_counts(into: Path, names: list[str]) -> dict[str, int]:
    """How many times each material file names one of the six task ids."""
    found: dict[str, int] = {}
    for name in names:
        text = (into / name).read_text(encoding="utf-8", errors="replace")
        found[name] = sum(text.count(token) for token in TASK_TOKENS)
    return found


def _inside(cwd: Path, names: list[str]) -> set[str]:
    """Every spelling of a material path that `sanitize.relativize` can produce.

    The reviewer's directory is not a git repository, so the launcher has no repo root
    and an ABSOLUTE path is stored as `<outside>/<8 hex of sha256>`. The digest is
    computable, so the material's own paths are computed here and everything else that
    reaches the timeline is a read of something that is not the material.
    """
    allowed = set()
    for name in names:
        allowed |= {name, f"./{name}"}
        for root in {cwd, Path("/private") / cwd.relative_to("/")}:
            digest = hashlib.sha256(str(root / name).encode("utf-8")).hexdigest()[:8]
            allowed |= {str(root / name), f"<outside>/{digest}"}
    return allowed


def reads_outside(capture_id: str, cwd: Path, names: list[str]) -> dict[str, Any]:
    """The file_read paths of the reviewer's capture that are not its material.

    `commands_outside` is beside it because the file_read surface alone does not answer
    the question the protocol asks. A reviewer that runs `cat ../../key.json` has read
    outside its material and has produced no file_read at all: the pilot's arm S
    reviewer read all three of its files with `cat` and its file_read count is zero.
    """
    allowed = _inside(cwd, names)
    rows = query(
        "SELECT json_extract(fields, '$.file_path') FROM activities WHERE capture_id"
        " = ? AND activity_type = 'file_read'",
        (capture_id,),
    )
    paths = [str(row[0]) for row in rows if row[0] is not None]
    outside = sorted({one for one in paths if one not in allowed})
    commands = commands_outside(capture_id, names)
    return {
        "file_reads": len(paths),
        "paths": sorted(set(paths)),
        "outside": outside,
        "outside_count": len(outside),
        "commands_outside": commands,
        "commands_outside_decoded": decode(commands, cwd),
    }


def _leaves(token: str, names: list[str]) -> bool:
    """True when one word of a command names a place that is not the material."""
    if token in names or token in {f"./{one}" for one in names}:
        return False
    if token.startswith(("~", "/", "../", "<outside>")):
        return True
    return "/" in token.strip("'\"")


def commands_outside(capture_id: str, names: list[str]) -> list[str]:
    """Every stored command of the reviewer that names a path outside its material."""
    rows = query(
        "SELECT json_extract(fields, '$.command_norm') FROM activities WHERE"
        " capture_id = ? AND activity_type = 'command'",
        (capture_id,),
    )
    found = []
    for row in rows:
        text = str(row[0] or "")
        if any(_leaves(word, names) for word in text.split()):
            found.append(text)
    return sorted(set(found))


def _digests_under(root: Path) -> dict[str, str]:
    """Every path under one root, by the 8 hex `relativize` would print for it."""
    found: dict[str, str] = {}
    for parent, folders, files in os.walk(root):
        folders[:] = [one for one in folders if one not in {".git", ".venv"}]
        for name in [*files, *folders]:
            whole = str(Path(parent) / name)
            for spelling in {whole, _twin(whole)}:
                digest = hashlib.sha256(spelling.encode("utf-8")).hexdigest()[:8]
                found.setdefault(digest, spelling)
    return found


def _twin(path: str) -> str:
    """macOS spells one directory two ways, and the agent may pass either."""
    if path.startswith("/private/"):
        return path[len("/private") :]
    return "/private" + path if path.startswith("/var/") else path


def decode(commands: list[str], cwd: Path) -> dict[str, str | None]:
    """What each `<outside>/<digest>` in those commands named, where it can still be
    found on this disk.

    Best effort by construction: the digest is one way, so this hashes candidate paths
    and looks the answer up. A file the reviewer wrote and deleted decodes to null, and
    null is printed rather than treated as harmless.
    """
    wanted = {
        word.split("/", 1)[1]
        for text in commands
        for word in text.split()
        if word.startswith("<outside>/")
    }
    if not wanted:
        return {}
    table: dict[str, str] = {}
    for root in (cwd, cwd.parent, *DECODE_ROOTS):
        if root.exists():
            table = {**_digests_under(root), **table}
        for spelling in {str(root), _twin(str(root))}:
            table.setdefault(
                hashlib.sha256(spelling.encode("utf-8")).hexdigest()[:8], spelling
            )
    return {digest: table.get(digest) for digest in sorted(wanted)}
