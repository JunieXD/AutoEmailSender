#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "用法: scripts/prepare-prerelease.sh <version> --channel <alpha|beta|rc> --source-branch <branch> [--dry-run] [--repo-root <path>]" >&2
}

version=""
channel=""
source_branch=""
dry_run=0
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"

while (($#)); do
  case "$1" in
    --channel)
      (($# >= 2)) || { usage; exit 2; }
      channel="$2"
      shift 2
      ;;
    --source-branch)
      (($# >= 2)) || { usage; exit 2; }
      source_branch="$2"
      shift 2
      ;;
    --repo-root)
      (($# >= 2)) || { usage; exit 2; }
      repo_root="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -*)
      usage
      exit 2
      ;;
    *)
      if [[ -n "$version" ]]; then usage; exit 2; fi
      version="$1"
      shift
      ;;
  esac
done

if [[ -z "$version" || -z "$channel" || -z "$source_branch" ]]; then
  usage
  exit 2
fi

repo_root="$(cd "$repo_root" && pwd)"
current_branch="$(git -C "$repo_root" branch --show-current)"
if [[ "$current_branch" != "$source_branch" ]]; then
  echo "当前分支 ${current_branch:-<detached>} 与显式 source branch $source_branch 不一致。" >&2
  exit 1
fi
if [[ -n "$(git -C "$repo_root" status --porcelain --untracked-files=all)" ]]; then
  echo "Prepare Prerelease 前工作区必须干净；不会覆盖现有改动。" >&2
  exit 1
fi

head_sha="$(git -C "$repo_root" rev-parse HEAD)"
node "$script_dir/prerelease-contract.mjs" validate \
  --version "$version" \
  --channel "$channel" \
  --source-branch "$source_branch" \
  --release-sha "$head_sha" >/dev/null
node "$script_dir/prerelease-contract.mjs" check-tags \
  --version "$version" \
  --channel "$channel" \
  --repo-root "$repo_root" >/dev/null

release_tag="v$version"
release_notes_path="$repo_root/docs/releases/$release_tag.md"
if [[ -e "$release_notes_path" ]]; then
  echo "docs/releases/$release_tag.md 已经存在；不会覆盖测试版公告。" >&2
  exit 1
fi

if ((dry_run)); then
  echo "[dry-run] node scripts/release/prerelease-notes.mjs --version $version --channel $channel --output docs/releases/$release_tag.md"
  echo "[dry-run] uv version $version --no-sync in cli"
  echo "[dry-run] npm version $version --no-git-tag-version --allow-same-version in desktop"
  echo "[dry-run] npm version $version --no-git-tag-version --allow-same-version in frontend"
  echo "[dry-run] copy docs/releases/$release_tag.md to desktop/release-notes.md"
  echo "[dry-run] 未 push、tag、dispatch，也未创建 GitHub Release。"
  exit 0
fi

node "$script_dir/prerelease-notes.mjs" \
  --version "$version" \
  --channel "$channel" \
  --output "$release_notes_path"
(
  cd "$repo_root/cli"
  uv version "$version" --no-sync
)
(
  cd "$repo_root/desktop"
  npm version "$version" --no-git-tag-version --allow-same-version
)
(
  cd "$repo_root/frontend"
  npm version "$version" --no-git-tag-version --allow-same-version
)
cp "$release_notes_path" "$repo_root/desktop/release-notes.md"

echo "已准备 $release_tag 的本地版本元数据和公告草稿。"
echo "请编辑 docs/releases/$release_tag.md，删除全部占位文本，并同步复制到 desktop/release-notes.md。"
echo "完成测试并提交后，记录精确 HEAD，再运行："
echo "./scripts/prerelease.sh certify $version --channel $channel --source-branch $source_branch --release-sha <40位SHA>"
echo "本步骤没有 push、tag、workflow dispatch 或 GitHub Release。"
