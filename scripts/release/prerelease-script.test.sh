#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
script_path="$repository_root/scripts/release/prerelease.sh"
notes_script="$repository_root/scripts/release/prerelease-notes.mjs"
temp_root="$(mktemp -d)"
fixture="$temp_root/fixture"
remote="$temp_root/origin.git"
temp_bin="$temp_root/bin"
gh_calls="$temp_root/gh-calls.txt"
stdout_path="$temp_root/stdout.txt"
stderr_path="$temp_root/stderr.txt"

cleanup() {
  local status=$?
  if ((status != 0)); then
    [[ ! -f "$stdout_path" ]] || { printf '%s\n' 'captured stdout:' >&2; cat "$stdout_path" >&2; }
    [[ ! -f "$stderr_path" ]] || { printf '%s\n' 'captured stderr:' >&2; cat "$stderr_path" >&2; }
  fi
  rm -rf "$temp_root"
  return "$status"
}
trap cleanup EXIT

assert_contains() {
  local text="$1"
  local needle="$2"
  if [[ "$text" != *"$needle"* ]]; then
    printf 'missing: %s\n%s\n' "$needle" "$text" >&2
    exit 1
  fi
}

mkdir -p "$fixture/cli" "$fixture/desktop" "$fixture/frontend" "$fixture/docs/releases" "$temp_bin"
git -C "$fixture" init -b release/generic-topic >/dev/null
git -C "$fixture" config user.email test@example.test
git -C "$fixture" config user.name "Test User"
version="9.9.9-beta.1"
printf '%s\n' '[project]' "version = \"$version\"" > "$fixture/cli/pyproject.toml"
printf '%s\n' "{\"version\":\"$version\"}" > "$fixture/desktop/package.json"
printf '%s\n' "{\"version\":\"$version\",\"packages\":{\"\":{\"version\":\"$version\"}}}" > "$fixture/desktop/package-lock.json"
printf '%s\n' "{\"version\":\"$version\"}" > "$fixture/frontend/package.json"
printf '%s\n' "{\"version\":\"$version\",\"packages\":{\"\":{\"version\":\"$version\"}}}" > "$fixture/frontend/package-lock.json"
node "$notes_script" --version "$version" --channel beta --output "$fixture/docs/releases/v$version.md" >/dev/null
sed -i.bak \
  -e 's/待根据本次候选的用户可见变化补充。/已补充用户可见变化。/g' \
  -e 's/待列出本次需要重点覆盖的正常流程、模式切换和故障场景。/重点覆盖模式切换和故障恢复。/' \
  "$fixture/docs/releases/v$version.md"
rm "$fixture/docs/releases/v$version.md.bak"
cp "$fixture/docs/releases/v$version.md" "$fixture/desktop/release-notes.md"
cat > "$fixture/desktop/electron-builder.yml" <<'YAML'
mac:
  extendInfo:
    SUFeedURL: https://github.com/example/repo/releases/latest/download/appcast.xml
publish:
  releaseType: release
YAML
git -C "$fixture" add .
git -C "$fixture" commit -m candidate >/dev/null
release_sha="$(git -C "$fixture" rev-parse HEAD)"
git init --bare "$remote" >/dev/null
git -C "$fixture" remote add origin "$remote"

"$script_path" certify "$version" \
  --channel beta \
  --source-branch release/generic-topic \
  --release-sha "$release_sha" \
  --dry-run \
  --repo-root "$fixture" > "$stdout_path" 2> "$stderr_path"
dry_output="$(cat "$stdout_path")"$'\n'"$(cat "$stderr_path")"
assert_contains "$dry_output" "release_kind=prerelease"
assert_contains "$dry_output" "source_branch=release/generic-topic"
assert_contains "$dry_output" "publish=false"
assert_contains "$dry_output" "未 push、tag、dispatch"
if [[ -n "$(git -C "$remote" show-ref 2>/dev/null || true)" ]]; then
  echo "dry-run unexpectedly pushed the source branch" >&2
  exit 1
fi

cat > "$temp_bin/gh" <<SHIM
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$gh_calls"
SHIM
chmod +x "$temp_bin/gh"
PATH="$temp_bin:$PATH" "$script_path" certify "$version" \
  --channel beta \
  --source-branch release/generic-topic \
  --release-sha "$release_sha" \
  --repo-root "$fixture" > "$stdout_path" 2> "$stderr_path"
certify_output="$(cat "$stdout_path")"$'\n'"$(cat "$stderr_path")"
assert_contains "$certify_output" "不会创建 tag 或 Release"
assert_contains "$(cat "$gh_calls")" "publish=false"
if [[ "$(git -C "$remote" rev-parse refs/heads/release/generic-topic)" != "$release_sha" ]]; then
  echo "certify did not bind the remote source branch to the exact SHA" >&2
  exit 1
fi
if git -C "$fixture" rev-parse "refs/tags/v$version" >/dev/null 2>&1; then
  echo "certify must not create a tag" >&2
  exit 1
fi

: > "$gh_calls"
PATH="$temp_bin:$PATH" "$script_path" publish "$version" \
  --channel beta \
  --source-branch release/generic-topic \
  --release-sha "$release_sha" \
  --candidate-run 123456 \
  --repo-root "$fixture" > "$stdout_path" 2> "$stderr_path"
publish_output="$(cat "$stdout_path")"$'\n'"$(cat "$stderr_path")"
assert_contains "$publish_output" "只会提升候选 run 123456"
publish_call="$(cat "$gh_calls")"
assert_contains "$publish_call" "publish=true"
assert_contains "$publish_call" "candidate_run_id=123456"

set +e
PATH="$temp_bin:$PATH" "$script_path" publish "$version" \
  --channel beta \
  --source-branch release/generic-topic \
  --release-sha "$release_sha" \
  --repo-root "$fixture" > "$stdout_path" 2> "$stderr_path"
missing_run_exit=$?
set -e
if [[ "$missing_run_exit" -eq 0 ]]; then
  echo "publish must require a candidate run" >&2
  exit 1
fi
assert_contains "$(cat "$stderr_path")" "必须提供有效的 --candidate-run"
