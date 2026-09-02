# rollout-import: a synthetic backfill fixture

Hand-written by W2-T2 on 2026-09-02, for the same reason as
`../../claude/2.1.257/transcript/MANIFEST.md`: an import reads the owner's own sessions,
and those are never copied into this repository. The shapes were measured first over the
owner's real `~/.codex/sessions` (1452 rollout files, 2.9 GB).

## What is here

```
2026/09/02/rollout-2026-09-02T10-00-00-019adada-1111-7282-acf2-a7be9046cc69.jsonl  32 lines
```

The `YYYY/MM/DD/rollout-<timestamp>-<id>.jsonl` layout is the real one, and the day is
what `telltale import codex-rollouts --dry-run` groups by: a date is not a path, so
unlike a transcript slug it can be printed.

`session_meta.id` is the session id. Measured on 25 sampled files: 25 carry `id` and one
carries `session_id` beside it, which is why the importer reads both.

## What it exercises

| line | why it is here |
| --- | --- |
| `session_meta` | the session id, the cwd, `base_instructions` and `instructions` (unlisted, so named as dropped) |
| `turn_context` x2 | the model, the effort and the policies of two turns |
| `event_msg/task_started` | `model_context_window` 258400, the only trustworthy occupancy denominator Codex offers |
| `event_msg/token_count` x2 | `total_token_usage` and `last_token_usage`, which is where cached tokens live |
| `response_item/reasoning` x2 | `summary`, `content` and `encrypted_content`, each holding a probe |
| `event_msg/agent_reasoning` | the second spelling of reasoning (W0-E02 finding 11) |
| `response_item/custom_tool_call` and `custom_tool_call_output` | the shell call and what it printed |
| `event_msg/item_completed` CommandExecution | `aggregated_output`, holding a probe, beside the exit code and the cwd |
| `event_msg/item_completed` FileChange | `changes` keyed by path, with a `unified_diff` holding two probes |
| `response_item/function_call` and `function_call_output` | two types no allowlist entry describes, so every field of them is dropped and named. The whole patch is inside `arguments`. |
| `world_state` | the environment list, holding two identity probes |
| `event_msg/user_message`, `agent_message`, `response_item/message` | the prompt and the answer, at full length |
| `task_complete` | the turn's duration and time to first token |

## Probes

The same strings as the transcript fixture and as `../S3/`, byte for byte:
`sk-ant-api03-TELLTALEFAKE0000...`, `AKIATELLTALEFAKE00001`,
`-----BEGIN TELLTALEFAKE PRIVATE KEY-----`, the three 64-character
`TELLTALEFAKE...` values, `telltale-fake-user-id`, `user@telltale.invalid`,
`user_telltalefakeaccount` and `00000000-0000-4000-8000-000000000001`.

`<repo>` and `<home>` are the two machine paths, substituted by
`tests/integration/test_import.py::materialise`.
