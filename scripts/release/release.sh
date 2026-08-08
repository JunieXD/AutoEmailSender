#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "用法: scripts/release.sh <version> [--dry-run] [--skip-verify] [--repo-root <path>]" >&2
}

version=""
dry_run=0
skip_verify=0
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
release_version_checker="$script_dir/check-release-version.mjs"

while (($#)); do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --skip-verify)
      skip_verify=1
      shift
      ;;
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

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
  usage
  exit 2
fi

repo_root="$(cd "$repo_root" && pwd)"
release_tag="v$version"
curated_release_notes_path="$repo_root/docs/releases/$release_tag.md"
desktop_release_notes_path="$repo_root/desktop/release-notes.md"

run_git() {
  if ((dry_run)); then
    echo "[dry-run] git $*"
    return 0
  fi
  git -C "$repo_root" "$@"
}

invoke_checked_command() {
  local label="$1"
  shift
  if ! "$@"; then
    echo "[fail] $label 失败。" >&2
    exit 1
  fi
}

assert_clean_repository() {
  local branch
  branch="$(git -C "$repo_root" branch --show-current)"
  if ((dry_run)); then
    echo "[dry-run] current branch is $branch; real release requires master"
    return 0
  fi

  if [[ "$branch" != "master" ]]; then
    echo "发布必须在 master 分支执行，当前分支是 ${branch}。" >&2
    exit 1
  fi

  local allowed_release_notes_path="docs/releases/$release_tag.md"
  local unexpected_status=0
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    local path="${line:3}"
    path="${path//\\//}"
    if [[ "$path" != "$allowed_release_notes_path" ]]; then
      unexpected_status=1
      break
    fi
  done < <(git -C "$repo_root" status --porcelain --untracked-files=all)

  if ((unexpected_status)); then
    echo "工作区存在未提交改动，请先提交或清理后再发布。" >&2
    exit 1
  fi
}

assert_release_notes() {
  local relative_path="docs/releases/$release_tag.md"
  if [[ ! -f "$curated_release_notes_path" ]]; then
    echo "缺少 ${relative_path}，请先运行 ./scripts/prepare-release.sh ${version} 并润色公告后再发布。" >&2
    exit 1
  fi
}

assert_release_version() {
  invoke_checked_command "release version preflight" \
    node "$release_version_checker" --version "$version" --repo-root "$repo_root"
}

copy_release_notes() {
  if ((dry_run)); then
    echo "[dry-run] copy docs/releases/$release_tag.md to desktop/release-notes.md"
    return 0
  fi
  cp "$curated_release_notes_path" "$desktop_release_notes_path"
}

invoke_verification() {
  if ((skip_verify)); then
    echo "[skip] 跳过发布前验证"
    return 0
  fi

  echo "=== 验证 frontend ==="
  (
    cd "$repo_root/frontend"
    invoke_checked_command "frontend: npm test" npm test
    invoke_checked_command "frontend: npm run lint" npm run lint
    invoke_checked_command "frontend: npm run build" npm run build
  )

  echo "=== 验证 backend ==="
  (
    cd "$repo_root/backend"
    invoke_checked_command "backend: uv sync --dev" uv sync --dev
    invoke_checked_command "backend: uv run python -m unittest test.test_desktop_runtime" \
      uv run python -m unittest test.test_desktop_runtime
    invoke_checked_command "backend: uv run python -m unittest test.test_database_schema test.test_migrations_runtime" \
      uv run python -m unittest test.test_database_schema test.test_migrations_runtime
    invoke_checked_command "backend: uv run python -m unittest test.test_crawl_mentors_skill_contract test.test_crawl_mentors_skill_package" \
      uv run python -m unittest test.test_crawl_mentors_skill_contract test.test_crawl_mentors_skill_package
  )

  echo "=== 验证 cli ==="
  (
    cd "$repo_root/cli"
    invoke_checked_command "cli: uv sync --dev" uv sync --dev
    invoke_checked_command "cli: uv run python -m unittest discover test" \
      uv run python -m unittest discover test
  )
  invoke_checked_command "cli: frozen binary" \
    "$repo_root/scripts/build-cli.sh" --clean

  echo "=== 验证 desktop ==="
  (
    cd "$repo_root/desktop"
    invoke_checked_command "desktop: npm test" npm test
  )
}

set_cli_version() {
  (
    cd "$repo_root/cli"
    if ((dry_run)); then
      echo "[dry-run] uv version $version --no-sync in cli"
      return 0
    fi
    uv version "$version" --no-sync
  )
}

set_npm_version() {
  local directory="$1"
  (
    cd "$repo_root/$directory"
    if ((dry_run)); then
      echo "[dry-run] npm version $version --no-git-tag-version in $directory"
      return 0
    fi
    npm version "$version" --no-git-tag-version --allow-same-version
  )
}

assert_release_version
assert_clean_repository
assert_release_notes
invoke_verification
set_cli_version
set_npm_version "desktop"
set_npm_version "frontend"
copy_release_notes

run_git add cli/pyproject.toml cli/uv.lock desktop/package.json desktop/package-lock.json frontend/package.json frontend/package-lock.json desktop/release-notes.md "docs/releases/$release_tag.md"
if ((dry_run)); then
  run_git commit -m "chore(release): $release_tag"
else
  staged_paths="$(git -C "$repo_root" diff --cached --name-only)"
  if [[ -n "$staged_paths" ]]; then
    run_git commit -m "chore(release): $release_tag"
  else
    echo "发布版本和公告已包含在候选提交中，复用当前 HEAD。"
  fi
fi
run_git push origin master

if ((dry_run)); then
  echo "[dry-run] gh workflow run release.yml --ref master -f release_tag=$release_tag -f release_sha=<release-commit-sha> -f publish=true"
  echo "[dry-run] 未创建提交、tag 或推送。正式 tag 只会在双平台构建成功后创建。"
else
  release_sha="$(git -C "$repo_root" rev-parse HEAD)"
  (
    cd "$repo_root"
    gh workflow run release.yml \
      --ref master \
      -f "release_tag=$release_tag" \
      -f "release_sha=$release_sha" \
      -f "publish=true"
  )
  echo "已推送发布提交 $release_sha 并启动 ${release_tag} 候选工作流。双平台构建成功后才会创建 tag 和公开 Release。"
fi
