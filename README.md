<div align="center">
  <img src="frontend/public/favicon.svg" alt="Auto Email Sender Logo" width="100" height="100" />
  <h1>Auto Email Sender</h1>
  <p>
    <strong>面向导师套磁场景的智能邮件助手</strong>
  </p>
  <p>
    <a href="https://github.com/JunieXD/AutoEmailSender/releases/latest"><img src="https://img.shields.io/github/v/release/JunieXD/AutoEmailSender?display_name=tag&amp;sort=semver&amp;label=release&amp;logo=github&amp;color=ef4f43&amp;style=plastic&amp;labelColor=3f1515&amp;logoColor=white" alt="Latest release" /></a>
    <a href="https://github.com/JunieXD/AutoEmailSender/releases"><img src="https://img.shields.io/github/downloads/JunieXD/AutoEmailSender/total?label=downloads&amp;logo=githubsponsors&amp;color=16a34a&amp;style=plastic&amp;labelColor=3f1515&amp;logoColor=white" alt="Total downloads" /></a>
    <a href="https://github.com/JunieXD/AutoEmailSender/releases/latest"><img src="https://img.shields.io/badge/Windows-x64-0078d4?logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0wIDMuNCA5LjggMnY5LjRIMHpNMTEgMS44IDI0IDB2MTEuNEgxMXpNMCAxMi42aDkuOFYyMkwwIDIwLjZ6TTExIDEyLjZoMTNWMjRsLTEzLTEuOHoiLz48L3N2Zz4%3D&amp;style=plastic&amp;labelColor=3f1515&amp;logoColor=white" alt="Platform: Windows x64" /></a>
    <a href="https://github.com/JunieXD/AutoEmailSender/releases/latest"><img src="https://img.shields.io/badge/macOS-ARM64-111827?logo=apple&amp;style=plastic&amp;labelColor=3f1515&amp;logoColor=white" alt="Platform: macOS ARM64" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/github/license/JunieXD/AutoEmailSender?label=license&amp;logo=gnu&amp;color=2563eb&amp;style=plastic&amp;labelColor=3f1515&amp;logoColor=white" alt="License: GPL-3.0" /></a>
  </p>
</div>

---

> 利用 Agent 从学校导师页智能抓取导师信息，结合 LLM 分析匹配度，完成模板改写、定时批量发送和回复追踪。

Auto Email Sender 是一个本地运行的导师联系工具。从导师抓取、匹配分析、邮件草稿生成，到定时批量发送和回复追踪，整个流程在一个应用内完成。适合要联系很多位导师、又不想群发模板的同学。

整理信息和重复写信的活交给工具，发给谁、什么时候发、发什么内容，由你自己决定。

## 宣传片

<p align="center">
  <a href="https://www.bilibili.com/video/BV1sUHS6uEUZ">
    <img src="website/public/screenshots/promo/bilibili-promo-v10-play.jpg" alt="Auto Email Sender 宣传片，点击前往哔哩哔哩观看" width="720" />
  </a>
</p>

<p align="center">
  <a href="https://www.bilibili.com/video/BV1sUHS6uEUZ">在哔哩哔哩观看宣传片</a>
</p>

## 界面预览

| 导师看板 | 批量草稿审核 |
| --- | --- |
| <img src="website/public/screenshots/docs/home/app-home-overview.png" alt="导师看板截图" /> | <img src="website/public/screenshots/docs/tasks/task-center-batch-review.png" alt="批量草稿审核截图" /> |

| 统计面板 | 社区导师库 |
| --- | --- |
| <img src="website/public/screenshots/docs/statistics/statistics-overview.png" alt="统计面板截图" /> | <img src="website/public/screenshots/docs/community/community-mentor-catalog.png" alt="社区导师库截图" /> |

## 入口

- [官网](https://juniexd.github.io/AutoEmailSender/)
- [文档](https://juniexd.github.io/AutoEmailSender/docs/getting-started)
- [导师抓取 Skill](https://juniexd.github.io/AutoEmailSender/docs/mentor-crawler-skill)
- [下载桌面版](https://github.com/JunieXD/AutoEmailSender/releases)
- [问题反馈](https://github.com/JunieXD/AutoEmailSender/issues)
- QQ 交流群：`952383261`

## 交流与反馈

欢迎加入 QQ 交流群反馈 Bug、提出功能建议，或和其他同学交流使用经验。

<p>
  <img src="website/public/qq-group-952383261.jpg" alt="Auto Email Sender QQ 交流群二维码" width="220" />
</p>

如果需要提交可追踪的问题、复现步骤或截图，也可以前往 [GitHub Issues](https://github.com/JunieXD/AutoEmailSender/issues)。

## 核心特点

从整理导师资料到追踪回复，联系导师的整个流程都包含在内。邮件发出前，一定会经过你的审核和确认。

### 导师资料

- **智能抓取：** 从学校官网整理邮箱、院系、研究方向、近期论文和主页链接，抓取结果要经你审核，才会进入导师库。
- **社区导师库：** 浏览并导入已整理的公开导师资料，导入前可预览差异和处理冲突。

### 匹配与写信

- **匹配度分析：** 结合个人材料、意向研究方向和导师近期研究，给出匹配理由和联系建议。
- **AI 个性化改写：** 结合导师资料、邮件模板和你选定的参考材料生成草稿，语气、长度和保留多少模板原文都可以调整。

### 审核与发送

- **草稿审核：** 批量生成后可以逐封修改、重新改写或批准，AI 写的草稿不会自动发出。
- **发送计划：** 可以立即发送，也可以按日期、时段和每次数量定时发送；单封邮件还能改期、取消或恢复。

### 跟进与协作

- **回复追踪：** 通过 IMAP 同步已发送邮件和导师回复，自动更新任务状态，并保留往来记录。
- **Agent 支持：** 可通过本地命令行让 Agent 查询导师、筛选数据、管理任务；涉及发送的操作仍需你确认。

## License

GPL-3.0

## Star History

<a href="https://www.star-history.com/">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=JunieXD/AutoEmailSender&type=date&theme=dark&legend=top-left&sealed_token=xQN4NTyRjKuKYpjssLWpx_McJlHJe9s0mXmYmQNRrHYQxlq42KhQO8eOJoQTOoUvzxupHrY21TS9FVPsvpIRGboRX2_YEJ7DzXwvpVVDQWNGST4xKVGTSRCCMgPgJJ3i5MKjSv6LLZhC3-TQqfFZNUIdsvwZpaFvtoPPcjEaA19zdgH55LvDIyaOQyJ-" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=JunieXD/AutoEmailSender&type=date&legend=top-left&sealed_token=xQN4NTyRjKuKYpjssLWpx_McJlHJe9s0mXmYmQNRrHYQxlq42KhQO8eOJoQTOoUvzxupHrY21TS9FVPsvpIRGboRX2_YEJ7DzXwvpVVDQWNGST4xKVGTSRCCMgPgJJ3i5MKjSv6LLZhC3-TQqfFZNUIdsvwZpaFvtoPPcjEaA19zdgH55LvDIyaOQyJ-" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=JunieXD/AutoEmailSender&type=date&legend=top-left&sealed_token=xQN4NTyRjKuKYpjssLWpx_McJlHJe9s0mXmYmQNRrHYQxlq42KhQO8eOJoQTOoUvzxupHrY21TS9FVPsvpIRGboRX2_YEJ7DzXwvpVVDQWNGST4xKVGTSRCCMgPgJJ3i5MKjSv6LLZhC3-TQqfFZNUIdsvwZpaFvtoPPcjEaA19zdgH55LvDIyaOQyJ-" />
 </picture>
</a>
