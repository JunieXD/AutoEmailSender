# 投稿恢复

保留原批次文件和 manifest。重新运行不带 `--execute` 的 submit 命令；查询覆盖 OPEN、MERGED、CLOSED，并核对目标分支、批次标记及 PR 文件哈希。查重失败不代表没有投稿。

- 返回 `submitted` / `verified` / `closed`：已有同内容 PR，不再创建。加 `--execute` 可将确认的状态与 URL 写回本地；关闭的 PR 不会自动重开。
- 推送阶段中断：原命令加 `--execute`。脚本读取远端确定分支，核对其所有改动，只继续同一批次；不会强制推送。未推送成功的本地提交可从基线重新准备，用户原 checkout 不受影响。
- `CREATE_RESULT_UNKNOWN`：上次可能已创建 PR，但当前查询未找到。先检查 GitHub 上目标仓库该分支的所有 PR、账号和网络；确认没有 PR 后，原命令加 `--retry-create --execute`。这只允许继续创建同批次 PR，不跳过审计、查重和远端内容核对。已有投稿授权仍有效，无需因一次中断重复询问。
- `PR_CONFLICT` / `PR_CONTENT_MISMATCH` / `BRANCH_CONTENT_MISMATCH`：远端不是可自动复用的同内容投稿；报告具体批次与冲突，不覆盖、不另建批次重发。人工核对后再继续。
- 其他 `code`：按 `next_action` 修复缺失工具、认证、目标地址、权限或本地文件，再运行相同命令。

需要人工查询时：

```bash
gh pr list --repo owner/repo --head maintainer/community-batch-<batch_id> --state all --json number,url,state,headRefName,baseRefName
```

脚本只创建 PR，不创建 Issue 或 gist。正常退出会移除它自己创建的临时工作区；进程被强制终止留下的临时路径可用 `git worktree list` 核对后清理，不要删除用户的 checkout。
