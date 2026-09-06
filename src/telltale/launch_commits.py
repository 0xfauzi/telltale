"""Which commits a CAPTURE writes down, and the one command that runs linkage again.

Split out of launch.py, which was at the 800-line ratchet. repo_link.py answers "did
this session make that commit" and writes nothing; this module is the other half of
that sentence: it takes the answer, emits one `telltale.repo.commit` observation per
commit through the capture that owns it, and is therefore the only place linkage
touches the store. launch.py keeps the process it launched.

The import of launch.py is deferred into `link_commits` on purpose. launch.py imports
this module at the top, so the one direction that may exist at import time is that one;
`link_commits` builds the launcher's own `_Capture` to write through, which is what kept
this function in launch.py until now, and a function-level import is what lets the two
live apart without a cycle. Same shape as allowlist.py's deferred import of
allowlist_telltale.py, and for the same reason.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from telltale import repo, repo_link
from telltale.facts import facts, text
from telltale.sanitize import Ctx

if TYPE_CHECKING:
    from telltale.facts import Facts
    from telltale.launch import _Capture
    from telltale.store import Store


def commits(capture: _Capture) -> int:
    """Link commits to this capture and emit one observation each. Spec 12.3.

    provider_reported ids come out of the store, so this runs after the flush that
    committed the child's own records; explicit ids are what `--commit` stated, and a
    statement outranks every clock in repo_link.commits_since.

    ended_at is None while the capture is still running, which is what makes the window
    end `now` at capture end and the capture's real end on a later relink.
    """
    found = repo_link.commits_since(
        capture.cwd,
        capture.started_at,
        capture.snapshots,
        provider_reported=repo_link.reported_commits(capture.store, capture.capture_id),
        explicit=capture.explicit_commits,
        until_ts=capture.ended_at,
    )
    for payload in found:
        capture.emit("telltale.repo.commit", payload)
    return len(found)


# -- relinking a stored capture -------------------------------------------------------


def link_commits(
    store: Store, capture_id: str, level: int = 1, root: Path | None = None
) -> int:
    """Run commit linkage again for one stored capture. Returns commits added.

    `sessions --link-commits` exists because linkage at capture end can only see the
    commits that exist at capture end, and spec 12.3's tree_match_after rung is about
    a commit made in the ten minutes AFTER it. This is the same function the launcher
    runs, with the capture's real end as the window's end instead of now.

    The repository is `root` when a caller names one and the current working directory
    otherwise, and a capture whose repo_id is not that repository's is skipped rather
    than linked: a capture stores the sha256 of its root and not the root, so there is
    no way back from an id to a checkout, and linking against whatever directory the
    command was run in would be an invented attribution. A capture with no repo_id at
    all is skipped too, because "no repository" and "this repository" are not the same
    answer and `None == None` would make them one.
    """
    # See the module docstring: launch.py imports this module at the top, so this
    # direction is the deferred one. By the time any caller reaches here, launch.py has
    # finished importing.
    from telltale.launch import GENERIC, _Capture

    where = Path.cwd() if root is None else Path(root)
    known = facts(store, capture_id)
    started, repo_id = _window_start(store, capture_id, known)
    if started is None or repo_id is None or repo_id != repo.identity(where)["repo_id"]:
        return 0
    top = repo.git_root(where)
    found = commits(
        _Capture(
            capture_id=capture_id,
            provider=GENERIC,
            level=level,
            cwd=where,
            store=store,
            ctx=Ctx(repo_root=None if top is None else Path(top).resolve()),
            started_at=started,
            started_ns=time.monotonic_ns(),
            repo_id=repo_id,
            snapshots=known.snapshots,
            ended_at=known.ended_at,
        )
    )
    store.flush()
    if found:
        # The launcher reduces a capture at its end (W2-T6); a commit appended later
        # would otherwise sit in observations while activities, and so the change
        # clock, keep saying the capture has no commit.
        store.rebuild(capture_id)
    return found


def _window_start(
    store: Store, capture_id: str, known: Facts
) -> tuple[str | None, str | None]:
    """(started_at, repo_id) for any capture, launched or derived by a daemon.

    `facts` reads both off `telltale.capture_started`, and only the launcher writes
    one. A daemon capture has none, so both would be None and this whole function
    would skip it: that is the defect W9-T1 removes, and it is why the fallbacks are
    here rather than in facts.py, which answers about a capture rather than about
    linkage.

    The fallbacks say the least that is true. The first record to arrive is the
    earliest moment Telltale can prove the session existed, so it is the window start;
    a stated sha is kept whatever the window says (repo_link.commits_since), so this
    bounds the tree-match rungs alone. The first non-null repo_id column is the
    repository daemon_capture bound the session to, which is the same reading the
    `captures` view already takes for its own repo_id column.
    """
    if known.started_at is not None and known.repo_id is not None:
        return known.started_at, known.repo_id
    started, repo_id = known.started_at, known.repo_id
    for row in store.observations(capture_id):
        started = started or str(row["ingest_ts"])
        repo_id = repo_id or text(row["repo_id"])
        if started is not None and repo_id is not None:
            break
    return started, repo_id
