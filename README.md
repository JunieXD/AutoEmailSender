<div align="center">
  <img src="frontend/public/favicon.svg" alt="Auto Email Sender Logo" width="100" height="100" />
  <h1>Auto Email Sender</h1>
  <p>
    <strong>面向导师套磁场景的智能邮件助手</strong>
  </p>
  <p>
    <a href="https://www.gnu.org/licenses/gpl-3.0">
      <img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3" />
    </a>
    <img src="https://img.shields.io/badge/frontend-React%2019%20%7C%20Vite%208-61DAFB" alt="Frontend: React 19 | Vite 8" />
    <img src="https://img.shields.io/badge/backend-FastAPI-009688" alt="Backend: FastAPI" />
    <img src="https://img.shields.io/badge/database-SQLite-003B57" alt="Database: SQLite" />
  </p>
</div>

---

> 利用 Agent 从学校导师页智能抓取导师信息，结合 LLM 分析匹配度，完成模板改写、定时批量发送和回复追踪。

Auto Email Sender 是一个本地运行的导师联系工具。从导师抓取、匹配分析、邮件草稿生成，到定时批量发送和回复追踪，整个流程在一个应用内完成。适合需要批量联系导师、又不想无脑群发的场景。

系统替你完成信息整理和重复写信的工作，但最终发给谁、什么时候发、发什么内容，仍然由你来定。

## 界面预览

| 首页                                                   | 工作区                                                     |
| ------------------------------------------------------ | ---------------------------------------------------------- |
| <img src="docs/screenshots/首页.png" alt="首页截图" /> | <img src="docs/screenshots/工作区.png" alt="工作区截图" /> |

| 导师管理                                                         | 个人页                                                       |
| ---------------------------------------------------------------- | ------------------------------------------------------------ |
| <img src="docs/screenshots/导师管理页.png" alt="导师管理截图" /> | <img src="docs/screenshots/个人中心.png" alt="个人页截图" /> |

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

| 特点         | 说明                                                     |
| ------------ | -------------------------------------------------------- |
| 智能抓取     | Agent 从学校官网整理导师信息，减少手动复制和表格维护     |
| 匹配度分析   | LLM 结合你的材料和导师资料，辅助判断联系优先级           |
| 导师标签     | 自定义标签标记和筛选导师，支持导入导出时携带标签         |
| 定时批量发送 | 草稿确认后立即发送或排定时间窗口，控制发送节奏           |
| 回复追踪     | IMAP 自动检测导师回复，标记联系状态，方便后续跟进        |

## 页面概览

| 页面       | 用途                           |
| ---------- | ------------------------------ |
| 首页       | 筛选导师，创建联系任务         |
| 导师管理   | 抓取、导入和维护导师信息及标签 |
| 任务中心   | 查看批量任务和发送计划         |
| 工作区     | 查看匹配结果，审核草稿并发送   |
| 个人页     | 配置发件身份、材料、模板和模型 |
| 测试写信页 | 先给自己发一封测试邮件         |

## 未来计划

- [ ] 安卓移动端适配
- [ ] 降低导师信息抓取的 token 消耗
- [ ] 减小 Windows 安装包体积

## License

GPL-3.0

## Star History

<a href="https://www.star-history.com/?repos=JunieXD%2FAutoEmailSender&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=JunieXD/AutoEmailSender&type=date&theme=dark&legend=top-left&sealed_token=j-U7ERD8ZvkWZ-pMmPYWRYfrH9PrtKje1b6tDOQIjX8VneS8yycq6bHCU6RMRx1fbHTn7PTsbSDldjzCO_9TQPqPWfFAPW952tDvN7ixyqW1QmmdmM4XQ9y1c1_oh6gmAW5W-EE8rsFYmPJGRdp3hlgewFNtnmbxlWmF8SkUdRzkudl1Vxw7Q_fXePXZ" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=JunieXD/AutoEmailSender&type=date&legend=top-left&sealed_token=j-U7ERD8ZvkWZ-pMmPYWRYfrH9PrtKje1b6tDOQIjX8VneS8yycq6bHCU6RMRx1fbHTn7PTsbSDldjzCO_9TQPqPWfFAPW952tDvN7ixyqW1QmmdmM4XQ9y1c1_oh6gmAW5W-EE8rsFYmPJGRdp3hlgewFNtnmbxlWmF8SkUdRzkudl1Vxw7Q_fXePXZ" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=JunieXD/AutoEmailSender&type=date&legend=top-left&sealed_token=j-U7ERD8ZvkWZ-pMmPYWRYfrH9PrtKje1b6tDOQIjX8VneS8yycq6bHCU6RMRx1fbHTn7PTsbSDldjzCO_9TQPqPWfFAPW952tDvN7ixyqW1QmmdmM4XQ9y1c1_oh6gmAW5W-EE8rsFYmPJGRdp3hlgewFNtnmbxlWmF8SkUdRzkudl1Vxw7Q_fXePXZ" />
 </picture>
</a>
