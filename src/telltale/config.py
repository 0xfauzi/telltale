"""Where Telltale keeps its own two files, and the promise that it only reads them.

`$TELLTALE_HOME` (default `~/.telltale/`) holds `telltale.db` and `config.json` (design
6.9). Nothing here writes: a `load()` that dropped a default file on first read would be
exactly the edit the owner decision of 2026-09-01 forbids, under another name.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# The port `telltale daemon` binds when config.json does not name one. An orchestrator
# decision (W0-T5 brief), not a measurement and not an IANA registration.
DEFAULT_DAEMON_PORT = 47311


def home() -> Path:
    """`$TELLTALE_HOME`, or `~/.telltale`. Not created here."""
    return Path(os.environ.get("TELLTALE_HOME") or "~/.telltale").expanduser()


def db_path() -> Path:
    return home() / "telltale.db"


def load() -> dict[str, Any]:
    """`config.json` as a dict. No file is {}; a file that cannot be read is an error.

    Those are different facts and AGENTS.md's last invariant is about telling them
    apart: no file means nothing was configured, and a file this process cannot parse
    means something WAS configured and it cannot tell what. Returning {} for both would
    answer the second question with the first one's answer, quietly.
    """
    path = home() / "config.json"
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"{path}: {error}") from None
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: holds a {type(loaded).__name__}, not an object")
    return loaded
