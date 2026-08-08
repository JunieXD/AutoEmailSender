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
      echo "用法: scripts/build-cli.sh [--clean]" >&2
      exit 2
      ;;
  esac
done

RepoRoot="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CliDir="$RepoRoot/cli"
TargetArchArgs=()

if [[ "$(uname -s)" == "Darwin" ]]; then
  TargetArchArgs=(--target-arch arm64)
fi

cd "$CliDir"
if ((Clean)); then
  rm -rf "$CliDir/build" "$CliDir/dist"
fi

uv sync --dev
BuildIdentityHook="$CliDir/build/generated/cli_build_identity_hook.py"
uv run python "$RepoRoot/scripts/build/generate_cli_build_identity.py" \
  --repo-root "$RepoRoot" \
  --output "$BuildIdentityHook"
uv run pyinstaller \
  --noconfirm \
  --clean \
  --onedir \
  --console \
  --name auto-email-sender \
  --distpath "$CliDir/dist" \
  --workpath "$CliDir/build/work" \
  --specpath "$CliDir/build" \
  --paths "$CliDir/src" \
  --runtime-hook "$BuildIdentityHook" \
  "${TargetArchArgs[@]}" \
  "$CliDir/src/auto_email_sender_cli/__main__.py"

CliExecutable="$CliDir/dist/auto-email-sender/auto-email-sender"
uv run python "$RepoRoot/scripts/build/verify_cli_binary.py" \
  --executable "$CliExecutable"
