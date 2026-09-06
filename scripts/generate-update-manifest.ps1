[CmdletBinding()]
param(
    # tauri build 产出的 NSIS 安装包（本地名可带空格；上传 R2 前会转下划线名）
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    # R2 自定义域（latest.json 里 url 的前缀）
    [string]$DownloadBaseUrl = "https://dl.aimingcookie.com",
    [string]$OutputPath
)

# 发版 runbook 的一步：读 tauri build 生成的 .sig，产出 updater 用的 latest.json。
# 上传时把安装包与 .sig 改成下划线名传 R2，再把本 latest.json 传桶根（application/json）。
$ErrorActionPreference = "Stop"
$installerResolved = Resolve-Path -LiteralPath $InstallerPath
$installer = $installerResolved.Path
$sigPath = "$installer.sig"
if (-not (Test-Path -LiteralPath $sigPath)) {
    throw "Updater signature not found: $sigPath (build with -UpdaterSigningKeyPath so TAURI_SIGNING_PRIVATE_KEY is set)"
}
$signature = (Get-Content -LiteralPath $sigPath -Raw).Trim()
$versionMatch = [regex]::Match((Split-Path -Leaf $installer), '_([0-9]+\.[0-9]+\.[0-9]+)_')
if (-not $versionMatch.Success) {
    throw "Cannot parse version from installer name: $(Split-Path -Leaf $installer)"
}
$version = $versionMatch.Groups[1].Value
$remoteName = (Split-Path -Leaf $installer) -replace ' ', '_'
if (-not $OutputPath) {
    $OutputPath = Join-Path (Split-Path -Parent $installer) "latest.json"
}
$pubDate = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$manifest = @{
    version  = $version
    notes    = "Aiming Cookie $version"
    pub_date = $pubDate
    platforms = @{
        "windows-x86_64" = @{
            signature = $signature
            url       = "$DownloadBaseUrl/$remoteName"
        }
    }
} | ConvertTo-Json -Depth 6
# WriteAllText 默认 UTF-8 无 BOM；updater 的 HTTP 客户端不吃 BOM。
[System.IO.File]::WriteAllText($OutputPath, $manifest)
Write-Host "latest.json: $OutputPath"
Write-Host "  version: $version"
Write-Host "  url:     $DownloadBaseUrl/$remoteName"
