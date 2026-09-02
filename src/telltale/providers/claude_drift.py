"""What E01, W2-T2 and W2-T6 MEASURED about Claude Code, as two tables.

Data, not behaviour, and split out of claude.py for the reason allowlist.py was split
out of sanitize.py: the parser is one shape and these two tables grow with every
capture that meets a new version. claude.py re-exports both names, because
providers/__init__.py's Provider protocol asks a provider MODULE for them.

Nothing here is a judgement. CAPABILITIES is the spec 9.1 matrix cell by cell, and
every line of DRIFT is a difference from docs/design/00-digest.md 2.1 that a real
capture measured, with the experiment or the capture id that measured it.
"""

from __future__ import annotations

# The five surfaces, in the order every CAPABILITIES row is written in. Here rather
# than in claude.py because the order is what the table means: change it and every row
# below says something different.
SURFACES = ("otel_logs", "otel_metrics", "hook", "stream", "transcript")

# Spec 9.1 facts against the four surfaces, one row per capability, in SURFACES order.
# Every cell is a cell of the E01 matrix, and a `partial` carries its note with it: tool
# success on the hooks surface is partial because the EVENT NAME differed, not because
# a success field said so. The notes are the matrix rows in docs/experiments/E01.md.
CAPABILITIES: dict[str, dict[str, str]] = {
    name: dict(zip(SURFACES, cells, strict=True))
    for name, cells in (
        # otel api_request has the four counters; the token.usage counter carries no
        # request id, so it cannot be split per request; no hook payload has tokens.
        # The transcript carries message.usage on every assistant line it writes
        # (W2-T2: 10382 of 10382 over 250 of the owner's files).
        (
            "request_usage",
            ("observed", "partial", "unavailable", "observed", "observed"),
        ),
        # result.modelUsage.<model>.contextWindow, on the result message alone. No
        # transcript line carries one: W2-T2 grepped all 1806 of the owner's files and
        # the 19 hits are inside a tool's own output, not a denominator for the session.
        (
            "context_window",
            ("unavailable", "unavailable", "unavailable", "observed", "unavailable"),
        ),
        # claude_code.compaction, which the digest denies, carries the token counts.
        # PreCompact and PostCompact carry the trigger and no counts. The transcript's
        # compact_boundary is the only surface carrying postTokens after the fact.
        ("compaction", ("observed", "unavailable", "partial", "observed", "observed")),
        # code_edit_tool.decision is edit tools only, and only as a decision counter.
        ("tool_calls", ("observed", "partial", "observed", "observed", "observed")),
        ("file_paths", ("observed", "unavailable", "observed", "observed", "observed")),
        # otel: tool_parameters.full_command, beside bash_command (the first word).
        ("commands", ("observed", "unavailable", "observed", "observed", "observed")),
        # subagent_completed describes the child and names no agent id and no parent;
        # SubagentStart.agent_id links start to stop but not to the spawning call;
        # only the stream has parent_tool_use_id with task_started.tool_use_id. On the
        # transcript a subagent writes its own FILE and isSidechain marks every line of
        # it, but nothing in either file links the child to the call that spawned it.
        ("subagents", ("partial", "unavailable", "partial", "observed", "partial")),
        # E01 marks the stream cell partial: the failure text begins "Exit code N" and
        # nothing structured carries it. What this parser makes of that is derived,
        # which is design 6.3's word and the weaker claim of the two. The transcript
        # carries the same text: 110 of 110 such blocks measured also carry is_error.
        (
            "exit_codes",
            ("unavailable", "unavailable", "unavailable", "derived", "derived"),
        ),
        # commit.count counts commits and never names one.
        ("commit_ids", ("observed", "partial", "observed", "observed", "observed")),
        # permissionMode is on 142 of 835 transcript user lines and on a
        # `permission-mode` line this parser does not read, so the transcript states
        # it for some turns and not for others.
        (
            "permission_mode",
            ("unavailable", "unavailable", "observed", "observed", "partial"),
        ),
    )
}

# Every difference from docs/design/00-digest.md 2.1 this parser had to code around.
# Each line is a measurement in docs/experiments/E01.md, not a judgement, and
# docs/log/W0-T4.md carries the long form.
DRIFT: list[str] = [
    "claude_code.compaction exists, against the digest's 'no compaction OTel event': "
    "trigger, pre_tokens, post_tokens, duration_ms, success, error. post_tokens is "
    "absent when success is false, so a reducer reads success first.",
    "Six OTel log events the digest omits: compaction, hook_execution_start, "
    "hook_execution_complete, hook_registered, plugin_loaded, subagent_completed.",
    "Four documented OTel events never arrived in 8 sessions: api_error, api_refusal, "
    "auth, permission_mode_changed. Their allowlist entries stay, unexercised.",
    "code_edit_tool.decision is a metric the digest omits; pull_request.count is "
    "documented and was never seen.",
    "user_prompt.prompt and assistant_response.response arrive with the literal value "
    "<REDACTED>: dropped, and only the lengths kept.",
    "OTLP number encoding is mixed. intValue is a JSON number, timeUnixNano a decimal "
    "string, and duration_ms, pre_tokens, num_hooks, prompt_length and both size "
    "attributes are stringValue. Every value is coerced by its allowlist Kind.",
    "OTel attribute keys are dotted (event.name, session.id, prompt.id, message.uuid, "
    "agent.name, terminal.type, plugin.name); the parser rewrites the dot.",
    "tool_input and tool_parameters are JSON strings inside an attribute, not objects, "
    "and tool_input carries whole Edit contents.",
    "query_source is sdk in headless mode, not repl_main_thread, and compact while "
    "compacting.",
    "Hook bodies carry no timestamp, so provider_ts is None on every hook observation.",
    "Twelve stream types the digest omits: rate_limit_event and the system subtypes "
    "hook_started, hook_response, hook_progress, notification, status, task_started, "
    "task_progress, task_notification, task_updated, thinking_tokens, "
    "vcs_state_changed. System messages are claude.stream.system.<subtype>, renaming "
    "W0-T2's claude.stream.init and claude.stream.compact_boundary.",
    "A result can carry subtype success while is_error is true (E01 S7): is_error is "
    "the field that says whether the session failed.",
    "The result message holds the final assistant text in `result` and "
    "system:task_notification a subagent's answer in `summary`. Neither is in "
    "NEVER_PERSIST, so the allowlist gate drops them and neither is ever added to it.",
    "The SessionStart http hook registers and never fires, so the plan declares it and "
    "the launcher may not wait for it.",
    "service.version is a resource attribute on every record without "
    "OTEL_METRICS_INCLUDE_VERSION; kept as service_version.",
    "OTel tool_result_size_bytes and the stream block it names are different numbers "
    "(910 against 568 on one S1 Bash call), so the stream number is stored as "
    "tool_result_content_bytes.",
    # Transcript surface (W2-T2). Measured over the owner's ~/.claude/projects on
    # 2026-09-02: 1806 files, 1.7 GB, versions 2.1.219 to 2.1.257.
    "Transcripts are not one file per session in one directory per project. 774 of the "
    "1806 files are <slug>/<session>.jsonl as the digest says; 852 are "
    "<slug>/<session>/subagents/agent-<id>.jsonl, 65 of those a level deeper under "
    "workflows/<id>, and 115 are <slug>/vercel-plugin/skill-injections.jsonl, which is "
    "not a session at all. An importer walks the tree and reads the session id out of "
    "the file rather than off the path.",
    "A subagent file carries the PARENT session's sessionId on every line, every line "
    "has isSidechain true, and none of its uuids appear in the parent file (measured "
    "on a 4111-line parent and its 11 subagent files, 0 overlap). So the two files are "
    "disjoint halves of one session, and summing both into one capture would sum a "
    "subagent's tokens into the main thread's, which spec 13.6 forbids.",
    "18 line types exist, against the digest's 5. Beyond assistant, user and system "
    "there are attachment, last-prompt, mode, ai-title, atis-latch, permission-mode, "
    "queue-operation, file-history-snapshot, file-history-delta, bridge-session "
    "(carrying ownerAccountUuid and ownerOrganizationUuid), custom-title, agent-name, "
    "pr-link, frame-link and cost-state. This parser reads three of them and counts "
    "the rest as skipped rather than storing a type nobody has measured.",
    "No `summary` line exists in any of the 1806 files, on any version from 2.1.219 to "
    "2.1.257, although the digest names one. claude.transcript.summary is implemented "
    "and has never been exercised by a real file.",
    "The first line of a transcript is not a session line: 188 of 250 sampled files "
    "open with queue-operation and others with mode, ai-title, custom-title or "
    "last-prompt. The session id, the cwd and the first provider timestamp are found "
    "by reading forward, not by reading line one.",
    "6 system subtypes, of which compact_boundary is one: the others are "
    "stop_hook_summary, turn_duration, local_command, away_summary and "
    "model_refusal_fallback. compactMetadata carries three fields the digest omits: "
    "preCompactDiscoveredTools, preservedSegment and preservedMessages.",
    "`effort` is a bare string on a transcript assistant line (xhigh) where the hook "
    "body spells it {level: ...}, and it is absent on 1506 of 10382 assistant lines.",
    "message.usage carries seven names the digest does not: server_tool_use, "
    "service_tier, inference_geo, iterations, speed, output_tokens_details and the "
    "cache_creation object holding ephemeral_1h_input_tokens and "
    "ephemeral_5m_input_tokens.",
    "No transcript line carries a context window for the session. 19 of the 1806 files "
    "mention contextWindow and every hit is inside a tool's own output, so occupancy "
    "has no denominator on this surface and reports None.",
    "toolUseResult is the transcript's tool_response: it holds stdout, stderr, "
    "originalFile, oldString, newString and structuredPatch. It is consumed by this "
    "parser, which lifts gitOperation.commit only, exactly as the stream path does.",
    # Claude Code 2.1.258 (W2-T6). Measured on the build's own captures in the owner's
    # store: cap_01M1GPSMW1ZRXVADWZPF0KZ9H3 (the wave 1 gate session) and
    # cap_01M1HE3XS4E3S2XTSNB99C7WQT, cap_01M1HE3ZCX63HRX8PAWJDYSNG6,
    # cap_01M1HE3ZDFB9WWW60Z5XZ89F7Z (the wave 2 row 2 sessions).
    "2.1.258 sends no git_commit_id anywhere. E01's S8 fixture on 2.1.257 carried one "
    "on a tool_result; across 19313 observations of the four 2.1.258 captures above, "
    "no payload on any surface has the field, so the provider_reported rung is "
    "unreachable on this version and commit linkage is snapshots only.",
    "api_request carries cost_usd_micros beside cost_usd, and assistant_response "
    "carries model, query_source and request_id beside response_length. The wave 1 "
    "gate read these as 2.1.258 drift and they are not: all four are in the 2.1.257 "
    "S1 and S4 fixtures this repository has held since E01, and W0-T4 simply did not "
    "list them. Allowlisted by W2-T6, which is why the S1 and S4 show goldens lost one "
    "diagnostics row each.",
    "api_request, and every other OTel log event, carries five identity attributes: "
    "organization_id, user_account_id, user_account_uuid, user_email, user_id. They "
    "are NOT allowlisted, on purpose: they identify a person, nothing here needs them, "
    "and leaving them unlisted keeps one unknown_field diagnostic per batch saying "
    "they still arrive.",
    "2.1.258 Stop hook bodies carry background_tasks and session_crons. Both are "
    "arrays, and the binary's own schema says an entry holds a shell command line, a "
    "free-text description and the prompt a cron will submit, so this parser consumes "
    "both and stores background_task_count and session_cron_count.",
    "hook_execution_complete arrives on 2.1.258 with exactly the fields W0-T4 listed "
    "for it (hook_event, hook_name, hook_source, num_hooks, num_success, num_blocking, "
    "num_cancelled, num_non_blocking_error, total_duration_ms, managed_only, "
    "safe_mode), so it needed no allowlist change: the wave 1 gate read its identity "
    "attributes as a new event.",
    "Three more 2.1.258 shapes are still dropped whole, because no capture has "
    "measured what their fields mean: claude.otel.retention_sweep (a housekeeping "
    "event with 11 counters), claude.stream.tool_progress and the system subtypes "
    "background_tasks_changed and code_change_published.",
    "A metric point carries service_version and terminal_type and nothing else of "
    "_OTEL_COMMON, so W3-T4 allowlists those two on claude.otel.metric alone rather "
    "than giving the metric entry the log events' field set. Until then both were "
    "dropped as unknown fields on every point: measured over the eight E01 scenarios, "
    "171 of 171 metric observations dropped each, and 746 of the 1935 unknown_field "
    "rows in the owner's store came from this one entry. It predates 2.1.258.",
]
