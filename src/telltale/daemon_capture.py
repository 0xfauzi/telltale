"""The repository a DAEMON capture happened in, learned from the session's own cwd.

`telltale run` knows the repository before the agent starts: it is the launcher's own
working directory, and every observation of that capture carries its repo_id from the
first row. A session the owner starts by hand has no launcher, so until this module
existed every observation of a daemon capture carried repo_id NULL, every path in it
was outside the repository and therefore hashed, and no commit was ever linked to it.
Measured on cap_b0be0e0da48a5a1a78bdb8fa (Claude Code 2.1.263, 2026-09-06): a
PreToolUse that wrote hello.txt stored `"file_path":"<outside>/0cc7d242"`, and
`sessions --link-commits` skipped the capture because a NULL repo_id matches nothing.

What the daemon does have is the hook body, and every Claude Code hook carries the
session's `cwd`. So the first hook record of a capture is where the repository is
bound, and four properties of that binding are worth stating before the code:

  The ROOT is never stored. `repo.identity` hashes it into repo_id, which is the same
  identity the launcher writes (spec 12.1), and the root itself lives in this object
  for the daemon's lifetime and nowhere else. A path is a disclosure; a hash answers
  "the same checkout?" without being one.

  The root is spelled the way the SESSION spells it. `git rev-parse --show-toplevel`
  answers with every symlink resolved, and on macOS a session started under
  /var/folders reports its file paths under /var while git answers /private/var for the
  same tree (measured, macOS 25.6). sanitize.py touches no filesystem by design, so a
  repo_root in the other spelling would send every path of the session to `<outside>`
  and this module would have changed nothing. `--show-cdup` is git's answer in the
  caller's own spelling, and `_seen_root` walks up from there.

  Binding happens BEFORE the record is parsed. The hook body that carries `cwd` is the
  body whose own paths have to come out repo-relative, so the order is bind, then
  parse, not the other way round.

  Nothing here may change what the agent does. Every entry point is called on the
  request thread holding a hook open, and the snippet in providers/claude_launch.py
  declares a 5 second hook timeout, so each one either binds once, looks one thing up,
  or hands a job to a worker thread. The bind is the expensive one and a capture pays
  it exactly once: `repo.identity` measured 81 to 102 ms over three runs on the
  Telltale checkout and 94 to 101 ms on a one-commit repository, 2026-09-06, against
  that 5000 ms. What may never happen here is the linkage at session end, which walks
  `git rev-list` and reads the store, and is why `session_ended` starts a thread.
"""

from __future__ import annotations

import os
import threading
import time
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import launch_commits, repo, repo_link
from telltale.facts import text
from telltale.launch import SNAPSHOT_DEBOUNCE_S, _Capture, _guard, _snapshot
from telltale.model import now_iso
from telltale.sanitize import Ctx

if TYPE_CHECKING:
    from collections.abc import Callable

    from telltale.receiver import Receiver
    from telltale.store import Store

# How long `close()` waits for one session's linkage worker. The daemon calls it
# between stopping the receiver and closing the store, so this is the owner's Ctrl-C
# waiting for a `git rev-list` to finish; past it the linkage is lost and
# `sessions --link-commits` is the way back.
JOIN_TIMEOUT_S = 30.0


class Binder:
    """One repository per daemon capture, bound from the first hook that names a cwd.

    The receiver owns none of this: it calls `observe` for every record it attributes,
    `ctx` for the sanitizer context of each one, and `repo_changed` when a record says
    the working tree moved. A daemon with no Binder behaves exactly as it did before,
    which is what keeps the launcher's path (it binds through `bind_capture` itself)
    unchanged.
    """

    def __init__(
        self,
        store: Store,
        receiver: Receiver,
        level: int = 1,
        on_bind: Callable[[str, str], None] | None = None,
    ) -> None:
        self.store = store
        self.receiver = receiver
        self.level = level
        self._on_bind = on_bind
        self._lock = threading.Lock()
        self._bound: dict[str, _Capture] = {}
        # Three sets, and the differences between them are the point. `_outside` is a
        # capture whose cwd is in no working tree, which is a fact about that session
        # and not a failure, so it is remembered and never probed again. `_seen` is
        # every capture ever bound, so a record arriving after a session end rebinds
        # without writing a second identity. `_ending` stops a second SessionEnd from
        # starting a second linkage worker over the same capture.
        self._outside: set[str] = set()
        self._seen: set[str] = set()
        self._ending: set[str] = set()
        self._workers: list[threading.Thread] = []
        # One Ctx for every unbound capture, built once: the receiver asks for one on
        # every record, and each Ctx() reads and normalizes the home directory.
        self._nowhere = Ctx()

    # -- what the receiver calls ------------------------------------------------------

    def observe(self, capture_id: str, surface: str, provider: str, raw: Any) -> None:
        """One record, before it is parsed. Binds a repository and ends a session.

        `provider` is here because the `captures` view reads a capture's provider off
        its EARLIEST observation, and the identity row this writes is earlier than the
        hook that caused it. Recording it as anything but the provider that sent the
        hook would rename every daemon capture in `telltale sessions`.
        """
        if surface != "hook" or not isinstance(raw, dict):
            return
        with self._lock:
            known = capture_id in self._bound or capture_id in self._outside
        if not known:
            self._bind(capture_id, provider, raw)
        if raw.get("hook_event_name") == "SessionEnd":
            self.session_ended(capture_id)

    def ctx(self, capture_id: str) -> Ctx:
        """The sanitizer context for one capture: its repository, or nowhere."""
        with self._lock:
            capture = self._bound.get(capture_id)
        return self._nowhere if capture is None else capture.ctx

    def repo_changed(self, capture_id: str, trigger: str) -> None:
        """A record said the working tree moved. Schedule the snapshot and return.

        Called on the request thread that is holding an agent's hook open, exactly as
        the launcher's wiring is (launch.py `_wire`), so it may only schedule: the
        debouncer starts a timer and returns.
        """
        with self._lock:
            capture = self._bound.get(capture_id)
        if capture is None or capture.debouncer is None:
            return
        capture.debouncer.trigger(trigger)

    def session_ended(self, capture_id: str) -> None:
        """Link this session's commits, on a worker thread, and forget the capture.

        Not on the request thread: the provider_reported ids come back out of the store,
        so linkage waits for a flush and then walks `git rev-list`, and the hook that
        asked for it has 5 seconds. The capture stays bound until the worker is done,
        so the SessionEnd record that triggered this still has its own paths
        relativized on the way through.
        """
        with self._lock:
            capture = self._bound.get(capture_id)
            if capture is None or capture_id in self._ending:
                return
            self._ending.add(capture_id)
            worker = threading.Thread(
                target=self._link, args=(capture,), name="telltale-link", daemon=True
            )
            self._workers.append(worker)
        worker.start()

    def close(self, timeout: float = JOIN_TIMEOUT_S) -> None:
        """Wait for the linkage workers a session end started. The daemon's last step
        before it closes the store."""
        with self._lock:
            workers = list(self._workers)
        for worker in workers:
            worker.join(timeout=timeout)

    # -- binding ----------------------------------------------------------------------

    def _bind(self, capture_id: str, provider: str, raw: dict[str, Any]) -> None:
        """Give this capture its repository, or record that it has none.

        `_seen_root` runs before `repo.identity` so that a session outside a checkout
        pays one refused git call rather than the eight identity makes. The check under
        the lock is the authoritative one: two hook records of one session can arrive
        together, and only one of them may write the identity.
        """
        cwd = raw.get("cwd")
        if not isinstance(cwd, str) or not cwd.strip():
            return
        root = _seen_root(Path(cwd))
        identity = {} if root is None else repo.identity(root)
        repo_id = text(identity.get("repo_id"))
        with self._lock:
            if capture_id in self._bound or capture_id in self._outside:
                return
            if root is None or repo_id is None:
                self._outside.add(capture_id)
                return
            capture = _open(
                capture_id, provider, root, repo_id, identity, raw, self.store,
                self.level,
            )  # fmt: skip
            self._bound[capture_id] = capture
            first = capture_id not in self._seen
            self._seen.add(capture_id)
            # Inside the lock: a record arriving on another thread right now reads its
            # repo_id out of the receiver, and the window where the capture is bound
            # here and not there would be a row with a NULL repository.
            self.receiver.bind_capture(capture_id, repo_id, None)
        if first:
            capture.emit("telltale.repo.identity", identity)
        if self._on_bind is not None:
            self._on_bind(capture_id, repo_id)

    def _link(self, capture: _Capture) -> None:
        """The worker `session_ended` starts: last snapshot, commits, reduce, forget."""
        with _guard(capture, "session end linkage"):
            if capture.debouncer is not None:
                capture.debouncer.flush()
            self.store.flush()
            _snapshot(capture, repo_link.CAPTURE_END)
            found = launch_commits.commits(capture)
            self.store.flush()
            if found:
                # Importing the reducers is what registers them (design 6.10), and a
                # commit that is only an observation leaves the change clock saying
                # this session landed nothing. Same step as launch.py `_reduce`.
                from telltale import measures  # noqa: F401

                self.store.rebuild(capture.capture_id)
        with self._lock:
            self._bound.pop(capture.capture_id, None)
            self._ending.discard(capture.capture_id)


def _open(
    capture_id: str,
    provider: str,
    root: Path,
    repo_id: str,
    identity: dict[str, Any],
    raw: dict[str, Any],
    store: Store,
    level: int,
) -> _Capture:
    """The launcher's own capture object, for a capture the launcher did not start.

    The same `_Capture` `launch_commits.link_commits` builds, so snapshots, commit
    linkage and the emit path are one implementation rather than two.

    started_at is now, and that is a claim about Telltale rather than about the
    session: the first hook is the earliest moment a daemon can prove the session
    existed. It bounds the tree-match rungs of spec 12.3 only, because a sha the
    provider stated is kept whatever the clock says (repo_link.commits_since).
    """
    capture = _Capture(
        capture_id=capture_id,
        provider=provider,
        level=level,
        cwd=root,
        store=store,
        ctx=Ctx(repo_root=root),
        started_at=now_iso(),
        started_ns=time.monotonic_ns(),
        repo_id=repo_id,
        worktree_id=text(identity.get("worktree_id")),
        session_id=text(raw.get("session_id")),
    )
    capture.debouncer = repo.Debouncer(partial(_snapshot, capture), SNAPSHOT_DEBOUNCE_S)
    return capture


def _seen_root(cwd: Path) -> Path | None:
    """The working tree root, spelled the way the session spells its own paths.

    `--show-cdup` is the relative path from cwd up to the root: the empty string at the
    root itself, `../` one directory down. Walking up from the cwd the hook reported
    keeps the session's spelling, where `--show-toplevel` would hand back a path with
    every symlink resolved. None when git refused, which is one answer for "not a
    working tree", "no git" and "a bare repository", and the right one for all three:
    there is no root here to bind. None too for a cwd that is not absolute, because the
    sanitizer's root must be, and a relative cwd names no place on its own.
    """
    if not cwd.is_absolute():
        return None
    cdup = repo.git_line(cwd, "rev-parse", "--show-cdup")
    if cdup is None:
        return None
    return Path(os.path.normpath(cwd / cdup))
