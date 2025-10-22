
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
$ErrorActionPreference = 'Stop'

function Print-Section([string]$ColorName, [string]$Title) {
  Write-Host '==============' -ForegroundColor $ColorName
  Write-Host $Title           -ForegroundColor $ColorName
  Write-Host '--------------' -ForegroundColor $ColorName
}

function Get-PipCmd {
  if (Get-Command pip -ErrorAction SilentlyContinue)    { return @('pip') }
  if (Get-Command python -ErrorAction SilentlyContinue) { return @('python','-m','pip') }
  if (Get-Command py -ErrorAction SilentlyContinue)     { return @('py','-3','-m','pip') }
  throw "No pip/python found on PATH. Install Python 3 and ensure 'pip' or 'python' or 'py' is available."
}

function Invoke-PipInstall([string]$ReqFile, [string]$DisplayPath) {
  Print-Section 'Cyan'   "📦 Installing from: $DisplayPath"
  Print-Section 'Yellow' '📋 REQUIREMENTS'
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
  Write-Host ''
}

# Collect requirement files: agents\*\requirements.txt and api_library\*\requirements.txt
$files = @()
$files += Get-ChildItem -Path 'agents' -Recurse -Filter 'requirements.txt' -File -ErrorAction SilentlyContinue
$files += Get-ChildItem -Path 'api_library' -Recurse -Filter 'requirements.txt' -File -ErrorAction SilentlyContinue

# Remove any nulls and dedupe
$files = $files | Where-Object { $_ } | Sort-Object FullName -Unique

if (-not $files -or $files.Count -eq 0) {
  Write-Host -ForegroundColor Yellow "No requirements.txt files found under agents/* or api_library/*"
  return
}

foreach ($f in $files) {
  Invoke-PipInstall -ReqFile $f.FullName -DisplayPath $f.FullName
}