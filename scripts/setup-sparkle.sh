#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Sparkle 只能在 macOS 上准备。" >&2
  exit 1
fi

sparkle_version="2.9.4"
sparkle_sha256="ce89daf967db1e1893ed3ebd67575ed82d3902563e3191ca92aaec9164fbdef9"
sparkle_url="https://github.com/sparkle-project/Sparkle/releases/download/${sparkle_version}/Sparkle-${sparkle_version}.tar.xz"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
vendor_dir="$repo_root/desktop/native/sparkle/vendor"
version_marker="$vendor_dir/.sparkle-version"

if [[ -f "$version_marker" ]] \
  && [[ "$(<"$version_marker")" == "$sparkle_version" ]] \
  && [[ -f "$vendor_dir/Sparkle.framework/Versions/B/Sparkle" ]] \
  && [[ -x "$vendor_dir/bin/generate_appcast" ]]; then
  echo "Sparkle ${sparkle_version} 已准备完成。"
  exit 0
fi

temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/auto-email-sender-sparkle.XXXXXX")"
cleanup() {
  rm -rf "$temp_dir"
}
trap cleanup EXIT

archive_path="$temp_dir/Sparkle-${sparkle_version}.tar.xz"
if [[ -n "${SPARKLE_ARCHIVE:-}" ]]; then
  cp "$SPARKLE_ARCHIVE" "$archive_path"
else
  curl --fail --location --retry 3 --output "$archive_path" "$sparkle_url"
fi

actual_sha256="$(shasum -a 256 "$archive_path" | awk '{print $1}')"
if [[ "$actual_sha256" != "$sparkle_sha256" ]]; then
  echo "Sparkle 下载包校验失败：期望 ${sparkle_sha256}，实际 ${actual_sha256}。" >&2
  exit 1
fi

extract_dir="$temp_dir/extracted"
mkdir -p "$extract_dir"
tar -xJf "$archive_path" -C "$extract_dir"

if [[ ! -d "$extract_dir/Sparkle.framework" ]] || [[ ! -x "$extract_dir/bin/generate_appcast" ]]; then
  echo "Sparkle 下载包内容不完整。" >&2
  exit 1
fi

case "$vendor_dir" in
  "$repo_root"/desktop/native/sparkle/vendor) ;;
  *)
    echo "拒绝清理未识别的 Sparkle 目录：${vendor_dir}" >&2
    exit 1
    ;;
esac

rm -rf "$vendor_dir"
mkdir -p "$vendor_dir"
ditto "$extract_dir/Sparkle.framework" "$vendor_dir/Sparkle.framework"
ditto "$extract_dir/bin" "$vendor_dir/bin"
cp "$extract_dir/LICENSE" "$vendor_dir/LICENSE"
printf '%s\n' "$sparkle_version" > "$version_marker"

echo "已下载并验证 Sparkle ${sparkle_version}。"
