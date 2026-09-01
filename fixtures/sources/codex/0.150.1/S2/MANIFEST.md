# S2 fixture manifest

Codex 0.150.1. Captured by `experiments/E02/run.py --scenario S2`, rewritten
by `experiments/E02/sanitize_fixture.py`. Raw capture: `experiments/E02/out/S2/`
(gitignored). Command line and every timing number: `out/S2/meta.json`.

Prompt: Explain how divide in pkg/calc.py handles zero. Do not edit any file.

Exit code 1, wall time 4.519s.

## Requests and rows per surface

| file | rows in | rows out | reasoning dropped | bytes |
|---|---|---|---|---|
| `exec.jsonl` | 9 | 9 | 0 | 1414 |
| `hooks.jsonl` | 3 | 3 | 0 | 1649 |
| `hooks_local.jsonl` | 3 | 3 | 0 | 1416 |
| `other.jsonl` | 4 | 4 | 0 | 27588 |
| `rollout.jsonl` | 12 | 12 | 0 | 15703 |

## Record kinds

- `exec.jsonl`: `error` 1, `item.completed:error` 5, `thread.started` 1, `turn.failed` 1, `turn.started` 1
- `hooks.jsonl`: `request /hooks/codex` 3
- `hooks_local.jsonl`: `hook SessionEnd` 1, `hook SessionStart` 1, `hook UserPromptSubmit` 1
- `other.jsonl`: `request /` 4
- `rollout.jsonl`: `event_msg/item_completed` 1, `event_msg/task_complete` 1, `event_msg/task_started` 1, `event_msg/token_count` 1, `response_item/message` 5, `session_meta` 1, `turn_context` 1, `world_state` 1

## What was replaced

Counts are of individual substitutions, not of rows.

- `<email>`: 15
- `<home>`: 7
- `<host.name>`: 4
- `<repo>`: 31
- `<user.account_id>`: 15
- `<user.email>`: 15
- `agents_md`: 1
- `base_instructions`: 1
- `developer message`: 6
- `host_skills`: 1
- `permissions`: 1

`<repo>` is the scenario's throwaway repository copy, `<home>` the machine's home
directory. `user.account_id` and `user.email` are on EVERY OTel log record and are
replaced with `00000000-0000-4000-8000-000000000000` and `capture@example.invalid`. `host.name` becomes `<host>`.
`base_instructions` (the Codex system prompt) and `agents_md` (whatever AGENTS.md was in
scope, which on the capture machine is the owner's own) are replaced by a placeholder
that keeps the character count.

## What was stripped

0 reasoning rows, across every surface. Telltale never persists reasoning, so
the fixture does not carry it either. `grep -rc reasoning` over this tree must be 0.

## What was deliberately kept

- The TELLTALEFAKE probes from `secrets_note.txt`, wherever a surface carried them. A
  fixture without the probe cannot show the probe was removed.
- **`command_execution.aggregated_output` is command output and it is still here.** The
  fixture's job is to show what the surface carries. THE SANITIZER WRITTEN LATER MUST
  DROP IT: it is the stdout of an arbitrary command run in the owner's workspace, and
  nothing about the exec stream bounds what ends up in it.
