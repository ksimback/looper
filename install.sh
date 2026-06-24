#!/usr/bin/env bash
# install.sh — install Looper (Hermes port) for Hermes Agent and/or Claude Code.
#
# Default install root: $HOME/.hermes/skills/looper
# Also copies commands/looper.md to $HOME/.claude/commands/ if $HOME/.claude
# exists, so a user with both Hermes and Claude Code can use the same checkout.
#
# Override the source repo with LOOPER_REPO_URL (default: alvroble/loopermes).

set -euo pipefail

repo_url="${LOOPER_REPO_URL:-https://github.com/alvroble/loopermes}"
home_dir="${HOME}"

hermes_dir="${home_dir}/.hermes"
claude_dir="${home_dir}/.claude"

hermes_skill_dir="${hermes_dir}/skills/looper"
hermes_skills_dir="${hermes_dir}/skills"

# Claude Code: optional. Only if the user already has Claude Code installed
# (presence of $HOME/.claude) do we also wire the slash command.
claude_skill_dir="${claude_dir}/skills/looper"
claude_skills_dir="${claude_dir}/skills"
claude_commands_dir="${claude_dir}/commands"
claude_command_target="${claude_commands_dir}/looper.md"

# Where the venv lives. We put it inside the primary install (Hermes), and
# reuse it from Claude Code if Claude Code is also present and the install
# paths are the same checkout.
venv_dir="${hermes_skill_dir}/.venv"

if ! command -v git >/dev/null 2>&1; then
  echo "Git is required to install Looper. Install Git, then rerun this command." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required to install Looper. Install Python 3, then rerun this command." >&2
  exit 1
fi

# 1. Make sure the Hermes skills/ directory exists.
mkdir -p "${hermes_skills_dir}"

# 2. Install or update the skill checkout.
if [ -e "${hermes_skill_dir}" ]; then
  if [ -d "${hermes_skill_dir}/.git" ]; then
    echo "Updating Looper at ${hermes_skill_dir}"
    # Stash any local edits to tracked files (we never want git pull to clobber
    # user work). We only stash; untracked files are kept as-is.
    git -C "${hermes_skill_dir}" stash push -u -m "looper-install-$(date +%s)" >/dev/null 2>&1 || true
    git -C "${hermes_skill_dir}" pull --ff-only
    # Pop the stash back. If conflicts arise, the user resolves manually.
    git -C "${hermes_skill_dir}" stash pop >/dev/null 2>&1 || true
  else
    echo "Install target already exists and is not a Git checkout: ${hermes_skill_dir}" >&2
    exit 1
  fi
else
  echo "Installing Looper to ${hermes_skill_dir}"
  git clone "${repo_url}" "${hermes_skill_dir}"
fi

# 3. Create the venv + install PyYAML (Python 3.10+ compatible).
if [ ! -d "${venv_dir}" ]; then
  python3 -m venv "${venv_dir}"
fi
"${venv_dir}/bin/python" -m pip install --upgrade pip >/dev/null
"${venv_dir}/bin/python" -m pip install "PyYAML>=6.0" >/dev/null

# 4. Optional Claude Code wiring: if $HOME/.claude exists, also set up the
#    slash command. The skill itself lives at $HOME/.hermes/skills/looper, so
#    users on Claude Code will see Looper in the Hermes skill directory but
#    will be able to invoke /looper as a slash command. If they want the
#    upstream Claude Code path (skill at $HOME/.claude/skills/looper), they
#    should run the upstream install script directly.
if [ -d "${claude_dir}" ]; then
  if [ -f "${hermes_skill_dir}/commands/looper.md" ]; then
    mkdir -p "${claude_commands_dir}"
    cp "${hermes_skill_dir}/commands/looper.md" "${claude_command_target}"
    echo "Wired /looper slash command to ${claude_command_target}."
  else
    echo "Warning: ${hermes_skill_dir}/commands/looper.md not found; slash command not wired." >&2
  fi
fi

cat <<EOF

Looper installed.
  Skill root:    ${hermes_skill_dir}
  Venv:          ${venv_dir}
  Python:        $(${venv_dir}/bin/python --version 2>&1)
  PyYAML:        $(${venv_dir}/bin/python -c "import yaml; print(yaml.__version__)" 2>&1)

Next steps:
  - In Hermes: the skill loads automatically as 'looper'. Invoke by saying
    "design a loop" or "scaffold an agent loop", or by name from any agent.
  - In Claude Code: restart, then run /looper.

Override the source repository by setting LOOPER_REPO_URL before running.
EOF
