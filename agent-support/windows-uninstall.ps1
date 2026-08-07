[CmdletBinding()]
param(
    [string]$ManifestPath,
    [string]$CliTarget,
    [string]$CommandDirectory,
    [string]$UserProfilePath,
    [switch]$SkipPathCleanup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-NormalizedPath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $null
    }
    return [System.IO.Path]::GetFullPath($Value).TrimEnd([char[]]@(92, 47))
}

function Test-SamePath([string]$Left, [string]$Right) {
    $normalizedLeft = Get-NormalizedPath $Left
    $normalizedRight = Get-NormalizedPath $Right
    if ($null -eq $normalizedLeft -or $null -eq $normalizedRight) {
        return $false
    }
    return [string]::Equals(
        $normalizedLeft,
        $normalizedRight,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Get-ObjectProperty([object]$Object, [string]$Name) {
    if ($null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name]) {
        return $Object.$Name
    }
    return $null
}

function Remove-PathSafely([string]$TargetPath) {
    if (-not (Test-Path -LiteralPath $TargetPath)) {
        return
    }
    $item = Get-Item -LiteralPath $TargetPath -Force
    $isReparsePoint = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    if ($isReparsePoint) {
        if ($item.PSIsContainer) {
            [System.IO.Directory]::Delete($item.FullName, $false)
        } else {
            [System.IO.File]::Delete($item.FullName)
        }
        return
    }
    if (-not $item.PSIsContainer) {
        [System.IO.File]::Delete($item.FullName)
        return
    }

    foreach ($child in @(Get-ChildItem -LiteralPath $TargetPath -Force)) {
        Remove-PathSafely $child.FullName
    }
    [System.IO.Directory]::Delete($item.FullName, $false)
}

function Remove-ManagedUserPathEntry([string]$ExpectedDirectory) {
    $registryKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
    if ($null -eq $registryKey) {
        return
    }
    try {
        $currentValue = $registryKey.GetValue(
            "Path",
            $null,
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
        )
        if ($null -eq $currentValue) {
            return
        }
        $keptEntries = [System.Collections.Generic.List[string]]::new()
        $removed = $false
        foreach ($entry in ([string]$currentValue).Split(";")) {
            if ([string]::IsNullOrWhiteSpace($entry)) {
                continue
            }
            if (Test-SamePath $entry $ExpectedDirectory) {
                $removed = $true
            } else {
                $keptEntries.Add($entry.Trim())
            }
        }
        if ($removed) {
            $valueKind = $registryKey.GetValueKind("Path")
            $registryKey.SetValue("Path", [string]::Join(";", $keptEntries), $valueKind)
        }
    } finally {
        $registryKey.Dispose()
    }
}

if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    if ([string]::IsNullOrWhiteSpace($env:APPDATA)) {
        return
    }
    $ManifestPath = Join-Path $env:APPDATA "auto-email-sender-desktop\agent\installation.json"
}
if ([string]::IsNullOrWhiteSpace($CliTarget)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        return
    }
    $CliTarget = Join-Path $env:LOCALAPPDATA "AutoEmailSender\bin\auto-email-sender.exe"
}
if ([string]::IsNullOrWhiteSpace($CommandDirectory)) {
    $CommandDirectory = Split-Path -Parent $CliTarget
}
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    return
}

try {
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    return
}

$manifestSchemaVersion = Get-ObjectProperty $manifest "schema_version"
$manifestEnabled = Get-ObjectProperty $manifest "enabled"
$manifestCliTarget = Get-ObjectProperty $manifest "cli_target"
$manifestPathManaged = Get-ObjectProperty $manifest "path_managed"
if (
    $manifestSchemaVersion -notin @(1, 2, 3, 4) -or
    $manifestEnabled -isnot [bool] -or
    $manifestEnabled -ne $true -or
    $manifestCliTarget -isnot [string] -or
    $manifestPathManaged -isnot [bool]
) {
    return
}

$userProfile = if ([string]::IsNullOrWhiteSpace($UserProfilePath)) {
    [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::UserProfile)
} else {
    $UserProfilePath
}
if ([string]::IsNullOrWhiteSpace($userProfile)) {
    return
}
$expectedAgentTargets = [ordered]@{
    codex = (Join-Path $userProfile ".agents\skills\auto-email-sender")
    claude_code = (Join-Path $userProfile ".claude\skills\auto-email-sender")
    cursor = (Join-Path $userProfile ".cursor\skills\auto-email-sender")
    copilot_cli = (Join-Path $userProfile ".copilot\skills\auto-email-sender")
}

$managedSkillTargets = [System.Collections.Generic.List[string]]::new()
if ($manifestSchemaVersion -eq 4) {
    $agents = Get-ObjectProperty $manifest "agents"
    foreach ($agentId in $expectedAgentTargets.Keys) {
        $record = Get-ObjectProperty $agents $agentId
        $recordTarget = Get-ObjectProperty $record "skill_target"
        if ($recordTarget -is [string] -and (Test-SamePath $recordTarget $expectedAgentTargets[$agentId])) {
            $managedSkillTargets.Add($expectedAgentTargets[$agentId])
        }
    }
} else {
    $legacySkillTarget = Get-ObjectProperty $manifest "skill_target"
    if ($legacySkillTarget -is [string] -and (Test-SamePath $legacySkillTarget $expectedAgentTargets.codex)) {
        $managedSkillTargets.Add($expectedAgentTargets.codex)
    }
}

$ownsCli = Test-SamePath ([string]$manifestCliTarget) $CliTarget
$ownsPath = $ownsCli -and (Test-SamePath (Split-Path -Parent ([string]$manifestCliTarget)) $CommandDirectory)
if (-not $ownsCli -and $managedSkillTargets.Count -eq 0 -and -not $ownsPath) {
    return
}

if ($ownsCli) {
    Remove-PathSafely $CliTarget
}
foreach ($skillTarget in $managedSkillTargets) {
    Remove-PathSafely $skillTarget
}
if (-not $SkipPathCleanup -and $ownsPath -and $manifestPathManaged -eq $true) {
    Remove-ManagedUserPathEntry $CommandDirectory
}
