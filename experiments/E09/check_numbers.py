"""Every number in docs/experiments/E09.md, against the JSON under experiments/E09/out/.

The same check E08 runs, with the same limits: it reads every number out of the prose
and asks whether any number anywhere in `out/` matches it at the precision it was
written to. A number that matches nothing is printed with its line. Some of those are
not measurements at all (design section numbers, the pre-registered constants 2.8 and
0.25, dates, key sizes typed as prose), and nothing here decides which is which. It
prints the list and a reader decides.

    uv run python experiments/E09/check_numbers.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

E09 = Path(__file__).resolve().parent
OUT = E09 / "out"
DOC = E09.parents[1] / "docs" / "experiments" / "E09.md"
# A comma is a thousands separator only in front of exactly three digits. E08's
# regex was `-?\d[\d,]*`, which reads the stats tables' value lists
# ("52713,54623,54623") as one 15-digit number and reports all of them unmatched.
NUMBER = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    return [match.group(0) for match in NUMBER.finditer(text)]


def harvest(value: object, found: set[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        found.add(float(value))
    elif isinstance(value, dict):
        for item in value.values():
            harvest(item, found)
    elif isinstance(value, list):
        for item in value:
            harvest(item, found)
    elif isinstance(value, str):
        for token in numbers_in(value):
            found.add(float(token.replace(",", "")))


def haystack() -> set[float]:
    found: set[float] = set()
    for path in sorted(OUT.rglob("*.json")):
        harvest(json.loads(path.read_text(encoding="utf-8")), found)
    # The write-up quotes derived totals and the two derived ratios that the runner does
    # not itself write down. Each is computed here from the same files rather than
    # trusted from the prose, so a typo in the write-up still fails the check.
    tokens, wall, cost = [], [], []
    for tag in ("E09-pilot", "E09"):
        report = json.loads((OUT / tag / "probe.json").read_text(encoding="utf-8"))
        for block in report["probes"]:
            for run in block["repetitions"]:
                tokens.append(run["vector"]["stable_state_work.stable_state_tokens"])
                wall.append(run["wall_ms"])
    usage: dict[str, list[float]] = {}
    for one in json.loads(
        (OUT / "store_facts.json").read_text(encoding="utf-8")
    ).values():
        cost.append(one["total_cost_usd"])
        for name, value in one["usage"].items():
            usage.setdefault(name, []).append(value)
    found |= {float(sum(tokens)), float(sum(wall)), round(sum(cost), 6)}
    found |= {round(sum(wall) / 1000, 1), float(len(tokens))}
    found |= {float(sum(values)) for values in usage.values()}
    found |= {round(0.25 * one, 3) for one in found if isinstance(one, float)}
    return found


def one_matches(written: str, pool: set[float]) -> bool:
    places = len(written.split(".")[1]) if "." in written else 0
    target = float(written)
    return any(round(one, places) == target for one in pool)


def matches(written: str, pool: set[float]) -> bool:
    """As one grouped number, or as the list of small numbers it may also be.

    "121,171,177" is a stats table's three output-token values and also a legal
    thousands-grouped 121171177, and nothing in the text says which. Both readings are
    tried and either one counts, which is the only way a row of three-digit values and a
    total in the millions can be checked by one function.
    """
    if one_matches(written.replace(",", ""), pool):
        return True
    parts = written.split(",")
    return len(parts) > 1 and all(one_matches(part, pool) for part in parts)


def main() -> int:
    pool = haystack()
    lines = DOC.read_text(encoding="utf-8").splitlines()
    unmatched: list[tuple[int, str, str]] = []
    total = 0
    for index, line in enumerate(lines, start=1):
        for written in numbers_in(line):
            total += 1
            if not matches(written, pool):
                unmatched.append((index, written, line.strip()[:90]))
    print(f"{DOC}: {total} numbers, {len(pool)} distinct numbers in {OUT}")
    print(f"matched {total - len(unmatched)}, unmatched {len(unmatched)}")
    for index, written, line in unmatched:
        print(f"  line {index:4d}  {written:>14s}  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
