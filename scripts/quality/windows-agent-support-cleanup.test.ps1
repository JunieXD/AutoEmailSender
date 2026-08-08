$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw $Message
    }
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $parent = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

function Write-Manifest(
    [string]$Path,
    [string]$CliTarget,
    [object]$Agents,
    [bool]$Enabled = $true
) {
    $manifest = [ordered]@{
        schema_version = 5
        enabled = $Enabled
        prompt_dismissed = $true
        app_version = "2.4.1"
        cli_source = "C:\Program Files\Auto Email Sender\resources\cli\auto-email-sender.exe"
        skill_source = "C:\Program Files\Auto Email Sender\resources\agent-support\skills\auto-email-sender"
        cli_target = $CliTarget
        cli_sha256 = ("a" * 64)
        path_managed = $false
        agents = $Agents
        updated_at = "2026-08-04T00:00:00.000Z"
    }
    Write-Utf8NoBom $Path (($manifest | ConvertTo-Json -Depth 10) + "`n")
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$cleanupScript = Join-Path $repoRoot "agent-support\windows-uninstall.ps1"
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("auto-email-sender-uninstall-test-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $testRoot | Out-Null

try {
    $managedRoot = Join-Path $testRoot "managed"
    $profile = Join-Path $managedRoot "profile"
    $managedCli = Join-Path $managedRoot "bin\auto-email-sender.cmd"
    $managedManifest = Join-Path $managedRoot "user-data\agent\installation.json"
    $codexSkill = Join-Path $profile ".agents\skills\auto-email-sender"
    $claudeSkill = Join-Path $profile ".claude\skills\auto-email-sender"
    Write-Utf8NoBom $managedCli "@echo off`r`nmanaged cli"
    Write-Utf8NoBom (Join-Path $codexSkill "SKILL.md") "managed codex skill"
    Write-Utf8NoBom (Join-Path $claudeSkill "SKILL.md") "managed claude skill"
    Write-Manifest $managedManifest $managedCli ([ordered]@{
        codex = @{ skill_target = $codexSkill; skill_sha256 = ("b" * 64) }
        claude_code = @{ skill_target = $claudeSkill; skill_sha256 = ("b" * 64) }
    })

    & $cleanupScript `
        -ManifestPath $managedManifest `
        -CliTarget $managedCli `
        -CommandDirectory (Split-Path -Parent $managedCli) `
        -UserProfilePath $profile `
        -SkipPathCleanup

    Assert-True (-not (Test-Path -LiteralPath $managedCli)) "Managed CLI was not removed."
    Assert-True (-not (Test-Path -LiteralPath $codexSkill)) "Managed Codex Skill was not removed."
    Assert-True (-not (Test-Path -LiteralPath $claudeSkill)) "Managed Claude Code Skill was not removed."

    $v4Root = Join-Path $testRoot "v4"
    $v4Profile = Join-Path $v4Root "profile"
    $v4Cli = Join-Path $v4Root "bin\auto-email-sender.exe"
    $v4Manifest = Join-Path $v4Root "user-data\agent\installation.json"
    Write-Utf8NoBom $v4Cli "v4 managed cli"
    $v4 = [ordered]@{
        schema_version = 4
        enabled = $true
        prompt_dismissed = $true
        app_version = "2.4.1"
        cli_target = $v4Cli
        cli_sha256 = ("a" * 64)
        path_managed = $false
        agents = @{}
        updated_at = "2026-08-04T00:00:00.000Z"
    }
    Write-Utf8NoBom $v4Manifest (($v4 | ConvertTo-Json -Depth 10) + "`n")

    & $cleanupScript `
        -ManifestPath $v4Manifest `
        -CommandDirectory (Split-Path -Parent $v4Cli) `
        -UserProfilePath $v4Profile `
        -SkipPathCleanup

    Assert-True (-not (Test-Path -LiteralPath $v4Cli)) "V4 managed CLI was not removed."

    $legacyRoot = Join-Path $testRoot "legacy"
    $legacyProfile = Join-Path $legacyRoot "profile"
    $legacyCli = Join-Path $legacyRoot "bin\auto-email-sender.exe"
    $legacySkill = Join-Path $legacyProfile ".agents\skills\auto-email-sender"
    $legacyManifest = Join-Path $legacyRoot "user-data\agent\installation.json"
    Write-Utf8NoBom $legacyCli "legacy cli"
    Write-Utf8NoBom (Join-Path $legacySkill "SKILL.md") "legacy skill"
    $legacy = [ordered]@{
        schema_version = 3
        enabled = $true
        prompt_dismissed = $true
        app_version = "2.4.1"
        cli_target = $legacyCli
        skill_target = $legacySkill
        cli_sha256 = ("a" * 64)
        skill_sha256 = ("b" * 64)
        path_managed = $false
        updated_at = "2026-08-04T00:00:00.000Z"
    }
    Write-Utf8NoBom $legacyManifest (($legacy | ConvertTo-Json -Depth 10) + "`n")

    & $cleanupScript `
        -ManifestPath $legacyManifest `
        -CliTarget $legacyCli `
        -CommandDirectory (Split-Path -Parent $legacyCli) `
        -UserProfilePath $legacyProfile `
        -SkipPathCleanup

    Assert-True (-not (Test-Path -LiteralPath $legacyCli)) "Legacy managed CLI was not removed."
    Assert-True (-not (Test-Path -LiteralPath $legacySkill)) "Legacy managed Skill was not removed."

    $unmanagedRoot = Join-Path $testRoot "unmanaged"
    $unmanagedProfile = Join-Path $unmanagedRoot "profile"
    $unmanagedCli = Join-Path $unmanagedRoot "bin\auto-email-sender.exe"
    $unmanagedSkill = Join-Path $unmanagedProfile ".cursor\skills\auto-email-sender"
    $unmanagedManifest = Join-Path $unmanagedRoot "user-data\agent\installation.json"
    Write-Utf8NoBom $unmanagedCli "user cli"
    Write-Utf8NoBom (Join-Path $unmanagedSkill "SKILL.md") "user skill"
    Write-Manifest $unmanagedManifest "C:\SomeoneElse\auto-email-sender.exe" @{}

    & $cleanupScript `
        -ManifestPath $unmanagedManifest `
        -CliTarget $unmanagedCli `
        -CommandDirectory (Split-Path -Parent $unmanagedCli) `
        -UserProfilePath $unmanagedProfile `
        -SkipPathCleanup

    Assert-True (Test-Path -LiteralPath $unmanagedCli) "Unmanaged CLI was deleted."
    Assert-True (Test-Path -LiteralPath $unmanagedSkill) "Unmanaged Skill was deleted."

    Write-Host "Windows Agent support cleanup tests passed."
} finally {
    if (Test-Path -LiteralPath $testRoot) {
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
}
