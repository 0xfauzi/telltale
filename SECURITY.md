# Security and privacy

Telltale records coding-agent sessions on the machine that runs them. The interesting
failure here is not a remote exploit, it is a private string reaching a database file that
somebody later shares. This document says what the system is designed to keep, what it is
designed never to keep, and how to report it when that design fails.

## What Telltale stores

Level 1, engineering metadata, is the default.

- Provider, runtime version, model, reasoning effort and sandbox posture, as values or as
  sha256 hashes.
- Token and usage quantities, compaction events, session and turn boundaries.
- Tool names, tool decisions, and correlation ids such as `tool_use_id` and `request_id`.
- File paths relativized to the repository root. A path outside the repository becomes
  `<outside>/<sha256 prefix>`, so the home directory never survives. Level 0 drops paths
  entirely.
- Normalized commands: the executable basename, up to two bare subcommand tokens, and flag
  names with any `=value` stripped. Every other token becomes `_`.
- Exit codes, read from a Bash tool result's `Exit code N` line before that text is
  dropped.
- Diff statistics and hashes: files changed, additions, deletions, per-file patch hashes.
- Repository identity: root hash, HEAD, branch, base SHA, remote fingerprint, dirty-tree
  hash.

## What Telltale never stores, at any level

Prompt text. Assistant text. Reasoning. Tool result bodies. The `old_string`, `new_string`
and `content` fields of Edit, Write and MultiEdit. Command output. Environment variable
values. Patch content. The absolute path of the repository.

Four mechanisms enforce that, rather than describing it.

1. **Sanitization is an allowlist.** A provider field nobody has classified is dropped and
   counted as an unknown field, not kept. A new provider release that adds a field cannot
   leak it by default.
2. **Every surviving string is scrubbed and bounded.** Known secret shapes are replaced
   with `<redacted:N>` and listed beside the row: AWS access key ids, `sk-ant-` and `sk-`
   keys, GitHub `ghp_` and `gho_` tokens, PEM private key headers, `Bearer` tokens, and
   `key=value` pairs whose value is 32 or more characters of base64 or hex. Strings are
   bounded to 512 characters and a payload to 8 KB, with any truncation recorded.
3. **Capture is launcher-only.** Configuration reaches an agent through the launched
   process, its argv and its environment, and never through a file on disk that outlives
   the capture. Nothing outside this repository and `$TELLTALE_HOME` is written or edited,
   and `telltale setup` prints a snippet for the owner to paste rather than applying one.
4. **Privacy fixtures seed known fake secrets and source strings** and assert that none of
   them reaches storage at level 0 or level 1.

The receiver binds loopback only. There is no outbound exporter unless one is explicitly
configured, no model traffic is intercepted, and stored strings are never executed.

## Deleting a capture

`telltale purge <capture>` deletes that capture's rows from `observations` and
`diagnostics` by `capture_id` and then rebuilds the derived rows. `telltale purge
--diagnostics-older-than N` applies an age cutoff to diagnostics. No other deletion path
exists, by design: one door in through `store.py`, one door out through `purge`.

That command is specified in [`docs/design/01-design.md`](docs/design/01-design.md),
sections 6.5 and 6.13. It is not implemented yet, and no brief has assigned it a wave, so
this document does not name one. Until it lands, deleting the SQLite file under
`$TELLTALE_HOME` removes everything.

## Reporting a privacy leak or a vulnerability

Report it privately, through a GitHub security advisory on this repository:
https://github.com/0xfauzi/telltale/security/advisories/new

Please do not open a public issue for anything of this kind, and please do not paste the
leaked value itself. What helps most:

- The observation type and the surface it arrived on, for example
  `claude.hook.PostToolUse` on `hook`.
- The field name that carried the content, and the shape of the value rather than the
  value, for example "a 40 character hex string in `tool_parameters.token`".
- The provider and runtime version, from `telltale.environment`, so the report can be
  reproduced against the same surface.
- The content level the capture ran at.

A leak of content that this document says is never stored is treated as a defect in the
sanitizer allowlist, which means the fix is a new fixture that fails first and a hook or
schema change that makes the class of leak impossible, not a patch to the one field that
escaped.

## Supported versions

The project is pre-release. There is no released version to backport a fix to, so fixes
land on `main`.
