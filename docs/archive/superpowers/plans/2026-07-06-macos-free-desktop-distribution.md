# macOS 免费桌面分发实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 Auto Email Sender 增加免费 macOS 桌面分发能力：构建未签名 `.dmg`、支持首次打开文档指引、macOS 开机自启，以及应用内检查更新后跳转 GitHub Releases 手动下载。

**架构：** 继续复用现有 Electron 桌面壳和 PyInstaller 后端。Windows 维持现有 NSIS 与 `electron-updater` 自动下载/安装链路；macOS 新增平台分支，打包 `.dmg`，更新入口只检查 GitHub Releases 并打开下载页。官网文档和 release note 模板负责解释 macOS 首次打开的 Gatekeeper 放行流程。

**技术栈：** Electron、electron-builder、electron-updater、React/Vite、FastAPI、PyInstaller、uv、GitHub Actions、Vitest、unittest、VitePress。

---

## 文件结构

- 创建：`scripts/build-backend.sh`
  - 职责：在 macOS/Linux shell 环境构建 PyInstaller 后端目录，并执行 packaged runtime self-check。
- 修改：`backend/test/test_backend_build_script.py`
  - 职责：覆盖 macOS 后端构建脚本的 Playwright、PyInstaller collect 规则和 self-check。
- 修改：`desktop/src/backend.ts`
  - 职责：根据平台解析 packaged 后端可执行文件路径。
- 修改：`desktop/test/backend.test.ts`
  - 职责：覆盖 Windows 与 macOS packaged 后端路径。
- 修改：`desktop/electron-builder.yml`
  - 职责：保留 Windows NSIS 配置，新增 macOS `.dmg` target、artifact 命名、图标和未签名配置。
- 修改：`desktop/package.json`
  - 职责：新增 macOS pack/dist/publish 脚本。
- 创建：`desktop/build/icon.icns`
  - 职责：macOS 应用图标资源。
- 修改：`desktop/test/packaging.test.ts`
  - 职责：覆盖 macOS target、`icon.icns` 和 Windows 配置不回退。
- 修改：`desktop/src/startup.ts`
  - 职责：在 Windows 注册表逻辑之外新增 macOS login item 逻辑。
- 修改：`desktop/src/main.ts`
  - 职责：为 `startup.ts` 传入 Electron login item adapter。
- 修改：`desktop/test/startup.test.ts`
  - 职责：覆盖 macOS 开机自启读取、启用、禁用和开发模式不支持。
- 修改：`desktop/src/types.ts`
  - 职责：新增 macOS 手动下载更新状态。
- 修改：`frontend/src/types/desktop.d.ts`
  - 职责：同步桌面更新状态类型。
- 修改：`desktop/src/updates.ts`
  - 职责：macOS packaged 环境下检查 GitHub Releases 最新版本，返回手动下载状态；Windows 保持原 updater 流程。
- 修改：`desktop/test/updates.test.ts`
  - 职责：覆盖 release tag 解析、版本比较、macOS 手动下载状态和 Windows updater 配置保留。
- 修改：`frontend/src/lib/desktopApi.ts`
  - 职责：暴露 `openDesktopExternalUrl`，供 macOS 手动更新按钮打开 GitHub Releases。
- 修改：`frontend/src/lib/desktopApi.test.ts`
  - 职责：覆盖外链 API 代理。
- 修改：`frontend/src/components/molecules/DesktopUpdateButton.tsx`
  - 职责：识别 `manual_download_available`，展示“前往下载”而非下载/安装按钮。
- 修改：`frontend/src/components/molecules/DesktopUpdateButton.test.tsx`
  - 职责：覆盖 macOS 手动更新按钮、release notes 弹窗和不调用下载/安装 API。
- 修改：`.github/workflows/release.yml`
  - 职责：新增 macOS release job，构建并发布 `.dmg`。
- 修改：`scripts/release-notes.mjs`
  - 职责：生成同时包含 Windows 与 macOS 安装说明、更新说明的 release note 模板。
- 修改：`scripts/release-notes.test.mjs`
  - 职责：覆盖新 release note 模板。
- 修改：`scripts/prepare-release.test.sh`
  - 职责：断言 prepare 输出仍可生成包含 macOS 安装说明的公告模板。
- 修改：`website/docs/getting-started.md`
  - 职责：快速开始下载说明拆分 Windows/macOS，并说明 macOS 首次打开。
- 修改：`website/docs/install.md`
  - 职责：从 Windows 安装页改为桌面版安装页，增加 macOS 安装、自启和数据目录说明。
- 修改：`website/docs/faq.md`
  - 职责：更新不同平台的更新方式和常见安全提示。
- 修改：`README.md`
  - 职责：下载入口改为桌面版，而非只写 Windows。

---

### 任务 1：macOS 后端 PyInstaller 构建脚本

**文件：**
- 创建：`scripts/build-backend.sh`
- 修改：`backend/test/test_backend_build_script.py`

- [ ] **步骤 1：编写失败的脚本结构测试**

在 `backend/test/test_backend_build_script.py` 追加测试：

```python
    def test_macos_backend_build_script_matches_packaged_runtime_dependencies(self) -> None:
        script = Path(__file__).resolve().parents[1] / ".." / "scripts" / "build-backend.sh"
        content = script.resolve().read_text(encoding="utf-8")

        self.assertIn("set -euo pipefail", content)
        self.assertIn('PLAYWRIGHT_BROWSERS_PATH="$PlaywrightBrowsersDir"', content)
        self.assertIn("uv run python -m playwright install --only-shell chromium", content)
        self.assertIn("uv run pyinstaller", content)
        self.assertIn("--debug noarchive", content)
        self.assertIn("--hidden-import main", content)
        self.assertIn("--hidden-import aiosqlite", content)
        self.assertIn("--collect-all markitdown", content)
        self.assertIn("--collect-all mammoth", content)
        self.assertIn("--collect-all pdfminer", content)
        self.assertIn("--collect-all pdfplumber", content)
        self.assertIn("--collect-all pypdf", content)
        self.assertIn("--collect-all playwright", content)
        self.assertIn("--collect-all tiktoken", content)
        self.assertIn("--collect-submodules tiktoken_ext", content)
        self.assertIn("--hidden-import tiktoken_ext.openai_public", content)
        self.assertIn('--add-data "$AlembicIni:."', content)
        self.assertIn('--add-data "$AlembicDir:alembic"', content)
        self.assertIn('"$PackagedBackendExe" --self-check', content)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
rtk sh -lc "cd backend && uv run python -m unittest test.test_backend_build_script.BackendBuildScriptTest.test_macos_backend_build_script_matches_packaged_runtime_dependencies"
```

预期：FAIL，报错包含 `No such file or directory` 或缺少 `scripts/build-backend.sh`。

- [ ] **步骤 3：创建 `scripts/build-backend.sh`**

创建脚本：

```bash
#!/usr/bin/env bash
set -euo pipefail

Clean=0
while (($#)); do
  case "$1" in
    --clean|-Clean)
      Clean=1
      shift
      ;;
    *)
      echo "用法: scripts/build-backend.sh [--clean]" >&2
      exit 2
      ;;
  esac
done

RepoRoot="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BackendDir="$RepoRoot/backend"
AlembicIni="$BackendDir/alembic.ini"
AlembicDir="$BackendDir/alembic"
PlaywrightBrowsersDir="$BackendDir/ms-playwright"

cd "$BackendDir"

if ((Clean)); then
  rm -rf build dist ms-playwright
fi

uv sync --dev
export PLAYWRIGHT_BROWSERS_PATH="$PlaywrightBrowsersDir"
uv run python -m playwright install --only-shell chromium
uv run pyinstaller \
  --noconfirm \
  --clean \
  --onedir \
  --debug noarchive \
  --name backend \
  --specpath build \
  --hidden-import main \
  --hidden-import aiosqlite \
  --collect-all markitdown \
  --collect-all mammoth \
  --collect-all pdfminer \
  --collect-all pdfplumber \
  --collect-all pypdf \
  --collect-all playwright \
  --collect-all tiktoken \
  --collect-submodules tiktoken_ext \
  --hidden-import tiktoken_ext.openai_public \
  --add-data "$AlembicIni:." \
  --add-data "$AlembicDir:alembic" \
  desktop_entry.py

PackagedBackendExe="$BackendDir/dist/backend/backend"
"$PackagedBackendExe" --self-check
```

再赋予执行权限：

```bash
chmod +x scripts/build-backend.sh
```

- [ ] **步骤 4：运行脚本测试验证通过**

运行：

```bash
rtk sh -lc "cd backend && uv run python -m unittest test.test_backend_build_script.BackendBuildScriptTest.test_macos_backend_build_script_matches_packaged_runtime_dependencies"
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
rtk git add scripts/build-backend.sh backend/test/test_backend_build_script.py
rtk git commit -m "build(backend): add macOS packaged backend script"
```

---

### 任务 2：平台化 packaged 后端路径与 macOS 打包配置

**文件：**
- 修改：`desktop/src/backend.ts`
- 修改：`desktop/test/backend.test.ts`
- 修改：`desktop/electron-builder.yml`
- 修改：`desktop/package.json`
- 创建：`desktop/build/icon.icns`
- 修改：`desktop/test/packaging.test.ts`

- [ ] **步骤 1：编写失败的后端路径测试**

在 `desktop/test/backend.test.ts` 的 packaged path 测试附近加入：

```ts
  it("resolves packaged backend executable path on macOS", () => {
    expect(
      getBackendExecutablePath({
        isPackaged: true,
        platform: "darwin",
        resourcesPath: "/Applications/Auto Email Sender.app/Contents/Resources",
        repoRoot: "/repo",
      }),
    ).toBe(path.join("/Applications/Auto Email Sender.app/Contents/Resources", "backend", "backend"));
  });
```

同时把现有 Windows packaged path 调用补上 `platform: "win32"`。

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
rtk sh -lc "cd desktop && npm test -- backend.test.ts"
```

预期：FAIL，TypeScript 或断言提示 `BackendPathInput` 不接受 `platform`，或 macOS 路径仍返回 `backend.exe`。

- [ ] **步骤 3：修改路径输入类型与解析逻辑**

在 `desktop/src/types.ts` 中扩展：

```ts
export type BackendPathInput = {
  isPackaged: boolean;
  platform?: NodeJS.Platform;
  resourcesPath: string;
  repoRoot: string;
};
```

在 `desktop/src/backend.ts` 中替换 `getBackendExecutablePath`：

```ts
export function getBackendExecutablePath(input: BackendPathInput): string {
  if (input.isPackaged) {
    const executableName = (input.platform ?? process.platform) === "win32" ? "backend.exe" : "backend";
    return path.join(input.resourcesPath, "backend", executableName);
  }
  return path.join(input.repoRoot, "backend", "desktop_entry.py");
}
```

在 `startBackend` 调用处传入平台：

```ts
  const backendPath = getBackendExecutablePath({
    ...options,
    platform: process.platform,
  });
```

- [ ] **步骤 4：运行后端路径测试验证通过**

运行：

```bash
rtk sh -lc "cd desktop && npm test -- backend.test.ts"
```

预期：PASS。

- [ ] **步骤 5：编写失败的 macOS 打包配置测试**

在 `desktop/test/packaging.test.ts` 追加：

```ts
describe("macOS desktop packaging", () => {
  it("builds an unsigned dmg with a macOS icon", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain("mac:");
    expect(config).toContain("target: dmg");
    expect(config).toContain("icon: build/icon.icns");
    expect(config).toContain("identity: null");
    expect(existsSync(path.resolve("build", "icon.icns"))).toBe(true);
  });

  it("keeps platform-specific artifact names", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain('artifactName: "AutoEmailSender Setup ${version}.${ext}"');
    expect(config).toContain('artifactName: "AutoEmailSender-${version}-${arch}.${ext}"');
  });

  it("declares macOS package scripts without changing Windows scripts", () => {
    const packageJson = readFileSync(path.resolve("package.json"), "utf8");

    expect(packageJson).toContain('"dist": "npm run build && electron-builder --config electron-builder.yml --win nsis --publish never"');
    expect(packageJson).toContain('"dist:mac": "npm run build && electron-builder --config electron-builder.yml --mac dmg --publish never"');
    expect(packageJson).toContain('"publish:mac": "npm run build && electron-builder --config electron-builder.yml --mac dmg --publish always"');
  });
});
```

- [ ] **步骤 6：运行打包配置测试验证失败**

运行：

```bash
rtk sh -lc "cd desktop && npm test -- packaging.test.ts"
```

预期：FAIL，缺少 `mac:`、`icon.icns` 或 macOS scripts。

- [ ] **步骤 7：更新 `desktop/electron-builder.yml`**

把全局 `artifactName` 移入平台配置，并新增 macOS 配置。目标结构：

```yaml
appId: com.juniexd.autoemailsender
productName: Auto Email Sender
asar: true
directories:
  output: release
files:
  - dist/**
  - package.json
extraResources:
  - from: ../frontend/dist
    to: frontend
  - from: ../backend/dist/backend
    to: backend
  - from: ../backend/ms-playwright
    to: ms-playwright
  - from: build/icon.ico
    to: build/icon.ico
win:
  artifactName: "AutoEmailSender Setup ${version}.${ext}"
  icon: build/icon.ico
  target:
    - target: nsis
      arch:
        - x64
mac:
  artifactName: "AutoEmailSender-${version}-${arch}.${ext}"
  icon: build/icon.icns
  category: public.app-category.productivity
  identity: null
  target:
    - target: dmg
      arch:
        - arm64
```

保留原有 `nsis`、`publish` 和 `releaseInfo` 配置。

- [ ] **步骤 8：更新 `desktop/package.json` 脚本**

在 `scripts` 中新增：

```json
"pack:mac": "npm run build && electron-builder --config electron-builder.yml --mac --dir --publish never",
"dist:mac": "npm run build && electron-builder --config electron-builder.yml --mac dmg --publish never",
"publish:mac": "npm run build && electron-builder --config electron-builder.yml --mac dmg --publish always"
```

不要修改现有 `pack`、`dist`、`publish` 的 Windows 行为。

- [ ] **步骤 9：生成 `desktop/build/icon.icns`**

在 macOS 上运行：

```bash
rtk sh -lc 'mkdir -p desktop/build/icon.iconset && \
sips -z 16 16 desktop/build/icon.png --out desktop/build/icon.iconset/icon_16x16.png >/dev/null && \
sips -z 32 32 desktop/build/icon.png --out desktop/build/icon.iconset/icon_16x16@2x.png >/dev/null && \
sips -z 32 32 desktop/build/icon.png --out desktop/build/icon.iconset/icon_32x32.png >/dev/null && \
sips -z 64 64 desktop/build/icon.png --out desktop/build/icon.iconset/icon_32x32@2x.png >/dev/null && \
sips -z 128 128 desktop/build/icon.png --out desktop/build/icon.iconset/icon_128x128.png >/dev/null && \
sips -z 256 256 desktop/build/icon.png --out desktop/build/icon.iconset/icon_128x128@2x.png >/dev/null && \
sips -z 256 256 desktop/build/icon.png --out desktop/build/icon.iconset/icon_256x256.png >/dev/null && \
cp desktop/build/icon.png desktop/build/icon.iconset/icon_256x256@2x.png && \
iconutil -c icns desktop/build/icon.iconset -o desktop/build/icon.icns && \
rm -rf desktop/build/icon.iconset'
```

预期：`desktop/build/icon.icns` 存在。由于源图是 256px，`icon_256x256@2x.png` 会复用现有图；后续若有 1024px 源图再替换。

- [ ] **步骤 10：运行相关测试验证通过**

运行：

```bash
rtk sh -lc "cd desktop && npm test -- backend.test.ts packaging.test.ts"
```

预期：PASS。

- [ ] **步骤 11：Commit**

```bash
rtk git add desktop/src/types.ts desktop/src/backend.ts desktop/test/backend.test.ts desktop/electron-builder.yml desktop/package.json desktop/test/packaging.test.ts desktop/build/icon.icns
rtk git commit -m "build(desktop): add unsigned macOS dmg packaging"
```

---

### 任务 3：macOS 开机自启

**文件：**
- 修改：`desktop/src/startup.ts`
- 修改：`desktop/src/main.ts`
- 修改：`desktop/test/startup.test.ts`

- [ ] **步骤 1：编写失败的 macOS startup 测试**

在 `desktop/test/startup.test.ts` 追加：

```ts
describe("startup at login macOS service", () => {
  const macExecutablePath = "/Applications/Auto Email Sender.app/Contents/MacOS/Auto Email Sender";

  it("reads macOS login item status", async () => {
    const loginItems = {
      getLoginItemSettings: vi.fn(() => ({ openAtLogin: true })),
      setLoginItemSettings: vi.fn(),
    };

    await expect(
      getStartupAtLoginStatus({
        platform: "darwin",
        isPackaged: true,
        executablePath: macExecutablePath,
        dependencies: { loginItems },
      }),
    ).resolves.toEqual({ supported: true, enabled: true });
    expect(loginItems.getLoginItemSettings).toHaveBeenCalled();
  });

  it("enables macOS login item with startup args", async () => {
    const loginItems = {
      getLoginItemSettings: vi.fn(() => ({ openAtLogin: true })),
      setLoginItemSettings: vi.fn(),
    };

    await expect(
      setStartupAtLoginEnabled({
        platform: "darwin",
        isPackaged: true,
        executablePath: macExecutablePath,
        dependencies: { loginItems },
      }, true),
    ).resolves.toEqual({ supported: true, enabled: true });
    expect(loginItems.setLoginItemSettings).toHaveBeenCalledWith({
      openAtLogin: true,
      args: ["--startup"],
    });
  });

  it("disables macOS login item", async () => {
    const loginItems = {
      getLoginItemSettings: vi.fn(() => ({ openAtLogin: false })),
      setLoginItemSettings: vi.fn(),
    };

    await expect(
      setStartupAtLoginEnabled({
        platform: "darwin",
        isPackaged: true,
        executablePath: macExecutablePath,
        dependencies: { loginItems },
      }, false),
    ).resolves.toEqual({ supported: true, enabled: false });
    expect(loginItems.setLoginItemSettings).toHaveBeenCalledWith({
      openAtLogin: false,
      args: [],
    });
  });
});
```

Update the existing unsupported test name to expect Linux unsupported and unpackaged Windows/macOS unsupported:

```ts
  it("only supports packaged Windows and macOS builds", async () => {
    await expect(
      getStartupAtLoginStatus({ platform: "linux", isPackaged: true, executablePath }),
    ).resolves.toMatchObject({ supported: false, enabled: false });
    await expect(
      getStartupAtLoginStatus({ platform: "win32", isPackaged: false, executablePath }),
    ).resolves.toMatchObject({ supported: false, enabled: false });
    await expect(
      getStartupAtLoginStatus({ platform: "darwin", isPackaged: false, executablePath }),
    ).resolves.toMatchObject({ supported: false, enabled: false });
  });
```

- [ ] **步骤 2：运行 startup 测试验证失败**

运行：

```bash
rtk sh -lc "cd desktop && npm test -- startup.test.ts"
```

预期：FAIL，`dependencies.loginItems` 类型不存在或 macOS 仍返回不支持。

- [ ] **步骤 3：扩展 `desktop/src/startup.ts` 类型与 macOS 分支**

添加类型：

```ts
type MacLoginItemSettings = {
  openAtLogin?: boolean;
};

type MacLoginItemAdapter = {
  getLoginItemSettings: () => MacLoginItemSettings;
  setLoginItemSettings: (settings: { openAtLogin: boolean; args: string[] }) => void;
};
```

扩展 `StartupAtLoginInput.dependencies`：

```ts
  dependencies?: {
    execFile?: typeof execFile;
    loginItems?: MacLoginItemAdapter;
  };
```

在 `getStartupAtLoginStatus` 中 registry 逻辑前加入：

```ts
  if (input.platform === "darwin") {
    const settings = getMacLoginItems(input).getLoginItemSettings();
    return {
      supported: true,
      enabled: Boolean(settings.openAtLogin),
    };
  }
```

在 `setStartupAtLoginEnabled` 中 registry 逻辑前加入：

```ts
  if (input.platform === "darwin") {
    getMacLoginItems(input).setLoginItemSettings({
      openAtLogin: enabled,
      args: enabled ? ["--startup"] : [],
    });
    return getStartupAtLoginStatus(input);
  }
```

修改 `getUnsupportedStatus`：

```ts
  if (input.platform !== "win32" && input.platform !== "darwin") {
    return {
      supported: false,
      enabled: false,
      message: "当前平台不支持开机自启动。",
    };
  }
```

添加 helper：

```ts
function getMacLoginItems(input: StartupAtLoginInput): MacLoginItemAdapter {
  const loginItems = input.dependencies?.loginItems;
  if (!loginItems) {
    throw new Error("macOS 开机自启动接口未初始化。");
  }
  return loginItems;
}
```

- [ ] **步骤 4：在 `desktop/src/main.ts` 传入 Electron login item adapter**

把 `getStartupInput` 改为：

```ts
function getStartupInput() {
  return {
    platform: process.platform,
    isPackaged: app.isPackaged,
    executablePath: process.execPath,
    dependencies:
      process.platform === "darwin"
        ? {
            loginItems: {
              getLoginItemSettings: () => app.getLoginItemSettings(),
              setLoginItemSettings: (settings: { openAtLogin: boolean; args: string[] }) => {
                app.setLoginItemSettings(settings);
              },
            },
          }
        : undefined,
  };
}
```

- [ ] **步骤 5：运行 startup 测试验证通过**

运行：

```bash
rtk sh -lc "cd desktop && npm test -- startup.test.ts"
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
rtk git add desktop/src/startup.ts desktop/src/main.ts desktop/test/startup.test.ts
rtk git commit -m "feat(desktop): support macOS startup at login"
```

---

### 任务 4：macOS 手动更新状态机

**文件：**
- 修改：`desktop/src/types.ts`
- 修改：`frontend/src/types/desktop.d.ts`
- 修改：`desktop/src/updates.ts`
- 修改：`desktop/test/updates.test.ts`

- [ ] **步骤 1：编写失败的更新 helper 测试**

在 `desktop/test/updates.test.ts` 的 import 中加入：

```ts
  buildManualDownloadStatus,
  compareReleaseVersions,
  normalizeReleaseTag,
```

追加测试：

```ts
  it("normalizes GitHub release tags for comparison", () => {
    expect(normalizeReleaseTag("v2.4.0")).toBe("2.4.0");
    expect(normalizeReleaseTag("2.4.0")).toBe("2.4.0");
    expect(normalizeReleaseTag(" v2.4.0 ")).toBe("2.4.0");
  });

  it("compares semantic release versions", () => {
    expect(compareReleaseVersions("2.4.0", "2.3.9")).toBeGreaterThan(0);
    expect(compareReleaseVersions("2.3.8", "2.3.8")).toBe(0);
    expect(compareReleaseVersions("2.3.8", "2.4.0")).toBeLessThan(0);
  });

  it("builds manual download status for newer macOS releases", () => {
    expect(
      buildManualDownloadStatus({
        currentVersion: "2.3.8",
        release: {
          tag_name: "v2.4.0",
          html_url: "https://github.com/JunieXD/AutoEmailSender/releases/tag/v2.4.0",
          body: "## 更新内容\n\n- 支持 macOS",
        },
      }),
    ).toEqual({
      state: "manual_download_available",
      version: "2.3.8",
      nextVersion: "2.4.0",
      releaseUrl: "https://github.com/JunieXD/AutoEmailSender/releases/tag/v2.4.0",
      releaseNotes: "## 更新内容\n\n- 支持 macOS",
    });
  });

  it("returns not available when GitHub latest release is current", () => {
    expect(
      buildManualDownloadStatus({
        currentVersion: "2.4.0",
        release: {
          tag_name: "v2.4.0",
          html_url: "https://github.com/JunieXD/AutoEmailSender/releases/tag/v2.4.0",
        },
      }),
    ).toEqual({ state: "not_available", version: "2.4.0" });
  });
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
rtk sh -lc "cd desktop && npm test -- updates.test.ts"
```

预期：FAIL，新增 helper 未导出。

- [ ] **步骤 3：新增更新状态类型**

在 `desktop/src/types.ts` 的 `UpdateStatus` union 中新增：

```ts
  | {
      state: "manual_download_available";
      version: string;
      nextVersion: string;
      releaseUrl: string;
      releaseNotes?: string;
    }
```

在 `frontend/src/types/desktop.d.ts` 的 `DesktopUpdateStatus` union 中同步新增同一状态。

- [ ] **步骤 4：实现 `desktop/src/updates.ts` 纯 helper**

在文件顶部常量区加入：

```ts
const GITHUB_LATEST_RELEASE_API_URL =
  "https://api.github.com/repos/JunieXD/AutoEmailSender/releases/latest";
const GITHUB_RELEASES_URL = "https://github.com/JunieXD/AutoEmailSender/releases";
```

加入类型和函数：

```ts
type GitHubLatestRelease = {
  tag_name?: string;
  html_url?: string;
  body?: string | null;
};

export function normalizeReleaseTag(tagName: string): string {
  return tagName.trim().replace(/^v/i, "");
}

export function compareReleaseVersions(left: string, right: string): number {
  const leftParts = normalizeReleaseTag(left).split(".").map((part) => Number.parseInt(part, 10) || 0);
  const rightParts = normalizeReleaseTag(right).split(".").map((part) => Number.parseInt(part, 10) || 0);
  const length = Math.max(leftParts.length, rightParts.length);
  for (let index = 0; index < length; index += 1) {
    const difference = (leftParts[index] ?? 0) - (rightParts[index] ?? 0);
    if (difference !== 0) {
      return difference;
    }
  }
  return 0;
}

export function buildManualDownloadStatus(input: {
  currentVersion: string;
  release: GitHubLatestRelease;
}): UpdateStatus {
  const nextVersion = input.release.tag_name ? normalizeReleaseTag(input.release.tag_name) : "";
  if (!nextVersion || compareReleaseVersions(nextVersion, input.currentVersion) <= 0) {
    return { state: "not_available", version: input.currentVersion };
  }

  return {
    state: "manual_download_available",
    version: input.currentVersion,
    nextVersion,
    releaseUrl: input.release.html_url ?? GITHUB_RELEASES_URL,
    ...(input.release.body ? { releaseNotes: input.release.body } : {}),
  };
}
```

- [ ] **步骤 5：接入 macOS IPC 检查更新分支**

在 `registerUpdateIpc` 的 `ipcMain.handle("update:check", ...)` 中，把 packaged 检查逻辑改为：

```ts
    if (!app.isPackaged) {
      currentStatus = { state: "not_available", version: app.getVersion() };
      return currentStatus;
    }
    if (process.platform === "darwin") {
      publish(getWindow, { state: "checking", version: app.getVersion() });
      const response = await fetch(GITHUB_LATEST_RELEASE_API_URL, {
        headers: { Accept: "application/vnd.github+json" },
      });
      if (!response.ok) {
        throw new Error(`GitHub Releases 检查失败：HTTP ${response.status}`);
      }
      const release = (await response.json()) as GitHubLatestRelease;
      const status = buildManualDownloadStatus({
        currentVersion: app.getVersion(),
        release,
      });
      publish(getWindow, status);
      return status;
    }
```

在 `checkForUpdatesOnStartup` 开头加入：

```ts
  if (process.platform === "darwin") {
    return;
  }
```

保留 Windows 现有 `autoUpdater.checkForUpdates()` 流程。

- [ ] **步骤 6：运行更新测试验证通过**

运行：

```bash
rtk sh -lc "cd desktop && npm test -- updates.test.ts"
```

预期：PASS。

- [ ] **步骤 7：运行 desktop 类型检查**

运行：

```bash
rtk sh -lc "cd desktop && npm run typecheck"
```

预期：PASS。

- [ ] **步骤 8：Commit**

```bash
rtk git add desktop/src/types.ts frontend/src/types/desktop.d.ts desktop/src/updates.ts desktop/test/updates.test.ts
rtk git commit -m "feat(desktop): check macOS updates as manual downloads"
```

---

### 任务 5：前端更新入口支持 macOS 手动下载

**文件：**
- 修改：`frontend/src/lib/desktopApi.ts`
- 修改：`frontend/src/lib/desktopApi.test.ts`
- 修改：`frontend/src/components/molecules/DesktopUpdateButton.tsx`
- 修改：`frontend/src/components/molecules/DesktopUpdateButton.test.tsx`

- [ ] **步骤 1：编写失败的 desktop API 外链测试**

在 `frontend/src/lib/desktopApi.test.ts` import 中加入：

```ts
  openDesktopExternalUrl,
```

追加测试：

```ts
  it("opens external urls through the desktop bridge", async () => {
    const openExternalUrl = vi.fn(async () => undefined);
    window.autoEmailSender = buildDesktopApi({ openExternalUrl });

    await openDesktopExternalUrl("https://github.com/JunieXD/AutoEmailSender/releases");

    expect(openExternalUrl).toHaveBeenCalledWith("https://github.com/JunieXD/AutoEmailSender/releases");
  });
```

- [ ] **步骤 2：运行 API 测试验证失败**

运行：

```bash
rtk sh -lc "cd frontend && npm run test -- src/lib/desktopApi.test.ts"
```

预期：FAIL，`openDesktopExternalUrl` 未导出。

- [ ] **步骤 3：实现 `openDesktopExternalUrl`**

在 `frontend/src/lib/desktopApi.ts` 中加入：

```ts
export async function openDesktopExternalUrl(url: string): Promise<void> {
  const api = getDesktopApi();
  if (!api.openExternalUrl) {
    throw new Error("当前桌面应用版本不支持打开外部链接");
  }
  await api.openExternalUrl(url);
}
```

- [ ] **步骤 4：运行 API 测试验证通过**

运行：

```bash
rtk sh -lc "cd frontend && npm run test -- src/lib/desktopApi.test.ts"
```

预期：PASS。

- [ ] **步骤 5：编写失败的手动更新 UI 测试**

在 `frontend/src/components/molecules/DesktopUpdateButton.test.tsx` 追加：

```tsx
  it("opens GitHub Releases for macOS manual updates", async () => {
    const openExternalUrl = vi.fn(async () => undefined);
    const downloadUpdate = vi.fn();
    const quitAndInstall = vi.fn();
    window.autoEmailSender = buildDesktopApi({
      openExternalUrl,
      downloadUpdate,
      quitAndInstall,
      checkForUpdate: async () => ({
        state: "manual_download_available",
        version: "2.3.8",
        nextVersion: "2.4.0",
        releaseUrl: "https://github.com/JunieXD/AutoEmailSender/releases/tag/v2.4.0",
        releaseNotes: "- 支持 macOS",
      }),
    });

    render(<DesktopUpdateButton />);
    fireEvent.click(await screen.findByRole("button", { name: /检查更新/ }));

    const dialog = await screen.findByRole("dialog", { name: /发现新版本 v2\.4\.0/ });
    expect(within(dialog).getByText("支持 macOS")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /差量下载/ })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /全量下载/ })).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: /前往下载/ }));

    await waitFor(() => {
      expect(openExternalUrl).toHaveBeenCalledWith(
        "https://github.com/JunieXD/AutoEmailSender/releases/tag/v2.4.0",
      );
    });
    expect(downloadUpdate).not.toHaveBeenCalled();
    expect(quitAndInstall).not.toHaveBeenCalled();
  });
```

- [ ] **步骤 6：运行组件测试验证失败**

运行：

```bash
rtk sh -lc "cd frontend && npm run test -- src/components/molecules/DesktopUpdateButton.test.tsx"
```

预期：FAIL，组件不处理 `manual_download_available`。

- [ ] **步骤 7：更新 `DesktopUpdateButton.tsx` 状态处理**

在 import 中加入：

```ts
  openDesktopExternalUrl,
```

把 `releaseDialogStatus` 类型扩展为：

```ts
  const [releaseDialogStatus, setReleaseDialogStatus] = useState<
    Extract<DesktopUpdateStatus, { state: "available" | "manual_download_available" }> | null
  >(null);
```

在 `handleStatus` 中把 available 分支改为：

```ts
      if (status.state === "available" || status.state === "manual_download_available") {
        setChecking(false);
        setPendingVersion(status.nextVersion);
        writePendingVersion(status.nextVersion);
        setReleaseDialogStatus(status);
        return;
      }
```

新增 callback：

```ts
  const openManualDownload = useCallback(
    async (url: string) => {
      try {
        await openDesktopExternalUrl(url);
      } catch (openError) {
        const message = openError instanceof Error ? openError.message : "打开下载页失败";
        notifyError("打开下载页失败", message);
      }
    },
    [notifyError],
  );
```

传给 dialog：

```tsx
        <DesktopUpdateReleaseNotesDialog
          status={releaseDialogStatus}
          onClose={() => setReleaseDialogStatus(null)}
          onOpenManualDownload={(url) => {
            setReleaseDialogStatus(null);
            void openManualDownload(url);
          }}
          onStartDownload={(mode) => {
            setReleaseDialogStatus(null);
            void startDownload(mode);
          }}
        />
```

- [ ] **步骤 8：更新 release notes dialog**

把 props 改为：

```ts
}: {
  status: Extract<DesktopUpdateStatus, { state: "available" | "manual_download_available" }> | null;
  onClose: () => void;
  onOpenManualDownload: (url: string) => void;
  onStartDownload: (mode: DesktopUpdateDownloadMode) => void;
}) {
```

底部按钮区域中按状态分支：

```tsx
          {status.state === "manual_download_available" ? (
            <button
              type="button"
              onClick={() => onOpenManualDownload(status.releaseUrl)}
              className="rounded-2xl bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-primary/20 transition hover:bg-primary/90"
            >
              前往下载
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={() => onStartDownload("full")}
                className="rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm font-semibold text-stone-700 transition hover:border-primary/40 hover:text-primary"
              >
                全量下载
              </button>
              <button
                type="button"
                onClick={() => onStartDownload("differential")}
                className="rounded-2xl bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-primary/20 transition hover:bg-primary/90"
              >
                差量下载
              </button>
            </>
          )}
```

在 `DesktopUpdateStatusBar` 中新增状态分支：

```tsx
  if (status.state === "manual_download_available") {
    return (
      <span className="inline-flex min-h-[2.8rem] items-center gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2 text-xs text-stone-600 shadow-sm">
        <span className="font-medium text-stone-800">发现 v{status.nextVersion}</span>
        <span>macOS 版需要前往 GitHub Releases 手动下载新版安装包。</span>
      </span>
    );
  }
```

- [ ] **步骤 9：运行前端测试验证通过**

运行：

```bash
rtk sh -lc "cd frontend && npm run test -- src/lib/desktopApi.test.ts src/components/molecules/DesktopUpdateButton.test.tsx"
```

预期：PASS。

- [ ] **步骤 10：运行前端类型检查和 lint**

运行：

```bash
rtk sh -lc "cd frontend && npm run lint && npm run build"
```

预期：PASS。

- [ ] **步骤 11：Commit**

```bash
rtk git add frontend/src/lib/desktopApi.ts frontend/src/lib/desktopApi.test.ts frontend/src/components/molecules/DesktopUpdateButton.tsx frontend/src/components/molecules/DesktopUpdateButton.test.tsx
rtk git commit -m "feat(frontend): guide macOS users to manual updates"
```

---

### 任务 6：macOS Release CI

**文件：**
- 修改：`.github/workflows/release.yml`

- [ ] **步骤 1：更新 release workflow**

把 workflow name 改为：

```yaml
name: Release Desktop
```

保留 `build-windows` job，新增 `build-macos` job：

```yaml
  build-macos:
    runs-on: macos-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: 24

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Setup uv
        uses: astral-sh/setup-uv@v5

      - name: Install frontend dependencies
        working-directory: frontend
        run: npm ci

      - name: Build frontend
        working-directory: frontend
        run: npm run build

      - name: Install backend dependencies
        working-directory: backend
        run: uv sync --dev

      - name: Build backend executable
        run: ./scripts/build-backend.sh --clean

      - name: Install desktop dependencies
        working-directory: desktop
        run: npm ci

      - name: Test desktop
        working-directory: desktop
        run: npm test

      - name: Build and publish macOS desktop release
        working-directory: desktop
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          CSC_IDENTITY_AUTO_DISCOVERY: false
        run: npm run publish:mac
```

- [ ] **步骤 2：本地 YAML sanity check**

运行：

```bash
rtk sh -lc "ruby -e 'require \"yaml\"; YAML.load_file(\".github/workflows/release.yml\"); puts \"release workflow yaml ok\"'"
```

预期：输出 `release workflow yaml ok`。

- [ ] **步骤 3：Commit**

```bash
rtk git add .github/workflows/release.yml
rtk git commit -m "ci: publish macOS desktop releases"
```

---

### 任务 7：官网文档与 release note 模板

**文件：**
- 修改：`scripts/release-notes.mjs`
- 修改：`scripts/release-notes.test.mjs`
- 修改：`scripts/prepare-release.test.sh`
- 修改：`website/docs/getting-started.md`
- 修改：`website/docs/install.md`
- 修改：`website/docs/faq.md`
- 修改：`README.md`

- [ ] **步骤 1：编写失败的 release note 模板测试**

在 `scripts/release-notes.test.mjs` 第一条测试中，把安装和更新断言替换为：

```js
    expect(notes).toContain("Windows 用户下载 `AutoEmailSender Setup 2.0.2.exe`");
    expect(notes).toContain("macOS 用户下载 `AutoEmailSender-2.0.2-arm64.dmg`");
    expect(notes).toContain("系统设置 > 隐私与安全性");
    expect(notes).toContain("Windows：应用内可下载并安装更新。");
    expect(notes).toContain("macOS：应用内可检查更新，发现新版本后会打开 GitHub Releases 手动下载新版 `.dmg`。");
```

删除旧断言：

```js
    expect(notes).toContain("普通用户只需下载 `AutoEmailSender Setup 2.0.2.exe`");
    expect(notes).toContain("发现新版本后，可以选择增量下载或全量下载。");
```

在 `scripts/prepare-release.test.sh` 中追加：

```bash
assert_contains "$notes" "macOS 用户下载" "公告模板缺少 macOS 安装说明。"
assert_contains "$notes" "系统设置 > 隐私与安全性" "公告模板缺少 macOS 首次打开说明。"
```

- [ ] **步骤 2：运行 release note 测试验证失败**

运行：

```bash
rtk sh -lc "cd frontend && npx vitest run ../scripts/release-notes.test.mjs"
rtk sh scripts/prepare-release.test.sh
```

预期：第一条命令或第二条命令 FAIL，缺少 macOS 文案。

- [ ] **步骤 3：更新 `scripts/release-notes.mjs` 模板**

把安装说明和自动更新部分改为：

```js
    "## 安装说明",
    "",
    `- Windows 用户下载 \`${installerName}\`。`,
    `- macOS 用户下载 \`AutoEmailSender-${normalizedVersion}-arm64.dmg\`，打开后拖到“应用程序”。首次打开若提示无法验证开发者，到“系统设置 > 隐私与安全性”点击“仍要打开”，再确认打开。`,
    "",
    "## 自动更新",
    "",
    "- Windows：应用内可下载并安装更新。",
    "- macOS：应用内可检查更新，发现新版本后会打开 GitHub Releases 手动下载新版 `.dmg`。",
    "",
```

- [ ] **步骤 4：更新官网快速开始页**

把 `website/docs/getting-started.md` 的“下载安装”改为：

```md
## 下载安装

1. 打开 [GitHub Releases](https://github.com/JunieXD/AutoEmailSender/releases)。
2. Windows 下载 `AutoEmailSender Setup x.y.z.exe`，双击安装后从开始菜单或桌面快捷方式打开。
3. macOS 下载 `AutoEmailSender-x.y.z-arm64.dmg`，打开后把应用拖到“应用程序”。
4. macOS 首次打开若提示无法验证开发者，到“系统设置 > 隐私与安全性”点击“仍要打开”，再确认打开。

请只从本项目 GitHub Releases 页面下载安装包。Windows 安装包暂未购买代码签名证书，看到「未知发布者」或 SmartScreen 提示时，请确认下载来源后再继续。
```

把“更新”改为：

```md
## 更新

应用会检查 GitHub Releases 的新版本。Windows 版可在应用内下载并安装更新；macOS 版发现新版本后会打开 GitHub Releases，由你下载新版 `.dmg` 后手动替换安装。
```

- [ ] **步骤 5：更新安装页**

把 `website/docs/install.md` 标题改为：

```md
# 安装桌面版
```

新增 macOS 下载和安装段落，保留 Windows 段落：

```md
## 下载位置

[打开 GitHub Releases](https://github.com/JunieXD/AutoEmailSender/releases)

- Windows 下载 `AutoEmailSender Setup x.y.z.exe`。
- macOS Apple Silicon 下载 `AutoEmailSender-x.y.z-arm64.dmg`。

## Windows 安装步骤

1. 双击安装包。
2. 按安装向导选择安装位置。
3. 根据需要创建桌面快捷方式。
4. 安装完成后，从开始菜单或桌面快捷方式启动。

## macOS 安装步骤

1. 打开 `.dmg`，把 `Auto Email Sender.app` 拖到“应用程序”。
2. 首次打开若提示无法验证开发者，到“系统设置 > 隐私与安全性”点击“仍要打开”。
3. 再次确认“打开”后即可正常使用。
```

把数据目录段落改为：

```md
Windows 安装版数据目录：

`C:\Users\<你的用户名>\AppData\Roaming\Auto Email Sender`

macOS 安装版数据目录：

`~/Library/Application Support/Auto Email Sender`
```

- [ ] **步骤 6：更新 FAQ**

把“如何更新”改为：

```md
## 如何更新

应用会检查 GitHub Releases 的新版本。Windows 版可在应用内下载并安装更新；macOS 版会打开 GitHub Releases 下载页，你需要下载新版 `.dmg` 后手动拖到“应用程序”覆盖安装。
```

在安全提示附近加入：

```md
## macOS 为什么提示无法验证开发者

当前 macOS 版未购买 Apple Developer Program 账号签名，因此首次打开会被系统拦截。请确认安装包来自本项目 GitHub Releases 页面，然后到“系统设置 > 隐私与安全性”点击“仍要打开”，再确认打开。之后可以正常双击启动。
```

- [ ] **步骤 7：更新 README 下载入口**

把 README 入口列表中的：

```md
- [下载 Windows 安装包](https://github.com/JunieXD/AutoEmailSender/releases)
```

改为：

```md
- [下载桌面版](https://github.com/JunieXD/AutoEmailSender/releases)
```

- [ ] **步骤 8：运行文档和脚本测试**

运行：

```bash
rtk sh -lc "cd frontend && npx vitest run ../scripts/release-notes.test.mjs"
rtk sh scripts/prepare-release.test.sh
rtk sh -lc "cd website && npm run test && npm run build"
```

预期：全部 PASS。

- [ ] **步骤 9：Commit**

```bash
rtk git add scripts/release-notes.mjs scripts/release-notes.test.mjs scripts/prepare-release.test.sh website/docs/getting-started.md website/docs/install.md website/docs/faq.md README.md
rtk git commit -m "docs: document macOS desktop installation"
```

---

### 任务 8：整体验证与发布前检查

**文件：**
- 无新增代码文件；执行验证命令并修复前面任务遗漏的问题。

- [ ] **步骤 1：运行后端测试**

运行：

```bash
rtk sh -lc "cd backend && uv run python -m unittest test.test_backend_build_script test.test_desktop_runtime"
```

预期：PASS。

- [ ] **步骤 2：运行桌面测试和类型检查**

运行：

```bash
rtk sh -lc "cd desktop && npm run typecheck && npm test"
```

预期：PASS。

- [ ] **步骤 3：运行前端 lint、测试和构建**

运行：

```bash
rtk sh -lc "cd frontend && npm run lint && npm run test && npm run build"
```

预期：PASS。

- [ ] **步骤 4：运行网站测试和构建**

运行：

```bash
rtk sh -lc "cd website && npm run test && npm run build"
```

预期：PASS。

- [ ] **步骤 5：在 macOS 本机验证后端打包**

运行：

```bash
rtk ./scripts/build-backend.sh --clean
```

预期：

- `backend/dist/backend/backend` 存在。
- 命令输出包含 `packaged runtime self-check ok`。

- [ ] **步骤 6：在 macOS 本机验证 `.dmg` 构建**

运行：

```bash
rtk sh -lc "cd desktop && npm run dist:mac"
```

预期：

- `desktop/release/AutoEmailSender-<version>-arm64.dmg` 存在。
- 构建日志不要求 Apple 证书。

- [ ] **步骤 7：手动 smoke test**

在 macOS 上执行：

1. 打开 `.dmg`。
2. 把 `Auto Email Sender.app` 拖入“应用程序”。
3. 首次打开时按文档到“系统设置 > 隐私与安全性”点击“仍要打开”。
4. 确认应用启动后本地后端进入 ready。
5. 验证托盘菜单可以隐藏/显示窗口。
6. 验证开机自启可以启用和禁用。
7. 点击检查更新，确认 macOS 分支不会出现“差量下载”“全量下载”“立即重启安装”，而是打开 GitHub Releases。

- [ ] **步骤 8：最终状态检查**

运行：

```bash
rtk git status --short
```

预期：没有未提交的任务相关改动。若只剩 `.DS_Store` 未跟踪，不要加入提交。
