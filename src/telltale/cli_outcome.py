"""The `telltale outcome` subcommand. Design 6.3, 6.12 and 6.13.

`outcome --kind K --status S --task-id X --attempt N` records what happened to one
attempt: a mechanical verification, an adversarial review, a merge decision, a revert
or repair, or a runtime signal. It is the only CLI write path besides `run` and
`import`, and it exists because three columns of the attempt clock (design 6.12) are
outcomes and nothing but the experiment runner (`experiments._outcome`) could post one
before it. The build's own merge protocol can now record its decisions.

In its own file rather than in cli.py for the reason cli_import.py is: adding it there
took that file to 825 lines against the 800-line ratchet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from telltale import cli_common as common
from telltale import config, repo
from telltale import series_lineage as lineage
from telltale.model import Observation, now_iso, to_json, ulid
from telltale.receiver import _post, _with_capture
from telltale.sanitize import Ctx, sanitize
from telltale.store import Store

if TYPE_CHECKING:
    import argparse


# The five outcome kinds of design 6.3, and the two ways one reaches the store. A kind
# outside this tuple is an argparse error rather than a stored word nobody can read.
OUTCOME_KINDS = (
    "mechanical_verification",
    "adversarial_review",
    "merge_decision",
    "revert_or_repair",
    "runtime_signal",
)

# external.outcome has no task_id field in the allowlist (design 6.3), so the task
# travels in component_id and in external_run_id, which are the two the schema has.
# experiments.py `_outcome` makes the same substitution, and this is the same fact.
_OUTCOME_TYPE = "external.outcome"
_OUTCOME_ROUTE = "/v1/outcomes"


def outcome(args: argparse.Namespace) -> int:
    """Record what happened to one attempt. Design 6.3 and 6.12.

    The only CLI write path besides `run` and `import`, and the reason it exists: an
    attempt clock's verification_passed, review_fail_count and accepted columns come
    from outcomes, and until this command nothing but the experiment runner could post
    one. The build's own merge protocol can now record its decisions.

    The observation goes to the capture carrying the matching (task_id, attempt), and
    to nothing else. Zero captures carrying it and two captures carrying it are both
    refusals that name what was found: absence is not zero and duplicate is not one.
    """
    repo_id = args.repo or repo.identity(".")["repo_id"]
    if not repo_id:
        return common.refuse(
            "telltale outcome: no repository here and no --repo given, so there is no"
            " lineage to search for this attempt"
        )
    store = common.store()
    try:
        found = lineage.lineage(store, str(repo_id))
    except lineage.Refused as refused:
        return common.refuse(f"telltale outcome: {refused}")
    matched = lineage.find(found, args.task_id, args.attempt)
    if len(matched) != 1:
        return common.refuse(_no_attempt(args, found, matched))
    return _post_outcome(args, matched[0].capture_id, str(repo_id))


def _no_attempt(
    args: argparse.Namespace, found: lineage.Lineage, matched: list[lineage.Attempt]
) -> str:
    """The refusal, naming what was searched and what was found."""
    wanted = f"{args.task_id} attempt {args.attempt}"
    if not matched:
        seen = ", ".join(
            sorted({f"{one.task_id}/{one.attempt}" for one in found.attempts})
        )
        return (
            f"telltale outcome: no capture of this repository carries {wanted}."
            f" {len(found.attempts)} attempt(s) read: {seen or 'none'}"
        )
    listed = ", ".join(one.capture_id for one in matched)
    return (
        f"telltale outcome: {len(matched)} captures carry {wanted} ({listed}),"
        " so there is no one capture this outcome belongs to"
    )


def _outcome_payload(args: argparse.Namespace) -> dict[str, Any]:
    """The allowlisted external.outcome body. A field with no value is left out."""
    body: dict[str, Any] = {
        "kind": args.kind,
        "status": args.status,
        "component_id": args.task_id,
        "attempt": args.attempt,
        "timestamp": now_iso(),
        "external_run_id": args.external_run_id,
        # W5-T1: how long the run this outcome reports on took. The change clock's
        # merge_verification_ms reads it off a mechanical_verification outcome, and an
        # outcome posted without it leaves that cell None rather than 0: a duration
        # nobody stated is not a fast verification.
        "duration_ms": args.duration_ms,
        "categories": [word for word in (args.categories or "").split(",") if word],
    }
    # `value not in (None, [])` and not a falsiness test: --duration-ms 0 is a
    # measurement (a verification that finished inside a millisecond) and 0 == False in
    # Python, so `if value` would drop it and the cell would read as unstated.
    return {name: value for name, value in body.items() if value not in (None, [])}


def _post_outcome(args: argparse.Namespace, capture_id: str, repo_id: str) -> int:
    """Through a running receiver when one was named, and through the store otherwise.

    Two paths because a capture may be in flight: a receiver already serving it is the
    right door, and its own store is the only one open. With no receiver this appends
    through `$TELLTALE_HOME`'s store and rebuilds the capture, so the activity the
    clocks read exists when the command returns.
    """
    payload = _outcome_payload(args)
    if args.receiver:
        return _post_to_receiver(args.receiver, capture_id, payload)
    body, redaction, unknown = sanitize(_OUTCOME_TYPE, payload, 1, Ctx())
    store = Store(config.db_path()).open()
    try:
        store.append([
            Observation(
                observation_id=ulid(),
                capture_id=capture_id,
                observation_type=_OUTCOME_TYPE,
                surface="external",
                provider="external",
                adapter="external@1",
                ingest_ts=now_iso(),
                repo_id=repo_id,
                payload=body,
                redaction=redaction,
            )
        ])  # fmt: skip
        store.flush()
        store.rebuild(capture_id)
    finally:
        store.close()
    print(f"outcome {args.kind} {args.status} recorded on {capture_id}")
    _print_unknown(unknown)
    return 0


def _post_to_receiver(target: str, capture_id: str, payload: dict[str, Any]) -> int:
    """POST to a receiver somebody else is running. It answers 200 whatever happened.

    So the status is printed rather than trusted: spec 5.2 makes every endpoint answer
    200, and `telltale rebuild` is what makes the record visible to the clocks, since
    this process may not open a second writer on the same database.
    """
    parsed = urlsplit(target if "://" in target else f"http://{target}")
    if parsed.hostname is None or parsed.port is None:
        return common.refuse(
            f"telltale outcome: --receiver {target!r} is not host:port or a URL"
        )
    route = _with_capture(_OUTCOME_ROUTE, capture_id)
    status = _post(parsed.port, route, to_json(payload).encode("utf-8"))
    print(f"posted to {parsed.hostname}:{parsed.port} -> {status} for {capture_id}")
    print(f"run `telltale rebuild {capture_id}` to fold it into the clocks")
    return 0


def _print_unknown(unknown: list[str]) -> None:
    if unknown:
        print(f"fields the allowlist does not carry, and did not store: {unknown}")


def add_commands(subcommands: argparse._SubParsersAction[Any]) -> None:
    """`telltale outcome --kind K --status S --task-id X --attempt N`. Design 6.3."""
    said = subcommands.add_parser(
        "outcome", help="record what happened to one attempt (design 6.3)"
    )
    said.add_argument("--kind", required=True, choices=OUTCOME_KINDS)
    said.add_argument(
        "--status", required=True, metavar="S", help="the orchestrator's own word"
    )
    said.add_argument("--task-id", required=True, metavar="X")
    said.add_argument("--attempt", required=True, type=int, metavar="N")
    said.add_argument(
        "--duration-ms",
        type=int,
        default=None,
        metavar="N",
        help="whole milliseconds the run this outcome reports on took. Absent leaves"
        " the change clock's merge_verification_ms unknown, which is not 0",
    )
    said.add_argument("--categories", default=None, metavar="A,B")
    said.add_argument("--external-run-id", default=None, metavar="ID")
    said.add_argument(
        "--repo", default=None, metavar="REPO_ID", help="default: the repository here"
    )
    said.add_argument(
        "--receiver",
        default=None,
        metavar="URL",
        help="post to a receiver already serving this capture, rather than the store",
    )
