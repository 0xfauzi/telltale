---
name: Experiment
about: Propose a measurement, or report what one showed
title: 'E: '
labels: experiment
assignees: ''
---

<!--
An experiment whose decision rule is written after the run can only confirm what was
already believed. Write the rule first, in the section below, before any number exists.

The full write-up template is section 7.5 of docs/design/02-protocol.md, and the finished
write-up lives at docs/experiments/E##.md. This issue is the proposal and the result
summary; the raw output lives under experiments/E##/out/ and is cited by path.
-->

## The question, in one sentence

## Why this matters, in thirty seconds

The claim that depends on the answer. If nothing downstream changes whichever way this
comes out, it is not worth running.

## Decision rule, written before the run

Outcome A means we may say X. Outcome B means we may not, and we do Y instead. Name the
threshold and the sample size now, or say that a pilot has to measure them first.

## Cost

Sessions, tokens and wall time, from a pilot rather than an estimate. Every experiment that
starts real agent sessions needs the owner's approval first.

---

Once it has run, fill the five headings below. They are the same order every task report in
`docs/log/` uses.

## OUTCOME

Which branch of the decision rule the numbers took.

## WHAT WAS WRONG

What the plan got wrong when it met the real system. Every experiment so far has had at
least one of these, and they are the most useful part of the write-up.

## WHAT CHANGED

Exact commands, environment fingerprint ids, sample sizes, wall time. Tables of raw
numbers with units, each traceable to a file under `experiments/E##/out/`.

## UNSURE OR NOT DONE

Per claim: measured, reasoned, judgement or unknown. The alternative reading you rejected,
and the evidence against it.

## NEXT

What downstream may now say: claim class, cohort, coverage caveats, in one paragraph.
