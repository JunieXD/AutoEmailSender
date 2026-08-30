param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidatePattern('^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$')]
  [string]$Version,

  [switch]$DryRun,
  [switch]$SkipVerify,
  [string]$PromoteRun = "",
  [string]$QualityEvidence = "",
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$RepoRoot = (Resolve-Path $RepoRoot).Path
$ReleaseTag = "v$Version"
$ReleaseVersionChecker = Join-Path $PSScriptRoot "check-release-version.mjs"
$ReleasePreflight = Join-Path $PSScriptRoot "release-preflight.mjs"
$CuratedReleaseNotesPath = Join-Path $RepoRoot "docs\releases\$ReleaseTag.md"
$DesktopReleaseNotesPath = Join-Path $RepoRoot "desktop\release-notes.md"
if ($PromoteRun -and $PromoteRun -notmatch '^[1-9][0-9]*$') {
  throw "PromoteRun 必须是有效的候选 workflow run ID。"
}

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
      $PromoteRun -or $path -ne $allowedReleaseNotesPath
    }
  )
  if ($unexpectedStatus.Count -gt 0) {
    throw "工作区存在未提交改动，请先提交或清理后再发布。"
  }
}

function Invoke-ReleasePreflight {
  param([string]$ReleaseSha = "")
  if ($DryRun) {
    $suffix = if ($ReleaseSha) { " at $ReleaseSha" } else { "" }
    Write-Host "[dry-run] validate frozen $ReleaseTag metadata$suffix"
    return
  }
  $arguments = @($ReleasePreflight, "--version", $Version, "--repo-root", $RepoRoot)
  if ($ReleaseSha) {
    $arguments += @("--release-sha", $ReleaseSha)
  }
  Invoke-CheckedCommand "frozen release candidate preflight" {
    node @arguments
  }
}

function Assert-ReleaseNotes {
  $relativePath = "docs/releases/$ReleaseTag.md"
  if (-not (Test-Path $CuratedReleaseNotesPath)) {
    throw "缺少 $relativePath，请先运行 .\scripts\prepare-release.ps1 $Version 并润色公告后再发布。"
  }
}

function Assert-ReleaseVersion {
  Invoke-CheckedCommand "release version preflight" {
    node $ReleaseVersionChecker --version $Version --repo-root $RepoRoot
  }
}

function Copy-ReleaseNotes {
  if ($DryRun) {
    Write-Host "[dry-run] copy docs/releases/$ReleaseTag.md to desktop/release-notes.md"
    return
  }
  Copy-Item -LiteralPath $CuratedReleaseNotesPath -Destination $DesktopReleaseNotesPath -Force
}

$QualitySuites = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::Ordinal
)

function Import-QualityEvidence {
  if (-not $QualityEvidence) {
    return
  }
  if ($PromoteRun) {
    throw "QualityEvidence 只用于候选认证前的本地验证。"
  }
  $evidenceScript = Join-Path $PSScriptRoot "quality-evidence.mjs"
  $verifiedSuites = @(& node $evidenceScript --evidence $QualityEvidence --repo-root $RepoRoot)
  if ($LASTEXITCODE -ne 0) {
    throw "全仓质量证据无效。"
  }
  foreach ($suite in $verifiedSuites) {
    if ($suite) {
      [void]$QualitySuites.Add($suite.Trim())
    }
  }
  Write-Host "[reuse] 已加载绑定当前 SHA 和工具链的全仓质量证据。"
}

function Invoke-Verification {
  if ($SkipVerify) {
    Write-Host "[skip] 跳过发布前验证"
    return
  }

  Write-Host "=== 验证 frontend ==="
  Push-Location (Join-Path $RepoRoot "frontend")
  try {
    if ($QualitySuites.Contains("frontend")) {
      Write-Host "[reuse] frontend tests 已由全仓质量证据覆盖"
    } else {
      Invoke-CheckedCommand "frontend: npm test" { npm test }
    }
    Invoke-CheckedCommand "frontend: npm run lint" { npm run lint }
    Invoke-CheckedCommand "frontend: npm run build" { npm run build }
  } finally {
    Pop-Location
  }

  Write-Host "=== 验证 backend ==="
  Push-Location (Join-Path $RepoRoot "backend")
  try {
    Invoke-CheckedCommand "backend: uv sync --dev" { uv sync --dev }
    if ($QualitySuites.Contains("backend")) {
      Write-Host "[reuse] backend tests 已由全仓质量证据覆盖"
    } else {
      Invoke-CheckedCommand "backend: uv run python -m unittest test.test_desktop_runtime" {
        uv run python -m unittest test.test_desktop_runtime
      }
      Invoke-CheckedCommand "backend: uv run python -m unittest test.test_database_schema test.test_migrations_runtime" {
        uv run python -m unittest test.test_database_schema test.test_migrations_runtime
      }
      Invoke-CheckedCommand "backend: uv run python -m unittest test.test_crawl_mentors_skill_contract test.test_crawl_mentors_skill_package" {
        uv run python -m unittest test.test_crawl_mentors_skill_contract test.test_crawl_mentors_skill_package
      }
    }
  } finally {
    Pop-Location
  }

  Write-Host "=== 验证 cli ==="
  Push-Location (Join-Path $RepoRoot "cli")
  try {
    Invoke-CheckedCommand "cli: uv sync --dev" { uv sync --dev }
    if ($QualitySuites.Contains("cli")) {
      Write-Host "[reuse] CLI tests 已由全仓质量证据覆盖"
    } else {
      Invoke-CheckedCommand "cli: uv run python -m unittest discover test" {
        uv run python -m unittest discover test
      }
    }
  } finally {
    Pop-Location
  }
  Invoke-CheckedCommand "cli: frozen binary" {
    & (Join-Path $RepoRoot "scripts\build-cli.ps1") -Clean
  }

  Write-Host "=== 验证 desktop ==="
  Push-Location (Join-Path $RepoRoot "desktop")
  try {
    if ($QualitySuites.Contains("desktop")) {
      Write-Host "[reuse] desktop tests 已由全仓质量证据覆盖"
    } else {
      Invoke-CheckedCommand "desktop: npm test" { npm test }
    }
  } finally {
    Pop-Location
  }
}

function Set-CliVersion {
  Push-Location (Join-Path $RepoRoot "cli")
  try {
    if ($DryRun) {
      Write-Host "[dry-run] uv version $Version --no-sync in cli"
      return
    }
    uv version $Version --no-sync
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
    npm version $Version --no-git-tag-version --allow-same-version
  } finally {
    Pop-Location
  }
}

Assert-ReleaseVersion
Assert-CleanRepository
Assert-ReleaseNotes
Import-QualityEvidence

if ($PromoteRun) {
  if ($SkipVerify) {
    throw "SkipVerify 不能用于候选提升。"
  }
  if ($DryRun) {
    Invoke-ReleasePreflight -ReleaseSha "<release-commit-sha>"
    Write-Host "[dry-run] gh workflow run release.yml --ref master -f release_tag=$ReleaseTag -f release_sha=<release-commit-sha> -f publish=true -f candidate_run_id=$PromoteRun"
  } else {
    Invoke-ReleasePreflight
    Push-Location $RepoRoot
    try {
      $releaseSha = (gh run view $PromoteRun --json headSha --jq .headSha).Trim()
      if ($releaseSha -notmatch '^[0-9a-f]{40}$') {
        throw "候选 run $PromoteRun 没有有效的 head SHA。"
      }
      gh workflow run release.yml `
        --ref master `
        -f "release_tag=$ReleaseTag" `
        -f "release_sha=$releaseSha" `
        -f "publish=true" `
        -f "candidate_run_id=$PromoteRun"
    } finally {
      Pop-Location
    }
    Write-Host "已启动 $ReleaseTag 提升工作流，只会发布候选 run $PromoteRun 的已认证产物。"
  }
  exit 0
}

Invoke-Verification
Set-CliVersion
Set-NpmVersion "desktop"
Set-NpmVersion "frontend"
Copy-ReleaseNotes
Invoke-ReleasePreflight

Run-Git add cli/pyproject.toml cli/uv.lock desktop/package.json desktop/package-lock.json frontend/package.json frontend/package-lock.json desktop/release-notes.md "docs/releases/$ReleaseTag.md"
if ($DryRun) {
  Run-Git commit -m "chore(release): $ReleaseTag"
} else {
  $stagedPaths = @(git -C $RepoRoot diff --cached --name-only)
  if ($stagedPaths.Count -gt 0) {
    Run-Git commit -m "chore(release): $ReleaseTag"
  } else {
    Write-Host "发布版本和公告已包含在候选提交中，复用当前 HEAD。"
  }
}
if ($DryRun) {
  Run-Git push origin master
  Write-Host "[dry-run] gh workflow run release.yml --ref master -f release_tag=$ReleaseTag -f release_sha=<release-commit-sha> -f publish=false -f candidate_run_id="
  Write-Host "[dry-run] 未创建提交、tag 或推送。候选认证成功后，使用 -PromoteRun <run-id> 发布同一批产物。"
} else {
  $releaseSha = (git -C $RepoRoot rev-parse HEAD).Trim()
  Invoke-ReleasePreflight -ReleaseSha $releaseSha
  Run-Git push origin master
  Push-Location $RepoRoot
  try {
    gh workflow run release.yml `
      --ref master `
      -f "release_tag=$ReleaseTag" `
      -f "release_sha=$releaseSha" `
      -f "publish=false" `
      -f "candidate_run_id="
  } finally {
    Pop-Location
  }
  Write-Host "已推送发布提交 $releaseSha 并启动 $ReleaseTag 候选认证；本次不会创建 tag 或 Release。"
  Write-Host "认证成功后运行：.\scripts\release.ps1 $Version -PromoteRun <candidate-run-id>"
}
