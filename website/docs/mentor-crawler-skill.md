# 导师抓取 Skill：用 Codex 或 Claude Code 从导师官网生成导入表

导师抓取 Skill（`crawl-mentors-to-xlsx`）可以读取学校、学院、系所或实验室官网中的公开导师信息，核对个人主页和邮箱证据，并生成可直接导入 Auto Email Sender 的 XLSX。

你不需要下载 Auto Email Sender 源码，也不需要自己写爬虫。安装一次后，这个 Skill 可以在这台电脑上的其他本地项目中继续使用。

它只整理公开的学术职业信息，不会猜测邮箱、绕过登录或验证码，也不会自动把文件导入系统。缺邮箱、通用邮箱、身份冲突或证据不足的候选会放进 `Needs Review`，不会混入可导入数据。

## 安装到 Codex

打开任意一个 Codex 任务，把下面这段话完整发送给 Codex：

```text
请使用 $skill-installer，从 GitHub 仓库 JunieXD/AutoEmailSender 的 master 分支安装
.agents/skills/crawl-mentors-to-xlsx。请安装完整 Skill 目录，并设为当前用户全局可用。
```

Codex 会请求联网和写入个人 Skill 目录的权限，确认后等待安装完成即可。通常下一轮对话就能使用；如果没有出现，重启 Codex 后再试。

`$skill-installer` 通常把 Skill 安装到 `~/.codex/skills`。Codex 官方也支持把完整目录放在用户级 `$HOME/.agents/skills`；这两个位置都不依赖某个项目。不要只复制 `SKILL.md`，生成器、校验器和字段契约位于同一目录的 `scripts`、`references` 与 `assets` 中。

## 安装到 Claude Code

打开 Claude Code，把下面这段话完整发送给 Claude：

```text
请从 GitHub 仓库 JunieXD/AutoEmailSender 的 master 分支下载
.agents/skills/crawl-mentors-to-xlsx 完整目录，并安装到
~/.claude/skills/crawl-mentors-to-xlsx，使它在本机所有项目中可用。
不要只下载 SKILL.md；完成后请确认 scripts、references 和 assets 目录都已安装。
```

Claude Code 会请求联网和写入 `~/.claude/skills` 的权限。确认后等待安装完成即可。若这是本机第一次创建 `~/.claude/skills`，安装后重启一次 Claude Code。

## 手动安装 Release ZIP

如果不想让 Agent 联网安装，可以直接下载 Release 附带的 Skill ZIP。支持此功能的版本会提供以下附件；如果当前最新版本还没有该文件，说明它发布于这项功能上线之前，请暂时使用上面的 Agent 安装方式。

1. 打开 [Auto Email Sender Releases](https://github.com/JunieXD/AutoEmailSender/releases)，进入最新版本。
2. 在版本附件中下载 `crawl-mentors-to-xlsx-vX.Y.Z.zip`。文件名中的版本号会与该 Release 相同。
3. 解压 ZIP，得到一个名为 `crawl-mentors-to-xlsx` 的完整文件夹。
4. 按下表把这个文件夹复制到对应的个人 Skill 目录；目录不存在时可以新建。

| 使用工具 | macOS / Linux | Windows |
| --- | --- | --- |
| Codex | `~/.agents/skills/` | `%USERPROFILE%\.agents\skills\` |
| Claude Code | `~/.claude/skills/` | `%USERPROFILE%\.claude\skills\` |

Windows 用户可以把表格中的路径粘贴到文件资源管理器地址栏；macOS 用户可以在 Finder 中选择“前往 > 前往文件夹”并粘贴路径。复制完成后，最终应能直接看到以下文件，不能多套一层同名目录：

```text
<个人 Skill 目录>/crawl-mentors-to-xlsx/SKILL.md
```

重启 Codex 或 Claude Code 后，再按下一节检查安装。不要下载 GitHub 自动生成的整个项目源码压缩包，也不要只复制 `SKILL.md`；Release 中独立的 Skill ZIP 已经包含生成器、校验器和字段契约。

这里的“全局”是指同一台电脑上的所有本地 Codex 或 Claude Code 项目。云端任务不会自动读取你电脑上的个人 Skill 目录。

## 检查是否安装成功

安装后新开一个对话，发送：

```text
使用 $crawl-mentors-to-xlsx 检查它自己的安装目录，确认下面这些文件都存在：
SKILL.md
scripts/build_professors_xlsx.py
scripts/validate_professors_xlsx.py
references/import-contract.md
assets/professor-import-contract.v1.json
然后分别运行两个 Python 脚本的 --help。暂时不要访问网站或生成 XLSX；
如果有文件缺失或命令失败，请直接告诉我。
```

在 Claude Code 中也可以输入 `/crawl-mentors-to-xlsx` 显式调用。所有文件都存在，而且两个 `--help` 命令都成功，才说明完整目录已经安装；只识别到 Skill 名称或只读到 `SKILL.md` 还不够。

## 从导师官网生成 XLSX

至少提供一个公开的导师列表 URL。例如：

```text
使用 $crawl-mentors-to-xlsx，抓取下面学院官网的在职导师，
生成可直接导入 Auto Email Sender 的 XLSX：
https://example.edu/faculty
```

也可以额外说明学校和学院、只抓取哪些职称或研究方向、输出目录或文件名。只有当你明确要求写入标签或个人备注时，Skill 才会生成这两列。

默认输出包含三张工作表：

| 工作表 | 用途 |
| --- | --- |
| `Professors` | 活动且排在第一位的可导入数据。 |
| `Needs Review` | 缺邮箱、冲突、越界或抓取失败的候选及原因。 |
| `Sources` | 实际使用、跳过和失败的列表页或个人主页。 |

## 为什么默认只有 10 列

默认的 `Professors` 工作表不包含 `tags` 和 `personal_note`，这是保护已有用户数据的安全模式，并不是漏生成字段。Auto Email Sender 明确兼容这份 10 列格式：

| 导入场景 | 标签 | 个人备注 |
| --- | --- | --- |
| 新增导师 | 保持为空 | 保持为空 |
| 更新相同邮箱的已有导师 | 保留系统中的原标签 | 保留系统中的原备注 |

如果 XLSX 包含非空 `tags`，导入时会替换已有标签；如果包含 `personal_note` 列，即使单元格为空，也会覆盖已有备注。因此只有你明确要求写入这些用户字段时，Skill 才会输出完整 12 列。

## 更新 Skill

安装后的 Skill 不会随 Auto Email Sender 桌面应用自动更新。看到新版本公告后，可以再次让 Codex 或 Claude Code 执行：

```text
请把本机已安装的 crawl-mentors-to-xlsx 更新为
JunieXD/AutoEmailSender 仓库 master 分支中的最新版。
请只替换这个 Skill 的完整目录，不要改动其他 Skill；完成后确认
SKILL.md、scripts、references 和 assets 都存在。
```

`master` 代表当前最新版。如果需要复现某个 Auto Email Sender 版本，把安装请求中的 `master` 换成对应 tag，例如 `v2.4.0`。Skill 与应用共用版本 tag，每个应用 Release 会附带对应的独立 Skill ZIP；暂不通过插件市场或创建单独的 Skill Release。

如果采用手动安装，则从新版本 Release 下载对应 ZIP，用其中完整的 `crawl-mentors-to-xlsx` 文件夹替换原文件夹。只替换这个 Skill，不要删除整个个人 Skill 目录。

## 成功标准与常见限制

Skill 只有在生成器和独立校验器都成功后才会交付文件。校验会检查表头、活动工作表、字段长度、邮箱、职称、分隔符、重复邮箱、公式和错误值，并报告可导入人数、待复核人数、主要来源与未解决问题。

如果网站依赖复杂的客户端渲染、拒绝访问或没有公开邮箱，结果人数可能少于官网显示人数。此时应查看 `Needs Review` 和 `Sources`，不要让 Agent 猜测缺失字段。

参考：[Codex 官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills) · [Claude Code 官方 Skill 文档](https://code.claude.com/docs/en/skills)
