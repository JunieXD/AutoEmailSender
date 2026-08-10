# Beta 本地诊断与分析操作说明

本说明适用于 Auto Email Sender 的 `alpha`、`beta` 和 `rc` 桌面测试版本。稳定版本默认不启用、
不显示这套入口。诊断数据不会自动上传；只有测试者主动保存 ZIP 并自行发送时才会离开本机。

## 测试者操作

在“其他设置 → 系统设置 → Beta 本地诊断”中可以：

- 查看当前本地占用、总量上限、保留天数、分片数和最近记录时间；
- 导出最近 1 小时、24 小时、7 天或全部保留记录；
- 在异常刚发生时选择类别并“标记刚才的问题”；
- 清空本机诊断记录。清空不会删除数据库、邮件或其他业务数据。

可选问题说明不会保存原文。桌面端只从中提取固定的故障关键词，例如启动、睡眠恢复、模式切换、
后台卡住、数据库、网络和资源占用；其余自由文本全部写成 `[FREE_TEXT_OMITTED]`。即便如此，
界面仍会提醒测试者不要填写邮件正文或个人信息。

如果 API 无法工作，设置页可能不可达，但托盘和启动失败原生窗口仍可导出。此时 ZIP 标记为
`partial`，并列出缺失项；这不是导出失败。每次导出都使用系统原生保存对话框，取消时不会创建 ZIP。

## 数据边界

诊断包包含 schema-versioned 的生命周期、资源趋势、六类后台工作聚合、数据库健康、事件分类和
候选身份。它不包含数据库副本、邮件地址、导师姓名、主题/正文、附件、LLM prompt/response、
凭据、完整 home/userData 路径、非 loopback IP 或原始 crawler 调试正文。

记录器按时间、单文件大小和总目录大小轮转。记录或清理失败不得影响产品业务；Electron 记录器
在 API/Worker 失效时仍继续工作。安装 ID 是首次启用时随机生成的 UUID，不来源于硬件标识，且
只有用户主动导出后才会离开本机。

## 安全分析单个或多个 ZIP

从仓库根目录运行：

```bash
rtk uv run --project backend --no-sync python \
  scripts/quality/analyze_beta_diagnostics.py \
  /absolute/path/report.zip \
  --output /absolute/path/report-analysis.json
```

同时分析多份报告时，逐个列出 ZIP：

```bash
rtk uv run --project backend --no-sync python \
  scripts/quality/analyze_beta_diagnostics.py \
  /absolute/path/mac-combined.zip \
  /absolute/path/mac-split.zip \
  /absolute/path/windows-split.zip \
  --output /absolute/path/combined-analysis.json
```

不指定 `--output` 时，JSON 写到标准输出。指定输出文件时，analyzer 使用私有权限创建新文件，
拒绝覆盖已有文件，也拒绝把输出路径指向任一输入 ZIP。

analyzer 从不把 ZIP 解压到磁盘，也不执行包内内容。它会在解析 JSON 前拒绝：

- symlink 输入、ZIP symlink/特殊文件、绝对路径、反斜杠和 `..` traversal；
- 重复、缺失或未知 entry，以及未知 schema；
- 超过压缩文件、单 entry、总展开大小、记录数或压缩率上限的包；
- 加密 entry、未知压缩算法、CRC 错误和声明/实际大小不一致；
- 缺失、重复或不匹配的 `checksums.sha256`；
- manifest、summary、component log 和 JSONL 计数/内容不一致。

只要批次中有一份不可信 ZIP，整个批次失败，不输出部分聚合结果。成功报告按 combined/split、
平台和版本汇总，并列出资源变化、重启/异常退出、SQLite lock/busy、队列积压、数据库完整性、
重复 accepted-delivery group 和孤儿 claim 告警。

## Canary 隐私门禁

内部 QA 使用 UTF-8 文本文件保存 canary，每行一个，值不得写入命令行：

```bash
rtk uv run --project backend --no-sync python \
  scripts/quality/analyze_beta_diagnostics.py \
  /absolute/path/final-diagnostics.zip \
  --forbidden-token-file /absolute/path/canaries.txt \
  --output /absolute/path/canary-analysis.json
```

analyzer 在校验最终 ZIP 和全部 checksum 后扫描每个展开 entry。错误只报告 canary 序号和 entry，
不会把 canary 本身打印到终端。任何命中都属于 AC-PRIV 阻断缺陷，不能通过删除测试或缩小扫描
范围放行。

跨 Electron exporter 与 Python analyzer 的真实最终 ZIP 合同测试：

```bash
cd desktop
rtk env AUTO_EMAIL_SENDER_BETA_DIAGNOSTICS_CROSS_QA=1 \
  npm run test -- betaDiagnosticsExporter.test.ts
```

该测试向结构化时间线和原始 startup/backend-error 来源注入 token、密码、邮箱、中文姓名、
home 路径、远程 URL、非 loopback IP、机器名和正文 canary，再由实际 yazl writer 写出 ZIP，最后
使用安全 analyzer 解包扫描。必须为零命中。

## 解释限制

- `partial` 报告只能说明 Electron 侧证据仍可用，不能把缺失的后端指标当成零。
- 没有远程遥测分母；健康测试者也应定期主动导出，避免样本只包含故障设备。
- 多份报告使用随机安装 ID 去重，但这不能证明它们代表全部测试者。
- Beta 的 2 小时 normal 与 1 小时 seeded chaos 是内部测试门禁，不替代稳定版的 24h/8h 认证。
- 诊断报告只能帮助定位风险，不能证明 SMTP exactly-once。SMTP 结果不确定时仍禁止自动重发，
  不要求用户确认，也不依赖 Sent/IMAP 证据。
