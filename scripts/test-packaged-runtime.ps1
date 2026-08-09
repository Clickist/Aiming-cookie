[CmdletBinding()]
param(
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path (Split-Path -Parent $PSCommandPath) "..")).Path }
$runtimeRoot = Join-Path $RepoRoot "webapp\frontend\src-tauri\resources\runtime"
$backend = Join-Path $runtimeRoot "aiming-cookie-runtime\aiming-cookie-runtime.exe"
$coach = Join-Path $runtimeRoot "coach-sidecar.exe"
foreach ($path in @($backend, $coach, (Join-Path $runtimeRoot "knowledge"), (Join-Path $runtimeRoot "coach-system.md"))) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Packaged runtime resource is missing: $path" }
}

$root = Join-Path ([IO.Path]::GetTempPath()) ("aiming-cookie-packaged-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $root | Out-Null
$dataRoot = Join-Path $root "data"
New-Item -ItemType Directory -Path $dataRoot | Out-Null
$stdout = Join-Path $root "runtime.stdout.log"
$stderr = Join-Path $root "runtime.stderr.log"
$coachStdErr = Join-Path $root "coach.stderr.log"
$old = @{}
foreach ($name in @("AIMING_COOKIE_PROJECT_ROOT", "AIMING_COOKIE_PYTHON", "PI_SOURCE_DIR", "PYTHONPATH", "TSX_TSCONFIG_PATH", "COACH_SIDECAR_URL")) {
    $old[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
[Environment]::SetEnvironmentVariable("AIMING_COOKIE_RESOURCE_ROOT", $runtimeRoot, "Process")
[Environment]::SetEnvironmentVariable("DATA_ROOT", $dataRoot, "Process")
[Environment]::SetEnvironmentVariable("KOVAAK_INSTALL_DIR", (Join-Path $root "no-kovaak"), "Process")
[Environment]::SetEnvironmentVariable("DATABASE_URL", "sqlite+aiosqlite:///" + (Join-Path $dataRoot "aiming_cookie.db").Replace("\", "/"), "Process")

$coachProcess = $null
$backendProcess = $null
try {
    $coachProcess = Start-Process -FilePath $coach -WorkingDirectory $runtimeRoot -RedirectStandardError $coachStdErr -PassThru -WindowStyle Hidden
    $backendProcess = Start-Process -FilePath $backend -WorkingDirectory $runtimeRoot -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(30)
    $port = $null
    while ((Get-Date) -lt $deadline -and -not $port) {
        if (Test-Path -LiteralPath $stdout) {
            $line = Get-Content -LiteralPath $stdout -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($line) {
                $ready = $line | ConvertFrom-Json
                if ($ready.type -eq "ready") { $port = [int]$ready.port }
            }
        }
        if ($backendProcess.HasExited) { throw "packaged backend exited before readiness: $(Get-Content $stderr -Raw -ErrorAction SilentlyContinue)" }
        Start-Sleep -Milliseconds 100
    }
    if (-not $port) { throw "packaged backend readiness timed out: $(Get-Content $stderr -Raw -ErrorAction SilentlyContinue)" }
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/healthz" -TimeoutSec 10
    if ($health.status -notin @("ok", "healthy") -and $health.ok -ne $true) { throw "packaged runtime health check failed" }
    Write-Host "Packaged runtime smoke passed: backend=$port coach=$($coachProcess.Id)"
}
finally {
    if ($backendProcess -and -not $backendProcess.HasExited) { Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($coachProcess -and -not $coachProcess.HasExited) { Stop-Process -Id $coachProcess.Id -Force -ErrorAction SilentlyContinue }
    foreach ($pair in $old.GetEnumerator()) { [Environment]::SetEnvironmentVariable($pair.Key, $pair.Value, "Process") }
}
