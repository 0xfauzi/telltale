"""E08b: E08's 38 rows re-labelled under the W3-E08b amendment to design 6.12.

No model runs. E08 stored, per pair, every term the decision rule reads (E_M, E_B,
E_P, W_MB, W_MP, n_windows, k_min, delta, w, the per-half values, the placebo block and
the validity check), so an amendment to the RULE can be applied to those terms without
touching a forecaster. That is the whole design of this experiment: if any number here
were recomputed, the difference between E08 and E08b would no longer be the rule alone.

The rule is not restated here. `telltale.forecast.decide.relabel` is the same `_label`
the CLI reaches through `forecast placebo`, and this file hands it E08's stored terms
and E08's stored validity verdict and writes down what comes back. A re-implementation
of the four labels in an experiment script would be a second rule, and the point of the
amendment was that there should be one.

ORDER. The amendment was written into docs/design/01-design.md and into
src/telltale/forecast/decide.py and committed BEFORE this file existed. The commit that
carries the rule is recorded in out/summary.json as `rule_commit`, read from git rather
than typed, beside the HEAD this run was made at.

    uv run python experiments/E08b/run.py

It needs no extra and no store: decide.py is standard library only.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from telltale.forecast import decide as decider

E08B_DIR = Path(__file__).resolve().parent
REPO_ROOT = E08B_DIR.parents[1]
E08_OUT = REPO_ROOT / "experiments" / "E08" / "out"
OUT = E08B_DIR / "out"
RULE_FILE = "src/telltale/forecast/decide.py"

# What `relabel` reads off a stored decision. A row missing one of these is labelled
# "not assessable: term missing" and counted, rather than defaulted into an answer.
TERMS = (
    "model",
    "best_baseline",
    "e_m",
    "e_b",
    "e_p",
    "w_mb",
    "w_mp",
    "n_windows",
    "k_min",
    "delta",
    "w",
    "covariate_free",
    "halves",
    "placebo",
    "baselines",
)
TERM_MISSING = "not assessable: term missing"


def pair_files() -> list[Path]:
    """E08's 38 per-pair files: 36 per-capture pairs and the 2 pooled rows."""
    return sorted(E08_OUT.glob("cap_*/*.json")) + sorted(
        (E08_OUT / "pooled").glob("*.json")
    )


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def row(path: Path) -> dict[str, Any]:
    """One E08 pair, re-labelled. Every term is read from the file, none computed."""
    record = json.loads(path.read_text(encoding="utf-8"))
    terms, check = record["decision"], record["sentinel"]
    missing = [name for name in TERMS if name not in terms]
    common = {
        "capture_id": record["capture"]["capture_id"],
        "task_id": record["capture"].get("task_id"),
        "target": record["target"],
        "horizon": record["horizon"],
        "n_windows": terms.get("n_windows"),
        "placebo_valid": check["valid"],
        "placebo_runs_worse": [check["n_worse"], check["n_runs"]],
        "label_e08": terms["label"],
        "source": str(path.relative_to(REPO_ROOT)),
    }
    if missing:
        return {**common, "label": TERM_MISSING, "missing_terms": missing}
    found = decider.relabel(terms, placebo_valid=check["valid"])
    return {**common, "missing_terms": [], **_decided(found)}


def _decided(found: decider.Decision) -> dict[str, Any]:
    """The label and everything a reader needs to redo the comparison by hand."""
    return {
        "label": found.label,
        "reason": found.reason,
        "notes": found.notes,
        "model": found.model,
        "best_baseline": found.best_baseline,
        "e_m": found.e_m,
        "e_b": found.e_b,
        "e_p": found.e_p,
        "w_mb": found.w_mb,
        "w_mp": found.w_mp,
        "inequalities": found.inequalities,
        "halves": found.halves,
    }


def counts(values: list[Any]) -> dict[str, int]:
    """One count per distinct value. A label nobody took is absent rather than 0."""
    return {str(one): values.count(one) for one in sorted({str(v) for v in values})}


def main() -> int:
    files = pair_files()
    if not files:
        raise SystemExit(f"{E08_OUT}: no E08 pair files. Nothing to re-label.")
    rows = [row(path) for path in files]
    payload = {
        "experiment": "E08b",
        "question": "what are E08's 38 rows labelled under the W3-E08b amendment",
        "read_from": str(E08_OUT.relative_to(REPO_ROOT)),
        "rule": RULE_FILE,
        "rule_commit": git("log", "-1", "--format=%H", "--", RULE_FILE),
        "head": git("rev-parse", "HEAD"),
        "n_rows": len(rows),
        "n_pooled": sum(1 for one in rows if one["capture_id"] == "pooled"),
        "n_placebo_invalid": sum(1 for one in rows if not one["placebo_valid"]),
        "n_rows_missing_a_term": sum(1 for one in rows if one["missing_terms"]),
        "labels": counts([one["label"] for one in rows]),
        "labels_e08": counts([one["label_e08"] for one in rows]),
        "rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    print(json.dumps(
        {key: value for key, value in payload.items() if key != "rows"},
        indent=2, sort_keys=True,
    ))  # fmt: skip
    print(f"\nwrote {(OUT / 'summary.json').relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
