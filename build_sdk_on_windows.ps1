# build_sdk_on_windows.ps1 — PowerShell manager for building/Installing the Summoner SDK
# Commands: setup [build|test_build] | delete | reset | deps | test_server | clean | use_venv
# ==============================================================================
# How to use this script
# ==============================================================================
# > First, you may need to allow the script to run:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#
# > Then run:
#   .\build_sdk_on_windows.ps1 setup
#   .\build_sdk_on_windows.ps1 setup test_build
#   .\build_sdk_on_windows.ps1 deps
#   .\build_sdk_on_windows.ps1 test_server
#   . .\build_sdk_on_windows.ps1 use_venv   # dot-source to activate the repo venv in THIS session (optional)
# ==============================================================================

[CmdletBinding()]
param(
  [Parameter(Position=0)]
  [ValidateSet('setup','delete','reset','deps','test_server','clean','use_venv')]
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
  $pip = Join-Path $VenvDir 'Scripts\pip.exe'
  $scripts = Join-Path $VenvDir 'Scripts'
  if (Test-Path $exe) { return @{ Py=$exe; Pip=$pip; Bin=$scripts } }
  return @{ Py=$null; Pip=$null; Bin=$null }
}

function Ensure-Git {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "'git' not found on PATH."
  }
}

# Activate the repo venv in the current PowerShell process.
# Minimal helper: prefer to dot-source Activate.ps1 if present, otherwise set VIRTUAL_ENV and PATH.
function Activate-Venv {
  param([string]$VenvDir)

  $vp = Resolve-VenvPaths $VenvDir
  if (-not $vp.Py) { Die ("venv not found at {0}. Run setup first." -f $VenvDir) }

  $activatePS = Join-Path $VenvDir 'Scripts\Activate.ps1'
  if (Test-Path $activatePS) {
    try {
      . $activatePS
    } catch {
      Write-Warning ("Activation script failed: {0}" -f $_.Exception.Message)
      # fallback to manual env setup below
    }
  }

  # Ensure environment variables and PATH are set so python/pip resolve to venv
  Remove-Item Function:\python -ErrorAction SilentlyContinue
  Remove-Item Function:\pip   -ErrorAction SilentlyContinue

  $env:VIRTUAL_ENV = (Resolve-Path $VenvDir).ProviderPath
  $env:Path = "$($vp.Bin);$env:Path"

  # Verification
  # verification via temp script to avoid quoting/parsing issues
function Activate-Venv {
  param([string]$VenvDir)

  $vp = Resolve-VenvPaths $VenvDir
  if (-not $vp.Py) { Die ("venv not found at {0}. Run setup first." -f $VenvDir) }

  $activatePS = Join-Path $VenvDir 'Scripts\Activate.ps1'
  if (Test-Path $activatePS) {
    try {
      . $activatePS
    } catch {
      Write-Warning ("Activation script failed: {0}" -f $_.Exception.Message)
      # fallback to manual env setup below
    }
  }

  # Ensure environment variables and PATH are set so python/pip resolve to venv
  Remove-Item Function:\python -ErrorAction SilentlyContinue
  Remove-Item Function:\pip   -ErrorAction SilentlyContinue

  $env:VIRTUAL_ENV = (Resolve-Path $VenvDir).ProviderPath
  $env:Path = "$($vp.Bin);$env:Path"

  # Verification
  $verifyPy = @'
import sys, os
print("python executable:", sys.executable)
print("sys.prefix:", os.path.abspath(sys.prefix))
'@
  $tmpVerify = [IO.Path]::Combine($env:TEMP, "verify_python_prefix.py")
  $verifyPy | Set-Content -Path $tmpVerify -Encoding UTF8
  & $vp.Py $tmpVerify
  Remove-Item $tmpVerify -ErrorAction SilentlyContinue

  Write-Host ("Activated venv at: {0}" -f $VenvDir)
}

  Write-Host ("Activated venv at: {0}" -f $VenvDir)
}

# Rewrites ONLY: "from tooling.X"  → "from summoner.X"
# Leaves any existing "summoner.*" imports untouched.
# Shows before/after lines for visibility.
function Rewrite-Imports([string]$pkg, [string]$dir) {
  Write-Host ("    Rewriting imports in {0}" -f $dir)
  $files = Get-ChildItem -Path $dir -Filter *.py -File -Recurse -ErrorAction SilentlyContinue
  foreach ($file in $files) {
    Write-Host ("    Processing: {0}" -f $file.FullName)

    $lines = Get-Content -LiteralPath $file.FullName -Encoding UTF8
    if ($null -eq $lines) { $lines = @() }

    $changed = $false
    Write-Host "      -> Before:" -ForegroundColor Yellow
    $foundAny = $false
    foreach ($line in $lines) {
      if ($line -match '^[ \t]*#?[ \t]*from[ \t]+tooling\.[A-Za-z0-9_]+') {
        $foundAny = $true
        Write-Host ("        {0}" -f $line) -ForegroundColor Red
      }
    }
    if (-not $foundAny) {
      Write-Host "        (no matches)"
    }

    $newLines = @()
    foreach ($line in $lines) {
      $new = $line
      # tooling.*  →  summoner.*   (do NOT touch existing summoner.*)
      $new = $new -replace '(^[ \t]*#?[ \t]*from[ \t]+)tooling\.([A-Za-z0-9_]+)', '$1summoner.$2'
      if ($new -ne $line) { $changed = $true }
      $newLines += $new
    }

    Write-Host "      -> After:" -ForegroundColor Yellow
    if ($changed) {
      for ($i=0; $i -lt $lines.Count; $i++) {
        if ($newLines[$i] -ne $lines[$i]) {
          Write-Host ("        {0}" -f $newLines[$i]) -ForegroundColor Green
        }
      }
      Set-Content -LiteralPath $file.FullName -Value $newLines -Encoding UTF8
    } else {
      Write-Host "        (no visible changes)"
    }
  }
}

function Clone-Native([string]$url) {
  $name = [IO.Path]::GetFileNameWithoutExtension($url)
  Write-Host ("Cloning native repo: {0}" -f $name)
  $dest = Join-Path $ROOT ("native_build/{0}" -f $name)
  git clone --depth 1 $url $dest
}

# Merge repo's tooling/<pkg> into $SRC/summoner/<pkg>
function Merge-Tooling([string]$repoUrl, [string[]]$features) {
  $name = [IO.Path]::GetFileNameWithoutExtension($repoUrl)
  $srcdir = Join-Path $ROOT ("native_build/{0}/tooling" -f $name)
  if (-not (Test-Path $srcdir)) {
    Write-Host "No tooling/ directory in repo; skipping"
    return
  }

  $destRoot = Join-Path $SRC 'summoner'
  New-Item -ItemType Directory -Force -Path $destRoot | Out-Null

  Write-Host ("  Processing tooling in {0}" -f $name)
  if (-not $features -or $features.Count -eq 0) {
    Get-ChildItem -Path $srcdir -Directory | ForEach-Object {
      $pkg = $_.Name
      $dest = Join-Path $destRoot $pkg
      Write-Host ("    Adding package: {0}" -f $pkg)
      Copy-Item -Path $_.FullName -Destination $dest -Recurse -Force
      Rewrite-Imports -pkg $pkg -dir $dest
    }
  } else {
    foreach ($pkg in $features) {
      $pkgPath = Join-Path $srcdir $pkg
      if (Test-Path $pkgPath) {
        $dest = Join-Path $destRoot $pkg
        Write-Host ("    Adding package: {0}" -f $pkg)
        Copy-Item -Path $pkgPath -Destination $dest -Recurse -Force
        Rewrite-Imports -pkg $pkg -dir $dest
      } else {
        Write-Host ("    Missing {0}/tooling/{1}; skipping" -f $name, $pkg)
      }
    }
  }
}

function Print-Usage {
  Die "Usage: .\build_sdk_on_windows.ps1 {setup|delete|reset|deps|test_server|clean|use_venv} [build|test_build]"
}

# ─────────────────────────────────────────────────────
# Core Workflows
# ─────────────────────────────────────────────────────

function Install-PythonSDK {
  # Non-editable install of the Python package from $SRC (pip install .)
  if (-not (Test-Path $VENVDIR)) { Die "Run setup first" }
  $vp = Resolve-VenvPaths $VENVDIR
  if (-not $vp.Py) { Die ("Could not locate venv python inside {0}" -f $VENVDIR) }

  Push-Location $SRC
  try {
    Write-Host "  Installing/Updating build tools in venv"
    & $vp.Py -m pip install --upgrade pip setuptools wheel maturin

    Write-Host "  Uninstalling any existing 'summoner' (ignore errors)"
    try { & $vp.Py -m pip uninstall -y summoner | Out-Null } catch {}

    Write-Host "  Installing 'summoner' (non-editable) from source"
    & $vp.Py -m pip install --no-build-isolation .

    Write-Host "  Verifying install"
    & $vp.Py -c "import sys; print('OK python:', sys.executable); import summoner as s; print('OK import summoner; version:', getattr(s,'__version__','n/a'))"
  } finally {
    Pop-Location
  }
}

function Bootstrap {
  Write-Host "Bootstrapping environment..."

  Ensure-Git
  $pySpec = Get-PythonSpec

  # 1) Clone core into $SRC (matching original Bash semantics)
  if (-not (Test-Path $SRC)) {
    Write-Host ("  Cloning Summoner core -> {0}" -f $SRC)
    git clone --depth 1 --branch $CORE_BRANCH $CORE_REPO $SRC
  }

  # 2) Validate build list
  $BUILD_LIST = if ($Variant -eq 'test_build') { $BUILD_FILE_TEST } else { $BUILD_FILE_BUILD }
  Write-Host ("  Using build list: {0}" -f $BUILD_LIST)
  if (-not (Test-Path $BUILD_LIST)) { Die ("Missing build list: {0}" -f $BUILD_LIST) }

  # show sanitized list
  Write-Host ""
  Write-Host "  Sanitized build list:"
  Get-Content $BUILD_LIST -Raw |
    ForEach-Object { $_ -split "(`r`n|`n)" } |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and ($_ -notmatch '^[ \t]*#') } |
    ForEach-Object { Write-Host ("    {0}" -f $_) }
  Write-Host ""

  # 3+4) Parse BUILD_LIST, clone & merge tooling
  Write-Host "  Parsing build list and merging tooling..."
  $nativeRoot = Join-Path $ROOT 'native_build'
  if (Test-Path $nativeRoot) { Remove-Item $nativeRoot -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $nativeRoot | Out-Null
  New-Item -ItemType Directory -Force -Path (Join-Path $SRC 'summoner') | Out-Null

  $currentUrl = $null
  $currentFeatures = New-Object System.Collections.Generic.List[string]

  $lines = Get-Content $BUILD_LIST -Raw | ForEach-Object { $_ -split "(`r`n|`n)" }
  foreach ($rawLine in $lines) {
    if ($null -eq $rawLine) { continue }
    $line = $rawLine.Trim()
    if (-not $line) { continue }
    if ($line -match '^[ \t]*#') { continue }

    if ($line -match '\.git:$') {
      if ($currentUrl) {
        Clone-Native $currentUrl
        Merge-Tooling $currentUrl ($currentFeatures.ToArray())
      }
      $currentUrl = $line.TrimEnd(':')
      $currentFeatures.Clear() | Out-Null
    }
    elseif ($line -match '\.git$') {
      if ($currentUrl) {
        Clone-Native $currentUrl
        Merge-Tooling $currentUrl ($currentFeatures.ToArray())
      }
      $currentUrl = $line
      $currentFeatures.Clear() | Out-Null
    }
    else {
      $currentFeatures.Add($line) | Out-Null
    }
  }

  if ($currentUrl) {
    Clone-Native $currentUrl
    Merge-Tooling $currentUrl ($currentFeatures.ToArray())
  }

  # 5) Create venv
  if (-not (Test-Path $VENVDIR)) {
    Write-Host ("  Creating virtualenv -> {0}" -f $VENVDIR)
    & $pySpec.Program @($pySpec.Args) -m venv $VENVDIR
  }

  $vp = Resolve-VenvPaths $VENVDIR
  if (-not $vp.Py) { Die ("Could not locate venv python inside {0}" -f $VENVDIR) }

  # 6) Install build tools (pip/setuptools/maturin) — kept here for clarity
  Write-Host "  Installing build requirements"
  & $vp.Py -m pip install --upgrade pip setuptools wheel maturin

  # 7) Write .env
  Write-Host "  Writing .env"
@"
DATABASE_URL=postgres://user:pass@localhost:5432/mydb
SECRET_KEY=supersecret
"@ | Set-Content -Path (Join-Path $SRC '.env') -Encoding utf8

  # 8) Install Python SDK (non-editable) directly — NO bash scripts
  Install-PythonSDK

  Write-Host "Setup complete."

  # Minimal: activate venv in current session so user can immediately use it.
  try {
    Activate-Venv -VenvDir $VENVDIR
  } catch {
    Write-Warning ("Failed to auto-activate venv after setup: {0}" -f $_.Exception.Message)
    Write-Host "You can activate manually with: . $VENVDIR\Scripts\Activate.ps1  (dot-source into current shell)"
  }
}

function Delete-Env {
  Write-Host "Deleting environment..."
  if (Test-Path $SRC) { Remove-Item $SRC -Recurse -Force }
  if (Test-Path $VENVDIR) { Remove-Item $VENVDIR -Recurse -Force }
  if (Test-Path (Join-Path $ROOT 'native_build')) { Remove-Item (Join-Path $ROOT 'native_build') -Recurse -Force }
  if (Test-Path (Join-Path $ROOT 'logs')) { Remove-Item (Join-Path $ROOT 'logs') -Recurse -Force }
  Get-ChildItem $ROOT -Filter 'test_*.py'   -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
  Get-ChildItem $ROOT -Filter 'test_*.json' -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
  Write-Host "Deletion complete."
}

function Reset-Env {
  Write-Host "Resetting environment..."
  Delete-Env
  Bootstrap

  # Minimal: activate venv in current session after reset
  try {
    Activate-Venv -VenvDir $VENVDIR
  } catch {
    Write-Warning ("Failed to auto-activate venv after reset: {0}" -f $_.Exception.Message)
    Write-Host "You can activate manually with: . $VENVDIR\Scripts\Activate.ps1  (dot-source into current shell)"
  }

  Write-Host "Reset complete."
}

function Deps {
  Write-Host "Reinstalling dependencies (Python SDK non-editable)..."
  if (-not (Test-Path $VENVDIR)) { Die "Run setup first" }
  Install-PythonSDK
  Write-Host "Dependencies reinstalled."
}

function Test-Server {
  Write-Host "Running test_server..."
  if (-not (Test-Path $VENVDIR)) { Die "Run setup first" }
  $vp = Resolve-VenvPaths $VENVDIR
  if (-not $vp.Py) { Die ("venv missing: {0}" -f $VENVDIR) }

  if (-not (Test-Path $DATA)) { Die ("Data dir missing: {0}" -f $DATA) }
  Copy-Item (Join-Path $DATA 'default_config.json') (Join-Path $ROOT 'test_server_config.json') -Force

  $testPy = Join-Path $ROOT 'test_server.py'
@'
from summoner.server import SummonerServer
from summoner.your_package import hello_summoner

if __name__ == "__main__":
    hello_summoner()
    SummonerServer(name="test_Server").run(config_path="test_server_config.json")
'@ | Set-Content -Path $testPy -Encoding utf8

  & $vp.Py $testPy
}

function Clean {
  Write-Host "Cleaning generated files..."
  if (Test-Path (Join-Path $ROOT 'native_build')) { Remove-Item (Join-Path $ROOT 'native_build') -Recurse -Force }
  if (Test-Path (Join-Path $ROOT 'logs')) { Get-ChildItem (Join-Path $ROOT 'logs') -Recurse -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue }
  Get-ChildItem $ROOT -Filter 'test_*.py'   -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
  Get-ChildItem $ROOT -Filter 'test_*.json' -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
  Write-Host "Clean complete."
}

# ─────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────
switch ($Action) {
  'setup'       { Bootstrap }
  'delete'      { Delete-Env }
  'reset'       { Reset-Env }
  'deps'        { Deps }
  'test_server' { Test-Server }
  'clean'       { Clean }
  'use_venv'    { Activate-Venv -VenvDir $VENVDIR }
  default       { Print-Usage }
}
