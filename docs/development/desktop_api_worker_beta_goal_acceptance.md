# 桌面 API + Worker 通用 Beta 验证 Goal 验收记录

- 状态：执行中（B0 已完成，B1 进行中）
- 计划：[`desktop-api-worker-beta-goal-plan.md`](../architecture/desktop-api-worker-beta-goal-plan.md)
- 前置证据：[`desktop_api_worker_goal_acceptance.md`](./desktop_api_worker_goal_acceptance.md)
- 建立日期：2026-08-10

## 起点

- 原 Goal 已取消；G0～G5 已完成证据不重跑、不改写，G6 开发验证证据继续作为前置输入。
- B0 开始时为 detached HEAD `6e06be9bfeae11b78eae78096782d84b3176c931`，工作区包含
  前置双进程改动。
- B0 开始时本地 `master` 多 5 个提交；该风险已通过前置快照和语义化合并解除。
- 当前 Desktop 版本和最新稳定版均为 `2.5.4`，默认模式为 `combined`。
- 尚未授权 push、远端 workflow、tag、GitHub Prerelease 或 master 合并。

## 阶段证据

| 阶段 | 状态 | 证据 |
| --- | --- | --- |
| B0：分支与 master 集成 | 已完成 | `e062f36`、`c51df44`、`4fe1bdf`；聚焦 291/291；修复后全仓 0 failures |
| B1：模式设置与安全重启 | 执行中 | — |
| B2：本地诊断与 analyzer | 待执行 | — |
| B3：通用 prerelease 发布体系 | 待执行 | — |
| B4：完整与重复回归 | 待执行 | — |
| B5：Mac/Windows 内部 Beta | 待执行 | — |
| B6：远端与公开批准门 | 待批准 | — |
| B7：证据收口 | 待执行 | — |

## AC 证据矩阵

没有证据的条目保持未通过，不以实现说明、源码 smoke 或另一平台结果替代。

| AC 组 | 当前状态 | 关闭要求 |
| --- | --- | --- |
| AC-BRANCH | 部分通过 | AC-BRANCH-01/02 已通过；AC-BRANCH-03 随 B3 的通用 source branch/SHA 合同关闭 |
| AC-MODE | 未通过 | UI、Electron 设置、安全重启、页面外回退和双向切换 |
| AC-OBS | 未通过 | 本地有界记录、故障时导出、bundle schema 和 analyzer |
| AC-PRIV | 未通过 | allowlist、统一脱敏、canary 解包扫描和零上传路径 |
| AC-REL | 未通过 | Skill、脚本、workflow、候选 manifest 与恢复合同 |
| AC-ISO | 未通过 | Prerelease 非 Latest，稳定 feed/客户端完全隔离 |
| AC-BETA-QA | 未通过 | 双平台 exact-package lifecycle、2h normal、1h chaos |

## 首次失败与修复记录

所有首次失败、错误命令、环境问题、产品缺陷和修复后原场景重放都追加在此，不覆盖旧记录。

### B0：保护工作与合入 master

1. 将 detached/dirty 前置实现完整保护到本地分支 `beta/desktop-api-worker`，
   快照提交为 `e062f36c19d34db7b937b8367b80671b22dc9401`。
2. 合入 `origin/master@4b54b5897d796bdb496432d1e3d41b7a3c32f2d3`，合并提交为
   `c51df440b8c7cd827a9c346e552e6468cc0f48af`。本次分支名不会写入通用 Release Skill。
3. 三个文本冲突均做了语义合并：
   - `email_log.py`：同时保留 delivery-attempt 唯一索引和大规模查询索引；
   - IMAP sync：同时保留双进程 fault injection 与 master 的 chunked query；
   - workspace runtime：同时保留 fault injection 与 master 的分块查询工具。
4. 两侧 Alembic 分支使用 merge revision `20260810_merge_delivery_scale` 收敛，
   `alembic heads` 只有一个 head。Ruff、compileall 和高风险聚焦回归 291/291 通过。

### B0：首次合并后全仓回归

命令：

```bash
rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py
```

- Backend：1893 tests，2 failures + 1 error，1 skip，约 10m20s。
- CLI：218/218；Frontend、Desktop、Website 均通过；全仓约 11m34s。
- 两个 failure 是 packaged upgrade 测试硬编码了合并前 Alembic head
  `20260809_delivery_at_most_once`，已改为唯一新 head `20260810_merge_delivery_scale`。
- 剩余 error 为
  `test_api_worker_lock_contention_degrades_and_recovers_without_corruption`。单独运行和部分
  前序组合会通过，但特定全套件顺序中将恢复窗口从 10 秒临时放宽到 30 秒
  仍失败，因此按产品缺陷处理，未当作普通负载波动。

### B0：SQLite maintenance 恢复缺陷

- 根因：master 新增的 `sqlite-maintenance` 正常周期为 21,600 秒。通用 Worker
  loop 失败后仍等待完整正常周期；若首轮在 SQLite 写锁下失败，Worker 会保持
  degraded 最长 6 小时。该子系统也未预先放入初始 health 列表，因此行为取决于
  启动时的协程调度顺序。
- 修复：`4fe1bdf20734bf53742eb0862dacecbed905a180` 为长周期循环增加独立的有界
  failure retry，SQLite maintenance 失败后 5 秒内重试，成功后恢复 6 小时正常周期；
  `sqlite-maintenance` 从 Worker 启动第一刻起就在 health 列表中。
- 测试：新增确定性 5s failure retry/health 合同测试；真实 API + Worker + SQLite
  锁冲突测试恢复为原本严格的 10 秒门限。

聚焦命令：

```bash
rtk uv run --no-sync python -m unittest \
  test.test_runtime_manager test.test_database_engine test.test_split_sqlite_faults
```

结果：27/27 通过，57.225s。

### B0：修复后完整全仓

命令：

```bash
rtk proxy uv run --project backend --no-sync python scripts/quality/run_all_tests.py --slowest 10
```

| 套件 | 结果 | 时长 |
| --- | --- | --- |
| Backend | 1894/1894 | 10m40s |
| CLI | 218/218 | 16s |
| Frontend | PASS | 19s |
| Desktop | PASS | 38s |
| Website | PASS | <1s |
| 合计 | 0 failures | 11m54s |

B0 完成。这是 master 集成与缺陷修复的基线证据；B4 仍会在 B1～B3 完成后
执行计划要求的连续两次全仓和 20 次 split 集成，不以本轮提前替代。
