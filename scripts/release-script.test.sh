#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release_script="$repo_root/scripts/release.sh"
temp_root="$(mktemp -d)"
temp_bin="$temp_root/bin"
stdout_path="$temp_root/stdout.txt"
stderr_path="$temp_root/stderr.txt"
uv_calls_path="$temp_root/uv-calls.txt"

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

new_shim() {
  local name="$1"
  local content="$2"
  printf '%s\n' "$content" > "$temp_bin/$name"
  chmod +x "$temp_bin/$name"
}

mkdir -p "$temp_bin"

release_repo="$temp_root/release-repo"
mkdir -p "$release_repo/docs/releases" "$release_repo/desktop" "$release_repo/frontend" "$release_repo/backend"
cat > "$release_repo/docs/releases/v9.9.9.md" <<'NOTES'
# v9.9.9

## 更新内容

- 测试公告。
NOTES

new_shim git '#!/usr/bin/env bash
if [[ "$3" == "branch" ]]; then echo master; exit 0; fi
if [[ "$3" == "status" ]]; then
  case " $* " in
    *" --untracked-files=all "*) echo "?? docs/releases/v9.9.9.md"; exit 0 ;;
    *) exit 2 ;;
  esac
fi
exit 0'

new_shim npm '#!/usr/bin/env bash
echo fake npm "$@"
if [[ "${1:-}" == "test" ]]; then exit 1; fi
exit 0'

new_shim uv "#!/usr/bin/env bash
echo fake uv \"\$@\"
printf '%s\n' \"\$*\" >> '$uv_calls_path'
exit 0"

old_path="$PATH"
export PATH="$temp_bin:$PATH"

set +e
"$release_script" 9.9.9 --dry-run --repo-root "$release_repo" > "$stdout_path" 2> "$stderr_path"
failure_exit=$?
set -e
failure_output="$(cat "$stdout_path")"$'\n'"$(cat "$stderr_path")"
if [[ "$failure_exit" -eq 0 ]]; then
  printf '%s\n' "release.sh 应该在 frontend 的 npm test 失败时返回非零退出码。" >&2
  exit 1
fi
assert_contains "$failure_output" "[fail] frontend: npm test" "输出里没有看到 frontend: npm test 的失败信息。"
if [[ "$failure_output" == *"验证 backend"* || "$failure_output" == *"fake npm run lint"* || "$failure_output" == *"fake npm run build"* ]]; then
  printf '%s\n%s\n' "release.sh 没有在第一个失败处停下。" "$failure_output" >&2
  exit 1
fi

new_shim npm '#!/usr/bin/env bash
echo fake npm "$@"
exit 0'
rm -f "$uv_calls_path"

"$release_script" 9.9.9 --dry-run --repo-root "$release_repo" > "$stdout_path" 2> "$stderr_path"
output="$(cat "$stdout_path")"$'\n'"$(cat "$stderr_path")"
uv_calls="$(cat "$uv_calls_path")"
assert_contains "$uv_calls" "run python -m unittest test.test_database_schema test.test_migrations_runtime" "release.sh 没有执行迁移相关后端测试。"
assert_contains "$uv_calls" "run python -m unittest test.test_crawl_mentors_skill_contract" "release.sh 没有执行导师抓取 Skill 契约测试。"
assert_contains "$output" "[dry-run] 未创建提交、tag 或推送" "release.sh dry-run 成功时没有输出完成信息。"

rm -f "$uv_calls_path"
"$release_script" 9.9.9 --skip-verify --repo-root "$release_repo" > "$stdout_path" 2> "$stderr_path"
output="$(cat "$stdout_path")"$'\n'"$(cat "$stderr_path")"
if [[ ! -f "$release_repo/desktop/release-notes.md" ]]; then
  printf '%s\n%s\n' "release.sh 应该把公告复制到 desktop/release-notes.md。" "$output" >&2
  exit 1
fi
assert_contains "$output" "已推送 v9.9.9" "release.sh 成功时没有输出 tag 已推送信息。"

set +e
"$release_script" 8.8.8 --dry-run --repo-root "$release_repo" > "$stdout_path" 2> "$stderr_path"
missing_exit=$?
set -e
missing_output="$(cat "$stdout_path")"$'\n'"$(cat "$stderr_path")"
if [[ "$missing_exit" -eq 0 ]]; then
  printf '%s\n' "release.sh 缺少公告文件时应该返回非零退出码。" >&2
  exit 1
fi
assert_contains "$missing_output" "缺少 docs/releases/v8.8.8.md" "缺少公告时没有给出明确提示。"
assert_contains "$missing_output" "./scripts/prepare-release.sh 8.8.8" "缺少公告时没有提示准备脚本命令。"

export PATH="$old_path"
