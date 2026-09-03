"""Every number in docs/experiments/E10.md, against the JSON under experiments/E10/out/.

The same check E09 runs, in the same shape and with the same limits: it reads every
number out of the prose and asks whether any number anywhere in `out/` matches it at the
precision it was written to. A number that matches nothing is printed with its line.
Some of those are not measurements at all (design section numbers, the pre-registered
constants 2.8 and 0.25, dates, commit-sha digits, key sizes typed as prose, and E09's
numbers, which live under experiments/E09/out/ and are quoted here for comparison), and
nothing here decides which is which. It prints the list and a reader decides.

E09's `out/` is added to the haystack when it is present, rather than left to the
unmatched list: this write-up compares two experiments, and a quoted E09 number that
nobody checked would be the easiest number in the file to get wrong.
`experiments/*/out/` is gitignored, so in a checkout that did not run E09 that
directory is empty and the header below says so. Every E09 number then falls to the
unmatched list, which is the honest report of a haystack that does not hold them.

    uv run python experiments/E10/check_numbers.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

E10 = Path(__file__).resolve().parent
OUT = E10 / "out"
E09_OUT = E10.parent / "E09" / "out"
DOC = E10.parents[1] / "docs" / "experiments" / "E10.md"
# A comma is a thousands separator only in front of exactly three digits. E08's regex
# was `-?\d[\d,]*`, which reads the stats tables' value lists ("52713,54623,54623") as
# one 15-digit number and reports all of them unmatched.
NUMBER = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")

ARMS = ("E10-before", "E10-after")

# The write-up pastes this script's own output, and the counts in it are counts of the
# prose. Reading them back as prose would make the file describe itself: the totals
# would move every time the block was refreshed, and no fixed point is reachable. So
# the scan skips from the command line below to the fence that closes it. A record of a
# run is not a claim about the run, and nothing else in the file is exempt.
ECHOED = "$ uv run python experiments/E10/check_numbers.py"
FENCE = "```"


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


def haystack(read: dict[str, int]) -> set[float]:
    found: set[float] = set()
    for root in (OUT, E09_OUT):
        paths = sorted(root.rglob("*.json"))
        read[str(root)] = len(paths)
        for path in paths:
            harvest(json.loads(path.read_text(encoding="utf-8")), found)
    found |= _derived()
    # The demotion quarter of design 6.12, which the write-up quotes for a measure whose
    # MDD the report prints and whose 0.25 median it does not.
    found |= {round(0.25 * one, 3) for one in set(found)}
    return found


def _derived() -> set[float]:
    """The per-arm and whole-run totals the runner does not itself write down.

    Computed here from the same files rather than trusted from the prose, so a typo in
    the write-up still fails the check. `session_tokens` is the sum `telltale show`
    reports for one capture (fresh_input, cache_read, cache_creation, output), which is
    the number the stop bound is compared with, and it is on every repetition.
    """
    found: set[float] = set()
    grand: list[list[int]] = [[], []]
    for tag in ARMS:
        report = json.loads((OUT / tag / "probe.json").read_text(encoding="utf-8"))
        tokens: list[int] = []
        wall: list[int] = []
        for block in report["probes"]:
            per_probe = [run["session_tokens"] for run in block["repetitions"]]
            per_wall = [run["wall_ms"] for run in block["repetitions"]]
            found |= {float(sum(per_probe)), float(sum(per_wall))}
            found.add(round(sum(per_wall) / 1000, 1))
            tokens += per_probe
            wall += per_wall
        found |= {float(sum(tokens)), float(sum(wall)), float(len(tokens))}
        found.add(round(sum(wall) / 1000, 1))
        grand[0] += tokens
        grand[1] += wall
    found |= {float(sum(grand[0])), float(sum(grand[1])), float(len(grand[0]))}
    found.add(round(sum(grand[1]) / 1000, 1))
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
    read: dict[str, int] = {}
    pool = haystack(read)
    lines = DOC.read_text(encoding="utf-8").splitlines()
    unmatched: list[tuple[int, str, str]] = []
    total = 0
    echoing = False
    for index, line in enumerate(lines, start=1):
        if line.strip() == ECHOED:
            echoing = True
        elif echoing and line.strip() == FENCE:
            echoing = False
        if echoing:
            continue
        for written in numbers_in(line):
            total += 1
            if not matches(written, pool):
                unmatched.append((index, written, line.strip()[:90]))
    print(f"{DOC}: {total} numbers, {len(pool)} distinct numbers")
    for root, count in read.items():
        print(f"  read {count} json file(s) under {root}")
    print(f"matched {total - len(unmatched)}, unmatched {len(unmatched)}")
    for index, written, line in unmatched:
        print(f"  line {index:4d}  {written:>14s}  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
