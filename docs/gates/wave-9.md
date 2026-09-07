# Wave 9 gate: day-to-day capture and the repository behind a daemon capture

Opened 2026-09-06 after wave 8 closed. The owner switched on day-to-day capture (the
`telltale setup` snippets, installed by hand) and the first evening of real use exposed
what the launcher path had hidden.

## What the first evening measured (all on Claude Code 2.1.263, codex-cli 0.150.1)

| Fact | Number | Source |
|---|---|---|
| Claude haiku smoke sessions captured through the daemon | 6 of 7 | store captures for session ids under `~/.claude/projects/*e17-smoke*` |
| The lost session: stall before its first transcript line | about 110 s (process start 22:09:40 UTC, first line 22:11:35) | transcript timestamps; no telemetry, no hooks, cause not identified |
| Wall time of a two-tool haiku session | 12, 15, 12 s | three repeat runs |
| `repo.identity` on this repository | 56, 45, 48 ms | three runs |
| Codex under the pasted config.toml block | 0 batches | healthz `otel_logs` 8 before, 8 after |
| Codex with the same values as `-c` overrides | 4 batches | healthz 3 to 7 |
| Codex under the `[otel]` table form | 17 attributed observations, 207 metric points unattributed | captures cap_085f06878df43323d24fb6ad and `unattributed` |
| `git_commit_id` as reported by Claude Code | 7 characters (`0881c21`) | tool_result payload, cap_b0be0e0da48a5a1a78bdb8fa |
| unknown_field diagnostics for one short session | 41 | diagnostics 22:00 to 22:16 UTC |

## Defects found

1. **Codex snippet placement.** Dotted keys (`otel.exporter = ...`) pasted after a
   `[projects."..."]` table header become keys of that table. tomllib on the installed file:
   no top-level `otel`. Fix: the snippet is a `[otel]` table (W9-F1). The owner installed a
   corrected copy at 22:26 UTC and the next `codex exec` was captured.
2. **A daemon capture has no repository.** `repo_id` is NULL on every observation, the hook
   `cwd` and every `file_path` are hashed as `<outside>/...`, and `sessions --link-commits`
   skips the capture because NULL never equals the current repository's id. Nothing from
   day-to-day use could reach the change clock. Fix: W9-T1.
3. **Claude Code 2.1.263 fields.** `scratchpad_dir` on every hook; new SubagentStop and
   PostModelSwitch fields; `permission_mode_changed` attributes; five account-identity
   attributes on every OTel event, refused on purpose but counted as unknown. Fix: W9-F1.
4. **Codex metrics are unattributed in daemon mode.** Metric batches carry no session id.
   Carried: needs a mechanism (resource attribute, or attribution by time to the one live
   Codex session) and a measurement of what the 207 points contain.

## Dispatch record

| Task | Attempt | Started (UTC) | Model | Outcome |
|---|---|---|---|---|
| W9-T1 daemon captures bind their repository | 1 | 2026-09-06 22:20 | opus | merged #74; 147 turns, 13.74 USD; gate 258 s, 426 passed; CI 6m44s |
| W9-F1 codex setup table, 2.1.263 fields, refused identity attributes | 1 | 2026-09-06 22:20 | opus | merged #73; 122 turns, 9.36 USD; gate 275 s, 422 passed; CI 6m37s |
| W9-T2 change rows fold process columns from recording captures | 1 | 2026-09-06 23:35 | opus | merged #75; 230 turns, 30.64 USD; gate 301 s, 427 passed; CI 6m45s |

## Carried items

- Codex metrics unattributed in daemon mode (defect 4).
- One of seven daemon sessions sent nothing; a capture-gap check (transcripts on disk
  against captures in the store) would measure the loss rate over real use. Needs
  measuring before any rate is stated.
- The owner's Codex model `gpt-6-astra` needs a newer codex-cli than 0.150.1.
- Daemon captures have no environment fingerprint (no argv); `env_changed` on the change
  clock stays None for their rows.

## Exit verdict (2026-09-07 00:10 UTC)

The path the first evening lacked exists and was run once from main after the three
merges: `telltale daemon` on a temporary home, a 9 s haiku session in a fresh checkout that
wrote one file and committed, then `series build --clock change --repo <id>` from that
checkout.

| Cell | Value |
|---|---|
| daemon output | `capture cap_65c4...` then `capture cap_65c4... repo 14b3e108...` |
| observations | 90; `sessions` shows COMMITS 1 |
| change-clock rows | 1, keyed on the commit the session made |
| six repository columns | observed, 0 nulls |
| seven process columns | observed, 0 nulls (folded from the recording capture) |
| `attempts_to_land`, `env_changed`, four post-merge columns | unavailable, None |
| cohort | `process_from_recording_captures: [1e0e850c...]` |

What day-to-day capture can now feed H7: every session started inside a git checkout that
commits contributes a change-clock row with its process columns. What it still cannot:
`env_changed` (no fingerprint without a launched argv), `attempts_to_land` (a hand-started
session is not a numbered attempt), post-merge outcomes (attach by task and attempt), and
any Codex session (command hooks only, no cwd on OTel: W9-T1's NEXT).

## Carried items (added at exit)

- `series.REDUCER_VERSION` hashes series.py, series_lineage.py and series_regime.py only;
  series_changes.py, series_outcomes.py and series_paths.py are outside it, so a change to
  the change clock's fold alone does not move a series id (W9-T2 UNSURE). Fix: hash every
  series_*.py module.
- `sessions` prints RUNTIME, MODEL, DURATION_MS and COVERAGE as `-` for a daemon capture:
  there is no launcher lifecycle row. The model is on the api_request observations and the
  runtime on `service_version`; a daemon capture needs its own lifecycle reduction.
- A daemon capture is reduced only when linkage found a commit; a session that made none
  has no activities until `telltale rebuild` (W9-T1 NEXT).
- The daemon is a launchd agent since 2026-09-07 (`com.telltale.daemon`, KeepAlive, port
  47311, logs in `~/.telltale/daemon.log` and `daemon.err`), installed at the owner's
  request; its first capture bound to a repository within seconds of loading.
