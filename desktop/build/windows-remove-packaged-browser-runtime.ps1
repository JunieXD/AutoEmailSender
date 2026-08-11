[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-NormalizedPath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "InstallRoot must not be empty."
    }
    return [System.IO.Path]::GetFullPath($Value).TrimEnd([char[]]@(92, 47))
}

function Convert-ToExtendedPath([string]$Value) {
    $normalized = Get-NormalizedPath $Value
    if ($normalized.StartsWith("\\", [System.StringComparison]::Ordinal)) {
        return "\\?\UNC\" + $normalized.TrimStart([char[]]@(92))
    }
    return "\\?\" + $normalized
}

function Remove-ExtendedDirectoryTree([string]$Root) {
    $pending = [System.Collections.Generic.Stack[string]]::new()
    $directories = [System.Collections.Generic.List[string]]::new()
    $files = [System.Collections.Generic.List[string]]::new()
    $pending.Push($Root)

    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($current)) {
            $attributes = [System.IO.File]::GetAttributes($entry)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing to traverse a reparse point inside the packaged browser runtime."
            }
            if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                $directories.Add($entry)
                $pending.Push($entry)
            } else {
                $files.Add($entry)
            }
        }
    }

    foreach ($file in $files) {
        $attributes = [System.IO.File]::GetAttributes($file)
        if (($attributes -band [System.IO.FileAttributes]::ReadOnly) -ne 0) {
            $writable = $attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
            [System.IO.File]::SetAttributes($file, [System.IO.FileAttributes]$writable)
        }
        [System.IO.File]::Delete($file)
    }
    for ($index = $directories.Count - 1; $index -ge 0; $index -= 1) {
        [System.IO.Directory]::Delete($directories[$index], $false)
    }
    [System.IO.Directory]::Delete($Root, $false)
}

$normalizedInstallRoot = Get-NormalizedPath $InstallRoot
$resourcesRoot = Get-NormalizedPath (Join-Path $normalizedInstallRoot "resources")
$browserRuntime = Get-NormalizedPath (Join-Path $resourcesRoot "ms-playwright")
$expectedBrowserRuntime = $resourcesRoot.TrimEnd("\") + "\ms-playwright"
if (-not $browserRuntime.Equals(
    $expectedBrowserRuntime,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing an unexpected packaged browser runtime path."
}

foreach ($ancestor in @($normalizedInstallRoot, $resourcesRoot)) {
    $extendedAncestor = Convert-ToExtendedPath $ancestor
    if (-not [System.IO.Directory]::Exists($extendedAncestor)) {
        return
    }
    $attributes = [System.IO.File]::GetAttributes($extendedAncestor)
    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to traverse a reparse point while removing packaged browser files."
    }
}

$extendedBrowserRuntime = Convert-ToExtendedPath $browserRuntime
if (-not [System.IO.Directory]::Exists($extendedBrowserRuntime)) {
    return
}
$runtimeAttributes = [System.IO.File]::GetAttributes($extendedBrowserRuntime)
$runtimeIsReparsePoint = (
    $runtimeAttributes -band [System.IO.FileAttributes]::ReparsePoint
) -ne 0
if ($runtimeIsReparsePoint) {
    [System.IO.Directory]::Delete($extendedBrowserRuntime, $false)
} else {
    Remove-ExtendedDirectoryTree $extendedBrowserRuntime
}
if ([System.IO.Directory]::Exists($extendedBrowserRuntime)) {
    throw "Packaged browser runtime remains after extended-path cleanup."
}
