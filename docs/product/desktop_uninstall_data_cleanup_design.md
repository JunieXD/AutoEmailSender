# Windows 卸载与本地数据

默认卸载保留数据库、上传材料、缓存和配置，重新安装后可继续使用。

需要彻底卸载时，用户可在卸载组件页选择“删除本地数据”。该项默认不选，选择后会提示删除不可恢复；确认才删除，取消确认则继续卸载并保留数据。

命令行传入 `--delete-app-data` 表示明确要求清理，可与 `/S` 静默卸载组合使用。只传 `/S` 会保留数据。

清理范围固定为当前用户的 `%APPDATA%\auto-email-sender-desktop`，不包含用户导出到其他位置的文件，不允许扩展到 `%APPDATA%` 本身或其他应用目录。

卸载同时清理归本应用管理的 CLI 和 Agent 支持；无法确认归属的文件保留。此清理与上述业务数据选项分别处理。

NSIS 实现入口、路径约束和 Windows 验证场景见 [卸载维护说明](../development/desktop_uninstall_data_cleanup_implementation.md)。
