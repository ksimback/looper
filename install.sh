#!/usr/bin/env bash
set -euo pipefail

repo_url="https://github.com/ksimback/looper"
claude_dir="${HOME}/.claude"
skills_dir="${claude_dir}/skills"
commands_dir="${claude_dir}/commands"
skill_dir="${skills_dir}/looper"
command_source="${skill_dir}/commands/looper.md"
command_target="${commands_dir}/looper.md"
venv_dir="${skill_dir}/.venv"

if ! command -v git >/dev/null 2>&1; then
  echo "Git is required to install Looper. Install Git, then rerun this command." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required to install Looper. Install Python 3, then rerun this command." >&2
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

if [ ! -d "${venv_dir}" ]; then
  python3 -m venv "${venv_dir}"
fi
"${venv_dir}/bin/python" -m pip install --upgrade pip >/dev/null
"${venv_dir}/bin/python" -m pip install "PyYAML>=6.0" >/dev/null

echo
echo "Looper installed."
echo "Python dependencies installed in ${venv_dir}."
echo "Restart Claude Code, then run /looper."
