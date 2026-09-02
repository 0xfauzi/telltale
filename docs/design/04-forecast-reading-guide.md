# Reading a forecast result

Why this exists, in thirty seconds. A forecast that should never have been believed does
not announce itself. It comes back in the right units, with a sensible-looking band, and
the only thing wrong with it is that nothing in the data supported it. This laboratory
answers that with three tables and one word. This page teaches you to read all four, and
above all what each word lets you say out loud and what it does not.

Everything below uses real numbers from the two seeded series that ship with the tests.
Neither is a measurement of anybody's session: they exist so that a table in this guide
is a table you can reproduce, not an illustration.

```
uv run python tests/integration/synthetic_series.py --db $TELLTALE_HOME/telltale.db --rows 200 --seed 1
uv run telltale forecast placebo --series ser_a2688e3b16a09f1a4882c3c8 --target fresh_input_tokens
```

## 1. The backtest table: how a forecaster was scored

Start with the scoring rule, because every number after it is that rule applied twice.

Pick a row in the history. Call it the origin. Hand the forecaster the rows before it and
nothing else, ask it for the next value, and compare its answer against the row you held
back. Then move the origin forward and do it again. Every origin is a separate test the
forecaster could not have peeked at, and its score is the average error over all of them.
That is a **rolling-origin backtest**, and the average is the mean absolute error, MAE:
average of `|what happened - what was forecast|`, in the units of the column.

Here is the table for the 200-row synthetic series, forecasting `fresh_input_tokens` one
step ahead. 136 origins survived; the rest were dropped and counted, which the report
prints under `dropped windows`.

| forecaster | MAE (tokens) | what it does |
|---|---|---|
| echo | 90.4338 | the previous value plus 1 |
| persistence | 90.4338 | the previous value |
| local_drift | 101.1866 | the previous value plus the recent slope |
| rolling_mean | 166.6480 | the mean of the last 8 values |
| rolling_median | 168.7721 | the median of the last 8 values |

Read the bottom of the table first. The four one-line rules under `echo` are the
**baselines**, and they are there so that a big model has something to beat. On this
series the crudest of them, "say whatever happened last time", scores 90.43 tokens, and
the registry's stub scores exactly the same. That equality is not a bug and it is worth a
moment: the stub is persistence plus one token, the column holds whole numbers, and the 68
origins where the series rose cancel the 68 where it did not, to the last bit.

Two habits when you read this table. Read `n_windows` before any score, because an average
over 12 origins is not the same kind of statement as an average over 136. And read the
constants line above the table, which prints `c_min 32 horizon 1 baseline_window 8` and
the rest: those numbers were fixed before any result existed, and a run made under
different ones is a different experiment with a different id.

## 2. The placebo table: did it use the order of the past?

Now the question the backtest cannot answer. A forecaster that beats four baselines has
shown it is better than four crude rules. It has NOT shown that it used the fact that
these rows came in a particular order, and "it learned the dynamics" is exactly what a
reader will take a time-series result to mean.

The control for that is a **placebo**: run the identical backtest again with the past
scrambled, and see how much of the score survives. Scrambling has to destroy the order and
nothing else, or the control tests two things at once. So the context is cut into
consecutive blocks of two whole rows and the blocks are shuffled. Whole rows move
together, so every row still holds the values it held and a covariate still sits beside
the target value it was measured with. What is gone is recency.

Three properties make this honest, and each is enforced in code.

**Only the context moves.** The origin, the row being forecast and its true value are
untouched, so every placebo window is paired with its true-order twin at the same origin.
Without that pairing there is no share-of-windows statistic to compute.

**The baselines are shuffled too.** They are not a fixed reference. They read the same
scrambled context the model reads.

**Persistence must get worse, or the run is void.** Persistence reads exactly one row: the
last one in the context. If the shuffle reached the forecasters at all, it moved that row.
If persistence did not get worse, either the shuffle did not arrive or the series has no
recency to destroy, and either way the control controlled for nothing. The runner refuses
to write a label from such a run.

Here is that check on the same series. `true` is the row from the table above; the rest
are the five block placebos and the five one-row controls.

| ordering | seed | persistence MAE |
|---|---|---|
| true | - | 90.4338 |
| placebo_block | 0 | 365.1544 |
| placebo_block | 1 | 395.9485 |
| placebo_block | 2 | 332.8676 |
| placebo_block | 3 | 379.5147 |
| placebo_block | 4 | 379.5147 |
| placebo_row | 0 | 388.4779 |
| placebo_row | 1 | 441.8897 |
| placebo_row | 2 | 340.0809 |
| placebo_row | 3 | 378.5515 |
| placebo_row | 4 | 378.5515 |

Ten runs out of ten are worse, by a factor of about four. The report says
`10 of 10 placebo runs worse -> valid`, and only then is a label allowed. Seeds 3 and 4
give persistence the identical score, which is not an error either: persistence depends
only on which block the shuffle left last, and two seeds can agree about that while
disagreeing about everything else, which is why the other four columns differ between
them.

`placebo_row` is the second control, with a block of one row. It destroys dependence at
every lag rather than only at lags above two. It is reported and is not part of the rule.

## 3. The decision: four words, and what each one licenses

The rule takes five numbers.

- **E_M**, the model's mean MAE in true order.
- **E_B**, the best of the four baselines in true order.
- **E_P**, the model's mean MAE on the shuffled contexts, taken as the median over the
  five seeds.
- **W_MB**, the share of origins where the model beat the best baseline on that origin.
- **W_MP**, the share of origins where the true-order model beat its own placebo twin.

And two thresholds fixed in advance: `delta = 0.10`, a margin, and `w = 0.60`, a share.
Ties count as losses everywhere.

Here are all four labels, each with a real run behind it.

### not assessable

Fewer than 20 origins, a missing term, no placebo, or a placebo that failed its validity
check. There is no label, and that is a result rather than a hole.

Real case: 200 rows of independent draws, forecast by a stub that adds 400 tokens to the
previous value. The shuffle has no recency to destroy, so persistence scores about the
same either way and only 3 of the 10 placebo runs came out worse. The report prints
`3 of 10 placebo runs worse -> INVALID` and withholds the label, although the baseline
clause on its own would have fired (E_M 401.14 against a best baseline of 90.06).

You may say: nothing. Specifically, you may not say "the model did no better than the
baselines" on the strength of a run whose control failed.

### baseline sufficient

`E_M > (1 - delta) E_B` **or** `W_MB < w`. Either clause alone is enough.

Real case, the run above: E_M 90.4338, E_B 90.4338, so the first clause reads
`90.4338 > 81.3904`, which holds; and W_MB is 0.5, so the second reads `0.5 < 0.6`, which
also holds. Label: baseline sufficient.

Why either clause. The first says the model did not beat the best baseline by a wide
enough margin. The second says it did not beat it often enough, which catches the model
that wins on average by winning enormously on three origins out of a hundred. A single
average cannot tell those apart, so the rule asks both questions.

You may say: on this series, at this horizon, a one-line rule did as well. You may not say
the model is bad in general, and you may not say the series is unforecastable: you have
tested one model, one variant, one horizon.

Note that this branch needs no placebo. E07 labelled 19 (target, horizon) pairs before the
placebo existed for exactly this reason.

### temporal evolution

Not baseline sufficient, **and** `E_M <= (1 - delta) E_P`, **and** `W_MP >= w`, **and**
both of those hold separately in each half of the origin range.

Real case: the noisy straight line in the tests, forecast by a least-squares line fitted
to the context in its true order.

```
E_M 2.5264   E_B 3.9610 (local_drift)   E_P 788.6690   W_MB 0.6978   W_MP 1.0000
E_M > (1 - delta) E_B    2.5264 vs 3.5649   no
W_MB < w                 0.6978 vs 0.6000   no
E_M <= (1 - delta) E_P   2.5264 vs 709.8021 yes
W_MP >= w                1.0000 vs 0.6000   yes
first  [32, 165]  E_M 2.4401  E_P  497.8352  W_MP 1.0000  both hold
second [166, 299] E_M 2.6128  E_P 1103.3020  W_MP 1.0000  both hold
```

The last clause is the anti-cherry-pick rule. A result that lives in one half of the
history is a result about that half, so the two inequalities are recomputed on the first
half of the origins and on the second half, and both halves must pass. It is not
decoration. The same tests contain a fixture that is a ramp for 150 rows and then
independent draws, and a true-order line on it looks like this:

```
E_M <= (1 - delta) E_P   1.6970 vs 206.1989  yes
W_MP >= w                0.7203 vs 0.6000    yes
first  [32, 149]  E_M 2.5474  E_P 457.3595  W_MP 1.0000  both hold
second [182, 299] E_M 0.8466  E_P   0.8429  W_MP 0.4407  DOES NOT HOLD
```

Pooled, both inequalities pass comfortably. They pass because the ramp half carries them:
in the second half the model does not beat its own placebo at all. Without the half clause
this run is labelled temporal evolution; with it, the label drops to conditional
prediction, which is the true statement.

You may say: on this series, this model's advantage over the baselines depends on the
order of the rows, and it holds in both halves of the range. You may **not** say the model
learned a mechanism, that the past causes the future, or what any intervention will do.
This is a statement about a forecast, and the claim class on the stored row is
`predictive`, which is the weakest of the four and never becomes anything else.

### conditional prediction

Everything else: it beat the baselines, and the placebo did not explain the beating.

Real case, and this is the one to keep. The SAME series, and a forecaster that does the
same arithmetic with one difference: it sorts the context before it fits the line.

```
                    sorted first        true order
E_M                 2.5263              2.5264
E_B                 3.9610              3.9610
W_MB                0.6978              0.6978
E_P                 2.5263              788.6690
W_MP                0.0000              1.0000
label               conditional         temporal evolution
                    prediction
```

The two agree on every column of the backtest table to three decimals. On a noisy straight
line, sorting very nearly recovers the true order, so the two fits are almost the same
fit. What separates them is entirely what the placebo did: a permutation cannot change a
function of the sorted values, so E_P is E_M to the last bit and `E_M <= 0.9 E_P` reads
`x <= 0.9x`, which no positive number satisfies.

A reader given only the backtest table could not tell these two apart. That is what the
placebo is for.

You may say: the model is reading something in the window that the baselines miss, and it
is not the order. You may not say it learned the dynamics.

Where the variant carries no covariates at all, there is nothing left for it to be reading
but the level of the target itself, and the report adds a note saying exactly that:
`level prediction: the gain is distributional`.

### The order the rule tries them

Not assessable, then baseline sufficient, then temporal evolution, then conditional
prediction. The order matters: a run that is baseline sufficient never reaches the placebo
clauses, which is why a table can show W_MP 0.8971 beside the label "baseline sufficient".
The model did beat its placebo. It did not beat the baselines, and that comes first.

## 4. The ablation table: do the behaviour columns forecast a change?

The A/B/C ablation asks one narrow question about the change clock, where a row is one
change that landed. Three nested sets of columns:

- **A**, what the change IS: files changed, lines added and removed, subsystems touched,
  test files changed, whether a lockfile moved. Six columns anybody can read off a diff.
- **B**, A plus what the session SPENT: fresh tokens, cached tokens, compactions, attempts
  to land, whether the environment changed.
- **C**, B plus how the session BEHAVED: verification cycles, edit turnover, stable-state
  intervals, unique files read.

Because the sets are nested, the only thing that differs between two runs is the columns
the larger one adds, so the verdict is about those columns and not about two unrelated
models. Three rules hold that in place: the three runs are cut down to the origins all
three retained and rescored there, the target is excluded from its own covariates, and a
variant past 15 variates is refused rather than quietly narrowed.

The verdict: C **adds temporal information** when `E_C <= (1 - delta) min(E_A, E_B)` and
`W >= w`. Otherwise **diagnostic only**, and that is not a failure. It says the C columns
describe a change without forecasting it, which is a true and useful thing for a column to
be.

On the 60-row synthetic change series, forecasting `attempts_to_land`:

```
VARIANT  COLUMNS  VARIATES  N_WINDOWS  MAE_MEAN
A        6        7         44         1.0682
B        11       11        44         1.0682
C        15       15        44         1.0682

E_C <= (1 - delta) min(E_A, E_B)   1.0682 vs 0.9614   no
W >= w                             0.0000 vs 0.6000   no
verdict: C diagnostic only
warnings:
  A, B and C scored identically: no forecaster in this run read a covariate at all,
  so the verdict is a property of the forecasters and not of the columns
```

Read the warning before the verdict. The three variants agree to the last bit, and the
runner says why: none of the four baselines nor the stub looks at a covariate at all. The
verdict here is a fact about the forecasters that were run, not about the A, B and C
columns. It becomes a fact about the columns the day a covariate-reading forecaster is
run, and the warning disappears on its own when the three numbers stop being equal.

The winning variant's placebo is printed underneath, so a reader never has an ablation
verdict without knowing whether the winner used the order at all.

## 5. The candidate protocol, and the sentence that must ride with it

The last table answers a question at a merge decision. A change is sitting there. What is
known is its A block: how many files, how many lines, how many subsystems. What is not
known is anything after the merge, so the targets are post-merge columns:
`merge_verification_ms`, `merge_verification_failed`, `rework_within_3`.

Two runs over the same origins. The unconditioned one knows the history and nothing about
the candidate. The conditioned one is identical, plus the candidate's own A block reaching
one row past the context, because those features are known at the moment the decision is
taken. The difference between them is reported as a paired median, origin by origin.

Two refusals hold the question in shape. `attempts_to_land` is refused as a target,
because it is already known at merge time and conditioning a forecast of it on the
candidate's features scores a lookup. And `rework_within_3` is a delayed label: a change is
only labelled once three more have landed, so its origins stop at `o <= N - 3`.

Every output carries this sentence, and it is mandatory rather than advisory:

> The difference between the conditioned and unconditioned forecast measures how much the
> candidate's known features change the forecast; it is not the effect of merging the
> candidate, because only one future is observed.

Read it as a warning about the reader rather than about the code. The number is a
difference between two forecasts of one history. There is no world in which that change
was not merged, so there is nothing to subtract, and the moment somebody reads the
difference as the difference merging made, this protocol has misled them.

## 6. Why "would" is refused

Three words may not appear anywhere in a forecast report: **cause**, **impact**, **would**.
The renderer checks the finished string and raises before anything is printed or stored,
so a report that uses one is not produced at all rather than produced and regretted.

The reason is the shape of the evidence. A backtest observes one history. It can say what
a rule scored on rows it had not seen, and that is a genuinely useful thing, but "cause"
and "impact" claim that changing one thing changes another, and "would" claims what the
unobserved branch holds. None of those is in the data. There is no second history where
the change was not merged, the model was not switched, the session did not compact.

The check is a word boundary, so "because" is not a hit: the letters of "cause" are inside
it with no boundary in front of them. That is deliberate, and the mandatory candidate
sentence above depends on it. Inflections are refused with their stems, so "caused",
"impacts" and "impacting" go the same way as the bare words.

The rule cannot be argued with by a report, only by a different kind of evidence. That is
its whole point: the claim class on every forecast row is `predictive`, the weakest of the
four, and nothing anywhere may present a number as stronger evidence than the layer that
produced it.
