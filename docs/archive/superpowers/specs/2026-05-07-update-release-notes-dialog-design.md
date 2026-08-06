# 更新公告弹窗与两段式发布设计

## 背景

当前桌面端已经使用 `electron-updater` 和 GitHub Releases 检查更新，发布流程中也已有 `scripts/release-notes.mjs`，可以从 Git commit subject 生成 `release-notes.md`。现有流程的问题是：公告生成发生在 GitHub Actions 发布阶段，发布者没有一个稳定的本地润色入口；用户侧发现新版本时，也只能看到下载入口，看不到本次更新内容。

本设计补齐两个能力：

- 发布前先生成 Markdown 公告草稿，发布者润色后再正式发布。
- 桌面端发现新版本时弹出公告弹窗，展示完整 Markdown 内容。

## 目标

- 发布流程采用严格的两段式流程：先准备公告，再正式发布。
- 公告内容以自动生成为主，允许发布者在本地编辑 Markdown 文件后发布。
- 正式发布时必须存在 `docs/releases/vX.Y.Z.md`，缺失则失败并提示先运行准备脚本。
- 应用内更新弹窗展示完整 Markdown 公告。
- 公告正文限制高度，内容过长时在弹窗内部滚动，不撑高外层容器。
- GitHub Release 正文、`electron-builder` 的 `releaseNotesFile` 和应用内弹窗尽量共用同一份公告内容。

## 非目标

- 不引入新的发布平台或更新渠道。
- 不改变现有安装包格式、自动更新下载逻辑和增量 / 全量下载选择。
- 不引入在线编辑公告能力。
- 不要求自动把 commit message 改写成完美的用户文案；润色由发布者在 Markdown 草稿中完成。
- 不实现多语言公告。

## 方案选择

采用严格方案：

1. 新增 `scripts/prepare-release.ps1 <version>` 生成 `docs/releases/v<version>.md` 草稿。
2. 发布者手动编辑这份 Markdown。
3. `scripts/release.ps1 <version>` 在发布前强制检查这份文件。
4. 文件存在时，正式发布脚本将其复制到 `desktop/release-notes.md`，再继续现有版本号、提交、tag 和推送流程。
5. 文件不存在时，发布脚本直接失败，并提示先运行 `prepare-release.ps1`。

这个方案让公告润色成为发布流程中的显式步骤，避免发布完成后再临时改 GitHub Release 正文，也避免脚本执行到一半等待人工编辑。

## 发布流程

### 准备公告

发布者先运行：

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\prepare-release.ps1 2.1.6
```

脚本行为：

- 校验版本号格式，接受 `2.1.6` 这种不带 `v` 的输入。
- 根据版本号生成目标文件 `docs/releases/v2.1.6.md`。
- 复用 `scripts/release-notes.mjs` 从上一个 `v*` tag 到当前 `HEAD` 收集 commit subject。
- 如果目标文件已存在，则默认失败，避免覆盖已经润色过的公告；后续如需要可增加显式 `-Force` 参数。
- 输出下一步提示：编辑 `docs/releases/v2.1.6.md` 后再运行 `scripts/release.ps1 2.1.6`。

### 润色公告

发布者只需要编辑生成好的 Markdown 文件。例如将：

```markdown
- fix(backend): 防止候选补全 worker 卡死
- feat(设置页): 优化草稿偏好预览体验
```

润色为：

```markdown
- 修复候选人信息补全时可能长时间卡住的问题。
- 优化设置页里的草稿偏好预览，调整后更容易确认生成效果。
```

Markdown 文件保留完整公告，而不是只写摘要。应用内弹窗按这份内容展示。

### 正式发布

发布者再运行：

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\release.ps1 2.1.6
```

脚本行为：

- 继续执行现有发布前检查：必须在 `master` 分支，工作区必须干净，版本号格式合法。
- 强制检查 `docs/releases/v2.1.6.md` 是否存在。
- 如果公告文件缺失，直接失败，提示：
  `缺少 docs/releases/v2.1.6.md，请先运行 .\scripts\prepare-release.ps1 2.1.6 并润色公告后再发布。`
- 如果公告文件存在，将其复制到 `desktop/release-notes.md`。
- 将版本文件和公告文件纳入发布提交。
- 创建 `chore(release): v2.1.6` 提交、`v2.1.6` tag，并推送 `master` 和 tag。

GitHub Actions 后续仍构建 Windows 安装包并发布 GitHub Release。CI 阶段可以继续以 `desktop/release-notes.md` 作为 Release 正文来源，避免重新生成覆盖人工润色结果。

## 公告内容模板

默认生成的 `docs/releases/vX.Y.Z.md` 使用以下结构：

```markdown
# vX.Y.Z

## 更新内容

- 从 commit subject 自动生成的条目。

## 安装说明

- 普通用户只需下载 `AutoEmailSender Setup X.Y.Z.exe`。

## 自动更新

- 应用内会自动检查更新。
- 发现新版本后，可以选择增量下载或全量下载。
```

模板保留安装说明和自动更新说明，因为这份 Markdown 同时用于 GitHub Release 和应用内弹窗。发布者通常只需要润色「更新内容」。

## 更新状态数据

主进程在 `update-available` 事件中读取 `electron-updater` 提供的 `UpdateInfo.releaseNotes`，并把公告内容追加到 `available` 状态：

```ts
{
  state: "available";
  version: string;
  nextVersion: string;
  fullDownloadBytes?: number;
  releaseNotes?: string;
}
```

如果 `releaseNotes` 是数组或结构化对象，主进程负责归一化为 Markdown 字符串；如果不可用，则不传或传空字符串。前端需要有兜底文案：

```text
新版本已发布，更新内容暂不可用。
```

已有的 `downloading`、`slow_download_offered`、`downloaded_pending_install` 和 `installing` 状态不需要携带公告内容。

## 前端交互

发现新版本时，前端展示「更新公告」弹窗：

- 标题：`发现新版本 vX.Y.Z`
- 副标题：显示当前版本和目标版本，例如 `当前 v2.1.5 -> v2.1.6`
- 正文：渲染完整 Markdown 公告。
- 正文容器：设置最大高度，例如 `max-h-[50vh] overflow-y-auto`，长内容在内部滚动。
- 底部操作：
  - `增量下载`
  - `全量下载`
  - `稍后`

用户选择 `稍后` 或关闭弹窗后：

- 不开始下载。
- 保留现有 `NEW` 标记。
- 更新按钮旁边的现有状态区继续提供下载入口。

用户选择 `增量下载` 或 `全量下载` 后：

- 复用现有 `downloadDesktopUpdate(mode)` 流程。
- 弹窗关闭。
- 下载进度继续在现有常驻状态区展示，不使用弹窗承载下载进度。

## Markdown 渲染约束

应用内渲染只需要支持常见发布公告格式：

- 标题
- 段落
- 无序列表
- 有序列表
- 行内代码
- 代码块
- 链接

渲染时必须避免执行不可信 HTML。实现时优先使用受控 Markdown 渲染方案，或禁用原始 HTML。链接如果需要打开，应交给桌面端外部浏览器处理，而不是在当前 Electron 页面内跳转。

## 错误处理

- `prepare-release.ps1` 发现公告文件已存在时失败，避免覆盖人工润色内容。
- `release.ps1` 发现公告文件缺失时失败，并给出准备脚本命令。
- 更新检查成功但公告缺失时，弹窗仍展示，正文使用兜底文案。
- Markdown 渲染失败时，前端降级展示纯文本，不影响下载入口。
- 更新检查失败仍沿用现有错误提示逻辑，不弹公告。

## 测试计划

- `scripts/prepare-release.ps1 2.1.6` 能生成 `docs/releases/v2.1.6.md`，内容包含版本号、安装说明和从 commit 生成的更新条目。
- 目标公告文件已存在时，准备脚本默认失败且不覆盖文件。
- `scripts/release.ps1 2.1.6` 在缺少 `docs/releases/v2.1.6.md` 时失败，并提示先运行准备脚本。
- `scripts/release.ps1 2.1.6` 在公告文件存在时，将其复制到 `desktop/release-notes.md` 并纳入发布提交。
- 主进程 `available` 状态能携带归一化后的 Markdown 公告。
- 前端收到 `available` 状态后弹出更新公告。
- 长公告不会撑高弹窗，正文区域内部滚动。
- 点击 `增量下载` 和 `全量下载` 后调用现有下载 API，并进入现有进度展示。
- 点击 `稍后` 后不触发下载，`NEW` 标记仍保留。

## 验收标准

- 发布者可以通过「准备公告 -> 编辑 Markdown -> 正式发布」完成发布。
- 正式发布缺少公告文件时会直接失败，不会静默生成默认公告继续发布。
- 每个已发布版本都有对应的 `docs/releases/vX.Y.Z.md`。
- GitHub Release 正文与应用内更新公告来自同一份 Markdown 内容。
- 用户发现新版本时能看到完整更新公告。
- 公告过长时弹窗高度保持稳定，内容在公告正文区域内滚动。
