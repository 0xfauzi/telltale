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
  `telltale vector` for the session's capture, written to files named `material/
  summary.txt`, `material/timeline.txt`, `material/vector.txt`.
- Arm R (raw): the stream-json file copied to `material/session.jsonl`.
- Blinding: the reviewer's working directory holds the material and nothing else; the
  prompt names no task, no session, no arm and no date. The two arms get the same ten
  questions and the same output format.

## The key

`key.json`, derived from the raw streams by `make_key.py` (rules in the file; heredoc
bodies are data, a pytest execution is a segment whose head is pytest). The key is
independent of Telltale. Its `--check` against `telltale show` on 2026-09-03: 25 of 36
comparable cells agree; every disagreement is Q1 or Q2, and the cause is a classifier
gap (a test segment after a known segment or a newline is not a verification run:
52 pytest executions in the six streams, 14 in Telltale), fixed by W4-T3. The check is
re-run after W4-T3 merges and the store is rebuilt; E12 does not run until Q1 agrees on
all six or every remaining difference is explained in this file.

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

## Prerequisites

1. W4-T3 merged and `telltale rebuild` run on the home store (the classifier version
   changes, so every capture's verification measures change).
2. `make_key.py --check` re-run and the agreement recorded here.
3. Owner approval of the 14 sessions.
