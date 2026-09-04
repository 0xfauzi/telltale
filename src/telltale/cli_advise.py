"""The `telltale advise` subcommand: the shadow advisory. Spec 14.6, design 6.12.

    telltale advise --head <ref> [--base <ref>] [--series <id>]

One candidate, one page, one observation. The page is `report_advise.render`; the
observation is a `policy.advisory` that Telltale emits about ITSELF, and there is
nothing else. Nothing is posted anywhere, no receiver is contacted, and `--post` does
not exist.

Four decisions carry the whole command.

  An advisory is an observation of provider `telltale` on surface `telltale`, in a
  capture of its own whose id is `adv_` plus 24 hex of a sha256 over (head_sha,
  base_sha, series_id, targets, created_at). Its own capture, never an agent's: an
  advisory is a statement this system made, and hanging it on a capture would make it
  read as something the recorded session produced. The id being a content hash means
  the same advisory re-run at the same instant is the same row, and `telltale sessions`
  and `telltale show` list it because a capture is whatever has observations.

  The label is READ, never computed here. It is the decision label of the latest
  true-order backtest of that target on that series, `not assessable` when the stored
  row carries no decision, and `no assessable forecast` when nothing scored the pair at
  all. A label this command derived would be a second decision rule.

  "Confidence" is the label plus the readiness word and nothing else. There is no
  number here that says how much to trust a forecast, because nothing measured one.

  Spec 14.6 draws the line this command stops at: when a policy begins ACTING on a
  forecast, Telltale writes a `policy.intervention` observation naming the advisory,
  the action and the policy version. `advise` never writes one. It is the thing a
  policy would act on, not the acting.

In its own file rather than in cli.py for the reason cli_outcome.py is: cli.py is at
665 lines against the 800-line ratchet, and this is a command with its own report.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telltale import cli_common as common
from telltale import repo, report_advise
from telltale.forecast import (
    ABLATION_A,
    CANDIDATE_SENTENCE,
    CANDIDATE_TARGETS,
    DEFAULT_DEVICE,
    ORDERING_TRUE,
    TARGETS,
    make,
    readiness,
)
from telltale.forecast import backtest as backtester
from telltale.forecast import candidate as protocol
from telltale.forecast import decide as decider
from telltale.model import Observation, now_iso, to_json, ulid
from telltale.sanitize import Ctx, sanitize

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping, Sequence

    from telltale.model import Series
    from telltale.store import Store

ADVISORY_TYPE = "policy.advisory"
# The capture id prefix, and the one spelling of it: cli.py routes `telltale show` to
# this module on it, and a second copy of the string would be a route that goes stale.
PREFIX = "adv_"
PROVIDER = "telltale"
SURFACE = "telltale"
ADAPTER = "telltale.advise@1"
# The only action this task writes. Spec 14.6: forecasts are shadow-only during primary
# evaluation, and the word an acting policy writes goes in a `policy.intervention`.
ACTION = "shadow"
CLOCK = "change"
HORIZON = 1
READY = "ready"
# What the readiness word says when the checklist could not run at all: a target the
# series carries with `partial` coverage is refused by the backtester before check 1,
# and "no line failed" is not the same answer as "no line was asked".
READINESS_REFUSED = "refused"
# The label for a target nothing has scored on this series. Distinct from decide's
# `not assessable`, which is the rule LOOKING at a run and writing no label: here there
# is no run to look at, and the two are different facts about different evidence.
NO_FORECAST = "no assessable forecast"
NO_RUN = (
    "no stored true-order backtest of this target on this series, so there is nothing"
    " whose label could say how strongly a forecast of this candidate may be read."
    " Run `telltale forecast backtest` or `telltale forecast candidate` on it first"
)
# A sanitized payload holds no path and no command, so the content level changes
# nothing about what is stored. 1 is the default a capture runs at.
LEVEL = 1
# How much of the sha256 an advisory id carries. 24 hex is what `series.series_id`
# uses, and the whole 64 would put the id past nothing here; it is short because it is
# read aloud off a report and typed into `telltale show`.
ID_HEX = 24


class Refusal(Exception):
    """The command declining on purpose. cli.py turns it into exit 2."""


def _policy_version() -> str:
    """The reducer version of the protocol this advisory ran under.

    The hash of forecast/candidate.py, which is the file that defines what the A block
    is, which targets a candidate may have and what the conditioned run sees. Truncated
    to ID_HEX: `policy_version` is `Kind.ENUM` and the sanitizer bounds an enum at 64
    characters silently, so a full sha256 behind a prefix would be stored cut at an
    arbitrary point and two versions could collide in the store while differing in the
    code.
    """
    try:
        digest = hashlib.sha256(Path(protocol.__file__).read_bytes()).hexdigest()
    except OSError:
        return "cnd-source-unavailable"
    return f"cnd-{digest[:ID_HEX]}"


POLICY_VERSION = _policy_version()


def advise(args: argparse.Namespace) -> int:
    """One candidate, printed and stored, and acted on by nothing. Design 6.12.

    The store is opened for the whole command because the last thing it does is write:
    the reads (the series, the stored runs) take their own read-only connections and
    the append needs the writer thread.

    Every refusal is exit 2 and one line. A candidate whose head is a merge, a
    repository with no change series, a target whose A block holds an unknown: all of
    them are the command declining, and none of them is a half-built advisory.
    """
    store = common.store().open()
    try:
        record = build(store, ".", args)
        printed = emit(store, record)
    except (Refusal, protocol.NotACandidate, backtester.Refused) as refused:
        return common.refuse(f"telltale advise: {refused}")
    finally:
        store.close()
    print(printed)
    print(f"\nadvisory {record['advisory_id']}")
    print(f"run `telltale show {record['advisory_id']}` for the stored payload")
    return 0


def emit(store: Store, record: Mapping[str, Any]) -> str:
    """Render, then store, then hand the page back. ADR-014 fixes that order.

    A report that may not be printed is a report that may not be stored either, so
    `render` runs first and its ForbiddenWord leaves this function with nothing
    appended. `advise` above turns it into a refusal; a caller that wants the exception
    calls this.
    """
    printed = report_advise.render(record)
    body, redaction, unknown = sanitize(ADVISORY_TYPE, payload(record), LEVEL, Ctx())
    capture = str(record["advisory_id"])
    store.append([
        Observation(
            observation_id=ulid(),
            capture_id=capture,
            observation_type=ADVISORY_TYPE,
            surface=SURFACE,
            provider=PROVIDER,
            adapter=ADAPTER,
            ingest_ts=str(record["created_at"]),
            repo_id=record["repo_id"],
            payload=body,
            redaction=redaction,
        )
    ])  # fmt: skip
    if unknown:
        # Named with the type, so a reader of the diagnostics table can tell an
        # advisory's unknown field from a provider's. This system's own payload
        # reaching here is a bug in this file rather than a provider that moved.
        store.diagnose(
            "unknown_field",
            f"{ADVISORY_TYPE}: {' '.join(sorted(unknown))}",
            capture_id=capture,
        )
    store.flush()
    return printed


def payload(record: Mapping[str, Any]) -> dict[str, Any]:
    """The allowlisted `policy.advisory` body. Design 6.3.

    `label`, `readiness` and `forecast_run_ids` are keyed BY TARGET rather than being
    three lists parallel to `target`. Positional alignment is the defect class
    AGENTS.md names: the sanitizer drops a value it cannot clean, and a dropped entry
    in a parallel list silently re-labels a different target. A key cannot slide.
    """
    targets = [str(one["target"]) for one in record["targets"]]
    return {
        "advisory_id": record["advisory_id"],
        "action": record["action"],
        "policy_version": record["policy_version"],
        "base_sha": record["base_sha"],
        "head_sha": record["head_sha"],
        "series_id": record["series_id"],
        "target": targets,
        "forecast_run_ids": {
            str(one["target"]): list(one["forecast_run_ids"])
            for one in record["targets"]
        },
        "label": {str(one["target"]): one["label"] for one in record["targets"]},
        "readiness": {
            str(one["target"]): one["readiness"] for one in record["targets"]
        },
        "features": {name: record["features"][name] for name in ABLATION_A},
    }


# -- building the record --------------------------------------------------------------


def build(store: Store, cwd: str, args: argparse.Namespace) -> dict[str, Any]:
    """Everything the page and the payload are made of, and nothing derived twice."""
    repo_id = repo.identity(cwd)["repo_id"]
    if not repo_id:
        raise Refusal(
            f"no repository at {cwd!r}, so there is no change history this candidate"
            " is a change against"
        )
    base = args.base or _default_base(cwd, args.head)
    found = protocol.features(cwd, base, args.head)
    series = _series(store, str(repo_id), args.series)
    created = now_iso()
    targets = [
        _target(store, series, name, found) for name in sorted(CANDIDATE_TARGETS)
    ]
    return {
        "advisory_id": advisory_id(found, series.series_id, targets, created),
        "action": ACTION,
        "policy_version": POLICY_VERSION,
        "created_at": created,
        "repo_id": str(repo_id),
        "base_ref": base,
        "head_ref": args.head,
        "base_sha": found["base_sha"],
        "head_sha": found["head_sha"],
        "merge_base": found["merge_base"],
        "series_id": series.series_id,
        "clock": series.clock,
        "rows": len(series.rows),
        "built_at": series.built_at,
        "features": {name: found[name] for name in ABLATION_A},
        "targets": targets,
        "sentence": CANDIDATE_SENTENCE,
    }


def advisory_id(
    found: Mapping[str, Any],
    series_id: str,
    targets: Sequence[Mapping[str, Any]],
    created_at: str,
) -> str:
    """`adv_` plus 24 hex over what this advisory IS about.

    The five things design 6.12 makes an advisory a statement about: which change
    (head), against what (base), read off which history (series), for which targets,
    and when. Two advisories that differ in any of them are two rows; the timestamp is
    in the hash because the same candidate advised twice against a series that has
    grown is a second statement and not a correction of the first.
    """
    canonical = to_json([
        found["head_sha"],
        found["base_sha"],
        series_id,
        [str(one["target"]) for one in targets],
        created_at,
    ])  # fmt: skip
    return f"{PREFIX}{hashlib.sha256(canonical.encode()).hexdigest()[:ID_HEX]}"


def _default_base(cwd: str, head: str) -> str:
    """merge-base of `head` with the default branch, the way repo.identity does it.

    `repo._DEFAULT_BRANCH_REFS` rather than a second copy of the list: base_sha on
    every stored `telltale.repo.identity` was resolved through it, and an advisory that
    called a different commit the base would be measuring a candidate against something
    no capture in the store agrees with. The head is this command's argument rather
    than HEAD, which is the only thing that differs from `repo._base_sha`.
    """
    remote = repo.git_line(
        cwd, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"
    )
    tried = [*([remote] if remote else []), *repo._DEFAULT_BRANCH_REFS]
    for ref in tried:
        base = repo.git_line(cwd, "merge-base", head, ref)
        if base:
            return base
    raise Refusal(
        f"--base was not given and none of {tried} shares an ancestor with {head}, so"
        " there is no default branch this candidate is a change against. Name one with"
        " --base"
    )


def _series(store: Store, repo_id: str, series_id: str | None) -> Series:
    """The change-clock series this advisory reads, named or resolved.

    Resolved, the NEWEST change series of this repository wins, and the header prints
    its id, its row count and when it was built so the choice is auditable. That is not
    the "duplicate is not one" rule being waived: a rebuilt change series is a later
    view of one history as it grew, not a second history, and refusing while several
    exist would make the command unusable on any store that has ever run `series build`
    twice. `--series` is how a reader names a different one.
    """
    if series_id is not None:
        found = store.series(series_id)
        if found is None:
            raise Refusal(f"{series_id}: no such series. Run `telltale series list`.")
        return found
    rows = [
        row
        for row in store.series_ids(CLOCK)
        if dict(row["cohort"]).get("repo_id") == repo_id
    ]
    if not rows:
        raise Refusal(
            f"no {CLOCK}-clock series of repository {repo_id[:12]} in this store."
            f" Run `telltale series build --clock {CLOCK} --repo {repo_id}` first"
        )
    return _named(store, str(rows[0]["series_id"]))


def _named(store: Store, series_id: str) -> Series:
    found = store.series(series_id)
    if found is None:
        raise Refusal(f"{series_id}: the snapshot list names it and the table does not")
    return found


def _target(
    store: Store, series: Series, name: str, found: Mapping[str, Any]
) -> dict[str, Any]:
    """One target's block: the readiness word, the label, and the pair at origin N."""
    row = _latest(store, series.series_id, name)
    word, detail = _readiness(series, name)
    decision = dict(row["decision"] or {}) if row is not None else {}
    forecast, note, licences = _forecast(series, name, row, found)
    return {
        "target": name,
        "unit": TARGETS[name].unit,
        "readiness": word,
        "readiness_detail": detail,
        "label": _label(row, decision),
        "decision_reason": decision.get("reason"),
        "inequalities": list(decision.get("inequalities") or []),
        "run": None if row is None else _run(row, decision),
        "forecast_run_ids": [] if row is None else [str(row["forecast_run_id"])],
        "forecast": forecast,
        "forecast_note": note,
        "licences": licences,
    }


def _latest(store: Store, series_id: str, target: str) -> dict[str, Any] | None:
    """The newest SCORED true-order run of this pair, or None when nothing scored it.

    `forecast_runs` is ordered by created_at, so the last match is the newest. Newest
    rather than any: a re-run of a pair is a correction of the older one, which is the
    rule `cli_forecast._stored_true` already applies to the same table.

    `metrics["forecasters"]` is what separates a scored run from a scenario. A scenario
    (W6-T1) stores a true-order row of this same pair whose `metrics` is `{}` and whose
    `decision` is null, because its origin is one past the last row and there is no
    actual to score against. Taken as the newest run it would make `_label` below print
    NOT_ASSESSABLE, which says the decision rule looked and found too few windows, when
    no rule looked at all. `forecast.scenario.calibration` draws the same line the same
    way. Without a scored run this returns None and the label is NO_FORECAST, which is
    the honest sentence.
    """
    matched = [
        row
        for row in store.forecast_runs(series_id)
        if row["target"] == target
        and row["ordering"] == ORDERING_TRUE
        and (row["metrics"] or {}).get("forecasters")
    ]
    return matched[-1] if matched else None


def _label(row: Mapping[str, Any] | None, decision: Mapping[str, Any]) -> str:
    """The stored decision's own word, never one this command derived."""
    if row is None:
        return NO_FORECAST
    return str(decision.get("label") or decider.NOT_ASSESSABLE)


def _run(row: Mapping[str, Any], decision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "forecast_run_id": str(row["forecast_run_id"]),
        "variant": str(row["variant"]),
        "created_at": str(row["created_at"]),
        "model": decision.get("model"),
    }


def _readiness(series: Series, target: str) -> tuple[str, str | None]:
    """`ready`, or the name of the FIRST line of the checklist that failed.

    The first rather than all of them because the eight checks are in cost order and a
    reader stops at the first FAIL; the whole list is `telltale forecast readiness`.
    A checklist the backtester refused to build at all is `refused` with its reason,
    which is a different answer from every line passing.
    """
    try:
        checks = readiness.check(series, target, HORIZON)
    except backtester.Refused as refused:
        return READINESS_REFUSED, str(refused)
    failed = [item for item in checks if not item.passed]
    if not failed:
        return READY, None
    return failed[0].name, failed[0].stated()


def _forecast(
    series: Series,
    target: str,
    row: Mapping[str, Any] | None,
    found: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """The two forecasts of row N, or None and the reason there are none.

    The forecasters are the ones the stored run declared, rebuilt by name. Any other
    set would put a forecast next to a label that was measured on something else.
    """
    if row is None:
        return _no_forecast(NO_RUN)
    try:
        forecasters = {
            str(one["name"]): make(
                str(one["name"]), str(one["device"] or DEFAULT_DEVICE)
            )
            for one in row["forecasters"]
        }
        ahead = protocol.one_step(series, target, forecasters, found)
    except ImportError as missing:
        return _no_forecast(f"timesfm needs the forecast extra: {missing}")
    except (KeyError, backtester.Refused) as refused:
        return _no_forecast(str(refused))
    return ahead, None, protocol.licences([ahead])


def _no_forecast(note: str) -> tuple[None, str, list[str]]:
    """No pair, the reason, and the line that says no checkpoint was involved."""
    return None, note, [protocol.NO_LICENCE]


# -- `telltale show adv_...` ----------------------------------------------------------


def show(store: Store, capture_id: str) -> int:
    """The stored advisory, as JSON. cli.py routes `show` here on the id prefix.

    Not through `measures.summary`: an advisory has no activities and never will, so
    the session summary would report a capture of nulls for it, and that is exactly the
    confusion invariant 5 forbids. What a reader wants here is the payload.
    """
    rows = [
        row
        for row in store.observations(capture_id)
        if row["observation_type"] == ADVISORY_TYPE
    ]
    if not rows:
        return common.refuse(
            f"{capture_id}: no advisory on this disk. `telltale sessions` lists the"
            f" captures, and an advisory is one of provider {PROVIDER}"
        )
    print(_json(report_advise.stored(rows[0])))
    return 0


def _json(record: Mapping[str, Any]) -> str:
    # json.dumps rather than model.to_json: sorted keys would move `coverage` out of
    # first place, and design 6.13 puts it there. Same rule as report.show.
    return json.dumps(record, indent=2, ensure_ascii=False)


def add_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """`telltale advise --head REF [--base REF] [--series ID]`. Spec 14.6."""
    said = subcommands.add_parser(
        "advise", help="the shadow advisory for one candidate (spec 14.6)"
    )
    said.add_argument("--head", required=True, metavar="REF")
    said.add_argument(
        "--base",
        default=None,
        metavar="REF",
        help="default: the merge base of --head with this repository's default branch,"
        " resolved the way `telltale.repo.identity` resolves base_sha",
    )
    said.add_argument(
        "--series",
        default=None,
        metavar="ID",
        help=f"default: the newest {CLOCK}-clock series of this repository",
    )
