# transcript: a synthetic backfill fixture

Hand-written by W2-T2 on 2026-09-02. NOT captured: an import reads the owner's own
sessions, and the owner's own sessions are the one thing that must never be copied into
this repository. Every shape here was measured first, over the owner's real
`~/.claude/projects` (1806 files, 1.7 GB, Claude Code 2.1.219 to 2.1.257), and the
measurements are in `providers/claude.py` DRIFT and `docs/log/W2-T2.md`.

## What is here

```
-telltale-fake-project/11111111-2222-4333-8444-555555555555.jsonl        34 lines
-telltale-fake-project/11111111-.../subagents/agent-a1b2c3d4e5f60718.jsonl  12 lines
-telltale-fake-project/vercel-plugin/skill-injections.jsonl               2 lines
```

The three shapes the real tree has, in the proportion it has them: 774 of the owner's
1806 files are `<slug>/<session>.jsonl`, 852 are `<slug>/<session>/subagents/agent-*.jsonl`
and 115 are `<slug>/vercel-plugin/skill-injections.jsonl`, which is not a session and
names no session id. The slug is invented; a real slug is the project's absolute path
with the separators changed, which is why nothing prints one.

The subagent file carries the PARENT session's `sessionId` on every line, every line has
`isSidechain: true`, and it shares no uuid with the parent file. That is what the owner's
files do (measured on a 4111-line parent and its 11 subagent files: 0 uuid overlap), and
it is why the importer gives the two files two captures.

## What the main transcript exercises

| line kind | why it is here |
| --- | --- |
| `queue-operation`, `mode`, `ai-title`, `permission-mode`, `attachment`, `file-history-snapshot`, `bridge-session` | seven of the 15 line kinds the parser does not read, counted as skipped |
| `user` with a prompt | prompt_length, and the prompt text that must not survive |
| `assistant` with usage | the four counters, `cache_creation.ephemeral_*`, model, effort, requestId |
| `tool_use` Bash | a command holding an absolute home path |
| `tool_use` Edit | `old_string` and `new_string`, both holding probes |
| `tool_use` Write | `content`, holding a private key header |
| `tool_result` with `is_error` | `Exit code 2` as the first line, which is the only place an exit code exists |
| `toolUseResult` | stdout, stderr, originalFile, oldString, newString, structuredPatch, and one `gitOperation.commit` |
| `system` `compact_boundary` | preTokens, postTokens, cumulativeDroppedTokens, durationMs |
| `system` `stop_hook_summary` | a system subtype that is not a compaction |
| `summary` | SYNTHETIC AND UNMEASURED: no summary line exists in any of the owner's 1806 files, on any version from 2.1.219 to 2.1.257, although the digest names one. It is here so the type is exercised at all, and no claim is made that a real transcript carries it. |
| a truncated last line | what a transcript gets when the process is killed mid-write: one parse_failure row, and the file still imports |
| `classifierMetaLines` | a field no allowlist entry knows, so the drop is named rather than silent |
| `userId`, `userEmail` | account identity on the transcript surface. Named as unknown fields until W9-F1, which put them in `sanitize.REFUSED`: they are still dropped, and the drop now reads `refused` rather than `unknown` |

## Probes

Every probe string in `../S3/MANIFEST.md` is present, byte for byte, and left in place:
a fixture without the probe cannot show that the probe was removed.

| probe | where it is |
| --- | --- |
| `sk-ant-api03-TELLTALEFAKE0000...` | the prompt, a tool_result body, a later user turn |
| `AKIATELLTALEFAKE00001` | a thinking block, a tool_result body, toolUseResult.stdout |
| `-----BEGIN TELLTALEFAKE PRIVATE KEY-----` | a Write's `content`, an originalFile, a tool_result body |
| `TELLTALEFAKE...0000`, `...1111`, `...2222` | an Edit's old_string and new_string, a queue-operation, a compaction summary, a hook summary, the subagent's Grep |
| `telltale-fake-user-id`, `user@telltale.invalid`, `user_telltalefakeaccount`, `00000000-0000-4000-8000-00000000000{1,2}` | a bridge-session line and two unlisted fields on a user line |

`<repo>` and `<home>` are the two machine paths, the same placeholders the E01 and E02
fixtures use. `tests/integration/test_import.py::materialise` substitutes a temporary git
repository and a temporary home back in, because a path that is not absolute is a path
the sanitizer cannot decide about.
