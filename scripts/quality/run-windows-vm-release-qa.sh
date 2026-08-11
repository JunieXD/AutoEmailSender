#!/usr/bin/env bash

set -euo pipefail

usage() {
  echo "Usage: $0 [--force-full] [--quick|--prerelease-certification|--candidate-admission|--harness-rehearsal]" >&2
  echo "          [--candidate-installer PATH] [--candidate-installer-sha256 HEX]" >&2
  echo "          [--candidate-manifest PATH] [--candidate-run-id N]" >&2
  echo "          [--previous-installer PATH] [--previous-installer-sha256 HEX]" >&2
  echo "          [--normal-soak] [--seeded-chaos]" >&2
  echo "          [--normal-soak-seconds N] [--seeded-chaos-seconds N] [--seed N]" >&2
  echo "          [--inject-interruption-after-previous-install]" >&2
  echo "          [--require-recovered-stale-state]" >&2
}

force_full=0
qa_mode="release"
run_normal_soak=0
run_seeded_chaos=0
normal_soak_seconds=""
seeded_chaos_seconds=""
seeded_chaos_seed=20260810
previous_installer=""
previous_installer_sha256=""
candidate_installer=""
candidate_installer_sha256=""
candidate_manifest=""
candidate_run_id=""
inject_interruption_after_previous_install=0
require_recovered_stale_state=0
while (($#)); do
  case "$1" in
    --force-full)
      force_full=1
      ;;
    --quick)
      qa_mode="quick"
      ;;
    --prerelease-certification)
      qa_mode="prerelease"
      ;;
    --candidate-admission)
      qa_mode="candidate-admission"
      ;;
    --harness-rehearsal)
      qa_mode="harness-rehearsal"
      ;;
    --inject-interruption-after-previous-install)
      inject_interruption_after_previous_install=1
      ;;
    --require-recovered-stale-state)
      require_recovered_stale_state=1
      ;;
    --normal-soak)
      run_normal_soak=1
      ;;
    --seeded-chaos)
      run_seeded_chaos=1
      ;;
    --normal-soak-seconds)
      normal_soak_seconds="${2:-}"
      shift
      ;;
    --seeded-chaos-seconds)
      seeded_chaos_seconds="${2:-}"
      shift
      ;;
    --seed)
      seeded_chaos_seed="${2:-}"
      shift
      ;;
    --previous-installer)
      previous_installer="${2:-}"
      shift
      ;;
    --previous-installer-sha256)
      previous_installer_sha256="${2:-}"
      shift
      ;;
    --candidate-installer)
      candidate_installer="${2:-}"
      shift
      ;;
    --candidate-installer-sha256)
      candidate_installer_sha256="${2:-}"
      shift
      ;;
    --candidate-manifest)
      candidate_manifest="${2:-}"
      shift
      ;;
    --candidate-run-id)
      candidate_run_id="${2:-}"
      shift
      ;;
    *)
      usage
      exit 2
      ;;
  esac
  shift
done

formal_qa=0
packaged_preflight=0
packaged_qa=0
exact_candidate_qa=0
normal_soak_minimum=86400
seeded_chaos_minimum=28800
if [[ "$qa_mode" == "release" || "$qa_mode" == "prerelease" ]]; then formal_qa=1; fi
if [[ "$qa_mode" == "candidate-admission" || "$qa_mode" == "harness-rehearsal" ]]; then packaged_preflight=1; fi
if ((formal_qa || packaged_preflight)); then packaged_qa=1; fi
if ((formal_qa)) || [[ "$qa_mode" == "candidate-admission" ]]; then exact_candidate_qa=1; fi
if [[ "$qa_mode" == "prerelease" ]]; then
  normal_soak_minimum=300
  seeded_chaos_minimum=300
fi
if [[ -z "$normal_soak_seconds" ]]; then normal_soak_seconds="$normal_soak_minimum"; fi
if [[ -z "$seeded_chaos_seconds" ]]; then seeded_chaos_seconds="$seeded_chaos_minimum"; fi

if [[ "$qa_mode" == "quick" ]] && ((run_normal_soak || run_seeded_chaos)); then
  echo "--quick 不能与长稳认证参数一起使用。" >&2
  exit 2
fi
if ((packaged_preflight && (run_normal_soak || run_seeded_chaos))); then
  echo "快速 packaged preflight 不能与长稳认证参数一起使用。" >&2
  exit 2
fi
if ((packaged_preflight && force_full)); then
  echo "packaged preflight 跳过源码/构建阶段，不接受 --force-full。" >&2
  exit 2
fi
if ((inject_interruption_after_previous_install)) && [[ "$qa_mode" != "harness-rehearsal" ]]; then
  echo "--inject-interruption-after-previous-install 只允许用于 --harness-rehearsal。" >&2
  exit 2
fi
if ((require_recovered_stale_state)) && [[ "$qa_mode" != "harness-rehearsal" ]]; then
  echo "--require-recovered-stale-state 只允许用于 --harness-rehearsal。" >&2
  exit 2
fi
if [[ "$qa_mode" == "quick" ]] && [[ -n "$previous_installer" || -n "$previous_installer_sha256" || -n "$candidate_installer" || -n "$candidate_installer_sha256" || -n "$candidate_manifest" || -n "$candidate_run_id" ]]; then
  echo "--quick 不接受正式候选或上一稳定版安装包参数。" >&2
  exit 2
fi
if ((packaged_qa)) && [[ -z "$previous_installer" ]]; then
  echo "Windows packaged QA 必须用 --previous-installer 指定上一稳定版真实 NSIS 安装包。" >&2
  exit 2
fi
if ((packaged_qa)) && [[ ! "$previous_installer_sha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
  echo "正式 Windows QA 必须用 --previous-installer-sha256 绑定公开稳定版摘要。" >&2
  exit 2
fi
if ((packaged_qa)) && [[ -z "$candidate_installer" ]]; then
  echo "正式 Windows QA 必须用 --candidate-installer 指定候选 workflow 的确切 NSIS 安装包。" >&2
  exit 2
fi
if ((packaged_qa)) && [[ ! "$candidate_installer_sha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
  echo "正式 Windows QA 必须用 --candidate-installer-sha256 绑定候选清单摘要。" >&2
  exit 2
fi
if ((exact_candidate_qa)) && [[ -z "$candidate_manifest" ]]; then
  echo "精确候选 Windows QA 必须用 --candidate-manifest 指定同一 workflow 的候选 manifest。" >&2
  exit 2
fi
if ((exact_candidate_qa)) && [[ ! "$candidate_run_id" =~ ^[1-9][0-9]*$ ]]; then
  echo "正式 Windows QA 必须用 --candidate-run-id 绑定正整数 workflow run ID。" >&2
  exit 2
fi
if [[ "$qa_mode" == "harness-rehearsal" ]] && [[ -n "$candidate_manifest" || -n "$candidate_run_id" ]]; then
  echo "harness rehearsal 不得绑定已经失效的 candidate manifest/run ID。" >&2
  exit 2
fi
if [[ -n "$previous_installer" && ! -f "$previous_installer" ]]; then
  echo "上一稳定版安装包不存在: $previous_installer" >&2
  exit 2
fi
if [[ -n "$candidate_installer" && ! -f "$candidate_installer" ]]; then
  echo "候选 Windows 安装包不存在: $candidate_installer" >&2
  exit 2
fi
if [[ -n "$candidate_manifest" && ! -f "$candidate_manifest" ]]; then
  echo "候选认证清单不存在: $candidate_manifest" >&2
  exit 2
fi
if [[ ! "$normal_soak_seconds" =~ ^[0-9]+$ ]] || ((normal_soak_seconds < normal_soak_minimum)); then
  echo "--normal-soak-seconds 在 $qa_mode 模式下至少为 $normal_soak_minimum。" >&2
  exit 2
fi
if [[ ! "$seeded_chaos_seconds" =~ ^[0-9]+$ ]] || ((seeded_chaos_seconds < seeded_chaos_minimum)); then
  echo "--seeded-chaos-seconds 在 $qa_mode 模式下至少为 $seeded_chaos_minimum。" >&2
  exit 2
fi
if [[ ! "$seeded_chaos_seed" =~ ^-?[0-9]+$ ]]; then
  echo "--seed 必须是整数。" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
target_revision="$(git -C "$repo_root" rev-parse HEAD)"
current_app_version="$(cd "$repo_root" && node -p "require('./desktop/package.json').version")"
if [[ "$qa_mode" == "prerelease" && ! "$current_app_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+-(alpha|beta|rc)\.[0-9A-Za-z-]+([.][0-9A-Za-z-]+)*$ ]]; then
  echo "测试版 Windows 认证要求 alpha、beta 或 rc 版本；当前为 $current_app_version。" >&2
  exit 2
fi
if [[ "$qa_mode" == "release" && ! "$current_app_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "稳定版 Windows 认证只接受 x.y.z；当前为 $current_app_version。" >&2
  exit 2
fi
vm_name="${AUTO_EMAIL_SENDER_WINDOWS_VM_NAME:-Windows 11}"
guest_checkout="${AUTO_EMAIL_SENDER_WINDOWS_QA_CHECKOUT:-C:\Users\junie\Projects\AutoEmailSender-Windows-QA}"
host_transfer_dir="${AUTO_EMAIL_SENDER_WINDOWS_QA_HOST_TRANSFER_DIR:-$HOME/Parallels Shared}"
guest_transfer_dir="${AUTO_EMAIL_SENDER_WINDOWS_QA_GUEST_TRANSFER_DIR:-Z:}"
guest_transfer_dir="${guest_transfer_dir//\\//}"
guest_transfer_dir="${guest_transfer_dir%/}"
expected_previous_version=""
if ((packaged_qa)); then
  if ! previous_tag="$(node "$repo_root/scripts/release/prerelease-contract.mjs" latest-stable --repo-root "$repo_root" --ref HEAD)"; then
    echo "无法从当前 SHA 推导上一稳定版 tag。" >&2
    exit 1
  fi
  expected_previous_version="${previous_tag#v}"
  if [[ ! "$expected_previous_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "上一稳定版 tag 格式无效: $previous_tag" >&2
    exit 1
  fi
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This host runner requires macOS with Parallels Desktop." >&2
  exit 1
fi
if [[ -n "$previous_installer" ]]; then
  actual_previous_sha256="$(/usr/bin/shasum -a 256 "$previous_installer" | /usr/bin/awk '{print $1}')"
  actual_previous_sha256="$(printf '%s' "$actual_previous_sha256" | tr '[:upper:]' '[:lower:]')"
  expected_previous_sha256="$(printf '%s' "$previous_installer_sha256" | tr '[:upper:]' '[:lower:]')"
  if [[ "$actual_previous_sha256" != "$expected_previous_sha256" ]]; then
    echo "上一稳定版 Windows 安装包 SHA-256 与期望值不一致。" >&2
    exit 1
  fi
fi
if [[ -n "$candidate_installer" ]]; then
  actual_candidate_sha256="$(/usr/bin/shasum -a 256 "$candidate_installer" | /usr/bin/awk '{print $1}')"
  actual_candidate_sha256="$(printf '%s' "$actual_candidate_sha256" | tr '[:upper:]' '[:lower:]')"
  expected_candidate_sha256="$(printf '%s' "$candidate_installer_sha256" | tr '[:upper:]' '[:lower:]')"
  if [[ "$actual_candidate_sha256" != "$expected_candidate_sha256" ]]; then
    echo "Windows 候选安装包 SHA-256 与候选清单不一致。" >&2
    exit 1
  fi
fi

if ! command -v prlctl >/dev/null 2>&1; then
  echo "prlctl was not found. Install or repair Parallels Desktop first." >&2
  exit 1
fi

if [[ -n "$(git -C "$repo_root" status --porcelain)" ]]; then
  echo "Windows release QA only tests a completely clean committed worktree." >&2
  exit 1
fi

if ((exact_candidate_qa)); then
  node "$repo_root/scripts/release/release-candidate.mjs" asset \
    --manifest "$candidate_manifest" \
    --platform windows \
    --release-sha "$target_revision" \
    --run-id "$candidate_run_id" \
    --version "$current_app_version" \
    --asset "$candidate_installer"
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
previous_installer_name="${previous_installer##*/}"
candidate_installer_name="${candidate_installer##*/}"
candidate_manifest_name="${candidate_manifest##*/}"
if [[ -z "$previous_installer_name" ]]; then previous_installer_name="previous-installer-unused"; fi
if [[ -z "$candidate_installer_name" ]]; then candidate_installer_name="candidate-installer-unused"; fi
if [[ -z "$candidate_manifest_name" ]]; then candidate_manifest_name="candidate-manifest-unused"; fi
transfer_directory_path="$(mktemp -d "$host_transfer_dir/.auto-email-sender-windows-qa.XXXXXX")"
transfer_directory_name="${transfer_directory_path##*/}"
guest_transfer_directory_path="$guest_transfer_dir/$transfer_directory_name"
bundle_path="$transfer_directory_path/$bundle_name"
runner_path="$transfer_directory_path/$runner_name"
probe_path="$transfer_directory_path/$probe_name"
previous_installer_transfer_path="$transfer_directory_path/$previous_installer_name"
candidate_installer_transfer_path="$transfer_directory_path/$candidate_installer_name"
candidate_manifest_transfer_path="$transfer_directory_path/$candidate_manifest_name"
guest_runner_path="$guest_transfer_directory_path/$runner_name"
guest_bundle_path="$guest_transfer_directory_path/$bundle_name"
guest_probe_path="$guest_transfer_directory_path/$probe_name"
guest_previous_installer_path="$guest_transfer_directory_path/$previous_installer_name"
guest_candidate_installer_path="$guest_transfer_directory_path/$candidate_installer_name"
guest_candidate_manifest_path="$guest_transfer_directory_path/$candidate_manifest_name"
suspend_vm_on_exit=false

cleanup() {
  local exit_status=$?
  trap - EXIT
  set +e
  rm -f -- \
    "$bundle_path" \
    "$runner_path" \
    "$probe_path" \
    "$previous_installer_transfer_path" \
    "$candidate_installer_transfer_path" \
    "$candidate_manifest_transfer_path"
  rmdir "$transfer_directory_path" 2>/dev/null || true
  if [[ "$suspend_vm_on_exit" == "true" ]]; then
    local cleanup_vm_status
    cleanup_vm_status="$(prlctl status "$vm_name" 2>&1 || true)"
    if [[ "$cleanup_vm_status" == *"running"* ]]; then
      echo "Restoring Parallels VM to suspended state: $vm_name"
      if ! prlctl suspend "$vm_name"; then
        echo "Unable to restore Parallels VM to suspended state: $vm_name" >&2
        if [[ "$exit_status" -eq 0 ]]; then
          exit_status=1
        fi
      fi
    fi
  fi
  exit "$exit_status"
}
trap cleanup EXIT

vm_status="$(prlctl status "$vm_name" 2>&1 || true)"
if [[ "$vm_status" != *"running"* ]]; then
  suspend_vm_on_exit=true
  echo "Starting Parallels VM: $vm_name"
  prlctl start "$vm_name"
fi

touch "$probe_path"
if ! prlctl exec "$vm_name" --current-user powershell.exe \
  -NoLogo \
  -NoProfile \
  -Command "if (-not (Test-Path -LiteralPath '$guest_probe_path')) { exit 1 }"
then
  echo "The Parallels shared-folder mapping is unavailable." >&2
  echo "Host path: $host_transfer_dir" >&2
  echo "Guest path: $guest_transfer_dir" >&2
  echo "Configure the mapping or set the AUTO_EMAIL_SENDER_WINDOWS_QA_*_TRANSFER_DIR variables." >&2
  exit 1
fi

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
if [[ -n "$previous_installer" ]]; then
  cp "$previous_installer" "$previous_installer_transfer_path"
fi
if [[ -n "$candidate_installer" ]]; then
  cp "$candidate_installer" "$candidate_installer_transfer_path"
fi
if ((exact_candidate_qa)); then
  cp "$candidate_manifest" "$candidate_manifest_transfer_path"
fi

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
if [[ -n "$previous_installer" ]]; then
  guest_args+=(
    -PreviousInstallerPath "$guest_previous_installer_path"
    -ExpectedPreviousVersion "$expected_previous_version"
    -ExpectedPreviousPackageSha256 "$previous_installer_sha256"
  )
fi
if [[ -n "$candidate_installer" ]]; then
  guest_args+=(
    -CandidateInstallerPath "$guest_candidate_installer_path"
    -ExpectedCandidatePackageSha256 "$candidate_installer_sha256"
  )
fi
if ((exact_candidate_qa)); then
  guest_args+=(
    -CandidateManifestPath "$guest_candidate_manifest_path"
    -ExpectedCandidateRunId "$candidate_run_id"
  )
fi
if ((force_full)); then
  guest_args+=(-ForceFull)
fi
if ((inject_interruption_after_previous_install)); then
  guest_args+=(-InjectInterruptionAfterPreviousInstall)
fi
if ((require_recovered_stale_state)); then
  guest_args+=(-RequireRecoveredStaleState)
fi
if ((run_normal_soak)); then
  guest_args+=(-RunNormalSoak -NormalSoakDurationSeconds "$normal_soak_seconds")
fi
if ((run_seeded_chaos)); then
  guest_args+=(
    -RunSeededChaos
    -SeededChaosDurationSeconds "$seeded_chaos_seconds"
    -SeededChaosSeed "$seeded_chaos_seed"
  )
fi
prlctl exec "$vm_name" --current-user powershell.exe "${guest_args[@]}"
