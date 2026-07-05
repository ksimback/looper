$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/ksimback/looper"
$HomeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$ClaudeDir = Join-Path $HomeDir ".claude"
$SkillsDir = Join-Path $ClaudeDir "skills"
$CommandsDir = Join-Path $ClaudeDir "commands"
$SkillDir = Join-Path $SkillsDir "looper"
$CommandSource = Join-Path $SkillDir "commands\looper.md"
$CommandTarget = Join-Path $CommandsDir "looper.md"
$VenvDir = Join-Path $SkillDir ".venv"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required to install Looper. Install Git, then rerun this command."
}

# Validate by executing, not just locating: on Windows, "python" on PATH is
# often the Microsoft Store alias stub, which exists but cannot run scripts.
# The probe must tolerate stub stderr output without tripping the script's
# ErrorActionPreference = "Stop".
$Python = $null
foreach ($Candidate in @("python", "py")) {
    $Command = Get-Command $Candidate -ErrorAction SilentlyContinue
    if (-not $Command) { continue }
    $PreviousEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $null = & $Command.Source --version 2>&1
        $Works = ($LASTEXITCODE -eq 0)
    } catch {
        $Works = $false
    } finally {
        $ErrorActionPreference = $PreviousEAP
    }
    if ($Works) {
        $Python = $Command
        break
    }
}
if (-not $Python) {
    throw "A working Python 3 is required to install Looper. Install Python 3 (not the Microsoft Store alias), then rerun this command."
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

if (-not (Test-Path $VenvDir)) {
    & $Python.Source -m venv $VenvDir
}
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip | Out-Null
& $VenvPython -m pip install "PyYAML>=6.0" | Out-Null

Write-Host ""
Write-Host "Looper installed."
Write-Host "Python dependencies installed in $VenvDir."
Write-Host "Restart Claude Code, then run /looper."
