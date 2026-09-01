# S3 fixture manifest

Codex 0.150.1. Captured by `experiments/E02/run.py --scenario S3`, rewritten
by `experiments/E02/sanitize_fixture.py`. Raw capture: `experiments/E02/out/S3/`
(gitignored). Command line and every timing number: `out/S3/meta.json`.

Prompt: Print the contents of secrets_note.txt with cat and then summarize what kinds of values it contains.

Exit code 0, wall time 11.244s.

## Requests and rows per surface

| file | rows captured | rows kept | reasoning dropped | bytes |
|---|---|---|---|---|
| `exec.jsonl` | 12 | 12 | 0 | 2635 |
| `hooks.jsonl` | 6 | 6 | 0 | 4496 |
| `hooks_local.jsonl` | 6 | 6 | 0 | 4032 |
| `otel_logs.jsonl` | 7 | 7 | 0 | 40200 |
| `otel_metrics.jsonl` | 1 | 1 | 0 | 52517 |
| `otel_traces.jsonl` | 3 | 3 | 0 | 62363 |
| `rollout.jsonl` | 20 | 20 | 0 | 23854 |

## Record kinds

- `exec.jsonl`: `item.completed:agent_message` 2, `item.completed:command_execution` 1, `item.completed:error` 5, `item.started:command_execution` 1, `thread.started` 1, `turn.completed` 1, `turn.started` 1
- `hooks.jsonl`: `request /hooks/codex` 6
- `hooks_local.jsonl`: `hook PostToolUse` 1, `hook PreToolUse` 1, `hook SessionEnd` 1, `hook SessionStart` 1, `hook Stop` 1, `hook UserPromptSubmit` 1
- `otel_logs.jsonl`: `request /` 7
- `otel_metrics.jsonl`: `request /` 1
- `otel_traces.jsonl`: `request /` 3
- `rollout.jsonl`: `event_msg/item_completed` 4, `event_msg/task_complete` 1, `event_msg/task_started` 1, `event_msg/token_count` 2, `response_item/custom_tool_call` 1, `response_item/custom_tool_call_output` 1, `response_item/message` 7, `session_meta` 1, `turn_context` 1, `world_state` 1

## What was replaced

Counts are of individual substitutions, not of rows.

- `<email>`: 21
- `<home>`: 13
- `<host.name>`: 7
- `<repo>`: 41
- `<user.account_id>`: 21
- `<user.email>`: 21
- `agents_md`: 1
- `base_instructions`: 1
- `developer message`: 6
- `host_skills`: 1
- `metrics trimmed`: 34
- `permissions`: 1
- `spans trimmed`: 1171

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
