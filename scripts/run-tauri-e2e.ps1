[CmdletBinding()]
param(
    [string]$RepoRoot,
    [int]$CdpPort = 0,
    [string[]]$Spec = @("desktop-matrix.spec.ts", "desktop-managed-media.spec.ts", "interaction-polish.spec.ts")
)

# Real Tauri Playwright runner: launches an isolated Tauri dev instance with
# WebView2 CDP, injects the connection details into Playwright env vars, runs
# the desktop specs, and cleans up only what it started.
$ErrorActionPreference = "Stop"
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path (Split-Path -Parent $PSCommandPath) "..")).Path }

# --- Helpers ---

# BFS the process tree rooted at $RootPid and return the first descendant
# whose process name matches $Name. Avoids grabbing unrelated instances.
function Find-ChildProcessByName([int]$RootPid, [string]$Name) {
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $queue.Enqueue($RootPid)
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        if (-not $seen.Add($current)) { continue }
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$current" -ErrorAction SilentlyContinue
        foreach ($child in $children) {
            if ($child.Name -ieq $Name) { return [int]$child.ProcessId }
            $queue.Enqueue([int]$child.ProcessId)
        }
    }
    return 0
}

# Let the OS assign a free loopback TCP port. If $Preferred is non-zero and
# free, use it instead.
function Resolve-FreePort([int]$Preferred = 0) {
    if ($Preferred -gt 0) {
        $busy = Get-NetTCPConnection -LocalPort $Preferred -State Listen -ErrorAction SilentlyContinue
        if (-not $busy) { return $Preferred }
    }
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try {
        return ($listener.LocalEndpoint).Port
    } finally {
        $listener.Stop()
    }
}

# --- Prerequisites ---
$frontendRoot = Join-Path $RepoRoot "webapp\frontend"
$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Desktop Python runtime is missing: $python" }

$mediaFixture = Join-Path $frontendRoot "fixtures\task7-video.mp4"
if (-not (Test-Path -LiteralPath $mediaFixture)) { throw "Media fixture missing: $mediaFixture" }

# --- CDP port (auto-select if not specified or in use) ---
$CdpPort = Resolve-FreePort $CdpPort

# --- Isolation: unique identifier keeps AppData separate from real installs ---
$runTag = [System.Guid]::NewGuid().ToString('N').Substring(0, 8)
$identifier = "com.aimingcookie.e2e.$runTag"
$appDataDir = Join-Path $env:APPDATA $identifier
# Non-existent KovaaK path prevents real score ingestion during E2E.
$kovaakIsolationPath = Join-Path $RepoRoot ".tauri-e2e-kovaak-$runTag"

$configOverridePath = Join-Path $RepoRoot ".tauri-e2e-config-$runTag.json"
$tauriLogPath = Join-Path $RepoRoot ".tauri-e2e-log-$runTag.txt"
@{ identifier = $identifier } | ConvertTo-Json -Compress | Set-Content -LiteralPath $configOverridePath

# --- Environment for Tauri + Python backend ---
$env:RUSTUP_TOOLCHAIN = "stable-x86_64-pc-windows-msvc"
$env:AIMING_COOKIE_PYTHON = $python
$env:AIMING_COOKIE_PROJECT_ROOT = $RepoRoot
$env:KOVAAK_INSTALL_DIR = $kovaakIsolationPath
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=$CdpPort"

# --- Environment consumed by Playwright desktop specs ---
$env:AIMING_COOKIE_TAURI_CDP_URL = "http://127.0.0.1:$CdpPort"
$env:AIMING_COOKIE_TAURI_APP_URL = "http://localhost:3000"
$env:AIMING_COOKIE_TAURI_APP_DATA = $appDataDir
$env:AIMING_COOKIE_TAURI_MEDIA_FIXTURE = $mediaFixture

Write-Host "=== Tauri E2E Runner ===" -ForegroundColor Cyan
Write-Host "  Identifier: $identifier"
Write-Host "  AppData:    $appDataDir"
Write-Host "  CDP port:   $CdpPort"
Write-Host "  KovaaK dir: $kovaakIsolationPath (non-existent)"
Write-Host "  Specs:      $($Spec -join ', ')"

# --- Main ---
$tauriJob = $null
$exitCode = 1

try {
    # 1. Start Tauri dev with isolated identifier and CDP-enabled WebView2.
    #    Use cmd.exe /c with output redirect so errors are visible if CDP fails.
    Write-Host "`n[1/3] Starting Tauri dev..." -ForegroundColor Cyan
    $cmdLine = "npm.cmd --prefix webapp\frontend run tauri -- dev --no-watch --config `"$configOverridePath`" > `"$tauriLogPath`" 2>&1"
    $tauriJob = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmdLine -PassThru -WindowStyle Hidden -WorkingDirectory $RepoRoot

    # 2. Wait for the CDP endpoint and at least one WebView page.
    Write-Host "`n[2/3] Waiting for CDP at http://127.0.0.1:$CdpPort ..." -ForegroundColor Cyan
    $deadline = (Get-Date).AddSeconds(300)
    $cdpReady = $false
    while ((Get-Date) -lt $deadline) {
        if ($tauriJob.HasExited) {
            $tail = if (Test-Path -LiteralPath $tauriLogPath) { Get-Content -LiteralPath $tauriLogPath -Tail 30 -ErrorAction SilentlyContinue } else { "(no log)" }
            throw "Tauri exited prematurely (exit $($tauriJob.ExitCode)). Last log lines:`n$($tail -join "`n")"
        }
        try {
            $response = Invoke-RestMethod "http://127.0.0.1:$CdpPort/json/list" -ErrorAction Stop
            if ($response) { $cdpReady = $true; break }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $cdpReady) {
        $tail = if (Test-Path -LiteralPath $tauriLogPath) { Get-Content -LiteralPath $tauriLogPath -Tail 30 -ErrorAction SilentlyContinue } else { "(no log)" }
        throw "CDP did not become ready at port $CdpPort within 300s. Last log lines:`n$($tail -join "`n")"
    }
    Write-Host "  CDP ready." -ForegroundColor Green

    # Resolve the Tauri app PID by searching our own process tree (not system-wide).
    $tauriAppPid = Find-ChildProcessByName $tauriJob.Id "aiming-cookie-desktop.exe"
    if ($tauriAppPid -eq 0) {
        Start-Sleep -Seconds 3
        $tauriAppPid = Find-ChildProcessByName $tauriJob.Id "aiming-cookie-desktop.exe"
    }
    if ($tauriAppPid -gt 0) {
        $env:AIMING_COOKIE_TAURI_PID = $tauriAppPid
    } else {
        $env:AIMING_COOKIE_TAURI_PID = $tauriJob.Id
        Write-Host "  Warning: aiming-cookie-desktop not found in process tree; falling back to runner PID" -ForegroundColor Yellow
    }
    Write-Host "  Tauri PID: $($env:AIMING_COOKIE_TAURI_PID)" -ForegroundColor Green

    # 3. Run only the Tauri Desktop specs via Playwright (grep filters browser tests out).
    Write-Host "`n[3/3] Running Tauri Desktop specs..." -ForegroundColor Cyan
    Push-Location $frontendRoot
    try {
        & npx.cmd playwright test $Spec -g "real Tauri"
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
}
finally {
    Write-Host "`n=== Cleanup ===" -ForegroundColor Cyan

    # Kill only the Tauri process tree this runner started.
    if ($tauriJob -and -not $tauriJob.HasExited) {
        Write-Host "  Killing Tauri process tree (PID $($tauriJob.Id))..."
        taskkill /T /F /PID $tauriJob.Id 2>$null | Out-Null
    }

    # Remove isolated data directories and temp files (regenerable runner artifacts).
    foreach ($dir in @($appDataDir, $kovaakIsolationPath)) {
        if (Test-Path -LiteralPath $dir) {
            Write-Host "  Removing $dir"
            Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    foreach ($file in @($configOverridePath, $tauriLogPath)) {
        Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "`nPlaywright exit code: $exitCode" -ForegroundColor $(if ($exitCode -eq 0) { 'Green' } else { 'Yellow' })
exit $exitCode
