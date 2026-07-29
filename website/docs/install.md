# 安装桌面版

## 下载位置

[打开 GitHub Releases](https://github.com/JunieXD/AutoEmailSender/releases)

- Windows 下载 `AutoEmailSender-Setup-x.y.z.exe`。
- macOS Apple Silicon 下载 `AutoEmailSender-x.y.z-arm64.dmg`。
- Intel Mac 暂未提供安装包。

## Windows 安装步骤

1. 双击安装包。
2. 按安装向导选择安装位置。
3. 根据需要创建桌面快捷方式。
4. 安装完成后，从开始菜单或桌面快捷方式启动。

## macOS Apple Silicon 安装步骤

1. 打开 `.dmg`，把 `Auto Email Sender.app` 拖到“应用程序”。
2. 首次打开若提示无法验证开发者，到“系统设置 > 隐私与安全性”点击“仍要打开”。
3. 再次确认“打开”后即可正常使用。

## 桌面端特性

安装版基于 Electron 构建，具备以下桌面端独有功能：

### 系统托盘/菜单栏

应用启动后会在系统托盘或菜单栏显示图标。关闭主窗口时，应用默认最小化到托盘/菜单栏而非退出。点击或右键系统托盘/菜单栏图标可弹出菜单：

- **打开窗口：** 重新显示主界面。
- **开机自启动：** 勾选后随系统自动启动。
- **退出：** 完全退出应用。

### 开机自启动

在托盘/菜单栏菜单中开启"开机自启动"后，应用会在系统启动时自动运行并最小化到托盘/菜单栏。自启动状态可在托盘/菜单栏菜单中查看和切换。

### 单实例运行

应用同时只能运行一个实例。再次双击启动图标时，会自动激活已有的窗口而非打开新的，避免后台服务冲突。

### 数据目录

Windows 安装版数据目录：

`C:\Users\<你的用户名>\AppData\Roaming\auto-email-sender-desktop`

macOS Apple Silicon 安装版数据目录：

`~/Library/Application Support/auto-email-sender-desktop`

包含数据库文件、上传的附件和运行日志。

## 常见提示

Windows 安装包暂未购买代码签名证书，看到「未知发布者」或 SmartScreen 拦截是正常现象。macOS Apple Silicon 版采用 ad-hoc 签名，未使用 Developer ID 签名和 Apple 公证；首次打开若提示无法验证开发者，请到“系统设置 > 隐私与安全性”点击“仍要打开”，再确认打开。请确认安装包来源为本项目 GitHub Releases 页面。

启动后白屏或连接失败，先退出应用后重新打开。问题依旧的话，请到 GitHub Issues 反馈，附上系统版本、安装包版本和错误截图。
