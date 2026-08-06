[CmdletBinding()]
param(
    [string]$DatabasePath,
    [string]$OutputDirectory = "data/logs"
)

[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$backendDir = Join-Path $repoRoot "backend"
$outputPath = Join-Path $repoRoot $OutputDirectory

Push-Location $backendDir
try {
    $argsList = @("scripts/audit_time_data.py", "--output-directory", $outputPath)
    if ($DatabasePath) {
        $argsList += @("--database", $DatabasePath)
    }
    uv run python @argsList
}
finally {
    Pop-Location
}
