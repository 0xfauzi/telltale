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
| W9-T1 daemon captures bind their repository | 1 | 2026-09-06 22:20 | opus | running |
| W9-F1 codex setup table, 2.1.263 fields, refused identity attributes | 1 | 2026-09-06 22:20 | opus | merged #73; 122 turns, 9.36 USD; gate 275 s, 422 passed; CI 6m37s |
| W9-T2 change rows fold process columns from recording captures | 1 | 2026-09-06 23:35 | opus | running |

## Carried items

- Codex metrics unattributed in daemon mode (defect 4).
- One of seven daemon sessions sent nothing; a capture-gap check (transcripts on disk
  against captures in the store) would measure the loss rate over real use. Needs
  measuring before any rate is stated.
- The owner's Codex model `gpt-6-astra` needs a newer codex-cli than 0.150.1.
- Daemon captures have no environment fingerprint (no argv); `env_changed` on the change
  clock stays None for their rows.
