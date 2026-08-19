<div align="center">
  <img src="frontend/public/favicon.svg" alt="Auto Email Sender Logo" width="100" height="100" />
  <h1>Auto Email Sender</h1>
  <p>
    <strong>面向导师套磁场景的智能邮件助手</strong>
  </p>
  <p>
    <a href="https://github.com/JunieXD/AutoEmailSender/releases/latest"><img src="https://img.shields.io/github/v/release/JunieXD/AutoEmailSender?display_name=tag&amp;sort=semver&amp;style=flat-square&amp;label=release&amp;labelColor=dbeafe&amp;color=2563eb" alt="Latest release" /></a>
    <a href="https://github.com/JunieXD/AutoEmailSender/releases"><img src="https://img.shields.io/github/downloads/JunieXD/AutoEmailSender/total?style=flat-square&amp;label=downloads&amp;labelColor=dcfce7&amp;color=16a34a" alt="Total downloads" /></a>
    <a href="https://github.com/JunieXD/AutoEmailSender/releases/latest"><img src="https://img.shields.io/badge/platform-Windows%20x64%20%7C%20macOS%20ARM64-d97706?style=flat-square&amp;labelColor=ffedd5" alt="Platforms: Windows x64 and macOS ARM64" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/github/license/JunieXD/AutoEmailSender?style=flat-square&amp;label=license&amp;labelColor=e2e8f0&amp;color=64748b" alt="License: GPL-3.0" /></a>
  </p>
</div>

---

> 利用 Agent 从学校导师页智能抓取导师信息，结合 LLM 分析匹配度，完成模板改写、定时批量发送和回复追踪。

Auto Email Sender 是一个本地运行的导师联系工具。从导师抓取、匹配分析、邮件草稿生成，到定时批量发送和回复追踪，整个流程在一个应用内完成。适合需要批量联系导师、又不想无脑群发的场景。

系统替你完成信息整理和重复写信的工作，但最终发给谁、什么时候发、发什么内容，仍然由你来定。

## 宣传片

<p align="center">
  <a href="https://www.bilibili.com/video/BV1yQgw6rEXG">
    <img src="website/public/screenshots/promo/bilibili-promo-play-tv-white-70.jpg" alt="Auto Email Sender 宣传片，点击前往哔哩哔哩观看" width="720" />
  </a>
</p>

<p align="center">
  <a href="https://www.bilibili.com/video/BV1yQgw6rEXG">在哔哩哔哩观看宣传片</a>
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

从导师资料整理到回复追踪，覆盖联系导师的完整流程。自动化操作保留草稿审核与发送确认。

### 导师资料

- **智能抓取：** 从学校官网整理邮箱、院系、研究方向、近期论文和主页链接，结果审核后再导入导师库。
- **社区导师库：** 浏览并导入已整理的公开导师资料，导入前可预览差异和处理冲突。

### 匹配与写信

- **匹配度分析：** 结合个人材料、意向研究方向和导师近期研究，给出匹配理由和联系建议。
- **AI 个性化改写：** 结合导师资料、邮件模板和单独选择的参考材料生成草稿，支持调整语气、长度和模板保留方式。

### 审核与发送

- **草稿审核：** 批量生成后可逐封修改、重新改写或批准，AI 草稿不会自动发送。
- **发送计划：** 支持立即发送或按日期、时段和数量定时发送，也可单独改期、取消或恢复。

### 跟进与协作

- **回复追踪：** 通过 IMAP 同步已发送邮件和导师回复，自动更新任务状态并保留通信记录。
- **Agent 支持：** 可使用本地命令行和 Agent 查询导师、筛选数据和管理任务；发送操作仍需确认。

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
