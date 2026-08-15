#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The native Vision OCR helper can only be built on macOS." >&2
  exit 1
fi

RepoRoot="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SourcePath="$RepoRoot/backend/native/ocr/macos/email_ocr.swift"
OutputDir="$RepoRoot/backend/build/native-ocr"
OutputPath="$OutputDir/email-ocr"

mkdir -p "$OutputDir"
xcrun swiftc \
  -O \
  -framework Vision \
  -framework Foundation \
  "$SourcePath" \
  -o "$OutputPath"
codesign --force --sign - "$OutputPath"
echo "Built $OutputPath"
