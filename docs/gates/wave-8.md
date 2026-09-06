# Wave 8 gate: the change clock from git history

Status: W8-T1 and W8-T2 merged; W8-T3 dispatched. Written by the orchestrator as each
task merged. Wave 8 is not a spec version gate: it is the wave that gives the change clock
its rows. Before it, the change clock had 54 launcher-linked commits on one repository
against about 1,400 commits the owner landed in 30 days, so H7 and H8 were "not
assessable" for want of rows, not for want of a forecaster (docs/experiments/E11.md).

## Dispatch record

| Task | Attempt | Dispatched | Outcome |
|---|---|---|---|
| W8-T1 import git-history: unlinked change rows with git and check-run outcomes | 1, 2 (Opus; the first stopped on the account's session limit at 12:40pm London and was resumed by `resume-until-done.sh` at 12:47) | 2026-09-06 | PR #65 opened by the session; gate set green on the branch merged with main (394 passed, wall 231 s); merged #65 |
| W8-T2 uncaptured change rows: git-backfilled lineages on the change clock | 1 (killed by process group: the brief named `lines_added`, which is not a registered target; the brief was fixed and cherry-picked into the worktree), 2, 3 (resumed after the same session limit) | 2026-09-06 | PR #66 opened by the session against a hand-written contract for W8-T1's payloads (W8-T1 had not landed); one test failed on the merged tree (below); merged #66 |
| W8-T3 targets with holes run over their known rows; candidate protocol at H = 4 for the lagged rework label | 1 | 2026-09-06 | pending |

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
- Gate set wall: 231 s (W8-T1) and 256 s of pytest alone (W8-T2, 402 passed).
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
  (29) sit on main above the complexipy gate and surface on every merge commit; refactor
  with an identical-numbers check, as E15's was.
