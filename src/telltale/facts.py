"""What one stored capture says about itself. The reading half of launch.py.

Split out of launch.py by W1-T4 with no behaviour change, because that file was 761
lines against the 800-line ratchet and the environment probe had to go somewhere. The
split follows the direction of the dependency rather than convenience: nothing here
writes, so nothing here imports the launcher, and `telltale sessions` reads a capture
through this module while `telltale run` writes one through that one.

Every value is read out of the capture's own `telltale.*` observations. A field a
capture never recorded stays None; there is no default anywhere in this file, because a
capture that ended without an exit code and one that exited 0 are different facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from telltale.store import Store


@dataclass
class Facts:
    """What one stored capture says about itself, from its telltale.* observations."""

    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    model: str | None = None
    repo_id: str | None = None
    worktree_id: str | None = None
    surfaces_configured: list[str] = field(default_factory=list)
    surfaces_received: dict[str, int] = field(default_factory=dict)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    commits: int = 0

    def coverage(self) -> str | None:
        """`delivered/configured` surfaces, or None when no plan configured any.

        None rather than 0/0: a capture with no launch plan (a generic child, or a
        provider module that has none yet) configured nothing, and "0 of 0 surfaces
        delivered" reads like a failure of something that was never attempted.
        """
        if not self.surfaces_configured:
            return None
        delivered = [
            name
            for name in self.surfaces_configured
            if self.surfaces_received.get(name)
        ]
        return f"{len(delivered)}/{len(self.surfaces_configured)}"


def facts(store: Store, capture_id: str) -> Facts:
    """One pass over a capture's observations for everything a reader asks of it."""
    out = Facts()
    for row in store.observations(capture_id):
        _read(out, str(row["observation_type"]), row)
    return out


def _read(out: Facts, obs_type: str, row: Mapping[str, Any]) -> None:
    payload = row["payload"]
    if obs_type == "telltale.capture_started":
        out.started_at = str(row["ingest_ts"])
        out.repo_id = text(row["repo_id"])
        out.worktree_id = text(payload.get("worktree_id"))
        out.surfaces_configured = [
            str(name) for name in payload.get("surfaces_configured") or ()
        ]
    elif obs_type == "telltale.capture_ended":
        out.ended_at = str(row["ingest_ts"])
        out.duration_ms = _whole(payload.get("duration_ms"))
        out.exit_code = _whole(payload.get("exit_code"))
        out.surfaces_received = {
            str(name): int(count)
            for name, count in (payload.get("surfaces_received") or {}).items()
        }
    elif obs_type == "telltale.environment":
        out.model = text(payload.get("model"))
    elif obs_type == "telltale.repo.snapshot":
        out.snapshots.append(dict(payload))
    elif obs_type == "telltale.repo.commit":
        out.commits += 1


def text(value: Any) -> str | None:
    """A non-empty string, or None. The empty string is not a value here.

    Public because launch.py reads the same untrusted structures (a repo identity, an
    environment fingerprint) on the way IN and needs the same answer for them.
    """
    return value if isinstance(value, str) and value else None


def _whole(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
