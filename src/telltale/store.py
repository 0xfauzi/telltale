"""The database, and the one thread allowed to write to it. Design 6.5.

Observations arrive on the request threads of an HTTP server that must answer in
milliseconds, and SQLite takes one writer at a time. So a request thread parses,
sanitizes, hands a batch to a bounded queue and returns. One writer thread owns one
connection; readers open their own read-only connections, which is a rule rather than a
preference: a sqlite3 connection belongs to the thread that made it.

That buys a recorder which cannot stall the agent it records. It costs the guarantee
that everything sent is stored: a bounded queue drops when full, and a drop nobody
counted is data loss that looks like a quiet session. So drops are counted per surface,
written back as a diagnostics row when the queue drains, and reported by `health()` for
as long as the process lives.

Every INSERT into activities, evidence, series_snapshots and forecast_runs is in this
file, and the derived-writes-only-in-store hook keeps it that way: the CHECK constraints
in schema.py and the Evidence constructors in model.py are the only route a derived
number has into the database.
"""

from __future__ import annotations

import argparse
import queue
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from telltale.commands import (
    FALLBACK_VERSION,
    NORMALIZATION_VERSION,
    resanitize_row,
)
from telltale.model import (
    Activity,
    Evidence,
    Observation,
    Series,
    from_json,
    new_id,
    now_iso,
    to_json,
)
from telltale.schema import DDL
from telltale.store_reads import Reads

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

QUEUE_MAX = 10_000
BATCH_MAX = 500  # one item is taken, then up to this many more join its transaction
PUT_TIMEOUT_S = 0.25  # design 6.5: a request thread never waits longer than this
CLOSE_TIMEOUT_S = 5.0
FLUSH_TIMEOUT_S = 30.0  # the default barrier wait; a caller who knows better passes one
SUBMIT_TIMEOUT_S = 30.0  # turns a wait on a wedged writer into an error, not a hang
BUSY_TIMEOUT_MS = 5000
MAX_DETAIL = 2048  # diagnostics.detail, design 6.5

# How long the writer keeps retrying one transaction before it gives the batch up. A
# policy, not a measurement: about 1.5 s covers a lock held by another process, and past
# that the batch is lost and SAID to be lost rather than held forever.
RETRY_DELAYS_S = (0.05, 0.1, 0.2, 0.4, 0.8)

DIAGNOSTIC_KINDS = (
    "parse_failure", "dropped", "conflict", "unknown_field", "level2_raw", "launcher",
)  # fmt: skip

# Observation types that may be thrown away when the queue is full. Each is high volume
# and adds nothing a slower surface does not carry: a metric is a rollup of stored
# events, a partial message is a prefix of the assistant message after it, and a retry
# is visible in the api_error that caused it. Everything else waits.
DROPPABLE_TYPE_PREFIXES = (
    "claude.otel.metric",
    "claude.stream.stream_event",
    "claude.stream.api_retry",
    # Provisional: E02 fixes the codex OTel vocabulary. A prefix matching nothing costs
    # nothing; a missing one costs a stall on the request thread.
    "codex.otel.metric",
)


# The column list of a table is the field list of its dataclass, read once at import.
# One tuple drives both the INSERT text and the row tuple, so the two cannot disagree,
# and a field added to a shape with no column fails at the first write.
_OBSERVATION_COLUMNS = tuple(f.name for f in fields(Observation))
_EVIDENCE_COLUMNS = tuple(f.name for f in fields(Evidence))
_SERIES_COLUMNS = tuple(f.name for f in fields(Series))
# claim_class is the exception: design 6.5 gives the activities table no such column,
# and model.py refuses any Activity that is not derived, so there is nothing to store.
_ACTIVITY_COLUMNS = tuple(f.name for f in fields(Activity) if f.name != "claim_class")
_DIAGNOSTIC_COLUMNS = (
    "diagnostic_id", "capture_id", "ingest_ts", "kind", "observation_id", "detail",
)  # fmt: skip
# forecast_runs is written from a mapping rather than a dataclass: design 6.2 defines no
# durable shape for a backtest run, and inventing one here would fix a contract the
# forecasting lab has not measured yet. This tuple IS the contract, and a mapping that
# does not match it exactly is refused rather than written with holes.
FORECAST_RUN_COLUMNS = (
    "series_id", "target", "variant", "ordering", "placebo_seed", "horizon", "c_min",
    "stride", "forecasters", "windows", "metrics", "decision", "scenario",
    "missingness_policy", "warnings", "assumptions",
)  # fmt: skip
_FORECAST_COLUMNS = (
    "forecast_run_id",
    *FORECAST_RUN_COLUMNS,
    "claim_class",
    "created_at",
)
# What `rebuild` deletes before the reducers run. Nothing else is recomputable from
# observations alone.
_DERIVED_TABLES = ("activities", "evidence")

# The one diagnostic kind a REDUCER writes, and so the one `rebuild` may delete. The
# others (parse_failure, dropped, unknown_field, level2_raw, launcher) are ingest facts
# about a record that arrived once, and no rebuild can reproduce them.
#
# The reverse does not hold, and the cost is real: receiver.py writes `conflict` when
# one provider session binds to a second capture, and importer.py when two files key on
# one capture id. Both are ingest facts, both are deleted here, and neither comes back.
# Telling them apart needs a marker no row carries today (a seventh kind is a CHECK
# constraint change, so it is a migration), and leaving every capture with the conflict
# count of an older reducer version was judged the worse of the two. W2-T6 measured 276
# conflict rows in the owner's store, all of them the reducer's.
_REDUCER_DIAGNOSTIC = "conflict"

# Columns whose text is JSON, in every table. Read functions decode them, so a caller
# gets the structure back rather than a string it would have to know to parse.
_JSON_COLUMNS = frozenset({
    "assumptions", "changepoints", "cohort", "columns", "correlation_ids", "decision",
    "fields", "forecasters", "metrics", "payload", "provenance", "redaction",
    "row_meta", "rows", "scenario", "source", "warnings", "windows",
})  # fmt: skip


def _insert(table: str, columns: Sequence[str], replace: bool = False) -> str:
    verb = "INSERT OR REPLACE INTO" if replace else "INSERT INTO"
    names = ", ".join(f'"{name}"' for name in columns)
    marks = ",".join("?" * len(columns))
    return f"{verb} {table} ({names}) VALUES ({marks})"


_INSERT_OBSERVATION = _insert("observations", _OBSERVATION_COLUMNS)
_INSERT_ACTIVITY = _insert("activities", _ACTIVITY_COLUMNS)
_INSERT_EVIDENCE = _insert("evidence", _EVIDENCE_COLUMNS)
_INSERT_SERIES = _insert("series_snapshots", _SERIES_COLUMNS, replace=True)
_INSERT_DIAGNOSTIC = _insert("diagnostics", _DIAGNOSTIC_COLUMNS)
_INSERT_FORECAST = _insert("forecast_runs", _FORECAST_COLUMNS)
# The two tables a reducer rewrites wholesale, each with the statement that fills it.
_REPLACEABLE = {"activities": _INSERT_ACTIVITY, "evidence": _INSERT_EVIDENCE}

# The ONE update of an observation in this codebase; the resanitize-is-the-only-update
# hook keeps it here. Observations are immutable and append-only and `purge <capture>`
# is the only deletion path (design 6.5), and W2-T8 found the defect neither rule can
# answer: a credential inside a stored command, put there by a normalization rule that
# was too loose. Purging the ten captures that held one would have thrown away three of
# this build's own launcher captures to remove ten tokens, and the next import would
# write the shape again. So there is one sanctioned rewrite, and it can only remove.
_UPDATE_OBSERVATION = (
    "UPDATE observations SET payload = ?, redaction = ? WHERE observation_id = ?"
)


@dataclass
class _Job:
    """One unit of work for the writer thread, with a place to put its answer."""

    run: Callable[[sqlite3.Connection], Any]
    done: threading.Event | None = None
    result: Any = None
    error: BaseException | None = None


class Store(Reads):
    """The database and its writer thread. Open it, append to it, close it.

    Every SELECT it answers is `Reads`, in store_reads.py; `_read` below is the half
    that stays here, with the connection it makes.
    """

    # Reducers run in registration order; each rebuilds one projection of a capture.
    # activities.py and measures.py register theirs (design 6.10 and 6.11).
    reducers: ClassVar[list[Callable[[Store, str], None]]] = []

    def __init__(self, path: str | Path, queue_max: int = QUEUE_MAX) -> None:
        # `queue_max` is a constructor argument rather than the module constant alone
        # because the queue's behaviour when it is FULL is a contract (design 6.5), and
        # the only way to see that contract hold is to fill it. Filling 10000 slots
        # takes seconds and megabytes; a store built with 2 shows the same code path in
        # milliseconds. Rebinding the constant instead would change every store in the
        # process, including the one under test's neighbours.
        self.path = Path(path).expanduser().resolve()
        self.down = False
        self._queue_max = queue_max
        self._queue: queue.Queue[_Job | None] = queue.Queue(maxsize=queue_max)
        self._writer: threading.Thread | None = None
        # Drop bookkeeping under _lock: _pending empties into a diagnostics row when the
        # queue drains, _dropped never empties, so health() can still see an old loss.
        self._pending: dict[str, int] = {}
        self._dropped: dict[str, int] = {}
        self._last_ingest: dict[str, str] = {}
        # Jobs the writer has taken off the queue and not yet committed. queue_depth
        # alone hides them, and they are exactly the rows a reader would miss.
        self._in_flight = 0
        self._lock = threading.Lock()
        self._resume = threading.Event()
        self._resume.set()

    def open(self) -> Store:
        """Create the schema, then start the writer thread that owns its connection."""
        if self._writer is not None:
            raise RuntimeError(f"store {self.path} is already open")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare()
        self._writer = threading.Thread(
            target=self._serve, name="telltale-writer", daemon=True
        )
        self._writer.start()
        return self

    def close(self) -> None:
        """Send the sentinel and wait. A writer still alive afterwards sets `down`."""
        writer = self._writer
        if writer is None:
            return
        self._resume.set()  # a paused writer has to be able to see the sentinel
        self._queue.put(None)
        writer.join(timeout=CLOSE_TIMEOUT_S)
        if writer.is_alive():
            self.down = True
        self._writer = None

    def _prepare(self) -> None:
        """Journal mode and schema, retried while another process is doing the same.

        `PRAGMA journal_mode = WAL` needs an exclusive lock and is the one statement
        SQLite does not apply `busy_timeout` to: it answers "database is locked" at once
        when any other connection holds the database. Measured on a FRESH database
        opened by four processes at the same instant, 100 opens: 30 raised there, and
        CI failed one of a concurrent pair of `telltale run` exactly that way.

        Two halves, and both are needed. Reading the mode first takes the pragma out of
        every open after the first, because WAL is a property of the file and persists.
        The retry covers the first one, where every process reads `delete` and all of
        them try to change it. Measured with both: 0 failures in 100 opens.
        """
        for delay in (*RETRY_DELAYS_S, None):
            try:
                self._create()
                return
            except sqlite3.OperationalError:
                if delay is None:
                    raise
                time.sleep(delay)

    def _create(self) -> None:
        conn = self._connect()
        try:
            if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(DDL)
            conn.commit()
        finally:
            conn.close()

    def flush(self, timeout: float = FLUSH_TIMEOUT_S) -> bool:
        """Wait until everything queued before this call is committed. Never raises.

        An empty queue is NOT a write barrier, which W0-T5 measured: `_serve_once`
        takes its job off the queue before it opens the transaction, so `queue_depth`
        reaches 0 while the batch is still in flight and a reader at that instant sees
        fewer rows than were accepted. This puts a job of its own at the BACK of the
        queue. The writer reaches it only after the jobs in front of it, and every
        job's Event is set after the `with conn` block that committed its batch, so a
        set Event means committed and readable.

        False is the writer failing to reach the barrier in time, which is a fact about
        the writer and not about the data: a flush that returns False dropped nothing.
        """
        writer = self._writer
        if writer is None or threading.current_thread() is writer:
            return False
        done = threading.Event()
        try:
            self._queue.put(_Job(run=_barrier, done=done), timeout=PUT_TIMEOUT_S)
        except queue.Full:
            return False
        return done.wait(timeout=timeout)

    def pause_writer(self) -> None:
        """TEST-ONLY. Holds the writer so a caller can fill the queue and see drops."""
        self._resume.clear()

    def resume_writer(self) -> None:
        """TEST-ONLY. The other half of pause_writer()."""
        self._resume.set()

    def append(self, observations: Sequence[Observation]) -> int:
        """Queue a batch. Returns how many were accepted, which is 0 when it is dropped.

        Never blocks longer than PUT_TIMEOUT_S, and not at all for an entirely
        droppable batch. The caller must answer 200 whatever happened (spec 5.2), so the
        return value is for counting, not for raising.
        """
        if not observations:
            return 0
        rows = [_row(asdict(obs), _OBSERVATION_COLUMNS) for obs in observations]
        job = _Job(run=partial(_write, _INSERT_OBSERVATION, rows))
        droppable = all(
            obs.observation_type.startswith(DROPPABLE_TYPE_PREFIXES)
            for obs in observations
        )
        if self._enqueue(job, droppable):
            self._record_received(observations)
            return len(observations)
        self._count_drops(observations)
        return 0

    def diagnose(
        self,
        kind: str,
        detail: str,
        capture_id: str | None = None,
        observation_id: str | None = None,
    ) -> None:
        """Record why something did not become an observation. Never raises."""
        if kind not in DIAGNOSTIC_KINDS:
            raise ValueError(f"diagnostic kind {kind!r} is not in {DIAGNOSTIC_KINDS}")
        text = detail[:MAX_DETAIL]
        row = (new_id("diag"), capture_id, now_iso(), kind, observation_id, text)
        job = _Job(run=partial(_write, _INSERT_DIAGNOSTIC, [row]))
        if not self._enqueue(job, droppable=False):
            self._bump("diagnostics", 1)

    def replace_activities(
        self, capture_id: str, activities: Sequence[Activity]
    ) -> int:
        """Delete this capture's activities and write these, in one transaction."""
        rows = [_row(asdict(item), _ACTIVITY_COLUMNS) for item in activities]
        return int(self._submit(partial(_replace, "activities", capture_id, rows)))

    def replace_evidence(self, capture_id: str, evidence: Sequence[Evidence]) -> int:
        """Delete this capture's evidence and write this, in one transaction."""
        rows = [_row(asdict(item), _EVIDENCE_COLUMNS) for item in evidence]
        return int(self._submit(partial(_replace, "evidence", capture_id, rows)))

    def put_series(self, series: Series) -> str:
        """Write a series snapshot. The id is a content hash, so a rewrite replaces."""
        row = _row(asdict(series), _SERIES_COLUMNS)
        self._submit(partial(_write, _INSERT_SERIES, [row]))
        return series.series_id

    def put_forecast_run(self, run: Mapping[str, Any]) -> str:
        """Write one backtest run. The mapping carries exactly FORECAST_RUN_COLUMNS."""
        missing = sorted(set(FORECAST_RUN_COLUMNS) - set(run))
        extra = sorted(set(run) - set(FORECAST_RUN_COLUMNS))
        if missing or extra:
            raise ValueError(f"forecast run: missing {missing}, unexpected {extra}")
        values = {
            **run,
            "forecast_run_id": new_id("fc"),
            "claim_class": "predictive",
            "created_at": now_iso(),
        }
        row = _row(values, _FORECAST_COLUMNS)
        self._submit(partial(_write, _INSERT_FORECAST, [row]))
        return str(values["forecast_run_id"])

    def rebuild(self, capture_id: str | None = None) -> int:
        """Delete the derived rows in scope and run every registered reducer over them.

        The capture's `conflict` diagnostics go with them, in the same transaction: a
        conflict is something a reducer FOUND, so after a rebuild the count has to be
        what this run found and not that plus what every earlier run found. Measured on
        the build's own store: cap_01M1GPSMW1ZRXVADWZPF0KZ9H3 kept 32 conflict rows from
        its first reduction after a rebuild that wrote none, and a rebuild under the old
        reducer would have written 32 more. Every other kind is an ingest fact about a
        record that arrived once and stays.

        Series snapshots and forecast runs are NOT touched: they are the output of an
        explicit command with arguments this function does not have (a clock, a cohort,
        a horizon), and each carries the reducer version it was built with, so a stale
        one is identifiable rather than silently refreshed.
        """
        targets = (
            [capture_id]
            if capture_id is not None
            else [str(row["capture_id"]) for row in self.captures()]
        )
        for target in targets:
            self._submit(partial(_clear_derived, target))
            for reducer in self.reducers:
                reducer(self, target)
        return len(targets)

    def purge(self, capture_id: str) -> int:
        """Delete a capture's observations and diagnostics, then rebuild the rest."""
        deleted = int(self._submit(partial(_purge_capture, capture_id)))
        self.rebuild(capture_id)
        return deleted

    def purge_diagnostics(self, older_than_days: int) -> int:
        """The only age-based deletion in the system. Design 6.5."""
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        stamp = cutoff.isoformat(timespec="microseconds").replace("+00:00", "Z")
        return int(self._submit(partial(_purge_diagnostics, stamp)))

    def resanitize(self, capture_id: str | None = None) -> dict[str, int]:
        """Rewrite stored commands an older normalization version wrote. Design 6.5.

        Returns {capture_id: fields rewritten}, with an entry only where the count is
        above zero. So a second run over the same store returns {}, which is the whole
        idempotence claim: the rules are applied to their own output and find nothing.

        One transaction per capture, each carrying its own diagnostics row. Not one
        transaction for the store: 3189 captures is a transaction nobody could
        interrupt, and a capture is the unit `purge` and `rebuild` already work in.

        Then the capture is rebuilt, for the reason `purge` rebuilds: an activity
        COPIES the normal form into `fields.command_norm`, so rewriting the observation
        alone leaves the leak standing one table over. Measured on a copy of the
        owner's store: six activity rows still held a probe after the observations were
        clean.
        """
        counts: dict[str, int] = {}
        for target in self._stale_captures(capture_id):
            updates, fields_changed, versions = _rewrites(self._stale_commands(target))
            if not updates:
                continue
            detail = (
                f"resanitize {','.join(sorted(versions))} to {NORMALIZATION_VERSION}:"
                f" {fields_changed} field(s) rewritten"
            )
            self._submit(partial(_apply_rewrites, target, updates, detail))
            self.rebuild(target)
            counts[target] = fields_changed
        return counts

    def _stale_captures(self, capture_id: str | None) -> list[str]:
        """The captures holding a command normalized by anything but the current rules.

        One scan of the table, so that the per-capture reads below can each take the
        (capture_id, observation_id) index. Measured on the owner's store on 2026-09-02:
        0.73 s over 1,040,662 rows, of which 88,754 carry a normalization_version.
        """
        if capture_id is not None:
            return [capture_id]
        rows = self._read(
            "SELECT DISTINCT capture_id FROM observations WHERE"
            " json_extract(payload, '$.normalization_version') NOT IN (?, ?)",
            (NORMALIZATION_VERSION, FALLBACK_VERSION),
        )
        return [str(row["capture_id"]) for row in rows]

    def _stale_commands(self, capture_id: str) -> list[dict[str, Any]]:
        """One capture's observations that carry a command an older rule normalized.

        `NOT IN` and not `!=`: json_extract returns NULL for a payload with no
        normalization_version at all, `NULL NOT IN (...)` is NULL rather than true, and
        a row with no normalized command in it is not this function's business.
        """
        return self._read(
            "SELECT observation_id, observation_type, payload, redaction FROM"
            " observations WHERE capture_id = ? AND"
            " json_extract(payload, '$.normalization_version') NOT IN (?, ?)",
            (capture_id, NORMALIZATION_VERSION, FALLBACK_VERSION),
        )

    def health(self) -> dict[str, Any]:
        """What /healthz answers with: design 6.5's one surface that tells the truth."""
        writer = self._writer
        with self._lock:
            last_ingest = dict(self._last_ingest)
            total = dict(self._dropped)
            unreported = sum(self._pending.values())
            in_flight = self._in_flight
        alive = "alive" if writer is not None and writer.is_alive() else "dead"
        return {
            "writer": "stopped" if writer is None else alive,
            "down": self.down,
            "queue_depth": self._queue.qsize(),
            "in_flight": in_flight,
            "queue_max": self._queue_max,
            "last_ingest_ts": last_ingest,
            "drops_by_surface": total,
            "drops_total": sum(total.values()),
            "drops_unreported": unreported,
            "path": str(self.path),
        }

    def _connect(self, readonly: bool = False) -> sqlite3.Connection:
        target = self.path.as_uri() + "?mode=ro" if readonly else str(self.path)
        conn = sqlite3.connect(target, uri=readonly, timeout=BUSY_TIMEOUT_MS / 1000)
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _read(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        conn = self._connect(readonly=True)
        try:
            conn.row_factory = sqlite3.Row
            return [_decode(dict(row)) for row in conn.execute(sql, params)]
        finally:
            conn.close()

    def _enqueue(self, job: _Job, droppable: bool) -> bool:
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            if droppable:
                return False
        try:
            self._queue.put(job, timeout=PUT_TIMEOUT_S)
            return True
        except queue.Full:
            return False

    def _submit(self, run: Callable[[sqlite3.Connection], Any]) -> Any:
        """Queue a write and wait for it. Raises whatever the writer raised."""
        writer = self._writer
        if writer is None:
            raise RuntimeError("store is not open")
        if threading.current_thread() is writer:
            raise RuntimeError("a write from the writer thread would wait for itself")
        done = threading.Event()
        job = _Job(run=run, done=done)
        self._queue.put(job)
        if not done.wait(timeout=SUBMIT_TIMEOUT_S):
            raise TimeoutError(f"writer did not finish within {SUBMIT_TIMEOUT_S} s")
        if job.error is not None:
            raise job.error
        return job.result

    def _record_received(self, observations: Sequence[Observation]) -> None:
        with self._lock:
            for obs in observations:
                if obs.ingest_ts > self._last_ingest.get(obs.surface, ""):
                    self._last_ingest[obs.surface] = obs.ingest_ts

    def _count_drops(self, observations: Sequence[Observation]) -> None:
        for obs in observations:
            self._bump(obs.surface, 1)

    def _bump(self, surface: str, count: int) -> None:
        with self._lock:
            self._pending[surface] = self._pending.get(surface, 0) + count
            self._dropped[surface] = self._dropped.get(surface, 0) + count

    # -- the writer thread -------------------------------------------------------

    def _serve(self) -> None:
        try:
            conn = self._connect()
        except sqlite3.Error:
            self.down = True
            return
        try:
            while self._serve_once(conn):
                pass
        finally:
            conn.close()

    def _serve_once(self, conn: sqlite3.Connection) -> bool:
        """One transaction. Returns False once the sentinel has been seen."""
        first = self._queue.get()
        if first is None:
            return False
        self._resume.wait()  # the test-only pause hook
        more, stopping = self._drain()
        jobs = [first, *more]
        with self._lock:
            self._in_flight = len(jobs)
        self._run_batch(conn, jobs)
        with self._lock:
            self._in_flight = 0
        self._flush_drops(conn)
        return not stopping

    def _drain(self) -> tuple[list[_Job], bool]:
        jobs: list[_Job] = []
        while len(jobs) < BATCH_MAX:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return jobs, False
            if item is None:
                return jobs, True
            jobs.append(item)
        return jobs, False

    def _run_batch(self, conn: sqlite3.Connection, jobs: list[_Job]) -> None:
        # `Exception`, not `sqlite3.Error`. Measured: one observation holding a lone
        # UTF-16 surrogate (json.loads makes one from "\\ud800", and sqlite3 cannot
        # encode it) raised UnicodeEncodeError here, killed this thread, and left
        # append() reporting success into a queue nobody drained. A writer that dies
        # quietly is the failure spec 5.2 forbids, so any exception becomes a drop.
        for delay in (*RETRY_DELAYS_S, None):
            try:
                with conn:
                    for job in jobs:
                        job.result = job.run(conn)
            except Exception:
                self.down = True
                if delay is None:
                    self._give_up(conn, jobs)
                    break
                time.sleep(delay)
            else:
                self.down = False
                break
        for job in jobs:
            if job.done is not None:
                job.done.set()

    def _give_up(self, conn: sqlite3.Connection, jobs: list[_Job]) -> None:
        """Retry the failed batch one job at a time, so one bad row costs one row.

        Without it, one observation SQLite refuses (a STRICT type violation from a new
        provider module, say) takes the other 499 in its transaction with it. The
        diagnostic names every distinct failure, because a batch can fail twice for two
        reasons and one of them would otherwise never be seen.
        """
        failures: list[str] = []
        for job in jobs:
            try:
                with conn:
                    job.result = job.run(conn)
            except Exception as single:
                job.error = single
                failures.append(f"{type(single).__name__}: {single}")
        if failures:
            self._bump("store", len(failures))
            detail = "; ".join(sorted(set(failures)))
            self._diagnose_here(conn, f"{len(failures)}/{len(jobs)} failed: {detail}")

    def _flush_drops(self, conn: sqlite3.Connection) -> None:
        """One diagnostics row per drain, not one per drop. Design 6.5."""
        if not self._queue.empty():
            return
        with self._lock:
            drops = dict(self._pending)
            self._pending.clear()
        if drops:
            self._diagnose_here(
                conn, to_json({"per_surface": drops, "total": sum(drops.values())})
            )

    def _diagnose_here(self, conn: sqlite3.Connection, detail: str) -> None:
        """A diagnostics row written on the writer thread, outside the queue."""
        row = (new_id("diag"), None, now_iso(), "dropped", None, detail[:MAX_DETAIL])
        try:
            with conn:
                _write(_INSERT_DIAGNOSTIC, [row], conn)
        except sqlite3.Error:
            # Nothing further is possible. health()'s counters are cumulative and never
            # cleared precisely so that this loss is still visible somewhere.
            self.down = True


# -- statements run on the writer connection -------------------------------------


def _write(sql: str, rows: Sequence[Sequence[Any]], conn: sqlite3.Connection) -> int:
    return int(conn.executemany(sql, rows).rowcount)


def _barrier(_conn: sqlite3.Connection) -> None:
    """The job `flush` queues. It writes nothing: its value is its place in line."""


def _delete_by_capture(
    tables: Sequence[str], capture_id: str, conn: sqlite3.Connection
) -> int:
    deleted = 0
    for table in tables:
        sql = f"DELETE FROM {table} WHERE capture_id = ?"  # noqa: S608 - fixed names
        deleted += int(conn.execute(sql, (capture_id,)).rowcount)
    return deleted


def _replace(
    table: str, capture_id: str, rows: Sequence[Sequence[Any]], conn: sqlite3.Connection
) -> int:
    _delete_by_capture((table,), capture_id, conn)
    conn.executemany(_REPLACEABLE[table], rows)
    return len(rows)


def _clear_derived(capture_id: str, conn: sqlite3.Connection) -> int:
    """One transaction: the derived rows of a capture, and what a reducer said about it.

    Together, because a half-applied delete would leave activities from one run beside
    conflicts from another and nothing would say which.
    """
    deleted = _delete_by_capture(_DERIVED_TABLES, capture_id, conn)
    conn.execute(
        "DELETE FROM diagnostics WHERE capture_id = ? AND kind = ?",
        (capture_id, _REDUCER_DIAGNOSTIC),
    )
    return deleted


def _purge_capture(capture_id: str, conn: sqlite3.Connection) -> int:
    deleted = _delete_by_capture(("observations",), capture_id, conn)
    _delete_by_capture(("diagnostics",), capture_id, conn)
    return deleted


def _apply_rewrites(
    capture_id: str,
    updates: Sequence[Sequence[Any]],
    detail: str,
    conn: sqlite3.Connection,
) -> int:
    """The rewrites of one capture and the row that records them, in one transaction."""
    conn.executemany(_UPDATE_OBSERVATION, updates)
    row = (new_id("diag"), capture_id, now_iso(), "dropped", None, detail[:MAX_DETAIL])
    _write(_INSERT_DIAGNOSTIC, [row], conn)
    return len(updates)


def _purge_diagnostics(cutoff: str, conn: sqlite3.Connection) -> int:
    cursor = conn.execute("DELETE FROM diagnostics WHERE ingest_ts < ?", (cutoff,))
    return int(cursor.rowcount)


# -- row shapes ------------------------------------------------------------------


def _rewrites(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[tuple[Any, ...]], int, set[str]]:
    """The UPDATE parameters for the rows the current rules change, and what changed.

    Nothing is written for a row whose commands come back identical, so an observation
    that was already right keeps its redaction and its old version.
    """
    updates: list[tuple[Any, ...]] = []
    changed = 0
    versions: set[str] = set()
    for row in rows:
        payload, redaction, fields = resanitize_row(
            str(row["observation_type"]), dict(row["payload"]), row["redaction"]
        )
        if not fields:
            continue
        versions.add(str(row["payload"]["normalization_version"]))
        changed += fields
        updates.append((to_json(payload), to_json(redaction), row["observation_id"]))
    return updates, changed, versions


def _row(values: Mapping[str, Any], columns: Sequence[str]) -> tuple[Any, ...]:
    return tuple(
        to_json(values[name])
        if name in _JSON_COLUMNS and values[name] is not None
        else values[name]
        for name in columns
    )


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: from_json(value)
        if key in _JSON_COLUMNS and isinstance(value, str)
        else value
        for key, value in row.items()
    }


def _main(argv: Sequence[str] | None = None) -> int:
    """`python -m telltale.store --selfcheck DB`. The check itself is selfcheck.py.

    Imported inside the function because selfcheck.py imports Store from this module,
    and this command is its only caller here.
    """
    parser = argparse.ArgumentParser(prog="python -m telltale.store")
    parser.add_argument(
        "--selfcheck",
        metavar="DB",
        required=True,
        help="write 1000 observations from 8 threads into DB and read them back",
    )
    args = parser.parse_args(argv)
    from telltale.selfcheck import main

    return main([args.selfcheck])


if __name__ == "__main__":
    raise SystemExit(_main())
