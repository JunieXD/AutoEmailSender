# 安装桌面版

## 下载位置

[下载最新版本](https://github.com/JunieXD/AutoEmailSender/releases)

- Windows 下载 `AutoEmailSender-Setup-x.y.z.exe`。
- macOS Apple Silicon 下载 `AutoEmailSender-x.y.z-arm64.dmg`。
- Intel Mac 暂未提供安装包。

## Windows 安装步骤

1. 双击安装包，按向导完成安装。
2. 从开始菜单或桌面快捷方式打开应用。

## macOS Apple Silicon 安装步骤

1. 打开 `.dmg`，把 `Auto Email Sender.app` 拖到“应用程序”。
2. 首次打开若提示无法验证开发者，请在“系统设置 > 隐私与安全性”中点击“仍要打开”，再确认打开。

## 桌面端特性

桌面版还支持：

### 系统托盘/菜单栏

关闭窗口后，应用会留在系统托盘或菜单栏。你可以从这里：

- 重新打开主界面。
- 开启或关闭“开机自启动”。
- 完全退出应用。

### 单实例运行

重复打开应用时，会回到现有窗口，不会启动多个后台服务。

### 数据目录

Windows 安装版数据目录：

`C:\Users\<你的用户名>\AppData\Roaming\auto-email-sender-desktop`

macOS Apple Silicon 安装版数据目录：

`~/Library/Application Support/auto-email-sender-desktop`

这里保存数据库、附件和运行日志。

## 常见提示

- **Windows：** 安装包尚未购买代码签名证书，可能出现“未知发布者”或 SmartScreen 提示。请确认安装包来自本项目 GitHub Releases 后再继续。
- **macOS：** 当前版本采用 ad-hoc 签名，未使用 Developer ID 签名和 Apple 公证。首次打开时可能需要在“系统设置 > 隐私与安全性”中点击“仍要打开”。

遇到白屏、连接失败或其他问题，请查看[更新与常见问题](./faq)。
