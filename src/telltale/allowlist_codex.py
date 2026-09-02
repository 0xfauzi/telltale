"""The Codex half of the one allowlist table, measured by E02 and W1-T3.

Not a second table: `allowlist.py` calls `codex_tables()` and merges the result into
ALLOWLIST, and nothing else imports this module. It is a separate FILE because
allowlist.py reached the 800-line ratchet when these 40 observation types arrived, which
is the same cut W0-T4 made when the table outgrew sanitize.py. The Claude tables stay
where they are until they force the same decision.

Every entry here is a field E02 saw on a real record of Codex CLI 0.150.1, or a scalar
that `providers/codex.py` lifts out of a container that never reaches the sanitizer.
Nothing is copied from documentation: docs/design/00-digest.md 2.2 says the Codex OTel
vocabulary is undocumented, and it was right.
"""

from __future__ import annotations

from telltale.allowlist import Kind


def codex_tables() -> dict[str, dict[str, Kind]]:
    """Every `codex.exec.*`, `codex.otel.*` and `codex.rollout.*` type.

    The `codex.hook.*` types are built in allowlist.py, beside Claude's, because the two
    providers share one hook field table and one builder.
    """
    return _codex_exec() | _codex_otel() | _codex_rollout()


# What E02 measured on a Codex item, on the exec stream and in the rollout alike. The
# parser lifts these out and copies no container: aggregated_output, formatted_output,
# stdout, stderr and unified_diff sit beside them and are the stdout of an arbitrary
# command in the owner's workspace. `phase` is `started` or `completed`, and an
# item.started for a file_change arrives BEFORE the write.
_CODEX_ITEM: dict[str, Kind] = {
    "item_id": Kind.ID, "item_type": Kind.ENUM, "phase": Kind.ENUM,
    "status": Kind.ENUM, "turn_id": Kind.ID, "command": Kind.COMMAND,
    "exit_code": Kind.SIZE, "duration_ms": Kind.SIZE, "cwd": Kind.PATH,
    "source": Kind.ENUM, "path": Kind.PATH, "kind": Kind.ENUM,
    "server": Kind.ENUM, "tool": Kind.ENUM,
}  # fmt: skip

# The four token names `turn.completed` carries, plus the two the rollout adds.
_CODEX_TOKENS: dict[str, Kind] = {
    "input_tokens": Kind.SIZE, "cached_input_tokens": Kind.SIZE,
    "cache_write_input_tokens": Kind.SIZE, "output_tokens": Kind.SIZE,
    "reasoning_output_tokens": Kind.SIZE, "total_tokens": Kind.SIZE,
}  # fmt: skip


def _codex_exec() -> dict[str, dict[str, Kind]]:
    return {
        "codex.exec.thread_started": {"thread_id": Kind.ID},
        "codex.exec.turn_started": {"turn_id": Kind.ID},
        # turn_id is listed and never arrives: E02 measured neither turn.started nor
        # turn.completed carrying one, which is why exec usage cannot be joined to a
        # rollout turn. Unknown stays absent rather than being invented.
        "codex.exec.turn_completed": _CODEX_TOKENS | {"turn_id": Kind.ID},
        "codex.exec.turn_failed": {"turn_id": Kind.ID, "error": Kind.SCALAR},
        "codex.exec.item": _CODEX_ITEM,
        # `error` rather than `message`: the parser lifts the one value out of the item,
        # and the container name stays in NEVER_PERSIST so a future field inside it
        # cannot ride along.
        "codex.exec.error": {
            "item_id": Kind.ID, "error": Kind.SCALAR, "code": Kind.ENUM,
        },
    }  # fmt: skip


# Attributes every Codex OTel log record carries (E02, 211 records over 6 scenarios).
# user.account_id and user.email are deliberately absent AND are removed by the parser
# before the sanitizer sees them: they arrive on every record of every session, so
# leaving them to the allowlist gate would write one diagnostic per OTLP request.
_CODEX_OTEL_COMMON: dict[str, Kind] = {
    "event_timestamp": Kind.ENUM, "app_version": Kind.ENUM,
    "terminal_type": Kind.ENUM, "service_version": Kind.ENUM, "model": Kind.ENUM,
    "slug": Kind.ENUM, "originator": Kind.ENUM, "auth_mode": Kind.ENUM,
}  # fmt: skip

# Which credential SOURCE was used, never a credential. `auth.mode` on api_request and
# `auth_mode` everywhere else are the same fact under two spellings (DRIFT).
_CODEX_AUTH: dict[str, Kind] = {
    "auth_header_attached": Kind.SCALAR, "auth_header_name": Kind.ENUM,
    "auth_connection_reused": Kind.SCALAR, "auth_retry_after_unauthorized": Kind.SCALAR,
    "auth_env_openai_api_key_present": Kind.SCALAR,
    "auth_env_codex_api_key_present": Kind.SCALAR,
    "auth_env_codex_api_key_enabled": Kind.SCALAR,
    "auth_env_refresh_token_url_override_present": Kind.SCALAR,
}  # fmt: skip

# 37 metric names over the six exporting scenarios, and every dimension they carry.
# `value` is the counter's value or the histogram's SUM: E02 measured that
# codex.turn.token_usage is one histogram dimensioned by token_type and that its buckets
# are lossy, so the sum is the only number here and the buckets are not stored.
_CODEX_METRIC: dict[str, Kind] = {
    "name": Kind.ENUM, "value": Kind.SCALAR, "unit": Kind.ENUM,
    "service_version": Kind.ENUM,
    "app_version": Kind.ENUM, "auth_mode": Kind.ENUM, "model": Kind.ENUM,
    "originator": Kind.ENUM, "session_source": Kind.ENUM, "token_type": Kind.ENUM,
    "status": Kind.ENUM, "success": Kind.SCALAR, "outcome": Kind.ENUM,
    "result": Kind.ENUM, "error": Kind.SCALAR, "phase": Kind.ENUM,
    "mode": Kind.ENUM, "kind": Kind.ENUM, "trigger": Kind.ENUM, "cache": Kind.ENUM,
    "db": Kind.ENUM, "server_kind": Kind.ENUM, "source": Kind.ENUM,
    "path": Kind.PATH, "is_git": Kind.SCALAR, "sandbox": Kind.ENUM,
    "sandbox_policy": Kind.ENUM, "tool": Kind.ENUM, "command_category": Kind.ENUM,
    "tty": Kind.SCALAR, "tmp_mem_enabled": Kind.SCALAR, "active": Kind.SCALAR,
    "config_use_memories": Kind.SCALAR, "feature_enabled": Kind.SCALAR,
    "has_citations": Kind.SCALAR, "read_allowed": Kind.SCALAR,
    "execution_mode": Kind.ENUM, "handler_type": Kind.ENUM, "hook_name": Kind.ENUM,
}  # fmt: skip


def _codex_otel() -> dict[str, dict[str, Kind]]:
    """The eleven log events E02 found, plus the unnamed records and the metric.

    The digest names none of these: 2.2 says the vocabulary is undocumented and that
    E02 would capture it, and this is what it captured.
    """
    common, auth = _CODEX_OTEL_COMMON, _CODEX_AUTH
    return {
        "codex.otel.api_request": common | auth | {
            "attempt": Kind.SIZE, "duration_ms": Kind.SIZE, "endpoint": Kind.ENUM,
            "success": Kind.SCALAR, "http_response_status_code": Kind.SIZE,
        },
        "codex.otel.conversation_starts": common | auth | {
            "approval_policy": Kind.ENUM, "sandbox_policy": Kind.ENUM,
            "reasoning_effort": Kind.ENUM, "reasoning_summary": Kind.ENUM,
            "provider_name": Kind.ENUM, "mcp_servers": Kind.ENUM,
        },
        "codex.otel.sandbox_outcome": common | {
            "call_id": Kind.ID, "initial_duration_ms": Kind.SIZE,
            "outcome": Kind.ENUM, "tool_name": Kind.ENUM,
        },
        # The per-model-response usage event, which E02's matrix reads as unavailable
        # (DRIFT). The names are the wire's: tool_token_count was input plus output on
        # every record measured and renaming it would be a claim about what it means.
        "codex.otel.sse_event": common | {
            "event_kind": Kind.ENUM, "error_message": Kind.SCALAR,
            "ttft_ms": Kind.SIZE, "model_reasoning_effort": Kind.ENUM,
            "input_token_count": Kind.SIZE, "output_token_count": Kind.SIZE,
            "cached_token_count": Kind.SIZE, "cache_write_token_count": Kind.SIZE,
            "reasoning_token_count": Kind.SIZE, "tool_token_count": Kind.SIZE,
        },
        "codex.otel.startup_phase": common | {
            "duration_ms": Kind.SIZE, "startup_phase": Kind.ENUM,
            "startup_status": Kind.ENUM,
        },
        "codex.otel.tool_decision": common | {
            "call_id": Kind.ID, "decision": Kind.ENUM, "source": Kind.ENUM,
            "tool_name": Kind.ENUM, "tool_namespace": Kind.ENUM,
        },
        # `command` and `cwd` are lifted OUT of `arguments`, a JSON string that also
        # holds the whole patch when the tool is apply_patch; `output` holds what the
        # command printed. Both containers are in NEVER_PERSIST and neither is listed.
        "codex.otel.tool_result": common | {
            "call_id": Kind.ID, "duration_ms": Kind.SIZE, "success": Kind.SCALAR,
            "output_truncated": Kind.SCALAR, "tool_name": Kind.ENUM,
            "tool_namespace": Kind.ENUM, "tool_result_seq": Kind.SIZE,
            "agent_name": Kind.ENUM, "mcp_server": Kind.ENUM,
            "mcp_server_origin": Kind.ENUM, "command": Kind.COMMAND,
            "cwd": Kind.PATH,
        },
        "codex.otel.turn_ttft": common | {"duration_ms": Kind.SIZE},
        # `prompt` arrives holding the literal [REDACTED] with otel.log_user_prompt off,
        # and is in NEVER_PERSIST whatever it holds. Only the length is kept.
        "codex.otel.user_prompt": common | {"prompt_length": Kind.SIZE},
        "codex.otel.websocket_connect": common | auth | {
            "duration_ms": Kind.SIZE, "endpoint": Kind.ENUM, "success": Kind.SCALAR,
        },
        "codex.otel.websocket_request": common | auth | {
            "duration_ms": Kind.SIZE, "success": Kind.SCALAR,
        },
        # A log record with no event.name: one of the CLI's own Rust tracing lines. Its
        # body is a sentence and NEVER_PERSIST takes it; the version is what is left.
        "codex.otel.log": {"service_version": Kind.ENUM},
        "codex.otel.metric": _CODEX_METRIC,
    }  # fmt: skip


def _codex_rollout() -> dict[str, dict[str, Kind]]:
    """The rollout file, which design 6.3 calls a wave 2 backfill surface.

    Listed now because the same lines are the only place `model_context_window` exists
    (E02: `event_msg/task_started`, not `token_count.info` as the digest says), and
    `--ephemeral` writes no rollout at all, so an ephemeral session has no occupancy
    denominator and must report None rather than a constant.

    Four containers are deliberately unlisted, so every field inside them is dropped and
    NAMED: `permission_profile` and `file_system_sandbox_policy` (nested
    absolute paths), `base_instructions` (the Codex system prompt) and
    `parsed_cmd` (the command again).
    """
    return {
        "codex.rollout.session_meta": {
            "session_id": Kind.ID, "id": Kind.ID, "timestamp": Kind.ENUM,
            "cwd": Kind.PATH, "originator": Kind.ENUM, "cli_version": Kind.ENUM,
            "source": Kind.ENUM, "thread_source": Kind.ENUM,
            "model_provider": Kind.ENUM, "history_mode": Kind.ENUM,
            "context_window": Kind.SCALAR,
            # HEAD when the session started, which is NOT a commit the agent made. No
            # Codex surface reports one, so this is never a provider_reported link.
            "git_commit_hash": Kind.ID, "git_branch": Kind.ENUM,
        },
        "codex.rollout.turn_context": {
            "turn_id": Kind.ID, "cwd": Kind.PATH, "workspace_roots": Kind.PATH,
            "model": Kind.ENUM, "effort": Kind.ENUM, "approval_policy": Kind.ENUM,
            "approvals_reviewer": Kind.ENUM, "sandbox_policy": Kind.SCALAR,
            "collaboration_mode": Kind.SCALAR, "personality": Kind.ENUM,
            "realtime_active": Kind.SCALAR, "multi_agent_version": Kind.ENUM,
            "comp_hash": Kind.ID, "current_date": Kind.ENUM, "timezone": Kind.ENUM,
        },
        # `state` holds AGENTS.md, the permission table and the environment list. The
        # parser consumes it whole; only whether this record is a full snapshot is kept.
        "codex.rollout.world_state": {"full": Kind.SCALAR},
        "codex.rollout.event_msg.task_started": {
            "turn_id": Kind.ID, "trace_id": Kind.ID, "started_at": Kind.SIZE,
            "model_context_window": Kind.SIZE, "collaboration_mode_kind": Kind.ENUM,
        },
        "codex.rollout.event_msg.task_complete": {
            "turn_id": Kind.ID, "started_at": Kind.SIZE, "completed_at": Kind.SIZE,
            "duration_ms": Kind.SIZE, "time_to_first_token_ms": Kind.SIZE,
        },
        # Both usages are dicts of the six token names; SIZE cleans every value in one
        # and drops anything that is not a number. The model_context_window here is the
        # one the digest points at, and E02 measured it null on every turn: it is kept
        # under this type and never copied onto task_started's, which has the value.
        "codex.rollout.event_msg.token_count": {
            "last_token_usage": Kind.SIZE, "total_token_usage": Kind.SIZE,
            "model_context_window": Kind.SIZE, "rate_limits": Kind.SCALAR,
        },
        "codex.rollout.event_msg.thread_settings_applied": {
            "model": Kind.ENUM, "model_provider_id": Kind.ENUM,
            "service_tier": Kind.ENUM, "approval_policy": Kind.ENUM,
        },
        "codex.rollout.event_msg.item_completed": _CODEX_ITEM | {
            "thread_id": Kind.ID, "started_at_ms": Kind.SIZE,
            "completed_at_ms": Kind.SIZE,
        },
        "codex.rollout.response_item.message": {
            "id": Kind.ID, "role": Kind.ENUM, "phase": Kind.ENUM,
        },
        "codex.rollout.response_item.custom_tool_call": {
            "id": Kind.ID, "call_id": Kind.ID, "name": Kind.ENUM,
            "status": Kind.ENUM,
        },
        "codex.rollout.response_item.custom_tool_call_output": {
            "id": Kind.ID, "call_id": Kind.ID,
        },
    }  # fmt: skip
