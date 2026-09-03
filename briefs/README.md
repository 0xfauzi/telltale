# Task briefs

One file per dispatched task, `<TASK>.md`, written by the orchestrator before the task
starts. A brief is the whole context an implementer gets, so a number it needs is INJECTED
into it rather than referenced: an implementer that has to go and look a value up is an
implementer that will guess it.

Format, from the orchestration protocol (`docs/design/02-protocol.md`, section 7.4):

```
Task <id>: one sentence stating the work.
OWNS: paths the task may create or modify.  READS: interfaces it consumes, never edits.
READ: concrete files; the numbers a step needs are INJECTED here, never referenced.
  Name the trap.
DO:
(1) path: the mechanism, not the outcome.
VERIFY: exact commands, with the assertion each makes in parentheses. Prefer running the
  real system over tests.
STOP if <the condition that means refuse rather than guess>.
REPORT: docs/log/<id>.md in the order OUTCOME / WHAT WAS WRONG / WHAT CHANGED / UNSURE OR
  NOT DONE / NEXT, plus EXPLAIN FOR THE OWNER in the Teacher register
  (~/.claude/output-styles/Teacher.md): idea before name, one real example with real
  values, how each number was measured, the answer rejected and why. Commit message as
  given.
```

House rules every brief inherits: no emoji, no em dashes anywhere; standard library only
outside `forecast/timesfm.py`; every derived number is an Evidence; unknown stays None;
never zero-fill; never guess a number (say "needs measuring" and measure it); comments
state constraints the code cannot show; a test that passes against the un-fixed code is not
a test; before writing "unsupported" or "not possible", search and cite the search.

House rule added 2026-09-03 after the W6-T4 incident: never kill a process by name
pattern (`pkill -f`, `killall`) on this machine; other sessions run beside yours and their
command lines contain brief text. Kill by pid only.
