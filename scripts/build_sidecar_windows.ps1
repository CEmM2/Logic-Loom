$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$BuildVenv = Join-Path $Root ".venv-sidecar-build"
$BinDir = Join-Path $Root "apps\desktop\src-tauri\binaries"
$PyiWork = Join-Path $Root ".pyinstaller-build"
$PyiDist = Join-Path $Root ".pyinstaller-dist"
$Entry = Join-Path $Root "packaging\pyinstaller\logic_loom_api_entry.py"
$LockedRequirements = Join-Path $Root ".sidecar-build-requirements.txt"

# What goes into the sidecar. The default is the full bundle: the AKMS tabs and
# Learn (`akms` extra), the MechDSL emit bridge (`mechdsl`), Failure Memory
# (`failure-memory`) and the Records browser (`records`). Every one of those
# packages comes from the package index at the version uv.lock records, so no
# source checkout of AKMS, MechDSL or zotero-summarizer is involved.
#
# LOGIC_LOOM_SIDECAR_CORE_ONLY=1 builds the zsum-only sidecar instead. The app
# then reports the AKMS-family modes as unavailable, which is the same state as
# a development install without the extras.
$CoreOnly = ($env:LOGIC_LOOM_SIDECAR_CORE_ONLY -eq "1")
$SidecarExtras = @("akms", "mechdsl", "failure-memory", "records")

Write-Host "[sidecar] root: $Root"

Set-Location $Root

# Security guard: assert no Nodes_Vault paths leaked into the
# packaged resources tree. MUST run before any build step so a leak halts
# the pipeline before the wheel/PyInstaller stages copy the tree.
Write-Host "[sidecar] running resources/ Nodes_Vault security guard"
& python (Join-Path $Root "scripts\check_resources_no_vault.py")
if ($LASTEXITCODE -ne 0) {
  Write-Error "[sidecar] resources/ security guard failed with exit code $LASTEXITCODE"
  exit $LASTEXITCODE
}

# Sync curated established nodes into the shippable resources tree.
# Runs unconditionally — verified_nodes/ is always present in the repo.
# Must run after the vault guard (which checks accumulated state from prior
# syncs) and before the build venv is created. Invoked via `uv run` because
# the sync imports third-party python-frontmatter (a logic-loom-api dep) that
# is NOT on the bare system interpreter on a clean build host.
Write-Host "[sidecar] syncing curated established nodes via sync_akms_nodes.py"
& uv run python (Join-Path $Root "scripts\sync_akms_nodes.py")
if ($LASTEXITCODE -ne 0) {
  Write-Error "[sidecar] sync_akms_nodes.py failed with exit code $LASTEXITCODE"
  exit $LASTEXITCODE
}

# Established-only guard: assert every node in the synced established tree carries
# status: established AND passes the AKMS GlobalNodeFrontmatter schema compile.
# The guard FAILS CLOSED when akms is not importable, so the full build runs it
# with `--extra akms` (full schema compile). Only a core-only build, which
# installs no AKMS package at all, passes --allow-missing-akms so the
# status-only fallback runs instead of wedging the build.
Write-Host "[sidecar] running established-only build guard"
$GuardScript = Join-Path $Root "scripts\check_resources_established_only.py"
if (-not $CoreOnly) {
  & uv run --package logic-loom-api --extra akms python $GuardScript
} else {
  & uv run python $GuardScript --allow-missing-akms
}
if ($LASTEXITCODE -ne 0) {
  Write-Error "[sidecar] established-only guard failed with exit code $LASTEXITCODE"
  exit $LASTEXITCODE
}

Write-Host "[sidecar] cleaning old build output"
Remove-Item -Recurse -Force $BuildVenv, $PyiWork, $PyiDist, $LockedRequirements -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $BinDir | Out-Null

Write-Host "[sidecar] creating isolated build venv"
uv venv $BuildVenv

$Activate = Join-Path $BuildVenv "Scripts\Activate.ps1"
. $Activate

# The build installs exactly what uv.lock records: `uv export --locked` fails if
# the lockfile is out of date with pyproject.toml, and the exported pins are the
# builds the test suite ran against. The extras are the ones declared in
# services/logic_loom_api/pyproject.toml, so the version ranges live in one
# place only.
$ExportArgs = @(
  "export",
  "--locked",
  "--no-hashes",
  "--no-dev",
  "--no-emit-workspace",
  "--package", "logic-loom-api",
  "--output-file", $LockedRequirements
)

$PyinstallerArgs = @(
  "--clean",
  "--noconfirm",
  # One-folder, not one-file, on Windows. A one-file build unpacks its whole
  # payload (thousands of files, several hundred MB) into %TEMP% on every
  # launch, and antivirus scanning of those writes routinely pushed startup
  # past the shell's handshake timeout. Force-killing the one-file bootloader
  # on exit also left the unpacked folder behind in %TEMP%. The folder build
  # is unpacked once, by the installer.
  "--onedir",
  "--name", "logic-loom-api",
  "--workpath", $PyiWork,
  "--distpath", $PyiDist,
  "--collect-all", "logic_loom_api",
  "--collect-all", "zsum",
  "--hidden-import", "uvicorn.protocols.http.auto",
  "--hidden-import", "uvicorn.protocols.websockets.auto",
  "--hidden-import", "uvicorn.lifespan.on"
)

if (-not $CoreOnly) {
  Write-Host "[sidecar] full bundle: AKMS + Learn, MechDSL emit bridge, Failure Memory, Records"
  foreach ($Extra in $SidecarExtras) {
    $ExportArgs += @("--extra", $Extra)
  }
  $PyinstallerArgs += @(
    # AKMS tabs (Picker, Nodes, Graph, Batches) and Learn.
    "--collect-all", "akms",
    "--collect-all", "akms_nodes_gen",
    "--collect-all", "akms_learn",
    "--collect-data", "litellm",
    # Failure Memory workspace and the Records browser.
    "--collect-all", "akms_failure_memory",
    "--collect-all", "akms_records",
    # MechDSL emit bridge: compile_from_sources / transpile via
    # compmech-reference-pack[mechdsl] -> mechdsl-core + algo2code. Taichi-free:
    # taichi lives in mechdsl-core's [verify] extra, which is never requested,
    # so only run_verify=True would need it and that is gated off. opt-einsum
    # (compile-time contraction optimizer) rides along.
    "--collect-all", "compmech_reference_pack",
    "--collect-all", "mechdsl",
    "--collect-all", "algo2code",
    "--collect-all", "opt_einsum",
    "--collect-all", "nrpylatex",
    "--exclude-module", "taichi",
    # pip rides along so the in-app "Install taichi" action (MechDSL Overview)
    # can side-install taichi 1.7.4 at runtime via the binary's hidden
    # --install-runtime-pkg re-entry. Taichi itself stays excluded (~170 MB).
    "--collect-all", "pip"
  )
} else {
  Write-Host "[sidecar] LOGIC_LOOM_SIDECAR_CORE_ONLY=1: building the core-only sidecar (no AKMS-family packages)"
}

Write-Host "[sidecar] exporting the locked dependency set"
& uv @ExportArgs | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Error "[sidecar] uv export --locked failed with exit code $LASTEXITCODE (is uv.lock up to date?)"
  exit $LASTEXITCODE
}

Write-Host "[sidecar] installing PyInstaller + the locked dependencies"
& uv pip install pyinstaller -r $LockedRequirements
if ($LASTEXITCODE -ne 0) {
  Write-Error "[sidecar] dependency install failed with exit code $LASTEXITCODE"
  exit $LASTEXITCODE
}

if (-not $CoreOnly) {
  # Resolve the runtime workspace cwd ahead of time so Windows long-path /
  # junction edge cases surface here (where they are diagnosable) instead of
  # inside the asyncio.create_subprocess_exec call at dispatch time.
  $ResolvedRoot = (Resolve-Path $Root).ProviderPath
  Write-Host "[sidecar] dispatch cwd resolves to: $ResolvedRoot"
  # The curated AKMS agent assets are read from the `akms` package that was just
  # installed, so the bundle carries the assets of exactly the AKMS version it
  # freezes. This must run before logic-loom-api is installed below: that
  # install copies the resources tree the sync writes into.
  Write-Host "[sidecar] syncing curated AKMS Claude assets via sync_akms_claude_assets.py"
  & python (Join-Path $Root "scripts\sync_akms_claude_assets.py")
  if ($LASTEXITCODE -ne 0) {
    Write-Error "[sidecar] sync_akms_claude_assets.py failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
  }
}

Write-Host "[sidecar] installing the Logic-Loom API"
& uv pip install --no-deps ./services/logic_loom_api
if ($LASTEXITCODE -ne 0) {
  Write-Error "[sidecar] logic-loom-api install failed with exit code $LASTEXITCODE"
  exit $LASTEXITCODE
}

Write-Host "[sidecar] freezing logic-loom-api"
& pyinstaller @PyinstallerArgs $Entry
if ($LASTEXITCODE -ne 0) {
  Write-Error "[sidecar] pyinstaller failed with exit code $LASTEXITCODE"
  exit $LASTEXITCODE
}

# The shell looks for binaries\logic-loom-api\logic-loom-api.exe (see
# apps/desktop/src-tauri/src/sidecar.rs). Remove both the folder and any
# one-file binary left by an older build, so only one layout is bundled.
$SidecarDir = Join-Path $BinDir "logic-loom-api"
$SidecarExe = Join-Path $SidecarDir "logic-loom-api.exe"
Write-Host "[sidecar] copying the sidecar folder into Tauri resources"
Remove-Item -Recurse -Force $SidecarDir, (Join-Path $BinDir "logic-loom-api.exe") -ErrorAction SilentlyContinue
Copy-Item -Recurse (Join-Path $PyiDist "logic-loom-api") $SidecarDir

Write-Host "[sidecar] smoke test: the sidecar announces READY"
# Same handshake the shell waits for. The limit here is well under the shell's
# so a slow-starting build fails the build instead of reaching users.
$SmokeLimitSeconds = 60
$Psi = New-Object System.Diagnostics.ProcessStartInfo
$Psi.FileName = $SidecarExe
$Psi.Arguments = "--auto-port"
$Psi.UseShellExecute = $false
$Psi.RedirectStandardOutput = $true
$Psi.CreateNoWindow = $true
$Watch = [System.Diagnostics.Stopwatch]::StartNew()
$Proc = [System.Diagnostics.Process]::Start($Psi)
$ReadLine = $Proc.StandardOutput.ReadLineAsync()
$Ready = $false
while ($Watch.Elapsed.TotalSeconds -lt $SmokeLimitSeconds) {
  if ($ReadLine.Wait(500)) {
    $Line = $ReadLine.Result
    if ($null -eq $Line) { break }
    if ($Line -match '^LOGIC_LOOM_API_READY:\d+$') { $Ready = $true; break }
    $ReadLine = $Proc.StandardOutput.ReadLineAsync()
  }
}
$Elapsed = [math]::Round($Watch.Elapsed.TotalSeconds, 1)
& taskkill /PID $Proc.Id /T /F | Out-Null
if (-not $Ready) {
  Write-Error "[sidecar] sidecar did not announce READY within $SmokeLimitSeconds s"
  exit 1
}
Write-Host "[sidecar] sidecar ready after $Elapsed s"

Remove-Item -Force $LockedRequirements -ErrorAction SilentlyContinue

Write-Host "[sidecar] done"
exit 0
