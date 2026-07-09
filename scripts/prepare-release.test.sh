#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script_path="$repo_root/scripts/prepare-release.sh"
temp_root="$(mktemp -d)"
stdout_path="$temp_root/stdout.txt"
stderr_path="$temp_root/stderr.txt"

cleanup() {
  rm -rf "$temp_root"
}
trap cleanup EXIT

assert_contains() {
  local text="$1"
  local needle="$2"
  local message="$3"
  if [[ "$text" != *"$needle"* ]]; then
    printf '%s\n%s\n' "$message" "$text" >&2
    exit 1
  fi
}

git -C "$temp_root" init >/dev/null
git -C "$temp_root" config user.email "test@example.com"
git -C "$temp_root" config user.name "Test User"
printf '%s\n' "base" > "$temp_root/file.txt"
git -C "$temp_root" add file.txt >/dev/null
git -C "$temp_root" commit -m "chore(release): v1.0.0" >/dev/null
git -C "$temp_root" tag v1.0.0
printf '%s\n' "next" > "$temp_root/file.txt"
git -C "$temp_root" commit -am "fix(更新): 修复公告弹窗高度" >/dev/null

"$script_path" 1.0.1 --repo-root "$temp_root" > "$stdout_path" 2> "$stderr_path"
output="$(cat "$stdout_path")"$'\n'"$(cat "$stderr_path")"
notes_path="$temp_root/docs/releases/v1.0.1.md"

if [[ ! -f "$notes_path" ]]; then
  printf '%s\n' "没有生成 docs/releases/v1.0.1.md。" >&2
  exit 1
fi

notes="$(cat "$notes_path")"
assert_contains "$notes" "# v1.0.1" "公告模板缺少版本标题。"
assert_contains "$notes" "### 新增功能" "公告模板缺少新增功能分组。"
assert_contains "$notes" "### 体验优化" "公告模板缺少体验优化分组。"
assert_contains "$notes" "### 问题修复" "公告模板缺少问题修复分组。"
assert_contains "$notes" "macOS Apple Silicon：下载 \`AutoEmailSender-1.0.1-arm64.dmg\`" "公告模板缺少 macOS Apple Silicon 安装说明。"
assert_contains "$notes" "系统设置 > 隐私与安全性" "公告模板缺少 macOS 首次打开说明。"
if [[ "$notes" == *"fix(更新): 修复公告弹窗高度"* ]]; then
  printf '%s\n%s\n' "公告模板不应该直接包含 commit subject。" "$notes" >&2
  exit 1
fi
assert_contains "$output" "请编辑 docs/releases/v1.0.1.md" "输出里缺少润色提示。"
assert_contains "$output" "./scripts/release.sh 1.0.1" "输出里缺少 Linux 发布脚本提示。"

set +e
"$script_path" 1.0.1 --repo-root "$temp_root" > "$stdout_path" 2> "$stderr_path"
second_exit=$?
set -e
second_output="$(cat "$stdout_path")"$'\n'"$(cat "$stderr_path")"
if [[ "$second_exit" -eq 0 ]]; then
  printf '%s\n' "公告文件已存在时，prepare-release.sh 应该失败。" >&2
  exit 1
fi
assert_contains "$second_output" "已经存在" "重复生成时没有提示文件已存在。"
