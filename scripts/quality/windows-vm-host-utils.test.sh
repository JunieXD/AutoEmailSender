#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
temp_root="$(mktemp -d)"
calls_path="$temp_root/prlctl-calls"

cleanup() {
  rm -rf -- "$temp_root"
}
trap cleanup EXIT

source "$script_dir/windows-vm-host-utils.sh"

prlctl() {
  local calls=0
  if [[ -f "$calls_path" ]]; then
    calls="$(<"$calls_path")"
  fi
  calls=$((calls + 1))
  printf '%s\n' "$calls" > "$calls_path"
  ((calls >= 3))
}

sleep() {
  :
}

wait_for_windows_vm_ready "Windows 11" "Z:/.probe" 9 3 >/dev/null
if [[ "$(<"$calls_path")" != "3" ]]; then
  echo "readiness probe should retry until the third successful attempt" >&2
  exit 1
fi

prlctl() {
  return 1
}
if wait_for_windows_vm_ready "Windows 11" "Z:/.probe" 6 3 >/dev/null; then
  echo "readiness probe should fail after the bounded attempts" >&2
  exit 1
fi
