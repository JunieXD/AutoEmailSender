# 更新公告弹窗实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现严格的两段式发布公告流程，并在桌面端发现新版本时弹窗展示完整 Markdown 更新公告。

**架构：** 发布链路先生成 `docs/releases/vX.Y.Z.md` 草稿，正式发布时强制读取该文件并复制到 `desktop/release-notes.md`。桌面主进程从 `electron-updater` 的 `releaseNotes` 读取公告并通过 IPC 状态传给前端。前端在 `DesktopUpdateButton` 内弹出公告弹窗，下载流程继续复用现有增量 / 全量下载状态区。

**技术栈：** PowerShell 7、Node.js ESM、Electron、electron-updater、React 19、TypeScript、react-markdown、Vitest、Testing Library。

---

## 文件结构

- 修改：`scripts/release-notes.mjs`
  - 职责：生成用户可读的 Markdown 公告模板，保留从 commit subject 自动生成的条目。
- 修改：`scripts/release-notes.test.mjs`
  - 职责：覆盖新公告模板和输出路径。
- 创建：`scripts/prepare-release.ps1`
  - 职责：发布前生成 `docs/releases/vX.Y.Z.md` 草稿，默认不覆盖已有文件。
- 创建：`scripts/prepare-release.test.ps1`
  - 职责：验证准备脚本生成文件、拒绝覆盖文件、输出下一步提示。
- 修改：`scripts/release.ps1`
  - 职责：正式发布前强制检查公告文件，复制到 `desktop/release-notes.md`，并纳入发布提交。
- 修改：`scripts/release-script.test.ps1`
  - 职责：覆盖缺少公告文件时发布失败，以及公告存在时进入现有验证流程。
- 修改：`.github/workflows/release.yml`
  - 职责：停止在 CI 阶段重新生成公告，改用发布提交中的 `desktop/release-notes.md`。
- 修改：`desktop/src/types.ts`
  - 职责：为 `available` 更新状态增加 `releaseNotes?: string`。
- 修改：`desktop/src/updates.ts`
  - 职责：归一化 `UpdateInfo.releaseNotes` 并随 `available` 状态发布。
- 修改：`desktop/test/updates.test.ts`
  - 职责：覆盖 release notes 归一化和 `available` 状态类型源代码约束。
- 修改：`frontend/src/types/desktop.d.ts`
  - 职责：同步前端桌面更新状态类型。
- 修改：`frontend/src/components/molecules/DesktopUpdateButton.tsx`
  - 职责：保存公告弹窗状态，渲染 Markdown 公告，并把弹窗按钮接入现有下载函数。
- 修改：`frontend/src/components/molecules/DesktopUpdateButton.test.tsx`
  - 职责：覆盖弹窗展示、Markdown 内容、长公告滚动容器、按钮行为和兜底文案。

## 任务 1：更新公告生成器模板

**文件：**
- 修改：`scripts/release-notes.mjs`
- 修改：`scripts/release-notes.test.mjs`

- [ ] **步骤 1：编写失败的模板测试**

在 `scripts/release-notes.test.mjs` 的第一个测试中，把断言改为新模板：

```js
it("combines the release announcement template with recent commit subjects", () => {
  const notes = buildReleaseNotes("v2.0.2", [
    "fix(后端): 修复桌面路径断言兼容性",
    "test(前端): 修复时间断言时区依赖",
  ]);

  expect(notes).toContain("# v2.0.2");
  expect(notes).toContain("## 更新内容");
  expect(notes).toContain("- fix(后端): 修复桌面路径断言兼容性");
  expect(notes).toContain("- test(前端): 修复时间断言时区依赖");
  expect(notes).toContain("普通用户只需下载 `AutoEmailSender Setup 2.0.2.exe`");
  expect(notes).toContain("发现新版本后，可以选择增量下载或全量下载。");
  expect(notes).not.toContain("AutoEmailSender-Setup-2.0.2.exe");
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
cd frontend
rtk npx vitest run ../scripts/release-notes.test.mjs
```

预期：FAIL，至少看到旧模板仍输出 `AutoEmailSender-Setup-2.0.2.exe` 或缺少 `## 更新内容`。

- [ ] **步骤 3：修改 `buildReleaseNotes` 模板**

在 `scripts/release-notes.mjs` 中将 `buildReleaseNotes` 改为：

```js
export function buildReleaseNotes(version, commits) {
  const normalizedVersion = normalizeVersion(version);
  const installerName = `AutoEmailSender Setup ${normalizedVersion}.exe`;
  const recentUpdates = commits.length
    ? commits.map((commit) => `- ${commit}`).join("\n")
    : "- 本次发布未包含额外的功能提交。";

  return [
    `# ${version}`,
    "",
    "## 更新内容",
    "",
    recentUpdates,
    "",
    "## 安装说明",
    "",
    `- 普通用户只需下载 \`${installerName}\`。`,
    "",
    "## 自动更新",
    "",
    "- 应用内会自动检查更新。",
    "- 发现新版本后，可以选择增量下载或全量下载。",
    "",
  ].join("\n");
}
```

- [ ] **步骤 4：运行生成器测试验证通过**

运行：

```powershell
cd frontend
rtk npx vitest run ../scripts/release-notes.test.mjs
```

预期：PASS，`release notes generator` 的测试全部通过。

- [ ] **步骤 5：Commit**

```powershell
rtk git add scripts/release-notes.mjs scripts/release-notes.test.mjs
rtk git commit -m "feat(发布): 优化更新公告模板"
```

## 任务 2：新增准备发布脚本

**文件：**
- 创建：`scripts/prepare-release.ps1`
- 创建：`scripts/prepare-release.test.ps1`

- [ ] **步骤 1：编写准备脚本测试**

创建 `scripts/prepare-release.test.ps1`：

```powershell
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$repoRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $repoRoot "scripts\prepare-release.ps1"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString("N"))
$stdoutPath = Join-Path $tempRoot "stdout.txt"
$stderrPath = Join-Path $tempRoot "stderr.txt"

function Invoke-PrepareRelease {
  param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$RepoRoot
  )

  $pwshPath = (Get-Command pwsh).Source
  Start-Process -FilePath $pwshPath -ArgumentList @(
    "-NoLogo",
    "-NoProfile",
    "-File",
    $scriptPath,
    $Version,
    "-RepoRoot",
    $RepoRoot
  ) -WorkingDirectory $RepoRoot -PassThru -Wait -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
}

try {
  New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
  git -C $tempRoot init | Out-Null
  git -C $tempRoot config user.email "test@example.com" | Out-Null
  git -C $tempRoot config user.name "Test User" | Out-Null
  Set-Content -Encoding UTF8 -Path (Join-Path $tempRoot "file.txt") -Value "base"
  git -C $tempRoot add file.txt | Out-Null
  git -C $tempRoot commit -m "chore(release): v1.0.0" | Out-Null
  git -C $tempRoot tag v1.0.0 | Out-Null
  Set-Content -Encoding UTF8 -Path (Join-Path $tempRoot "file.txt") -Value "next"
  git -C $tempRoot commit -am "fix(更新): 修复公告弹窗高度" | Out-Null

  $process = Invoke-PrepareRelease -Version "1.0.1" -RepoRoot $tempRoot
  $output = "$(Get-Content -Raw -Encoding UTF8 $stdoutPath)`n$(Get-Content -Raw -Encoding UTF8 $stderrPath)"
  $notesPath = Join-Path $tempRoot "docs\releases\v1.0.1.md"

  if ($process.ExitCode -ne 0) {
    throw "prepare-release.ps1 应该成功生成公告草稿。`n$output"
  }
  if (-not (Test-Path $notesPath)) {
    throw "没有生成 docs/releases/v1.0.1.md。"
  }

  $notes = Get-Content -Raw -Encoding UTF8 $notesPath
  if ($notes -notmatch "# v1.0.1" -or $notes -notmatch "fix\(更新\): 修复公告弹窗高度") {
    throw "公告草稿内容不符合预期。`n$notes"
  }
  if ($output -notmatch "请编辑 docs/releases/v1.0.1.md") {
    throw "输出里缺少润色提示。`n$output"
  }

  $second = Invoke-PrepareRelease -Version "1.0.1" -RepoRoot $tempRoot
  $secondOutput = "$(Get-Content -Raw -Encoding UTF8 $stdoutPath)`n$(Get-Content -Raw -Encoding UTF8 $stderrPath)"
  if ($second.ExitCode -eq 0) {
    throw "公告文件已存在时，prepare-release.ps1 应该失败。"
  }
  if ($secondOutput -notmatch "已经存在") {
    throw "重复生成时没有提示文件已存在。`n$secondOutput"
  }
} finally {
  Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\prepare-release.test.ps1
```

预期：FAIL，报错指出找不到 `scripts\prepare-release.ps1`。

- [ ] **步骤 3：创建准备脚本**

创建 `scripts/prepare-release.ps1`：

```powershell
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidatePattern('^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$')]
  [string]$Version,

  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$tag = "v$Version"
$releaseDirectory = Join-Path $RepoRoot "docs\releases"
$releaseNotesPath = Join-Path $releaseDirectory "$tag.md"
$relativeReleaseNotesPath = "docs/releases/$tag.md"

if (Test-Path $releaseNotesPath) {
  throw "$relativeReleaseNotesPath 已经存在。请直接编辑该文件，或删除后重新生成。"
}

New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null
node (Join-Path $RepoRoot "scripts\release-notes.mjs") `
  --repo-root $RepoRoot `
  --version $tag `
  --output $releaseNotesPath

Write-Host "已生成 $relativeReleaseNotesPath。"
Write-Host "请编辑 $relativeReleaseNotesPath，润色更新内容后再运行："
Write-Host "pwsh -NoLogo -NoProfile -File .\scripts\release.ps1 $Version"
```

- [ ] **步骤 4：运行准备脚本测试验证通过**

运行：

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\prepare-release.test.ps1
```

预期：PASS，无错误输出，退出码为 0。

- [ ] **步骤 5：Commit**

```powershell
rtk git add scripts/prepare-release.ps1 scripts/prepare-release.test.ps1
rtk git commit -m "feat(发布): 添加更新公告准备脚本"
```

## 任务 3：让正式发布强制依赖公告文件

**文件：**
- 修改：`scripts/release.ps1`
- 修改：`scripts/release-script.test.ps1`
- 修改：`.github/workflows/release.yml`

- [ ] **步骤 1：扩展发布脚本测试**

在 `scripts/release-script.test.ps1` 中，当前测试验证 `npm test` 失败即停。先在 `try` 块开始后创建公告文件，保证原测试继续覆盖验证阶段：

```powershell
$releaseNotesDirectory = Join-Path $repoRoot "docs\releases"
$releaseNotesPath = Join-Path $releaseNotesDirectory "v9.9.9.md"
New-Item -ItemType Directory -Path $releaseNotesDirectory -Force | Out-Null
Set-Content -Encoding UTF8 -Path $releaseNotesPath -Value @"
# v9.9.9

## 更新内容

- 测试公告。
"@
```

在最外层 `finally` 中清理这份测试公告，避免测试污染工作区：

```powershell
Remove-Item -LiteralPath $releaseNotesPath -Force -ErrorAction SilentlyContinue
```

然后在同一文件中新增缺公告失败场景，放在现有测试之后：

```powershell
$missingNotesProcess = Start-Process -FilePath $pwshPath -ArgumentList @(
  "-NoLogo",
  "-NoProfile",
  "-File",
  $releaseScript,
  "8.8.8",
  "-DryRun"
) -WorkingDirectory $repoRoot -PassThru -Wait -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

$missingOutput = "$(Get-Content -Raw -Encoding UTF8 $stdoutPath)`n$(Get-Content -Raw -Encoding UTF8 $stderrPath)"
if ($missingNotesProcess.ExitCode -eq 0) {
  throw "release.ps1 缺少公告文件时应该返回非零退出码。"
}
Assert-Contains -Text $missingOutput -Needle "缺少 docs/releases/v8.8.8.md" -Message "缺少公告时没有给出明确提示。`n$missingOutput"
Assert-Contains -Text $missingOutput -Needle ".\scripts\prepare-release.ps1 8.8.8" -Message "缺少公告时没有提示准备脚本命令。`n$missingOutput"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\release-script.test.ps1
```

预期：FAIL，缺公告场景没有失败，或者没有匹配到 `缺少 docs/releases/v8.8.8.md`。

- [ ] **步骤 3：修改 `release.ps1` 检查并复制公告**

在 `scripts/release.ps1` 中 `$RepoRoot` 后添加：

```powershell
$ReleaseTag = "v$Version"
$CuratedReleaseNotesPath = Join-Path $RepoRoot "docs\releases\$ReleaseTag.md"
$DesktopReleaseNotesPath = Join-Path $RepoRoot "desktop\release-notes.md"
```

调整 `Assert-CleanRepository`，允许当前版本公告文件作为唯一的未提交改动，因为该文件由准备脚本生成，并会被发布脚本纳入发布提交：

```powershell
function Assert-CleanRepository {
  $branch = git -C $RepoRoot branch --show-current
  if ($DryRun) {
    Write-Host "[dry-run] current branch is $branch; real release requires master"
    return
  }

  if ($branch -ne "master") {
    throw "发布必须在 master 分支执行，当前分支是 $branch。"
  }

  $status = git -C $RepoRoot status --porcelain
  $allowedReleaseNotesPath = "docs/releases/$ReleaseTag.md"
  $unexpectedStatus = @(
    $status | Where-Object {
      $path = $_.Substring(3).Replace("\", "/")
      $path -ne $allowedReleaseNotesPath
    }
  )
  if ($unexpectedStatus.Count -gt 0) {
    throw "工作区存在未提交改动，请先提交或清理后再发布。"
  }
}
```

新增函数：

```powershell
function Assert-ReleaseNotes {
  $relativePath = "docs/releases/$ReleaseTag.md"
  if (-not (Test-Path $CuratedReleaseNotesPath)) {
    throw "缺少 $relativePath，请先运行 .\scripts\prepare-release.ps1 $Version 并润色公告后再发布。"
  }
}

function Copy-ReleaseNotes {
  if ($DryRun) {
    Write-Host "[dry-run] copy docs/releases/$ReleaseTag.md to desktop/release-notes.md"
    return
  }
  Copy-Item -LiteralPath $CuratedReleaseNotesPath -Destination $DesktopReleaseNotesPath -Force
}
```

在 `Assert-CleanRepository` 后、`Invoke-Verification` 前调用：

```powershell
Assert-ReleaseNotes
```

在 `Set-NpmVersion "frontend"` 后调用：

```powershell
Copy-ReleaseNotes
```

将 `Run-Git add ...` 改为：

```powershell
Run-Git add desktop/package.json desktop/package-lock.json frontend/package.json frontend/package-lock.json desktop/release-notes.md "docs/releases/$ReleaseTag.md"
```

将 `Run-Git tag "v$Version"` 和最后输出改用 `$ReleaseTag`：

```powershell
Run-Git tag $ReleaseTag
Run-Git push origin $ReleaseTag
Write-Host "已发布 $ReleaseTag。GitHub Actions 将自动创建 Release。"
```

- [ ] **步骤 4：修改 GitHub Actions 不再重新生成公告**

在 `.github/workflows/release.yml` 删除 `Generate release notes` 步骤：

```yaml
      - name: Generate release notes
        run: node ./scripts/release-notes.mjs --version "${{ github.ref_name }}" --upper-ref "${{ github.sha }}" --output ./desktop/release-notes.md
```

保留 `Update GitHub release notes`，它继续使用发布提交中的 `./desktop/release-notes.md`。

- [ ] **步骤 5：运行发布脚本测试验证通过**

运行：

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\release-script.test.ps1
```

预期：PASS，现有失败即停场景和缺公告失败场景都通过。

- [ ] **步骤 6：Commit**

```powershell
rtk git add scripts/release.ps1 scripts/release-script.test.ps1 .github/workflows/release.yml
rtk git commit -m "feat(发布): 强制发布前准备更新公告"
```

## 任务 4：桌面端更新状态携带 Markdown 公告

**文件：**
- 修改：`desktop/src/types.ts`
- 修改：`desktop/src/updates.ts`
- 修改：`desktop/test/updates.test.ts`

- [ ] **步骤 1：编写桌面端测试**

在 `desktop/test/updates.test.ts` 中追加：

```ts
import { normalizeReleaseNotes } from "../src/updates.js";
```

并新增测试：

```ts
it("normalizes electron-updater release notes into markdown text", () => {
  expect(normalizeReleaseNotes("## 更新内容\n\n- 修复问题")).toBe("## 更新内容\n\n- 修复问题");
  expect(
    normalizeReleaseNotes([
      { version: "2.1.6", note: "- 修复公告弹窗高度" },
      { version: "2.1.5", note: "- 优化更新下载" },
    ]),
  ).toBe("## v2.1.6\n\n- 修复公告弹窗高度\n\n## v2.1.5\n\n- 优化更新下载");
  expect(normalizeReleaseNotes(undefined)).toBeUndefined();
});

it("adds release notes to the available update status", () => {
  const source = readFileSync(path.resolve("src", "updates.ts"), "utf8");
  const types = readFileSync(path.resolve("src", "types.ts"), "utf8");

  expect(types).toContain('releaseNotes?: string');
  expect(source).toContain("releaseNotes: normalizeReleaseNotes(info.releaseNotes)");
});
```

- [ ] **步骤 2：运行桌面测试验证失败**

运行：

```powershell
cd desktop
rtk npm test -- updates
```

预期：FAIL，`normalizeReleaseNotes` 未导出或类型中没有 `releaseNotes?: string`。

- [ ] **步骤 3：修改桌面类型**

在 `desktop/src/types.ts` 中将 `available` 状态改为：

```ts
| {
    state: "available";
    version: string;
    nextVersion: string;
    fullDownloadBytes?: number;
    releaseNotes?: string;
  }
```

- [ ] **步骤 4：实现 release notes 归一化**

在 `desktop/src/updates.ts` 中添加导出函数：

```ts
type ElectronReleaseNote = {
  version?: string;
  note?: string;
};

export function normalizeReleaseNotes(
  releaseNotes: string | ElectronReleaseNote[] | null | undefined,
): string | undefined {
  if (typeof releaseNotes === "string") {
    const trimmed = releaseNotes.trim();
    return trimmed ? trimmed : undefined;
  }

  if (!Array.isArray(releaseNotes)) {
    return undefined;
  }

  const sections = releaseNotes
    .map((entry) => {
      const note = entry.note?.trim();
      if (!note) {
        return "";
      }
      const version = entry.version?.trim();
      return version ? `## v${version.replace(/^v/, "")}\n\n${note}` : note;
    })
    .filter(Boolean);

  return sections.length ? sections.join("\n\n") : undefined;
}
```

在 `update-available` 发布状态中增加：

```ts
releaseNotes: normalizeReleaseNotes(info.releaseNotes),
```

- [ ] **步骤 5：运行桌面测试验证通过**

运行：

```powershell
cd desktop
rtk npm test -- updates
rtk npm run typecheck
```

预期：两个命令都 PASS。

- [ ] **步骤 6：Commit**

```powershell
rtk git add desktop/src/types.ts desktop/src/updates.ts desktop/test/updates.test.ts
rtk git commit -m "feat(桌面端): 更新状态携带公告内容"
```

## 任务 5：前端更新公告弹窗

**文件：**
- 修改：`frontend/src/types/desktop.d.ts`
- 修改：`frontend/src/components/molecules/DesktopUpdateButton.tsx`
- 修改：`frontend/src/components/molecules/DesktopUpdateButton.test.tsx`

- [ ] **步骤 1：编写弹窗行为测试**

在 `frontend/src/components/molecules/DesktopUpdateButton.test.tsx` 中新增测试：

```tsx
it("opens a release notes dialog when an update is available", async () => {
  window.autoEmailSender = buildDesktopApi({
    checkForUpdate: async () => ({
      state: "available",
      version: "2.1.5",
      nextVersion: "2.1.6",
      fullDownloadBytes: 200 * 1024 * 1024,
      releaseNotes: "# v2.1.6\n\n## 更新内容\n\n- 修复公告弹窗高度",
    }),
  });

  render(<DesktopUpdateButton />);
  fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));

  expect(await screen.findByRole("dialog", { name: /发现新版本 v2\.1\.6/ })).toBeInTheDocument();
  expect(screen.getByText("更新内容")).toBeInTheDocument();
  expect(screen.getByText("修复公告弹窗高度")).toBeInTheDocument();
  expect(screen.getByTestId("desktop-update-release-notes")).toHaveClass("max-h-[50vh]", "overflow-y-auto");
});

it("starts the selected download mode from the release notes dialog", async () => {
  const downloadUpdate = vi.fn(async () => ({
    state: "downloaded_pending_install" as const,
    version: "2.1.5",
    nextVersion: "2.1.6",
  }));
  window.autoEmailSender = buildDesktopApi({
    checkForUpdate: async () => ({
      state: "available",
      version: "2.1.5",
      nextVersion: "2.1.6",
      releaseNotes: "- 更新公告",
    }),
    downloadUpdate,
  });

  render(<DesktopUpdateButton />);
  fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));
  fireEvent.click(await screen.findByRole("button", { name: /全量下载/ }));

  await waitFor(() => {
    expect(downloadUpdate).toHaveBeenCalledWith({ mode: "full" });
  });
  expect(screen.queryByRole("dialog", { name: /发现新版本/ })).not.toBeInTheDocument();
});

it("uses fallback release notes and keeps the pending marker when users dismiss the dialog", async () => {
  window.autoEmailSender = buildDesktopApi({
    checkForUpdate: async () => ({
      state: "available",
      version: "2.1.5",
      nextVersion: "2.1.6",
    }),
  });

  render(<DesktopUpdateButton />);
  fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));
  expect(await screen.findByText("新版本已发布，更新内容暂不可用。")).toBeInTheDocument();

  fireEvent.click(await screen.findByRole("button", { name: /稍后/ }));
  expect(screen.queryByRole("dialog", { name: /发现新版本/ })).not.toBeInTheDocument();
  expect(await screen.findByText("NEW")).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行前端测试验证失败**

运行：

```powershell
cd frontend
rtk npm test -- DesktopUpdateButton
```

预期：FAIL，找不到 `dialog` 或 `desktop-update-release-notes`。

- [ ] **步骤 3：同步前端类型**

在 `frontend/src/types/desktop.d.ts` 中将 `available` 状态改为：

```ts
| {
    state: "available";
    version: string;
    nextVersion: string;
    fullDownloadBytes?: number;
    releaseNotes?: string;
  }
```

- [ ] **步骤 4：在 `DesktopUpdateButton` 中添加弹窗状态**

在 `DesktopUpdateButtonInner` 中新增状态：

```tsx
const [releaseDialogStatus, setReleaseDialogStatus] = useState<
  Extract<DesktopUpdateStatus, { state: "available" }> | null
>(null);
```

在 `handleStatus` 的 `available` 分支中追加：

```tsx
setReleaseDialogStatus(status);
```

在 `not_available` 分支清空：

```tsx
setReleaseDialogStatus(null);
```

- [ ] **步骤 5：添加公告弹窗组件**

在 `DesktopUpdateButton.tsx` 顶部增加 `ReactMarkdown`，并把现有 `lucide-react` import 改为包含 `X`：

```tsx
import ReactMarkdown from "react-markdown";
import { Loader2, RefreshCw, X } from "lucide-react";
```

在返回 JSX 中 `DesktopUpdateStatusBar` 后渲染：

```tsx
<DesktopUpdateReleaseNotesDialog
  status={releaseDialogStatus}
  onClose={() => setReleaseDialogStatus(null)}
  onStartDownload={(mode) => {
    setReleaseDialogStatus(null);
    void startDownload(mode);
  }}
/>
```

在文件底部新增组件：

```tsx
function DesktopUpdateReleaseNotesDialog({
  status,
  onClose,
  onStartDownload,
}: {
  status: Extract<DesktopUpdateStatus, { state: "available" }> | null;
  onClose: () => void;
  onStartDownload: (mode: DesktopUpdateDownloadMode) => void;
}) {
  if (status === null) {
    return null;
  }

  const releaseNotes = status.releaseNotes?.trim() || "新版本已发布，更新内容暂不可用。";

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-stone-950/35 p-4 backdrop-blur-md"
      role="presentation"
      onClick={onClose}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="desktop-update-release-title"
        className="w-full max-w-2xl overflow-hidden rounded-[28px] border border-stone-200/80 bg-white shadow-[0_34px_90px_-32px_rgba(41,37,36,0.55)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-stone-100 px-6 py-5">
          <div>
            <h3 id="desktop-update-release-title" className="text-lg font-semibold text-stone-900">
              发现新版本 v{status.nextVersion}
            </h3>
            <p className="mt-1 text-sm text-stone-500">
              当前 v{status.version} -&gt; v{status.nextVersion}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-900"
            aria-label="关闭更新公告"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div
          data-testid="desktop-update-release-notes"
          className="max-h-[50vh] overflow-y-auto px-6 py-5"
        >
          <article className="prose prose-sm max-w-none break-words text-stone-700 prose-headings:text-stone-900 prose-a:text-primary prose-code:text-stone-900">
            <ReactMarkdown>{releaseNotes}</ReactMarkdown>
          </article>
        </div>
        <div className="flex flex-wrap justify-end gap-3 border-t border-stone-100 px-6 py-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm font-medium text-stone-700 transition hover:border-stone-300 hover:bg-stone-50"
          >
            稍后
          </button>
          <button
            type="button"
            onClick={() => onStartDownload("differential")}
            className="rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm font-semibold text-stone-700 transition hover:border-primary/40 hover:text-primary"
          >
            增量下载
          </button>
          <button
            type="button"
            onClick={() => onStartDownload("full")}
            className="rounded-2xl bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-primary/20 transition hover:bg-primary/90"
          >
            全量下载
          </button>
        </div>
      </section>
    </div>
  );
}
```

- [ ] **步骤 6：运行前端测试验证通过**

运行：

```powershell
cd frontend
rtk npm test -- DesktopUpdateButton
rtk npm run lint
```

预期：两个命令都 PASS。

- [ ] **步骤 7：Commit**

```powershell
rtk git add frontend/src/types/desktop.d.ts frontend/src/components/molecules/DesktopUpdateButton.tsx frontend/src/components/molecules/DesktopUpdateButton.test.tsx
rtk git commit -m "feat(frontend): 展示桌面更新公告弹窗"
```

## 任务 6：端到端验证发布链路与构建

**文件：**
- 不新增或修改功能文件；本任务只运行验证命令。
- 如果验证失败，回到对应任务修复对应文件，再重新执行本任务的验证命令。

- [ ] **步骤 1：运行 PowerShell 脚本测试**

运行：

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\prepare-release.test.ps1
pwsh -NoLogo -NoProfile -File .\scripts\release-script.test.ps1
```

预期：两个命令都退出码 0。

- [ ] **步骤 2：运行桌面端测试与类型检查**

运行：

```powershell
cd desktop
rtk npm test
rtk npm run typecheck
```

预期：两个命令都 PASS。

- [ ] **步骤 3：运行前端测试、lint 和构建**

运行：

```powershell
cd frontend
rtk npm test
rtk npm run lint
rtk npm run build
```

预期：三个命令都 PASS。

- [ ] **步骤 4：检查发布准备脚本的真实输出路径**

运行：

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\prepare-release.ps1 9.9.8
```

预期：生成 `docs/releases/v9.9.8.md`，输出提示编辑该文件并运行 `release.ps1 9.9.8`。

验证后清理这次手动生成的测试文件：

```powershell
pwsh -NoLogo -NoProfile -Command "Remove-Item -LiteralPath 'docs/releases/v9.9.8.md' -Force"
```

- [ ] **步骤 5：检查最终 diff**

运行：

```powershell
rtk git status --short
rtk git diff --stat
rtk git diff --check
```

预期：只包含本功能相关文件；`git diff --check` 无输出。

- [ ] **步骤 6：最终 Commit**

如果步骤 1 到步骤 5 中有修正文件，提交修正：

```powershell
rtk git add scripts desktop frontend .github
rtk git commit -m "test(更新): 补充公告发布链路验证"
```

如果没有新增修正文件，跳过本步骤，不创建空提交。

## 规格覆盖自检

- 两段式发布：任务 1、任务 2、任务 3 覆盖。
- 严格缺公告失败：任务 3 覆盖。
- 手动润色 Markdown：任务 2 生成 `docs/releases/vX.Y.Z.md`，任务 3 强制使用该文件。
- GitHub Release 与应用内公告共用内容：任务 3 和任务 4 覆盖。
- 应用内弹窗展示完整 Markdown：任务 5 覆盖。
- 长公告内部滚动：任务 5 测试 `max-h-[50vh] overflow-y-auto`。
- 增量 / 全量下载按钮复用现有流程：任务 5 覆盖。
- 验证命令：任务 6 覆盖脚本、桌面端和前端。
