"""Codex tool results survive masked verification through the real recorder."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from conftest import activity_fields, telltale_cli

from telltale import measures

if TYPE_CHECKING:
    from collections.abc import Callable

    from conftest import Live

    from telltale.store import Store


@pytest.mark.integration
@pytest.mark.parametrize("success", [False, True, None])
def test_codex_keeps_tool_results_separate_from_masked_checks(
    receiver: Callable[..., Live], store: Store, success: bool | None
) -> None:
    capture = "cap_codex_masked"
    attrs: dict[str, Any] = {
        "event.name": "codex.tool_result",
        "conversation.id": "session_codex_masked",
        "call_id": "exec-masked",
        "tool_name": "exec_command",
        "arguments": json.dumps({"cmd": "uv run pytest -q | tail -2"}),
        "event.timestamp": "2026-09-04T10:00:00Z",
    }
    if success is not None:
        attrs["success"] = success
    record = {
        "attributes": [
            {
                "key": key,
                "value": {
                    "boolValue" if isinstance(value, bool) else "stringValue": value
                },
            }
            for key, value in attrs.items()
        ]
    }
    body = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "codex_exec"},
                        }
                    ]
                },
                "scopeLogs": [{"logRecords": [record]}],
            }
        ]
    }
    live = receiver()
    assert live.post("/v1/logs", json.dumps(body).encode(), capture) == 200
    if success is not None:
        rollout = {
            "timestamp": "2026-09-04T10:00:00Z",
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "thread_id": "session_codex_masked",
                "item": {
                    "type": "CommandExecution",
                    "id": "exec-masked",
                    "command": ["/bin/zsh", "-lc", "uv run pytest -q | tail -2"],
                    "exit_code": 1,
                },
            },
        }
        assert (
            live.post(
                "/v1/stream/codex?surface=rollout",
                json.dumps(rollout).encode(),
                capture,
            )
            == 200
        )
    live.drain()
    store.rebuild(capture)
    assert store.flush()

    (run,) = activity_fields(store, capture, "verification_run")
    summary = measures.summary(store, capture)
    timeline = telltale_cli("timeline", capture, home=store.path.parent)
    (line,) = [row for row in timeline.splitlines() if "verification_run" in row]
    expected = (
        "check masked"
        if success is None
        else ("ok, check masked" if success else "failed, check masked")
    )
    assert expected in line, line
    assert run.get("success") is success
    assert run["exit_masked"] is True
    assert "exit_code" not in run
    assert summary["verification"]["agent_test_runs"] == 1
    assert summary["verification"]["failed_test_runs"] is None
    assert summary["verification"]["fail_to_pass_cycles"] is None
    assert any(
        "1 of 1 verification runs has a masked exit status" in warning
        for warning in summary["warnings"]["failed_test_runs"]
    )
    (observed,) = store.observations(capture, ("codex.otel.tool_result",))
    assert observed["payload"].get("success") is success
    if success is not None:
        (execution,) = store.observations(
            capture, ("codex.rollout.event_msg.item_completed",)
        )
        assert execution["payload"]["exit_code"] == 1
