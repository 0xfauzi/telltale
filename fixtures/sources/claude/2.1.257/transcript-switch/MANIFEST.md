# transcript-switch: one imported session, two models

Hand-written by W7-T2 on 2026-09-05, for the same reason `transcript/` beside it is
hand-written: an import reads the owner's own sessions, and the owner's own sessions are
the one thing that must never be copied into this repository. The line shape is the
shape `transcript/` already carries, which was measured against the owner's real
`~/.claude/projects` (1806 files, 1.7 GB) by W2-T2.

## What is here

```
-telltale-fake-project/33333333-4444-4555-8666-777777777777.jsonl   42 lines
```

One `mode` line, then twenty user/assistant pairs, and one more assistant line after
the fifth pair. Every assistant line carries `version` 2.1.257, `effort` xhigh and
`message.usage`. The first ten name `claude-opus-5` and the last ten name
`claude-sonnet-5`, and nothing else about the environment changes. The extra line names
`<synthetic>`, carries no `requestId` and has every token count zero, which is what
Claude Code writes when it puts one of its own error messages in the transcript.

## What it exercises

| what | why it is here |
| --- | --- |
| a `mode` line before the first assistant line | the lines before the first regime is named still have to carry it, or an otherwise complete capture reports `partial` |
| ten assistant lines on one model, then ten on another | two environment fingerprints in one file: two `telltale.environment` rows, `fingerprints: 2` on capture_started |
| the switch at request eleven | `env_changed` is `observed` over 20 rows with exactly one changepoint, at index 10 |
| one `<synthetic>` line inside the first ten | it names no model, so it names no environment and adds no changepoint; measured on the E13 store, 105 such rows in 64 of the 1841 imported Claude captures |
| one `sk-ant-api03-TELLTALEFAKE...` probe in the first user message | a fixture without the probe cannot show that the probe was removed |

Twenty requests and not two, because readiness check 1 refuses a capture with fewer than
32; a fixture is not there to pass that check, but a one-row-per-model file could not
tell a changepoint apart from an off-by-one in the row that carries it.
