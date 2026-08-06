$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$BackendDir = Join-Path $RepoRoot "backend"
$PlaywrightBrowsersDir = Join-Path $BackendDir "ms-playwright"

Push-Location $BackendDir
try {
  uv sync --dev
  $env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersDir
  uv run python -m playwright install --only-shell chromium
} finally {
  Pop-Location
}
