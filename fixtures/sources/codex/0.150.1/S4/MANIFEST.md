# S4 fixture manifest

Codex 0.150.1. Captured by `experiments/E02/run.py --scenario S4`, rewritten
by `experiments/E02/sanitize_fixture.py`. Raw capture: `experiments/E02/out/S4/`
(gitignored). Command line and every timing number: `out/S4/meta.json`.

Prompt: Now say which file you would edit to guard against zero, without editing.

Exit code 0, wall time 7.769s.

## Requests and rows per surface

| file | rows captured | rows kept | reasoning dropped | bytes |
|---|---|---|---|---|
| `exec.jsonl` | 9 | 9 | 0 | 1361 |
| `hooks.jsonl` | 4 | 4 | 0 | 2382 |
| `hooks_local.jsonl` | 4 | 4 | 0 | 2072 |
| `otel_logs.jsonl` | 8 | 8 | 0 | 33286 |
| `otel_metrics.jsonl` | 1 | 1 | 0 | 49884 |
| `otel_traces.jsonl` | 2 | 2 | 0 | 41941 |
| `rollout.jsonl` | 38 | 38 | 0 | 38138 |

## Record kinds

- `exec.jsonl`: `item.completed:agent_message` 1, `item.completed:error` 5, `thread.started` 1, `turn.completed` 1, `turn.started` 1
- `hooks.jsonl`: `request /hooks/codex` 4
- `hooks_local.jsonl`: `hook SessionEnd` 1, `hook SessionStart` 1, `hook Stop` 1, `hook UserPromptSubmit` 1
- `otel_logs.jsonl`: `request /` 8
- `otel_metrics.jsonl`: `request /` 1
- `otel_traces.jsonl`: `request /` 2
- `rollout.jsonl`: `event_msg/item_completed` 8, `event_msg/task_complete` 2, `event_msg/task_started` 2, `event_msg/thread_settings_applied` 1, `event_msg/token_count` 5, `response_item/custom_tool_call` 2, `response_item/custom_tool_call_output` 2, `response_item/message` 11, `session_meta` 1, `turn_context` 2, `world_state` 2

## What was replaced

Counts are of individual substitutions, not of rows.

- `<email>`: 17
- `<home>`: 9
- `<host.name>`: 8
- `<repo>`: 61
- `<user.account_id>`: 17
- `<user.email>`: 17
- `agents_md`: 1
- `base_instructions`: 1
- `developer message`: 7
- `host_skills`: 2
- `metrics trimmed`: 31
- `permissions`: 1
- `spans trimmed`: 859

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
