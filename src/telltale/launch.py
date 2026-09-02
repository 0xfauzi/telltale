"""`telltale run`: one child process, recorded, and nothing about it changed. 6.9.

The launcher is the only place capture is configured, which is the whole of the owner
decision of 2026-09-01: what reaches the agent reaches it as argv and environment of the
process this module starts, and nothing survives the capture on disk. AGENTS.md
invariant 7.

Three properties are worth stating before the code, because each one is a decision that
looks like an omission:

  There is no linger after the child exits. E01 and E02 measured no late export on
  either provider: the last request arrives 0.24 to 0.56 s BEFORE the child's exit, on
  all 13 exporting scenarios. So the wait at the end is `Store.flush`, which returns
  when everything the receiver ALREADY accepted is committed, and not a timer.

  SIGINT is absorbed, not forwarded. See `_signals`.

  Every step here runs inside `_guard`. A capture that fails is a diagnostics row of
  kind launcher; it is never a changed exit code, a changed byte of the child's output
  or a changed thing the child does. AGENTS.md invariant 8, and it is why this module
  returns the child's exit code from `run` rather than raising anything of its own.
"""

from __future__ import annotations

import contextlib
import http.client
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import config, env, providers, repo
from telltale.model import Observation, new_id, now_iso, to_json, ulid
from telltale.providers import LaunchPlan
from telltale.receiver import Receiver
from telltale.sanitize import MAX_PAYLOAD_BYTES, Ctx, sanitize
from telltale.store import Store

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterator, Mapping, Sequence
    from types import FrameType

# The adapter every observation this module writes carries. It says who produced the
# row, which for a launcher record is Telltale itself rather than any provider.
ADAPTER = "telltale@1"
SURFACE = "launcher"

# The provider name for a child that is not one of the agents we have a module for.
# It is a real name in the provider column rather than None, because "we watched
# something we have no parser for" is a different fact from "we do not know what ran".
GENERIC = "generic"

# Design 6.6: a burst of edits is one piece of work, so snapshots are debounced. W0-T3
# left the seconds to the caller and nothing has measured how a real session's edits
# are spaced, so this is the design's number.
SNAPSHOT_DEBOUNCE_S = 2.0

# The most per-file entries a snapshot observation carries. W0-T2 measured that the
# 8 KB payload bound drops the largest FIELD whole, and in a snapshot that is per_file:
# without a cap a 300-file change stores no per-file record at all.
PER_FILE_MAX = 100

# A POST of one teed stdout line. The receiver is in this process and answers
# unconditionally (spec 5.2), so this bound is for a receiver that has stopped
# answering rather than one that is slow: past it, the line is a diagnostic.
_STREAM_TIMEOUT_S = 5.0

# What `telltale run` returns when there is nothing to run, and when the command does
# not exist. 127 is what a shell returns for a command it cannot find, and the whole
# contract of this program is that the caller sees what it would have seen.
_NO_COMMAND = 2
_NOT_FOUND = 127


@dataclass
class _Capture:
    """One capture in flight: the ids that go on every row, and what wrote them."""

    capture_id: str
    provider: str
    level: int
    cwd: Path
    store: Store
    ctx: Ctx
    started_at: str
    started_ns: int
    receiver: Receiver | None = None
    port: int = 0
    repo_id: str | None = None
    worktree_id: str | None = None
    fingerprint_id: str | None = None
    session_id: str | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    explicit_commits: list[str] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    debouncer: repo.Debouncer | None = None

    def emit(self, obs_type: str, payload: Mapping[str, Any]) -> None:
        """Sanitize one Telltale payload and queue it. The launcher's only write.

        Through the same three gates a provider record passes (design 6.4), because the
        launcher's own payloads carry repository paths and an allowlist that is only
        applied to somebody else's records is an allowlist with a hole in it.
        """
        body, redaction, unknown = sanitize(
            obs_type, dict(payload), self.level, self.ctx
        )
        self.store.append(
            [
                Observation(
                    observation_id=ulid(),
                    capture_id=self.capture_id,
                    observation_type=obs_type,
                    surface=SURFACE,
                    provider=self.provider,
                    adapter=ADAPTER,
                    ingest_ts=now_iso(),
                    provider_session_id=self.session_id,
                    environment_fingerprint_id=self.fingerprint_id,
                    repo_id=self.repo_id,
                    payload=body,
                    redaction=redaction,
                )
            ]
        )
        if unknown:
            self.store.diagnose(
                "unknown_field",
                f"{obs_type}: {' '.join(sorted(unknown))}",
                capture_id=self.capture_id,
            )


@contextlib.contextmanager
def _guard(capture: _Capture | None, what: str) -> Iterator[None]:
    """Whatever fails inside becomes a diagnostics row, and nothing else. Invariant 8.

    `Exception`, not `BaseException`: a KeyboardInterrupt is the owner asking for this
    process to stop, and swallowing it here would be the launcher deciding it knows
    better.
    """
    try:
        yield
    except Exception as error:
        if capture is None:
            return
        with contextlib.suppress(Exception):
            capture.store.diagnose(
                "launcher", f"{what}: {error!r}", capture_id=capture.capture_id
            )


# -- the command ----------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    """Record one child process. Returns the child's exit code, whatever happened here.

    The order is fixed by what each step needs: the receiver has to be listening before
    the plan can name its port, and the plan has to exist before capture_started can
    say which surfaces were configured (design 6.7, coverage).
    """
    argv = list(args.argv)
    if not argv:
        sys.stderr.write("telltale run: name a command after --\n")
        return _NO_COMMAND
    provider = _provider(args.provider, argv)
    capture = _open(args, provider)
    code: int | None = None
    try:
        plan = _plan(capture, argv, provider)
        _begin(capture, args, argv, plan)
        code = _child(plan, capture)
        return code
    finally:
        _finish(capture, code)


def _provider(name: str, argv: Sequence[str]) -> str:
    """`--provider`, or the child's own name when it is `auto`. Design 6.9."""
    if name != "auto":
        return name
    executable = Path(argv[0]).name
    return executable if executable in providers.KNOWN else GENERIC


def _open(args: argparse.Namespace, provider: str) -> _Capture | None:
    """The store, the receiver and the ids, or None and a child that runs unrecorded.

    None is the fail-open path (AGENTS.md invariant 8) and it costs one line on stderr,
    which is the only place left to say it: a store that would not open has nowhere to
    put a diagnostic, and the alternative is a session the owner believes was recorded.
    """
    store: Store | None = None
    try:
        store = Store(config.db_path()).open()
        root = repo.git_root(Path.cwd())
        capture = _Capture(
            capture_id=new_id("cap"),
            provider=provider,
            level=args.level,
            cwd=Path.cwd(),
            store=store,
            ctx=Ctx(repo_root=None if root is None else Path(root).resolve()),
            started_at=now_iso(),
            started_ns=time.monotonic_ns(),
            session_id=str(uuid.uuid4()),
        )
        capture.receiver = Receiver(
            store,
            level=args.level,
            ctx_for_capture=lambda _capture: capture.ctx,
            provider=provider,
            default_capture=capture.capture_id,
        )
        capture.port = capture.receiver.start()
    except Exception as error:
        if store is not None:
            with contextlib.suppress(Exception):
                store.close()
        sys.stderr.write(f"telltale: not recording this run: {error!r}\n")
        return None
    return capture


def _plan(capture: _Capture | None, argv: list[str], provider: str) -> LaunchPlan:
    """The provider's launch plan, or the child's own argv for a provider without one.

    A provider module that does not exist yet, or one whose launch() refuses, is not a
    reason to change what the owner asked to run. The capture continues with the
    surfaces that need no configuration at all: the repository and the environment.

    `generic` takes that path without a diagnostic, because it is not a failure: it is
    what the owner asked for by wrapping something that is not an agent. A diagnostics
    row on every `telltale run -- make test` would teach a reader to ignore the table
    that is supposed to be worth reading.
    """
    bare = LaunchPlan(argv=list(argv), env={}, tee=False)
    if capture is None or provider == GENERIC:
        return bare
    try:
        return providers.get(provider).launch(
            argv, capture.port, capture.level, capture.session_id, capture.capture_id
        )
    except Exception as error:
        capture.store.diagnose(
            "launcher",
            f"provider {provider} has no launch plan ({error!r}): capturing the"
            " repository and the environment only",
            capture_id=capture.capture_id,
        )
        return bare


def _begin(
    capture: _Capture | None,
    args: argparse.Namespace,
    argv: Sequence[str],
    plan: LaunchPlan,
) -> None:
    """Emit what is true before the child starts, and wire the receiver to it."""
    if capture is None:
        return
    with _guard(capture, "capture start"):
        # The session id is only OURS if the plan actually asked for it: claude adds
        # `--session-id` in -p mode alone, and a provider that ignores it must not
        # leave the receiver holding a binding no record will ever carry.
        capture.session_id = (
            capture.session_id if capture.session_id in plan.argv else None
        )
        capture.explicit_commits = list(args.commit or ())
        capture.identity = repo.identity(capture.cwd)
        capture.repo_id = _text(capture.identity.get("repo_id"))
        capture.worktree_id = _text(capture.identity.get("worktree_id"))
        capture.emit("telltale.repo.identity", capture.identity)
        _environment(capture, argv, plan)
        capture.emit("telltale.capture_started", _started(capture, args, argv, plan))
        if capture.receiver is not None:
            _wire(capture, capture.receiver)


def _environment(capture: _Capture, argv: Sequence[str], plan: LaunchPlan) -> None:
    """The environment fingerprint, and the id every later observation carries.

    Fingerprinted from the argv the OWNER wrote, not from the plan's: the flags this
    launcher adds describe the recording, and capture_modes below is where they belong.
    runtime_version stays None. Reading it costs a process spawn per capture, and the
    provider's own records carry service.version (W0-T4), so it is filled in by the
    parser rather than paid for here.
    """
    fingerprint = env.fingerprint(
        capture.provider,
        argv,
        capture.cwd,
        extra={"capture_modes": list(plan.surfaces), "content_level": capture.level},
    )
    capture.fingerprint_id = _text(fingerprint.get("fingerprint_id"))
    payload = {
        name: value for name, value in fingerprint.items() if name != "fingerprint_id"
    }
    capture.emit("telltale.environment", payload)


def _started(
    capture: _Capture,
    args: argparse.Namespace,
    argv: Sequence[str],
    plan: LaunchPlan,
) -> dict[str, Any]:
    """The capture_started payload: design 6.3's fields, plus env_removed.

    `surfaces_configured` is what the plan CONFIGURED, which the activities reducer
    reads against the surfaces that delivered to compute coverage (design 6.7): a
    surface that was never configured and one that stayed silent are different facts.
    `env_removed` names what was taken out of the child's environment, which is E01
    finding 6 and the one change this launcher makes to a process it is recording.
    """
    return {
        "provider": capture.provider,
        "argv_shape": _argv_shape(argv),
        "content_level": capture.level,
        "surfaces_configured": list(plan.surfaces),
        "provider_session_id_requested": capture.session_id,
        "task_id": args.task_id,
        "attempt": args.attempt,
        "experiment": args.experiment,
        "worktree_id": capture.worktree_id,
        "env_removed": [name for name in plan.env_remove if name in os.environ],
    }


def _argv_shape(argv: Sequence[str]) -> list[str]:
    """The executable's name and the flag names. Never a flag's value. Design 6.3.

    `-c` and its script, `-p` and its prompt: the values are content this system does
    not store, and the flags are what says what kind of run this was.
    """
    flags = [token.split("=", 1)[0] for token in argv[1:] if token.startswith("-")]
    return [Path(argv[0]).name, *flags]


def _wire(capture: _Capture, receiver: Receiver) -> None:
    """Attach the capture's ids to later records, and its snapshots to file edits."""
    receiver.bind_capture(capture.capture_id, capture.repo_id, capture.fingerprint_id)
    if capture.session_id is not None:
        receiver.register_session(
            capture.session_id, capture.capture_id, capture.provider
        )
    debouncer = repo.Debouncer(
        partial(_snapshot, capture, "file_mutation"), SNAPSHOT_DEBOUNCE_S
    )
    capture.debouncer = debouncer
    # The receiver calls this on the request thread that is holding an agent's hook
    # open, so it may only schedule work. trigger() starts a timer and returns.
    receiver.on_file_mutation(lambda _observation: debouncer.trigger())


# -- the child ------------------------------------------------------------------------


def _child(plan: LaunchPlan, capture: _Capture | None) -> int:
    """Run the child to completion and return the code a shell would have seen.

    stdin and stderr are inherited untouched. stdout is inherited too unless the plan
    asked to tee it, which it only does when the child's own argv asked for JSON
    output: teeing a human-readable stream would put a recorder between an agent and
    its terminal for no observation in return.
    """
    try:
        # Never shell=True (design 6.8): the argv is the owner's, and a branch name
        # or a path out of a repository under observation must not become a command.
        child = subprocess.Popen(
            plan.argv,
            env=_child_env(plan),
            stdout=subprocess.PIPE if plan.tee else None,
        )
    except OSError as error:
        sys.stderr.write(f"telltale run: {plan.argv[0]}: {error.strerror}\n")
        return _NOT_FOUND
    with _signals(child):
        if child.stdout is not None:
            _tee(child.stdout, capture)
        code = child.wait()
    # A child killed by signal N is -N here and 128 + N to a shell. The two spellings
    # of one fact, and the shell's is the one the caller of `telltale run` compares.
    return code if code >= 0 else 128 - code


def _child_env(plan: LaunchPlan) -> dict[str, str]:
    """The parent's environment plus the plan's, minus the names it removes.

    Removed rather than emptied: E01 measured a child that inherited VIRTUAL_ENV from
    `uv run` failing to run `uv run pytest` in its own repository, and an empty
    VIRTUAL_ENV is a second wrong answer rather than no answer.
    """
    child = {**os.environ, **plan.env}
    for name in plan.env_remove:
        child.pop(name, None)
    return child


@contextlib.contextmanager
def _signals(child: subprocess.Popen[bytes]) -> Iterator[None]:
    """Keep the launcher alive until the child is done, and pass SIGTERM through.

    SIGTERM: a terminal never sends one, so a SIGTERM that arrives here was aimed at
    this process and the child would otherwise never hear it. Forwarded, then waited
    for, so the code the caller sees is still the child's.

    SIGINT is absorbed and NOT forwarded, which is a decision rather than an omission.
    Nothing here starts a new session, so the child is in this process's group and the
    tty delivers Ctrl-C to it directly; a second copy from here would be a second
    Ctrl-C, and in Claude Code the first cancels the turn while the second quits the
    session. That is the child behaviour change invariant 8 forbids. The cost is real
    and stated in docs/log/W1-T1.md: `kill -INT <launcher pid>` alone reaches the
    launcher and not the child.

    A handler function, never SIG_IGN: an ignored signal is inherited across exec and
    would leave the child unkillable from the keyboard, while a handler is reset to the
    default in the child.
    """
    previous: list[tuple[signal.Signals, Any]] = []
    for number, handler in (
        (signal.SIGTERM, partial(_relay, child)),
        (signal.SIGINT, _absorb),
    ):
        # ValueError: not the main thread, which no caller of run() is today.
        with contextlib.suppress(ValueError, OSError):
            previous.append((number, signal.signal(number, handler)))
    try:
        yield
    finally:
        for signalled, restored in previous:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(signalled, restored)


def _relay(
    child: subprocess.Popen[bytes], number: int, _frame: FrameType | None
) -> None:
    with contextlib.suppress(OSError):
        child.send_signal(number)


def _absorb(_number: int, _frame: FrameType | None) -> None:
    """Received, and deliberately not acted on. See _signals."""


def _tee(stream: Any, capture: _Capture | None) -> None:
    """Echo every line the child writes, unchanged, then record it.

    Echo FIRST and byte for byte: a consumer of `--output-format stream-json` must see
    the child's own bytes in the child's own order, so nothing here re-serializes a
    line and nothing delays one for the sake of recording it. A line that fails to
    reach the receiver is a diagnostic, never a missing line of output.
    """
    out = sys.stdout.buffer
    post = _Stream(capture)
    try:
        for line in stream:
            out.write(line)
            out.flush()
            post.send(line)
    finally:
        post.close()


class _Stream:
    """One keep-alive connection to /v1/stream/<provider>, reopened when it breaks.

    A connection per line would be a TCP handshake per line of the child's output. The
    receiver speaks HTTP/1.1 with a Content-Length on every answer, so one connection
    carries the whole session.
    """

    def __init__(self, capture: _Capture | None) -> None:
        self._capture = capture
        self._path = "" if capture is None else f"/v1/stream/{capture.provider}"
        self._connection: http.client.HTTPConnection | None = None

    def send(self, line: bytes) -> None:
        capture = self._capture
        if capture is None or not line.strip():
            return
        # Twice: the first failure of a keep-alive connection is the connection, not
        # the receiver, and reopening costs one handshake against losing a line.
        for last in (False, True):
            try:
                self._post(capture, line)
                return
            except (OSError, http.client.HTTPException) as error:
                self.close()
                if last:
                    self._diagnose(capture, error)

    def _post(self, capture: _Capture, line: bytes) -> None:
        if self._connection is None:
            self._connection = http.client.HTTPConnection(
                "127.0.0.1", capture.port, timeout=_STREAM_TIMEOUT_S
            )
        self._connection.request(
            "POST", self._path, body=line, headers={"Content-Type": "application/json"}
        )
        self._connection.getresponse().read()

    def _diagnose(self, capture: _Capture, error: Exception) -> None:
        with contextlib.suppress(Exception):
            capture.store.diagnose(
                "launcher", f"stream tee: {error!r}", capture_id=capture.capture_id
            )

    def close(self) -> None:
        if self._connection is not None:
            with contextlib.suppress(Exception):
                self._connection.close()
            self._connection = None


# -- the end of a capture -------------------------------------------------------------


def _finish(capture: _Capture | None, code: int | None) -> None:
    """Close the capture: last snapshot, commit links, capture_ended, flush, stop.

    The receiver stops FIRST, and that is what makes the rest of this correct: the
    child is gone, so nothing can post again, and the debounced snapshot below cannot
    be raced by a mutation arriving behind it. No linger: E01 and E02 measured the last
    request arriving before the child exits on every exporting scenario, so what would
    be waited for has already been accepted, and flush() is what proves it is on disk.
    """
    if capture is None:
        return
    if capture.receiver is not None:
        with _guard(capture, "receiver stop"):
            capture.receiver.stop()
    with _guard(capture, "pending snapshot"):
        if capture.debouncer is not None:
            capture.debouncer.flush()
    capture.store.flush()
    with _guard(capture, "final snapshot"):
        _snapshot(capture, "capture_end")
    with _guard(capture, "commit linkage"):
        _commits(capture)

    with _guard(capture, "capture end"):
        capture.emit("telltale.capture_ended", _ended(capture, code))
    capture.store.flush()
    capture.store.close()


def _ended(capture: _Capture, code: int | None) -> dict[str, Any]:
    received = {} if capture.receiver is None else capture.receiver.counts()
    return {
        # None, never 0: a capture whose child never started has no exit code, and 0
        # is the code that means it succeeded.
        "exit_code": code,
        "duration_ms": (time.monotonic_ns() - capture.started_ns) // 1_000_000,
        "surfaces_received": received,
        "worktree_id": capture.worktree_id,
    }


def _snapshot(capture: _Capture, trigger: str) -> None:
    """One repo.snapshot observation, and the payload kept for commit linkage."""
    payload = repo.snapshot(capture.cwd, trigger)
    capture.snapshots.append(payload)
    capture.emit("telltale.repo.snapshot", _capped(payload, capture.level))


def _capped(payload: Mapping[str, Any], level: int = 1) -> dict[str, Any]:
    """Cut per_file until the payload fits, rather than letting it be dropped whole.

    W0-T2 measured that the 8 KB bound of design 6.4 drops the LARGEST FIELD whole, and
    in a snapshot that is per_file: a 300-file change would store no per-file record at
    all. So the list is cut here, first to PER_FILE_MAX and then to what fits, and
    per_file_truncated says the list is a prefix. files_changed still carries the true
    count, so nothing about the SIZE of the change is lost by the cut.

    Measured against the payload before sanitization, which is at least as large as the
    one that will be stored: a path only ever gets shorter when it is made relative.
    """
    entries = payload.get("per_file")
    if not isinstance(entries, list):
        return dict(payload)
    if level <= 0:
        # Level 0 keeps no path at all (design 6.4), and every field of a per_file
        # entry is cleaned as a path, so what survives the sanitizer is a list of
        # empty objects: measured, four changed files gave [{}, {}, {}, {}]. None
        # says "not recorded here", which is the truth; four empty objects say
        # nothing at all while looking like data. files_changed keeps the count.
        return {**payload, "per_file": None}
    kept = list(entries[:PER_FILE_MAX])
    while True:
        out = {**payload, "per_file": kept}
        if len(kept) < len(entries):
            out["per_file_truncated"] = True
        if not kept or len(to_json(out).encode("utf-8")) <= MAX_PAYLOAD_BYTES:
            return out
        kept.pop()


def _commits(capture: _Capture) -> None:
    """Link commits to this capture and emit one observation each. Spec 12.3.

    provider_reported ids come out of the store, so this runs after the flush that
    committed the child's own records; explicit ids are what `--commit` stated, and a
    statement outranks every clock in repo.commits_since.
    """
    found = repo.commits_since(
        capture.cwd,
        capture.started_at,
        capture.snapshots,
        provider_reported=reported_commits(capture.store, capture.capture_id),
        explicit=capture.explicit_commits,
    )
    for payload in found:
        capture.emit("telltale.repo.commit", payload)


def reported_commits(store: Store, capture_id: str) -> list[str]:
    """Every git_commit_id a provider reported inside this capture, in arrival order.

    Read back out of the store rather than counted on the way in, because the field
    arrives on a provider's own records (design 6.3: a git_commit_id lifted out of
    tool_parameters) and this module never sees one. The caller flushes first.
    """
    seen = [
        str(row["payload"]["git_commit_id"])
        for row in store.observations(capture_id)
        if row["payload"].get("git_commit_id")
    ]
    return list(dict.fromkeys(seen))


# -- reading a stored capture ---------------------------------------------------------


@dataclass
class Facts:
    """What one stored capture says about itself, from its telltale.* observations."""

    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    model: str | None = None
    repo_id: str | None = None
    worktree_id: str | None = None
    surfaces_configured: list[str] = field(default_factory=list)
    surfaces_received: dict[str, int] = field(default_factory=dict)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    commits: int = 0

    def coverage(self) -> str | None:
        """`delivered/configured` surfaces, or None when no plan configured any.

        None rather than 0/0: a capture with no launch plan (a generic child, or a
        provider module that has none yet) configured nothing, and "0 of 0 surfaces
        delivered" reads like a failure of something that was never attempted.
        """
        if not self.surfaces_configured:
            return None
        delivered = [
            name
            for name in self.surfaces_configured
            if self.surfaces_received.get(name)
        ]
        return f"{len(delivered)}/{len(self.surfaces_configured)}"


def facts(store: Store, capture_id: str) -> Facts:
    """One pass over a capture's observations for everything a reader asks of it."""
    out = Facts()
    for row in store.observations(capture_id):
        _read(out, str(row["observation_type"]), row)
    return out


def _read(out: Facts, obs_type: str, row: Mapping[str, Any]) -> None:
    payload = row["payload"]
    if obs_type == "telltale.capture_started":
        out.started_at = str(row["ingest_ts"])
        out.repo_id = _text(row["repo_id"])
        out.worktree_id = _text(payload.get("worktree_id"))
        out.surfaces_configured = [
            str(name) for name in payload.get("surfaces_configured") or ()
        ]
    elif obs_type == "telltale.capture_ended":
        out.ended_at = str(row["ingest_ts"])
        out.duration_ms = _whole(payload.get("duration_ms"))
        out.exit_code = _whole(payload.get("exit_code"))
        out.surfaces_received = {
            str(name): int(count)
            for name, count in (payload.get("surfaces_received") or {}).items()
        }
    elif obs_type == "telltale.environment":
        out.model = _text(payload.get("model"))
    elif obs_type == "telltale.repo.snapshot":
        out.snapshots.append(dict(payload))
    elif obs_type == "telltale.repo.commit":
        out.commits += 1


def link_commits(store: Store, capture_id: str, level: int = 1) -> int:
    """Run commit linkage again for one stored capture. Returns commits added.

    `sessions --link-commits` exists because linkage at capture end can only see the
    commits that exist at capture end, and spec 12.3's tree_match_after rung is about
    a commit made in the ten minutes AFTER it. This is the same function the launcher
    runs, with the capture's real end as the window's end instead of now.

    The repository is the current working directory, and a capture whose repo_id is
    not this repository's is skipped rather than linked: a capture stores the sha256 of
    its root and not the root, so there is no way back from an id to a checkout, and
    linking against whatever directory the command was run in would be an invented
    attribution.
    """
    known = facts(store, capture_id)
    identity = repo.identity(Path.cwd())
    if known.started_at is None or known.repo_id != identity.get("repo_id"):
        return 0
    root = repo.git_root(Path.cwd())
    capture = _Capture(
        capture_id=capture_id,
        provider=GENERIC,
        level=level,
        cwd=Path.cwd(),
        store=store,
        ctx=Ctx(repo_root=None if root is None else Path(root).resolve()),
        started_at=known.started_at,
        started_ns=time.monotonic_ns(),
        repo_id=known.repo_id,
        snapshots=known.snapshots,
    )
    found = repo.commits_since(
        capture.cwd,
        known.started_at,
        known.snapshots,
        provider_reported=reported_commits(store, capture_id),
        until_ts=known.ended_at,
    )
    for payload in found:
        capture.emit("telltale.repo.commit", payload)
    store.flush()
    return len(found)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _whole(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
