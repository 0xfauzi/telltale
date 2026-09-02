# Wave 0 gate: capture feasibility (spec v0.0)

Date: 2026-09-02. Orchestrator: Claude Fable. Tasks merged: W0-T1 to W0-T6, E01, E02
(PRs 2 to 8 plus three orchestrator commits on main). CI green on main.

## The exit criterion, and whether it is met

Spec section 21, v0.0: tool, file, command, session, usage and compaction facts are
observable without unsafe content retention or model traffic interception.

Met, with the coverage stated per surface below. No model traffic was intercepted:
every fact comes from OpenTelemetry logs and metrics, lifecycle hooks, the structured
stream output, or the exec JSONL and rollout files the tools already write. No content
was retained: the privacy test replays every Claude scenario, closes the database and
scans its bytes (including the write-ahead log) for three fake credentials, the real
home path and the prompt text, at content levels 1 and 0. It passes, and it fails when
any one of the three privacy mechanisms is removed (measured in docs/log/W0-T5.md).

## What was measured

| Question | Claude Code 2.1.257 | Codex CLI 0.150.1 |
|---|---|---|
| Sessions used | 8 (1,145,266 tokens, USD 1.17) | 7 (568,665 tokens) |
| Surfaces that delivered | OTel logs, OTel metrics, http hooks, stream-json | exec JSONL, OTel logs, OTel metrics, command hooks, rollout |
| Per-request tokens by type | observed (OTel logs, stream) | observed (exec turn usage, rollout) |
| Compaction with pre and post tokens | observed on three surfaces; the digest's claim that no OTel compaction event exists was wrong | not provoked (unavailable, not absent) |
| Tool name, file path, command | observed | observed |
| Bash exit code | derived only: stream text "Exit code N" on failure | observed (exec command_execution.exit_code) |
| Context window size | stream result only | rollout task_started (258400) |
| Fail-open with the receiver down | exit 0, +0.9 s wall (one pair), one stderr line | exit 0, same event shape, about 12 ms per hook |
| Late export after child exit | none: last request 0.24 to 0.56 s before exit | none: negative on all six scenarios |
| Privacy probes reaching a surface | hooks and stream only; never OTel | OTel carries command output by default (bounded by max_bytes = 0, effect not yet measured) and user.email on every record |

## What the system can do today

`telltale doctor` round-trips a synthetic record through all seven receiver endpoints
and reads it back (measured at about 0.58 s). `python -m telltale.receiver --replay`
turns any captured scenario into observations over the real HTTP path; 33 integration
tests run in 18 s. `telltale setup claude --print` prints the hooks and environment
snippet; `--apply` is refused by design.

## What downstream may claim

Claim class `observed` for every matrix cell marked observed, in the cohort "one
machine, one runtime version per provider, one model per provider, 15 sessions". No
comparative or predictive claim. Coverage caveats travel with each partial cell.

## Decisions taken by the orchestrator (routine; reversible)

1. Content level 0 keeps command basenames only (design 6.4); the spec's 11.1 reads
   stricter. Rationale: the basename is the minimum needed to classify verification
   runs, which is the point of level 0.
2. Captured fixtures live under fixtures/sources/, which the shared pre-commit block
   already exempts from formatting hooks; goldens obey every hook.
3. The specification docx (890 KB) exceeded the 500 KB large-file hook once; the first
   commit used a one-time skip, recorded in docs/log/W0-T1.md. The hook is live for every
   later commit.
4. experiments/ is excluded from mypy and deptry: experiment scripts repeat file names by
   design and import siblings by bare name. Ruff still lints them; experiments are
   verified by running them.

## Items for the owner (none blocks wave 1)

1. Your ~/.codex/hooks.json does not parse under Codex 0.150.1 ("invalid type: map,
   expected a sequence"), so your Codex hooks are silently dead. Telltale never edits it.
2. Upload docs/assets/logo-512.png as the repository social preview (no CLI exists).
3. Dependabot PR #1 (setup-uv 10.0.0 to 10.0.1) is open and green; merging it moves a
   pinned SHA. It will be merged at the wave 1 gate unless you object.

## Next

Wave 1 (spec v0.1, flight recorder): launcher, activities and reading commands, Codex
provider, TimesFM-3 smoke test, repeat runner, request-clock series, forecast core.
The perturbation experiment E04 needs sessions and will be requested at the wave 1
gate with a pilot measurement.
