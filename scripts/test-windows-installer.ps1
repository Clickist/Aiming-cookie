[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [switch]$RequireValidSignature,
    [switch]$InstallSmoke
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
    New-Item -ItemType Directory -Path $installRoot | Out-Null
    try {
        Start-Process -FilePath $InstallerPath -ArgumentList @("/S", "/D=$installRoot") -Wait -NoNewWindow
        $app = Get-ChildItem -LiteralPath $installRoot -Filter *.exe -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $app) { throw "Installer did not place an executable in $installRoot" }
        Write-Host "Install smoke placed: $($app.FullName)"
        $uninstaller = Join-Path $installRoot "uninstall.exe"
        if (Test-Path -LiteralPath $uninstaller) {
            Start-Process -FilePath $uninstaller -ArgumentList "/S" -Wait -NoNewWindow
            if (Test-Path -LiteralPath $installRoot) { Write-Warning "Uninstaller left files at $installRoot" }
        } else {
            Write-Warning "Uninstaller was not found; launch/close smoke must be run manually"
        }
    } finally {
        # The smoke directory only contains generated installer output.
        if (Test-Path -LiteralPath $installRoot) { Remove-Item -LiteralPath $installRoot -Recurse -Force -ErrorAction SilentlyContinue }
    }
}
