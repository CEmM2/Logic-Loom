$ErrorActionPreference = "Stop"

# scripts/build_release_win11.ps1 — build Logic-Loom Windows release artifacts.
#
# Wraps pnpm logic-loom:package:windows (sidecar + Tauri app → .msi + setup.exe).
# Build-only: no install, no release-repo promotion.
#
# Publishing CI-built binaries to the public release repo + GitHub Release is
# handled by scripts/release.sh --tag vX.Y.Z.

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

# ───────────────────────────── defaults ──────────────────────────────

$CleanArtifacts = $false
$DryRun = $false

$CleanPaths = @(
  (Join-Path $Root ".venv-sidecar-build"),
  (Join-Path $Root ".pyinstaller-build"),
  (Join-Path $Root ".pyinstaller-dist"),
  (Join-Path $Root "logic-loom-api.spec"),
  (Join-Path $Root "apps\desktop\dist"),
  (Join-Path $Root "apps\desktop\src-tauri\binaries"),
  (Join-Path $Root "apps\desktop\src-tauri\target")
)

# ───────────────────────────── helpers ───────────────────────────────

function Write-Log {
  param([string]$Message)
  Write-Host "[build-release:win11] $Message"
}

function Write-Die {
  param([string]$Message)
  Write-Error "[build-release:win11] error: $Message"
  exit 1
}

function ConvertTo-BoolFlag {
  param(
    [string]$FlagName,
    [string]$Value
  )

  switch ($Value.ToLowerInvariant()) {
    "yes"   { return $true }
    "true"  { return $true }
    "1"     { return $true }
    "no"    { return $false }
    "false" { return $false }
    "0"     { return $false }
    default { Write-Die "invalid value for $FlagName`: $Value (expected yes|no)" }
  }
}

function Invoke-Run {
  param(
    [string]$Command,
    [string[]]$Arguments
  )

  if ($DryRun) {
    Write-Host ("+ {0} {1}" -f $Command, ($Arguments -join ' '))
    return
  }

  & $Command @Arguments
  if ($LASTEXITCODE -ne 0) {
    Write-Die "$Command exited with code $LASTEXITCODE"
  }
}

function Show-Usage {
@"
Build Logic-Loom Windows release artifacts (sidecar + Tauri app).

Build-only. Publishing to the release repo is handled by scripts/release.sh.

Usage:
  powershell -ExecutionPolicy Bypass -File scripts/build_release_win11.ps1 [options]

Options:
  --clean <yes|no>   Remove local build artifacts when finished. Default: no.
  --dry-run          Print planned actions without changing anything.
  -h, --help         Show this help.

Examples:
  powershell -File scripts/build_release_win11.ps1
  powershell -File scripts/build_release_win11.ps1 --clean yes
  powershell -File scripts/build_release_win11.ps1 --dry-run
"@ | Write-Host
}

# ───────────────────────────── parse args ────────────────────────────

$i = 0
while ($i -lt $args.Count) {
  $arg = [string]$args[$i]

  switch -Regex ($arg) {
    '^--clean=(.+)$' {
      $CleanArtifacts = ConvertTo-BoolFlag '--clean' $matches[1]
      $i += 1
      continue
    }
    '^--clean$' {
      if ($i + 1 -ge $args.Count) { Write-Die 'missing value for --clean' }
      $CleanArtifacts = ConvertTo-BoolFlag '--clean' ([string]$args[$i + 1])
      $i += 2
      continue
    }
    '^--dry-run$' {
      $DryRun = $true
      $i += 1
      continue
    }
    '^-h$|^--help$' {
      Show-Usage
      exit 0
    }
    default {
      Write-Die "unknown argument: $arg"
    }
  }
}

# ───────────────────────────── preflight ─────────────────────────────

if (-not $IsWindows) {
  Write-Die "scripts/build_release_win11.ps1 must be run on Windows."
}

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
  Write-Die "pnpm not found on PATH"
}

Write-Log ("clean artifacts: " + ($(if ($CleanArtifacts) { 'yes' } else { 'no' })))
if ($DryRun) { Write-Log 'dry-run mode enabled' }

# ───────────────────────────── build ─────────────────────────────────

Write-Log 'building Windows sidecar + Tauri app (pnpm logic-loom:package:windows)'
Invoke-Run 'pnpm' @('logic-loom:package:windows')

# ───────────────────────────── cleanup ───────────────────────────────

if ($CleanArtifacts) {
  Write-Log 'cleaning local build artifacts'
  foreach ($Path in $CleanPaths) {
    if (Test-Path $Path) {
      if ($DryRun) {
        Write-Host "+ Remove-Item -Recurse -Force $Path"
      } else {
        Remove-Item -Recurse -Force $Path
      }
    }
  }
} else {
  Write-Log 'leaving local build artifacts in place'
}

Write-Log 'done'
