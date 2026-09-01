<!-- Verbatim copy of section 2 of the approved implementation plan (~/.claude/plans/i-need-you-to-fuzzy-journal.md), taken 2026-09-01 by W0-T1. Edit the plan, not this file. -->

## 2. Research digest (verified 2026-09-01; every fact has a source)

### 2.1 Claude Code 2.1.257 (installed)

Sources: code.claude.com/docs/en/monitoring-usage, /hooks, /headless, /cli-reference; the
SDK type file `@anthropic-ai/claude-code/sdk.d.ts` (copy at
/opt/homebrew/lib/node_modules/task-master-ai/node_modules/@anthropic-ai/claude-code/,
version 1.0.117, so field lists may be stale); real transcripts in `~/.claude/projects/`.

- OTel: `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_LOGS_EXPORTER=otlp`,
  `OTEL_METRICS_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_PROTOCOL` in {grpc, http/json,
  http/protobuf} with no default, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_LOGS_EXPORT_INTERVAL`
  (default 5000 ms), `OTEL_METRIC_EXPORT_INTERVAL` (default 60000 ms),
  `OTEL_RESOURCE_ATTRIBUTES=key=value,...` (custom attributes on every event; no spaces).
  **http/json is supported: the receiver needs no protobuf.**
- Content switches: `OTEL_LOG_USER_PROMPTS` (off), `OTEL_LOG_ASSISTANT_RESPONSES` (off),
  `OTEL_LOG_TOOL_DETAILS=1` (Level 1 only: tool_input JSON bounded to about 4 KB with values
  over 512 chars truncated; bash_command and full_command; file paths for Read/Edit/Write;
  subagent_type; skill_name; `git_commit_id` when a `git commit` succeeds; full error
  message), `OTEL_LOG_TOOL_CONTENT` (needs tracing; never enabled by Telltale).
- Events (OTLP log records; body is the event name; attributes include `event.name`,
  `event.timestamp`, `event.sequence`, `session.id`, `prompt.id`, `message.uuid`,
  `user.id`, `user.email` (when logged in), `terminal.type`, `app.version` opt-in via
  `OTEL_METRICS_INCLUDE_VERSION`): `claude_code.api_request` (model, input_tokens,
  output_tokens, cache_read_tokens, cache_creation_tokens, cost_usd, duration_ms,
  request_id, client_request_id, speed, effort, query_source in {repl_main_thread,
  compact, subagent name}, agent.name), `api_error` (status_code, attempt, error),
  `api_refusal`, `tool_result` (tool_name, tool_use_id, success, duration_ms, error_type,
  tool_input_size_bytes, tool_result_size_bytes, decision_source, mcp_server_scope, and
  with details on: tool_parameters, tool_input), `tool_decision` (decision, source,
  tool_source), `user_prompt` (prompt_length; prompt text redacted), `assistant_response`
  (response_length; text redacted), `permission_mode_changed`, `auth`,
  `mcp_server_connection`. Metrics: `claude_code.session.count` (start_type fresh/resume/
  continue), `token.usage` (type input/output/cacheRead/cacheCreation, model,
  query_source), `cost.usage`, `lines_of_code.count` (type added/removed), `commit.count`,
  `pull_request.count`, `active_time.total`.
- **No compaction OTel event exists.** Compaction is visible as api_request with
  `query_source=compact`, PreCompact/PostCompact hooks, the stream-json
  `system/compact_boundary` message (`compact_metadata: {trigger, pre_tokens}`), and in
  transcripts as `type=system, subtype=compact_boundary, compactMetadata: {trigger,
  preTokens, postTokens, cumulativeDroppedTokens, durationMs}`.
- Hooks: SessionStart (source: startup|resume|clear|compact|fork), SessionEnd (reason),
  UserPromptSubmit, PreToolUse, PostToolUse (tool_name, tool_input, tool_response,
  tool_use_id), PostToolUseFailure, PostToolBatch, Notification, SubagentStart/Stop
  (agent_id, agent_type), Stop (stop_reason, last_assistant_message), PreCompact
  (trigger, custom_instructions), PostCompact, PreModelSwitch/PostModelSwitch,
  WorktreeCreate/Remove, plus others. Common input: session_id, prompt_id,
  transcript_path, cwd, permission_mode, hook_event_name, agent_id/agent_type (in a
  subagent), effort.level. **Hook type `http` exists**: `{"type":"http","url":"http://
  127.0.0.1:PORT/path","timeout":N}`; body is the hook JSON; response body uses the
  command-hook output format. Settable per launch with `--settings '<json>'`. Behaviour
  when the endpoint is unreachable is undocumented: E01 measures it.
- Headless: `claude -p --output-format stream-json --verbose [--include-partial-messages]
  [--forward-subagent-text]`. Message types: `system/init` (session_id, model, tools,
  mcp_servers, permissionMode, output_style, cwd, apiKeySource, claude_code_version,
  capabilities), `assistant` and `user` (with `parent_tool_use_id`; assistant `message.usage`
  carries input_tokens, cache_creation_input_tokens, cache_read_input_tokens,
  output_tokens; content blocks tool_use {id, name, input} and tool_result {tool_use_id,
  is_error, content}), `system/compact_boundary`, `system/api_retry` (attempt,
  max_retries, retry_delay_ms, error_status, error), `result` (subtype success|error_*,
  duration_ms, duration_api_ms, num_turns, total_cost_usd, usage, modelUsage per model with
  contextWindow, permission_denials). `--session-id <uuid>` pins the id (correlation
  before the first event). `--bare` skips hooks, CLAUDE.md and MCP but uses the API key
  rather than the subscription, so Telltale does not use it. `--effort`, `--model`,
  `--max-turns`, `--max-budget-usd`, `--no-session-persistence` exist.
- Transcript JSONL (`~/.claude/projects/<slug>/<session>.jsonl`): assistant lines carry
  requestId, message.usage (incl. cache_creation.ephemeral_1h/5m), model, effort,
  version, gitBranch, cwd, sessionId; `isSidechain` marks subagent lines; tool_use inputs
  are in full (so the importer must sanitize); compaction line as above. Backfill only.
- Owner's `~/.claude/settings.json` already has command hooks on SessionStart, Stop,
  PostToolUse, UserPromptSubmit, PostCompact (praxis, context handoff), `defaultMode:
  auto`, model `claude-fable-5-1[1m]`, `effortLevel: xhigh`, `autoCompactWindow: 700000`,
  env `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=60`. Telltale never edits this file.

### 2.2 Codex CLI 0.150.1 (installed)

Sources: `codex exec --help` (local); learn.chatgpt.com/docs/non-interactive-mode.md,
/config-file/config-reference.md, /hooks.md; local rollouts in `~/.codex/sessions/`.

- `codex exec --json` emits JSONL: `thread.started {thread_id}`, `turn.started`,
  `turn.completed {usage: input_tokens, cached_input_tokens, output_tokens,
  reasoning_output_tokens}`, `turn.failed`, `item.started|updated|completed` with item
  types agent_message (text), reasoning (never persisted), command_execution (command,
  aggregated_output, exit_code, status), file_change (path, kind), mcp_tool_call,
  web_search, todo_list, error. Flags: `-C dir`, `-m model`, `-c key=value` (TOML value),
  `-c model_reasoning_effort="high"`, `-s read-only|workspace-write|danger-full-access`,
  `--skip-git-repo-check`, `--ephemeral`, `--ignore-user-config`, `--output-last-message`,
  `--output-schema`, `--dangerously-bypass-hook-trust`, `exec resume <id>`.
- OTel keys: `otel.exporter = "none|otlp-http|otlp-grpc"` with `otel.exporter.<id>.endpoint`,
  `otel.exporter.<id>.protocol = "binary|json"`, `otel.exporter.<id>.headers`;
  `otel.log_user_prompt` (bool, off); `otel.environment`; `otel.trace_exporter`;
  `otel.metrics_exporter` (default statsig). **json protocol is supported.** The exact TOML
  spelling for `-c` and the event vocabulary are undocumented: E02 captures them.
- Hooks: PreToolUse, PermissionRequest, PostToolUse, PreCompact, PostCompact,
  SessionStart, SessionEnd, SubagentStart, SubagentStop, UserPromptSubmit, Stop. Types
  `command` and `mcp_tool` only (no http). Locations `~/.codex/hooks.json`,
  `<repo>/.codex/hooks.json`, or inline `[hooks]` in config.toml. Input: session_id, cwd,
  hook_event_name, model, transcript_path, turn_id, tool_name, tool_input, tool_response.
  Non-managed hooks need trust; `--dangerously-bypass-hook-trust` for automation.
- Rollout files (`~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl`): `session_meta`
  (session_id, cwd, cli_version, model_provider, originator codex_exec, source exec),
  `turn_context` (turn_id, model, effort, sandbox_policy, approval_policy),
  `event_msg/token_count` with `info.total_token_usage` and `info.last_token_usage`
  (input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens,
  reasoning_output_tokens, total_tokens) and **`info.model_context_window` (258400 seen):
  a trustworthy occupancy denominator exists for Codex**; `response_item/custom_tool_call`
  (name, input script) and `custom_tool_call_output`; `event_msg/item_completed`;
  `task_started`, `task_complete`. Reasoning items are present in rollouts and are never
  persisted by Telltale.
- Owner's `~/.codex/config.toml`: model gpt-5.6-sol, reasoning high, sandbox
  workspace-write with network, several MCP servers, plan type plus.

### 2.3 kstrl 0.2.0 (owner's orchestrator; editable install from ~/Documents/code/ralph)

Deferred by owner decision. Facts kept for the day it is enabled: launches `claude --print
--output-format stream-json --verbose [--model] [--effort]` with the prompt on stdin and
`codex exec -C <cwd> -m <model> -c model_reasoning_effort=...`; subprocess env is inherited;
per-component worktrees at `.kstrl/worktrees/<run>/<component>`; event log
`.kstrl/runs/<run_id>/events.jsonl` with envelope `{schema:2, event, ts, run_id, component,
source, seq, data}` and events verification_result, review_result, component_usage,
component_retrying (attempt), contract_result, merge_pending, budget_exceeded.

### 2.4 TimesFM 3.0.0 (PyPI, released 2026-08-28)

Sources: pypi.org/project/timesfm; `google-research/timesfm` at master:
`src/timesfm3/{evaluator,timesfm3_forecaster}.py`, `pyproject.toml` (read via GitHub API).

- Install `timesfm[torch]==3.0.0` (deps numpy>=1.26.4, huggingface_hub>=0.28,
  safetensors>=0.5.3; extra torch>=2.0). Python >=3.10. Checkpoint
  `google/timesfm-3.0-pytorch`, downloaded from Hugging Face on first use (`cache_dir`,
  `local_files_only` supported). Size and download time: measured in E03.
- API: `from timesfm3 import TimesFM3Forecaster, TimesFM3Evaluator, ModelConfig`;
  `ModelConfig(checkpoint_path, per_core_batch_size=4, device="cpu"|"cuda"|None,
  quantiles=[0.1,...,0.9], use_stitching=True, use_linear_detrending=True,
  use_iterative_cpm_revin=True, use_variate_attention=True, cache_dir, local_files_only)`;
  `predict_batch(contexts: list[np.ndarray], horizon: int, past_only_covariates=None,
  past_future_covariates=None, ts_ids=None, return_quantiles=True,
  use_symmetric_averaging, make_positive, sort_quantiles, use_znorm, padding_mode)` yields
  `ForecastOutput(ts_id, forecast (H,) or (V,H), quantiles (H,9) or (V,H,9))`. Contexts
  are 2D `(num_variates, context_length)`; `_MAX_CONTEXT_LENGTH = 15360`; **at most 32
  variates per forward pass** (targets + past-only + past-future); the Evaluator subsamples
  covariates at random beyond 31, which Telltale must never rely on: variate counts stay
  small and explicit.
- **The forecaster linearly interpolates NaN internally** (`linear_interpolation`).
  Telltale never passes NaN: gaps are refused or filled by a named policy before the call.
- 330M parameters, 9 quantiles (0.1..0.9), per-series normalization, whole-horizon
  generation. `device="cpu"` is an accepted value; M5 CPU/MPS timing is measured in E03.
- **License: 3.0 weights are `timesfm-non-commercial-license-v1.0`, non-commercial and
  non-production only** (code Apache-2.0). Research use is fine; a production advisory
  would need a different model behind the same Forecaster interface. Owner acknowledgment
  at the wave 5 gate.

### 2.5 deckgen tooling (inspiration; measured facts carried over)

Sources: `~/Documents/code/deckgen/{.pre-commit-config.yaml, pyproject.toml,
.github/workflows/ci.yml, .gitleaks.toml, .github/dependabot.yml, scripts/precommit/*.py,
AGENTS.md, .gitignore, docs/spec/new-repo-CLAUDE.md, docs/procedures/plan.md}`.

Kept as-is (deckgen already measured the reasons; its comments travel with the config):
pre-commit-hooks hygiene set; ruff check --fix before ruff format; explicit ruff select
(never ALL) with PGH guarding blanket suppressions; complexipy --staged at cognitive 15 with
--suggest-refactors; cyclomatic ratchet at 10 (script); file-length ratchet at 800 lines
(script); no-em-dash pygrep; no-mocks-in-production (ast); agent-docs-sync; codespell;
vulture at min_confidence 80; gitleaks on the staged diff plus a manual-stage tree scan
that CI runs; uv-lock; shellcheck; actionlint, zizmor --offline, check-jsonschema
(workflows, dependabot, require-timeout); mypy strict + warn_unreachable in CI and as a uv
target, never in pre-commit; deptry in CI; `uv audit --preview-features audit-command`;
actions pinned to SHAs with persist-credentials false, least-privilege permissions,
concurrency cancel except on main, UV_LOCKED=1, timeout-minutes on every job; dependabot
for actions, monthly, 7-day cooldown; 30-day dependency cooling applied per package at
upgrade time (`uv lock --upgrade-package X --exclude-newer-package 'X=30 days'`).
`scripts/precommit/*.py` are copied byte-identical (deckgen's shared unit).

Improvements for Telltale (each is a mechanism):
1. One package, no workspace: one deptry run, one mypy run (deckgen needed three).
2. Integration-only tests by construction: `tests/integration/` is the only test directory;
   a conftest hook fails collection of any test file elsewhere; `--strict-markers` with
   exactly two markers, `integration` and `live`.
3. `forecast-isolation` pygrep: `import torch|timesfm` allowed only in
   `src/telltale/forecast/timesfm.py` (ADR-009: the collector never imports the model stack).
4. `derived-writes-only-in-store` pygrep: `INSERT INTO (activities|evidence|
   series_snapshots|forecast_runs)` only in store.py, so the CHECK constraints and the one
   Evidence constructor are the only way a derived number is written.
5. `tests-live-under-integration` hook: fails on any `tests/**/test_*.py` outside
   `tests/integration/`.
6. Ruff additions over deckgen: `DTZ` (naive datetimes are a real bug class in a recorder),
   the curated `S` slice (S102, S307, S602, S604, S605, S606, S608, S113, S324, S501),
   `T20` with cli.py and report.py as the only exemptions.
7. CI: one job, no docker, no node, measured timeouts written into the workflow comment
   after the first green run.
8. `check-jsonschema` over `fixtures/**/*.jsonl` is replaced by the privacy integration
   test, which is stronger (bytes, not shape); the observation JSON schema is exported from
   the dataclasses by `telltale schema` for readers, not enforced twice.
9. `.python-version` = 3.12 committed, and CI `UV_PYTHON: "3.12"`, so local and CI resolve
   the same interpreter.
