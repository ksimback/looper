#!/usr/bin/env bash
set -euo pipefail

repo_url="https://github.com/ksimback/looper"
claude_dir="${HOME}/.claude"
skills_dir="${claude_dir}/skills"
commands_dir="${claude_dir}/commands"
skill_dir="${skills_dir}/looper"
command_source="${skill_dir}/commands/looper.md"
command_target="${commands_dir}/looper.md"

if ! command -v git >/dev/null 2>&1; then
  echo "Git is required to install Looper. Install Git, then rerun this command." >&2
  exit 1
fi

mkdir -p "${skills_dir}" "${commands_dir}"

if [ -e "${skill_dir}" ]; then
  if [ -d "${skill_dir}/.git" ]; then
    echo "Updating Looper at ${skill_dir}"
    git -C "${skill_dir}" pull --ff-only
  else
    echo "Install target already exists and is not a Git checkout: ${skill_dir}" >&2
    exit 1
  fi
else
  echo "Installing Looper to ${skill_dir}"
  git clone "${repo_url}" "${skill_dir}"
fi

if [ ! -f "${command_source}" ]; then
  echo "Could not find slash command file after install: ${command_source}" >&2
  exit 1
fi

cp "${command_source}" "${command_target}"

echo
echo "Looper installed."
echo "Restart Claude Code, then run /looper."
