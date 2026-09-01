"""The one table that says which field of which observation type may be stored.

Data, not behaviour: sanitize.py imports ALLOWLIST and Kind and does the walking. The
table lives in its own module because it is the schema of design 6.3 and it grows with
every provider surface an experiment measures, while the three gates that read it do
not. Nothing here imports sanitize.py, so the direction of the dependency stays one way.
"""

from __future__ import annotations

from enum import Enum


class Kind(Enum):
    """How a field is cleaned. Design 6.4."""

    SCALAR = "scalar"  # any JSON value; strings scrubbed and bounded to MAX_STRING
    PATH = "path"  # repo-relative, or hashed when it is outside the repository
    COMMAND = "command"  # normalized by commands.normalize
    ENUM = "enum"  # a short symbolic string
    ID = "id"  # an opaque provider identifier
    SIZE = "size"  # a count or a byte quantity; a non-number here is dropped


def _build_allowlist() -> dict[str, dict[str, Kind]]:
    """The schema of design 6.3, as the one table that decides what may be stored.

    Built rather than written out because the hook and OTel event families repeat one
    common field set, and 30 near-identical literals hide a divergence. E01 and E02 add
    the provider fields they measure to this table.

    Absent on purpose: `claude.transcript.*` and `codex.rollout.*` (backfill, wave 2)
    and `codex.otel.*` (its vocabulary is what E02 exists to find out). Until an entry
    exists, every field of those types is dropped and reported, which is the fail-closed
    behaviour spec 20.2 asks for.
    """
    table: dict[str, dict[str, Kind]] = {
        "telltale.capture_started": {
            "provider": Kind.ENUM,
            "argv_shape": Kind.ENUM,  # executable and flag names only, never values
            "content_level": Kind.SIZE,
            "surfaces_configured": Kind.ENUM,
            "provider_session_id_requested": Kind.ID,
            "task_id": Kind.ID,
            "attempt": Kind.SIZE,
            "experiment": Kind.ID,
            "worktree_id": Kind.ID,
        },
        "telltale.capture_ended": {
            "exit_code": Kind.SIZE,
            "duration_ms": Kind.SIZE,
            "surfaces_received": Kind.SIZE,
        },
        "telltale.environment": {
            "provider": Kind.ENUM,
            "runtime_version": Kind.ENUM,
            "model": Kind.ENUM,
            "effort": Kind.ENUM,
            "tool_set_hash": Kind.ID,
            "mcp_names_hash": Kind.ID,
            "instruction_hashes": Kind.PATH,  # {path: {sha256, bytes}}: keys are paths
            "settings_hash": Kind.ID,
            "sandbox_posture": Kind.ENUM,
            "capture_modes": Kind.ENUM,
            "content_level": Kind.SIZE,
        },
        "telltale.repo.identity": {
            "repo_id": Kind.ID,
            "worktree_id": Kind.ID,
            "root_hash": Kind.ID,
            "head": Kind.ID,
            "branch": Kind.ENUM,
            "base_sha": Kind.ID,
            "remote_fingerprint": Kind.ID,
            "dirty_tree_hash": Kind.ID,
        },
        "telltale.repo.snapshot": {
            "trigger": Kind.ENUM,
            "head": Kind.ID,
            "dirty_tree_hash": Kind.ID,
            "diff_hash": Kind.ID,
            "files_changed": Kind.SIZE,
            "additions": Kind.SIZE,
            "deletions": Kind.SIZE,
            "renames": Kind.SIZE,
            "staged_files": Kind.SIZE,
            "unstaged_files": Kind.SIZE,
            "untracked_count": Kind.SIZE,
            "per_file": Kind.PATH,  # [{path, additions, deletions, patch_hash}]
        },
        "telltale.repo.commit": {
            "sha": Kind.ID,
            "parents": Kind.ID,
            "tree": Kind.ID,
            "committed_ts": Kind.ENUM,
            "files_changed": Kind.SIZE,
            "additions": Kind.SIZE,
            "deletions": Kind.SIZE,
            "link_confidence": Kind.ENUM,
        },
        "external.correlation": {
            "external_system": Kind.ENUM,
            "external_run_id": Kind.ID,
            "component_id": Kind.ID,
            "task_id": Kind.ID,
            "attempt": Kind.SIZE,
            "provider_session_id": Kind.ID,
        },
        "external.outcome": {
            "kind": Kind.ENUM,
            "status": Kind.ENUM,
            "categories": Kind.ENUM,
            "timestamp": Kind.ENUM,
            "external_run_id": Kind.ID,
            "component_id": Kind.ID,
            "attempt": Kind.SIZE,
        },
        "policy.intervention": {
            "advisory_id": Kind.ID,
            "action": Kind.ENUM,
            "policy_version": Kind.ENUM,
            "external_system": Kind.ENUM,
        },
    }
    table.update(_claude_otel())
    table.update(_claude_stream())
    table.update(_codex_exec())
    table.update(_hooks())
    return table


# Attributes every Claude OTel log record carries (digest 2.1). user.id and user.email
# are deliberately absent: they identify a person and nothing here needs them, so they
# arrive as unknown fields and raise a diagnostic.
_OTEL_COMMON: dict[str, Kind] = {
    "event_name": Kind.ENUM,
    "event_timestamp": Kind.ENUM,
    "event_sequence": Kind.SIZE,
    "terminal_type": Kind.ENUM,
    "app_version": Kind.ENUM,
}


def _claude_otel() -> dict[str, dict[str, Kind]]:
    return {
        "claude.otel.api_request": _OTEL_COMMON
        | {
            "model": Kind.ENUM,
            "input_tokens": Kind.SIZE,
            "output_tokens": Kind.SIZE,
            "cache_read_tokens": Kind.SIZE,
            "cache_creation_tokens": Kind.SIZE,
            "cost_usd": Kind.SCALAR,
            "duration_ms": Kind.SIZE,
            "request_id": Kind.ID,
            "client_request_id": Kind.ID,
            "speed": Kind.ENUM,
            "effort": Kind.ENUM,
            "query_source": Kind.ENUM,
            "agent_name": Kind.ENUM,
        },
        "claude.otel.api_error": _OTEL_COMMON
        | {
            "model": Kind.ENUM,
            "status_code": Kind.SIZE,
            "attempt": Kind.SIZE,
            "duration_ms": Kind.SIZE,
            "request_id": Kind.ID,
            # The one free-text field kept at level 1. Spec 11.1 keeps error categories,
            # and the category is only in the message. Scrubbed and bounded to 512 like
            # any other string, and the privacy fixtures aim at it.
            "error": Kind.SCALAR,
        },
        "claude.otel.api_refusal": _OTEL_COMMON | {"model": Kind.ENUM},
        "claude.otel.tool_result": _OTEL_COMMON
        | {
            "tool_name": Kind.ENUM,
            "tool_use_id": Kind.ID,
            "success": Kind.SCALAR,
            "duration_ms": Kind.SIZE,
            "error_type": Kind.ENUM,
            "tool_input_size_bytes": Kind.SIZE,
            "tool_result_size_bytes": Kind.SIZE,
            "decision_source": Kind.ENUM,
            "mcp_server_scope": Kind.ENUM,
            "subagent_type": Kind.ENUM,
            "skill_name": Kind.ENUM,
            # Selected OUT of tool_parameters and tool_input by the provider parser,
            # which are themselves in NEVER_PERSIST. Design 6.3: the commit id is
            # provider_reported, the exit code is derived from the result text before
            # that text is dropped, and neither container ever reaches this module.
            "git_commit_id": Kind.ID,
            "exit_code": Kind.SIZE,
            "file_path": Kind.PATH,
            "command": Kind.COMMAND,
        },
        "claude.otel.tool_decision": _OTEL_COMMON
        | {
            "tool_name": Kind.ENUM,
            "decision": Kind.ENUM,
            "source": Kind.ENUM,
            "tool_source": Kind.ENUM,
        },
        "claude.otel.user_prompt": _OTEL_COMMON | {"prompt_length": Kind.SIZE},
        "claude.otel.assistant_response": _OTEL_COMMON | {"response_length": Kind.SIZE},
        "claude.otel.permission_mode_changed": _OTEL_COMMON
        | {"mode": Kind.ENUM, "previous_mode": Kind.ENUM},
        "claude.otel.mcp_server_connection": _OTEL_COMMON
        | {"name": Kind.ENUM, "status": Kind.ENUM, "transport": Kind.ENUM},
        "claude.otel.metric": {
            "name": Kind.ENUM,
            "value": Kind.SCALAR,
            "unit": Kind.ENUM,
            "type": Kind.ENUM,
            "model": Kind.ENUM,
            "query_source": Kind.ENUM,
            "start_type": Kind.ENUM,
        },
    }


def _claude_stream() -> dict[str, dict[str, Kind]]:
    usage: dict[str, Kind] = {
        "input_tokens": Kind.SIZE,
        "output_tokens": Kind.SIZE,
        "cache_read_input_tokens": Kind.SIZE,
        "cache_creation_input_tokens": Kind.SIZE,
    }
    return {
        "claude.stream.init": {
            "model": Kind.ENUM,
            "tools": Kind.ENUM,
            "mcp_servers": Kind.ENUM,
            "permission_mode": Kind.ENUM,
            "output_style": Kind.ENUM,
            "cwd": Kind.PATH,
            "api_key_source": Kind.ENUM,
            "claude_code_version": Kind.ENUM,
            "capabilities": Kind.ENUM,
        },
        "claude.stream.assistant": usage
        | {
            "message_uuid": Kind.ID,
            "model": Kind.ENUM,
            "parent_tool_use_id": Kind.ID,
            "stop_reason": Kind.ENUM,
            "tool_names": Kind.ENUM,
            "tool_use_ids": Kind.ID,
        },
        "claude.stream.user": {
            "parent_tool_use_id": Kind.ID,
            "tool_use_id": Kind.ID,
            "is_error": Kind.SCALAR,
        },
        # Only under --include-partial-messages. DROPPABLE in store.py: it is the one
        # surface that can arrive faster than the writer, and losing it loses nothing
        # that is not also in the assistant message that follows.
        "claude.stream.stream_event": {"event_type": Kind.ENUM, "index": Kind.SIZE},
        "claude.stream.compact_boundary": {
            "trigger": Kind.ENUM,
            "pre_tokens": Kind.SIZE,
            "post_tokens": Kind.SIZE,
        },
        "claude.stream.api_retry": {
            "attempt": Kind.SIZE,
            "max_retries": Kind.SIZE,
            "retry_delay_ms": Kind.SIZE,
            "error_status": Kind.SIZE,
            "error": Kind.SCALAR,
        },
        "claude.stream.result": usage
        | {
            "subtype": Kind.ENUM,
            "duration_ms": Kind.SIZE,
            "duration_api_ms": Kind.SIZE,
            "num_turns": Kind.SIZE,
            "total_cost_usd": Kind.SCALAR,
            "is_error": Kind.SCALAR,
            # modelUsage per model, carrying contextWindow: the only trustworthy
            # occupancy denominator Claude offers (design 6.11).
            "model_usage": Kind.SCALAR,
            "permission_denials": Kind.SCALAR,
        },
    }


def _codex_exec() -> dict[str, dict[str, Kind]]:
    return {
        "codex.exec.thread_started": {"thread_id": Kind.ID},
        "codex.exec.turn_started": {"turn_id": Kind.ID},
        "codex.exec.turn_completed": {
            "turn_id": Kind.ID,
            "input_tokens": Kind.SIZE,
            "cached_input_tokens": Kind.SIZE,
            "output_tokens": Kind.SIZE,
            "reasoning_output_tokens": Kind.SIZE,
            "total_tokens": Kind.SIZE,
            "model_context_window": Kind.SIZE,
        },
        "codex.exec.turn_failed": {"turn_id": Kind.ID, "error": Kind.SCALAR},
        "codex.exec.item": {
            "item_id": Kind.ID,
            "item_type": Kind.ENUM,
            "status": Kind.ENUM,
            "turn_id": Kind.ID,
            "command": Kind.COMMAND,
            "exit_code": Kind.SIZE,
            "duration_ms": Kind.SIZE,
            "path": Kind.PATH,
            "kind": Kind.ENUM,
            "server": Kind.ENUM,
            "tool": Kind.ENUM,
        },
        # `error` rather than `message`: the parser lifts the one value out of the item,
        # and the container name stays in NEVER_PERSIST so a future field inside it
        # cannot ride along.
        "codex.exec.error": {"error": Kind.SCALAR, "code": Kind.ENUM},
    }


_HOOK_COMMON: dict[str, Kind] = {
    "hook_event_name": Kind.ENUM,
    "session_id": Kind.ID,
    "prompt_id": Kind.ID,
    "turn_id": Kind.ID,
    "cwd": Kind.PATH,
    "transcript_path": Kind.PATH,
    "permission_mode": Kind.ENUM,
    "agent_id": Kind.ID,
    "agent_type": Kind.ENUM,
    "effort_level": Kind.ENUM,
    "model": Kind.ENUM,
}

_HOOK_EXTRA: dict[str, dict[str, Kind]] = {
    "SessionStart": {"source": Kind.ENUM},
    "SessionEnd": {"reason": Kind.ENUM},
    "UserPromptSubmit": {"prompt_length": Kind.SIZE},
    "PreToolUse": {
        "tool_name": Kind.ENUM,
        "tool_use_id": Kind.ID,
        "file_path": Kind.PATH,
        "command": Kind.COMMAND,
    },
    "PostToolUse": {
        "tool_name": Kind.ENUM,
        "tool_use_id": Kind.ID,
        "file_path": Kind.PATH,
        "command": Kind.COMMAND,
        "exit_code": Kind.SIZE,
        "success": Kind.SCALAR,
    },
    "PostToolUseFailure": {
        "tool_name": Kind.ENUM,
        "tool_use_id": Kind.ID,
        "error_type": Kind.ENUM,
    },
    "PostToolBatch": {"tool_names": Kind.ENUM, "count": Kind.SIZE},
    "PermissionRequest": {"tool_name": Kind.ENUM, "decision": Kind.ENUM},
    "Notification": {"notification_type": Kind.ENUM},
    "SubagentStart": {"subagent_type": Kind.ENUM},
    "SubagentStop": {"subagent_type": Kind.ENUM, "stop_reason": Kind.ENUM},
    "Stop": {"stop_reason": Kind.ENUM},
    "PreCompact": {"trigger": Kind.ENUM},
    "PostCompact": {"trigger": Kind.ENUM},
    "PreModelSwitch": {"from_model": Kind.ENUM, "to_model": Kind.ENUM},
    "PostModelSwitch": {"from_model": Kind.ENUM, "to_model": Kind.ENUM},
    "WorktreeCreate": {"worktree_id": Kind.ID, "path": Kind.PATH},
    "WorktreeRemove": {"worktree_id": Kind.ID, "path": Kind.PATH},
}

# Each provider's own documented event list (digest 2.1 and 2.2), not the union: a type
# key that no provider can emit would be printed by `telltale schema` as if it could.
_CLAUDE_HOOKS = (
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PostToolBatch",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "PreCompact",
    "PostCompact",
    "PreModelSwitch",
    "PostModelSwitch",
    "WorktreeCreate",
    "WorktreeRemove",
)
_CODEX_HOOKS = (
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SubagentStart",
    "SubagentStop",
    "Stop",
)


def _hooks() -> dict[str, dict[str, Kind]]:
    table: dict[str, dict[str, Kind]] = {}
    for provider, events in (("claude", _CLAUDE_HOOKS), ("codex", _CODEX_HOOKS)):
        for event in events:
            table[f"{provider}.hook.{event}"] = _HOOK_COMMON | _HOOK_EXTRA[event]
    return table


ALLOWLIST: dict[str, dict[str, Kind]] = _build_allowlist()
