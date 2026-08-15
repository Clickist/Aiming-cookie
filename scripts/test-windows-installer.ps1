[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [switch]$RequireValidSignature,
    [switch]$InstallSmoke,
    [switch]$WebViewSmoke
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $InstallerPath)) { throw "Installer is missing: $InstallerPath" }
$hash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Installer SHA-256: $hash"
$signature = Get-AuthenticodeSignature -FilePath $InstallerPath
Write-Host "Installer signature: $($signature.Status)"
if ($signature.Status -eq "Valid") { Write-Host "Signer: $($signature.SignerCertificate.Subject)" }
if ($RequireValidSignature -and $signature.Status -ne "Valid") {
    throw "Installer is not validly signed: $($signature.Status)"
}

if ($InstallSmoke) {
    $installRoot = Join-Path ([IO.Path]::GetTempPath()) ("aiming-cookie-install-smoke-" + [guid]::NewGuid().ToString("N"))
    $smokeAppData = Join-Path ([IO.Path]::GetTempPath()) ("aiming-cookie-appdata-smoke-" + [guid]::NewGuid().ToString("N"))
    $previousAppData = $env:APPDATA
    $previousBrowserArgs = $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
    $previousCdpUrl = $env:AIMING_COOKIE_TAURI_CDP_URL
    $previousScreenshot = $env:AIMING_COOKIE_TAURI_SMOKE_SCREENSHOT
    New-Item -ItemType Directory -Path $installRoot | Out-Null
    try {
        Start-Process -FilePath $InstallerPath -ArgumentList @("/S", "/D=$installRoot") -Wait -NoNewWindow
        $app = Get-ChildItem -LiteralPath $installRoot -Filter *.exe -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $app) { throw "Installer did not place an executable in $installRoot" }
        Write-Host "Install smoke placed: $($app.FullName)"
        New-Item -ItemType Directory -Path $smokeAppData | Out-Null
        $env:APPDATA = $smokeAppData
        if ($WebViewSmoke) {
            $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
            $listener.Start()
            try { $cdpPort = $listener.LocalEndpoint.Port } finally { $listener.Stop() }
            $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=$cdpPort"
            $env:AIMING_COOKIE_TAURI_CDP_URL = "http://127.0.0.1:$cdpPort"
            $env:AIMING_COOKIE_TAURI_SMOKE_SCREENSHOT = Join-Path (
                Split-Path -Parent $InstallerPath
            ) "$([IO.Path]::GetFileNameWithoutExtension($InstallerPath))-launch-smoke.png"
        }
        $first = Start-Process -FilePath $app.FullName -PassThru
        try {
            $windowDeadline = [DateTime]::UtcNow.AddSeconds(45)
            do {
                Start-Sleep -Milliseconds 250
                $first.Refresh()
            } while (-not $first.HasExited -and $first.MainWindowHandle -eq 0 -and [DateTime]::UtcNow -lt $windowDeadline)
            if ($first.HasExited) { throw "Installed application exited before showing its main window" }
            if ($first.MainWindowHandle -eq 0) { throw "Installed application did not show its main window" }
            if ($WebViewSmoke) {
                $cdpDeadline = [DateTime]::UtcNow.AddSeconds(60)
                do {
                    try {
                        $cdpPages = Invoke-RestMethod "$($env:AIMING_COOKIE_TAURI_CDP_URL)/json/list" -ErrorAction Stop
                    } catch {
                        $cdpPages = $null
                        Start-Sleep -Milliseconds 250
                    }
                } while (-not $cdpPages -and [DateTime]::UtcNow -lt $cdpDeadline)
                if (-not $cdpPages) { throw "Packaged WebView CDP endpoint did not become ready" }

                Push-Location (Join-Path (Split-Path -Parent $PSScriptRoot) "webapp\frontend")
                try {
                    & npx.cmd playwright test packaged-release.spec.ts
                    if ($LASTEXITCODE -ne 0) { throw "Packaged WebView smoke failed with exit code $LASTEXITCODE" }
                } finally {
                    Pop-Location
                }
            }

            $second = Start-Process -FilePath $app.FullName -PassThru
            if (-not $second.WaitForExit(15000)) {
                Stop-Process -Id $second.Id -Force -ErrorAction SilentlyContinue
                throw "Second application launch remained running; single-instance enforcement failed"
            }
            Start-Sleep -Milliseconds 500
            $first.Refresh()
            if ($first.HasExited -or $first.MainWindowHandle -eq 0) {
                throw "The existing application window was not preserved after the second launch"
            }
            $installedInstances = @(Get-Process -Name $app.BaseName -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $app.FullName })
            if ($installedInstances.Count -ne 1) {
                throw "Expected one installed application process after the second launch; found $($installedInstances.Count)"
            }
            foreach ($processName in @("aiming-cookie-runtime", "coach-sidecar")) {
                $visibleSidecars = @(Get-Process -Name $processName -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 })
                if ($visibleSidecars.Count -ne 0) {
                    throw "$processName opened a visible console window"
                }
            }
            Write-Host "Single-instance launch smoke passed: main=$($first.Id) second=$($second.Id)"
        } finally {
            if (-not $first.HasExited) {
                $null = $first.CloseMainWindow()
                if (-not $first.WaitForExit(10000)) {
                    Stop-Process -Id $first.Id -Force -ErrorAction SilentlyContinue
                }
            }
        }
        $uninstaller = Join-Path $installRoot "uninstall.exe"
        if (Test-Path -LiteralPath $uninstaller) {
            Start-Process -FilePath $uninstaller -ArgumentList "/S" -Wait -NoNewWindow
            if (Test-Path -LiteralPath $installRoot) { Write-Warning "Uninstaller left files at $installRoot" }
        } else {
            Write-Warning "Uninstaller was not found; launch/close smoke must be run manually"
        }
    } finally {
        $env:APPDATA = $previousAppData
        $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = $previousBrowserArgs
        $env:AIMING_COOKIE_TAURI_CDP_URL = $previousCdpUrl
        $env:AIMING_COOKIE_TAURI_SMOKE_SCREENSHOT = $previousScreenshot
        # The smoke directory only contains generated installer output.
        if (Test-Path -LiteralPath $installRoot) { Remove-Item -LiteralPath $installRoot -Recurse -Force -ErrorAction SilentlyContinue }
        if (Test-Path -LiteralPath $smokeAppData) { Remove-Item -LiteralPath $smokeAppData -Recurse -Force -ErrorAction SilentlyContinue }
    }
}
