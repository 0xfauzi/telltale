# W3-V: wave 3 verification

Verifier session, fresh context, branch `task/W3-V` from `main` at 80c1a11 (W3-E08
merged). Every VERIFY line of the six wave 3 briefs was re-run from this checkout, and
every OUTCOME claim was attacked with the cheapest input that would show it false.
Nothing in `src/` or `tests/` was edited on this branch.

## Result

**62 VERIFY assertions run: 58 pass, 4 fail.** One assertion for each row of the seven
tables below; several rows bundle more than one command, and every command and its output
is named in the evidence column.

The four failures:

1. `telltale vector` on the copy is 1.00 to 1.04 s against an acceptance of "under 1.0 s"
   (W3-T0). The code W3-T0 wrote is 0.053 s of that second.
2. W3-T0's claim that the new importer wording "stops 1696 rows on the owner's store
   reading like queue loss" is false for those rows: 1696 still carry the old wording and
   0 carry the new. New imports are correct.
3. `telltale forecast backtest` does not stop saying "placebo not run: label withheld"
   once a placebo exists (W3-T2's brief said "until"), and the row it stores carries no
   label at all.
4. One number in `docs/experiments/E08.md` is no longer findable under
   `experiments/E08/out/`, which is the rule E08 set for itself.

**Three findings that no report states.** One is the defect in `telltale forecast
backtest` above. The other two are gaps in the guard rails rather than wrong numbers.

Nothing in the wave's central claims broke. Every attack failed to move the machinery:
the masked-exit rule, the refused-call rule, the truncation rule, the placebo pairing and
the word refusal all held against inputs built to defeat them.

## Method

- The home store was copied, never opened for writing:
  `sqlite3 ~/.telltale/telltale.db ".backup /tmp/w3v/home/telltale.db"`, 2.223 s,
  1541439488 bytes, taken 2026-09-02 23:15. That copy holds **3224 captures and 1070877
  observations**; W3-T0 measured on 3200 and 1044858, W3-E08 on 3223.
- Every command that writes ran under a temporary `TELLTALE_HOME` under `/tmp/w3v/`.
  Nothing was written under `~/.claude` or `~/.codex`.
- The only agent that ran is `tests/integration/fake_agent.py` and one crafted stream
  fed through the real launcher. No tokens were spent.
- Break-and-restore needs a broken tree, and this branch may not have one. Each break was
  applied in a throwaway git worktree at `/tmp/w3v/break` (`git worktree add --detach`),
  the named test run there, and the file restored with `git checkout --`. That worktree
  ended clean (`git status --short` empty).

---

## Brief W3-T0: reads that scale with the capture; refused calls are not test runs

| VERIFY line | Result | Evidence |
|---|---|---|
| `time uv run telltale vector <capture>` on the copy, under 1.0 s (report: 0.85 s; gate report: 0.92 s) | **FAIL** | 7 runs on a quiet machine (load 1.56, no other process above 3 percent CPU): `real 1.01 1.00 1.01 1.04 1.00 1.00`, first run 4.50 s cold. In process: 0.948, 0.969, 0.941 s. The acceptance was "under 1.0 s"; measured today it is 1.00 to 1.04 s. See the attribution below: the code W3-T0 changed is 5 percent of that. |
| The 36-capture timing, within 2x of 245 ms | PASS (not like for like) | 36 real launcher captures of the fake agent, 828 observations: `real 0.13 0.11 0.10 0.11 0.11`. W3-T0 measured 0.030 s on a 30708-observation store of the same capture count. Both are inside 2x of 0.245 s. My store is 37x smaller in observations, so this is a weaker reproduction than W3-T0's and I say so rather than implying otherwise. |
| The query plans, after | PASS | `EXPLAIN QUERY PLAN` on the copy, byte for byte as the report pasted them: typed read `SEARCH observations USING INDEX obs_by_type_capture (observation_type=? AND capture_id=?)` plus `USE TEMP B-TREE FOR ORDER BY`; `observations_of_type` `SEARCH ... (observation_type=?)`; unfiltered read still `SEARCH ... USING INDEX obs_by_capture (capture_id=?)`; typed activities read `SEARCH activities USING INDEX activities_by_capture`. |
| The observations DDL | PASS, with one change the report predicted | `.schema observations` on the copy shows `obs_by_capture`, `obs_by_session`, `obs_by_type_capture` and **no `obs_by_type`**: W3-T3's `DROP INDEX` has already run on the owner's store. |
| `telltale show` on one E05 pilot capture on the copy after `telltale rebuild`: agent_test_runs 0, refused_tool_calls 3 | PASS | All five, after rebuild: `agent_test_runs 0 failed_test_runs 0 fail_to_pass_cycles 0 refused_tool_calls 3`. Before the rebuild the same five read `agent_test_runs 3 failed_test_runs 3 refused_tool_calls null`, which is the defect as stored. |
| `telltale show` on a fake-agent capture: `cache_read_tokens` not null | PASS, number moved | `usage.cache_read_tokens 21000`, `usage.cache_creation_tokens 300`. The report recorded 15000. `fake_agent._usage` sets `cache_read_input_tokens = 1000 * turn`, and W3-T3 reordered the calls, so the turn count and therefore the total changed. The claim (not null on a stream-only capture) holds. |
| `telltale run --provider claude -- python tests/integration/fake_agent.py --output-format stream-json --deny`, then show | PASS | Through the real launcher: `work.refused_tool_calls 1`, `verification.agent_test_runs 0`, `verification.failed_test_runs 0`. Timeline: one `tool_call` named `uv run pytest` with outcome `-`. `telltale explain` shows `outcome: refused`, `executed: false`, sourced on the `claude.stream.system.permission_denied` observation, coverage `observed`. |
| The importer's new wording | PASS forward, **FAIL on the store** | A fresh import of `fixtures/sources/claude/2.1.257/transcript` writes `importer: unparsed line kind claude.transcript:attachment: 1 ...`. But on the copy of the owner's store, `select count(*) from diagnostics where kind='dropped' and detail like 'importer: unparsed line kind%'` is **0**, and exactly **1696** rows still carry the old form (`claude.transcript:attachment x2`). The OUTCOME sentence "which stops 1696 rows on the owner's store reading like queue loss" is false for those rows: diagnostics are historical and were not rewritten. The fix works for every import from now on. |
| The 22-entry vector is unchanged and `refused_tool_calls` is not in it | PASS | `telltale vector` prints 22 metric rows and `grep -c refused_tool_calls` is 0. |
| `uv run pytest -m integration` | PASS | 213 passed, 2 deselected in 81.92 s. |
| Every gate | PASS | See the gate set below. |
| Break and restore (1): remove the type filter, the timing test fails | PASS | `cohorts._payloads` reading `store.observations(capture_id)` untyped: `FAILED test_the_cohort_scan_does_not_slow_down_when_the_captures_get_bigger`, `assert 0.04418766702292487 < (0.013527542003430426 * 2.0)`. Restored: 1 passed. |
| Break and restore (2): is_error as the only route, the refused test fails | PASS | Removing the `denied is not None` branch of `activities_tools._tool_outcome`: `FAILED test_a_refused_call_is_not_a_test_run` (`assert 0 == 1`) and `FAILED test_a_refused_read_is_not_a_file_read`. Restored. |
| Break and restore (3): USAGE_KEYS only, the fake-agent usage test fails | PASS | `correlate.usage` returning the OTel value alone: `FAILED test_a_stream_only_capture_reports_its_cache_counters`, `assert None is not None` on `cache_read_tokens`. Restored. |

### Where the vector second goes now

The acceptance is missed, and the code W3-T0 wrote is not why. `cProfile` on the copy,
quiet machine, 1.048 s total:

```
0.490 s  store_reads.captures()          one call, a view over observations
0.497 s  cohorts._by_metric -> evidence  30 calls, one per metric of the cohort
0.053 s  cohorts._members                72 cohort_keys calls
```

`_members` is the thing the brief was about and it is 5 percent of the command. The store
carries 128937 evidence rows and 3224 captures today. This is a measurement, not a
regression in the fix.

---

## Brief W3-T1: attempt and change clocks; `telltale outcome`

| VERIFY line | Result | Evidence |
|---|---|---|
| `series build --clock attempt --repo <repo_id>` on the copy | PASS, counts moved | `ser_924d87859a046bc34857d9ec clock attempt 29 rows`, exit 0. Report: 22 rows. Gate report and W3-E08: 23 and 29. The lineage grew as the wave ran. |
| verification_passed and accepted all None ("no outcomes exist yet") | PASS as a statement about the code, superseded by fact | Both columns now read `partial` with 7 nulls: 22 of 29 attempts carry an outcome, because the merge protocol started posting them (protocol 7.3 item 5). Read back row by row: 22 rows with `accepted 1`, 7 with `None`. No row is zero-filled. `review_fail_count` is `unavailable` on all 29: no `adversarial_review` outcome has ever been posted. |
| `series build --clock change --repo <repo_id>`; the count and the low_confidence count | PASS, counts moved | `ser_da5d456533d444c544f80003 clock change 25 rows`, `low_confidence rows 0`. Report: 5 rows, 0 low confidence. W3-E08: 23 rows. |
| `series check <id>` ok on both | PASS | `uv run telltale series check ser_924d87859a046bc34857d9ec` -> `ok`, exit 0. Same for `ser_da5d456533d444c544f80003`. |
| `telltale outcome --kind merge_decision --status merged --task-id W2-T8 --attempt 1`, then accepted 1 on that row | PASS | `outcome merge_decision merged recorded on cap_01M1HKTJB8C34SPHQYAQMHGF4W`, exit 0. Rebuilding the series gives the same `series_id` (`ser_924d87859a046bc34857d9ec`), because that attempt already carried the outcome; row 3, which is that capture, reads `accepted 1`. |
| The refusal when nobody carries the attempt | PASS | `telltale outcome --kind merge_decision --status merged --task-id NOPE --attempt 9` -> exit **2**, `telltale outcome: no capture of this repository carries NOPE attempt 9. 29 attempt(s) read: W1-E04/1, ... W3-T4/1`. The report showed 21 attempts read; 29 now. |
| `uv run pytest -m integration` | PASS | 213 passed. |
| Every gate | PASS | Below. |
| Break and restore: a change row reading a later activity, check names the row | PASS | `series._late`'s `position > meta.row_end_ts` replaced by `False`: `FAILED test_a_change_row_reading_a_later_activity_is_caught_by_check`, `assert []`. Restored. |
| Break and restore: drop the duplicate refusal | PASS, and one half of it is untested | The report's break is two edits. With **both** (`series_lineage._identity` returning `sorted(named)[0]` and `cli_outcome` accepting `len(matched) < 1`): `FAILED test_an_attempt_two_captures_carry_is_refused_naming_both`, `assert 0 == 2`, which is the report's own output. With **only** the `_identity` edit the suite is green. `grep -rn "two attempt identities" tests/` returns nothing: `_identity`'s own duplicate refusal has no test. |

---

## Brief W3-T2: placebo, decision rule, ablation, candidate protocol

| VERIFY line | Result | Evidence |
|---|---|---|
| `forecast placebo` on the synthetic 200-row request series: persistence worse under every seed, the label with the inequalities and the constants | PASS, reproduces to the last digit | `uv run python tests/integration/synthetic_series.py --db ... --clock request --rows 200 --seed 1` gives `ser_a2688e3b16a09f1a4882c3c8`, the report's own id. `forecast placebo` prints the same 11-row table the report pasted: true persistence 90.4338, `10 of 10 placebo runs worse -> valid`, `decision: baseline sufficient`, `E_M 90.4338 E_B 90.4338 E_P 379.2794 W_MB 0.5 W_MP 0.8971`, both inequalities with LHS and RHS, both halves, and every constant. 11 forecast_run_ids. Exit 0. |
| `forecast ablate` on the synthetic 60-row change series: A, B, C paired per window, the verdict, the winner's placebo beside it | PASS, reproduces to the last digit | `ser_ece0da60f64066f5a3e8db42`, the report's id. `origins common to A, B and C: 44`, all three MAE_MEAN 1.0682, `verdict: C diagnostic only`, both rule inequalities with values, the warning that no forecaster read a covariate, then the winner's full placebo with `10 of 10 placebo runs worse -> valid` and `decision: baseline sufficient`. 14 run ids. Exit 0. |
| `forecast backtest` prints "placebo not run: label withheld" until a placebo exists | **FAIL** | It prints it unconditionally. After running `forecast placebo` on `(ser_a2688e3b16a09f1a4882c3c8, fresh_input_tokens, H=1)` and storing 10 placebo rows, `forecast backtest` on the same triple still prints `decision: not assessable (placebo not run: label withheld ...)`. `cli_forecast.py:179` prints the constant with no lookup. See the finding below. |
| A report string containing "would" raises | PASS | Through the real renderer (`backtest.report` on a real run with one assumption appended): refused for `would`, `impact`, `impacts`, `cause`, case-insensitively. `because`, `causal`, `impactful`, `wouldn't` and the mandatory candidate sentence all pass. |
| pytest, contract test 3 passes, name the tests | PASS | 21 tests across `tests/integration/test_forecast_decision.py` (13) and `test_forecast_ablation.py` (8), all green inside the 213. The five that are contract test 3 by name: `test_block_shuffle_keeps_every_row_and_costs_persistence_its_score`, `test_a_stub_worse_than_persistence_is_baseline_sufficient`, `test_a_multiset_stub_that_beats_the_baselines_is_a_conditional_prediction`, `test_a_placebo_that_leaves_persistence_alone_is_not_assessable`, `test_a_report_that_claims_a_cause_raises_and_the_sentence_passes`. |
| Every gate | PASS | Below. |
| Break and restore: shuffle the test rows too, test (d) fails | PASS, harder than the report said | Shuffling the whole Series rather than the window context: **5 failed, 8 passed**, including `test_the_shuffle_reaches_the_context_and_nothing_else` and `test_one_score_two_labels_and_the_placebo_is_the_whole_difference`. The report recorded 4. Restored. |
| Break and restore: drop the per-half clause | PASS | `decide._label`'s `halves` forced True: `FAILED test_the_half_clause_refuses_a_label_the_whole_range_earns`, `+ temporal evolution`. Restored. |
| Break and restore: let `attempts_to_land` through as a candidate target | PASS | The `if target == CANDIDATE_FORBIDDEN: raise` removed: `FAILED test_the_candidate_protocol_refuses_the_target_known_at_merge_time`, `DID NOT RAISE Refused`. Restored. |

---

## Brief W3-T3: the reducer says unknown where it cannot know

| VERIFY line | Result | Evidence |
|---|---|---|
| fake agent `--pipe` through the launcher, then show: agent_test_runs 2, failed_test_runs coverage partial with the warning, timeline outcome `-` not `ok` | PASS | Real `telltale run` in a temporary git repository: `agent_test_runs 2, failed_test_runs 1, fail_to_pass_cycles 0`, warning "1 of 2 verification runs has a masked exit status ...". Timeline: `uv run pytest _ >& _ | tail -50` outcome `-`, `uv run pytest` outcome `failed`. `telltale explain failed_test_runs` gives coverage `partial`. |
| fake agent `--deny-read`, then show: unique_files_read 0, refused_tool_calls 1 | PASS | `unique_files_read 0`, `unique_files_read_before_first_edit 0`, `directories_traversed 0`, `read_to_edit_ratio 0.0`, `refused_tool_calls 1`, `agent_test_runs 0`. Timeline: one `tool_call` named `answer.txt` with outcome `-`, and no `file_read` row. |
| The five W2-E05 re-run captures on the copy, rebuilt: fail_to_pass_cycles 0 at coverage partial with the count | PASS, count reproduces exactly | All five: `agent_test_runs 2 / observed`, `failed_test_runs 0 / partial`, `fail_to_pass_cycles 0 / partial`, with both warnings, the first reading "**2 of 2** verification runs have a masked exit status". Timeline of `cap_01M1HQJD1PD1B6QKETVDHWDM6N`: both `uv run pytest _ >& _ | tail -N` rows read `-`. |
| The five W2-E05 pilot captures still report three refusals | PASS | `refused_tool_calls 3` on all five after rebuild. |
| `series build --clock request <codex fixture>`: request_duration_ms with the coverage word and column_report's reason | PASS on the word, **the reason is not printed** | Codex S1 replayed through the real receiver into a temporary store, then `series build`: `request_duration_ms unavailable 7`, `compaction_before unavailable 7`, `env_changed unavailable 7`, everything else `observed 0`. `series.column_report` carries `reason: "no value in any row"` for those three, but `cli_forecast._COLUMN_COLUMNS` is `("column","unit","role","coverage","nulls")`, so the CLI never prints it. W3-T3 disclosed this in its UNSURE list; it is still true at HEAD, and W3-T4 disclosed the same gap from the other side. |
| The `refuse` policy no longer stops on that column | PASS | `series build --clock request --capture codex-S1 --policy refuse` exits 0 and builds 7 rows. |
| The selfcheck timings | PASS | Nine runs: `wall_ms 8.6 8.5 8.4 18.9 19.7 19.1 8.4 8.5 8.4`, `selfcheck PASS` every time. Same bimodal 8.4/19 spread the report recorded, and the same conclusion: the number moves nothing, because it times the worker threads enqueuing. |
| The `DROP INDEX` migration at open | PASS | A store carrying only `obs_by_type`, opened once by `Store.open`: before `obs_by_type`; after `obs_by_capture obs_by_session obs_by_type_capture activities_by_capture`. |
| pytest and every gate | PASS | Below. |
| Break and restore: the masked branch | PASS | The early `return` after `built.put("exit_masked", True)` removed: 4 failed, 209 passed. `test_a_masked_exit_status_is_unknown_and_not_a_pass`, `test_s1_verification_block_is_the_shape_the_product_exists_for`, `test_timeline_matches_golden[claude-S1]`, `test_the_counters_add_up_to_the_activities_they_count`. Restored. |
| Break and restore: refused file tools | PASS | `_tool_type` back to `if category in VERIFICATION and not refused`: `FAILED test_a_refused_read_is_not_a_file_read`, `assert 1 == 0`. Restored. |
| Break and restore: the column coverage rule | PASS | The `elif` of `series.blank_unobservable` removed: `FAILED test_a_column_with_no_value_in_any_row_is_never_observed`, `+ observed`. Restored. |
| Break and restore: the DROP INDEX line | PASS | Removed from `schema.py`: `FAILED test_an_existing_store_loses_the_index_nobody_reads`, `assert 'obs_by_type' not in {...}`. Restored. |

---

## Brief W3-T4: commits carry their paths; the change clock fills its last three columns

| VERIFY line | Result | Evidence |
|---|---|---|
| A temporary repository, three commits linked through the launcher, one touching `tests/` and one touching `pyproject.toml`: the three columns filled with the hand-computed values | PASS | Three real `telltale run` captures, each committing. git: `8994e76 src/a.py`, `0629bca tests/test_a.py`, `c31bcbf pyproject.toml + src/a.py`. By hand: 1/0/0, 1/1/0, 2/0/1. The stored change series reads exactly that. Stored payloads carry `per_file: [{"additions":1,"deletions":0,"path":"src/a.py"}]` and so on. |
| On a copy of the home store, the three columns None on old rows with coverage partial and the reason | PASS | 25 rows, `subsystems_touched / test_files_changed / dependency_delta` all `partial` with **19 nulls**, and the cohort carries `unknown_columns` naming "the commit carries no per_file list: recorded before W3-T4, or a merge, ... or a list the 8 KB payload bound truncated". W3-E08 measured 23 rows with 19 nulls on the same store two hours earlier. |
| `strings <db> \| grep -c "<the absolute temp path>"` is 0 | PASS | 0 for `/tmp/w3v/t4/repo`. `"path":"src/a.py"` appears 5 times. Paths are repo-relative. |
| The unknown_field counts before and after | PASS, reproduces exactly | Eight E01 Claude 2.1.257 scenarios replayed through a real receiver: 171 `claude.otel.metric` observations, `service_version` stored on 171 and dropped on 0, `terminal_type` stored on 171 and dropped on 0, `unknown_field` diagnostics 127 of which **0** name either. W3-T4's "AFTER" column, number for number. |
| pytest and every gate | PASS | Below. |
| Break and restore: the dependency rule | PASS | `"pyproject.toml"` removed from `series_paths._MANIFESTS`: `FAILED test_the_three_path_columns_are_read_off_the_commits_own_paths`. Restored. |
| Break and restore: the truncation rule | PASS | The `per_file_truncated` check removed: `FAILED test_a_truncated_path_list_leaves_the_three_columns_unknown`. Restored. |

---

## Brief W3-E08: temporal validity on captured data

| VERIFY line | Result | Evidence |
|---|---|---|
| The pilot pair's wall time and the projection | PASS, wall differs | The pilot pair reproduced end to end on my own copy: `uv run --extra forecast telltale forecast placebo --series ser_54409247e77f5ae6f70f4e02 --target output_tokens --horizon 1 --forecasters persistence,rolling_median,rolling_mean,local_drift,echo,timesfm --model timesfm --device cpu`, **221.62 s** wall. E08 recorded 190.23 s for the full invocation and 164.55 s for the ten placebo runs alone against a stored true run; mine had to compute the true-order run as well. The projection E08 wrote (49.6 minutes against a 90-minute stop) is against a measured total of 43.22 minutes, which is internally consistent in `out/summary.json`. |
| `forecast placebo` output for one pair on the copy | PASS, byte for byte | Rebuilding `cap_01M1HE3XS4E3S2XTSNB99C7WQT` on my copy gives `ser_54409247e77f5ae6f70f4e02`, E08's own series id. Every decision number matches the stored `out/cap_01M1HE3XS4E3S2XTSNB99C7WQT/output_tokens-H1.json` to full float precision: `E_M 689.7284479809415`, `E_B 670.3471337579617`, `E_P 661.5382245300682`, `W_MB 0.4968152866242038`, `W_MP 0.5605095541401274`, `n_windows 157`, both inequalities, both halves. |
| The persistence validity line per seed | PASS | `validity (persistence must be worse under every placebo): true 932.3439, 2 of 10 placebo runs worse -> INVALID`, with the ten per-seed persistence values in the table (786.83, 674.30, 691.73, 1059.70, 1059.70, 698.92, 686.06, 711.46, 802.99, 802.99). The central finding of E08 reproduces on a fresh copy in a fresh environment. |
| `out/summary.json` label counts equal the table's | PASS | 38 pair files under `out/`, every one carrying `decision.label` and a non-empty `decision.inequalities`. Counted: `{"not assessable": 38}`. `summary.json` `label_counts`: `{"not assessable": 38}`. |
| Every number in E08.md found in an out/ file | **FAIL, by one number** | `uv run python experiments/E08/check_numbers.py` on the merged tree: `1344 numbers, 16059 distinct numbers in out`, `matched 1314, unmatched 30`. Of the 30, 22 are the checker's own output quoted inside E08.md and 7 are the ones E08.md explains (a spec section number and four fragments of a commit sha read as decimals, and two of E07's own measurements). The eighth is new: **176.249** at line 106, "of which the rebuild of 3223 captures is 176.249 s". `grep -rl "176.249" experiments/E08/out/` finds nothing. That number lived in `census.json`, which E08 deliberately did not re-include (1.2 MB against the 500 KB `check-added-large-files` limit). The write-up's own rule, that every number is traceable to a file under `out/`, is broken by one number in the merged repository. |
| The lineage clocks are counts, not failures | PASS, counts moved | `out/lineage.json`: attempt clock 29 rows, `registered_attempt_targets: []`, 13 planned origins at H = 1 against k_min 20; change clock 23 rows, 7 planned origins at H = 1 and 1 at H = 4. My copy: attempt 29 rows, change 25 rows. Both still below k_min by the same formula. |
| pytest and every gate | PASS | Below. |

### The E08 placebo, attacked directly

E08's whole result rests on one thing: that the shuffle moved only the context. I checked
it from the stored rows rather than from the report. Reading the 11 `forecast_runs` rows
my own re-run wrote for that pair and comparing `origin`, `ctx_start`, `n_ctx`,
`horizon`, `actual` and `last_context` window by window:

```
placebo runs paired with the true run: 10; differing in origin/actual/last_context: 0
n_windows in each: 157
```

Ten placebo runs, 157 windows each, and not one differs from its true-order twin in any
field except the context. The finding is not an artefact of a shuffle that reached too far.

---

## The orchestrator's fix (#33): `sessions --link-commits` reduces the capture it appends to

| Check | Result | Evidence |
|---|---|---|
| The fix works through the real CLI | PASS | Temporary repository, one `telltale run` leaving the tree dirty, then a commit made after the capture ended. `telltale timeline <capture>` before: 0 rows matching `repo_commit`. `telltale sessions --link-commits --limit 8` prints `linked 1 commits in this repository`. `telltale timeline` after: one `repo_commit` row. No manual `telltale rebuild` in between. |
| The regression test fails on the un-fixed code | PASS | The five-line `if found: store.rebuild(capture_id)` removed from `launch_commits.link_commits`: `FAILED test_link_commits_reduces_the_capture_it_appends_to`, `assert 'repo_commit' in 'TIME TYPE ACTOR ...'`. Restored: 1 passed. |

---

## Break attempts

Every attempt below ran through the real launcher, the real receiver or the real CLI. No
stub was used anywhere.

| # | The attack | What happened |
|---|---|---|
| 1 | **A test command with `\|\|` after it, and one with `;`.** A crafted Claude stream carrying four `uv run pytest` variants, each with a tool_result that lies (`is_error: false` while the output says "1 failed"), fed to the real launcher as `telltale run --provider claude -- python -c "..." --output-format stream-json`. | The rule held exactly. `uv run pytest \|\| true` and `uv run pytest ; echo done` are `exit_masked: true`, outcome `-`, `success` unset. `uv run pytest && echo ok` and bare `uv run pytest` read `ok`. `agent_test_runs 4` at coverage `observed`, `failed_test_runs 0` at coverage `partial`, warning "**2 of 4** verification runs have a masked exit status". Nothing was zero-filled and nothing claimed a pass it had not seen. |
| 2 | **A refused Read.** `fake_agent.py --deny-read` through the launcher, in a repository that has a readable file, so "read nothing" is a choice and not an empty directory. | `unique_files_read 0`, `directories_traversed 0`, `refused_tool_calls 1`. The refused call is a `tool_call` that still names `answer.txt`. No exploration number counts it. |
| 3 | **A commit whose per_file list is truncated.** A 101-file commit (one past `launch.PER_FILE_MAX` = 100) through the real launcher, in the same repository as the three hand-checked commits. | `files_changed 101` (the true count, kept), `per_file` 100 entries, `per_file_truncated 1`, payload 5512 bytes. The change clock reads `subsystems_touched None, test_files_changed None, dependency_delta None` on that row alone, the three columns drop from `observed` to `partial` with 1 null, and the cohort names the reason. The three known rows are untouched. A prefix of the paths never became a confident answer. |
| 4 | **A series with an all-None column.** The Codex S1 fixture replayed through the real receiver: `request_duration_ms` rides a capability Codex reports, and no Codex surface carries a per-response duration. | `unavailable`, not `observed`, and `column_report` gives the rule that fixed it: `no value in any row`. The `refuse` policy builds rather than stopping. |
| 5 | **A placebo where the true rows moved too.** In the scratch worktree, `placebo.run` shuffling the whole `Series` instead of the window context. | 5 tests failed, including the pairing test and three label tests. Restored. And on the real E08 pair, the stored rows prove the shipped code does not do this (see above). |
| 6 | **A forecast report containing "impacts".** One assumption appended to a real run and rendered by `backtest.report`. | Refused, and so are `would`, `cause` and `impact`, case-insensitively; `because`, `causal`, `impactful` and the mandatory candidate sentence pass. Then I called `backtest.persist` directly with the poisoned run: **it stored**, `fc_01M1J47GNJPXKRYCR4FZTE59KT`. The word refusal guards the renderer, not the store. Every CLI path renders first (`cli_forecast.py` lines 172/173, 224/225, 268/269) and `test_the_renderer_refuses_before_it_prints_or_stores` asserts that ordering, so the CLI cannot do it. A future caller of `persist` can. |
| 7 | **`block_shuffle` losing or copying a row.** Block sizes 1, 2, 4 and 7 crossed with seeds 0 to 4 on 200 two-column rows. | The sorted multiset is identical every time, the length is preserved, a fixed seed is deterministic, and `block=0` raises `ValueError: block 0 is not a positive number of rows`. |
| 8 | **A `forecast backtest` whose pair already has a placebo.** Ten placebo rows stored for `(ser_a2688e3b16a09f1a4882c3c8, fresh_input_tokens, H=1)`, then `forecast backtest` on that triple. | It printed `placebo not run: label withheld`. That is false. See the findings below. |
| 9 | **Two captures carrying one (task_id, attempt).** Covered by `test_an_attempt_two_captures_carry_is_refused_naming_both` and by the live refusal on the copy: `telltale outcome ... --task-id NOPE --attempt 9` exits 2 and lists the 29 attempts it read rather than inventing one. | Held. |

---

## Findings no report states

### 1. `telltale forecast backtest` stores a forecast with no label, and prints a false reason

`cli_forecast.forecast_backtest` (src/telltale/cli_forecast.py:179) prints
`decision: not assessable (placebo not run: label withheld ...)` unconditionally. It never
looks for a stored placebo. Measured: after `forecast placebo` wrote 10 placebo rows for
`(ser_a2688e3b16a09f1a4882c3c8, fresh_input_tokens, horizon 1)`, `forecast backtest` on
that exact triple still said "placebo not run".

Worse, the row it writes carries no label at all. Of the 8 true-order rows my session
created through the CLI, **6 have `decision` NULL**:

```
target              ordering  decision NULL  count
attempts_to_land    true      yes            3     (the ablation's A, B and C variants)
attempts_to_land    true      no             1     (the winner's placebo pair)
fresh_input_tokens  true      yes            2     (forecast backtest)
fresh_input_tokens  true      no             1     (forecast placebo)
output_tokens       true      yes            1     (forecast backtest)
```

The printed label is honest and never over-claims. The stored row is where the exit
criterion lives, and there the number sits with nothing beside it.

### 2. The word refusal is a renderer guard, not a store guard

`backtest.persist` accepted a run whose assumptions contained "impacts". The CLI cannot
reach that state because it renders first, and a test holds the ordering. An experiment
runner that calls `persist` directly can. Design 6.12 and ADR-014 talk about what the
package may present; whether that includes what it may write is the owner's reading.

### 3. `series_lineage._identity`'s duplicate refusal has no test

Making it return `sorted(named)[0]` instead of refusing leaves the whole suite green. The
CLI-level duplicate refusal in `cli_outcome` is tested; the compiler-level one, which is
what stops a capture with two attempt identities from silently becoming one row, is not.
"Duplicate is not one" is one of the two failure modes AGENTS.md names by name.

---

## The gate set

Run once, on this branch, in this order, with `uv sync` (dev group, no extras) as
AGENTS.md prescribes.

```
uv run pytest -m integration    213 passed, 2 deselected in 81.92s
uv run mypy .                   Success: no issues found in 81 source files
                                (note: unused section(s): module = ['pyarrow.*'])
uv run ruff check .             All checks passed!
uv run ruff format --check .    160 files already formatted
uv run deptry .                 Success! No dependency issues found.
uv run pre-commit run --all-files   every hook Passed; two Skipped (no files)
```

Every hook in the last line, named, because they are where the invariants live:
`forbid em dashes`, `no file grows past 800 lines`, `cyclomatic complexity does not
grow`, `mocks and stubs stay in tests`, `the collector never imports the model stack`,
`derived rows are written through store.py alone`, `an observation is updated in store.py
alone`, `AGENTS.md and CLAUDE.md have not drifted`, `tests live under tests/integration/
only` (skipped, no test file changed), `shellcheck` (skipped, no shell file).

---

## The wave 3 exit criterion (spec 21 v0.3)

> every forecast claim labelled temporal evolution, conditional prediction, baseline
> sufficient or not assessable, with the inequalities shown

**Verdict: met everywhere except `telltale forecast backtest`, which fails both halves.**

Every place a forecast number is printed or stored, and what sits beside it:

| Where | Label? | Inequalities? |
|---|---|---|
| `telltale forecast placebo` (stdout) | Yes, one of the four, with the reason | Yes: a `TEST / LHS / RHS / HOLDS` table, a per-half table, and every pre-registered constant printed on one line |
| `telltale forecast placebo` (the stored true-order row) | Yes, in the `decision` column | Yes, `decision.inequalities` and `decision.halves` |
| `telltale forecast placebo` (the 10 stored placebo rows) | No, `decision` NULL | No |
| `telltale forecast ablate` (stdout) | Yes: the ablation verdict with its own two inequalities, then the winner's placebo decision in full | Yes for both |
| `telltale forecast ablate` (the 3 stored variant rows) | **No, `decision` NULL** | No |
| `telltale forecast ablate` (the winner's stored true-order row) | Yes | Yes |
| **`telltale forecast backtest` (stdout)** | A label, `not assessable` | **No. No inequality is printed at all, and the reason given is false whenever a placebo exists.** |
| **`telltale forecast backtest` (the stored row)** | **No, `decision` NULL** | **No** |
| `telltale forecast readiness` | Not applicable: it prints preflight checks, not forecast error | Not applicable |
| `experiments/E08/out/**/output_tokens-H*.json`, 38 files | Yes, all 38 | Yes, all 38, with both sides and both halves |
| `experiments/E08/out/summary.json` | Yes, `label_counts` | Through the per-pair files it names |
| `experiments/E08/out/lineage.json` | Yes, on the ablation's placebo | Yes, in the captured `printed` text |
| `docs/experiments/E08.md` | Yes, a `label` column on every row of both measured tables | The rule is quoted at lines 33 to 35, the operands (E_M, E_B, E_P, W_MB, W_MP) are columns of every table, and one full `TEST / LHS / RHS / HOLDS` block appears verbatim at lines 285 to 286. Judgement: this satisfies "shown", though not per row. |
| `docs/design/04-forecast-reading-guide.md` | Yes, a worked example per label | Yes, with values on both sides |
| `src/telltale/report.py` and `telltale show` | No forecast number is printed there (`grep -n forecast src/telltale/report.py` is empty) | Not applicable |

The two rows in bold are the gap. The 10 placebo rows and the 3 ablation variant rows
carrying no label are defensible: a placebo is a control for a run and the ablation
variants are inputs to a verdict, and in both cases the row that carries the claim does
carry the label. The `forecast backtest` row is not defensible on that argument: it is a
run somebody asked for, its metrics are the answer, and nothing stored beside it says how
strong a claim they support.

---

## UNSURE OR NOT DONE

- **I did not re-run the whole E08 experiment.** It is 43.22 minutes of TimesFM on CPU
  by its own measurement, and one pair costs about 190 to 220 s. I re-ran the pilot pair
  end to end and got E08's stored numbers to full float precision, and I re-derived the
  38 labels and the summary counts from the committed JSON. The other 35 pairs are
  checked against `out/`, not recomputed. What that leaves unverified is whether the
  other 35 stored files were produced by the code at HEAD; the pilot's exact match across
  two environments is strong evidence that they were, and it is evidence rather than proof.
- **The 36-capture timing is not a like-for-like reproduction.** W3-T0 grew its 36-capture
  store to 30708 observations to match W2-T4's shape. Mine has 828. Both are well inside
  the 2x bound, but my number does not test the same thing.
- **I could not attribute the `telltale vector` acceptance miss to a change.** It is
  1.00 to 1.04 s today against an acceptance of "under 1.0 s" and a reported 0.85 s. The
  profile says the cohort scan W3-T0 rewrote is 0.053 s of it, and the remaining second
  is `captures()` and 30 per-metric evidence reads. Whether the store simply grew past
  the number, or something between W3-T0 and HEAD slowed one of those two reads, needs a
  bisect I did not run.
- **`telltale outcome` on the copy appended a real observation.** It was a duplicate of
  one the merge protocol had already posted for W2-T8 attempt 1, so the series id did not
  change. Had it been new, my verification would have altered the data it verified. The
  copy is disposable and the real store was never opened for writing, but the pattern is
  worth naming: the tool a verifier is testing is a tool that writes.
- **I did not test the candidate protocol through a CLI, because there is none.**
  `telltale forecast` has four subcommands and `candidate` is not one of them.
  `forecast/candidate.py` is exercised by tests only. That is consistent with W3-T2's
  brief, which asked for `forecast placebo` and `forecast ablate` and no third command.
- **The Codex duration decision is verified as a coverage word, not as a measurement.**
  W3-T3 says it measured that no Codex surface carries a per-response duration. I
  reproduced the resulting `unavailable` on the S1 fixture. I did not independently
  re-measure the six Codex surfaces to confirm that none of them could carry one.

---

# EXPLAIN FOR THE OWNER

## What a verifier is for

Every task in this build ends the same way: the implementer writes a report saying what
it did, and pastes the commands and the output that show it. That is a good practice and
it is not enough, for one reason that has nothing to do with honesty.

The person who made a change is the worst-placed person to test it. Not because they
lie. Because they test the thing they built, in the state they built it in, on the
machine they built it on, against the input they had in mind. A test written by the
author of a fix passes for the same reason the fix works: they are two expressions of one
idea. If the idea is wrong in a way the author cannot see, both are wrong together, and
the green tick means nothing.

So a wave ends with somebody who did none of the work re-running all of it. Fresh
context. No memory of why a decision was made, which means no inherited belief that it
was right. The job has three parts and they are different.

**Re-run every check the brief demanded, exactly as written.** Not the report's summary
of the check. The check. Sixty-two of them this wave. Most pass, and passing is not
the interesting outcome: what matters is that they were run again, somewhere else, by
somebody with no stake in the answer.

**Measure every number again and put the two side by side.** A report that says "0.85
seconds" is making a claim about a machine on a day. I measured 1.01. Both numbers can be
honest. Printing them together is what lets you see that the acceptance bar the brief set
has been crossed back over, which no single measurement can tell you.

**Then try to break it.** This is the part that is not bookkeeping. For every sentence of
the form "X is now Y", find the cheapest input that would make it false, and feed that
input to the real system. Not to a test. Not to a mock. To the launcher, the receiver,
the command line, the same path a user takes.

That last part deserves an example, because it is where this session spent most of its
time.

## The one claim I tried hardest to break

W3-T3 made a claim about shell commands, and it is the sharpest claim in the wave.

Here is the situation it addresses. A coding agent wants to know whether the tests pass.
It runs a command. Telltale records whether that command succeeded, and everything
downstream depends on it: how many test runs happened, how many failed, whether a failure
was later fixed.

Now the trap. Agents rarely run one program. They run this:

```
uv run pytest 2>&1 | tail -50
```

Two programs. `pytest` runs the tests, and `tail` prints the last fifty lines of the
output. A shell reports the exit status of the **last** program in the chain, and `tail`
succeeds whether it was handed a pass or a failure. So the shell says "fine" about a
command whose first half said "1 failed".

Telltale used to believe it. All five sessions of experiment E05 ran that exact command,
the tests failed, and Telltale recorded zero failed test runs at full confidence. W3-T3's
fix is a rule: when the classified command is followed by `|`, `;` or `||`, the outcome is
**unknown**, and the row says so rather than guessing. When it is followed by `&&`, or by
nothing, the status is the command's own and may be believed.

That `&&` exception is what I attacked. It is the one case where the rule chooses to
trust, and a rule that trusts in one place is a rule with a hole in it if the reasoning is
wrong.

The reasoning is this: `&&` means "run the next thing only if this one succeeded". So if
the tests fail, the chain stops at the failure and the shell reports the failure's own
code. Nothing is masked. That sounds right. I wanted to know whether it was right, or
whether it was a plausible sentence somebody wrote once.

I could not test it with the scripted test agent, because that agent only produces the
shapes it was written to produce, and none of them uses `&&`. So I built the input by
hand: a Claude session transcript with four test commands in it, one per shape.

```
uv run pytest || true        <- || after the command
uv run pytest ; echo done    <- ; after the command
uv run pytest && echo ok     <- && after the command
uv run pytest                <- nothing after the command
```

Every one of them carries a result that **lies**: it says the command succeeded, while
its own printed output says "1 failed, 1 passed". That is exactly what the shell hands
back for a piped failure, and it is what fooled Telltale before the fix.

Then I fed the whole thing through the real launcher, not through a test:

```
telltale run --provider claude -- python -c "..." --output-format stream-json
```

The launcher recorded it, the receiver stored it, the reducer read it, and the timeline
came out like this:

```
verification_run  uv run pytest || true       outcome -
verification_run  uv run pytest ; echo done   outcome -
verification_run  uv run pytest && echo ok    outcome ok
verification_run  uv run pytest               outcome ok
```

The two masked shapes report nothing. The other two report `ok`. And the summary says:

```
agent_test_runs 4          coverage observed
failed_test_runs 0         coverage partial
warning: 2 of 4 verification runs have a masked exit status, so this count covers
only the runs whose outcome a surface stated
```

Four test runs happened, that is certain and the word is `observed`. Zero of them are
known to have failed, that is a weaker statement and the word is `partial`, and the
warning says exactly how much weaker: half the evidence is missing.

So: did I break it? No. But look closely at rows three and four, because that is where the
honest answer lives.

Those two say `ok` about commands whose output says the tests failed. In my crafted
input, that is wrong. But my input is a **lie that a real shell cannot tell.** If
`pytest` had really failed, `&& echo ok` would never have run, and the status would have
been pytest's own failure. I fabricated a combination the world does not produce. The
rule reported it faithfully, which is the correct behaviour: a recorder's job is to say
what the surfaces said, not to second-guess a shell that has never been wrong about this.

The distinction matters and it is worth holding on to. **The rule is not "read the
output and decide".** It is "know when the status you were handed belongs to the command
you are asking about". A recorder that started parsing pytest's printed text to override
the exit code would be guessing, and it would be wrong on the day somebody's test
framework prints the word "failed" in a passing run.

That is what a hard break attempt buys. Not a defect. A precise understanding of what the
rule claims and what it does not, which is the thing a green test cannot give you.

## What did break

Three things, and one of them matters.

**A command stores a forecast with no label attached to it.** The wave's exit criterion
says every forecast claim must carry one of four labels: baseline sufficient, temporal
evolution, conditional prediction, or not assessable. `telltale forecast backtest` prints
`not assessable`, which is correct and cautious. But the row it writes to the database
has an empty label field. So a number sits on disk with nothing beside it saying how much
weight it can hold, and the next reader of that table has no way to know.

The same command also prints a reason that can be false. It says "placebo not run", and I
showed that it says this even when a placebo has been run and stored for that exact
series, target and horizon. The code prints a fixed string and never looks.

**The importer's diagnostic wording fixed the future and not the past.** W3-T0's report
says the new wording "stops 1696 rows on the owner's store reading like queue loss". I
counted those rows on a copy of your store: 1696 of them, still carrying the old wording.
Zero carry the new one. A fresh import writes the new wording correctly, so the fix works.
The rows already on your disk were never rewritten, because Telltale does not rewrite
history. The number in the report is right and the verb is wrong.

**One number in the E08 write-up is no longer traceable.** E08 has a rule it applies to
itself: every number in the write-up must be findable in a file under `experiments/E08/out/`.
It ships a script that checks this. Running that script now, one number fails that has
nothing wrong with it: the 176.249 seconds a rebuild took. It lived in a 1.2 MB file that
the repository's own size limit would not accept, so E08 deliberately left it out, and the
number it supported stayed in the prose. This is small. I report it because the rule E08
set for itself is a good rule, and a rule with one silent exception stops being one.

## The number that moved

`telltale vector` compares one session against similar ones. W3-T0 made it 13 times
faster and set the bar before making the change: under one second on your store. It
reported 0.85 seconds.

I measured 1.00 to 1.04 seconds, seven runs, on an idle machine.

Before reading anything into that, here is where the second goes:

```
0.490 s   listing the captures
0.497 s   reading the stored numbers of the comparison group, thirty times
0.053 s   working out which sessions are in the comparison group
```

That third line is what W3-T0 rewrote, and it is five percent of the command. The fix is
intact and doing its job. Your store has grown from 3200 sessions to 3224, and from
1044858 records to 1070877, and it now carries 128937 stored numbers. The bar was set at
one second and the command is at one second.

I am not going to tell you which of those two reads grew, because I did not measure it
across the intervening commits and guessing would be worth nothing. What I can tell you
is what would settle it: run `telltale vector` at each of the wave's merge commits against
this same copy and read where the line crosses one second. That is a bisect, it is about
twenty minutes, and it is the only thing that turns "the number moved" into "this change
moved it".
