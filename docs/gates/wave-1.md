# Wave 1 gate: v0.1 flight recorder, experiment harness, TimesFM smoke

Date: 2026-09-02. Orchestrator: Claude Fable. Spec gate: v0.1 (spec section 21).

## What was merged

| Task | PR | Delivered | Tests after merge |
|---|---|---|---|
| W1-T1 launcher, daemon, setup | #9 (+ fix on main) | `telltale run`, `daemon`, `setup --print`, session map, repo and environment observations per capture | 40 |
| W1-E03 TimesFM-3 smoke | #10 | measured install, per-call cost, NaN, covariates, CPU against MPS | 40 |
| W1-T2 activities, timeline, show, explain | #12 | activities.py, correlate.py, measures.py (summary skeleton), report.py, goldens for S1, S4, S7 | 55 |
| W1-T3 Codex provider | #11 | providers/codex.py, allowlist_codex.py, activities_codex.py, Codex goldens, Codex privacy cases | 85 |
| W1-T4 repeat runner, fake agent, purge, runtime_version | #13 | experiments.py, fake_agent.py, `experiment repeat`, `purge`, versions.json cache, doctor.py split | 61 |
| W1-T5 request-clock series | #14 | series.py, schema.py, store readers, `series build`, `series check`, `series list`, synthetic writer | 65 |
| W1-T6 forecast core | #15 | forecast/{__init__,baselines,backtest,timesfm}.py, `forecast backtest`, contract tests 1 and 2 | 76 |

Main after the six merges: 101 integration tests, every gate green, CI green.

## Exit criteria (spec 21, v0.1) and what was measured

### 1. Diagnose a non-trivial session from Telltale alone

The session: W1-T5 attempt 2, the implementer that wrote the request-clock series
compiler, resumed after the session limit and run through `telltale run` (capture
`cap_01M1GPSMW1ZRXVADWZPF0KZ9H3`, 651 observations, coverage 4/4 surfaces).

Diagnosis from `show`, `timeline` and `explain` only: an Opus session of 32 model
requests over 7 minutes 27 seconds that read no files with the Read tool, wrote two files
(tests/integration/test_series.py and docs/log/W1-T5.md), ran 29 shell commands of which
6 were verification runs (4 pytest, 2 ruff), all reporting success, spent 64 fresh input
tokens against 6,570,582 cache-read tokens and 27,598 output tokens, had no compaction
(coverage observed, so that is a measured zero), took four repository snapshots, and
ended by pushing a branch. The timeline reads: state check, one ruff run, one telltale
command, a test file write, tests, gates, the report write, push.

Then the raw stream-json of the same session, which Telltale never reads, was counted
independently:

| fact | Telltale | raw stream | agree |
|---|---|---|---|
| model requests | 32 | num_turns 32 | yes |
| fresh input tokens | 64 | 64 | yes |
| cache read tokens | 6,570,582 | 6,570,582 | yes |
| output tokens | 27,598 | 27,598 | yes |
| Bash calls | 29 (23 command + 6 verification_run) | 29 | yes |
| file writes | 2 | 2 (Write) | yes |
| files read with Read | 0 | 0 | yes |
| pytest runs | 4 (agent_test_runs) | 4 | yes |
| failed tool results | 0 | 0 | yes |
| subagents | 5 | 0 (no Task or Agent tool use) | NO |
| commits linked | 0 | 1 commit and 1 push during the capture | NO |

The two disagreements are defects, both measured to their cause:

- **Five false subagents.** Claude Code 2.1.258 emits `system/task_started` for every
  Bash tool call (`task_type: local_bash`, `is_backgrounded: false`). E01 measured on
  2.1.257 that `SubagentStart.agent_id` equals `task_started.task_id` for a real subagent,
  and the reducer took every task_started as a subagent start. The fix is to accept
  task_started only when its task_type is not local_bash (a subagent is `local_agent`),
  and to count subagents from SubagentStart hooks and parent_tool_use_id first.
  Assigned to W2-T1 (measures), which owns the summary these numbers feed.
- **The commit did not link.** The capture's own snapshots show HEAD moving from
  e2aa6a95 at capture start to 3512c938 at capture end with a clean tree, and no
  `telltale.repo.commit` observation was written. Two rungs of the ladder were both out
  of reach: `provider_reported` needs `git_commit_id` from OTel tool details, and no
  tool_result on 2.1.258 carried one (payload keys: command, duration_ms, success,
  tool_name, tool_use_id, sizes, file_path); `tree_match` needs a snapshot whose
  (head, diff_hash) equals the commit's (parent, patch), and the last file-mutation
  snapshot predates the ruff fixes and the tests the agent changed through Bash. A
  snapshot at capture end whose head IS the commit and whose diff is empty is the
  working tree at that commit, which is a tree match by definition; `_tree_match` does
  not accept it. Assigned to wave 2 as its own task (W2-T6, commit linkage and 2.1.258
  drift), because repo.py is at the 800-line ratchet and the change has to move code
  out first.

Neither defect is a false number presented confidently in the cross-check above beyond
the two rows marked NO: subagent_count is derived with coverage observed, so it claims to
be a count, and it is wrong. That is the class of error the design exists to prevent, and
it was caught by the exit criterion working as intended.

Other observations from the same capture:

- 84 `unknown_field` diagnostics, all 2.1.258 drift: `cost_usd_micros`, `organization_id`,
  `user_account_id`, `user_account_uuid`, `user_email`, `user_id` on api_request; a new
  `hook_execution_complete` event; `assistant_response` now carries model, query_source
  and request_id; `Stop` hooks carry `background_tasks` and `session_crons`. Fail closed
  worked: none reached the store. Allowlist additions for the non-identity fields go
  with W2-T6; the identity fields stay dropped.
- 32 `conflict` diagnostics: every request has an output_tokens disagreement between the
  OTel api_request (primary, kept) and the stream's per-message assistant usage
  (secondary), for example 103 against 17. A turn produces several assistant messages and
  the stream reports each message's own tokens, so the comparison is between different
  things. W2-T1 sums stream usage per request before comparing, or stops comparing.
- A commit command with a multi-line message normalizes through the shlex fallback to a
  single token, so no stored command contains "git commit" and the classifier cannot see
  a commit. Recorded for W2-T6.
- Lifecycle rows from hooks print no time (they carry the arrival clock). Readable, and
  honest, but the two capture_start rows at the top of every timeline are noise.

### 2. Activities reproduce from observations

`rebuild` twice on the replayed S1 fixture gives byte-identical activity rows (hash
32d61c1f1fee56d4 both times, W1-T2 verification); the home store's six build captures
rebuilt under three successive reducer versions (W1-T2, W1-T3, and the merged main)
without a diagnostic. Goldens for Claude S1, S4, S7 and Codex S1, S3, S6 are checked by
`test_capture_to_summary.py` on every run.

### 3. The repeated-task harness exposes within-condition variance

`telltale experiment repeat` with the fake agent, 5 repetitions on a temporary
repository, run by the orchestrator on the PR head:

```
experiment W1-T4 task W1-T4-verify2: 5 captures, environment env_8f96c6e0...
acceptance: {'pass': 5, 'fail': 0}
METRIC                       CLAIM_CLASS  N  UNKNOWN  MEDIAN  MAD_SCALED  IQR   MIN   MAX    VALUES
input_tokens                 comparative  5  0        3915    911.799     615   1245  8595   1245,3915,3915,4530,8595
num_turns                    comparative  5  0        5       1.483       1     4     6      4,5,5,6,6
output_tokens                comparative  5  0        320     115.643     85    242   405    242,320,320,405,405
tool_calls.Read              comparative  5  0        4       0           1     2     4      2,3,4,4,4
```

Attempts 1 to 5 are correlated to the task id through `/v1/correlations`, each
repetition's acceptance outcome is an `external.outcome` observation, all five
fingerprints are one id, the worktrees are removed afterwards, a `--fail` repetition
records status fail, and `telltale purge` removed one capture (22 observations) and left
the others. The variance shown is the fake agent's seeded variation, which proves the
harness and says nothing about any real agent: real within-condition variance is E05,
owner-gated.

An orchestrator error worth recording: the first verification run reported 5 failures
because the orchestrator's acceptance command compared the file to `'42'` without
stripping the newline the agent writes. The harness reported exactly what happened; the
spec was wrong, not the runner.

### 4. The TimesFM interface works without predictive claims

E03 (docs/experiments/E03.md): TimesFM 3.0.0 installs on Python 3.12 with torch 2.13.0;
one predict_batch call is 0.35 s median on CPU (worst 0.81 s); MPS agrees to 9.1e-7
relative and is about 2x faster per call; NaN is interpolated, trimmed, forward-filled or
zero-filled silently, which is why the adapter refuses it. W1-T6 (pending) puts the
adapter behind the Forecaster signature with the four baselines and the backtester.

### W1-T6 forecast core (merged as PR #15 after this report was drafted)

Verified by the orchestrator on the branch: gates green, 76 integration tests in the
default environment with torch absent (`import torch` fails; `import telltale.forecast`
succeeds because the adapter is registered lazily), then the synthetic 200-row
request-clock series (`tests/integration/synthetic_series.py --rows 200 --seed 1`)
backtested with the five in-tree forecasters:

```
constants: delta 0.1  w 0.6  k_min 20  c_min 32  horizon 1  stride 1  baseline_window 8  threshold_rule q80_first_c_min
tau 1606.4 (q80_first_c_min)  rows 200  windows 136 retained, 32 dropped (regime_too_short)
FORECASTER      N_WINDOWS  MAE_MEAN  MAE_MEDIAN  SKILL    CAL_MAX_DEV  COVERAGE80  WQS     LEAD_HIT_RATE  FALSE_ALARM_RATE  MEDIAN_LEAD  CLAIM_CLASS
echo            136        90.4338   75.0        0.0      0.4          0.0074      0.0914  0.0            0.0               -            derived
local_drift     136        101.1866  80.875      -0.1189  0.0574       0.7647      0.0816  1.0            0.0435            0            derived
persistence     136        90.4338   74.5        0.0      0.0324       0.7794      0.0742  1.0            0.0435            0            derived
rolling_mean    136        166.648   126.1875    -0.8428  0.0765       0.7132      0.1317  0.0            0.0652            -            derived
rolling_median  136        168.7721  132.25      -0.8662  0.0691       0.6985      0.1358  0.0            0.0652            -            derived
```

With `--forecasters persistence,timesfm` under the forecast extra: TimesFM-3 MAE mean
92.7228 against persistence 90.4338, skill -0.0253, on CPU (62 s wall for the command,
which included the 1.3 GB checkpoint download) and the same to four decimals on MPS
(11 s wall). The license line printed and stored in the run: weights
google/timesfm-3.0-pytorch under timesfm-non-commercial-license-v1.0, device, and
padding_mode edge. No forecaster beats persistence on a seeded random walk, which is the
expected answer and the reason the baselines are in the table: the interface works, and
it makes no predictive claim about anything real. The exit criterion "TimesFM interface
works without predictive claims" is met.

Two things the implementer flagged that the gate carries forward: the lead-time
denominators (hit rate = hits / (hits + misses), false-alarm rate = false alarms /
(false alarms + quiet), both over windows with y_{o-1} < tau) are the implementer's
definitions, printed in every run's assumptions, and a reviewer could prefer eligible
windows as the denominator for both; and a run that produced zero windows is still
written (the attempt is recorded, with the constants and the reason). No real capture is
long enough to backtest yet (the longest has 32 requests against c_min 32), so every
number above is synthetic by construction.


## Exit verdict (spec 21 v0.1)

Met, on all four criteria above: a session diagnosed from Telltale alone agrees with
its raw stream on every counted fact (criterion 1); two rebuilds are byte-identical
under three reducer versions (2); the repeat runner exposes within-condition variance
on the fake agent and refuses differing fingerprints, with the real floors deferred to
E05 (3); the TimesFM-3 adapter runs behind the Forecaster signature with no predictive
claim, E03 and W1-T6 (#15) (4). Added on 2026-09-04 at the wave 6 gate, because W6-T5
found no single verdict sentence here to cite; it restates the findings above and adds
none.

## Claims that are now supportable

- A launcher capture of a Claude Code or Codex session yields observed request usage,
  tool calls, file paths, commands, exit codes (derived for Claude), compaction (Claude:
  observed; Codex: unavailable) and a context-window denominator (Codex: observed from the
  rollout; Claude: from the stream result), each with its coverage word.
- A session summary and timeline reproduce from observations and walk down to provider
  bytes through `explain`.
- Repeated captures of one task under one fingerprint produce comparative statistics
  within the condition, and the harness refuses when fingerprints differ.
- The request-clock series is built with no look-ahead by construction and `series
  check` catches a violated row.

Not supportable yet: any number about a real agent's variance (E05), any environment
effect (E06), any forecast (W1-T6 lands the mechanism; E07 runs it on data), subagent
counts on 2.1.258 (defect above), commit linkage on real sessions (defect above).

## The build recorded itself

| capture | task | attempt | duration | observations | note |
|---|---|---|---|---|---|
| cap_01M1FV8DKMH5MG0XD08JZEVA76 | W1-T4 | 1 | 30 s | 197 | killed by the orchestrator (tool timeout) |
| cap_01M1FVAAWQNWBBG9G318YE6WVM | W1-T4 | 2 | 27 min | 2596 | ended by the session limit after 120 turns |
| cap_01M1FW3DA7M6YK37QM4KAY308H | W1-T5 | 1 | 10.8 min | 1334 | ended by the session limit after 65 turns |
| cap_01M1GPSMV4JTBGZXBCMVZXJK2T | W1-T4 | 3 | 7.3 min | 900 | resumed; delivered PR #13 |
| cap_01M1GPSMW1ZRXVADWZPF0KZ9H3 | W1-T5 | 2 | 7.5 min | 651 | resumed; delivered PR #14 |
| cap_01M1GQSRAC1APK7ZM8S8G3YJTA | W1-T6 | 1 | 28.5 min | 98 turns | delivered PR #15 |

W1-T2 and W1-T3 ran through the Agent tool (dispatched before the launcher existed) and
are not captured. Three attempts above were ended by the harness, not by the task: the
subscription's session limit, and one orchestrator kill. The attempt series of wave 3
must carry that distinction, and it is recorded here so that it can.

The session limit ends a headless session with a result of subtype success, is_error
true and the text "You've hit your session limit"; the capture looks like a success
unless is_error is read. W2-T1 reads it.

## Design amendments folded

docs/design/01-design.md now carries "Amendments from wave 1" and docs/design/
02-protocol.md 7.2 carries the detached dispatch command that works on macOS (no
setsid) and the resume protocol.

## Decisions taken by the orchestrator (routine)

- The TimesFM adapter defaults to CPU and takes MPS as an opt-in: the CPU cost model is
  the measured one; MPS agreement was measured on one input.
- cli.py was split at the ratchet (doctor.py) by the orchestrator rather than by a
  re-dispatched implementer, with the full gate set as the check.
- Harness-caused retries are attempts in the store and are labelled in this report,
  not purged.

## E04, run after the owner's approval (2026-09-02)

Approved by the owner on 2026-09-02 ("Approve E04, run the 10 sessions") and merged as
PR #16. Decision file: docs/experiments/E04.md. Raw numbers: experiments/E04/out/
results.json (overhead), out/floor.json (wrapper floor), out/load/results.json (load).
Every number below is copied from those files.

Rule (1), overhead: five interleaved pairs of the E01 S2 prompt (sonnet, explore-only),
telemetry on against telemetry off, whole-process wall time.

| quantity | value | source |
|---|---|---|
| OFF wall median | 9.584 s | results.json wall_off_s |
| ON wall median | 11.230 s | results.json wall_on_s |
| delta median, ON minus OFF | 0.986 s | results.json delta_s |
| delta scaled MAD | 1.5256 s | results.json delta_s |
| OFF arm scaled MAD | 0.7265 s | results.json off_arm_mad_scaled_s |
| delta of the child's own clock, median | 891 ms | results.json delta_api_ms |
| minimal detectable difference at n = 5 | 1.2865 s | results.json mdd_s |
| pairs needed to resolve 0.986 s | 9 | results.json n_needed_for_measured_delta |
| wrapper floor with no agent, median | 0.3507 s | floor.json, n = 10 per arm |

The pre-registered rule compares the median delta with the OFF arm's scaled MAD, and
0.986 s exceeds 0.7265 s, so the rule's "exceeds run-to-run noise at n = 5" branch is
the one taken. Design 6.12's own yardstick is stricter: the minimal detectable
difference at n = 5 is 1.2865 s, larger than the measured delta, so a difference of
this size is not resolvable at five pairs. The write-up states both, carries no
percentage, and one pair (pair 4) was negative. A comparative claim about capture
overhead needs 9 pairs at this spread and does not exist yet.

Rule (2), fail-open under load: 1555 POSTs carrying 155,500 records were sent to the
receiver of a fake-agent capture. All 1555 were answered 200; the slowest took 186 ms
against the 300 ms bound; the agent's exit code and its 5636 stdout bytes were
unchanged; the flooded capture stored the same 17 stream observations as the quiet
one. No fail-open violation. The drop path was never exercised: the queue peaked at
580 of 10,000 and drops_total was 0, because the store accepted about 7,800
observations a second. That is a measured absence, and a load that outruns the writer
is the next load experiment; it spends no tokens.

Rule (3), completeness: all four surfaces (hook, otel_logs, otel_metrics, stream)
delivered on all five ON captures.

Housekeeping after the merge: the three flooded load captures (369,569 synthetic
observations, 475 MB in the home store) were purged with `telltale purge` and the file
vacuumed from 479 MB to 24 MB. Their capture ids in the E04 write-up no longer resolve
in the store; the numbers stay in out/.

## Owner decisions requested

1. **E04: approved and run.** The result is in the section above; it asks for no
   further decision.
2. **Carried from wave 0, still open:** the owner's `~/.codex/hooks.json` does not parse
   under Codex 0.150.1 (the praxis Codex hooks are silently dead; Telltale did not touch
   it); the GitHub social preview image (docs/assets/logo-512.png) is uploaded by hand in
   the repository settings.
3. **Nothing else.** Wave 2 dispatch (measures, backfill importers with the dry-run report,
   environment runner, compare and cohorts, readiness, commit linkage) is automatic per
   the plan; the backfill's real import waits for the dry-run counts.
