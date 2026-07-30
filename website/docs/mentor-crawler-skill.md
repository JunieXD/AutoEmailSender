# 导师官网转导入表 Skill

仓库内置 `crawl-mentors-to-xlsx` Skill，可让 Codex 或 Claude Code 从用户提供的高校官网导师列表页出发，核对个人主页并生成可直接导入 Auto Email Sender 的 XLSX。

它只整理公开的学术职业信息，不会猜测邮箱、绕过登录或验证码，也不会自动把文件上传到系统。缺邮箱、通用邮箱、身份冲突或证据不足的候选会进入 `Needs Review`，而不是混入可导入数据。

## 在本仓库中使用

克隆本仓库后，Codex 会从 `.agents/skills/crawl-mentors-to-xlsx` 自动发现 Skill。可以在提示中显式调用：

```text
使用 $crawl-mentors-to-xlsx，抓取下面学院官网的在职导师，
生成可直接导入 Auto Email Sender 的 XLSX：
https://example.edu/faculty
```

仓库同时提供 `.claude/skills/crawl-mentors-to-xlsx` 入口。Claude Code 用户可在提示中明确要求使用同名 Skill；该入口会读取 `.agents/skills` 中的同一份契约和脚本。

## 安装到其他项目

暂时不通过插件市场发布，也不维护独立仓库。规范实现始终与 Auto Email Sender 一起维护。

每个 Auto Email Sender 版本 tag 也固定了当时的 Skill 版本，GitHub 自动生成的源码压缩包会包含 `.agents` 和 `.claude` 目录。Skill 不在 EXE、DMG 或 GitHub Release 的独立附件中；需要可复现版本时，请从对应 tag 获取整个 Skill 目录。

Codex 用户可以：

- 调用 `$skill-installer`，要求它从 `JunieXD/AutoEmailSender` 仓库的 `.agents/skills/crawl-mentors-to-xlsx` 路径安装。
- 或把该目录复制到目标项目的 `.agents/skills/`；若希望所有项目可用，则复制到个人目录 `$HOME/.agents/skills/`。

Claude Code 用户可把 `.agents/skills/crawl-mentors-to-xlsx` 这个完整规范目录复制到目标项目的 `.claude/skills/crawl-mentors-to-xlsx`，或复制到个人的 `~/.claude/skills/crawl-mentors-to-xlsx`。不要单独复制仓库中的 `.claude` 转发入口，也不要只复制 `SKILL.md`，因为前者依赖本仓库的相对路径，生成器、校验器和机器可读契约也都在规范目录的子目录中。

更新时重新从本仓库复制该目录即可。若直接在本仓库工作，执行常规 `git pull` 后会同步更新。

## 输入与输出

至少提供一个学校、学院、系所、研究院或实验室官网的公开导师列表 URL。可以额外说明：

- 目标学校和学院；
- 只抓取哪些职称或研究方向；
- 是否包含研究序列人员；
- 输出目录或文件名；
- 是否明确需要写入标签或个人备注。

默认输出包含三张工作表：

| 工作表 | 用途 |
| --- | --- |
| `Professors` | 活动且排在第一位的可导入数据，只包含安全的 10 个系统字段。 |
| `Needs Review` | 缺邮箱、冲突、越界或抓取失败的候选及原因。 |
| `Sources` | 实际访问、跳过和失败的列表页或个人主页。 |

只有用户明确要求写入标签或个人备注时，Skill 才会输出完整 12 列。这样可以避免同邮箱更新时意外修改用户已有标签或清空个人备注。

## 成功标准

Skill 只有在以下检查全部完成后才能交付：

1. 使用随附生成器创建 XLSX，而不是由代理手写工作簿。
2. 使用独立校验器确认表头、活动工作表、字段长度、邮箱、职称、分隔符、重复邮箱、公式和错误值都符合导入契约，并查看三张工作表的尺寸摘要。
3. 已有电子表格渲染工具时做一次限时只读预览，检查中文、链接、列宽与数据；没有现成工具时记录“视觉预览未执行”，不要临时安装字体或长时间驱动桌面软件。
4. 报告可导入人数、待复核人数、主要来源和未解决问题，不声称文件已自动导入。

若网站依赖复杂的客户端渲染、拒绝访问或没有公开邮箱，结果人数可能少于官网显示人数。这种情况应查看 `Needs Review` 和 `Sources`，而不是让代理猜测缺失字段。
