param(
  [switch]$Clean,
  [switch]$CleanPlaywright,
  [switch]$SkipSync
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$BackendDir = Join-Path $RepoRoot "backend"
$AlembicIni = Join-Path $BackendDir "alembic.ini"
$AlembicDir = Join-Path $BackendDir "alembic"
$DocumentExtractionNotice = Join-Path $BackendDir "app\services\document_extraction\MARKITDOWN_NOTICE.txt"
$PlaywrightBrowsersDir = Join-Path $BackendDir "ms-playwright"
$PlaywrightHooksDir = Join-Path $RepoRoot "scripts\build\pyinstaller-hooks"

Push-Location $BackendDir
try {
  if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "build", "dist"
  }
  if ($CleanPlaywright) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "ms-playwright"
  }

  if (-not $SkipSync) {
    uv sync --dev
  }
  $env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersDir
  uv run python -m playwright install --only-shell chromium
  uv run pyinstaller `
    --noconfirm `
    --clean `
    --onedir `
    --debug noarchive `
    --name backend `
    --specpath build `
    --additional-hooks-dir $PlaywrightHooksDir `
    --hidden-import main `
    --hidden-import aiosqlite `
    --hidden-import openai `
    --hidden-import app.modules.professors.enrichment.public `
    --hidden-import app.services.document_extraction `
    --hidden-import lxml.etree `
    --collect-all mammoth `
    --collect-all pdfminer `
    --collect-all pypdf `
    --collect-all tldextract `
    --exclude-module markitdown `
    --exclude-module magika `
    --exclude-module onnxruntime `
    --exclude-module numpy `
    --exclude-module PIL `
    --exclude-module pypdfium2 `
    --add-data "$AlembicIni;." `
    --add-data "$AlembicDir;alembic" `
    --add-data "$DocumentExtractionNotice;licenses" `
    desktop_entry.py

  $PackagedBackendExe = Join-Path $BackendDir "dist\backend\backend.exe"
  $PackagedPlaywrightDriverDir = Join-Path $BackendDir "dist\backend\_internal\playwright\driver"
  foreach ($NodeName in @("node", "node.exe")) {
    $BundledNode = Join-Path $PackagedPlaywrightDriverDir $NodeName
    if (Test-Path $BundledNode) {
      throw "Playwright bundled Node must be excluded: $BundledNode"
    }
  }
  $PackagedPlaywrightCli = Join-Path $PackagedPlaywrightDriverDir "package\cli.js"
  if (-not (Test-Path $PackagedPlaywrightCli)) {
    throw "Playwright driver package is incomplete: $PackagedPlaywrightCli"
  }
  & $PackagedBackendExe --self-check
  & $PackagedBackendExe --document-self-check (Join-Path $BackendDir "test\fixtures\document_extraction")
} finally {
  Pop-Location
}
