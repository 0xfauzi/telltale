"""tests/integration/ is the only place a test may live, and this is the enforcement.

Why by construction rather than by convention: the owner's rule is that Telltale is
verified by running the real system, so a unit test that stubs the store, the receiver
or a provider would be a test of the stub. A directory is the cheapest boundary that a
reader and a hook can both see.

TWO hooks, because one of them cannot fire on the command everybody actually runs.
[tool.pytest.ini_options] sets `testpaths = ["tests/integration"]`, so a bare `uv run
pytest` never descends into tests/ at all and `pytest_collect_file` is never called
for a file outside this directory. Measured: with a stray tests/test_probe.py present,
the collect hook alone lets the run finish green. `pytest_configure` runs once per
session whatever the collection roots are, so the filesystem scan is what makes the
default command refuse. The collect hook stays for the explicit invocations
(`pytest tests/`), where it names the file at the moment pytest opens it.

The third copy of this rule is the tests-live-under-integration pre-commit hook, the
one that runs on a server where --no-verify cannot reach.

The fixtures below are the other half of the same rule. Every one of them hands out a
piece of the real system: a real Store with its writer thread, a real Receiver bound to
a real port, and the recorded bytes of a real Claude Code session posted over HTTP to
the routes they were recorded on. The only things monkeypatched are HOME and
TELLTALE_HOME, which are the process environment rather than anything of Telltale's,
and the only Telltale internals a test may reach for are `Store.pause_writer` and
`Store.resume_writer`, which say TEST-ONLY in their own docstrings.
"""

from __future__ import annotations

import http.client
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from telltale.receiver import (
    Receiver,
    _drain,
    _post,
    _replay_records,
    _with_capture,
)
from telltale.sanitize import Ctx
from telltale.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_INTEGRATION_ROOT = Path(__file__).resolve().parent
_TESTS_ROOT = _INTEGRATION_ROOT.parent
_REPO_ROOT = _TESTS_ROOT.parent

# The recorded sessions E01 captured from Claude Code 2.1.257, and the four files each
# one holds. S6 has stream.jsonl only, which is why every reader here skips what is
# absent rather than requiring four files.
FIXTURES = _REPO_ROOT / "fixtures" / "sources" / "claude" / "2.1.257"
SCENARIOS = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")
FIXTURE_FILES = ("otel_logs.jsonl", "otel_metrics.jsonl", "hooks.jsonl", "stream.jsonl")

# What experiments/E01/sanitize_fixture.py put in place of the two machine paths before
# the fixtures were committed. A replay substitutes them back (see `replay`), because a
# path that is not absolute is a path the sanitizer cannot decide about, and the whole
# question a privacy test asks is what the sanitizer decides.
REPO_PLACEHOLDER = "<repo>"
HOME_PLACEHOLDER = "<home>"


def _is_stray(path: Path) -> bool:
    """True for a pytest-collectable test file that sits outside tests/integration/."""
    return not path.resolve().is_relative_to(_INTEGRATION_ROOT)


def _stray_test_files() -> list[Path]:
    # Both of pytest's default python_files patterns, so the scan and the pre-commit
    # hook answer the same question about the same set of files.
    candidates = [*_TESTS_ROOT.rglob("test_*.py"), *_TESTS_ROOT.rglob("*_test.py")]
    return sorted({path for path in candidates if _is_stray(path)})


def _refuse(paths: list[Path], root: Path) -> None:
    listed = ", ".join(str(path.relative_to(root)) for path in paths)
    raise pytest.UsageError(
        f"tests live under tests/integration/ only; move or delete: {listed}"
    )


def pytest_configure(config: pytest.Config) -> None:
    stray = _stray_test_files()
    if stray:
        _refuse(stray, Path(config.rootpath))


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> None:
    if _is_stray(file_path):
        _refuse([file_path], Path(parent.config.rootpath))
    return None


# -- the running system ---------------------------------------------------------------


@dataclass(frozen=True)
class Live:
    """A started Receiver, the store behind it, and the port it actually bound."""

    receiver: Receiver
    store: Store
    port: int

    def post(self, route: str, body: bytes, capture: str | None = None) -> int:
        """One POST. Returns the status, which is always 200 (spec 5.2)."""
        path = _with_capture(route, capture) if capture else route
        return _post(self.port, path, body)

    def timed_post(
        self, route: str, body: bytes, capture: str | None = None
    ) -> tuple[int, float]:
        """(status, milliseconds). The wall time an agent's hook would have waited."""
        started = time.perf_counter()
        status = self.post(route, body, capture)
        return status, (time.perf_counter() - started) * 1000.0

    def healthz(self) -> tuple[int, dict[str, Any]]:
        """(status, body) from GET /healthz. 503 is the receiver saying it is not
        well, and it is the only place that says so."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request("GET", "/healthz")
            response = connection.getresponse()
            return response.status, dict(json.loads(response.read()))
        finally:
            connection.close()

    def drain(self) -> dict[str, Any]:
        """Wait for the writer to empty its queue. The receiver's own drain loop."""
        return _drain(self.port)


@pytest.fixture
def telltale_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A temporary $TELLTALE_HOME and a temporary $HOME, and proof nothing wrote home.

    The listing of the fake HOME is compared before and after every test that asks for
    this fixture. os.environ is the process environment rather than anything of
    Telltale's, which is why setting it is not the monkeypatching the house rules
    refuse: no Telltale function is replaced, and every path decision under test is the
    one the real code makes.
    """
    home = tmp_path / "home"
    telltale = tmp_path / "telltale-home"
    home.mkdir()
    telltale.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TELLTALE_HOME", str(telltale))
    before = _tree(home)
    yield telltale
    assert _tree(home) == before, f"the test wrote under HOME {home}"


def _tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


@pytest.fixture
def store(telltale_home: Path) -> Iterator[Store]:
    """The real store, in the temporary home. Closing it twice is a no-op, so a test
    that closes it to read the file bytes still gets a clean teardown."""
    opened = Store(telltale_home / "telltale.db").open()
    yield opened
    opened.close()


@pytest.fixture
def receiver(store: Store) -> Iterator[Callable[..., Live]]:
    """Start a Receiver on a free port; every one started is stopped afterwards.

    A factory rather than one instance, because the content level is fixed at
    construction: a test that compares level 1 with level 0 needs two of them, and the
    fail-open test needs one over a store whose queue holds two items.
    """
    started: list[Receiver] = []

    def start(
        target: Store | None = None, level: int = 1, ctx: Ctx | None = None
    ) -> Live:
        into = target if target is not None else store
        live = Receiver(
            into,
            level=level,
            ctx_for_capture=None if ctx is None else (lambda _capture: ctx),
            provider="claude",
        )
        port = live.start()
        started.append(live)
        return Live(receiver=live, store=into, port=port)

    yield start
    for live in started:
        live.stop()


@dataclass(frozen=True)
class Replayed:
    """What one replayed scenario was, so a test can assert about its input too."""

    scenario: str
    capture: str
    level: int
    repo_root: Path
    home: Path
    posted: int
    statuses: dict[int, int]
    source: bytes
    health: dict[str, Any]

    def occurrences(self, marker: bytes) -> int:
        """How often a marker appears in the bytes that were POSTed.

        An absence assertion about the database means nothing unless the thing was in
        the input, and this is what lets a test say so rather than assume it.
        """
        return self.source.count(marker)


@pytest.fixture
def replay(receiver: Callable[..., Live], tmp_path: Path) -> Callable[..., Replayed]:
    """Post one recorded scenario through a real receiver, at a content level.

    The two machine paths the fixture sanitizer replaced are substituted back first,
    with the temporary repository and the temporary HOME of this test. That is the only
    change made to the recorded bytes, and it is what makes the replay faithful: on the
    wire Claude Code sends absolute paths, `<repo>/pkg/calc.py` is not one, and a
    sanitizer handed a relative path cannot answer the question the fixture is asking
    (inside the repository, or outside it and hashed).
    """

    def run(scenario: str, level: int = 1, capture: str | None = None) -> Replayed:
        repo_root = tmp_path / "repo"
        home = tmp_path / "home"
        repo_root.mkdir(exist_ok=True)
        prepared = _materialise(
            scenario, tmp_path / f"in-{scenario}-{level}", repo_root, home
        )
        live = receiver(level=level, ctx=Ctx(repo_root=repo_root, home=home))
        target = capture or f"{scenario}-L{level}"
        statuses: dict[int, int] = {}
        # The receiver's own reader, so the test posts what `--replay` posts: each
        # sink record to the route it was recorded on, in ingest order, then the
        # stream lines.
        records = _replay_records(prepared)
        for route, body in records:
            status = live.post(route, body, capture=target)
            statuses[status] = statuses.get(status, 0) + 1
        health = live.drain()
        return Replayed(
            scenario=scenario,
            capture=target,
            level=level,
            repo_root=repo_root,
            home=home,
            posted=len(records),
            statuses=statuses,
            source=b"".join(path.read_bytes() for path in sorted(prepared.iterdir())),
            health=health,
        )

    return run


def _materialise(scenario: str, out: Path, repo_root: Path, home: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    for name in FIXTURE_FILES:
        source = FIXTURES / scenario / name
        if not source.exists():
            continue
        text = source.read_text(encoding="utf-8")
        text = text.replace(REPO_PLACEHOLDER, str(repo_root))
        text = text.replace(HOME_PLACEHOLDER, str(home))
        (out / name).write_text(text, encoding="utf-8")
    return out


@pytest.fixture
def db_after_close() -> Callable[[Store], bytes]:
    """Close the store and read every byte SQLite left on disk.

    The database FILE, not a query: a value that a SELECT no longer returns can still
    be sitting in a freelist page, in the write-ahead log or in the shared-memory index,
    and the question a privacy test asks is what is on the disk of the machine.
    """

    def read(target: Store) -> bytes:
        target.close()
        return b"".join(
            path.read_bytes() for path in _db_files(target.path) if path.exists()
        )

    return read


def _db_files(path: Path) -> list[Path]:
    return [path, Path(f"{path}-wal"), Path(f"{path}-shm")]
