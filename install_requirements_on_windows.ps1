
# install_requirements_on_windows.ps1 — PowerShell-only manager (uses core\.venv exclusively)
# ==============================================================================
# How to use this script
# ==============================================================================
# > If needed for this session:
# Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# > Run:
# .\install_requirements_on_windows.ps1
# ==============================================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Print-Section([string]$ColorName, [string]$Title) {
  Write-Host "==============" -ForegroundColor $ColorName
  Write-Host $Title           -ForegroundColor $ColorName
  Write-Host "--------------" -ForegroundColor $ColorName
}

function Get-PipCmd {
  if (Get-Command pip -ErrorAction SilentlyContinue)    { return @('pip') }
  if (Get-Command python -ErrorAction SilentlyContinue) { return @('python','-m','pip') }
  if (Get-Command py -ErrorAction SilentlyContinue)     { return @('py','-3','-m','pip') }
  throw "No pip/python found on PATH. Install Python 3 and ensure 'pip' or 'python' or 'py' is available."
}

function Invoke-PipInstall([string]$ReqFile, [string]$DisplayPath) {
  Print-Section "Cyan"   "📦 Installing from: $DisplayPath"
  Print-Section "Yellow" "📋 REQUIREMENTS"
  Get-Content -LiteralPath $ReqFile | ForEach-Object { Write-Host $_ }
  Write-Host ''

  Print-Section 'Green'  '⚙️ LOGS'

  # Force array to avoid single-item unwrapping
  $pip = @(Get-PipCmd)
  $cmd = $pip[0]
  $prefixArgs = @()
  if ($pip.Length -gt 1) { $prefixArgs += $pip[1..($pip.Length-1)] }

  $args = @()
  $args += $prefixArgs
  $args += @('install','-r', $ReqFile)

  & $cmd @args
  if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed installing from $DisplayPath" -ForegroundColor Red
    exit 1
  }

  Write-Host "✅ Successfully installed from $DisplayPath" -ForegroundColor Green
  Write-Host ""
}

# Collect requirement files: .\requirements.txt agents\*\requirements.txt and api_library\*\requirements.txt
$files = @()

if (Test-Path -LiteralPath (Join-Path $PSScriptRoot "agents")) {
  $files += Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot "agents") -Recurse -Filter "requirements.txt" -ErrorAction SilentlyContinue |
            Where-Object { -not $_.PSIsContainer }
}

if (Test-Path -LiteralPath (Join-Path $PSScriptRoot "api_library")) {
  $files += Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot "api_library") -Recurse -Filter "requirements.txt" -ErrorAction SilentlyContinue |
            Where-Object { -not $_.PSIsContainer }
}

# Root requirements.txt next to the script
$rootReq = Join-Path $PSScriptRoot "requirements.txt"
if (Test-Path -LiteralPath $rootReq) {
  $files += Get-Item -LiteralPath $rootReq
}

if (-not $files -or $files.Count -eq 0) {
  Write-Host -ForegroundColor Yellow "No requirements.txt files found under agents/*, api_library/*, or next to this script."
  return
}

foreach ($f in $files) {
  Invoke-PipInstall -ReqFile $f.FullName -DisplayPath $f.FullName
}