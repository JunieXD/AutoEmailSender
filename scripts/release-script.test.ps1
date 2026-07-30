$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$repoRoot = Split-Path -Parent $PSScriptRoot
$releaseScript = Join-Path $repoRoot "scripts\release.ps1"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString("N"))
$tempBin = Join-Path $tempRoot "bin"
$stdoutPath = Join-Path $tempRoot "stdout.txt"
$stderrPath = Join-Path $tempRoot "stderr.txt"
$uvCallsPath = Join-Path $tempRoot "uv-calls.txt"
$releaseNotesDirectory = Join-Path $repoRoot "docs\releases"
$releaseNotesPath = Join-Path $releaseNotesDirectory "v9.9.9.md"

function New-CmdShim {
  param(
    [Parameter(Mandatory = $true)][string]$Directory,
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Content
  )

  Set-Content -Encoding UTF8 -Path (Join-Path $Directory "$Name.cmd") -Value $Content
}

function Assert-Contains {
  param(
    [Parameter(Mandatory = $true)][string]$Text,
    [Parameter(Mandatory = $true)][string]$Needle,
    [Parameter(Mandatory = $true)][string]$Message
  )

  if ($Text -notmatch [regex]::Escape($Needle)) {
    throw $Message
  }
}

New-Item -ItemType Directory -Path $tempBin -Force | Out-Null

try {
  New-Item -ItemType Directory -Path $releaseNotesDirectory -Force | Out-Null
  Set-Content -Encoding UTF8 -Path $releaseNotesPath -Value @"
# v9.9.9

## 更新内容

- 测试公告。
"@

  New-CmdShim -Directory $tempBin -Name "git" -Content @"
@echo off
if "%3"=="branch" echo master & exit /b 0
if "%3"=="status" exit /b 0
exit /b 0
"@
  New-CmdShim -Directory $tempBin -Name "npm" -Content @"
@echo off
echo fake npm %*
if "%1"=="test" exit /b 1
exit /b 0
"@
  New-CmdShim -Directory $tempBin -Name "uv" -Content @"
@echo off
echo fake uv %*
echo %* >> "$uvCallsPath"
exit /b 0
"@

  $releaseRepo = Join-Path $tempRoot "release-repo"
  New-Item -ItemType Directory -Path (Join-Path $releaseRepo "docs\releases") -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $releaseRepo "desktop") -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $releaseRepo "frontend") -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $releaseRepo "backend") -Force | Out-Null
  Set-Content -Encoding UTF8 -Path (Join-Path $releaseRepo "docs\releases\v9.9.9.md") -Value @"
# v9.9.9

## 更新内容

- 测试公告。
"@

  New-CmdShim -Directory $tempBin -Name "git" -Content @"
@echo off
if "%3"=="branch" echo master & exit /b 0
if "%3"=="status" (
  echo %* | findstr /C:"--untracked-files=all" >nul || exit /b 2
  echo ?? docs/releases/v9.9.9.md
  exit /b 0
)
if "%3"=="add" exit /b 0
if "%3"=="commit" exit /b 0
if "%3"=="tag" exit /b 0
if "%3"=="push" exit /b 0
exit /b 0
"@

  $oldPath = $env:PATH
  $env:PATH = "$tempBin;$oldPath"
  try {
    $pwshPath = (Get-Command pwsh).Source
    $failureProcess = Start-Process -FilePath $pwshPath -ArgumentList @(
      "-NoLogo",
      "-NoProfile",
      "-File",
      $releaseScript,
      "9.9.9",
      "-DryRun",
      "-RepoRoot",
      $releaseRepo
    ) -WorkingDirectory $repoRoot -PassThru -Wait -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

    $failureOutput = "$(Get-Content -Raw -Encoding UTF8 $stdoutPath)`n$(Get-Content -Raw -Encoding UTF8 $stderrPath)"
    if ($failureProcess.ExitCode -eq 0) {
      throw "release.ps1 应该在 frontend 的 npm test 失败时返回非零退出码。"
    }

    Assert-Contains -Text $failureOutput -Needle "[fail] frontend: npm test" -Message "输出里没有看到 frontend: npm test 的失败信息。`n$failureOutput"
    if ($failureOutput -match "验证 backend" -or $failureOutput -match "fake npm run lint" -or $failureOutput -match "fake npm run build") {
      throw "release.ps1 没有在第一个失败处停下。`n$failureOutput"
    }

    New-CmdShim -Directory $tempBin -Name "npm" -Content @"
@echo off
echo fake npm %*
exit /b 0
"@
    if (Test-Path $uvCallsPath) {
      Remove-Item -LiteralPath $uvCallsPath -Force
    }

    $verificationProcess = Start-Process -FilePath $pwshPath -ArgumentList @(
      "-NoLogo",
      "-NoProfile",
      "-File",
      $releaseScript,
      "9.9.9",
      "-DryRun",
      "-RepoRoot",
      $releaseRepo
    ) -WorkingDirectory $repoRoot -PassThru -Wait -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

    $verificationOutput = "$(Get-Content -Raw -Encoding UTF8 $stdoutPath)`n$(Get-Content -Raw -Encoding UTF8 $stderrPath)"
    if ($verificationProcess.ExitCode -ne 0) {
      throw "release.ps1 在验证命令都成功时应该完成 dry-run。`n$verificationOutput"
    }
    $uvCalls = Get-Content -Raw -Encoding UTF8 $uvCallsPath
    Assert-Contains -Text $uvCalls -Needle "run python -m unittest test.test_database_schema test.test_migrations_runtime" -Message "release.ps1 没有执行迁移相关后端测试。`n$uvCalls"
    Assert-Contains -Text $uvCalls -Needle "run python -m unittest test.test_crawl_mentors_skill_contract" -Message "release.ps1 没有执行导师抓取 Skill 契约测试。`n$uvCalls"

    if (Test-Path $uvCallsPath) {
      Remove-Item -LiteralPath $uvCallsPath -Force
    }

    $process = Start-Process -FilePath $pwshPath -ArgumentList @(
      "-NoLogo",
      "-NoProfile",
      "-File",
      $releaseScript,
      "9.9.9",
      "-SkipVerify",
      "-RepoRoot",
      $releaseRepo
    ) -WorkingDirectory $repoRoot -PassThru -Wait -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

    $stdout = Get-Content -Raw -Encoding UTF8 $stdoutPath
    $stderr = Get-Content -Raw -Encoding UTF8 $stderrPath
    $output = "$stdout`n$stderr"

    if ($process.ExitCode -eq 0) {
      if (-not (Test-Path (Join-Path $releaseRepo "desktop\release-notes.md"))) {
        throw "release.ps1 应该把公告复制到 desktop\\release-notes.md。`n$output"
      }
      if ($output -notmatch "已推送 v9.9.9") {
        throw "release.ps1 成功时没有输出 tag 已推送信息。`n$output"
      }
    } else {
      throw "release.ps1 在允许的未跟踪公告文件存在时应该成功。`n$output"
    }

    $missingNotesProcess = Start-Process -FilePath $pwshPath -ArgumentList @(
      "-NoLogo",
      "-NoProfile",
      "-File",
      $releaseScript,
      "8.8.8",
      "-DryRun",
      "-RepoRoot",
      $releaseRepo
    ) -WorkingDirectory $repoRoot -PassThru -Wait -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

    $missingOutput = "$(Get-Content -Raw -Encoding UTF8 $stdoutPath)`n$(Get-Content -Raw -Encoding UTF8 $stderrPath)"
    if ($missingNotesProcess.ExitCode -eq 0) {
      throw "release.ps1 缺少公告文件时应该返回非零退出码。"
    }
    Assert-Contains -Text $missingOutput -Needle "缺少 docs/releases/v8.8.8.md" -Message "缺少公告时没有给出明确提示。`n$missingOutput"
    Assert-Contains -Text $missingOutput -Needle ".\scripts\prepare-release.ps1 8.8.8" -Message "缺少公告时没有提示准备脚本命令。`n$missingOutput"
  } finally {
    $env:PATH = $oldPath
  }
} finally {
  Remove-Item -LiteralPath $releaseNotesPath -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
