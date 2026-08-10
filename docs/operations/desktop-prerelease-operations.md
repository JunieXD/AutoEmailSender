# 桌面测试版发布运维说明

本说明适用于 Auto Email Sender 的通用 `alpha`、`beta` 和 `rc` 桌面测试版。来源分支由每次发布
显式指定，可以是 `beta/<topic>`、`release/<series>` 或其他安全分支；任何脚本、Skill 或 workflow
都不得绑定某个固定业务分支。稳定版继续只允许从 `master` 发布。

测试版只提供 Windows EXE、macOS Apple Silicon DMG 和 `prerelease-candidate.json`。它不会成为
GitHub Latest，也不发布稳定更新通道使用的 `latest.yml`、`appcast.xml`、blockmap 或 delta。
仓库首页和 `/releases/latest` 仍指向稳定版；测试版可从完整 Releases 列表或直接 tag URL 找到并
手动覆盖安装。

## 固定输入与批准门

每次测试版必须先冻结四个值：

- `version`：`x.y.z-alpha.n`、`x.y.z-beta.n` 或 `x.y.z-rc.n`，后缀与 channel 一致；
- `channel`：只能是 `alpha`、`beta` 或 `rc`；
- `source_branch`：远端来源分支，不得使用 `refs/...`、空格、控制字符或危险 ref 语法；
- `release_sha`：该分支精确的 40 位提交 SHA。

核心版本必须高于最新稳定版，同核心下的新测试版必须高于已经存在的 prerelease。稳定 tag 的
发现只接受精确的 `vX.Y.Z`，不能把 alpha、beta 或 rc 当作覆盖升级基线。

以下远端动作各自需要所有者明确批准，不能从“准备测试版”自动推导授权：push 来源分支、
dispatch 候选 workflow、创建或推送 tag、创建或公开 GitHub Prerelease、修改公开测试版公告。
合回 `master` 和发布稳定版始终是另一道批准门。

## 状态机

| 阶段 | 允许的结果 | 禁止事项 |
| --- | --- | --- |
| Prepare Prerelease | 本地版本元数据、公告草稿和最终提交 | 不 push、tag、dispatch 或创建 Release |
| Certify Prerelease | 同一 run 构建双平台安装包和候选 manifest | 不创建 tag/Release，不发布 update metadata |
| Publish Prerelease | 原样提升指定 candidate run 的三个资产 | 不重建、不覆盖旧资产、不设为 Latest |
| Verify Isolation | 核对公开资产、tag/SHA 和稳定 feed 前后摘要 | 不把源码 smoke 当作公开资产证据 |
| Observe | 分析测试者主动提供的本地诊断 ZIP | 不自动上传、拉取或远程采集用户数据 |
| Supersede/Withdraw | 更高版本取代；旧版本标记停止使用 | 不移动 tag，不删除或替换公开资产 |

## Prepare Prerelease

先获取远端分支和 tag，确认当前位于预期来源分支、工作区干净，并决定是否需要把最新 `master`
合入该开发分支。合入 `master` 不等于反向合回 `master`。

POSIX：

```bash
./scripts/prepare-prerelease.sh 2.6.0-beta.1 \
  --channel beta \
  --source-branch beta/example-topic
```

PowerShell：

```powershell
.\scripts\prepare-prerelease.ps1 2.6.0-beta.1 `
  -Channel beta `
  -SourceBranch beta/example-topic
```

入口生成测试版公告、更新 CLI/Desktop/Frontend 版本并同步桌面公告，但不提交或访问远端。编辑
`docs/releases/v<version>.md`，删除全部占位文本，确认风险、测试重点、备份、同版本 combined
回退、本地诊断、自动更新隔离和停止使用方案都清楚，再同步到 `desktop/release-notes.md`。

完成所有本地改动和必要测试后形成一个干净提交，记录 `git rev-parse HEAD`；之后不得改代码、
版本元数据或公告。任何变化都产生新的 SHA 和新候选。

## Certify Prerelease

先运行 dry-run，检查即将使用的四个冻结值。非 dry-run 会 push 来源分支并 dispatch GitHub
Actions，因此只有取得这两项明确批准后才能执行。

```bash
./scripts/prerelease.sh certify 2.6.0-beta.1 \
  --channel beta \
  --source-branch beta/example-topic \
  --release-sha <40位SHA> \
  --dry-run
```

PowerShell 使用同样参数：

```powershell
.\scripts\prerelease.ps1 certify 2.6.0-beta.1 `
  -Channel beta `
  -SourceBranch beta/example-topic `
  -ReleaseSha <40位SHA> `
  -DryRun
```

批准后去掉 dry-run。成功的 `Release Desktop` run 应只产生：

- `prerelease-windows`：`AutoEmailSender-Setup-<version>.exe`；
- `prerelease-macos`：`AutoEmailSender-<version>-arm64.dmg`；
- `prerelease-candidate`：`prerelease-candidate.json`。

manifest 绑定 repository、版本、channel、来源分支、SHA、run ID、默认 split、诊断 schema、公告
哈希、稳定 Latest/feed 基线以及两平台资产名、大小和 SHA-256。记录 candidate run ID；不要混用
不同 run 的安装包、manifest 或公告。

## Exact-package 内部认证

两平台都必须使用候选 workflow 下载的原始字节。`--prerelease-certification` 是测试版正式证据，
normal soak 下限为连续 7200 秒，seeded chaos 下限为连续 3600 秒；稳定版的
`--certification` 仍保持 86400/28800 秒，不能互相替代。

macOS lifecycle 示例：

```bash
rtk bash scripts/quality/run-macos-packaged-qa.sh \
  --scenario lifecycle \
  --prerelease-certification \
  --expected-revision <40位SHA> \
  --dmg /absolute/path/AutoEmailSender-<version>-arm64.dmg \
  --expected-dmg-sha256 <manifest中的SHA-256> \
  --candidate-manifest /absolute/path/prerelease-candidate.json \
  --candidate-run-id <candidate-run-id> \
  --previous-dmg /absolute/path/AutoEmailSender-<stable>-arm64.dmg \
  --expected-previous-dmg-sha256 <上一稳定版公开DMG摘要> \
  --dedicated-test-account
```

同一 DMG 再分别运行 `normal-soak --duration-seconds 7200` 和
`seeded-chaos --duration-seconds 3600 --seed <recorded-seed>`。lifecycle 和 seeded chaos 会要求
真实 sleep/wake；不要跳过浏览器进程树验证。

Windows Parallels VM 一次运行 lifecycle 和两个长稳场景：

```bash
rtk bash scripts/quality/run-windows-vm-release-qa.sh \
  --prerelease-certification \
  --candidate-installer /absolute/path/AutoEmailSender-Setup-<version>.exe \
  --candidate-installer-sha256 <manifest中的SHA-256> \
  --candidate-manifest /absolute/path/prerelease-candidate.json \
  --candidate-run-id <candidate-run-id> \
  --previous-installer /absolute/path/AutoEmailSender-Setup-<stable>.exe \
  --previous-installer-sha256 <上一稳定版公开EXE摘要> \
  --normal-soak --normal-soak-seconds 7200 \
  --seeded-chaos --seeded-chaos-seconds 3600 \
  --seed <recorded-seed>
```

必须保留 lifecycle、normal 和 chaos 的报告、首次失败、原 seed 重放、候选 manifest 摘要以及最终
本地诊断 ZIP 分析。SMTP 只连接受控 loopback fake 服务；发送结果不确定时不自动重发，不要求
用户确认，也不依赖 Sent/IMAP 证据。

## Publish Prerelease

只有 exact-package 双平台门禁通过、没有未解决的阻断/高风险缺陷、公告已确认且所有者再次明确
批准 tag 与公开 GitHub Prerelease 后，才能运行 publish。先 dry-run：

```bash
./scripts/prerelease.sh publish 2.6.0-beta.1 \
  --channel beta \
  --source-branch beta/example-topic \
  --release-sha <40位SHA> \
  --candidate-run <candidate-run-id> \
  --dry-run
```

批准后去掉 dry-run。publish job 只下载并核验指定 run 的原资产，先检查稳定 feed，再创建不可变
tag 和 draft，下载复核 draft 资产后公开为 `prerelease=true`、`Latest=false`。公开阶段不运行产品
构建，也不使用 `--clobber`。

## Verify Isolation

publish workflow 必须验证：tag 精确指向冻结 SHA；公开 Release 恰好有 EXE、DMG 和 manifest；
不存在 `latest.yml`、`appcast.xml`、blockmap 或 delta；稳定 `/releases/latest` 的 Release ID、tag、
发布时间、`appcast.xml` 和 `latest.yml` 资产 ID/大小/SHA-256 与认证基线完全一致。

此外，在上一稳定版的 Windows 和 macOS 实际客户端中点击“检查更新”，两边都必须看不到测试版。
记录客户端版本、OS、时间和截图/结果。仅检查 GitHub API 或 workflow 文本不能替代这项真实验证。

## Observe

测试版不包含远程遥测。只接收测试者主动导出的健康或故障 ZIP，并用
[`beta-diagnostics-operations.md`](./beta-diagnostics-operations.md) 中的本地 analyzer 处理。健康运行
也要定期提交样本，否则没有故障率分母。原始 ZIP、解压内容和可能含设备关联信息的分析结果都
不得提交到 Git；保存在受控本地目录，并记录版本、平台、模式、场景和报告时间即可。

## Supersede / Withdraw

先验证取代版本严格更高且核心版本相同：

```bash
node scripts/release/prerelease-contract.mjs supersede \
  --previous-version 2.6.0-beta.1 \
  --replacement-version 2.6.0-beta.2
```

严重问题的“撤回”表示在取得批准后把旧 Release 标题和正文标记为“停止使用”，说明回退、诊断和
替代版本；不是删除 Release、移动 tag 或覆盖资产。修复必须走完整 Prepare → Certify → Publish，
使用更高版本。即使失败只留下未公开 tag/draft，也默认保留证据并使用更高版本；任何清理或 tag
复用都需要单独的破坏性操作批准和完整不可见证明。

## 硬停止

出现以下任一情况立即停止：来源分支与 SHA 不一致；worktree 不干净；manifest/公告/资产来自不同
run；公开阶段需要重建；稳定 Latest/feed 发生变化；测试版含稳定更新 metadata；诊断包泄露禁止
项；真实稳定客户端发现测试版；需要远端或公开操作但尚未获得对应批准。
