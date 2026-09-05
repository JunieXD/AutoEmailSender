# macOS Sparkle 发布运维说明

## 方案边界

macOS Apple Silicon 版使用 Sparkle 2.9.4 更新，Windows 的 `electron-updater` 流程保持不变。macOS 应用仍采用 ad-hoc 签名，不购买 Apple Developer Program、不做 notarization；Sparkle 通过独立的 Ed25519 密钥校验 appcast、DMG 和差分包。

发布继续保留 DMG，而不直接分发裸 `.app`：

- DMG 是用户首次安装时可下载、可挂载并拖入“应用程序”的完整载体。
- `.app` 实际是目录包，直接作为 GitHub Release 资产上传不可靠，也不能保证目录结构、权限和符号链接在传输中保持完整。
- Sparkle 原生支持从 DMG 提取 `.app`；差分更新不可用时，同一个 DMG 也是全量回退包。

因此不需要再增加 macOS 应用 ZIP。每个 Release 的 macOS 更新资产包括当前 DMG、`appcast.xml`，以及存在旧 Sparkle 版本时生成的 `.delta` 文件；供 Codex 或 Claude Code 手动安装的 `crawl-mentors-to-xlsx-vx.y.z.zip` 是独立的 Skill 附件，不参与 Sparkle 更新。

## 一次性密钥配置

先在可信 macOS 设备上下载并校验固定版本的 Sparkle：

```bash
./scripts/build/setup-sparkle.sh
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

发布分为候选认证和公开提升。先准备公告，再构建一次候选：

```bash
./scripts/prepare-release.sh 2.4.0
./scripts/release.sh 2.4.0
```

第二条命令会提交并推送精确候选 SHA，以 `publish=false` 启动 `Release Desktop` workflow。workflow 先运行廉价 preflight，再完成双平台构建和签名，生成绑定版本、SHA、run ID、release note hash、资产名、大小与 SHA-256 的 `release-candidate.json`；此阶段不创建 tag 或 Release。

候选 workflow 和同一 SHA 的正式 Windows VM 验收通过、公告得到确认后，按候选 run ID 公开原产物：

```bash
./scripts/release.sh 2.4.0 --promote-run <candidate-run-id>
```

提升流程会重新核对候选报告和下载后的每个资产，但不会重新运行产品测试或构建安装包。只有核对通过后，publish job 才创建 tag、暂存 draft 并公开 Release。候选以后出现任何代码、版本元数据或公告变更，都必须形成新 SHA 并重新认证；不同候选的资产不能混用。为兼容历史操作，手动推送 tag 仍可触发同一 workflow，但日常发布使用候选提升流程。

workflow 会：

1. 廉价 preflight 先检查版本、公告、发布脚本和后端发布契约；通过后才启动 Windows/macOS runner，并让完整 Agent CLI gate 与两个平台构建并行。最终认证同时依赖 CLI gate 和双平台构建，任何一项失败都不能提升。
2. 分别构建 Windows 安装包和 macOS arm64 DMG，但不在两个 job 中直接发布，也不提前占用版本 tag。
3. macOS 打包在签名后清理应用包中的扩展属性，并重新校验签名，避免 Sparkle 无法生成差分包。
4. macOS job 只下载并缓存上一版 DMG，生成上一版到当前版的差分包。更早的客户端使用当前版签名 DMG 做全量更新，减少发布时的历史下载、解包和差分计算。脚本先验证上一版 appcast 的 whole-feed 签名，再核对缓存 DMG 的长度和 enclosure Ed25519 签名；缺失或损坏时重新下载，移除过期缓存。
   从 v2.5.3 干净基线开始，必须生成最新旧版本的 delta；缺少时终止发布。观察日志中的历史 DMG 准备和 `generate_appcast` 分段耗时；远端缓存仍需要传输文件。
5. 私钥只通过标准输入传给 `generate_appcast`，不会写入临时密钥文件。
6. `generate_appcast` 可能按 `.app` 目录名生成含空格的差分文件名。发布脚本会先把差分包规范化为 GitHub 不会改写的安全文件名，并同步重写 appcast URL；签名覆盖差分包内容，因此文件改名不会改变 enclosure 的 `sparkle:edSignature`。但 feed 签名覆盖整个 XML，脚本必须在完成所有 URL 改写后用 `sign_update` 对最终 appcast 重新签名，并立即使用配置的公钥验签。
7. publish job 只下载并核验候选 run 的原产物，在暂存的 draft Release 中先上传安装包和差分包，最后上传 `appcast.xml`。公开前必须再次验证最终 feed 签名，并逐项核对 appcast 当前版本引用的文件名、URL、GitHub 实际资产名和非零大小，全部一致后再发布为稳定 Release。

工作流失败后可以在 Release 仍为 draft 时重跑；一旦 Release 已公开，重跑必须在上传任何资产前失败。已公开版本不得用 `--clobber` 替换安装包、Skill ZIP 或 appcast，修复后应发布新版本。

首个集成 Sparkle 的版本没有旧 appcast，因此只生成当前 DMG 的 appcast，不会生成差分包。这是正常结果。尚未集成 Sparkle 的旧 macOS 客户端必须手动覆盖安装这个过渡版本一次；之后才能使用原生更新。

v2.4.0 和 v2.4.1 的 DMG 含有旧式代码签名扩展属性，Sparkle 无法把它们作为差分源；v2.5.3 是首个经过签名后清理并验证的公开基线。ad-hoc 构建之间可能出现签名 identity 不一致警告，但已验证这不会阻止从干净基线生成 delta。后续版本必须至少包含从最新干净基线生成的差分包，否则 workflow 失败。

2026-08-09 对 v2.5.4 做过一次经所有者明确授权的公开 appcast 元数据修复。当时全部 Release 资产下载计数均为 0，已备份原 appcast，只把 2.5.3 → 2.5.4 差分 URL 从 Sparkle 生成的空格文件名改为 GitHub 实际保存的点号文件名；tag、DMG、差分包内容、长度和 enclosure Ed25519 签名均未改变。随后发现这次 URL 修改使覆盖整个 XML 的 feed 签名失效，2.5.3 会因 `SURequireSignedFeed=true` 拒绝清单。再次应急处理时，先备份 SHA-256 为 `47d447514eb2e49fc675ca0ae02166c4063aca62bb865aa294ea4ac84a33b874` 的无效 feed，仅用原 Sparkle 密钥更新 feed 签名和签名长度；修复后的 SHA-256 为 `c73c5cee9f66064b16896440227af2cbb952e558daf495e9d388963107d7c5a4`，并已用 2.5.3 内置公钥、Sparkle 官方工具及线上 `latest/download` 文件完成验签。DMG、差分包、tag 和其他 Release 资产均未改变。此记录不放宽常规规则：以后不得在签名后修改 appcast，draft 公开前必须验签最终文件；已有用户可能读取的公开资产仍应优先通过更高版本修复。

## 发布后检查

优先运行统一验收命令，不要临时拼接下载、哈希和验签命令：

```bash
./scripts/verify-release.sh <version> \
  --candidate-run <candidate-run-id> \
  --promotion-run <promotion-run-id>
```

脚本从上一版公开 DMG 的应用包中读取 `SUPublicEDKey`，不会读取或输出私钥；随后核对候选报告、公开资产、latest appcast、feed/enclosure 签名、必要 delta、Skill ZIP 和对应 website 部署。appcast 可能保留历史 `<item>`，且历史完整 DMG 的 URL 可能被 Sparkle 改写到当前 tag；验收时必须先按精确的 `<sparkle:version>` 选中当前 item，不能只按 `/download/v<version>/` URL 前缀筛选。

确认 GitHub Release 至少包含：

- `AutoEmailSender-Setup-x.y.z.exe`、对应 blockmap 和 `latest.yml`
- `AutoEmailSender-x.y.z-arm64.dmg`
- `crawl-mentors-to-xlsx-vx.y.z.zip`
- `appcast.xml`
- 从干净 Sparkle 基线后的版本起，一个面向上一版的 `.delta`

再在已安装的上一版 macOS 应用中点击“检查更新”，确认 Sparkle 原生窗口能展示版本说明、下载并在用户确认后重启安装。自动检查周期是 24 小时，自动下载和静默安装均关闭。

端到端升级测试必须与日常使用数据隔离。不要只在首次启动时传入 Electron 的 `--user-data-dir`：Sparkle 完成替换后会自行重新启动应用，不保证保留原进程的命令行参数，重启后的应用可能重新使用默认的 `~/Library/Application Support/auto-email-sender-desktop`。推荐在独立的 macOS 测试账户中完成升级测试；如果使用专用 QA 构建，则必须在应用代码读取任何数据路径之前固定设置独立的 `userData` 路径，并确认更新后的构建仍包含相同隔离配置。测试前还应备份目标数据库，测试后检查没有 Auto Email Sender 后台进程继续运行。

由于应用未经过 Apple 公证，首次安装仍需要用户在“系统设置 > 隐私与安全性”中选择“仍要打开”。个别 macOS 版本若在更新后再次拦截，也按同一系统流程放行；不要指导用户运行 `xattr` 或关闭 Gatekeeper。

## 密钥轮换

不要直接替换 `SPARKLE_PUBLIC_ED_KEY` 和 `SPARKLE_ED_PRIVATE_KEY`。旧客户端只信任打包时写入的旧公钥，直接轮换会切断更新链路。确需轮换时，应先设计一个由旧私钥签名的过渡版本，在客户端内同时完成信任迁移并经过实际升级测试，再更换发布 Secret。
