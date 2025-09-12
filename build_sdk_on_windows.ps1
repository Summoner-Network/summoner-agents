# build_sdk_on_windows.ps1 — PowerShell translation of your Bash manager
# Commands: setup [build|test_build] | delete | reset | deps | test_server | clean
# ==============================================================================
# How to use this script
# ==============================================================================
# > First, you may need to use the following command to allow the script to run:
# Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#
# > Then, you can run the script as follows:
# .\build_sdk_on_windows.ps1 setup
# .\build_sdk_on_windows.ps1 setup test_build
# .\build_sdk_on_windows.ps1 deps
# .\build_sdk_on_windows.ps1 test_server
# ==============================================================================
[CmdletBinding()]
param(
  [Parameter(Position=0)]
  [ValidateSet('setup','delete','reset','deps','test_server','clean')]
  [string]$Action = 'setup',

  # only used for: setup
  [Parameter(Position=1)]
  [ValidateSet('build','test_build')]
  [string]$Variant = 'build'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ─────────────────────────────────────────────────────
# Paths & Config
# ─────────────────────────────────────────────────────
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$CORE_REPO  = 'https://github.com/Summoner-Network/summoner-core.git'
$CORE_BRANCH = 'main'
$SRC = Join-Path $ROOT 'summoner-sdk'
$BUILD_FILE_BUILD = Join-Path $ROOT 'build.txt'
$BUILD_FILE_TEST  = Join-Path $ROOT 'test_build.txt'
$VENVDIR = Join-Path $ROOT 'venv'
$DATA = Join-Path $SRC 'desktop_data'

# ANSI colors (Windows Terminal / VS Code / Cursor support these)
$RED   = "`e[31m"
$GREEN = "`e[32m"
$RESET = "`e[0m"

# ─────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────
function Die($msg) { throw $msg }

function Get-PythonSpec {
  $candidates = @(
    @{ Program='python';  Args=@()     },
    @{ Program='py';      Args=@('-3') },
    @{ Program='python3'; Args=@()     }
  )
  foreach ($c in $candidates) {
    $cmd = Get-Command $c.Program -ErrorAction SilentlyContinue
    if ($cmd) {
      & $c.Program @($c.Args) -c 'import sys; raise SystemExit(0 if sys.version_info[0]==3 else 1)' | Out-Null
      if ($LASTEXITCODE -eq 0) { return $c }
    }
  }
  Die "Python 3 not found on PATH. Install Python 3 and ensure 'python' or 'py' is available."
}

function Resolve-VenvPaths([string]$VenvDir) {
  $exe = Join-Path $VenvDir 'Scripts\python.exe'
  $nix = Join-Path $VenvDir 'bin\python'
  if (Test-Path $exe) { return @{ Py=$exe; Bin=(Join-Path $VenvDir 'Scripts') } }
  if (Test-Path $nix) { return @{ Py=$nix; Bin=(Join-Path $VenvDir 'bin') } }
  return @{ Py=$null; Bin=$null }
}

function Ensure-Git {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "'git' not found on PATH."
  }
}

# Rewrites: "from tooling.X" / "from summoner.X"  → "from X"
function Rewrite-Imports([string]$pkg, [string]$dir) {
  Write-Host ("    🔎 Rewriting imports in {0}" -f $dir)
  $files = Get-ChildItem -Path $dir -Filter *.py -File -Recurse -ErrorAction SilentlyContinue
  foreach ($file in $files) {
    Write-Host ("    📄 Processing: {0}" -f $file.FullName)

    $content = Get-Content -Path $file.FullName -Raw -Encoding UTF8

    # Before — show lines that match the tooling/summoner import pattern
    Write-Host "      ↪ Before:"
    $beforeMatches = [regex]::Matches($content, '^[ \t]*#?[ \t]*from[ \t]+(tooling|summoner)\.[A-Za-z0-9_]+', 'Multiline')
    if ($beforeMatches.Count -gt 0) {
      $beforeMatches | ForEach-Object {
        Write-Host ("        {0}{1}{2}" -f $RED, $_.Value, $RESET)
      }
    } else {
      Write-Host "        (no matches)"
    }

    $original = $content
    $content = [regex]::Replace($content, '(^[ \t]*#?[ \t]*from[ \t]+)tooling\.([A-Za-z0-9_]+)', '$1$2', 'Multiline')
    $content = [regex]::Replace($content, '(^[ \t]*#?[ \t]*from[ \t]+)summoner\.([A-Za-z0-9_]+)', '$1$2', 'Multiline')

    # After — display changed lines (best effort)
    Write-Host "      ↪ After:"
    if ($content -ne $original) {
      $afterLines = New-Object System.Collections.Generic.List[string]
      $origLines  = $original -split "(`r`n|`n)"
      $newLines   = $content  -split "(`r`n|`n)"
      $count = [Math]::Min($origLines.Count, $newLines.Count)
      for ($i=0; $i -lt $count; $i++) {
        if ($origLines[$i] -ne $newLines[$i]) {
          if ($newLines[$i] -match '^[ \t]*#?[ \t]*from[ \t]+[A-Za-z0-9_]+') {
            $afterLines.Add($newLines[$i])
          }
        }
      }
      if ($afterLines.Count -eq 0) {
        # fallback: show any top-level "from <name>" lines
        [regex]::Matches($content, '^[ \t]*#?[ \t]*from[ \t]+[A-Za-z0-9_]+', 'Multiline') |
          Select-Object -First 6 |
          ForEach-Object { Write-Host ("        {0}{1}{2}" -f $GREEN, $_.Value, $RESET) }
      } else {
        $afterLines | ForEach-Object { Write-Host ("        {0}{1}{2}" -f $GREEN, $_, $RESET) }
      }
      Set-Content -Path $file.FullName -Value $content -Encoding UTF8
    } else {
      Write-Host "        (no visible changes)"
    }
  }
}

function Clone-Native([string]$url) {
  $name = [IO.Path]::GetFileNameWithoutExtension($url)
  Write-Host ("📥 Cloning native repo: {0}" -f $name)
  $dest = Join-Path $ROOT ("native_build/{0}" -f $name)
  git clone --depth 1 $url $dest
}

# Merge repo's tooling/<pkg> into $SRC/summoner/<pkg>
function Merge-Tooling([string]$repoUrl, [string[]]$features) {
  $name = [IO.Path]::GetFileNameWithoutExtension($repoUrl)
  $srcdir = Join-Path $ROOT ("native_build/{0}/tooling"_
