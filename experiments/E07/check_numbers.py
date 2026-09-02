"""Every number in docs/experiments/E07.md, against the JSON under experiments/E07/out/.

The write-up quotes about four hundred numbers and each one was typed or pasted by
hand. This reads them all back out of the prose and asks, of each, whether any number in
`out/` matches it at the precision it was written to. A number that matches nothing is
printed with its line, and the write-up says why: some are constants (delta, k_min),
some are dates, some are section numbers of the design, and some are E03's measurements
cited as E03's. Nothing here decides which is which. It prints the list, and a reader
decides.

    uv run python experiments/E07/check_numbers.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

E07_DIR = Path(__file__).resolve().parent
OUT = E07_DIR / "out"
DOC = E07_DIR.parents[1] / "docs" / "experiments" / "E07.md"

# 1,234.5 and -0.0225 and 0.9759. Thousands separators are stripped before comparison,
# because the tables print 14,805 and the JSON holds 14805.
NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    return [match.group(0) for match in NUMBER.finditer(text)]


def harvest(value: object, found: set[float]) -> None:
    """Every float and int anywhere in a decoded JSON document."""
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
        if "home" in path.parts:
            continue
        harvest(json.loads(path.read_text(encoding="utf-8")), found)
    return found


def matches(written: str, pool: set[float]) -> bool:
    """True when some measured number rounds to the number as it was written."""
    plain = written.replace(",", "")
    places = len(plain.split(".")[1]) if "." in plain else 0
    target = float(plain)
    return any(round(one, places) == target for one in pool)


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
