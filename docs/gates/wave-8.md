# Wave 8 gate: the change clock from git history

Status: complete. Seven code PRs and the experiment merged; verdict below. Written by the orchestrator as each
task merged. Wave 8 is not a spec version gate: it is the wave that gives the change clock
its rows. Before it, the change clock had 54 launcher-linked commits on one repository
against about 1,400 commits the owner landed in 30 days, so H7 and H8 were "not
assessable" for want of rows, not for want of a forecaster (docs/experiments/E11.md).

## Dispatch record

| Task | Attempt | Dispatched | Outcome |
|---|---|---|---|
| W8-T1 import git-history: unlinked change rows with git and check-run outcomes | 1, 2 (Opus; the first stopped on the account's session limit at 12:40pm London and was resumed by `resume-until-done.sh` at 12:47) | 2026-09-06 | PR #65 opened by the session; gate set green on the branch merged with main (394 passed, wall 231 s); merged #65 |
| W8-T2 uncaptured change rows: git-backfilled lineages on the change clock | 1 (killed by process group: the brief named `lines_added`, which is not a registered target; the brief was fixed and cherry-picked into the worktree), 2, 3 (resumed after the same session limit) | 2026-09-06 | PR #66 opened by the session against a hand-written contract for W8-T1's payloads (W8-T1 had not landed); one test failed on the merged tree (below); merged #66 |
| W8-T3 targets with holes run over their known rows; candidate protocol at H = 4 for the lagged rework label | 1 (Opus, 291 turns, 47.74 USD) | 2026-09-06 14:04 | PR #69 opened by the session; merge with main conflicted on the docs index only; gate set green (411 passed, wall 246 s); orchestrator re-ran the VERIFY lines on a fresh copy of the E16 store; merged #69 |
| W8-T5 path-derived commit cells survive the payload bound | 1 (Opus, 98 turns, 7.27 USD) | 2026-09-06 14:47, in parallel with W8-T3 | PR #70 opened by the session; merge with main conflicted on the docs index only; gate set green (414 passed, wall 243 s); orchestrator re-verified deckgen (three path columns observed, 0 nulls); merged #70 |
| W8-F1 flags vary, the candidate block costs rows not runs, advise finds a holed target's run | 1 (Opus, 190 turns, 26.20 USD) | 2026-09-06 15:18, after #69 | PR #71 opened by the session; merge with main conflicted on the docs index only; gate set green (420 passed, wall 246 s); orchestrator re-verified on the E16 store (the lagged label `ready` with minority share 0.2102, 40 windows; `forecast candidate` on it: 175 retained, excluded_for_block 2, 39 paired windows); merged #71 |
| W8-E16 the experiment: run.py, the run, the write-up | 1 (Opus, 98 turns, 10.66 USD; stopped on the session limit, resets 5:40pm, after running deckgen and kstrl), 2 (resumed 19:16, 29 min) | 2026-09-06 15:52 | PR #72 opened by the session; gate set green (420 passed, wall 252 s); merged #72; out/ copied from the worktree to experiments/E16/out before the worktree was removed |
| W8-T4 the tracked base defines the change clock's rows; first-parent merges report their landed diff | 1 (Opus, 124 turns, 11.54 USD) | 2026-09-06 14:09, in parallel with W8-T3 on disjoint files | PR #68 opened by the session; gate set green on main (405 passed, wall 254 s); orchestrator verified the row counts on a fresh copy of the E16 store; merged #68 |

## Incident: a test written against the rule W7-T3 replaced

W8-T2's readiness test expected check 1 to print `not forecastable:` for the seven process
columns of a backfilled lineage. W7-T3, merged first, had changed check 1 to exclude an
`unavailable` column by name (`excluded by name: attempts_to_land (unavailable), ...`) and
to fail only on a `partial` column. The merged tree printed the new wording, so the test
failed on the words, not on the behaviour it meant to pin. The orchestrator pinned the test
to the merged wording. The same run showed what the test could not: check 1 FAILED on the
merged tree anyway, because `rework_within_3` and `rework_within_3_lag3` are `partial` on
every lineage by construction (no label on the last three rows, none on the first three).
That is the defect W8-T3's brief was widened to fix; the measurement is under
"Measurements".

Two hook interactions on the merge commits, recorded because they will recur: the
file-length ratchet refused backtest.py at 801 lines after W8-T2's one-line import was
merged over main's 800 (one assumption sentence was shortened); complexipy `--staged`
flagged experiments/E13/census.py `main` (20) and E13/run.py `main` (29) as new on the
merge commit because they arrive from main and are new relative to the branch. They are
main's, committed there, and are carried below as a refactor item; the merge commit was
made with `SKIP=complexipy` and says so in its message.

## Merged

| PR | Task | What landed |
|---|---|---|
| #65 | W8-T1 | `telltale import git-history --repo R [--branch B] [--no-checks]`: one capture per repository (`imp_` + sha of ("git", repo_id)), provider `git`, one `telltale.repo.commit` per first-parent commit with `link_confidence` `unlinked` and the per_file list, one `external.outcome` `mechanical_verification` per commit with check runs (external_system `github:check-runs`, duration = max completed minus min started, status pass or fail), one `revert_or_repair` outcome per commit a later commit within three reworks by line overlap (external_system `git:line-overlap`). Idempotent on commits; re-import appends a second capture bracket (carried). |
| #66 | W8-T2 | Uncaptured change rows: a lineage's row for an `unlinked` commit with its six repository columns from the commit, the seven process columns None, the post-merge columns from outcomes by sha; `rework_within_3_lag3` (row j carries change j - 3's label, None when j < 3 or undecided); the cohort's `uncaptured_rows` count and `unknown_columns` sentences; the header line of readiness prints the uncaptured count; the run's assumptions carry one sentence when any row is uncaptured. |
| #72 | W8-E16 | experiments/E16/run.py and rows.py (the runner split under the 800-line ratchet), docs/experiments/E16.md filled, README and CHANGELOG. |
| #71 | W8-F1 | Readiness check 7 measures a `flag` target by its minority share (a 0/1 column with median 0 has MAD 0 at any base rate below a half); `frame.retained(series, target, columns)` also excludes rows where a named column is unknown, and the candidate protocol's pair runs over the frame cut with the block, so a binary-file commit costs rows (`excluded_for_block`) and never the run; `advise` looks a holed target's run up under the suffixed frame id and asks the checklist at the target's own horizon. |
| #70 | W8-T5 | `repo_link._commit_stats` folds subsystems_touched, test_files_changed and dependency_delta over the whole path list before any bound and stores them on the commit payload with `path_rules_version`; `series_paths.columns` prefers the stored cells and falls back to per_file; the allowlist (in allowlist_telltale.py, not sanitize.py as the brief said) admits the four fields. deckgen 5 and kstrl 4 truncated lists no longer cost the cells; 0 disagreements between stored cells and the per_file rule over 646 complete lists. |
| #69 | W8-T3 | `forecast/frame.py`: `retained(series, target)` is the series over the rows whose target cell is known (identity when nothing is excluded, so every E13-shaped run is unchanged), with the excluded count and row keys on the cohort and the run; `backtest._variant` admits a `partial` target; readiness runs every check over the retained frame, check 1 passes on a partial target and names partial covariates as excluded; `rework_within_3_lag3` registered at H 4 with `CANDIDATE_HORIZON` and `SCORED_STEPS`; the candidate protocol's future block is the candidate row edge-replicated, scored on step 4 of 4 for the lagged label; `forecast/features.py` split out of candidate.py. |
| #68 | W8-T4 | When a repository has a git-history capture, the change clock's rows are that base's commits and nothing else: a captured commit joins the base row it names by sha or by tree (a squash-merged branch tip has the tree the squash put on main), a captured commit on neither is dropped by name with the reason `not on the tracked base` and counted (`off_base_commits`); one row per sha with two links collapsed to the higher rung (`collapsed_links`); `repo_link._commit_stats(first_parent=True)` gives a merge on the walked branch the diff the branch took. The row assembly lives in series_changes.py, which the brief did not name; the report says why. |
| #67 | chore | experiments/E13/census.py `main` (cognitive 20 to 5) and run.py `main` (29 to 1) under the complexipy gate by extraction only; the census output on the rebuilt store is byte-identical apart from wall-time fields (sha256 9179b464...). |

## Measurements (verified by the orchestrator on the merged trees)

- W8-T1 on this repository at 159 first-parent commits (before the wave 8 merges):
  159 commits, 31 rework outcomes, every row `unlinked`; rework rate 0.195 against the
  0.170 the brief measured over 90 days with a different window (carried).
- W8-T2's VERIFY on its merged tree at 162 first-parent commits, `--no-checks`: import
  5.4 s wall; `series build --clock change --repo <id>` 162 rows, 0 captured, 162
  uncaptured; the A block `partial` (2 to 4 nulls: the root commit and merges without a
  per_file list), the seven process columns `unavailable` (162 nulls), rework_within_3
  and rework_within_3_lag3 `partial` (3 nulls each), merge_verification_ms
  `unavailable`; `series check` ok; readiness on merge_verification_ms refused: the target
  is unavailable, which is the right answer under `--no-checks`.
- The same import with check runs: 78 s wall (one `gh api` call per commit), 135
  check-run outcomes, 24 commits with no check run, 3 diagnostics; merge_verification_ms
  and merge_verification_failed `partial` with 27 nulls. Readiness refused: `target
  merge_verification_ms has coverage partial: a target must be one of ['observed',
  'derived']`. Window-level exclusion (drop every window whose context or actual holds an
  unknown target cell) would keep 16 of 146 windows at H 1 (k_min is 20) and 0 of 143 for
  the lagged label at H 4; row-level exclusion (the target's series is the rows where the
  target is known) keeps 135 rows and 119 windows, and 159 rows and 140 windows. The
  row-level rule went into W8-T3's brief and E16's pre-registration before any run.
- What the change lineage held before W8-T4, measured on the E16 store: telltale built
  219 rows = 167 unlinked first-parent commits plus 52 captured `repo_commit` activities
  of 47 distinct shas, every one a task-branch commit that was squash-merged (none on
  `git rev-list --first-parent main`); 5 of them share a tree with a first-parent commit;
  5 shas were linked twice from one capture. After W8-T4 the same store builds 168 rows,
  captured_rows 5, off_base_commits 46, collapsed_links 5, `series check` ok.
- Merge commits on a first-parent walk reported every count as unknown (W3-T4's rule for
  a merge inside a capture, reused by the importer): deckgen 79 of 118 rows with a null
  A block, kstrl 100 of 252. After W8-T4: 0 and 0 (subsystems_touched, test_files_changed
  and dependency_delta: deckgen 81 nulls to 5, kstrl 103 to 4, per the report).
- Rework outcomes over the whole first-parent history (W8-T1's line-overlap rule):
  telltale 34 of 168, systemap 71 of 108, deckgen 61 of 118, kstrl 124 of 252, against
  the 90-day pilot rates 0.170, 0.667, 0.522, 0.495 in E16's pre-registration.
- W8-T3 on the re-imported E16 store (telltale at 172 first-parent commits, 32 without a
  usable check run): `forecast readiness --target merge_verification_ms` retains 140
  rows, 124 windows, ready; `forecast backtest` scores 124 windows with 0 dropped
  (persistence MAE mean 16,347 ms, rolling_median 17,738 ms, so persistence is the
  baseline to beat); `rework_within_3_lag3 --horizon 4` retains 169 rows, 38 windows at
  stride 4, and fails check 7 alone: the scaled MAD of a 0/1 column with base rate 0.20
  is 0. W8-T3's report also measured that the candidate protocol refuses this lineage at
  origin 16 because two binary-file commits leave lines_added unknown, and that `advise`
  cannot find a holed target's run. All three are W8-F1.
- Gate set wall: 231 s (W8-T1), 256 s of pytest alone (W8-T2, 402 passed), 254 s
  (W8-T4, 405 passed), 246 s (W8-T3, 411 passed).
- GitHub API budget for E16's population: 168 + 108 + 118 + 252 = 646 first-parent
  commits on main across telltale, systemap, deckgen and kstrl, one call each, against
  5,000 per hour.

## Carried from the reports

- W8-T1: re-import appends a second capture_started/ended bracket to the same capture
  (idempotent on commits, not on brackets); `git_branch` and `git_since` are not recorded
  on capture_started; the rework rate 0.195 (whole history, W8-T1's rule) against the
  brief's 0.170 (90 days) is unreconciled; `external_system` was added to the
  external.outcome allowlist.
- W8-T2: `model.py` RowMeta docstring is stale; `rework_within_3_lag3` was built but not
  registered as a target (W8-T3 registers it).
- Orchestrator: experiments/E13/census.py `main` (cognitive 20) and E13/run.py `main`
  (29) sat on main above the complexipy gate and surfaced on every merge commit; done in
  #67 by extraction only, census output byte-identical apart from wall-time fields.
- W8-T3: `scenario.py` refuses a holed target later rather than earlier (its own context
  reader meets the unknown cell); not run over post-merge columns today.
- W8-F1: `candidate._covariates`, which `one_step` (the advisory's row-N forecast) calls,
  still checks the target against the covariate rule `backtest.FORECASTABLE` instead of
  `TARGET_COVERAGE`, so `advise` prints "forecast: not run" for every partial target;
  `one_step` cuts its frame without the block, so a lineage with a binary-file commit
  refuses the row-N forecast. Both are one-line changes with a design question behind
  them (what a refused advisory should say); neither touches E16's `conditioned`.
- W8-T5: the allowlist for `telltale.repo.commit` lives in allowlist_telltale.py, and the
  change clock's row assembly in series_changes.py; two briefs named the old files.
- lines_added and lines_removed are unknown on any commit touching a binary file (git
  numstat prints `-`): telltale 2, systemap 7, deckgen 27 of 118, kstrl 0. Whether a
  text-only count with a binary flag beside it is the better column is a design question
  for after E16, which reports the rows it cost.

## Exit verdict

Wave 8 was the wave that gave the change clock rows, and it did: 657 first-parent
commits across four repositories against the 54 branch commits the clock held before,
every one with the candidate block, the rework label and, where a check run exists, the
merge verification. The experiment those rows were for, E16, ran under its
pre-registered rule and its answer is in docs/experiments/E16.md:

- H5 and H6 on the change clock: twelve (repository, target) rows, twelve "baseline
  sufficient". TimesFM-3's best share of origins beating the best one-line baseline is
  0.5308 against the pre-registered 0.60; four rows have a lower mean error than the best
  baseline and are still baseline sufficient by the margin or the share clause. The
  chronology placebo is valid on 6 of 12; on the other six the positive labels were not
  assessable, which does not change a label the baseline clause had already decided.
- H8: eleven rows reached the reading; ten read "no measurable conditioning at this n"
  and one, systemap merge_verification_ms, reads "the candidate's features move the
  forecast" on 23 paired windows over 39 rows, three windows above the floor. Carried
  forward, not built on. deckgen rework_within_3_lag3 is not assessable at 18 paired
  windows because 27 binary-file commits cost the block.
- H7 stays not assessable: the process columns are unavailable on all but 5 of 657 rows,
  because those rows came from git and not from captured sessions. That is E17's
  question and it waits on day-to-day capture being switched on.

What downstream may say, in one sentence: on these four repositories' first-parent
histories, post-merge verification time, verification failure and rework within three
changes are not forecast better than a one-line baseline by TimesFM-3 in one regime, and a
candidate's size and shape do not measurably move that forecast at this n. Claim class
predictive on every run; cohort one repository at a time; coverage partial on every
target with the excluded rows counted.

Wall of the wave: dispatched 12:10, last merge 20:24 London, seven implementer sessions
(one killed for a wrong brief, two resumed after the account's session limit) and one
subagent; E13b ran on the same GPU throughout.
