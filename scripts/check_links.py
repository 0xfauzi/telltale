#!/usr/bin/env python3
"""Every relative Markdown link resolves, and every file under docs/ is
reachable from docs/README.md. A page nobody can find from the index does
not exist for the reader who needed it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
LINK = re.compile(r"\[[^\]]*\]\(([^)\s][^)]*)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+)$")
# Listed by pattern with a count in docs/README.md, not linked file by file.
EXEMPT: dict[str, Callable[[Path], bool]] = {
    "spec/media": lambda _p: True,
    "assets": lambda p: p.suffix == ".png",
    "log": lambda p: p.suffix == ".md" and p.name != "README.md",
}


def slug(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"\s+", "-", text.strip())


def headings(path: Path) -> set[str]:
    matches = (HEADING.match(line) for line in path.read_text().splitlines())
    return {slug(m.group(1)) for m in matches if m}


def check_link(
    path: Path, lineno: int, target: str, failures: list[str]
) -> Path | None:
    if re.match(r"^\w+://|^mailto:", target):
        return None
    file_part, _, anchor = target.partition("#")
    dest = (path.parent / file_part).resolve() if file_part else path
    if file_part and not dest.exists():
        failures.append(f"{path}:{lineno}: {target}")
        return None
    if anchor and dest.suffix == ".md" and slug(anchor) not in headings(dest):
        failures.append(f"{path}:{lineno}: {target}")
        return None
    return dest if file_part else None


def scan(path: Path, failures: list[str]) -> set[Path]:
    linked: set[Path] = set()
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        # A link syntax shown as a literal example, wholly inside one code
        # span (as this repo's own briefs do), is prose, not a link.
        line = re.sub(r"`[^`]*`", "", line)
        for target in LINK.findall(line):
            dest = check_link(path, lineno, target, failures)
            if dest is not None:
                linked.add(dest)
    return linked


def check_index_counts(index_text: str, failures: list[str]) -> None:
    for rel, is_exempt in EXEMPT.items():
        matches = [p for p in (DOCS / rel).iterdir() if p.is_file() and is_exempt(p)]
        count = str(len(matches))
        if not re.search(rf"\b{count}\b", index_text):
            failures.append(f"docs/README.md:0: {rel} count ({count}) is not stated")


def check_index_completeness(index_links: set[Path], failures: list[str]) -> None:
    for path in sorted(DOCS.rglob("*")):
        if not path.is_file() or path == DOCS / "README.md":
            continue
        rel = path.relative_to(DOCS)
        exempt = (str(rel).startswith(pat) and chk(path) for pat, chk in EXEMPT.items())
        if any(exempt):
            continue
        if path.resolve() not in index_links:
            failures.append(f"docs/README.md:0: docs/{rel} is not indexed")


def main() -> int:
    failures: list[str] = []
    files = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md"]
    files += sorted(DOCS.rglob("*.md"))
    files += sorted((ROOT / "briefs").glob("*.md"))
    index_links: set[Path] = set()
    for f in files:
        linked = scan(f, failures)
        if f == DOCS / "README.md":
            index_links = linked

    check_index_counts((DOCS / "README.md").read_text(), failures)
    check_index_completeness(index_links, failures)

    sys.stdout.writelines(f"{line}\n" for line in failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
