# S2: explore-only (PILOT)

Captured by `experiments/E01/run.py --scenario S2` from Claude Code 2.1.257 (Claude Code) at 2026-09-01T20:03:32.966572+00:00, sanitized by `experiments/E01/sanitize_fixture.py`. The run's own numbers (wall time, exit code, usage, late-export window) are in `experiments/E01/out/S2/meta.json`, which is not committed.

Prompt: Explain how divide in pkg/calc.py handles zero. Do not edit any file.

## Requests per surface

| surface | requests |
|---|---|
| otel_logs | 4 |
| otel_metrics | 2 |
| hooks | 6 |
| stream | 17 |

## Observations by event name

| surface | name | count |
|---|---|---|
| otel_logs | claude_code.api_request | 3 |
| otel_logs | claude_code.assistant_response | 1 |
| otel_logs | claude_code.hook_execution_complete | 7 |
| otel_logs | claude_code.hook_execution_start | 7 |
| otel_logs | claude_code.hook_registered | 26 |
| otel_logs | claude_code.mcp_server_connection | 4 |
| otel_logs | claude_code.plugin_loaded | 3 |
| otel_logs | claude_code.tool_decision | 2 |
| otel_logs | claude_code.tool_result | 2 |
| otel_logs | claude_code.user_prompt | 1 |
| otel_metrics | claude_code.active_time.total | 1 |
| otel_metrics | claude_code.cost.usage | 2 |
| otel_metrics | claude_code.session.count | 1 |
| otel_metrics | claude_code.token.usage | 8 |
| hooks | PostToolUse | 2 |
| hooks | PreToolUse | 2 |
| hooks | SessionEnd | 1 |
| hooks | Stop | 1 |
| stream | assistant | 4 |
| stream | rate_limit_event | 1 |
| stream | result:success | 1 |
| stream | system:hook_response | 3 |
| stream | system:hook_started | 3 |
| stream | system:init | 1 |
| stream | system:thinking_tokens | 2 |
| stream | user | 2 |

## What was replaced

| original | replacement | occurrences |
|---|---|---|
| the machine path behind `<repo>` | `<repo>` | 13 |
| the machine path behind `<repo-slug>` | `<repo-slug>` | 0 |
| the identity value replaced by `telltale-fake-user-id` | `telltale-fake-user-id` | 68 |
| the identity value replaced by `00000000-0000-4000-8000-000000000001` | `00000000-0000-4000-8000-000000000001` | 68 |
| the identity value replaced by `00000000-0000-4000-8000-000000000002` | `00000000-0000-4000-8000-000000000002` | 68 |
| the identity value replaced by `user_telltalefakeaccount` | `user_telltalefakeaccount` | 68 |
| the identity value replaced by `user@telltale.invalid` | `user@telltale.invalid` | 68 |
| the machine path behind `<home>` | `<home>` | 10 |
| the machine path behind `<home-slug>` | `<home-slug>` | 7 |
| the machine path behind `<user>` | `<user>` | 0 |

Nothing else was removed. Prompt text, assistant text and tool output are as captured, and the three TELLTALEFAKE credential probes are left in place: a fixture without the probe cannot show that the probe was removed.
