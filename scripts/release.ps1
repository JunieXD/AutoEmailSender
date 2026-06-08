param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidatePattern('^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$')]
  [string]$Version,

  [switch]$DryRun,
  [switch]$SkipVerify,
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$RepoRoot = (Resolve-Path $RepoRoot).Path
$ReleaseTag = "v$Version"
$CuratedReleaseNotesPath = Join-Path $RepoRoot "docs\releases\$ReleaseTag.md"
$DesktopReleaseNotesPath = Join-Path $RepoRoot "desktop\release-notes.md"

function Run-Git {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
  if ($DryRun) {
    Write-Host "[dry-run] git $($Args -join ' ')"
    return
  }
  git -C $RepoRoot @Args
}

function Invoke-CheckedCommand {
  param(
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][scriptblock]$Action
  )

  try {
    & $Action
  } catch {
    throw "[fail] $Label 失败：$($_.Exception.Message)"
  }
}

function Assert-CleanRepository {
  $branch = (git -C $RepoRoot branch --show-current).Trim()
  if ($DryRun) {
    Write-Host "[dry-run] current branch is $branch; real release requires master"
    return
  }

  if ($branch -ne "master") {
    throw "发布必须在 master 分支执行，当前分支是 $branch。"
  }

  $status = git -C $RepoRoot status --porcelain --untracked-files=all
  $allowedReleaseNotesPath = "docs/releases/$ReleaseTag.md"
  $unexpectedStatus = @(
    $status | Where-Object {
      $path = $_.Substring(3).Replace("\", "/")
      $path -ne $allowedReleaseNotesPath
    }
  )
  if ($unexpectedStatus.Count -gt 0) {
    throw "工作区存在未提交改动，请先提交或清理后再发布。"
  }
}

function Assert-ReleaseNotes {
  $relativePath = "docs/releases/$ReleaseTag.md"
  if (-not (Test-Path $CuratedReleaseNotesPath)) {
    throw "缺少 $relativePath，请先运行 .\scripts\prepare-release.ps1 $Version 并润色公告后再发布。"
  }
}

function Copy-ReleaseNotes {
  if ($DryRun) {
    Write-Host "[dry-run] copy docs/releases/$ReleaseTag.md to desktop/release-notes.md"
    return
  }
  Copy-Item -LiteralPath $CuratedReleaseNotesPath -Destination $DesktopReleaseNotesPath -Force
}

function Invoke-Verification {
  if ($SkipVerify) {
    Write-Host "[skip] 跳过发布前验证"
    return
  }

  Write-Host "=== 验证 frontend ==="
  Push-Location (Join-Path $RepoRoot "frontend")
  try {
    Invoke-CheckedCommand "frontend: npm test" { npm test }
    Invoke-CheckedCommand "frontend: npm run lint" { npm run lint }
    Invoke-CheckedCommand "frontend: npm run build" { npm run build }
  } finally {
    Pop-Location
  }

  Write-Host "=== 验证 backend ==="
  Push-Location (Join-Path $RepoRoot "backend")
  try {
    Invoke-CheckedCommand "backend: uv sync --dev" { uv sync --dev }
    Invoke-CheckedCommand "backend: uv run python -m unittest test.test_desktop_runtime" {
      uv run python -m unittest test.test_desktop_runtime
    }
    Invoke-CheckedCommand "backend: uv run python -m unittest test.test_database_schema test.test_migrations_runtime" {
      uv run python -m unittest test.test_database_schema test.test_migrations_runtime
    }
  } finally {
    Pop-Location
  }

  Write-Host "=== 验证 desktop ==="
  Push-Location (Join-Path $RepoRoot "desktop")
  try {
    Invoke-CheckedCommand "desktop: npm test" { npm test }
  } finally {
    Pop-Location
  }
}

function Set-NpmVersion {
  param([string]$Directory)
  Push-Location (Join-Path $RepoRoot $Directory)
  try {
    if ($DryRun) {
      Write-Host "[dry-run] npm version $Version --no-git-tag-version in $Directory"
      return
    }
    npm version $Version --no-git-tag-version
  } finally {
    Pop-Location
  }
}

Assert-CleanRepository
Assert-ReleaseNotes
Invoke-Verification
Set-NpmVersion "desktop"
Set-NpmVersion "frontend"
Copy-ReleaseNotes

Run-Git add desktop/package.json desktop/package-lock.json frontend/package.json frontend/package-lock.json desktop/release-notes.md "docs/releases/$ReleaseTag.md"
Run-Git commit -m "chore(release): $ReleaseTag"
Run-Git tag $ReleaseTag
Run-Git push origin master
Run-Git push origin $ReleaseTag

if ($DryRun) {
  Write-Host "[dry-run] 未创建提交、tag 或推送。真实发布会触发 GitHub Actions 创建 Release。"
} else {
  Write-Host "已发布 $ReleaseTag。GitHub Actions 将自动创建 Release。"
}
