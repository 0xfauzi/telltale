# rollout-switch: one imported rollout, two models

Hand-written by W7-T2 on 2026-09-05. The Codex half of the pair whose Claude half is
`claude/2.1.257/transcript-switch/`, and the same rule applies: nothing here was
captured. The record shape is the shape `rollout-import/` beside it already carries.

## What is here

```
2026/09/03/rollout-2026-09-03T08-00-00-019adbdb-2222-7282-acf2-a7be9046cc70.jsonl  15 lines
```

One `session_meta` naming `cli_version` 0.150.1, then two turns. Each turn is a
`turn_context`, a `task_started`, a shell call, its `item_completed`, a `token_count`
and a `task_complete`. `turn_1` runs on `gpt-5.6-sol` and `turn_2` on `gpt-5.6-mini`;
`effort` is `high` on both.

## What it exercises

| what | why it is here |
| --- | --- |
| `cli_version` on `session_meta` alone | the version a rollout regime is built from is not on the turn record that names the model |
| two `turn_context` records differing in model | two fingerprints in one file, and the second `telltale.environment` row written before the first line of the second turn |
| the lines between `session_meta` and the first `turn_context` | they carry the first turn's id, as the pre-`assistant` lines of a transcript do |

`env_changed` on the request clock is not what this fixture shows, and cannot be: an
imported rollout builds no request activity, because `correlate.ROLES` maps no
`codex.rollout.*` type to `request` or `assistant`, so `series build --clock request`
reports 0 rows here. Measured 2026-09-05, W7-T2. The Codex request clock is W7-T1's.
What is asserted instead is the observation rows themselves: which id each line carries,
and where the two partitions meet.
