"""Decide which `-c` spelling Codex accepts for the OTel exporter, at zero token cost.

The config reference documents the KEYS (`otel.exporter`, `otel.exporter.<id>.endpoint`,
`otel.exporter.<id>.protocol`, `otel.environment`) but not the TOML shape `-c` wants,
and a wrong shape is only visible as a config-load failure. Running `codex exec` to
find out costs a session each time, so this probe uses two cheaper oracles instead:

1. `codex doctor --json` runs the SAME config loader and reports the check named
   config.load as ok or fail. It never contacts a model, so a probe is free. It does not
   print the underlying serde message.
2. `codex exec` prints that message on stderr and exits BEFORE the first model call when
   the config fails to load, so running it on a candidate the doctor already rejected is
   also free. `--exec-errors` turns that second pass on; it is capped by
   EXEC_KILL_SECONDS because a candidate that unexpectedly loads would start a session.

Nothing here writes to ~/.codex. Every candidate is passed on argv only.

Usage: python config_probe.py [--port N] [--exec-errors] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER = Path(__file__).resolve().parent / "run.py"

# A config-load failure exits in well under a second. Anything still alive after this
# either loaded the config and started a session, or hung; either way it is killed, and
# the record says so rather than reporting a clean rejection.
EXEC_KILL_SECONDS = 10.0
DOCTOR_TIMEOUT_SECONDS = 90.0


def candidates(port: int) -> list[tuple[str, list[str], str]]:
    """(label, argv fragment, what the label is testing)."""
    ep = f"http://127.0.0.1:{port}"
    return [
        (
            "baseline",
            ["-c", 'model_reasoning_effort="low"'],
            "control: a key known to work, so a failure means the oracle is broken",
        ),
        (
            "a-inline-table-signal-path",
            [
                "-c",
                'otel.exporter={ "otlp-http" = { endpoint = '
                f'"{ep}/v1/logs", protocol = "json" }} }}',
            ],
            "brief spelling (a): externally tagged enum as an inline table",
        ),
        (
            "a-inline-table-base-path",
            [
                "-c",
                'otel.exporter={ "otlp-http" = { endpoint = '
                f'"{ep}", protocol = "json" }} }}',
            ],
            "brief spelling (c) applied to (a): endpoint without the /v1/logs suffix",
        ),
        (
            "b-string-plus-dotted",
            [
                "-c",
                'otel.exporter="otlp-http"',
                "-c",
                f'otel.exporter.otlp-http.endpoint="{ep}/v1/logs"',
                "-c",
                'otel.exporter.otlp-http.protocol="json"',
            ],
            "brief spelling (b): a string tag plus dotted member keys",
        ),
        (
            "b-string-only",
            ["-c", 'otel.exporter="otlp-http"'],
            "the tag alone: does (b) load on its own merits, or only because the "
            "later dotted overrides replace the string with a table",
        ),
        (
            "bogus-value",
            ["-c", "otel.exporter=42"],
            "control in the other direction: a value the enum cannot possibly accept",
        ),
        (
            "c-dotted-only",
            [
                "-c",
                f'otel.exporter.otlp-http.endpoint="{ep}"',
                "-c",
                'otel.exporter.otlp-http.protocol="json"',
            ],
            "brief spelling (c): dotted member keys with no tag and no signal path",
        ),
        (
            "trace-exporter-inline-table",
            [
                "-c",
                'otel.trace_exporter={ "otlp-http" = { endpoint = '
                f'"{ep}", protocol = "json" }} }}',
            ],
            "does otel.trace_exporter take the same shape",
        ),
        (
            "metrics-exporter-inline-table",
            [
                "-c",
                'otel.metrics_exporter={ "otlp-http" = { endpoint = '
                f'"{ep}", protocol = "json" }} }}',
            ],
            "does otel.metrics_exporter take the same shape (default is statsig)",
        ),
        (
            "environment",
            ["-c", 'otel.environment="telltale-e02"'],
            "otel.environment as a bare string",
        ),
        (
            "exporter-bare-string-none",
            ["-c", 'otel.exporter="none"'],
            "unit variant of the same enum: proves the enum is externally tagged",
        ),
        (
            "unknown-key",
            ["-c", "otel.definitely_not_a_key=1"],
            "is an unrecognised key under otel reported at all",
        ),
        (
            "unknown-key-strict",
            ["--strict-config", "-c", "otel.definitely_not_a_key=1"],
            "does --strict-config extend to -c overrides or only to config.toml",
        ),
    ]


def otel_args(port: int, spelling: str) -> list[str]:
    """Ask run.py for the exact overrides it will use.

    A subprocess rather than an import: run.py is a sibling script and not a package, so
    importing it would declare a dependency on a module name deptry cannot resolve. What
    matters is that the probe and the runner test the SAME strings.
    """
    proc = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--print-otel-args",
            "--spelling",
            spelling,
            "--port",
            str(port),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return list(json.loads(proc.stdout))


def doctor(args: list[str], cwd: Path) -> tuple[str, str, list[str]]:
    proc = subprocess.run(
        ["codex", "doctor", "--json", "--no-color", *args],
        capture_output=True,
        text=True,
        timeout=DOCTOR_TIMEOUT_SECONDS,
        cwd=cwd,
        check=False,
    )
    try:
        check = json.loads(proc.stdout)["checks"]["config.load"]
    except (json.JSONDecodeError, KeyError) as exc:
        # The oracle produced no JSON. That is itself an answer when a flag makes the
        # doctor refuse before it reports, so the raw streams are kept rather than a
        # bare "unknown": a summary that hid them would be the guess this repo forbids.
        return (
            "oracle-failed",
            f"{type(exc).__name__}: {exc}",
            [
                f"exit={proc.returncode}",
                f"stdout={proc.stdout!r}",
                f"stderr={proc.stderr!r}",
            ],
        )
    return (str(check["status"]), str(check["summary"]), list(check.get("notes") or []))


def exec_error(
    args: list[str], cwd: Path, working_root: str | None = None
) -> dict[str, object]:
    """Capture the verbatim message `codex exec` prints for a config it cannot load.

    `working_root` is passed to `-C`. Naming a directory that does not exist makes the
    process abort after the config load and before the first model call, which is how a
    config that LOADS cleanly can still be probed for free.
    """
    root = ["-C", working_root] if working_root else []
    proc = subprocess.Popen(
        [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-s",
            "read-only",
            *root,
            *args,
            "probe",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
    )
    try:
        out, err = proc.communicate(timeout=EXEC_KILL_SECONDS)
        return {
            "exit_code": proc.returncode,
            "stderr": err,
            "stdout": out,
            "killed": False,
        }
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return {"exit_code": None, "stderr": err, "stdout": out, "killed": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--port", type=int, default=9999, help="port to spell in endpoints"
    )
    parser.add_argument(
        "--exec-errors",
        action="store_true",
        help="also run codex exec on rejected candidates for the verbatim message",
    )
    parser.add_argument("--out", help="write the full JSON record here")
    args = parser.parse_args()

    # A neutral cwd: the probe must not read this repository's AGENTS.md or .codex/.
    with tempfile.TemporaryDirectory(prefix="e02-config-probe-") as tmp:
        cwd = Path(tmp)
        records: list[dict[str, object]] = []
        for label, argv, question in candidates(args.port):
            status, summary, notes = doctor(argv, cwd)
            record: dict[str, object] = {
                "label": label,
                "question": question,
                "argv": argv,
                "doctor_status": status,
                "doctor_summary": summary,
                "doctor_notes": notes,
            }
            if args.exec_errors and status != "ok":
                record["exec"] = exec_error(argv, cwd)
            records.append(record)
            print(f"[{status:>4}] {label}: {summary}", flush=True)

        # `--strict-config` is on `codex exec` but not on `codex doctor`, so the doctor
        # cannot answer what strict mode does. The order the checks run in is itself a
        # finding: a nonexistent -C aborts BEFORE the config is read, which made the
        # first version of this probe report a clean load that had never happened. With
        # a real cwd, strict mode reads the -c overrides first and the owner's
        # config.toml second, and the owner's config.toml does not survive it. Every
        # line below therefore exits before any model call, at zero token cost.
        for label, argv, root in (
            (
                "strict-with-bad-working-root",
                ["--strict-config", "-c", 'model_reasoning_effort="low"'],
                "/e02-probe-no-such-directory",
            ),
            (
                "nonstrict-unknown-key-exec",
                ["-c", "otel.definitely_not_a_key=1"],
                "/e02-probe-no-such-directory",
            ),
            (
                "strict-owner-config",
                ["--strict-config", "-c", 'model_reasoning_effort="low"'],
                None,
            ),
            (
                "strict-otel-keys-spelling-a",
                ["--strict-config", *otel_args(args.port, "a")],
                None,
            ),
            (
                "strict-otel-keys-spelling-c",
                ["--strict-config", *otel_args(args.port, "c")],
                None,
            ),
        ):
            result = exec_error(argv, cwd, working_root=root)
            records.append(
                {
                    "label": label,
                    "question": "exec-only probe, aborted with a nonexistent -C",
                    "argv": argv,
                    "doctor_status": "not-applicable",
                    "doctor_summary": "",
                    "doctor_notes": [],
                    "exec": result,
                }
            )
            print(f"[exec] {label}: {str(result['stderr'])[:200]!r}", flush=True)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(records, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
