"""Claude Code launch plan: argv shaping, hook settings, environment.

Split out of claude.py so the parser module stays under the 800-line ratchet. The
facts here were measured in E01 (docs/experiments/E01.md) and W1-T1
(docs/log/W1-T1.md): http hooks are declared through --settings, the OTel
variables travel in the environment, and flags are inserted right after -p.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from telltale.providers import LaunchPlan
from telltale.providers.claude import CAPTURE_ATTR

if TYPE_CHECKING:
    from collections.abc import Sequence


# Hook events the launch plan declares. The nine E01 saw fire, plus SessionStart (which
# registers and never runs on 2.1.257), UserPromptSubmit and PostModelSwitch (neither
# was declared in E01, so neither has been measured).
HOOK_EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "PreCompact", "PostCompact", "SubagentStart", "SubagentStop",
    "Stop", "PostModelSwitch",
)  # fmt: skip

# Removed from the child's environment rather than set empty. The first three are the
# content switches Telltale never turns on, so a value exported in the parent shell
# cannot reach the child. The last two are E01 finding 6: a child inheriting VIRTUAL_ENV
# from `uv run` cannot run `uv run pytest` in its own repository, and S1 spent a turn on
# `rm -rf .venv && uv sync` because of it.
ENV_REMOVE = (
    "OTEL_LOG_USER_PROMPTS",
    "OTEL_LOG_ASSISTANT_RESPONSES",
    "OTEL_LOG_TOOL_CONTENT",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)


def _after_print(argv: list[str]) -> int:
    """Index just after -p or --print, or len(argv) when neither is present."""
    for flag in ("-p", "--print"):
        if flag in argv:
            return argv.index(flag) + 1
    return len(argv)


def launch(
    argv: Sequence[str],
    port: int,
    level: int,
    session_id: str | None,
    capture_id: str | None = None,
) -> LaunchPlan:
    """The child's argv, environment and tee decision. Design 6.9.

    `capture_id` is not in the design's signature and the environment variable it names
    is: without it the OTel records carry no telltale.capture_id and the receiver has to
    fall back to the session-id map, which only works once the session id is known.
    """
    child = list(argv)
    surfaces = ["otel_logs", "otel_metrics"]
    settings = _settings(child)
    if settings is not None:
        _apply_settings(child, settings, port)
        surfaces.append("hook")
    if _has(child, "-p", "--print") and not _has(child, "--session-id", "--resume"):
        # Inserted right after -p, before any positional prompt: options before
        # positionals are valid for every argv shape, and whether Claude Code
        # accepts options AFTER the prompt has not been measured. Index 1 would
        # break wrapped scripts, which reject options they do not know.
        at = _after_print(child)
        child[at:at] = ["--session-id", session_id or str(uuid.uuid4())]
    tee = _has(child, "--output-format") and "stream-json" in child
    if tee:
        surfaces.append("stream")
    return LaunchPlan(
        argv=child,
        env=_env(port, level, capture_id),
        tee=tee,
        env_remove=ENV_REMOVE,
        surfaces=tuple(surfaces),
    )


# -- launch -------------------------------------------------------------------------
def _env(port: int, level: int, capture_id: str | None) -> dict[str, str]:
    env = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        # No default exists for the protocol: without this the exporter has none.
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{port}",
        # Design 6.9. The documented defaults are 5000 and 60000 ms, and E01 measured
        # every session with these two values.
        "OTEL_LOGS_EXPORT_INTERVAL": "1000",
        "OTEL_METRIC_EXPORT_INTERVAL": "5000",
    }
    if level >= 1:
        # tool_input, tool_parameters, file paths, the commit id. E01 measured that this
        # switch carries no file CONTENT on either OTel surface.
        env["OTEL_LOG_TOOL_DETAILS"] = "1"
    if capture_id:
        env["OTEL_RESOURCE_ATTRIBUTES"] = f"{CAPTURE_ATTR}={capture_id}"
    return env


def _settings(argv: list[str]) -> dict[str, Any] | None:
    """The settings object to add http hooks to, or None when we must not touch it.

    A `--settings` that names a FILE cannot be merged without reading and rewriting the
    child's configuration, and a second `--settings` would replace it. Design rule 8
    says capture never changes the child's behaviour, so the hook surface is given up
    instead, and the plan says so by leaving `hook` out of its surfaces.
    """
    if "--settings" not in argv:
        return {}
    value = argv[argv.index("--settings") + 1] if _has_value(argv, "--settings") else ""
    text = value.strip()
    if not text.startswith("{"):
        return None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _apply_settings(argv: list[str], settings: dict[str, Any], port: int) -> None:
    """Merge one http hook per event into `settings`, in place, then into argv."""
    url = f"http://127.0.0.1:{port}/hooks/claude"
    # Seconds, not milliseconds: 5000 here would be a 5000-second hook timeout.
    hook = {"type": "http", "url": url, "timeout": 5}
    hooks = dict(settings.get("hooks") or {})
    for event in HOOK_EVENTS:
        entries = list(hooks.get(event) or [])
        entries.append({"hooks": [hook]})
        hooks[event] = entries
    settings["hooks"] = hooks
    text = json.dumps(settings, separators=(",", ":"))
    if "--settings" in argv and _has_value(argv, "--settings"):
        argv[argv.index("--settings") + 1] = text
    else:
        at = _after_print(argv)
        argv[at:at] = ["--settings", text]


def _has(argv: Sequence[str], *flags: str) -> bool:
    return any(flag in argv for flag in flags)


def _has_value(argv: Sequence[str], flag: str) -> bool:
    return flag in argv and argv.index(flag) + 1 < len(argv)
