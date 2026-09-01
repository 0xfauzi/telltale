# S6 fixture manifest

Codex 0.150.1. Captured by `experiments/E02/run.py --scenario S6`, rewritten
by `experiments/E02/sanitize_fixture.py`. Raw capture: `experiments/E02/out/S6/`
(gitignored). Command line and every timing number: `out/S6/meta.json`.

Prompt: Fix the bug in pkg/calc.py and commit it with the message E02.

Exit code 0, wall time 71.929s.

## Requests and rows per surface

| file | rows captured | rows kept | reasoning dropped | bytes |
|---|---|---|---|---|
| `exec.jsonl` | 26 | 26 | 0 | 8101 |
| `hooks.jsonl` | 19 | 19 | 0 | 17627 |
| `hooks_local.jsonl` | 19 | 19 | 0 | 16158 |
| `otel_logs.jsonl` | 18 | 18 | 0 | 125179 |
| `otel_metrics.jsonl` | 2 | 2 | 0 | 110092 |
| `otel_traces.jsonl` | 17 | 17 | 0 | 363692 |
| `rollout.jsonl` | 74 | 58 | 16 | 57965 |

## Record kinds

- `exec.jsonl`: `item.completed:agent_message` 8, `item.completed:command_execution` 4, `item.completed:error` 5, `item.completed:file_change` 1, `item.started:command_execution` 4, `item.started:file_change` 1, `thread.started` 1, `turn.completed` 1, `turn.started` 1
- `hooks.jsonl`: `request /hooks/codex` 19
- `hooks_local.jsonl`: `hook PostToolUse` 7, `hook PreToolUse` 8, `hook SessionEnd` 1, `hook SessionStart` 1, `hook Stop` 1, `hook UserPromptSubmit` 1
- `otel_logs.jsonl`: `request /` 18
- `otel_metrics.jsonl`: `request /` 2
- `otel_traces.jsonl`: `request /` 17
- `rollout.jsonl`: `event_msg/item_completed` 14, `event_msg/task_complete` 1, `event_msg/task_started` 1, `event_msg/token_count` 10, `response_item/custom_tool_call` 8, `response_item/custom_tool_call_output` 8, `response_item/message` 13, `session_meta` 1, `turn_context` 1, `world_state` 1

## What was replaced

Counts are of individual substitutions, not of rows.

- `<email>`: 60
- `<home>`: 54
- `<host.name>`: 18
- `<repo>`: 151
- `<repo> (key)`: 1
- `<user.account_id>`: 60
- `<user.email>`: 60
- `<user>`: 9
- `agents_md`: 1
- `base_instructions`: 1
- `developer message`: 6
- `host_skills`: 1
- `metrics trimmed`: 29
- `permissions`: 1
- `spans trimmed`: 5367

`<repo>` is the scenario's throwaway repository copy, `<home>` the machine's home
directory. `user.account_id` and `user.email` are on EVERY OTel log record and are
replaced with `00000000-0000-4000-8000-000000000000` and `capture@example.invalid`. `host.name` becomes `<host>`.
`base_instructions` (the Codex system prompt) and `agents_md` (whatever AGENTS.md was in
scope, which on the capture machine is the owner's own) are replaced by a placeholder
that keeps the character count.

## What was stripped

16 model-thinking rows, across every surface. Telltale never persists them, so
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
