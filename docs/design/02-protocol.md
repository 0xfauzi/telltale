<!-- Verbatim copy of sections 4, 5 and 7 of the approved implementation plan (~/.claude/plans/i-need-you-to-fuzzy-journal.md), taken 2026-09-01 by W0-T1. Edit the plan, not this file. -->

## 4. Owner decisions (answered 2026-09-01)

| Decision | Answer | Consequence |
|---|---|---|
| Kstrl integration | Defer all Kstrl work | No bridge, no kstrl edits. The correlations/outcomes API is still built because the experiment runner is an orchestrator (task_id, attempt) and the repo observer supplies accepted changes. |
| Experiment budget | Ask before every experiment run; spend nothing specifically; piggyback on day-to-day Claude and Codex usage | Every brief that would start agent sessions carries STOP: do not launch sessions. Three free data sources are built in: the build records itself (implementers run under `telltale run` from wave 1), `telltale setup --print` gives the owner the snippet for day-to-day capture, and wave 2 backfills the owner's existing transcripts and rollouts through the same sanitizer. |
| GitHub + CI | Private 0xfauzi/telltale, CI on PRs and main | Bootstrap creates the repo with `gh`; one lean job. |
| Global config | Launcher-only; never edit ~/.claude or ~/.codex | `telltale setup` prints; it never writes. |
| License | MIT | LICENSE from the first commit; README "Licensing" states MIT for the code and the TimesFM-3 weights non-commercial notice separately. |
| Logo concept | Sail with telltales | W0-T6 builds the mark, dark variant, wordmark and 512 px PNG. |
| Wave 0 fixture sessions | Approved: E01 (about 8 short sonnet runs) and E02 (about 6 codex runs) | Pilot first in each; the 5 minute / 200k token STOP rule stands. |
| Backfill of existing transcripts and rollouts | Yes, after a dry run | W2-T2 ships `--dry-run`; the orchestrator reports the counts before the real import. |

## 5. Standing owner gates

1. Wave 0 fixture capture: approved on 2026-09-01 (E01 about 8 one-prompt `claude -p
   --model sonnet` sessions; E02 about 6 `codex exec` sessions). The pilot measures the
   first one and the STOP rules in the briefs stand.
5. Backfill import (wave 2): dry-run counts are reported to the owner before the real
   import runs.
2. Every experiment that starts agent sessions (E04, E05, E06, E09, E10, E12): the
   orchestrator presents the pilot measurement, projected session count and wall time,
   what the result can and cannot support, and a recommendation.
3. Each wave exit: gate report (criteria, measurements, supportable claims, next decision).
4. Wave 5 entry: TimesFM-3 license acknowledgment.

## 7. Orchestration protocol

### 7.1 Roles

- Orchestrator (this session): writes briefs into `briefs/<TASK>.md`, dispatches, verifies
  by running the system, merges, writes gate reports to the owner, asks at the standing
  gates only.
- Implementers (Opus 5): one brief each, in a git worktree; no owner contact. End with a
  green gate set and a report, or a BLOCKED report naming the missing decision.
- Verifier (Opus 5, fresh context, after each wave): re-runs every VERIFY line of the
  wave's briefs from a clean checkout and tries to break each report's claims. Never
  fixes; reports.

### 7.2 Dispatch mechanics

Wave 0: Agent tool with `isolation: worktree`, one task per agent, up to 4 in parallel.
Waves 1+: implementers run as separate processes through the launcher, so the build
records itself (owner's piggyback decision):

```
cd ../wt-<TASK> && telltale run --provider claude --task-id <TASK> --attempt <N> \
  --experiment build -- claude -p --model opus --permission-mode bypassPermissions \
  --max-turns 400 --output-format stream-json --verbose "$(cat briefs/<TASK>.md)" \
  > session.jsonl
```

detached, from a worktree made with `git worktree add -b task/<TASK> ../wt-<TASK> main`.
Measured in wave 1: macOS ships no `setsid`, so `nohup setsid ... &` never starts; the
form that outlives the orchestrator's tool call is a Python
`subprocess.Popen(argv, cwd=worktree, start_new_session=True, stdin=DEVNULL,
stdout=<file>, stderr=<file>)`. The launcher supplies `--session-id`. A session ended by
the subscription's session limit prints a result with subtype success and is_error true
and the text "You've hit your session limit"; it is resumed as the next attempt with
`claude -p --resume <session_id> "<continuation prompt>"` through the same launcher,
which leaves the id alone when `--resume` is present. Harness-caused retries are
recorded as such in the gate report so the build's own attempt series does not read
them as task retries. Falls back to the Agent tool if the launcher misbehaves; the fallback is recorded as
a diagnostic finding against the launcher. Trap for E01: verify that `claude -p` starts
from inside a Claude Code Bash tool (the `CLAUDECODE` variable may need unsetting).

### 7.3 Merge protocol (every task)

1. Implementer pushes `task/<TASK>` and opens a PR whose body is the report.
2. Orchestrator runs the brief's VERIFY lines on the branch (not only reads the report),
   plus `uv run pytest -m integration`, `uv run mypy .`, `uv run ruff check .`,
   `uv run deptry .`, `pre-commit run --all-files`.
3. CI green. Squash-merge. Remove the worktree.
4. `docs/log/<TASK>.md` is part of the PR and immutable afterwards.
5. (Added 2026-09-02, after W3-T1.) The orchestrator records the merge in the store the
   build runs in: `telltale outcome --kind mechanical_verification --status pass
   --task-id <TASK> --attempt <N>` after the VERIFY lines and gates are green, and
   `telltale outcome --kind merge_decision --status merged --task-id <TASK> --attempt
   <N>` after the squash merge, where N is the attempt whose session pushed the merged
   branch. An attempt that died (rate limit, killed runner) gets no outcome: unknown
   stays unknown. A PR closed without merging gets `merge_decision rejected`, a fix
   after merge gets `revert_or_repair`. The 16 merged attempts up to W3-T1 were
   recorded on 2026-09-02 from the merged PR list.

### 7.4 Brief format

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

House rules every brief inherits (stated once here, repeated in AGENTS.md): no emoji, no
em dashes anywhere; stdlib only outside `forecast/timesfm.py`; every derived number is an
Evidence; unknown stays None; never zero-fill; never guess a number (say "needs
measuring" and measure); comments state constraints the code cannot show; a test that
passes against the un-fixed code is not a test; before writing "unsupported" or "not
possible", search and cite the search.

### 7.5 Experiment write-up template (docs/experiments/E##.md)

```
# E##: <the question in one sentence>
Why this matters (30 seconds): the claim that depends on the answer.
Decision rule, written before the run: outcome A means ..., outcome B means ...
What we did: exact commands, environment fingerprint ids, sample sizes, wall time.
What we measured: tables of raw numbers with units; each traceable to experiments/E##/out/.
What it means: which branch of the rule the numbers took.
What we rejected: the alternative reading and the evidence against it.
How sure we are: per claim: measured | reasoned | judgement | unknown.
What downstream may now say: claim class, cohort, coverage caveats, in one paragraph.
```
