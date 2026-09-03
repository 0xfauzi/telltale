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
| #51 | W5-E11 | One-step candidate conditioning on this repository's change clock: not assessable on all three targets (0 windows against k_min 20), five measured blockers, a seeded synthetic control that reaches branch (c) on one target; docs/experiments/E11.md |

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

- W5-E11, re-run by the orchestrator (2026-09-03) in a verify worktree merged with main
  under `--extra forecast` on a fresh backup of the home store: gates 279 passed, mypy 99
  files, ruff, format, deptry, pre-commit; `run.py` reproduces the STOP outcome (every
  target branch (a), readiness coverage measured 12 needed 18, windows 0 against 20;
  origins dropped 13 for a null `edit_turnover_ratio` inside the context and 12 for
  `regime_too_short`; the flags are 0 on every known row so their scaled MAD is 0), the
  synthetic control reaches branch (c) on `merge_verification_ms` with the same paired
  median MAE difference W5-T1 measured (482.8867, label baseline sufficient), the
  forbidden target refuses, the sentence and the license line print; 3:31 wall. The
  tracked out/ files differ after a re-run only in the series id and cohort capture list
  (the store grew from 40 to 41 change rows during the build), which the write-up
  states. `check_numbers.py`: 464 of 482 matched, the 18 unmatched named in the file
  (spec section numbers, an ADR number, the database size and copy time, id fragments).
- Carried from W5-E11: the placebo twin of a conditioned run is unconditioned because
  `backtest.run` has one `prepare` hook (a composable prepare is the fix); the synthetic
  control's flag targets fail readiness on variation too; `.gitleaks.toml` gained a
  match-only allowlist for Telltale's own id shapes (`cap_`, `imp_`, `ser_`, `fc_`,
  `env_`), verified not to blind a planted key.

## Exit criterion (spec 21 v0.5), provisional until W5-T2 merges

"Candidate conditioning must improve calibrated near-term prediction or warning
usefulness on held-out future changes. Otherwise keep it a research command." On this
repository the protocol is not assessable (E11: 0 windows on every target), so the
criterion is not met and `forecast candidate` stays a research command; the advisory
(W5-T2) stays shadow. What would change the verdict is data, measured in E11: an outcome
with a duration on every landed change (`telltale outcome --duration-ms`, from now on),
a failed merge verification or a rework on record, `edit_turnover_ratio` known on every
row a context covers, and 36 rows inside one environment regime (7 more landed changes
under the current fingerprint, if it holds).
