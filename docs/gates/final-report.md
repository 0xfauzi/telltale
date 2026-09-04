# Final report to the owner (2026-09-04)

Written by the orchestrator at the wave 6 gate. Every number below is measured and
names its source; every label is the one the gate report or the experiment file gives.
The build plan is docs/design/02-protocol.md; the per-wave verdicts are
docs/gates/wave-0.md to wave-6.md.

## What Telltale can say now, by claim class

- **Observed.** Per capture, what the provider surfaces delivered: model requests with
  token counts by type, tool calls with name, path or normalized command, exit status
  where a surface states it, subagent parent-child links, compaction events with pre and
  post tokens where a surface carries them, repository identity, dirty-tree and diff
  hashes, commits with a link confidence, the environment fingerprint. Coverage is
  recorded per capability per capture, so "0 compactions" and "compaction not
  observable" stay different statements. Claude Code 2.1.257 and 2.1.259 through six
  launcher surfaces, transcripts and imports; Codex 0.39.0 and 0.150.1 through exec JSON,
  rollouts and imports (doctor --matrix, wave 6).
- **Derived.** Every measure of spec 13 as an Evidence row with its sources: usage,
  verification cycles, edit turnover, exploration, context burden, stable-state work,
  delegation. A masked shell chain (`pytest | tail`) leaves the check's outcome unknown
  and says so; a tool error is kept beside it (W4-F3, W6-F2). Export and re-import of the
  whole store is byte-identical on observations (W6-T3).
- **Comparative.** Two captures side by side with both coverages; cohort percentiles only
  inside a stated cohort with n at least 10; repository work profiles over the change
  clock; between-arm statistics (Hodges-Lehmann shift, Cliff's delta, exact Mann-Whitney
  p, MDD at the measured n) from the experiment runners.
- **Associative.** The one-step candidate protocol prints the paired difference between a
  conditioned and an unconditioned forecast with the mandatory sentence that it is not
  the effect of merging. Not assessable on this repository yet (E11).
- **Predictive.** Rolling-origin backtests with four baselines and TimesFM-3, calibration,
  weighted quantile score, lead time, a chronology placebo, the four-label decision with
  its inequalities printed, scenario horizons with explicit future paths, and regime
  segmentation at policy interventions. Every run carries the weights license.

## What it cannot say

- Anything causal. The words cause, impact and would are refused in forecast output.
- Any quality or difficulty score. None exists; the spec forbids one (17.2).
- That TimesFM-3 adds anything over `rolling_median` on this data: it does not (E07,
  E08b), and no order-dependent label was ever earned because the chronology control
  failed on every row (E08).
- Anything about a repository other than this one and the E01 fixture repository, or
  about a model other than the ones the experiments ran.

## The hypotheses, with the labels the experiment files give

| Hypothesis | Label | Source |
|---|---|---|
| H1 summaries support diagnosis | Summary sufficient on 8 of 10 questions over six sessions per arm; insufficient on Q2 (the designed unknown of a masked test exit) and Q8 (no commit count in the summary). Token cost ratio summary to raw 1.647 at the median session in favour of the summary; wall ratio 0.776 against it. | E12, docs/gates/wave-4.md |
| H2 measures have a within-condition floor | The pilot was invalid (five refused sessions, one flag); the corrected run gives floors on one small task at n = 5 and demotes nothing. Not generalizable beyond that task. | E05 |
| H3 environment changes telemetry | Material effect of `--effort` on three token measures (cache_read_tokens, output_tokens, stable_state_tokens) at five against five; four measures moved and did not clear the floor, labelled "not resolved at n = 5". | E06 |
| H4 repository interventions change work | Recall 1.000 in all ten after and nine of ten before repetitions; every measure labelled material moved down; the precision prediction was falsified by a scoring artefact (the answer named AGENTS.md, which is not in the key). | E10 |
| H5 chronology improves forecasts | Not assessable on all 38 rows: the placebo made persistence better, so the control failed; the request clock's `output_tokens` alternates and carries no recency. | E08 |
| H6 TimesFM-3 beats baselines | Baseline sufficient on 37 of 38 rows under the amended rule that reads only true-order numbers; 1 not assessable. Best skill 0.0844 against the 0.10 the rule needs (E07). | E07, E08b |
| H7 agent semantics add predictive information | Not assessable: 0 origins common to the A, B and C variants on the change clock. | docs/gates/wave-3.md |
| H8 candidate features improve near-term forecasts | Not assessable: 0 windows on every target; the blockers are measured (merge_verification_ms unavailable on all 40 rows, and 36 rows needed inside one regime). | E11, docs/gates/wave-5.md |

The Codex continuation's docs/project-report.md summarizes the same eight; where its
wording differs from the experiment files, the files govern (its H4 line says recall
differences stayed unresolved; E10 measured recall at 1.000 everywhere except one
`before` repetition).

## What the build cost, from Telltale's own captures

Measured on 2026-09-04 from the home store (capture_started labels, capture_ended wall,
the last `claude.stream.result` total_cost_usd at list price per capture):

| Scope | Captures | Wall | List cost USD | Note |
|---|---|---|---|---|
| Waves 1 to 5 implementers | 52 | 22.65 h | 725.62 | median 14.18 per session, max 30.44 |
| Wave 6 implementers | 7 | 3.39 h | 61.50 | W6-T5 ran as Sonnet (21.5 min, 5.94); two killed captures (W6-T2/1, W6-T3/1) ended without a result message, so their cost is unknown, not 0 |
| Experiments E04 to E12 | 146 labelled captures | under 2 h | 21.91 | E12 12.25 of it |
| Wave 0 | not captured | | | ran through the Agent tool before the launcher existed |
| Codex continuation and orchestrator edits | not captured | | | outside the launcher by construction |

Tokens for waves 1 to 5: output 4,328,654; cache read 988,248,948;
cache creation 12,430,802; fresh input 12,560.

## Open decisions

1. **The public flip.** The repository is private. docs/log/W6-T5.md ends with "What to
   click" (Settings, General, Danger Zone, Change visibility; and the social preview
   upload of docs/assets/logo-512.png); the release version stays 0.0.1 until you choose one. Nothing here runs
   `gh repo edit --visibility`.
2. **Kstrl.** Deferred by your decision; the correlations, outcomes and intervention API
   exists and the build used it on itself. A bridge is a new brief.
3. **A production-licensed forecaster.** TimesFM-3's weights are non-commercial. Behind
   the same `forecast()` signature any model can go; on this data the baselines win, so
   the decision has no measured urgency.
4. **The spec's unbuilt v0.6 items.** App-server coverage, an exporter or plugin SDK and
   a web UI were not in the plan's wave 6 and were not attempted.
5. **More data or not.** Every forecasting label is blocked by data, and every blocker
   has its number (E11): outcomes with durations on every landed change, and about 36
   rows inside one environment regime. Day-to-day capture through `telltale setup --print`
   and `telltale daemon` is the free source.

## What ran outside capture, named

The owner's Codex session advanced the work while the Opus weekly limit held
(docs/continuation.md): the reviews of #56 and #57, the W6-T2 completion and W6-F2. The
orchestrator's own edits (the #56 repair's finish, the renormalize fix, fixture fixes,
gate reports) are equally uncaptured. W6-T5 ran as a Sonnet implementer through the
launcher and is captured.
