"""The schema, and nothing else. Design 6.5.

Split out of store.py, which stood at 765 lines against the 800-line ratchet before
W1-T5 added its readers. The CREATE statements are the one part of that file no reader
needs in order to follow the writer thread, and no part of it needs them in order to
run. Moving them changed no behaviour.

Nothing here writes a row. The `derived-writes-only-in-store` hook keeps every write
into activities, evidence, series_snapshots and forecast_runs inside store.py, so this
file holds CREATE statements only, and a grep for insert statements over it prints
nothing.
"""

from __future__ import annotations

DDL = """
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
-- W3-T3. `obs_by_type (observation_type)` was here and is dropped, not merely no longer
-- created: an index costs the writer on every insert forever, and a store that already
-- has one keeps paying until something removes it. The DROP runs at every open, beside
-- the CREATEs, because that is where a schema this file owns is brought up to date.
--
-- It is a strict PREFIX of obs_by_type_capture below, and the only read in src/ that
-- filters on observation_type alone is `Reads.observations_of_type`, which orders by
-- (capture_id, observation_id) and is therefore served better by the composite.
-- Measured on 2026-09-02, 50000 observations in 500-row batches, first append to
-- `close()` returning, eight interleaved pairs in one process: median 0.602 s with this
-- index and 0.568 s without it, 6.1 per cent of the write path, 0.68 us per
-- observation. The read it might have served is unchanged: on a `.backup` copy of the
-- owner's store, 1066523 observations, `observations_of_type` returns its 69 rows in
-- 0.8 ms either way and the query plan names obs_by_type_capture in both, because it
-- already preferred it. The migration itself, on that copy:
-- 0.017 s for the open that drops it and 0.001 s for every open after.
DROP INDEX IF EXISTS obs_by_type;
-- W3-T0. A caller that names the observation types it wants seeks straight to them
-- instead of walking the capture: measured on the owner's 3200-capture store,
-- 3200 two-type reads take 0.266 s through obs_by_capture and 0.026 s through this.
-- `Reads.observations` writes ORDER BY +observation_id for that reason: with a plain
-- ORDER BY, SQLite answers the sort from obs_by_capture and never reaches this index.
-- The cost is on the writer, 0.234 s against 0.274 s for 50000 inserts in 500-row
-- transactions, which is 0.8 us per observation.
CREATE INDEX IF NOT EXISTS obs_by_type_capture
  ON observations (observation_type, capture_id);
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
