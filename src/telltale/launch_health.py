"""What the store lost during a capture, said out loud. W6-T4.

Split from launch.py rather than written into it, for the reason facts.py and
launch_commits.py were: that file was at 734 lines against the 800-line ratchet. The
cut also follows a boundary worth having. Everything in launch.py reaches the database
through store.py's API, and this module is the one place that asks the FILE a question
sqlite3 alone can answer: is somebody else holding the write lock right now. It is the
only import of sqlite3 outside store.py, and keeping it here is what stops that import
from being in the launcher.

Nothing here writes an observation and nothing changes the child's exit code. It runs
inside `_guard` like every other step of the end of a capture (AGENTS.md invariant 8).
"""

from __future__ import annotations

import sqlite3
import sys
from typing import TYPE_CHECKING

from telltale.model import to_json

if TYPE_CHECKING:
    from pathlib import Path

    from telltale.store import Store

# The key store.py's drop counters use for a batch its writer gave up on, as against
# the surface names it uses for an observation a full queue refused (store._give_up
# against store._count_drops). Two different units in one map, so `lost` names which.
_GIVEN_UP = "store"


def lost(store: Store, capture_id: str) -> None:
    """Write a launcher diagnostic when the store lost part of this capture.

    The store counts what it could not write and design 6.5 puts those counters on
    /healthz, but a `telltale run` has no /healthz once it has exited, and the writer's
    own diagnostic cannot be written by the same connection that is failing to write.
    So a capture that lost its ending read exactly like a capture that was recorded and
    never reduced. Measured on 2026-09-03, a second process holding BEGIN IMMEDIATE on
    the store for 75 s while a run captured: the run returned the child's 0, three
    observations reached the disk, there was no capture_ended row, no activities, and
    ZERO diagnostics anywhere.

    The line on stderr is written only when the writer is `down`, which is the flag
    that says this diagnostic may not land either. A capture that dropped droppable
    metrics under a full queue has a healthy writer and the store's own `dropped` row
    is on the disk, so a line on the owner's terminal would be a second copy of
    something already recorded.
    """
    health = store.health()
    if not health["drops_total"]:
        return
    drops = dict(health["drops_by_surface"])
    detail = (
        f"the store lost part of this capture: {drops.get(_GIVEN_UP, 0)} batch(es)"
        f" the writer gave up on, drop counters {to_json(drops)};"
        f" {lock_state(store.path)}"
    )
    store.diagnose("launcher", detail, capture_id=capture_id)
    if health["down"]:
        sys.stderr.write(f"telltale: {detail}\n")


def lock_state(path: Path) -> str:
    """Whether another process holds the store's write lock at this instant.

    This is what lets the diagnostic NAME the lock instead of reporting a loss with no
    cause. Measured on 2026-09-03: a batch the writer cannot commit is given up 39.3 s
    after its first blocked attempt (six tries, each waiting store.BUSY_TIMEOUT_MS of
    5000 ms for the lock, plus store.RETRY_DELAYS_S between them and one per-job retry
    at the end), so by the time this is asked the two honest answers are "still held",
    which names the cause, and "free now", which does not and says so.

    busy_timeout 0 and an immediate rollback, for two reasons: this must not wait, and
    the write lock it holds for the length of one statement cannot cause the failure it
    is reporting on, which takes 5 s of continuous holding to provoke.
    """
    try:
        conn = sqlite3.connect(str(path), timeout=0)
    except sqlite3.Error as error:
        return f"the write lock on {path} could not be tested ({error!r})"
    try:
        conn.execute("PRAGMA busy_timeout = 0")
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
    except sqlite3.OperationalError as error:
        return f"another process holds the write lock on {path} ({error})"
    except sqlite3.Error as error:
        return f"the write lock on {path} could not be tested ({error!r})"
    finally:
        conn.close()
    return f"the write lock on {path} is free now, so nothing here can name the cause"
