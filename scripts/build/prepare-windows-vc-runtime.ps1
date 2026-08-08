param(
  [string]$OutputPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
if (-not $OutputPath) {
  $OutputPath = Join-Path $RepoRoot "desktop\build\runtime\vc_redist.x64.exe"
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
$DownloadUrl = "https://aka.ms/vs/17/release/vc_redist.x64.exe"

function Test-MicrosoftRedistributable {
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return $false
  }
  $signature = Get-AuthenticodeSignature -LiteralPath $Path
  return (
    $signature.Status -eq [System.Management.Automation.SignatureStatus]::Valid -and
    $null -ne $signature.SignerCertificate -and
    $signature.SignerCertificate.Subject -match "Microsoft Corporation"
  )
}

if (Test-MicrosoftRedistributable -Path $OutputPath) {
  Write-Host "Using verified Microsoft VC++ runtime: $OutputPath"
  exit 0
}

$OutputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$TemporaryPath = Join-Path $env:TEMP ("vc_redist.x64-" + [guid]::NewGuid().ToString("N") + ".exe")

try {
  Write-Host "Downloading Microsoft Visual C++ x64 Redistributable..."
  Invoke-WebRequest -UseBasicParsing -Uri $DownloadUrl -OutFile $TemporaryPath
  if (-not (Test-MicrosoftRedistributable -Path $TemporaryPath)) {
    throw "Downloaded VC++ runtime is not validly signed by Microsoft Corporation."
  }
  Move-Item -Force -LiteralPath $TemporaryPath -Destination $OutputPath
  Write-Host "Prepared verified Microsoft VC++ runtime: $OutputPath"
} finally {
  Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $TemporaryPath
}
