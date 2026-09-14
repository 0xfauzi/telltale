# Wave 10 gate: a stray OTLP source found and refused

Opened 2026-09-14. The owner asked whether their Claude Code and Codex sessions were being
captured. Claude Code was fine (26 sessions since the wave 9 daemon went live, five
repositories, correctly bound). Codex CLI had zero real sessions since 2026-09-06; a fresh
live test proved the routing itself still works, so the owner simply had not run `codex
exec` since that day. What filled the gap was a third process, unrelated to anything
Telltale was built to capture, posting its own telemetry at the daemon's port; the daemon
silently mislabeled all of it as Claude Code rather than refusing it.

## What was measured

| Fact | Number | Source |
|---|---|---|
| Observations under the `unattributed` pseudo-capture since 2026-09-07 | 18,897 | store query |
| `claude.otel.metric` (mislabeled, values usually null) | 14,256 | same |
| `claude.otel.codex.*` types (never in Claude's allowlist; payload usually `{}`) | 4,632 across 12 types | same |
| Claude Code captures since 2026-09-07 | 26, bound to 5 repositories | store query |
| Codex CLI (`codex exec`) captures since 2026-09-06 22:27 | 0 | store query |
| A fresh `codex exec` against a test listener | routed correctly, `service.name` = `codex_exec` | live sniff, 2026-09-14 |
| The unrecognized source | a separate local process, not Claude Code or the `codex` CLI | live process listing at the time of the finding |

## Decisions

- **In scope, done without asking:** the daemon must refuse an OTLP batch whose
  `service.name` is present but unrecognized, rather than defaulting it to Claude Code. This
  follows directly from the closed-vocabulary and fail-open invariants; it changes no
  behaviour for a batch that already worked. W10-F1.
- **Out of scope, the owner's decision, not yet made:** whether Telltale should ever capture
  the unrecognized source as a named provider. It is a different product than anything E02
  measured, with its own event vocabulary; admitting it would need its own privacy review
  before any field is allowlisted. Nothing was built toward this.

## Dispatch record

| Task | Attempt | Started (UTC) | Model | Outcome |
|---|---|---|---|---|
| W10-F1 unrecognized service.name is refused | 1 | 2026-09-14 | opus | merged #76; 78 turns, 5.72 USD; gate pytest 222s/436 passed, mypy/ruff/deptry/pre-commit clean; CI 6m17s |

## Exit verdict (2026-09-14)

Fixed and verified live. The daemon was restarted (`launchctl unload` then `load`) so the
merged code is what is running now (pid confirmed changed). A synthetic OTLP batch with
`service.name = "wave10-verify-probe"` sent straight at the running daemon got a 200,
stored zero observations, and produced exactly one `parse_failure` diagnostic naming the
value verbatim. The `unattributed` pseudo-capture gained zero rows after the restart.

What this closes: the daemon no longer fabricates provenance for traffic it cannot
identify. What it does not close: the 18,897 rows already stored under `unattributed`
are untouched (a fact, not a bug: this task refuses future misrouting, it does not
rewrite history), and whether to build support for the unrecognized source as a named
provider is still the owner's decision, not made.

## Carried items

- The 18,897 pre-existing `unattributed` rows are dead weight in the store. `purge
  unattributed` (if that capture_id is a valid purge target) or a one-off delete would
  reclaim the space; not done, since it is destructive and was not asked for.
- Diagnostic volume after the restart needs measuring over a full day: one `parse_failure`
  row is written per REQUEST, not per observation, so the rate is much lower than the
  18,897 figure, but the exact number is unmeasured (W10-F1's own note).
- Whether the unrecognized source (a separate local process, not Claude Code or the
  `codex` CLI) should ever be captured is open.
