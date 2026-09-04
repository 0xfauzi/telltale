# Telltale project report

This report separates delivered software from research findings.
The [wave gates](gates/wave-6.md) record implementation verification.
The experiments below record the limits of each research claim.

## Research findings

| Hypothesis | Finding | Evidence and limit |
|---|---|---|
| H1: summaries support diagnosis | Partly supported in the measured cohort. | [E12](experiments/E12.md) labels eight questions sufficient and two insufficient across six sessions per reviewer arm. |
| H2: measures support comparison | The repeated task establishes a narrow variation floor. | [E05, corrected run](experiments/E05.md#the-measured-condition-e05r2) used five successful runs of one small task. Larger tasks remain unmeasured. |
| H3: environment changes telemetry | Supported for the tested effort change. | [E06](experiments/E06.md) resolves changes in output tokens, cache-read tokens, and stable-state tokens. Each arm contains five runs. |
| H4: repository interventions change work | The tested instruction change reduced work on two probes. | [E10](experiments/E10.md) reports fewer tool calls. Recall differences remain unresolved, and the answer key limits precision interpretation. |
| H5: chronology improves forecasts | Not assessable under the registered control. | [E08](experiments/E08.md) reports no valid chronology control among 38 evaluated rows. No temporal-evolution claim follows. |
| H6: TimesFM improves on baselines | The measured results favor retaining baselines. | [E08b](experiments/E08b.md) labels 37 rows baseline sufficient and one not assessable under the approved amended rule. |
| H7: agent semantics add predictive information | Not assessable. | [Wave 3](gates/wave-3.md) records zero evaluation origins common to all three feature groups. |
| H8: candidate features improve near-term forecasts | Not assessable on this repository. | [E11](experiments/E11.md) finds zero valid windows for each target. Candidate forecasting remains a research command. |

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

The next research priority is outcome data with measured verification durations and complete coverage.
The existing candidate protocol cannot establish forecasting usefulness from its current zero-window result.
New research must preserve failures and unknown outcomes instead of manufacturing complete histories.
