# Windows 卸载数据清理维护说明

实现位于 [installer.nsh](../../desktop/build/installer.nsh)，打包配置位于 [electron-builder.yml](../../desktop/electron-builder.yml)。使用 electron-builder 的 NSIS 扩展宏，不修改依赖中的安装器模板。

## 当前行为

- 默认卸载保留本地数据。
- 卸载组件页的“删除本地数据”是可选项；交互模式选择后再次确认，取消确认仍继续卸载并保留数据。
- `--delete-app-data` 显式启用数据清理，可与 `/S` 静默卸载组合使用。
- 删除范围固定为当前用户的 `%APPDATA%\auto-email-sender-desktop`。应用名称或数据目录改变时，先核对 Electron 的 `userData` 路径和迁移规则，再调整这里。
- `customUnInstall` 调用 [windows-uninstall.ps1](../../agent-support/windows-uninstall.ps1) 移除受管 CLI 和 Agent 支持；脚本无法确认归属的文件会保留。此操作与本地业务数据清理分别处理。

`customUnInit` 解析清理参数，`customUnInstallSection` 注册可选组件和参数触发的清理入口。删除函数始终只处理上述固定目录。

## 验证

在 `desktop/` 运行 `npm run typecheck` 和 `npm run test`。涉及 NSIS 行为的修改还需用 Windows 安装包验证：

| 场景 | 预期 |
| --- | --- |
| 默认卸载；`/S` | 保留数据，重装后可读取 |
| 选择清理并确认 | 删除应用数据目录 |
| 选择清理后取消确认 | 继续卸载，保留数据 |
| `/S --delete-app-data` | 删除应用数据目录 |

卸载程序路径以安装位置或注册表为准；测试使用可丢弃的数据目录。打包与正式 Windows 验证流程见 [发布运维指南](../operations/sparkle-release-operations.md)。
