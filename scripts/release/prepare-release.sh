#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "用法: scripts/prepare-release.sh <version> [--repo-root <path>]" >&2
}

version=""
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

while (($#)); do
  case "$1" in
    --repo-root)
      if (($# < 2)); then
        usage
        exit 2
      fi
      repo_root="$2"
      shift 2
      ;;
    -*)
      usage
      exit 2
      ;;
    *)
      if [[ -n "$version" ]]; then
        usage
        exit 2
      fi
      version="$1"
      shift
      ;;
  esac
done

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  usage
  exit 2
fi

repo_root="$(cd "$repo_root" && pwd)"
tag="v$version"
release_directory="$repo_root/docs/releases"
release_notes_path="$release_directory/$tag.md"
relative_release_notes_path="docs/releases/$tag.md"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -e "$release_notes_path" ]]; then
  echo "$relative_release_notes_path 已经存在。请直接编辑该文件，或删除后重新生成。" >&2
  exit 1
fi

mkdir -p "$release_directory"
node "$script_dir/release-notes.mjs" \
  --repo-root "$repo_root" \
  --version "$tag" \
  --output "$release_notes_path"

echo "已生成 ${relative_release_notes_path}。"
echo "请编辑 ${relative_release_notes_path}，润色更新内容后再运行："
echo "./scripts/release.sh $version"
