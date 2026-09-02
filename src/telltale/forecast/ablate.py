"""The A/B/C ablation of design 6.12 (H7), on the change clock.

The question is narrow and worth stating before any code: does knowing what a session
DID while producing a change help forecast that change, over and above knowing what the
change IS? A is the change itself, six columns anybody could read off a diff. B adds
what the session spent. C adds how the session behaved. The blocks are nested, so the
only thing that differs between two variants is the columns C or B adds, and the verdict
is about those columns rather than about two unrelated models.

Four rules make the three runs comparable, each enforced here rather than assumed.

  Identical origins and actuals. The three runs are planned separately and a covariate
  with a hole drops windows in one variant and not another, so the three are cut down to
  the origins all three retained and every score is recomputed on that set. The number
  dropped for alignment is reported; a comparison over three different window sets is
  three results rather than one ablation.

  The target is excluded from its own covariates. `attempts_to_land` is a column of
  block B and a target of the ablation, and a variant that fed it to itself would be
  scoring a lookup rather than a forecast.

  All covariates are past-only, which is what the backtester's Window is: a copy of
  rows [ctx_start, o). The candidate protocol (forecast/candidate.py) is the one place
  a future covariate is legitimate, and it is a different run with a different name.

  At most 15 variates, counting the target. Design 6.12 fixes the number; block C plus a
  target is exactly 15, so a column added to C without a decision fails here.

The verdict is `E_C <= (1 - delta) min(E_A, E_B)` and `W >= w`, else "diagnostic only".
"Diagnostic only" is not a failure: it says the C columns describe the change without
forecasting it, which is a true and useful thing for a column to be.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telltale.forecast import (
    ABLATION_BLOCKS,
    DELTA,
    MAX_VARIATES,
    W,
    refuse_words,
)
from telltale.forecast import backtest as backtester
from telltale.forecast import placebo as placebos
from telltale.forecast.backtest import Refused, window_mae
from telltale.report import render_table

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from telltale.forecast import Forecaster
    from telltale.model import Series

ADDS = "adds temporal information"
DIAGNOSTIC = "diagnostic only"

# What it means when three nested variants score to the last bit alike. Measured rather
# than assumed: it is read off the numbers, not off a capability a forecaster declares.
NO_COVARIATE_READER = (
    "A, B and C scored identically: no forecaster in this run read a covariate at all,"
    " so the verdict is a property of the forecasters and not of the columns"
)

_TABLE = ("variant", "columns", "variates", "n_windows", "mae_mean", "mae_median")
_RULE = ("test", "lhs", "rhs", "holds")


def run(
    series: Series,
    target: str,
    horizon: int,
    factory: Callable[[], Mapping[str, Forecaster]],
    model: str,
    *,
    c_min: int | None = None,
    blocks: Mapping[str, Sequence[str]] = ABLATION_BLOCKS,
) -> dict[str, Any]:
    """A, B and C over identical origins, the rule's verdict, the winner's placebo.

    `factory` returns a FRESH forecaster per variant. One instance across three runs
    would carry whatever the first run left on it, and the stub that the contract tests
    read keeps every window it was handed.
    """
    spec = backtester.registered(series, target, horizon)
    raw = {
        key: backtester.run(
            series,
            target,
            horizon,
            factory(),
            f"{spec.variant}_{key.lower()}",
            c_min=c_min,
            covariates=block,
        )
        for key, block in blocks.items()
    }
    for key, one in raw.items():
        _cap(key, one)
    aligned, dropped = _aligned(raw)
    verdict = _verdict(aligned, model)
    winner = min(aligned, key=lambda key: _mae(aligned[key], model) or float("inf"))
    return {
        "series_id": series.series_id,
        "target": target,
        "unit": spec.unit,
        "horizon": horizon,
        "model": model,
        "blocks": {key: list(block) for key, block in blocks.items()},
        "runs": aligned,
        "aligned_origins": len(next(iter(aligned.values()))["windows"]),
        "dropped_for_alignment": dropped,
        "verdict": verdict,
        "winner": winner,
        "placebo": placebos.paired(
            series,
            target,
            horizon,
            factory(),
            model,
            truth=raw[winner],
            variant=f"{spec.variant}_{winner.lower()}",
            c_min=c_min,
            covariates=blocks[winner],
        ),
        "warnings": _warnings(aligned, model),
    }


def _cap(key: str, one: Mapping[str, Any]) -> None:
    """Design 6.12's 15-variate limit, target included. Refuses, never subsamples."""
    variates = 1 + len(one["covariates"])
    if variates > MAX_VARIATES:
        raise Refused(
            f"variant {key} is {variates} variates against the cap of {MAX_VARIATES}:"
            f" target {one['target']} plus {len(one['covariates'])} covariates"
        )


def _aligned(
    raw: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """The three runs cut down to the origins all three retained, rescored on those."""
    common = set.intersection(
        *[{int(record["origin"]) for record in one["windows"]} for one in raw.values()]
    )
    aligned: dict[str, dict[str, Any]] = {}
    dropped: dict[str, int] = {}
    for key, one in raw.items():
        kept = [record for record in one["windows"] if int(record["origin"]) in common]
        dropped[key] = len(one["windows"]) - len(kept)
        aligned[key] = {
            **one,
            "windows": kept,
            "metrics": backtester.metrics(kept, one["tau"]),
        }
    return aligned, dropped


def _verdict(aligned: Mapping[str, Mapping[str, Any]], model: str) -> dict[str, Any]:
    """`E_C <= (1 - delta) min(E_A, E_B)` and `W >= w`, with both sides of each."""
    e_a, e_b, e_c = (_mae(aligned[key], model) for key in ("A", "B", "C"))
    if e_a is None or e_b is None or e_c is None:
        return {
            "label": DIAGNOSTIC,
            "reason": f"{model} did not score in every variant",
            "tests": [],
        }
    against = "A" if e_a <= e_b else "B"
    floor = (1.0 - DELTA) * min(e_a, e_b)
    beaten = [
        window_mae(c_record, model) < window_mae(other, model)
        for c_record, other in zip(
            aligned["C"]["windows"], aligned[against]["windows"], strict=True
        )
    ]
    share = sum(beaten) / len(beaten) if beaten else None
    tests: list[dict[str, Any]] = [
        {
            "test": "E_C <= (1 - delta) min(E_A, E_B)",
            "lhs": e_c,
            "rhs": floor,
            "holds": e_c <= floor,
        },
        {
            "test": "W >= w",
            "lhs": share,
            "rhs": W,
            "holds": share is not None and share >= W,
        },
    ]
    return {
        "label": ADDS if all(item["holds"] for item in tests) else DIAGNOSTIC,
        "reason": None,
        "E_A": e_a,
        "E_B": e_b,
        "E_C": e_c,
        "against": against,
        "W": share,
        "tests": tests,
    }


def _warnings(aligned: Mapping[str, Mapping[str, Any]], model: str) -> list[str]:
    scores = [_mae(aligned[key], model) for key in ("A", "B", "C")]
    return [NO_COVARIATE_READER] if len(set(scores)) == 1 else []


def _mae(one: Mapping[str, Any], model: str) -> float | None:
    scored = one["metrics"]["forecasters"].get(model)
    return None if scored is None else scored["mae_mean"]


def report(found: Mapping[str, Any]) -> str:
    """The three variants, the rule with both sides, the winner's placebo under it."""
    verdict = found["verdict"]
    lines = [
        f"forecast ablate  series {found['series_id']}  target {found['target']}"
        f" ({found['unit']})  horizon {found['horizon']}  model {found['model']}",
        f"origins common to A, B and C: {found['aligned_origins']}"
        f"  dropped for alignment: {found['dropped_for_alignment']}",
        "",
        render_table(
            [_row(key, found) for key in found["blocks"]],
            _TABLE,
        ),
        "",
        f"verdict: C {verdict['label']}"
        + (f" ({verdict['reason']})" if verdict.get("reason") else ""),
        render_table(
            [
                {
                    "test": item["test"],
                    "lhs": _round(item["lhs"]),
                    "rhs": _round(item["rhs"]),
                    "holds": "yes" if item["holds"] else "no",
                }
                for item in verdict["tests"]
            ]
            or [{"test": "none evaluated"}],
            _RULE,
        ),
        "warnings:",
        *[f"  {line}" for line in found["warnings"] or ["none"]],
        "",
        f"the placebo of the winning variant ({found['winner']}), beside it:",
        "",
        placebos.report(found["placebo"]),
    ]
    return refuse_words("\n".join(lines))


def _row(key: str, found: Mapping[str, Any]) -> dict[str, Any]:
    one = found["runs"][key]
    scored = one["metrics"]["forecasters"].get(found["model"], {})
    return {
        "variant": key,
        "columns": len(found["blocks"][key]),
        "variates": 1 + len(one["covariates"]),
        "n_windows": len(one["windows"]),
        "mae_mean": _round(scored.get("mae_mean")),
        "mae_median": _round(scored.get("mae_median")),
    }


def store_all(store: Any, found: Mapping[str, Any]) -> list[str]:
    """The three variant runs, then the winner's placebo pair."""
    variants = [
        backtester.persist(store, found["runs"][key]) for key in found["blocks"]
    ]
    return [*variants, *placebos.store_all(store, found["placebo"])]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
