"""`telltale experiment probe` and `telltale experiment intervention`. Spec 14.3.

`probe <spec.json>` runs a suite of fixed read-only probes: each probe is a prompt with
an answer key of repository paths and symbols, run `repetitions` times in its own
disposable worktree at one base commit. The agent's final answer is scored for precision
and recall against that key, and the score is what is reported and stored; the answer
text is read in the runner's own process and dropped.

`intervention <spec.json>` runs the same suite at two commits, before and after a scoped
refactor, with the environment fingerprint held constant, and pairs the two arms BY
PROBE.

In its own file rather than in cli.py for the reason cli_import.py and cli_outcome.py
are: cli.py is at the 800-line ratchet's near side with another task adding to it, and
a subcommand that registers itself costs that file one import and one table entry. The
two kinds are added to the `experiment` subparser cli.py already builds, so
`telltale experiment --help` lists all four in one place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import cli_common as common
from telltale import config, experiments, experiments_env, experiments_measure
from telltale import experiments_probe as probing
from telltale import report_probe as rendering

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable

# Both refusals are exit code 2 and one line rather than a traceback: a spec that is not
# a probe suite, an answer key naming a path the commit does not carry, and two arms
# whose environments differ are all the runner declining on purpose.
_REFUSALS = (experiments.SpecError, experiments_measure.FingerprintMismatch)


def probe(spec_path: str, out: str | None) -> int:
    """Run one probe suite and print what it scored. Spec 14.3."""
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    try:
        measured = probing.probe(
            spec, config.home(), out=None if out is None else Path(out)
        )
    except _REFUSALS as refusal:
        print(f"experiment probe: {refusal}")
        return common.REFUSED
    print(rendering.probe(measured))
    return 0


def intervention(spec_path: str, out: str | None) -> int:
    """Run one probe suite at two commits and print the paired comparison. Spec 14.3."""
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    try:
        measured = experiments_env.intervention(
            spec, config.home(), out=None if out is None else Path(out)
        )
    except _REFUSALS as refusal:
        print(f"experiment intervention: {refusal}")
        return common.REFUSED
    print(rendering.intervention(measured))
    return 0


def add_kinds(kinds: argparse._SubParsersAction[Any]) -> None:
    """Register both kinds on the `experiment` subparser cli.py builds."""
    probing_parser = kinds.add_parser(
        "probe", help="fixed read-only probes, scored against an answer key"
    )
    probing_parser.add_argument("spec", metavar="spec.json")
    probing_parser.add_argument(
        "--out",
        default=None,
        metavar="DIR",
        help="also write DIR/<task_id>/probe.json (default: print only)",
    )
    intervening = kinds.add_parser(
        "intervention", help="one probe suite at two commits, paired by probe"
    )
    intervening.add_argument("spec", metavar="spec.json")
    intervening.add_argument(
        "--out",
        default=None,
        metavar="DIR",
        help="also write DIR/<task_id>/intervention.json (default: print only)",
    )


# The kinds cli.py's `_EXPERIMENTS` table merges in, for the reason that table exists.
KINDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "probe": lambda args: probe(args.spec, args.out),
    "intervention": lambda args: intervention(args.spec, args.out),
}
