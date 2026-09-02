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
| #32 | W3-T2 | block-shuffle placebo, the four-label decision with every inequality, A/B/C ablation, one-step candidate protocol, `forecast placebo` and `forecast ablate`, the word refusal (cause, impact, would), the owner's reading guide docs/design/04-forecast-reading-guide.md; `forecast backtest` withholds the label until a placebo exists |
| #34 | W3-T3 | a verification run whose chain hands its exit status to another program (`\|`, `;`, `\|\|` after the test command) is `exit_masked` with outcome unknown, and failed_test_runs and fail_to_pass_cycles say so in coverage and warnings; a refused Read or Edit is a tool_call and nothing else; a series column with no value is never observed; the unread obs_by_type index is dropped at open (measured 6.1 percent writer cost, W3-T0 had 16.8); activities.py split |

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
