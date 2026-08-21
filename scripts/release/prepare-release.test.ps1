$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$scriptPath = Join-Path $repoRoot "scripts\release\prepare-release.ps1"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString("N"))
$stdoutPath = Join-Path $tempRoot "stdout.txt"
$stderrPath = Join-Path $tempRoot "stderr.txt"

function Invoke-PrepareRelease {
  param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$RepoRoot
  )

  $pwshPath = (Get-Command pwsh).Source
  Start-Process -FilePath $pwshPath -ArgumentList @(
    "-NoLogo",
    "-NoProfile",
    "-File",
    $scriptPath,
    $Version,
    "-RepoRoot",
    $RepoRoot
  ) -WorkingDirectory $RepoRoot -PassThru -Wait -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
}

try {
  New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
  git -C $tempRoot init | Out-Null
  git -C $tempRoot config user.email "test@example.com" | Out-Null
  git -C $tempRoot config user.name "Test User" | Out-Null
  Set-Content -Encoding UTF8 -Path (Join-Path $tempRoot "file.txt") -Value "base"
  git -C $tempRoot add file.txt | Out-Null
  git -C $tempRoot commit -m "chore(release): v1.0.0" | Out-Null
  git -C $tempRoot tag v1.0.0 | Out-Null
  Set-Content -Encoding UTF8 -Path (Join-Path $tempRoot "file.txt") -Value "next"
  git -C $tempRoot commit -am "fix(更新): 修复公告弹窗高度" | Out-Null

  $process = Invoke-PrepareRelease -Version "1.0.1" -RepoRoot $tempRoot
  $output = "$(Get-Content -Raw -Encoding UTF8 $stdoutPath)`n$(Get-Content -Raw -Encoding UTF8 $stderrPath)"
  $notesPath = Join-Path $tempRoot "docs\releases\v1.0.1.md"

  if ($process.ExitCode -ne 0) {
    throw "prepare-release.ps1 应该成功生成公告草稿。`n$output"
  }
  if (-not (Test-Path $notesPath)) {
    throw "没有生成 docs/releases/v1.0.1.md。"
  }

  $notes = Get-Content -Raw -Encoding UTF8 $notesPath
  if ($notes -notmatch "# v1.0.1" -or $notes -notmatch "### 新增功能" -or $notes -notmatch "### 体验优化" -or $notes -notmatch "### 问题修复") {
    throw "公告模板内容不符合预期。`n$notes"
  }
  if ($notes -notmatch "Windows：下载 ``AutoEmailSender-Setup-1.0.1.exe``") {
    throw "公告模板缺少 Windows 安装说明。`n$notes"
  }
  if ($notes -notmatch "macOS Apple Silicon：下载 ``AutoEmailSender-1.0.1-arm64.dmg``") {
    throw "公告模板缺少 macOS Apple Silicon 安装说明。`n$notes"
  }
  if ($notes -match "## 自动更新" -or $notes -match "## 导师抓取 Skill" -or $notes -match "Intel Mac 暂未提供安装包") {
    throw "公告模板不应包含自动更新、导师抓取 Skill 或 Intel Mac 提示。`n$notes"
  }
  if ($notes -match "crawl-mentors-to-xlsx-v1.0.1.zip") {
    throw "公告模板不应该包含 Skill ZIP 下载链接。`n$notes"
  }
  if ($notes -match "fix\(更新\): 修复公告弹窗高度") {
    throw "公告模板不应该直接包含 commit subject。`n$notes"
  }
  if ($output -notmatch "请编辑 docs/releases/v1.0.1.md") {
    throw "输出里缺少润色提示。`n$output"
  }

  $second = Invoke-PrepareRelease -Version "1.0.1" -RepoRoot $tempRoot
  $secondOutput = "$(Get-Content -Raw -Encoding UTF8 $stdoutPath)`n$(Get-Content -Raw -Encoding UTF8 $stderrPath)"
  if ($second.ExitCode -eq 0) {
    throw "公告文件已存在时，prepare-release.ps1 应该失败。"
  }
  if ($secondOutput -notmatch "已经存在") {
    throw "重复生成时没有提示文件已存在。`n$secondOutput"
  }
} finally {
  Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
