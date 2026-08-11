param(
  [Parameter(Mandatory = $true)]
  [string]$RuntimePath,
  [string[]]$TestInstalledVersionOverride = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function ConvertTo-RuntimeVersion {
  param([AllowNull()][object]$Value)

  if ($null -eq $Value) {
    return $null
  }
  $text = ([string]$Value).Trim().TrimStart("v", "V")
  $parsed = $null
  if ([System.Version]::TryParse($text, [ref]$parsed)) {
    return $parsed
  }
  return $null
}

function Get-InstalledX64RuntimeVersions {
  $versions = New-Object System.Collections.Generic.List[System.Version]
  foreach ($view in @(
    [Microsoft.Win32.RegistryView]::Registry64,
    [Microsoft.Win32.RegistryView]::Registry32
  )) {
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
      [Microsoft.Win32.RegistryHive]::LocalMachine,
      $view
    )
    try {
      $key = $base.OpenSubKey("SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64")
      if ($null -eq $key) {
        continue
      }
      try {
        if ([int]$key.GetValue("Installed", 0) -ne 1) {
          continue
        }
        $version = ConvertTo-RuntimeVersion $key.GetValue("Version", $null)
        if ($null -ne $version) {
          $versions.Add($version)
        }
      } finally {
        $key.Dispose()
      }
    } finally {
      $base.Dispose()
    }
  }
  return $versions.ToArray()
}

try {
  $runtime = Get-Item -LiteralPath $RuntimePath -ErrorAction Stop
  if (-not $runtime.PSIsContainer -and $runtime.Extension -ieq ".exe") {
    $requiredVersion = ConvertTo-RuntimeVersion $runtime.VersionInfo.FileVersion
  } else {
    $requiredVersion = $null
  }
  if ($null -eq $requiredVersion) {
    Write-Host "Unable to determine the bundled VC++ runtime version."
    exit 1
  }

  if ($TestInstalledVersionOverride.Count -gt 0) {
    if ($env:AUTO_EMAIL_SENDER_PACKAGING_TEST -ne "1") {
      throw "TestInstalledVersionOverride is restricted to packaging tests."
    }
    $installedVersions = @(
      $TestInstalledVersionOverride |
        ForEach-Object { ConvertTo-RuntimeVersion $_ } |
        Where-Object { $null -ne $_ }
    )
  } else {
    $installedVersions = @(Get-InstalledX64RuntimeVersions)
  }

  $compatibleVersion = @(
    $installedVersions |
      Where-Object { $_ -ge $requiredVersion } |
      Sort-Object -Descending |
      Select-Object -First 1
  )
  if ($compatibleVersion.Count -gt 0) {
    Write-Host "Compatible Microsoft VC++ x64 runtime is already installed: $($compatibleVersion[0]) (required $requiredVersion)."
    exit 0
  }

  Write-Host "Microsoft VC++ x64 runtime installation is required: required $requiredVersion."
  exit 1
} catch {
  Write-Host "VC++ runtime compatibility check failed: $($_.Exception.Message)"
  exit 1
}
