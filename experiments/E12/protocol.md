# E12 protocol: blinded session diagnosis (H1), written before any reviewer session

Question: can a reviewer who sees only Telltale's summary of a coding-agent session
answer fixed diagnostic questions about it as well as a reviewer who sees the raw
stream-json, and at what cost in tokens and wall time?

## Material

- Six sessions, chosen by the rule in `manifest.json` (the six most recent build
  attempts with exactly one result line, chosen 2026-09-03 before any reviewer ran):
  W4-T2/1, W4-T1/1, W3-E08b/1, W3-V/1, W3-E08/1, W3-T4/1. Their raw stream-json files
  are archived under `out/raw/` (gitignored), 1.0 to 2.0 MB each.
- Arm S (summary): the text of `telltale show`, `telltale timeline` and
  `telltale vector` for the session's capture in the E12 material store
  (`experiments/E12/out/store`, gitignored, built by `build_store.py`: each archived
  stream captured through the real launcher, `emit.py` printing the bytes, at the
  classifier version of the checkout; the capture ids are `material_capture_id` in
  `manifest.json`), written to files named `material/summary.txt`,
  `material/timeline.txt`, `material/vector.txt`. Not the owner's store: measured
  2026-09-03, the owner's captures of these sessions cannot recover the 32 pytest runs
  typed on more than one line (the raw command was never stored; a rebuild after W4-T3
  reaches 19 of 51), while the launcher capture of the archived stream reaches 49. The
  material captures hold the stream surface only (no OTel, no hooks); the summary's
  coverage line says so and is part of the material.
- Arm R (raw): the stream-json file copied to `material/session.jsonl`.
- Blinding: the reviewer's working directory holds the material and nothing else; the
  prompt names no task, no session, no arm and no date. The two arms get the same ten
  questions and the same output format. The reviewer runs under the launcher, so its own
  capture lists every path it read (content level 1 keeps paths): a reviewer capture
  whose timeline shows a file_read outside its material directory is invalid and is run
  once more with the restriction restated in the prompt; a second such run stops the arm.
  The answer key is committed in this repository, so the check is the mechanism that
  keeps arm S from reading it.

## The key

`key.json`, derived from the raw streams by `make_key.py` (rules in the file; heredoc
bodies are data, a pytest execution is a segment whose head is pytest). The key is
independent of Telltale. Its `--check` compares it with `telltale show`; three runs so
far, 2026-09-03:

| store | Q1 telltale (key 10, 11, 6, 11, 2, 11) | agreeing cells of 36 | what differs |
|---|---|---|---|
| owner's, before W4-T3 | 4, 1, 3, 0, 2, 4 | 26 | Q1 and Q2 on five sessions: the classifier gap |
| owner's, after W4-T3 and the rebuild | 4, 3, 3, 1, 2, 6 | 26 | the same cells: a rebuild cannot recover the multi-line runs |
| material store before W4-T4 (`--check --material`) | 10, 10, 5, 11, 2, 11 | 23 | Q1 on two sessions (W4-T1/1, W3-E08b/1, one short each: a chain over `MAX_COMMAND`'s 200 characters with pytest at its end, the key is right); Q2 on five (Telltale withholds a failure count whose exit status is masked and says so: arm S is expected to answer unknown); Q10 on all six (a stream-only capture reports a message-start snapshot, 1902 for 141206: W4-T4) |

| material store after W4-T4 (#43), rebuilt 2026-09-03 03:20, capture ids in manifest.json | 10, 10, 5, 11, 2, 11 | 29 | Q1 on the same two sessions; Q2 on the same five; Q10 agrees on all six (141206, 122438, 67988, 99305, 153943, 89125) |
| material store after W4-F2 (#44, MAX_COMMAND 512), rebuilt 2026-09-03, capture ids in manifest.json | 10, 11, 6, 11, 2, 11 | 31 | Q2 on five sessions only, the designed withholding; Q1 and Q10 agree on all six |
| material store after W4-F3 (#49, 2026-09-03; `make_key.py experiments/E12/manifest.json --check --material`) | 10, 11, 6, 11, 2, 11 | 30 | Q2 on all six: Telltale prints null where any test run's exit status is masked (every test run of every one of the six sessions pipes into tail) and moves the stated-run counts into the warning; arm S is expected to answer unknown. W3-E08/1's earlier Q2 agreement (0 = 0) was a count over two masked runs, so 31 of 36 overstated it by one cell |

Rule: E12 does not run until, against the material store, Q1 and Q10 agree on all six
or every remaining difference is explained in this table. Both agree on all six since
W4-F2 raised the stored-command bound to 512 (the two Q1 cases were that bound); Q2 is
withheld by design where the exit status is masked and arm S is expected to say unknown.

## Questions

The ten in `key.json` `questions`. Answers are a number, yes/no, `success|error after N
turns`, or the word `unknown`.

## Pre-registered decision rule

- Per question and arm: agreement = the number of sessions (of 6) whose answer equals
  the key exactly. An `unknown` counts as disagreement, and is tallied separately as
  "declared unknown" because in arm S it is the designed answer wherever Telltale's
  coverage says partial (a masked exit status, W3-T3).
- Per question: "summary sufficient" when arm S agrees on at least as many sessions as
  arm R; "summary insufficient" when arm S agrees on fewer; "neither" when both agree on
  fewer than 4 of 6. No pooled score across questions is computed: the ten questions
  are not one thing.
- Cost: per reviewer session, wall time and total tokens from the launcher's own
  capture (the reviewer runs under `telltale run --experiment E12`), reported as the
  median and the full sorted list per arm. H1's claim about cost is a comparison of
  these two lists, stated with n = 6 per arm and no significance test at that n.
- What the result can support: a statement about these six sessions, this
  questionnaire and one reviewer model, claim class comparative. It cannot support a
  statement about sessions in general or about human reviewers.

## Sessions

- Pilot: one session (W4-T1/1, the smallest raw file that still has failures in it)
  in both arms: 2 Opus sessions. Measures wall, tokens, and whether the raw arm can
  answer at all from a 1.4 MB file inside `--max-turns 60`.
- Full: six sessions in both arms: 12 Opus sessions (the pilot pair is re-run inside the
  full set so every session sees the same prompt version).
- STOP rules: 10 minutes or 400,000 tokens per reviewer session; stop the arm if the
  pilot reviewer of that arm answers fewer than 5 of 10 questions.

Amended 2026-09-03 after the pilot: the per-session token bound is 2,000,000 (was
400,000). The pilot's raw arm spent 588,848 tokens on the 1.44 MB stream (cache reads
534,249) and the largest stream is 1.97 MB; the owner approved the raised bound on
2026-09-03. The wall bound of 10 minutes and the answered-questions bound are unchanged.

## Prerequisites

1. W4-T3 merged and `telltale rebuild` run on the home store: done 2026-09-03 (#42,
   3229 captures in 63 s).
2. W4-T4 merged, `build_store.py` re-run, `make_key.py --check --material` showing Q10
   agreeing on all six: done 2026-09-03 (#43). W4-F2 (#44) then closed the two Q1 cases;
   the material store was rebuilt again and the check is 31 of 36 (the table above). After
   W4-F3 (#49) the store was rebuilt once more and the check is 30 of 36, the drop being the
   Q2 cell that had agreed by coincidence.
3. Owner approval of the 14 sessions: given 2026-09-03 ("Approved all 4").
