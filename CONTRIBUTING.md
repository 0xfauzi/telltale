# Contributing

Telltale is a recorder. Its failure mode is not a crash, it is a confident number that the
data does not support, so the rules below are mostly about not letting that happen. Most of
them are enforced by a hook or a CI check rather than by review; where a rule cannot be
enforced, it says so.

## The gates

Every one of these is run by CI on pull requests and on `main`, where `git commit
--no-verify` cannot reach them. Run them locally first.

| Command | What it checks |
|---|---|
| `uv sync` | Install or refresh the environment. Installs the `dev` group and no extras. |
| `uv run pytest -m integration` | The suite. Every test is an integration test and there is one test directory. |
| `uv run mypy .` | Strict typing, the same configuration CI runs. Never weaken the configuration to make code pass. |
| `uv run ruff check --fix . && uv run ruff format .` | Lint then format, in that order. `--fix` rewrites code, and formatting the result is the only order that converges in one pass. |
| `uv run deptry .` | Dependency hygiene. One package, one dependency list, one run. |
| `uv run pre-commit run --all-files` | Everything fast. Hooks auto-fix, so re-stage and retry on the first failure. |

`uv run pytest -m live` drives a real agent binary and spends real tokens. CI never runs
it. Run it by hand, and only when the change touches capture itself.

## Style rules that a hook enforces

- **No emoji, anywhere.** Code, comments, commit messages, documentation, badges, issue
  templates, the logo.
- **No em dashes, anywhere.** The `no-em-dash` pre-commit hook greps for U+2014 in every
  text file. Use a hyphen, a colon or a full stop.
- **Python 3.12, uv only, `pyproject.toml` only.** No `requirements.txt`, no `setup.py`,
  no `pip install` in any instruction.
- **Tests live in `tests/integration/` and nowhere else.** `tests/integration/conftest.py`
  refuses to run while a test file exists anywhere else, and the
  `tests-live-under-integration` hook refuses to commit one. Verification is by running the
  real system: a test that stubs the store, the receiver or a provider is a test of the
  stub.
- **The collector imports nothing outside the standard library.** `dependencies` in
  `pyproject.toml` is empty and stays empty. The model stack may be imported in exactly one
  module, `src/telltale/forecast/timesfm.py`, which the `forecast-isolation` hook enforces.
- **Derived rows are written through `store.py` alone.** The
  `derived-writes-only-in-store` hook keeps the INSERT statements for `activities`,
  `evidence`, `series_snapshots` and `forecast_runs` in that one file, so the CHECK
  constraints are the only way in.

`AGENTS.md` holds the full list of architecture invariants. Violating one of them is a bug,
not a style choice.

## Rules a hook cannot enforce

- **A test that passes against the un-fixed code is not a test.** Break the fix, watch the
  test fail, restore it.
- **Never guess a number.** Measure it, or write "needs measuring" and say what would
  measure it.
- **Unknown stays unknown.** A missing value is `None` through the model, the series and
  the report. Never zero-fill: absence is not zero, and "0 compactions" and "compaction was
  not observable on this surface" are different statements.
- **A claim class is never upgraded.** A report may narrow a claim or drop it. Nothing may
  present a number as stronger evidence than the layer that produced it.
- **Before writing that something is missing, unspecified, impossible or out of scope,
  search for it and cite the search.**

## Dependencies

The dependency set is governed by `uv.lock`, which is a reviewed change with its own gates.
Dependabot is configured for GitHub Actions only, on a monthly schedule with a 7 day
cooldown, because the workflows pin actions to commit SHAs and a pin never updates itself.

Do not adopt a Python release the month it ships. A compromised or broken release is
usually caught and yanked within weeks, and this repository has no need to be the thing
that finds out first. When a package genuinely needs bumping, let it cool for 30 days:

```console
$ uv lock --upgrade-package X --exclude-newer-package 'X=30 days'
```

## Changing a rule

New rule or constraint? Enforce it first: a schema, a validator, a pre-commit hook or a CI
check. `AGENTS.md` takes only what cannot be enforced, and when it grows the fix is to
convert lines into hooks and cut them.

## Working from a brief

Work on Telltale is dispatched as briefs. The format is section 7.4 of
[`docs/design/02-protocol.md`](docs/design/02-protocol.md): a brief names what a task owns
and what it only reads, injects the numbers the task needs rather than referencing them,
gives the mechanism rather than the outcome for each step, lists the exact verification
commands with the assertion each makes, and states the condition under which the task must
stop and refuse rather than guess.

Every task ends with a report at `docs/log/<TASK>.md` in a fixed order: OUTCOME, WHAT WAS
WRONG, WHAT CHANGED, UNSURE OR NOT DONE, NEXT, then an explanation written for the owner. A
report is immutable once merged. When a later task finds a claim in one to be wrong, it says
so in its own report and names the file it corrects. The wrong claim stays where it is,
which is what makes the correction legible.

## Pull requests

The pull request body is the report. Open it against `main`, keep the branch named
`task/<TASK>`, and make sure CI is green before asking for a merge.
