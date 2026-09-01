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
only), verification_runs_since_prev, last_verification_exit (0/1/None), env_changed.

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
