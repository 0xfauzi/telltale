"""Validate export rows before the import writes any destination row.

Legacy compatibility names exact historical fields and validates their numeric values.
Stored commands use the removal-only normalizer plus explicit path and version checks.
Neither compatibility rule accepts arbitrary unknown fields or raw command paths.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import fields
from typing import Any

from telltale import commands
from telltale.allowlist import ALLOWLIST, Kind
from telltale.model import Observation, to_json
from telltale.sanitize import MAX_PAYLOAD_BYTES, Ctx, relativize, sanitize, scrub

JSON_FIELDS = ("correlation_ids", "payload", "redaction")
LEGACY_FIELDS = {
    "claude.stream.assistant": {"output_tokens"},
    "claude.stream.result": {
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    },
}
_VERSION = re.compile(r"cmdnorm-v[1-5](?:-fallback)?\Z")
_OPTIONAL = {
    "provider_ts",
    "provider_session_id",
    "environment_fingerprint_id",
    "repo_id",
}


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def decode(text: str) -> Any:
    return json.loads(text, object_pairs_hook=unique_object)


def observation(row: dict[str, Any]) -> Observation:
    expected = {spec.name for spec in fields(Observation)}
    if set(row) != expected:
        raise ValueError(
            f"observation columns: missing {sorted(expected - set(row))},"
            f" unexpected {sorted(set(row) - expected)}"
        )
    for name, value in row.items():
        if name in JSON_FIELDS:
            _json_column(name, value)
        elif name == "schema_version":
            if type(value) is not int or value < 1 or value > 2**63 - 1:
                raise ValueError("schema_version must be a positive SQLite integer")
        elif not (name in _OPTIONAL and value is None):
            _text(name, value)
    return Observation(**row)


def _text(name: str, value: Any) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must be UTF-8 text") from None


def _json_column(name: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    json.dumps(value, allow_nan=False).encode("utf-8")
    if name == "correlation_ids" and any(
        not isinstance(item, str) for item in value.values()
    ):
        raise ValueError("correlation_ids values must be text")
    if name == "redaction" and any(
        not isinstance(items, list) or any(not isinstance(item, str) for item in items)
        for items in value.values()
    ):
        raise ValueError("redaction values must be lists of text")


def levels(rows: list[dict[str, Any]]) -> tuple[int, ...]:
    starts = [
        row for row in rows if row["observation_type"] == "telltale.capture_started"
    ]
    if len(starts) > 1:
        raise ValueError("capture carries multiple capture_started observations")
    if not starts:
        return (0, 1)
    payload = starts[0]["payload"]
    if "content_level" not in payload:
        raise ValueError("capture_started does not record content_level")
    level = payload["content_level"]
    if type(level) is not int or level not in (0, 1, 2):
        raise ValueError("capture content_level must be 0, 1 or 2")
    return (level,)


def payload(row: dict[str, Any], content_levels: tuple[int, ...]) -> dict[str, int]:
    failures: list[str] = []
    for level in content_levels:
        try:
            return _payload(row["observation_type"], row["payload"], level)
        except ValueError as error:
            failures.append(str(error))
    raise ValueError(f"{row['observation_id']}: {'; '.join(failures)}")


def _payload(obs_type: str, original: dict[str, Any], level: int) -> dict[str, int]:
    if not original:
        return {}
    if obs_type not in ALLOWLIST:
        raise ValueError(f"unknown observation type {obs_type!r}")
    if len(to_json(original).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds its byte bound")
    checked = dict(original)
    drift = _legacy(obs_type, checked)
    version = checked.pop("normalization_version", None)
    _commands(obs_type, checked, version, level)
    if "normalization_version" in original and not _version_known(version):
        raise ValueError("unknown normalization_version")
    cleaned, redaction, _unknown = sanitize(obs_type, checked, level, Ctx())
    removed = [entry for entries in redaction.values() for entry in entries]
    if removed or cleaned != checked:
        raise ValueError(f"payload fails sanitize: {removed or 'changed value'}")
    return drift


def _commands(obs_type: str, checked: dict[str, Any], version: Any, level: int) -> None:
    """Check and remove every COMMAND field, so the sanitizer sees none of them."""
    for name, kind in ALLOWLIST[obs_type].items():
        if kind is Kind.COMMAND and isinstance(checked.get(name), str):
            _command(checked.pop(name), version, level)


def _version_known(version: Any) -> bool:
    return isinstance(version, str) and _VERSION.fullmatch(version) is not None


def _legacy(obs_type: str, checked: dict[str, Any]) -> dict[str, int]:
    drift: dict[str, int] = {}
    for name in LEGACY_FIELDS.get(obs_type, set()):
        if name not in checked:
            continue
        value = checked.pop(name)
        if value is not None and (
            type(value) not in (int, float) or not math.isfinite(value) or value < 0
        ):
            raise ValueError(
                f"legacy field {name} must hold a finite nonnegative number"
            )
        drift[f"{obs_type}.{name}"] = 1
    return drift


def _command(value: str, version: Any, level: int) -> None:
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError("a stored command requires a known normalization_version")
    if len(value) > commands.MAX_COMMAND or scrub(value)[1]:
        raise ValueError("command exceeds its bound or contains a secret")
    checked = _bounded_prefix(value, version)
    rewritten, hits = commands.renormalize(checked)
    if rewritten != checked.rstrip() or hits:
        raise ValueError(
            "command is not a stored normal form: run `telltale resanitize` on the"
            " source store and export again"
        )
    for token in value.split():
        if commands._ENV_ASSIGN.match(token) and not token.endswith("="):
            raise ValueError("command contains an environment value")
        if commands._is_path_like(token) and (
            level == 0 or relativize(token, Ctx(), 1) != token
        ):
            raise ValueError("command contains an unsanitized path")


def _bounded_prefix(value: str, version: str) -> str:
    """Validate complete tokens separately from a known bound's safe final prefix."""
    bound = 512 if version.startswith("cmdnorm-v5") else 200
    if len(value) != bound or " " not in value:
        return value
    prefix, _, last = value.rpartition(" ")
    if last in {"-", "--"} or (last and "<outside>/".startswith(last)):
        return prefix
    return value
