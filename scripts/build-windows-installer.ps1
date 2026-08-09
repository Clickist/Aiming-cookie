[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$CertificateThumbprint,
    [string]$TimestampUrl,
    [switch]$Unsigned
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path (Split-Path -Parent $PSCommandPath) "..")).Path }
$env:RUSTUP_TOOLCHAIN = "stable-x86_64-pc-windows-msvc"
$frontend = Join-Path $RepoRoot "webapp\frontend"
$runtimeRoot = Join-Path $frontend "src-tauri\resources\runtime"
$tauriRoot = Join-Path $frontend "src-tauri"
$signingOverlay = Join-Path $tauriRoot ".tauri-signing.json"
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) { $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x86\signtool.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1 }

if ($CertificateThumbprint) {
    if (-not $TimestampUrl) { throw "TimestampUrl is required for signed builds" }
    $thumbprint = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()
    $certificate = Get-ChildItem "Cert:\CurrentUser\My\$thumbprint" -ErrorAction SilentlyContinue
    if (-not $certificate -or -not $certificate.HasPrivateKey) { throw "No code-signing certificate with private key found for thumbprint $thumbprint" }
    if (-not $signtool) { throw "signtool.exe was not found" }
} elseif (-not $Unsigned) {
    throw "Unsigned mode is explicit. Pass -Unsigned for an internal package, or provide -CertificateThumbprint and -TimestampUrl."
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RepoRoot "scripts\build-windows-runtime.ps1") -RepoRoot $RepoRoot
if ($LASTEXITCODE -ne 0) { throw "Packaged runtime build failed" }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RepoRoot "scripts\test-packaged-runtime.ps1") -RepoRoot $RepoRoot
if ($LASTEXITCODE -ne 0) { throw "Packaged runtime smoke failed" }

function Sign-File([string]$Path) {
    & $signtool.FullName sign /sha1 $thumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $Path
    if ($LASTEXITCODE -ne 0) { throw "Signing failed: $Path" }
}
if ($CertificateThumbprint) {
    Get-ChildItem $runtimeRoot -Filter *.exe -Recurse | ForEach-Object { Sign-File $_.FullName }
}

Push-Location $frontend
try {
    $tauriArgs = @("run", "tauri", "--", "build", "--bundles", "nsis")
    if ($CertificateThumbprint) {
        $overlay = @{
            bundle = @{
                windows = @{
                    certificateThumbprint = $thumbprint
                    digestAlgorithm = "SHA256"
                    timestampUrl = $TimestampUrl
                }
            }
        } | ConvertTo-Json -Depth 8
        Set-Content -LiteralPath $signingOverlay -Value $overlay -Encoding utf8
        $tauriArgs += @("--config", $signingOverlay)
    }
    npm.cmd @tauriArgs
    if ($LASTEXITCODE -ne 0) { throw "Tauri NSIS build failed with exit code $LASTEXITCODE" }
} finally {
    if (Test-Path -LiteralPath $signingOverlay) { Remove-Item -LiteralPath $signingOverlay -Force }
    Pop-Location
}

$installer = Get-ChildItem (Join-Path $tauriRoot "target\release\bundle\nsis") -Filter *.exe | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) { throw "NSIS installer was not generated" }
if ($CertificateThumbprint) { Sign-File $installer.FullName }
$hash = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath ($installer.FullName + ".sha256") -Value "$hash  $($installer.Name)" -Encoding ascii
if ($Unsigned) { Set-Content -LiteralPath ($installer.FullName + ".unsigned.txt") -Value "UNSIGNED INTERNAL BUILD; SmartScreen may warn." -Encoding ascii }
Write-Host "Installer: $($installer.FullName)"
Write-Host "SHA-256: $hash"
if ($CertificateThumbprint) { Get-AuthenticodeSignature -FilePath $installer.FullName | Format-List Status,SignerCertificate,StatusMessage }
