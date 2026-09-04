# Wave 4 gate report (spec v0.4: controlled repository interaction), in progress

Owner approval to dispatch wave 4: 2026-09-02 night ("APPROVED for all 3": E08b, the
W4-T1 and W4-T2 dispatch with the E09/E10/E12 session requests to follow with pilot
numbers, the backup deletion). This file grows as tasks merge; every number is measured
and says where.

## What was merged

| PR | Task | What landed |
|---|---|---|
| #38 | W4-T1 | `telltale experiment probe <spec.json>`: fixed read-only probes with an answer key written before the run, N repetitions each in its own detached worktree at one base_sha, precision and recall per repetition, the score posted as an external.outcome (no new observation type), the answer text read and dropped; `telltale experiment intervention <spec.json>`: the same probes at two commits, paired by probe, the fingerprint assertion inverted (the arms must differ in NO field); `experiments_probe.py`, `report_probe.py`, `cli_probe.py`, 12 tests |
| #40 | W4-T2 | `telltale profile <repo_id> [--path PREFIX] [--by week\|subsystem\|path] [--json] [--include-backfill]`: 22 observed distributions per group with the sample composition beside them, one comparative number per row (the ratio of the group's median to the median of the cohort OUTSIDE the group, refused below ten outside members, per metric), backfill captures in groups of their own, the four words maintainability, quality, difficulty and score refused by the renderer; `profile.py`, `report_profile.py`, 18 tests |
| #41 | W4-F1 | Orchestrator fix: `evidence_by_capture` index, decided by measurement (docs/log/W4-F1.md) |
| #42 | W4-T3 | The command classifier reads a chain whole: a newline is a separator outside quotes and heredoc bodies (`commands_shell.py`, new), a heredoc body is `_`, a runner word does not spend the two-bare-token budget, the highest-priority verification category anywhere in the chain names the run and a new `categories` field on verification_run lists every one it held; `exit_masked` judged on the chosen segment; `cmdnorm-v4`, `commands-v1-556dc49d`; 1 new test through the launcher, goldens unchanged |
| #43 | W4-T4 | A Claude stream assistant message's `output_tokens` is a pre-completion snapshot, stored as `output_tokens_snapshot` and read by nothing; the result record's `usage` block is the main thread's totals (stored as `main_thread_*`), the session figure is `modelUsage`, carried on the session_end lifecycle row; a stream-only capture's usage.output_tokens is that figure at coverage partial with a warning that the split per request is unknown; the request-clock column is None there; `stable_state_tokens` is null when a counter is unknown; a rebuild fixes captures on disk; 8 new tests |
| #45 | W4-E09 | The legibility probes: 21 sonnet sessions, decision file docs/experiments/E09.md, checker and store facts scripts |
| #46 | W4-T5 | An `instructions` factor for the environment and intervention runners: arms are two commits whose `git diff --name-only` is instruction surfaces alone (refused otherwise, naming the path), and whose fingerprints differ in `instruction_hashes` alone after the runs, the path named; `experiments_factor.py` split out; 5 tests |
| #47 | W4-E12 | Blinded diagnosis, pilot only: 2 Opus reviewer sessions, the raw arm over the token bound; docs/experiments/E12.md records the pilot and the decision |
| #48 | W4-E10 | Instruction intervention: 20 sonnet sessions, repetition-major runner with a stop block (experiments_stop.py), docs/experiments/E10.md |
| #49 | W4-F3 | Unknown outcomes make a verification count None; a tool error survives a masked chain; timeline says "check masked" |

The amendment files (docs/design/amendments/W4-T1.md, W4-T2.md, W4-F1.md, W4-T3.md,
W4-T4.md, W4-F2.md, W4-T5.md) are folded into 01-design.md at the wave exit.

## Measurements

- W4-T1, re-run by the orchestrator from the test helpers' repository under a
  temporary home (2026-09-03 00:05): P-exact scored 1.000/1.000 pass on all three
  repetitions and P-narrow 0.500/1.000 fail on all three, the hand-computed numbers of
  docs/log/W4-T1.md; the intervention printed identical fingerprint ids for both arms,
  precision HL shift -0.500, Cliff's delta -1, exact p 0.100, recall 0/0/1.000, Reads
  +1/+1/0.100; a spec whose arms differ in base_sha and a flag was refused naming both
  and created no database; the store bytes held the answer sentence 0 times, the path
  `src/alpha.py` 24 times and `precision:0.500` 6 times. The implementer measured 2.19 s
  for 6 captures of the probe suite, 2.25 s for the intervention, and 0.080 ms per
  scorer call over this repository's 427 tracked paths.
- W4-T2, re-run by the orchestrator on a fresh `.backup` copy of the home store
  (2026-09-03 00:30, 1,541,439,488 bytes): 49 captures carry this repository's id.
  `--by week` is one group, 2026-W36, refused for every metric with "10 of 43 captures
  have an unknown cohort key" (the `telltale run -- bash` smoke captures have provider
  generic and no model). `--by subsystem` places docs 17, experiments 7, fixtures 1, src
  21 and tests 13 captures with the ratio column filled (12 to 32 cohort members outside
  each group). `--path src/telltale/forecast` selects 3 captures whose median
  fresh_input_tokens is 160 tokens, 0.714 times the median of the 30 outside.
  `--include-backfill --by subsystem` adds eight `(backfill)` groups with no cohort.
  Each invocation took 1.18 to 1.28 s. The four refused words appear 0 times in all
  five outputs. Break-and-restore: `>= 2` in place of COHORT_MIN fails
  `test_a_cohort_with_fewer_than_ten_outside_the_group_is_refused` and
  `test_the_command_prints_one_table_per_metric_with_the_cohort_column`; restored, the
  suite is 247 passed.

- W4-T3, re-run by the orchestrator on a fresh `.backup` copy of the home store
  (2026-09-03 01:55): rebuilt, the six E12 captures say agent_test_runs 4, 3, 3, 1, 2, 6
  (19 of the key's 51: the one-line runs); the six archived streams captured fresh
  through the real launcher (`telltale run --provider claude -- python3 emit.py <raw>
  --output-format stream-json`) say 10, 10, 5, 11, 2, 11 (49 of 51) with masked counts
  9 of 10, 9 of 10, 5 of 5, 11 of 11, 2 of 2, 10 of 11, fail_to_pass_cycles 1 and 2 on
  the two E08 sessions (lint and typecheck cycles), and 124 verification_run rows every
  one carrying `categories`; the five W2-E05 pilot captures still say refused_tool_calls
  3 and agent_test_runs 0 and the five re-runs agent_test_runs 2 with "2 of 2
  verification runs have a masked exit status"; `git diff main -- fixtures/` empty.
  Break-and-restore: `text` in place of `commands_shell.with_separators(text)` fails
  test_commands.py with `assert 2 == 4`; restored, 1 passed. Gates on the merged tree:
  249 passed, mypy 91 files, ruff, format, deptry, pre-commit. Home store rebuilt after
  the merge: 3229 captures in 63 s (183 s was measured for 3223 before W4-F1 and W4-T3;
  the two changes are not separated).

- W4-T4, re-run by the orchestrator (2026-09-03 03:10): gates on the merged tree 257
  passed, mypy 92 files, ruff, format, deptry, pre-commit; a full rebuild of a fresh
  `.backup` copy (3230 captures, 68.8 s) moved the usage evidence of 8 captures, all of
  them `stable_state_tokens` (three scripted stream-only captures to null, five mixed
  captures down), and left output_tokens of the six E12 originals (141206, 122438,
  67988, 99305, 153943, 89125) and of the five W2-E05 re-runs (629, 636, 641, 595, 656)
  unchanged; the material store rebuilt from the archived streams gives the same six
  output_tokens and the key check is 29 of 36 (Q10 agree on all six; Q1 10, 10, 5, 11,
  2, 11 as after W4-T3). Break-and-restore: `and False` on the session-total branch of
  measures_spec13.usage fails 3 of the 8 new tests; restored, 8 passed. Home store
  rebuilt after the merge: 3230 captures in 64 s.

- W4-E09 (legibility probes, 21 sonnet sessions, PR #45), re-checked by the
  orchestrator (2026-09-03): the home store holds 3 captures for each of the six probes
  and 3 for the pilot, all with experiment E09; `store_facts.py` sums total_cost_usd
  over the 21 suite captures to 2.688383; the report's 3,701,121 tokens and 313.0 s of
  session wall are the runner's own totals; `check_numbers.py` runs on the out/ files;
  the answer sentence probes read 754 hits for `src/telltale/store.py` and 30 for
  `precision:1.000` (both are stored facts, not answer text); nothing under src/ or
  tests/ changed. Verdicts under the pre-registered rule: P1, P2, P6 answered (median
  precision and recall 1.000); P3 partially answered (0.833 / 1.000); P5 partially
  answered (0.600 / 1.000); P4 not answered (1.000 / 0.667: two of three named
  `classify` and not `_RULES`). Four sessions crossed the 200,000-token STOP bound
  (P3 at 648,183, 461,295 and 427,873; P4 attempt 2 at 227,192) and none crossed five
  minutes; the bound could not act because the pilot ran P1 alone (about 72,000
  tokens) and the runner completes every repetition in one invocation.

- W4-T5, re-run by the orchestrator (2026-09-03): gates on the merged tree 262 passed,
  mypy 95 files, ruff, format, deptry, pre-commit; test_experiments.py 635 lines after
  the split; with the pre-run diff check disabled (`stray = []`) the two-factor refusal
  test stops refusing and runs the experiment to completion; restored, 5 passed.

- W4-F3, re-run by the orchestrator (2026-09-03) in a verify worktree merged with main:
  gates 268 passed, mypy 97 files, ruff, format, deptry, pre-commit; the material store
  copy rebuilt with the branch: `failed_test_runs`, `fail_to_pass_cycles`,
  `edits_after_last_successful_test` and `post_failure_revisits` null on all six
  captures at coverage partial with the "of the K runs whose outcome a surface stated"
  sentence; timeline failed rows 3, 1, 1, 6, 4, 1 (the is_error counts, 16) and
  "check masked" rows 35, 20, 7, 12, 20, 15 (109); with the `exit_masked` guard removed
  from `failed()` the masked-chain test fails (`assert 0 is None`), restored it passes.
  After the merge: home store rebuilt (3280 captures, 69 s); material store rebuilt and
  `make_key.py experiments/E12/manifest.json --check --material` prints 30 of 36 with Q2
  None on all six (the designed arm S answer). Carried: the Codex reducer
  (activities_codex.py) still drops a tool error on a masked chain; no Codex fixture has
  a masked verification run to measure it on.

## Exit criterion (spec 21 v0.4)

"Determine which repository claims are supportable and which remain workload analytics.
Do not create a universal maintainability score." Verdict, final on 2026-09-03 after
the E12 full run (#53):

Supportable, claim class comparative, with the cohort stated in every case (this
repository, one environment fingerprint per experiment, `claude -p --model sonnet`):

- Whether a fixed read-only question about this repository is answered by an agent, per
  probe, under the pre-registered rule of E09: P1, P2 and P6 answered (median precision
  and recall 1.000 over 3 repetitions), P3 and P5 partially answered (0.833 / 1.000 and
  0.600 / 1.000, precision a lower bound because the orchestrator's keys were narrow),
  P4 not answered (1.000 / 0.667). n = 3 per probe.
- Whether an instruction-file rewrite under matched fingerprints is followed by a
  different amount of work on the same questions: E10 labels "material environment
  effect" on every effort measure that moved (P3 tool calls 12 to 7, P5 tool calls 7 to
  0, Cliff's delta -1, exact p 0.008) and "not resolved at n" on recall; n = 5 per arm;
  every such measure is demoted from repository comparison until N_needed (8 to 31).
- Whether a reviewer reading only Telltale's summary diagnoses a build session as well
  as one reading the raw stream (H1, E12 full run, 6 sessions per arm, Opus): "summary
  sufficient" on 8 of 10 questions (Q1, Q3 to Q7, Q9, Q10; arm S 6 of 6 on each, and on
  Q3 arm S beat arm R 6 to 5), "summary insufficient" on Q2 (arm S declared unknown on
  all six, the designed answer after W4-F3, where the pilot's reviewer had read the
  pre-fix zero as zero) and on Q8 (no commit count in the summary: arm S 1 of 6, arm R
  4 of 6). Cost: raw median 696,509 tokens against summary 422,835.5 (1.647x), wall
  138,098.5 ms against 177,974 ms (0.776x); largest session 975,489 tokens and 486,416
  ms against the bounds of 2,000,000 and 600,000; no reviewer read outside its material.
- The natural history of measures on the change clock as distributions with n and
  coverage per point (W4-T2), and one cohort-gated percentile per measure when ten
  members outside a homogeneous group measured it.

Workload analytics, not repository claims, and said so in every output:

- Any statement about the repository's quality, difficulty or maintainability: nothing
  computes one, and the profile renderer that would print a score raises (W4-T2).
- The effort measures of E10 as a property of the repository: demoted at n = 5, and the
  intervention's precision result is a key artefact (the after arm cites AGENTS.md).
- Whether Telltale's summary suffices to diagnose a session in general: E12 supports it
  for six sessions, ten questions and one reviewer model, and nothing wider.
- Anything beyond this repository, this model, these commits and these questions.

Wave 4 exits. Carried into wave 6: the summary carries no commit count for the agent's
own `git commit` calls (Q8), and the Q8 wording (calls versus commits) is a
questionnaire defect recorded, not rescored.

## Constraints found, decisions to take

- The intervention runner refuses an AGENTS.md rewrite as an intervention: base_sha
  arms must differ in no fingerprint field, and instruction_hashes is one. The plan's
  E10 (an AGENTS.md rewrite on a branch) therefore needs a runner amendment first
  (a declared `instructions` factor whose assertion expects instruction_hashes to differ
  and nothing else), or E10 becomes a scoped code refactor with keys valid at both
  commits, which is spec 14.4 as written. Recommendation in the session request below.
- The evidence table had no index on capture_id: each per-capture evidence read was a
  scan, 22 ms each and 0.75 s of every profile on the owner's store (W4-T2 measured it
  and memoised the reads instead). W3-T3 had dropped an unread index on observations
  for its writer cost, so this one was measured before it was added (W4-F1, 2026-09-03,
  a `.backup` copy with 129089 evidence rows, arms interleaved): the write path begins
  with `DELETE FROM evidence WHERE capture_id = ?`, so the index makes the writer
  faster, not slower: 16.4 ms against 0.82 ms per delete-and-reinsert of one capture,
  `profile --by subsystem` 1.12 s against 0.61 s, the CLI rebuild of 20 captures
  12.09 s against 10.64 s. Added in schema.py; every store gains it at its next open
  (0.078 s). docs/log/W4-F1.md has the table.
- A group of one capture gets a ratio (`fixtures`, 1.643x). No floor on the group's own
  size exists; n is printed and the scaled MAD is "-". Whether a floor belongs is the
  owner's call.
- `briefs` is a subsystem group only with `--include-backfill`: the orchestrator's own
  sessions reach the store through the transcript importer, not the launcher.
- Probe precision is an upper bound: a wrongly named symbol is counted nowhere (the
  repository enumerates paths, not the symbols an answer could invent). Reports say so.
- `n_needed = 0` at a spread of 0 (stats.py, shared with the environment runner), and
  the 37-line between-arm warning tail: carried for whichever task owns stats output.

- E12's key cross-check (experiments/E12/make_key.py, ten facts per session derived
  from the raw stream-json of the six most recent build sessions) disagreed with
  `telltale show` on 10 of 36 comparable cells, all of them the two pytest questions on
  five sessions: the streams hold 51 pytest executions and Telltale's agent_test_runs
  summed to 14 (W4-T2/1 10 against 4, W4-T1/1 11 against 1, W3-E08b/1 6 against 3,
  W3-V/1 11 against 0, W3-E08/1 2 against 2, W3-T4/1 11 against 4). The brief for W4-T3
  said 52 and gave W3-E08/1 as 3; that was the orchestrator's transcription error, the
  key file had 51 and 2 all along, and docs/log/W4-T3.md names it. Cause, read in
  commands.classify: "first-segment-with-a-known-category-wins" and no newline
  separator. Fixed by W4-T3 (#42), which found two more rules in the same place (a
  heredoc body filled the 200-character normal form; a runner word spent the bare-token
  budget, so `lint` and `format` were the category of none of the 769 Bash calls while
  81 ran ruff).
- **A rebuild cannot apply a normalization rule to a capture already on disk.** The
  reducer reads the stored normal form and the raw command line was never kept (design
  6.4). After the merge and the rebuild, the six captures say 19 of 51 (every one-line
  run); the 32 multi-line runs are recoverable only from the raw streams, which the
  store never held. Every capture from 2026-09-03 on gets all four rules.
- **`MAX_COMMAND`, the 200-character bound on a stored normal form, hid 2 of the 51
  runs** (chains of 311 and 355 characters with pytest at the end). Raising it puts more
  of an arbitrary command line on disk: the owner approved raising it on 2026-09-03 and
  W4-F2 set it to 512, the bound every other kept string has, on this measurement over
  the same 769 commands with the bound lifted: 100 normal forms reach 200, 7 reach 512,
  none reaches 768 (longest 717), no pytest segment starts past character 300; at 512
  the E12 key and `telltale show` agree on Q1 for all six sessions. `cmdnorm-v5`.
- `NORMALIZATION_VERSION` is a hand-bumped string (`cmdnorm-v4`), unlike the two hashed
  versions beside it. Carried.
- **A stream-only capture reported output_tokens from a message-start snapshot.** Found
  by the E12 material store (below): `show` said 1902, 912, 636, 1620, 1421, 1271 where
  the sessions' result records say 141206, 122438, 67988, 99305, 153943, 89125. Fixed
  by W4-T4 (#43), which measured two more things on the way: the stream's per-message
  number is below the transcript's final count on all 761 messages of the six sessions
  joined by id (so the transcript backfill is right and untouched), and the result
  record's `usage` block is the MAIN THREAD's total, not the session's (E01's subagent
  fixture 350 against 2368, its compaction fixture 2714 against 18548); the session
  figure is `modelUsage`. Carried from its report: 00-digest.md 2.1 must say so at the
  wave gate; `stable_state_tokens` changed in a file beyond the brief's OWNS
  (measures_intervals.py, null instead of a sum short by one counter), which the
  orchestrator accepts as the named defect class; the `modelUsage` sum over two models is
  untested (every session so far used one); tests/integration/test_experiments.py sits
  at the 800-line limit and the next task touching it splits it.
- **E12 arm S material comes from a dedicated store, not the owner's.**
  experiments/E12/build_store.py captures each archived stream through the real launcher
  into experiments/E12/out/store (six captures, ids in manifest.json as
  material_capture_id): 49 of 51 pytest runs against 19 in the owner's store. The key
  cross-check against it after W4-T4: 29 of 36. What differs is explained in
  protocol.md and stays: Q1 one short on two sessions (`MAX_COMMAND`), Q2 withheld on
  five (a masked exit status is not a failure count; arm S is expected to answer
  unknown).

- **E09's P3 and P5 keys were narrow, and the orchestrator wrote them.** Every P3
  repetition also named `src/telltale/cli_common.py`, every P5 repetition also named
  `src/telltale/allowlist_codex.py` and `src/telltale/commands.py`. Read in the code
  on 2026-09-03: cli.py's `show` reaches the store and the capture lookup through
  cli_common.py, so it is on the path P3 asks for; allowlist_codex.py is the Codex half
  of the allowlist P5 asks for; commands.py is where a kept command string is scrubbed
  and bounded. So P3's and P5's precision is a lower bound of what the answers earned.
  Decision: E09 stands as scored under its pre-registered keys and is not re-run (the
  owner's rule on sessions, and P3's sessions exceed the STOP bound); E10 pre-registers
  the corrected keys and says so. The labels above carry this caveat.
- **A pilot has to run one repetition of every probe**, not three of one, and the probe
  runner needs a stop that acts between sessions: the next probe brief (W4-E10) carries
  both, the second as a runner change.
- E09's write-up notes P1 is answered from AGENTS.md (invariant 4 names store.py), which
  is legibility of the instruction file rather than of the code: the E10 shape below is
  built on that observation.

## Sessions requested

- E09: 6 probes on this repository, keys in experiments/E09/answer_keys.json (written
  from the code, verified by grep, committed before any run), 3 repetitions each, pilot
  first: experiments/E09/pilot.json (P1, 3 sonnet sessions) then spec.json (18). Both
  pass the runner's checker. Brief briefs/W4-E09.md with the pre-registered rule and the
  STOP bounds (5 minutes or 200,000 tokens per session; stop if the runner cannot read
  the result text). Cost needs measuring: the only reference on this machine is E05r2's
  fix-the-test sessions at 173,496 to 173,713 tokens and 13,600 to 15,132 ms each.
- E10 (approved 2026-09-03, 20 sessions): dispatched as W4-E10 after #46 merged, on P3
  and P5 with the corrected keys (experiments/E10/answer_keys.json), 5 per arm in
  repetition-major order with a stop block at twice E09's largest session; arms
  46ce655 (main) and 99ea05a (branch e10/instructions: a "Where things live" section in
  AGENTS.md and nothing else); the prediction is written in briefs/W4-E10.md.
- E12 (H1, blinded diagnosis): ready. Protocol, key, material store and brief are
  written (experiments/E12/protocol.md, key.json, out/store, briefs/W4-E12.md); the
  prerequisites in the protocol are met except the owner's approval. Two Opus reviewer
  arms over six sessions: pilot 2 sessions (W4-T1/1 in both arms), full run 12. STOP at
  10 minutes or 400,000 tokens per reviewer, or fewer than 5 of 10 questions answered
  in the pilot. Cost needs measuring: no reviewer session has run; the nearest reference
  on this machine is the Opus implementer sessions of this wave (W4-T3: 145,420 output
  tokens, 27.2 M cache-read tokens, 48 minutes; W4-T4: 117,469 output, 30.8 M
  cache-read, 37 minutes), which read and edited code rather than one file, so the pilot
  is the measurement.

## E12 pilot (attempt 1, #47, merged 2026-09-03)

The full run did not start: the raw arm's pilot session spent 588,848 tokens against the
pre-registered stop bound of 400,000 tokens per reviewer session, and the runner stopped
after the pilot as the brief required. The pilot pair (W4-T1/1 in both arms, Opus,
Claude Code 2.1.259, one fingerprint) measured:

| arm | capture | wall | tokens (cache read) | cost | turns | agree of 10 |
|---|---|---|---|---|---|---|
| S summary | cap_01M1KC1G3G7CFGXYZK48VNYMXW | 82.1 s | 149,323 (116,027) | 0.488 USD | 5 | 6 |
| R raw | cap_01M1KC5VBE5KZYPBJ53CPEX9K1 | 105.5 s | 588,848 (534,249) | 0.922 USD | 15 | 9 |

Arm S disagreed on Q2 (answered 0 failed pytest runs; key 6), Q3 (yes; key no), Q5 (0
tool errors; key 1) and Q8; arm R disagreed on Q8 only. Q8 asks how many Bash calls ran
`git commit`; both arms answered 2 because the one call made two commits, so the wording
costs both arms equally and the question stays as pre-registered.

Q2, Q3 and Q5 are recorder defects, found by reading the material the arm S reviewer
read (docs/gates measurement, experiments/E12/out/store):

- `failed_test_runs` printed 0 while every one of the 11 test runs had a masked exit
  status (`... | tail -2` chains); `fail_to_pass_cycles`, `post_failure_revisits` and
  `edits_after_last_successful_test` (3) are counts over runs whose outcome nobody saw.
  All six material captures have every test run masked. This is invariant 5 in the
  summary's own output: a count over nothing printed as 0.
- The one tool error of W4-T1/1 sits on a masked chain (stream tool_result is_error
  true, exit_code 1 from the result text). `_tool_outcome` returns before reading it on
  a masked chain, so the timeline shows no failed row and the summary no error.

W4-F3 (briefs/W4-F3.md, dispatched 2026-09-03, attempt 1) fixes both: the four counts
become None with coverage partial and the stated-run counts move into the warning; a
masked chain keeps the call's own success and the timeline says "ok, check masked" or
"failed, check masked". Acceptance on the material store: null on all six captures, timeline
failed rows 3, 1, 1, 6, 4, 1 (the is_error counts), `make_key.py --check --material` 30 of
36 with Q2 withheld on all six (the W3-E08/1 Q2 agreement, 0 = 0 over two masked runs,
was a coincidence).

Blinding: `telltale timeline` prints paths and branch names, so the arm S material
mentions the task id 2 to 15 times per session; the runner reported it rather than
scrubbing. The reads-outside-material rule held (arm S 0 reads; arm R's two flagged
commands decode to the material directory itself).

Owner decision (standing gate 2): whether to raise the token bound and run the 12
sessions after W4-F3 lands and the material store is rebuilt. Linear projection from the
pilot pair: 4.43 M tokens, about 8.5 USD, about 19 minutes of reviewer wall; the raw
streams range 1.0 MB to 1.97 MB against the pilot's 1.44 MB, and tokens grow faster than
bytes (each turn re-reads the context), so the raw arm's real cost is unmeasured beyond
the pilot. Recommendation in the session message of 2026-09-03.

## E10 result (attempt 1, #48, merged 2026-09-03)

20 sessions (5 repetitions per arm, two probes, `claude -p --model sonnet`), 10:40 to
10:50, no session over the stop bounds (300 s, 1,300,000 tokens; the runner's own record
says planned 20, run 20, crossed none). Verified by hand: `git diff --name-only 46ce655
99ea05a` is AGENTS.md alone; the fingerprint assertion names `instruction_hashes` differing
at AGENTS.md (6197 to 7164 bytes) and nothing else; the five stop tests fail against the
un-fixed runner (5 failed) and pass with it; gates green in the verify worktree merged
with main; src changes confined to the runner and its report (experiments_env.py,
experiments_probe.py, experiments_stop.py, report_probe.py).

| probe | measure | before median | after median | HL shift | Cliff's delta | exact p | MDD | label per 6.12 |
|---|---|---|---|---|---|---|---|---|
| P3 | tool_calls | 12 | 7 | -5 | -1 | 0.008 | 4.151 | material environment effect |
| P3 | cache_read_tokens | 453,649 | 167,002 | -316,122 | -1 | 0.008 | 93,563.566 | material environment effect |
| P5 | tool_calls | 7 | 0 | -7 | -1 | 0.008 | 1.857 | material environment effect |
| P3 | precision | 1.000 | 0.857 | -0.143 | -0.8 | 0.048 | 0 | material (MDD 0: spread 0 in both arms) |
| P3, P5 | recall | 1.000 | 1.000 | 0 | | 1 | 0 | not resolved at n |

The pre-registered precision prediction (1.000 in every after repetition) was falsified:
the after arm cites AGENTS.md, which exists and is not in the key, in all five P3 and one
of five P5 repetitions. The write-up reports it as the key artefact it is and changes
nothing after the fact. Every effort measure labelled material moved down; all are
demoted from repository comparison (MDD > 0.25 median at n = 5) with N_needed printed.
All claims comparative; no causal reading; the branch `e10/instructions` stays on the
remote because the write-up cites commit 99ea05a.

Carried from E10: a key that cannot carry the instruction surface as an allowed citation
must be designed before the next intervention experiment, not patched after; the
`after` arm answering P5 with zero tool calls is a legibility risk (an answer from the
instructions without a read) that the probe runner should be able to flag.

## Requests open at the wave 4 gate (2026-09-03)

1. E12 full run (standing gate 2). Prerequisites now met on the fixed recorder: W4-F3
   merged (#49), material store rebuilt, key check 30 of 36 with Q2 None on all six.
   Request: raise the per-session token bound from 400,000 to 2,000,000 (the pilot's raw
   arm spent 588,848 on the 1.44 MB stream; the largest stream is 1.97 MB; tokens grow
   faster than bytes), keep the 10-minute wall bound, run the 12 sessions (6 per arm; the
   pilot pair re-run inside the set). Linear projection from the pilot pair: 4.4 M tokens,
   about 8.5 USD, about 19 minutes; the real cost of the larger streams is unmeasured.
   The attempt 2 brief is drafted and is committed on approval with the owner's date.
2. Wave 5 entry (standing gate 4): TimesFM-3 weights license acknowledgment
   (timesfm-non-commercial-license-v1.0: research use, not production). The wave 5
   briefs are committed (briefs/W5-T1.md, W5-T2.md, W5-E11.md). Measured limit stated
   up front: the change clock has 38 rows in two regimes (29 and 9, split by the Claude
   Code 2.1.257 to 2.1.259 update), 13 windows at H = 1 against k_min 20; every landed
   change passed its merge verification and no rework is on record, so E11 is expected
   to come out "not assessable" on every target and to say what data would change that
   (36 rows inside one regime, a failed merge verification or a rework on record). The
   mechanisms (feature extractor, post-merge columns, `--duration-ms` on outcomes,
   past-future covariates in the adapter, `forecast candidate`, `advise`) are built and
   tested on synthetic and fake-agent data regardless.

## E12 full run (attempt 5, #53, merged 2026-09-03)

Attempts 2 and 3 died on the 529 overload before any work; attempt 4 launched three
reviewers into the overload (recorded in experiments/E12/results/failed_launches.json,
zero tokens) and stopped itself; attempt 5 ran all 12 sessions between 15:29 and 16:03
at the amended bound (protocol.md "Sessions", stop_tokens 2,000,000). Verified by the
orchestrator in a verify worktree merged with main: gates 284 passed, mypy 102 files,
ruff, format, deptry, pre-commit; summary.json bounds.stop_tokens 2000000 and six
scored per arm; reads outside the material 0 of 12; src/tests untouched except the
bound constant in experiments/E12/run.py; check_numbers 1190 of 1248 matched, the rest
quoted rule text and MB figures. Results in the verdict above and in
docs/experiments/E12.md.
