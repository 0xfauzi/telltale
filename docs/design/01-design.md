<!-- Verbatim copy of sections 3 and 6 of the approved implementation plan (~/.claude/plans/i-need-you-to-fuzzy-journal.md), taken 2026-09-01 by W0-T1. Edit the plan, not this file. -->

## 3. Orchestrator decisions (routine; not owner decisions)

- Python 3.12, uv only, single package `telltale`, src layout.
- Zero runtime dependencies for the collector: stdlib `sqlite3` (CPython 3.12.8 here
  bundles SQLite 3.47.1, so STRICT tables and json functions are available),
  `http.server.ThreadingHTTPServer` on 127.0.0.1, `json`, `argparse`, `hashlib`,
  `subprocess` (git only), `dataclasses`, `queue`, `threading`. Extras:
  `telltale[forecast]` = numpy, torch, timesfm==3.0.0; `telltale[export]` = pyarrow.
- OTLP over HTTP with JSON encoding for both providers. No protobuf, ever.
- No resident daemon is required: `telltale run` hosts the receiver in-process on a free
  port for the life of the child. `telltale daemon` (same class, fixed port) exists only
  for the owner's opt-in day-to-day capture.
- Content level 1 by default; 0 and 2 per capture; level 2 fragments live in diagnostics
  with a 7-day purge.
- Reducer versions are hashes of the reducer source files computed at import time.
- Experiments are E01..; each has `experiments/E##/` (runner + `out/`) and a decision file
  `docs/experiments/E##.md` written in the Teacher register.
- Sub-agent reports are `docs/log/<TASK>.md`, immutable after merge.

## 6. Design

### 6.1 Four durable shapes and two function signatures

1. `Observation` (immutable, append-only): the only thing a provider module produces.
2. `Activity` (rebuildable): the only thing measures read.
3. `Evidence`: the only way a derived, comparative, associative or predictive number is
   written or returned.
4. `Series`: the only input to a forecaster.
5. `parse(surface, raw, ctx) -> list[Observation]` per provider module, plus a
   `CAPABILITIES` constant.
6. `forecast(context: Series, horizon, future_covariates=None) -> ForecastResult` per
   forecaster; baselines are pure functions of the context array behind the same signature.

Everything else the spec lists as a table (environment fingerprints, captures, repo
snapshots, changes/attempts, outcomes, experiment runs, policy interventions) is an
observation type emitted by Telltale itself or received on the external API. Spec 10.1
(late evidence appends another observation) and 14.6 (the intervention is an observation)
say so; spec 2.3's "stored separately" is satisfied by distinct observation_type values.

### 6.2 Model (src/telltale/model.py)

```python
@dataclass(frozen=True)
class Observation:
    observation_id: str            # ULID: primary key order is arrival order
    capture_id: str
    observation_type: str          # closed vocabulary, section 6.3
    surface: str                   # otel_logs|otel_metrics|hook|stream|exec_json|transcript|rollout|telltale|external
    provider: str                  # claude|codex|telltale|external
    adapter: str                   # "claude@1" (module name @ parser version)
    ingest_ts: str                 # ISO 8601 UTC with Z
    provider_ts: str | None        # provider clock, ISO 8601 UTC
    provider_session_id: str | None
    environment_fingerprint_id: str | None
    repo_id: str | None
    schema_version: int            # 1
    correlation_ids: dict[str, str]   # tool_use_id, prompt_id, request_id, turn_id, agent_id,
                                      # parent_tool_use_id, task_id, attempt, external_run_id
    payload: dict[str, Any]        # allowlisted, bounded
    redaction: dict[str, list[str]]   # {"dropped": [...], "truncated": [...], "redacted": [...]}

@dataclass(frozen=True)
class Activity:
    activity_id: str
    capture_id: str
    activity_type: str   # lifecycle|model_request|tool_call|file_read|file_edit|command|
                         # verification_run|compaction|subagent|repo_snapshot|repo_commit|
                         # correlation|outcome
    actor: str           # agent|subagent:<id>|telltale|external
    started_at: str
    ended_at: str | None
    fields: dict[str, Any]
    provenance: dict[str, list[str]]   # field name -> observation ids
    reducer_version: str
    claim_class: str = "derived"

@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    metric: str
    value: float | int | None
    unit: str
    claim_class: str     # derived|comparative|associative|predictive (never observed, never causal)
    coverage: str        # observed|partial|derived|unavailable
    source: list[str]    # activity or observation ids; non-empty unless coverage == unavailable
    capture_id: str | None
    cohort: dict[str, Any] | None
    environment_fingerprint_id: str | None
    reducer_version: str
    assumptions: list[str]
    warnings: list[str]
    # constructors: Evidence.derived(...), .comparative(...), .associative(...), .predictive(...)
    # each raises ValueError on empty source (unless unavailable) or a wrong claim_class

@dataclass(frozen=True)
class Series:
    series_id: str
    clock: str                       # request|attempt|change
    cohort: dict[str, Any]           # repo_id, environment ids, provider, task template, ...
    columns: list[ColumnSpec]        # name, unit, role (target|past_covariate|future_covariate), coverage
    rows: list[list[float | None]]   # None means "unknown"; never zero-filled
    row_meta: list[RowMeta]          # row_key, row_end_ts, env_fingerprint_id, provenance ids
    changepoints: list[int]          # row indices where the environment fingerprint changed
    missingness_policy: str          # exclude | refuse; no policy imputes
    reducer_version: str

@dataclass(frozen=True)
class ForecastResult:
    forecaster: str; checkpoint: str | None; horizon: int
    point: list[list[float]]; quantiles: list[list[list[float]]] | None; quantile_levels: list[float]
    targets: list[str]; covariates: list[str]; missingness_policy: str
    warnings: list[str]; claim_class: str = "predictive"
```

ULIDs: 26-char Crockford base32 from time + `os.urandom`, ~15 lines in model.py.

### 6.3 Observation types (closed vocabulary; the allowlist in sanitize.py is the schema)

Telltale-emitted: `telltale.capture_started` {provider, argv_shape (executable + flag
names only), content_level, surfaces_configured, provider_session_id_requested, task_id,
attempt, experiment, worktree_id}, `telltale.capture_ended` {exit_code, duration_ms,
surfaces_received: {surface: count}}, `telltale.environment` {spec 9.2 fields as values or
sha256 hashes: provider, runtime_version, model, effort, tool_set_hash, mcp_names_hash,
instruction_hashes: {path: {sha256, bytes}}, settings_hash, sandbox_posture,
capture_modes, content_level; fingerprint id = "env_" + sha256 of canonical payload},
`telltale.repo.identity` {repo_id, worktree_id, root_hash, head, branch, base_sha,
remote_fingerprint, dirty_tree_hash}, `telltale.repo.snapshot` {trigger, head,
dirty_tree_hash, diff_hash, files_changed, additions, deletions, renames,
staged_files, unstaged_files, untracked_count, per_file: [{path, additions, deletions,
patch_hash}]}, `telltale.repo.commit` {sha, parents, tree, committed_ts, files_changed,
additions, deletions, link_confidence in {explicit, provider_reported,
tree_match_during, tree_match_after, heuristic}}.

Claude: `claude.otel.<event>` for api_request, api_error, api_refusal, tool_result,
tool_decision, user_prompt, assistant_response, permission_mode_changed,
mcp_server_connection; `claude.otel.metric` {name, value, attributes allowlisted};
`claude.hook.<HookEventName>`; `claude.stream.<type>` for init, assistant, user,
compact_boundary, api_retry, result; `claude.transcript.<kind>` (backfill, wave 2).

Codex: `codex.exec.<event>` for thread_started, turn_started, turn_completed,
turn_failed, item (with item_type), error; `codex.otel.<event>` (vocabulary fixed by E02);
`codex.hook.<HookEventName>`; `codex.rollout.<kind>` (backfill, wave 2).

External API: `external.correlation` {external_system, external_run_id, component_id,
task_id, attempt, provider_session_id}, `external.outcome` {kind in
{mechanical_verification, adversarial_review, merge_decision, revert_or_repair,
runtime_signal}, status, categories, timestamp, external_run_id, component_id, attempt},
`policy.intervention` {advisory_id, action, policy_version, external_system}.

Never persisted at any level: prompt text, assistant text, tool_response bodies,
Edit/Write/MultiEdit contents (old_string, new_string, content), command output,
environment variable values, reasoning. At level 1 a Bash tool_result's exit code is
derived from the `Exit code N` pattern of the result text before the text is dropped
(coverage derived); a `git_commit_id` from tool_parameters is kept (provider_reported).

### 6.4 Sanitization (src/telltale/sanitize.py)

`ALLOWLIST: dict[observation_type, dict[field, Kind]]` with Kind in {SCALAR, PATH, COMMAND,
ENUM, ID, SIZE}. Unknown field: dropped and recorded (diagnostics kind unknown_field): fail
closed (spec 20.2). Strings bounded to 512 chars, payload to 8 KB after sanitization;
truncation recorded in `redaction.truncated`. Secret scrub on every kept string:
patterns for AKIA..., sk-ant-..., sk-..., ghp_/gho_..., `-----BEGIN ... PRIVATE KEY`,
`Bearer <token>`, `key=value` where value has 32+ chars of base64/hex; replaced by
`<redacted:N>` and listed in `redaction.redacted`.

PATH: absolute paths under the capture's repo root become repo-relative; paths elsewhere
become `<outside>/<sha256 prefix 8>`; the home directory never survives. Level 0 drops
paths entirely.

COMMAND normalization (decides spec open question 3): shlex-split (fallback: one token);
split on `|`, `&&`, `||`, `;`; per segment keep argv[0] basename, the first two bare
tokens matching `^[a-z][a-z0-9_.-]{0,31}$` (subcommands like `uv run pytest`, `git
commit`, `npm run test`), every token starting with `-` with any `=value` stripped, and
path-like tokens relativized; every other token becomes `_`. Bounded to 200 chars.
Level 0 keeps only the basenames. `normalization_version` recorded in the payload.

### 6.5 Storage (src/telltale/store.py)

Six STRICT tables and one view. JSON columns have `CHECK(json_valid(col))`.

- `observations` (insert-only): observation_id PK, capture_id, observation_type, surface,
  provider, adapter, provider_session_id, provider_ts, ingest_ts NOT NULL,
  environment_fingerprint_id, repo_id, schema_version, correlation_ids JSON, payload JSON,
  redaction JSON. Indexes (capture_id, observation_id), (provider_session_id),
  (observation_type).
- `activities`: activity_id PK, capture_id, activity_type, actor, started_at, ended_at,
  fields JSON, provenance JSON, reducer_version. Index (capture_id, started_at).
- `evidence`: evidence_id PK, capture_id NULL, metric, value REAL NULL, unit, claim_class
  CHECK IN ('derived','comparative','associative','predictive'), coverage CHECK IN
  ('observed','partial','derived','unavailable'), source JSON, cohort JSON,
  environment_fingerprint_id, reducer_version, assumptions JSON, warnings JSON, created_at.
- `series_snapshots`: series_id PK, clock CHECK IN ('request','attempt','change'), cohort
  JSON, columns JSON, rows JSON, row_meta JSON, changepoints JSON, missingness_policy
  CHECK IN ('exclude','refuse'), reducer_version, built_at.
- `forecast_runs`: forecast_run_id PK, series_id, target, variant, ordering CHECK IN
  ('true','placebo_block','placebo_row'), placebo_seed NULL, horizon, c_min, stride,
  forecasters JSON (names, checkpoint, license), windows JSON (per window: origin,
  ctx_start, n_ctx, actual, per-forecaster point, quantiles, wall_ms, flags), metrics
  JSON, decision JSON NULL, scenario JSON NULL, missingness_policy, warnings JSON,
  assumptions JSON, claim_class CHECK (claim_class = 'predictive'), created_at.
- `diagnostics`: diagnostic_id PK, capture_id NULL, ingest_ts, kind CHECK IN
  ('parse_failure','dropped','conflict','unknown_field','level2_raw','launcher'),
  observation_id NULL, detail TEXT (bounded 2 KB). The only table with age-based deletion.
- `captures` VIEW: capture_id, provider, repo_id, first_ts, last_ts, observation_count,
  from observations grouped by capture_id.

`rebuild([capture])`: delete derived rows for the scope, recompute from observations.
`purge <capture>`: delete from observations and diagnostics by capture_id, then rebuild.
`purge --diagnostics-older-than N`: age cutoff. No other deletion path exists.

One writer thread owns one connection (WAL, synchronous=NORMAL, busy_timeout 5000 ms);
readers open their own read-only connections. Handlers parse and sanitize on the request
thread and `put_nowait` a batch into a bounded `queue.Queue(maxsize=10000)`. On
`queue.Full`: a batch whose observation types are all DROPPABLE (otel_metrics, stream
partial messages, api_retry) is dropped immediately; any other batch waits
`put(timeout=0.25)` and is dropped on timeout, so a client sees at most 250 ms. Drops
increment a per-surface counter the writer flushes as one diagnostics row (kind dropped)
when the queue drains. The writer takes one item, drains up to 500 more, commits them in
one transaction. A sqlite3 error sets `store.down`, writes a diagnostic if it can, and
retries with backoff. Handlers return 200 with `{}` in every case (4xx/5xx would make the
OTel exporter retry and hooks stall: spec 5.2 fail open). `/healthz` alone tells the
truth: 503 with writer state, per-surface last-received time and drop counts.

### 6.6 Receiver (src/telltale/receiver.py)

`ThreadingHTTPServer` bound to 127.0.0.1. Routes: `POST /v1/logs` and `POST /v1/metrics`
(OTLP JSON: resourceLogs[].scopeLogs[].logRecords[] with body and attributes as
`{"key","value":{"stringValue"|"intValue"|"doubleValue"|"boolValue"}}`; resource attributes
carry `OTEL_RESOURCE_ATTRIBUTES` such as telltale.capture_id), `POST /hooks/<provider>`
(hook JSON), `POST /v1/stream/<provider>` (one JSONL line per request from the launcher
tee), `POST /v1/correlations`, `POST /v1/outcomes`, `POST /v1/policy_interventions`,
`GET /healthz`. Each POST: `capture_id` comes from the URL query, the resource attribute,
or the provider_session_id map the launcher registered; parse -> sanitize -> append;
any exception -> diagnostics(parse_failure) -> 200. Accepts `Content-Encoding: gzip`.
A file-mutation observation (Edit/Write/MultiEdit/NotebookEdit hook, codex file_change)
schedules a debounced (2 s) `repo.snapshot` when a launcher context is attached.

### 6.7 Providers (src/telltale/providers/claude.py, codex.py)

Each exposes `CAPABILITIES: dict[str, str]` (spec 9.1: request_usage, context_window,
compaction, tool_calls, file_paths, commands, subagents, each observed|partial|
unavailable|derived per surface set), `parse(surface, raw, ctx) -> list[Observation]`,
and `launch(argv, port, level, session_id) -> LaunchPlan {argv, env, tee}` used by the
launcher. Measured coverage per capture is computed by the activities reducer from
`surfaces_configured` versus surfaces that actually delivered observations; a configured
surface that delivered nothing marks its capabilities `unavailable` with a diagnostic.
This is how "0 compactions" and "compaction unobservable" stay different (spec 9.1).

### 6.8 Repository and environment observers (src/telltale/repo.py, env.py)

Git only, via subprocess with a 5 s timeout, never shell=True. Identity at capture start:
`git rev-parse --show-toplevel`, HEAD, branch, merge-base with the default branch when
resolvable (base_sha), remote URL sha256 (remote_fingerprint), `git rev-parse --git-dir`
versus `--git-common-dir` (worktree_id = sha256 of the git-dir path relative to the common
dir), dirty_tree_hash = sha256 of `git diff HEAD --numstat` plus `git status --porcelain`.
Snapshot: `git diff --numstat`, `git diff --cached --numstat`, `git diff | sha256`, per-file
patch hashes (`git diff -- <path> | sha256`), untracked count. No patch bodies. Commit
linkage at capture end and on `sessions --link-commits`: commits since start whose tree
matches a snapshot (tree_match_during), commits within 10 minutes after end
(tree_match_after), provider-reported `git_commit_id` (provider_reported), explicit
correlation (explicit). Temporal proximity alone is never a link (spec 12.3). Two captures
overlapping in time on one worktree_id mark their repo_snapshot activities
`attribution: ambiguous` (spec 12.4).

Environment fingerprint: provider, runtime version (`claude --version` / `codex --version`
or app.version from events), model and effort (from argv, then from the first
api_request), tool set hash (stream init tools list) and MCP names hash, instruction
surfaces (sha256 and byte size of CLAUDE.md, AGENTS.md, .claude/rules/*.md, ~/.claude/
CLAUDE.md, ~/.codex/AGENTS.md when present; content never stored), settings hash
(permission mode, hook event names, allowed tools), sandbox posture (codex sandbox
policy, claude permission mode), capture modes and content level. A model or runtime
change inside a capture emits a second fingerprint and a changepoint (spec 8.1).

### 6.9 Launcher (src/telltale/launch.py)

`telltale run [--provider auto|claude|codex] [--level 0|1|2] [--task-id X --attempt N
--experiment E] -- <argv...>`. Steps: load config (`$TELLTALE_HOME`, default
`~/.telltale/`: `telltale.db`, `config.json`); start Receiver in-process on a free port;
emit repo.identity and environment; emit capture_started with surfaces_configured;
provider.launch() returns the child argv (Claude: `--settings '<json with http hooks>'`
and `--session-id <uuid>` when `-p` is present; Codex: `-c` otel keys from E02 plus
hook config when E02 shows hooks work under exec) and env (Claude: the OTel variables of
2.1 with `OTEL_LOGS_EXPORT_INTERVAL=1000`, `OTEL_METRIC_EXPORT_INTERVAL=5000`,
`OTEL_RESOURCE_ATTRIBUTES=telltale.capture_id=<id>`, `OTEL_LOG_TOOL_DETAILS=1` at level
1 and above); spawn the child with stdin passthrough; when the child's argv already
requests `--output-format stream-json` or `--json`, tee stdout: each line is POSTed to
`/v1/stream/<provider>` and echoed unchanged to the real stdout (other consumers see the
same bytes); after exit wait up to the measured late-export window (E01 measures it;
default 6 s) for the last OTel batch; final repo snapshot; commit linkage; capture_ended;
flush; stop the receiver; exit with the child's code. Any Telltale exception is a
diagnostic (kind launcher) and never changes the child's behaviour or exit code.

`telltale daemon [--port]` runs the same receiver in the foreground on a fixed port for
the owner's opt-in day-to-day capture; `telltale setup claude|codex --print` prints the
settings JSON / config TOML that points at it (never writes; `--apply` prints a refusal
naming the owner decision).

### 6.10 Activities (src/telltale/activities.py) and commands (commands.py)

`rebuild(store, capture_id)`: reads all observations of the capture, correlates by ids
(tool_use_id across otel tool_result, hooks and stream blocks; request_id/message uuid
across api_request and stream assistant messages; agent_id/parent_tool_use_id for
subagents), and writes activities with per-field provenance. Rules:
- model_request: from otel api_request (primary), stream assistant usage (secondary),
  transcript (backfill). `request_index` = rank by provider_ts within the capture.
  Conflicting token values from two surfaces keep the primary and record a diagnostics
  row (kind conflict) with both ids (spec 7.2).
- tool_call: name, started_at (earliest observation), ended_at, duration_ms, success,
  path (file tools), command_norm and category (from commands.classify), exit_code
  (derived), parent (subagent), size facts.
- file_read (Read, Glob, Grep, NotebookRead; codex read-like commands: cat, sed -n, rg,
  grep, ls, find, head, tail), file_edit (Edit, Write, MultiEdit, NotebookEdit; codex
  file_change), command (Bash; codex command_execution), verification_run (a command
  whose category is test|typecheck|lint|format|build|benchmark|security_scan, with scope,
  exit status, repository dirty_tree_hash at the time from the latest snapshot, and
  `superseded_by_edit` once a later file_edit exists).
- compaction: from hooks PreCompact/PostCompact (trigger), stream compact_boundary
  (pre_tokens), api_request with query_source=compact (tokens of the summarizing request);
  post_tokens only when a surface supplies it (transcript backfill), else coverage partial.
- subagent: SubagentStart/Stop plus parent_tool_use_id; token attribution via
  agent.name/query_source, never summed twice (spec 13.6).
- repo_snapshot, repo_commit, lifecycle (capture start/end, session start/end, model
  switch), correlation, outcome: one activity per observation.
- Coverage per capability computed as in 6.7 and stored on the lifecycle activity.

`commands.classify(command_norm) -> (category, scope)`: table keyed by (executable,
subcommand prefix): test (pytest, `uv run pytest`, `python -m pytest`, `npm test`,
vitest, jest, `go test`, `cargo test`), typecheck (mypy, pyright, tsc, ty), lint (`ruff
check`, eslint, flake8, pylint), format (`ruff format`, black, prettier), build (`uv
build`, `npm run build`, make, `cargo build`, `docker build`), benchmark (pytest-benchmark,
hyperfine), security_scan (gitleaks, bandit, `uv audit`, `npm audit`), package_op (`uv
sync|add|lock|pip`, pip, `npm install|ci`), git (git), process_mgmt (kill, pkill, ps,
sleep, wait), shell (ls, cat, sed, grep, rg, find, head, tail, echo, wc, which),
unknown otherwise. scope = targeted when a path-like or `-k` argument is present on a
test command, full when none, unknown otherwise. `CLASSIFIER_VERSION` constant.

### 6.11 Measures (src/telltale/measures.py) and the session summary

`summarize(store, capture_id) -> (summary: dict, evidence: list[Evidence])` following spec
Appendix B exactly: coverage, usage, work, verification, context, claim_class derived,
forecast_readiness. Metric definitions (spec 13):
- verification: agent_test_runs, failed_test_runs, fail_to_pass_cycles (a failed
  verification followed by a passing one of the same category), edits_after_last_successful_test,
  edit_epochs_with_verification and without (an epoch is a maximal run of file_edits with
  no verification_run in between), last_verification_repo_hash, targeted_vs_full counts.
- edit turnover: unique_files_changed, file_revisits (an edit to a file edited earlier
  in the capture), max_diff_lines vs final_diff_lines (from snapshots: additions plus
  deletions), reversions (a snapshot diff_hash equal to an earlier one after an edit),
  post_failure_revisits (edit to a file within N=3 edits after a failed verification).
- exploration: unique_files_read_before_first_edit, unique_files_read, search_ops,
  directories_traversed (distinct parent dirs of read paths), read_to_edit_ratio,
  explored_to_final_ratio (distinct dirs read / distinct dirs edited).
- context/token burden: totals by type (fresh_input = input_tokens, cache_read,
  cache_creation, output), model_requests, compactions, pre_compaction_tokens,
  post_compaction_tokens (when observed), occupancy_ratio only with a trustworthy
  denominator (Codex model_context_window; Claude stream result modelUsage.contextWindow)
  with denominator_source recorded, else null.
- stable-state work intervals: maximal intervals where the latest snapshot diff_hash and
  the latest verification signature (category, exit status, repo hash) are unchanged while
  model_request or tool_call activities continue; count, durations, tokens inside, what
  ended each (edit, verification, capture end).
- delegation: subagent_count, subagent_tokens (attributed), direct_vs_delegated_tool_calls.
Each metric is an Evidence with claim_class derived, coverage from the capability
coverage of the surfaces it needed, source = the activity ids it read.

`compare(a, b)` (wave 2) prints both evidence vectors side by side and refuses cohort
percentiles unless a stated cohort (same provider, runtime major version, model, content
level, and n >= 10) exists; percentiles are Evidence comparative with the cohort recorded.

### 6.12 Series, forecasting lab and experiments

Pre-registered constants (printed in every report; changing one after seeing results
requires a new experiment id): delta = 0.10, w = 0.60, k_min = 20 windows, c_min = 32 rows
on the request clock and 16 on the attempt and change clocks, H in {1, 4}, stride s = H,
placebo block B = max(2, H), placebo seeds R = 5, baseline window 8, threshold tau = q80 of
the first c_min rows unless the target registry declares an absolute value, relative
resolution 0.25 for demotion. These are decision rules, not estimates of anything; the
numbers they are compared against are always measured.

**SeriesCompiler (series.py).** `build(store, clock, cohort, missingness_policy="exclude")
-> Series`. One frame per history: one capture for the request clock; one repository
lineage for the attempt and change clocks. Rows are produced by a prefix fold over
activities sorted by clock position, so row i is emitted from state that has consumed only
activities at positions <= i: no look-ahead by construction. Build-time invariant checked
and printed by `series check`: `max(provider_ts of provenance[i]) <= row_end_ts[i]` and
`row_end_ts` non-decreasing. Outcomes about earlier rows (reverts, repairs) are recorded at
the row where they were observed, never back-filled. `env_fingerprint_id` lives in
`row_meta`, not in a numeric column; `changepoints` are the indices where it differs from
the previous row; the only model-visible form is the 0/1 column `env_changed`.
`series_id` = sha256 of clock, cohort, column schema, reducer_version and rows.

Request clock (row = one model request): fresh_input_tokens, cache_read_tokens,
output_tokens (request_usage), request_duration_ms (Codex: derived from turn timestamps,
partial), compaction_before (0/1), tool_calls_since_prev, files_edited_since_prev (level 1
only), verification_runs_since_prev, verification_seen (0/1), last_verification_failed
(0/1), env_changed.

Attempt clock (row = one attempt in a repository lineage, in start order; the retry ordinal
is a column because per-component retry series have 1 to 5 rows and cannot be
backtested; delta from spec 10.3 recorded in the ADR log): attempt_of_component,
model_requests, fresh_input_tokens_total, compactions, duration_ms, verification_passed
(0/1 from outcome kind mechanical_verification), review_fail_count (adversarial_review),
accepted (0/1 from merge_decision; join key to the change clock), env_changed.

Change clock (row = one change landed on the tracked base): files_changed, lines_added,
lines_removed, subsystems_touched (distinct top-level directories), test_files_changed,
dependency_delta (lockfile or manifest changed), attempts_to_land, fresh_input_tokens_total,
cache_read_tokens_total, compactions, verification_cycles, edit_turnover_ratio,
stable_state_intervals, unique_files_read (summed over the attempts that landed it),
env_changed.

**Forecaster interface (forecast/__init__.py).** `FORECASTERS: dict[str, Forecaster]`
where a Forecaster is `forecast(window: Window, horizon: int) -> ForecastResult` and a
Window is a copy of the context rows (targets plus variant covariates) with no store
handle. `TARGETS` registry: target -> {clock, unit, nonnegative, c_min, H, stride, k_min,
threshold_rule, variant}. Every forecaster sees the identical window; all windows of one
run go to one TimesFM `predict_batch` call.

**Baselines (forecast/baselines.py, pure functions).** Persistence `yhat_{o+h} = y_{o-1}`;
RollingMedian and RollingMean over the last 8 context values; LocalDrift `y_{o-1} + h
(y_{o-1} - y_{o-9}) / 8`, undefined when n_ctx < 9 (falls back to persistence and flags).
No seasonal naive: no clock has a season. Baseline quantiles = point plus the empirical
quantiles of the baseline's own one-step residuals inside the context (needs >= 8
residuals, else calibration and WQS are "not assessable" for that baseline).

**Rolling-origin backtester (forecast/backtest.py).** (1) registry lookup; (2) keep only
rows where the target's coverage is observed (capability-homogeneous, ADR-012); drop any
window containing None in the target or a selected covariate, counting each drop with its
reason; nothing is imputed; (3) origins `o in range(c_min, N - H + 1, s)`, `ctx_start =
max(last changepoint <= o, o - 512)`, skip when `o - ctx_start < c_min` (reason
regime_too_short); windows with a changepoint inside `[o, o + H)` are excluded from the
headline and listed; (4) window = copy of `rows[ctx_start:o]`, `actual = target[o:o + H]`;
(5) every forecaster runs on the identical window; (6) per window store origin, ctx_start,
n_ctx, H, actual, per-forecaster point[H], quantiles[H][9], wall_ms, flags; one
`forecast_runs` row per (series, target, variant, order) with the windows embedded as JSON.

Metrics: point error = MAE of the median forecast (quantile index 4) per window, aggregated
by mean and median across windows (MASE rejected: zero denominator on constant contexts;
RMSE rejected: heavy-tailed counts); skill = 1 - E_model / E_baseline. Calibration:
`coverage_q` = share of (window, step) points with `y <= yhat_q` for the nine q; report
`max_q |coverage_q - q|` and central 80 percent coverage with n; flag when any
`|coverage_q - q| > 2 sqrt(q (1 - q) / n)`. WQS = `2 * sum_q sum_{w,h} rho_q(y, yhat_q) /
(9 * sum_{w,h} |y|)` with `rho_q(y, yhat) = max(q (y - yhat), (q - 1)(y - yhat))`;
unnormalized mean pinball loss when `sum |y| = 0`. Lead time: only windows with `y_{o-1} <
tau` count; `a* = min{h : y_{o+h} >= tau}`, `f* = min{h : yhat_{0.8,o+h} >= tau}`; hit when
both exist (lead a*, timing error |a* - f*|), miss when only a* exists, false alarm when
only f* exists; report hit rate, false-alarm rate, median lead.

**Chronology placebo.** `block_shuffle(rows, B, seed)`: cut the context into consecutive
blocks of B whole rows, permute the blocks, concatenate. Whole rows move together, so the
multiset of rows and the contemporaneous covariate-target relations are preserved exactly;
recency and dependence at lags above B are destroyed. Second control: B = 1 (destroys
dependence at every lag). Only the context is shuffled; origin, test rows and actuals stay
true, so every placebo window is paired with its true-order twin. R = 5 seeds; median and
range reported. Baselines also run on shuffled contexts: persistence must get worse,
otherwise the placebo is broken and the run is invalid.

Decision per (target, clock, variant): E_M = mean MAE of the model in true order; E_B =
min over baselines in true order; E_P = median over seeds of the model's mean MAE on
shuffled contexts; W_MB = share of windows where the model beats the best baseline; W_MP =
share of windows where the true-order model beats its placebo twin.
- n_windows < k_min: not assessable, no label.
- baseline sufficient: `E_M > (1 - delta) E_B` or `W_MB < w`.
- temporal evolution: not baseline sufficient, and `E_M <= (1 - delta) E_P` and `W_MP >= w`,
  and both inequalities hold separately in each half of the origin range (spec 15.7
  anti-cherry-pick clause).
- conditional prediction: otherwise. For a covariate-free variant the report says "level
  prediction: the gain is distributional".

**Readiness checklist (forecast readiness, spec 15.3)**: `readiness(series, target)`
prints pass/fail per line and sets `forecast_readiness[clock]` in the session summary:
(1) coverage: target and variant columns observed or derived-from-observed on retained
rows; (2) windows: `floor((N - H - c_min) / s) + 1 >= k_min` after changepoint exclusions;
(3) missingness: zero None cells inside any retained window under policy exclude; (4)
baselines: n_ctx >= 9 at every origin; (5) changepoints: at least one regime with >= c_min
+ H rows; (6) placebo: n_ctx >= 2B at every origin; (7) variation: MAD of the target over
retained rows > 0; (8) threshold: tau computable or declared. Missingness policies are
`exclude` (default) and `refuse` (any None in the frame refuses the build). No policy
imputes.

**A/B/C ablation (H7; change clock; target excluded from its own covariates; all
covariates past-only; identical origins and actuals across variants):** A = files_changed,
lines_added, lines_removed, subsystems_touched, test_files_changed, dependency_delta; B = A
+ fresh_input_tokens_total, cache_read_tokens_total, compactions, attempts_to_land,
env_changed; C = B + verification_cycles, edit_turnover_ratio, stable_state_intervals,
unique_files_read. Targets: attempts_to_land, fresh_input_tokens_total,
verification_cycles. At most 15 variates. C "adds temporal information" iff `E_C <= (1 -
delta) min(E_A, E_B)` and `W >= w`; otherwise "diagnostic only". The placebo of the winning
variant is reported beside it.

**One-step candidate conditioning (H8; spec 15.8).** Timing = the merge decision of change
o: known are row o's A block and its fingerprint; unknown are post-merge quantities, so
targets are post-merge columns of row o: merge_verification_ms, merge_verification_failed
(0/1), rework_within_3 (0/1, delayed label; origins limited to `o <= N - 3`).
attempts_to_land is known at merge time and is forbidden as a candidate target.
Unconditioned run: context `[ctx_start, o)`, H = 1, past-only covariates. Conditioned run:
same context and origin plus row o's A block as past-future covariates of length n_ctx +
1 (edge padding). Pairs matched by origin; report the paired median difference in MAE and
pinball loss and each run's calibration. Every output and the assumptions field carry:
"The difference between the conditioned and unconditioned forecast measures how much the
candidate's known features change the forecast; it is not the effect of merging the
candidate, because only one future is observed." The report writer refuses the words
cause, impact and would (ADR-014).

**H2 and H3 protocol (experiments.py).** One run = `telltale run` of a task spec (repo,
base sha, prompt file hash, deterministic acceptance command executed by the harness, not
the agent) in a disposable worktree under a pinned fingerprint. Recorded per run:
capture_id, env_fingerprint_id, repo_id, base sha, task hash, repetition index, wall time,
cost, coverage record, acceptance result, and the evidence vector of spec 13.7. Pilot: 5
repetitions per condition. H3 varies one launch flag at a time (effort first, then model)
at 5 per arm because flags need no repository modification (spec 5.1.1); the harness
asserts fingerprints identical within a condition and differing only in the declared
factor between arms. Scale to `N = min(20, N_needed)` only for measures the pilot shows
resolvable. Statistics per condition: n, median, scaled MAD (`s = 1.4826 MAD`), IQR, min,
max, full sorted values. Between arms: Hodges-Lehmann shift, Cliff's delta, exact
Mann-Whitney p. Minimal detectable median difference at the current n: `MDD = 2.8 s
sqrt(2 / n)` (2.8 = z_{0.975} + z_{0.80}, the two-sample normal approximation at alpha
0.05 and power 0.80, with s the scaled MAD as a robust spread). Demotion: a measure is
withheld from repository comparison while `MDD > 0.25 median`; the report prints
`N_needed = ceil(2 (2.8 s / (0.25 median))^2)`. H3 per factor: material environment effect
iff `|HL shift| > MDD` (the fingerprint becomes a changepoint for that measure); otherwise
"not resolved at n", never "no effect".

**Where TimesFM-3 bites (forecast/timesfm.py; facts from the 3.0.0 source):** the
forecaster left-pads to a multiple of 32 with a mask and enforces no minimum (the adapter
enforces c_min itself and rejects shorter contexts with a named reason; all windows of a
run go in one `predict_batch` sorted by length); the horizon is rounded up to 64
internally, so H = 1 costs as much as 64, and past-future covariates must span context +
64 unless `padding_mode="edge"` (the adapter passes n_ctx + H with edge padding and records
"horizon covariates beyond step H are edge-replicated by the model"); the 32-variate cap
is not enforced in the forecaster (the adapter asserts `1 + past_only + past_future <=
32` and never subsamples); the forecaster trims leading all-NaN columns, interpolates
interior and trailing NaN, zero-fills all-NaN rows and raises nothing (the adapter asserts
`np.isfinite` on every array); `make_positive` from the registry's nonnegative flag,
`sort_quantiles` default, `use_znorm` off, point = quantile index 4; `device="cpu"`
pinned unless E03 shows MPS works, model loaded once per process, wall_ms per call
recorded; the weights license `timesfm-non-commercial-license-v1.0` is recorded in every
ForecastRun and printed by every forecast command.

**Forecast integration tests (tests/integration/test_forecast_contracts.py):** (1) no
look-ahead: a stub forecaster records each window; after every row at index >= origin is
set to 1e9 and the run repeated, the recorded windows, baseline outputs and placebo
contexts are identical, and every provenance timestamp is <= its row_end_ts; (2) placebo
and metrics: on a seeded random walk `block_shuffle` yields the same sorted rows,
persistence MAE on shuffled contexts exceeds true order, and MAE, coverage, WQS and lead
time on a fixed fixture equal hand-computed values; (3) decision and run: a stub worse
than persistence is labelled "baseline sufficient", one better than baselines but
unchanged under placebo is labelled "conditional prediction", and the persisted
ForecastRun contains missingness_policy, placebo definition, baseline outputs, license,
`claim_class = "predictive"` and, for a candidate run, the mandatory associative sentence.

### 6.13 CLI (src/telltale/cli.py, argparse; report.py renders)

`doctor` (round-trips one synthetic event through every endpoint of a temporary in-process
receiver and reads it back; checks git, claude, codex, uv, timesfm importability; prints
per-surface results; exit 1 naming the failing surface), `setup claude|codex --print`,
`run`, `daemon`, `sessions [--repo] [--limit] [--link-commits]`, `show <capture>` (Appendix B
JSON; coverage first, diagnostics count last), `timeline <capture>` (activities table:
time, type, actor, name/path/command_norm, duration, outcome), `explain <capture>
<metric>` (evidence -> activities -> observations with payloads), `compare <a> <b>`,
`rebuild [<capture>]`, `purge`, `schema` (prints the observation JSON schema and the
allowlist), `export --format jsonl|parquet --out DIR`, `experiment repeat|environment|probe
<spec.json>`, `series build --clock request|attempt|change [--cohort key=value...]`,
`forecast [--target] [--horizon]`, `forecast backtest|placebo|ablate|candidate`.
Every printed number is an Evidence field; the claim_class column is never omitted. No
quality_score or difficulty_score anywhere (spec 17.2).

### 6.14 Repository layout

```
telltale/
  AGENTS.md  CLAUDE.md (one line: @AGENTS.md)  README.md  pyproject.toml  uv.lock
  .python-version  .pre-commit-config.yaml  .gitleaks.toml  .gitignore
  .github/workflows/ci.yml  .github/dependabot.yml
  scripts/precommit/{cyclomatic_ratchet,file_length_ratchet,check_no_mocks,check_agent_docs_sync}.py
  src/telltale/{__init__,model,store,sanitize,receiver,repo,env,launch,activities,commands,
                measures,series,experiments,report,cli}.py
  src/telltale/providers/{__init__,claude,codex}.py
  src/telltale/forecast/{__init__,baselines,backtest,timesfm,candidate}.py
  tests/integration/{conftest,fake_agent,test_privacy,test_capture_to_summary,test_fail_open,
                     test_forecast_contracts,test_doctor_setup,test_live}.py
  fixtures/claude/<runtime_version>/<scenario>/{otel_logs.jsonl,otel_metrics.jsonl,hooks.jsonl,stream.jsonl,MANIFEST.md}
  fixtures/codex/<runtime_version>/<scenario>/...
  fixtures/golden/<provider>/<scenario>/{show.json,timeline.txt}
  experiments/E##/{run.py,out/}   docs/experiments/E##.md   docs/log/<TASK>.md
  docs/spec/{telltale-architecture.md,telltale-architecture.docx}  docs/design/{00-digest,01-design,02-protocol}.md
  briefs/<TASK>.md
```

### 6.15 Branding and public readiness (owner request, 2026-09-01)

The repository will be made public later, so it is built public-ready from the first
commit: a logo designed in-repo as SVG (light and dark variants plus a wordmark), a README
with a hero, badges, quickstart, privacy statement, claim-class table, architecture
diagram and a documentation index; LICENSE, CONTRIBUTING.md, SECURITY.md (privacy contact
and disclosure), CHANGELOG.md (keep-a-changelog), CITATION.cff (research project);
repository description and topics set with `gh repo edit`. Badges that query the
repository (CI status) render only for viewers with access while private; static badges
(python, uv, ruff, mypy strict, pre-commit, license) render regardless. W0-T6 builds it;
W6-T5 verifies the quickstart from a fresh clone before the public flip.

## Amendments from wave 0 (2026-09-01 to 2026-09-02)

The design above is the plan as approved. Each line below records a change forced by
running the system, with the task that measured it in parentheses. A later section
number refers to the design above.

- 6.8 worktree_id: sha256 of the git dir relative to the common dir is sha256(".") for
  every MAIN worktree of every repository (W0-T3 measured). Overlap detection and every
  capture record key on (repo_id, worktree_id). W1-T1 must pass both.
- 6.14 fixtures: captured fixtures live under fixtures/sources/<provider>/<version>/ because
  the shared pre-commit block exempts fixtures/sources/ from formatting, spelling and
  em-dash hooks; goldens stay under fixtures/golden/ and obey every hook.
- 6.8 dirty_tree_hash hashes the -z forms of numstat and status, not the human-readable
  commands. additions/deletions are None when any changed file is binary.
- 6.5 forecast_runs shape already updated in the plan (windows JSON per run).
- repo.py is 797 lines against the 800-line ratchet: any later change must move code out
  (e.g. commit linkage into repo_link.py) rather than grow the file.
- W0-T1: docs/ excluded from ruff (ruff format rewrites python inside markdown fences);
  pep621_dev_dependency_groups removed from deptry config (option withdrawn).
- 6.9 (E01): no late export on 2.1.257 (0 requests after child exit across 7 scenarios;
  last request 0.24 to 0.56 s BEFORE exit). Launcher grace period: none needed for Claude;
  keep a short bounded wait (measured value null, so use 2 s and record it as a guess to
  be replaced) - or better, wait until the SessionEnd hook has been received, then stop.
- 6.9 (E01): the child inherits VIRTUAL_ENV from `uv run`, which breaks `uv run pytest`
  inside the target repo. The launcher must remove VIRTUAL_ENV (and UV_PROJECT_ENVIRONMENT
  if set) from the child's environment and record that it did.
- 6.3 (E01): TELLTALEFAKE probes reached hooks and stream surfaces, never otel_logs or
  otel_metrics. Bash exit code is nowhere structured; stream tool_result text carries
  "Exit code N" only on failure.
- 6.4 (T2): tool_input, tool_parameters and tool_response are NEVER_PERSIST containers;
  provider parse() lifts the allowed scalars (command, file_path, git_commit_id, sizes)
  to top level before sanitize(). Contract stated in the W0-T4 brief.
- 6.4 (T2): the 8 KB payload bound drops an oversized per_file list whole; the repo
  observer or the launcher must cap per_file (first 100 entries, truncated flag) before
  wrapping a snapshot into an observation. Assign to W1-T1.
- (T2): store.py 798 and sanitize.py 799 lines at the 800-line ratchet; W0-T4 moves the
  allowlist table to allowlist.py and the selfcheck block to selfcheck.py before adding
  provider fields.
- 11.1 vs 6.4 (T2): level 0 keeps command basenames only (6.4); the spec's 11.1 reads
  stricter. Present at the gate as a decision taken, with the reason.
- (T2): a lone UTF-16 surrogate killed the writer thread once (UnicodeEncodeError is not
  sqlite3.Error); the writer now catches Exception and the sanitizer guarantees encodable
  strings. Worth a line in the privacy/fail-open test (W0-T5).
- 6.6 (T4): an unattributable record is stored under capture_id "unattributed" with a
  diagnostics row of kind launcher (not parse_failure): the record parsed; the wiring
  failed. 6.3 stream types: system messages are claude.stream.system.<subtype>.
- 6.7 (T4): launch() takes capture_id so OTel records carry telltale.capture_id.
- 6.4 (T4): result, summary and compact_summary carry prose and are dropped only by the
  allowlist gate; W0-T5 adds them to NEVER_PERSIST.
- (T4): hook bodies carry no timestamp; provider_ts is None on hook observations and the
  activities reducer must use ingest_ts for hooks (ordering by arrival).
- (T4): OTel tool_result_size_bytes and the stream block size differ (910 vs 568 on one
  Bash call); the stream number is stored as tool_result_content_bytes.
- (T4): providers/__init__.py get("codex") names a module that does not exist until W1-T3;
  a /hooks/codex POST is a parse_failure diagnostic and 200 meanwhile.
- 6.9 (E02): late export negative on all six exporting scenarios: no linger for Codex.
- W0-T1: docs/ excluded from ruff (ruff format rewrites python inside markdown fences);
  pep621_dev_dependency_groups removed from deptry config (option withdrawn).
- Tooling: experiments/ excluded from mypy and deptry (duplicate script names across
  experiments); ruff still lints them.
- 6.12 (E03): the TimesFM adapter must consume `predict_batch` as a generator, refuse
  NaN before the call (the model forward-fills trailing NaN silently), assert the variate
  count itself (the model does not), and record padding_mode in every ForecastRun; per-window
  calls are affordable at 0.35 s each on CPU, so the backtester may call once per window
  and still batch all windows of a run into one call when convenient.

## Amendments from wave 1 (2026-09-02)

Same rule as above: each line is a change forced by running the system, with the task
that measured it in parentheses.

- 6.9 (W1-T1): `Store.flush(timeout)` is the barrier the launcher waits on;
  `Receiver(default_capture=...)` attributes everything a `telltale run` receives; the
  daemon derives `cap_<sha256(session)[:24]>` per unregistered session. Launcher overhead
  213 ms per capture. The claude flags (`--settings`, `--session-id`) are inserted right
  after `-p`, because a python child rejects unknown options placed first. SIGINT is
  absorbed (the tty delivers it to the child's process group); SIGTERM is forwarded. The
  child loses VIRTUAL_ENV and UV_PROJECT_ENVIRONMENT, and the capture records that.
  `--session-id` is not inserted when `--resume` is present, so a resumed session keeps
  its id. The Claude launch plan lives in providers/claude_launch.py; claude.launch() is
  a lazy wrapper (orchestrator split at the 800-line ratchet).
- 6.8 (W1-T1, W1-T4): runtime_version comes from `<binary> --version` (first line,
  verbatim, bounded to 64 characters), cached under `$TELLTALE_HOME/versions.json` by the
  binary's resolved path and mtime. Provider binaries only: a generic child (python,
  make) fingerprints with runtime_version None. A cosmetic banner change is an
  environment change by construction.
- 6.10 (W1-T2): the reducer is activities.py plus correlate.py (one file was 1248 lines);
  reducer_version hashes activities.py, correlate.py, commands.py and measures.py. Hook
  observations carry no provider timestamp: lifecycle rows from hooks use ingest_ts with
  fields.clock "arrival" and the timeline prints no time for them. A count over an empty
  set is 0 only when the capability was observed; with coverage unavailable it is None.
  repo_id and environment_fingerprint_id live on the observation ROW, not in payloads;
  replayed fixtures have neither (no launcher stamped them). A test run piped into
  another program (`pytest | tail`) reports that program's exit status on every surface:
  failed_test_runs carries that warning rather than a guessed value.
  conversation_lineage_id is null until a resume link exists.
- 6.10 (W1-T3): Codex activities are built by activities_codex.py from the same field
  names. One Codex tool call has three id spaces (execution id on hooks, OTel and the
  rollout; the model's call id; the exec stream's item index): activities key on the
  execution id, the only one two surfaces agree on. A Codex turn is not a request
  (S1: 1 turn, 7 responses): turn totals live on a `turn` activity that no metric reads.
  Codex input_token_count INCLUDES cached tokens (measured twice): fresh_input_tokens =
  input - cached, total_input_tokens keeps the wire value. ttft_ms cannot be joined to
  a response (no id): it lives on the turn. request_id, duration_ms and cost_usd are on
  no Codex surface per response. unique_files_read is None for Codex: reads are shell
  commands and nothing extracts a path from a command string. `providers.prose()`
  relativizes absolute paths inside prose fields (the home directory was reaching the
  store through codex.exec.error); claude.py's own error field still has the gap.
  The classifier misfiles `UV_CACHE_DIR=... uv run pytest` as git (W2-T1 fixes
  commands.py). The Codex launch plan configures otel_logs, otel_metrics and exec_json;
  hooks are parsed but not configured by launch() (whether inline `-c hooks=` fires
  under exec needs measuring in a real capture).
- 6.12 (W1-T4): `experiments.repeat` runs each repetition in a detached worktree of the
  target repository, records correlation and outcome through the receiver of the runner's
  own process attributed by `?capture=`, refuses statistics across differing
  fingerprints naming the fields, and prints per-capture rows as derived and the
  statistics rows as comparative within the condition. vector() reads usage and tool
  counts until W2-T4 supplies the evidence vector; stats() returns n, median, scaled MAD,
  IQR, min, max and the sorted values. Between-arm statistics are W2-T3.
- 6.13 (W1-T4, W1-T5): `telltale purge <capture>`, `experiment repeat`, `series
  build|check|list` exist. main() is a dispatch table (the cyclomatic ratchet refused
  the eleventh branch). The doctor round trip moved to doctor.py (orchestrator, at the
  800-line ratchet after the W1-T4 and W1-T5 merge).
- 6.5 (W1-T5): the DDL lives in schema.py; store.py gained the readers series(),
  series_ids() and forecast_runs().
- 6.12 (W1-T5): the request-clock compiler emits one row per model_request from a fold
  over activities in clock position order (ended_at, else started_at; ties by activity
  id). row_meta.env_fingerprint_id is the environment_fingerprint_id column of the
  request's primary observation (the one observation read the compiler makes, metadata
  only). Under `refuse`, last_verification_exit refuses nearly every capture because its
  first row is None until a test has run; that is the rule as written, and whether a
  structural None should be exempt is an open decision. content_level is null in every
  cohort until activities.py lifts it onto the capture lifecycle row.
- 6.12 (E03): the TimesFM adapter must consume `predict_batch` as a generator, refuse
  NaN before the call (the model forward-fills trailing NaN silently), assert the variate
  count itself (the model does not), and record padding_mode in every ForecastRun.
  Per-window calls are affordable at 0.35 s each on CPU (worst seen 0.81 s); MPS agrees
  to 9.1e-7 relative on one probe and is about 2x faster per call but loads in 2.0 to
  4.9 s. Design 6.12's "trailing NaN interpolated" was wrong: it is forward-filled.

## Amendments from wave 2 (2026-09-02)

Same rule as above: each line is a change forced by running the system, with the task
that measured it in parentheses.

- 6.12 (W2-T7): the request clock's `last_verification_exit` (0/1/None) is replaced by
  two columns that carry the same three states with no None: `verification_seen` (0/1: a
  verification_run precedes this row) and `last_verification_failed` (0/1: the most
  recent one failed; 0 when none has run, which verification_seen disambiguates). Both
  rest on the capabilities `commands` and `tool_calls`, as the old column did, and both
  are all None when that capability is unavailable, as every column is.

  What forced it. Measured on a copy of this build's own store, rebuilt at main eb3304f:
  nine captured sessions have more than the 52 model requests W2-T5 measured as the
  shortest backtestable request clock at H = 1 (175, 168, 158, 120, 111, 86, 83, 80 and
  65 requests). Every one of them failed readiness for every target with `windows 0 of
  20` and the drop reason `missing_context_value(last_verification_exit)` on every
  planned origin: 143 of 143 for the 175-request capture. The None cells sat in rows
  0..k-1 of that column, before the capture's first verification run, with k = 106, 64,
  76, 50, 111, 30, 17, 45 and 63; context windows start at row 0 (no changepoints, fewer
  than 512 rows), so every window read row 0 and policy exclude dropped all of them. The
  column report of the 175-request capture shows 0 nulls in each of the other nine
  columns. After the change all nine retain every planned origin and all nine are ready
  for output_tokens at H = 1. W1-T5's report and the wave 1 amendment above both saw the
  refusal and called it structural; neither changed the encoding, and the encoding was
  the defect.

  The reasoning, which is the rule this adds. "No verification has run yet" is a state
  the capture OBSERVED: it saw every command and none of them was a verification. A None
  there writes a known fact as an unknown, and the exclude policy, which is right to
  refuse unknowns, then refuses the fact along with them. So: a state the capture
  observed is a number, and None is reserved for what no surface delivered. `_exit`'s
  previous-answer rule survives as `_failed`, one layer down: a verification_run whose
  success nobody stated leaves the last stated result standing, does not flip
  last_verification_failed and does not make it None, and verification_seen still turns 1
  because a run happened.

  What it does not do. It changes no target's variation. On the 175-request Claude
  capture, fresh_input_tokens and tool_calls_since_prev have scaled MAD 0 (tau 2 by the
  q80 rule: eight of ten requests carry at most 2 fresh input tokens, which is Claude
  Code's cache pattern) and output_tokens has scaled MAD 409.2. Readiness check 7 fails
  on the first two and passes on the third, and the registry is unchanged.

  Consequences that are not bugs. The column schema is part of `series_id`, and
  `REDUCER_VERSION` hashes series.py, so every capture's series id and reducer version
  change. Snapshots stored before this change are not rebuilt by anything and `series
  list` shows both generations. On a replayed fixture the `refuse` policy no longer fires
  on Claude S1, which now has no gap in any observed column; the gap the tests exercise
  it on is the Codex S1 replay's `request_duration_ms`, which is observed (it rides
  request_usage, which Codex reports) and empty on all seven rows.

- 6.9 and 6.5 (W2-T6): the launcher runs the reducers over its own capture at capture
  end, after capture_ended is flushed and inside the fail-open guard, so `telltale
  show` on a fresh capture prints numbers rather than a summary of nulls; the three
  reading commands refuse, naming `telltale rebuild <id>`, when a capture has no
  activities. `rebuild` deletes the capture's diagnostics of kind conflict before the
  reducers run (32 stale rows on cap_01M1GPSMW1ZRXVADWZPF0KZ9H3 became 0 across two
  rebuilds); this also deletes the importer's collision rows and the receiver's
  rebinding rows, which is the carried defect the wave 2 gate names.
- 6.8 (W2-T6): a repo.snapshot whose head is the commit and whose diff is empty is a
  tree match, `tree_match_during` inside the capture window and `tree_match_after` at
  the capture-end snapshot or later; two more snapshot triggers, a Bash result whose
  normalized command commits and the 2.1.258 stream message `vcs_state_changed`.
  Temporal proximity alone is still never a rung.
- 6.11 (W2-T4): the spec 13.7 vector is one table, `VECTOR` in cohorts.py, of six
  families over 22 evidence metrics; a cohort is the captures sharing provider,
  runtime major version (from the session's own runtime string, since the
  fingerprint's runtime_version is None for a scripted agent), model and content
  level, imported captures excluded unless asked; percentiles are mid-rank over the
  non-null values, need n >= 10 both for the cohort and per metric, are built through
  Evidence.comparative and are never stored, because a percentile is a statement
  about a cohort on a date.
- 6.13 (orchestrator, 2026-09-02): the CLI is three files, cli.py, cli_forecast.py
  (series and forecast) and cli_common.py (the store, the capture lookup, the refusal
  exit code); the T201 exemption covers `src/telltale/cli*.py`.
- 6.4 (W2-T8): a token beginning with `-` survives normalization only when it is
  FLAG-SHAPED, `^--?[A-Za-z0-9][A-Za-z0-9_.:+-]{0,31}$` after the `=value` split; every
  other dash-leading token becomes `_`. And the secret scrub runs on the normal form,
  inside `commands.normalize`, before the 200-character bound, which is the first time
  it has ever seen a command. Both change stored strings, so `NORMALIZATION_VERSION` is
  `cmdnorm-v3` and the fallback `cmdnorm-v3-fallback`.

  What forced it. Design 6.4 said "every token starting with `-` with any `=value`
  stripped", which is a rule about a token's FIRST CHARACTER and not about its shape. A
  shell token is whatever the quoting says it is, so `echo "--- sk-ant not X ---"` is
  ONE token, it begins with a dash, and the whole quoted string was stored. Measured on
  the owner's store on 2026-09-02 after the backfill import (3152 imported captures, 37
  launcher captures, 1,038,932 observations): a byte scan for the E01 and E02
  credential probes found ten observation rows, every one of them a `payload.command`,
  across claude.stream.assistant, claude.hook.PreToolUse, claude.hook.PostToolUse,
  claude.otel.tool_decision and claude.transcript.assistant, in launcher captures and
  imported ones alike, and every one inside such a token. Paths, bare words and
  `NAME=value` all behaved.

  The second defect, in the same rows: the private key header survived although
  sanitize.py has carried `_KEY_HEADER` since W0-T2. The scrub never ran. The COMMAND
  branch of `sanitize._clean_str` returned the normalized string above the branch that
  scrubs, so a normal form was the one kept string design 6.4's patterns had never
  seen. (Had it run at the old position it would still have missed four of the ten
  rows, whose stored form ends `KEY---`: the 200-character bound had cut the trailing
  dashes off, which is why the scrub now runs before the bound and not after it.) The
  scrub is load-bearing on its own, and not only as a second line: `relativize` returns
  a path INSIDE the repository unchanged, so `git clone https://x-access-token:ghp_...@
  github.com/o/r` and `cat config/sk-ant-....env` reached the disk verbatim; both are
  in the privacy suite and both fail with the scrub removed.

  What it does not do. The rule is a shape, so a short quoted argument that happens to
  be flag-shaped survives: `git commit -m "-TELLTALEFAKE"` stores `-TELLTALEFAKE`,
  because nothing in a normal form distinguishes it from `-m`. That residual is
  asserted in tests/integration/test_privacy.py rather than left implicit, and the
  scrub is what stands behind it. A bare `-` and a bare `--` also become `_`, which is
  a loss and not a leak: measured over the 88,754 stored rows that carry a
  normalization_version, 21,477 change and NONE of them changes what `classify()`
  returns, and over the committed fixtures the change is three golden timeline lines
  (`echo ---` twice and `git diff --` once).

- 6.5 (W2-T8): `resanitize [capture]` is the ONE UPDATE of `observations` in the
  codebase, kept in store.py by the resanitize-is-the-only-update pygrep hook exactly
  as the INSERTs are. It rewrites every stored command field whose
  `normalization_version` is neither the current one nor the current fallback, through
  the current token rules applied to the STORED normal form, then scrubs; it writes
  `payload` (the field and the version) and appends `resanitize:cmdnorm-v3` to
  `redaction.redacted` only where a token actually changed, one transaction per
  capture, one diagnostics row of kind `dropped` per capture reading `resanitize
  cmdnorm-vN to cmdnorm-v3: K field(s) rewritten`; then it rebuilds the capture, for
  the reason `purge` rebuilds. Observation ids never change and no other column moves.

  Why purge was the wrong remedy. `purge <capture>` is the only deletion path and it
  deletes a whole capture. Three of the ten captures holding a probe are this build's
  own launcher captures, hundreds of model requests each, and the leak is one token in
  one field of each; and the shape is in any command that quoted an argument beginning
  with a dash, so the next import would write it again. A stricter sanitizer applied to
  an already-sanitized string can only REMOVE information, which is what makes an
  in-place rewrite safe to sanction: every branch of `commands.renormalize` either
  keeps a token or replaces it with `_`.

  What resanitize does not touch. The raw command, which no longer exists anywhere: it
  re-normalizes the stored string, whose tokens are whitespace-separated, whose paths
  are already relativized and whose `_` are already placeholders. It does not re-apply
  the bare-token budget, because that budget was spent over the ORIGINAL tokenization
  and a multi-word token that survived as one "flag" arrives here as several. It writes
  nothing for a row it does not change, so a second run rewrites 0.

  The residual, stated rather than hidden. A rewritten v1 row is labelled `cmdnorm-v3`
  and re-running the token rules over a v1 string cannot restore what v1 never recorded
  (v1 stored an environment assignment as `NAME`, v2 as `NAME=`). That is why the row
  also carries `resanitize:cmdnorm-v3` in `redaction.redacted` and the capture carries
  a diagnostics row naming the version it came from: the label says which rules the
  string satisfies, the marker says it was rewritten into them rather than produced by
  them. Measured on a copy of the owner's store: `strings | grep -c
  "TELLTALEFAKE\|sk-ant-api03-"` 16 before, 0 after, 69 s for 21,725 fields across 1154
  captures, second run 0. Six of those sixteen were `activities.fields.command_norm`,
  which is what the rebuild is for.

## Amendments from wave 3 (2026-09-02)

Same rule as above: each line is a change forced by running the system, with the task
that measured it in parentheses.

- 6.12 (W3-T2): the forecasting lab's module layout is `forecast/{__init__, baselines,
  backtest, readiness, timesfm, placebo, decide, ablate, candidate}.py`. backtest.py was
  at 727 lines against the 800-line ratchet, so the placebo, the decision rule, the
  ablation and the candidate protocol are four new modules and backtest.py gained only
  what all four need: `ordering`, `placebo_seed`, a named `covariates` override, and ONE
  `prepare` hook that rewrites a Window between planning it and forecasting it. The hook
  is what makes a placebo the same run rather than a second implementation of one, and
  the window RECORD is built from the true rows before it fires, so origin, actual and
  y_{o-1} stay true in every placebo run. `backtest.run` takes no store and never did;
  `persist` does.
- 6.12 (W3-T2): the constants the design states in prose but forecast/__init__.py did not
  carry are now there and are printed by every decision: placebo block `B = max(2, H)`,
  `R = 5` seeds, the `B = 1` control, `c_min 16` on the attempt and change clocks, the
  demotion resolution 0.25, and the ablation's 15-variate cap (which is NOT the model's
  32-variate cap in forecast/timesfm.py). `TARGETS` gains the three change-clock ablation
  targets and the three post-merge candidate targets, all at c_min 16, the candidate ones
  at H = 1 only. `backtest.registered` now also refuses a target whose registry clock is
  not the series' clock, which is what keeps a change-clock target off a request series.
- 6.12 (W3-T2): W_MP is defined against the MEDIAN over the R seeds of the twin's MAE at
  the same origin. Design 6.12 says "its placebo twin" and a window has R of them; the
  share over all (window, seed) pairs is computed too and printed beside it.
- 6.12 (W3-T2): the decision rule refuses rather than falling through when no placebo was
  run. Design 6.12's "conditional prediction: otherwise" would otherwise hand that label
  to a run with no control at all, so `decide` returns "not assessable: placebo not run,
  label withheld" and `forecast backtest` prints that line under every table.
- 6.12 (W3-T2): ADR-014's word refusal is a function in forecast/__init__.py that every
  renderer in the package calls on its finished string, and every caller renders BEFORE
  it stores. It matches whole words with inflections (`caused`, `impacts`, `impacting`),
  so `because` is not a hit and the mandatory candidate sentence passes. Two strings in
  this task's own code had to be rewritten to satisfy it, which is the check working.
- 6.12 (W3-T2, measured): design 6.12's own suggestion for a "conditional prediction"
  fixture does not clear delta. A forecaster whose answer is a function of the MULTISET of
  its context (the full-context mean or median) beats the window-8 baselines on i.i.d.
  data by too little to pass `E_M <= (1 - delta) E_B`: measured over 13 distribution
  families (normal, uniform, exponential, lognormal at four widths, gamma at three shapes,
  Pareto at three tails), 8 seeds each, 400 rows, the best ratio to the best baseline was
  0.932 and never below 0.9, and W_MB never reached 0.60 (best 0.585). The reason is
  structural: on i.i.d. data every baseline is estimating the same constant, and going
  from 8 samples to 400 is worth about 6 percent of MAE. The contract test therefore uses
  a noisy straight line, where a line fitted to the SORTED context is still a multiset
  function (so E_P is E_M to the last bit) and beats the baselines by 36 percent.
- 6.12 (W3-T2): a placebo needs a series with recency, and the change-clock fixture had to
  be given some. The first version of `synthetic_series.write_change` drew every row
  independently; persistence then scored 0.7045 in true order and was worse in only 7 of
  10 placebo runs, so the run was invalid by design 6.12's own validity rule and no label
  could be written. The size of a change now walks, and persistence goes 0.4773 -> 1.11 to
  1.84 across all ten runs.
- 6.12 (W3-T2): neither the ablation nor the candidate protocol can be exercised end to
  end without a covariate-reading forecaster, and the four baselines and the stub are not
  ones. Both runners therefore read their OWN numbers rather than a declared capability:
  when A, B and C score identically, or when the conditioned and unconditioned runs do,
  the report carries a warning saying the verdict is a property of the forecasters rather
  than of the columns. Measured on the 60-row synthetic change series: A, B and C all
  score 1.0682 and the paired candidate difference is 0.0.
- 6.12 (W3-T2): the ablation cuts its three variants down to the origins all three
  retained and rescores on that set, rather than refusing when they differ. Design 6.12
  says "identical origins and actuals across variants" and does not say how to get them; a
  covariate with a hole drops windows in one variant and not another, and the number
  dropped for alignment is reported per variant.
- 6.13 (W3-T2): `telltale forecast placebo` and `telltale forecast ablate` exist.
  `forecast placebo` reuses a stored true-order run when one matches on every field that
  could move a number (series, target, variant, horizon, c_min, stride and the forecaster
  names), which is what lets a placebo be paired with a run E07 already stored. The
  decision is written to the `decision` column of the true-order row and of no other: a
  placebo row carries no decision, because the label is about the pair.

- 6.5 (W3-T0): a read may name the row types it wants.
  `Reads.observations(capture_id, types=...)` and `Reads.activities(capture_id,
  types=...)` return only the named types, and `Reads.observations_of_type(type)`
  answers the same question of the whole store. One new index,
  `obs_by_type_capture` on `(observation_type, capture_id)`, and it arrives on an
  existing store the way every index here does, through `CREATE INDEX IF NOT EXISTS`
  in the DDL that `Store.open` runs.

  What forced it. `cohorts._members` compares four keys per capture and read every
  capture's whole observation list and whole activity list to find them. Measured on a
  copy of the owner's store on 2026-09-02, 3200 captures and 1044858 observations:
  `telltale vector` took 11.13 s, of which 6.30 s was reading observations to look at
  3247 of them and 2.30 s reading activities to look at 9684. Afterwards, 0.85 s, with
  byte-identical output. At the 36-capture, 30708-observation shape W2-T4 measured, on
  the same store both ways: 0.20 s before, 0.030 s after.

  The one thing to know if you write such a read. `ORDER BY observation_id` is answered
  for free by `obs_by_capture` and SQLite will keep choosing that index for the sort
  even when a type filter makes another one far better. `ORDER BY +observation_id`
  takes the column out of the ORDER BY's index candidates and costs a temp b-tree over
  the handful of rows the filter left: measured, 0.266 s against 0.026 s for 3200
  two-type reads.

  Scaling, stated honestly. The cohort scan is still linear in the NUMBER of captures,
  and design 6.11 makes it so: a cohort is a statement about every capture sharing the
  four keys. What it is no longer is linear in how BIG those captures are.

- 6.10 (W3-T0): a tool call the provider REFUSED is not a verification run. A tool_call
  whose tool_use id appears in a `permission_denied` observation, or in the session
  result's `permission_denials` list, carries `outcome: "refused"` and
  `executed: false` in its fields, sourced on the observation that said so, and takes
  the `command` or `tool_call` type rather than `verification_run`. Its command is
  still classified, because the agent did ask to run a test.

  What forced it. W2-E05's five pilot captures each report agent_test_runs 3 and
  failed_test_runs 3 and no test ever ran. A denial arrives as a tool_result with
  `is_error` true, which is one of the three routes `_tool_outcome` reads for success,
  so a refused call and a failing test had one spelling. Both denial shapes are read
  because neither is always present: four of the five carry three of each, and the
  fifth carries three `permission_denied` messages and no `claude.stream.result`
  observation at all.

- 6.11 (W3-T0): `refused_tool_calls` (unit `calls`) joins the summary's work block. It
  is NOT in the spec 13.7 vector, which stays at 22 entries. Its coverage is `observed`
  only where a surface that STATES a refusal delivered, and `unavailable` otherwise, so
  the count is null rather than 0 for a provider whose refusals nothing has measured.
  A Codex capture delivers a surface also called `stream` and no Codex denial has ever
  been measured on it, which is why the rule is keyed on the provider and not on the
  surface name.

- 6.12 "Attempt clock" (W3-T1): an attempt is a capture that NAMES which attempt it is,
  and two surfaces say so. The design named only `external.correlation`; the launcher
  does not write one. Measured on the owner's store on 2026-09-02: of 37 captures
  carrying this repository's repo_id, 22 carry `task_id` and `attempt` in the
  `telltale.capture_started` PAYLOAD (`--task-id X --attempt N --experiment E`, design
  6.9) and NONE carries an `external.correlation`; the five captures that do carry one
  are the E05 pilot's, written by `experiments._correlate` through `/v1/correlations`,
  and they belong to a different repo_id (the disposable worktree the pilot made). A
  compiler reading correlations alone would report an empty lineage for the build that
  produced it. So `series_lineage._identity` reads both, through the
  `primary_observation` the capture_start lifecycle activity already carries, in one
  `observations_by_id` call per lineage. Both have to agree: a capture carrying two
  different identities is dropped by name rather than resolved.

- 6.12 (W3-T1): `build(store, clock, key, missingness_policy)`. `key` is a capture id
  on the request clock and a repo_id on the attempt and change clocks, which is what
  "one frame per history" means. The CLI spells it `--capture` or `--repo`, one
  required, mutually exclusive.

- 6.12 (W3-T1): on the attempt and change clocks `row_end_ts` is a RUNNING MAXIMUM of
  each row's own last observation, not the row's own last observation. Two measured
  facts force it, and design 6.12 requires row_end_ts to be non-decreasing. Attempts of
  one repository OVERLAP: W1-T4 attempt 2 spans 01:20:41 to 01:48:46 and W1-T5 attempt
  1, which starts later, ends at 01:46:15, so start order and end order already differ
  before any outcome exists. And an outcome about an attempt is observed after that
  attempt closed and often after a later attempt has closed: W2-T8 attempt 1 ended at
  18:24:28 and W2-E05 attempt 2, which started later, ended at 18:11:22. The field
  therefore states "everything in rows 0..i had been observed by here", which is the
  claim `check` tests (`max(position of provenance[i]) <= row_end_ts[i]`, and
  non-decreasing). The residual, stated rather than hidden: on these two clocks an
  outcome cell of row i may have become known after row i+1 began, and `row_end_ts` is
  where a reader sees it. Rows stay in capture-start order, as design 6.12 says.

- 6.12 (W3-T1): a row reads its own capture and nothing else. Because attempts overlap,
  any rule that partitioned the lineage by wall-clock time would put one session's
  requests in another session's row. An outcome is placed on the attempt it NAMES
  (`telltale outcome --task-id X --attempt N` writes into that attempt's capture), not
  on whichever row was open when it arrived; design 6.12's "recorded at the row where
  they were observed, never back-filled" is about an outcome concerning a DIFFERENT
  row, and that case is what the running maximum above records honestly.

- 6.12 (W3-T1): a column with no capability behind it takes its coverage from its own
  cells, which is the rule the request clock already used for `env_changed`, now
  written once and applied to eight columns: all None is `unavailable`, no None is
  `observed`, anything between is `partial`. On the build's own store this makes
  `verification_passed`, `review_fail_count` and `accepted` unavailable and all None
  over 21 attempt rows, because no outcome had been posted; after one `telltale outcome
  --kind merge_decision --status merged --task-id W2-T8 --attempt 1` the `accepted`
  column reads `partial` with 20 nulls and a 1 on that row. That is the difference
  between "this attempt was not accepted" and "nobody has said".

- 6.12 "Change clock" (W3-T1): THREE COLUMNS CANNOT BE BUILT and are None with coverage
  unavailable. `subsystems_touched`, `test_files_changed` and `dependency_delta` all
  need per-file PATHS. `telltale.repo.commit` carries `sha`, `parents`, `tree`,
  `committed_ts`, `files_changed`, `additions`, `deletions` and `link_confidence`, and
  those eight keys and no others on all four commits stored on 2026-09-02. Design 6.12
  forbids reading git at series-build time (a series is rebuildable from the store
  alone), so this is a missing FIELD and not a missing call: the fix is a path list on
  the repo.commit payload, and until then `series_lineage.MISSING_FIELD` names the
  field each column wants.

- 6.12 (W3-T1): one change row per SHA, not per repo_commit activity, because two
  captures may each record one commit; the row takes the BEST rung any of them reached.
  `tree_match_after` and `heuristic` set `low_confidence` in `RowMeta.flags`, which
  W3-T2's backtester excludes by default. `RowMeta` gains `flags: list[str]`, defaulted
  and additive; it is words about the row, never a number a forecaster may read.
  `attempts_to_land` is the highest attempt ordinal among the attempts whose captures
  recorded the commit, and the other per-attempt aggregates are summed over the same
  set, as design 6.12 says for `unique_files_read`.

- 6.12 (W3-T1): `edit_turnover_ratio` is named by design 6.12 and defined by no
  document. W3-T1 chose file_edit activities over distinct edited paths (1.0 when every
  file was written once, growing as a file is rewritten), None when no edit named a
  path and None when there was no edit. `stable_state_intervals` is
  `measures_intervals.intervals` counted, which is spec 13.5's one definition, called
  rather than copied, and None without a repo_snapshot.

- 6.13 (W3-T1): `telltale outcome --kind mechanical_verification|adversarial_review|
  merge_decision|revert_or_repair|runtime_signal --status S --task-id X --attempt N
  [--categories A,B] [--external-run-id ID] [--repo REPO_ID] [--receiver URL]`. It is
  the only CLI write path besides `run` and `import`. The repository is the one the
  command is run in, and the observation goes to the capture of that lineage carrying
  the matching (task_id, attempt): zero captures and two captures are both refusals
  naming what was found. `external.outcome` has no task_id field in the allowlist, so
  the task travels in `component_id`, which is the substitution `experiments._outcome`
  already makes. With `--receiver` it POSTs to a receiver somebody else is running and
  prints the `telltale rebuild` the operator then needs, because this process may not
  open a second writer on one SQLite file; without it, it appends through the store of
  `$TELLTALE_HOME` and rebuilds the capture itself. `--status` is free text because an
  orchestrator's vocabulary is its own, and `series_lineage._PASSED` / `_FAILED` are the
  one place a word becomes a number: a word neither carries leaves the cell None.

- 6.13 (W3-T1): the CLI is five files. `cli_import.py` takes the import group (cli.py
  was at 764 lines against the 800-line ratchet; the moved commands print byte-identical
  output on the fixture import, measured both for `--dry-run` and for the real import)
  and `cli_outcome.py` takes `outcome`, which had taken cli.py to 825. Both register
  their own subcommand through `add_commands`, as cli_forecast.py does.

- 6.12 (W3-T1): `REDUCER_VERSION` hashes series.py AND series_lineage.py, because the
  fold is both files. `series.check` reads the capture ids off the cohort
  (`cohort["captures"]` on a lineage frame, `cohort["capture_id"]` on a request frame),
  since a Series carries activity ids and an id has no position until the capture that
  owns it is read. A lineage cohort also carries `dropped`: every capture or commit the
  frame refused and why, so a frame that skipped something can be audited, and so that
  a drop which later resolves gives a different series id.

- 6.12 (W3-E08b, pre-registered before the re-labelling, owner-approved 2026-09-02):

  > The placebo validity check (persistence must be worse under every placebo run)
  > gates the two labels that read the placebo, temporal evolution and conditional
  > prediction. It does not gate "baseline sufficient", which is a paired comparison of
  > the model against the best baseline on the same true-order windows and reads no
  > placebo. An invalid placebo therefore yields: "baseline sufficient" when E_M >
  > (1 - delta) E_B or W_MB < w, with the warning "placebo invalid: the positive labels
  > were not assessable"; otherwise "not assessable: placebo invalid" with the
  > inequalities shown. "Not assessable" for n_windows < k_min is unchanged and comes
  > first. E08's 38 rows are re-labelled under this rule as E08b; E08's labels stand as
  > the record of the rule before the amendment.

<!-- folded from docs/design/amendments/W3-T3.md by the orchestrator at the wave 3 gate -->
# W3-T3 amendments

Lines for the wave 3 amendment block of `docs/design/01-design.md`. Same rule as the
blocks already there: each one is a change forced by running the system, with the task
that measured it in parentheses.

- 6.10 (W3-T3): the exit status of a chain belongs to the classified segment only when
  that segment is LAST, or when everything after it is joined by `&&`. A non-zero there
  ends the chain with its own code, so a failure cannot hide; after a `|`, a `;`, a `||`
  or a `&` the status is another program's. `commands.exit_masked(command_norm)` decides
  it from the normal form alone, which keeps the operators (`_STRUCTURAL` is `<>&|;()`
  and `_SEPARATORS` is the four the segments are cut on). A redirection is not a
  separator: `pytest 2>& _` is still pytest's status.

  A verification_run whose chain is masked carries `exit_masked: true` and states NO
  outcome: no `success`, and no `exit_code` read onto the row. The observation still
  holds whatever the surfaces said and `telltale explain` reaches it; the row may not
  answer "did the check pass". The rule applies on both providers, because a Codex tool
  call is a shell command too.

  What forced it. Every W2-E05 re-run session ran `uv run pytest 2>&1 | tail -50` first,
  the tests failed, and all five captures reported `fail_to_pass_cycles` 0 at coverage
  `derived`. The tool's exit status is the pipeline's last command's, and
  providers/claude.py derives an exit code only from the "Exit code N" text of an
  `is_error` result (`_EXIT_CODE`), so a masked failure looked like success on every
  surface. Measured on a `.backup` copy of the owner's store: after a rebuild all five
  report 0 at coverage `partial` with the warning "2 of 2 verification runs have a
  masked exit status", and the timeline prints `-` for both runs instead of `ok`. Both
  runs of each capture are piped, which is why the count is 2 of 2. Codex S6's
  `uv run pytest && git diff --check && git diff _ pkg/calc.py` is NOT masked and did
  not move, which is the `&&` half of the rule measured on a real capture.

  Scope, stated rather than left implicit. Only a verification_run is affected. A
  `command` row's `success` is a fact about the tool call and stays: Claude S1's
  `rm -rf _ && uv sync _ >& _ | tail -30` still reads `ok` in the timeline. It is the
  verification_run that reinterprets `success` as "the check passed", and that is the
  reinterpretation the rule refuses.

- 6.11 (W3-T3): `failed_test_runs` and `fail_to_pass_cycles` count only the runs whose
  outcome a surface stated. Their coverage is `partial` whenever some run in scope
  stated none, which `measures_spec13._stated` already produced, and their Evidence
  carries a warning naming the masked count over the runs THAT metric is taken over.
  `agent_test_runs` still counts a masked run: the agent did run the tests. The 22
  metrics of spec 13.7 stay 22 and no metric is added.

- 6.10 (W3-T3): a tool call the provider REFUSED is a `tool_call` and nothing narrower,
  whatever tool it named. W3-T0 made that true for a refused test command and left the
  four other types alone because it had no capture where it mattered: all three of
  W2-E05's refusals were Bash calls. The argument is the same one type over. A refused
  Read read no file, so `unique_files_read`, `directories_traversed`, the two
  exploration ratios and the edit family must not count it; a refused Edit edited none.
  The row keeps its `tool_name`, its `file_path`, its `category` and `outcome:
  refused`, so what the agent ASKED for is still recorded and `refused_tool_calls`
  still counts it. Measured: the five W2-E05 pilot captures still report
  `refused_tool_calls` 3 after a rebuild, with their three refused rows now `tool_call`
  where they were `command`.

- 6.12 (W3-T3): a series column with no value in any row is never `observed`. Its
  coverage is the worse of two things, the capability's word and whether any value
  arrived, and `series.column_report` names which rule fixed the word (`no value in any
  row`, or `measured for this column`). Both directions of the reconciliation live in
  `blank_unobservable`, which already rewrote the rows in place and now rewrites the
  ColumnSpecs beside them, so all three clocks get the rule from one function. A series
  with no rows is left alone: nothing follows about coverage from an empty table.

  The residual, stated rather than hidden: once the emptiness rule fires, the capability
  word it replaced is not preserved, so `column_report` cannot say whether the provider
  is unable to report the number or whether this capture happened not to carry it. The
  coverage block `telltale show` prints answers that, per capability, per capture.

- 6.12 "Request clock" (W3-T3): design 6.12's "(Codex: derived from turn timestamps,
  partial)" for `request_duration_ms` is withdrawn. The cells stay None and the column
  is `unavailable`. Measured on the replayed E02 fixtures: the exec stream's
  `turn.started` and `turn.completed` carry no clock at all (`provider_ts` null on S1,
  S3, S6 and S7), and the rollout's `task_started` and `task_complete` do carry one but
  bracket a TURN. Model responses per turn are 7 on S1 (one turn, 31584 ms), 3 on S3
  (10200 ms), 11 on S6 (70250 ms), and S7 is `--ephemeral`, writes no rollout at all,
  and its two turn rows fall back to the arrival clock with no end and no duration. No
  capture in E02's cohort has one request in one turn, so a turn's span is not a
  request's; copying it onto each request or dividing it by the count are both numbers
  nobody measured.

  Two consequences. Forecast readiness on the three Codex goldens goes from "coverage:
  measured 9, needed 11" to 8, which is the honest count of usable columns. And the
  `refuse` policy needed a new subject, because the hole it used to fire on was never a
  hole: it is now Codex S6, whose SECOND model response carries none of the six token
  counters while the other ten carry all of them. One row of three observed columns,
  found in the recorded bytes rather than punched into them.

- 6.5 (W3-T3): `DROP INDEX IF EXISTS obs_by_type` runs at every `Store.open`, beside the
  CREATEs, in schema.py. It is a strict prefix of `obs_by_type_capture` (W3-T0) and the
  only read in src/ that filters on `observation_type` alone is
  `Reads.observations_of_type`, which orders by `(capture_id, observation_id)` and is
  served better by the composite. Measured on 2026-09-02, eight interleaved pairs in one
  process, 50000 observations in 500-row batches, first append to `close()` returning:
  median 0.602 s with the index and 0.568 s without, 6.1 per cent of the write path,
  0.68 microseconds per observation. The read is unchanged: on a `.backup` copy of the
  owner's store, 1066523 observations, 69 rows in 0.8 ms either way, and the query plan
  names `obs_by_type_capture` both times because SQLite already preferred it. The
  migration itself is 0.017 s for the open that drops it and 0.001 s afterwards.

- 6.14 (W3-T3): `src/telltale/activities_tools.py` holds the tool-call reducers
  (`tool_calls`, the denial lookup, `_tool_call`, `_tool_outcome`, `_tool_type` and
  `VERIFICATION`). activities.py was at 798 lines against the 800-line ratchet. The tool
  block moved and the request block did not, because the tool block is the one with a
  thin interface: `tool_calls(capture_id, observed)` is the whole of it and
  `activities._subagents` reads the activities it returns rather than any function in
  it, while `_requests` and `_attributed` share `_agent_of` and `_requests` takes the
  subagent activities as an argument. `correlate._RULE_MODULES` names the new file, so
  an edit to a moved rule still moves `REDUCER_VERSION`.

<!-- folded from docs/design/amendments/W3-T4.md by the orchestrator at the wave 3 gate -->
# W3-T4 design amendments

Lines for 01-design.md, to be folded in at the wave 3 gate. Nothing here edits
01-design.md.

- 6.3 (W3-T4): `telltale.repo.commit` gains `per_file: [{path, additions, deletions}]`
  and `per_file_truncated`. THREE keys, not the four `telltale.repo.snapshot` carries:
  measured over this repository's own 65 commits, a `patch_hash` per entry takes the
  largest (65 files, 04c00142) from 5820 bytes to 11020, past the 8 KB bound of design
  6.4, so the list would be cut to 48 entries and the change clock's three path columns
  would go unknown on exactly the biggest changes. Without it every one of the 65 fits,
  the largest payload measuring 5829 bytes and none over the bound. A commit's patch is
  also not in hand where the list is built: hashing it per file means reading the patch,
  which is a second git call over every commit of a capture, and design 6.3 lists diff
  text among the things never persisted. The list comes from the numstat
  `repo_link._commit_stats` already reads (`git diff-tree --numstat -r -M --root`), so
  it costs no extra call. A MERGE reports `per_file` None like its other numbers: a path
  list picked from one parent's diff is the same invented answer as a line count picked
  from it. Paths are `Kind.PATH` and go through the sanitizer like the snapshot's.

- 6.4 (W3-T4): the per_file cap of design 6.4's amendment for W0-T2 applies to every
  payload the launcher emits, not to the snapshot call site. `_capped` moved into
  `_Capture.emit`: cut to PER_FILE_MAX, then to what fits the 8 KB bound, with
  `per_file_truncated` marking a prefix and `files_changed` keeping the true count. A
  payload with no per_file passes through unchanged, so the rule is stated once and a
  third payload that grows a path list cannot miss it.

- 6.12 "Change clock" (W3-T4): `subsystems_touched`, `test_files_changed` and
  `dependency_delta` ARE BUILT, from the commit's own `per_file` list, in
  `series_paths.py`. This supersedes the W3-T1 amendment that declared all three
  unbuildable with coverage unavailable, and `series_lineage.MISSING_FIELD` is gone.
  - `subsystems_touched`: the count of distinct FIRST path components, with `.` naming
    the repository root. A file at the root is in no directory, and counting 0 there
    would say a commit that edits pyproject.toml alone touched nothing.
  - `test_files_changed`: the count of paths with a `tests` or `test` path component, or
    a basename matching `test_*.py`, `*_test.py`, `*.test.*` or `*.spec.*`. Two
    directory words because `tests/` is the Python convention and `test/` the Go and
    Java one, and a change clock is not per-language. `fnmatchcase`, never `fnmatch`,
    which folds case on a case-insensitive filesystem and would make `Test_x.py` a test
    file on macOS and not on Linux.
  - `dependency_delta`: 1 when any changed BASENAME is one of `uv.lock`,
    `pyproject.toml`, `poetry.lock`, `package.json`, `package-lock.json`, `yarn.lock`,
    `pnpm-lock.yaml`, `Cargo.toml`, `Cargo.lock`, `go.mod`, `go.sum`, `Gemfile`,
    `Gemfile.lock` or matches `requirements*.txt`, and 0 when none is. Matched on the
    basename, so a manifest inside a subdirectory of a monorepo counts. It is a flag and
    not a count of anything: a path list says a dependency statement changed and cannot
    say which way a dependency moved.
  All three are None TOGETHER, never 0, whenever the store does not hold the commit's
  paths: a commit recorded before W3-T4, a merge, a `per_file_truncated` prefix, or an
  entry carrying no path. One answer between them, because the question is whether the
  paths are there. The coverage word is measured from the cells like every other column
  with no capability behind it, so a frame of commits recorded before this change reads
  `unavailable` and a mixed frame reads `partial`.

- 6.12 (W3-T4): a change-clock cohort carries `unknown_columns: {column: reason}`, the
  columns of `series_paths.PATH_COLUMNS` that hold an unknown cell and the one sentence
  saying why. On the cohort because `ColumnSpec` carries a coverage WORD and has no room
  for a sentence, and because `series build` prints the cohort beside the column table:
  a reader who sees `partial` there sees here what the partial is made of. An empty map
  is the frame saying every linked commit carried its paths. It is part of the cohort
  and therefore of the series id, which is right: a frame whose unknowns later resolve
  should not keep the same id.

- 6.12 (W3-T4): `series_paths.py` is a separate module from `series_lineage.py`, which
  was at 769 lines against the 800-line ratchet. Nothing in it reads the store and
  nothing in it runs git, which is what design 6.12's "rebuildable from the store" needs:
  a rule that stat()ed a file would make a series depend on the checkout the build
  happened to run in.

- 6.3 (W3-T4): `claude.otel.metric` allowlists `service_version` and `terminal_type`,
  and NOT the rest of `_OTEL_COMMON`. A metric point carries neither `event_name`,
  `event_timestamp`, `event_sequence` nor `app_version`, so the log events' field set
  would allowlist four fields no metric has ever carried. Measured by replaying the eight
  E01 scenarios: 171 of 171 metric observations dropped each of the two as an unknown
  field before, 0 of 171 after; they named 30 of the 127 unknown_field diagnostics rows.

- 6.4 and 6.5 (W3-T4), file splits forced by the 800-line ratchet, both
  no-behaviour-change:
  - `allowlist_telltale.py` holds the `telltale.*`, `external.*` and `policy.*` entries,
    merged into the one ALLOWLIST at import through the deferred-import shape
    `allowlist_codex.py` already uses. Verified by dumping ALLOWLIST as sorted JSON
    before and after: byte-identical, 30.6 KB. allowlist.py 798 lines to 696.
  - `launch_commits.py` holds `commits` (was `launch._commits`) and `link_commits`.
    repo_link.py answers whether a session made a commit and writes nothing; this module
    takes that answer and emits one observation per commit through the capture that owns
    it, which makes it the only place linkage touches the store. launch.py imports it at
    the top; launch_commits.py defers its import of launch.py into `link_commits`, the
    function that has to build a `_Capture`. launch.py 790 lines to 726.
- 6.12 (W3-VF, after the wave 3 verifier): `telltale forecast backtest` labels the row it
  stores through the one decision rule, handed no placebo. Under the W3-E08b amendment
  the baseline clause reads the true-order windows alone, so the label is "baseline
  sufficient" when either clause fires, with the note "placebo not run: the positive
  labels were not assessable", and "not assessable (placebo not run: label withheld
  ...)" otherwise; the two inequalities the rule evaluated are printed and stored either
  way. `Decision.placebo_valid` is None when no placebo ran: a control nobody ran has no
  verdict, and `placebo.n_runs` beside it says so. Placebo rows already stored for the
  pair are counted and the newest is named; they are never reused, because a placebo is
  a control for the run it was made for. `forecast backtest` takes `--model` on the same
  terms as `forecast placebo`.
- 6.12 and ADR-014 (W3-VF): the word refusal (cause, impact, would) guards the stored row
  as well as the rendered report. `backtest.persist` refuses a run whose warnings,
  assumptions or decision carry one, so a caller that skips the renderer cannot store
  what it may not print.
- 6.12 (W3-VF): a capture carrying two attempt identities (its capture_started payload
  and an external.correlation disagree) is dropped from the attempt clock by name with
  both identities listed, never picked from. The rule was W3-T1's; the test that holds
  it is new, after the verifier showed the suite green without it.

## Amendments from wave 4 (2026-09-02 to 2026-09-03)

Folded from docs/design/amendments/ at the wave 4 gate, in merge order. The gate report
is docs/gates/wave-4.md.

<!-- folded from docs/design/amendments/W4-T1.md by the orchestrator at the wave 4 gate -->
# W4-T1 design amendments

Wave 4. Each line is a change forced by running the system, with the task that measured
it in parentheses, in the form the orchestrator folds into `docs/design/01-design.md` at
the wave gate.

- 6.12, spec 14.3 (W4-T1): `telltale experiment probe <spec.json>` runs a suite of fixed
  read-only probes and scores each answer against a key written before the run. The spec
  is `{task_id, experiment, repo, base_sha, provider, level, command, probes,
  repetitions}` and a probe is `{probe_id, prompt, answer_key: {paths, symbols}}`. The
  command carries `{prompt}` at least once and every occurrence is replaced by the
  probe's prompt: one agent, one set of flags, one prompt per question. There is no
  `acceptance` key, because a probe is read-only and the answer key IS the acceptance
  criterion. The runner is `src/telltale/experiments_probe.py`; experiments.py is 637
  lines and the 800-line ratchet is a gate.

- 6.12, spec 14.3 (W4-T1): the two score definitions, both stated in every report's
  assumptions, because neither is the textbook one.
  `recall = matched_keys / len(answer_key)` over the paths and the symbols together;
  that one is textbook and computable, since the key is finite and known.
  `precision = matched_keys / (matched_keys + wrong_paths)`, where a wrong path is one
  the answer named, that exists at base_sha, and that the key does not carry. The
  textbook denominator is everything the answer asserted, and that is NOT computable
  from prose: there is no way to enumerate what an arbitrary sentence claimed. So the
  denominator is the answer-key vocabulary plus what the REPOSITORY can recognize. A
  wrongly named symbol is counted nowhere, because a repository enumerates its paths and
  not the symbols an answer could invent, which makes this number an UPPER BOUND on
  precision rather than an estimate of it. Both numbers are printed with n.

- 6.12, spec 14.3 (W4-T1): a path matches only on an exact repository-relative match. A
  token in the answer matches path P when it IS P or ends `/` plus P, so
  `/tmp/w/src/a.py` and `./src/a.py` both name `src/a.py` while `bsrc/a.py` and
  `src/a.pyx` do not. A match on the basename alone is reported separately under
  `basename_only` and scores nothing: an answer that says `a.py` has not located the file
  in a repository with three of them. A symbol matches as a whole word with identifier
  boundaries rather than `\b`, so `Store.open` matches that literal and `load` does not
  match `loader`.

- 6.12, spec 14.3 (W4-T1): a repetition whose stdout carried no result message, or an
  empty one, scores None for both numbers and never 0. Design invariant 5 at the place
  the probe runner could break it: a recall of 0 there would be a measurement of an agent
  that answered wrongly, and what happened is that nobody saw an answer. Precision is
  also None when the answer named nothing the key or the repository recognizes, because a
  ratio with an empty denominator is unknown; recall in that case is a measured 0.

- 6.3, 6.12 (W4-T1, measured): a probe score needs NO new observation type. It is posted
  as an `external.outcome` of kind `mechanical_verification` (the key is deterministic
  and the harness checks it after the agent exited), with the score in `categories`,
  which the allowlist carries as Kind.ENUM: a list of short symbolic strings, each
  scrubbed and bounded to `sanitize.MAX_ENUM` = 64 characters, at most
  `sanitize.MAX_ITEMS` = 256 of them. The three written are `probe:<probe_id>`,
  `precision:<0.000..1.000>` and `recall:<0.000..1.000>`, and an unknown score says
  `precision:unknown` rather than being left out, because a missing category and a
  category that means unknown are different facts and only one of them is a measurement.
  Measured on a real store: the three strings round-trip byte for byte. A probe_id long
  enough for `probe:<id>` to reach the 64-character bound is REFUSED at spec check, since
  a truncated probe_id is two probes' scores under one string.

- 6.5, 6.12 (W4-T1): the agent's answer TEXT is read by the runner and dropped.
  `experiments._launch` now returns the finished `subprocess.CompletedProcess` rather
  than its exit code, because the child's stdout is the agent's own bytes: `launch._tee`
  echoes every line unchanged before recording it. The probe runner parses the last
  `result` message out of those bytes, scores it in its own process, and stores the
  score. The text cannot reach the store and never could: `result`, `text` and `content`
  are in `sanitize.NEVER_PERSIST` and the `claude.stream.result` allowlist has no field
  for it. Verified on a real database:
  `strings telltale.db | grep -c "the probe answer names these files"` is 0 while
  `grep -c "src/alpha.py"` is 24, so the paths are stored (a Read tool_use carries
  file_path) and the sentence around them is not.

- 6.12 (W4-T1): a probe condition is NEVER resumed. `experiments.repeat` reads attempt k
  back out of the store when a capture already claims it, because everything its report
  needs is on the capture; a probe's score is not, by the line above. So a spec whose
  (task, attempt) pairs are already in the store is refused by name, and the refusal says
  to use a new experiment id, a new task_id, or `telltale purge`. There is no `from_store`
  and no `finish` for a probe suite, and there cannot be one that reports a score.

- 6.12 (W4-T1): an answer-key path that is not in the tree at base_sha is REFUSED before
  any token is spent, with the path named. It would score recall 0 in every repetition for
  a reason that has nothing to do with the agent. The consequence for an intervention is
  deliberate: a suite reusing one key across two commits requires the key to exist at
  BOTH, so a refactor that moved the answer forces the key to be updated rather than
  scoring the agent down for the move.

- 6.12, spec 14.3 (W4-T1): `experiments_env.FACTORS` gains `base_sha`, and
  `experiments_env.intervention` runs one probe suite at two commits.
  `telltale experiment intervention <spec.json>` is the command; an arm is
  `{name, base_sha, command, level?}` and there is no spec-level `base_sha`, because an
  intervention arm IS a commit. The pre-run assertion is INVERTED for this factor: the
  two arms' argv must be token-identical and their commits must differ, and a spec that
  moves both is refused with the differing tokens AND both commits named. That refusal is
  the only place the pair can be caught: base_sha is not in the argv and the fingerprint
  carries no commit, so the post-run assertion would report the flag and say nothing
  about the two repository versions the arms actually ran.

- 6.12, spec 14.3 (W4-T1): the post-run fingerprint assertion is inverted for base_sha
  too. For every other factor the arms must differ in exactly the declared field; for
  base_sha they must differ in NO field at all, which is spec 14.3's "the environment
  fingerprint is held constant" stated as a check. The fingerprint carries no commit and
  cannot be given one: a fingerprint that carried the commit would make every repository
  comparison a comparison of two environments, and every commit a changepoint. The
  assertion is NOT vacuous. `instruction_hashes` is a fingerprint field and env.py reads
  it out of the arm's worktree, so a refactor that also touched AGENTS.md or CLAUDE.md is
  caught there and named. Measured: two commits of one repository that differ only in an
  added source file produce the SAME fingerprint id
  (`env_15a292737f136808618220fe42e680f9f6c80b21ad0927afac62c2378c9b6b9c` on both arms).
  The report says all of this in its own `assertion` field rather than only in the code.

- 6.12 (W4-T1): an intervention's between-arm table is PAIRED BY PROBE. One
  `stats.compare` table per probe_id, over the same metrics the environment runner
  compares, and no row pools two probes: two probes are two questions, and a shift
  computed across them would be a shift between questions rather than between commits.
  The two score columns travel through `stats` and `stats.compare` on the same path as
  every measure, as vector keys `probe_score.precision` and `probe_score.recall`, so one
  table covers a token count and a precision without either being a special case.

- 6.12 (W4-T1): the per-probe statistics table is the repeat runner's row plus design
  6.12's two, `mdd` and `n_needed`, computed from that one condition's own n and scaled
  MAD. Design 6.12 says the report prints N_needed and does not say it is a between-arm
  number only; within one condition it is the per-arm n at which MDD would fall to a
  quarter of the median, which is what a pilot exists to answer. Observed and NOT changed:
  at a scaled MAD of 0 the formula answers `n_needed = 0`, which reads as "no repetitions
  are needed" and is a statement about a spread of 0. That is pre-existing
  `stats.n_needed` behaviour, shared with the environment runner, and the report warns
  when a score column's spread is 0.

- 6.13 (W4-T1): `experiment probe` and `experiment intervention` register themselves on
  cli.py's existing `experiment` subparser from `src/telltale/cli_probe.py`, the way
  cli_import.py, cli_outcome.py and cli_forecast.py register theirs. Their renderer is
  `src/telltale/report_probe.py`: report.py was at 635 lines with another wave 4 task
  adding to it, and two more renderers plus their paragraphs are 200 more. cli.py gains
  one import, one `add_kinds` call and one table entry, and report.py is untouched.

- Test fixture (W4-T1): `tests/integration/fake_agent.py` gains `--answer P,Q`. It reads
  the named paths that EXIST in the checkout, one Read tool_use each, makes no Edit and
  no Bash, and emits a `result` field naming what it read behind a fixed distinctive
  frame. A path that is not there is neither read nor named, which is what makes the
  agent's answer a function of the commit rather than of its argv: an intervention arm's
  answer has to be one, and the two arms run byte-identical commands. The `result` field
  is present in this mode only, so nothing W1-T4 or W2-T3 pinned changed.

<!-- folded from docs/design/amendments/W4-T2.md by the orchestrator at the wave 4 gate -->
# W4-T2 design amendments

Lines for the wave 4 amendment block of `docs/design/01-design.md`, folded in at the
wave gate by the orchestrator. Same rule as the blocks already there: each one is a
change forced by running the system, with the task that measured it in parentheses.
Nothing here edits 01-design.md.

- 6.11 (W4-T2): the cohort rule gains a SECOND gate, for a comparison between a group
  of captures and the cohort rather than between one capture and the cohort. Design
  6.11's `n >= 10` is the size of the cohort a percentile is taken over, and every
  member of it is a peer of the one capture being ranked. A repository work profile
  places a GROUP of captures against a cohort, and the group is usually inside that
  cohort: the five subsystem groups of this repository on 2026-09-03 are 16, 7, 1, 19
  and 12 captures drawn from one cohort of 31. So the gate is applied to the cohort
  MINUS the group, and the median that forms the denominator is taken over that
  outside set alone.

  The reason is not fastidiousness. `src` holds 19 of the 31, so a "cohort median"
  including it would be mostly the group's own median and the ratio would be pulled
  towards 1.0 by construction, most strongly for the groups whose n is largest, which is
  the opposite of what a reader would assume the number means. On this repository the
  outside counts are 15, 24, 30, 12 and 19, so all five groups are still placed; the
  gate is what refuses a group that IS its whole cohort, which is the case
  `tests/integration/test_profile.py` builds with three `--model opus` captures.

  The per-metric gate of `cohorts._placed` applies unchanged and separately: at least
  ten of the outside members must have MEASURED the metric. Measured on the same store,
  `exploration_scope / read_to_edit_ratio` has 15 members outside the `docs` group and 9
  of them carrying a value, so that row is refused where the other 21 metrics are not,
  and 12 outside `src` with 6 carrying one. A cohort median of 0 refuses too, rather
  than dividing: the same row for `experiments` and `fixtures` says so, because a ratio
  against 0 is undefined and both 0 and 1 in that cell are answers nobody measured.

- 6.11 (W4-T2): a group must be cohort-HOMOGENEOUS before it may be placed at all. Every
  capture in it must have all four keys known and must agree on all four, or there is no
  single cohort the whole group belongs to and spec 16's "same model/runtime family"
  clause is not satisfied. Measured on the owner's store: the default `--by week`
  profile of this repository is one ISO week holding 43 captures, of which 31 are the
  build sessions (claude, runtime major 2, opus, content level 1), 10 are
  `telltale run -- true` smoke captures whose provider is `generic` and whose runtime
  and model are unknown, and 2 have no provider. The whole week is therefore refused
  with "12 of 43 captures have an unknown cohort key", and `--by subsystem` fills the
  column. That is the rule working: a distribution over a smoke test and a build
  session mixed together is not a cohort-qualified statement about anything.

- 6.13 (W4-T2): `telltale profile <repo_id> [--path PREFIX] [--by week|subsystem|path]
  [--json] [--include-backfill]`. `src/telltale/profile.py` aggregates and
  `src/telltale/report_profile.py` renders and registers the subcommand; cli.py gains
  one import, one `add_commands` line and one `_COMMANDS` entry and nothing else.

  `--path` is BOTH a filter and, when `--by` is absent, the name of the one group the
  selected captures form. Spec 16 says "for each path/subsystem", and its allowed
  sentence is about one prefix ("sessions touching payments used 2.1x ..."), so a
  prefix is a group and not only a selection. `--by path` without `--path` is refused
  rather than defaulted, because there is then no prefix to name the group.

  A capture belongs to EVERY subsystem it edited, so subsystem groups overlap and their
  capture counts do not sum to the repository's. The frame prints `grouped` against
  `captures` and one line per reason for the difference, so a profile whose numbers are
  over fewer captures than the repository holds says where the rest went.

- 6.13 (W4-T2): every number a profile prints is an `Evidence.comparative` built in
  memory and never written, exactly as a percentile is (cohorts.py). One Evidence per
  (group, metric) carries the group's median as its value and the evidence ids of the
  captures it was taken over as its source; the scaled MAD, the minimum and the maximum
  printed beside it describe that same distribution under the same claim class,
  coverage word and source list. A second Evidence carries the ratio. The coverage of a
  row is the weakest coverage among the captures' evidence rows for that metric, and a
  metric no capture in the group measured prints `-` with the coverage word beside it.

- 6.13 and 17.2 (W4-T2): `report_profile.refuse_words` refuses the words
  `maintainability`, `quality`, `difficulty` and `score` in the FINISHED profile string,
  the way `forecast.refuse_words` refuses `cause`, `impact` and `would` under ADR-014.
  It reads the data as well as the headings, which is a stated trade: a repository with
  a top-level directory named `quality` cannot be profiled by subsystem, and the
  refusal says which word and that the profile may be taken by week instead. A check
  over the headings alone would pass a table whose group column carried the word, and
  the whole reason the check exists is that a per-path table is the shape somebody would
  add a latent scalar to.

- 6.5 (W4-T2, a MEASUREMENT and not a change): the `evidence` table has no index on
  `capture_id`, so `Reads.evidence` is a scan of the whole table. Measured on a
  `.backup` copy of the owner's store on 2026-09-03, 1541 MB, 3227 captures: 49 reads
  take 1.09 s, 22 ms each. `profile.build` therefore memoizes the read, because one
  capture is in more than one subsystem group and is usually in the cohort those groups
  are placed against as well; that took the subsystem profile of this repository from
  1.98 s to 1.03 s. `store.captures()` is the other second (1.04 s for 3227 rows, the
  view's two correlated subqueries over 1.07 M observations). Both are the same cost
  `telltale vector` pays. No index is added here: an index costs the writer on every
  insert forever, W3-T3 removed one for that reason, and the measurement belongs to
  whoever decides that trade rather than to the command that noticed it.

- Test fixture (W4-T2): `tests/integration/fake_agent.py` gains `--target PATH`, the
  file the run rewrites, defaulting to `answer.txt` so every existing caller is
  unchanged. Without it every capture of the scripted agent edits one file at the
  repository root and a subsystem grouping has nothing to group. Measured first, and
  this is why the flag exists rather than a `cwd` trick: running the launcher from
  `repo/src` records the Edit's `file_path` as `answer.txt`, because the path a tool
  call carries is the one the agent wrote and nothing rewrites a relative path against
  the repository root. `_act` now writes the path the Edit names instead of the module
  constant, which is the same file in every existing caller.

<!-- folded from docs/design/amendments/W4-F1.md by the orchestrator at the wave 4 gate -->
# W4-F1 design amendment

Lines for the wave 4 amendment block of `docs/design/01-design.md`, folded by the
orchestrator at the wave 4 gate.

- 6.5 (W4-F1, orchestrator fix after the W4-T2 measurement): the `evidence` table has
  one index, `evidence_by_capture ON evidence (capture_id)`. The gate report had left
  it "needs measuring" because W3-T3 dropped an index for its writer cost; measured on
  a copy of the owner's store (129089 evidence rows), this one costs the writer
  nothing, because the write path begins with `DELETE FROM evidence WHERE capture_id =
  ?` and that delete is the read the index serves: 16.4 ms against 0.82 ms per
  delete-and-reinsert of one capture's rows, `profile --by subsystem` 1.12 s against
  0.61 s, the CLI rebuild of 20 captures 12.09 s against 10.64 s (interleaved arms,
  numbers in schema.py). An existing store gains it at its next open, 0.078 s.

<!-- folded from docs/design/amendments/W4-T3.md by the orchestrator at the wave 4 gate -->
# W4-T3 design amendments

Lines for the wave 4 amendment block of `docs/design/01-design.md`, folded in at the
wave gate by the orchestrator. Same rule as the blocks already there: each one is a
change forced by running the system, with the task that measured it in parentheses.
Nothing here edits 01-design.md.

- 6.4, 6.10 (W4-T3): a NEWLINE inside a Bash command is a `;`, and the normalizer marks
  it as one before shlex is allowed near the line. shlex is a lexer for words and treats
  a newline as whitespace, so `cd /tmp/x` on one line and `uv run pytest -q 2>&1 | tail
  -5` on the next arrived at `classify` as a single segment whose head was `cd`; the
  segment matched nothing, the rule of the day fell through to the next one, and the
  call was recorded as `shell` on the strength of the `tail` at the end of it. Three
  newlines are NOT separators and that is the whole of the rule: one inside a quoted
  string, one inside a heredoc body, and one after a trailing `&&`, `||`, `|`, `&`, `;`
  or `(`, where the shell is still waiting for the rest of the command. An escaped
  newline is a line continuation and is skipped for the same reason. The marking is in
  `src/telltale/commands_shell.py`, split out of commands.py at the 800-line ratchet;
  `with_separators(text) -> str` is the whole of its interface.

- 6.4 (W4-T3): a HEREDOC BODY is data and is replaced by `_` before the line is lexed.
  The same sentence as the one above read to its end. Measured on the six E12 streams:
  `python3 - <<'PY' <3.9 kB of script> PY` followed on the next line by `uv run pytest`
  lexed the script into 190 characters of `_`, `(` and `)`, hit the 200-character
  MAX_COMMAND bound of design 6.4, and the pytest at the end of the line was cut off the
  stored normal form, so no rule downstream could see it. 14 of the 17 test runs still
  missing after the newline rule alone were that, and 2 more were bodies whose quoting
  made shlex refuse the whole line into the fallback. It is also a privacy improvement,
  and `test_privacy.py` measures it: `python3 - <<EOF` with a private key header in the
  body stored `python3 _ << _ _ _ _ _ _`, one `_` per word of the header, and now stores
  `python3 _ << _ _`.

  The residual, measured rather than guessed. A heredoc opened INSIDE a double-quoted
  command substitution is not seen, because the quote is read first and the whole
  substitution is one protected span: `git commit -m "$(cat <<'EOF' <message> EOF )"`
  keeps whatever of that message shlex hands back. Disabling the quote branch alone over
  the 769 Bash commands of the six E12 streams moves exactly ONE normal form and it is
  that shape, so the cost is one command in 769 and the normal form it gets is the one
  it had before W4-T3. Reading it properly means parsing `$(...)`, which the normalizer
  does not do.

- 6.4 (W4-T3): a RUNNER word does not spend the bare-token budget. Design 6.4 keeps the
  first two bare tokens of a segment, and `uv run ruff format .` has three words before
  its first argument, so `run` and `ruff` were kept and `format` became `_`. The stored
  normal form was `uv run ruff _ .`, on which the table of 6.10 matches neither
  `("ruff", "format")` nor `("ruff", "check")`, so the command was `unknown` and was not
  a verification run at all. Measured on the six E12 streams: 81 of 769 Bash calls hold
  `uv run ruff _`, and `lint` and `format` were the category of NONE of the 769. After
  the change, 47 are lint. The words freed are members of `_RUNNERS`, which is a fixed
  table, so nothing a person typed reaches the store through this: `run` is stored
  because it equals a constant. `_runner_words` is read from both ends of the pipeline,
  by `_segment` for the budget and by `_strip_runner` for the table, so the two cannot
  disagree about where a command starts.

- 6.4 (W4-T3): the three changes above all change stored strings, so
  `NORMALIZATION_VERSION` is `cmdnorm-v4` and the fallback `cmdnorm-v4-fallback`.
  Measured on the six E12 streams: 259 of 769 commands normalize differently under v4,
  with 578 separators inserted across them. The fallback set does not move: shlex refuses
  the same 40 of 769 before and after the marking, because the marking only inserts.

- 6.10 (W4-T3): a VERIFICATION command anywhere in a chain makes the call a verification
  run, and the highest-priority verification category present names it. The order is
  test, benchmark, typecheck, lint, format, build, security_scan, and it lives in
  commands.py as `_VERIFICATION_ORDER` because it is a classification rule and belongs to
  `CLASSIFIER_VERSION`; `activities_tools.VERIFICATION` is now that same tuple as a set,
  since one list cannot be two lists. The scope comes from the chosen segment. A chain
  with NO verification category keeps the rule that stood before, the first segment with
  a known category, because there the leading command is the work and the rest is what
  was done with its output.

  What forced it, measured by the orchestrator over the six E12 sessions: their raw
  streams hold 51 pytest executions (`experiments/E12/key.json`) and the reducer summed
  `agent_test_runs` to 14. `uv sync -q | tail -2; uv run pytest` was a package_op, and
  `uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest` was a
  format run. A recorder that tells a reviewer the verifier session ran no tests is
  wrong about the one thing spec 13 is for.

- 6.10 (W4-T3): a verification_run carries `categories`, every VERIFICATION category the
  chain held, in segment order and each named once. One tool call gets one category and
  the choice above is what picks it; this field is what stops the choice from hiding the
  rest. Measured on six fresh captures of the E12 streams: 124 verification_run rows,
  every one carrying the field, and the commonest values are `[test]` 39 times,
  `[lint, format]` 28 and `[format, lint, typecheck]` 9. Only the verification names,
  because the field is read as "what checking did this run do": the `uv sync` and the
  `tail` of `uv sync -q | tail -2 ; uv run pytest` are in the `command_norm` the row
  already carries, and what they are not is checking. A `command` row does not carry the
  field.

- 6.10 (W4-T3): `exit_masked` is judged for the segment `classify` CHOSE, which is no
  longer always the first one with a category. W3-T3's rule is unchanged in every other
  respect: masked when any separator after the chosen segment is not `&&`. `uv sync -q |
  tail -2 ; uv run pytest` is NOT masked, because the pytest is last and the `|` is in
  front of it; reading the `uv sync` instead would have called it masked by a pipe that
  is not in front of the test at all. A newline counts as the `;` it is, because
  `commands_shell` has already written it as one.

- 6.10 (W4-T3): `correlate._RULE_MODULES` gains `commands_shell.py`, and the reason is
  weaker than the one that put the others there, so it is written down beside them. The
  scanner decides what NORMALIZE writes at capture time and no reduction calls it, so
  editing it cannot change what the reducer makes of stored observations. What it can
  change is the strings a LATER capture stores, and until this split that code sat inside
  commands.py and moved `REDUCER_VERSION` whenever it was edited. Listing it keeps that
  exactly, at the price of a version that sometimes moves when no reduction rule did. The
  alternative is a normalizer edit that moves no version at all, because
  `NORMALIZATION_VERSION` is a constant somebody has to remember to bump.

- 6.4, 6.10 (W4-T3, a MEASUREMENT and not a change): `telltale rebuild` cannot recover a
  capture already on disk from the two normalization rules above, and this is design 6.4
  working rather than a defect. The reducer reads `command_norm` out of the observation
  payload; the RAW command line was never stored and cannot be, which is the whole point
  of the normal form. So a rebuild applies the classification rules to strings the old
  normalizer produced, and a newline the old normalizer turned into a space is gone.
  Measured on a `.backup` copy of the owner's store, the six E12 captures rebuilt:
  `agent_test_runs` goes from 14 to 19, and 19 is exactly the number of those 51 pytest
  executions that were typed on ONE line. The other 30 were multi-line commands and need
  a capture taken under cmdnorm-v4. Six fresh captures of the same six raw streams
  through the real launcher report 10, 10, 5, 11, 2 and 11 against the key's 10, 11, 6,
  11, 2 and 11.

- 6.4 (W4-T3, a MEASUREMENT and not a change): the last 2 of those 51 are lost to design
  6.4's 200-character MAX_COMMAND bound and no classification rule can reach them. Both
  are single-line chains over 300 characters whose pytest is last: `echo "=== mypy ==="
  ; uv run mypy . ... ; echo "=== pytest ===" ; uv run pytest ...` (W3-E08b/1) and a
  four-`cp` chain ending in the gate set (W4-T1/1). Both are still recorded as
  verification runs, of the highest-priority category the first 200 characters hold. The
  bound is what stops an arbitrary command line reaching the disk, so raising it is a
  privacy decision and not a classifier one, and it is the owner's.

<!-- folded from docs/design/amendments/W4-T4.md by the orchestrator at the wave 4 gate -->
# W4-T4 design amendments

Lines for the wave 4 amendment block of `docs/design/01-design.md`, folded in at the
wave gate by the orchestrator. Same rule as the blocks already there: each one is a
change forced by running the system, with the task that measured it in parentheses.
Nothing here edits 01-design.md.

- 6.3, 6.7 (W4-T4): a Claude stream ASSISTANT message states no output token count, and
  the field it calls `output_tokens` is stored as `output_tokens_snapshot`. What the
  provider puts there is a count from before the message finished. Measured by joining
  the six E12 streams to the provider's own transcripts of the same sessions, message id
  to message id: 761 message ids appear on both surfaces, every record of one message
  repeats one usage block, and the stream's number is STRICTLY SMALLER than the
  transcript's final count on all 761 of them and equal on none. On W4-T2/1 the
  snapshots run 1 to 21 and sum to 1902 for a session that produced 141206. The three
  other counters on the same message ARE final: over the same 761 requests,
  `input_tokens`, `cache_read_input_tokens` and `cache_creation_input_tokens` per message
  equal the OTel api_request's for the same request and their sums agree exactly with the
  OTel capture (340 / 35097982 / 318963 on W4-T2/1, and so on for all six). So this is
  one field and not a surface.

- 6.3, 6.7 (W4-T4): the stream RESULT message's own `usage` block is the MAIN THREAD's
  totals, not the session's, and its four counters are stored as `main_thread_*`. The
  session's figure is `modelUsage`, which is already kept whole. Measured on the eight
  E01 fixtures: `sum(modelUsage[*].outputTokens)` equals the OTel api_request sum on all
  seven that have an OTel surface, and so do its input, cache-read and cache-creation
  counterparts; `usage.output_tokens` equals it on five. It differs on S4 (350 against
  2368: exactly the two `sdk` requests, leaving out the five `agent:builtin:Explore`
  ones) and on S7 (2714 against 18548: exactly the five `sdk` requests, leaving out the
  three `query_source=compact` ones). The same rule holds for the other three counters
  on both. Reading `usage` as a session total would under-report every session that
  delegated or compacted, which is every session this recorder exists for.

- 6.10 (W4-T4): a `model_request` built from the stream carries no `output_tokens` at
  all, and carries `output_tokens_snapshot` beside its three other counters. The rule is
  keyed on the observation TYPE (`claude.stream.assistant`) and not on the field name and
  not on `usage_source`, for two measured reasons. Rows written before this amendment
  spell the snapshot `output_tokens`, so a rule that trusted the name would leave every
  stream-only capture already in a store reporting it after a rebuild, and a rebuild
  cannot re-read the raw stream. And `claude.transcript.assistant` reaches the same
  grouping with `usage_source` stream while stating a FINAL per-message count: the
  transcript sums of the six E12 sessions are 141206, 122438, 67988, 99305, 153943 and
  89125, equal to their OTel sums exactly, so the wave 2 backfill importer is right and
  is untouched by this task.

- 6.10 (W4-T4): the `session_end` lifecycle activity built from the stream result carries
  `session_output_tokens`, the sum of `modelUsage[*].outputTokens`. It is the only
  session-wide output figure Claude states, and it is on the lifecycle row rather than
  spread over the requests because the split across requests is exactly what no surface
  said.

- 6.10 (W4-T4, a rule RESTATED and not changed): `COMPARABLE_USAGE` still holds three
  counters and no conflict diagnostic is written when the OTel surface and the stream
  both deliver one request. W2-T1 excluded `output_tokens` because the two surfaces
  disagreed on 333 of 333 requests; this task measured WHY, and the reason is stronger
  than the one recorded then. A conflict is two surfaces answering the same question
  differently. A snapshot taken before a message finished is not a smaller measurement of
  that message's output, so a conflict row there would be a count of requests wearing the
  name of a defect. The difference is on the Evidence as an assumption, and the
  assumption's wording now says what the stream's figure is.

- 6.11 (W4-T4): `usage.output_tokens` is the OTel sum when the OTel surface delivered
  every request; otherwise the provider's session figure with coverage `partial` and a
  warning that per-request output tokens were not observable and that the number is the
  whole session's; otherwise null with coverage `unavailable`. The middle case is
  `partial` however complete the provider's figure is, because what a reader may do with
  it is add it up across captures and what they may not do is place it against a request.
  Measured on a `.backup` copy of the owner's store: five captures have OTel for all but
  one request each (119 of 120, 64 of 65, 75 of 76, 99 of 100, 70 of 71), and on all five
  the session figure equals what the OTel sum had been, 113910, 49360, 61272, 66486 and
  79688, so what changes there is the coverage word and the warning and not the number.
  Five independent sessions agreeing to the token is the second confirmation that
  modelUsage is the right field; the E01 fixtures are the first.

- 6.11 (W4-T4): `stable_state_tokens` is null for an interval where any of the four
  counters is unknown on any request in it, rather than a sum of the ones that are
  known. Spec 13.5 asks for the tokens spent inside an interval; on a stream-only Claude
  capture the output figure exists only for the whole session, and a sum of the other
  three under that name would be short by all of it. This is design invariant 5 applied
  to a sum: an unknown addend makes the sum unknown. It is the one metric outside the
  usage block this task changed, and the session total is still reported in the usage
  block.

- 6.12 (W4-T4): the request-clock `output_tokens` column is None in every row of a
  stream-only capture, and `blank_unobservable` therefore marks it `unavailable` with no
  further code. Verified on a real stream-only capture in the owner's store: five rows,
  five nulls, coverage `unavailable`, `column_report` reason `no value in any row`. The
  readiness checklist names it both ways: check 1 on another target prints `not
  forecastable: output_tokens (unavailable)`, and a forecast targeting output_tokens is
  refused by name before any window is formed.

- 6.10, 6.11 (W4-T4, a MEASUREMENT and not a change): a capture already on disk IS
  recoverable by `telltale rebuild`, unlike W4-T3's normalization rules. The reducer
  needs two things the observations already hold: the assistant rows, whose old
  `output_tokens` spelling the type-keyed rule above catches, and the result row's
  `model_usage`, which has been stored since W0-T2 (the first sanitizer commit) because
  the context window denominator is read from it. Measured on a `.backup` copy of the owner's store, 3230
  captures: 3 Claude captures are stream-only, all 3 have a result record, and rebuilding
  them moves output_tokens from `observed` to `partial` with the warning. Their VALUE
  does not move, and that is a fact about those three rather than about the rule: all
  three are scripted-agent captures (`tests/integration/fake_agent.py`), whose fabricated
  per-message numbers are consistent with its own totals, so 320 is 320 either way. The
  six E12 captures are where a real stream-only value moves, from 1902, 912, 636, 1620,
  1421 and 1271 to 141206, 122438, 67988, 99305, 153943 and 89125. 1648 captures in that
  store are transcript-only; 30 sampled and rebuilt do not move at all. No capture in the
  store needs its raw stream re-read.

<!-- folded from docs/design/amendments/W4-F2.md by the orchestrator at the wave 4 gate -->
# W4-F2 design amendment

Lines for the wave 4 amendment block of `docs/design/01-design.md`, folded by the
orchestrator at the wave 4 gate.

- 6.4 (W4-F2, orchestrator fix, owner decision of 2026-09-03): a stored command normal
  form is bounded at 512 characters, the bound every other kept string has, and no
  longer at 200. `NORMALIZATION_VERSION` is `cmdnorm-v5` (fallback `cmdnorm-v5-fallback`).
  Why: W4-T3 measured on the six E12 streams that 2 of their 51 pytest runs sat past the
  200th character of a chain's normal form and were cut off it, so the recorder called
  one chain a typecheck and the other a lint. Measured with the bound lifted on the same
  769 Bash commands: 100 normal forms reach 200 characters, 7 reach 512, none reaches
  768 (the longest is 717), and no pytest segment starts past character 300; at 512 the
  E12 key and `telltale show` agree on the pytest count of all six sessions (51 of 51).
  What a longer form carries: flags, placeholders and paths already made repo-relative
  or hashed, never a value; the secret scrub runs before the bound as before. A row cut
  at 200 by v4 cannot be extended by a rewrite (the raw line was never stored), which is
  why the version moves and `store.resanitize` marks such rows rather than restoring
  them.

<!-- folded from docs/design/amendments/W4-T5.md by the orchestrator at the wave 4 gate -->
# W4-T5 design amendments

Wave 4. Each line is a change forced by running the system, with the task that measured
it in parentheses, in the form the orchestrator folds into `docs/design/01-design.md` at
the wave gate.

- 6.12, spec 14.2 and 14.3 (W4-T5): `experiments_env.FACTORS` gains `instructions`, and
  both two-arm runners take it. An arm's value is a COMMIT, carried by the arm key
  `base_sha` as for the repository factor; the spec's `factor` is what says which
  assertion applies to that pair of commits. `telltale experiment intervention` now
  accepts either commit-valued factor, which is what lets E10 (the same probes before
  and after an AGENTS.md rewrite on a branch) run at all: the base_sha assertion refused
  it, correctly, and the owner approved the amendment on 2026-09-03.

- 6.12 (W4-T5): the post-run assertion for `instructions` is the MIRROR of base_sha's
  and not a relaxation of it. base_sha asserts the two arms' fingerprint payloads differ
  in NO field; `instructions` asserts they differ in `instruction_hashes` and in no
  other field. Every field base_sha holds constant this factor still holds constant, and
  the one field base_sha forbids moving is the one this factor requires to move. A
  relaxation would have been "differ in nothing except instruction_hashes, and do not
  check what else moved", which is a different sentence: it would pass an arm pair that
  also changed the model, since a payload differing in two fields would still contain
  the expected one. The check is equality of the sorted differing-field list against a
  per-factor table (`_EXPECTED_FIELDS`), and `FACTORS` is that table's keys, so a factor
  cannot be declared without an assertion.

- 6.12, 6.8 (W4-T5): the post-run assertion alone would be a HOPE, and the pre-run diff
  check is what makes it a mechanism. `instruction_hashes` moves whether or not the same
  commit also edited a module, and the payload carries no commit, so a two-factor commit
  passes the fingerprint check and reports the instructions as the only difference.
  Before anything runs, `git diff --name-only -z <shaA> <shaB>` in the spec's repository
  must name at least one path, and every path it names must be a surface env.py hashes.
  Otherwise the spec is refused with the offending paths listed and the surfaces named:
  `factor instructions, and before=<sha> and after=<sha> differ in 2 path(s) of which
  ['src/gamma.py'] is not an instruction surface the fingerprint hashes (CLAUDE.md,
  AGENTS.md, .claude/rules/*.md): a commit that changes code AND an instruction file is
  two factors`. An empty diff is refused too: two shas with one tree are one arm.
  Measured on this repository: the check is 3.9 ms over a one-path diff and 4.4 ms over
  a 33-path one, which is one `git diff` and a shape test per path.

- 6.8, 6.12 (W4-T5): the surfaces the diff check accepts are read from `env.py`'s own
  constants (`_REPO_FILES`, `_RULES_DIR`) and never listed a second time. What the check
  adds is the SHAPE, since a git diff names a path in a commit and nothing is checked
  out while it runs: the repository-root `CLAUDE.md` and `AGENTS.md`, and `*.md`
  directly inside the rules directory. `docs/AGENTS.md` and `.claude/rules/deep/x.md`
  are not surfaces, because an arm runs with the worktree root as its working directory
  and env.py globs one level. That shape is a restatement of behaviour, so it is pinned
  by a test that builds a directory holding one file of every shape, takes a real
  `env.fingerprint` of it, and asserts the hashed paths and the accepted paths are the
  same set. The direction that must not drift is named there: a path env.py hashes and
  the check refuses costs a readable refusal, while a path env.py does not hash and the
  check accepts would let a code change through as an instruction change.

- 6.12 (W4-T5): `instruction_hashes` is one mapping over every surface in play, and the
  operator's home files (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`) are hashed into it
  and are equal for both arms. So "the field differs" names a field and not a file, and
  the assertion now carries `instruction_paths`: the paths inside the field whose digest
  differs, with each arm's `{sha256, bytes}` beside them, and `None` for an arm that does
  not carry the file at all. It is present on every report and empty when the field did
  not move, rather than absent, so a reader who does not see it is not left guessing
  which case they are in. `report_probe` prints it instead of the field's two values
  (`instruction_hashes differ at AGENTS.md: before=sha256 faeca353850b, 36 bytes,
  after=sha256 905381088818, 73 bytes`), and the post-run refusal appends the same list.

- 6.12 (W4-T5): the first assumption of both reports is now the experiment's SHAPE and
  depends on the declared factor, since one line cannot be true of all five. A launch
  flag says two arms at ONE base commit; base_sha says two commits and NO differing
  field; `instructions` says two commits that differ only in the instruction surfaces,
  asserted on the argv, on the paths `git diff` names, and on the payloads. The base_sha
  report keeps its sentence that holding the fingerprint constant is what makes the
  commit the only declared difference and is not evidence that it is the only difference
  there is; the instructions report says the matching thing about its diff check.

- Structure (W4-T5): `src/telltale/experiments_factor.py` (new, 492 lines) holds the
  factor tables and the two assertions; `experiments_env.py` (707 to 451) keeps the two
  runners and their reports. The factor took experiments_env.py to 910 lines, past the
  800-line ratchet, and this is the seam its own docstring already named: the runners run
  and report, and the factor is the question they are checked against. Public interface:
  `FACTORS`, `COMMIT_FACTORS`, `REPOSITORY_FACTOR`, `INSTRUCTIONS_FACTOR`,
  `INSTRUCTION_FIELD`, `SHAPE`, `arm_sha`, `assert_declared`, `assert_between`.
  `experiments/E06/run.py` calls `assert_between` under its new name; nothing else
  outside the two files referred to the moved names.

- Test fixture (W4-T5): `tests/integration/experiment_helpers.py` (new, 322 lines) holds
  the repositories, the argv and the constants that test_experiments.py, test_probe.py
  and test_instructions.py share. test_experiments.py was at exactly 800 lines, the
  ratchet's limit, which W4-T4 said the next task to touch it had to fix; it is now 635
  and test_probe.py is 670. Three copies of `_git`, `_store` and `_telltale` became one
  each. `probe_repository(root, agents=None)` is the W4-T1 fixture with one parameter
  added: the base_sha tests pass nothing and get the byte-identical repository they had,
  and the instructions tests ask for an AGENTS.md in the first commit.
