$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$directory = $env:AUTOEMAIL_CLI_DIRECTORY
$action = $env:AUTOEMAIL_CLI_ACTION
$environment = if ($action -eq 'status') {
    [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment')
} else {
    [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
}
$marker = $null
try {
    if ($null -eq $environment) { 'false'; return }
    $path = $environment.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    $kind = if ($environment.GetValueNames() -contains 'Path') {
        $environment.GetValueKind('Path')
    } else {
        [Microsoft.Win32.RegistryValueKind]::ExpandString
    }
    if ($kind -ne [Microsoft.Win32.RegistryValueKind]::String -and
        $kind -ne [Microsoft.Win32.RegistryValueKind]::ExpandString) {
        throw 'User PATH is not a string.'
    }
    $entries = [System.Collections.Generic.List[string]]::new()
    if ($path.Length -gt 0) { $entries.AddRange([string[]]$path.Split(';')) }
    function SameDirectory([string]$entry) {
        $expanded = [Environment]::ExpandEnvironmentVariables($entry.Trim().Trim('"')).TrimEnd([char[]]'\/')
        return [String]::Equals($expanded, $directory.TrimEnd([char[]]'\/'), [StringComparison]::OrdinalIgnoreCase)
    }
    $installed = @($entries | Where-Object { SameDirectory $_ }).Count -gt 0
    if ($action -ne 'status') {
        $marker = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Software\AutoEmailSender')
        $owned = $marker.GetValue('CliPath', $null)
        if ($null -ne $owned -and ($action -eq 'uninstall' -or $owned -ne $directory)) {
            # Remove only the single exact entry we recorded, preserving every other entry.
            $index = $entries.IndexOf($owned)
            if ($index -ge 0) { $entries.RemoveAt($index) }
            $environment.SetValue('Path', ($entries -join ';'), $kind)
            $marker.DeleteValue('CliPath', $false)
        }
        $installed = @($entries | Where-Object { SameDirectory $_ }).Count -gt 0
        if ($action -eq 'install' -and -not $installed) {
            # Record intent first so an interrupted installation can still be uninstalled.
            $marker.SetValue('CliPath', $directory)
            $entries.Add($directory)
            $environment.SetValue('Path', ($entries -join ';'), $kind)
            $installed = $true
        }
        Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class AutoEmailEnvironment {
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr SendMessageTimeout(
        IntPtr hwnd, uint msg, UIntPtr wparam, string lparam,
        uint flags, uint timeout, out UIntPtr result);
}
'@
        $result = [UIntPtr]::Zero
        $sent = [AutoEmailEnvironment]::SendMessageTimeout(
            [IntPtr]0xffff, 0x1a, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$result)
        if ($sent -eq [IntPtr]::Zero) {
            throw 'PATH was saved, but WM_SETTINGCHANGE could not be broadcast. Retry the operation.'
        }
    }
    ConvertTo-Json -Compress -InputObject ([bool]$installed)
} finally {
    if ($null -ne $environment) { $environment.Dispose() }
    if ($null -ne $marker) { $marker.Dispose() }
}
