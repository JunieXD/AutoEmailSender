param(
  [Parameter(Mandatory = $true)]
  [string]$BundlePath,
  [string]$CheckoutPath = "$env:USERPROFILE\Projects\AutoEmailSender-Windows-QA",
  [switch]$ForceFull,
  [switch]$SkipRuntimeLifecycle
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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
    [string[]]$AdditionalValues = @()
  )

  $parts = New-Object System.Collections.Generic.List[string]
  foreach ($path in $Paths) {
    $gitPath = $path.Replace("\", "/")
    $treeId = (& git -C $CheckoutPath rev-parse "HEAD:$gitPath" 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $treeId) {
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
if (-not (Test-Path -LiteralPath $BundlePath -PathType Leaf)) {
  throw "Git bundle is missing: $BundlePath"
}

$checkoutParent = Split-Path -Parent $CheckoutPath
New-Item -ItemType Directory -Force -Path $checkoutParent | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $CheckoutPath ".git"))) {
  Invoke-QaStep "Clone dedicated NTFS checkout" {
    & git clone $BundlePath $CheckoutPath
    Assert-NativeSuccess "git clone"
  }
}

$trackedChanges = & git -C $CheckoutPath status --porcelain --untracked-files=no
if ($trackedChanges) {
  throw "The dedicated Windows QA checkout has tracked changes. Preserve or discard them before release QA.`n$trackedChanges"
}

Invoke-QaStep "Update checkout from committed bundle" {
  & git -C $CheckoutPath fetch --force $BundlePath HEAD
  Assert-NativeSuccess "git fetch"
  & git -C $CheckoutPath checkout --detach FETCH_HEAD
  Assert-NativeSuccess "git checkout"
}

$revision = (& git -C $CheckoutPath rev-parse HEAD).Trim()
Write-Host "Testing committed revision $revision"

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

Invoke-QaStep "Windows packaging prerequisites" {
  & (Join-Path $CheckoutPath "scripts\build\prepare-windows-vc-runtime.ps1")
}

$qaRunnerInput = "scripts/quality/run-windows-release-qa.ps1"
$frontendFingerprint = Get-StageFingerprint -Paths @("frontend", $qaRunnerInput) -AdditionalValues @($toolchainFingerprint)
$frontendDist = Join-Path $CheckoutPath "frontend\dist\index.html"
$frontendNodeModules = Join-Path $CheckoutPath "frontend\node_modules"
if (-not (Test-VerifiedStage -Name "frontend" -Fingerprint $frontendFingerprint -RequiredPaths @($frontendDist, $frontendNodeModules))) {
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

$cliInputs = @("cli", "scripts/build/build-cli.ps1", "scripts/build/generate_cli_build_identity.py", "scripts/build/verify_cli_binary.py", $qaRunnerInput)
$cliTestFingerprint = Get-StageFingerprint -Paths $cliInputs -AdditionalValues @($toolchainFingerprint)
$cliEnvironment = Join-Path $CheckoutPath "cli\.venv\Scripts\python.exe"
if (-not (Test-VerifiedStage -Name "cli-tests" -Fingerprint $cliTestFingerprint -RequiredPaths @($cliEnvironment))) {
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
  Save-VerifiedStage -Name "cli-tests" -Fingerprint $cliTestFingerprint
}

$cliBuildFingerprint = Get-StageFingerprint -Paths $cliInputs -AdditionalValues @($toolchainFingerprint, "revision=$revision")
$cliExecutable = Join-Path $CheckoutPath "cli\dist\auto-email-sender\auto-email-sender.exe"
if (-not (Test-VerifiedStage -Name "cli-build" -Fingerprint $cliBuildFingerprint -RequiredPaths @($cliExecutable))) {
  Invoke-QaStep "Agent CLI frozen build" {
    & (Join-Path $CheckoutPath "scripts\build\build-cli.ps1") -Clean -SkipSync
  }
  Save-VerifiedStage -Name "cli-build" -Fingerprint $cliBuildFingerprint
}

$backendTestInputs = @("backend", "scripts/build", "scripts/packaging", ".agents/skills", ".claude/skills", ".codex/skills", $qaRunnerInput)
$backendTestFingerprint = Get-StageFingerprint -Paths $backendTestInputs -AdditionalValues @($toolchainFingerprint)
$backendEnvironment = Join-Path $CheckoutPath "backend\.venv\Scripts\python.exe"
if (-not (Test-VerifiedStage -Name "backend-tests" -Fingerprint $backendTestFingerprint -RequiredPaths @($backendEnvironment))) {
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
  Save-VerifiedStage -Name "backend-tests" -Fingerprint $backendTestFingerprint
}

$backendBuildInputs = @("backend", "scripts/build/build-backend.ps1", "scripts/build/pyinstaller-hooks", $qaRunnerInput)
$backendBuildFingerprint = Get-StageFingerprint -Paths $backendBuildInputs -AdditionalValues @($toolchainFingerprint)
$backendExecutable = Join-Path $CheckoutPath "backend\dist\backend\backend.exe"
if (-not (Test-VerifiedStage -Name "backend-build" -Fingerprint $backendBuildFingerprint -RequiredPaths @($backendExecutable))) {
  Invoke-QaStep "Backend frozen build" {
    & (Join-Path $CheckoutPath "scripts\build\build-backend.ps1") -Clean -SkipSync
  }
  Save-VerifiedStage -Name "backend-build" -Fingerprint $backendBuildFingerprint
}

$desktopTestInputs = @("desktop", "scripts/build", "frontend/package.json", "frontend/package-lock.json", $qaRunnerInput)
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

Invoke-QaStep "Windows installer build" {
  Push-Location (Join-Path $CheckoutPath "desktop")
  try {
    & npm run dist
    Assert-NativeSuccess "desktop installer build"
  } finally {
    Pop-Location
  }
}

if (-not $SkipRuntimeLifecycle) {
  Invoke-QaStep "Packaged runtime identity and stale-process lifecycle" {
    $appExecutable = Join-Path $CheckoutPath "desktop\release\win-unpacked\Auto Email Sender.exe"
    $cliExecutable = Join-Path $CheckoutPath "cli\dist\auto-email-sender\auto-email-sender.exe"
    if (-not (Test-Path -LiteralPath $appExecutable -PathType Leaf)) {
      throw "Packaged app is missing: $appExecutable"
    }
    if (-not (Test-Path -LiteralPath $cliExecutable -PathType Leaf)) {
      throw "Frozen CLI is missing: $cliExecutable"
    }

    $firstDesktop = Start-Process -FilePath $appExecutable -PassThru
    try {
      $firstStatus = Wait-AgentReady -CliExecutable $cliExecutable
      $firstDescriptor = Get-AgentRuntimeDescriptor
      $firstRuntimeId = [string]$firstDescriptor.runtime_id
      if ($firstDescriptor.protocol_version -ne "3") {
        throw "Packaged runtime did not publish protocol v3."
      }
      if (-not (Get-Process -Id ([int]$firstDescriptor.desktop.pid) -ErrorAction SilentlyContinue)) {
        throw "Runtime descriptor desktop PID is not running."
      }
      if (-not (Get-Process -Id ([int]$firstDescriptor.backend.pid) -ErrorAction SilentlyContinue)) {
        throw "Runtime descriptor backend PID is not running."
      }
      for ($attempt = 0; $attempt -lt 20; $attempt += 1) {
        $status = Get-AgentStatus -CliExecutable $cliExecutable
        if ($status.backend_ready -ne $true -or $status.state -ne "ready") {
          throw "Repeated CLI status changed or invalidated the active runtime."
        }
      }
      Stop-QaDesktopTree -DesktopPid ([int]$firstDescriptor.desktop.pid)
      $firstDesktop = $null

      Start-Sleep -Seconds 1
      $stoppedStatus = Get-AgentStatus -CliExecutable $cliExecutable
      if ($stoppedStatus.state -ne "stopped" -or $stoppedStatus.backend_ready -ne $false) {
        throw "CLI did not fail closed after the desktop process exited."
      }

      $secondDesktop = Start-Process -FilePath $appExecutable -PassThru
      try {
        $secondStatus = Wait-AgentReady -CliExecutable $cliExecutable
        $secondDescriptor = Get-AgentRuntimeDescriptor
        if ([string]$secondDescriptor.runtime_id -eq $firstRuntimeId) {
          throw "Restarted desktop reused the previous runtime identity."
        }
      } finally {
        if ($secondDesktop -and -not $secondDesktop.HasExited) {
          Stop-QaDesktopTree -DesktopPid $secondDesktop.Id
        }
      }
    } finally {
      if ($firstDesktop -and -not $firstDesktop.HasExited) {
        Stop-QaDesktopTree -DesktopPid $firstDesktop.Id
      }
    }
  }
}

Write-Host "`nWindows release QA passed for $revision"
