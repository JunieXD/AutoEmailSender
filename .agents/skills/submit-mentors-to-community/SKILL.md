---
name: submit-mentors-to-community
description: 校验、准备、审计并通过维护者 CLI 投稿社区导师数据。Use when a user asks to submit, contribute, batch-submit, or publish verified mentor/professor XLSX data to the community mentor library. Do not use for discovering faculty listing pages, crawling websites, or importing community data into the local app.
---

# Submit Mentors To Community

通过维护者 CLI 投稿已核对的 `community-share` XLSX。普通贡献者使用应用的 GitHub Issue Form。仅提交社区公开字段，保留个人备注、标签和邮件记录在本地。

## 操作

1. 输入 JSON 的 `submissions` 数组列出各学校/学院的 XLSX。运行 `scripts/prepare_submissions.py --input submissions.json --output-dir <batch-dir>`，生成批次文件和 `manifest.json`。
2. 运行 `scripts/audit_submissions.py <batch-dir>/manifest.json` 核对文件、行数和批次标识；有错误先修正。
3. 运行 `scripts/submit_submissions.py <batch-dir>/manifest.json` 查看计划。用户已明确授权该批次、目标仓库和许可证下的投稿时，加 `--execute` 执行，无需重复确认；缺少这些信息时先展示具体计划再询问。
4. 报告 Issue/PR URL、状态和未完成项。执行结果会写回 manifest；`--human` 提供短摘要。

批次中的每个文件对应一个学校/学院。批次 ID 由元数据和文件 SHA-256 决定，可用于查重。网络写入结果未知时，按 [recovery.md](references/recovery.md) 查询已有 Issue/PR 后恢复，不直接重发。准备、审计、查重或目标仓库检查失败时，修复相应问题后继续。
