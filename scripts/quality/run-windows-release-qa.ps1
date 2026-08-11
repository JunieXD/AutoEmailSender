param(
  [string]$BundlePath = "",
  [string]$CheckoutPath = "$env:USERPROFILE\Projects\AutoEmailSender-Windows-QA",
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^[0-9a-fA-F]{40}$')]
  [string]$ExpectedRevision,
  [ValidatePattern('^$|^[0-9a-fA-F]{40}$')]
  [string]$PreviousRevision = "",
  [string]$PreviousInstallerPath = "",
  [string]$ExpectedPreviousVersion = "",
  [ValidatePattern('^$|^[0-9a-fA-F]{64}$')]
  [string]$ExpectedPreviousPackageSha256 = "",
  [string]$CandidateInstallerPath = "",
  [ValidatePattern('^$|^[0-9a-fA-F]{64}$')]
  [string]$ExpectedCandidatePackageSha256 = "",
  [string]$CandidateManifestPath = "",
  [long]$ExpectedCandidateRunId = 0,
  [switch]$ForceFull,
  [ValidateSet("release", "prerelease", "quick", "candidate-admission", "harness-rehearsal")]
  [string]$Mode = "release",
  [switch]$InjectInterruptionAfterPreviousInstall,
  [switch]$RequireRecoveredStaleState,
  [switch]$RunNormalSoak,
  [switch]$RunSeededChaos,
  [ValidateRange(1, 604800)]
  [int]$NormalSoakDurationSeconds = 86400,
  [ValidateRange(1, 604800)]
  [int]$SeededChaosDurationSeconds = 28800,
  [int]$SeededChaosSeed = 20260810
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$IsFormal = $Mode -in @("release", "prerelease")
$IsPackagedPreflight = $Mode -in @("candidate-admission", "harness-rehearsal")
$RunsPackagedLifecycle = $IsFormal -or $IsPackagedPreflight
$RequiresExactCandidate = $IsFormal -or $Mode -eq "candidate-admission"

if ($Mode -eq "release" -and $NormalSoakDurationSeconds -lt 86400) {
  throw "Stable release normal soak requires at least 86400 seconds."
}
if ($Mode -eq "release" -and $SeededChaosDurationSeconds -lt 28800) {
  throw "Stable release seeded chaos requires at least 28800 seconds."
}
if ($Mode -eq "prerelease" -and $NormalSoakDurationSeconds -lt 300) {
  throw "Prerelease normal soak requires at least 300 seconds."
}
if ($Mode -eq "prerelease" -and $SeededChaosDurationSeconds -lt 300) {
  throw "Prerelease seeded chaos requires at least 300 seconds."
}
if ($IsPackagedPreflight -and ($RunNormalSoak -or $RunSeededChaos)) {
  throw "Packaged preflight cannot run normal soak or seeded chaos."
}
if ($InjectInterruptionAfterPreviousInstall -and $Mode -ne "harness-rehearsal") {
  throw "Intentional interruption is only valid in harness-rehearsal mode."
}
if ($RequireRecoveredStaleState -and $Mode -ne "harness-rehearsal") {
  throw "Stale-state recovery assertions are only valid in harness-rehearsal mode."
}

if ($RunsPackagedLifecycle) {
  if ([string]::IsNullOrWhiteSpace($PreviousInstallerPath)) {
    throw "Release QA requires -PreviousInstallerPath for a real previous-stable in-place upgrade."
  }
  if ([string]::IsNullOrWhiteSpace($ExpectedPreviousVersion)) {
    throw "Release QA requires -ExpectedPreviousVersion for previous-stable provenance."
  }
  if ([string]::IsNullOrWhiteSpace($ExpectedPreviousPackageSha256)) {
    throw "Release QA requires -ExpectedPreviousPackageSha256 for previous-stable provenance."
  }
  if ([string]::IsNullOrWhiteSpace($CandidateInstallerPath)) {
    throw "Release QA requires -CandidateInstallerPath for the exact candidate NSIS asset."
  }
  if ([string]::IsNullOrWhiteSpace($ExpectedCandidatePackageSha256)) {
    throw "Release QA requires -ExpectedCandidatePackageSha256 from the candidate manifest."
  }
  if ($RequiresExactCandidate -and [string]::IsNullOrWhiteSpace($CandidateManifestPath)) {
    throw "Exact candidate QA requires -CandidateManifestPath from the same candidate workflow."
  }
  if ($RequiresExactCandidate -and $ExpectedCandidateRunId -le 0) {
    throw "Exact candidate QA requires a positive -ExpectedCandidateRunId."
  }
  if ($Mode -eq "harness-rehearsal" -and (
    -not [string]::IsNullOrWhiteSpace($CandidateManifestPath) -or
    $ExpectedCandidateRunId -ne 0
  )) {
    throw "Harness rehearsal must not bind an invalidated candidate manifest or run ID."
  }
  if ($IsPackagedPreflight -and $ForceFull) {
    throw "Packaged preflight skips source/build stages and does not accept -ForceFull."
  }
  $PreviousInstallerPath = [System.IO.Path]::GetFullPath($PreviousInstallerPath)
  if (-not (Test-Path -LiteralPath $PreviousInstallerPath -PathType Leaf)) {
    throw "Previous stable installer is missing: $PreviousInstallerPath"
  }
  $actualPreviousPackageSha256 = (
    Get-FileHash -LiteralPath $PreviousInstallerPath -Algorithm SHA256
  ).Hash.ToLowerInvariant()
  if ($actualPreviousPackageSha256 -ne $ExpectedPreviousPackageSha256.ToLowerInvariant()) {
    throw "Previous stable installer SHA-256 does not match the host-provided digest."
  }
  $ExpectedPreviousPackageSha256 = $ExpectedPreviousPackageSha256.ToLowerInvariant()
  $CandidateInstallerPath = [System.IO.Path]::GetFullPath($CandidateInstallerPath)
  if (-not (Test-Path -LiteralPath $CandidateInstallerPath -PathType Leaf)) {
    throw "Candidate installer is missing: $CandidateInstallerPath"
  }
  $actualCandidatePackageSha256 = (
    Get-FileHash -LiteralPath $CandidateInstallerPath -Algorithm SHA256
  ).Hash.ToLowerInvariant()
  if ($actualCandidatePackageSha256 -ne $ExpectedCandidatePackageSha256.ToLowerInvariant()) {
    throw "Candidate installer SHA-256 does not match the candidate manifest digest."
  }
  $ExpectedCandidatePackageSha256 = $ExpectedCandidatePackageSha256.ToLowerInvariant()
  if ($RequiresExactCandidate) {
    $CandidateManifestPath = [System.IO.Path]::GetFullPath($CandidateManifestPath)
    if (-not (Test-Path -LiteralPath $CandidateManifestPath -PathType Leaf)) {
      throw "Candidate manifest is missing: $CandidateManifestPath"
    }
  }
}

function Invoke-QaStep {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name,
    [Parameter(Mandatory = $true)]
    [scriptblock]$Action
  )

  Write-Host "`n=== $Name ==="
  $timer = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    & $Action
    if ($LASTEXITCODE -ne 0) {
      throw "$Name failed with exit code $LASTEXITCODE"
    }
  } finally {
    $timer.Stop()
    Write-Host ("--- {0}: {1:n1}s ---" -f $Name, $timer.Elapsed.TotalSeconds)
  }
}

function Assert-NativeSuccess {
  param([Parameter(Mandatory = $true)][string]$Operation)

  if ($LASTEXITCODE -ne 0) {
    throw "$Operation failed with exit code $LASTEXITCODE"
  }
}

function Get-AgentStatus {
  param([Parameter(Mandatory = $true)][string]$CliExecutable)

  $output = & $CliExecutable --format json status 2>&1 | Out-String
  try {
    $payload = $output | ConvertFrom-Json
    if ($payload.ok -ne $true -or $null -eq $payload.data) {
      throw "Agent CLI returned an unsuccessful status payload."
    }
    return $payload.data
  } catch {
    throw "Agent CLI did not return JSON: $output"
  }
}

function Get-AgentRuntimeDescriptor {
  $runtimePath = Join-Path $env:APPDATA "auto-email-sender-desktop\agent\runtime.json"
  if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) {
    throw "Agent runtime descriptor is missing: $runtimePath"
  }
  return Get-Content -Raw -LiteralPath $runtimePath | ConvertFrom-Json
}

function Wait-AgentReady {
  param(
    [Parameter(Mandatory = $true)][string]$CliExecutable,
    [int]$TimeoutSeconds = 120
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    try {
      $status = Get-AgentStatus -CliExecutable $CliExecutable
      if ($status.backend_ready -eq $true -and $status.state -eq "ready") {
        return $status
      }
    } catch {
      # Startup can publish the descriptor before the authenticated backend is ready.
    }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)

  throw "Agent backend did not become ready within $TimeoutSeconds seconds."
}

function Stop-QaDesktopTree {
  param([Parameter(Mandatory = $true)][int]$DesktopPid)

  & taskkill.exe /PID $DesktopPid /T /F | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to stop QA desktop process tree $DesktopPid"
  }
}

function Stop-StaleQaCheckoutProcesses {
  param([Parameter(Mandatory = $true)][string]$RootPath)

  $checkoutPrefix = [System.IO.Path]::GetFullPath($RootPath).TrimEnd("\") + "\"
  $staleProcesses = @(
    Get-CimInstance Win32_Process | Where-Object {
      $_.ExecutablePath -and
      $_.ExecutablePath.StartsWith($checkoutPrefix, [System.StringComparison]::OrdinalIgnoreCase)
    }
  )
  if ($staleProcesses.Count -eq 0) {
    Write-Host "No stale process is running from the dedicated QA checkout."
    return
  }

  foreach ($process in $staleProcesses) {
    Write-Host "Stopping stale QA process $($process.Name) ($($process.ProcessId)): $($process.ExecutablePath)"
    & taskkill.exe /PID $process.ProcessId /T /F | Out-Host
    if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue)) {
      throw "Unable to stop stale QA process $($process.ProcessId)."
    }
  }
}

function Stop-QaProcessesFromRoot {
  param([Parameter(Mandatory = $true)][string]$RootPath)

  if (-not (Test-Path -LiteralPath $RootPath)) {
    return
  }
  $rootPrefix = [System.IO.Path]::GetFullPath($RootPath).TrimEnd("\") + "\"
  $processes = @(
    Get-CimInstance Win32_Process | Where-Object {
      $_.ExecutablePath -and
      $_.ExecutablePath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
    }
  )
  foreach ($process in $processes) {
    & taskkill.exe /PID $process.ProcessId /T /F | Out-Host
    if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue)) {
      throw "Unable to stop packaged QA process $($process.ProcessId)."
    }
  }
}

function Get-QaInstallerRegistrations {
  param([Parameter(Mandatory = $true)][string]$QaBasePath)

  $qaBasePrefix = [System.IO.Path]::GetFullPath($QaBasePath).TrimEnd("\") + "\"
  $uninstallRoot = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
  if (-not (Test-Path -LiteralPath $uninstallRoot)) {
    return @()
  }

  $registrations = New-Object System.Collections.Generic.List[object]
  foreach ($key in @(Get-ChildItem -LiteralPath $uninstallRoot)) {
    $entry = Get-ItemProperty -LiteralPath $key.PSPath
    $displayNameProperty = $entry.PSObject.Properties["DisplayName"]
    $uninstallStringProperty = $entry.PSObject.Properties["UninstallString"]
    if (
      $null -eq $displayNameProperty -or
      $null -eq $uninstallStringProperty -or
      -not ($displayNameProperty.Value -is [string]) -or
      -not $displayNameProperty.Value.StartsWith(
        "Auto Email Sender ",
        [System.StringComparison]::Ordinal
      )
    ) {
      continue
    }
    $uninstallString = [string]$uninstallStringProperty.Value
    if ($uninstallString -notmatch '^\s*"([^"]+)"') {
      continue
    }
    try {
      $uninstallerPath = [System.IO.Path]::GetFullPath($Matches[1])
    } catch {
      continue
    }
    if (-not $uninstallerPath.StartsWith(
      $qaBasePrefix,
      [System.StringComparison]::OrdinalIgnoreCase
    )) {
      continue
    }
    $registrations.Add([pscustomobject]@{
      RegistryPath = $key.PSPath
      DisplayName = [string]$displayNameProperty.Value
      UninstallerPath = $uninstallerPath
      InstallRoot = Split-Path -Parent $uninstallerPath
    })
  }
  return $registrations.ToArray()
}

function Remove-QaInstallerRegistrations {
  param(
    [Parameter(Mandatory = $true)][string]$QaBasePath,
    [string]$InstallRoot = ""
  )

  $expectedRoot = if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    ""
  } else {
    [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd("\")
  }
  foreach ($registration in @(Get-QaInstallerRegistrations -QaBasePath $QaBasePath)) {
    if (
      $expectedRoot -and
      -not $registration.InstallRoot.Equals(
        $expectedRoot,
        [System.StringComparison]::OrdinalIgnoreCase
      )
    ) {
      continue
    }
    Stop-QaProcessesFromRoot -RootPath $registration.InstallRoot
    Write-Host (
      "Removing stale dedicated QA installer registration: {0} -> {1}" -f
      $registration.DisplayName,
      $registration.UninstallerPath
    )
    Remove-Item -LiteralPath $registration.RegistryPath -Recurse -Force
  }
}

function Test-HarnessEphemeralRuntimeStateAbsent {
  param(
    [Parameter(Mandatory = $true)][string]$UserDataPath
  )

  try {
    return -not (Test-Path `
      -LiteralPath (Join-Path $UserDataPath "runtime") `
      -ErrorAction Stop)
  } catch {
    return $false
  }
}

function Remove-HarnessEphemeralRuntimeState {
  param(
    [Parameter(Mandatory = $true)][string]$UserDataPath
  )

  $userDataFullPath = [System.IO.Path]::GetFullPath($UserDataPath).TrimEnd("\")
  $runtimePath = [System.IO.Path]::GetFullPath(
    (Join-Path $userDataFullPath "runtime")
  ).TrimEnd("\")
  if (-not ([System.IO.Path]::GetDirectoryName($runtimePath)).Equals(
    $userDataFullPath,
    [System.StringComparison]::OrdinalIgnoreCase
  )) {
    throw "Harness runtime cleanup escaped its userData root: $runtimePath"
  }
  if (-not (Test-Path -LiteralPath $runtimePath -ErrorAction Stop)) {
    return
  }
  $runtimeItem = Get-Item -LiteralPath $runtimePath -Force -ErrorAction Stop
  if ($runtimeItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    throw "Harness runtime cleanup refuses a reparse point: $runtimePath"
  }
  Remove-Item -LiteralPath $runtimePath -Recurse -Force -ErrorAction Stop
  if (Test-Path -LiteralPath $runtimePath -ErrorAction Stop) {
    throw "Harness runtime cleanup did not remove: $runtimePath"
  }
}

function Get-ValidatedHarnessSeedCheckpoint {
  param(
    [Parameter(Mandatory = $true)][string]$QaBasePath,
    [Parameter(Mandatory = $true)][string]$ExpectedPreviousVersion,
    [Parameter(Mandatory = $true)][string]$ExpectedPreviousPackageSha256,
    [string]$RequiredQaRoot = ""
  )

  $qaBaseFullPath = [System.IO.Path]::GetFullPath($QaBasePath).TrimEnd("\")
  if (
    -not (Test-Path -LiteralPath $qaBaseFullPath -PathType Container) -or
    ((Get-Item -LiteralPath $qaBaseFullPath).Attributes -band [System.IO.FileAttributes]::ReparsePoint)
  ) {
    throw "Dedicated QA base is missing or is a reparse point: $qaBaseFullPath"
  }
  $qaRoots = if ([string]::IsNullOrWhiteSpace($RequiredQaRoot)) {
    @(
      Get-ChildItem -LiteralPath $qaBaseFullPath -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    )
  } else {
    @([System.IO.DirectoryInfo]::new([System.IO.Path]::GetFullPath($RequiredQaRoot)))
  }
  foreach ($qaRootItem in $qaRoots) {
    $qaRoot = [System.IO.Path]::GetFullPath($qaRootItem.FullName).TrimEnd("\")
    if (
      -not (Test-Path -LiteralPath $qaRoot -PathType Container) -or
      -not ([System.IO.Path]::GetDirectoryName($qaRoot)).Equals(
        $qaBaseFullPath,
        [System.StringComparison]::OrdinalIgnoreCase
      ) -or
      ((Get-Item -LiteralPath $qaRoot).Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    ) {
      continue
    }

    $installRoot = Join-Path $qaRoot "安装 路径 Ω"
    $appExecutable = Join-Path $installRoot "Auto Email Sender.exe"
    $uninstaller = Join-Path $installRoot "Uninstall Auto Email Sender.exe"
    $browserRuntime = Join-Path $installRoot "resources\ms-playwright"
    $userData = Join-Path $qaRoot "auto-email-sender-packaged-qa\previous-stable-user-data\用户 数据 Ω"
    $database = Join-Path $userData "auto_email_sender.db"
    $upgradeManifest = Join-Path $qaRoot "previous-upgrade\manifest.json"
    $packageRoot = Join-Path $qaRoot "candidate packages"
    $previousPackage = Join-Path $packageRoot "previous-stable.exe"
    if (
      -not (Test-Path -LiteralPath $appExecutable -PathType Leaf) -or
      -not (Test-Path -LiteralPath $uninstaller -PathType Leaf) -or
      -not (Test-Path -LiteralPath $browserRuntime -PathType Container) -or
      -not (Test-Path -LiteralPath $database -PathType Leaf) -or
      -not (Test-Path -LiteralPath $upgradeManifest -PathType Leaf) -or
      -not (Test-Path -LiteralPath $previousPackage -PathType Leaf) -or
      ((Get-Item -LiteralPath $installRoot).Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
      ((Get-Item -LiteralPath $browserRuntime).Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
      ((Get-Item -LiteralPath $userData).Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
      ((Get-Item -LiteralPath $database).Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
      -not (Test-HarnessEphemeralRuntimeStateAbsent -UserDataPath $userData)
    ) {
      continue
    }

    try {
      $manifestText = [System.IO.File]::ReadAllText(
        $upgradeManifest,
        [System.Text.UTF8Encoding]::new($false)
      )
      $manifest = $manifestText | ConvertFrom-Json
      $manifestUserData = [System.IO.Path]::GetFullPath([string]$manifest.user_data_path)
      $manifestArtifactRoot = [System.IO.Path]::GetFullPath([string]$manifest.previous_artifact_root)
      $appVersionValue = [System.Version](Get-Item -LiteralPath $appExecutable).VersionInfo.ProductVersion
      $appVersion = "$($appVersionValue.Major).$($appVersionValue.Minor).$($appVersionValue.Build)"
      if (
        $manifest.protocol_version -ne "1" -or
        $manifest.purpose -ne "previous-stable-packaged-upgrade" -or
        [string]$manifest.previous_app_version -ne $ExpectedPreviousVersion -or
        $appVersion -ne $ExpectedPreviousVersion -or
        [string]$manifest.previous_package_sha256 -ne $ExpectedPreviousPackageSha256 -or
        [string]$manifest.integrity_check -ne "ok" -or
        [int]$manifest.foreign_key_violations -ne 0 -or
        -not $manifestUserData.Equals(
          [System.IO.Path]::GetFullPath($userData),
          [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not $manifestArtifactRoot.Equals(
          [System.IO.Path]::GetFullPath($installRoot),
          [System.StringComparison]::OrdinalIgnoreCase
        )
      ) {
        continue
      }
      $databaseSha256 = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash.ToLowerInvariant()
      if ([string]$manifest.database_sha256 -ne $databaseSha256) {
        continue
      }
      $appSha256 = (Get-FileHash -LiteralPath $appExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
      if ([string]$manifest.previous_executable_sha256 -ne $appSha256) {
        continue
      }
    } catch {
      continue
    }

    return [pscustomobject]@{
      QaRoot = $qaRoot
      InstallRoot = $installRoot
      EvidenceRoot = Join-Path $qaRoot "evidence"
      PackageRoot = $packageRoot
      UpgradeUserData = $userData
      UpgradeManifest = $upgradeManifest
      PreviousAppExecutable = $appExecutable
    }
  }
  return $null
}

function Invoke-QaMirrorCopy {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )

  if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    throw "Harness seed mirror source is missing: $Source"
  }
  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  $null = & robocopy.exe `
    $Source `
    $Destination `
    /MIR `
    /COPY:DAT `
    /DCOPY:DAT `
    /R:0 `
    /W:0 `
    /XJ `
    /NFL `
    /NDL `
    /NJH `
    /NJS `
    /NP
  $robocopyExitCode = $LASTEXITCODE
  if ($robocopyExitCode -gt 7) {
    throw "Harness seed mirror failed with robocopy exit code $robocopyExitCode."
  }
}

function Save-HarnessSeedBackup {
  param(
    [Parameter(Mandatory = $true)][object]$Checkpoint,
    [Parameter(Mandatory = $true)][string]$ExpectedPreviousVersion,
    [Parameter(Mandatory = $true)][string]$ExpectedPreviousPackageSha256
  )

  $qaRoot = [string]$Checkpoint.QaRoot
  $backupRoot = Join-Path $qaRoot ".harness-previous-seed"
  $markerPath = Join-Path $backupRoot "backup.json"
  if (Test-Path -LiteralPath $markerPath -PathType Leaf) {
    try {
      $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
      if (
        $marker.protocol_version -eq "2" -and
        $marker.purpose -eq "previous-stable-harness-seed-backup" -and
        [string]$marker.qa_root -eq $qaRoot -and
        [string]$marker.previous_version -eq $ExpectedPreviousVersion -and
        [string]$marker.previous_package_sha256 -eq $ExpectedPreviousPackageSha256
      ) {
        return
      }
    } catch {
      throw "Existing harness seed backup marker is invalid: $markerPath"
    }
    throw "Existing harness seed backup does not match the required baseline: $markerPath"
  }
  if (Test-Path -LiteralPath $backupRoot) {
    throw "Unmarked harness seed backup path already exists: $backupRoot"
  }

  $temporaryBackup = "$backupRoot.tmp-$([Guid]::NewGuid().ToString('N'))"
  try {
    Invoke-QaMirrorCopy `
      -Source ([string]$Checkpoint.InstallRoot) `
      -Destination (Join-Path $temporaryBackup "install")
    Invoke-QaMirrorCopy `
      -Source ([string]$Checkpoint.UpgradeUserData) `
      -Destination (Join-Path $temporaryBackup "user-data")
    Remove-HarnessEphemeralRuntimeState `
      -UserDataPath (Join-Path $temporaryBackup "user-data")
    $manifestBackup = Join-Path $temporaryBackup "upgrade-manifest.json"
    [System.IO.File]::Copy(
      [string]$Checkpoint.UpgradeManifest,
      $manifestBackup,
      $true
    )
    $marker = [ordered]@{
      protocol_version = "2"
      purpose = "previous-stable-harness-seed-backup"
      qa_root = $qaRoot
      previous_version = $ExpectedPreviousVersion
      previous_package_sha256 = $ExpectedPreviousPackageSha256
      created_at = [datetime]::UtcNow.ToString("o")
    }
    $markerJson = ($marker | ConvertTo-Json -Depth 4) + "`n"
    [System.IO.File]::WriteAllText(
      (Join-Path $temporaryBackup "backup.json"),
      $markerJson,
      [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporaryBackup -Destination $backupRoot
  } catch {
    if (Test-Path -LiteralPath $temporaryBackup) {
      Remove-Item -LiteralPath $temporaryBackup -Recurse -Force
    }
    throw
  }
  Write-Host "Saved validated previous-stable harness seed backup: $backupRoot"
}

function Restore-HarnessSeedBackup {
  param(
    [Parameter(Mandatory = $true)][string]$QaBasePath,
    [Parameter(Mandatory = $true)][string]$ExpectedPreviousVersion,
    [Parameter(Mandatory = $true)][string]$ExpectedPreviousPackageSha256
  )

  $qaBaseFullPath = [System.IO.Path]::GetFullPath($QaBasePath).TrimEnd("\")
  $qaRoots = @(
    Get-ChildItem -LiteralPath $qaBaseFullPath -Directory -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending
  )
  foreach ($qaRootItem in $qaRoots) {
    $qaRoot = [System.IO.Path]::GetFullPath($qaRootItem.FullName).TrimEnd("\")
    if (
      -not ([System.IO.Path]::GetDirectoryName($qaRoot)).Equals(
        $qaBaseFullPath,
        [System.StringComparison]::OrdinalIgnoreCase
      ) -or
      ((Get-Item -LiteralPath $qaRoot).Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    ) {
      continue
    }
    $backupRoot = Join-Path $qaRoot ".harness-previous-seed"
    $markerPath = Join-Path $backupRoot "backup.json"
    if (
      -not (Test-Path -LiteralPath $markerPath -PathType Leaf) -or
      ((Get-Item -LiteralPath $backupRoot).Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    ) {
      continue
    }
    try {
      $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
      if (
        $marker.protocol_version -ne "2" -or
        $marker.purpose -ne "previous-stable-harness-seed-backup" -or
        [string]$marker.qa_root -ne $qaRoot -or
        [string]$marker.previous_version -ne $ExpectedPreviousVersion -or
        [string]$marker.previous_package_sha256 -ne $ExpectedPreviousPackageSha256
      ) {
        continue
      }
      $installRoot = Join-Path $qaRoot "安装 路径 Ω"
      Stop-QaProcessesFromRoot -RootPath $installRoot
      Invoke-QaMirrorCopy `
        -Source (Join-Path $backupRoot "install") `
        -Destination $installRoot
      $userData = Join-Path $qaRoot "auto-email-sender-packaged-qa\previous-stable-user-data\用户 数据 Ω"
      Invoke-QaMirrorCopy `
        -Source (Join-Path $backupRoot "user-data") `
        -Destination $userData
      Remove-HarnessEphemeralRuntimeState -UserDataPath $userData
      [System.IO.File]::Copy(
        (Join-Path $backupRoot "upgrade-manifest.json"),
        (Join-Path $qaRoot "previous-upgrade\manifest.json"),
        $true
      )
      $checkpoint = Get-ValidatedHarnessSeedCheckpoint `
        -QaBasePath $qaBaseFullPath `
        -ExpectedPreviousVersion $ExpectedPreviousVersion `
        -ExpectedPreviousPackageSha256 $ExpectedPreviousPackageSha256 `
        -RequiredQaRoot $qaRoot
      if ($null -eq $checkpoint) {
        throw "Restored harness seed backup failed baseline validation: $qaRoot"
      }
      Write-Host "Restored and revalidated previous-stable harness seed backup: $qaRoot"
      return $checkpoint
    } catch {
      Write-Warning "Ignoring unusable harness seed backup at ${qaRoot}: $($_.Exception.Message)"
    }
  }
  return $null
}

function Add-QaHarnessInstallerRegistration {
  param(
    [Parameter(Mandatory = $true)][string]$QaBasePath,
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$Version
  )

  $existing = @(
    Get-QaInstallerRegistrations -QaBasePath $QaBasePath |
      Where-Object {
        $_.InstallRoot.Equals(
          [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd("\"),
          [System.StringComparison]::OrdinalIgnoreCase
        )
      }
  )
  if ($existing.Count -gt 0) {
    return
  }
  $uninstaller = Join-Path $InstallRoot "Uninstall Auto Email Sender.exe"
  if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
    throw "Reusable harness seed has no uninstaller: $uninstaller"
  }
  $registrationPath = Join-Path `
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" `
    ("AutoEmailSenderHarnessRehearsal-" + [guid]::NewGuid().ToString("N"))
  New-Item -Path $registrationPath -Force | Out-Null
  Set-ItemProperty -LiteralPath $registrationPath -Name "DisplayName" -Value "Auto Email Sender $Version"
  Set-ItemProperty `
    -LiteralPath $registrationPath `
    -Name "UninstallString" `
    -Value ('"' + $uninstaller + '" /currentuser')
}

function Copy-QaPackage {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )

  $sourcePath = [System.IO.Path]::GetFullPath($Source)
  $destinationPath = [System.IO.Path]::GetFullPath($Destination)
  if ($sourcePath.Equals($destinationPath, [System.StringComparison]::OrdinalIgnoreCase)) {
    return
  }
  Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
}

function Get-QaVcRedistTimeoutDiagnostic {
  param([Parameter(Mandatory = $true)][datetime]$StartedAt)

  $cutoff = $StartedAt.AddSeconds(-5)
  $log = @(
    Get-ChildItem -LiteralPath $env:TEMP -Filter "dd_vcredist_*.log" -File -ErrorAction SilentlyContinue |
      Where-Object { $_.LastWriteTime -ge $cutoff } |
      Sort-Object LastWriteTime -Descending |
      Select-Object -First 1
  )
  if ($log.Count -eq 0) {
    return $null
  }

  $eventLines = @(
    Get-Content -LiteralPath $log[0].FullName -Tail 120 -ErrorAction SilentlyContinue |
      Where-Object { $_ -match '\][iwe][0-9]{3}: ' }
  )
  $lastEvent = if ($eventLines.Count -eq 0) {
    ""
  } else {
    [string]$eventLines[$eventLines.Count - 1]
  }
  if ($lastEvent.Length -gt 512) {
    $lastEvent = $lastEvent.Substring(0, 512)
  }

  return [pscustomobject]@{
    LogPath = $log[0].FullName
    Length = [long]$log[0].Length
    LastWriteTime = $log[0].LastWriteTime.ToString("o")
    SecondsSinceLastWrite = [math]::Round(
      ((Get-Date) - $log[0].LastWriteTime).TotalSeconds,
      1
    )
    LastEvent = $lastEvent
  }
}

function Invoke-QaExecutable {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string]$Arguments = "",
    [hashtable]$Environment = @{},
    [ValidateRange(1, 1800)]
    [int]$TimeoutSeconds = 600,
    [switch]$RejectVisibleWindow,
    [Parameter(Mandatory = $true)][string]$Operation
  )

  $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $FilePath
  $startInfo.UseShellExecute = $false
  # The Parallels runner intentionally invokes Windows PowerShell 5.1.  Its
  # .NET Framework ProcessStartInfo has no ArgumentList on the supported VM.
  # Arguments/EnvironmentVariables are the compatible APIs across supported
  # PowerShell 5.1 hosts, regardless of whether a host also exposes Environment.
  $startInfo.Arguments = $Arguments
  foreach ($name in $Environment.Keys) {
    $startInfo.EnvironmentVariables[[string]$name] = [string]$Environment[$name]
  }
  $startedAt = Get-Date
  $process = [System.Diagnostics.Process]::Start($startInfo)
  if ($null -eq $process) {
    throw "$Operation did not start."
  }
  try {
    $deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
    $timedOut = $false
    $unexpectedWindowTitle = ""
    while (-not $process.WaitForExit(500)) {
      if ($RejectVisibleWindow) {
        $process.Refresh()
        $observedWindowTitle = $process.MainWindowTitle
        if (-not [string]::IsNullOrWhiteSpace($observedWindowTitle)) {
          $unexpectedWindowTitle = $observedWindowTitle
          if ($unexpectedWindowTitle.Length -gt 256) {
            $unexpectedWindowTitle = $unexpectedWindowTitle.Substring(0, 256)
          }
          break
        }
      }
      if ([datetime]::UtcNow -ge $deadline) {
        $timedOut = $true
        break
      }
    }
    if ($timedOut -or $unexpectedWindowTitle) {
      $process.Refresh()
      $windowTitle = if ($unexpectedWindowTitle) {
        $unexpectedWindowTitle
      } else {
        $process.MainWindowTitle
      }
      $allProcesses = @(Get-CimInstance Win32_Process)
      $treePids = New-Object System.Collections.Generic.HashSet[int]
      $null = $treePids.Add([int]$process.Id)
      do {
        $added = $false
        foreach ($candidate in $allProcesses) {
          if (
            $treePids.Contains([int]$candidate.ParentProcessId) -and
            $treePids.Add([int]$candidate.ProcessId)
          ) {
            $added = $true
          }
        }
      } while ($added)
      $treeSummary = @(
        $allProcesses |
          Where-Object { $treePids.Contains([int]$_.ProcessId) } |
          ForEach-Object {
            [pscustomobject]@{
              Pid = [int]$_.ProcessId
              ParentPid = [int]$_.ParentProcessId
              Name = [string]$_.Name
              ExecutablePath = [string]$_.ExecutablePath
            }
          }
      )
      $treeLabel = if ($unexpectedWindowTitle) {
        "Unexpected-window process tree: "
      } else {
        "Timed-out process tree: "
      }
      Write-Warning ($treeLabel + ($treeSummary | ConvertTo-Json -Compress -Depth 3))
      if ($timedOut -and @($treeSummary | Where-Object { $_.Name -match '^vc_redist(?:\.x64)?\.exe$' }).Count -gt 0) {
        $vcDiagnostic = Get-QaVcRedistTimeoutDiagnostic -StartedAt $startedAt
        if ($null -ne $vcDiagnostic) {
          Write-Warning (
            "VC++ Burn timeout diagnostic: " +
            ($vcDiagnostic | ConvertTo-Json -Compress -Depth 3)
          )
        }
      }
      & taskkill.exe /PID $process.Id /T /F | Out-Host
      $stopped = $process.WaitForExit(30000)
      $windowDetail = if ([string]::IsNullOrWhiteSpace($windowTitle)) {
        ""
      } else {
        " Last window title: $windowTitle"
      }
      if (-not $stopped) {
        if ($unexpectedWindowTitle) {
          throw (
            "$Operation displayed an unexpected window during silent execution and " +
            "its process tree could not be stopped.$windowDetail"
          )
        }
        throw (
          "$Operation timed out after $TimeoutSeconds seconds and its process tree " +
          "could not be stopped.$windowDetail"
        )
      }
      if ($unexpectedWindowTitle) {
        throw "$Operation displayed an unexpected window during silent execution.$windowDetail"
      }
      throw "$Operation timed out after $TimeoutSeconds seconds.$windowDetail"
    }
    if ($process.ExitCode -ne 0) {
      throw "$Operation failed with exit code $($process.ExitCode)."
    }
  } finally {
    $process.Dispose()
  }
}

function Test-QaExecutableTimeoutRecovery {
  $timer = [System.Diagnostics.Stopwatch]::StartNew()
  $timedOutAsExpected = $false
  try {
    Invoke-QaExecutable `
      -FilePath "powershell.exe" `
      -Arguments '-NoLogo -NoProfile -Command "Start-Sleep -Seconds 120"' `
      -Environment @{} `
      -TimeoutSeconds 1 `
      -Operation "controlled packaged QA timeout probe"
  } catch {
    if ($_.Exception.Message -notmatch "timed out after 1 seconds") {
      throw
    }
    $timedOutAsExpected = $true
  } finally {
    $timer.Stop()
  }
  if (-not $timedOutAsExpected -or $timer.Elapsed.TotalSeconds -gt 35) {
    throw "Packaged QA timeout recovery was not bounded."
  }
  Write-Host ("Controlled timeout recovery passed in {0:n1}s." -f $timer.Elapsed.TotalSeconds)
}

function Get-StringSha256 {
  param([Parameter(Mandatory = $true)][string]$Value)

  $algorithm = [System.Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    return -join ($algorithm.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") })
  } finally {
    $algorithm.Dispose()
  }
}

function Get-StageFingerprint {
  param(
    [Parameter(Mandatory = $true)][string[]]$Paths,
    [string[]]$AdditionalValues = @(),
    [string]$Revision = "HEAD"
  )

  $parts = New-Object System.Collections.Generic.List[string]
  foreach ($path in $Paths) {
    $gitPath = $path.Replace("\", "/")
    # `rev-parse <revision>:<path>` writes a fatal native error when a path was
    # added after PreviousRevision.  With ErrorActionPreference=Stop that
    # expected cache miss terminates Windows PowerShell before LASTEXITCODE can
    # be inspected.  ls-tree reports an absent path as empty output with exit
    # code zero, while still failing closed for an invalid revision.
    $treeId = (& git -C $CheckoutPath ls-tree "--format=%(objectname)" $Revision -- $gitPath | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
      throw "Unable to fingerprint $gitPath at revision $Revision."
    }
    if (-not $treeId) {
      $treeId = "missing"
    }
    $parts.Add("$gitPath=$treeId")
  }
  foreach ($value in $AdditionalValues) {
    $parts.Add($value)
  }
  return Get-StringSha256 -Value ($parts -join "`n")
}

function Test-VerifiedStage {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Fingerprint,
    [string[]]$RequiredPaths = @()
  )

  if ($ForceFull -or -not $script:VerifiedStages.ContainsKey($Name)) {
    return $false
  }
  if ([string]$script:VerifiedStages[$Name] -ne $Fingerprint) {
    return $false
  }
  foreach ($path in $RequiredPaths) {
    if (-not (Test-Path -LiteralPath $path)) {
      return $false
    }
  }
  Write-Host "[reuse] $Name inputs and outputs match a previously successful stage."
  return $true
}

function Save-VerifiedStage {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Fingerprint
  )

  $script:VerifiedStages[$Name] = $Fingerprint
  New-Item -ItemType Directory -Force -Path $script:QaCacheDirectory | Out-Null
  $temporaryPath = "$($script:QaCachePath).tmp"
  $script:VerifiedStages | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $temporaryPath
  Move-Item -Force -LiteralPath $temporaryPath -Destination $script:QaCachePath
}

function Import-LegacyVerifiedStage {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$LegacyName,
    [Parameter(Mandatory = $true)][string[]]$LegacyPaths,
    [Parameter(Mandatory = $true)][string[]]$CurrentContentPaths,
    [Parameter(Mandatory = $true)][string[]]$LegacyAdditionalValues,
    [Parameter(Mandatory = $true)][string]$CurrentFingerprint,
    [string[]]$RequiredPaths = @()
  )

  if ($ForceFull -or -not $PreviousRevision -or -not $script:VerifiedStages.ContainsKey($LegacyName)) {
    return $false
  }
  foreach ($path in $RequiredPaths) {
    if (-not (Test-Path -LiteralPath $path)) {
      return $false
    }
  }

  $legacyFingerprint = Get-StageFingerprint `
    -Paths $LegacyPaths `
    -AdditionalValues $LegacyAdditionalValues `
    -Revision $PreviousRevision
  if ([string]$script:VerifiedStages[$LegacyName] -ne $legacyFingerprint) {
    return $false
  }
  $currentContentFingerprint = Get-StageFingerprint -Paths $CurrentContentPaths
  $previousContentFingerprint = Get-StageFingerprint -Paths $CurrentContentPaths -Revision $PreviousRevision
  if ($currentContentFingerprint -ne $previousContentFingerprint) {
    return $false
  }

  Write-Host "[reuse] Migrating verified $LegacyName from unchanged inputs at $PreviousRevision."
  Save-VerifiedStage -Name $Name -Fingerprint $CurrentFingerprint
  return $true
}

if ($env:OS -ne "Windows_NT") {
  throw "This guest runner must execute on Windows."
}

$uvRoot = Join-Path $env:USERPROFILE "DevTools\uv"
$nodeRoot = Join-Path $env:USERPROFILE "DevTools\node-v24.19.0-win-x64"
$env:PATH = "$nodeRoot;$uvRoot;$env:USERPROFILE\.local\bin;$env:PATH"
$env:UV_PYTHON = "3.12"
$env:CI = "true"
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
$env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
$env:npm_config_audit = "false"
$env:npm_config_progress = "false"

foreach ($command in @("git", "node", "npm", "uv")) {
  if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
    throw "Required command is missing: $command. See docs/operations/windows-parallels-release-qa.md."
  }
}

$nodeMajor = [int]((& node -p "process.versions.node.split('.')[0]").Trim())
if ($nodeMajor -ne 24) {
  throw "Node.js 24 is required; found $(node --version)."
}
$nodeArchitecture = (& node -p "process.arch").Trim()
if ($nodeArchitecture -ne "x64") {
  throw "The Windows x64 release target requires x64 Node.js; found $nodeArchitecture."
}
if (-not ((& uv python find 3.12) -match "python")) {
  throw "Python 3.12 managed by uv is required."
}
if ($BundlePath -and -not (Test-Path -LiteralPath $BundlePath -PathType Leaf)) {
  throw "Git bundle is missing: $BundlePath"
}

$checkoutParent = Split-Path -Parent $CheckoutPath
New-Item -ItemType Directory -Force -Path $checkoutParent | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $CheckoutPath ".git"))) {
  if (-not $BundlePath) {
    throw "The dedicated Windows QA checkout is missing and no Git bundle was provided."
  }
  Invoke-QaStep "Clone dedicated NTFS checkout" {
    & git clone $BundlePath $CheckoutPath
    Assert-NativeSuccess "git clone"
  }
}

$trackedChanges = & git -C $CheckoutPath status --porcelain --untracked-files=no
if ($trackedChanges) {
  throw "The dedicated Windows QA checkout has tracked changes. Preserve or discard them before release QA.`n$trackedChanges"
}

if ($BundlePath) {
  Invoke-QaStep "Update checkout from committed bundle" {
    & git -C $CheckoutPath fetch --force $BundlePath HEAD
    Assert-NativeSuccess "git fetch"
    & git -C $CheckoutPath checkout --detach FETCH_HEAD
    Assert-NativeSuccess "git checkout"
  }
}

$revision = (& git -C $CheckoutPath rev-parse HEAD).Trim()
if ($revision -ne $ExpectedRevision) {
  throw "Windows checkout revision $revision does not match expected revision $ExpectedRevision."
}
Write-Host "Testing committed revision $revision"
Write-Host "Windows QA mode: $Mode"
Stop-StaleQaCheckoutProcesses -RootPath $CheckoutPath

if ($RequiresExactCandidate) {
  $candidateDesktopPackage = Get-Content -Raw -LiteralPath (Join-Path $CheckoutPath "desktop\package.json") | ConvertFrom-Json
  & node (Join-Path $CheckoutPath "scripts\release\release-candidate.mjs") `
    asset `
    --manifest $CandidateManifestPath `
    --platform windows `
    --release-sha $revision `
    --run-id ([string]$ExpectedCandidateRunId) `
    --version ([string]$candidateDesktopPackage.version) `
    --asset $CandidateInstallerPath
  Assert-NativeSuccess "candidate manifest and Windows installer binding"
}

if (-not $IsPackagedPreflight) {
$gitDirectory = (& git -C $CheckoutPath rev-parse --absolute-git-dir).Trim()
$script:QaCacheDirectory = Join-Path $gitDirectory "auto-email-sender-windows-qa"
$script:QaCachePath = Join-Path $script:QaCacheDirectory "verified-stages.json"
$script:VerifiedStages = @{}
if (-not $ForceFull -and (Test-Path -LiteralPath $script:QaCachePath -PathType Leaf)) {
  $cachedStages = Get-Content -Raw -LiteralPath $script:QaCachePath | ConvertFrom-Json
  foreach ($property in $cachedStages.PSObject.Properties) {
    $script:VerifiedStages[$property.Name] = [string]$property.Value
  }
}

$nodeVersion = (& node --version).Trim()
$npmVersion = (& npm --version).Trim()
$uvVersion = (& uv --version).Trim()
$pythonPath = (& uv python find 3.12).Trim()
$toolchainFingerprint = "node=$nodeVersion;npm=$npmVersion;uv=$uvVersion;python=$pythonPath"

$releaseContractInputs = @(
  "scripts/release",
  ".github/workflows/release.yml",
  ".codex/skills/auto-email-sender-release",
  "scripts/quality/run-windows-release-qa.ps1"
)
$releaseContractFingerprint = Get-StageFingerprint -Paths $releaseContractInputs -AdditionalValues @(
  $toolchainFingerprint,
  "command=PowerShell prepare and release entrypoint contracts"
)
if (-not (Test-VerifiedStage -Name "release-orchestration-contracts" -Fingerprint $releaseContractFingerprint)) {
  Invoke-QaStep "PowerShell release orchestration contract tests" {
    & pwsh -NoLogo -NoProfile -File (Join-Path $CheckoutPath "scripts\release\prepare-release.test.ps1")
    Assert-NativeSuccess "PowerShell prepare-release contract tests"
    & pwsh -NoLogo -NoProfile -File (Join-Path $CheckoutPath "scripts\release\release-script.test.ps1")
    Assert-NativeSuccess "PowerShell release contract tests"
    & pwsh -NoLogo -NoProfile -File (Join-Path $CheckoutPath "scripts\release\prerelease-script.test.ps1")
    Assert-NativeSuccess "PowerShell prerelease contract tests"
  }
  Save-VerifiedStage -Name "release-orchestration-contracts" -Fingerprint $releaseContractFingerprint
}

if ($IsFormal) {
  Invoke-QaStep "Windows packaging prerequisites" {
    & (Join-Path $CheckoutPath "scripts\build\prepare-windows-vc-runtime.ps1")
    Assert-NativeSuccess "Windows VC++ runtime preparation"
    & pwsh `
      -NoLogo `
      -NoProfile `
      -ExecutionPolicy Bypass `
      -File (Join-Path $CheckoutPath "scripts\quality\windows-vc-runtime-status.test.ps1")
    Assert-NativeSuccess "Windows VC++ runtime detection tests"
  }
}

$qaRunnerInput = "scripts/quality/run-windows-release-qa.ps1"
$frontendFingerprint = Get-StageFingerprint -Paths @("frontend") -AdditionalValues @(
  $toolchainFingerprint,
  "command=npm ci; native binding check; npm run build"
)
$frontendDist = Join-Path $CheckoutPath "frontend\dist\index.html"
$frontendNodeModules = Join-Path $CheckoutPath "frontend\node_modules"
$frontendVerified = Test-VerifiedStage -Name "frontend" -Fingerprint $frontendFingerprint -RequiredPaths @($frontendDist, $frontendNodeModules)
if (-not $frontendVerified) {
  $frontendVerified = Import-LegacyVerifiedStage `
    -Name "frontend" `
    -LegacyName "frontend" `
    -LegacyPaths @("frontend", $qaRunnerInput) `
    -CurrentContentPaths @("frontend") `
    -LegacyAdditionalValues @($toolchainFingerprint) `
    -CurrentFingerprint $frontendFingerprint `
    -RequiredPaths @($frontendDist, $frontendNodeModules)
}
if (-not $frontendVerified) {
  Invoke-QaStep "Frontend clean install and production build" {
    Push-Location (Join-Path $CheckoutPath "frontend")
    try {
      & npm ci --prefer-offline
      Assert-NativeSuccess "frontend npm ci"
      & node -e "require.resolve('@rolldown/binding-win32-' + process.arch + '-msvc')"
      Assert-NativeSuccess "Rolldown native binding check"
      & npm run build
      Assert-NativeSuccess "frontend build"
    } finally {
      Pop-Location
    }
  }
  Save-VerifiedStage -Name "frontend" -Fingerprint $frontendFingerprint
}

$cliBuildInputs = @(
  "cli",
  "scripts/build/build-cli.ps1",
  "scripts/build/generate_cli_build_identity.py",
  "scripts/build/verify_cli_binary.py",
  "scripts/quality/benchmark_agent_cli.py"
)
$cliTestFingerprint = Get-StageFingerprint -Paths @("cli") -AdditionalValues @(
  $toolchainFingerprint,
  "command=python -m unittest discover test"
)
$cliContractFingerprint = Get-StageFingerprint -Paths @(
  "cli/test/test_build_scripts.py",
  "scripts/build/build-cli.ps1",
  "scripts/build/generate_cli_build_identity.py",
  "scripts/build/verify_cli_binary.py",
  "scripts/quality/benchmark_agent_cli.py"
) -AdditionalValues @($toolchainFingerprint, "command=test.test_build_scripts")
$cliEnvironment = Join-Path $CheckoutPath "cli\.venv\Scripts\python.exe"
$cliSuiteVerified = Test-VerifiedStage -Name "cli-suite" -Fingerprint $cliTestFingerprint -RequiredPaths @($cliEnvironment)
if (-not $cliSuiteVerified) {
  $cliSuiteVerified = Import-LegacyVerifiedStage `
    -Name "cli-suite" `
    -LegacyName "cli-tests" `
    -LegacyPaths @($cliBuildInputs + $qaRunnerInput) `
    -CurrentContentPaths @("cli") `
    -LegacyAdditionalValues @($toolchainFingerprint) `
    -CurrentFingerprint $cliTestFingerprint `
    -RequiredPaths @($cliEnvironment)
}
$cliSuiteRan = $false
if (-not $cliSuiteVerified) {
  Invoke-QaStep "Agent CLI dependency sync and tests" {
    Push-Location (Join-Path $CheckoutPath "cli")
    try {
      & uv sync --dev
      Assert-NativeSuccess "CLI uv sync"
      & uv run --no-sync python -m unittest discover test
      Assert-NativeSuccess "CLI tests"
    } finally {
      Pop-Location
    }
  }
  Save-VerifiedStage -Name "cli-suite" -Fingerprint $cliTestFingerprint
  $cliSuiteRan = $true
}
if ($cliSuiteRan) {
  Save-VerifiedStage -Name "cli-build-contracts" -Fingerprint $cliContractFingerprint
} elseif (-not (Test-VerifiedStage -Name "cli-build-contracts" -Fingerprint $cliContractFingerprint -RequiredPaths @($cliEnvironment))) {
  Invoke-QaStep "Agent CLI build contract tests" {
    Push-Location (Join-Path $CheckoutPath "cli")
    try {
      & uv run --no-sync python -m unittest test.test_build_scripts
      Assert-NativeSuccess "CLI build contract tests"
    } finally {
      Pop-Location
    }
  }
  Save-VerifiedStage -Name "cli-build-contracts" -Fingerprint $cliContractFingerprint
}

$cliQuickBuildFingerprint = Get-StageFingerprint -Paths $cliBuildInputs -AdditionalValues @($toolchainFingerprint)
$cliBuildStageName = if ($IsFormal) { "cli-build" } else { "cli-build-quick" }
$cliBuildFingerprint = if ($IsFormal) {
  Get-StageFingerprint -Paths $cliBuildInputs -AdditionalValues @($toolchainFingerprint, "revision=$revision")
} else {
  $cliQuickBuildFingerprint
}
$cliExecutable = Join-Path $CheckoutPath "cli\dist\auto-email-sender\auto-email-sender.exe"
if (-not (Test-VerifiedStage -Name $cliBuildStageName -Fingerprint $cliBuildFingerprint -RequiredPaths @($cliExecutable))) {
  Invoke-QaStep "Agent CLI frozen build" {
    & (Join-Path $CheckoutPath "scripts\build\build-cli.ps1") -Clean -SkipSync
  }
  Save-VerifiedStage -Name $cliBuildStageName -Fingerprint $cliBuildFingerprint
  if ($IsFormal) {
    Save-VerifiedStage -Name "cli-build-quick" -Fingerprint $cliQuickBuildFingerprint
  }
}

$backendEnvironment = Join-Path $CheckoutPath "backend\.venv\Scripts\python.exe"
$backendSuiteFingerprint = Get-StageFingerprint -Paths @("backend") -AdditionalValues @(
  $toolchainFingerprint,
  "command=python -m unittest discover test"
)
$backendContractInputs = @(
  "backend/test/test_backend_build_script.py",
  "backend/test/test_crawl_mentors_skill_contract.py",
  "backend/test/test_crawl_mentors_skill_package.py",
  "scripts/build",
  "scripts/packaging",
  ".agents/skills",
  ".claude/skills"
)
$backendContractFingerprint = Get-StageFingerprint -Paths $backendContractInputs -AdditionalValues @(
  $toolchainFingerprint,
  "command=release packaging contract tests"
)
$backendSuiteRan = $false
$backendSuiteVerified = Test-VerifiedStage -Name "backend-suite" -Fingerprint $backendSuiteFingerprint -RequiredPaths @($backendEnvironment)
if (-not $backendSuiteVerified) {
  $backendSuiteVerified = Import-LegacyVerifiedStage `
    -Name "backend-suite" `
    -LegacyName "backend-tests" `
    -LegacyPaths @("backend", "scripts/build", "scripts/packaging", ".agents/skills", ".claude/skills", ".codex/skills", $qaRunnerInput) `
    -CurrentContentPaths @("backend") `
    -LegacyAdditionalValues @($toolchainFingerprint) `
    -CurrentFingerprint $backendSuiteFingerprint `
    -RequiredPaths @($backendEnvironment)
}
if (-not $backendSuiteVerified) {
  Invoke-QaStep "Backend dependency sync and tests" {
    Push-Location (Join-Path $CheckoutPath "backend")
    try {
      & uv sync --dev
      Assert-NativeSuccess "backend uv sync"
      & uv run --no-sync python -m unittest discover test
      Assert-NativeSuccess "backend tests"
    } finally {
      Pop-Location
    }
  }
  Save-VerifiedStage -Name "backend-suite" -Fingerprint $backendSuiteFingerprint
  $backendSuiteRan = $true
}

if ($backendSuiteRan) {
  # The full discovery above includes these release-specific contract modules.
  Save-VerifiedStage -Name "backend-release-contracts" -Fingerprint $backendContractFingerprint
} elseif (-not (Test-VerifiedStage -Name "backend-release-contracts" -Fingerprint $backendContractFingerprint -RequiredPaths @($backendEnvironment))) {
  Invoke-QaStep "Backend release packaging contract tests" {
    Push-Location (Join-Path $CheckoutPath "backend")
    try {
      & uv run --no-sync python -m unittest `
        test.test_backend_build_script `
        test.test_crawl_mentors_skill_contract `
        test.test_crawl_mentors_skill_package
      Assert-NativeSuccess "backend release packaging contract tests"
    } finally {
      Pop-Location
    }
  }
  Save-VerifiedStage -Name "backend-release-contracts" -Fingerprint $backendContractFingerprint
}

$backendBuildInputs = @("backend", "scripts/build/build-backend.ps1", "scripts/build/pyinstaller-hooks")
$backendBuildFingerprint = Get-StageFingerprint -Paths $backendBuildInputs -AdditionalValues @($toolchainFingerprint)
$backendExecutable = Join-Path $CheckoutPath "backend\dist\backend\backend.exe"
if (-not (Test-VerifiedStage -Name "backend-build" -Fingerprint $backendBuildFingerprint -RequiredPaths @($backendExecutable))) {
  Invoke-QaStep "Backend frozen build" {
    & (Join-Path $CheckoutPath "scripts\build\build-backend.ps1") -Clean -SkipSync
  }
  Save-VerifiedStage -Name "backend-build" -Fingerprint $backendBuildFingerprint
}

$desktopTestInputs = @("desktop", "scripts/build", "frontend/package.json", "frontend/package-lock.json")
$desktopTestFingerprint = Get-StageFingerprint -Paths $desktopTestInputs -AdditionalValues @($toolchainFingerprint)
$desktopNodeModules = Join-Path $CheckoutPath "desktop\node_modules"
if (-not (Test-VerifiedStage -Name "desktop-tests" -Fingerprint $desktopTestFingerprint -RequiredPaths @($desktopNodeModules))) {
  Invoke-QaStep "Desktop clean install, typecheck, and tests" {
    Push-Location (Join-Path $CheckoutPath "desktop")
    try {
      & npm ci --prefer-offline
      Assert-NativeSuccess "desktop npm ci"
      & npm run typecheck
      Assert-NativeSuccess "desktop typecheck"
      & npm run test
      Assert-NativeSuccess "desktop tests"
    } finally {
      Pop-Location
    }
  }
  Save-VerifiedStage -Name "desktop-tests" -Fingerprint $desktopTestFingerprint
}
}

if ($RunsPackagedLifecycle) {
  if ($IsFormal) {
    Invoke-QaStep "Windows installer build" {
    Push-Location (Join-Path $CheckoutPath "desktop")
    try {
      & npm run dist:prepared
      Assert-NativeSuccess "desktop installer build"
    } finally {
      Pop-Location
    }
  }
  }

  Invoke-QaStep "Installed packaged split lifecycle and optional soak certification" {
    $desktopPackage = Get-Content -Raw -LiteralPath (Join-Path $CheckoutPath "desktop\package.json") | ConvertFrom-Json
    if ($IsFormal) {
      $builtInstallerPath = Join-Path $CheckoutPath "desktop\release\AutoEmailSender-Setup-$($desktopPackage.version).exe"
      if (-not (Test-Path -LiteralPath $builtInstallerPath -PathType Leaf)) {
        throw "Locally built Windows installer is missing: $builtInstallerPath"
      }
    }

    $qaTimestamp = Get-Date -Format "yyyyMMddTHHmmss"
    $qaBase = Join-Path $env:TEMP "auto-email-sender-packaged-qa"
    $staleRegistrations = @(Get-QaInstallerRegistrations -QaBasePath $qaBase)
    $staleProcessIds = New-Object System.Collections.Generic.List[int]
    foreach ($registration in $staleRegistrations) {
      $stalePrefix = [System.IO.Path]::GetFullPath($registration.InstallRoot).TrimEnd("\") + "\"
      foreach ($process in @(Get-CimInstance Win32_Process)) {
        if (
          $process.ExecutablePath -and
          $process.ExecutablePath.StartsWith(
            $stalePrefix,
            [System.StringComparison]::OrdinalIgnoreCase
          )
        ) {
          $staleProcessIds.Add([int]$process.ProcessId)
        }
      }
    }
    if ($RequireRecoveredStaleState -and (
      $staleRegistrations.Count -eq 0 -or
      $staleProcessIds.Count -eq 0
    )) {
      throw "The interrupted rehearsal did not leave both registration and process state to recover."
    }
    if ($RequireRecoveredStaleState -and $staleRegistrations.Count -ne 1) {
      throw "The interrupted rehearsal must leave exactly one scoped installer registration to recover."
    }
    $recoveredQaRoot = if ($RequireRecoveredStaleState) {
      Split-Path -Parent ([string]$staleRegistrations[0].InstallRoot)
    } else {
      ""
    }
    Remove-QaInstallerRegistrations -QaBasePath $qaBase
    $remainingRegistrations = @(Get-QaInstallerRegistrations -QaBasePath $qaBase)
    if ($remainingRegistrations.Count -ne 0) {
      throw "Dedicated QA installer registrations remain after startup cleanup."
    }
    foreach ($staleProcessId in $staleProcessIds) {
      if (Get-Process -Id $staleProcessId -ErrorAction SilentlyContinue) {
        throw "Stale packaged QA process $staleProcessId survived startup recovery."
      }
    }
    Write-Host "Recovered $($staleRegistrations.Count) stale dedicated QA installer registration(s)."
    if ($IsPackagedPreflight) {
      Test-QaExecutableTimeoutRecovery
      & uv run --project (Join-Path $CheckoutPath "backend") --no-sync python -c "import psutil; print(psutil.__version__)"
      Assert-NativeSuccess "packaged QA driver dependency probe"
    }
    $harnessCheckpoint = if ($RequireRecoveredStaleState) {
      Get-ValidatedHarnessSeedCheckpoint `
        -QaBasePath $qaBase `
        -ExpectedPreviousVersion $ExpectedPreviousVersion `
        -ExpectedPreviousPackageSha256 $ExpectedPreviousPackageSha256 `
        -RequiredQaRoot $recoveredQaRoot
    } elseif ($Mode -eq "harness-rehearsal" -and $InjectInterruptionAfterPreviousInstall) {
      Get-ValidatedHarnessSeedCheckpoint `
        -QaBasePath $qaBase `
        -ExpectedPreviousVersion $ExpectedPreviousVersion `
        -ExpectedPreviousPackageSha256 $ExpectedPreviousPackageSha256
    } else {
      $null
    }
    if (
      $null -eq $harnessCheckpoint -and
      $Mode -eq "harness-rehearsal" -and
      $InjectInterruptionAfterPreviousInstall
    ) {
      $harnessCheckpoint = Restore-HarnessSeedBackup `
        -QaBasePath $qaBase `
        -ExpectedPreviousVersion $ExpectedPreviousVersion `
        -ExpectedPreviousPackageSha256 $ExpectedPreviousPackageSha256
    }
    $reusePreviousSeed = $null -ne $harnessCheckpoint
    if ($reusePreviousSeed) {
      $qaRoot = [string]$harnessCheckpoint.QaRoot
      $installRoot = [string]$harnessCheckpoint.InstallRoot
      $packageRoot = [string]$harnessCheckpoint.PackageRoot
      $upgradeUserData = [string]$harnessCheckpoint.UpgradeUserData
      $upgradeManifest = [string]$harnessCheckpoint.UpgradeManifest
      Write-Host "Reusing validated previous-stable harness seed checkpoint: $qaRoot"
    } else {
      $qaRoot = Join-Path $qaBase "$revision-$qaTimestamp"
      $installRoot = Join-Path $qaRoot "安装 路径 Ω"
      $packageRoot = Join-Path $qaRoot "candidate packages"
      $upgradeUserData = Join-Path $qaRoot "auto-email-sender-packaged-qa\previous-stable-user-data\用户 数据 Ω"
      $upgradeManifest = Join-Path $qaRoot "previous-upgrade\manifest.json"
    }
    $evidenceRoot = Join-Path $qaBase "e-$qaTimestamp"
    $pathBudgetProbe = Join-Path $evidenceRoot (
      "seeded-chaos\auto-email-sender-packaged-qa\" +
      "yyyyMMddTHHmmssZ-0123456789\fault-controls\" +
      "clock-offset-seconds.tmp-0123456789abcdef0123456789abcdef"
    )
    if ($pathBudgetProbe.Length -ge 260) {
      throw "Packaged QA lifecycle evidence path exceeds the Windows path budget: $pathBudgetProbe"
    }
    New-Item -ItemType Directory -Force -Path $qaRoot, $evidenceRoot, $packageRoot | Out-Null

    $previousInstallerPathLocal = Join-Path $packageRoot "previous-stable.exe"
    $candidateInstallerPathLocal = Join-Path $packageRoot "current-candidate.exe"
    $candidateManifestPathLocal = Join-Path $packageRoot "candidate-manifest.json"
    Copy-QaPackage -Source $PreviousInstallerPath -Destination $previousInstallerPathLocal
    Copy-QaPackage -Source $CandidateInstallerPath -Destination $candidateInstallerPathLocal
    if ($RequiresExactCandidate) {
      Copy-QaPackage -Source $CandidateManifestPath -Destination $candidateManifestPathLocal
    }
    $previousInstallerSha256 = (
      Get-FileHash -LiteralPath $previousInstallerPathLocal -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $installerSha256 = (
      Get-FileHash -LiteralPath $candidateInstallerPathLocal -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($previousInstallerSha256 -ne $ExpectedPreviousPackageSha256) {
      throw "Guest-local previous installer copy changed after shared-folder transfer."
    }
    if ($installerSha256 -ne $ExpectedCandidatePackageSha256) {
      throw "Guest-local candidate installer copy changed after shared-folder transfer."
    }

    # The previous public installer can spend 394 seconds launching its VC++
    # elevated engine. Current candidates preflight the installed runtime and
    # must not inherit that long wait after the bootstrapper is skipped.
    $previousInstallerTimeoutSeconds = 600
    $candidateInstallerTimeoutSeconds = 300
    $uninstallerTimeoutSeconds = if ($IsPackagedPreflight) { 120 } else { 600 }
    try {
    $previousAppExecutable = Join-Path $installRoot "Auto Email Sender.exe"
    if ($reusePreviousSeed) {
      Stop-QaProcessesFromRoot -RootPath $installRoot
      Write-Host "Previous-stable install and seed are already validated; skipping redundant bootstrapper execution."
    } else {
      Invoke-QaExecutable `
        -FilePath $previousInstallerPathLocal `
        -Arguments "/S /D=$installRoot" `
        -Environment @{} `
        -TimeoutSeconds $previousInstallerTimeoutSeconds `
        -RejectVisibleWindow `
        -Operation "silent previous-stable Windows installer"
      Start-Sleep -Seconds 2
      Stop-QaProcessesFromRoot -RootPath $installRoot

      if (-not (Test-Path -LiteralPath $previousAppExecutable -PathType Leaf)) {
        throw "Previous stable installed app is missing: $previousAppExecutable"
      }
      New-Item -ItemType Directory -Force -Path $upgradeUserData | Out-Null
      & uv run `
        --project (Join-Path $CheckoutPath "backend") `
        --no-sync `
        python `
        (Join-Path $CheckoutPath "scripts\quality\seed-previous-packaged-upgrade.py") `
        --app-executable $previousAppExecutable `
        --artifact-root $installRoot `
        --package-file $previousInstallerPathLocal `
        --user-data $upgradeUserData `
        --manifest $upgradeManifest
      Assert-NativeSuccess "previous-stable packaged upgrade seeding"
    }

    if ($InjectInterruptionAfterPreviousInstall) {
      $checkpointToBackup = Get-ValidatedHarnessSeedCheckpoint `
        -QaBasePath $qaBase `
        -ExpectedPreviousVersion $ExpectedPreviousVersion `
        -ExpectedPreviousPackageSha256 $ExpectedPreviousPackageSha256 `
        -RequiredQaRoot $qaRoot
      if ($null -eq $checkpointToBackup) {
        throw "Previous-stable seed failed validation before interruption backup."
      }
      Save-HarnessSeedBackup `
        -Checkpoint $checkpointToBackup `
        -ExpectedPreviousVersion $ExpectedPreviousVersion `
        -ExpectedPreviousPackageSha256 $ExpectedPreviousPackageSha256
      Add-QaHarnessInstallerRegistration `
        -QaBasePath $qaBase `
        -InstallRoot $installRoot `
        -Version $ExpectedPreviousVersion
      Write-Warning "Injecting the requested hard interruption after previous-stable install and seed."
      $staleProcessProbe = Join-Path $installRoot "qa-stale-process-probe.exe"
      Copy-Item -LiteralPath $env:COMSPEC -Destination $staleProcessProbe -Force
      $staleProcess = Start-Process `
        -FilePath $staleProcessProbe `
        -ArgumentList "/c ping.exe -t 127.0.0.1" `
        -WindowStyle Hidden `
        -PassThru
      Start-Sleep -Seconds 1
      if ($staleProcess.HasExited) {
        throw "Unable to create stale process state for the interrupted rehearsal."
      }
      Stop-Process -Id $PID -Force
      Start-Sleep -Seconds 5
      throw "Intentional interruption did not terminate the rehearsal runner."
    }

    # A silent NSIS install must not be allowed to auto-launch against real
    # userData.  If it attempts to launch, this incomplete QA gate makes the
    # packaged main process fail closed before desktop bootstrap.
    Invoke-QaExecutable `
      -FilePath $candidateInstallerPathLocal `
      -Arguments "/S /D=$installRoot" `
      -Environment @{ "AUTO_EMAIL_SENDER_PACKAGED_QA" = "installer-auto-launch-must-fail-closed" } `
      -TimeoutSeconds $candidateInstallerTimeoutSeconds `
      -RejectVisibleWindow `
      -Operation "silent Windows installer"
    Start-Sleep -Seconds 2
    Stop-QaProcessesFromRoot -RootPath $installRoot

    $appExecutable = Join-Path $installRoot "Auto Email Sender.exe"
    if (-not (Test-Path -LiteralPath $appExecutable -PathType Leaf)) {
      throw "Installed packaged app is missing: $appExecutable"
    }

    $scenarioSettings = @(
      @{ Name = "lifecycle"; Duration = $null; Seed = $null }
    )
    if ($RunNormalSoak) {
      $scenarioSettings += @{
        Name = "normal-soak"
        Duration = $NormalSoakDurationSeconds
        Seed = $null
      }
    }
    if ($RunSeededChaos) {
      $scenarioSettings += @{
        Name = "seeded-chaos"
        Duration = $SeededChaosDurationSeconds
        Seed = $SeededChaosSeed
      }
    }

    $reportPaths = New-Object System.Collections.Generic.List[string]
    $driverTierArgument = switch ($Mode) {
      "release" { "--certification" }
      "prerelease" { "--prerelease-certification" }
      "candidate-admission" { "--candidate-admission" }
      "harness-rehearsal" { "--harness-rehearsal" }
      default { throw "Unsupported packaged QA mode: $Mode" }
    }
    foreach ($scenario in $scenarioSettings) {
      $scenarioEvidence = Join-Path $evidenceRoot ([string]$scenario.Name)
      $driverArguments = @(
        "run",
        "--project", (Join-Path $CheckoutPath "backend"),
        "--no-sync",
        "python",
        (Join-Path $CheckoutPath "scripts\quality\packaged-runtime-qa.py"),
        "--scenario", ([string]$scenario.Name),
        "--app-executable", $appExecutable,
        "--artifact-root", $installRoot,
        "--package-file", $candidateInstallerPathLocal,
        "--artifacts-dir", $scenarioEvidence,
        $driverTierArgument,
        "--expected-app-version", ([string]$desktopPackage.version),
        "--expected-package-sha256", $installerSha256,
        "--expected-revision", $revision,
        "--repository-root", $CheckoutPath
      )
      if ($RequiresExactCandidate) {
        $driverArguments += @(
          "--candidate-manifest-file", $candidateManifestPathLocal,
          "--expected-candidate-run-id", ([string]$ExpectedCandidateRunId)
        )
      }
      if ($null -ne $scenario.Duration) {
        $driverArguments += @("--duration-seconds", ([string]$scenario.Duration))
      }
      if ($null -ne $scenario.Seed) {
        $driverArguments += @("--seed", ([string]$scenario.Seed))
      }
      if (
        $scenario.Name -in @("lifecycle", "seeded-chaos") -and
        ($IsFormal -or $Mode -eq "candidate-admission")
      ) {
        $driverArguments += "--system-sleep-wake"
      }
      if ($scenario.Name -eq "lifecycle") {
        $driverArguments += @(
          "--existing-user-data", $upgradeUserData,
          "--upgrade-manifest", $upgradeManifest,
          "--expected-previous-version", $ExpectedPreviousVersion,
          "--previous-package-file", $previousInstallerPathLocal,
          "--expected-previous-package-sha256", $ExpectedPreviousPackageSha256
        )
      }
      & uv @driverArguments
      Assert-NativeSuccess "packaged $($scenario.Name) $Mode"

      $reports = @(
        Get-ChildItem -LiteralPath $scenarioEvidence -Filter "report.json" -File -Recurse
      )
      if ($reports.Count -ne 1) {
        throw "Expected one packaged $($scenario.Name) report; found $($reports.Count)."
      }
      $report = Get-Content -Raw -LiteralPath $reports[0].FullName | ConvertFrom-Json
      $expectedCertificationEligible = $IsFormal
      $expectedEvidencePurpose = if ($IsFormal) {
        "formal-certification"
      } elseif ($Mode -eq "candidate-admission") {
        "non-certifying-candidate-admission"
      } else {
        "non-certifying-harness-rehearsal"
      }
      if (
        $report.status -ne "passed" -or
        $report.certification_eligible -ne $expectedCertificationEligible -or
        $report.evidence_purpose -ne $expectedEvidencePurpose
      ) {
        throw "Packaged $($scenario.Name) report has the wrong evidence tier for $Mode."
      }
      $reportPaths.Add($reports[0].FullName)
    }

    Stop-QaProcessesFromRoot -RootPath $installRoot
    $uninstallerPath = Join-Path $installRoot "Uninstall Auto Email Sender.exe"
    if (-not (Test-Path -LiteralPath $uninstallerPath -PathType Leaf)) {
      throw "Windows uninstaller is missing: $uninstallerPath"
    }
    Invoke-QaExecutable `
      -FilePath $uninstallerPath `
      -Arguments "/S" `
      -Environment @{ "AUTO_EMAIL_SENDER_PACKAGED_QA" = "uninstaller-must-not-launch-app" } `
      -TimeoutSeconds $uninstallerTimeoutSeconds `
      -RejectVisibleWindow `
      -Operation "silent Windows uninstaller"
    Start-Sleep -Seconds 2
    if (Test-Path -LiteralPath $appExecutable) {
      throw "Installed executable remains after uninstall: $appExecutable"
    }
    foreach ($reportPath in $reportPaths) {
      $report = Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
      $databasePath = Join-Path ([string]$report.user_data_path) "auto_email_sender.db"
      if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) {
        throw "Uninstall did not preserve isolated user data: $databasePath"
      }
    }
    if ($IsPackagedPreflight) {
      Invoke-QaExecutable `
        -FilePath $candidateInstallerPathLocal `
        -Arguments "/S /D=$installRoot" `
        -Environment @{ "AUTO_EMAIL_SENDER_PACKAGED_QA" = "repeat-install-must-fail-closed" } `
        -TimeoutSeconds $candidateInstallerTimeoutSeconds `
        -RejectVisibleWindow `
        -Operation "repeat candidate Windows installer"
      Start-Sleep -Seconds 2
      Stop-QaProcessesFromRoot -RootPath $installRoot
      if (-not (Test-Path -LiteralPath $appExecutable -PathType Leaf)) {
        throw "Repeat candidate install did not restore the packaged app."
      }
      $repeatUninstallerPath = Join-Path $installRoot "Uninstall Auto Email Sender.exe"
      Invoke-QaExecutable `
        -FilePath $repeatUninstallerPath `
        -Arguments "/S" `
        -Environment @{ "AUTO_EMAIL_SENDER_PACKAGED_QA" = "repeat-uninstall-must-not-launch-app" } `
        -TimeoutSeconds $uninstallerTimeoutSeconds `
        -RejectVisibleWindow `
        -Operation "repeat Windows uninstaller"
      Start-Sleep -Seconds 2
      if (Test-Path -LiteralPath $appExecutable) {
        throw "Installed executable remains after repeat uninstall: $appExecutable"
      }
    }
    Write-Host "Windows packaged QA artifacts: $qaRoot"
    } finally {
      Stop-QaProcessesFromRoot -RootPath $installRoot
      Remove-QaInstallerRegistrations -QaBasePath $qaBase -InstallRoot $installRoot
    }
  }
  if ($IsFormal) {
    Write-Host "`nWindows release QA passed for $revision"
  } else {
    Write-Host "`nWindows $Mode passed for $revision"
    Write-Host "This report is explicitly non-certifying and cannot replace formal QA."
  }
} else {
  Write-Host "`nWindows quick QA passed for $revision"
  Write-Host "Quick QA skips VC++ installer preparation, NSIS, and packaged lifecycle checks; it is not valid release preflight evidence."
}
