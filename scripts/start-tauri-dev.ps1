[CmdletBinding()]
param(
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path (Split-Path -Parent $PSCommandPath) "..")).Path }

$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Desktop Python runtime is missing: $python" }

$env:RUSTUP_TOOLCHAIN = "stable-x86_64-pc-windows-msvc"
$env:AIMING_COOKIE_PYTHON = $python
$env:AIMING_COOKIE_PROJECT_ROOT = $RepoRoot

Push-Location $RepoRoot
try {
    npm.cmd --prefix webapp\frontend run tauri -- dev --no-watch
    if ($LASTEXITCODE -ne 0) { throw "Tauri development startup failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
