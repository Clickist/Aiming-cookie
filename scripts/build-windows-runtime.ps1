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
    "--add-data", "$(Join-Path $RepoRoot 'telemetry_capture');telemetry_capture",
    "--collect-submodules", "webapp.backend",
    "--collect-submodules", "kovaak_tracker",
    # CV 视频分析已退役（2026-10-24，PRD §14）：cv2/PIL/pyarrow 不在运行路径
    # 上，显式排除防止传递导入把它们拖回包体（pyarrow ~40MB、PIL+cv2 共 ~50MB）。
    "--exclude-module", "pyarrow",
    "--exclude-module", "PIL",
    "--exclude-module", "cv2",
    $entry
)
& $python @commonArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$backendExe = Join-Path $pyDist "aiming-cookie-runtime.exe"
Require-Path $backendExe "packaged backend executable"
Copy-Item -LiteralPath $pyDist -Destination $runtimeRoot -Recurse
$backendExe = Join-Path $runtimeRoot "aiming-cookie-runtime\aiming-cookie-runtime.exe"
Require-Path $backendExe "packaged backend executable"

# Plotly 本体保留（Figure.to_dict() 运行期在用），但 _internal\plotly\package_data
# （内置地理/字体大数据包）不在任何运行路径上，删除以瘦身。
$plotlyDir = Join-Path $runtimeRoot "aiming-cookie-runtime\_internal\plotly"
Require-Path $plotlyDir "packaged plotly module"
$plotlyPackageData = Join-Path $plotlyDir "package_data"
if (Test-Path -LiteralPath $plotlyPackageData) {
    Remove-Item -LiteralPath $plotlyPackageData -Recurse -Force
}

$coachExe = Join-Path $runtimeRoot "coach-sidecar.exe"
$bunItem = Get-Item -LiteralPath $bun
$bunSource = if ($bunItem.Target) { $bunItem.Target[0] } else { $bunItem.FullName }
$bunStageRoot = Join-Path ([System.IO.Path]::GetTempPath()) "aiming-cookie-bun-$PID"
if ($bunStageRoot -notmatch '^[\u0000-\u007F]+$') {
    throw "Bun needs an ASCII temporary path for Windows compilation: $bunStageRoot"
}
New-Item -ItemType Directory -Path $bunStageRoot -Force | Out-Null
$stagedBun = Join-Path $bunStageRoot "bun.exe"

# Relay base_url is injected at build time and never committed to the repo
# (the packaged sidecar needs the real address; the public source does not).
$relayUrlFile = Join-Path $RepoRoot "webapp\coach-runtime\relay-base-url.local.txt"
if (-not (Test-Path -LiteralPath $relayUrlFile)) {
    throw "relay-base-url.local.txt is missing (kept out of the repo on purpose; it carries the relay base_url consumed by provider-models.ts)."
}
$relayBaseUrl = (Get-Content -LiteralPath $relayUrlFile -Raw).Trim()
if (-not $relayBaseUrl) { throw "relay-base-url.local.txt is empty" }

# 基础设施域名（accounts 会员站 / member 网关）与 relay 同款构建期注入：读仓库根
# infra-urls.local.txt（gitignore 的本地文件，KEY=VALUE 每行一对）。缺文件或缺键
# 不报错，回落 example.invalid 占位——公开源码构建即此形态（会员外链打不开，
# 其余功能不受影响）。affiliate 不在这里注入：affiliate-native.ts 走运行期
# 配置文件 config/affiliate-service.json，本就没有仓库内域名。
$infraUrlFile = Join-Path $RepoRoot "infra-urls.local.txt"
$infraUrls = @{}
if (Test-Path -LiteralPath $infraUrlFile) {
    foreach ($line in Get-Content -LiteralPath $infraUrlFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $separator = $trimmed.IndexOf("=")
        if ($separator -lt 1) { continue }
        $infraUrls[$trimmed.Substring(0, $separator).Trim()] = $trimmed.Substring($separator + 1).Trim()
    }
}
$infraDefines = @(
    @{ Define = "AC_ACCOUNTS_BASE_URL"; Key = "ACCOUNTS_BASE_URL"; Placeholder = "https://accounts.example.invalid" },
    @{ Define = "AC_MEMBER_GATEWAY_BASE_URL"; Key = "MEMBER_GATEWAY_BASE_URL"; Placeholder = "https://member-gateway.example.invalid:8443/member/v1" }
)
$infraDefineArgs = @()
foreach ($entry in $infraDefines) {
    $value = $infraUrls[$entry.Key]
    if (-not $value) { $value = $entry.Placeholder }
    $infraDefineArgs += @("--define", "process.env.$($entry.Define)=`"$value`"")
}

try {
    # Bun 1.3 on Windows cannot compile from the WinGet shim under this user's Unicode profile path.
    Copy-Item -LiteralPath $bunSource -Destination $stagedBun -Force
    & $stagedBun build (Join-Path $RepoRoot "webapp\coach-runtime\start-sidecar.ts") --compile --outfile $coachExe --define "process.env.AC_RELAY_BASE_URL=`"$relayBaseUrl`"" @infraDefineArgs
    if ($LASTEXITCODE -ne 0) { throw "Bun Coach compilation failed with exit code $LASTEXITCODE" }
} finally {
    if (Test-Path -LiteralPath $bunStageRoot) {
        # This directory contains only the transient Bun copy created above.
        Remove-Item -LiteralPath $bunStageRoot -Recurse -Force
    }
}
Require-Path $coachExe "packaged Coach executable"

Copy-Item -LiteralPath (Join-Path $RepoRoot "knowledge") -Destination (Join-Path $runtimeRoot "knowledge") -Recurse
# Coach mapping resources ride inside the wholesale knowledge copy above; the
# explicit pass (mirroring the eloshapes block) keeps them guaranteed if the
# knowledge tree is ever filtered, and fails the build if they are missing.
$mappingSource = Join-Path $RepoRoot "knowledge\mapping"
Require-Path (Join-Path $mappingSource "vocabulary.v1.json") "mapping vocabulary resource"
Require-Path (Join-Path $mappingSource "official.v1.json") "official mapping resource"
New-Item -ItemType Directory -Path (Join-Path $runtimeRoot "knowledge\mapping") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $mappingSource "vocabulary.v1.json") -Destination (Join-Path $runtimeRoot "knowledge\mapping\vocabulary.v1.json")
Copy-Item -LiteralPath (Join-Path $mappingSource "official.v1.json") -Destination (Join-Path $runtimeRoot "knowledge\mapping\official.v1.json")
Copy-Item -LiteralPath (Join-Path $RepoRoot "webapp\coach-runtime\prompts\coach-system.md") -Destination (Join-Path $runtimeRoot "coach-system.md")
Copy-Item -LiteralPath (Join-Path $RepoRoot "webapp\coach-runtime\prompts\skills") -Destination (Join-Path $runtimeRoot "skills") -Recurse
New-Item -ItemType Directory -Path (Join-Path $runtimeRoot "pi\packages\agent") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $RepoRoot "third_party\pi\packages\agent\package.json") -Destination (Join-Path $runtimeRoot "pi\packages\agent\package.json")
# EloShapes 外设目录（eloshapes-native 只读这两个文件，打全量 artifacts 会拖包体）。
New-Item -ItemType Directory -Path (Join-Path $runtimeRoot "artifacts\eloshapes\snapshots"), (Join-Path $runtimeRoot "artifacts\eloshapes\marketplace-mapping") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $RepoRoot "artifacts\eloshapes\snapshots\eloshapes_mouse_catalog_2026-07-31T211736Z.json") -Destination (Join-Path $runtimeRoot "artifacts\eloshapes\snapshots\eloshapes_mouse_catalog_2026-07-31T211736Z.json")
Copy-Item -LiteralPath (Join-Path $RepoRoot "artifacts\eloshapes\marketplace-mapping\marketplace-mapping.json") -Destination (Join-Path $runtimeRoot "artifacts\eloshapes\marketplace-mapping\marketplace-mapping.json")

# 体积守门：CV 退役后未压缩 runtime 预期 ~287MB。超出 300MB 说明重依赖
# （cv2/PIL/pyarrow 等）被传递导入意外拉回包内——先修构建，不发更大的安装包。
$runtimeBytes = (
    Get-ChildItem -LiteralPath $runtimeRoot -Recurse -File |
        Measure-Object -Property Length -Sum
).Sum
$runtimeMb = [math]::Round($runtimeBytes / 1MB)
if ($runtimeMb -gt 300) {
    throw (
        "Packaged runtime is ${runtimeMb}MB uncompressed, over the 300MB gate " +
        "(expected ~287MB after CV retirement). Inspect " +
        "$runtimeRoot\aiming-cookie-runtime\_internal for pyarrow/PIL/cv2 " +
        "or other oversized modules before releasing."
    )
}

# 排除模块守门（1102 升级残留事故）：--exclude-module 只在打包期生效；安装器
# 升级若不清旧目录，旧版留下的 _internal\pyarrow 会变成空壳 namespace 包，
# pandas 读 pa.__version__ 崩 → 后端启动即死，用户卡在「本地服务未能启动」
# 且自更新通道同时失效。两道防线：构建侧断言这些目录不得进包，安装侧
# NSIS_HOOK_PREINSTALL 整删旧 runtime。
$excludedInternal = Join-Path $runtimeRoot "aiming-cookie-runtime\_internal"
foreach ($name in @("pyarrow", "PIL", "cv2")) {
    $leaked = Join-Path $excludedInternal $name
    if (Test-Path -LiteralPath $leaked) {
        throw (
            "Excluded module '$name' leaked into the package: $leaked. " +
            "Remove the transitive import that pulls it back in, or upgraded " +
            "installs will break on startup."
        )
    }
}

if (Test-Path -LiteralPath $buildRoot) { Remove-Item -LiteralPath $buildRoot -Recurse -Force }
Write-Host "Packaged runtime ready: $runtimeRoot (${runtimeMb}MB uncompressed)"
