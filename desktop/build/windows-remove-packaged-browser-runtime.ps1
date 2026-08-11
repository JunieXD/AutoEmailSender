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
[System.IO.Directory]::Delete($extendedBrowserRuntime, -not $runtimeIsReparsePoint)
if ([System.IO.Directory]::Exists($extendedBrowserRuntime)) {
    throw "Packaged browser runtime remains after extended-path cleanup."
}
