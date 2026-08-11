# 导师抓取 Skill

用 Codex 或 Claude Code 把导师官网整理成可导入 Auto Email Sender 的 XLSX。

它会读取公开导师页面、核对个人主页和邮箱来源，并把缺邮箱、身份冲突或证据不足的记录放进 `Needs Review`。它不会猜测邮箱、绕过登录或验证码，也不会自动导入数据。

安装一次后，可在这台电脑上的其他本地项目中继续使用，无需下载 Auto Email Sender 源码或编写爬虫。

::: warning 请安装完整目录
不要只复制 `SKILL.md`。生成器、校验器和字段规则位于同一目录的 `scripts`、`references` 和 `assets` 中。
:::

## 安装到 Codex

新建一个 Codex 任务并发送：

```text
请使用 $skill-installer，从 GitHub 仓库 JunieXD/AutoEmailSender 的 master 分支安装
.agents/skills/crawl-mentors-to-xlsx。请安装完整 Skill 目录，并设为当前用户全局可用。
```

允许联网和写入个人 Skill 目录后，等待安装完成。如果新任务中仍未出现该 Skill，请重启 Codex。

## 安装到 Claude Code

打开 Claude Code 并发送：

```text
请从 GitHub 仓库 JunieXD/AutoEmailSender 的 master 分支下载
.agents/skills/crawl-mentors-to-xlsx 完整目录，并安装到
~/.claude/skills/crawl-mentors-to-xlsx，使它在本机所有项目中可用。
不要只下载 SKILL.md；完成后请确认 scripts、references 和 assets 目录都已安装。
```

允许联网和写入个人 Skill 目录后，等待安装完成。如果安装后无法使用，请重启 Claude Code。

## 手动安装 Release ZIP

不想让 Agent 联网时，可手动安装：

1. 打开 [Auto Email Sender Releases](https://github.com/JunieXD/AutoEmailSender/releases)。
2. 下载 `crawl-mentors-to-xlsx-vX.Y.Z.zip`。不要下载 GitHub 自动生成的源码压缩包；如果当前版本没有 Skill ZIP，请使用上面的 Agent 安装方式。
3. 解压得到 `crawl-mentors-to-xlsx` 文件夹。
4. 把整个文件夹复制到对应目录：

| 使用工具 | macOS / Linux | Windows |
| --- | --- | --- |
| Codex | `~/.agents/skills/` | `%USERPROFILE%\.agents\skills\` |
| Claude Code | `~/.claude/skills/` | `%USERPROFILE%\.claude\skills\` |

最终路径应为：

```text
<个人 Skill 目录>/crawl-mentors-to-xlsx/SKILL.md
```

不要多套一层同名目录。完成后重启 Codex 或 Claude Code。

这里的“全局”仅指这台电脑上的本地项目；云端任务不会读取本机的个人 Skill 目录。

## 检查安装

新建对话并发送：

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

所有文件存在，且两个 `--help` 命令都成功，才表示安装完整。

## 生成 XLSX

至少提供一个公开的导师列表 URL，例如：

```text
使用 $crawl-mentors-to-xlsx，抓取下面学院官网的在职导师，
生成可直接导入 Auto Email Sender 的 XLSX：
https://example.edu/faculty
```

还可以指定学校、学院、职称、研究方向、输出目录或文件名。只有你明确要求时，Skill 才会写入标签或个人备注。

默认输出三张工作表：

| 工作表 | 用途 |
| --- | --- |
| `Professors` | 可直接导入的数据，也是默认活动工作表。 |
| `Needs Review` | 缺邮箱、冲突、越界或抓取失败的记录。 |
| `Sources` | 已使用、跳过和失败的列表页或个人主页。 |

## 默认导出 10 列的原因

默认不生成 `tags` 和 `personal_note`，避免覆盖已有标签和备注。只有你明确要求时，才会生成完整 12 列。

| 导入场景 | 标签 | 个人备注 |
| --- | --- | --- |
| 新增导师 | 保持为空 | 保持为空 |
| 更新相同邮箱的导师 | 保留原标签 | 保留原备注 |

如果 XLSX 包含非空 `tags`，导入时会替换已有标签；只要包含 `personal_note` 列，空单元格也会覆盖已有备注。

## 更新 Skill

桌面应用不会自动更新 Skill。看到新版本后，可让 Codex 或 Claude Code 执行：

```text
请把本机已安装的 crawl-mentors-to-xlsx 更新为
JunieXD/AutoEmailSender 仓库 master 分支中的最新版。
请只替换这个 Skill 的完整目录，不要改动其他 Skill；完成后确认
SKILL.md、scripts、references 和 assets 都存在。
```

手动安装时，请用新版本 ZIP 中的完整文件夹替换旧文件夹。`master` 代表最新版；需要复现旧版本时，可改用对应 tag，例如 `v2.4.0`。

## 成功标准与限制

Skill 只有在生成器和独立校验器都通过后才会交付文件。校验会检查表头、活动工作表、字段长度、邮箱、职称、分隔符、重复邮箱、公式和错误值，并报告可导入人数、待复核人数、来源和未解决问题。

依赖复杂客户端渲染、拒绝访问或没有公开邮箱的网站，结果可能少于官网显示人数。此时请查看 `Needs Review` 和 `Sources`，不要让 Agent 猜测缺失字段。

参考：[Codex 官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills) · [Claude Code 官方 Skill 文档](https://code.claude.com/docs/en/skills)
