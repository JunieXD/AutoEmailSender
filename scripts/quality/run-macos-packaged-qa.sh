#!/usr/bin/env bash
set -euo pipefail

Scenario=""
ExpectedRevision=""
Seed=""
DurationSeconds=""
ArtifactsDir=""
AppBundle=""
DmgPath=""
PreviousDmgPath=""
ExpectedPreviousVersion=""
ExpectedDmgSha256=""
ExpectedPreviousDmgSha256=""
CandidateManifestPath=""
CandidateRunId=""
Build=0
Certification=0
PrereleaseCertification=0
DevelopmentSmoke=0
SkipBrowserProbe=0
KeepInstalledApp=0
DedicatedTestAccount=0
SystemSleepWake=0
NativeSleepRequested=0
QA_PATH_MARKER="auto-email-sender-packaged-qa"
DATABASE_NAME="auto_email_sender.db"

usage() {
  cat <<'EOF'
用法: scripts/quality/run-macos-packaged-qa.sh \
  --scenario lifecycle|normal-soak|seeded-chaos \
  (--certification | --prerelease-certification | --development-smoke) \
  [--expected-revision <40位SHA>] \
  [--build] [--dmg <path> | --app-bundle <path>] \
  [--expected-dmg-sha256 <候选DMG的64位SHA-256>] \
  [--previous-dmg <上一稳定版路径>] \
  [--expected-previous-dmg-sha256 <上一稳定版DMG的64位SHA-256>] \
  [--candidate-manifest <release-candidate.json>] [--candidate-run-id <正整数>] \
  [--dedicated-test-account] \
  [--artifacts-dir <path>] [--duration-seconds <seconds>] [--seed <integer>] \
  [--skip-browser-probe] [--keep-installed-app] [--system-sleep-wake]

正式认证要求 clean committed SHA；全部正式场景都要求从 DMG 挂载并复制真实 app bundle。
--development-smoke 只用于实现验证，生成的报告不会被标记为正式认证证据。
EOF
}

while (($#)); do
  case "$1" in
    --scenario)
      Scenario="${2:-}"
      shift 2
      ;;
    --expected-revision)
      ExpectedRevision="${2:-}"
      shift 2
      ;;
    --seed)
      Seed="${2:-}"
      shift 2
      ;;
    --duration-seconds)
      DurationSeconds="${2:-}"
      shift 2
      ;;
    --artifacts-dir)
      ArtifactsDir="${2:-}"
      shift 2
      ;;
    --app-bundle)
      AppBundle="${2:-}"
      shift 2
      ;;
    --dmg)
      DmgPath="${2:-}"
      shift 2
      ;;
    --previous-dmg)
      PreviousDmgPath="${2:-}"
      shift 2
      ;;
    --expected-dmg-sha256)
      ExpectedDmgSha256="${2:-}"
      shift 2
      ;;
    --expected-previous-dmg-sha256)
      ExpectedPreviousDmgSha256="${2:-}"
      shift 2
      ;;
    --candidate-manifest)
      CandidateManifestPath="${2:-}"
      shift 2
      ;;
    --candidate-run-id)
      CandidateRunId="${2:-}"
      shift 2
      ;;
    --build)
      Build=1
      shift
      ;;
    --certification)
      Certification=1
      shift
      ;;
    --prerelease-certification)
      PrereleaseCertification=1
      shift
      ;;
    --development-smoke)
      DevelopmentSmoke=1
      shift
      ;;
    --skip-browser-probe)
      SkipBrowserProbe=1
      shift
      ;;
    --keep-installed-app)
      KeepInstalledApp=1
      shift
      ;;
    --dedicated-test-account)
      DedicatedTestAccount=1
      shift
      ;;
    --system-sleep-wake)
      SystemSleepWake=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$Scenario" != "lifecycle" && "$Scenario" != "normal-soak" && "$Scenario" != "seeded-chaos" ]]; then
  echo "--scenario 必须是 lifecycle、normal-soak 或 seeded-chaos。" >&2
  exit 2
fi
if ((Certification + PrereleaseCertification + DevelopmentSmoke != 1)); then
  echo "必须且只能指定 --certification、--prerelease-certification 或 --development-smoke 之一。" >&2
  exit 2
fi
FormalCertification=$((Certification || PrereleaseCertification))
if [[ "$Scenario" == "seeded-chaos" && -z "$Seed" ]]; then
  echo "seeded-chaos 必须指定 --seed。" >&2
  exit 2
fi
if [[ -n "$AppBundle" && -n "$DmgPath" ]]; then
  echo "--app-bundle 与 --dmg 不能同时指定。" >&2
  exit 2
fi
if [[ "$Scenario" != "lifecycle" && -n "$PreviousDmgPath" ]]; then
  echo "--previous-dmg 只允许用于 lifecycle 场景。" >&2
  exit 2
fi
if [[ "$Scenario" != "lifecycle" && -n "$ExpectedPreviousDmgSha256" ]]; then
  echo "--expected-previous-dmg-sha256 只允许用于 lifecycle 场景。" >&2
  exit 2
fi
if [[ -z "$PreviousDmgPath" && -n "$ExpectedPreviousDmgSha256" ]]; then
  echo "--expected-previous-dmg-sha256 必须与 --previous-dmg 一起使用。" >&2
  exit 2
fi
if [[ -n "$ExpectedPreviousDmgSha256" && ! "$ExpectedPreviousDmgSha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
  echo "--expected-previous-dmg-sha256 必须是 64 位 SHA-256。" >&2
  exit 2
fi
if ((FormalCertification)) && [[ ! "$ExpectedRevision" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "正式认证必须指定 40 位 --expected-revision。" >&2
  exit 2
fi
if ((FormalCertification)) && [[ -z "$DmgPath" && Build -eq 0 ]]; then
  echo "macOS 正式认证的全部场景都必须使用 --dmg，或用 --build 生成并挂载 DMG。" >&2
  exit 2
fi
if ((FormalCertification && Build)); then
  echo "macOS 正式认证必须使用候选 workflow 的确切 DMG；--build 只允许 development smoke。" >&2
  exit 2
fi
if ((FormalCertification && Build == 0)) && [[ ! "$ExpectedDmgSha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
  echo "使用外部候选 DMG 正式认证时必须指定 64 位 --expected-dmg-sha256。" >&2
  exit 2
fi
if [[ -n "$CandidateManifestPath" && -z "$CandidateRunId" ]] || [[ -z "$CandidateManifestPath" && -n "$CandidateRunId" ]]; then
  echo "--candidate-manifest 与 --candidate-run-id 必须一起使用。" >&2
  exit 2
fi
if [[ -n "$CandidateRunId" && ! "$CandidateRunId" =~ ^[1-9][0-9]*$ ]]; then
  echo "--candidate-run-id 必须是正整数。" >&2
  exit 2
fi
if ((FormalCertification)) && [[ -z "$CandidateManifestPath" ]]; then
  echo "macOS 正式认证必须用 --candidate-manifest 和 --candidate-run-id 绑定候选 workflow。" >&2
  exit 2
fi
if ((FormalCertification)) && [[ "$Scenario" == "lifecycle" && -z "$PreviousDmgPath" ]]; then
  echo "macOS lifecycle 正式认证必须用 --previous-dmg 指定上一稳定版真实 DMG。" >&2
  exit 2
fi
if ((FormalCertification)) && [[ "$Scenario" == "lifecycle" && ! "$ExpectedPreviousDmgSha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
  echo "macOS lifecycle 正式认证必须用 --expected-previous-dmg-sha256 绑定上一稳定版公开 DMG 摘要。" >&2
  exit 2
fi
if ((FormalCertification)) && [[ "$Scenario" == "lifecycle" && $DedicatedTestAccount -eq 0 ]]; then
  echo "上一稳定版可能使用旧 updater/cache 路径；正式升级认证必须在专用 macOS 测试账户运行并显式指定 --dedicated-test-account。" >&2
  exit 2
fi
if ((FormalCertification && SkipBrowserProbe)); then
  echo "正式认证不能跳过真实 Playwright/Chromium 进程树验证。" >&2
  exit 2
fi

RepoRoot="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -n "$PreviousDmgPath" ]]; then
  if ! PreviousTag="$(node "$RepoRoot/scripts/release/prerelease-contract.mjs" latest-stable --repo-root "$RepoRoot" --ref HEAD)"; then
    echo "无法从当前 SHA 推导上一稳定版 tag。" >&2
    exit 1
  fi
  ExpectedPreviousVersion="${PreviousTag#v}"
  if [[ ! "$ExpectedPreviousVersion" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "上一稳定版 tag 格式无效: $PreviousTag" >&2
    exit 1
  fi
fi
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "此 runner 只能在 macOS 上运行。" >&2
  exit 1
fi
if ((FormalCertification)) && [[ "$(uname -m)" != "arm64" ]]; then
  echo "正式 macOS 认证要求 Apple Silicon arm64；当前为 $(uname -m)。" >&2
  exit 1
fi

NodeMajor="$(node -p 'process.versions.node.split(".")[0]')"
if [[ "$NodeMajor" != "24" ]]; then
  echo "需要 Node.js 24；当前为 $(node --version)。" >&2
  exit 1
fi
CurrentAppVersion="$(cd "$RepoRoot" && node -p "require('./desktop/package.json').version")"
if ((PrereleaseCertification)) && [[ ! "$CurrentAppVersion" =~ ^[0-9]+\.[0-9]+\.[0-9]+-(alpha|beta|rc)\.[0-9A-Za-z-]+([.][0-9A-Za-z-]+)*$ ]]; then
  echo "测试版认证要求 alpha、beta 或 rc 版本；当前为 $CurrentAppVersion。" >&2
  exit 2
fi
if ((Certification)) && [[ ! "$CurrentAppVersion" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "稳定版认证只接受 x.y.z；当前为 $CurrentAppVersion。" >&2
  exit 2
fi

CurrentRevision="$(git -C "$RepoRoot" rev-parse HEAD)"
if ((FormalCertification)); then
  CurrentRevisionLower="$(printf '%s' "$CurrentRevision" | tr '[:upper:]' '[:lower:]')"
  ExpectedRevisionLower="$(printf '%s' "$ExpectedRevision" | tr '[:upper:]' '[:lower:]')"
  if [[ "$CurrentRevisionLower" != "$ExpectedRevisionLower" ]]; then
    echo "当前 SHA $CurrentRevision 与 --expected-revision $ExpectedRevision 不一致。" >&2
    exit 1
  fi
  if [[ -n "$(git -C "$RepoRoot" status --porcelain)" ]]; then
    echo "正式认证要求完全干净的工作树；请先形成最终提交。" >&2
    exit 1
  fi
fi

if [[ -z "$ArtifactsDir" ]]; then
  ArtifactsDir="$(mktemp -d "${TMPDIR:-/tmp}/auto-email-sender-macos-qa.XXXXXX")"
else
  mkdir -p "$ArtifactsDir"
  ArtifactsDir="$(cd "$ArtifactsDir" && pwd)"
fi

if ((Build)); then
  if [[ -z "${SPARKLE_PUBLIC_ED_KEY:-}" ]]; then
    echo "构建 macOS bundle 前必须设置 SPARKLE_PUBLIC_ED_KEY；runner 不会输出其值。" >&2
    exit 1
  fi

  (
    cd "$RepoRoot/frontend"
    npm ci
    npm run build
  )
  (
    cd "$RepoRoot/backend"
    uv sync --dev
  )
  "$RepoRoot/scripts/build/build-backend.sh" --clean
  (
    cd "$RepoRoot/cli"
    uv sync --dev
  )
  "$RepoRoot/scripts/build/build-cli.sh" --clean
  (
    cd "$RepoRoot/desktop"
    npm ci
    npm run typecheck
    npm run test
    npm run dist:mac
  )

  BuiltDmgs=()
  while IFS= read -r candidate; do
    BuiltDmgs+=("$candidate")
  done < <(find "$RepoRoot/desktop/release" -maxdepth 1 -type f -name 'AutoEmailSender-*-arm64.dmg' -print | sort)
  if ((${#BuiltDmgs[@]} != 1)); then
    echo "期望恰好一个 arm64 DMG，实际找到 ${#BuiltDmgs[@]} 个。请用 --dmg 明确指定。" >&2
    exit 1
  fi
  DmgPath="${BuiltDmgs[0]}"
fi

DmgSha256=""
if [[ -n "$DmgPath" ]]; then
  DmgPath="$(cd "$(dirname "$DmgPath")" && pwd)/$(basename "$DmgPath")"
  if [[ ! -f "$DmgPath" ]]; then
    echo "DMG 不存在: $DmgPath" >&2
    exit 1
  fi
  DmgSha256="$(/usr/bin/shasum -a 256 "$DmgPath" | /usr/bin/awk '{print $1}')"
  if ((Build)) && [[ -z "$ExpectedDmgSha256" ]]; then
    ExpectedDmgSha256="$DmgSha256"
  fi
  DmgSha256Lower="$(printf '%s' "$DmgSha256" | tr '[:upper:]' '[:lower:]')"
  ExpectedDmgSha256Lower="$(printf '%s' "$ExpectedDmgSha256" | tr '[:upper:]' '[:lower:]')"
  if [[ -n "$ExpectedDmgSha256" && "$DmgSha256Lower" != "$ExpectedDmgSha256Lower" ]]; then
    echo "当前 DMG SHA-256 与 --expected-dmg-sha256 不一致。" >&2
    exit 1
  fi
fi
if [[ -n "$CandidateManifestPath" ]]; then
  CandidateManifestPath="$(cd "$(dirname "$CandidateManifestPath")" && pwd)/$(basename "$CandidateManifestPath")"
  if [[ ! -f "$CandidateManifestPath" ]]; then
    echo "候选认证清单不存在: $CandidateManifestPath" >&2
    exit 1
  fi
  node "$RepoRoot/scripts/release/release-candidate.mjs" asset \
    --manifest "$CandidateManifestPath" \
    --platform macos \
    --release-sha "$CurrentRevision" \
    --run-id "$CandidateRunId" \
    --version "$CurrentAppVersion" \
    --asset "$DmgPath"
fi

MountedDevice=""
MountedPath=""
MountPlist=""
cleanup_mount() {
  if [[ -n "$MountedDevice" ]]; then
    hdiutil detach "$MountedDevice" -quiet || true
  fi
  if [[ -n "$MountPlist" ]]; then
    rm -f "$MountPlist"
  fi
}
trap cleanup_mount EXIT

copy_app_from_dmg() {
  local source_dmg="$1"
  local target_bundle="$2"
  local source_absolute
  local mounted_app
  source_absolute="$(cd "$(dirname "$source_dmg")" && pwd)/$(basename "$source_dmg")"
  if [[ ! -f "$source_absolute" ]]; then
    echo "DMG 不存在: $source_absolute" >&2
    return 1
  fi
  MountPlist="$(mktemp "${TMPDIR:-/tmp}/auto-email-sender-mount.XXXXXX.plist")"
  hdiutil attach -readonly -nobrowse -plist "$source_absolute" >"$MountPlist"
  MountedDevice="$(python3 - "$MountPlist" <<'PY'
import plistlib
import sys
with open(sys.argv[1], "rb") as file:
    payload = plistlib.load(file)
for entity in payload.get("system-entities", []):
    device = entity.get("dev-entry")
    mount = entity.get("mount-point")
    if device and mount:
        print(device)
        break
PY
)"
  MountedPath="$(python3 - "$MountPlist" <<'PY'
import plistlib
import sys
with open(sys.argv[1], "rb") as file:
    payload = plistlib.load(file)
for entity in payload.get("system-entities", []):
    mount = entity.get("mount-point")
    if mount:
        print(mount)
        break
PY
)"
  if [[ -z "$MountedDevice" || -z "$MountedPath" ]]; then
    echo "无法解析 DMG 挂载信息: $source_absolute" >&2
    return 1
  fi
  MountedApps=()
  while IFS= read -r candidate; do
    MountedApps+=("$candidate")
  done < <(find "$MountedPath" -maxdepth 1 -type d -name '*.app' -print)
  if ((${#MountedApps[@]} != 1)); then
    echo "DMG 根目录必须恰好包含一个 app bundle: $source_absolute" >&2
    return 1
  fi
  mounted_app="${MountedApps[0]}"
  mkdir -p "$(dirname "$target_bundle")"
  ditto "$mounted_app" "$target_bundle"
  hdiutil detach "$MountedDevice" -quiet
  MountedDevice=""
  rm -f "$MountPlist"
  MountPlist=""
}

InstalledRoot="$ArtifactsDir/安装 路径 Ω"
InstalledBundle="$InstalledRoot/Auto Email Sender.app"
UpgradeUserData=""
UpgradeManifest=""
if [[ "$Scenario" == "lifecycle" && -n "$PreviousDmgPath" ]]; then
  PreviousDmgPath="$(cd "$(dirname "$PreviousDmgPath")" && pwd)/$(basename "$PreviousDmgPath")"
  if [[ ! -f "$PreviousDmgPath" ]]; then
    echo "上一稳定版 DMG 不存在: $PreviousDmgPath" >&2
    exit 1
  fi
  PreviousDmgSha256="$(/usr/bin/shasum -a 256 "$PreviousDmgPath" | /usr/bin/awk '{print $1}')"
  if ((DevelopmentSmoke)) && [[ -z "$ExpectedPreviousDmgSha256" ]]; then
    ExpectedPreviousDmgSha256="$PreviousDmgSha256"
  fi
  PreviousDmgSha256Lower="$(printf '%s' "$PreviousDmgSha256" | tr '[:upper:]' '[:lower:]')"
  ExpectedPreviousDmgSha256Lower="$(printf '%s' "$ExpectedPreviousDmgSha256" | tr '[:upper:]' '[:lower:]')"
  if [[ ! "$ExpectedPreviousDmgSha256Lower" =~ ^[0-9a-f]{64}$ ]]; then
    echo "指定上一稳定版 DMG 时必须提供 64 位 --expected-previous-dmg-sha256；development smoke 可自动采用现场摘要。" >&2
    exit 2
  fi
  if [[ "$PreviousDmgSha256Lower" != "$ExpectedPreviousDmgSha256Lower" ]]; then
    echo "上一稳定版 DMG SHA-256 与 --expected-previous-dmg-sha256 不一致。" >&2
    exit 1
  fi
  copy_app_from_dmg "$PreviousDmgPath" "$InstalledBundle"
  PreviousExecutable="$InstalledBundle/Contents/MacOS/Auto Email Sender"
  if [[ ! -x "$PreviousExecutable" ]]; then
    echo "上一稳定版 app 主可执行文件不存在或不可执行: $PreviousExecutable" >&2
    exit 1
  fi
  codesign --verify --deep --strict --verbose=2 "$InstalledBundle"
  UpgradeUserData="$ArtifactsDir/$QA_PATH_MARKER/previous-stable-user-data/用户 数据 Ω"
  UpgradeManifest="$ArtifactsDir/previous-upgrade/manifest.json"
  mkdir -p "$UpgradeUserData"
  chmod 700 "$ArtifactsDir/$QA_PATH_MARKER" \
    "$ArtifactsDir/$QA_PATH_MARKER/previous-stable-user-data" \
    "$UpgradeUserData"
  uv run --project "$RepoRoot/backend" --no-sync python \
    "$RepoRoot/scripts/quality/seed-previous-packaged-upgrade.py" \
    --app-executable "$PreviousExecutable" \
    --artifact-root "$InstalledBundle" \
    --package-file "$PreviousDmgPath" \
    --user-data "$UpgradeUserData" \
    --manifest "$UpgradeManifest"
  PreservedPreviousBundle="$ArtifactsDir/上一稳定版 bundle/Auto Email Sender.app"
  mkdir -p "$(dirname "$PreservedPreviousBundle")"
  mv "$InstalledBundle" "$PreservedPreviousBundle"
fi
if [[ -n "$DmgPath" ]]; then
  MountPlist="$(mktemp "${TMPDIR:-/tmp}/auto-email-sender-mount.XXXXXX.plist")"
  hdiutil attach -readonly -nobrowse -plist "$DmgPath" >"$MountPlist"
  MountedDevice="$(python3 - "$MountPlist" <<'PY'
import plistlib
import sys
with open(sys.argv[1], "rb") as file:
    payload = plistlib.load(file)
for entity in payload.get("system-entities", []):
    device = entity.get("dev-entry")
    mount = entity.get("mount-point")
    if device and mount:
        print(device)
        break
PY
)"
  MountedPath="$(python3 - "$MountPlist" <<'PY'
import plistlib
import sys
with open(sys.argv[1], "rb") as file:
    payload = plistlib.load(file)
for entity in payload.get("system-entities", []):
    mount = entity.get("mount-point")
    if mount:
        print(mount)
        break
PY
)"
  if [[ -z "$MountedDevice" || -z "$MountedPath" ]]; then
    echo "无法解析 DMG 挂载信息。" >&2
    exit 1
  fi
  MountedApps=()
  while IFS= read -r candidate; do
    MountedApps+=("$candidate")
  done < <(find "$MountedPath" -maxdepth 1 -type d -name '*.app' -print)
  if ((${#MountedApps[@]} != 1)); then
    echo "DMG 根目录必须恰好包含一个 app bundle。" >&2
    exit 1
  fi
  mkdir -p "$InstalledRoot"
  ditto "${MountedApps[0]}" "$InstalledBundle"
  hdiutil detach "$MountedDevice" -quiet
  MountedDevice=""
  AppBundle="$InstalledBundle"
elif [[ -n "$AppBundle" ]]; then
  AppBundle="$(cd "$(dirname "$AppBundle")" && pwd)/$(basename "$AppBundle")"
else
  DefaultBundle="$RepoRoot/desktop/release/mac-arm64/Auto Email Sender.app"
  if [[ ! -d "$DefaultBundle" ]]; then
    echo "未找到 app bundle；请指定 --app-bundle、--dmg 或 --build。" >&2
    exit 1
  fi
  AppBundle="$DefaultBundle"
fi

AppExecutable="$AppBundle/Contents/MacOS/Auto Email Sender"
if [[ ! -x "$AppExecutable" ]]; then
  echo "app bundle 主可执行文件不存在或不可执行: $AppExecutable" >&2
  exit 1
fi
codesign --verify --deep --strict --verbose=2 "$AppBundle"

DriverArguments=(
  --scenario "$Scenario"
  --app-executable "$AppExecutable"
  --artifact-root "$AppBundle"
  --artifacts-dir "$ArtifactsDir/evidence"
  --repository-root "$RepoRoot"
  --expected-app-version "$CurrentAppVersion"
)
if [[ -n "$DmgPath" ]]; then
  DriverArguments+=(
    --package-file "$DmgPath"
    --expected-package-sha256 "$ExpectedDmgSha256"
  )
fi
if [[ -n "$CandidateManifestPath" ]]; then
  DriverArguments+=(
    --candidate-manifest-file "$CandidateManifestPath"
    --expected-candidate-run-id "$CandidateRunId"
  )
fi
if ((Certification)); then
  DriverArguments+=(
    --certification
    --expected-revision "$ExpectedRevision"
  )
  if [[ "$Scenario" == "lifecycle" || "$Scenario" == "seeded-chaos" ]]; then
    DriverArguments+=(--system-sleep-wake)
    NativeSleepRequested=1
  fi
elif ((PrereleaseCertification)); then
  DriverArguments+=(
    --prerelease-certification
    --expected-revision "$ExpectedRevision"
  )
  if [[ "$Scenario" == "lifecycle" || "$Scenario" == "seeded-chaos" ]]; then
    DriverArguments+=(--system-sleep-wake)
    NativeSleepRequested=1
  fi
else
  DriverArguments+=(--development-smoke)
  if ((SystemSleepWake)); then
    DriverArguments+=(--system-sleep-wake)
    NativeSleepRequested=1
  fi
fi
if [[ -n "$DurationSeconds" ]]; then
  DriverArguments+=(--duration-seconds "$DurationSeconds")
fi
if [[ -n "$Seed" ]]; then
  DriverArguments+=(--seed "$Seed")
fi
if [[ -n "$UpgradeManifest" ]]; then
  DriverArguments+=(
    --existing-user-data "$UpgradeUserData"
    --upgrade-manifest "$UpgradeManifest"
    --expected-previous-version "$ExpectedPreviousVersion"
    --previous-package-file "$PreviousDmgPath"
    --expected-previous-package-sha256 "$ExpectedPreviousDmgSha256"
  )
fi
if ((SkipBrowserProbe)); then
  DriverArguments+=(--skip-browser-probe)
fi

if ((NativeSleepRequested)) && ! /usr/bin/sudo -n /usr/bin/true; then
  echo "原生 sleep/wake 需要有效的 sudo ticket；请在运行前执行 sudo -v。" >&2
  exit 1
fi

uv run --project "$RepoRoot/backend" --no-sync python \
  "$RepoRoot/scripts/quality/packaged-runtime-qa.py" \
  "${DriverArguments[@]}"

Reports=()
while IFS= read -r candidate; do
  Reports+=("$candidate")
done < <(find "$ArtifactsDir/evidence/$QA_PATH_MARKER" -type f -name report.json -print 2>/dev/null | sort)
if ((${#Reports[@]} != 1)); then
  echo "期望恰好一份 packaged QA 报告，实际找到 ${#Reports[@]} 份。" >&2
  exit 1
fi
ReportPath="${Reports[0]}"
UserDataPath="$(python3 - "$ReportPath" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as file:
    payload = json.load(file)
if payload.get("status") != "passed":
    raise SystemExit("packaged QA report did not pass")
print(payload["user_data_path"])
PY
)"

if [[ "$AppBundle" == "$InstalledBundle" && $KeepInstalledApp -eq 0 ]]; then
  UninstalledBundle="$ArtifactsDir/已卸载 bundle/Auto Email Sender.app"
  mkdir -p "$(dirname "$UninstalledBundle")"
  mv "$InstalledBundle" "$UninstalledBundle"
  if [[ -e "$InstalledBundle" ]]; then
    echo "测试安装的 app bundle 在卸载模拟后仍存在。" >&2
    exit 1
  fi
fi
if [[ ! -f "$UserDataPath/$DATABASE_NAME" ]]; then
  echo "卸载模拟后隔离用户数据库未保留: $UserDataPath/$DATABASE_NAME" >&2
  exit 1
fi

echo "macOS packaged QA passed."
echo "Report: $ReportPath"
echo "Artifacts: $ArtifactsDir"
