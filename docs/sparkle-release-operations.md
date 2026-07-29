# macOS Sparkle 发布运维说明

## 方案边界

macOS Apple Silicon 版使用 Sparkle 2.9.4 更新，Windows 的 `electron-updater` 流程保持不变。macOS 应用仍采用 ad-hoc 签名，不购买 Apple Developer Program、不做 notarization；Sparkle 通过独立的 Ed25519 密钥校验 appcast、DMG 和差分包。

发布继续保留 DMG，而不直接分发裸 `.app`：

- DMG 是用户首次安装时可下载、可挂载并拖入“应用程序”的完整载体。
- `.app` 实际是目录包，直接作为 GitHub Release 资产上传不可靠，也不能保证目录结构、权限和符号链接在传输中保持完整。
- Sparkle 原生支持从 DMG 提取 `.app`；差分更新不可用时，同一个 DMG 也是全量回退包。

因此不需要再增加 ZIP。每个 Release 的 macOS 资产包括当前 DMG、`appcast.xml`，以及存在旧 Sparkle 版本时生成的 `.delta` 文件。

## 一次性密钥配置

先在可信 macOS 设备上下载并校验固定版本的 Sparkle：

```bash
./scripts/setup-sparkle.sh
```

生成项目专用密钥。工具会把私钥存入当前用户的 macOS 钥匙串，并输出 `SUPublicEDKey`：

```bash
desktop/native/sparkle/vendor/bin/generate_keys \
  --account com.juniexd.autoemailsender
```

将输出的公钥保存为 GitHub Actions Secret `SPARKLE_PUBLIC_ED_KEY`。公钥本身不敏感，但通过 Secret 注入可以让没有完成配置的构建立刻失败，避免发布一个无法验证更新的应用。

把私钥导出到仓库以外的受保护路径：

```bash
desktop/native/sparkle/vendor/bin/generate_keys \
  --account com.juniexd.autoemailsender \
  -x /安全路径/auto-email-sender-sparkle-private-key
```

将该文件的完整内容保存为 GitHub Actions Secret `SPARKLE_ED_PRIVATE_KEY`。可使用 GitHub 网页设置，也可在确认当前 `gh` 已登录正确仓库后执行：

```bash
gh secret set SPARKLE_ED_PRIVATE_KEY < /安全路径/auto-email-sender-sparkle-private-key
```

私钥不得放在仓库目录、提交到 Git、写入 workflow、命令行参数或构建日志。请把离线备份保存在访问受控的位置；丢失私钥后，已发布客户端无法信任用新密钥签名的更新。发布脚本会从私钥种子推导公钥并与 `SPARKLE_PUBLIC_ED_KEY` 核对，不匹配时拒绝发布。

## 发布流程

正常发布仍使用现有脚本创建 tag：

```bash
./scripts/prepare-release.sh 2.4.0
./scripts/release.sh 2.4.0
```

tag 触发的 workflow 会：

1. 分别构建 Windows 安装包和 macOS arm64 DMG，但不在两个 job 中直接发布。
2. macOS job 从上一版 appcast 中解析最近 3 个全量 DMG，并生成最多 3 个差分包。
3. 私钥只通过标准输入传给 `generate_appcast`，不会写入临时密钥文件。
4. publish job 合并两端产物，在暂存的 draft Release 中先上传安装包和差分包，最后上传 `appcast.xml`，全部成功后再发布为稳定 Release。

首个集成 Sparkle 的版本没有旧 appcast，因此只生成当前 DMG 的 appcast，不会生成差分包。这是正常结果。尚未集成 Sparkle 的旧 macOS 客户端必须手动覆盖安装这个过渡版本一次；之后才能使用原生更新。

## 发布后检查

确认 GitHub Release 至少包含：

- `AutoEmailSender-Setup-x.y.z.exe`、对应 blockmap 和 `latest.yml`
- `AutoEmailSender-x.y.z-arm64.dmg`
- `appcast.xml`
- 从第二个 Sparkle 版本起，最多 3 个面向最近旧版本的 `.delta`

再在已安装的上一版 macOS 应用中点击“检查更新”，确认 Sparkle 原生窗口能展示版本说明、下载并在用户确认后重启安装。自动检查周期是 24 小时，自动下载和静默安装均关闭。

端到端升级测试必须与日常使用数据隔离。不要只在首次启动时传入 Electron 的 `--user-data-dir`：Sparkle 完成替换后会自行重新启动应用，不保证保留原进程的命令行参数，重启后的应用可能重新使用默认的 `~/Library/Application Support/auto-email-sender-desktop`。推荐在独立的 macOS 测试账户中完成升级测试；如果使用专用 QA 构建，则必须在应用代码读取任何数据路径之前固定设置独立的 `userData` 路径，并确认更新后的构建仍包含相同隔离配置。测试前还应备份目标数据库，测试后检查没有 Auto Email Sender 后台进程继续运行。

由于应用未经过 Apple 公证，首次安装仍需要用户在“系统设置 > 隐私与安全性”中选择“仍要打开”。个别 macOS 版本若在更新后再次拦截，也按同一系统流程放行；不要指导用户运行 `xattr` 或关闭 Gatekeeper。

## 密钥轮换

不要直接替换 `SPARKLE_PUBLIC_ED_KEY` 和 `SPARKLE_ED_PRIVATE_KEY`。旧客户端只信任打包时写入的旧公钥，直接轮换会切断更新链路。确需轮换时，应先设计一个由旧私钥签名的过渡版本，在客户端内同时完成信任迁移并经过实际升级测试，再更换发布 Secret。
