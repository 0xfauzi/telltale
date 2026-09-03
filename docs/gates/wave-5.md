# Wave 5 gate

## Entry (standing gate 4): TimesFM-3 license

The owner acknowledged on 2026-09-03 that the TimesFM-3 weights (`google/timesfm-3.0-pytorch`)
are under timesfm-non-commercial-license-v1.0: research use, not production. Every
ForecastRun records the license and every forecast command prints it. The advisory stays
shadow (spec 14.6). A production advisory needs a different model behind the same
Forecaster interface.

On the same day the owner approved the E12 full run at a per-session token bound of
2,000,000 (wave 4, standing gate 2); it runs as W4-E12 attempt 2 beside W5-T1.

## Dispatch

- W5-T1 (briefs/W5-T1.md) dispatched 2026-09-03: attempt 1 died on a server-side 529 before
  any work (num_turns 1, zero tokens); attempt 2 merged as #50 the same day.
- W5-T2 and W5-E11 wait for W5-T1 to merge (both STOP on its absence).

## What was merged

| PR | task | one line |
|---|---|---|
| #50 | W5-T1 | `candidate.features` reads block A off `git diff --numstat base...head`; the change clock gains `merge_verification_ms`, `merge_verification_failed`, `rework_within_3` (series_outcomes.py); `telltale outcome --duration-ms`; the TimesFM adapter reads `Window.future` as past-future covariates (length n_ctx + H asserted by name, padding edge, 32-variate cap asserted); `telltale forecast candidate` |

## Measurements

- W5-T1, re-run by the orchestrator (2026-09-03) in a verify worktree merged with main:
  gates 279 passed, mypy 99 files, ruff, format, deptry, pre-commit. On a copy of the home
  store: the change series builds at 38 rows and `forecast candidate --target
  merge_verification_failed` refuses with "coverage partial: a target must be observed
  or derived", `--target attempts_to_land` refuses with the 6.12 text; with TimesFM on the
  60-row synthetic change series (`--extra forecast`, cpu) the unconditioned and
  conditioned runs differ (MAE 21349.4473 against 22382.3404, paired median difference
  482.8867, identical to the implementer's paste to every digit), the sentence and the
  license line print. Break-and-restore: with `_attach` returning the window unchanged
  the conditioning test fails (`31490.227 != 31490.227`), restored 11 pass. The A block of
  the branch by hand from `git diff --numstat origin/main...HEAD`: 11 files, 2450 added,
  60 removed, 3 subsystems, 2 test files (the implementer's 10 / 1748 was before its
  second commit added the log).
- Carried from W5-T1: `forecast candidate --base --head` refuses on the target's
  coverage before printing the extracted A block, so on this repository the live path
  prints nothing (W5-T2's `advise` must print and store the A block regardless);
  stored candidate rows carry no decision label; `ForecastResult.covariates` reaches the
  store only through `scenario.covariates`; `rework_within_3` is None on the last three
  rows (the rule as written, the brief's "two" was wrong); the VERIFY run wrote one
  change series, one synthetic series and six forecast_runs rows into the home store.
- Measured limit, restated: `merge_verification_ms` is unknown on all 38 rows (no outcome
  payload carries a duration), `merge_verification_failed` is 0 wherever known, no
  rework event exists. E11 runs against that.
