# Wave 6 gate: scenario horizons, policy experiments and hardening (spec 21 v0.6)

Status: in progress. Written by the orchestrator as each task merges; the exit verdict
is added when the wave closes.

## Dispatch record

| Task | Attempt | Dispatched | Outcome |
|---|---|---|---|
| W6-T1 scenario horizons | 1 | 2026-09-03 16:19 UTC via retry.sh | merged #54 at 2026-09-03 after 23 min of implementer wall |
| W6-T3 export, purge, retention | 1 | 2026-09-03 16:20 UTC | session killed at 17:28:48 UTC (incident below) after its report and gates were written; the orchestrator committed the staged tree and opened the PR |
| W6-T4 compatibility matrix and hardening | 1 | 2026-09-03 16:21 UTC | PR 57 opened by the session; two review findings fixed by the Codex continuation at 2367b3e; merged #57 on 2026-09-04 |
| W6-T2 policy intervention regimes | 1 | 2026-09-03 17:36 UTC via retry.sh, from main after #54 | session killed at 17:28:48 UTC mid-VERIFY (incident below); attempt 2 dispatched with an addendum on the worktree state |
| W6-F1 hf cache env and advise scored-run guard | 1 | 2026-09-03 17:41 UTC via retry.sh | merged #55 after 12 min of implementer wall |
| W6-T5 public release pass | 1 | after all others merge | not yet dispatched |

## Incident: a sibling session's pkill killed two implementers

At 17:28:48 UTC W6-T4's session ran `pkill -f "pytest -m integration"` to stop a stuck
gate run of its own. Every implementer is launched with its brief as the prompt argument,
so the brief text is on the claude process's argv, and the VERIFY line of the W6-T3 and
W6-T2 briefs contains that string: both sessions received the signal and ended without a
result message (their logs end at 17:28:48.447 UTC with their background pytest tasks
marked killed). W6-T4 itself survived. Recovery: W6-T3's staged tree was complete
(report written, gates recorded green in its log) and was committed and pushed by the
orchestrator; W6-T2 was mid-VERIFY and was re-dispatched as attempt 2 with an addendum
describing the worktree. Mechanism change: dispatch.py now passes the brief on stdin, so
no brief text is on any process argv; the brief README gains the rule "never kill a
process by name pattern".

## Merged

| PR | Task | What landed |
|---|---|---|
| #54 | W6-T1 | `telltale forecast scenario --series --target --horizon --future col=v1,..,vH [--name] [--forecasters] [--compare-with]`; `DECLARABLE` registry keyed by clock (four request-clock columns, the A block on the change clock); `SCENARIO_HORIZONS = (1, 4, 8, 16)`; one predictive `forecast_runs` row per scenario with the declared paths, the registry, the command and the sentence "a scenario is a conditional forecast, not a plan" in `scenario`; `forecast/scenario.py` and `forecast/scenario_report.py` new; backtest.py and candidate.py untouched. |
| #55 | W6-F1 | `hf_cache_dir(explicit)` in forecast/__init__.py (stdlib): `explicit or os.environ.get(CACHE_ENV) or None`, so `TELLTALE_HF_CACHE=` no longer sends the checkpoint into the working directory; `cli_advise._latest` keeps only rows whose `metrics` carry `forecasters`, so a scenario row is no longer "the newest true-order run" and an advise over a scenario-only pair prints "no assessable forecast". Two of W6-T1's carried items closed. |
| #57 | W6-T4 | `doctor --matrix` (one row per provider and runtime version with captures, surfaces and per-capability coverage counts, then each provider's DRIFT list once); `telltale.capture_ended.terminal` (`exit` or `signal N`); a held daemon port is one line and exit 1; a locked database past the writer's retry ladder leaves a `launcher` diagnostic naming the lost batches and `doctor` prints it as `last launcher diagnostic`; `setup claude|codex --print --daemon [--port] [--level]` prints a launchd plist and never writes it; doctor_matrix.py, launch_health.py, setup_daemon.py, test_hardening.py new. Codex continuation fixes at 2367b3e: doctor no longer tracebacks on a corrupt store, and the printed save command keeps the requested port and level. |

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

| #56 | W6-T3 | `export --format jsonl|parquet --out DIR` (six tables, MANIFEST.json, claim_class on every derived row, one read transaction), `import export --root DIR [--dry-run]` (every row validated and staged before any write; duplicate and conflicting identities refuse; diagnostics keep their id and ingest_ts; a second run adds only what is missing), `purge --diagnostics-older-than N`, `doctor`'s retention block, `telltale schema`; export.py, export_import.py, export_validation.py, cli_export.py, cli_purge.py new. The implementer's attempt was killed by the pkill incident with its work staged; the Codex continuation's review found six defects in the first import gate, its repair was finished by the orchestrator, and the orchestrator's full round trip found a seventh (the gate refused the owner's own export because `renormalize` kept assignment values and absolute paths), fixed in commands.py. |

## Measurements (W6-T3, verified by the orchestrator at 4bc9af1 and c8d0e25 merged with main)

- Reproduced against c74c528: an unknown payload field carrying a prompt and a synthetic key was stored; two rows with one id reported 2 added and stored none; a row missing ingest_ts at position 551 left 500 rows; 46 of 110,419 exported commands held a token starting with / or ~. Against 4bc9af1 all refuse with 0 rows stored. Break-and-restore: unknown-field refusal removed fails 1 test; staging primary key removed fails 13.
- Repaired gate on the owner's export: 65 of 110,655 stored commands refused, all cmdnorm-v3 (25 bound-cut tokens, 37 absolute paths, 3 assignment values); `renormalize` repaired 25 and kept 40, so `resanitize` could not clear them. After the commands.py fix: `resanitize` 52 captures, 712 fields, 8.5 s, second run 0.
- Round trip of a fresh copy of the home store after resanitize: export 1,657,281 rows in 12.3 s; import 144 s; 1,145,146 observations back with payload, redaction and correlation_ids byte-identical on every row; 20,594 diagnostics back with id and ingest_ts (plus 24 the rebuild wrote); activities and evidence identical per capture except the two `adv_` captures the source never reduced; sessions 3317 lines diff 0; show differs only in reducer_version; second import adds 0. series_snapshots and forecast_runs are not observations and are not rebuilt (8 and 12 rows stay in the source).
- Purge: `--diagnostics-older-than 7` removed 0 and `1` removed 20,542, equal to sqlite's counts. Doctor exit 0 with retention; schema 108 types; parquet counts equal jsonl counts.
- Gates at c8d0e25 merged with main: 347 passed, 2 skipped, mypy 117 files, ruff, format, deptry, pre-commit clean. Outcomes posted on the implementer capture: adversarial_review fail (privacy, cardinality, restart, snapshot), mechanical_verification pass 494 s, merge_decision merged.

## Carried from W6-T3

- A relative path in a stored command is accepted by the import gate as written (`../../etc/passwd` included): the gate has no repository root to resolve one against. Named in the amendment.
- The bare-word budget is not re-applied by the gate or by `renormalize`; a hand-written export can carry more lowercase words than the normalizer keeps.
- `commands.normalize` is not idempotent on its own output (37,955 rows change on a second pass); `renormalize` is the fixed-point rule instead. Unchanged.
- Captures killed by the pkill incident: W6-T3 attempt 1's capture ends `143|signal 15` with the work complete and staged.

## Measurements (W6-T4, verified by the orchestrator at 6e5d139 and again at 2367b3e merged with main)

- `doctor --matrix` on a copy of the home store: exit 0 in 5.30 s, 896 lines; rows for claude 2.1.257 (37 captures, import and transcript surfaces), claude 2.1.259 (86 captures, six launcher surfaces), codex 0.39.0 and 0.150.1 (104 captures); DRIFT printed once per provider (claude 33 measured differences, codex 21).
- `setup claude --print --daemon --port 4318` and the codex variant: the plist passes `plutil -lint`; with `--level 2` the printed save command carries `--port 4318 --level 2`; `~/Library/LaunchAgents` holds 0 telltale entries before and after.
- (a) held port: `telltale daemon` prints one line and exits 1; `telltale run` with the port held exits 0 and records `0|exit`.
- (b) SIGTERM to the launcher: exit 143, `capture_ended` 143|signal 15; SIGKILL to the child: exit 137, 137|signal 9; launcher stderr empty in both.
- (c) a second process holding `BEGIN IMMEDIATE` for 45 s: the run exits 0 and records `0|exit`, the store writes `dropped {"store":1}` and a `launcher` diagnostic naming 1 lost batch, and `doctor` prints it as `last launcher diagnostic`.
- Corrupt store (a text file as telltale.db): `doctor` exits 0 with no traceback and prints `last launcher diagnostic: unavailable ... file is not a database`.
- Gates at 2367b3e merged with main: 320 passed, mypy 110 files, ruff, format, deptry, pre-commit clean. Verify wall 173 s.

## Carried from W6-T4's report

- The locked-database test costs 46 s (27 per cent of the suite); the writer's retry ladder is store.py's decision.
- A locked run takes as long as the lock (71.2 s measured for a 6 s child); whether a recorder should wait that long belongs to store.py.
- `first_seen` and `last_seen` in the matrix are ingest dates, not session dates.
- The plist is macOS only; nothing refuses to print it elsewhere.
- W6-T4's session ran `pkill -f "pytest -m integration"` and killed two sibling implementers (incident above); its own report does not mention it.

## Carried from W6-T1's report

- (closed by W6-F1, #55) `cli_advise._latest` took a scenario row for the newest scored run.
- (closed by W6-F1, #55) `TELLTALE_HF_CACHE=` sent the checkpoint download into the working directory.
- A declared column rides both as past-only history and as the future block; what that
  costs the model was not measured (E03 did not ask).
- The calibration quoting path (a stored true-order backtest of the same series, target
  and horizon) is exercised by no test.
- 32 and 64 are absent from `SCENARIO_HORIZONS` by decision, not cost.
