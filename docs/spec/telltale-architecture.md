1 September 2026

## DECISION

Build a local coding-agent flight recorder and a TimesFM-3 forecasting laboratory together, but make the boundary between measurement, interpretation, prediction and causation explicit in the architecture. The system is successful even if forecasting fails; it is not successful if it produces confident stories that the data cannot support.

Telltale records observable coding-agent work, reconstructs software-work activities, joins those activities to repository state and independent outcomes, and compiles carefully scoped numerical series for forecasting experiments. The research question is not assumed to be true: does the behavioural trail of coding agents contain stable, temporally useful information about software work and repository evolution beyond final diffs and ordinary verification outcomes?

## Design stance

> **•** Observable agent behaviour is not a direct measurement of cognition. Context usage, compaction, exploration and edit turnover are facts about execution, not proof of confusion or difficulty.
>
> **•** Repository association is not repository causation. A hard task can make a module look expensive even when the module is well designed.
>
> **•** An ordered sequence is not automatically a forecastable time series. Telltale must establish that chronology adds signal before describing change-index predictions as software-evolution forecasts.
>
> **•** TimesFM-3 is an active research engine, not an oracle. It must beat simple baselines and survive temporal-placebo tests for each target it supports.
>
> **•** Outcomes are evidence with different strengths, not a hidden universal quality label. Tests, reviewers, merges, reverts and production incidents remain separate observations.
>
> **•** Candidate-conditioned forecasts are associative. One-step conditioning is the default; longer horizons require an explicit future-workload scenario.
>
> **•** All advisory use begins in shadow mode. Once a forecast changes Kstrl behaviour, that intervention is recorded because it changes the future data-generating process.

## Primary success conditions

> **1.** A local session view reconstructs what the agent did more reliably and quickly than reading provider logs or transcripts.
>
> **2.** Repeated-task experiments show which measures are stable enough to compare and how much variance comes from model/runtime randomness rather than software.
>
> **3.** Controlled repository interventions or fixed probes demonstrate whether software structure measurably changes agent work under comparable conditions.
>
> **4.** For at least some concrete targets, true chronological history gives TimesFM-3 useful forecast information beyond deterministic baselines and shuffled-order placebos.
>
> **5.** Agent-state and process semantics add incremental temporal information in controlled ablations; if not, they remain diagnostic signals only.
>
> **6.** Candidate conditioning provides calibrated one-step advisory information without being presented as causal impact.

# 1. Purpose and scope

## 1.1 The problem

Coding-agent systems expose unusually fine-grained traces of software work: model requests, token usage, compactions, tool calls, file reads and writes, commands, test runs, repeated attempts and repository mutations. The final patch discards most of that history. Telltale exists to preserve the safe, useful portion of the work path and ask what engineering information it contains.

The system deliberately separates three products that can succeed or fail independently:

> **•** Flight recorder: high-fidelity, local, privacy-preserving reconstruction of coding-agent work.
>
> **•** Research instrument: controlled experiments that determine which behavioural measures are stable and what they can legitimately support.
>
> **•** Forecast laboratory: TimesFM-3 experiments over request, attempt and accepted-change sequences, with deterministic baselines and explicit validity tests.

## 1.2 Non-goals

> **•** A generic LLM trace dashboard, cost monitor or prompt-management system.
>
> **•** A hidden “agent difficulty” or “code quality” score.
>
> **•** A supervised good/bad PR classifier.
>
> **•** A claim that agent behaviour reveals private chain-of-thought or cognitive state.
>
> **•** A causal-inference system for the effect of merging a pull request.
>
> **•** A correctness oracle or replacement for Kstrl verification.
>
> **•** A cloud SaaS or distributed telemetry platform in the early architecture.

## 1.3 Terminology

| **Term** | **Meaning** |
|----|----|
| Observed agent work | Provider/Telltale facts about actions and resource use. Does not imply struggle, confusion or difficulty. |
| Interaction burden | A neutral description of how much observable work occurred under a stated task, model, runtime and configuration. |
| Edit turnover | The path of repository mutations, revisits and reversions. Neutral replacement for “edit churn”. |
| Stable-state work interval | An interval where repository diff and verification signature are unchanged while work continues. Neutral replacement for “no progress”. |
| Context token burden | Reported prompt/cache/context-related token quantities and compaction facts. Not automatically “context pressure”. |
| Repository work profile | Cohort-qualified distribution of observed agent work for tasks touching an area. Not an intrinsic maintainability score. |
| Candidate-conditioned forecast | A forecast produced while supplying known candidate features. It is associative, not a causal effect estimate. |

# 2. Epistemic contract

Telltale’s principal failure mode would be an inference error rather than a storage error: turning a measurement into an interpretation without preserving the boundary. Claim class is therefore part of the data and API model, not merely UI wording.

## 2.1 Claim classes

| **Class** | **Example** | **Permitted language** | **Not permitted** |
|----|----|----|----|
| observed | Claude emitted a compaction event | “A compaction occurred.” | “The agent forgot the codebase.” |
| derived | Four edits followed the last passing test | “4 edits occurred after the last successful test.” | “The patch is unsafe.” |
| comparative | Token burden is P91 within a matched cohort | “Higher than 91% of this cohort.” | “The code is 91% harder.” |
| associative | High edit turnover co-occurs with rework | “Associated with / accompanies.” | “Causes rework.” |
| predictive | TimesFM forecasts more Kstrl retries | “Forecast / predicted conditional trajectory.” | “Will happen” or “caused by this PR”. |
| causal | Effect of an intervention | Unsupported by ordinary observational Telltale data. Only controlled interventions may support narrowly scoped causal language. | Any causal claim from ordinary candidate-conditioned forecasts. |

## 2.2 Evidence metadata

Every materialized semantic measure and forecast output carries claim_class, source/provenance, coverage, cohort, environment fingerprint, reducer/schema version, and any assumptions required for interpretation. Query surfaces must not silently upgrade claim class.

{

"metric": "edits_after_last_successful_test",

"value": 4,

"claim_class": "derived",

"source": \["act_edit\_...", "act_test\_..."\],

"coverage": "observed",

"environment_fingerprint_id": "env\_...",

"reducer_version": "verification-v2"

}

## 2.3 Evidence hierarchy for outcomes

Downstream outcomes are stored separately because they answer different questions and have different epistemic strength. No reducer collapses them into a universal quality label.

| **Outcome family** | **What it actually establishes** | **Typical caveat** |
|----|----|----|
| Mechanical verification | The configured checks passed or failed on the measured state. | Coverage and oracle quality are bounded by the checks that exist. |
| Adversarial review | A reviewer under a stated model/prompt produced findings or accepted. | Still an LLM judgement; reviewer calibration matters. |
| Merge decision | The policy/human allowed the change to enter the base. | May reflect workflow policy, not correctness. |
| Revert / follow-up repair | A later action suggests the prior state needed reversal or repair. | Detection window and attribution can be ambiguous. |
| Runtime/production signal | Observed operational effect after release. | Confounded by workload, deployment and unrelated changes. |

# 3. Research hypotheses and falsification

## 3.1 H1 - the work path has standalone diagnostic value

The sequence of exploration, edits, verification, failures, compactions, delegation and stable-state work intervals contains process facts that are costly to reconstruct from the terminal patch or provider transcript.

> **•** Evaluate blinded session diagnosis using concrete facts, time-to-diagnosis and inter-reviewer agreement.
>
> **•** Treat correlation with duration, tokens or diff size as a confound diagnostic, not a validity test.

## 3.2 H2 - some work measures are stable enough to compare

Repeated executions of the same task against the same commit, model, runtime, instructions and tool configuration should reveal the stochastic floor for each measure. A measure is unsuitable for repository comparison if within-condition variance is of the same order as or larger than the differences we want to attribute to repository state.

> **•** Run the same scripted/benchmark task repeatedly, initially targeting 10-20 repetitions where cost permits.
>
> **•** Report median, robust spread and full distributions; do not hide high stochasticity behind a single percentile.

## 3.3 H3 - execution environment materially affects telemetry

Model version, reasoning effort, provider runtime, instruction set, Kstrl feedforward context, tool/MCP availability and sandbox posture can move the same measures independently of the repository. Telltale therefore treats environment as a first-class covariate/changepoint, not metadata decoration.

> **•** Same task + same commit, vary one environment factor at a time where practical.
>
> **•** Do not compare raw context/token quantities across materially different environment fingerprints without explicit cohorting.

## 3.4 H4 - software structure can change observed agent work

The strongest support for a repository property comes from controlled or quasi-controlled comparison, not natural task history. Fixed read-only legibility probes and repeated task templates are run before and after selected repository interventions, such as an architectural refactor, while holding the agent environment as constant as possible.

A before/after shift under controlled probes can support a narrow claim that the repository intervention changed the observed interaction burden for those probes. It still does not establish a universal maintainability score.

## 3.5 H5 - the chronological sequence contains forecastable temporal structure

An accepted-change index is merely an ordering until shown otherwise. The next task can be an exogenous disturbance unrelated to the last one. Before calling a model result software-evolution forecasting, Telltale runs temporal-placebo tests comparing the true order with block-shuffled or otherwise chronology-destroyed histories while preserving marginal feature distributions.

> **•** If chronological order provides no material advantage, Telltale may still support conditional prediction, but it must not claim the system has learned repository evolution.

## 3.6 H6 - TimesFM-3 adds value over deterministic baselines

For a target to remain model-backed, TimesFM-3 must provide repeatable improvement in point error, quantile calibration or useful threshold-warning lead time over persistence, rolling median/mean and simple local drift on rolling-origin future windows.

## 3.7 H7 - agent state and process semantics add temporal information

A/B/C ablations test whether behavioural telemetry adds value to the same TimesFM forecasting task: A uses repository/change variables only; B adds raw agent state; C adds neutral software-work semantics. If C does not improve on B/A, the semantics remain diagnostic rather than predictive.

## 3.8 H8 - candidate features can condition a useful near-term forecast

Candidate-conditioned forecasting begins one step ahead because the proposed change features are actually known for that step. Longer horizons require an explicit future-workload scenario; Telltale must not silently fill unknown future change features with zero or historical averages and then present the result as “what happens if this PR merges”.

# 4. Threats to validity and assumptions

| **Threat** | **Bad assumption to avoid** | **Architectural response** |
|----|----|----|
| Cognitive-state inference | High context, compaction or repeated edits means the agent was confused. | Store neutral work facts; interpretation remains a separate claim class. |
| Task-mix confounding | Sessions touching a module are comparable because they touch the same module. | Task metadata, repeated templates and fixed probes; cohort-qualified reporting. |
| Harness/model confounding | A rising token series reflects repository deterioration. | ExecutionEnvironment fingerprint; explicit changepoints and cohorts. |
| No temporal dependence | Accepted changes form a forecastable dynamical process by definition. | Chronology placebo and baseline tests before temporal claims. |
| Data scarcity | Zero-shot forecasting removes the need for sufficient history. | Per-target forecast-readiness checks; no global minimum invented in advance. |
| Non-random missingness | A missing value can be masked and safely mixed with every cohort. | Capability-homogeneous series where possible; no silent zero fill; missingness diagnostics. |
| Outcome ground truth | A Kstrl pass/fail or reviewer verdict equals software quality. | Keep outcome families distinct and provenance-rich. |
| Survivorship bias | Accepted-change history represents factory health. | Attempt and accepted-change clocks are analysed together; aborted/rejected work remains visible. |
| Feedback policy | A forecast remains observational after Kstrl starts acting on it. | Shadow mode first; record policy interventions; segment post-intervention evaluation. |
| Candidate causal effect | Conditioned/unconditioned forecast delta is the impact of merging. | Associative language only; one-step first; no counterfactual wording. |

## 4.1 Data may be abundant in events but scarce in independent histories

Thousands of tool events do not equal thousands of forecasting examples. An accepted-change series with 80 changes is one 80-step repository history, and model/runtime/Kstrl upgrades can divide that history into shorter comparability regimes. Forecast readiness is determined per target and clock by rolling-origin feasibility and stability, not event count.

## 4.2 Missingness is part of the data-generating process

Telemetry availability varies by provider, CLI version, capture mode and configuration. A mask in Telltale’s internal series schema does not imply the forecasting model consumes an arbitrary mask. The current TimesFM-3 public predict_batch examples expose numerical target/covariate arrays, not a general user-supplied missingness-mask argument \[R2\]. The forecast adapter must therefore choose an explicit strategy rather than pretending the mask solves model input semantics.

> **•** Prefer capability-homogeneous series: only combine rows where the target/covariate is measured consistently.
>
> **•** Where gaps remain, make the treatment target-specific and documented; never silently encode unknown as zero.
>
> **•** A forecast run records the missingness policy and excludes unsupported series configurations.

## 4.3 The next task is often an exogenous disturbance

Software work is not retail demand. A future OAuth migration, CSS tweak or database redesign can arrive because of planning rather than the prior telemetry trajectory. Candidate/task information may therefore be essential covariate context. Forecasting claims must distinguish persistence in factory/repository state from variation introduced by the workload sequence.

## 4.4 Prediction changes the future once acted upon

If a Telltale warning causes Kstrl to add review, retry, tests or human approval, subsequent outcomes are generated under a different policy. Telltale records a policy.intervention observation describing the action and segments evaluation around policy changes. Post-intervention data cannot be naively compared with the pre-intervention observational regime.

# 5. Requirements

## 5.1 Functional

> **1.** Ingest Claude Code and Codex telemetry without modifying the target repository.
>
> **2.** Capture provider/runtime/model identity and a first-class execution-environment fingerprint.
>
> **3.** Preserve sanitized provider facts as immutable observations with coverage and provenance.
>
> **4.** Reconstruct logical activities deterministically without mutating the observation history.
>
> **5.** Correlate agent work with Git state, diff evolution, commits and explicit orchestrator identities.
>
> **6.** Derive neutral software-work measures: verification cycles, edit turnover, exploration scope, stable-state work, context/token burden, compactions and delegation.
>
> **7.** Represent unknown as unknown; expose capability coverage alongside every affected measure.
>
> **8.** Compile capability-aware numerical series on request, attempt and accepted-change logical clocks.
>
> **9.** Run repeated-task, configuration-sensitivity, repository-intervention and chronology-placebo experiments.
>
> **10.** Run TimesFM-3 behind an optional forecasting boundary and persist reproducible input/output/backtest metadata.
>
> **11.** Keep outcome families separate and allow Kstrl/external systems to attach them without defining a universal label.
>
> **12.** Expose claim_class and assumptions through CLI/API outputs.
>
> **13.** Record forecast-driven policy interventions when advisory signals influence an orchestrator.
>
> **14.** Operate usefully with forecasting completely disabled.

## 5.2 Non-functional

> **•** Fail open: telemetry failure never blocks the coding agent or Kstrl correctness path.
>
> **•** Low perturbation: observation overhead is measured using repeated scripted tasks, not assumed.
>
> **•** Local first and private by default: prompts, assistant text, source/edit bodies, raw reasoning, environment values and command output are not ordinarily persisted.
>
> **•** Replayable: observations are the durable source; activities, semantics, series and forecasts are versioned derived data.
>
> **•** Version tolerant: provider schema drift lowers coverage and raises diagnostics rather than silently changing semantics.
>
> **•** Inference safe: query/UI code cannot present a stronger claim class than its source allows.

# 6. Architecture

<img src="docs/spec/media/image1.png" style="width:6.8in;height:7.1553in" alt="Telltale architecture showing provider capture, immutable observations, reconstructed activities, semantic reducers, experiments, series compilation, TimesFM forecasting and advisory outputs." />

Figure 1. Current-state architecture. Measurement and inference are separate layers; claim class constrains every user-facing interpretation.

Telltale is one local process plus provider integrations. TimesFM/PyTorch are optional and are never imported by the always-on collector unless forecasting is explicitly enabled.

## 6.1 Core components

| **Component** | **Responsibility** |
|----|----|
| telltaled | Loopback OTLP receiver; lifecycle HTTP endpoints; validation/sanitization; immutable observation writer; repository/environment observers; reducer scheduling; local query API. |
| Provider adapters | Provider-specific parsing and capability declarations. Provider semantics stop at the observation boundary. |
| Repository observer | Repo/worktree identity, HEAD/base, dirty-state/diff fingerprints, changed paths, additions/deletions, commits. No patch content by default. |
| Environment observer | Stable fingerprint of model/runtime/effort/tool/instruction/Kstrl/config posture without storing sensitive instruction content. |
| Activity reducer | Correlates multiple observations into rebuildable tool/model/edit/test/compaction/subagent activities with field-level provenance. |
| Semantic reducers | Neutral work measures and session summaries; no hidden latent difficulty score. |
| Experiment lab | Repeated-task, configuration sensitivity, controlled probe/intervention and chronology-placebo orchestration/evaluation. |
| SeriesCompiler | Builds explicit-clock, capability-aware numerical series and target/covariate schemas. |
| Forecast module | Optional TimesFM-3 adapter, deterministic baselines, rolling-origin backtester, ForecastStore. |
| Claim-aware API/CLI | Exposes facts, comparisons, associations and forecasts with evidence class, cohort, coverage and assumptions. |

## 6.2 Why local ingestion remains justified

Repository correlation and privacy sanitization are workstation-local concerns. If an existing OTel pipeline can eventually guarantee both while preserving the software-work semantics, Telltale should integrate rather than duplicate commodity collection. The durable value is the reconstruction, experimental discipline and series semantics, not owning an OTLP receiver.

# 7. Capture strategy

<img src="docs/spec/media/image2.png" style="width:5.85in;height:2.48261in" alt="Telltale capture path showing OTel, structured CLI JSON, lifecycle hooks and local observers flowing through sanitization into immutable observations and asynchronous reducers." />

Figure 2. Capture path. Safe provider facts are appended before any interpretation or correlation occurs.

## 7.1 Capture modes

> **•** Native OpenTelemetry: default for interactive sessions. Captures provider-supported usage/tool/compaction/session facts with low integration friction.
>
> **•** Structured CLI stream: highest-fidelity path for wrapped sessions and Kstrl; preserves machine events before display projection.
>
> **•** Lifecycle HTTP hooks: enrichment only where a lifecycle fact is absent or clearer than OTel/stream data. Avoid per-tool hook dependence when ordinary tool operations are already observable.
>
> **•** Transcript import: backfill/diagnostics only, never the long-term contract.

## 7.2 Capture precedence

No single surface is authoritative for every field. Telltale appends each safe observation separately, correlates by stable provider IDs where possible, and chooses/merges fields only in rebuildable activity projections. Conflicting observed values remain visible as diagnostics.

# 8. Provider notes

## 8.1 Claude Code

The intended Level 1 interactive setup enables Claude Code telemetry and tool details while leaving user-prompt and tool-content logging disabled. Tool details can carry sensitive input fragments, so sanitization must happen before persistence \[R4\]. Structured stream-json remains the preferred wrapped/Kstrl source for tool chronology; OTel can run in parallel for request-level usage details.

CLAUDE_CODE_ENABLE_TELEMETRY=1

OTEL_LOGS_EXPORTER=otlp

OTEL_METRICS_EXPORTER=otlp

OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf

OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:\<port\>

OTEL_LOG_TOOL_DETAILS=1 \# Level 1 only

OTEL_LOG_USER_PROMPTS unset

OTEL_LOG_TOOL_CONTENT unset

Context/token handling is conservative: compaction pre/post token facts are used directly when available; request-level token components are stored by type; a context occupancy ratio is only computed when a trustworthy denominator is available and records denominator_source. A provider/model upgrade is a changepoint, not just another categorical value.

## 8.2 Codex

Codex support follows fixture-first measurement. \`codex exec --json\` and app-server surfaces are treated as versioned provider contracts only after real sanitized fixtures establish their current shape \[R6\]\[R7\]\[R8\]. Reasoning content, if exposed by a provider stream, is excluded from ordinary persistence.

# 9. Capability and execution-environment model

## 9.1 Capability record

{

"provider": "claude",

"runtime_version": "...",

"adapter": "claude_otel",

"content_level": 1,

"capabilities": {

"request_usage": "observed",

"context_window": "unavailable",

"compaction": "observed",

"tool_calls": "observed",

"file_paths": "observed",

"commands": "observed",

"subagents": "partial"

}

}

Coverage states distinguish observed, unavailable, partial and derived. “0 compactions” and “compaction unobservable” are different statements.

## 9.2 ExecutionEnvironment fingerprint

Every capture references a content-safe environment fingerprint so longitudinal changes are not casually attributed to the repository. The fingerprint stores values or hashes, never secret/prompt content.

| **Field** | **Treatment** |
|----|----|
| Provider, runtime and exact model identifier where emitted | Observed; model aliases are not assumed stable across time. |
| Reasoning/effort setting | Observed from Telltale/Kstrl launch config or provider metadata where available. |
| Tool set and MCP posture | Names/capability hashes; sensitive server configuration excluded. |
| Instruction surfaces | Hash + token/byte size of AGENTS.md/CLAUDE.md/system/feedforward inputs; content excluded. |
| Kstrl version/config | Version and stable configuration hash; feedforward context size recorded separately. |
| Sandbox/network posture | Normalized mode/capability flags. |
| Capture mode/capability set | Part of fingerprint because observability itself changes available measures. |

# 10. Observation and activity model

## 10.1 Immutable ProviderObservation

{

"schema_version": 1,

"observation_id": "01J...",

"observation_type": "claude.otel.tool_result",

"provider_ts": "...",

"ingest_ts": "...",

"provider": "claude",

"runtime_version": "...",

"adapter": "claude_otel",

"capture_id": "...",

"provider_session_id": "...",

"environment_fingerprint_id": "env\_...",

"provider_correlation_ids": {"tool_use_id": "..."},

"repo_id": "...",

"payload": {},

"redaction": {},

"confidence": "observed"

}

ProviderObservation is the event-sourcing boundary. Late evidence always appends another observation; history is not rewritten.

## 10.2 Canonical Activity

{

"activity_id": "act\_...",

"activity_type": "verification_run",

"capture_id": "...",

"actor": "agent",

"started_at": "...",

"ended_at": "...",

"fields": {"category":"test", "exit_code":0, "scope":"targeted"},

"provenance": {"exit_code":\["obs\_..."\], "scope":\["obs\_..."\]},

"claim_class": "derived"

}

Activities are materialized/rebuildable logical operations. They may overlap; parallel tools and subagents are not forced into a false total order.

## 10.3 Identity and logical clocks

> **•** capture_id: one Telltale-observed runtime process/invocation.
>
> **•** provider_session_id: opaque provider identifier, preserved exactly.
>
> **•** conversation_lineage_id: derived relation across resumes/continuations when supported, with provenance.
>
> **•** request_index: provider request/turn ordering inside a capture/lineage.
>
> **•** attempt_index: orchestrator attempts at one component/task, including rejected/aborted attempts.
>
> **•** accepted_change_index: repository state transitions that become part of the tracked base.

Factory health cannot be inferred from accepted changes alone: Kstrl may successfully filter an increasingly difficult stream of failed attempts. Attempt and accepted-change series are therefore analysed together.

# 11. Privacy and content model

## 11.1 Content levels

| **Level** | **Persisted** | **Excluded** |
|----|----|----|
| 0 - minimal | Provider/runtime/model, usage quantities, tool names, success/duration, compaction, anonymous IDs. | Prompts, responses, commands, file paths, tool arguments, source content. |
| 1 - engineering metadata (default) | Repo-relative paths, normalized command executable/selected safe args, diff statistics/hashes, verification classification, error categories. | File/edit bodies, command output, prompt/assistant text, raw reasoning, environment values, secrets. |
| 2 - diagnostic opt-in | Selected bounded provider inputs/outputs for adapter debugging, short retention. | Never required for core semantics or forecasting thesis. |

## 11.2 Sanitization before persistence

> **•** Allowlist safe provider fields instead of trying to blacklist every future sensitive field.
>
> **•** Repository-relative paths; remove usernames/home roots; optional path-segment hashing.
>
> **•** Never persist environment variables; redact obvious secrets/high-entropy arguments; bound every string/payload.
>
> **•** Tool-specific sanitizers drop Edit/Write content fields before the observation writer.
>
> **•** Privacy fixtures seed known fake secrets/source strings and assert they never reach Level 0/1 storage.

# 12. Repository correlation

## 12.1 Repository identity

At capture start: canonical root, HEAD, branch, base SHA, worktree identity, remote fingerprint, dirty-tree hash and environment fingerprint. Absolute local path is not the portable repository identity.

## 12.2 Diff snapshots

File mutation observations trigger debounced Git snapshots outside hooks. Store changed paths, additions/deletions, rename metadata, staged/unstaged state, whole-diff hash and optional per-file patch hashes. No patch body by default.

## 12.3 Commit linkage

Confidence ranking: explicit orchestrator correlation; provider-reported commit ID; matching tree/parent during capture; matching tree shortly after capture; heuristic inference last. Temporal proximity alone is insufficient.

## 12.4 Overlapping sessions

worktree_id is distinct from repo_id. Two autonomous sessions mutating one worktree concurrently make attribution ambiguous unless a provider/orchestrator gives explicit ownership. Telltale marks ambiguity rather than inventing attribution.

# 13. Neutral semantic reduction

Reducers describe observable work. Names are intentionally neutral so the schema does not smuggle conclusions into later analysis.

## 13.1 Verification cycles

Classify commands deterministically into test, typecheck, lint, format, build, benchmark, security scan, package operation, Git, process management, generic shell or unknown. A verification activity records actor, scope, exit status, duration, repository state and whether later edits superseded it.

> **•** Derived facts: fail-to-pass cycles; edits after last successful test; edit epochs with/without verification; last verification repository hash; targeted vs full scope where knowable.

## 13.2 Edit turnover

Track files touched over time, repeated edits, maximum versus final diff size, additions later removed, reversions to earlier diff fingerprints and post-failure revisits. “High turnover” is descriptive until experiments establish what it means.

## 13.3 Exploration scope

Unique files/areas read before first edit and overall, search/glob operations, directories traversed, read-to-edit ratio and explored-area-to-final-area ratio. Wide exploration may be prudent or costly; no valence is attached.

## 13.4 Context/token burden

Store request-granularity fresh input, cache-read, cache-creation and output quantities separately, plus compaction pre/post facts. Only compute an occupancy ratio when a denominator is trustworthy. Do not compare raw token levels across environment fingerprints without cohorting.

## 13.5 Stable-state work intervals

A maximal interval where diff fingerprint and verification signature remain unchanged while model/tool activity continues. Store duration, token traffic, operations, eventual mutation and eventual verification outcome. It is not called “no progress” because reading, diagnosis and planning can be productive without modifying the tree.

## 13.6 Delegation and subagents

Track parent-child relationships and direct versus delegated work. Avoid double counting provider token summaries; aggregate only quantities whose semantics are verified for the provider/version.

## 13.7 Session evidence vector

The default output is a vector, not a composite score. Raw values are always available; cohort percentiles appear only when the comparison cohort is sufficiently large and environment/task comparability is stated.

context_token_burden raw + cohort percentile

edit_turnover raw + cohort percentile

verification_cycles raw + cohort percentile

exploration_scope raw + cohort percentile

stable_state_work raw + cohort percentile

compactions raw count + coverage

# 14. Experimental design and validity

<img src="docs/spec/media/image3.png" style="width:6.5in;height:2.49705in" alt="Validity experiment design showing repeated-task variance, configuration sensitivity, repository interventions and chronology placebo tests bounding allowed conclusions." />

Figure 3. Validity experiments bound what can be inferred from agent-work telemetry before repository or forecasting claims are made.

## 14.1 Repeated-task stochastic variance

Purpose: establish how noisy each measure is when software, task and environment are nominally unchanged.

> **•** Choose reproducible tasks with deterministic acceptance criteria and disposable branches/worktrees.
>
> **•** Run repeated independent agent sessions against the same base commit and environment fingerprint.
>
> **•** Report robust within-condition distributions. A metric with very high within-condition spread is not used as a fine-grained repository comparator.

## 14.2 Environment sensitivity

Purpose: quantify how much the measurement instrument itself moves the signal. Repeat selected tasks while varying one factor such as model version, reasoning effort, instruction surface, tool set or Kstrl feedforward context. Treat material shifts as environment effects/changepoints, not repository drift.

## 14.3 Controlled repository interventions and probes

Purpose: obtain stronger evidence that repository structure affects agent work. Fixed read-only probes are executed on selected repository versions; where practical, the same functional task template is run before and after a deliberately scoped refactor. The environment fingerprint is held constant or differences are explicit.

> **•** Example probes: locate the implementation behind an interface; identify tests covering a module; trace a public API to persistence; identify callers of a changed contract.
>
> **•** Measure correctness of the probe answer as well as work quantities. Cheap behaviour with a wrong answer is not an improvement.

## 14.4 Temporal placebo

Purpose: test whether chronology itself matters. For each forecast target, compare rolling-origin performance on true order with matched chronology-destroying controls (for example block shuffles that preserve local feature distributions but break long-run order).

If TimesFM retains essentially the same advantage after chronology is destroyed, the system may be exploiting contemporaneous covariates rather than temporal evolution. Telltale then labels the result conditional prediction, not evolution forecasting.

## 14.5 Task/workload conditioning

Natural histories mix bug fixes, migrations, refactors, UI work and cross-cutting features. Telltale stores orchestrator-provided task metadata where available and avoids model-generated semantic labels in the critical path. Comparable cohorts should prefer repeated templates or explicit Kstrl component metadata over inferred task categories.

## 14.6 Policy feedback experiments

Forecasts remain shadow-only during primary evaluation. When a policy begins acting on them, Telltale writes a policy.intervention observation containing the advisory ID, action taken and policy version. Subsequent performance is evaluated as a new regime.

# 15. Forecasting architecture

<img src="docs/spec/media/image4.png" style="width:5.7in;height:7.0339in" alt="Forecasting architecture showing logical clocks and neutral measures compiled into capability-homogeneous series, forecastability preflight, A B C ablations, TimesFM-3 evaluation, candidate conditioning and shadow advisory." />

Figure 4. TimesFM-3 is downstream of forecastability checks, capability-aware series construction and deterministic baselines.

## 15.1 TimesFM-3 boundary

TimesFM-3 is the initial zero-shot multivariate forecasting engine because it supports multiple target variates, past-only covariates, past-and-future covariates and point plus quantile forecasts \[R1\]\[R2\]. \`telltale\[forecast\]\` isolates PyTorch/model dependencies from the collector. The Forecaster interface exists for reproducibility and future substitution, not because the current research use is blocked by licensing.

## 15.2 SeriesCompiler

The compiler creates schema-versioned numerical frames on explicit logical clocks. Each series records target/covariate units, observation coverage, environment/changepoint metadata, row provenance and missingness treatment.

> **•** request_index: within-capture/lineage dynamics such as token burden, compaction and stable-state work.
>
> **•** attempt_index: convergence across Kstrl retries, including rejected/aborted attempts.
>
> **•** accepted_change_index: repository/base transitions that actually landed.

Calendar aggregates are secondary operational views, not substitutes for these clocks.

## 15.3 Forecastability preflight

Before calling TimesFM for a target, the forecast lab verifies: enough historical windows exist for rolling-origin evaluation; the target is measured consistently for the selected cohort; deterministic baselines are defined; missingness treatment is valid; environment changepoints are represented; and a chronology placebo can be constructed.

## 15.4 Missingness strategy

Telltale’s internal masks are provenance, not a promise that TimesFM consumes arbitrary missing-value masks. The adapter preferentially builds capability-homogeneous frames. Unsupported gaps cause a series/window to be excluded or use an explicitly documented target-specific preprocessing policy. Every ForecastRun records the policy.

## 15.5 Targets

Forecast directly observed/derived quantities with units, not latent scores: token/context burden, compaction count, verification cycles, stable-state work, edit turnover, Kstrl attempts/retries, time to verified terminal state, build/CI duration, rework events and other well-defined observables. Different targets may legitimately conclude that a deterministic baseline is sufficient.

## 15.6 A/B/C ablation

| **Variant** | **Inputs** | **Question** |
|----|----|----|
| A | Repository/change state only | Is there forecastable structure without agent telemetry? |
| B | A + raw agent state (token/context/compaction/model/environment) | Does agent state add information? |
| C | B + software-work semantics (verification, edit turnover, exploration, stable-state work) | Do reconstructed behavioural semantics add temporal information? |

## 15.7 Evaluation

> **•** Rolling-origin future windows only; never random train/test splits.
>
> **•** Baselines: persistence/last value, rolling median/mean, local deterministic drift, seasonal naive only when a genuine season exists.
>
> **•** Metrics: point error; quantile/interval calibration; weighted quantile score where appropriate; threshold-crossing warning lead time.
>
> **•** Temporal-placebo performance is reported beside the true-order result.
>
> **•** A target remains TimesFM-backed only when incremental value is repeatable rather than cherry-picked.

## 15.8 Candidate-conditioned forecasting

The first defensible candidate experiment is one-step ahead: candidate features such as touched subsystem counts, diff size, dependency delta and test delta are truly known for that next change. TimesFM-3 past-and-future covariates require covariate values across the forecast horizon \[R2\]; Telltale must not invent later changes.

Longer-horizon candidate runs are explicitly scenario forecasts. A scenario declares assumptions for future workload/covariates (for example, only candidate features at step 1 and a named workload profile thereafter). The output reports those assumptions prominently.

The delta between conditioned and unconditioned forecasts is not the causal impact of the candidate. Only one future is observed; ordinary backtesting can assess conditional prediction quality but cannot validate the forecast delta as a treatment effect.

## 15.9 ForecastStore

ForecastRun {

forecast_run_id,

forecaster/checkpoint,

series_schema + reducer versions,

environment/cohort definition,

exact context rows + horizon,

target/covariates,

missingness_policy,

chronology_placebo_definition,

future_workload_scenario?,

point + quantiles,

baseline outputs,

evaluation results,

claim_class: "predictive"

}

# 16. Repository work profiles

Natural-history aggregation is useful descriptively but is not called maintainability. For each path/subsystem, Telltale reports cohort-qualified observed distributions and sample composition. Controlled probes/interventions provide the stronger evidence needed for claims about repository structure.

| **Natural-history statement** | **Allowed?** |
|----|----|
| “Sessions touching payments used 2.1x the matched-cohort median fresh token burden (n=31; same model/runtime family).” | Yes - comparative, with cohort. |
| “payments has maintainability 0.17.” | No - false precision and latent score. |
| “After refactor R, fixed probe P required fewer files and tokens across repeated matched runs.” | Yes - narrow controlled comparison. |
| “Refactor R caused all future coding tasks to become easier.” | No - overgeneralizes intervention evidence. |

# 17. Storage, API and CLI

## 17.1 SQLite first

SQLite/WAL remains appropriate for a local single-user research tool. Core tables: observations; environment_fingerprints; captures/lineages; activities; repositories/repo_snapshots; changes/attempts; outcomes; derived_measures; experiments/experiment_runs; reducer_checkpoints; series_snapshots; forecast_runs/forecast_values/backtest_results; policy_interventions; adapter_diagnostics.

Observations are immutable. Everything else can be rebuilt/versioned. Parquet export is an interchange/research format, not the write path.

## 17.2 Query/API output contract

Every material query result includes claim_class, coverage, cohort/environment definition where applicable, provenance/reducer version and warnings. APIs do not expose a \`quality_score\` or \`difficulty_score\`.

## 17.3 CLI

telltale doctor

telltale setup claude \| codex

telltale sessions

telltale show \<capture\>

telltale timeline \<capture\>

telltale compare \<a\> \<b\>

telltale experiment repeat \<task-spec\>

telltale experiment environment \<task-spec\>

telltale experiment probe \<probe-spec\> --revisions \<a\> \<b\>

telltale series build --clock request\|attempt\|change

telltale forecast --target \<metric\> --horizon \<n\>

telltale forecast backtest --target \<metric\>

telltale forecast placebo --target \<metric\>

telltale forecast ablate --target \<metric\>

telltale forecast candidate \<change.json\> \[--scenario \<scenario.json\>\]

telltale export --format parquet

telltale purge

No general dashboard first. Early interfaces prioritize timeline reconstruction, experiment comparison and forecast/baseline diagnostics.

# 18. Kstrl integration

<img src="docs/spec/media/image5.png" style="width:6.6in;height:3.79287in" alt="Kstrl integration showing identities and outcomes flowing to Telltale, TimesFM advisory flowing to Kstrl policy, and policy interventions fed back as regime-changing observations." />

Figure 5. Kstrl supplies identities and independent evidence; Telltale supplies observation, research and advisory. Any policy intervention is fed back as data because it changes the regime.

## 18.1 Integration contract

Kstrl supplies explicit run/component/task/attempt/change identity and outcome events. Telltale does not read \`.kstrl/\` as its primary contract, and Kstrl does not embed provider-specific Telltale schemas.

POST /v1/correlations

{

"external_system":"kstrl",

"external_run_id":"factory-...",

"component_id":"payments-api",

"task_id":"...",

"attempt":3,

"provider_session_id":"..."

}

POST /v1/outcomes

{

"kind":"mechanical_verification",

"status":"failed",

"categories":\["typecheck:arg-type"\],

"timestamp":"..."

}

## 18.2 Attempt and accepted-change views

Kstrl can become more selective while the underlying work becomes harder. Telltale therefore keeps rejected attempts, budget halts and retries in an attempt-clock series while separately tracking accepted changes. Neither is a substitute for the other.

## 18.3 Advisory integration

Forecasts initially run in shadow mode and do not change Kstrl control flow. If later enabled as an advisory input, Kstrl remains responsible for policy. Any action attributable to a Telltale advisory is emitted back as a policy.intervention observation so evaluation knows the policy changed.

# 19. Security, reliability and perturbation

## 19.1 Security

> **•** Provider tool inputs and repository names are untrusted; stored strings are never executed.
>
> **•** Loopback/socket only; filesystem permissions protect state; no outbound exporter unless explicitly configured.
>
> **•** Hooks are one-way and non-instructive; Telltale does not write derived analysis into agent context unless a separate explicit integration is enabled.

## 19.2 Reliability

> **•** Critical path: receive -\> sanitize -\> append. Reducers, Git snapshots, experiments and forecasts are asynchronous.
>
> **•** Adapter parse failure increments diagnostics; reducer failure replays from checkpoints; forecast failure never affects capture.
>
> **•** Under pressure, drop optional high-volume observations before lifecycle/tool-completion facts and count every drop.

## 19.3 Perturbation

Observation can alter the system being measured. Perturbation tests compare repeated scripted tasks with/without telemetry, report collector/hook overhead and inspect gross behavioural shifts. Native provider telemetry and direct HTTP lifecycle hooks are preferred over per-tool process spawning.

# 20. Testing strategy

## 20.1 Fixture and compatibility tests

> **•** Sanitized real provider fixtures for session lifecycle, reads/edits, commands, failures, tests, compaction, subagents, permission denial, model switch/reroute, resume and timeout.
>
> **•** Golden normalization/activity/session summaries per provider/runtime version.
>
> **•** Live compatibility smoke tests separate from fast CI.

## 20.2 Privacy tests

Seed known fake secrets/source strings into prompts, edit bodies, command args, environment and file names. Level 0/1 stores must not contain prohibited values. Tool-specific sanitizer coverage is versioned and fail-closed for unknown content-bearing fields where practical.

## 20.3 Experimental-validity tests

> **•** Repeated-task harness produces independently seeded captures against identical repository/task/environment.
>
> **•** Environment experiment changes only declared factors and records fingerprints.
>
> **•** Probe correctness is scored independently of work quantity.
>
> **•** Chronology-placebo generator preserves selected marginal/block structure while destroying the temporal property under test.

## 20.4 Forecast tests

> **•** Golden SeriesCompiler outputs for request/attempt/change clocks.
>
> **•** No look-ahead in covariates, baseline calculations or rolling-origin windows.
>
> **•** Missingness policy is explicit and reproducible; unsupported windows fail rather than silently impute.
>
> **•** Real TimesFM tests are separate from fast CI; a small stub tests integration contracts.
>
> **•** Backtest report always includes deterministic baselines and chronology placebo where applicable.

# 21. Incremental project plan

Versions are research gates. Failure of one hypothesis disables only the claims/layers that depend on it.

## v0.0 - capture feasibility and epistemic substrate

> **•** Claude Code fixtures in OTel Level 0/1 and structured-stream modes; minimal collector and sanitizer.
>
> **•** ProviderObservation, capability record, claim_class primitives and EnvironmentFingerprint schema.
>
> **•** Repository identity/diff observer spike and privacy/perturbation tests.

Exit: high-value tool/file/command/session/usage/compaction facts are observable without unsafe content retention or model traffic interception.

## v0.1 - flight recorder, experiment harness and TimesFM smoke path

> **•** SQLite store; activity reconstruction; session timeline; neutral semantic primitives.
>
> **•** Repeated-task experiment runner and environment-fingerprint comparison.
>
> **•** request_index SeriesCompiler and optional \`telltale\[forecast\]\` TimesFM-3 end-to-end smoke run on fixture/synthetic numerical series.
>
> **•** Codex fixture capture before adapter implementation.

Exit: diagnose a non-trivial session from Telltale alone; reproduce activities from observations; repeated-task harness exposes within-condition variance; TimesFM interface works without predictive claims.

## v0.2 - semantic measures, stochastic bounds and request-clock Forecast Lab

> **•** Verification cycles, edit turnover, exploration scope, context/token burden, stable-state work, compaction/subagent semantics.
>
> **•** Repeated-task distributions for key measures; environment-sensitivity experiments on selected tasks.
>
> **•** Real request-clock TimesFM backtests where sequence length allows; deterministic baselines and missingness policies.

Exit: H1/H2/H3 are characterized. Metrics that are too unstable are not promoted to cross-session/repository comparisons.

## v0.3 - Kstrl correlation, attempt/change clocks and temporal validity

> **•** Kstrl run/component/task/attempt/change correlation and separate outcome families.
>
> **•** Attempt and accepted-change SeriesCompiler; survivorship-aware views.
>
> **•** Rolling-origin TimesFM backtests, A/B/C ablations and chronology-placebo tests.

Exit: for each target, state whether chronology matters, whether TimesFM beats deterministic baselines, and whether agent/process semantics add incremental temporal value. No requirement that the answer be yes.

## v0.4 - controlled repository interaction research

> **•** Fixed read-only legibility probes with correctness scoring and repeated runs.
>
> **•** Before/after selected repository interventions under matched environment fingerprints.
>
> **•** Natural-history repository work profiles with strict task/environment cohorting.

Exit: determine which repository claims are supportable and which remain workload analytics. Do not create a universal maintainability score.

## v0.5 - candidate-conditioned one-step advisory

> **•** Candidate feature extractor limited to facts known before merge.
>
> **•** One-step unconditional vs candidate-conditioned TimesFM evaluation.
>
> **•** Shadow-mode Kstrl advisory; no control-flow changes; forecast delta explicitly associative.

Exit: candidate conditioning must improve calibrated near-term prediction or warning usefulness on held-out future changes. Otherwise keep it a research command.

## v0.6 - scenario horizons, policy experiments and hardening

> **•** Longer-horizon candidate scenarios only with explicit future-workload assumptions.
>
> **•** If advisory begins influencing Kstrl, emit policy.intervention events and segment evaluation by regime.
>
> **•** Compatibility matrix, richer Codex/app-server coverage, exporter/plugin SDK, optional local web UI, performance/storage hardening.

# 22. Architecture decision records

| **ID** | **Decision** | **Rationale** |
|----|----|----|
| ADR-001 | Local-first single process | One daemon, SQLite and provider adapters until measurement shows a need for distributed infrastructure. |
| ADR-002 | Two-layer event sourcing | Immutable ProviderObservations are durable; Activities/semantics/series/forecasts are rebuildable. |
| ADR-003 | Native structured telemetry first | OTel and structured CLI carry ordinary facts; lifecycle HTTP hooks only fill specific gaps. |
| ADR-004 | No raw content by default | Prompts, assistant text, source/edit bodies, raw reasoning, environment and command output are excluded from normal persistence. |
| ADR-005 | Epistemic contract is data | Every derived/comparative/predictive output carries claim_class and may not be presented as stronger evidence. |
| ADR-006 | Neutral semantic names | Schema stores edit turnover, stable-state work and context/token burden rather than difficulty/confusion/no-progress labels. |
| ADR-007 | Execution environment is first-class | Model/runtime/instructions/tools/Kstrl/config/capture posture form a fingerprint and changepoint boundary. |
| ADR-008 | No composite difficulty/quality score | Preserve vectors and separate outcomes; no hidden latent scalar. |
| ADR-009 | TimesFM-3 is active but optional | Forecasting starts early to shape data granularity but can be killed independently of capture/semantics. |
| ADR-010 | No supervised good/bad PR model | Use direct statistics, controlled experiments, deterministic baselines and zero-shot TimesFM research. |
| ADR-011 | Forecastability must be demonstrated | Chronology placebo is required before calling change-index results temporal/evolution forecasting. |
| ADR-012 | Capability-homogeneous series preferred | Internal masks do not justify arbitrary missing-value mixing; no silent zero imputation. |
| ADR-013 | Attempt and accepted-change clocks are separate | Avoid survivorship bias when Kstrl filters difficult attempts before merge. |
| ADR-014 | Candidate conditioning is one-step first | Longer horizons require explicit future-workload scenarios; forecast deltas remain associative. |
| ADR-015 | Advisory begins in shadow mode | Forecast-driven interventions are logged because they change the data-generating policy. |
| ADR-016 | Kstrl remains external | Identity/outcomes cross a stable API; correctness sensors remain Kstrl-side and authoritative for policy. |

# 23. Open questions

> **1.** Which exact Claude/Codex token quantities are comparable enough to use as burden measures across runtime versions?
>
> **2.** Can a trustworthy effective context-window denominator be obtained for each provider/model path, or should ratios remain unavailable for some cohorts?
>
> **3.** What minimum normalized command representation preserves useful verification classification without retaining sensitive arguments?
>
> **4.** How should tool/MCP capability changes be represented in environment fingerprints without exploding cohort cardinality?
>
> **5.** What is the minimum repeated-task sample needed to estimate useful within-condition variance for each measure?
>
> **6.** Which fixed probes are stable, meaningful and cheap enough to run repeatedly across repository revisions?
>
> **7.** How should task metadata be normalized without introducing another LLM classifier?
>
> **8.** Which outcome windows provide credible revert/follow-up attribution without overclaiming correctness?
>
> **9.** Which chronology-placebo transformations best preserve workload distribution while destroying the temporal property under test?
>
> **10.** For each TimesFM target, what history length/horizon is sufficient for meaningful rolling-origin evaluation?
>
> **11.** What is the correct missingness treatment for a given target when TimesFM does not directly consume Telltale’s arbitrary internal mask?
>
> **12.** Do model/runtime upgrades reset a series, become changepoints, or require parallel cohort series?
>
> **13.** How wide can a multivariate series become before sparse or heterogeneous variates degrade TimesFM more than they help?
>
> **14.** For longer-horizon candidate scenarios, what future-workload assumptions are meaningful enough to expose rather than hide?
>
> **15.** How long must shadow-mode evaluation run before any Kstrl policy intervention is scientifically interpretable?

# Appendix A. Provider capability snapshot

| **Capability** | **Claude Code** | **Codex** | **Treatment** |
|----|----|----|----|
| Session lifecycle | OTel/hooks/stream | JSON/app-server/OTel/hooks | Capture + opaque provider IDs; Telltale owns capture_id. |
| Model identity | API request/model events | turn/app-server/OTel | Environment fingerprint; alias changes are changepoints. |
| Per-request/turn tokens | Strong request-level components | Depends on surface; app-server richer | Store components by semantic type; capability-specific granularity. |
| Context window | May be unavailable on chosen events path | app-server can expose modelContextWindow | No ratio without trustworthy denominator. |
| Compaction | OTel compaction + hooks | app-server/hooks where verified | Observed compaction activity; no negative interpretation. |
| Tool calls | OTel/hooks/stream-json | exec JSON/app-server/hooks/OTel | Correlate by provider IDs where possible. |
| File paths / commands | Tool details or structured stream | file/command items | Sanitize before persistence; repo-relative. |
| Tests | Derived from commands | Derived from commands | Deterministic classifier + actor/scope/outcome. |
| Subagents | Provider events/hooks | app-server/hooks where verified | Parent-child graph; avoid double counting. |
| Raw reasoning | Not needed | May appear on some surfaces | Never ordinary persistence; outside thesis. |

# Appendix B. Session summary contract

{

"capture_id": "...",

"provider_session_id": "...",

"conversation_lineage_id": "...",

"repo_id": "...",

"environment_fingerprint_id": "env\_...",

"coverage": {

"context_window": "unavailable",

"compaction": "observed",

"tool_calls": "observed",

"file_paths": "observed",

"commands": "observed"

},

"usage": {

"model_requests": 14,

"fresh_input_tokens": 38120,

"cache_read_tokens": 122441,

"output_tokens": 8610

},

"work": {

"unique_files_read": 27,

"unique_files_changed": 6,

"file_revisits": 19,

"max_diff_lines": 911,

"final_diff_lines": 344,

"stable_state_work_intervals": 3

},

"verification": {

"agent_test_runs": 5,

"failed_test_runs": 2,

"edits_after_last_successful_test": 0

},

"context": {

"compactions": 1,

"pre_compaction_tokens": 181200,

"post_compaction_tokens": 72100,

"occupancy_ratio": null,

"denominator_source": null

},

"claim_class": "derived",

"forecast_readiness": {

"request_clock": true,

"attempt_clock": false,

"change_clock": false

}

}

# Appendix C. TimesFM-3 forecasting protocol

TimesFM-3 is used as an experiment in zero-shot multivariate forecasting, not assumed to fit this domain. The public v3 example accepts target arrays, past-only covariates and past-and-future covariates and returns joint point/quantile forecasts \[R2\]. Telltale’s research protocol adds domain-specific validity tests around that interface.

> **•** Interface: \`Forecaster.prepare(series_schema, model_config)\` and \`forecast(series_context, horizon, future_covariates=None)\` -\> ForecastResult.
>
> **•** ForecastResult includes model/checkpoint, target/covariate schema, missingness policy, environment/cohort definition, point forecast, quantiles, warnings and claim_class=predictive.
>
> **•** Backtester uses the same interface as interactive forecasts so evaluation cannot silently diverge from the displayed path.
>
> **•** Each target has a forecastability report: history/windows, coverage, baseline performance, true-order performance, chronology-placebo performance and calibration.
>
> **•** If true-order TimesFM does not beat deterministic baselines, remove model backing for that target. If it only matches shuffled-order performance, do not claim temporal evolution.
>
> **•** Candidate conditioning is one-step by default. Multi-step candidate scenarios must name the future-workload assumptions used to populate horizon covariates.

# Appendix D. References

| **Ref** | **Source** | **URL** |
|----|----|----|
| \[R1\] | Google Research, “TimesFM-3: A zero-shot foundation model for multivariate forecasting,” 31 Aug 2026. | https://research.google/blog/timesfm-3-a-zero-shot-foundation-model-for-multivariate-forecasting/ |
| \[R2\] | Google Research TimesFM repository, v3 README/API examples and checkpoint notice, accessed 1 Sep 2026. | https://github.com/google-research/timesfm |
| \[R3\] | Anthropic, Claude Code hooks reference. | https://code.claude.com/docs/en/hooks |
| \[R4\] | Anthropic, Claude Code monitoring / OpenTelemetry. | https://code.claude.com/docs/en/monitoring-usage |
| \[R5\] | Anthropic, Claude Code CLI reference. | https://code.claude.com/docs/en/cli-usage |
| \[R6\] | OpenAI, Codex non-interactive mode. | https://developers.openai.com/codex/noninteractive |
| \[R7\] | OpenAI, Codex app server. | https://developers.openai.com/codex/app-server |
| \[R8\] | OpenAI Codex app-server protocol, thread token usage schema. | https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/json/v2/ThreadTokenUsageUpdatedNotification.json |
| \[R9\] | OpenTelemetry, Generative AI semantic conventions. | https://github.com/open-telemetry/semantic-conventions-genai |
| \[R10\] | Kstrl repository. | https://github.com/0xfauzi/kstrl |
