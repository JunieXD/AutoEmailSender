param(
  [switch]$Clean
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $RepoRoot "backend"
$AlembicIni = Join-Path $BackendDir "alembic.ini"
$AlembicDir = Join-Path $BackendDir "alembic"
$DocumentExtractionNotice = Join-Path $BackendDir "app\services\document_extraction\MARKITDOWN_NOTICE.txt"
$PlaywrightBrowsersDir = Join-Path $BackendDir "ms-playwright"

Push-Location $BackendDir
try {
  if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "build", "dist", "ms-playwright"
  }

  uv sync --dev
  $env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersDir
  uv run python -m playwright install --only-shell chromium
  uv run pyinstaller `
    --noconfirm `
    --clean `
    --onedir `
    --debug noarchive `
    --name backend `
    --specpath build `
    --hidden-import main `
    --hidden-import aiosqlite `
    --hidden-import app.services.document_extraction `
    --hidden-import lxml.etree `
    --collect-all mammoth `
    --collect-all pdfminer `
    --collect-all pypdf `
    --collect-all playwright `
    --collect-all tldextract `
    --collect-all tiktoken `
    --collect-submodules tiktoken_ext `
    --hidden-import tiktoken_ext.openai_public `
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
  & $PackagedBackendExe --self-check
  & $PackagedBackendExe --document-self-check (Join-Path $BackendDir "test\fixtures\document_extraction")
} finally {
  Pop-Location
}
