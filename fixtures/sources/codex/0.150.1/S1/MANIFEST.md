# S1 fixture manifest

Codex 0.150.1. Captured by `experiments/E02/run.py --scenario S1`, rewritten
by `experiments/E02/sanitize_fixture.py`. Raw capture: `experiments/E02/out/S1/`
(gitignored). Command line and every timing number: `out/S1/meta.json`.

Prompt: Run the tests with `uv run pytest`, fix the bug in pkg/calc.py so the failing test passes, run the tests again, and stop.

Exit code 0, wall time 33.126s.

## Requests and rows per surface

| file | rows captured | rows kept | reasoning dropped | bytes |
|---|---|---|---|---|
| `exec.jsonl` | 20 | 20 | 0 | 6058 |
| `hooks.jsonl` | 14 | 14 | 0 | 12958 |
| `hooks_local.jsonl` | 14 | 14 | 0 | 11878 |
| `otel_logs.jsonl` | 14 | 14 | 0 | 88573 |
| `otel_metrics.jsonl` | 1 | 1 | 0 | 52529 |
| `otel_traces.jsonl` | 8 | 8 | 0 | 169655 |
| `rollout.jsonl` | 45 | 39 | 6 | 44337 |

## Record kinds

- `exec.jsonl`: `item.completed:agent_message` 4, `item.completed:command_execution` 3, `item.completed:error` 5, `item.completed:file_change` 1, `item.started:command_execution` 3, `item.started:file_change` 1, `thread.started` 1, `turn.completed` 1, `turn.started` 1
- `hooks.jsonl`: `request /hooks/codex` 14
- `hooks_local.jsonl`: `hook PostToolUse` 5, `hook PreToolUse` 5, `hook SessionEnd` 1, `hook SessionStart` 1, `hook Stop` 1, `hook UserPromptSubmit` 1
- `otel_logs.jsonl`: `request /` 14
- `otel_metrics.jsonl`: `request /` 1
- `otel_traces.jsonl`: `request /` 8
- `rollout.jsonl`: `event_msg/item_completed` 9, `event_msg/task_complete` 1, `event_msg/task_started` 1, `event_msg/token_count` 6, `response_item/custom_tool_call` 5, `response_item/custom_tool_call_output` 5, `response_item/message` 9, `session_meta` 1, `turn_context` 1, `world_state` 1

## What was replaced

Counts are of individual substitutions, not of rows.

- `<email>`: 42
- `<home>`: 53
- `<host.name>`: 14
- `<repo>`: 106
- `<repo> (key)`: 1
- `<user.account_id>`: 42
- `<user.email>`: 42
- `agents_md`: 1
- `base_instructions`: 1
- `developer message`: 6
- `host_skills`: 1
- `metrics trimmed`: 34
- `permissions`: 1
- `spans trimmed`: 2919

`<repo>` is the scenario's throwaway repository copy, `<home>` the machine's home
directory. `user.account_id` and `user.email` are on EVERY OTel log record and are
replaced with `00000000-0000-4000-8000-000000000000` and `capture@example.invalid`. `host.name` becomes `<host>`.
`base_instructions` (the Codex system prompt) and `agents_md` (whatever AGENTS.md was in
scope, which on the capture machine is the owner's own) are replaced by a placeholder
that keeps the character count.

## What was stripped

6 model-thinking rows, across every surface. Telltale never persists them, so
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
