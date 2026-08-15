[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$RunTag,
    [switch]$ReuseExistingState
)

# Interactive field-test runner. It keeps the window and all startup output visible.
$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path (Split-Path -Parent $PSCommandPath) "..")).Path
}
if (-not $RunTag) {
    $RunTag = "manual-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
}
if ($RunTag -notmatch "^[a-z0-9-]+$") {
    throw "RunTag must contain only lowercase letters, digits, and hyphens."
}

$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Desktop Python runtime is missing: $python"
}

$identifier = "com.aimingcookie.$RunTag"
$appDataDir = Join-Path $env:APPDATA $identifier
if ((Test-Path -LiteralPath $appDataDir) -and -not $ReuseExistingState) {
    throw "The isolated AppData directory already exists: $appDataDir"
}
if ($ReuseExistingState -and -not (Test-Path -LiteralPath $appDataDir)) {
    throw "Cannot reuse missing isolated AppData directory: $appDataDir"
}

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) "AimingCookieManualE2E"
$logDir = Join-Path $tempRoot $RunTag
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$configPath = Join-Path $logDir "tauri-config.json"
@{ identifier = $identifier } | ConvertTo-Json -Compress | Set-Content -LiteralPath $configPath -Encoding utf8
$tauriLog = Join-Path $logDir "tauri.log"

$env:RUSTUP_TOOLCHAIN = "stable-x86_64-pc-windows-msvc"
$env:AIMING_COOKIE_PYTHON = $python
$env:AIMING_COOKIE_PROJECT_ROOT = $RepoRoot

Write-Host "=== Aiming Cookie Manual Field Test ===" -ForegroundColor Cyan
Write-Host "  Run tag:  $RunTag"
Write-Host "  AppData:  $appDataDir"
Write-Host "  Log file: $tauriLog"
Write-Host "  KovaaK:   default discovery paths (existing files are observable)"
Write-Host "  Provider: not seeded"
Write-Host ""
Write-Host "Close the Tauri window to stop this field-test instance." -ForegroundColor Yellow

Push-Location $RepoRoot
try {
    & npm.cmd --prefix webapp\frontend run tauri -- dev --no-watch --config $configPath 2>&1 | Tee-Object -FilePath $tauriLog
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host "Tauri exited with code $exitCode. The isolated AppData and log were retained for inspection."
exit $exitCode
