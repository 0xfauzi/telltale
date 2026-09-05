# Capturing a session: the two ways, and what each one costs

Telltale records what a coding agent did, so that later you can ask what changed between
two sessions and get an answer with evidence attached. This page is how you record one.

There are two ways, and the difference between them is who starts the agent. If you can
put a word in front of the command, use `telltale run`. If the session is one you start
yourself, in your own terminal, the way you always do, use the daemon. Nothing here
edits a file outside this repository and `~/.telltale/`, in either mode. That is a
decision, not an oversight, and the reason is at the end.

## Before anything: nothing is on until you turn it on

Telltale records only the process it starts, or only the sessions you have pasted a
snippet for. There is no background agent, no shim on your PATH, and no file of ours in
`~/.claude/`. If you run nothing from this page, nothing is recorded.

The database is one file. `~/.telltale/telltale.db`, or `$TELLTALE_HOME/telltale.db` if
you set that variable. Deleting it deletes every capture, and nothing else on your
machine changes.

## The first way: put `telltale run` in front of the command

```
uv run telltale run -- claude -p "fix the failing test" --output-format stream-json
```

Everything after `--` is the agent's own command line, untouched. The exit code you get
back is the agent's exit code. The bytes on stdout are the agent's bytes, in the agent's
order. If the agent dies from a signal, you get 128 plus the signal number, which is
what a shell would have given you.

That last paragraph is the whole promise of the wrapper, and it is worth being concrete
about how it is kept. Measured on this machine, ten runs each: `telltale run -- true`
takes 215 ms against 1.8 ms for `true` alone, so the wrapper costs about 213 ms once per
session. The first run after a reboot cost 306 ms, because most of the time is git and
git's caches were cold. Of the 213 ms, about 98 ms is git (reading the repository's
identity at the start and its diff at the end), 57 ms is starting Python and importing
Telltale, and 48 ms is stopping the little web server that receives the agent's
telemetry. A session that lasts minutes pays this once.

Running several at once is fine. Four captures started together finished in 244 ms in
total against 206 ms for one, and all four were recorded: one SQLite file, one writer
thread each, and a five second busy timeout that this never came close to using.

### What the wrapper does to the agent

Three things, and no others.

It adds flags to the command. For Claude Code that is a `--settings` block naming twelve
lifecycle hooks that point at a port on 127.0.0.1, and, in `-p` mode, a `--session-id` so
that hooks can be tied to this run. If you passed your own `--settings` as inline JSON,
yours is parsed and ours is merged into it, so your hooks survive. If you passed
`--settings` naming a FILE, Telltale gives up the hook surface rather than rewrite your
file, and records that it did.

It sets environment variables. The OpenTelemetry ones that make Claude Code export logs
and metrics to that same local port, and nothing that turns on content logging.

It removes two environment variables: `VIRTUAL_ENV` and `UV_PROJECT_ENVIRONMENT`. This
one is a fix for a real failure. When you run Telltale under `uv run`, the child inherits
a virtual environment that belongs to Telltale, and then `uv run pytest` inside the
agent's own repository picks up the wrong environment. Experiment E01 watched a session
spend a whole turn on `rm -rf .venv && uv sync` because of it. The names that were
removed are recorded in the capture, so a reader can see that the environment the agent
ran in was not exactly the one you had.

### Telling the launcher what the run was for

```
uv run telltale run --task-id W1-T1 --attempt 2 --experiment E04 -- claude -p "..."
```

These three are labels. They are stored on the capture and nothing reads them yet; they
exist so that when the comparison commands arrive, a run can be found by what it was
trying to do. `--commit <sha>` is different: it states that this run produced that
commit, and a stated commit outranks every guess Telltale would otherwise make about
which commits belong to which session.

### Seeing what was recorded

```
uv run telltale sessions
```

```
CAPTURE_ID                      PROVIDER  MODEL  STARTED              DURATION_MS  OBSERVATIONS  COVERAGE  COMMITS
------------------------------  --------  -----  -------------------  -----------  ------------  --------  -------
cap_01M1FSH838Z8294QJ3VD8MASNC  claude    -      2026-09-02 00:50:33  2208         5             0/3       0
```

Read the coverage column carefully, because it is the column that keeps Telltale honest.
`0/3` means three surfaces were configured and none of them delivered anything. That row
is a real one: it is `telltale run -- claude --version`, which starts no session, so
there was nothing to deliver. It is not the same as a session where compaction never
happened. "Nothing was seen here" and "this could not be seen here" are different
statements, and every number Telltale prints later carries which one it is.

A dash means unknown. It never means zero.

## The second way: the daemon, for the sessions you start yourself

Wrapping works when you drive the agent from a script. It does not work for the session
you start by typing `claude` in a terminal, and that is most of them.

For those, run one long-lived receiver and tell Claude Code about it once.

```
uv run telltale daemon
```

It stays in the foreground, prints the address it bound and the database it writes, and
then prints one line per session it starts recording. Ctrl-C stops it: it stops
accepting, writes what it already accepted, and closes the file. Here is a whole run:

```
telltale daemon: http://127.0.0.1:47399 -> /tmp/daemon-home/telltale.db
content level 1. Ctrl-C stops it. One line per capture follows.
capture cap_cf4c4732fd3b8f8a55b60871
```

That capture id is not random. It is derived from the session id Claude Code generated,
by hashing it, so the same session always lands in the same capture even if you restart
the daemon in the middle of it.

The daemon receives nothing until Claude Code is told where to send it. That is the part
Telltale will not do for you:

```
uv run telltale setup claude --print
```

This prints a JSON block. Paste it into `~/.claude/settings.json` yourself, merging it
with what is already there. `telltale setup claude --apply` exists and refuses, and the
refusal names the reason: your global configuration is yours. A recorder that edits it
is a recorder that can silently outlive its own uninstall, and the day the pasted block
points at a port nothing is listening on, you want to know that you pasted it.

The snippet is generated from the same launch plan `telltale run` uses, so the two modes
cannot drift apart.

Codex has the same two commands and the same rule:

```
uv run telltale setup codex --print
```

This prints TOML: four lines to paste into `~/.codex/config.toml`, each one of the `-c`
overrides the launcher passes to `codex exec`, in the one spelling E02 measured. The
daemon then receives Codex's OTel logs and metrics and tells them from Claude's by the
`service.name` each batch carries. Two things it does not receive. Codex hooks are
command hooks read from `<repo>/.codex/hooks.json`, and Telltale configures none; and
the rollout file Codex writes under `~/.codex/sessions` is not sent anywhere, so run
`telltale import codex-rollouts` afterwards to add that surface to the same store.

One thing the daemon cannot do: it does not know what repository you are in, because
nothing told it. A daemon capture has no repository identity, no environment fingerprint
and no diff. It has the session's own records. If you want the repository half, wrap the
run.

### Starting the daemon at login, if you want that

The daemon runs in the foreground and stops when you close the terminal. On macOS, a
launchd agent is how you make it start at login instead, and Telltale will print one:

```
uv run telltale setup claude --print --daemon --port 47311
```

That prints a `com.telltale.daemon` plist naming the absolute path of the `telltale` you
just ran, the port you gave it, and your `$TELLTALE_HOME` in `EnvironmentVariables`,
followed by the same settings snippet as without `--daemon`, pointing at that same port.
launchd starts a job with almost no environment, which is why both the path and the home
are spelled out: a bare `telltale` is a job that never spawns, and a missing
`TELLTALE_HOME` is a daemon recording into a different database from the one your reports
read.

It is PRINTED. Telltale does not write it, does not call `launchctl`, and never touches
`~/Library/LaunchAgents`, which is outside this repository and outside `$TELLTALE_HOME`
and is therefore read-only to Telltale exactly as `~/.claude/settings.json` is. To
install it, you save it and load it:

```
uv run telltale setup claude --print --daemon | sed -n '/<?xml/,/<\/plist>/p' \
  > ~/Library/LaunchAgents/com.telltale.daemon.plist
launchctl load ~/Library/LaunchAgents/com.telltale.daemon.plist
```

and `launchctl unload` on the same path stops it. The plist sets `KeepAlive`, so launchd
restarts the daemon if it exits; a daemon whose port is already held exits 1 with one
line saying which port and what holds it, and launchd will retry it no more than once
every 10 seconds (`launchd.plist(5)`). Both the line and the retries go to
`$TELLTALE_HOME/daemon.err`, which the plist names.

## What is stored, and what is never stored

Every capture holds observations, and an observation is one fact with a type, a
timestamp and a payload. There are six types Telltale writes itself: the repository it
ran in, the environment it ran under, that the capture started, a snapshot of the working
tree's diff, any commits linked to the session, and that the capture ended. Everything
else comes from the agent: telemetry events, lifecycle hooks, and the structured stream
when you asked for JSON output.

What never reaches the file, at any setting: prompt text, assistant text, the contents of
a file the agent read or wrote, the output of a command it ran, reasoning, and the values
of environment variables. Not "removed afterwards" but never written: a field nobody has
put on an allowlist is dropped and its name is recorded, so a new field in a new version
of Claude Code lowers what Telltale can see rather than leaking something new.

What is stored instead of content: lengths, counts, hashes, tool names, file paths made
relative to the repository, and commands in a normalized form (`uv run pytest _` rather
than the command line you typed). Paths outside the repository become
`<outside>/<8 characters of hash>`, so "the agent read two files somewhere else" survives
and the place does not.

Three settings control this, and the middle one is the default:

- `--level 0` keeps no paths at all and only command basenames.
- `--level 1` is the default and is what the description above is.
- `--level 2` exists for a future retention mode and today records the same as level 1.

You can set the default in `~/.telltale/config.json` as `{"content_level": 0}`.

## How to delete a capture

Today: delete the file.

```
rm ~/.telltale/telltale.db
```

That is the whole retention story at the moment, and the honest version of it. To delete
one capture and keep the rest (W1-T4):

```
uv run telltale purge cap_01M1FSH838Z8294QJ3VD8MASNC
purged cap_01M1FSH838Z8294QJ3VD8MASNC: 25 observations, 0 diagnostics
```

An id this database does not hold is a refusal with exit code 2, not a silent zero.

## When nothing was recorded

Start with the built-in check. It sends one synthetic record through every route,
reads each one back, and tells you which surface did not survive the trip:

```
uv run telltale doctor
```

If doctor is clean and a session still recorded nothing, the capture will say so itself.
Every failure inside Telltale during a capture becomes a row in the `diagnostics` table
rather than an error the agent sees, because the alternative is a recorder that can break
the thing it is recording. Read them:

```
uv run python -c "
from telltale.store import Store
from telltale import config
for row in Store(config.db_path()).diagnostics():
    print(row['ingest_ts'], row['kind'], row['detail'][:160])"
```

The kinds you will see are `launcher` (Telltale's own plumbing: an unsupported provider,
a snapshot that failed, a record nothing could attribute), `unknown_field` (the agent
sent a field no allowlist knows, and it was dropped), `dropped` (the writer could not
keep up and said so), and `parse_failure` (a body the parser could not read).

`doctor` prints the most recent `launcher` row itself, on a line that begins `last
launcher diagnostic:`, because that is the kind that means the recorder failed rather
than the agent. One of its shapes is worth knowing: `the store lost part of this
capture: 1 batch(es) the writer gave up on`. That is a second process holding the
database's write lock for longer than the writer's retry ladder, which is about 39
seconds, and everything queued after it is gone. Two `telltale run`s at once do not
cause it; a `sqlite3` session left open on `telltale.db` with an uncommitted write does.

To see which agent versions this database has captures of, and what each one turned out
to be observable through:

```
uv run telltale doctor --matrix
```

One row per provider and runtime version, with the coverage word each capability got
counted across the captures of that version, and the provider's DRIFT list once. On the
owner's store that is 75 rows spanning Claude Code 2.1.185 to 2.1.259 and Codex 0.39.0
to 0.150.1. A capture whose version nothing recorded is its own row with `-` for the
runtime, rather than being folded into a version nobody measured.

## The one thing to remember

Telltale is loud about what it could not see and quiet about what it saw. A dash in a
table is a missing measurement, a coverage fraction is how much of the plan actually
delivered, and neither ever becomes a zero on the way to a number you might act on.
