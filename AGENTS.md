# Telltale

A local flight recorder for coding-agent sessions, plus a forecasting laboratory that is
kept at arm's length from it. The specification is `docs/spec/telltale-architecture.md`
(the `.docx` beside it is the authority); the digest of verified provider facts, the design
and the orchestration protocol are `docs/design/00-digest.md`, `01-design.md` and
`02-protocol.md`. This file holds the invariants and the commands. Enforceable rules live
in hooks and validators, not in prose.

## Commands

- `uv sync` - install or refresh the environment. Installs the `dev` group and no extras.
- `uv run pytest -m integration` - the suite. Every test is an integration test and there
  is one test directory; see the invariants below.
- `uv run pytest -m live` - the tests that drive a real agent binary and spend real tokens.
  Never run by CI. Run these by hand, and only when the work touches capture itself.
- `uv run mypy .` - strict typing, the same config CI runs. Never weaken the config to make
  code pass.
- `uv run ruff check --fix . && uv run ruff format .` - lint then format, in that order:
  `--fix` rewrites code, and formatting the result is the only order that converges in one
  pass.
- `uv run deptry .` - dependency hygiene. One package, one dependency list, one run.
- `pre-commit run --all-files` - everything fast. Hooks auto-fix, so re-stage and retry on
  the first failure.

## Architecture invariants (violating any is a bug, not a style choice)

1. **Four durable shapes and two signatures** (design 6.1). `Observation` is the only thing
   a provider module produces, and it is immutable and append-only. `Activity` is the only
   thing measures read, and it is rebuildable from observations. `Evidence` is the only way
   a derived, comparative, associative or predictive number is written or returned.
   `Series` is the only input to a forecaster. Every provider module exposes
   `parse(surface, raw, ctx) -> list[Observation]` and a `CAPABILITIES` constant; every
   forecaster exposes `forecast(context, horizon, future_covariates=None) -> ForecastResult`,
   and the baselines are pure functions behind that same signature.
2. **Integration tests only, in one directory.** `tests/integration/` is the whole test
   tree. `tests/integration/conftest.py` refuses to run while a test file exists anywhere
   else, and the `tests-live-under-integration` hook refuses to commit one. Verification is
   by running the real system; a test that stubs the store, the receiver or a provider is a
   test of the stub.
3. **The collector is standard library only.** `dependencies` in pyproject.toml is empty
   and stays empty. numpy, torch, timesfm and pyarrow live behind extras, and the
   `forecast-isolation` hook allows the model stack to be imported in exactly one module,
   `src/telltale/forecast/timesfm.py`. The recorder runs beside the agent it records: an
   import it adds is a cost somebody else's session pays.
4. **Every derived number is an Evidence, and it is written through store.py.** Evidence
   carries its claim class, its coverage, its non-empty source list, its reducer version,
   its assumptions and its warnings. The `derived-writes-only-in-store` hook keeps the
   INSERT statements for `activities`, `evidence`, `series_snapshots` and `forecast_runs`
   in `store.py`, so the CHECK constraints and the one constructor are the only way in.
5. **Unknown stays unknown. Never zero-fill.** A missing value is `None` through the model,
   the series and the report. A gap in a series is excluded or refuses the build by a named
   missingness policy; no policy imputes. Absence is not zero, and "0 compactions" and
   "compaction was not observable on this surface" are different statements that the
   coverage field keeps apart.
6. **A claim class is never upgraded.** derived, comparative, associative, predictive; never
   observed for something computed, and never causal. A report may narrow a claim or drop
   it. Nothing anywhere may present a number as stronger evidence than the layer that
   produced it, and the words cause, impact and would are refused in forecast output.
7. **No global configuration is ever edited.** `~/.claude/settings.json`, `~/.codex/config.toml`
   and everything else outside this repository and `$TELLTALE_HOME` are read-only to
   Telltale. `telltale setup` PRINTS the snippet the owner may paste; `--apply` prints a
   refusal that names this decision.
8. **Capture is launcher-only and fails open.** Configuration reaches an agent through the
   launched process (its argv and its environment), never through a file on disk that
   outlives the capture. Any Telltale exception during a capture becomes a diagnostic row
   and never changes the child's behaviour, its output bytes or its exit code; every
   receiver endpoint answers 200 whatever happened, and `/healthz` alone tells the truth.

## Style

- Python 3.12, uv only, pyproject.toml only. No emoji anywhere. No em dashes anywhere: a
  pre-commit hook blocks them, so use a hyphen, a colon or a full stop.
- Deep modules, thin interfaces. Match the idioms of the module you are editing.
- Comments state constraints the code cannot show, and nothing else.
- Never guess a number. Measure it, or write "needs measuring" and say what would measure
  it.

## Constraint intake

New rule or constraint? Enforce it first: a schema, a validator, a pre-commit hook or a CI
check. This file takes only what cannot be enforced. When it grows, convert lines into
hooks and cut them.

## Verification (none of these can be enforced by a hook)

- **A test that passes against the un-fixed code is not a test.** Break the fix, watch the
  test fail, restore it.
- **Before writing that something is missing, unspecified, impossible or out of scope,
  search for it and cite the search.** Every such claim made in deckgen so far has been
  wrong.
- **Absence is not zero and duplicate is not one.** `set`, `dict`, `.get()`, `continue` and
  `return {}` over untrusted structure all convert "I cannot tell" into a confident answer.
  Prove cardinality once, at the boundary, and refuse there. In a recorder this is the
  defect class, not a defect class.

## Where things live

Two paths that questions about this code keep asking for, named file by file so nobody
has to trace them.

- `telltale show <capture>`: `src/telltale/cli.py` parses the command and dispatches it;
  `src/telltale/cli_common.py` opens the store and resolves the capture id;
  `src/telltale/measures.py` builds the summary from the activities and evidence that
  `src/telltale/store.py` and `src/telltale/store_reads.py` read out of the database;
  `src/telltale/report.py` renders it.
- What may be stored and how a kept string is scrubbed: `src/telltale/allowlist.py` (the
  one table, and the Claude entries), `src/telltale/allowlist_codex.py` (the Codex
  entries), `src/telltale/allowlist_telltale.py` (the entries for what Telltale writes
  itself), `src/telltale/sanitize.py` (the allowlist walk, path rewriting and the secret
  scrub), `src/telltale/commands.py` (a command line's normal form, scrubbed and bounded
  before it is stored).
