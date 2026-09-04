"""`telltale doctor --matrix`: which provider runtimes this store has captured, and
what each one turned out to be observable through.

Spec 9.1's capability matrix is a claim about a provider. This is the measured version
of the same table over one store: one row per (provider, runtime version) that captures
on this disk actually carry, with the coverage word each capability got, counted as the
lifecycle activities recorded it (design 6.7 and 6.10). Beside it, the provider's DRIFT
list, which is what the parser had to code around to get those words.

Read-only, and it opens no writer: every method it calls takes its own read-only
connection, so a matrix runs while a capture is in flight.

Three places carry a runtime version, and they are three different facts, so a row
names which ones spoke for it rather than presenting one as the other:

  probe   `telltale.environment.runtime_version`, the launcher's `<binary> --version`
          answer from before the child started (launch.runtime_version). It is what
          `telltale sessions` prints as `runtime`.
  session the `session_start` lifecycle field `claude_code_version`, which is what the
          running session said about itself on its own stream.
  record  a version the provider wrote into its own session file, read by `telltale
          import`: `version` on a claude transcript line, `cli_version` on a codex
          rollout `session_meta`.

Measured on the owner's store on 2026-09-03 (3312 captures, 1131250 observations,
1.5 GB), counting which source won each capture: probe 136, session 7, record 3147, and
22 captures that no source names. Nothing disagreed where two of them spoke, once the
version token is taken out of the probe's string ("2.1.259 (Claude Code)" against
"2.1.259"). A capture no source names keeps runtime None and gets a row of its own:
not knowing which version produced a capture is a fact about this store, and folding
those captures into a version's row would put coverage counts under a version nobody
measured.

What is NOT read, and why: the per-record `service_version` resource attribute that
every OTel record of both providers carries. It lives in observation payloads, and no
store read answers "one row per capture" for a payload field, so reaching it means
reading every OTel observation in the database. Measured on the owner's 1.5 GB store,
`matrix()` takes 2.95 s warm and 8.82 s on a cold page cache; the cheapest addition (the
268655 claude transcript-assistant rows) costs 2.57 s more warm and named 0 captures the
transcript-user read had not already named. It stays out until a capture needs it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from telltale import providers
from telltale.facts import text
from telltale.report import render_table

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from telltale.store import Store

# Where a runtime version is read from, strongest first. The order is the order of
# directness: the launcher measured the binary it ran, the session said what it was,
# and an imported file says what wrote it.
SOURCES = ("probe", "session", "record")

# The observation type and the payload field each `record` version lives in. One
# store-wide read each: measured on the owner's store, 1601 codex session_meta rows in
# 0.02 s and 103879 claude transcript-user rows in 1.1 s.
_RECORDED = (
    ("claude.transcript.user", "version"),
    ("codex.rollout.session_meta", "cli_version"),
)

# The coverage words design 6.7 allows, in the order a row prints them: best first, so
# "observed 80, partial 3" reads as a summary rather than as a histogram in some order
# of the store's own.
WORDS = ("observed", "derived", "partial", "unavailable")

# A dotted number anywhere in a version string. `claude --version` answers
# "2.1.259 (Claude Code)" and `codex --version` answers "codex-cli 0.150.1" (both
# measured on 2026-09-03), while the session and the session file say "2.1.259".
# Without this the same runtime is two rows. A string with no dotted number is kept
# WHOLE rather than dropped: the fake agent answers "fake-agent", and that is a runtime
# this store has captures of.
_TOKEN = re.compile(r"\d+(?:\.\d+)+")

# Dates, not timestamps: two versions of a provider are weeks apart and the seconds
# would double the width of every row. The capture ids and `telltale sessions` are
# where an instant is asked for.
#
# The dates are the captures view's first_ts and last_ts, which are ARRIVAL (design
# 6.2), so "seen" here means when this store saw it. For an imported session that is
# the day `telltale import` read the file and not the day the session ran, which is
# why the columns are not called first_ran: a provider's own clock is on the session
# activity and `telltale sessions` is what prints it.
_DATE = 10

_COLUMNS = (
    "provider",
    "runtime",
    "sources",
    "captures",
    "first_seen",
    "last_seen",
    "surfaces",
)
_COVERAGE_COLUMNS = ("provider", "runtime", "capability", "coverage", "measured")


def matrix(store: Store) -> list[dict[str, Any]]:
    """One row per (provider, runtime version) this store holds captures for.

    Sorted by provider, then by version, with the unnamed runtime last: a row nobody
    can put a version on is the one a reader wants at the bottom of the table.
    """
    probed = _probed(store)
    recorded = _recorded(store)
    groups: dict[tuple[str, str | None], _Group] = {}
    for capture in store.captures():
        capture_id = str(capture["capture_id"])
        provider = str(capture["provider"])
        stated, coverage, surfaces = _from_activities(store, capture_id)
        version, source = _pick(
            probed.get(capture_id), stated, recorded.get(capture_id)
        )
        group = groups.setdefault((provider, version), _Group())
        group.add(capture, source, coverage, surfaces)
    return [
        groups[key].row(provider=key[0], runtime=key[1], drift=_drift(key[0]))
        for key in sorted(groups, key=_order)
    ]


def render(rows: Sequence[Mapping[str, Any]]) -> str:
    """The matrix as text: the runtimes, the coverage words, then DRIFT per provider.

    Three blocks rather than one wide table. Ten capabilities beside seven columns of
    identity is a 200-character line, and a DRIFT list is a property of the provider
    MODULE rather than of any runtime, so it is printed once per provider however many
    runtimes of it this store holds.
    """
    if not rows:
        return "no captures on this disk, so there is no compatibility matrix"
    blocks = [
        render_table(rows, _COLUMNS),
        "capability coverage, as the lifecycle activities recorded it:\n"
        + render_table(list(_coverage_rows(rows)), _COVERAGE_COLUMNS),
    ]
    # dict.fromkeys and not a set: the providers in the order the table printed them.
    for provider in dict.fromkeys(str(row["provider"]) for row in rows):
        drift = next(row["drift"] for row in rows if row["provider"] == provider)
        blocks.append(_drift_block(provider, drift))
    return "\n\n".join(blocks)


class _Group:
    """The captures of one (provider, runtime), folded as they are read.

    A class rather than a dict of dicts because two of the five things a group
    accumulates are SETS, and a row that carried them would hand a caller an internal
    it must not print. `row()` is where a group becomes a row, and nothing else does.
    """

    def __init__(self) -> None:
        self.captures = 0
        self.first_seen: str | None = None
        self.last_seen: str | None = None
        self.sources: set[str] = set()
        self.surfaces: set[str] = set()
        # {capability: {word: captures}}. The words count only the captures that
        # carried a coverage map, which is fewer than `captures` when one of them was
        # never reduced. Counting an unreduced capture as `unavailable` would be an
        # answer nobody measured.
        self.coverage: dict[str, dict[str, int]] = {}

    def add(
        self,
        capture: Mapping[str, Any],
        source: str | None,
        coverage: Mapping[str, Any],
        surfaces: Iterable[str],
    ) -> None:
        self.captures += 1
        self.first_seen = _min(self.first_seen, str(capture["first_ts"])[:_DATE])
        self.last_seen = _max(self.last_seen, str(capture["last_ts"])[:_DATE])
        if source is not None:
            self.sources.add(source)
        self.surfaces.update(surfaces)
        for capability, word in coverage.items():
            counted = self.coverage.setdefault(str(capability), {})
            counted[str(word)] = counted.get(str(word), 0) + 1

    def row(
        self, provider: str, runtime: str | None, drift: Sequence[str]
    ) -> dict[str, Any]:
        return {
            "provider": provider,
            "runtime": runtime,
            # None, not "": a group no source named has no source, and the table
            # prints unknown as `-`.
            "sources": ", ".join(name for name in SOURCES if name in self.sources)
            or None,
            "captures": self.captures,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "surfaces": ", ".join(sorted(self.surfaces)) or None,
            "coverage": self.coverage,
            "drift": list(drift),
        }


def _probed(store: Store) -> dict[str, str | None]:
    """The launcher's `--version` answer per capture. One indexed read of 157 rows."""
    return {
        str(row["capture_id"]): _version(
            text((row["payload"] or {}).get("runtime_version"))
        )
        for row in store.observations_of_type("telltale.environment")
    }


def _recorded(store: Store) -> dict[str, str | None]:
    """The version a provider wrote into its own session file, per capture.

    The first row of a capture that carries one wins: `observations_of_type` orders by
    (capture_id, observation_id), so that is the earliest line of the file that had a
    version on it.
    """
    found: dict[str, str | None] = {}
    for obs_type, field in _RECORDED:
        for row in store.observations_of_type(obs_type):
            capture_id = str(row["capture_id"])
            version = _version(text((row["payload"] or {}).get(field)))
            if version is not None and capture_id not in found:
                found[capture_id] = version
    return found


def _from_activities(
    store: Store, capture_id: str
) -> tuple[str | None, dict[str, Any], list[str]]:
    """(what the session said its version was, the coverage map, the surfaces seen).

    One read of one capture's lifecycle rows, and it is the read that has to happen per
    capture whatever else this module does: the coverage map lives on the capture's own
    lifecycle activity and nowhere else (design 6.7). Measured on the owner's store,
    3312 of these take 0.78 s.
    """
    stated: str | None = None
    coverage: dict[str, Any] = {}
    surfaces: list[str] = []
    for row in store.activities(capture_id, ("lifecycle",)):
        fields = dict(row["fields"])
        event = fields.get("event")
        if event == "session_start" and stated is None:
            stated = _version(text(fields.get("claude_code_version")))
        elif event == "capture":
            if isinstance(fields.get("coverage"), dict):
                coverage = dict(fields["coverage"])
            surfaces = [str(name) for name in fields.get("surfaces_delivered") or ()]
    return stated, coverage, surfaces


def _pick(
    probed: str | None, stated: str | None, recorded: str | None
) -> tuple[str | None, str | None]:
    """(the version, the source that named it), or (None, None) when none did."""
    named = ((probed, "probe"), (stated, "session"), (recorded, "record"))
    return next(((one, where) for one, where in named if one is not None), (None, None))


def _version(raw: str | None) -> str | None:
    """The dotted-number token of a version string, or the string itself. `_TOKEN`."""
    if raw is None:
        return None
    found = _TOKEN.search(raw)
    return found.group(0) if found else raw


def _drift(provider: str) -> list[str]:
    """The provider module's DRIFT list, or empty for a provider that has no module.

    `generic` and `telltale` are real values in the provider column: a wrapped `make
    test` and this system's own captures. Neither has a parser, so neither has a drift
    list, and the empty one here is not a claim that a provider has no drift.
    """
    try:
        return list(providers.get(provider).DRIFT)
    except (ValueError, ImportError):
        return []


def _coverage_rows(rows: Sequence[Mapping[str, Any]]) -> Iterator[dict[str, Any]]:
    """One row per (provider, runtime, capability) that some capture measured.

    `measured` is the number of captures in the group whose lifecycle activity carried
    a coverage map, which is the denominator of the words beside it. It has its own
    name because it is NOT the group's capture count: an unreduced capture measured
    nothing, and design 6.7's words are only about the captures that were reduced.
    """
    for row in rows:
        for capability, counted in sorted(row["coverage"].items()):
            yield {
                "provider": row["provider"],
                "runtime": row["runtime"],
                "capability": capability,
                "coverage": _words(counted),
                "measured": sum(counted.values()),
            }


def _words(counted: Mapping[str, int]) -> str:
    """`observed 80, partial 3`: WORDS order first, then anything else by name."""
    known = [word for word in WORDS if word in counted]
    listed = [*known, *sorted(set(counted) - set(known))]
    return ", ".join(f"{word} {counted[word]}" for word in listed)


def _drift_block(provider: str, drift: Sequence[str]) -> str:
    if not drift:
        return f"DRIFT, {provider}: no provider module, so nothing measured a drift"
    listed = "\n".join(f"  - {line}" for line in drift)
    return f"DRIFT, {provider} ({len(drift)} measured differences):\n{listed}"


def _order(key: tuple[str, str | None]) -> tuple[str, int, tuple[int, ...], str]:
    """Provider, then the version as NUMBERS, then anything unnumbered, then unknown.

    As numbers because a string sort puts codex 0.39.0 after 0.150.1, and a table of
    versions whose order is wrong is a table a reader has to sort by hand.
    """
    provider, runtime = key
    if runtime is None:
        return (provider, 2, (), "")
    numbers = _numbers(runtime)
    return (provider, 0 if numbers else 1, numbers, runtime)


def _numbers(runtime: str) -> tuple[int, ...]:
    """(2, 1, 259) for "2.1.259", and () for a version with no dotted number in it."""
    found = _TOKEN.search(runtime)
    return tuple(int(part) for part in found.group(0).split(".")) if found else ()


def _min(current: str | None, candidate: str) -> str:
    return candidate if current is None else min(current, candidate)


def _max(current: str | None, candidate: str) -> str:
    return candidate if current is None else max(current, candidate)
