#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
script_path="$repository_root/scripts/release/prepare-prerelease.sh"
temp_root="$(mktemp -d)"
fixture="$temp_root/fixture"
temp_bin="$temp_root/bin"
calls_path="$temp_root/calls.txt"
stdout_path="$temp_root/stdout.txt"
stderr_path="$temp_root/stderr.txt"

cleanup() {
  rm -rf "$temp_root"
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
printf '%s\n' '[project]' 'version = "2.5.4"' > "$fixture/cli/pyproject.toml"
printf '%s\n' '{"version":"2.5.4"}' > "$fixture/desktop/package.json"
printf '%s\n' '{"version":"2.5.4","packages":{"":{"version":"2.5.4"}}}' > "$fixture/desktop/package-lock.json"
printf '%s\n' '{"version":"2.5.4"}' > "$fixture/frontend/package.json"
printf '%s\n' '{"version":"2.5.4","packages":{"":{"version":"2.5.4"}}}' > "$fixture/frontend/package-lock.json"
printf '%s\n' '# stable notes' > "$fixture/desktop/release-notes.md"
git -C "$fixture" add .
git -C "$fixture" commit -m fixture >/dev/null
git -C "$fixture" tag v2.5.4

"$script_path" 2.6.0-beta.1 \
  --channel beta \
  --source-branch release/generic-topic \
  --dry-run \
  --repo-root "$fixture" > "$stdout_path" 2> "$stderr_path"
dry_output="$(cat "$stdout_path")"$'\n'"$(cat "$stderr_path")"
assert_contains "$dry_output" "[dry-run] uv version 2.6.0-beta.1 --no-sync in cli"
assert_contains "$dry_output" "未 push、tag、dispatch"
if [[ -e "$fixture/docs/releases/v2.6.0-beta.1.md" ]]; then
  echo "dry-run must not write release notes" >&2
  exit 1
fi

cat > "$temp_bin/uv" <<SHIM
#!/usr/bin/env bash
printf 'uv %s\n' "\$*" >> "$calls_path"
SHIM
cat > "$temp_bin/npm" <<SHIM
#!/usr/bin/env bash
printf 'npm %s\n' "\$*" >> "$calls_path"
SHIM
chmod +x "$temp_bin/uv" "$temp_bin/npm"
PATH="$temp_bin:$PATH" "$script_path" 2.6.0-beta.1 \
  --channel beta \
  --source-branch release/generic-topic \
  --repo-root "$fixture" > "$stdout_path" 2> "$stderr_path"
output="$(cat "$stdout_path")"$'\n'"$(cat "$stderr_path")"
calls="$(cat "$calls_path")"
assert_contains "$calls" "uv version 2.6.0-beta.1 --no-sync"
assert_contains "$calls" "npm version 2.6.0-beta.1 --no-git-tag-version --allow-same-version"
assert_contains "$output" "source-branch release/generic-topic"
assert_contains "$output" "没有 push、tag、workflow dispatch"
notes="$(cat "$fixture/docs/releases/v2.6.0-beta.1.md")"
assert_contains "$notes" "不会成为 GitHub Latest"
assert_contains "$notes" "不会自动上传"
cmp "$fixture/docs/releases/v2.6.0-beta.1.md" "$fixture/desktop/release-notes.md"

set +e
"$script_path" 2.6.0-beta.2 \
  --channel beta \
  --source-branch another/topic \
  --dry-run \
  --repo-root "$fixture" > "$stdout_path" 2> "$stderr_path"
wrong_branch_exit=$?
set -e
if [[ "$wrong_branch_exit" -eq 0 ]]; then
  echo "prepare prerelease must reject a different source branch" >&2
  exit 1
fi
assert_contains "$(cat "$stderr_path")" "与显式 source branch another/topic 不一致"
