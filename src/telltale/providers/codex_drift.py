"""What E02 and W7-T1 MEASURED about Codex CLI 0.150.1, as two tables.

Data, not behaviour, and split out of codex.py for the same reason claude_drift.py was
split out of claude.py: the parser is one shape and these two tables grow with every
capture that meets a new version. codex.py re-exports both names, because
providers/__init__.py's Provider protocol asks a provider MODULE for them.

Nothing here is a judgement. CAPABILITIES is the spec 9.1 matrix cell by cell, and every
line of DRIFT is a difference from docs/design/00-digest.md 2.2, from
docs/experiments/E02.md, or from what a reader of either would expect, that a real
capture measured, with the fixture or the probe that measured it.
"""

from __future__ import annotations

SURFACES = ("exec_json", "otel_logs", "otel_metrics", "hook", "rollout")

# Shorthands for the matrix below.
_NONE3, _NONE4, _NONE5 = (("unavailable",) * n for n in (3, 4, 5))
_SOME2 = ("partial",) * 2

# Spec 9.1 facts against the five surfaces, one row per capability, in SURFACES order.
# Every cell is a cell of the E02 matrix except the four marked (corrected) and the
# whole `request_duration` row, which E02 never asked about; those are this module's own
# reading of the same fixtures, and DRIFT names each one and the record it rests on.
# Every `unavailable` means "not observed in E02's cohort of seven single-prompt
# sessions", which is not the same as "does not exist".
CAPABILITIES: dict[str, dict[str, str]] = {
    name: dict(zip(SURFACES, cells, strict=True))
    for name, cells in (
        # exec carries usage per TURN, and a turn is many model requests; otel_logs
        # carries it per model RESPONSE on codex.sse_event (corrected); the metric is
        # one histogram whose sum is usable and whose buckets are lossy. The rollout
        # cell was `partial` on the turn-total reading and is `observed` on the
        # measured one (corrected): event_msg/token_count.info.last_token_usage is ONE
        # request's usage, and it equals the sse record field for field.
        (
            "request_usage",
            ("partial", "observed", "partial", "unavailable", "observed"),
        ),
        # Derived, never observed: no Codex surface carries a per-request duration
        # FIELD, and otel_logs is the only one whose records bracket a request. See
        # activities_codex._durations for the rollout derivation that was measured and
        # rejected, which is why the last cell is unavailable and not derived.
        ("request_duration", ("unavailable", "derived", *_NONE3)),
        # event_msg/task_started.model_context_window, 258400. The rollout alone, and
        # --ephemeral writes no rollout at all.
        ("context_window", (*_NONE4, "observed")),
        # Nothing compacted and PreCompact/PostCompact never fired.
        ("compaction", _NONE5),
        # hooks are partial because PreToolUse and PostToolUse do not pair (E02: 8
        # against 7 on S6). The metric counts calls and names no call id.
        ("tool_calls", ("observed", "observed", "partial", "partial", "observed")),
        # exec: item.changes[].path. rollout: the KEYS of item.changes.
        ("file_paths", ("observed", *_NONE3, "observed")),
        # otel_logs corrected: the command is inside the JSON-string attribute
        # `arguments` as `cmd`, which E02's path probe could not index.
        ("commands", ("observed", "observed", "unavailable", "observed", "observed")),
        ("subagents", _NONE5),
        # A real integer on both, not read out of prose the way Claude's is.
        ("exit_codes", ("observed", *_NONE3, "observed")),
        # Nothing names a commit the agent made. session_meta.git.commit_hash is HEAD
        # at session start, a different fact stored under its own name.
        ("commit_ids", _NONE5),
        # hooks: permission_mode on every body. The others carry sandbox_policy and
        # approval_policy, the same posture under other names, so partial.
        ("permission_mode", ("unavailable", *_SOME2, "observed", "partial")),
    )
}

# Every difference from docs/design/00-digest.md 2.2, from docs/experiments/E02.md, or
# from what a reader of either would expect, that this parser had to code around. Each
# line is a measurement over fixtures/sources/codex/0.150.1 or a probe recorded in
# docs/log/W1-T3.md, not a judgement.
DRIFT: list[str] = [
    "E02 matrix correction 1: command text IS on otel_logs. codex.tool_result carries "
    "`arguments`, a JSON STRING that holds {cmd, workdir} when tool_name is "
    "exec_command; the matrix probe indexed JSON paths and could not see inside a "
    "string. The parser lifts cmd and workdir and `arguments` stays NEVER_PERSIST, "
    "because for apply_patch the same attribute is the whole patch.",
    "E02 matrix correction 2: per-request token counts ARE on otel_logs, on "
    "codex.sse_event with event.kind response.completed: input_token_count, "
    "output_token_count, cached_token_count, cache_write_token_count, "
    "reasoning_token_count, tool_token_count. The wire names are kept, because "
    "tool_token_count was input plus output on every record and is not a tool count.",
    "E02 matrix correction 3: file change KIND is on the rollout too, as the `type` of "
    "each entry of item.changes, a dict keyed by absolute path.",
    "E02 matrix correction 4 (W7-T1): per-request token counts are on the ROLLOUT too. "
    "event_msg/token_count.info.last_token_usage is ONE model request's usage and "
    "info.total_token_usage is the turn's running total, on the same record. Measured "
    "here: each last_token_usage equals exactly one codex.sse_event response.completed "
    "record on input_tokens/cached_input_tokens/output_tokens against "
    "input_token_count/cached_token_count/output_token_count, S1 6 of 6, S3 2 of 2, S6 "
    "9 of 9. The sse records with no rollout twin are the pre-turn one on all three "
    "(input 11437, cached 0, output 0) and, on S6, one response.completed carrying no "
    "counters at all, whose rollout twin is the token_count whose `info` is null. So "
    "S6 has 11 responses and 10 token_count records, of which 9 carry a usage mapping.",
    "A rollout file is per THREAD and a capture is per RUN (W7-T1). E02's S2 and S4 "
    "are two runs against one thread (S4 resumes S2) and the fixture holds the SAME "
    "38-line rollout in both directories, byte for byte, same session id, five "
    "token_count records from 22:53:36 to 22:55:54. Each scenario's OTel capture "
    "covers only its own run, four responses on S2 and one on S4, so S2 has one "
    "rollout token_count with no response to pair with and S4 has three. That is two "
    "surfaces covering different spans, not a disagreement about one request: the "
    "reducer keeps the OTel count, writes a conflict row naming what was compared, "
    "and never adds a second request.",
    "No Codex surface carries a per-request DURATION field (W7-T1). "
    "codex.api_request.duration_ms times the /models HTTP call; codex.turn_ttft times "
    "a turn's first token; codex.websocket_request.duration_ms is 2, 0, 0, 0, 0, 0 and "
    "0 ms on S1 against responses that took 490 to 5812 ms, so it times writing the "
    "request onto the socket. The duration is derived from the pair of event.timestamp "
    "values that bracket a request, codex.websocket_request to codex.sse_event "
    "response.completed, whose counts are equal in every capture that carries both (S1 "
    "7 and 7, S3 3 and 3, S6 11 and 11).",
    'timeUnixNano is the string "0" on every OTel log record (211 of 211): the time '
    "is in observedTimeUnixNano and in the event.timestamp attribute where there is "
    "one, and provider_ts reads those in that order.",
    "Six OTel log records carry no event.name: DEBUG lines of the CLI's own Rust "
    "tracing, body 'flushing OTEL metrics'. They become codex.otel.log rather than "
    "failing the batch of up to 20 records they arrive in.",
    "OTLP encoding is mixed within one record: success is boolValue on api_request and "
    "the string 'true' on tool_result, output_truncated is the Python-spelled 'False', "
    "duration_ms and every *_token_count are stringValue while "
    "http.response.status_code is intValue. Every value is coerced by its allowlist "
    "Kind, and 'True'/'False' are read as well as 'true'/'false'.",
    "OTel attribute keys are dotted (event.name, event.kind, conversation.id, "
    "app.version, terminal.type, startup.phase, auth.mode) and the parser rewrites the "
    "dot. api_request spells it auth.mode and every other event auth_mode, so both "
    "arrive as auth_mode and never in one record.",
    "Every OTel log record carries user.account_id and user.email unconditionally. The "
    "parser removes both before sanitize sees them rather than leaving them unlisted: "
    "an unlisted field costs one diagnostic per request, and these are on every "
    "request of every session.",
    "service.name is codex_exec, not codex; providers.SERVICE_NAMES carries both.",
    "The exec stream spells a command as one string and the rollout as an argv LIST, "
    "and both wrap it in /bin/zsh -lc, which commands.normalize would store as "
    "`zsh -lc _`. Unwrapped here: measured, on all 10 calls that appear on both the "
    "rollout and the hooks, the script equals the hook's tool_input.command exactly.",
    "Two ways a Codex path gets past the rewriter, both measured here. The rollout "
    "spells CommandExecution.cwd as a file:// URL, which is not an absolute path: "
    "joined to the repository root it comes back as file:/Users/... with the machine "
    "path still in it, so the scheme comes off first. And Codex writes absolute paths "
    "into its own error prose (`failed to parse hooks config <home>/.codex/"
    "hooks.json`), which is Kind.SCALAR: scrubbed for secrets, never rewritten. The "
    "home directory reached the store on all seven scenarios until providers.prose() "
    "relativized each path inside the text. claude.py has the same gap on its own "
    "`error` field, and W0-T5 exempted it for the repo root.",
    "The rollout spells item types in CamelCase (CommandExecution, FileChange, "
    "Reasoning) where the exec stream uses snake case; the parser lowers the rollout "
    "spelling so one item_type means one thing on both.",
    "Codex apply_patch puts the WHOLE PATCH, new file contents included, in the hook "
    "body's tool_input.command. The parser lifts command only for other tools and "
    "stores patch_bytes instead: a normalized command is bounded prose, a patch is "
    "file content.",
    "Three fields carry command output or model text that sanitize.NEVER_PERSIST does "
    "not name: the rollout's CommandExecution.formatted_output and "
    "item.changes[].unified_diff, which no item container is copied so neither is, and "
    "event_msg/task_complete.last_agent_message, which the Stop hook spells "
    "last_assistant_message and NEVER_PERSIST does name. All three are dropped today; "
    "the file that would make that permanent is not this task's.",
    "turn.started and turn.completed carry no turn id, so exec usage cannot be joined "
    "to a rollout turn. The rollout carries turn_id on task_started, task_complete, "
    "item_completed and turn_context.",
    "model_context_window is in event_msg/task_started (258400), not in "
    "event_msg/token_count.info as the digest says. token_count.info carries its own, "
    "null on every turn E02 measured, so each is stored under its own record type and "
    "never copied to the other.",
    "PreToolUse and PostToolUse do not pair (E02: 8 against 7 on S6), so tool_calls is "
    "partial on the hook surface and a count that pairs them is wrong by one.",
    '-c \'hooks={ PreToolUse = [ { hooks = [ { type = "command", command = "true", '
    "timeout = 5 } ] } ] }' LOADS: measured with codex doctor --json (zero tokens) "
    'against a rejected hooks="nonsense", so the loader validates the shape, and '
    "`command` must be a STRING. Whether a hook registered that way FIRES under codex "
    "exec NEEDS MEASURING in the first launcher capture; until then launch() declares "
    "no hook surface and writes no file (invariant 8).",
    "otel.tool_result={ max_bytes = 0 } is accepted by the loader (re-measured here); "
    "its EFFECT on the output attribute NEEDS MEASURING on the first launcher capture. "
    "Until then the parser drops `output` by NEVER_PERSIST, which records the drop on "
    "every record.",
    "otel.log_user_prompt=false is accepted by the loader and is NOT passed: a -c "
    "override that some build rejects exits the child 1 before the session starts, and "
    "`prompt` is dropped by NEVER_PERSIST on both surfaces that carry it anyway.",
    "codex exec assigns its own thread id and takes no flag that names one, so "
    "launch() ignores session_id.",
]
