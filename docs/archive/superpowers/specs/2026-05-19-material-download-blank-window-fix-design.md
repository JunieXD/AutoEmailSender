# 材料下载空白窗口修复方案

## 1. 背景

用户在个人页上传材料后，点击材料库中的“下载”按钮，会出现两个并发感知：

1. Electron/Chromium 打开一个空白新窗口。
2. Windows 侧同时触发文件下载。

用户期望“下载”只产生保存文件的系统下载行为，不额外打开空白窗口。

## 2. 当前行为定位

当前材料下载链路如下：

1. 前端材料库“下载”按钮调用 `triggerDownload(getMaterialDownloadUrl(material.id))`。
2. `triggerDownload` 动态创建 `<a>` 元素。
3. `<a>` 设置 `href` 为 `/api/materials/{material_id}/download`。
4. `<a>` 同时设置 `target="_blank"`。
5. 点击该 `<a>` 后，Electron/Chromium 创建新窗口访问下载 URL。
6. 后端下载接口返回 `FileResponse(..., filename=build_material_download_name(material))`。
7. 浏览器根据附件响应触发系统下载，但新窗口没有可渲染页面内容，因此显示为空白。

关键代码位置：

- `frontend/src/pages/ProfilePage.tsx`：`triggerDownload`
- `frontend/src/pages/ProfilePage.tsx`：材料列表“下载”按钮
- `frontend/src/lib/api/materials.ts`：`getMaterialDownloadUrl`
- `backend/app/api/materials.py`：`GET /api/materials/{material_id}/download`

## 3. 根因

根因是下载触发函数使用了 `target="_blank"`。

后端下载接口返回附件响应是合理的；它负责告诉浏览器这是一个需要保存的文件。问题在于前端把附件下载 URL 放进新窗口打开，导致“新窗口导航”和“系统下载”同时发生。

## 4. 目标

- 点击“下载”后只触发 Windows/Chromium 的下载行为。
- 不再出现空白新窗口。
- 保持下载文件名继续由后端 `Content-Disposition` / `FileResponse(filename=...)` 控制。
- 不改变“打开材料”行为。
- 不改变后端材料下载接口。
- 不改变材料上传、删除、设为默认材料、任务引用等业务逻辑。

## 5. 非目标

- 不新增 Electron 主进程下载管理器。
- 不新增“另存为”对话框。
- 不改成前端 `fetch -> Blob -> objectURL` 下载。
- 不改变 `/api/materials/{id}/open` 和桌面端 `materials:open` IPC 链路。
- 不处理浏览器下载目录、下载进度、下载完成提示等系统层行为。

## 6. 推荐方案

保留现有前端动态 `<a>` 下载触发方式，但移除 `target="_blank"` 和与新窗口相关的 `rel="noreferrer"`。

调整后逻辑：

```ts
const triggerDownload = (url: string) => {
  const link = document.createElement("a");
  link.href = url;
  document.body.appendChild(link);
  link.click();
  link.remove();
};
```

说明：

- 仍由用户点击按钮触发，符合浏览器对下载动作的手势要求。
- 不再请求新窗口，因此不会出现空白窗口。
- 后端响应仍是附件文件响应，浏览器会继续走系统下载。
- 文件名仍以后端 `filename=build_material_download_name(material)` 为准。

## 7. 备选方案比较

### 方案 A：移除 `_blank`

优点：

- 改动最小。
- 直接命中根因。
- 不需要新增 IPC 或后端接口。
- 保持下载语义和当前后端能力一致。

缺点：

- 下载行为仍由 Chromium/Electron 默认处理，无法在应用内展示下载进度。

结论：推荐。

### 方案 B：前端 fetch 文件并创建 Blob 下载

优点：

- 前端可更细粒度控制错误提示。

缺点：

- 需要额外处理文件名解析、Blob URL 回收、跨环境兼容。
- 对大文件会引入额外内存占用。
- 绕开浏览器原生下载链路，复杂度超过当前问题需要。

结论：不推荐。

### 方案 C：新增 Electron IPC 下载服务

优点：

- 可以完全由桌面端管理下载路径、进度和错误。

缺点：

- 需要新增主进程下载能力、preload 暴露、前端分支逻辑和测试。
- 浏览器环境仍需保留原有下载逻辑。
- 当前问题只是空白窗口，新增 IPC 过重。

结论：暂不采用。

## 8. 实现步骤

1. 修改 `frontend/src/pages/ProfilePage.tsx` 中的 `triggerDownload`。
2. 删除 `link.target = "_blank"`。
3. 删除 `link.rel = "noreferrer"`。
4. 保留 `link.href = url`、插入 DOM、点击、移除 DOM 的流程。
5. 保持材料列表按钮继续调用 `triggerDownload(getMaterialDownloadUrl(material.id))`。
6. 不修改 `backend/app/api/materials.py` 的下载接口。
7. 不修改桌面端 `materials:open` IPC。

## 9. 测试方案

### 自动化测试

建议调整或新增前端测试，覆盖：

- `ProfilePage.tsx` 中仍保留 `triggerDownload(getMaterialDownloadUrl(material.id))`。
- `triggerDownload` 不再包含 `target = "_blank"`。
- 材料“打开”仍调用 `openDesktopMaterial(material.id)`。
- 材料“下载”仍使用 `getMaterialDownloadUrl(material.id)`，不误用 `getMaterialOpenUrl`。

可运行：

```bash
cd frontend && npm run test -- ProfilePage.test.ts
```

如测试命令不支持按文件筛选，则运行：

```bash
cd frontend && npm run test
```

### 手动验证

1. 启动桌面开发环境。
2. 进入个人页，上传一份材料。
3. 点击材料库中的“下载”。
4. 确认 Windows/Chromium 下载被触发。
5. 确认不会出现空白新窗口。
6. 再点击“打开”，确认仍走系统应用打开只读副本。
7. 分别用 `.pdf`、`.docx`、`.txt` 至少验证一次。

## 10. 回归风险

风险较低，原因：

- 只移除新窗口行为，不改变下载 URL。
- 后端文件响应不变。
- “打开”和“下载”两条产品语义继续分离。

需要注意：

- 如果某些浏览器环境对同窗口附件下载有特殊处理，需要以桌面端 Electron 为主验证。
- 如果后续希望支持下载进度、另存为路径选择，再考虑 Electron IPC 下载服务；本次不引入。

## 11. 验收标准

- 点击材料“下载”不会创建新窗口。
- 点击材料“下载”仍能下载原始上传文件。
- 下载文件名与当前后端命名保持一致。
- 点击材料“打开”行为不受影响。
- 前端相关测试通过。
