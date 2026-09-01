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
    """`config.json` as a dict. Absent, unreadable or not an object all give {}."""
    try:
        loaded = json.loads((home() / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
