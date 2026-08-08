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

function Assert-NativeSuccess {
  param([Parameter(Mandatory = $true)][string]$Operation)

  if ($LASTEXITCODE -ne 0) {
    throw "$Operation failed with exit code $LASTEXITCODE"
  }
}

function Remove-CleanBuildDirectory {
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }
  try {
    Remove-Item -Recurse -Force -ErrorAction Stop -LiteralPath $Path
  } catch {
    throw "Unable to clean build output '$Path'. Stop processes using this directory and retry. $($_.Exception.Message)"
  }
  if (Test-Path -LiteralPath $Path) {
    throw "Build output still exists after cleanup: $Path"
  }
}

Push-Location $BackendDir
try {
  if ($Clean) {
    Remove-CleanBuildDirectory -Path (Join-Path $BackendDir "build")
    Remove-CleanBuildDirectory -Path (Join-Path $BackendDir "dist")
  }
  if ($CleanPlaywright) {
    Remove-CleanBuildDirectory -Path $PlaywrightBrowsersDir
  }

  if (-not $SkipSync) {
    uv sync --dev
    Assert-NativeSuccess "backend dependency sync"
  }
  $env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsersDir
  uv run python -m playwright install --only-shell chromium
  Assert-NativeSuccess "Playwright Chromium installation"
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
  Assert-NativeSuccess "backend PyInstaller build"

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
  Assert-NativeSuccess "packaged backend self-check"
  & $PackagedBackendExe --document-self-check (Join-Path $BackendDir "test\fixtures\document_extraction")
  Assert-NativeSuccess "packaged backend document self-check"
} finally {
  Pop-Location
}
