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
