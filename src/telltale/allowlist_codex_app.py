"""The OpenAI Codex desktop app's half of the one allowlist table, W11-T2.

Not a second table: `allowlist.py` calls `codex_app_tables()` and merges the result into
ALLOWLIST, the same cut allowlist_codex.py made. Four observation types, one per
attribute-key set the orchestrator measured on 2026-09-14 (two schema-only sniffs of the
app's live OTLP traffic, key names only). providers/codex_app.py builds an observation
only for a record whose keys are EXACTLY one of those four sets, so every field below is
a field that was seen, and no field that was not seen can reach this table.

What each set keeps, and what it does not:

- `conversation.id` is consumed by the parser into the session column, never stored as
  a payload field.
- `arguments` and `output` (shape 1) are in sanitize.NEVER_PERSIST, and
  `user.account_id` and `user.email` (all four) become `user_account_id` and
  `user_email`, which are in sanitize.REFUSED. None of the four is listed here, and
  listing one would change nothing: those two sets win over this table.
- No field is Kind.PATH, Kind.COMMAND or free text. Kind.SCALAR here is only ever a
  flag: the parser admits a SCALAR value only when it is a boolean (or the string
  "true" or "false", which is how E02 measured the CLI sending `success`), and drops
  anything else with `not_a_bool`. So the longest string any field of this provider can
  store is an ID at sanitize.MAX_ID.
"""

from __future__ import annotations

from telltale.allowlist import Kind


def codex_app_tables() -> dict[str, dict[str, Kind]]:
    """Every `codex_app.otel.*` type. The names describe a key set, not an event."""
    return _codex_app_otel()


# In all four measured sets. `event_name` is kept because it is one of the twelve
# symbolic values the parser admits and nothing else: it is the field that will say,
# from real rows, which event name carries which shape, which nothing has measured yet.
# `slug` and `originator` are symbolic (E02 on the CLI: the model slug and `codex_exec`
# on 119 of 119 records); their length on the app was not measured, and ENUM is the
# bound that truncates and records a long one instead of keeping it whole.
_COMMON: dict[str, Kind] = {
    "event_name": Kind.ENUM, "event_timestamp": Kind.ENUM, "app_version": Kind.ENUM,
    "auth_mode": Kind.ENUM, "model": Kind.ENUM, "originator": Kind.ENUM,
    "slug": Kind.ENUM, "terminal_type": Kind.ENUM,
}  # fmt: skip


def _codex_app_otel() -> dict[str, dict[str, Kind]]:
    common = _COMMON
    return {
        # Shape 1. A tool call with its duration and outcome. `mcp_server`,
        # `mcp_server_origin`, `tool_namespace` and `agent_name` are names, bounded as
        # ENUM because their length on the app was not measured. `output_truncated` is
        # a flag: pass 2 never saw it as a string, and the parser refuses one if it is.
        "codex_app.otel.tool_call_outcome": common | {
            "call_id": Kind.ID, "duration_ms": Kind.SIZE, "success": Kind.SCALAR,
            "output_truncated": Kind.SCALAR, "tool_name": Kind.ENUM,
            "tool_namespace": Kind.ENUM, "tool_result_seq": Kind.SIZE,
            "agent_name": Kind.ENUM, "mcp_server": Kind.ENUM,
            "mcp_server_origin": Kind.ENUM,
        },
        # Shape 2. Token counts and a time to first token. The names are the wire's:
        # which unit of work one record counts (a turn, a request, a response) is not
        # measured on the app, and renaming a field would be a claim about it.
        "codex_app.otel.token_usage": common | {
            "event_kind": Kind.ENUM, "ttft_ms": Kind.SIZE,
            "model_reasoning_effort": Kind.ENUM, "input_token_count": Kind.SIZE,
            "output_token_count": Kind.SIZE, "cached_token_count": Kind.SIZE,
            "cache_write_token_count": Kind.SIZE, "reasoning_token_count": Kind.SIZE,
            "tool_token_count": Kind.SIZE,
        },
        # Shape 3. A decision about a tool call, and who made it.
        "codex_app.otel.tool_decision": common | {
            "call_id": Kind.ID, "decision": Kind.ENUM, "source": Kind.ENUM,
            "tool_name": Kind.ENUM, "tool_namespace": Kind.ENUM,
        },
        # Shape 4. A timed operation with a success flag and five facts about which
        # credential SOURCE was set up, never a credential. Each `auth_env_*` answers
        # whether a variable is set or a switch is on: a flag by its name, a boolValue
        # on 33 of 33 E02 CLI records, and refused by the parser if the app ever sends
        # anything that is not a boolean.
        "codex_app.otel.authenticated_call": common | {
            "duration_ms": Kind.SIZE, "success": Kind.SCALAR,
            "auth_connection_reused": Kind.SCALAR,
            "auth_env_codex_api_key_enabled": Kind.SCALAR,
            "auth_env_codex_api_key_present": Kind.SCALAR,
            "auth_env_openai_api_key_present": Kind.SCALAR,
            "auth_env_refresh_token_url_override_present": Kind.SCALAR,
        },
    }  # fmt: skip
