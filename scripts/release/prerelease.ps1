param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("certify", "publish")]
  [string]$Action,

  [Parameter(Mandatory = $true, Position = 1)]
  [string]$Version,

  [Parameter(Mandatory = $true)]
  [ValidateSet("alpha", "beta", "rc")]
  [string]$Channel,

  [Parameter(Mandatory = $true)]
  [string]$SourceBranch,

  [Parameter(Mandatory = $true)]
  [string]$ReleaseSha,

  [string]$CandidateRun = "",
  [switch]$DryRun,
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$RepoRoot = (Resolve-Path $RepoRoot).Path
if ($Action -eq "certify" -and $CandidateRun) {
  throw "certify 不接受 -CandidateRun；候选 run 只能由认证工作流产生。"
}
if ($Action -eq "publish" -and $CandidateRun -notmatch '^[1-9][0-9]*$') {
  throw "publish 必须提供有效的 -CandidateRun。"
}

node (Join-Path $PSScriptRoot "prerelease-contract.mjs") validate `
  --version $Version `
  --channel $Channel `
  --source-branch $SourceBranch `
  --release-sha $ReleaseSha | Out-Null
node (Join-Path $PSScriptRoot "prerelease-contract.mjs") check-tags `
  --version $Version `
  --channel $Channel `
  --repo-root $RepoRoot | Out-Null

$currentBranch = (git -C $RepoRoot branch --show-current).Trim()
if ($currentBranch -ne $SourceBranch) {
  $shown = if ($currentBranch) { $currentBranch } else { "<detached>" }
  throw "当前分支 $shown 与显式 source branch $SourceBranch 不一致。"
}
if ((git -C $RepoRoot rev-parse HEAD).Trim() -ne $ReleaseSha) {
  throw "当前 HEAD 与显式 release_sha 不一致。"
}
if (@(git -C $RepoRoot status --porcelain --untracked-files=all).Count -gt 0) {
  throw "prerelease 候选必须来自干净、已提交的工作区。"
}

node (Join-Path $PSScriptRoot "prerelease-preflight.mjs") `
  --version $Version `
  --channel $Channel `
  --source-branch $SourceBranch `
  --release-sha $ReleaseSha `
  --repo-root $RepoRoot

$publishValue = if ($Action -eq "publish") { "true" } else { "false" }
if ($DryRun) {
  if ($Action -eq "certify") {
    Write-Host "[dry-run] git push origin refs/heads/$SourceBranch`:refs/heads/$SourceBranch"
  } else {
    Write-Host "[dry-run] verify origin/$SourceBranch exactly equals $ReleaseSha"
  }
  Write-Host "[dry-run] gh workflow run release.yml --ref $SourceBranch -f release_kind=prerelease -f release_tag=v$Version -f release_sha=$ReleaseSha -f source_branch=$SourceBranch -f prerelease_channel=$Channel -f publish=$publishValue -f candidate_run_id=$CandidateRun"
  Write-Host "[dry-run] 未 push、tag、dispatch 或创建 GitHub Release。"
  exit 0
}

if ($Action -eq "certify") {
  node --test `
    (Join-Path $PSScriptRoot "prerelease-contract.test.mjs") `
    (Join-Path $PSScriptRoot "prerelease-preflight.test.mjs") `
    (Join-Path $PSScriptRoot "prerelease-build-identity.test.mjs") `
    (Join-Path $PSScriptRoot "prerelease-candidate.test.mjs") `
    (Join-Path $PSScriptRoot "prerelease-isolation.test.mjs")
  git -C $RepoRoot push origin "refs/heads/$SourceBranch`:refs/heads/$SourceBranch"
}

$remoteLine = @(git -C $RepoRoot ls-remote --heads origin "refs/heads/$SourceBranch")
$remoteSha = if ($remoteLine.Count -eq 1) { ($remoteLine[0] -split '\s+')[0] } else { "" }
if ($remoteSha -ne $ReleaseSha) {
  $shown = if ($remoteSha) { $remoteSha } else { "<missing>" }
  throw "origin/$SourceBranch 指向 $shown，预期 $ReleaseSha；拒绝 dispatch。"
}

Push-Location $RepoRoot
try {
  gh workflow run release.yml `
    --ref $SourceBranch `
    -f "release_kind=prerelease" `
    -f "release_tag=v$Version" `
    -f "release_sha=$ReleaseSha" `
    -f "source_branch=$SourceBranch" `
    -f "prerelease_channel=$Channel" `
    -f "publish=$publishValue" `
    -f "candidate_run_id=$CandidateRun"
} finally {
  Pop-Location
}

if ($Action -eq "certify") {
  Write-Host "已从 origin/$SourceBranch@$ReleaseSha 启动 v$Version 候选认证；不会创建 tag 或 Release。"
  Write-Host "记录成功的 candidate run ID；完成双平台 exact-package QA 并获得用户批准后再运行 publish。"
} else {
  Write-Host "已启动 v$Version 的 exact-candidate 发布工作流，只会提升候选 run $CandidateRun。"
  Write-Host "工作流必须发布为 prerelease=true、Latest=false，并验证稳定 feed 完全不变。"
}
