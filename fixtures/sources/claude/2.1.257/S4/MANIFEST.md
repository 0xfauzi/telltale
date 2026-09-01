# S4: subagent

Captured by `experiments/E01/run.py --scenario S4` from Claude Code 2.1.257 (Claude Code) at 2026-09-01T20:05:33.095520+00:00, sanitized by `experiments/E01/sanitize_fixture.py`. The run's own numbers (wall time, exit code, usage, late-export window) are in `experiments/E01/out/S4/meta.json`, which is not committed.

Prompt: Use a subagent to list the files in this project, then report the list.

## Requests per surface

| surface | requests |
|---|---|
| otel_logs | 9 |
| otel_metrics | 6 |
| hooks | 14 |
| stream | 33 |

## Observations by event name

| surface | name | count |
|---|---|---|
| otel_logs | claude_code.api_request | 7 |
| otel_logs | claude_code.assistant_response | 2 |
| otel_logs | claude_code.hook_execution_complete | 15 |
| otel_logs | claude_code.hook_execution_start | 15 |
| otel_logs | claude_code.hook_registered | 26 |
| otel_logs | claude_code.mcp_server_connection | 4 |
| otel_logs | claude_code.plugin_loaded | 3 |
| otel_logs | claude_code.subagent_completed | 1 |
| otel_logs | claude_code.tool_decision | 5 |
| otel_logs | claude_code.tool_result | 5 |
| otel_logs | claude_code.user_prompt | 1 |
| otel_metrics | claude_code.active_time.total | 1 |
| otel_metrics | claude_code.cost.usage | 7 |
| otel_metrics | claude_code.session.count | 1 |
| otel_metrics | claude_code.token.usage | 28 |
| hooks | PostToolUse | 5 |
| hooks | PreToolUse | 5 |
| hooks | SessionEnd | 1 |
| hooks | Stop | 1 |
| hooks | SubagentStart | 1 |
| hooks | SubagentStop | 1 |
| stream | assistant | 7 |
| stream | rate_limit_event | 2 |
| stream | result:success | 1 |
| stream | system:hook_progress | 1 |
| stream | system:hook_response | 3 |
| stream | system:hook_started | 3 |
| stream | system:init | 1 |
| stream | system:task_notification | 1 |
| stream | system:task_progress | 4 |
| stream | system:task_started | 1 |
| stream | system:task_updated | 1 |
| stream | system:thinking_tokens | 2 |
| stream | user | 6 |

## What was replaced

| original | replacement | occurrences |
|---|---|---|
| the machine path behind `<repo>` | `<repo>` | 28 |
| the machine path behind `<repo-slug>` | `<repo-slug>` | 0 |
| the identity value replaced by `telltale-fake-user-id` | `telltale-fake-user-id` | 121 |
| the identity value replaced by `00000000-0000-4000-8000-000000000001` | `00000000-0000-4000-8000-000000000001` | 121 |
| the identity value replaced by `00000000-0000-4000-8000-000000000002` | `00000000-0000-4000-8000-000000000002` | 121 |
| the identity value replaced by `user_telltalefakeaccount` | `user_telltalefakeaccount` | 121 |
| the identity value replaced by `user@telltale.invalid` | `user@telltale.invalid` | 121 |
| the machine path behind `<home>` | `<home>` | 21 |
| the machine path behind `<home-slug>` | `<home-slug>` | 19 |
| the machine path behind `<user>` | `<user>` | 20 |

Nothing else was removed. Prompt text, assistant text and tool output are as captured, and the three TELLTALEFAKE credential probes are left in place: a fixture without the probe cannot show that the probe was removed.
