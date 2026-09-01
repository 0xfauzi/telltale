# S7 fixture manifest

Codex 0.150.1. Captured by `experiments/E02/run.py --scenario S7`, rewritten
by `experiments/E02/sanitize_fixture.py`. Raw capture: `experiments/E02/out/S7/`
(gitignored). Command line and every timing number: `out/S7/meta.json`.

Prompt: Explain how divide in pkg/calc.py handles zero. Do not edit any file.

Exit code 0, wall time 18.799s.

## Requests and rows per surface

| file | rows captured | rows kept | reasoning dropped | bytes |
|---|---|---|---|---|
| `exec.jsonl` | 14 | 14 | 0 | 3759 |
| `hooks.jsonl` | 8 | 8 | 0 | 5786 |
| `hooks_local.jsonl` | 8 | 8 | 0 | 5166 |
| `otel_logs.jsonl` | 11 | 11 | 0 | 54602 |
| `otel_metrics.jsonl` | 1 | 1 | 0 | 63921 |
| `otel_traces.jsonl` | 5 | 5 | 0 | 108413 |

## Record kinds

- `exec.jsonl`: `item.completed:agent_message` 2, `item.completed:command_execution` 2, `item.completed:error` 5, `item.started:command_execution` 2, `thread.started` 1, `turn.completed` 1, `turn.started` 1
- `hooks.jsonl`: `request /hooks/codex` 8
- `hooks_local.jsonl`: `hook PostToolUse` 2, `hook PreToolUse` 2, `hook SessionEnd` 1, `hook SessionStart` 1, `hook Stop` 1, `hook UserPromptSubmit` 1
- `otel_logs.jsonl`: `request /` 11
- `otel_metrics.jsonl`: `request /` 1
- `otel_traces.jsonl`: `request /` 5

## What was replaced

Counts are of individual substitutions, not of rows.

- `<email>`: 26
- `<home>`: 1
- `<host.name>`: 11
- `<repo>`: 25
- `<user.account_id>`: 26
- `<user.email>`: 26
- `metrics trimmed`: 32
- `spans trimmed`: 1527

`<repo>` is the scenario's throwaway repository copy, `<home>` the machine's home
directory. `user.account_id` and `user.email` are on EVERY OTel log record and are
replaced with `00000000-0000-4000-8000-000000000000` and `capture@example.invalid`. `host.name` becomes `<host>`.
`base_instructions` (the Codex system prompt) and `agents_md` (whatever AGENTS.md was in
scope, which on the capture machine is the owner's own) are replaced by a placeholder
that keeps the character count.

## What was stripped

0 model-thinking rows, across every surface. Telltale never persists them, so
the fixture does not carry them either.

The assertion is a test for the ITEM:
`grep -rlE '"(agent_)?[Rr]easoning"' fixtures/sources/codex | wc -l` = 0.
A bare `grep -c reasoning` over this tree is NOT 0 and cannot be:
`reasoning_effort`, `reasoning_output_tokens`, `reasoning_token_count`,
`reasoning_summary` and the metric name `codex.turn.token_usage.reasoning_output_tokens`
are field names the matrix depends on, and deleting them would delete two of its rows.

## What was deliberately kept

- The TELLTALEFAKE probes from `secrets_note.txt`, wherever a surface carried them. A
  fixture without the probe cannot show the probe was removed.
- **`command_execution.aggregated_output` is command output and it is still here.** The
  fixture's job is to show what the surface carries. THE SANITIZER WRITTEN LATER MUST
  DROP IT: it is the stdout of an arbitrary command run in the owner's workspace, and
  nothing about the exec stream bounds what ends up in it.
