# Documentation index

Six kinds of document live here, and they answer different questions. The specification
says what the system may claim. The design says how it is built. A wave gate says whether
one research gate's exit criterion was met, against measurements taken on the day. An
experiment write-up says what one measurement showed. A task log says what one implementer
did, knew and got wrong. A brief says what an implementer was asked to do before they
started.

## `docs/spec/` the specification

| File | What it is |
|---|---|
| [`telltale-architecture.md`](spec/telltale-architecture.md) | The specification: purpose, epistemic contract, hypotheses and their falsification, requirements, architecture, capture strategy, privacy model, semantic reduction, forecasting laboratory and the v0.0 to v0.6 research gates. |
| [`telltale-architecture.docx`](spec/telltale-architecture.docx) | The authority the Markdown was converted from. When the two disagree, the `.docx` wins. |
| [`media/`](spec/media/) | 5 figures the specification references. |

## `docs/design/` the design

| File | What it is |
|---|---|
| [`00-digest.md`](design/00-digest.md) | Every provider fact the capture design rests on, each with the source it was verified against and the date it was checked. |
| [`01-design.md`](design/01-design.md) | The design: the four durable shapes, the observation vocabulary, sanitization, storage, receiver, providers, observers, launcher, reducers, series, forecasting, CLI and the repository layout. Carries every wave's design amendments once folded. |
| [`02-protocol.md`](design/02-protocol.md) | Owner decisions, the standing gates, and the orchestration protocol: roles, dispatch, merge, the brief format in section 7.4 and the experiment template in section 7.5. |
| [`03-capture-howto.md`](design/03-capture-howto.md) | How to record a session: `telltale run` for a command you can wrap, the daemon plus a pasted snippet for the sessions you start yourself, what is and is not stored, and how to delete it. |
| [`04-forecast-reading-guide.md`](design/04-forecast-reading-guide.md) | How to read a forecast result without over-claiming it: the claim classes a run may carry, the baselines it is judged against, and the words refused in its output. |
| [`amendments/README.md`](design/amendments/README.md) | Why this directory is usually near-empty: an amendment lives here only for the wave still in progress, and is folded into `01-design.md` at that wave's gate. |

## `docs/gates/` whether a research gate was met

One report per wave, written by the orchestrator as each wave's tasks merge, closing with
the spec section 21 exit verdict for that version.

| File | Verdict |
|---|---|
| [`wave-0.md`](gates/wave-0.md) | v0.0 capture feasibility: met. Tool, file, command, session, usage and compaction facts are observable without unsafe content retention or model traffic interception. |
| [`wave-1.md`](gates/wave-1.md) | v0.1 flight recorder, experiment harness, TimesFM smoke: met. A non-trivial session is diagnosable from Telltale alone, activities reproduce byte-identical, the repeat harness exposes within-condition variance, and the TimesFM interface runs with no predictive claim. |
| [`wave-2.md`](gates/wave-2.md) | v0.2 measures and stochastic bounds: met. Activities reproduce byte-identical from observations; H2 and H3 floors and factor effects are stated resolved or not resolved at n = 5 on real sessions; readiness is computed on real data. |
| [`wave-3.md`](gates/wave-3.md) | v0.3 outcome correlation, temporal validity: met. Every forecast claim is labelled temporal evolution, conditional prediction, baseline sufficient or not assessable, with the inequalities shown. |
| [`wave-4.md`](gates/wave-4.md) | v0.4 controlled repository interaction research: met. States, per claim, which repository claims are supportable (comparative, cohort-scoped) and which remain workload analytics; no universal maintainability score exists. |
| [`wave-5.md`](gates/wave-5.md) | v0.5 candidate-conditioned one-step advisory: **not met**. The protocol is not assessable on this repository (E11: 0 forecast windows on every target), so `forecast candidate` stays a research command and the shadow advisory stays shadow. |
| [`wave-6.md`](gates/wave-6.md) | v0.6 scenario horizons, policy experiments, hardening: met for everything the plan put in wave 6 (scenario horizons, policy intervention regimes and regime-segmented series, the compatibility matrix, export/import/purge/retention). The spec's exporter/plugin SDK, local web UI and app-server coverage were not attempted. |
| [`final-report.md`](gates/final-report.md) | The orchestrator's final report to the owner: what Telltale can say by claim class, the eight hypotheses with their measured labels, the build's cost from its own captures, the open decisions and what ran outside capture. |

## `docs/experiments/` what was measured

| File | What it is |
|---|---|
| [`README.md`](experiments/README.md) | The write-up template and the rule that the decision rule is written before the run. |
| [`E01.md`](experiments/E01.md) | Which Claude Code facts each capture surface carries on this machine, whether capture fails open, and how long records keep arriving after a session exits. |
| [`E02.md`](experiments/E02.md) | Which Codex facts a launcher can observe, on which surface, and what capture costs the session being recorded. |
| [`E03.md`](experiments/E03.md) | Whether TimesFM 3.0.0 installs and runs on this machine, and what one forecast costs. |
| [`E04.md`](experiments/E04.md) | What recording costs the session it records, and whether it still fails open when the receiver is overwhelmed. |
| [`E05.md`](experiments/E05.md) | How much a measure moves when five real sessions run the same task. |
| [`E06.md`](experiments/E06.md) | Whether one launch flag moves a measure further than the run-to-run floor. |
| [`E07.md`](experiments/E07.md) | Which request-clock targets of this build's own sessions can be forecast at all, and whether TimesFM-3 beats the baselines on any of them. |
| [`E08.md`](experiments/E08.md) | On captured data, whether anything the request clock can forecast survives its own chronology placebo. |
| [`E08b.md`](experiments/E08b.md) | Whether the true-order comparison alone earns "baseline sufficient" when the chronology control failed. |
| [`E09.md`](experiments/E09.md) | Whether a fixed probe of this repository can be answered by an agent, and how consistently. |
| [`E10.md`](experiments/E10.md) | Whether an AGENTS.md that names the key files changes how the same two questions are answered, and what they cost. |
| [`E11.md`](experiments/E11.md) | Whether knowing a candidate's own diff features changes the forecast of what happens after it is merged, on this repository. |
| [`E12.md`](experiments/E12.md) | Whether a reviewer who sees only Telltale's summary of a session can diagnose it as well as one who sees the raw stream, and at what cost. |
| [`E13.md`](experiments/E13.md) | Whether TimesFM-3 clears the baselines anywhere in the whole Claude population, launcher captures and imported day-to-day transcripts alike, and what blocks the rest. |
| [`E14.md`](experiments/E14.md) | Whether a transcript's own timestamps reproduce the request duration the OTel surface states, and which definition of the interval does it. |
| [`E15.md`](experiments/E15.md) | Whether four things Telltale records (a verification failing, a command failing, a session's remaining spend, the next activity kind) are predictable out of fold from what came before, against named nulls and pre-registered bars. |
| [`E16.md`](experiments/E16.md) | Whether a candidate change's known features condition a one-step forecast of its verification and rework on the owner's repositories, backfilled from git and GitHub (H8), and whether chronology survives the placebo there (H5, H6). |

## `docs/log/` what each task did

| File | What it is |
|---|---|
| [`README.md`](log/README.md) | Why these reports are immutable after merge, and the fixed section order. |
| `<TASK>.md` | 59 reports, one per merged task, immutable. |

## `briefs/` what each task was asked to do

| File | What it is |
|---|---|
| [`README.md`](../briefs/README.md) | The brief format, and why a number a task needs is injected into its brief rather than referenced. |
| `<TASK>.md` | One brief per dispatched task, written before the task starts. |

## `docs/assets/` the brand

| File | What it is |
|---|---|
| [`logo.svg`](assets/logo.svg) | The mark: a sail with three telltales, ink `#16232F` on transparent, 512 by 512. |
| [`logo-dark.svg`](assets/logo-dark.svg) | The mark for dark backgrounds: the ink becomes sailcloth `#E8EDF2`, the signal `#D93A2B` does not move. |
| [`wordmark.svg`](assets/wordmark.svg) | The mark plus the word, 1600 by 512, set in a system font stack so nothing is fetched. |
| [`wordmark-dark.svg`](assets/wordmark-dark.svg) | The wordmark for dark backgrounds. |
| [`logo-512.png`](assets/logo-512.png) | The mark rasterized at 512 px, for the repository social preview. This is 1 of 2 PNG rasters in this directory. |
| [`logo-32.png`](assets/logo-32.png) | The mark rasterized at 32 px, which is the size the design was checked at. |

## Written outside a brief

The owner's Codex continuation wrote these directly to `docs/` while the Opus weekly limit
blocked implementer dispatch (docs/gates/wave-6.md records the incident). This index links
them; nothing in either file is changed here.

| File | What it is |
|---|---|
| [`continuation.md`](continuation.md) | The recovered state and execution plan the continuation session picked up wave 6 from. |
| [`project-report.md`](project-report.md) | The H1 to H8 research findings, each cited to the experiment or gate that measured it. |
