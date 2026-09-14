"""Which provider an OTLP batch is parsed as, asked of a receiver built like the daemon.

/v1/logs and /v1/metrics serve every provider on one port, so the provider comes out of
the body's `resource.attributes["service.name"]`. W10-F1: the owner's daemon stored
18,897 records from a program that is neither Claude Code nor the codex CLI as
`claude`, because a name it did not know fell through to the default provider. A name
that is present and undeclared is now refused; a name that is absent still takes the
default. Both halves are asked here over HTTP, of the real receiver and the real store,
with the receiver constructed as `telltale daemon` constructs it.

Every batch carries the same log record, and only its resource changes from test to
test, so the resource is the one thing that can explain a difference in what was
stored. The record is the shape the daemon mis-stored: a `codex.api_request` event,
which the claude parser turns into `claude.otel.codex.api_request`.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

import pytest

from telltale.providers import SERVICE_NAMES
from telltale.receiver import Receiver, _post

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from telltale.store import Store

EVENT = "codex.api_request"
STRANGER = "some-other-app"


@pytest.fixture
def daemon(store: Store) -> Iterator[int]:
    """A receiver built the way `telltale daemon` builds one, and the port it bound.

    `derive_captures=True` and the constructor's default provider, which is `claude`:
    the two settings under which the 18,897 rows were written.
    """
    live = Receiver(store, derive_captures=True)
    port = live.start()
    yield port
    live.stop()


def _resource(**attrs: str) -> dict[str, Any]:
    return {
        "attributes": [
            {"key": key.replace("_", "."), "value": {"stringValue": value}}
            for key, value in attrs.items()
        ]
    }


def _block(resource: dict[str, Any] | None) -> dict[str, Any]:
    record = {
        "timeUnixNano": "1788293057178000000",
        "attributes": [
            {"key": "event.name", "value": {"stringValue": EVENT}},
            {"key": "model", "value": {"stringValue": "probe"}},
        ],
    }
    block: dict[str, Any] = {"scopeLogs": [{"logRecords": [record]}]}
    if resource is not None:
        block["resource"] = resource
    return block


def _logs(*blocks: dict[str, Any]) -> bytes:
    return json.dumps({"resourceLogs": list(blocks)}).encode("utf-8")


def _metrics(resource: dict[str, Any]) -> bytes:
    """One sum data point. Metrics were 14,256 of the daemon's 18,897 misfiled rows."""
    point = {"timeUnixNano": "1788293057178000000", "asInt": 1}
    metric = {"name": "some_other_app.requests", "sum": {"dataPoints": [point]}}
    block = {"resource": resource, "scopeMetrics": [{"metrics": [metric]}]}
    return json.dumps({"resourceMetrics": [block]}).encode("utf-8")


def _stored(path: Path) -> list[tuple[str, str]]:
    """(provider, observation_type) of every row in the table, whatever its capture.

    The whole table rather than one capture's rows, because the defect under test
    decides the capture too: a refused batch must leave no row anywhere, and asking one
    capture for its rows could not see a row filed under another.
    """
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT provider, observation_type FROM observations ORDER BY rowid"
        ).fetchall()
    return [(str(provider), str(kind)) for provider, kind in rows]


def _kinds(store: Store) -> list[str]:
    return [str(row["kind"]) for row in store.diagnostics()]


@pytest.mark.integration
@pytest.mark.parametrize("route", ["/v1/logs", "/v1/metrics"])
def test_an_unrecognized_service_name_is_refused_and_the_diagnostic_names_it(
    store: Store, daemon: int, settled: Callable[[Store], Store], route: str
) -> None:
    """Present and undeclared: a 200, no row, and one parse_failure naming the value."""
    resource = _resource(service_name=STRANGER)
    body = _logs(_block(resource)) if route == "/v1/logs" else _metrics(resource)

    assert _post(daemon, route, body) == 200
    settled(store)
    assert _stored(store.path) == []
    # Nothing else: no `launcher` row for an unattributed record, because the record
    # never reached attribution.
    assert _kinds(store) == ["parse_failure"]
    detail = str(store.diagnostics()[0]["detail"])
    assert detail == f"{route}: ValueError: unrecognized service.name {STRANGER!r}"


@pytest.mark.integration
@pytest.mark.parametrize("stranger_first", [True, False])
def test_one_unrecognized_block_refuses_the_whole_batch(
    store: Store, daemon: int, settled: Callable[[Store], Store], stranger_first: bool
) -> None:
    """A known block beside the stranger does not carry it in, in either order.

    Before W10-F1 both orders were stored whole as `claude`, the stranger's record
    included, because the first known name decided the batch.
    """
    known = _block(_resource(service_name="claude-code"))
    stranger = _block(_resource(service_name=STRANGER))
    blocks = (stranger, known) if stranger_first else (known, stranger)

    assert _post(daemon, "/v1/logs", _logs(*blocks)) == 200
    settled(store)
    assert _stored(store.path) == []
    assert _kinds(store) == ["parse_failure"]
    assert STRANGER in str(store.diagnostics()[0]["detail"])


@pytest.mark.integration
@pytest.mark.parametrize(
    "resource",
    [None, _resource(host_name="probe-host")],
    ids=["no-resource", "resource-without-service-name"],
)
def test_an_absent_service_name_still_takes_the_default_provider(
    store: Store,
    daemon: int,
    settled: Callable[[Store], Store],
    resource: dict[str, Any] | None,
) -> None:
    """Absent is not unrecognized: the record is parsed as the default, as before.

    The daemon's default is `claude`. The `launcher` row is the unattributed record's
    own diagnostic (no capture query, no session id), and it is the proof the record
    went on through attribution rather than being refused at routing. The
    `unknown_field` row is the claude allowlist meeting a codex event's attributes,
    which is how the daemon's 18,897 rows came to have emptied payloads: an absent name
    routes to the default exactly as before, including what the default gets wrong.
    """
    assert _post(daemon, "/v1/logs", _logs(_block(resource))) == 200
    settled(store)
    assert _stored(store.path) == [("claude", f"claude.otel.{EVENT}")]
    assert sorted(_kinds(store)) == ["launcher", "unknown_field"]


@pytest.mark.integration
@pytest.mark.parametrize(("service_name", "provider"), sorted(SERVICE_NAMES.items()))
def test_every_declared_service_name_still_routes_to_its_provider(
    store: Store,
    daemon: int,
    settled: Callable[[Store], Store],
    service_name: str,
    provider: str,
) -> None:
    """Each name in SERVICE_NAMES reaches the provider it names, and is not refused."""
    body = _logs(_block(_resource(service_name=service_name)))

    assert _post(daemon, "/v1/logs", body) == 200
    settled(store)
    stored = _stored(store.path)
    assert [row_provider for row_provider, _kind in stored] == [provider]
    assert "parse_failure" not in _kinds(store)
