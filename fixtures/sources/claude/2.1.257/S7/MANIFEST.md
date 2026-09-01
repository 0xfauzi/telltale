# S7: compaction attempt

Captured by `experiments/E01/run.py --scenario S7` from Claude Code 2.1.257 (Claude Code) at 2026-09-01T20:07:19.510819+00:00, sanitized by `experiments/E01/sanitize_fixture.py`. The run's own numbers (wall time, exit code, usage, late-export window) are in `experiments/E01/out/S7/meta.json`, which is not committed.

Prompt: First read every file in the project twice, one file per Read call. Run the tests with `uv run pytest`, fix the bug in pkg/calc.py so the failing test passes, run the tests again, and stop.

## Requests per surface

| surface | requests |
|---|---|
| otel_logs | 17 |
| otel_metrics | 7 |
| hooks | 47 |
| stream | 100 |

## Observations by event name

| surface | name | count |
|---|---|---|
| otel_logs | claude_code.api_request | 8 |
| otel_logs | claude_code.assistant_response | 3 |
| otel_logs | claude_code.compaction | 4 |
| otel_logs | claude_code.hook_execution_complete | 44 |
| otel_logs | claude_code.hook_execution_start | 44 |
| otel_logs | claude_code.hook_registered | 26 |
| otel_logs | claude_code.mcp_server_connection | 4 |
| otel_logs | claude_code.plugin_loaded | 3 |
| otel_logs | claude_code.tool_decision | 18 |
| otel_logs | claude_code.tool_result | 18 |
| otel_logs | claude_code.user_prompt | 1 |
| otel_metrics | claude_code.active_time.total | 1 |
| otel_metrics | claude_code.cost.usage | 8 |
| otel_metrics | claude_code.session.count | 1 |
| otel_metrics | claude_code.token.usage | 32 |
| hooks | PostCompact | 3 |
| hooks | PostToolUse | 17 |
| hooks | PostToolUseFailure | 1 |
| hooks | PreCompact | 4 |
| hooks | PreToolUse | 18 |
| hooks | SessionEnd | 1 |
| hooks | SubagentStop | 3 |
| stream | assistant | 24 |
| stream | rate_limit_event | 2 |
| stream | result:success | 1 |
| stream | system:compact_boundary | 3 |
| stream | system:hook_progress | 4 |
| stream | system:hook_response | 12 |
| stream | system:hook_started | 12 |
| stream | system:init | 1 |
| stream | system:status | 8 |
| stream | system:thinking_tokens | 12 |
| stream | user | 21 |

## What was replaced

| original | replacement | occurrences |
|---|---|---|
| the machine path behind `<repo>` | `<repo>` | 152 |
| the machine path behind `<repo-slug>` | `<repo-slug>` | 0 |
| the identity value replaced by `telltale-fake-user-id` | `telltale-fake-user-id` | 215 |
| the identity value replaced by `00000000-0000-4000-8000-000000000001` | `00000000-0000-4000-8000-000000000001` | 215 |
| the identity value replaced by `00000000-0000-4000-8000-000000000002` | `00000000-0000-4000-8000-000000000002` | 215 |
| the identity value replaced by `user_telltalefakeaccount` | `user_telltalefakeaccount` | 215 |
| the identity value replaced by `user@telltale.invalid` | `user@telltale.invalid` | 215 |
| the machine path behind `<home>` | `<home>` | 74 |
| the machine path behind `<home-slug>` | `<home-slug>` | 62 |
| the machine path behind `<user>` | `<user>` | 6 |

Nothing else was removed. Prompt text, assistant text and tool output are as captured, and the three TELLTALEFAKE credential probes are left in place: a fixture without the probe cannot show that the probe was removed.
