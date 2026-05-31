[CmdletBinding()]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$FailOnViolation,
    [switch]$CoreOnly
)

[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
$rootPath = (Resolve-Path -LiteralPath $Root).Path
$violations = New-Object System.Collections.Generic.List[object]

function Test-TimeCheckExempt {
    param([string]$Line)
    if ($Line -notmatch 'time-check:') {
        return $false
    }
    return $Line -match 'reason\s*=\s*"[^"]+"' -or $Line -match "reason\s*=\s*'[^']+'" -or $Line -match 'because\s+.+' -or $Line -match 'time-check:\s*[^,]+,\s*\S.+'
}

function Add-Violation {
    param(
        [string]$File,
        [int]$LineNumber,
        [string]$Rule,
        [string]$Line,
        [string]$PreviousLine = "",
        [string]$PreviousPreviousLine = ""
    )
    if ((Test-TimeCheckExempt -Line $Line) -or (Test-TimeCheckExempt -Line $PreviousLine) -or (Test-TimeCheckExempt -Line $PreviousPreviousLine)) {
        return
    }
    $violations.Add([pscustomobject]@{
        File = $File
        Line = $LineNumber
        Rule = $Rule
        Text = $Line.Trim()
    }) | Out-Null
}


function Scan-Files {
    param(
        [string]$RelativePath,
        [string[]]$Include,
        [hashtable]$Rules
    )
    $base = Join-Path $rootPath $RelativePath
    if (-not (Test-Path $base)) {
        return
    }
    Get-ChildItem -Path $base -Recurse -File | Where-Object {
        $candidate = $_.Name
        $matched = $false
        foreach ($pattern in $Include) {
            if ($candidate -like $pattern) { $matched = $true; break }
        }
        $matched
    } | ForEach-Object {
        $file = $_.FullName
        $relative = [System.IO.Path]::GetRelativePath($rootPath, $file)
        $lines = @(Get-Content -LiteralPath $file -Encoding UTF8)
        for ($i = 0; $i -lt $lines.Count; $i++) {
            foreach ($ruleName in $Rules.Keys) {
                if ($lines[$i] -match $Rules[$ruleName]) {
                    $previousLine = if ($i -gt 0) { $lines[$i - 1] } else { "" }
                    $previousPreviousLine = if ($i -gt 1) { $lines[$i - 2] } else { "" }
                    Add-Violation -File $relative -LineNumber ($i + 1) -Rule $ruleName -Line $lines[$i] -PreviousLine $previousLine -PreviousPreviousLine $previousPreviousLine
                }
            }
        }
    }
}

$backendPath = if ($CoreOnly) { 'backend/app/services' } else { 'backend/app' }
Scan-Files -RelativePath $backendPath -Include @('*.py') -Rules @{
    'backend.datetime_now_utc' = 'datetime\.now\(UTC\)'
    'backend.datetime_now_local' = 'datetime\.now\(\)'
    'backend.replace_tzinfo_utc' = '\.replace\(tzinfo=UTC\)'
}

if (-not $CoreOnly) {
    Scan-Files -RelativePath 'backend/app/models' -Include @('*.py') -Rules @{
        'models.datetime_timezone_true' = 'DateTime\(timezone=True\)'
    }
    Scan-Files -RelativePath 'backend/alembic/versions' -Include @('*.py') -Rules @{
        'alembic.sqlite_localtime' = "datetime\(''now'',\s*''localtime''\)|datetime\('now',\s*'localtime'\)"
        'alembic.datetime_timezone_true' = 'sa\.DateTime\(timezone=True\)'
    }
    Scan-Files -RelativePath 'frontend/src' -Include @('*.ts','*.tsx') -Rules @{
        'frontend.new_date_variable' = 'new Date\((?!\)|Date\.now\(|["''`])[^)]*[A-Za-z_][^)]*\)'
        'frontend.direct_tolocalestring' = 'new Date\([^)]*\)\.toLocaleString\('
    }
}

if ($violations.Count -eq 0) {
    Write-Output "time-check: no violations found"
    exit 0
}

Write-Output ("time-check: found {0} potential issue(s)" -f $violations.Count)
foreach ($violation in $violations) {
    Write-Output ("{0}:{1}: {2}: {3}" -f $violation.File, $violation.Line, $violation.Rule, $violation.Text)
}

if ($FailOnViolation) {
    exit 1
}
exit 0