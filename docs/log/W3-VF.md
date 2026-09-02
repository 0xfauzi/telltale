# W3-VF: the wave 3 verifier's three findings, closed

Orchestrator fix, branch `task/W3-VF` from `main` at d56fce1 (W3-V merged as #37). The
verifier (docs/log/W3-V.md) re-ran 62 VERIFY assertions, 58 passed, and named three
findings no report had stated. This branch closes the three, each with a test that fails
on the un-fixed code (the break-and-restore runs are pasted below).

## OUTCOME

1. `telltale forecast backtest` labels the row it stores. The label comes from the one
   decision rule (forecast/decide.py) handed no placebo: "baseline sufficient" when the
   true-order comparison earns it (6.12 as amended by W3-E08b, which pre-registered that
   the baseline clause reads no placebo), otherwise "not assessable" with the reason
   "placebo not run: label withheld". The two inequalities the rule evaluated are printed
   and stored either way. Placebo rows already stored for the pair are counted and the
   newest is named, instead of the old unconditional "placebo not run".
2. `backtest.persist` refuses the words cause, impact and would on the row's warnings,
   assumptions and decision. The renderer already refused them; a caller of `persist`
   alone could store them. Now it cannot.
3. A capture carrying two attempt identities is dropped from the attempt clock by name,
   with both identities listed. The compiler already did this (W3-T1); nothing tested it,
   and the verifier showed that `return sorted(named)[0]` in its place left the suite
   green. It no longer does.

## WHAT WAS WRONG

- `cli_forecast.forecast_backtest` printed `decision: not assessable (placebo not run
  ...)` as a constant and stored `decision` NULL. The verifier counted 6 of the 8
  true-order rows its session wrote through the CLI with no label, and showed the
  reason printed false after `forecast placebo` had stored 10 placebo rows for the same
  triple. The wave 3 exit criterion ("every forecast claim labelled ... with the
  inequalities shown") failed on exactly that command, stdout and stored row both.
- `refuse_words` ran in every renderer and no CLI path stores before it renders, but
  `backtest.persist` itself checked nothing: the guard was on the page, not on the row.
- `series_lineage._identity` refused two identities on one capture, and no test built
  such a capture.

## WHAT CHANGED

- `src/telltale/forecast/__init__.py`: `PLACEBO_ABSENT_WARNING` beside
  `PLACEBO_INVALID_WARNING`.
- `src/telltale/forecast/decide.py`: `Decision.placebo_valid` is `bool | None`, None
  when no placebo ran (a control nobody ran has no verdict; `placebo.n_runs` beside it
  says so). The baseline-sufficient note is chosen by `_notes`: absent, invalid, or none.
  `decide` and `relabel` accept None and keep it.
- `src/telltale/forecast/placebo.py`: `unpaired(true_run, model)` runs the rule with no
  placebo runs and writes the decision onto the run; `constants` is public (the backtest
  command prints the same constants line the placebo command does); `matches` takes the
  orderings to match, default true order.
- `src/telltale/forecast/backtest.py`: `persist` calls `refuse_words` on the JSON of
  warnings, assumptions and decision before `put_forecast_run`.
- `src/telltale/cli_forecast.py`: `forecast backtest` takes `--model` on the same terms
  as `forecast placebo` (default: the one forecaster that is not a baseline; refused
  when there is a choice), labels through `placebos.unpaired`, prints
  `decider.report`, counts the stored placebo rows of its pair through `_stored`, which
  `_stored_true` now shares, and prints the `forecast placebo` hint only when the reason
  is "placebo not run".
- `tests/integration/test_forecast_decision.py`: `test_a_backtest_alone_withholds_the_label`
  is replaced by `test_a_backtest_alone_labels_its_row_through_the_same_rule` (the CLI,
  baseline sufficient, the note, the stored decision with `placebo_valid` None and both
  inequalities), plus `test_a_backtest_after_a_placebo_names_the_stored_pair` (the
  verifier's break 8: 10 stored placebo rows, the count and the newest id printed, no
  "label withheld") and
  `test_a_run_that_beats_the_baselines_with_no_placebo_is_not_assessable` (the other
  branch of `unpaired`, at the function, then persisted and read back).
  `test_the_renderer_refuses_before_it_prints_or_stores` now also calls `persist`
  directly with the poisoned run and asserts the refusal and the empty table.
- `tests/integration/test_series_lineage.py`:
  `test_a_capture_with_two_identities_is_dropped_by_name_and_never_a_row`. A real
  `telltale run --task-id T-two --attempt 1` whose child posts `T-other/2` to
  `/v1/correlations` of the launcher's own receiver (the endpoint and the capture id
  come from the environment the launcher set), beside one clean attempt: the frame has
  one row, `dropped` names the capture with "two attempt identities on one capture
  (T-other/2, T-two/1): refusing to pick", and `series build` prints it.
- `docs/design/01-design.md`: three amendment lines under wave 3.

## VERIFY

Break-and-restore, each in this worktree with the file swapped for `main`'s and put
back:

```
main's cli_forecast.py (no labelling):
  FAILED test_a_backtest_alone_labels_its_row_through_the_same_rule
  FAILED test_a_backtest_after_a_placebo_names_the_stored_pair
main's backtest.py (no persist guard):
  FAILED test_the_renderer_refuses_before_it_prints_or_stores
_identity returning sorted(named)[0]:
  AssertionError: assert [('T-two', 1), ('T-clean', 1)] == [('T-clean', 1)]
```

Restored, `git status` shows only the seven intended files modified. The gate set and
the full suite are in the PR checks and in the merge record of docs/gates/wave-3.md.

## UNSURE OR NOT DONE

- The verifier's fourth failure, `telltale vector` at 1.00 to 1.04 s against "under
  1.0 s", is not touched. It needs the bisect the verifier did not run; the profile says
  the code W3-T0 changed is 0.053 s of the second. Carried to the gate report.
- The 1696 historical importer diagnostics keep the old wording. Diagnostics are ingest
  facts and are not rewritten; W3-T0's OUTCOME sentence overstated the fix. Recorded in
  the gate report, not changed here.
- E08.md's one number without an out/ file (176.249 s, the rebuild of 3223 captures)
  lived in census.json, excluded for size. Recorded in the gate report.
- `Decision.placebo_valid` None reaches E08's `relabel` path untouched: E08's stored
  terms carry booleans, and `relabel` keeps whatever it is handed.

## NEXT

Fold into docs/gates/wave-3.md with the verifier's four failures and the exit-criterion
verdict re-stated after this fix; record the outcomes for W3-VF attempt 1.
