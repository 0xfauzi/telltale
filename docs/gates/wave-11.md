# Wave 11 gate: two more capture sources found by asking "is it working"

Opened 2026-09-14, in the same sitting as wave 10. The owner asked to also capture the
ChatGPT/Codex desktop app and the Claude desktop app. Digging into what each actually is
turned up a real backfillable source for one and a still-open identification question for
the other.

## What was measured

| Fact | Number | Source |
|---|---|---|
| Claude desktop app bundle | `com.anthropic.claudefordesktop` 1.32885.1 | `defaults read` |
| Its "local agent mode" transcripts found under a new root | 131 files, 61 valid sessions, 27,467 lines | dry-run |
| Real import into a throwaway store, for a privacy check before touching `~/.telltale` | 15,248 observations, 186 diagnostics | real import, temp store |
| Observations carrying a real word from the owner's own bash commands | 41 of 15,248 (0.27%) | grep of the temp store's payloads |
| Cause | the command normalizer's documented "keep subcommand-shaped bare tokens" rule (design 6.4), not a new defect | reading `commands.py` |
| Real import into `~/.telltale`, after the owner accepted the tradeoff | 61 captures, 15,248 observations, 0 collisions on a repeat | real import, real store |
| ChatGPT/Codex desktop app bundle | `com.openai.codex` at `/Applications/ChatGPT.app`, version 26.908.40834, embedded "Codex Framework" 152.0.7977.83 | `defaults read`, `ps` |
| Its exact `service.name` on the wire | not yet captured: nothing was transmitting when checked; the earlier sample was purged before this dig | live check, 2026-09-14 |

## Decisions

- **Backfill the Claude desktop app's local-agent-mode sessions, on the accepted tradeoff.**
  The command-normalizer property is pre-existing and already applies to every daemon
  capture; the owner was told the exact fraction and source before approving. Done directly
  (a data operation against existing code, no new provider needed).
- **Build print-only periodic rescheduling of the same idempotent import,** so new
  local-agent-mode sessions keep landing without a live telemetry path. W11-T1, following
  the exact `setup --print --daemon` precedent: printed, never written, `--apply` refuses.
- **Not yet done: the Codex desktop app.** Its real `service.name` needs one live sample.
  Nothing is built toward it until that value is measured, not guessed.

## Dispatch record

| Task | Attempt | Started (UTC) | Model | Outcome |
|---|---|---|---|---|
| W11-T1 print-only import scheduling | 1 | 2026-09-14 | opus | pending |
