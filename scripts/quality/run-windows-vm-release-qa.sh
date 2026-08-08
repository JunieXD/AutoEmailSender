#!/usr/bin/env bash

set -euo pipefail

usage() {
  echo "Usage: $0 [--force-full] [--quick]" >&2
}

force_full=0
qa_mode="release"
while (($#)); do
  case "$1" in
    --force-full)
      force_full=1
      ;;
    --quick)
      qa_mode="quick"
      ;;
    *)
      usage
      exit 2
      ;;
  esac
  shift
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
vm_name="${AUTO_EMAIL_SENDER_WINDOWS_VM_NAME:-Windows 11}"
guest_checkout="${AUTO_EMAIL_SENDER_WINDOWS_QA_CHECKOUT:-C:\Users\junie\Projects\AutoEmailSender-Windows-QA}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This host runner requires macOS with Parallels Desktop." >&2
  exit 1
fi

if ! command -v prlctl >/dev/null 2>&1; then
  echo "prlctl was not found. Install or repair Parallels Desktop first." >&2
  exit 1
fi

if ! git -C "$repo_root" diff --quiet || ! git -C "$repo_root" diff --cached --quiet; then
  echo "Windows release QA only tests committed code. Commit tracked changes first." >&2
  exit 1
fi

vm_status="$(prlctl status "$vm_name" 2>&1 || true)"
if [[ "$vm_status" != *"running"* ]]; then
  echo "Starting Parallels VM: $vm_name"
  prlctl start "$vm_name"
fi

desktop_dir="$HOME/Desktop"
transfer_id="$$"
bundle_name="AutoEmailSender-Windows-QA-$transfer_id.bundle"
runner_name="run-windows-release-qa-$transfer_id.ps1"
bundle_path="$desktop_dir/$bundle_name"
runner_path="$desktop_dir/$runner_name"
target_revision="$(git -C "$repo_root" rev-parse HEAD)"

cleanup() {
  rm -f -- "$bundle_path" "$runner_path"
}
trap cleanup EXIT

guest_revision="$({
  prlctl exec "$vm_name" --current-user powershell.exe \
    -NoLogo \
    -NoProfile \
    -Command "if (Test-Path -LiteralPath '$guest_checkout\\.git') { git -C '$guest_checkout' rev-parse HEAD }"
} 2>/dev/null | tr -d '\r' | sed -nE 's/.*([0-9a-f]{40}).*/\1/p' | tail -n 1)"

if [[ "$guest_revision" == "$target_revision" ]]; then
  echo "Windows checkout already has $target_revision; skipping Git bundle transfer."
elif [[ -n "$guest_revision" ]] && git -C "$repo_root" merge-base --is-ancestor "$guest_revision" "$target_revision"; then
  echo "Creating incremental Git bundle from $guest_revision to $target_revision."
  git -C "$repo_root" bundle create "$bundle_path" HEAD "^$guest_revision"
  git -C "$repo_root" bundle verify "$bundle_path"
else
  echo "Creating full Git bundle for $target_revision."
  git -C "$repo_root" bundle create "$bundle_path" HEAD
  git -C "$repo_root" bundle verify "$bundle_path"
fi
cp "$script_dir/run-windows-release-qa.ps1" "$runner_path"

echo "Running Windows $qa_mode QA for ${target_revision:0:12} in $vm_name"
guest_args=(
  -NoLogo
  -NoProfile
  -ExecutionPolicy Bypass
  -File "Z:/Desktop/$runner_name"
  -CheckoutPath "$guest_checkout"
  -ExpectedRevision "$target_revision"
  -Mode "$qa_mode"
)
if [[ -f "$bundle_path" ]]; then
  guest_args+=(-BundlePath "Z:/Desktop/$bundle_name")
fi
if [[ -n "$guest_revision" && "$guest_revision" != "$target_revision" ]]; then
  guest_args+=(-PreviousRevision "$guest_revision")
fi
if ((force_full)); then
  guest_args+=(-ForceFull)
fi
prlctl exec "$vm_name" --current-user powershell.exe "${guest_args[@]}"
