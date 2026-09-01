# Telltale

Telltale is a local flight recorder for coding-agent sessions. It launches Claude Code or
Codex for you, listens on a loopback port for the telemetry, hooks and stream output that
the agent already emits, and writes a sanitized, append-only record of what happened: which
model requests were made and what they cost in tokens, which tools ran, which files were
read and edited, which verification commands passed or failed, when the context was
compacted, and what the repository looked like at each of those moments. Prompt text,
assistant text, tool results, file contents, command output and environment variable values
are never stored at any capture level. Nothing outside this repository and
`$TELLTALE_HOME` is ever written or edited: the settings that switch capture on travel with
the launched process and disappear with it.

The point of the record is that a number can be traced back to what produced it, so every
derived value carries a claim class and nothing downstream may present it as stronger
evidence than it is. `derived` means computed from this capture's own observations.
`comparative` means it puts two captures or a capture and a cohort side by side.
`associative` means two things moved together, which is not a statement about cause.
`predictive` means a forecaster produced it. There is no `observed` class for a computed
number and no `causal` class at all, a claim class is never upgraded, and a value that was
not observed stays unknown rather than becoming zero, so "no compactions happened" and "this
surface cannot see compaction" remain different answers. The forecasting laboratory that
consumes these records is optional, is kept behind an extra, and may legitimately conclude
that the signal it looks for is not there.

Nothing above works yet. This is the first commit: the package, the gates and the
documentation are in place, and the capture path arrives with the tasks that follow.
`uv sync` installs the environment and `uv run telltale doctor` is where to start once it
does something: it will round-trip a synthetic event through every receiver endpoint,
check that git, the agent binaries and the optional model stack are reachable, print one
line per surface and exit non-zero naming the first surface that failed. Today it prints
`doctor: not implemented` and exits 2, because a self-check that has not been written must
not report success.
