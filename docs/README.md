# Documentation index

Five kinds of document live here, and they answer different questions. The specification
says what the system may claim. The design says how it is built. An experiment write-up
says what one measurement showed. A task log says what one implementer did, knew and got
wrong. A brief says what an implementer was asked to do before they started.

## `docs/spec/` the specification

| File | What it is |
|---|---|
| [`telltale-architecture.md`](spec/telltale-architecture.md) | The specification: purpose, epistemic contract, hypotheses and their falsification, requirements, architecture, capture strategy, privacy model, semantic reduction, forecasting laboratory and the v0.0 to v0.6 research gates. |
| `telltale-architecture.docx` | The authority the Markdown was converted from. When the two disagree, the `.docx` wins. |
| `media/` | The figures the specification references. |

## `docs/design/` the design

| File | What it is |
|---|---|
| [`00-digest.md`](design/00-digest.md) | Every provider fact the capture design rests on, each with the source it was verified against and the date it was checked. |
| [`01-design.md`](design/01-design.md) | The design: the four durable shapes, the observation vocabulary, sanitization, storage, receiver, providers, observers, launcher, reducers, series, forecasting, CLI and the repository layout. |
| [`02-protocol.md`](design/02-protocol.md) | Owner decisions, the standing gates, and the orchestration protocol: roles, dispatch, merge, the brief format in section 7.4 and the experiment template in section 7.5. |

## `docs/experiments/` what was measured

| File | What it is |
|---|---|
| [`README.md`](experiments/README.md) | The write-up template and the rule that the decision rule is written before the run. |
| `E##.md` | One decision per experiment, written for the owner, citing the raw output under `experiments/E##/out/` by path. |

## `docs/log/` what each task did

| File | What it is |
|---|---|
| [`README.md`](log/README.md) | Why these reports are immutable after merge, and the fixed section order. |
| [`W0-T1.md`](log/W0-T1.md) | Bootstrap: the package, the gates, CI, and the six things the brief got wrong. |
| [`W0-T6.md`](log/W0-T6.md) | Brand and public-ready documents: the logo, this index, and the repository files a public reader expects. |

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
| [`logo-512.png`](assets/logo-512.png) | The mark rasterized at 512 px, for the repository social preview. |
| [`logo-32.png`](assets/logo-32.png) | The mark rasterized at 32 px, which is the size the design was checked at. |
