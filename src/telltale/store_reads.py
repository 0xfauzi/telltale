"""Every SELECT the database answers, as the read half of `Store`.

Split out of store.py on 2026-09-02, when it stood at 752 lines against the 800-line
ratchet and W2-T8 had to add `resanitize`. Nothing here changed in the move: these are
the same methods on the same class, mixed in below, and the tests that exercise them did
not move either.

They belong together because they share one property that the rest of store.py does not
have: a read takes no writer thread, no queue and no lock. Each opens its own read-only
connection and closes it, which is why a report can run while a capture is in flight.
`_read` itself stays in store.py, beside the connection function it calls.
"""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING, Any

from telltale.model import ColumnSpec, RowMeta, Series, to_json

if TYPE_CHECKING:
    from collections.abc import Sequence


class Reads:
    """The read half of `Store`. The class that mixes this in supplies `_read`."""

    def _read(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """One read-only connection per call, JSON decoded. See `Store._read`."""
        raise NotImplementedError

    def captures(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM captures ORDER BY first_ts DESC")

    def observations_of_type(self, observation_type: str) -> list[dict[str, Any]]:
        """One observation type across every capture, in capture then arrival order.

        The read that asks a question of the STORE rather than of one capture, and the
        reason obs_by_type_capture leads on the type: cohorts.py needs the environment
        fingerprint of every capture, and there is one of those per capture out of
        hundreds of rows. Measured on the owner's 3200-capture, 1044858-observation
        store on 2026-09-02: 47 rows, 1 ms.
        """
        return self._read(
            "SELECT * FROM observations WHERE observation_type = ?"
            " ORDER BY capture_id, observation_id",
            (observation_type,),
        )

    def observations(
        self, capture_id: str, types: Sequence[str] = ()
    ) -> list[dict[str, Any]]:
        """One capture's observations, or only the types the caller names.

        Two statements because they take two indexes, and the ORDER BY is what decides
        which. Unfiltered, obs_by_capture is (capture_id, observation_id) and answers
        both the search and the order with no sort. Filtered, the rows wanted are a
        handful out of hundreds, and obs_by_type_capture seeks straight to them: the
        unary plus takes observation_id out of the ORDER BY's index candidates, so the
        planner stops preferring obs_by_capture for the sort and pays a temp b-tree
        over the few rows it did read. Measured on the owner's 3200-capture store,
        3200 two-type reads: 0.266 s without the plus, 0.026 s with it.

        json_each rather than a generated list of placeholders, as `observations_by_id`
        does it: one constant statement, and no SQL text built from a caller's values.
        """
        if not types:
            return self._read(
                "SELECT * FROM observations WHERE capture_id = ?"
                " ORDER BY observation_id",
                (capture_id,),
            )
        return self._read(
            "SELECT * FROM observations WHERE capture_id = ? AND observation_type IN"
            " (SELECT value FROM json_each(?)) ORDER BY +observation_id",
            (capture_id, to_json(list(types))),
        )

    def observations_by_id(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        """Resolve Evidence.source ids. json_each keeps this one constant statement."""
        return self._read(
            "SELECT * FROM observations WHERE observation_id IN"
            " (SELECT value FROM json_each(?)) ORDER BY observation_id",
            (to_json(list(ids)),),
        )

    def activities(
        self, capture_id: str, types: Sequence[str] = ()
    ) -> list[dict[str, Any]]:
        """One capture's activities, or only the types the caller names.

        One statement, unlike `observations`: activities_by_capture is
        (capture_id, started_at) and the second ORDER BY term already costs a temp
        b-tree, so naming types adds a filter and no index decision. What it saves is
        the rows themselves and the JSON decoding of their fields and provenance.
        Measured on the owner's store, 3200 whole-capture reads over 344607 rows:
        0.595 s, against 0.176 s for the 9684 lifecycle rows among them.
        """
        return self._read(
            "SELECT * FROM activities WHERE capture_id = ?1 AND (?2 IS NULL OR"
            " activity_type IN (SELECT value FROM json_each(?2)))"
            " ORDER BY started_at, activity_id",
            (capture_id, to_json(list(types)) if types else None),
        )

    def evidence(self, capture_id: str) -> list[dict[str, Any]]:
        return self._read(
            "SELECT * FROM evidence WHERE capture_id = ? ORDER BY metric", (capture_id,)
        )

    def series(self, series_id: str) -> Series | None:
        """One stored snapshot, back in the shape a forecaster takes. None when absent.

        The two nested shapes are rebuilt here rather than left as dicts, because
        ColumnSpec refuses a coverage word that is not one of the four and that check
        is the only thing standing between a hand-edited row and a forecast built on it.
        """
        found = self._read(
            "SELECT * FROM series_snapshots WHERE series_id = ?", (series_id,)
        )
        if not found:
            return None
        row = found[0]
        # The field list comes off the dataclass, as store.py's INSERT column tuple
        # does, so the two cannot name different columns.
        return Series(
            **{
                **{spec.name: row[spec.name] for spec in fields(Series)},
                "columns": [ColumnSpec(**spec) for spec in row["columns"]],
                "row_meta": [RowMeta(**meta) for meta in row["row_meta"]],
            }
        )

    def series_ids(self, clock: str | None = None) -> list[dict[str, Any]]:
        """What snapshots exist, without reading their rows out of the database."""
        return self._read(
            "SELECT series_id, clock, cohort, json_array_length(rows) AS rows,"
            " built_at FROM series_snapshots WHERE (?1 IS NULL OR clock = ?1)"
            " ORDER BY built_at DESC",
            (clock,),
        )

    def forecast_runs(self, series_id: str) -> list[dict[str, Any]]:
        return self._read(
            "SELECT * FROM forecast_runs WHERE series_id = ? ORDER BY created_at",
            (series_id,),
        )

    def diagnostics(self, capture_id: str | None = None) -> list[dict[str, Any]]:
        return self._read(
            "SELECT * FROM diagnostics WHERE (?1 IS NULL OR capture_id = ?1)"
            " ORDER BY ingest_ts",
            (capture_id,),
        )

    def diagnostics_by_id(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        """Resolve exported diagnostic identities without collapsing equal details."""
        return self._read(
            "SELECT * FROM diagnostics WHERE diagnostic_id IN"
            " (SELECT value FROM json_each(?)) ORDER BY diagnostic_id",
            (to_json(list(ids)),),
        )
