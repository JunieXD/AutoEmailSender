[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$repoRoot = Split-Path -Parent $PSScriptRoot
$fixtureRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("time-check-" + [System.Guid]::NewGuid().ToString("N"))
try {
    New-Item -ItemType Directory -Path (Join-Path $fixtureRoot "backend/app/models") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $fixtureRoot "backend/app/services") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $fixtureRoot "frontend/src") -Force | Out-Null
    Set-Content -Path (Join-Path $fixtureRoot "backend/app/models/sample.py") -Encoding UTF8 -Value 'created_at = mapped_column(DateTime(timezone=True))'
    Set-Content -Path (Join-Path $fixtureRoot "backend/app/services/sample.py") -Encoding UTF8 -Value 'now = datetime.now(UTC)'
    Set-Content -Path (Join-Path $fixtureRoot "frontend/src/sample.ts") -Encoding UTF8 -Value 'const d = new Date(apiValue)'

    & (Join-Path $repoRoot "scripts/check-time-usage.ps1") -Root $fixtureRoot -FailOnViolation | Out-Host
    if ($LASTEXITCODE -eq 0) {
        throw "Expected check-time-usage.ps1 to fail for risky fixtures."
    }
}
finally {
    if (Test-Path $fixtureRoot) {
        Remove-Item -LiteralPath $fixtureRoot -Recurse -Force
    }
}