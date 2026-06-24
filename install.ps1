$ErrorActionPreference = "Stop"

# install.ps1 — Windows installer for Looper (Hermes port).
# Same dual-install behavior as install.sh: prefers $HOME/.hermes/skills/looper,
# also wires /looper slash command to $HOME/.claude/commands/ if Claude Code
# is already installed.

$RepoUrl = if ($env:LOOPER_REPO_URL) { $env:LOOPER_REPO_URL } else { "https://github.com/alvroble/loopermes" }
$HomeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }

$HermesDir = Join-Path $HomeDir ".hermes"
$ClaudeDir = Join-Path $HomeDir ".claude"

$HermesSkillDir = Join-Path $HermesDir "skills/looper"
$HermesSkillsDir = Join-Path $HermesDir "skills"

$ClaudeCommandsDir = Join-Path $ClaudeDir "commands"
$ClaudeCommandTarget = Join-Path $ClaudeCommandsDir "looper.md"

$VenvDir = Join-Path $HermesSkillDir ".venv"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required to install Looper. Install Git, then rerun this command."
}

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    $Python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $Python) {
    throw "Python 3 is required to install Looper. Install Python 3, then rerun this command."
}

New-Item -ItemType Directory -Force -Path $HermesSkillsDir | Out-Null

if (Test-Path $HermesSkillDir) {
    if (Test-Path (Join-Path $HermesSkillDir ".git")) {
        Write-Host "Updating Looper at $HermesSkillDir"
        git -C $HermesSkillDir pull --ff-only
    } else {
        throw "Install target already exists and is not a Git checkout: $HermesSkillDir"
    }
} else {
    Write-Host "Installing Looper to $HermesSkillDir"
    git clone $RepoUrl $HermesSkillDir
}

if (-not (Test-Path $VenvDir)) {
    & $Python.Source -m venv $VenvDir
}
$VenvPython = Join-Path $VenvDir "Scripts/python.exe"
& $VenvPython -m pip install --upgrade pip | Out-Null
& $VenvPython -m pip install "PyYAML>=6.0" | Out-Null

if (Test-Path $ClaudeDir) {
    $CommandSource = Join-Path $HermesSkillDir "commands/looper.md"
    if (Test-Path $CommandSource) {
        New-Item -ItemType Directory -Force -Path $ClaudeCommandsDir | Out-Null
        Copy-Item $CommandSource $ClaudeCommandTarget -Force
        Write-Host "Wired /looper slash command to $ClaudeCommandTarget."
    } else {
        Write-Warning "Slash command source not found at $CommandSource; skipping Claude Code wiring."
    }
}

Write-Host ""
Write-Host "Looper installed."
Write-Host "  Skill root: $HermesSkillDir"
Write-Host "  Venv:       $VenvDir"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  - In Hermes: the skill loads automatically as 'looper'."
Write-Host "  - In Claude Code: restart, then run /looper."
