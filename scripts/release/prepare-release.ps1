param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidatePattern('^\d+\.\d+\.\d+$')]
  [string]$Version,

  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$tag = "v$Version"
$releaseDirectory = Join-Path $RepoRoot "docs\releases"
$releaseNotesPath = Join-Path $releaseDirectory "$tag.md"
$relativeReleaseNotesPath = "docs/releases/$tag.md"

if (Test-Path $releaseNotesPath) {
  throw "$relativeReleaseNotesPath 已经存在。请直接编辑该文件，或删除后重新生成。"
}

New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null
node (Join-Path $PSScriptRoot "release-notes.mjs") `
  --repo-root $RepoRoot `
  --version $tag `
  --output $releaseNotesPath

Write-Host "已生成 $relativeReleaseNotesPath。"
Write-Host "请编辑 $relativeReleaseNotesPath，润色更新内容后再运行："
Write-Host "pwsh -NoLogo -NoProfile -File .\scripts\release.ps1 $Version"
