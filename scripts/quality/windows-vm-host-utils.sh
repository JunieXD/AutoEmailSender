#!/usr/bin/env bash

wait_for_windows_vm_ready() {
  local vm_name="$1"
  local guest_probe_path="$2"
  local timeout_seconds="$3"
  local interval_seconds="$4"
  local maximum_attempts

  if ((timeout_seconds <= 0 || interval_seconds <= 0)); then
    echo "Windows VM readiness timeout and interval must be positive." >&2
    return 2
  fi
  maximum_attempts=$(((timeout_seconds + interval_seconds - 1) / interval_seconds))

  local attempt
  for ((attempt = 1; attempt <= maximum_attempts; attempt += 1)); do
    if prlctl exec "$vm_name" --current-user powershell.exe \
      -NoLogo \
      -NoProfile \
      -Command "if (-not (Test-Path -LiteralPath '$guest_probe_path')) { exit 1 }" \
      >/dev/null 2>&1
    then
      echo "Parallels Tools and shared-folder mapping are ready."
      return 0
    fi
    if ((attempt == 1 || attempt % 10 == 0)); then
      echo "Waiting for Parallels guest readiness (${attempt}/${maximum_attempts})..."
    fi
    if ((attempt < maximum_attempts)); then
      sleep "$interval_seconds"
    fi
  done
  return 1
}
