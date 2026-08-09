[CmdletBinding()]
param(
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path (Split-Path -Parent $PSCommandPath) "..")).Path }
$runtimeRoot = Join-Path $RepoRoot "webapp\frontend\src-tauri\resources\runtime"
$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$entry = Join-Path $RepoRoot "scripts\desktop-runtime-entry.py"
$bun = (Get-Command bun -ErrorAction SilentlyContinue).Source

function Require-Path([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Label is missing: $Path"
    }
}

Require-Path $python "repository Python runtime"
Require-Path $entry "desktop runtime entry point"
if (-not $bun) { throw "bun is required to compile the Coach sidecar" }

& $python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 6.21.0 is not installed in .venv. Run: .venv\Scripts\python.exe -m pip install -r webapp\requirements.txt"
}

if (Test-Path -LiteralPath $runtimeRoot) {
    # This is a generated build tree, never user data or source.
    Remove-Item -LiteralPath $runtimeRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $runtimeRoot | Out-Null

$buildRoot = Join-Path $RepoRoot "webapp\frontend\src-tauri\resources\.runtime-build"
if (Test-Path -LiteralPath $buildRoot) { Remove-Item -LiteralPath $buildRoot -Recurse -Force }
New-Item -ItemType Directory -Path $buildRoot | Out-Null

$pyDistRoot = Join-Path $buildRoot "pyinstaller-dist"
$pyDist = Join-Path $pyDistRoot "aiming-cookie-runtime"
$pyWork = Join-Path $buildRoot "pyinstaller-work"
$pySpec = Join-Path $buildRoot "pyinstaller-spec"
$commonArgs = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
    "--name", "aiming-cookie-runtime",
    "--distpath", $pyDistRoot,
    "--workpath", $pyWork,
    "--specpath", $pySpec,
    "--paths", $RepoRoot,
    "--add-data", "$(Join-Path $RepoRoot 'knowledge');knowledge",
    "--add-data", "$(Join-Path $RepoRoot 'kovaak_tracker\coach\providers.json');kovaak_tracker\coach",
    "--collect-submodules", "webapp.backend",
    "--collect-submodules", "kovaak_tracker",
    $entry
)
& $python @commonArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$backendExe = Join-Path $pyDist "aiming-cookie-runtime.exe"
Require-Path $backendExe "packaged backend executable"
Copy-Item -LiteralPath $pyDist -Destination $runtimeRoot -Recurse
$backendExe = Join-Path $runtimeRoot "aiming-cookie-runtime\aiming-cookie-runtime.exe"
Require-Path $backendExe "packaged backend executable"

$coachExe = Join-Path $runtimeRoot "coach-sidecar.exe"
$bunItem = Get-Item -LiteralPath $bun
$bunSource = if ($bunItem.Target) { $bunItem.Target[0] } else { $bunItem.FullName }
$bunStageRoot = Join-Path ([System.IO.Path]::GetTempPath()) "aiming-cookie-bun-$PID"
if ($bunStageRoot -notmatch '^[\u0000-\u007F]+$') {
    throw "Bun needs an ASCII temporary path for Windows compilation: $bunStageRoot"
}
New-Item -ItemType Directory -Path $bunStageRoot -Force | Out-Null
$stagedBun = Join-Path $bunStageRoot "bun.exe"

try {
    # Bun 1.3 on Windows cannot compile from the WinGet shim under this user's Unicode profile path.
    Copy-Item -LiteralPath $bunSource -Destination $stagedBun -Force
    & $stagedBun build (Join-Path $RepoRoot "webapp\coach-runtime\start-sidecar.ts") --compile --outfile $coachExe
    if ($LASTEXITCODE -ne 0) { throw "Bun Coach compilation failed with exit code $LASTEXITCODE" }
} finally {
    if (Test-Path -LiteralPath $bunStageRoot) {
        # This directory contains only the transient Bun copy created above.
        Remove-Item -LiteralPath $bunStageRoot -Recurse -Force
    }
}
Require-Path $coachExe "packaged Coach executable"

Copy-Item -LiteralPath (Join-Path $RepoRoot "knowledge") -Destination (Join-Path $runtimeRoot "knowledge") -Recurse
Copy-Item -LiteralPath (Join-Path $RepoRoot "webapp\coach-runtime\prompts\coach-system.md") -Destination (Join-Path $runtimeRoot "coach-system.md")
New-Item -ItemType Directory -Path (Join-Path $runtimeRoot "pi\packages\agent") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $RepoRoot "third_party\pi\packages\agent\package.json") -Destination (Join-Path $runtimeRoot "pi\packages\agent\package.json")

if (Test-Path -LiteralPath $buildRoot) { Remove-Item -LiteralPath $buildRoot -Recurse -Force }
Write-Host "Packaged runtime ready: $runtimeRoot"
