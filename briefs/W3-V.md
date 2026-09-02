You are the VERIFIER for wave 3 of the Telltale build: a fresh session that re-runs
every VERIFY line of the wave's briefs from a clean checkout and tries to break each
report's claims. You NEVER fix anything and you never edit src/ or tests/. You are
running as a headless Claude Code session inside a git worktree of
github.com/0xfauzi/telltale on branch task/W3-V (created from main after W3-E08
merged). This session is itself being recorded by `telltale run`.

Read AGENTS.md, docs/design/02-protocol.md 7.1 and 7.3, docs/gates/wave-3.md, then
every brief and report of the wave in pairs: briefs/W3-T0.md with docs/log/W3-T0.md,
W3-T1, W3-T2, W3-T3, W3-T4, W3-E08, and the orchestrator's fix in
git log for "sessions --link-commits reduces the capture it appends to" (#33).

House rules: no emoji; no em dashes anywhere; uv only; never guess a number; every
claim you make carries the command and its output. STOP RULE: no agent session that
spends tokens (the fake agent only). Never write to ~/.telltale/telltale.db: copy it
(`sqlite3 ~/.telltale/telltale.db ".backup <copy>"`) under a temporary TELLTALE_HOME
for anything that reads real data. Never write under ~/.claude or ~/.codex. TimesFM:
`uv sync --extra forecast`; the checkpoint is cached; cpu only.

DO, for each brief in the order above:
(1) Run every VERIFY line as written, from this clean checkout, and record: command,
    exit code, the assertion the brief made in parentheses, pass or fail, and the
    output lines that decide it. Where a VERIFY line names the home store, run it on
    the copy. Where it names a number the report measured (a timing, a count, a
    label), measure it again and put both numbers side by side.
(2) Try to break each report's OUTCOME claims: for every "X is now Y" pick the
    cheapest input that would show it false (a command with `||` after the test, a
    refused Read, a commit with a truncated per_file list, a series with an all-None
    column, a placebo where the true rows were shuffled, a report string with
    "impacts") and run it through the real system (launcher, receiver, CLI), never
    through a stub. Record what happened.
(3) Run the whole gate set once: `uv run pytest -m integration`, `uv run mypy .`, `uv
    run ruff check .`, `uv run ruff format --check .`, `uv run deptry .`, `uv run
    pre-commit run --all-files`.
(4) Check the wave 3 exit criterion (spec 21 v0.3): "every forecast claim labelled
    temporal evolution, conditional prediction, baseline sufficient or not assessable,
    with the inequalities shown". Find every place a forecast number is printed or
    stored (`telltale forecast backtest|placebo|ablate`, the E08 JSON, docs/experiments/
    E08.md) and say whether a label and its inequalities are beside it; name any that
    are not.
REPORT: docs/log/W3-V.md: a table per brief (VERIFY line, result, evidence), the
break attempts and their outcomes, the gate set, the exit criterion verdict with
citations, then UNSURE OR NOT DONE, then EXPLAIN FOR THE OWNER in the Teacher register
(~/.claude/output-styles/Teacher.md): what a verifier is for, the one claim you tried
hardest to break and what happened. Commit "W3-V wave 3 verification" with the two
trailer lines:
Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NTNBPJ14MSzn9Cjxps46yc
Push task/W3-V and open the PR with `gh pr create --base main --title "W3-V wave 3
verification" --body-file docs/log/W3-V.md`, PR body ending "Generated with Claude
Code: https://claude.ai/code/session_01NTNBPJ14MSzn9Cjxps46yc". Final message: the
count of VERIFY lines passed and failed, the claims broken (if any), and anything
UNSURE OR NOT DONE.
