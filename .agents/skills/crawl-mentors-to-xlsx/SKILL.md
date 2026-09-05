---
name: crawl-mentors-to-xlsx
description: 从学校、学院、系所或实验室官网抓取公开导师/教师信息，核对个人主页与证据来源，并生成经过自动校验、可直接导入 Auto Email Sender 的 XLSX。Use when a user provides faculty, professor, mentor, supervisor, university, department, or lab directory URLs and asks to crawl, collect, export, or prepare 导师信息/教授信息 for spreadsheet import. Do not use for unrelated people scraping or automatic import into the application.
---

# Crawl Mentors To XLSX

从高校官网收集公开导师信息，生成 Auto Email Sender 可导入的 XLSX。相对路径以本 Skill 目录为基准。

## 抓取

- 从用户给定的学校、学院或目录开始；名称足以定位官网时可先查找入口，范围不明确再追问。
- 列表页用于发现候选与分页，个人页用于补全字段。邮箱必须有本人页面证据，可以还原混淆，不能猜测。缺邮箱、身份冲突或通用邮箱的候选放入 `review`。
- 读取 [crawling-policy.md](references/crawling-policy.md) 了解范围与证据规则；动态页面、分页不完整或断点续跑也见该文件。
- 大目录分批保存 UTF-8 JSON，保留已处理 URL。目录给出总数时核对唯一候选数，未完成的部分如实报告。

## 数据与生成

以 [candidates.example.json](assets/candidates.example.json) 为模板，字段、职称和空值规则见 [import-contract.md](references/import-contract.md)。

- `records` 保存可导入记录；`review` 保存待复核候选；`sources` 记录使用、失败或跳过的页面。
- 保留页面原文和字段来源。没有证据的字段留空；职称映射、论文分隔符及枚举使用契约规定的值。
- 默认将 `tags`、`personal_note` 留空，生成器输出 10 列，保留导入目标中已有的标签和备注。只有用户要求写入这两项时才加 `--include-user-fields`；完整格式中的空备注会清空原备注。

在 Skill 目录运行：

```bash
python3 scripts/build_professors_xlsx.py --input /绝对路径/candidates.json --output /绝对路径/professors_import.xlsx
python3 scripts/validate_professors_xlsx.py /绝对路径/professors_import.xlsx
```

使用随附生成器和校验器保证导入格式与文本单元格正确。校验失败时修正源数据再生成。需要检查版面时可用现有表格工具预览，不另建渲染流程。

交付 XLSX 链接、可导入/待复核人数、来源、未解决问题与校验结果。导入或投稿按用户另行指示执行。
