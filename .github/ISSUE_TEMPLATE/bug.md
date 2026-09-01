---
name: Bug
about: Something Telltale recorded, reported or refused is wrong
title: ''
labels: bug
assignees: ''
---

<!--
Do not paste prompt text, assistant text, file contents, command output or environment
variable values into this issue. If the bug is that one of those reached storage, report it
privately instead: see SECURITY.md. Describe the shape of the value, never the value.

The five headings below are the same order every task report in docs/log/ uses. The two
that matter most to somebody who was not there are WHAT WAS WRONG and UNSURE OR NOT DONE,
so they cannot be buried.
-->

## OUTCOME

What you ran and what happened. Exact command, exact output, exit code.

## WHAT WAS WRONG

What you expected instead, and why. If a number looked wrong, say which number, what claim
class it carried, and what it should have been. If a claim class looked wrong, say which
layer produced the value.

## WHAT CHANGED

What you had already changed or tried before this appeared: a provider upgrade, a different
model or effort, a new tool or MCP server, a different content level, a different
repository. If nothing changed, say so.

## UNSURE OR NOT DONE

What you could not check. Anything you are guessing at rather than measuring.

## NEXT

What you think should happen, and what you can supply if asked: a capture id, a fixture, a
reproduction.

## Environment

- Telltale version: output of `uv run telltale --version`
- Provider and runtime version:
- Operating system and Python version:
- Content level, if the capture path was involved:
