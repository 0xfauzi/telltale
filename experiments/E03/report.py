"""Tables for experiments/E03/run.py. Reads results.json; measures nothing.

Kept beside the runner rather than inside it so that what is printed cannot quietly
become what is recorded: every value here is read from the dict run.py wrote to
experiments/E03/out/results.json, and this module never calls the model.

Usage:
    uv run --extra forecast python experiments/E03/report.py            # results.json
    uv run --extra forecast python experiments/E03/report.py PATH.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent / "out"


def print_table(title: str, rows: list[list[str]], headers: list[str]) -> None:
    print()
    print(title)
    widths = [
        max([len(headers[i]), *(len(r[i]) for r in rows)]) for i in range(len(headers))
    ]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def matrix(results: dict[str, Any]) -> None:
    keys = ("kind", "length", "horizon", "variates")
    rows = [
        [
            *(str(c[k]) for k in keys),
            *(f"{c[k]:.4f}" for k in ("median_s", "min_s", "max_s")),
            str(c["execution_position"]),
            "x".join(str(d) for d in c["quantiles_shape"]),
            str(c["quantile_order"]["steps_disordered"]),
        ]
        for c in results["matrix"]
    ]
    print_table(
        f"predict_batch wall time, seconds, n={results['reps']} per cell, device cpu"
        f" (order: {'canonical' if results['canonical_order'] else 'shuffled'})",
        rows,
        ["series", "len", "H", "var", "median", "min", "max", "pos", "q shape", "bad"],
    )


def drift(results: dict[str, Any]) -> None:
    """The same cell before and after the matrix, plus the discarded warmup calls."""
    before = results["control_before"]
    after = results["control_after"]
    walls = results["warmup_walls_s"] or [float("nan")]
    by_position = sorted(results["matrix"], key=lambda c: c["execution_position"])
    half = len(by_position) // 2
    first = [c["median_s"] for c in by_position[:half]]
    last = [c["median_s"] for c in by_position[half:]]
    rows = [
        ["warmup calls", str(len(results["warmup_walls_s"]))],
        ["warmup first / last", f"{walls[0]:.4f} / {walls[-1]:.4f}"],
        ["control before matrix, median", f"{before['median_s']:.4f}"],
        ["control after matrix, median", f"{after['median_s']:.4f}"],
        ["control after / before", f"{after['median_s'] / before['median_s']:.3f}"],
        ["first half of matrix, mean", f"{sum(first) / len(first):.4f}"],
        ["second half of matrix, mean", f"{sum(last) / len(last):.4f}"],
    ]
    title = "drift control: random_walk length 64 horizon 24 univariate, seconds"
    print_table(title, rows, ["measure", "value"])


def _probe_summary(record: dict[str, Any]) -> str:
    if record["raised"]:
        return f"{record['exception_type']}: {record['exception']}"
    if "max_abs_diff" in record:
        return f"max abs diff {record['max_abs_diff']:.3e}"
    shape = record.get("quantiles_shape") or []
    return f"quantiles {'x'.join(str(d) for d in shape)} in {record['wall_s']}s"


def probes(results: dict[str, Any]) -> None:
    records = [*results["variate_cap"], *results["padding"], results["short_context"]]
    rows = [
        [r["label"], "raised" if r["raised"] else "ok", _probe_summary(r)]
        for r in records
    ]
    print_table("probes", rows, ["probe", "result", "detail"])


def nan_probe(results: dict[str, Any]) -> None:
    rows = []
    for key, value in results["nan_probe"].items():
        if not isinstance(value, dict):
            continue
        if value.get("same_shape") is False:
            detail = f"shape {value['shape_a']} vs {value['shape_b']}"
        else:
            detail = f"max abs diff {value['max_abs_diff']:.3e}"
        rows.append([key, detail])
    headers = ["comparison", "measurement"]
    print_table("NaN probe, horizon 24, context 64", rows, headers)


def device(results: dict[str, Any]) -> None:
    run = results["device"]["mps_run"]
    if run["raised"]:
        rows = [["mps", f"{run['exception_type']}: {run['exception']}"]]
    else:
        f = run["forecast"]
        rows = [
            ["cpu median s", f"{run['cpu']['median_s']:.4f}"],
            ["mps median s", f"{run['mps']['median_s']:.4f}"],
            ["cpu/mps", f"{run['speed_factor_cpu_over_mps']:.3f}"],
            ["forecast max abs diff", f"{f['max_abs_diff']:.3e}"],
            ["forecast max relative diff", f"{f['max_relative_diff_per_point']:.3e}"],
            ["forecast max diff / scale", f"{f['max_relative_diff_to_scale']:.3e}"],
        ]
    print_table("cpu against mps, context 64, horizon 64", rows, ["measure", "value"])


def decisions(results: dict[str, Any]) -> None:
    rows = [[k, v["branch"], v["evidence"]] for k, v in results["decisions"].items()]
    print_table("decision rules", rows, ["rule", "branch taken", "number behind it"])


def summary(results: dict[str, Any]) -> None:
    checkpoint = results["checkpoint"]
    disordered = sum(r["steps_disordered"] for r in results["raw_quantiles"])
    steps = sum(r["steps"] for r in results["raw_quantiles"])
    print()
    print(
        f"raw quantile head (sort_quantiles=False): {disordered} of {steps} "
        "forecast steps are not non-decreasing across the 9 quantiles"
    )
    print(
        f"checkpoint {checkpoint['bytes_on_disk']} bytes on disk, "
        f"first construct {checkpoint['first_construct_s']} s, "
        f"warm construct {checkpoint['warm_construct_s']} s"
    )
    print(
        f"peak rss {results['peak_rss_bytes']} "
        f"{results['meta']['ru_maxrss_unit']}, license {checkpoint['license']}"
    )


def render(results: dict[str, Any]) -> None:
    matrix(results)
    drift(results)
    nan_probe(results)
    probes(results)
    device(results)
    decisions(results)
    summary(results)


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "results.json"
    render(json.loads(path.read_text()))
    print(f"read: {path}")
