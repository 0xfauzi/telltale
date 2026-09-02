"""The two commands that answer questions about Telltale itself, run as the binary.

Every test here runs the installed `telltale` console script in a subprocess rather than
calling `main()`, because half of what is under test is outside the function: the
`[project.scripts]` entry point, the import chain, argparse, the exit code the shell
sees, and the process environment that says where $TELLTALE_HOME is.

`doctor` is the command a person runs when they suspect Telltale is not recording. It
has to be able to say "this surface does not round-trip" on a machine with no claude
binary, no codex binary and no network, which is exactly the CI machine: the binary
checks report present or absent and never decide the exit code, and only the surfaces
Telltale itself serves do.

`setup` is the command that must not write. The owner decision of 2026-09-01 is that
capture is launcher-only and Telltale never edits ~/.claude or ~/.codex, so the test for
it compares a directory listing of the temporary HOME and the temporary TELLTALE_HOME
before and after the command, and `--apply` is expected to refuse.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from telltale import __version__

if TYPE_CHECKING:
    from collections.abc import Sequence

# The default `telltale daemon` port when config.json does not name one.
DEFAULT_PORT = 47311

# The surface, and the observation type a healthy round trip through it produces.
# Spelled here rather than imported from the code under test: a table compared
# against itself agrees with itself whatever either says.
ROUND_TRIP = {
    "otel_logs": "claude.otel.api_request",
    "otel_metrics": "claude.otel.metric",
    "hook": "claude.hook.SessionEnd",
    "stream": "claude.stream.system.init",
    "correlations": "external.correlation",
    "outcomes": "external.outcome",
    "policy_interventions": "policy.intervention",
}

TOOLS = ("git", "claude", "codex", "uv", "timesfm")

# The twelve hook events a Claude Code settings snippet has to register for Telltale to
# see a session's lifecycle. Spelled out for the same reason as ROUND_TRIP.
HOOK_EVENTS = (
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PreCompact",
    "PostCompact",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "SessionEnd",
    "SessionStart",
    "UserPromptSubmit",
    "PostModelSwitch",
)

# The three switches that would make Claude Code send prompt text, assistant text and
# tool contents. Telltale never turns them on, and a snippet the owner pastes into a
# real settings.json is the one place where turning one on would be invisible.
CONTENT_SWITCHES = (
    "OTEL_LOG_USER_PROMPTS",
    "OTEL_LOG_ASSISTANT_RESPONSES",
    "OTEL_LOG_TOOL_CONTENT",
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("telltale")
    assert executable is not None, "no `telltale` on PATH: run `uv sync` first"
    return subprocess.run(
        [executable, *args], capture_output=True, text=True, check=False, timeout=60
    )


def _tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def _rows(stdout: str) -> dict[str, list[str]]:
    """The printed tables as {first column: the rest of the row}, rules dropped."""
    rows: dict[str, list[str]] = {}
    for line in stdout.splitlines():
        cells = line.split()
        if len(cells) >= 2 and not cells[0].startswith("-"):
            rows[cells[0]] = cells[1:]
    return rows


@pytest.mark.integration
def test_the_console_script_reports_the_installed_version() -> None:
    """The smallest end-to-end fact: packaging, entry point, imports and argparse.

    The expected string is not spelled here. The subprocess prints what argparse got
    from importlib.metadata and the test compares it against the same metadata read in
    this process, because spelling "0.0.1" in both places would only prove that the test
    author can copy a number.
    """
    # Not the import fallback: if this fires, the distribution metadata is missing and
    # the comparison below would be trivially true on both sides.
    assert __version__ != "0+unknown"

    completed = _run("--version")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == __version__


@pytest.mark.integration
def test_doctor_round_trips_every_surface_and_leaves_nothing_behind(
    telltale_home: Path,
) -> None:
    """Every endpoint takes a record, stores it, gives back its own type, and no more.

    `rows[surface] == ["ok", observation_type]` is an equality rather than a membership
    test on purpose: a second type in that cell would be a second whitespace-separated
    word, so a surface that answers with somebody else's record fails here.

    The diagnostics line is the other half. doctor's records carry allowlisted fields
    only and one session id per capture, so a healthy round trip writes nothing to the
    diagnostics table; a line naming a kind means doctor's synthetic input is wrong, and
    that is worth failing on while the round trip still says ok everywhere.
    """
    before = _tree(telltale_home)

    completed = _run("doctor")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    rows = _rows(completed.stdout)
    for surface, observation_type in ROUND_TRIP.items():
        assert rows[surface] == ["ok", observation_type], completed.stdout
    assert rows["daemon_port"][0] == "ok", completed.stdout
    assert "diagnostics written by the round trip: none" in completed.stdout
    assert _tree(telltale_home) == before, "doctor left files in $TELLTALE_HOME"


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_doctor_reports_the_tools_without_letting_them_decide() -> None:
    """git, claude, codex, uv and timesfm are reported, and never fail the command.

    CI has no claude binary and no forecasting stack, and Telltale still works there.
    A doctor that exits 1 for a missing agent binary would be a doctor nobody trusts to
    mean anything by exit 1.
    """
    completed = _run("doctor")

    rows = _rows(completed.stdout)
    reported = {tool: rows[tool][0] for tool in TOOLS}
    assert set(reported.values()) <= {"present", "absent"}, reported
    assert completed.returncode == 0, reported


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_doctor_fails_when_something_else_holds_the_daemon_port() -> None:
    """A listener that accepts and never answers is the failure this check exists for.

    The socket is bound and listened on and never accepted from, which is what a wedged
    or foreign process on the daemon port looks like: the connection completes, the
    request goes out, and no answer ever comes.
    """
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = int(listener.getsockname()[1])

        completed = _run("doctor", "--port", str(port))
    finally:
        listener.close()

    assert completed.returncode != 0, completed.stdout
    assert "daemon_port did not round-trip" in completed.stdout, completed.stdout
    rows = _rows(completed.stdout)
    assert rows["daemon_port"][0] == "failed", completed.stdout
    assert str(port) in " ".join(rows["daemon_port"]), completed.stdout
    # The surfaces Telltale serves itself still round-trip: the failure is one row.
    assert rows["otel_logs"] == ["ok", ROUND_TRIP["otel_logs"]], completed.stdout


@pytest.mark.integration
def test_setup_claude_prints_a_settings_snippet_and_writes_nothing(
    telltale_home: Path,
) -> None:
    """JSON on stdout, every hook event, the OTel block, and no file anywhere."""
    home = Path(os.environ["HOME"])
    before = (_tree(telltale_home), _tree(home))

    completed = _run("setup", "claude", "--print")

    assert completed.returncode == 0, completed.stderr
    settings = json.loads(completed.stdout)
    assert set(settings["hooks"]) == set(HOOK_EVENTS), sorted(settings["hooks"])
    for event in HOOK_EVENTS:
        assert _http_hooks(settings["hooks"][event]) == [
            {
                "type": "http",
                "url": f"http://127.0.0.1:{DEFAULT_PORT}/hooks/claude",
                "timeout": 5,
            }
        ], event
    environment = settings["env"]
    assert environment["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert environment["OTEL_LOGS_EXPORTER"] == "otlp"
    assert environment["OTEL_METRICS_EXPORTER"] == "otlp"
    assert environment["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/json"
    assert environment["OTEL_EXPORTER_OTLP_ENDPOINT"] == (
        f"http://127.0.0.1:{DEFAULT_PORT}"
    )
    assert not [name for name in CONTENT_SWITCHES if name in environment]
    assert (_tree(telltale_home), _tree(home)) == before, "setup wrote a file"


@pytest.mark.integration
def test_setup_claude_reads_the_configured_port_and_leaves_the_file_alone(
    telltale_home: Path,
) -> None:
    """config.json is read for the daemon port and is never rewritten by reading it."""
    configured = 47555
    path = telltale_home / "config.json"
    path.write_text(json.dumps({"daemon_port": configured}), encoding="utf-8")
    before = path.read_bytes()

    completed = _run("setup", "claude", "--print")

    settings = json.loads(completed.stdout)
    url = f"http://127.0.0.1:{configured}/hooks/claude"
    assert _http_hooks(settings["hooks"]["Stop"])[0]["url"] == url
    assert settings["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == (
        f"http://127.0.0.1:{configured}"
    )
    assert path.read_bytes() == before
    assert _tree(telltale_home) == ["config.json"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("contents", "wanted"),
    [("not json at all", "Expecting value"), ('["a list"]', "not an object")],
)
def test_setup_stops_on_a_config_it_cannot_read(
    telltale_home: Path, contents: str, wanted: str
) -> None:
    """A config.json that cannot be parsed is not the same as no config.json.

    No file means nothing was configured. A file that will not parse means something
    was configured and this process cannot tell what, and falling back to the default
    port there prints a snippet naming a port the owner did not choose, which looks
    exactly as right as one they did.
    """
    path = telltale_home / "config.json"
    path.write_text(contents, encoding="utf-8")

    completed = _run("setup", "claude", "--print")

    assert completed.returncode != 0, completed.stdout
    assert str(path) in completed.stderr, completed.stderr
    assert wanted in completed.stderr, completed.stderr
    assert not completed.stdout
    assert path.read_text(encoding="utf-8") == contents


@pytest.mark.integration
def test_setup_apply_refuses_and_names_the_owner_decision(telltale_home: Path) -> None:
    """`--apply` is the request the owner decided Telltale never grants."""
    home = Path(os.environ["HOME"])
    before = (_tree(telltale_home), _tree(home))

    completed = _run("setup", "claude", "--apply")

    assert completed.returncode == 2, completed.stdout
    assert "refused" in completed.stdout
    assert "Owner decision of 2026-09-01" in completed.stdout
    assert "~/.claude/settings.json" in completed.stdout
    assert (_tree(telltale_home), _tree(home)) == before, "setup --apply wrote a file"


@pytest.mark.integration
@pytest.mark.usefixtures("telltale_home")
def test_setup_codex_prints_the_snippet_that_e02_has_not_settled() -> None:
    """Until E02 measures how codex takes this configuration, the command says so.

    A key name nobody has run is worse than no key name: it would be pasted into a real
    config.toml, do nothing, and look like Telltale failing to record.
    """
    completed = _run("setup", "codex", "--print")

    assert completed.returncode == 0, completed.stderr
    assert "SPELLING PENDING E02" in completed.stdout
    assert f"http://127.0.0.1:{DEFAULT_PORT}/hooks/codex" in completed.stdout
    body = [
        line
        for line in completed.stdout.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert not body, f"the codex snippet claims a spelling: {body}"


def _http_hooks(entries: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [hook for entry in entries for hook in entry["hooks"]]
