"""The `telltale intervention` subcommand: the acting policy's own record. Spec 14.6."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import cli_common as common
from telltale import config, repo
from telltale.model import Observation, now_iso, to_json, ulid
from telltale.sanitize import MAX_ENUM, MAX_ID, Ctx, sanitize
from telltale.series_regime import INTERVENTION_TYPE
from telltale.store import Store

if TYPE_CHECKING:
    import argparse

# The capture id prefix and how much of the hash it carries, both for cli_advise.py's
# reasons: 24 hex is 96 bits, short enough to read aloud, and the prefix keeps a
# telltale-emitted capture apart from a launched `cap_<ULID>` by its alphabet alone.
PREFIX = "pol_"
ID_HEX = 24
PROVIDER = "telltale"
SURFACE = "telltale"
ADAPTER = "telltale.intervention@1"
# A sanitized payload holds no path and no command, so the content level changes
# nothing about what is stored. 1 is the default a capture runs at.
LEVEL = 1

# What a bounded lowercase token is. `Kind.ENUM` is a symbolic value, and a value with
# a space or a capital in it is prose that happens to be short: the sanitizer would
# store it unchanged and every later reader would have a second spelling to match.
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
# An id may carry the case an advisory id was minted with. `adv_` ids are lowercase
# hex, but this command records ids an EXTERNAL system chose.
_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def intervention(args: argparse.Namespace) -> int:
    """Record that a policy acted on one advisory. Spec 14.6."""
    repo_id = repo.identity(".")["repo_id"]
    if not repo_id:
        return common.refuse(
            "telltale intervention: no repository here, so there is no lineage this"
            " intervention is a boundary in. Run it inside the repository the policy"
            " acted on"
        )
    try:
        payload = _payload(args)
        stamp = _stamp(args.at)
    except ValueError as refused:
        return common.refuse(f"telltale intervention: {refused}")
    return _append(payload, stamp, str(repo_id), args.db)


def _payload(args: argparse.Namespace) -> dict[str, Any]:
    """Check the four allowlisted intervention fields."""
    return {
        "advisory_id": _checked("--advisory-id", args.advisory_id, _IDENT, MAX_ID),
        "action": _checked("--action", args.action, _TOKEN, MAX_ENUM),
        "policy_version": _checked(
            "--policy-version", args.policy_version, _TOKEN, MAX_ENUM
        ),
        "external_system": _checked(
            "--external-system", args.external_system, _TOKEN, MAX_ENUM
        ),
    }


def _checked(flag: str, value: str, shape: re.Pattern[str], limit: int) -> str:
    """One field, or the refusal naming the flag and what it may hold."""
    if len(value) > limit:
        raise ValueError(
            f"{flag} is {len(value)} characters and the store bounds this field at"
            f" {limit}; a value stored shorter than it was given matches nothing"
        )
    if not shape.fullmatch(value):
        raise ValueError(
            f"{flag} {value!r} is not a token: {shape.pattern}. A symbolic field"
            " holding a sentence is a second spelling nobody can match"
        )
    return value


def _stamp(at: str | None) -> str:
    """`--at` in the store's spelling, or now. Never a naive local time."""
    if at is None:
        return now_iso()
    try:
        when = datetime.fromisoformat(at)
    except ValueError as bad:
        raise ValueError(f"--at {at!r} is not ISO 8601: {bad}") from bad
    if when.tzinfo is None:
        raise ValueError(
            f"--at {at!r} carries no timezone. Spell the offset, or Z for UTC:"
            " a lineage boundary is an instant, not a wall clock"
        )
    spelled = when.astimezone(UTC).isoformat(timespec="microseconds")
    return spelled.replace("+00:00", "Z")


def capture_id(payload: dict[str, Any], stamp: str) -> str:
    """`pol_` plus 24 hex over what this intervention IS: the four fields and when."""
    canonical = to_json([payload, stamp])
    return f"{PREFIX}{hashlib.sha256(canonical.encode()).hexdigest()[:ID_HEX]}"


def _append(payload: dict[str, Any], stamp: str, repo_id: str, db: str | None) -> int:
    """Sanitize, append, flush. The one write, through the store and nothing else."""
    capture = capture_id(payload, stamp)
    body, redaction, unknown = sanitize(INTERVENTION_TYPE, payload, LEVEL, Ctx())
    observation_id = ulid()
    store = Store(_db_path(db)).open()
    try:
        store.append([
            Observation(
                observation_id=observation_id,
                capture_id=capture,
                observation_type=INTERVENTION_TYPE,
                surface=SURFACE,
                provider=PROVIDER,
                adapter=ADAPTER,
                ingest_ts=now_iso(),
                provider_ts=stamp,
                repo_id=repo_id,
                payload=body,
                redaction=redaction,
            )
        ])  # fmt: skip
        if unknown:
            # Named with the type, the way cli_advise does: this system's own payload
            # reaching here is a bug in this file rather than a provider that moved.
            store.diagnose(
                "unknown_field",
                f"{INTERVENTION_TYPE}: {' '.join(sorted(unknown))}",
                capture_id=capture,
            )
        store.flush()
    finally:
        store.close()
    print(f"intervention {payload['advisory_id']} recorded at {stamp}")
    print(f"observation {observation_id} in capture {capture}")
    print(
        "series built on this repository now carry the boundary. Segment one with"
        f" `telltale series build --clock attempt --repo {repo_id} --regime post"
        f" --intervention {payload['advisory_id']}`"
    )
    return 0


def _db_path(db: str | None) -> Path:
    """`--db`, or `$TELLTALE_HOME`'s database. Refused when it is not there."""
    if db is None:
        return config.db_path()
    found = Path(db).expanduser()
    if not found.exists():
        raise SystemExit(f"{found}: no database. Run a capture, or drop --db.")
    return found


def add_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """`telltale intervention --advisory-id ID --action A ...`. Spec 14.6."""
    said = subcommands.add_parser(
        "intervention", help="record that a policy acted on an advisory (spec 14.6)"
    )
    said.add_argument("--advisory-id", required=True, metavar="ID")
    said.add_argument(
        "--action",
        required=True,
        metavar="ACTION",
        help="what the policy did, as a bounded lowercase token",
    )
    said.add_argument("--policy-version", required=True, metavar="V")
    said.add_argument(
        "--external-system",
        required=True,
        metavar="NAME",
        help="the system that acted, as a bounded lowercase token",
    )
    said.add_argument(
        "--at",
        default=None,
        metavar="ISO8601",
        help="when the policy acted, with a timezone. Default: now. This is what the"
        " lineage boundary is measured against, not when the row was written",
    )
    said.add_argument(
        "--db", default=None, metavar="PATH", help="default: $TELLTALE_HOME's database"
    )
