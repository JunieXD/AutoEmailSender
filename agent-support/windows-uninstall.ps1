[CmdletBinding()]
param(
    [string]$ManifestPath,
    [string]$CliTarget,
    [string]$SkillTarget,
    [string]$CommandDirectory,
    [string]$BackupRoot,
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
    if ($null -ne $Object -and $Object.PSObject.Properties.Name -contains $Name) {
        return $Object.$Name
    }
    return $null
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-ReparseTarget([System.IO.FileSystemInfo]$Item) {
    if ($Item.PSObject.Properties.Name -contains "Target" -and $null -ne $Item.Target) {
        return [string]::Join(",", @($Item.Target))
    }
    return "unknown-reparse-target"
}

function Add-DirectoryFingerprintEntries(
    [string]$Root,
    [string]$RelativeDirectory,
    [System.Collections.Generic.List[string]]$Entries
) {
    $absoluteDirectory = if ([string]::IsNullOrEmpty($RelativeDirectory)) {
        $Root
    } else {
        Join-Path $Root ($RelativeDirectory.Replace("/", [string][System.IO.Path]::DirectorySeparatorChar))
    }
    [string[]]$childNames = @(
        Get-ChildItem -LiteralPath $absoluteDirectory -Force | ForEach-Object { $_.Name }
    )
    [System.Array]::Sort($childNames, [System.StringComparer]::Ordinal)
    foreach ($childName in $childNames) {
        $child = Get-Item -LiteralPath (Join-Path $absoluteDirectory $childName) -Force
        $relativePath = if ([string]::IsNullOrEmpty($RelativeDirectory)) {
            $child.Name
        } else {
            "$RelativeDirectory/$($child.Name)"
        }
        $isReparsePoint = ($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        if ($isReparsePoint) {
            $Entries.Add("L`t$relativePath`t$(Get-ReparseTarget $child)")
        } elseif ($child.PSIsContainer) {
            $Entries.Add("D`t$relativePath")
            Add-DirectoryFingerprintEntries $Root $relativePath $Entries
        } else {
            $Entries.Add("F`t$relativePath`t$(Get-FileSha256 $child.FullName)")
        }
    }
}

function Get-DirectorySha256([string]$Path) {
    $entries = [System.Collections.Generic.List[string]]::new()
    Add-DirectoryFingerprintEntries $Path "" $entries
    $canonicalListing = if ($entries.Count -eq 0) {
        ""
    } else {
        [string]::Join("`n", $entries) + "`n"
    }
    $encoding = [System.Text.UTF8Encoding]::new($false)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha256.ComputeHash($encoding.GetBytes($canonicalListing))
        return ([System.BitConverter]::ToString($digest)).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
}

function Test-IsSha256([object]$Value) {
    return $Value -is [string] -and $Value -match "^[a-fA-F0-9]{64}$"
}

function Copy-PathSafely([string]$Source, [string]$Destination) {
    $item = Get-Item -LiteralPath $Source -Force
    $isReparsePoint = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    if ($isReparsePoint) {
        $parent = Split-Path -Parent $Destination
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        Set-Content -LiteralPath "$Destination.reparse-point.txt" -Encoding UTF8 -Value (Get-ReparseTarget $item)
        return
    }
    if (-not $item.PSIsContainer) {
        $parent = Split-Path -Parent $Destination
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Destination
        return
    }

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($child in @(Get-ChildItem -LiteralPath $Source -Force)) {
        Copy-PathSafely $child.FullName (Join-Path $Destination $child.Name)
    }
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
        $currentPath = [string]$currentValue
        $keptEntries = [System.Collections.Generic.List[string]]::new()
        $removed = $false
        foreach ($entry in $currentPath.Split(";")) {
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
if ([string]::IsNullOrWhiteSpace($SkillTarget)) {
    $userProfile = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::UserProfile)
    if ([string]::IsNullOrWhiteSpace($userProfile)) {
        return
    }
    $SkillTarget = Join-Path $userProfile ".agents\skills\auto-email-sender"
}
if ([string]::IsNullOrWhiteSpace($CommandDirectory)) {
    $CommandDirectory = Split-Path -Parent $CliTarget
}
if ([string]::IsNullOrWhiteSpace($BackupRoot)) {
    $BackupRoot = Join-Path (Split-Path -Parent $ManifestPath) "backups"
}

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    return
}
try {
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    return
}
$manifestEnabled = Get-ObjectProperty $manifest "enabled"
$manifestSchemaVersion = Get-ObjectProperty $manifest "schema_version"
$manifestCliTarget = Get-ObjectProperty $manifest "cli_target"
$manifestSkillTarget = Get-ObjectProperty $manifest "skill_target"
$manifestCliSha256 = Get-ObjectProperty $manifest "cli_sha256"
$manifestSkillSha256 = Get-ObjectProperty $manifest "skill_sha256"
$manifestPathManaged = Get-ObjectProperty $manifest "path_managed"
if (
    $manifestSchemaVersion -notin @(1, 2) -or
    $manifestEnabled -isnot [bool] -or
    $manifestEnabled -ne $true -or
    $manifestCliTarget -isnot [string] -or
    $manifestSkillTarget -isnot [string] -or
    $manifestPathManaged -isnot [bool]
) {
    return
}

$ownsCli = Test-SamePath ([string]$manifestCliTarget) $CliTarget
$ownsSkill = Test-SamePath ([string]$manifestSkillTarget) $SkillTarget
$ownsPath = $ownsCli -and (Test-SamePath (Split-Path -Parent ([string]$manifestCliTarget)) $CommandDirectory)
if (-not $ownsCli -and -not $ownsSkill -and -not $ownsPath) {
    return
}

$cliChanged = $false
$skillChanged = $false
if ($ownsCli -and (Test-Path -LiteralPath $CliTarget)) {
    $cliItem = Get-Item -LiteralPath $CliTarget -Force
    $cliChanged = $cliItem.PSIsContainer -or
        (($cliItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) -or
        -not (Test-IsSha256 $manifestCliSha256) -or
        (Get-FileSha256 $CliTarget) -ne ([string]$manifestCliSha256).ToLowerInvariant()
}
if ($ownsSkill -and (Test-Path -LiteralPath $SkillTarget)) {
    $skillItem = Get-Item -LiteralPath $SkillTarget -Force
    $skillChanged = -not $skillItem.PSIsContainer -or
        (($skillItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) -or
        -not (Test-IsSha256 $manifestSkillSha256) -or
        (Get-DirectorySha256 $SkillTarget) -ne ([string]$manifestSkillSha256).ToLowerInvariant()
}

$backupDirectory = $null
if ($cliChanged -or $skillChanged) {
    $backupName = "uninstall-{0}-{1}" -f [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH-mm-ss-fffZ"), [Guid]::NewGuid().ToString("N")
    $backupDirectory = Join-Path $BackupRoot $backupName
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    if ($cliChanged) {
        Copy-PathSafely $CliTarget (Join-Path $backupDirectory "auto-email-sender.exe")
    }
    if ($skillChanged) {
        Copy-PathSafely $SkillTarget (Join-Path $backupDirectory "auto-email-sender-skill")
    }
}

if ($ownsCli) {
    Remove-PathSafely $CliTarget
}
if ($ownsSkill) {
    Remove-PathSafely $SkillTarget
}
if (-not $SkipPathCleanup -and $ownsPath -and $manifestPathManaged -eq $true) {
    Remove-ManagedUserPathEntry $CommandDirectory
}

if ($null -ne $backupDirectory) {
    if ($manifest.PSObject.Properties.Name -contains "last_backup_directory") {
        $manifest.last_backup_directory = $backupDirectory
    } else {
        $manifest | Add-Member -NotePropertyName last_backup_directory -NotePropertyValue $backupDirectory
    }
    if ($manifest.PSObject.Properties.Name -contains "updated_at") {
        $manifest.updated_at = [DateTime]::UtcNow.ToString("o")
    } else {
        $manifest | Add-Member -NotePropertyName updated_at -NotePropertyValue ([DateTime]::UtcNow.ToString("o"))
    }
    $temporaryManifest = "$ManifestPath.$([Guid]::NewGuid().ToString("N")).tmp"
    try {
        $manifestJson = ($manifest | ConvertTo-Json -Depth 20) + "`n"
        [System.IO.File]::WriteAllText(
            $temporaryManifest,
            $manifestJson,
            [System.Text.UTF8Encoding]::new($false)
        )
        Move-Item -LiteralPath $temporaryManifest -Destination $ManifestPath -Force
    } finally {
        if (Test-Path -LiteralPath $temporaryManifest) {
            Remove-Item -LiteralPath $temporaryManifest -Force
        }
    }
}
