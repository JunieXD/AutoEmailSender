#!/usr/bin/env bash
set -euo pipefail

Clean=0
while (($#)); do
  case "$1" in
    --clean|-Clean)
      Clean=1
      shift
      ;;
    *)
      echo "用法: scripts/build-backend.sh [--clean]" >&2
      exit 2
      ;;
  esac
done

RepoRoot="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BackendDir="$RepoRoot/backend"
AlembicIni="$BackendDir/alembic.ini"
AlembicDir="$BackendDir/alembic"
DocumentExtractionNotice="$BackendDir/app/services/document_extraction/MARKITDOWN_NOTICE.txt"
PlaywrightBrowsersDir="$BackendDir/ms-playwright"
PlaywrightHooksDir="$RepoRoot/scripts/build/pyinstaller-hooks"

cd "$BackendDir"

if ((Clean)); then
  rm -rf build dist ms-playwright
fi

uv sync --dev
export PLAYWRIGHT_BROWSERS_PATH="$PlaywrightBrowsersDir"
uv run python -m playwright install --only-shell chromium
uv run pyinstaller \
  --noconfirm \
  --clean \
  --onedir \
  --debug noarchive \
  --name backend \
  --specpath build \
  --additional-hooks-dir "$PlaywrightHooksDir" \
  --hidden-import main \
  --hidden-import aiosqlite \
  --hidden-import app.services.document_extraction \
  --hidden-import lxml.etree \
  --collect-all mammoth \
  --collect-all pdfminer \
  --collect-all pypdf \
  --collect-all tldextract \
  --collect-all tiktoken \
  --collect-submodules tiktoken_ext \
  --hidden-import tiktoken_ext.openai_public \
  --exclude-module markitdown \
  --exclude-module magika \
  --exclude-module onnxruntime \
  --exclude-module numpy \
  --exclude-module PIL \
  --exclude-module pypdfium2 \
  --add-data "$AlembicIni:." \
  --add-data "$AlembicDir:alembic" \
  --add-data "$DocumentExtractionNotice:licenses" \
  desktop_entry.py

PackagedBackendExe="$BackendDir/dist/backend/backend"
PackagedPlaywrightDriverDir="$BackendDir/dist/backend/_internal/playwright/driver"
for BundledNode in "$PackagedPlaywrightDriverDir/node" "$PackagedPlaywrightDriverDir/node.exe"; do
  if [[ -e "$BundledNode" ]]; then
    echo "Playwright bundled Node must be excluded: $BundledNode" >&2
    exit 1
  fi
done
PackagedPlaywrightCli="$PackagedPlaywrightDriverDir/package/cli.js"
if [[ ! -f "$PackagedPlaywrightCli" ]]; then
  echo "Playwright driver package is incomplete: $PackagedPlaywrightCli" >&2
  exit 1
fi
"$PackagedBackendExe" --self-check
"$PackagedBackendExe" --document-self-check "$BackendDir/test/fixtures/document_extraction"
