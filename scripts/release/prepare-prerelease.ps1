param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string]$Version,

  [Parameter(Mandatory = $true)]
  [ValidateSet("alpha", "beta", "rc")]
  [string]$Channel,

  [Parameter(Mandatory = $true)]
  [string]$SourceBranch,

  [switch]$DryRun,
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$RepoRoot = (Resolve-Path $RepoRoot).Path
$currentBranch = (git -C $RepoRoot branch --show-current).Trim()
if ($currentBranch -ne $SourceBranch) {
  $shown = if ($currentBranch) { $currentBranch } else { "<detached>" }
  throw "当前分支 $shown 与显式 source branch $SourceBranch 不一致。"
}
if (@(git -C $RepoRoot status --porcelain --untracked-files=all).Count -gt 0) {
  throw "Prepare Prerelease 前工作区必须干净；不会覆盖现有改动。"
}

$headSha = (git -C $RepoRoot rev-parse HEAD).Trim()
node (Join-Path $PSScriptRoot "prerelease-contract.mjs") validate `
  --version $Version `
  --channel $Channel `
  --source-branch $SourceBranch `
  --release-sha $headSha | Out-Null
node (Join-Path $PSScriptRoot "prerelease-contract.mjs") check-tags `
  --version $Version `
  --channel $Channel `
  --repo-root $RepoRoot | Out-Null

$releaseTag = "v$Version"
$releaseNotesPath = Join-Path $RepoRoot "docs\releases\$releaseTag.md"
if (Test-Path $releaseNotesPath) {
  throw "docs/releases/$releaseTag.md 已经存在；不会覆盖测试版公告。"
}

if ($DryRun) {
  Write-Host "[dry-run] node scripts/release/prerelease-notes.mjs --version $Version --channel $Channel --output docs/releases/$releaseTag.md"
  Write-Host "[dry-run] uv version $Version --no-sync in cli"
  Write-Host "[dry-run] npm version $Version --no-git-tag-version --allow-same-version in desktop"
  Write-Host "[dry-run] npm version $Version --no-git-tag-version --allow-same-version in frontend"
  Write-Host "[dry-run] copy docs/releases/$releaseTag.md to desktop/release-notes.md"
  Write-Host "[dry-run] 未 push、tag、dispatch，也未创建 GitHub Release。"
  exit 0
}

node (Join-Path $PSScriptRoot "prerelease-notes.mjs") `
  --version $Version `
  --channel $Channel `
  --output $releaseNotesPath
Push-Location (Join-Path $RepoRoot "cli")
try { uv version $Version --no-sync } finally { Pop-Location }
Push-Location (Join-Path $RepoRoot "desktop")
try { npm version $Version --no-git-tag-version --allow-same-version } finally { Pop-Location }
Push-Location (Join-Path $RepoRoot "frontend")
try { npm version $Version --no-git-tag-version --allow-same-version } finally { Pop-Location }
Copy-Item -LiteralPath $releaseNotesPath -Destination (Join-Path $RepoRoot "desktop\release-notes.md") -Force

Write-Host "已准备 $releaseTag 的本地版本元数据和公告草稿。"
Write-Host "请编辑 docs/releases/$releaseTag.md，删除全部占位文本，并同步复制到 desktop/release-notes.md。"
Write-Host "完成测试并提交后，记录精确 HEAD，再运行："
Write-Host ".\scripts\prerelease.ps1 certify $Version -Channel $Channel -SourceBranch $SourceBranch -ReleaseSha <40位SHA>"
Write-Host "本步骤没有 push、tag、workflow dispatch 或 GitHub Release。"
