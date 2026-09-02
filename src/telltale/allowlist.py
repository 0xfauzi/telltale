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

    Absent on purpose: `claude.transcript.*` (backfill, wave 2). Until an entry exists,
    every field of that type is dropped and reported, which is the fail-closed behaviour
    spec 20.2 asks for. `codex.otel.*` and `codex.rollout.*` arrived with E02 and W1-T3.
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
            # W1-T1, on E01 finding 6: the names the launcher took out of the child's
            # environment. Names only, never values, which are in NEVER_PERSIST.
            "env_removed": Kind.ENUM,
        },
        "telltale.capture_ended": {
            "exit_code": Kind.SIZE,
            "duration_ms": Kind.SIZE,
            "surfaces_received": Kind.SIZE,
            # W0-T3 measured that worktree_id alone is the same for every main worktree
            # of every repository, so a capture is keyed by (repo_id, worktree_id) and
            # both ends of it carry the pair: repo_id is a column, this is the other.
            "worktree_id": Kind.ID,
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
            # True when per_file is a prefix rather than the whole list (launch.py
            # caps it so the 8 KB payload bound cannot drop the list whole).
            # files_changed still carries the true count.
            "per_file_truncated": Kind.SCALAR,
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
    # Imported here, not at the top: allowlist_codex needs `Kind` from this
    # module, so one of the two directions has to be deferred. By the time this
    # function runs, Kind exists. Same shape as sanitize.py's deferred import of
    # commands.py, and for the same reason.
    from telltale.allowlist_codex import codex_tables

    table.update(codex_tables())
    table.update(_hooks())
    return table


# Attributes every Claude OTel log record carries (digest 2.1). user.id and user.email
# are deliberately absent: they identify a person and nothing here needs them, so they
# arrive as unknown fields and raise a diagnostic.
#
# The dots of the wire names (`event.name`, `terminal.type`) become underscores in the
# provider parser, because a key with a dot in it reads as a path in every JSON query
# this store will later be asked. `service_version` is the resource attribute
# `service.version`, which E01 measured on every record of every scenario without
# OTEL_METRICS_INCLUDE_VERSION being set; it is the Claude Code version.
_OTEL_COMMON: dict[str, Kind] = {
    "event_name": Kind.ENUM,
    "event_timestamp": Kind.ENUM,
    "event_sequence": Kind.SIZE,
    "terminal_type": Kind.ENUM,
    "app_version": Kind.ENUM,
    "service_version": Kind.ENUM,
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
            # E01: `error` sits beside `error_type` on a failed tool_result, and it is
            # a sentence ("Shell command failed"). Kept for the same reason api_error
            # keeps its own: the category is only in the message.
            "error": Kind.SCALAR,
            # Selected OUT of tool_parameters and tool_input by the provider parser,
            # which are themselves in NEVER_PERSIST. Design 6.3: the commit id is
            # provider_reported, the exit code is derived from the result text before
            # that text is dropped, and neither container ever reaches this module.
            "git_commit_id": Kind.ID,
            "exit_code": Kind.SIZE,
            "file_path": Kind.PATH,
            "command": Kind.COMMAND,
            "description_length": Kind.SIZE,
        },
        "claude.otel.tool_decision": _OTEL_COMMON
        | {
            "tool_name": Kind.ENUM,
            "tool_use_id": Kind.ID,
            "decision": Kind.ENUM,
            "source": Kind.ENUM,
            "tool_source": Kind.ENUM,
            "subagent_type": Kind.ENUM,
            "command": Kind.COMMAND,
            "file_path": Kind.PATH,
            "description_length": Kind.SIZE,
        },
        "claude.otel.user_prompt": _OTEL_COMMON | {"prompt_length": Kind.SIZE},
        "claude.otel.assistant_response": _OTEL_COMMON | {"response_length": Kind.SIZE},
        "claude.otel.permission_mode_changed": _OTEL_COMMON
        | {"mode": Kind.ENUM, "previous_mode": Kind.ENUM},
        # E01 measured the field names: server_name, server_scope and transport_type,
        # not the name/status/transport the digest implied.
        "claude.otel.mcp_server_connection": _OTEL_COMMON
        | {
            "server_name": Kind.ENUM,
            "server_scope": Kind.ENUM,
            "transport_type": Kind.ENUM,
            "status": Kind.ENUM,
            "is_plugin": Kind.SCALAR,
            "duration_ms": Kind.SIZE,
        },
        "claude.otel.metric": {
            "name": Kind.ENUM,
            "value": Kind.SCALAR,
            "unit": Kind.ENUM,
            "type": Kind.ENUM,
            "model": Kind.ENUM,
            "query_source": Kind.ENUM,
            "start_type": Kind.ENUM,
            # E01: code_edit_tool.decision carries these four, and the digest does not
            # name that metric at all.
            "decision": Kind.ENUM,
            "source": Kind.ENUM,
            "tool_name": Kind.ENUM,
            "language": Kind.ENUM,
            "effort": Kind.ENUM,
            "agent_name": Kind.ENUM,
        },
    } | _claude_otel_e01()


def _claude_otel_e01() -> dict[str, dict[str, Kind]]:
    """The six OTel log events E01 saw that docs/design/00-digest.md 2.1 does not name.

    `claude_code.compaction` is the one that changes what Telltale may claim: the digest
    says no compaction event exists on this surface, and it does, with the token counts
    on it. `post_tokens` is absent when `success` is false, which is why a reducer has
    to read `success` before either number.
    """
    hook_run: dict[str, Kind] = {
        "hook_event": Kind.ENUM,
        "hook_name": Kind.ENUM,
        "hook_source": Kind.ENUM,
        "num_hooks": Kind.SIZE,
        "managed_only": Kind.SCALAR,
        "safe_mode": Kind.SCALAR,
    }
    plugin: dict[str, Kind] = {
        "plugin_name": Kind.ENUM,
        "plugin_id_hash": Kind.ID,
        "safe_mode": Kind.SCALAR,
    }
    return {
        "claude.otel.compaction": _OTEL_COMMON
        | {
            "trigger": Kind.ENUM,
            "pre_tokens": Kind.SIZE,
            "post_tokens": Kind.SIZE,
            "duration_ms": Kind.SIZE,
            "success": Kind.SCALAR,
            "error": Kind.SCALAR,
        },
        "claude.otel.hook_execution_start": _OTEL_COMMON | hook_run,
        "claude.otel.hook_execution_complete": _OTEL_COMMON
        | hook_run
        | {
            "num_success": Kind.SIZE,
            "num_blocking": Kind.SIZE,
            "num_cancelled": Kind.SIZE,
            "num_non_blocking_error": Kind.SIZE,
            "total_duration_ms": Kind.SIZE,
        },
        "claude.otel.hook_registered": _OTEL_COMMON
        | plugin
        | {
            "hook_event": Kind.ENUM,
            "hook_type": Kind.ENUM,
            "hook_matcher": Kind.ENUM,
            "hook_source": Kind.ENUM,
        },
        "claude.otel.plugin_loaded": _OTEL_COMMON
        | plugin
        | {
            "plugin_scope": Kind.ENUM,
            "plugin_version": Kind.ENUM,
            "marketplace_name": Kind.ENUM,
            "enabled_via": Kind.ENUM,
            "has_hooks": Kind.SCALAR,
            "has_mcp": Kind.SCALAR,
            "host_owned_mcp": Kind.SCALAR,
            "agent_path_count": Kind.SIZE,
            "command_path_count": Kind.SIZE,
            "skill_path_count": Kind.SIZE,
        },
        "claude.otel.subagent_completed": _OTEL_COMMON
        | {
            "agent_type": Kind.ENUM,
            "agent_source": Kind.ENUM,
            "model": Kind.ENUM,
            "final_model": Kind.ENUM,
            "model_swapped": Kind.SCALAR,
            "is_async": Kind.SCALAR,
            "is_built_in": Kind.SCALAR,
            "duration_ms": Kind.SIZE,
            "total_tokens": Kind.SIZE,
            "total_tool_uses": Kind.SIZE,
        },
    }


def _claude_stream() -> dict[str, dict[str, Kind]]:
    usage: dict[str, Kind] = {
        "input_tokens": Kind.SIZE,
        "output_tokens": Kind.SIZE,
        "cache_read_input_tokens": Kind.SIZE,
        "cache_creation_input_tokens": Kind.SIZE,
    }
    # Every stream message carries its own uuid. It is the join between a stream row and
    # the compact_boundary's preserved_segment, so it is on every type rather than on
    # the two that happen to need it today.
    common: dict[str, Kind] = {"message_uuid": Kind.ID}
    tool_call: dict[str, Kind] = {
        "tool_name": Kind.ENUM,
        "tool_use_id": Kind.ID,
        "command": Kind.COMMAND,
        "file_path": Kind.PATH,
        "subagent_type": Kind.ENUM,
        "description_length": Kind.SIZE,
    }
    task: dict[str, Kind] = common | {
        # The stream's task id names one subagent run. It is NOT the orchestrator's
        # task_id of design 6.3, which is why it stays in the payload and never goes
        # into correlation_ids where the two would be indistinguishable.
        "task_id": Kind.ID,
        "tool_use_id": Kind.ID,
        "subagent_type": Kind.ENUM,
        "description_length": Kind.SIZE,
        "status": Kind.ENUM,
    }
    return {
        "claude.stream.system.init": common
        | {
            "model": Kind.ENUM,
            "tools": Kind.ENUM,
            "mcp_servers": Kind.ENUM,
            "permission_mode": Kind.ENUM,
            "output_style": Kind.ENUM,
            "cwd": Kind.PATH,
            "api_key_source": Kind.ENUM,
            "claude_code_version": Kind.ENUM,
            "capabilities": Kind.ENUM,
            "slash_commands": Kind.ENUM,
            "agents": Kind.ENUM,
            "skills": Kind.ENUM,
            "plugins": Kind.ENUM,
            "fast_mode_state": Kind.ENUM,
        },
        "claude.stream.assistant": common
        | usage
        | tool_call
        | {
            "model": Kind.ENUM,
            "parent_tool_use_id": Kind.ID,
            "stop_reason": Kind.ENUM,
            "tool_names": Kind.ENUM,
            "tool_use_ids": Kind.ID,
        },
        "claude.stream.user": common
        | {
            "parent_tool_use_id": Kind.ID,
            "tool_use_id": Kind.ID,
            "is_error": Kind.SCALAR,
            "prompt_length": Kind.SIZE,
            "git_commit_id": Kind.ID,
            # Measured, and the reason for the name. On one S1 Bash call the OTel
            # surface reported tool_result_size_bytes 910 while the stream block it
            # names is 568 bytes: the provider counts something else. Two names keep a
            # reducer from summing two different quantities.
            "tool_result_content_bytes": Kind.SIZE,
            "exit_code": Kind.SIZE,
            "exit_code_source": Kind.ENUM,
        },
        # Only under --include-partial-messages. DROPPABLE in store.py: it is the one
        # surface that can arrive faster than the writer, and losing it loses nothing
        # that is not also in the assistant message that follows.
        "claude.stream.stream_event": common
        | {"event_type": Kind.ENUM, "index": Kind.SIZE},
        "claude.stream.system.compact_boundary": common
        | {
            "trigger": Kind.ENUM,
            "pre_tokens": Kind.SIZE,
            "post_tokens": Kind.SIZE,
            "cumulative_dropped_tokens": Kind.SIZE,
            "duration_ms": Kind.SIZE,
            "logical_parent_uuid": Kind.ID,
        },
        "claude.stream.system.api_retry": common
        | {
            "attempt": Kind.SIZE,
            "max_retries": Kind.SIZE,
            "retry_delay_ms": Kind.SIZE,
            "error_status": Kind.SIZE,
            "error": Kind.SCALAR,
        },
        # The four hook messages the stream carries. `stdout`, `stderr` and `output`
        # are on them and are all in NEVER_PERSIST: E01 measured a secret file's
        # contents arriving in a hook's stdout on this surface.
        "claude.stream.system.hook_started": common | _STREAM_HOOK,
        "claude.stream.system.hook_progress": common | _STREAM_HOOK,
        "claude.stream.system.hook_response": common
        | _STREAM_HOOK
        | {"exit_code": Kind.SIZE, "outcome": Kind.ENUM},
        "claude.stream.system.notification": common
        | {"key": Kind.ENUM, "priority": Kind.ENUM},
        "claude.stream.system.status": common | {"status": Kind.ENUM},
        "claude.stream.system.task_started": task
        | {
            "task_type": Kind.ENUM,
            "is_backgrounded": Kind.SCALAR,
            "spawn_depth": Kind.SIZE,
        },
        "claude.stream.system.task_progress": task
        | {
            "last_tool_name": Kind.ENUM,
            "total_tokens": Kind.SIZE,
            "tool_uses": Kind.SIZE,
            "duration_ms": Kind.SIZE,
        },
        "claude.stream.system.task_notification": task | {"output_file": Kind.PATH},
        "claude.stream.system.task_updated": task,
        "claude.stream.system.thinking_tokens": common
        | {"estimated_tokens": Kind.SIZE, "estimated_tokens_delta": Kind.SIZE},
        "claude.stream.system.vcs_state_changed": common
        | {"kind": Kind.ENUM, "branch": Kind.ENUM, "cwd": Kind.PATH},
        "claude.stream.rate_limit_event": common | {"rate_limit_info": Kind.SCALAR},
        "claude.stream.result": common
        | usage
        | {
            "subtype": Kind.ENUM,
            "duration_ms": Kind.SIZE,
            "duration_api_ms": Kind.SIZE,
            "ttft_ms": Kind.SIZE,
            "ttft_stream_ms": Kind.SIZE,
            "time_to_request_ms": Kind.SIZE,
            "queued_turn_count": Kind.SIZE,
            "num_turns": Kind.SIZE,
            "total_cost_usd": Kind.SCALAR,
            # E01 S7: subtype is `success` while is_error is true, so is_error is the
            # field that answers "did this session fail" and subtype is not.
            "is_error": Kind.SCALAR,
            "stop_reason": Kind.ENUM,
            "terminal_reason": Kind.ENUM,
            "api_error_status": Kind.SCALAR,
            # modelUsage per model, carrying contextWindow: the only trustworthy
            # occupancy denominator Claude offers (design 6.11).
            "model_usage": Kind.SCALAR,
            "permission_denials": Kind.SCALAR,
            "subagent_stats": Kind.SCALAR,
        },
    }


# hook_id joins the started, progress and response messages of one hook run.
_STREAM_HOOK: dict[str, Kind] = {
    "hook_id": Kind.ID,
    "hook_name": Kind.ENUM,
    "hook_event": Kind.ENUM,
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
    # A twelfth Codex event the digest does not list. E02 registered it and Codex
    # accepted it and clamped its timeout, so the NAME is real on 0.150.1; no body has
    # ever been seen, so nothing beyond the common fields is claimed for it.
    "Interrupt": {},
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
    "Interrupt",
)


# What E01 measured on the Claude hook bodies and the digest does not list. Kept apart
# from _HOOK_EXTRA because that table is shared with Codex, whose hook payloads E02 has
# not measured yet: a field added to the shared table would claim a fact about a
# provider nobody has looked at.
_CLAUDE_HOOK_EXTRA: dict[str, dict[str, Kind]] = {
    "PreToolUse": {"subagent_type": Kind.ENUM, "description_length": Kind.SIZE},
    "PostToolUse": {
        "duration_ms": Kind.SIZE,
        "subagent_type": Kind.ENUM,
        "description_length": Kind.SIZE,
        # Lifted out of tool_response.gitOperation.commit, which never reaches the
        # sanitizer: the same commit id the OTel surface reports as provider_reported.
        "git_commit_id": Kind.ID,
        "git_commit_kind": Kind.ENUM,
        "git_branch": Kind.ENUM,
    },
    "PostToolUseFailure": {
        "duration_ms": Kind.SIZE,
        "error": Kind.SCALAR,
        "is_interrupt": Kind.SCALAR,
        "subagent_type": Kind.ENUM,
        "description_length": Kind.SIZE,
    },
    "Stop": {"stop_hook_active": Kind.SCALAR},
    "SubagentStop": {"stop_hook_active": Kind.SCALAR},
}


# What E02 measured on the Codex hook bodies and the digest does not list. `patch_bytes`
# is not a field Codex sends: it is the SIZE of tool_input.command when the tool is
# apply_patch, because that field holds the whole patch, new file contents included, and
# a normalized command of it would be a bounded quotation of the file.
_CODEX_HOOK_EXTRA: dict[str, dict[str, Kind]] = {
    "PreToolUse": {"patch_bytes": Kind.SIZE},
    "PostToolUse": {"patch_bytes": Kind.SIZE},
    "Stop": {"stop_hook_active": Kind.SCALAR},
}

_HOOK_PROVIDER_EXTRA = {"claude": _CLAUDE_HOOK_EXTRA, "codex": _CODEX_HOOK_EXTRA}


def _hooks() -> dict[str, dict[str, Kind]]:
    table: dict[str, dict[str, Kind]] = {}
    for provider, events in (("claude", _CLAUDE_HOOKS), ("codex", _CODEX_HOOKS)):
        for event in events:
            extra = _HOOK_PROVIDER_EXTRA[provider].get(event, {})
            table[f"{provider}.hook.{event}"] = (
                _HOOK_COMMON | _HOOK_EXTRA[event] | extra
            )
    return table


ALLOWLIST: dict[str, dict[str, Kind]] = _build_allowlist()
