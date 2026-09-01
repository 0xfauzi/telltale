"""Turn the raw E01 capture into committed fixtures: identity removed, the rest kept.

What this does NOT do is as important as what it does. It removes exactly two classes of
thing: the machine (the home directory and the scenario's repository path, in both their
slash form and the dashed form Claude Code uses for project directory names, plus the
account name on its own, which `ls -l` prints in the owner column) and the person
(user.id, user.email, user.account_id, user.account_uuid, organization.id). It removes
nothing else. The prompt text, the assistant text, the tool output and the three
TELLTALEFAKE credential probes all stay, because a fixture that has already been cleaned
cannot show what a surface really carries, and every downstream privacy test needs a
payload with a probe in it to prove the probe was removed later.

The identity values are found in the capture rather than declared here: the OTel
attributes name them, so the real values are read off the attributes and then replaced
everywhere they appear, including inside JSON that was itself encoded into a string.
Replacing by value rather than by key is what catches the copy of an id that ended up in
a path or a URL.

Two self-checks refuse to write a fixture that would be wrong in either direction: after
rewriting, no real identity value and no home path may remain, and the number of
TELLTALEFAKE occurrences must be exactly what the raw capture had.

Usage:
    uv run python experiments/E01/sanitize_fixture.py
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import matrix

E01_DIR = Path(__file__).resolve().parent
REPO_ROOT = E01_DIR.parents[1]
OUT_ROOT = E01_DIR / "out"
FIXTURE_ROOT = REPO_ROOT / "fixtures" / "sources" / "claude"

SURFACE_FILES = ("otel_logs", "otel_metrics", "hooks", "stream")

# The OTel attributes that name a person or an account, and the fixed value each is
# replaced with. Fixed rather than random so the same person is the same person across
# scenarios and a correlation test still has something to correlate.
IDENTITY_ATTRS = {
    "user.id": "telltale-fake-user-id",
    "user.email": "user@telltale.invalid",
    "user.account_id": "user_telltalefakeaccount",
    "user.account_uuid": "00000000-0000-4000-8000-000000000001",
    "organization.id": "00000000-0000-4000-8000-000000000002",
}

PROBE = "TELLTALEFAKE"


def scenario_dirs() -> list[Path]:
    ran = (p for p in OUT_ROOT.glob("S*") if p.is_dir() and (p / "meta.json").exists())
    return sorted(ran)


def meta_of(scen: Path) -> dict[str, Any]:
    meta: dict[str, Any] = json.loads((scen / "meta.json").read_text(encoding="utf-8"))
    return meta


def version_dir() -> str:
    """The one Claude Code version every scenario recorded. Two is a mixed capture."""
    versions = {
        str(meta_of(scen)["claude_version"]).split()[0] for scen in scenario_dirs()
    }
    if len(versions) != 1:
        raise RuntimeError(f"scenarios disagree about the version: {sorted(versions)}")
    return versions.pop()


def identity_values() -> dict[str, str]:
    """Real value to fake value, read off the attributes that name each one."""
    found: dict[str, str] = {}
    for scen in scenario_dirs():
        for _name, attrs in matrix.log_records(scen):
            for key, fake in IDENTITY_ATTRS.items():
                value = attrs.get(key)
                if isinstance(value, str) and value:
                    found[value] = fake
    return found


def path_rules(scen: Path) -> dict[str, str]:
    """Machine paths, in both the slash form and the dashed project-directory form."""
    repo = meta_of(scen)["cwd"]
    home = str(Path.home())
    return {
        repo: "<repo>",
        repo.replace("/", "-"): "<repo-slug>",
        home: "<home>",
        home.replace("/", "-"): "<home-slug>",
        # The account name on its own, which is what `ls -l` prints in the owner
        # column. Measured: S4 and S7 captured `ls -la` output, so the name reached
        # hooks.jsonl and stream.jsonl with no path around it for the rules above to
        # match. Applied last because it is a substring of the two path rules.
        Path(home).name: "<user>",
    }


def apply_rules(text: str, rules: dict[str, str], hits: Counter[str]) -> str:
    """Longest target first: a repository path contains the home path inside it."""
    for target in sorted(rules, key=len, reverse=True):
        count = text.count(target)
        if count:
            hits[target] += count
            text = text.replace(target, rules[target])
    return text


def sanitize_file(
    source: Path, dest: Path, rules: dict[str, str]
) -> tuple[int, Counter[str]]:
    raw = source.read_text(encoding="utf-8")
    hits: Counter[str] = Counter()
    clean = apply_rules(raw, rules, hits)
    if raw.count(PROBE) != clean.count(PROBE):
        raise RuntimeError(f"{source}: sanitizing changed the {PROBE} count")
    leaked = [target for target in rules if target in clean]
    if leaked:
        raise RuntimeError(f"{dest}: {len(leaked)} value(s) survived sanitizing")
    dest.write_text(clean, encoding="utf-8")
    return len([line for line in raw.splitlines() if line.strip()]), hits


def event_counts(scen: Path) -> dict[str, Counter[str]]:
    counts = {surface: Counter[str]() for surface in matrix.SURFACES}
    keys = {surface: Counter[str]() for surface in matrix.SURFACES}
    matrix.index_logs(scen, keys["otel_logs"], counts["otel_logs"])
    matrix.index_metrics(scen, keys["otel_metrics"], counts["otel_metrics"])
    matrix.index_flat(matrix.hook_bodies(scen), keys["hooks"], counts["hooks"])
    matrix.index_flat(matrix.stream_messages(scen), keys["stream"], counts["stream"])
    return counts


def manifest_text(
    scen: Path, requests: dict[str, int], hits: Counter[str], rules: dict[str, str]
) -> str:
    meta = meta_of(scen)
    lines = [
        f"# {meta['scenario']}: {meta['question']}",
        "",
        f"Captured by `experiments/E01/run.py --scenario {meta['scenario']}` from "
        f"Claude "
        f"Code {meta['claude_version']} at {meta['started_at_iso']}, sanitized by "
        "`experiments/E01/sanitize_fixture.py`. The run's own numbers (wall time, exit "
        "code, usage, late-export window) are in `experiments/E01/out/"
        f"{meta['scenario']}/meta.json`, which is not committed.",
        "",
        f"Prompt: {meta['prompt']}",
        "",
        "## Requests per surface",
        "",
        "| surface | requests |",
        "|---|---|",
    ]
    for surface in SURFACE_FILES:
        lines.append(f"| {surface} | {requests.get(surface, 0)} |")
    lines += ["", "## Observations by event name", ""]
    lines += ["| surface | name | count |", "|---|---|---|"]
    for surface, counter in event_counts(scen).items():
        for name, count in sorted(counter.items()):
            lines.append(f"| {surface} | {name} | {count} |")
    lines += [
        "",
        "## What was replaced",
        "",
        "| original | replacement | occurrences |",
        "|---|---|---|",
    ]
    for target in sorted(rules, key=len, reverse=True):
        row = f"| {describe(target, rules)} | `{rules[target]}` | {hits[target]} |"
        lines.append(row)
    lines += [
        "",
        "Nothing else was removed. Prompt text, assistant text and tool output are as "
        "captured, and the three TELLTALEFAKE credential probes are left in place: a "
        "fixture without the probe cannot show that the probe was removed.",
        "",
    ]
    return "\n".join(lines)


def describe(target: str, rules: dict[str, str]) -> str:
    """A rule names what it removes without printing the value it removes."""
    placeholder = rules[target]
    if placeholder.startswith("<"):
        return f"the machine path behind `{placeholder}`"
    return f"the identity value replaced by `{placeholder}`"


def sanitize_scenario(scen: Path, version: str, identity: dict[str, str]) -> str:
    dest_dir = FIXTURE_ROOT / version / scen.name
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True)
    rules = {**path_rules(scen), **identity}
    requests: dict[str, int] = {}
    hits: Counter[str] = Counter()
    for surface in SURFACE_FILES:
        source = scen / f"{surface}.jsonl"
        if not source.exists():
            continue
        count, file_hits = sanitize_file(source, dest_dir / f"{surface}.jsonl", rules)
        requests[surface] = count
        hits.update(file_hits)
    (dest_dir / "MANIFEST.md").write_text(
        manifest_text(scen, requests, hits, rules), encoding="utf-8"
    )
    return f"{scen.name}: {sum(requests.values())} lines over {len(requests)} surfaces"


def main() -> int:
    version = version_dir()
    identity = identity_values()
    print(f"claude version {version}, {len(identity)} identity values found")
    for scen in scenario_dirs():
        print(sanitize_scenario(scen, version, identity))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
