# commands/

This directory contains the upstream Claude Code slash command for Looper.
**It is not used by the Hermes install path.** Hermes has no slash-command
system; the equivalent on Hermes is `skill_view(name='looper')` plus reading
the body of `../SKILL.md`.

The file is preserved unchanged from upstream for two reasons:

1. **Dual install**: if a user has both Claude Code and Hermes on the same
   machine, Claude Code still picks this up at `$HOME/.claude/commands/looper.md`
   (via `install.sh`'s copy step) and Hermes picks up the SKILL.md from
   `$HOME/.hermes/skills/looper/`.
2. **Sync hygiene**: keeping the file byte-identical to upstream makes
   `git diff upstream/main -- commands/looper.md` trivially empty, so future
   `git merge upstream/main` rebase flows stay clean.

If you maintain a Hermes-only install and want to drop this file, do so — the
upstream install does not require it.
