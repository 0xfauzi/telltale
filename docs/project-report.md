# Telltale project report

This report separates delivered software from research findings.
The [wave gates](README.md#docsgates-whether-a-research-gate-was-met) record implementation verification, wave 0 through wave 9.
The experiments below record the limits of each research claim.

## Research findings

| Hypothesis | Finding | Evidence and limit |
|---|---|---|
| H1: summaries support diagnosis | Partly supported in the measured cohort. | [E12](experiments/E12.md) labels eight questions sufficient and two insufficient across six sessions per reviewer arm. |
| H2: measures support comparison | The repeated task establishes a narrow variation floor. | [E05, corrected run](experiments/E05.md#the-measured-condition-e05r2) used five successful runs of one small task. Larger tasks remain unmeasured. |
| H3: environment changes telemetry | Supported for the tested effort change. | [E06](experiments/E06.md) resolves changes in output tokens, cache-read tokens, and stable-state tokens. Each arm contains five runs. |
| H4: repository interventions change work | The tested instruction change reduced work on two probes. | [E10](experiments/E10.md) reports fewer tool calls. Recall differences remain unresolved, and the answer key limits precision interpretation. |
| H5: chronology improves forecasts | Not assessable on the request clock; baseline sufficient on the change clock. | [E08](experiments/E08.md) reports no valid chronology control among 38 evaluated rows. [E16](experiments/E16.md) adds 12 change-clock rows, placebo valid on 6 of 12, every row baseline sufficient. No temporal-evolution claim follows. |
| H6: TimesFM improves on baselines | The measured results favor retaining baselines, now on both clocks and both providers. | [E08b](experiments/E08b.md) labels 37 rows baseline sufficient and one not assessable. [E13](experiments/E13.md) adds 596 backtests over the Claude population, [E13b](experiments/E13b.md) the Codex population, [E16](experiments/E16.md) 12 change-clock rows. Baseline sufficient on every pooled row. |
| H7: agent semantics add predictive information | Not assessable, and it is the only label still blocked by data. | The process columns exist on 5 of 657 change rows because the rest came from git history. [E17](experiments/E17.md) holds the rule and the bar; the daemon is accruing the rows. |
| H8: candidate features improve near-term forecasts | Assessable since E16, and it reads mostly negative. | [E11](experiments/E11.md) found zero valid windows. [E16](experiments/E16.md) reached the reading on 11 rows: 10 "no measurable conditioning at this n", 1 moved (systemap `merge_verification_ms`, 23 paired windows), carried forward and not built on. Candidate forecasting remains a research command. |

These results describe the measured tasks, models, environments, and repositories.
They do not establish a universal repository quality or maintainability score.
The recorder preserves unknown values and names incomplete coverage.

E12 also measures the cost of reading summaries.
Median token use equals 422,835.5 for summaries and 696,509 for raw streams.
Median elapsed time equals 177,974 ms for summaries and 138,098.5 ms for raw streams.
Summaries used fewer tokens but took longer in that experiment.
Question Q2 exposed an intentional unknown; Q8 exposed the summary's missing commit count.

## What each claim means

| Claim | Permitted statement |
|---|---|
| Observed | A provider or recorder surface reported an event, with its source preserved. |
| Derived | A named calculation produced a value from recorded evidence. |
| Comparative | A defined cohort supports a comparison under the stated measurement rule. |
| Associative | Known candidate features change a conditional forecast. This does not establish an effect from merging. |
| Predictive | A forecast describes future values under its recorded assumptions and coverage limits. |

Telltale does not upgrade computed values into observations or causal claims.
The advisory remains in shadow mode and changes no execution decisions.
The [wave 5 gate](gates/wave-5.md#exit-verdict-spec-21-v05) explicitly retains the research-only result.

## Build measurement limits

The recovered session measured build usage on 2026-09-03 before the remaining wave 6 work finished.
That snapshot contains 55 build captures, including three wave 6 sessions then running.
It reports 725.62 USD in cumulative provider list-price totals and 22.65 hours of captured implementer time.
These figures do not represent a final project bill or sequential completion time.
Wave 0 and early uncaptured implementers fall outside those totals.
The [Codex continuation](continuation.md) also falls outside those totals because its subagents bypass capture.

Five captures contain multiple result messages with cumulative cost values.
The recovered analysis uses each capture's final cumulative result and avoids summing those messages.
The source session retains the measurement query and its per-capture output.
Session identifier: `c7bb63ec-a5d0-4775-83da-c116371b5544`.

## Owner decisions

- Repository visibility remains private. The owner controls any public release and release version.
- Kstrl integration remains deferred under the [approved protocol](design/02-protocol.md).
- Production model use needs a suitable license. The acknowledged TimesFM weights remain restricted to research use here.
- Further agent experiments require approval under the protocol's standing budget gate.

The next research priority is H7, and it is the only one that cannot be run on demand.
Its rows accrue only from day-to-day capture, one commit at a time, and they cannot be
backfilled from git. [E17](experiments/E17.md) states the population, the bar and the
readings before any row is scored. New research must preserve failures and unknown
outcomes instead of manufacturing complete histories.
