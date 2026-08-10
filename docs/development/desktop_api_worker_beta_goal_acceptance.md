# 桌面 API + Worker 通用 Beta 验证 Goal 验收记录

- 状态：执行中（B0）
- 计划：[`desktop-api-worker-beta-goal-plan.md`](../architecture/desktop-api-worker-beta-goal-plan.md)
- 前置证据：[`desktop_api_worker_goal_acceptance.md`](./desktop_api_worker_goal_acceptance.md)
- 建立日期：2026-08-10

## 起点

- 原 Goal 已取消；G0～G5 已完成证据不重跑、不改写，G6 开发验证证据继续作为前置输入。
- 当前为 detached HEAD `6e06be9bfeae11b78eae78096782d84b3176c931`，工作区包含前置双进程改动。
- 本地 `master` 比当前 HEAD 多 5 个提交；B0 必须先保护工作并完成语义化集成。
- 当前 Desktop 版本和最新稳定版均为 `2.5.4`，默认模式为 `combined`。
- 尚未授权 push、远端 workflow、tag、GitHub Prerelease 或 master 合并。

## 阶段证据

| 阶段 | 状态 | 证据 |
| --- | --- | --- |
| B0：分支与 master 集成 | 待执行 | — |
| B1：模式设置与安全重启 | 待执行 | — |
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
| AC-BRANCH | 未通过 | 具名分支、master 集成和通用 source branch/SHA 合同 |
| AC-MODE | 未通过 | UI、Electron 设置、安全重启、页面外回退和双向切换 |
| AC-OBS | 未通过 | 本地有界记录、故障时导出、bundle schema 和 analyzer |
| AC-PRIV | 未通过 | allowlist、统一脱敏、canary 解包扫描和零上传路径 |
| AC-REL | 未通过 | Skill、脚本、workflow、候选 manifest 与恢复合同 |
| AC-ISO | 未通过 | Prerelease 非 Latest，稳定 feed/客户端完全隔离 |
| AC-BETA-QA | 未通过 | 双平台 exact-package lifecycle、2h normal、1h chaos |

## 首次失败与修复记录

所有首次失败、错误命令、环境问题、产品缺陷和修复后原场景重放都追加在此，不覆盖旧记录。
