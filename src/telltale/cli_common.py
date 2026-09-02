"""What cli.py and cli_forecast.py share: the store, the capture lookup, the refusal.

Split out of cli.py on 2026-09-02 when it reached 791 lines against the 800-line
ratchet, so that the wave 2 tasks that each add a subcommand (experiment environment,
forecast readiness, compare) do not all move the same code first.
"""

from __future__ import annotations

from telltale import config
from telltale.store import Store

# Exit code for a refusal: the command exists, it ran, and it declined on purpose.
# Distinct from 1, which cli.py spends on a surface that did not round-trip.
REFUSED = 2


def store() -> Store:
    """The database the reading commands open.

    Not opened: `Store.open()` starts the writer thread, and most commands only read,
    which store.py does on its own read-only connection. A database that is not there
    is an error naming the path rather than an empty report, because "no captures" and
    "no database" are different answers to `telltale show`.
    """
    path = config.db_path()
    if not path.exists():
        raise SystemExit(f"{path}: no database. Run a capture, or set TELLTALE_HOME.")
    return Store(path)


def known(store: Store, capture_id: str) -> str:
    ids = [str(row["capture_id"]) for row in store.captures()]
    if capture_id in ids:
        return capture_id
    listed = ", ".join(ids) or "none"
    raise SystemExit(f"{capture_id}: no such capture. Stored: {listed}")


def refuse(reason: str) -> int:
    print(reason)
    return REFUSED
