# 桌面端卸载清空数据实现说明

## 1. 实现范围

本次实现基于《桌面端卸载清空数据设计》，目标是在现有 NSIS 卸载流程中增加「同时删除本地数据」能力。实现后，普通卸载继续保留数据，用户明确勾选或通过命令行传入 `--delete-app-data` 时才删除应用数据目录。

## 2. 文件变更

### 2.1 新增 NSIS 脚本

新增文件：`desktop/build/installer.nsh`

职责：
- 定义卸载阶段的自定义页面。
- 展示「同时删除本地数据」复选框。
- 处理二次确认。
- 根据交互结果设置删除标记。
- 在卸载阶段安全删除应用数据目录。
- 兼容 `--delete-app-data` 命令行参数。

建议把自定义逻辑放在 electron-builder 约定的 `installer.nsh` 中。electron-builder 会在生成 NSIS 脚本时自动包含该文件，避免直接修改 `node_modules` 中的 NSIS 模板。

### 2.2 修改打包配置

修改文件：`desktop/electron-builder.yml`

职责：
- 继续保持 `nsis.oneClick: false`。
- 继续保持 `allowToChangeInstallationDirectory: true`。
- 如 electron-builder 当前版本不能自动识别 `desktop/build/installer.nsh`，则显式配置 NSIS include 或 script 入口。

优先采用 electron-builder 的默认自定义脚本发现机制，不要复制整份 NSIS 模板。

### 2.3 修改桌面端打包测试

修改文件：`desktop/test/packaging.test.ts`

职责：
- 验证 `desktop/build/installer.nsh` 存在。
- 验证 NSIS 脚本包含卸载数据清理选项文案。
- 验证 NSIS 脚本包含 `--delete-app-data` 参数处理。
- 验证 NSIS 脚本包含 `%APPDATA%` 路径限制或等价安全校验。

## 3. NSIS 实现设计

### 3.1 全局变量

在 `desktop/build/installer.nsh` 中定义卸载阶段变量：

```nsis
Var /GLOBAL UninstallDeleteAppDataCheckbox
Var /GLOBAL UninstallDeleteAppDataState
Var /GLOBAL UninstallShouldDeleteAppData
```

变量含义：
- `UninstallDeleteAppDataCheckbox`：复选框控件句柄。
- `UninstallDeleteAppDataState`：复选框状态。
- `UninstallShouldDeleteAppData`：最终是否删除数据，取值建议为 `0` 或 `1`。

### 3.2 命令行参数解析

在卸载初始化阶段读取参数：

```nsis
!macro customUnInit
  StrCpy $UninstallShouldDeleteAppData "0"
  ${GetParameters} $R0
  ${GetOptions} $R0 "--delete-app-data" $R1
  ${Unless} ${Errors}
    StrCpy $UninstallShouldDeleteAppData "1"
  ${EndUnless}
!macroend
```

说明：
- 未传入 `--delete-app-data` 时不删除数据。
- 传入 `--delete-app-data` 时设置删除标记。
- 后续真正删除前仍要执行路径校验。

### 3.3 卸载自定义页面

通过 `customUnInstall` 或对应的卸载页面宏插入自定义页面。页面内容包含说明文字和复选框：

```nsis
!macro customUnInstall
  Page custom un.DataCleanupPageCreate un.DataCleanupPageLeave
!macroend
```

页面创建函数：

```nsis
Function un.DataCleanupPageCreate
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 24u "如不勾选，卸载后本地数据会保留，重新安装后仍可继续使用。"
  Pop $0

  ${NSD_CreateCheckbox} 0 36u 100% 24u "同时删除本地数据（数据库、材料、缓存和本地配置）"
  Pop $UninstallDeleteAppDataCheckbox
  ${NSD_SetState} $UninstallDeleteAppDataCheckbox ${BST_UNCHECKED}

  nsDialogs::Show
FunctionEnd
```

页面离开函数：

```nsis
Function un.DataCleanupPageLeave
  ${NSD_GetState} $UninstallDeleteAppDataCheckbox $UninstallDeleteAppDataState
  ${If} $UninstallDeleteAppDataState == ${BST_CHECKED}
    MessageBox MB_ICONEXCLAMATION|MB_YESNO \
      "这将永久删除 Auto Email Sender 的本地数据，包括数据库、上传材料、缓存和本地配置。删除后无法通过重新安装恢复。是否继续？" \
      IDYES confirm_delete IDNO cancel_delete

    confirm_delete:
      StrCpy $UninstallShouldDeleteAppData "1"
      Goto done

    cancel_delete:
      StrCpy $UninstallShouldDeleteAppData "0"
      Goto done
  ${EndIf}

  done:
FunctionEnd
```

说明：
- 复选框默认不勾选。
- 用户勾选后必须二次确认。
- 用户取消二次确认时继续卸载，但保留数据。

### 3.4 静默卸载处理

静默卸载不展示交互页面。实现时需要判断 `${Silent}`：

- `${Silent}` 且未传入 `--delete-app-data`：保留数据。
- `${Silent}` 且传入 `--delete-app-data`：删除数据。

如果自定义页面在静默模式下仍被调用，需要在 `un.DataCleanupPageCreate` 开头直接 `Abort`，让卸载流程跳过该页面。

### 3.5 数据删除函数

新增删除函数，集中处理路径校验和删除：

```nsis
Function un.DeleteAutoEmailSenderAppData
  ${If} $UninstallShouldDeleteAppData != "1"
    Return
  ${EndIf}

  SetShellVarContext current
  StrCpy $R0 "$APPDATA\auto-email-sender-desktop"

  ${If} $R0 == ""
    Return
  ${EndIf}

  ${If} $R0 == "$APPDATA"
    Return
  ${EndIf}

  ${If} ${FileExists} "$R0\*.*"
    RMDir /r "$R0"
  ${EndIf}
FunctionEnd
```

实现时可以根据实际产物增加已知兼容目录，但必须逐个写死或通过严格匹配生成，不得使用宽泛通配符。

### 3.6 挂接删除时机

在卸载文件移除之后、卸载结束之前调用删除函数：

```nsis
!macro customUnInstallSection
  Call un.DeleteAutoEmailSenderAppData
!macroend
```

说明：
- 该阶段应用程序文件已经开始卸载，用户数据删除失败也不应阻塞程序卸载。
- 删除失败时可以静默跳过，不需要中断卸载流程。

## 4. 测试实现

### 4.1 配置级测试

在 `desktop/test/packaging.test.ts` 增加测试：

```ts
it("adds an explicit uninstall app data cleanup option", () => {
  const script = readFileSync(path.resolve("build", "installer.nsh"), "utf8");

  expect(script).toContain("同时删除本地数据");
  expect(script).toContain("--delete-app-data");
  expect(script).toContain("Auto Email Sender");
  expect(script).toContain("$APPDATA");
});
```

该测试用于防止后续重构误删关键卸载逻辑。

### 4.2 类型与单元测试

运行桌面端测试：

```powershell
cd desktop
npm run test
```

运行桌面端类型检查：

```powershell
cd desktop
npm run typecheck
```

### 4.3 打包验证

生成 Windows NSIS 安装包：

```powershell
cd desktop
npm run dist
```

检查产物：
- `desktop/release/AutoEmailSender-Setup-<version>.exe` 存在。
- 安装器能正常安装。
- Windows「应用和功能」中能正常卸载。

## 5. 手动验收流程

### 5.1 普通卸载保留数据

1. 安装桌面端。
2. 启动应用，创建测试数据。
3. 记录 `%APPDATA%\auto-email-sender-desktop` 是否存在。
4. 关闭应用。
5. 从 Windows「应用和功能」卸载。
6. 不勾选「同时删除本地数据」。
7. 确认卸载完成后 `%APPDATA%\auto-email-sender-desktop` 仍存在。
8. 重新安装应用，确认测试数据仍可读取。

### 5.2 交互式彻底卸载

1. 安装桌面端并创建测试数据。
2. 从 Windows「应用和功能」卸载。
3. 勾选「同时删除本地数据」。
4. 在二次确认中选择「是」。
5. 确认卸载完成后 `%APPDATA%\auto-email-sender-desktop` 不存在。

### 5.3 取消二次确认

1. 安装桌面端并创建测试数据。
2. 从 Windows「应用和功能」卸载。
3. 勾选「同时删除本地数据」。
4. 在二次确认中选择「否」。
5. 确认卸载继续完成。
6. 确认 `%APPDATA%\auto-email-sender-desktop` 仍存在。

### 5.4 静默卸载

使用不带清理参数的静默卸载：

```powershell
& "$env:LOCALAPPDATA\Programs\Auto Email Sender\Uninstall Auto Email Sender.exe" /S
```

预期：应用卸载，本地数据保留。

使用带清理参数的静默卸载：

```powershell
& "$env:LOCALAPPDATA\Programs\Auto Email Sender\Uninstall Auto Email Sender.exe" /S --delete-app-data
```

预期：应用卸载，本地数据删除。

实际卸载程序路径需要以安装后的快捷方式或注册表记录为准。

## 6. 注意事项

- 不要修改 `desktop/node_modules/app-builder-lib/templates/nsis` 下的模板文件。
- 不要把 `deleteAppDataOnUninstall` 作为唯一实现方式，因为当前安装器是 `oneClick: false`。
- 不要默认删除用户数据。
- 不要使用通配符删除 `%APPDATA%` 下的多个目录。
- 不要因为数据删除失败而阻断卸载主流程。
- 如果后续修改 Electron 包名或显式设置 `app` 名称，应先确认运行时 `app.getPath("userData")`，并设计现有数据目录的兼容迁移，再更新卸载脚本。

## 7. 发布说明

发布时建议在 release notes 中增加说明：

> 卸载程序新增「同时删除本地数据」选项。默认卸载仍会保留本地数据库、材料和配置；如果需要彻底卸载，请在卸载时勾选该选项并确认。

同时建议在文档中补充手动清理路径：

```text
%APPDATA%\auto-email-sender-desktop
```
