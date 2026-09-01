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
below and the Evidence constructors in model.py are the only route a derived number has
into the database.
"""

from __future__ import annotations

import argparse
import queue
import sqlite3
import sys
import threading
import time
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from telltale.model import (
    Activity,
    Evidence,
    Observation,
    Series,
    from_json,
    new_id,
    now_iso,
    to_json,
    ulid,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

QUEUE_MAX = 10_000
BATCH_MAX = 500  # one item is taken, then up to this many more join its transaction
PUT_TIMEOUT_S = 0.25  # design 6.5: a request thread never waits longer than this
CLOSE_TIMEOUT_S = 5.0
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

_DDL = """
CREATE TABLE IF NOT EXISTS observations (
  observation_id TEXT PRIMARY KEY, capture_id TEXT NOT NULL,
  observation_type TEXT NOT NULL, surface TEXT NOT NULL, provider TEXT NOT NULL,
  adapter TEXT NOT NULL, provider_session_id TEXT, provider_ts TEXT,
  ingest_ts TEXT NOT NULL, environment_fingerprint_id TEXT, repo_id TEXT,
  schema_version INTEGER NOT NULL,
  correlation_ids TEXT NOT NULL CHECK (json_valid(correlation_ids)),
  payload TEXT NOT NULL CHECK (json_valid(payload)),
  redaction TEXT NOT NULL CHECK (json_valid(redaction))
) STRICT;
CREATE INDEX IF NOT EXISTS obs_by_capture ON observations (capture_id, observation_id);
CREATE INDEX IF NOT EXISTS obs_by_session ON observations (provider_session_id);
CREATE INDEX IF NOT EXISTS obs_by_type ON observations (observation_type);
CREATE TABLE IF NOT EXISTS activities (
  activity_id TEXT PRIMARY KEY, capture_id TEXT NOT NULL,
  activity_type TEXT NOT NULL, actor TEXT NOT NULL, started_at TEXT NOT NULL,
  ended_at TEXT, fields TEXT NOT NULL CHECK (json_valid(fields)),
  provenance TEXT NOT NULL CHECK (json_valid(provenance)),
  reducer_version TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS activities_by_capture ON activities (capture_id, started_at);
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY, capture_id TEXT, metric TEXT NOT NULL, value REAL,
  unit TEXT NOT NULL,
  claim_class TEXT NOT NULL
    CHECK (claim_class IN ('derived','comparative','associative','predictive')),
  coverage TEXT NOT NULL
    CHECK (coverage IN ('observed','partial','derived','unavailable')),
  source TEXT NOT NULL CHECK (json_valid(source)),
  cohort TEXT CHECK (json_valid(cohort)), environment_fingerprint_id TEXT,
  reducer_version TEXT NOT NULL,
  assumptions TEXT NOT NULL CHECK (json_valid(assumptions)),
  warnings TEXT NOT NULL CHECK (json_valid(warnings)), created_at TEXT NOT NULL,
  -- A number with no source is a number nobody can check. The model refuses it too;
  -- this is the copy that holds when the caller is sqlite3 on the command line.
  CHECK (coverage = 'unavailable' OR json_array_length(source) > 0)
) STRICT;
CREATE TABLE IF NOT EXISTS series_snapshots (
  series_id TEXT PRIMARY KEY,
  clock TEXT NOT NULL CHECK (clock IN ('request','attempt','change')),
  cohort TEXT NOT NULL CHECK (json_valid(cohort)),
  "columns" TEXT NOT NULL CHECK (json_valid("columns")),
  rows TEXT NOT NULL CHECK (json_valid(rows)),
  row_meta TEXT NOT NULL CHECK (json_valid(row_meta)),
  changepoints TEXT NOT NULL CHECK (json_valid(changepoints)),
  missingness_policy TEXT NOT NULL CHECK (missingness_policy IN ('exclude','refuse')),
  reducer_version TEXT NOT NULL, built_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS forecast_runs (
  forecast_run_id TEXT PRIMARY KEY, series_id TEXT NOT NULL, target TEXT NOT NULL,
  variant TEXT NOT NULL,
  ordering TEXT NOT NULL CHECK (ordering IN ('true','placebo_block','placebo_row')),
  placebo_seed INTEGER, horizon INTEGER NOT NULL, c_min INTEGER NOT NULL,
  stride INTEGER NOT NULL,
  forecasters TEXT NOT NULL CHECK (json_valid(forecasters)),
  windows TEXT NOT NULL CHECK (json_valid(windows)),
  metrics TEXT NOT NULL CHECK (json_valid(metrics)),
  decision TEXT CHECK (json_valid(decision)),
  scenario TEXT CHECK (json_valid(scenario)),
  missingness_policy TEXT NOT NULL,
  warnings TEXT NOT NULL CHECK (json_valid(warnings)),
  assumptions TEXT NOT NULL CHECK (json_valid(assumptions)),
  claim_class TEXT NOT NULL CHECK (claim_class = 'predictive'),
  created_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS diagnostics (
  diagnostic_id TEXT PRIMARY KEY, capture_id TEXT, ingest_ts TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN
    ('parse_failure','dropped','conflict','unknown_field','level2_raw','launcher')),
  observation_id TEXT, detail TEXT NOT NULL
) STRICT;
-- provider and repo_id belong to the capture, not to a row, so the view takes them
-- from the EARLIEST observation (observation_id is arrival order) and the first
-- non-null repo_id. Grouping by them would split a capture in two the moment one
-- observation arrived without a repository.
CREATE VIEW IF NOT EXISTS captures AS
SELECT
  o.capture_id AS capture_id,
  (SELECT f.provider FROM observations f WHERE f.capture_id = o.capture_id
    ORDER BY f.observation_id LIMIT 1) AS provider,
  (SELECT f.repo_id FROM observations f WHERE f.capture_id = o.capture_id
    AND f.repo_id IS NOT NULL ORDER BY f.observation_id LIMIT 1) AS repo_id,
  min(o.ingest_ts) AS first_ts, max(o.ingest_ts) AS last_ts,
  count(*) AS observation_count
FROM observations o GROUP BY o.capture_id;
"""

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


@dataclass
class _Job:
    """One unit of work for the writer thread, with a place to put its answer."""

    run: Callable[[sqlite3.Connection], Any]
    done: threading.Event | None = None
    result: Any = None
    error: BaseException | None = None


class Store:
    """The database and its writer thread. Open it, append to it, close it."""

    # Reducers run in registration order; each rebuilds one projection of a capture.
    # activities.py and measures.py register theirs (design 6.10 and 6.11).
    reducers: ClassVar[list[Callable[[Store, str], None]]] = []

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.down = False
        self._queue: queue.Queue[_Job | None] = queue.Queue(maxsize=QUEUE_MAX)
        self._writer: threading.Thread | None = None
        # Drop bookkeeping under _lock: _pending empties into a diagnostics row when the
        # queue drains, _dropped never empties, so health() can still see an old loss.
        self._pending: dict[str, int] = {}
        self._dropped: dict[str, int] = {}
        self._last_ingest: dict[str, str] = {}
        self._lock = threading.Lock()
        self._resume = threading.Event()
        self._resume.set()

    def open(self) -> Store:
        """Create the schema, then start the writer thread that owns its connection."""
        if self._writer is not None:
            raise RuntimeError(f"store {self.path} is already open")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_DDL)
            conn.commit()
        finally:
            conn.close()
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
            self._submit(partial(_delete_by_capture, _DERIVED_TABLES, target))
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

    def captures(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM captures ORDER BY first_ts DESC")

    def observations(self, capture_id: str) -> list[dict[str, Any]]:
        return self._read(
            "SELECT * FROM observations WHERE capture_id = ? ORDER BY observation_id",
            (capture_id,),
        )

    def observations_by_id(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        """Resolve Evidence.source ids. json_each keeps this one constant statement."""
        return self._read(
            "SELECT * FROM observations WHERE observation_id IN"
            " (SELECT value FROM json_each(?)) ORDER BY observation_id",
            (to_json(list(ids)),),
        )

    def activities(self, capture_id: str) -> list[dict[str, Any]]:
        return self._read(
            "SELECT * FROM activities WHERE capture_id = ?"
            " ORDER BY started_at, activity_id",
            (capture_id,),
        )

    def evidence(self, capture_id: str) -> list[dict[str, Any]]:
        return self._read(
            "SELECT * FROM evidence WHERE capture_id = ? ORDER BY metric", (capture_id,)
        )

    def diagnostics(self, capture_id: str | None = None) -> list[dict[str, Any]]:
        return self._read(
            "SELECT * FROM diagnostics WHERE (?1 IS NULL OR capture_id = ?1)"
            " ORDER BY ingest_ts",
            (capture_id,),
        )

    def health(self) -> dict[str, Any]:
        """What /healthz answers with: design 6.5's one surface that tells the truth."""
        writer = self._writer
        with self._lock:
            last_ingest = dict(self._last_ingest)
            total = dict(self._dropped)
            unreported = sum(self._pending.values())
        alive = "alive" if writer is not None and writer.is_alive() else "dead"
        return {
            "writer": "stopped" if writer is None else alive,
            "down": self.down,
            "queue_depth": self._queue.qsize(),
            "queue_max": QUEUE_MAX,
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
        self._run_batch(conn, [first, *more])
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


def _purge_capture(capture_id: str, conn: sqlite3.Connection) -> int:
    deleted = _delete_by_capture(("observations",), capture_id, conn)
    _delete_by_capture(("diagnostics",), capture_id, conn)
    return deleted


def _purge_diagnostics(cutoff: str, conn: sqlite3.Connection) -> int:
    cursor = conn.execute("DELETE FROM diagnostics WHERE ingest_ts < ?", (cutoff,))
    return int(cursor.rowcount)


# -- row shapes ------------------------------------------------------------------


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


# -- selfcheck -------------------------------------------------------------------


@dataclass
class _Made:
    """One ulid() call: when it started, when it returned, and what it returned."""

    before: int
    after: int
    oid: str


def _worker(store: Store, surface: str, count: int, made: list[_Made]) -> None:
    batch: list[Observation] = []
    for index in range(count):
        before = time.monotonic_ns()
        oid = ulid()
        made.append(_Made(before, time.monotonic_ns(), oid))
        obs = Observation(
            oid, "cap_selfcheck", "telltale.capture_started", surface, "telltale",
            "selfcheck@1", now_iso(), payload={"index": index},
        )  # fmt: skip
        batch.append(obs)
        if len(batch) == 5:
            store.append(batch)
            batch = []
    if batch:
        store.append(batch)


def _order_violations(made: list[_Made]) -> int:
    """Count pairs where one ulid() call finished before another began, out of order.

    That is the promise of the id: if call A returned before call B was entered, A's id
    must sort before B's. Overlapping pairs are not compared, because neither call is
    "first" in any sense a reader could use.
    """
    by_after = sorted(made, key=lambda item: item.after)
    by_before = sorted(made, key=lambda item: item.before)
    pointer = 0
    largest = ""
    violations = 0
    for item in by_before:
        while pointer < len(by_after) and by_after[pointer].after <= item.before:
            largest = max(largest, by_after[pointer].oid)
            pointer += 1
        if largest and largest >= item.oid:
            violations += 1
    return violations


def _spawn(store: Store, threads: int, per_thread: int) -> tuple[list[_Made], float]:
    surfaces = ("otel_logs", "otel_metrics", "hook", "stream")
    made: list[list[_Made]] = [[] for _ in range(threads)]
    workers = [
        threading.Thread(
            target=_worker,
            args=(store, surfaces[index % len(surfaces)], per_thread, made[index]),
        )
        for index in range(threads)
    ]
    started = time.monotonic()
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    elapsed_ms = (time.monotonic() - started) * 1000
    return [item for chunk in made for item in chunk], elapsed_ms


def _selfcheck(path: Path, threads: int = 8, per_thread: int = 125) -> int:
    write = sys.stdout.write  # ruff T20: print() lives in cli.py and report.py only
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)

    store = Store(path).open()
    made, elapsed_ms = _spawn(store, threads, per_thread)
    before_close = store.health()
    store.close()

    reopened = Store(path).open()
    stored = reopened.observations("cap_selfcheck")
    captures = reopened.captures()
    after_open = reopened.health()
    reopened.close()

    expected = threads * per_thread
    generated = {item.oid for item in made}
    stored_ids = {str(row["observation_id"]) for row in stored}
    violations = _order_violations(made)
    write(f"db {path}\n")
    write(f"threads {threads} per_thread {per_thread} wall_ms {elapsed_ms:.1f}\n")
    write(f"generated {len(made)} distinct_ids {len(generated)}\n")
    write(f"stored {len(stored)} expected {expected}\n")
    write(f"arrival_order_violations {violations}\n")
    write(f"drops_total {before_close['drops_total']}\n")
    write(f"captures {to_json(captures)}\n")
    write(f"health_before_close {to_json(before_close)}\n")
    write(f"health_after_reopen {to_json(after_open)}\n")
    ok = all(
        (
            len(generated) == expected,
            stored_ids == generated,
            violations == 0,
            before_close["drops_total"] == 0,
        )
    )
    write("selfcheck PASS\n" if ok else "selfcheck FAIL\n")
    return 0 if ok else 1


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m telltale.store")
    parser.add_argument(
        "--selfcheck",
        metavar="DB",
        required=True,
        help="write 1000 observations from 8 threads into DB and read them back",
    )
    args = parser.parse_args(argv)
    return _selfcheck(Path(args.selfcheck))


if __name__ == "__main__":
    raise SystemExit(_main())
