# Wave 4 gate report (spec v0.4: controlled repository interaction), in progress

Owner approval to dispatch wave 4: 2026-09-02 night ("APPROVED for all 3": E08b, the
W4-T1 and W4-T2 dispatch with the E09/E10/E12 session requests to follow with pilot
numbers, the backup deletion). This file grows as tasks merge; every number is measured
and says where.

## What was merged

| PR | Task | What landed |
|---|---|---|
| #38 | W4-T1 | `telltale experiment probe <spec.json>`: fixed read-only probes with an answer key written before the run, N repetitions each in its own detached worktree at one base_sha, precision and recall per repetition, the score posted as an external.outcome (no new observation type), the answer text read and dropped; `telltale experiment intervention <spec.json>`: the same probes at two commits, paired by probe, the fingerprint assertion inverted (the arms must differ in NO field); `experiments_probe.py`, `report_probe.py`, `cli_probe.py`, 12 tests |
| #40 | W4-T2 | `telltale profile <repo_id> [--path PREFIX] [--by week\|subsystem\|path] [--json] [--include-backfill]`: 22 observed distributions per group with the sample composition beside them, one comparative number per row (the ratio of the group's median to the median of the cohort OUTSIDE the group, refused below ten outside members, per metric), backfill captures in groups of their own, the four words maintainability, quality, difficulty and score refused by the renderer; `profile.py`, `report_profile.py`, 18 tests |

Both amendments files (docs/design/amendments/W4-T1.md, W4-T2.md) are folded into
01-design.md at the wave exit.

## Measurements

- W4-T1, re-run by the orchestrator from the test helpers' repository under a
  temporary home (2026-09-03 00:05): P-exact scored 1.000/1.000 pass on all three
  repetitions and P-narrow 0.500/1.000 fail on all three, the hand-computed numbers of
  docs/log/W4-T1.md; the intervention printed identical fingerprint ids for both arms,
  precision HL shift -0.500, Cliff's delta -1, exact p 0.100, recall 0/0/1.000, Reads
  +1/+1/0.100; a spec whose arms differ in base_sha and a flag was refused naming both
  and created no database; the store bytes held the answer sentence 0 times, the path
  `src/alpha.py` 24 times and `precision:0.500` 6 times. The implementer measured 2.19 s
  for 6 captures of the probe suite, 2.25 s for the intervention, and 0.080 ms per
  scorer call over this repository's 427 tracked paths.
- W4-T2, re-run by the orchestrator on a fresh `.backup` copy of the home store
  (2026-09-03 00:30, 1,541,439,488 bytes): 49 captures carry this repository's id.
  `--by week` is one group, 2026-W36, refused for every metric with "10 of 43 captures
  have an unknown cohort key" (the `telltale run -- bash` smoke captures have provider
  generic and no model). `--by subsystem` places docs 17, experiments 7, fixtures 1, src
  21 and tests 13 captures with the ratio column filled (12 to 32 cohort members outside
  each group). `--path src/telltale/forecast` selects 3 captures whose median
  fresh_input_tokens is 160 tokens, 0.714 times the median of the 30 outside.
  `--include-backfill --by subsystem` adds eight `(backfill)` groups with no cohort.
  Each invocation took 1.18 to 1.28 s. The four refused words appear 0 times in all
  five outputs. Break-and-restore: `>= 2` in place of COHORT_MIN fails
  `test_a_cohort_with_fewer_than_ten_outside_the_group_is_refused` and
  `test_the_command_prints_one_table_per_metric_with_the_cohort_column`; restored, the
  suite is 247 passed.

## Exit criterion (spec 21 v0.4)

"Determine which repository claims are supportable and which remain workload analytics.
Do not create a universal maintainability score." Not yet assessable: the two commands
exist and are verified on the scripted agent and on this repository's own captures, and
no probe has yet been answered by a real agent. What can be said today: the profile's
one comparative number is cohort-gated on both sides (the group must be homogeneous on
the four cohort keys, and at least ten members outside it must have measured the
metric), and a renderer that would print a score raises. The E09 sessions decide the
rest.

## Constraints found, decisions to take

- The intervention runner refuses an AGENTS.md rewrite as an intervention: base_sha
  arms must differ in no fingerprint field, and instruction_hashes is one. The plan's
  E10 (an AGENTS.md rewrite on a branch) therefore needs a runner amendment first
  (a declared `instructions` factor whose assertion expects instruction_hashes to differ
  and nothing else), or E10 becomes a scoped code refactor with keys valid at both
  commits, which is spec 14.4 as written. Recommendation in the session request below.
- The evidence table had no index on capture_id: each per-capture evidence read was a
  scan, 22 ms each and 0.75 s of every profile on the owner's store (W4-T2 measured it
  and memoised the reads instead). W3-T3 had dropped an unread index on observations
  for its writer cost, so this one was measured before it was added (W4-F1, 2026-09-03,
  a `.backup` copy with 129089 evidence rows, arms interleaved): the write path begins
  with `DELETE FROM evidence WHERE capture_id = ?`, so the index makes the writer
  faster, not slower: 16.4 ms against 0.82 ms per delete-and-reinsert of one capture,
  `profile --by subsystem` 1.12 s against 0.61 s, the CLI rebuild of 20 captures
  12.09 s against 10.64 s. Added in schema.py; every store gains it at its next open
  (0.078 s). docs/log/W4-F1.md has the table.
- A group of one capture gets a ratio (`fixtures`, 1.643x). No floor on the group's own
  size exists; n is printed and the scaled MAD is "-". Whether a floor belongs is the
  owner's call.
- `briefs` is a subsystem group only with `--include-backfill`: the orchestrator's own
  sessions reach the store through the transcript importer, not the launcher.
- Probe precision is an upper bound: a wrongly named symbol is counted nowhere (the
  repository enumerates paths, not the symbols an answer could invent). Reports say so.
- `n_needed = 0` at a spread of 0 (stats.py, shared with the environment runner), and
  the 37-line between-arm warning tail: carried for whichever task owns stats output.

- E12's key cross-check (experiments/E12/make_key.py, ten facts per session derived
  from the raw stream-json of the six most recent build sessions) disagreed with
  `telltale show` on 11 of 36 comparable cells, all of them the two pytest questions:
  the streams hold 52 pytest executions and Telltale's agent_test_runs sums to 14
  (W4-T2/1 10 against 4, W4-T1/1 11 against 1, W3-E08b/1 6 against 3, W3-V/1 11
  against 0, W3-E08/1 3 against 2, W3-T4/1 11 against 4). Cause, read in
  commands.classify: "first-segment-with-a-known-category-wins" and no newline
  separator, so `uv sync | tail; uv run pytest`, `ruff format && ruff check && mypy &&
  pytest` and a pytest line after a heredoc script in one Bash call are recorded as
  something other than a test run. The other four comparable facts (files edited,
  compactions, subagents, output tokens) agree on all six. W4-T3 (briefs/W4-T3.md)
  fixes the classifier; its version changes, so the home store is rebuilt after the
  merge. Dispatched 2026-09-03 as attempt 2 (attempt 1 was dispatched seconds before
  its brief was committed and was stopped by the orchestrator with no work done).

## Sessions requested

- E09: 6 probes on this repository, keys in experiments/E09/answer_keys.json (written
  from the code, verified by grep, committed before any run), 3 repetitions each, pilot
  first: experiments/E09/pilot.json (P1, 3 sonnet sessions) then spec.json (18). Both
  pass the runner's checker. Brief briefs/W4-E09.md with the pre-registered rule and the
  STOP bounds (5 minutes or 200,000 tokens per session; stop if the runner cannot read
  the result text). Cost needs measuring: the only reference on this machine is E05r2's
  fix-the-test sessions at 173,496 to 173,713 tokens and 13,600 to 15,132 ms each.
- E10 after E09's numbers, on the two least legible probes, 5 per arm.
- E12 (H1, blinded diagnosis): protocol, key and brief are written
  (experiments/E12/protocol.md, key.json, briefs/W4-E12.md). Two Opus reviewer arms
  over six sessions: pilot 2 sessions, full run 12. Runs after W4-T3 merges, the
  store is rebuilt and the key cross-check is re-run.
