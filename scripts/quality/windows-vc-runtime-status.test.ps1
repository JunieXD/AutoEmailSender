$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-ExitCode {
  param(
    [Parameter(Mandatory = $true)][int]$Expected,
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [Parameter(Mandatory = $true)][string]$Message
  )

  & $script:PowerShellPath -NoLogo -NoProfile -ExecutionPolicy Bypass -File $script:StatusScript @Arguments | Out-Host
  if ($LASTEXITCODE -ne $Expected) {
    throw "$Message Expected exit code $Expected, received $LASTEXITCODE."
  }
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:StatusScript = Join-Path $repoRoot "desktop\build\windows-vc-runtime-status.ps1"
$runtimePath = Join-Path $repoRoot "desktop\build\runtime\vc_redist.x64.exe"
$script:PowerShellPath = (Get-Process -Id $PID).Path
if (-not (Test-Path -LiteralPath $script:StatusScript -PathType Leaf)) {
  throw "VC++ runtime status script is missing: $script:StatusScript"
}
if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) {
  throw "Prepared VC++ runtime is missing: $runtimePath"
}

$requiredVersion = [System.Version](Get-Item -LiteralPath $runtimePath).VersionInfo.FileVersion
$previousTestFlag = $env:AUTO_EMAIL_SENDER_PACKAGING_TEST
try {
  $env:AUTO_EMAIL_SENDER_PACKAGING_TEST = "1"
  Assert-ExitCode `
    -Expected 0 `
    -Arguments @(
      "-RuntimePath", $runtimePath,
      "-TestInstalledVersionOverride", $requiredVersion.ToString()
    ) `
    -Message "An equal installed runtime must be accepted."
  Assert-ExitCode `
    -Expected 0 `
    -Arguments @(
      "-RuntimePath", $runtimePath,
      "-TestInstalledVersionOverride", "99.0.0.0"
    ) `
    -Message "A newer installed runtime must be accepted."
  Assert-ExitCode `
    -Expected 1 `
    -Arguments @(
      "-RuntimePath", $runtimePath,
      "-TestInstalledVersionOverride", "1.0.0.0"
    ) `
    -Message "An older installed runtime must require installation."
  Assert-ExitCode `
    -Expected 1 `
    -Arguments @(
      "-RuntimePath", $runtimePath,
      "-TestInstalledVersionOverride", "not-a-version"
    ) `
    -Message "An invalid installed runtime version must fail closed."
  Assert-ExitCode `
    -Expected 1 `
    -Arguments @("-RuntimePath", (Join-Path $env:TEMP "missing-vc-runtime.exe")) `
    -Message "A missing bundled runtime must fail closed."
} finally {
  $env:AUTO_EMAIL_SENDER_PACKAGING_TEST = $previousTestFlag
}

Write-Host "Windows VC++ runtime status tests passed."
