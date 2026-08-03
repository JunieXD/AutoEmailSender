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

function Get-TextSha256([string]$Value) {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($Value)
        return ([System.BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
}

function Write-Manifest(
    [string]$Path,
    [string]$CliTarget,
    [string]$SkillTarget,
    [object]$CliSha256,
    [object]$SkillSha256
) {
    $manifest = [ordered]@{
        schema_version = 2
        enabled = $true
        prompt_dismissed = $true
        app_version = "2.4.1"
        desktop_executable = "C:\Program Files\Auto Email Sender\Auto Email Sender.exe"
        cli_source = "C:\Program Files\Auto Email Sender\resources\cli\auto-email-sender.exe"
        cli_target = $CliTarget
        skill_target = $SkillTarget
        cli_sha256 = $CliSha256
        skill_sha256 = $SkillSha256
        path_managed = $false
        last_backup_directory = $null
        updated_at = "2026-08-03T00:00:00.000Z"
    }
    Write-Utf8NoBom $Path (($manifest | ConvertTo-Json -Depth 10) + "`n")
}

$cleanupScript = Join-Path (Split-Path -Parent $PSScriptRoot) "agent-support\windows-uninstall.ps1"
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("auto-email-sender-uninstall-test-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $testRoot | Out-Null

try {
    $modifiedRoot = Join-Path $testRoot "modified"
    $modifiedCli = Join-Path $modifiedRoot "bin\auto-email-sender.exe"
    $modifiedSkill = Join-Path $modifiedRoot "skills\auto-email-sender"
    $modifiedManifest = Join-Path $modifiedRoot "user-data\agent\installation.json"
    $modifiedBackups = Join-Path $modifiedRoot "backups"
    Write-Utf8NoBom $modifiedCli "user changed cli"
    Write-Utf8NoBom (Join-Path $modifiedSkill "SKILL.md") "user changed skill"
    Write-Manifest $modifiedManifest $modifiedCli $modifiedSkill $null $null

    & $cleanupScript `
        -ManifestPath $modifiedManifest `
        -CliTarget $modifiedCli `
        -SkillTarget $modifiedSkill `
        -CommandDirectory (Split-Path -Parent $modifiedCli) `
        -BackupRoot $modifiedBackups `
        -SkipPathCleanup

    Assert-True (-not (Test-Path -LiteralPath $modifiedCli)) "Modified managed CLI was not removed."
    Assert-True (-not (Test-Path -LiteralPath $modifiedSkill)) "Modified managed Skill was not removed."
    $backupDirectories = @(Get-ChildItem -LiteralPath $modifiedBackups -Directory)
    Assert-True ($backupDirectories.Count -eq 1) "Expected exactly one backup directory."
    Assert-True ((Get-Content -LiteralPath (Join-Path $backupDirectories[0].FullName "auto-email-sender.exe") -Raw) -eq "user changed cli") "CLI backup content changed."
    Assert-True ((Get-Content -LiteralPath (Join-Path $backupDirectories[0].FullName "auto-email-sender-skill\SKILL.md") -Raw) -eq "user changed skill") "Skill backup content changed."
    $updatedManifest = Get-Content -LiteralPath $modifiedManifest -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True (-not [string]::IsNullOrWhiteSpace($updatedManifest.last_backup_directory)) "Backup path was not recorded."

    $managedRoot = Join-Path $testRoot "managed"
    $managedCli = Join-Path $managedRoot "bin\auto-email-sender.exe"
    $managedSkill = Join-Path $managedRoot "skills\auto-email-sender"
    $managedSkillFile = Join-Path $managedSkill "SKILL.md"
    $managedAgentFile = Join-Path $managedSkill "agents\openai.yaml"
    $managedManifest = Join-Path $managedRoot "user-data\agent\installation.json"
    $managedBackups = Join-Path $managedRoot "backups"
    Write-Utf8NoBom $managedCli "managed cli"
    Write-Utf8NoBom $managedSkillFile "managed skill"
    Write-Utf8NoBom $managedAgentFile "interface: cli"
    $managedCliSha = (Get-FileHash -LiteralPath $managedCli -Algorithm SHA256).Hash.ToLowerInvariant()
    $managedSkillFileSha = (Get-FileHash -LiteralPath $managedSkillFile -Algorithm SHA256).Hash.ToLowerInvariant()
    $managedAgentFileSha = (Get-FileHash -LiteralPath $managedAgentFile -Algorithm SHA256).Hash.ToLowerInvariant()
    $managedSkillSha = Get-TextSha256 (
        "F`tSKILL.md`t$managedSkillFileSha`n" +
        "D`tagents`n" +
        "F`tagents/openai.yaml`t$managedAgentFileSha`n"
    )
    Write-Manifest $managedManifest $managedCli $managedSkill $managedCliSha $managedSkillSha

    & $cleanupScript `
        -ManifestPath $managedManifest `
        -CliTarget $managedCli `
        -SkillTarget $managedSkill `
        -CommandDirectory (Split-Path -Parent $managedCli) `
        -BackupRoot $managedBackups `
        -SkipPathCleanup

    Assert-True (-not (Test-Path -LiteralPath $managedCli)) "Unmodified managed CLI was not removed."
    Assert-True (-not (Test-Path -LiteralPath $managedSkill)) "Unmodified managed Skill was not removed."
    Assert-True (-not (Test-Path -LiteralPath $managedBackups)) "Unmodified content should not create a backup."

    $unmanagedRoot = Join-Path $testRoot "unmanaged"
    $unmanagedCli = Join-Path $unmanagedRoot "bin\auto-email-sender.exe"
    $unmanagedSkill = Join-Path $unmanagedRoot "skills\auto-email-sender"
    $unmanagedManifest = Join-Path $unmanagedRoot "user-data\agent\installation.json"
    Write-Utf8NoBom $unmanagedCli "user cli"
    Write-Utf8NoBom (Join-Path $unmanagedSkill "SKILL.md") "user skill"
    Write-Manifest $unmanagedManifest "C:\SomeoneElse\auto-email-sender.exe" "C:\SomeoneElse\skill" $null $null

    & $cleanupScript `
        -ManifestPath $unmanagedManifest `
        -CliTarget $unmanagedCli `
        -SkillTarget $unmanagedSkill `
        -CommandDirectory (Split-Path -Parent $unmanagedCli) `
        -BackupRoot (Join-Path $unmanagedRoot "backups") `
        -SkipPathCleanup

    Assert-True (Test-Path -LiteralPath $unmanagedCli) "Unmanaged CLI was deleted."
    Assert-True (Test-Path -LiteralPath $unmanagedSkill) "Unmanaged Skill was deleted."

    Write-Host "Windows Agent support cleanup tests passed."
} finally {
    if (Test-Path -LiteralPath $testRoot) {
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
}
