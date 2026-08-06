# 材料下载空白窗口修复计划

关联设计文档：

- `docs/superpowers/specs/2026-05-19-material-download-blank-window-fix-design.md`

采用方案：

- 方案 A：移除前端下载触发里的 `_blank` 新窗口行为。

## 1. 改动范围

本次只修改前端材料下载触发逻辑和对应测试。

预计涉及文件：

- `frontend/src/pages/ProfilePage.tsx`
- `frontend/src/pages/ProfilePage.test.ts`

不修改文件：

- `backend/app/api/materials.py`
- `frontend/src/lib/api/materials.ts`
- `desktop/src/main.ts`
- `desktop/src/materialOpenService.ts`
- `desktop/src/preload.ts`

## 2. 实施步骤

### 2.1 修改下载触发函数

文件：`frontend/src/pages/ProfilePage.tsx`

操作：

1. 找到 `triggerDownload`。
2. 保留动态创建 `<a>` 的方式。
3. 保留 `link.href = url`。
4. 删除 `link.target = "_blank"`。
5. 删除 `link.rel = "noreferrer"`。
6. 保留 `document.body.appendChild(link)`、`link.click()`、`link.remove()`。

目标结果：

```ts
const triggerDownload = (url: string) => {
  const link = document.createElement("a");
  link.href = url;
  document.body.appendChild(link);
  link.click();
  link.remove();
};
```

### 2.2 保持按钮调用不变

文件：`frontend/src/pages/ProfilePage.tsx`

确认材料列表中的“下载”按钮继续调用：

```ts
triggerDownload(getMaterialDownloadUrl(material.id))
```

不把下载按钮改到：

- `getMaterialOpenUrl`
- `openDesktopMaterial`
- 新增 IPC
- `fetch -> Blob`

### 2.3 更新测试断言

文件：`frontend/src/pages/ProfilePage.test.ts`

建议调整现有测试 `opens materials through desktop api and keeps download endpoint`，增加或确认以下断言：

1. 仍包含 `openDesktopMaterial(material.id)`。
2. 仍包含 `triggerDownload(getMaterialDownloadUrl(material.id))`。
3. 不包含 `getMaterialOpenUrl(material.id)`。
4. 不包含 `openFileInNewTab(getMaterialOpenUrl`。
5. 不包含 `link.target = "_blank"`。
6. 不包含 `link.rel = "noreferrer"`。

如果现有测试不适合加入第 5、6 条，可单独新增一个轻量测试，专门保护 `triggerDownload` 不再打开新窗口。

## 3. 验证命令

优先运行聚焦测试：

```bash
cd frontend && npm run test -- ProfilePage.test.ts
```

如果该命令在当前 Vitest 配置下无法按文件筛选，则运行：

```bash
cd frontend && npm run test
```

如果改动触及 lint 规则或格式，也运行：

```bash
cd frontend && npm run lint
```

## 4. 手动验收

桌面端验证步骤：

1. 启动桌面开发环境。
2. 进入个人页。
3. 上传一份材料。
4. 点击材料库中的“下载”按钮。
5. 确认 Windows/Chromium 下载触发。
6. 确认没有空白新窗口出现。
7. 点击同一材料的“打开”按钮。
8. 确认仍通过系统应用打开只读副本。

建议至少覆盖：

- `.pdf`
- `.docx`
- `.txt`

## 5. 验收标准

- 材料“下载”不再打开空白窗口。
- 材料“下载”仍下载原始上传文件。
- 下载文件名仍由后端 `build_material_download_name(material)` 控制。
- 材料“打开”行为不变。
- 前端相关测试通过。

## 6. 风险与回滚

### 风险

风险较低。本次只移除新窗口属性，不改变下载接口、不改变材料数据、不改变 Electron 打开材料链路。

可能观察点：

- 某些浏览器环境可能对无 `download` 属性的附件链接表现不同，但桌面端 Electron 应以 `Content-Disposition` 附件响应触发下载。
- 如果发现点击后当前页面发生异常导航，需要进一步评估是否加空字符串 `download` 属性或改用受控下载服务。

### 回滚方式

如果修复导致下载无法触发，可回滚 `frontend/src/pages/ProfilePage.tsx` 中 `triggerDownload` 的变更。

回滚不会影响后端材料文件、数据库和桌面端 IPC。

## 7. 不做项确认

本次计划明确不做：

- 不新增 Electron 下载 IPC。
- 不新增下载进度 UI。
- 不新增“另存为”对话框。
- 不把下载改成前端 Blob。
- 不调整后端 `FileResponse`。
- 不调整材料“打开”按钮。
