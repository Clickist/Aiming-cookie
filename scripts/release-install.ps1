# release-install.ps1 — Silent install + post-install verification, one command.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release-install.ps1 [-InstallerPath <exe>] [-InstallDir <dir>]
#
# Defaults: newest Aiming_Cookie_*_x64-setup.exe in the NSIS output dir;
#           InstallDir = %LOCALAPPDATA%\Aiming Cookie.
#
# Why this script exists (each flag/step below guards a real past incident):
#   - Start-Process from PowerShell: Git Bash mangles the NSIS /S flag into a
#     path, which pops the interactive wizard instead of a silent install.
#   - Explicit /D= as the LAST argument, value unquoted: without it NSIS falls
#     back to the registry's "last InstallLocation", which smoke tests have
#     previously pointed at a temp dir.
#   - Verification uses the REAL main exe name (aiming-cookie-desktop.exe) and
#     the packaged runtime's LastWriteTime (same-version reinstalls don't bump
#     the version string, only file timestamps prove freshness).
param(
    [string]$InstallerPath,
    [string]$InstallDir = "$env:LOCALAPPDATA\Aiming Cookie"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

# ── 0) Resolve installer ─────────────────────────────────────────────────────
if (-not $InstallerPath) {
    $nsis = Join-Path $repo "webapp\frontend\src-tauri\target\release\bundle\nsis"
    $cand = Get-ChildItem $nsis -Filter "Aiming_Cookie_*_x64-setup.exe" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $cand) { throw "no Aiming_Cookie_*_x64-setup.exe found in $nsis; pass -InstallerPath" }
    $InstallerPath = $cand.FullName
}
if (-not (Test-Path $InstallerPath)) { throw "installer not found: $InstallerPath" }
$wantVer = (Get-Item $InstallerPath).VersionInfo.ProductVersion
Write-Host "== release-install: $InstallerPath (version $wantVer) =="
Write-Host "== target dir  : $InstallDir =="

$mainExe = Join-Path $InstallDir "aiming-cookie-desktop.exe"
$rtExe   = Join-Path $InstallDir "runtime\aiming-cookie-runtime\aiming-cookie-runtime.exe"
$regKey  = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Aiming Cookie"

$preRtTime  = (Get-Item $rtExe -ErrorAction SilentlyContinue).LastWriteTime
$startedAt  = Get-Date

# ── 1) Silent install (/S first, /D= LAST, value unquoted) ──────────────────
$p = Start-Process -FilePath $InstallerPath -ArgumentList "/S", "/D=$InstallDir" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "installer exited $($p.ExitCode)" }
Write-Host "== installer finished (exit 0), verifying =="

# ── 2) Post-install verification ─────────────────────────────────────────────
$fail = @()
if (-not (Test-Path $mainExe)) {
    $fail += "main exe missing: $mainExe"
} else {
    $gotVer = (Get-Item $mainExe).VersionInfo.ProductVersion
    if ($gotVer -ne $wantVer) { $fail += "main exe version '$gotVer' != installer '$wantVer'" }
}
if (Test-Path $rtExe) {
    $rtTime = (Get-Item $rtExe).LastWriteTime
    if ($preRtTime -and $rtTime -le $preRtTime) { $fail += "runtime exe not updated (LastWriteTime unchanged)" }
} else {
    $fail += "runtime exe missing: $rtExe"
}
$regLoc = (Get-ItemProperty $regKey -ErrorAction SilentlyContinue).InstallLocation
if (-not $regLoc -or ($regLoc.Trim('"') -ne $InstallDir)) {
    $fail += "registry InstallLocation '$regLoc' != target '$InstallDir' (stale smoke-test residue?)"
}

Write-Host ""
if ($fail.Count -eq 0) {
    Write-Host "== INSTALL OK ==" -ForegroundColor Green
    Write-Host "  version      : $wantVer (main exe + registry agree)"
    Write-Host "  runtime exe  : updated at $((Get-Item $rtExe).LastWriteTime)"
    Write-Host "  install dir  : $InstallDir"
} else {
    Write-Host "== INSTALL FAILED ==" -ForegroundColor Red
    $fail | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
