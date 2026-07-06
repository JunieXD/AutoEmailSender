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

RepoRoot="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BackendDir="$RepoRoot/backend"
AlembicIni="$BackendDir/alembic.ini"
AlembicDir="$BackendDir/alembic"
PlaywrightBrowsersDir="$BackendDir/ms-playwright"

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
  --hidden-import main \
  --hidden-import aiosqlite \
  --collect-all markitdown \
  --collect-all mammoth \
  --collect-all pdfminer \
  --collect-all pdfplumber \
  --collect-all pypdf \
  --collect-all playwright \
  --collect-all tiktoken \
  --collect-submodules tiktoken_ext \
  --hidden-import tiktoken_ext.openai_public \
  --add-data "$AlembicIni:." \
  --add-data "$AlembicDir:alembic" \
  desktop_entry.py

PackagedBackendExe="$BackendDir/dist/backend/backend"
"$PackagedBackendExe" --self-check
