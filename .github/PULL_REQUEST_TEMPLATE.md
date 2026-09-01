<!--
The body of a pull request is the report. Paste docs/log/<TASK>.md here, or write the same
five headings in the same order. The order is fixed so that the two sections that matter
most to somebody who was not there, what broke and what is still unsure, cannot be buried.

Before opening this, the gates in CONTRIBUTING.md should be green locally:
uv sync, uv run pytest -m integration, uv run mypy ., uv run ruff check . ,
uv run deptry . , uv run pre-commit run --all-files.

No emoji. No em dashes: a pre-commit hook blocks them, so use a hyphen, a colon or a full
stop.
-->

## OUTCOME

What now works, with the measurements that show it. Real commands, real output, real
numbers. Never an estimate where a measurement was possible.

## WHAT WAS WRONG

What the brief or the existing code got wrong when it met the real system, and what
replaced it. If a claim in an earlier report at `docs/log/` turned out to be wrong, name
the file: that report stays as it is, and this is the correction.

## WHAT CHANGED

The files, and why each one changed. Mechanism, not outcome.

## UNSURE OR NOT DONE

What is not verified, what was skipped, and what would settle it. "Needs measuring" is an
acceptable answer here. A guessed number is not.

## NEXT

What the following task can now assume, and what it must not.

---

- [ ] `uv run pre-commit run --all-files` is green
- [ ] `uv run pytest -m integration` is green
- [ ] `uv run mypy .` is clean
- [ ] Every new test fails against the un-fixed code
- [ ] No derived number is written outside `store.py`, and no claim class is upgraded
- [ ] `docs/log/<TASK>.md` is included in this change
