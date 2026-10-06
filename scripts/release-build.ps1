# release-build.ps1 — One-command release build (two-phase signing).
#
# Phase A: kill app processes -> (re)embed PyInstaller runtime -> tauri NSIS
#          build with -Unsigned (the updater-signing step inside EXITS 1 BY
#          DESIGN while the NSIS exe is fully produced — that is expected).
# Phase B: underscore-name copy -> re-sign via scripts\resign.sh (bash; empty
#          password env can only be exported from bash) -> .sha256 -> self-check
#          -> latest.json.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release-build.ps1 [-SkipRuntimeRebuild]
#
# Notes:
#   - Do NOT pass -RepoRoot to build-windows-installer.ps1: non-ASCII repo paths
#     get mangled through bash->PowerShell and it exits 0 without running.
#     All paths here derive from $PSScriptRoot.
#   - RUSTUP_TOOLCHAIN must be pinned: without it cargo falls back to the GNU
#     toolchain and the build explodes on MSVC-only deps.
param(
    [switch]$SkipRuntimeRebuild
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Write-Host "== release-build: repo root = $repo =="

# ── 0) Version from tauri.conf.json (single source for artifact names) ──────
$confPath = Join-Path $repo "webapp\frontend\src-tauri\tauri.conf.json"
$version = (Get-Content $confPath -Raw | ConvertFrom-Json).version
if (-not $version) { throw "cannot read version from tauri.conf.json" }
Write-Host "== version: $version =="

# ── 1) Toolchain pin ─────────────────────────────────────────────────────────
$env:RUSTUP_TOOLCHAIN = "stable-x86_64-pc-windows-msvc"

# ── 2) Scrub updater-signing env so phase A never hangs on a passphrase ─────
# Any leftover TAURI_SIGNING_PRIVATE_KEY* makes tauri try to sign; with the
# password variable impossible to set from PowerShell it waits for input in a
# headless run forever. Phase A builds unsigned; phase B signs via bash.
Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY -ErrorAction SilentlyContinue
Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY_PATH -ErrorAction SilentlyContinue
Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue

# ── 3) Kill app family (running apps lock resources\runtime exes) ───────────
foreach ($name in @("aiming-cookie-desktop", "aiming-cookie-runtime", "aiming-cookie-coach-sidecar")) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Write-Host "== app processes killed =="

# ── 4) Re-embed PyInstaller runtime ─────────────────────────────────────────
# Default ON: any webapp/backend/** or telemetry_capture/** change only reaches
# the installer through this step. Skipping it ships a stale backend.
if ($SkipRuntimeRebuild) {
    Write-Host "== SKIP runtime rebuild (per flag) =="
} else {
    Write-Host "== rebuilding PyInstaller runtime (~3 min) =="
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo "scripts\build-windows-runtime.ps1")
    if ($LASTEXITCODE -ne 0) { throw "runtime rebuild failed (exit $LASTEXITCODE)" }
}

# ── 5) Phase A: NSIS build, unsigned (signing step exits 1 by design) ───────
$nsis = Join-Path $repo "webapp\frontend\src-tauri\target\release\bundle\nsis"
$spaceExe = Join-Path $nsis "Aiming Cookie_${version}_x64-setup.exe"
if (Test-Path $spaceExe) { Remove-Item $spaceExe -Force }   # stale artifact guard
Write-Host "== tauri NSIS build, unsigned (8-12 min; signer exit 1 here is expected) =="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo "scripts\build-windows-installer.ps1") -Unsigned
Write-Host "== installer script returned (exit $LASTEXITCODE); waiting for artifact =="

# Wait for the exe to exist AND stay size-stable (NSIS writes it last).
$deadline = (Get-Date).AddMinutes(6)
$stable = $null
while ((Get-Date) -lt $deadline) {
    if (Test-Path $spaceExe) {
        $len = (Get-Item $spaceExe).Length
        if ($null -ne $stable -and $stable -eq $len -and $len -gt 100MB) { break }
        $stable = $len
    }
    Start-Sleep -Seconds 10
}
if (-not (Test-Path $spaceExe)) { throw "NSIS exe did not appear within timeout: $spaceExe" }
Write-Host ("== phase A done: {0:N0} bytes ==" -f (Get-Item $spaceExe).Length)

# ── 6) Phase B: underscore copy -> bash re-sign -> sha256 ───────────────────
$uExe = Join-Path $nsis "Aiming_Cookie_${version}_x64-setup.exe"
Copy-Item $spaceExe $uExe -Force
Write-Host "== re-signing (bash) : $uExe =="
& bash (Join-Path $repo "scripts\resign.sh") $uExe
if ($LASTEXITCODE -ne 0) { throw "re-sign failed (exit $LASTEXITCODE)" }
$uSig = "$uExe.sig"
if (-not (Test-Path $uSig)) { throw "signature missing after re-sign: $uSig" }
if ((Get-Item $uSig).LastWriteTime -lt (Get-Date).AddMinutes(-5)) { throw "signature file is stale (not freshly written)" }

$hash = (Get-FileHash $uExe -Algorithm SHA256).Hash.ToLower()
$shaPath = "$uExe.sha256"
"$hash  Aiming_Cookie_${version}_x64-setup.exe" | Set-Content -Path $shaPath -Encoding ascii -NoNewline:$false

# ── 7) Self-check ─────────────────────────────────────────────────────────────
$sigLen = (Get-Item $uSig).Length
if ($sigLen -lt 400) { throw "signature suspiciously small (${sigLen}B)" }
$builtVer = (Get-Item $uExe).VersionInfo.ProductVersion
if ($builtVer -ne $version) { throw "built exe version '$builtVer' != tauri.conf.json '$version'" }

# ── 8) latest.json (update manifest, underscore-name url) ───────────────────
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo "scripts\generate-update-manifest.ps1") -InstallerPath $uExe
if ($LASTEXITCODE -ne 0) { throw "generate-update-manifest failed" }
$latest = Join-Path $nsis "latest.json"
$latestVer = (Get-Content $latest -Raw | ConvertFrom-Json).version
if ($latestVer -ne $version) { throw "latest.json version '$latestVer' != $version" }

Write-Host ""
Write-Host "== BUILD OK ==" -ForegroundColor Green
Write-Host "  version      : $version"
Write-Host "  installer    : $uExe"
Write-Host "  sig / sha256 : $uSig / $shaPath"
Write-Host "  latest.json  : $latest"
Write-Host "  sha256       : $hash"
