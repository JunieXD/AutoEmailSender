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
source "$script_dir/windows-vm-host-utils.sh"
vm_name="${AUTO_EMAIL_SENDER_WINDOWS_VM_NAME:-Windows 11}"
guest_checkout="${AUTO_EMAIL_SENDER_WINDOWS_QA_CHECKOUT:-C:\Users\junie\Projects\AutoEmailSender-Windows-QA}"
host_transfer_dir="${AUTO_EMAIL_SENDER_WINDOWS_QA_HOST_TRANSFER_DIR:-$HOME/Parallels Shared}"
guest_transfer_dir="${AUTO_EMAIL_SENDER_WINDOWS_QA_GUEST_TRANSFER_DIR:-Z:}"
guest_transfer_dir="${guest_transfer_dir//\\//}"
guest_transfer_dir="${guest_transfer_dir%/}"
ready_timeout_seconds="${AUTO_EMAIL_SENDER_WINDOWS_QA_READY_TIMEOUT_SECONDS:-90}"
ready_interval_seconds="${AUTO_EMAIL_SENDER_WINDOWS_QA_READY_INTERVAL_SECONDS:-3}"

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

if [[ ! -d "$host_transfer_dir" || ! -w "$host_transfer_dir" ]]; then
  echo "The Parallels transfer directory is missing or not writable: $host_transfer_dir" >&2
  echo "Configure the shared folder or set AUTO_EMAIL_SENDER_WINDOWS_QA_HOST_TRANSFER_DIR." >&2
  exit 1
fi

if [[ -z "$guest_transfer_dir" ]]; then
  echo "AUTO_EMAIL_SENDER_WINDOWS_QA_GUEST_TRANSFER_DIR must not be empty." >&2
  exit 1
fi

transfer_id="$$"
bundle_name="AutoEmailSender-Windows-QA-$transfer_id.bundle"
runner_name="run-windows-release-qa-$transfer_id.ps1"
probe_name=".auto-email-sender-windows-qa-$transfer_id.probe"
bundle_path="$host_transfer_dir/$bundle_name"
runner_path="$host_transfer_dir/$runner_name"
probe_path="$host_transfer_dir/$probe_name"
guest_runner_path="$guest_transfer_dir/$runner_name"
guest_bundle_path="$guest_transfer_dir/$bundle_name"
guest_probe_path="$guest_transfer_dir/$probe_name"
vm_started_by_runner=0

cleanup() {
  local exit_code="$?"
  rm -f -- "$bundle_path" "$runner_path" "$probe_path"
  if ((vm_started_by_runner)); then
    echo "Restoring the Parallels VM to its initial stopped state."
    if ! prlctl stop "$vm_name" >/dev/null; then
      echo "Warning: failed to stop Parallels VM $vm_name after QA." >&2
    fi
  fi
  return "$exit_code"
}
trap cleanup EXIT

vm_status="$(prlctl status "$vm_name" 2>&1 || true)"
if [[ "$vm_status" != *"running"* ]]; then
  echo "Starting Parallels VM: $vm_name"
  prlctl start "$vm_name"
  vm_started_by_runner=1
fi

touch "$probe_path"
if ! wait_for_windows_vm_ready \
  "$vm_name" \
  "$guest_probe_path" \
  "$ready_timeout_seconds" \
  "$ready_interval_seconds"
then
  echo "The Parallels guest or shared-folder mapping did not become ready within ${ready_timeout_seconds}s." >&2
  echo "Host path: $host_transfer_dir" >&2
  echo "Guest path: $guest_transfer_dir" >&2
  echo "Configure the mapping or set the AUTO_EMAIL_SENDER_WINDOWS_QA_*_TRANSFER_DIR variables." >&2
  exit 1
fi

target_revision="$(git -C "$repo_root" rev-parse HEAD)"

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
  -File "$guest_runner_path"
  -CheckoutPath "$guest_checkout"
  -ExpectedRevision "$target_revision"
  -Mode "$qa_mode"
)
if [[ -f "$bundle_path" ]]; then
  guest_args+=(-BundlePath "$guest_bundle_path")
fi
if [[ -n "$guest_revision" && "$guest_revision" != "$target_revision" ]]; then
  guest_args+=(-PreviousRevision "$guest_revision")
fi
if ((force_full)); then
  guest_args+=(-ForceFull)
fi
prlctl exec "$vm_name" --current-user powershell.exe "${guest_args[@]}"
