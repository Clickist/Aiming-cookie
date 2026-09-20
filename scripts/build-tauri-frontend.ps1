[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $repoRoot "webapp\frontend"
$stageRoot = Join-Path $frontendRoot ".tauri-static"
$outputRoot = Join-Path $frontendRoot "out"

function Remove-BuildArtifact([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        # Staging and static output are fully regenerable build artifacts.
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

# Tauri incremental packaging can reuse a stale embedded asset snapshot even after out/
# changed (see docs/DEVELOPMENT.md, packaging-environment rule 2). Dropping the app
# crate's cached build dirs forces the next cargo build to re-embed. Never delete them
# under a live compiler, and never give up silently: a skipped purge is exactly the
# state that ships a stale WebView payload.
function Wait-ForCargoIdle {
    param(
        [int]$Attempts = 12,
        [int]$DelaySeconds = 10
    )
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $busy = @(Get-Process -Name cargo, rustc -ErrorAction SilentlyContinue)
        if ($busy.Count -eq 0) { return }
        if ($attempt -eq $Attempts) {
            throw "A cargo build is running; refusing to purge its asset embed cache mid-flight. Re-run this build after it finishes."
        }
        Write-Warning "cargo build in progress; waiting $DelaySeconds s before purging the asset embed cache (attempt $attempt/$Attempts)"
        Start-Sleep -Seconds $DelaySeconds
    }
}

function Remove-TauriEmbedCache {
    param(
        [string]$BuildRoot,
        [int]$Attempts = 6,
        [int]$DelaySeconds = 20
    )
    if (-not (Test-Path -LiteralPath $BuildRoot)) { return }
    Wait-ForCargoIdle
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $targets = @(Get-ChildItem -LiteralPath $BuildRoot -Directory -Filter "aiming-cookie-desktop-*" -ErrorAction SilentlyContinue)
        if ($targets.Count -eq 0) { return }
        try {
            foreach ($target in $targets) {
                Remove-Item -LiteralPath $target.FullName -Recurse -Force
            }
            Write-Host "Purged tauri asset embed cache: $($targets.Count) directories under $BuildRoot"
            return
        }
        catch {
            if ($attempt -eq $Attempts) {
                throw "Could not purge the tauri asset embed cache under $BuildRoot (a concurrent build may hold it). Original error: $($_.Exception.Message)"
            }
            Write-Warning "tauri asset embed cache is locked; retrying in $DelaySeconds s (attempt $attempt/$Attempts)"
            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

Remove-BuildArtifact $stageRoot
Remove-BuildArtifact $outputRoot
New-Item -ItemType Directory -Path $stageRoot | Out-Null

foreach ($directory in @("app", "components", "lib", "ui")) {
    Copy-Item -LiteralPath (Join-Path $frontendRoot $directory) -Destination $stageRoot -Recurse
}
if (Test-Path -LiteralPath (Join-Path $frontendRoot "public")) {
    Copy-Item -LiteralPath (Join-Path $frontendRoot "public") -Destination $stageRoot -Recurse
}
foreach ($file in @("next.config.ts", "package.json", "postcss.config.mjs", "tsconfig.json")) {
    Copy-Item -LiteralPath (Join-Path $frontendRoot $file) -Destination $stageRoot
}

# Next reads .env* from the build directory (the @next/env project dir), never from the
# repo root. Without an env file next to this staging build, NEXT_PUBLIC_* values are not
# inlined and the packaged WebView reads an empty string at runtime (diagnostics upload
# silently degrades to the local export). Mirror the production env files into staging;
# .env.local is dev-only (see .env.example) and stays out of packaged builds on purpose.
$mirroredEnvFiles = @()
foreach ($file in @(".env.production.local", ".env.production")) {
    $envFile = Join-Path $frontendRoot $file
    if (Test-Path -LiteralPath $envFile) {
        Copy-Item -LiteralPath $envFile -Destination (Join-Path $stageRoot $file)
        $mirroredEnvFiles += $file
    }
}
if ($mirroredEnvFiles.Count -gt 0) {
    Write-Host "Mirrored build env into staging: $($mirroredEnvFiles -join ', ')"
} else {
    Write-Warning "No production env file found under $frontendRoot; NEXT_PUBLIC_* values will not be inlined."
}

# The route is a Browser Mock server surface, not part of the Desktop WebView.
Remove-BuildArtifact (Join-Path $stageRoot "app\api")
# Legacy dynamic analysis paths stay available in development. The packaged
# WebView uses the static /analysis?id=<id> shell instead.
Remove-BuildArtifact (Join-Path $stageRoot "app\analysis\[analysisId]")

# Drop the cached asset embed before building: the next cargo build must re-embed the
# out/ produced below, not replay a stale snapshot.
Remove-TauriEmbedCache (Join-Path $frontendRoot "src-tauri\target\release\build")

$previousStaticExport = $env:AIMING_COOKIE_STATIC_EXPORT
try {
    $env:AIMING_COOKIE_STATIC_EXPORT = "1"
    & node (Join-Path $frontendRoot "node_modules\next\dist\bin\next") build $stageRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri static frontend build failed with exit code $LASTEXITCODE"
    }
    $stageOutput = Join-Path $stageRoot "out"
    if (-not (Test-Path -LiteralPath (Join-Path $stageOutput "index.html"))) {
        throw "Tauri static frontend did not emit index.html"
    }
    # Gate BEFORE the move into out/: a failed check must leave no out/ on disk, so no
    # later tauri build (even one bypassing this script) can embed the stale payload.
    & node (Join-Path $repoRoot "scripts\check-frontend-invariants.mjs") $stageOutput
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build invariant check failed: diagnostics upload token was not inlined (see the checker output above)."
    }
    Move-Item -LiteralPath $stageOutput -Destination $outputRoot
}
finally {
    if ($null -eq $previousStaticExport) {
        Remove-Item Env:AIMING_COOKIE_STATIC_EXPORT -ErrorAction SilentlyContinue
    } else {
        $env:AIMING_COOKIE_STATIC_EXPORT = $previousStaticExport
    }
}

Remove-BuildArtifact $stageRoot

Write-Host "Tauri static frontend ready: $outputRoot"
