$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$prepareScript = Join-Path $repositoryRoot "scripts\release\prepare-prerelease.ps1"
$prereleaseScript = Join-Path $repositoryRoot "scripts\release\prerelease.ps1"
$notesScript = Join-Path $repositoryRoot "scripts\release\prerelease-notes.mjs"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString("N"))
$fixture = Join-Path $tempRoot "fixture"
$stdoutPath = Join-Path $tempRoot "stdout.txt"
$stderrPath = Join-Path $tempRoot "stderr.txt"
$version = "9.9.9-beta.1"
$cliVersion = "9.9.9b1"
$branch = "release/powershell-generic"

function Invoke-PwshScript {
  param([string]$Script, [string[]]$Arguments)
  $pwsh = (Get-Command pwsh).Source
  Start-Process -FilePath $pwsh -ArgumentList (@(
    "-NoLogo",
    "-NoProfile",
    "-File",
    $Script
  ) + $Arguments) -WorkingDirectory $fixture -PassThru -Wait -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
}

function Read-Output {
  "$(Get-Content -Raw -Encoding UTF8 $stdoutPath)`n$(Get-Content -Raw -Encoding UTF8 $stderrPath)"
}

function Assert-Contains {
  param([string]$Text, [string]$Needle)
  if ($Text -notmatch [regex]::Escape($Needle)) {
    throw "缺少输出：$Needle`n$Text"
  }
}

try {
  New-Item -ItemType Directory -Path (Join-Path $fixture "cli") -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $fixture "desktop") -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $fixture "frontend") -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $fixture "docs\releases") -Force | Out-Null
  git -C $fixture init -b $branch | Out-Null
  git -C $fixture config user.email test@example.test
  git -C $fixture config user.name "Test User"
  Set-Content -Encoding UTF8 -Path (Join-Path $fixture "cli\pyproject.toml") -Value "[project]`nversion = `"2.5.4`""
  Set-Content -Encoding UTF8 -Path (Join-Path $fixture "desktop\package.json") -Value '{"version":"2.5.4"}'
  Set-Content -Encoding UTF8 -Path (Join-Path $fixture "desktop\package-lock.json") -Value '{"version":"2.5.4","packages":{"":{"version":"2.5.4"}}}'
  Set-Content -Encoding UTF8 -Path (Join-Path $fixture "frontend\package.json") -Value '{"version":"2.5.4"}'
  Set-Content -Encoding UTF8 -Path (Join-Path $fixture "frontend\package-lock.json") -Value '{"version":"2.5.4","packages":{"":{"version":"2.5.4"}}}'
  Set-Content -Encoding UTF8 -Path (Join-Path $fixture "desktop\release-notes.md") -Value "# stable"
  git -C $fixture add .
  git -C $fixture commit -m fixture | Out-Null
  git -C $fixture tag v2.5.4

  $prepare = Invoke-PwshScript -Script $prepareScript -Arguments @(
    $version,
    "-Channel", "beta",
    "-SourceBranch", $branch,
    "-DryRun",
    "-RepoRoot", $fixture
  )
  $prepareOutput = Read-Output
  if ($prepare.ExitCode -ne 0) { throw "PowerShell Prepare Prerelease dry-run 失败。`n$prepareOutput" }
  Assert-Contains -Text $prepareOutput -Needle "未 push、tag、dispatch"
  if (Test-Path (Join-Path $fixture "docs\releases\v$version.md")) {
    throw "PowerShell Prepare Prerelease dry-run 不应写文件。"
  }

  Set-Content -Encoding UTF8 -Path (Join-Path $fixture "cli\pyproject.toml") -Value "[project]`nversion = `"$cliVersion`""
  Set-Content -Encoding UTF8 -Path (Join-Path $fixture "desktop\package.json") -Value "{`"version`":`"$version`"}"
  Set-Content -Encoding UTF8 -Path (Join-Path $fixture "desktop\package-lock.json") -Value "{`"version`":`"$version`",`"packages`":{`"`":{`"version`":`"$version`"}}}"
  Set-Content -Encoding UTF8 -Path (Join-Path $fixture "frontend\package.json") -Value "{`"version`":`"$version`"}"
  Set-Content -Encoding UTF8 -Path (Join-Path $fixture "frontend\package-lock.json") -Value "{`"version`":`"$version`",`"packages`":{`"`":{`"version`":`"$version`"}}}"
  node $notesScript --version $version --channel beta --output (Join-Path $fixture "docs\releases\v$version.md") | Out-Null
  $notesPath = Join-Path $fixture "docs\releases\v$version.md"
  $notes = (Get-Content -Raw -Encoding UTF8 $notesPath) `
    -replace '待根据本次候选的用户可见变化补充。', '已补充用户可见变化。' `
    -replace '待列出本次需要重点覆盖的正常流程、模式切换和故障场景。', '重点覆盖模式切换和故障恢复。'
  Set-Content -Encoding UTF8 -Path $notesPath -Value $notes -NoNewline
  Copy-Item -LiteralPath $notesPath -Destination (Join-Path $fixture "desktop\release-notes.md") -Force
  Set-Content -Encoding UTF8 -Path (Join-Path $fixture "desktop\electron-builder.yml") -Value @"
mac:
  extendInfo:
    SUFeedURL: https://github.com/example/repo/releases/latest/download/appcast.xml
publish:
  releaseType: release
"@
  git -C $fixture add .
  git -C $fixture commit -m candidate | Out-Null
  $releaseSha = (git -C $fixture rev-parse HEAD).Trim()

  $certify = Invoke-PwshScript -Script $prereleaseScript -Arguments @(
    "certify", $version,
    "-Channel", "beta",
    "-SourceBranch", $branch,
    "-ReleaseSha", $releaseSha,
    "-DryRun",
    "-RepoRoot", $fixture
  )
  $certifyOutput = Read-Output
  if ($certify.ExitCode -ne 0) { throw "PowerShell prerelease certify dry-run 失败。`n$certifyOutput" }
  Assert-Contains -Text $certifyOutput -Needle "release_kind=prerelease"
  Assert-Contains -Text $certifyOutput -Needle "source_branch=$branch"
  Assert-Contains -Text $certifyOutput -Needle "publish=false"

  $publish = Invoke-PwshScript -Script $prereleaseScript -Arguments @(
    "publish", $version,
    "-Channel", "beta",
    "-SourceBranch", $branch,
    "-ReleaseSha", $releaseSha,
    "-CandidateRun", "123456",
    "-DryRun",
    "-RepoRoot", $fixture
  )
  $publishOutput = Read-Output
  if ($publish.ExitCode -ne 0) { throw "PowerShell prerelease publish dry-run 失败。`n$publishOutput" }
  Assert-Contains -Text $publishOutput -Needle "publish=true"
  Assert-Contains -Text $publishOutput -Needle "candidate_run_id=123456"
} finally {
  Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
