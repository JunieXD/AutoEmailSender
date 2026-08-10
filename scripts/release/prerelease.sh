#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "用法: scripts/prerelease.sh <certify|publish> <version> --channel <alpha|beta|rc> --source-branch <branch> --release-sha <40位SHA> [--candidate-run <run-id>] [--dry-run] [--repo-root <path>]" >&2
}

action="${1:-}"
version="${2:-}"
if (($# >= 2)); then shift 2; else usage; exit 2; fi
channel=""
source_branch=""
release_sha=""
candidate_run_id=""
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
    --release-sha)
      (($# >= 2)) || { usage; exit 2; }
      release_sha="$2"
      shift 2
      ;;
    --candidate-run)
      (($# >= 2)) || { usage; exit 2; }
      candidate_run_id="$2"
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
    *)
      usage
      exit 2
      ;;
  esac
done

if [[ "$action" != "certify" && "$action" != "publish" ]]; then usage; exit 2; fi
if [[ -z "$version" || -z "$channel" || -z "$source_branch" || -z "$release_sha" ]]; then
  usage
  exit 2
fi
if [[ "$action" == "certify" && -n "$candidate_run_id" ]]; then
  echo "certify 不接受 --candidate-run；候选 run 只能由认证工作流产生。" >&2
  exit 2
fi
if [[ "$action" == "publish" && ! "$candidate_run_id" =~ ^[1-9][0-9]*$ ]]; then
  echo "publish 必须提供有效的 --candidate-run。" >&2
  exit 2
fi

repo_root="$(cd "$repo_root" && pwd)"
node "$script_dir/prerelease-contract.mjs" validate \
  --version "$version" \
  --channel "$channel" \
  --source-branch "$source_branch" \
  --release-sha "$release_sha" >/dev/null
node "$script_dir/prerelease-contract.mjs" check-tags \
  --version "$version" \
  --channel "$channel" \
  --repo-root "$repo_root" >/dev/null

current_branch="$(git -C "$repo_root" branch --show-current)"
if [[ "$current_branch" != "$source_branch" ]]; then
  echo "当前分支 ${current_branch:-<detached>} 与显式 source branch $source_branch 不一致。" >&2
  exit 1
fi
if [[ "$(git -C "$repo_root" rev-parse HEAD)" != "$release_sha" ]]; then
  echo "当前 HEAD 与显式 release_sha 不一致。" >&2
  exit 1
fi
if [[ -n "$(git -C "$repo_root" status --porcelain --untracked-files=all)" ]]; then
  echo "prerelease 候选必须来自干净、已提交的工作区。" >&2
  exit 1
fi

node "$script_dir/prerelease-preflight.mjs" \
  --version "$version" \
  --channel "$channel" \
  --source-branch "$source_branch" \
  --release-sha "$release_sha" \
  --repo-root "$repo_root"

if ((dry_run)); then
  if [[ "$action" == "certify" ]]; then
    echo "[dry-run] git push origin refs/heads/$source_branch:refs/heads/$source_branch"
  else
    echo "[dry-run] verify origin/$source_branch exactly equals $release_sha"
  fi
  echo "[dry-run] gh workflow run release.yml --ref $source_branch -f release_kind=prerelease -f release_tag=v$version -f release_sha=$release_sha -f source_branch=$source_branch -f prerelease_channel=$channel -f publish=$([[ "$action" == "publish" ]] && echo true || echo false) -f candidate_run_id=$candidate_run_id"
  echo "[dry-run] 未 push、tag、dispatch 或创建 GitHub Release。"
  exit 0
fi

if [[ "$action" == "certify" ]]; then
  node --test \
    "$script_dir/prerelease-contract.test.mjs" \
    "$script_dir/prerelease-preflight.test.mjs" \
    "$script_dir/prerelease-build-identity.test.mjs" \
    "$script_dir/prerelease-candidate.test.mjs" \
    "$script_dir/prerelease-isolation.test.mjs"
  git -C "$repo_root" push origin "refs/heads/$source_branch:refs/heads/$source_branch"
fi

remote_sha="$(git -C "$repo_root" ls-remote --heads origin "refs/heads/$source_branch" | awk '{print $1}')"
if [[ "$remote_sha" != "$release_sha" ]]; then
  echo "origin/$source_branch 指向 ${remote_sha:-<missing>}，预期 $release_sha；拒绝 dispatch。" >&2
  exit 1
fi

publish_value=false
if [[ "$action" == "publish" ]]; then publish_value=true; fi
(
  cd "$repo_root"
  gh workflow run release.yml \
    --ref "$source_branch" \
    -f "release_kind=prerelease" \
    -f "release_tag=v$version" \
    -f "release_sha=$release_sha" \
    -f "source_branch=$source_branch" \
    -f "prerelease_channel=$channel" \
    -f "publish=$publish_value" \
    -f "candidate_run_id=$candidate_run_id"
)

if [[ "$action" == "certify" ]]; then
  echo "已从 origin/$source_branch@$release_sha 启动 v$version 候选认证；不会创建 tag 或 Release。"
  echo "记录成功的 candidate run ID；完成双平台 exact-package QA 并获得用户批准后再运行 publish。"
else
  echo "已启动 v$version 的 exact-candidate 发布工作流，只会提升候选 run ${candidate_run_id}。"
  echo "工作流必须发布为 prerelease=true、Latest=false，并验证稳定 feed 完全不变。"
fi
