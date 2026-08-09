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

# The route is a Browser Mock server surface, not part of the Desktop WebView.
Remove-BuildArtifact (Join-Path $stageRoot "app\api")
# Legacy dynamic analysis paths stay available in development. The packaged
# WebView uses the static /analysis?id=<id> shell instead.
Remove-BuildArtifact (Join-Path $stageRoot "app\analysis\[analysisId]")

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
