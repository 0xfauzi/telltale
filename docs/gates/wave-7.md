# Wave 7 gate: every session feeds the laboratory with every column

Status: tasks merged; the post-wave census is being measured. Written by the orchestrator
as each task merged. Wave 7 is not a spec version gate: it is the repair wave the owner
asked for on 2026-09-05 ("fix the Codex and Claude transcripts situation") after E13's
census measured that every imported Claude transcript failed readiness check 1 on two
columns and every Codex capture built to a request clock of 0 rows.

## Dispatch record

| Task | Attempt | Dispatched | Outcome |
|---|---|---|---|
| W7-T1 Codex request clock from rollout token_count | 1 (Sonnet, killed by process group when the owner corrected the Opus-limit premise), 2 (Opus) | 2026-09-05 23:45 London | the session ended on "You've hit your session limit" after writing its report with the whole change set staged and uncommitted; the orchestrator committed it with the brief's message, ran the gate set (381 passed) and re-ran the VERIFY line below; merged #64 |
| W7-T2 backfill environment fingerprints | 1 (Sonnet, killed), 2 (Opus) | 2026-09-05 23:45 London | PR #62 opened by the session; gate set green before and after merging main (374 and 385 passed); merged #62 |
| W7-T3 derived request duration and coverage-named variants | 1 (Sonnet, killed), 2 (Opus) | 2026-09-05 23:45 London | PR #63 opened by the session; merge with main conflicted on three Codex goldens and the docs index; goldens regenerated under the merged code with TELLTALE_GOLDEN=write (46 tests pass), gate set green (390 passed); merged #63 |

Merge order T1, T2, T3, each re-gated on main after the previous merge. The docs index's
"N reports" line conflicts on every merge and is set to `ls docs/log | grep -c ^W` (59).

## Incident: two implementers stopped on the account's session limit

W7-T1's session (168 turns, 21.7 USD) and, on 2026-09-06, both wave 8 sessions ended with
the plain-text assistant message "You've hit your session limit · resets 12:40pm
(Europe/London)" and exit 0: no error record, no 529. W7-T1 had everything staged and its
report written; the orchestrator committed. The wave 8 sessions had written nothing yet.
Mechanism added: `resume-until-done.sh` in the scratchpad resumes a stopped session with
`dispatch.py --resume <session id>`, parses the reset time out of the last message, sleeps
until it and resumes again; the owner's standing instruction is to wait for the reset and
retry. A resumed session keeps its context and its worktree.

## Merged

| PR | Task | What landed |
|---|---|---|
| #64 | W7-T1 | One `model_request` activity per rollout `event_msg/token_count` when no SSE response exists (fresh = input minus cached, `usage_source` `codex.rollout.event_msg.token_count.last_token_usage`), the SSE pairing rule with a conflict diagnostic, OTel request duration from websocket_request to response.completed (`duration_source` `otel:websocket_request->sse_event`), the rollout duration rule measured and rejected (kept None), CAPABILITIES request_usage on the rollout surface `observed` and a `request_duration` row, two file splits under the 800-line ratchet (activities_codex_requests.py, providers/codex_drift.py), Codex goldens regenerated, a new rollout-import golden. |
| #62 | W7-T2 | `env.backfill_fingerprint` (reads no disk; instruction_hashes empty), an importer pre-pass yielding the (version, model, effort) regime per line, one `telltale.environment` per distinct regime emitted before the lines it governs, every observation stamped with its fingerprint id, `fingerprints` count on capture_started; `<synthetic>` in message.model is not a model. |
| #63 | W7-T3 | Transcript-derived request duration (`duration_source` `transcript_timestamps`), the `request_duration` capability row on the Claude provider, the series column mapped to it, readiness check 1 amended (a column that is exactly `unavailable` is excluded by name and recorded on the run as `excluded`; `partial` still refuses), pooling requires equal `covariates`, E14 (the measurement: 6,518 requests over 128 sessions, last-line rule 99.7 percent within 10 percent of OTel), design amendment folded. |

## Measurements (verified by the orchestrator on main after the three merges)

- W7-T1's VERIFY on a copy of E13's store: the largest imported Codex capture
  (imp_869276e15eb90a31ac51856f) holds 5,736 `codex.rollout.event_msg.token_count`
  observations; after `telltale rebuild` its request clock builds 5,735 rows with
  fresh_input_tokens, cache_read_tokens and output_tokens `observed` and
  request_duration_ms `unavailable` (the rollout rule failed its own bar), one `dropped`
  diagnostic for the token_count record that carries rate limits and no usage. Before
  wave 7 the same capture built 0 rows.
- The Codex S1 golden's coverage now says `request_duration: derived` (OTel bracket) and
  the rollout-import golden `unavailable`; under the amended check 1 the rollout-import
  golden lost its "coverage: measured 8, needed 11" refusal lines.
- Gate set wall on this machine: 221 s (T2 on main) and 203 s (T3 on main), pytest at
  385 and 390 tests.
- A full `telltale rebuild` of a fresh copy of the owner's store (3,474 captures,
  1,183,633 observations) under the wave 7 reducers: 119 s.
- Source files still on disk for the imports on that copy: Codex 1,459 of 1,459; Claude
  1,772 of 1,841 (69 transcripts have been deleted from ~/.claude/projects since they
  were imported). A purge-and-re-import, which is the only way an existing import gains a
  fingerprint (W7-T2's report), must therefore skip those 69, which keep `env_changed`
  unavailable.

## Carried from the reports

- W7-T1: `activities_codex_requests.py` was added to `correlate._RULE_MODULES` and one
  series test rewritten (both outside OWNS, both forced); `request_duration_ms` on a
  Codex OTel capture is labelled by the `request_duration` row now that W7-T3 landed
  (golden: `derived`).
- W7-T2: `cohorts.cohort_keys` docstring is stale (an import now has a content level and
  a model; it still has no runtime, so still no cohort); an import reads its file three
  times (2.7 s for a 64 MB re-import, whole tree not re-measured); existing imports keep
  `env_changed` unavailable until purged and re-imported.
- W7-T3: the derivation is measured on headless launcher sessions and applied to
  interactive imports (median derived duration 12,036 ms on the one import checked
  against 4,684 ms on the launcher cohort; what would measure the gap is a launcher
  capture of an interactive session); `placebo.restored` does not carry `excluded`
  through (one line, in a file nobody in wave 7 owned); series.py and series_lineage.py
  sit at exactly 800 lines; E13's pool does not yet key on `covariates`.

## Census after wave 7

(filled from experiments/E16/out/census.json when the run finishes)
