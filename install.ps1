$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/ksimback/looper"
$HomeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$ClaudeDir = Join-Path $HomeDir ".claude"
$SkillsDir = Join-Path $ClaudeDir "skills"
$CommandsDir = Join-Path $ClaudeDir "commands"
$SkillDir = Join-Path $SkillsDir "looper"
$CommandSource = Join-Path $SkillDir "commands\looper.md"
$CommandTarget = Join-Path $CommandsDir "looper.md"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required to install Looper. Install Git, then rerun this command."
}

New-Item -ItemType Directory -Force -Path $SkillsDir, $CommandsDir | Out-Null

if (Test-Path $SkillDir) {
    if (Test-Path (Join-Path $SkillDir ".git")) {
        Write-Host "Updating Looper at $SkillDir"
        git -C $SkillDir pull --ff-only
    } else {
        throw "Install target already exists and is not a Git checkout: $SkillDir"
    }
} else {
    Write-Host "Installing Looper to $SkillDir"
    git clone $RepoUrl $SkillDir
}

if (-not (Test-Path $CommandSource)) {
    throw "Could not find slash command file after install: $CommandSource"
}

Copy-Item $CommandSource $CommandTarget -Force

Write-Host ""
Write-Host "Looper installed."
Write-Host "Restart Claude Code, then run /looper."
