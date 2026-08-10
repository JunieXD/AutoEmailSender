# 更新与常见问题

## 如何更新

应用会自动检查更新。Windows 和新版 macOS 可直接在应用内更新；旧版 macOS 需手动覆盖安装一次。Intel Mac 暂无安装包。

旧版 macOS 如果打开 GitHub Releases，请下载新版 `.dmg` 并覆盖安装；之后即可在应用内更新。

## 检查更新失败怎么办

更新检查需要访问 GitHub。失败时请检查网络和代理，稍后重试，或手动下载最新安装包：

[GitHub Releases](https://github.com/JunieXD/AutoEmailSender/releases)

## 数据默认保存在哪里

Windows 安装版会将数据库、上传文件和运行日志保存到当前用户的数据目录：

`C:\Users\<你的用户名>\AppData\Roaming\auto-email-sender-desktop`

macOS Apple Silicon 安装版数据目录：

`~/Library/Application Support/auto-email-sender-desktop`

## 为什么安装时提示未知发布者

安装包暂未购买 Windows 代码签名证书，因此 Windows 可能提示「未知发布者」或 SmartScreen 拦截。

请确保安装包来自本项目 GitHub Releases 页面，不要从其他来源下载。

## macOS 为什么提示无法验证开发者

当前 macOS 版采用 ad-hoc 签名，未使用 Developer ID 签名和 Apple 公证，首次打开可能被系统拦截。确认安装包来自本项目后，请到“系统设置 > 隐私与安全性”点击“仍要打开”，再确认打开。

## 启动后白屏怎么办

安装版启动后白屏，可按以下步骤排查：

1. 退出应用后重新打开。
2. 确认没有安全软件拦截本地服务。
3. 确认安装包来自最新 Release。
4. 前往 GitHub Issues 反馈，附上系统版本、安装包版本和错误截图。

## 邮件测试失败怎么办

优先检查：

- 邮箱是否开启 SMTP 和 IMAP。
- 密码填写的是授权码，而非网页登录密码。
- SMTP 和 IMAP 主机、端口是否正确。
- 邮箱服务商是否限制第三方客户端登录。
- 网络能否访问邮箱服务器。

还不清楚 SMTP、IMAP 或授权码是什么，请查看[首次配置](./first-run#_1-创建发件身份)和[个人中心](./profile#发件身份)。

## 匹配分析结果不准怎么办

优先检查：

- 主材料是否上传正确。
- 导师研究方向和近期论文是否完整。
- 模型配置是否可用。

匹配分析是辅助判断，不建议把分数当作唯一依据。
