---
description: Design and scaffold a Looper agent loop.
argument-hint: [target-dir]
allowed-tools: Read, Write, Bash, PowerShell
---

# /looper

Run the Looper skill as an explicit slash command.

Arguments from the user: `$ARGUMENTS`

## Resolve Looper

Find the Looper skill root before doing any loop-design work:

1. Prefer the global Claude Code skill install:
   - Windows: `%USERPROFILE%\.claude\skills\looper`
   - macOS/Linux: `$HOME/.claude/skills/looper`
2. If that directory does not contain `SKILL.md`, stop and tell the user to
   install Looper with the README instructions.
3. Read `SKILL.md` from that directory completely and follow its workflow
   exactly. Treat the located directory as `CLAUDE_SKILL_DIR` when running
   helper scripts.

## Target Directory

- If `$ARGUMENTS` is empty, use `./looper-output`.
- Otherwise, treat `$ARGUMENTS` as the target directory argument.

Then continue with the Looper skill workflow.
