# Wave 6 gate: scenario horizons, policy experiments and hardening (spec 21 v0.6)

Status: in progress. Written by the orchestrator as each task merges; the exit verdict
is added when the wave closes.

## Dispatch record

| Task | Attempt | Dispatched | Outcome |
|---|---|---|---|
| W6-T1 scenario horizons | 1 | 2026-09-03 16:19 UTC via retry.sh | merged #54 at 2026-09-03 after 23 min of implementer wall |
| W6-T3 export, purge, retention | 1 | 2026-09-03 16:20 UTC | running |
| W6-T4 compatibility matrix and hardening | 1 | 2026-09-03 16:21 UTC | running |
| W6-T2 policy intervention regimes | 1 | 2026-09-03 17:36 UTC via retry.sh, from main after #54 | running |
| W6-F1 hf cache env and advise scored-run guard | 1 | 2026-09-03 17:41 UTC via retry.sh | merged #55 after 12 min of implementer wall |
| W6-T5 public release pass | 1 | after all others merge | not yet dispatched |

## Merged

| PR | Task | What landed |
|---|---|---|
| #54 | W6-T1 | `telltale forecast scenario --series --target --horizon --future col=v1,..,vH [--name] [--forecasters] [--compare-with]`; `DECLARABLE` registry keyed by clock (four request-clock columns, the A block on the change clock); `SCENARIO_HORIZONS = (1, 4, 8, 16)`; one predictive `forecast_runs` row per scenario with the declared paths, the registry, the command and the sentence "a scenario is a conditional forecast, not a plan" in `scenario`; `forecast/scenario.py` and `forecast/scenario_report.py` new; backtest.py and candidate.py untouched. |
| #55 | W6-F1 | `hf_cache_dir(explicit)` in forecast/__init__.py (stdlib): `explicit or os.environ.get(CACHE_ENV) or None`, so `TELLTALE_HF_CACHE=` no longer sends the checkpoint into the working directory; `cli_advise._latest` keeps only rows whose `metrics` carry `forecasters`, so a scenario row is no longer "the newest true-order run" and an advise over a scenario-only pair prints "no assessable forecast". Two of W6-T1's carried items closed. |

## Measurements (W6-T1, verified by the orchestrator on the merge with main)

On the seeded 200-row request walk `ser_a2688e3b16a09f1a4882c3c8` (changepoint 120,
context rows 120 to 199, 80 rows, c_min 32), target `output_tokens`, horizon 4, declared
path `tool_calls_since_prev` at 2,2,2,2 ("quiet") and 8,8,8,8 ("busy"):

- Baselines: persistence 1127.0 and rolling_median 1188.5 at every step under both
  paths, `READ_FUTURE` False, `--compare-with` difference 0.0 at every step, and the
  page says the baselines read no past-future covariate.
- TimesFM: quiet points 1128.0195, 1127.6758, 1121.4869, 1119.3171; busy minus quiet
  -0.7301, -5.4766, -3.2938, -1.9359; wall 126.098 ms for the call inside an 8.63 s
  process (model load included); the weights line names
  `timesfm-non-commercial-license-v1.0`.
- Four `forecast_runs` rows stored, all `claim_class` predictive, `metrics` `{}`, the
  window's `actual` null, the scenario JSON carrying name and paths.
- Refusals: a path for `fresh_input_tokens` exits 2 with "past-only: this column is
  observed, never planned" and the declarable list; `--horizon 3` exits 2 naming
  `[1, 4, 8, 16]`.
- `git diff --stat origin/main -- backtest.py candidate.py` empty.
- Break-and-restore: with every column made declarable, 2 tests fail
  (`test_a_path_for_a_past_only_column_is_refused_naming_the_column` and the CLI exit-2
  test); restored, 15 pass.
- Gates on the merge with main: 299 passed, mypy 105 files, ruff, format, deptry,
  pre-commit all clean.

## Measurements (W6-F1, verified by the orchestrator on the merge with main)

- `uv run pytest -q tests/integration/test_forecast_env.py tests/integration/test_advise.py`: 11 passed.
- Break 1 (drop the trailing `or None`): 1 failed, `test_a_variable_set_to_the_empty_string_is_not_a_directory`; restored.
- Break 2 (drop the `forecasters` guard in `_latest`): 2 failed, `test_a_scenario_of_the_same_pair_does_not_displace_the_scored_backtest` and `test_a_scenario_and_nothing_else_is_no_assessable_forecast`; restored, worktree clean.
- From an empty temp directory with `TELLTALE_HF_CACHE=` and the forecast extra: `forecast scenario ... --forecasters timesfm` on the 200-row synthetic series completed in 8.52 s process wall (timesfm 126.571 ms, same points as W6-T1's run), and the temp directory held no `models--google-*` afterwards.
- Gates on the merge with main: 305 passed, mypy 106 files, ruff, format, deptry, pre-commit clean.

## Carried from W6-T1's report

- (closed by W6-F1, #55) `cli_advise._latest` took a scenario row for the newest scored run.
- (closed by W6-F1, #55) `TELLTALE_HF_CACHE=` sent the checkpoint download into the working directory.
- A declared column rides both as past-only history and as the future block; what that
  costs the model was not measured (E03 did not ask).
- The calibration quoting path (a stored true-order backtest of the same series, target
  and horizon) is exercised by no test.
- 32 and 64 are absent from `SCENARIO_HORIZONS` by decision, not cost.
