# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions here are research gates, not feature milestones. Section 21 of
[`docs/spec/telltale-architecture.md`](docs/spec/telltale-architecture.md) states what each
one has to demonstrate before it is called done, and [`docs/gates/`](docs/gates/) records
whether it was. Nothing has been released: the version in `pyproject.toml` stays `0.0.1`
and no tag is cut. Which version that release becomes is the owner's decision at the
public flip, not a task's.

## [Unreleased]

One line per merged pull request, grouped by the wave its task id names, in merge order.
`gh pr list --state merged` is the record; 59 pull requests have merged.

### Wave 0: capture feasibility (v0.0)

- #2 W0-T6 brand and public-ready docs
- #3 W0-T3 repo and env observers
- #4 W0-E01 Claude capture feasibility
- #5 W0-T2 model, store, sanitizer
- #6 W0-T4 receiver and Claude provider
- #7 W0-E02 Codex capture feasibility
- #8 W0-T5 privacy, fail-open, doctor

### Dependencies

- #1 Bump astral-sh/setup-uv from 10.0.0 to 10.0.1

### Wave 1: flight recorder, experiment harness, TimesFM smoke (v0.1)

- #10 W1-E03 TimesFM-3 smoke on this machine
- #9 W1-T1 launcher, daemon, sessions
- #12 W1-T2 activities, timeline, show, explain
- #14 W1-T5 request-clock series
- #13 W1-T4 repeat runner and fake agent
- #11 W1-T3 Codex provider
- #15 W1-T6 forecast core
- #16 W1-E04 perturbation and fail-open under load

### Wave 2: measures and stochastic bounds (v0.2)

- #17 W2-T1 measures and the session summary
- #18 W2-T2 backfill importers
- #19 W2-T5 readiness preflight and missingness policies
- #20 W2-T3 environment runner
- #21 W2-T6 commit linkage and 2.1.258 drift
- #22 W2-T7 verification columns of the request clock
- #23 W2-T4 compare, cohorts and the evidence vector
- #24 W2-E07 request-clock backtests on the build's own captures
- #25 W2-E05 repeated-task pilot: recovered from the store
- #26 W2-T8 command normalization keeps only flag-shaped tokens; resanitize
- #27 W2-E05 the re-run: five sessions under bypassPermissions
- #29 W2-E06 environment sensitivity (H3 pilot)

### Wave 3: outcome correlation, temporal validity (v0.3)

- #28 W3-T1 attempt and change clocks; telltale outcome
- #30 W3-T0 reads that scale with the capture; refused calls are not test runs
- #31 W3-T4 commits carry their paths; the change clock fills its last three columns
- #33 sessions --link-commits reduces the capture it appends to (orchestrator fix, no task id)
- #32 W3-T2 placebo, decision rule, ablation, candidate protocol
- #34 W3-T3 the reducer says unknown where it cannot know
- #35 W3-E08 temporal validity on captured data
- #36 W3-E08b the true-order comparison alone earns baseline sufficient
- #37 W3-V wave 3 verification
- #39 W3-VF close the wave 3 verifier's three findings

### Wave 4: controlled repository interaction research (v0.4)

- #38 W4-T1 probe runner and correctness scoring
- #40 W4-T2 repository work profiles
- #41 W4-F1 index evidence by capture: the delete in every rebuild was a scan
- #42 W4-T3 a verification command anywhere in a chain is a verification run
- #43 W4-T4 a stream snapshot is not an output token count
- #44 W4-F2 a stored command normal form is bounded at 512 characters, not 200
- #45 W4-E09 legibility probes on this repository
- #46 W4-T5 an instructions factor for the intervention runners
- #47 W4-E12 blinded session diagnosis
- #48 W4-E10 before and after an AGENTS.md rewrite under matched fingerprints
- #49 W4-F3 unknown outcomes make a verification count None; a tool error survives a masked chain
- #53 W4-E12 blinded session diagnosis: the full run

### Wave 5: candidate-conditioned one-step advisory (v0.5)

- #50 W5-T1 candidate features, post-merge columns, past-future covariates, forecast candidate
- #51 W5-E11 one-step candidate conditioning on the change clock
- #52 W5-T2 shadow advisory

### Wave 6: scenario horizons, policy experiments, hardening (v0.6)

- #54 W6-T1 scenario horizons
- #55 W6-F1 hf cache env and advise scored-run guard
- #57 W6-T4 compatibility matrix and hardening
- #56 W6-T3 export, purge, retention, schema
- #58 W6-T2 policy intervention regimes
- #59 W6-F2 preserve Codex tool outcomes on masked checks

[Unreleased]: https://github.com/0xfauzi/telltale/commits/main
