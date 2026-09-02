# Wave 2 gate report: measurement semantics and stochastic bounds (spec v0.2)

Date: 2026-09-02. Orchestrator: Claude Fable (this session). Main at the time of
writing: 49198a3, after the E07 merge. Every number here was produced by
running the system on this machine on the day; where a number is a projection it says
so and names the measurement it is projected from.

## What was merged

| PR | task | what it delivers | tests on its branch |
|---|---|---|---|
| #16 | W1-E04 | perturbation and fail-open under load (owner-approved sessions; folded into the wave 1 report) | - |
| #17 | W2-T1 | every spec 13 measure as Evidence; the Appendix B summary; the false-subagent defect from the wave 1 gate fixed | 106 |
| #18 | W2-T2 | `telltale import claude-transcripts` and `codex-rollouts`, idempotent, dry run, backfill column | 123 |
| #19 | W2-T5 | `forecast readiness`: the eight checks with measured and needed numbers; readiness in the summary | 134 |
| #20 | W2-T3 | `experiment environment`: arms that differ in one launch flag, fingerprint assertions, HL shift, Cliff's delta, exact Mann-Whitney, MDD, N_needed | 132 |
| #21 | W2-T6 | commits made inside a capture link through the ladder without provider help; 2.1.258 drift fields; rebuild clears stale conflict rows; a capture reduces itself at capture end | 148 |
| #22 | W2-T7 | `verification_seen` and `last_verification_failed` replace `last_verification_exit` on the request clock | 149 |
| #23 | W2-T4 | `telltale vector` (spec 13.7) and `telltale compare`; cohorts of n >= 10 gate every percentile | 164 |
| #24 | W2-E07 | request-clock backtests with TimesFM-3 and the baselines on the build's own captures | - |

Orchestrator commits on main between them: the split of cli.py into cli.py,
cli_forecast.py and cli_common.py (791 lines against the 800 ratchet, before three
tasks added subcommands), the E04 section of the wave 1 report, and the briefs.
Every PR was verified by re-running its brief's VERIFY lines in a separate worktree,
merged with main first where main had moved, before the squash merge.

## Exit criteria (spec 21, v0.2) and what was measured

### 1. Measures reproduce from activities

Two full rebuilds of a copy of the home store (35 captures) produced 3082 activities
and 1400 evidence rows both times, and a SHA-1 over every activity's fields and
provenance and every evidence row's value, coverage, source and warnings was identical
(4f5db833bf5527ba, twice). W2-T1's summary is a pure function of the capture, with
`created_at` taken from the capture's last arrival rather than the clock so the second
rebuild cannot differ by a timestamp.

### 2. H2 variance floors for the pilot task

Known for the scripted agent, not for a real one. `experiment repeat` (W1-T4) and
`experiment environment` (W2-T3) run the whole protocol of design 6.12 on fake-agent
captures: per-condition median, scaled MAD, IQR, min, max and the sorted values, and
between arms the Hodges-Lehmann shift, Cliff's delta, the exact Mann-Whitney p by
enumeration (checked by hand: 3 against 3 fully separated gives p = 0.1, 5 against 5
gives 2/252), MDD = 2.8 s sqrt(2/n) and N_needed. A spec whose arms differ in two flags
is refused naming the extra token. The real floors need E05, which starts agent
sessions and is an owner decision below.

### 3. H3 factor effects stated as resolved or not resolved at n

Same status: the mechanism is proven on the scripted agent (effort low against high, 5
per arm: `cache_read_input_tokens` shows complete separation, delta 1 and p 0.008, and
is still "not resolved at n" because its shift of 9000 is below the 9282 the spread
allows at n = 5, which is the demotion rule doing its job). E06 is the owner decision.

### 4. Forecast readiness computed on real data

Yes, and it found a defect first. The readiness command run over the nine longest
captured build sessions (65 to 175 model requests) returned `windows 0 of 20` for every
target: the request-clock column `last_verification_exit` was None until each
session's first test run (106 of 175 rows on the longest), and the exclude policy,
which is right to refuse unknowns, dropped every window that read row 0, which is all
of them. "No test has run yet" is a state the capture observed, not an unknown; W2-T7
replaced the column by two 0/1 columns carrying the same three states. After it, all
nine captures retain every planned origin (143 on the longest) and all nine are ready
for `output_tokens` at H = 1. `fresh_input_tokens` and `tool_calls_since_prev` fail the
variation check on Claude sessions with scaled MAD exactly 0: eight of ten requests
carry at most 2 fresh input tokens (the cache pattern of Claude Code) and most turns
make one tool call. That is a fact about the targets on this provider, and E07 reports
it rather than working around it.

## E07: request-clock backtests on the build's own captures

Merged as PR #24 (docs/experiments/E07.md; experiments/E07/out/summary.json and one JSON
per run; the copied store is not committed). The first run of the laboratory on real
data, over the 15 build captures on disk at 17:15 UTC, with TimesFM-3 on CPU and the
four baselines plus the stub, at H = 1 and H = 4, under the pre-registered constants.
No token was spent and the home store gained no series or forecast rows.

| target | ready at H = 1 | ready at H = 4 | first failing check | backtests | label |
|---|---|---|---|---|---|
| output_tokens | 12 of 15 | 5 of 15 | windows, on the three short captures | 17 | baseline sufficient, 17 of 17 |
| fresh_input_tokens | 0 | 0 | variation: scaled MAD exactly 0 on all 15 | 0 | not assessable |
| tool_calls_since_prev | 0 | 0 | variation: scaled MAD 0 on 14 of 15 | 0 | not assessable |

The two pooled rows (every window of the cohort concatenated) are baseline sufficient
too: at H = 1, 1,017 windows, TimesFM-3 mean MAE 817.20 against the best baseline's
803.44 and W_MB 0.5152; at H = 4, 157 windows, 777.58 against 809.78 and W_MB 0.5605.
In all 19 labels the rule terminated on its first clause (E_M > 0.9 E_B or W_MB < 0.6),
so the placebo half of the rule, which W3-T2 builds, was never reached; the best
TimesFM-3 skill anywhere is 0.0844 against the 0.10 bar, and rolling_median is the
best baseline in every run. What this supports: on this cohort (build sessions of one
agent on one repository, 2.1.258, opus), the request-clock output_tokens target is
forecastable and a rolling median of the last 8 requests is as good a one-step
forecast as the model; the other two targets on Claude sessions barely vary from
request to request and cannot be assessed. What it does not support: any claim about
another provider, task or cohort, and nothing about calibration, which the write-up
reads as a judgement (TimesFM-3's calibration flag is clear in 10 of 12 H = 1 runs
where every baseline's is raised, but part of that gap is an artefact: 20.5 percent of
local_drift's and 6.1 percent of rolling_median's quantile points are below zero on a
nonnegative target; no rule was pre-registered, so nothing is decided). Open items
the write-up names: 4 of the 15 captures carry no runtime_version (they predate the
version cache), the checkpoint load time of this run was not recorded, and the
`--device mps` path is wired and untested.

## Defects found by running the system this wave

1. Subagent false positives in the summary (from the wave 1 gate's diagnosis): fixed
   by W2-T1; the build capture that showed 6 subagents shows 0.
2. Commits made during a capture did not link without a provider-reported id: W2-T6
   added the tree-match rungs read from the capture's own snapshots and two new
   snapshot triggers (a Bash result whose normalized command commits, and 2.1.258's
   `vcs_state_changed` stream message). A commit made by a bash child inside a
   capture links at `tree_match_after` with its sha equal to HEAD.
3. The launcher never ran the reducers: `telltale show` on a fresh capture printed a
   summary of nulls with an empty coverage map, indistinguishable from "nothing
   observed", until somebody ran `telltale rebuild`. Found by the orchestrator while
   probing a fake-agent capture; W2-T6 made the capture reduce itself at capture end
   (median 271.98 ms with against 277.99 ms without, five fake-agent runs each: not
   measurable at that size; the largest capture rebuilds in about 0.15 s) and made the
   three reading commands refuse, naming the rebuild command, when a capture has no
   activities.
4. Stale conflict diagnostics: a rebuild left the previous run's conflict rows in
   place (32 on the W1-T5 build capture) and the old reducer would have doubled them.
   W2-T6's rebuild deletes the capture's conflict rows first (32 before, 0 after two
   rebuilds).
5. The verification-column encoding above (W2-T7).
6. Two report sentences were wrong when written and are corrected here: W2-T5's
   "the longest capture on this disk is 32 requests" (nine were above 65 at that
   moment), and the W2-T7 brief's request counts for two captures (the store was live
   and two sessions were still writing).

## Carried items, not fixed this wave

- `rebuild` deletes every conflict row of the capture, including the importer's
  collision rows and the receiver's session-rebinding rows, which are ingest facts.
  The brief's premise that only the reducer writes that kind was wrong. A marker-based
  deletion belongs to W6-T3 once every capture has been rebuilt once under the new
  rule.
- `claude.otel.metric` still drops `service_version` and `terminal_type` as unknown
  fields (746 rows on the home store): one allowlist line, W6-T4.
- Codex `request_duration_ms` is labelled observed with every cell empty (W2-T7): a
  coverage mislabel in the Codex activities mapping, for the next task that owns it.
- `telltale vector` scales with the database, not the capture: 0.31 s on a 36-capture
  store, 225 ms of it the cohort scan over every observation. The reader it needs
  (`store.observations(capture_id, types=...)`) is a store.py change for wave 3; it
  matters once the backfill lands (projection below).
- File-length ratchet: launch.py 790, allowlist.py 798, cli.py 733. The next task
  touching any of them moves code out first, as W2-T6 did for repo.py.

## The build recorded itself

Fourteen build sessions were captured through the launcher by the time of the copy
used above (the E07 session was still running): 1338 model requests, 1,164,718 output
tokens and 255,796,030 cache-read tokens in total, from 5 requests (a W1-T4 attempt
the session limit killed) to 189 (W2-T6). Every number in the readiness and
backtest sections came from these captures, which is the data source the owner chose
on 2026-09-01: no session was started for an experiment this wave except E04's ten.

## Backfill import (owner-approved; run 2026-09-02)

The real import of ~/.claude/projects and ~/.codex/sessions, after the dry run reported
at the wave 1 gate. Every number is from scratchpad/backfill/import.log and the store.

| Measurement | Value |
|---|---|
| Imported captures | 3152 (1698 Claude transcripts, 1454 Codex rollouts) |
| Observations written | 1,002,786 |
| Database size after import | 1.41 GB, plus an 18 MB WAL |
| Wall time | Claude 104.98 s, Codex 113.21 s (17:36:00Z to 17:39:38Z) |
| Second import of the Claude tree | 1 new session (started during the first pass), 1698 already stored, 116 unreadable |
| Full home-store rebuild | 3190 captures in 183 s: 344,180 activities, 127,800 evidence rows |
| `telltale vector` on the enlarged store | 11.94 s for one capture |

What the numbers mean:

- The import is idempotent: the capture id is derived from the session id, so the second
  pass added exactly the one session that did not exist during the first.
- The 1802 diagnostics of kind `dropped` written by the importer are its count of
  transcript line kinds it does not parse (attachment, last-prompt, queue-operation,
  atis-latch). They are not queue drops; the receiver's queue was never involved. A
  later brief should give the importer its own diagnostics wording so the two cannot be
  confused.
- The 116 unreadable files are counted, not listed; the per-file reason needs listing
  before anything is said about them.
- `telltale vector` reads every observation of the store to find one capture's rows
  (11.94 s at 1.04 million observations). The store reader needs an index-backed path per
  observation type. Carried to wave 3.
- A session captured by the launcher now also appears as an imported transcript capture
  of the same session, so the store holds two captures per build session. Nothing links
  them yet. The cohort key content_level keeps them in different cohorts, because an
  imported capture has no environment fingerprint and so no content level.

## Privacy defect found by the byte scan of the imported store

A scan of the database bytes for the E01/E02 privacy probes (`TELLTALEFAKE` and
`sk-ant-api03-`) after the import found 10 observation rows carrying a probe. Every one
is a COMMAND field (a shell command string) on the stream, hook, OTel tool_decision or
transcript surfaces, in launcher captures and imported captures alike. Diagnostics and
redaction columns have zero hits.

Mechanism: the command normalizer keeps every token that begins with `-` verbatim (design
6.4's "every token starting with - with any =value stripped") with no shape check, so a
quoted argument such as `"--- sk-ant not TELLTALEFAKE ---"` or a heredoc line beginning
`-----BEGIN TELLTALEFAKE PRIVATE KEY-----` reaches the store as one "flag". The private
key header also passed the secret scrub, which means either the scrub never sees the
normalized command or its pattern missed; W2-T8 measures which.

Why the privacy test did not catch it: the fixture sessions never quoted a string
beginning with a dash inside a command. The probes were planted in file contents, and
file contents never reach the store. The test was right about what it tested and silent
about this shape.

Remedy, in flight as W2-T8: a token beginning with `-` survives only when flag-shaped
(`^--?[A-Za-z0-9][A-Za-z0-9_.:+-]{0,31}$`); the scrub runs on every normalized command;
`telltale resanitize` re-normalizes every stored command field whose normalization
version is older than the current one, in place, as the one sanctioned UPDATE of the
observations table, guarded by a pre-commit rule like the INSERT rule, and recorded as a
diagnostics row and in the row's redaction list. Purging the ten captures was rejected:
three are the build's own launcher captures with hundreds of requests each, and the same
shape can exist in any command that quoted a dash-leading argument. After the merge the
orchestrator runs resanitize on the home store; the acceptance number is 0 probe hits in
the database and WAL bytes.

Done. W2-T8 merged as #26 after verification (168 integration tests; with the flag rule
widened back to "anything beginning with a dash", the two new privacy tests fail, so
they test the fix). The remediation on the home store, run from main at 2fb193b with a
`.backup` copy taken first (~/.telltale/backup/telltale-before-resanitize-2026-09-02.db,
1.43 GB, which still holds the probe bytes and is the only undo):

| Measurement | Value |
|---|---|
| Probe hits in db plus WAL bytes before | 16 |
| Captures rewritten | 1154 of 3190 |
| Fields rewritten | 21,786 (the ten leaks, and every dash run or multi-word "flag" the old rule kept) |
| Wall time, including the rebuild of each rewritten capture | 70 s |
| Probe hits after | 0 |
| Second run | 0 captures, 0 fields |
| Diagnostics rows written | 1154, one per rewritten capture, naming the version it came from |

Open items from the T8 report, carried: a rewritten cmdnorm-v1 row is relabelled v3
although the v1 form lost the `=` of environment assignments (9 of 1154 captures); a
short dash-leading quoted argument with no spaces is flag-shaped and survives, as the
test asserts, and the scrub stands behind it; `resanitize` and `purge` both rebuild
through `Store.reducers`, which is empty when store.py is imported alone (the CLI imports
measures, so the command is safe; a library caller is not); stale bytes in freed pages
were 0 in practice and `PRAGMA secure_delete` was not turned on.

## E05: the H2 pilot ran five sessions and measured the harness, not the task

The owner approved five `claude -p --model sonnet` sessions on the E01 fix-the-test task.
All five ran through `telltale experiment repeat` and were captured end to end. None did
the task: each session's transcript shows the agent asking for approval to run `uv run
pytest` three times and stopping, because the spec (written by the orchestrator) launched
with `--permission-mode acceptEdits`, which accepts file edits only; a Bash call in
headless mode then needs an approval nobody can give. E01 ran the same prompt with
`--permission-mode bypassPermissions --max-turns 40` and fixed the test in 7 turns.

| Attempt | Model requests | Output tokens | Cache-read tokens | Files changed | Acceptance |
|---|---|---|---|---|---|
| 1 | 4 | 303 | 119,496 | 0 | fail |
| 2 | 4 | 477 | 119,505 | 0 | fail |
| 3 | 4 | 366 | 119,504 | 0 | fail |
| 4 | 4 | 317 | 119,498 | 0 | fail |
| 5 | 3 | 301 | 85,643 | 0 | fail (recorded by hand: 1 failed, 1 passed) |

What this pilot does support: the harness end to end (worktree per repetition, capture,
correlation rows, acceptance command run by the harness, evidence vector per capture)
works on real sessions, and the acceptance check is independent of the agent. What it
does not support: anything about H2. The within-condition spread of these five vectors
is the spread of a refused tool call. The corrected spec is in the E05 PR and is refused
by the runner if it lacks bypassPermissions. The runner's session died with the
implementer's session because it ran in the background; the recovery from the store is
the E05 PR.

Merged as #25 after verification (gates green, 166 integration tests; the runner refuses
the old spec with "command is headless claude with --permission-mode 'acceptEdits' ...
Use bypassPermissions, or drop -p"; attempt 5's outcome is in the store, recorded by the
harness re-running the acceptance command in the leftover worktree). Two reducer
findings from that PR, recorded and not fixed, carried to wave 3:

- `failed_test_runs` counts a refused tool call the same way as a failing test: a
  permission denial sets the stream's `is_error`, which is the route a failing test
  takes too. The distinction is in the capture (`claude.stream.system.permission_denied`);
  the reducer does not read it. The fix is a decision about what `success` means when a
  call was never made.
- `correlate.USAGE_KEYS` uses the OTel spellings, and the stream spells two of them
  `cache_read_input_tokens` and `cache_creation_input_tokens`, so a stream-only capture
  (the fake agent) has `cache_read_tokens` null at coverage partial. Real sessions have
  OTel up and are unaffected.

One claim in the PR's report is wrong and is corrected here: it says nothing has yet run
a Bash-using session under the launcher with bypassPermissions. Every implementer
session of this build is exactly that (14 build captures at the time of the E07 run,
all `claude -p --permission-mode bypassPermissions` under `telltale run`, all making Bash
calls), so the corrected E05 command shape is already proven through the launcher.

## E05 re-run (owner-approved 2026-09-02): the H2 floors for the pilot task

Five new sessions under task id E05r2 with `--permission-mode bypassPermissions
--max-turns 40`, merged as #27. The first session was validated alone (timeline: failing
test run, read, edit of pkg/calc.py, passing test run) before the other four were paid
for. Every number is from experiments/E05/out/decision.json and was spot-checked against
the evidence table.

| Measurement | Value |
|---|---|
| Acceptance | 5 of 5 pass |
| Model requests, turns | 5 and 5 in every session |
| Work per session | one read, one edit of pkg/calc.py, two test runs, no searches, no compactions |
| Patches | four byte-identical (diff hash 01abbb17ef6b), attempt 4 a different fix (2b1cbe01861b) |
| Output tokens | 595 to 656, median 636, scaled MAD 10.4 |
| Cache-read tokens | 155,995 to 156,087, median 156,043 |
| Agent-reported duration | 11,460 to 12,982 ms, median 11,819, scaled MAD 532 |
| Total spend | 868,083 tokens, 0.5264 USD |
| Measures with no variation at n = 5 | 22 of 28 |
| Measures that moved | 5, every one resolving at n = 5 (MDD below 0.25 median); none demoted |
| Not judged | 1, for want of a value (pre_compaction_tokens: no compaction happened) |

What this supports: for this one task under this one fingerprint, the within-condition
spread of the work measures is zero and the spread of the usage measures is small
enough that a repository comparison at n = 5 resolves differences of a quarter of the
median. What it does not support: any floor for a task where the agent's work varies;
this task is short enough that four of five sessions wrote the same bytes.

One reducer finding from the timeline, carried: the first test run of every session is
`uv run pytest 2>&1 | tail -50`, and its verification_run outcome is "ok" although the
tests failed, because the tool's exit code is the pipeline's last command and the "Exit
code N" pattern never appears. So fail_to_pass_cycles is 0 on all five where the true
value is 1. A piped verification command's exit status is the pipe's, not the test's,
and the reducer must say "unknown" rather than "ok" when a pipe follows a test command.

## E06 (owner-approved): H3 environment sensitivity, effort low against high

Ten sessions, five per arm, differing in exactly one launch flag (`--effort low` against
`--effort high`), merged as #29. Fingerprints identical within each arm and differing
in the one declared field. All ten passed acceptance. Numbers from
experiments/E06/out/decision.json, spot-checked against the evidence table.

| Measure | Low arm | High arm | HL shift against MDD | Verdict |
|---|---|---|---|---|
| output_tokens | 455, 466, 466, 468, 469 | 467, 492, 575, 590, 631 | 109 > 104.0 | material environment effect |
| cache_read_tokens | 155,782 x5 | 155,782 to 156,006 | 168 > 104.0 | material environment effect |
| stable_state_tokens | (decision.json) | (decision.json) | above MDD | material environment effect |
| duration_ms, stable_state_total_ms, final_diff_lines, max_diff_lines | | | below MDD | not resolved at n = 5 |
| 20 work measures | identical | identical | no movement in either arm | no statement |

Spend 1,731,597 tokens and 1.0343 USD; runner wall 221 s. The effort fingerprint is a
changepoint for the three token measures and for nothing else, on this task on this
day. The one place the work itself varied is outside the vector: the five low-effort
sessions wrote a byte-identical patch and the five high-effort sessions wrote three
different patches, visible only through the capture-end snapshot's diff hash. Wave 2
exit criterion 3 (H3 factor effects stated as resolved or not resolved at n) is met.

## Design amendments folded

Recorded in docs/design/01-design.md under "Amendments from wave 2": the verification
columns (W2-T7), the capture-end reduce and the conflict-row deletion (W2-T6), the
snapshot triggers and tree-match rungs (W2-T6), the evidence vector table and the
unstored percentiles (W2-T4), and the CLI split.

## Owner decisions requested

1. **The real backfill import.** The dry run reported 1837 files / 1721 Claude
   sessions / 1.68 GB and 1454 Codex sessions / 3.03 GB. Measured on a slice into a
   temporary home: 80 Claude sessions (89.08 MB) imported in 8.13 s and 39 Codex
   sessions (72.43 MB) in 6.66 s, about 11 MB/s each, giving 28,687 and 7,459
   observations and a 61.8 MB database. Projected from that slice: about 2.5 minutes
   for Claude, 4.6 minutes for Codex, a database of roughly 1.8 GB (the slice is
   recent sessions; older ones may pack differently), and about one minute for the
   rebuild (119 captures in 2.16 s). One cost it carries: `vector` and `compare`
   scan every observation for cohort keys, so at that size a call takes seconds
   rather than 0.3 s until the store reader above lands; the cohort rule excludes
   imported captures by default, so nothing else changes. What it buys: about 3200
   sessions of the owner's real history through the same sanitizer, the corpus every
   later cohort, profile and backtest reads. Recommendation: approve the full import
   now (`telltale import claude-transcripts` then `codex-rollouts`, both idempotent),
   and let wave 3 land the reader.
2. **E05 and E06, the H2 and H3 pilots on a real agent.** Task: the E01 fix-the-test
   repository with `uv run pytest` as the harness acceptance command. E01 measured
   one S1 session at 19.5 s, 7 turns and 246,950 tokens (0.128 USD at API prices; the
   owner's subscription pays it). E05: 5 repetitions, one condition, about 100 s of
   agent wall time and 1.25 million tokens, nearly all cache reads. E06: effort low
   against high, 5 per arm, about 200 s and 2.5 million tokens (the high arm may cost
   more; that is what it measures). What they support: the first real within-condition
   floors per measure and the first real factor effect, each stated as resolved or
   not resolved at n = 5 with the N_needed printed. What they cannot: anything about
   a long session or another task. Recommendation: approve both, E05 first; they are
   15 short sonnet sessions and every later repository claim rests on the floors.
3. **Carried from earlier gates, still open:** the owner's `~/.codex/hooks.json` does
   not parse under Codex 0.150.1 (the praxis Codex hooks are silently dead; Telltale
   did not touch it); the GitHub social preview image (docs/assets/logo-512.png) is
   uploaded by hand in the repository settings.

4. E05 again, with the corrected flags: five new `claude -p --model sonnet
   --permission-mode bypassPermissions --max-turns 40` sessions on the same task. Cost
   basis is E01 S1 (19.5 s, 7 turns, 246,950 tokens); the five wasted sessions cost about
   7.7 s and 120k cache-read tokens each. Recommendation: approve, and hold E06 (10
   sessions, effort low vs high, same command shape) until the first corrected E05
   session shows the agent editing the file, so that E06 does not repeat the defect at
   twice the size. Both use the same runner path, so one good session proves the flag
   for both.
