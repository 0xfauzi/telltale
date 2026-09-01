# S5: resume

Captured by `experiments/E01/run.py --scenario S5` from Claude Code 2.1.257 (Claude Code) at 2026-09-01T20:06:24.449997+00:00, sanitized by `experiments/E01/sanitize_fixture.py`. The run's own numbers (wall time, exit code, usage, late-export window) are in `experiments/E01/out/S5/meta.json`, which is not committed.

Prompt: Now say which file you would edit to guard against zero, without editing.

## Requests per surface

| surface | requests |
|---|---|
| otel_logs | 3 |
| otel_metrics | 1 |
| hooks | 2 |
| stream | 10 |

## Observations by event name

| surface | name | count |
|---|---|---|
| otel_logs | claude_code.api_request | 1 |
| otel_logs | claude_code.assistant_response | 1 |
| otel_logs | claude_code.hook_execution_complete | 3 |
| otel_logs | claude_code.hook_execution_start | 3 |
| otel_logs | claude_code.hook_registered | 26 |
| otel_logs | claude_code.mcp_server_connection | 4 |
| otel_logs | claude_code.plugin_loaded | 3 |
| otel_logs | claude_code.user_prompt | 1 |
| otel_metrics | claude_code.active_time.total | 1 |
| otel_metrics | claude_code.cost.usage | 1 |
| otel_metrics | claude_code.session.count | 1 |
| otel_metrics | claude_code.token.usage | 4 |
| hooks | SessionEnd | 1 |
| hooks | Stop | 1 |
| stream | assistant | 1 |
| stream | rate_limit_event | 1 |
| stream | result:success | 1 |
| stream | system:hook_response | 3 |
| stream | system:hook_started | 3 |
| stream | system:init | 1 |

## What was replaced

| original | replacement | occurrences |
|---|---|---|
| the machine path behind `<repo>` | `<repo>` | 3 |
| the machine path behind `<repo-slug>` | `<repo-slug>` | 0 |
| the identity value replaced by `telltale-fake-user-id` | `telltale-fake-user-id` | 49 |
| the identity value replaced by `00000000-0000-4000-8000-000000000001` | `00000000-0000-4000-8000-000000000001` | 49 |
| the identity value replaced by `00000000-0000-4000-8000-000000000002` | `00000000-0000-4000-8000-000000000002` | 49 |
| the identity value replaced by `user_telltalefakeaccount` | `user_telltalefakeaccount` | 49 |
| the identity value replaced by `user@telltale.invalid` | `user@telltale.invalid` | 49 |
| the machine path behind `<home>` | `<home>` | 6 |
| the machine path behind `<home-slug>` | `<home-slug>` | 3 |
| the machine path behind `<user>` | `<user>` | 0 |

Nothing else was removed. Prompt text, assistant text and tool output are as captured, and the three TELLTALEFAKE credential probes are left in place: a fixture without the probe cannot show that the probe was removed.
