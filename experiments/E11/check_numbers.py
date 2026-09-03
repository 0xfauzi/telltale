"""Every number in docs/experiments/E11.md, against the JSON under experiments/E11/out/.

The check E09 and E10 run, in the same shape and with the same limits: it reads every
number out of the prose and asks whether any number anywhere in `out/` matches it at the
precision it was written to. A number that matches nothing is printed with its line, and
nothing here decides which of those are defects. Some are not measurements at all:
design and spec section numbers, dates, the pre-registered constants where they are
named rather than measured, and quoted strings from other documents. It prints the list
and a reader decides.

Two things this experiment needs that E10's version did not.

  `out/control/control.json` is 2.5 MB of scored windows and it is part of the haystack,
  because the control's numbers are quoted in the write-up and a quoted control number
  nobody checked is the easiest number in the file to get wrong.

  The sensitivity ladder (0, 0, 13, 24) and the arithmetic sentences around it are
  computed by the VERIFY command in docs/log/W5-E11.md rather than written to `out/`, so
  `_derived` recomputes them here from the same series the run compiled. A ladder that
  was only ever typed is a ladder nobody checked.

    uv run python experiments/E11/check_numbers.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

E11 = Path(__file__).resolve().parent
OUT = E11 / "out"
DOC = E11.parents[1] / "docs" / "experiments" / "E11.md"
# A comma is a thousands separator only in front of exactly three digits. E08's regex
# read a stats table's value list as one 15-digit number and reported all of them
# unmatched; this is E10's fix, carried forward unchanged.
NUMBER = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")

# The write-up pastes refusal strings and command output. Reading a fenced block back as
# prose would make the file describe itself, so fenced blocks are skipped: a record of a
# run is not a claim about the run. Everything outside a fence is checked.
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
    paths = sorted(OUT.rglob("*.json"))
    read[str(OUT)] = len(paths)
    for path in paths:
        harvest(json.loads(path.read_text(encoding="utf-8")), found)
    found |= _rounded(found)
    found |= _derived()
    return found


def _rounded(pool: set[float]) -> set[float]:
    """Every number in the pool at 4 decimal places, which is how the tables print it.

    The stored metrics carry full precision (0.08181818181818185) and the write-up
    quotes the rounded form (0.0818) beside numbers it quotes in full. Both readings
    have to count, or every rounded table cell falls to the unmatched list.
    """
    return {round(one, places) for one in pool for places in (0, 1, 2, 3, 4)}


def _derived() -> set[float]:
    """What the write-up computes rather than reads: the ladder and the wall split.

    Recomputed from `out/` and from the same series the run compiled, so a typo in the
    prose still fails the check. The ladder is the count of origins that survive as
    each measured hole is repaired in memory; it is the VERIFY command of
    docs/log/W5-E11.md, and it lives here so nothing in the write-up rests on a number
    that was only ever typed.
    """
    found: set[float] = set()
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    series = summary["series"]
    rows = series["n_rows"]
    c_min = summary["constants"]["c_min_change_clock"]
    horizon = summary["constants"]["horizon"]
    k_min = summary["constants"]["k_min"]
    # The two rungs above "as measured": the origins one regime admits, and the origins
    # the whole series admits with no changepoint at all.
    found.add(float(max(one["origins_at_h1"] for one in series["regimes"])))
    found.add(float(rows - c_min - horizon + 1))
    # The rows one regime needs before k_min origins exist, and how many are missing.
    needed = k_min + c_min + horizon - 1
    found.add(float(needed))
    for one in series["regimes"]:
        found.add(float(needed - one["rows"]))
    # The wall of everything that is not the control.
    found.add(round(summary["wall_ms"] - summary["control"]["wall_ms"], 1))
    return found


def one_matches(written: str, pool: set[float]) -> bool:
    places = len(written.split(".")[1]) if "." in written else 0
    target = float(written)
    return any(round(one, places) == target for one in pool)


def matches(written: str, pool: set[float]) -> bool:
    """As one grouped number, or as the list of small numbers it may also be.

    E10's rule, carried forward: "121,171,177" is a three-value list and also a legal
    thousands-grouped integer, and nothing in the text says which. Both readings are
    tried and either one counts.
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
    fenced = False
    for index, line in enumerate(lines, start=1):
        if line.strip().startswith(FENCE):
            fenced = not fenced
            continue
        if fenced:
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
        print(f"  line {index:4d}  {written:>18s}  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
