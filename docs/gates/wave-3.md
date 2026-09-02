# Wave 3 gate report (spec v0.3: clocks and temporal validity)

Owner approval to dispatch wave 3: 2026-09-02 ("Approve the E05 re-run and wave 3, go
ahead"). This file grows as tasks merge; every number is measured and says where.

## What was merged

| PR | Task | What landed |
|---|---|---|
| #28 | W3-T1 | attempt and change clocks (`series build --clock attempt|change --repo <id>`), `RowMeta.flags` low_confidence, `telltale outcome`, cli_import.py and cli_outcome.py splits |
| #30 | W3-T0 | typed store reads with the index the query plan uses; a refused tool call is `outcome: refused`, never a verification run; stream usage keys fill cache_read_tokens on stream-only captures; importer diagnostics say "unparsed line kind" |
| #31 | W3-T4 | telltale.repo.commit carries a bounded per_file list; the change clock fills subsystems_touched, test_files_changed and dependency_delta from it (None for commits recorded before, with the reason on the cohort); service_version and terminal_type on claude.otel.metric; allowlist.py and launch.py split; series_paths.py |
| #33 | orchestrator | `sessions --link-commits` rebuilds the capture it appended to; regression test fails on the un-fixed code |
| #32 | W3-T2 | block-shuffle placebo, the four-label decision with every inequality, A/B/C ablation, one-step candidate protocol, `forecast placebo` and `forecast ablate`, the word refusal (cause, impact, would), the owner's reading guide docs/design/04-forecast-reading-guide.md; `forecast backtest` withholds the label until a placebo exists (superseded by #39) |
| #34 | W3-T3 | a verification run whose chain hands its exit status to another program (`\|`, `;`, `\|\|` after the test command) is `exit_masked` with outcome unknown, and failed_test_runs and fail_to_pass_cycles say so in coverage and warnings; a refused Read or Edit is a tool_call and nothing else; a series column with no value is never observed; the unread obs_by_type index is dropped at open (measured 6.1 percent writer cost, W3-T0 had 16.8); activities.py split |

| #35 | W3-E08 | temporal validity on captured data: placebo and decision on every request-clock pair, the lineage clocks' readiness counts, the ablation attempt; runner reuses E07's |
| #36 | W3-E08b | the 38 E08 rows re-labelled under the pre-registered amendment: 37 baseline sufficient, 1 not assessable; amendment commit a351cac precedes the numbers |
| #37 | W3-V | the wave 3 verifier's report, docs/log/W3-V.md: 62 VERIFY assertions re-run, 58 pass, 4 fail, 9 break attempts held, 3 findings no report stated |
| #39 | orchestrator | W3-VF: `forecast backtest` labels the row it stores through the decision rule with no placebo and names a stored placebo; `backtest.persist` refuses cause, impact and would; a capture with two attempt identities is dropped by name, with a test |

## E08: the placebo control fails on this data, so no label may be written

Every number from experiments/E08/out/summary.json and aggregates.json, checked by
experiments/E08/check_numbers.py.

| Measurement | Value |
|---|---|
| Rows scored | 38 (36 per-capture pairs, 2 pooled), all output_tokens on the request clock: no other target passed readiness, in E07 or here |
| Valid placebos | 0 of 38 |
| Placebo runs that made persistence worse | median 2 of 10 per pair, range 0 to 8 |
| Placebo persistence MAE over true-order persistence MAE | median 0.8176: shuffling the past makes persistence BETTER |
| Lag-1 autocorrelation of output_tokens | median 0.0592 across the 36 pairs |
| Labels | not assessable, 38 times |
| E07's half-rule on the same 36 true-order runs | 35 baseline sufficient, 1 pending: E07's headline reproduces exactly |
| TimesFM-3 determinism | identical numbers across 3 repeats on this machine; 2 of 17 pairs shared with E07 differ in the timesfm column by 0.03 and 0.11 percent (environment, not model) |
| Wall | pilot projection 49.6 min against the 90 min stop; recorded stages 43.22 min |
| Attempt clock | 29 rows, no attempt-clock target registered, so no readiness check exists |
| Change clock | 23 rows: 7 origins at H = 1 and 1 at H = 4 against k_min 20; 20 windows need 37 changes at H = 1 or 96 at H = 4 |
| A/B/C ablation | attempted, 0 origins common to A, B and C |

What the data says: output_tokens per request alternates between short tool-call turns
and long text turns, so the distance from one request to the next is larger than the
distance between two requests picked at random (pilot capture: 932 tokens against 784).
A shuffle that destroys recency helps the one baseline whose method is recency. Design
6.12 says a placebo under which persistence does not get worse is broken or the series
carries no recency; the first reading was ruled out by measurement (true rows
unshuffled, four confirmations in docs/log/W3-E08.md), the second is what the data says.

What it costs: the laboratory cannot say "temporal evolution" or "conditional
prediction" on this data, and under the rule W3-T2 implemented it also withholds
"baseline sufficient", because a run whose control controlled for nothing licenses
nothing. The baseline comparison itself does not depend on the control, and E07 and E08
both show the model never beating the baselines. Whether the rule should let the
true-order comparison alone earn "baseline sufficient" is a change to a pre-registered
rule, so it needs a new experiment id and the owner's word: see the decisions below.

## E08b: the same 38 rows under the pre-registered amendment (owner-approved)

Merged as #36. The amendment (an invalid placebo gates only the two positive labels;
"baseline sufficient" is the true-order comparison alone) was committed as a351cac
before any number was produced, and experiments/E08b/out/summary.json records that
commit as its rule. No model, placebo or session ran again.

| Label | E08 | E08b |
|---|---|---|
| baseline sufficient | 0 | 37 (35 per-capture pairs, 2 pooled), each with the warning "placebo invalid: the positive labels were not assessable" |
| not assessable | 38 | 1 (cap_01M1HKTJB8C34SPHQYAQMHGF4W at H = 4: the model beat the baselines there and the control failed) |
| temporal evolution or conditional prediction | 0 | 0 |

The count matched E08's own prediction (35 and 1) exactly. What the laboratory may now
say about output_tokens on the request clock: TimesFM-3 does not beat rolling_median
on 35 of 36 captures and both pooled rows (predictive claim, observed coverage), and
nothing about the order of a session, because this target carries no recency.

## Verifier W3-V (#37) and the orchestrator's fix W3-VF (#39)

W3-V (a fresh Opus session, brief briefs/W3-V.md) re-ran every VERIFY line of the six
wave 3 briefs and of #33 from a clean checkout, on a copy of the home store (3224
captures, 1,070,877 observations, taken 2026-09-02 23:15), attacked each report's claims
with crafted inputs through the real launcher, receiver and CLI, and ran the gate set.
Its report is docs/log/W3-V.md; the numbers below are its.

- 62 VERIFY assertions: 58 pass, 4 fail.
  1. `telltale vector` on the copy: 1.00 to 1.04 s over six warm runs (first cold run
     4.50 s) against the brief's "under 1.0 s"; W3-T0 reported 0.85 s and the
     orchestrator measured 0.92 s on a copy 24 captures and 26,019 observations smaller.
     The verifier's profile puts the code W3-T0 rewrote at 0.053 s of that second; the
     rest is `captures()` and 30 per-metric evidence reads. Not bisected. Carried: the
     bisect comes before anyone calls it a regression.
  2. W3-T0's OUTCOME sentence that the importer wording "stops 1696 rows on the owner's
     store reading like queue loss" is false for those rows: diagnostics are ingest
     facts and are not rewritten, so 1696 rows keep the old form and 0 carry the new.
     Every import from now on writes the new form.
  3. `forecast backtest` printed "placebo not run: label withheld" unconditionally, even
     after `forecast placebo` had stored 10 placebo rows for the same triple, and stored
     `decision` NULL: 6 of the 8 true-order rows the verifier's session wrote through
     the CLI carried no label. Fixed as #39.
  4. One number in docs/experiments/E08.md (176.249 s, the rebuild of 3223 captures) is
     under no file in experiments/E08/out/: it lived in census.json, which E08 excluded
     for size (1.2 MB against the 500 KB hook limit). E08.md's own rule is broken by one
     number; recorded here, E08.md left as merged.
- Nine break attempts, all held: `||` and `;` after a test command through the real
  launcher (2 of 4 runs masked, failed_test_runs at coverage partial), a refused Read,
  a 101-file commit (per_file truncated, the three path columns None on that row
  alone), an all-None column (unavailable, not observed), a placebo that shuffles the
  test rows (5 tests fail), a report containing "impacts" (refused), block_shuffle over
  20 (block, seed) pairs (multiset preserved), a backtest after a placebo (the defect
  above), two captures per attempt (refused naming both).
- Three findings no report stated: the backtest defect; the word refusal guarded the
  renderer and not `persist` (a poisoned run stored as fc_01M1J47GNJPXKRYCR4FZTE59KT on
  the copy); `series_lineage._identity`'s duplicate refusal had no test (`sorted(named)[0]`
  in its place left the suite green). All three closed by #39, each with a test that
  fails on the un-fixed code; docs/log/W3-VF.md pastes the three failures.
- `telltale outcome` run on the store copy appended a real observation (a duplicate of
  one the merge protocol had posted for W2-T8/1, so the series id did not move). A
  verifier testing a tool that writes must expect it to write; the copy was disposable
  and the home store was never opened for writing.
- The orchestrator's own fixes (#33, #39) have no capture and so no attempt-clock row:
  `telltale outcome` refuses them by name ("no capture of this repository carries W3-VF
  attempt 1"), which is the right answer.

## Exit criterion (spec 21 v0.3)

"Every forecast claim labelled temporal evolution, conditional prediction, baseline
sufficient or not assessable, with the inequalities shown." The verifier checked every
place a forecast number is printed or stored (14 rows in docs/log/W3-V.md) and found the
criterion met everywhere except `telltale forecast backtest`, which failed both halves:
stdout carried a label with no inequality, and the stored row carried nothing. After
#39, `forecast backtest` labels through the one decision rule with no placebo
("baseline sufficient" when the true-order comparison earns it under the E08b
amendment, otherwise "not assessable" with the reason "placebo not run"), prints and
stores the two inequalities it evaluated, and names placebo rows already stored for the
pair instead of denying them. The 10 placebo rows and the 3 ablation-variant rows of a
run carry no label by design: the label is about the pair and rides on the true-order
row. Verdict: met.

## Measurements so far

- `telltale vector` on the 3190-capture store copy: 0.92 s wall for the whole command
  (was 10.9 s), inside the 1.0 s acceptance stated in the brief. The implementer's own
  timings are in docs/log/W3-T0.md.
- The five E05 pilot captures, rebuilt: agent_test_runs 0, failed_test_runs 0,
  refused_tool_calls 3 each.
- Attempt clock on the home store after the merge protocol posted outcomes for every
  merged attempt: 23 rows, 16 labelled at the time of posting (7 unknown: attempts that
  died on the rate limit or a killed runner, and attempts in flight). Change clock: 7
  rows; subsystems_touched, test_files_changed and dependency_delta are None on every
  row because telltale.repo.commit carries no path list (carried).
- With c_min 16 and k_min 20, neither lineage clock can reach 20 windows yet (23 - 16
  - 1 + 1 = 7 origins at H = 1). W3-E08 will say "not assessable" for them with the
  counts, and run the placebo on the request-clock pairs E07 already backtested.

- After #31, `telltale sessions --link-commits` on the home store linked 4 more
  commits, each with its per_file list, and the change clock still reported the three
  path columns unknown on every row: the observations were appended and the
  activities never rebuilt. Rebuilding the two captures by hand filled 4 of 19 rows
  (coverage partial). Fixed as #33: link_commits rebuilds the capture when it linked
  anything, the same rule the launcher follows at capture end. The change clock on the
  home store now has 19 rows, 4 with the path columns known; the rest were recorded
  before #31 and stay unknown by design (observations are immutable).

- On the five E05 re-run captures both test runs were piped into `tail`, so after #34
  fail_to_pass_cycles is 0 at coverage partial with the warning "2 of 2 verification
  runs have a masked exit status" instead of 0 at coverage observed. The E05 write-up
  still quotes the old coverage word for two measures (carried: a doc fix).
- W3-T2 measured that design 6.12's own suggested fixture for "conditional prediction"
  (a multiset-function forecaster on i.i.d. data) never clears delta: best ratio to the
  best baseline 0.932 over 13 distribution families and 8 seeds. The contract test uses
  a different construction; the amendment records the measurement.
- Neither the ablation nor the candidate protocol has yet been exercised against a
  forecaster that reads covariates: the baselines and the stub read only the target,
  so A, B and C score identically and both runners warn. The first meaningful run needs
  the TimesFM adapter to consume past-future covariates (W6-T1).

## Process facts

- Two implementers running in parallel both edited cli.py (W3-T1) and both appended a
  wave 3 amendment block to 01-design.md (W3-T0, W3-T1). The orchestrator resolved
  each conflict in the verify worktree, ran every gate, and pushed the merge commit to
  the task branch before the squash merge.
- Every implementer session of this row was killed once by the subscription rate limit
  and resumed as the next attempt with `--resume`; the E05 re-run had finished its five
  sessions before its session died, and the resumed attempt wrote the report from the
  store.
- W3-T0's session reported destroying uncommitted work with `git checkout HEAD --` on
  three files and reconstructing it; the timings were re-verified afterwards. The
  lesson goes into the next briefs: commit before any branch toggling.

## Owner decisions requested (answered 2026-09-02: "APPROVED for all 3")

E08b is dispatched with the amendment pre-registered in its brief; W4-T1 is dispatched
and W4-T2 waits for a slot; the backup was deleted.

1. The placebo-validity rule. As implemented (W3-T2, before E08 ran), an invalid placebo
   withholds every label, including "baseline sufficient". Options: (a) keep it: the lab
   says nothing about output_tokens on the request clock until a target with recency
   exists; (b) pre-register an amendment as experiment E08b: the true-order comparison
   alone may earn "baseline sufficient", and the placebo gates only the two positive
   labels; re-run the 38 rows under it (about 45 minutes of CPU, no sessions).
   Recommendation: (b), because the baseline comparison is a paired comparison on the
   same windows and does not use the control, and because the honest sentence "the
   model does not beat rolling_median here" is what both experiments measured. Either
   way, no target on the request clock shows recency, which is a finding to carry into
   the target registry (spec 15: a target with no recency cannot be a forecasting
   target).
2. Wave 4 dispatch: W4-T1 probe runner and correctness scoring, W4-T2 repository work
   profiles (both fake-agent only), then the session requests for E09 (6 probes on
   this repository, 3 repetitions at pilot), E10 (one AGENTS.md intervention, 5 per
   arm) and E12 (H1 blinded diagnosis: 6 captured build sessions, two reviewer
   sessions) with pilot measurements before each.
3. The pre-remediation store backup (~/.telltale/backup/, 1.43 GB, holds the old bytes)
   can be deleted once you are satisfied with the resanitize result.

Carried from earlier gates: the broken ~/.codex/hooks.json; the social preview upload.
