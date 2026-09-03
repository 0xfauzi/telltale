"""What a scenario run prints: the page, and the two-scenario comparison. W6-T1.

Split from forecast/scenario.py at the 800-line ratchet, on the line report.py and
report_advise.py are already split along: running a scenario and rendering one are two
jobs, and only this half knows about table widths.

Both functions end with SCENARIO_SENTENCE and both pass their finished string through
`refuse_words` before anything is printed, which is design 6.12's rule and ADR-014's:
the check runs before the caller stores, because a check after the INSERT is a check of
a row that is already on the disk.

`compare` reads two forecast_runs ROWS rather than two run dicts. `scenario.record`
builds one and `Store.forecast_runs` reads one back in the same shape, so a fresh
scenario and a stored one meet here without a second decoder in between.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telltale.forecast import POINT_INDEX, SCENARIO_SENTENCE, refuse_words
from telltale.forecast.candidate import licences
from telltale.forecast.scenario import NO_CALIBRATION, NotComparable
from telltale.report import render_table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_PATH_TABLE = ("column", "unit", "declarable", "path")
_FORECAST_TABLE = ("forecaster", "step", "point", "q10", "q50", "q90", "read_future")
_COMPARE_TABLE = ("forecaster", "step", "a", "b", "difference")


def report(found: Mapping[str, Any]) -> str:
    """The whole scenario on one page, ending with the sentence and the licence."""
    lines = [
        f"forecast scenario  {found['name']}  series {found['series_id']}"
        f"  target {found['target']} ({found['unit']})",
        f"clock {found['clock']}  variant {found['variant']}"
        f"  horizon {found['horizon']}  model {found['model']}"
        f"  policy {found['missingness_policy']}",
        f"origin {found['origin']} (one past row {found['n_rows'] - 1}, the last)"
        f"  context [{found['ctx_start']}, {found['origin']})"
        f" = {found['n_ctx']} rows  c_min {found['c_min']}",
        f"past-only covariates {len(found['covariates']) - len(found['paths'])}"
        f"  declared paths {len(found['paths'])}",
        f"command: {' '.join(found['command'])}",
        "",
        "declared future:",
        render_table(_path_rows(found), _PATH_TABLE),
        "",
        render_table(_forecast_rows(found), _FORECAST_TABLE),
        "",
        *_calibration_lines(found),
        "",
        "warnings:",
        *[f"  {line}" for line in found["warnings"]],
        "assumptions:",
        *[f"  {line}" for line in found["assumptions"]],
        "",
        found["sentence"],
        "",
        *licences([found]),
    ]
    # ADR-014, before anything is printed and before the caller stores the run.
    return refuse_words("\n".join(lines))


def _path_rows(found: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "column": name,
            "unit": found["units"][name],
            "declarable": name in found["declarable"],
            "path": ", ".join(_number(value) for value in path),
        }
        for name, path in sorted(found["paths"].items())
    ]


def _forecast_rows(found: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name in sorted(found["forecasts"]):
        one = found["forecasts"][name]
        band = one["quantiles"]
        for step, value in enumerate(one["point"], start=1):
            cells = None if band is None else band[step - 1]
            rows.append({
                "forecaster": name,
                "step": step,
                "point": _round(value),
                "q10": None if cells is None else _round(cells[0]),
                "q50": None if cells is None else _round(cells[POINT_INDEX]),
                "q90": None if cells is None else _round(cells[-1]),
                "read_future": one["read_future"],
            })  # fmt: skip
    return rows


def _calibration_lines(found: Mapping[str, Any]) -> list[str]:
    """The quoted calibration, or the sentence saying there is none to quote."""
    walls = "  ".join(
        f"{name} {found['forecasts'][name]['wall_ms']} ms"
        for name in sorted(found["forecasts"])
    )
    quoted = found["calibration"]
    if quoted is None:
        return [f"wall: {walls}", NO_CALIBRATION]
    entries = "  ".join(
        f"{name} coverage80 {_round(entry['coverage80'])}"
        f" max_dev {_round(entry['cal_max_dev'])}"
        for name, entry in sorted(quoted["forecasters"].items())
    )
    return [
        f"wall: {walls}",
        f"calibration quoted from forecast_run {quoted['forecast_run_id']}"
        f" ({quoted['created_at']}), recomputed nowhere: {entries}",
    ]


def compare(a: Mapping[str, Any], b: Mapping[str, Any]) -> str:
    """Two scenarios' point paths side by side, with the difference per step.

    Both arguments are forecast_runs rows: `record` above builds one and the store
    reads one back in the same shape, so the fresh run and the stored one meet here
    without a second decoder.

    The difference is between two FORECASTS made under two suppositions, and it is the
    measured version of NOT_A_READER: a forecaster whose two paths are identical while
    the declared paths differ did not read the declaration, whatever it reported.
    """
    _comparable(a, b)
    horizon = int(a["horizon"])
    left, right = _points(a), _points(b)
    shared = sorted(set(left) & set(right))
    rows = [
        {
            "forecaster": name,
            "step": step + 1,
            "a": _round(left[name][step]),
            "b": _round(right[name][step]),
            "difference": _round(right[name][step] - left[name][step]),
        }
        for name in shared
        for step in range(horizon)
    ]
    lines = [
        f"forecast scenario compare  series {a['series_id']}"
        f"  target {a['target']}  horizon {horizon}",
        f"a = {_label(a)}",
        f"b = {_label(b)}",
        "",
        *_path_diff(a, b),
        "",
        render_table(rows, _COMPARE_TABLE),
        "",
        *_flat(shared, left, right),
        SCENARIO_SENTENCE,
    ]
    return refuse_words("\n".join(lines))


def _comparable(a: Mapping[str, Any], b: Mapping[str, Any]) -> None:
    for field_name in ("series_id", "target", "horizon"):
        if a[field_name] != b[field_name]:
            raise NotComparable(
                f"{field_name} differs: {a[field_name]!r} against {b[field_name]!r}."
                " A per-step difference between two forecasts of different things is"
                " not a number."
            )


def _label(row: Mapping[str, Any]) -> str:
    name = row["scenario"]["name"]
    stored = row.get("forecast_run_id")
    return f"{name} ({stored})" if stored else f"{name} (not stored yet)"


def _points(row: Mapping[str, Any]) -> dict[str, list[float]]:
    forecasts = row["windows"]["retained"][0]["forecasts"]
    return {name: [float(v) for v in one["point"]] for name, one in forecasts.items()}


def _path_diff(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    """What the two declared differently, including a column only one of them has."""
    left, right = a["scenario"]["paths"], b["scenario"]["paths"]
    lines = ["declared differences:"]
    for name in sorted(set(left) | set(right)):
        one, other = left.get(name), right.get(name)
        if one == other:
            continue
        lines.append(f"  {name}: a {_path(one)}  b {_path(other)}")
    return lines if len(lines) > 1 else [*lines, "  none: the two declare the same"]


def _path(path: Sequence[float] | None) -> str:
    return "not declared" if path is None else ", ".join(_number(v) for v in path)


def _flat(
    shared: Sequence[str],
    left: Mapping[str, list[float]],
    right: Mapping[str, list[float]],
) -> list[str]:
    """The forecasters whose two forecasts are identical, read off the numbers."""
    same = [name for name in shared if left[name] == right[name]]
    if not same:
        return []
    return [
        f"identical under both declarations: {', '.join(same)}. Read off the numbers"
        " rather than off what a forecaster reported, this is the measurement that"
        " those forecasters did not read the declared paths.",
        "",
    ]


def _number(value: float) -> str:
    """A declared value as it was declared: 2 rather than 2.0 where it is whole."""
    return str(int(value)) if float(value).is_integer() else str(value)


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
