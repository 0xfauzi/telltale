# Experiment write-ups

One file per experiment, `E##.md`, written for the owner rather than for the person who
ran it. The experiment's runner and its raw output live in `experiments/E##/` (the runner
is tracked, `out/` is not); this directory holds the decision, and it cites the raw files
by path so the numbers can be checked.

The decision rule is written down BEFORE the run. An experiment whose rule is written
afterwards can only confirm what was already believed.

Template, from the orchestration protocol (`docs/design/02-protocol.md`, section 7.5):

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
