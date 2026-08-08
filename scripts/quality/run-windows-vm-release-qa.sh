#!/usr/bin/env bash

set -euo pipefail

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

cleanup() {
  rm -f -- "$bundle_path" "$runner_path"
}
trap cleanup EXIT

git -C "$repo_root" bundle create "$bundle_path" HEAD
git -C "$repo_root" bundle verify "$bundle_path"
cp "$script_dir/run-windows-release-qa.ps1" "$runner_path"

echo "Running Windows release QA for $(git -C "$repo_root" rev-parse --short=12 HEAD) in $vm_name"
prlctl exec "$vm_name" --current-user powershell.exe \
  -NoLogo \
  -NoProfile \
  -ExecutionPolicy Bypass \
  -File "Z:/Desktop/$runner_name" \
  -BundlePath "Z:/Desktop/$bundle_name" \
  -CheckoutPath "$guest_checkout"
