---
description: Design and scaffold a Looper agent loop.
argument-hint: [target-dir] [--template <name>]
allowed-tools: Read, Write, Bash
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

## Arguments

Parse `$ARGUMENTS` as `[target-dir] [--template <name>]`, in any order:

- If `--template` is present, take the next token as the template name and
  follow the skill's Template Mode (the catalog lives at
  `templates/loops/` inside the skill directory). `--template` with no
  name means: show the catalog and ask.
- The remaining token, if any, is the target directory. If none is left,
  use `./looper-output`.

Then continue with the Looper skill workflow.
