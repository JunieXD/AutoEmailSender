# Release Note Reference

## Required Structure

Keep these sections and order:

1. `### 新增功能`
2. `### 体验优化`
3. `### 问题修复`
4. `## 安装说明`
5. `## 自动更新`
6. `## 导师抓取 Skill`

Do not add `## 本次更新`, developer-only sections, raw commit lists, or sub-bullets. Keep the public Skill guide link at the bottom:

`https://juniexd.github.io/AutoEmailSender/docs/mentor-crawler-skill`

## Writing Rules

- Write for ordinary users. State the supported action, visible improvement, or fixed symptom rather than implementation details.
- Keep each bullet to one or two short clauses. Merge related changes into a few strong bullets and order them by impact.
- Preserve the key action, object, and visible result. Omit field lists, option lists, progress internals, background stages, table names, parameter names, protocols, cache keys, and lock files unless essential for installation, upgrades, or data safety.
- Prefer concrete results over generic claims such as `更方便`, `提升效率`, or `体验更好`.
- Translate packaging/signing work into user-visible installation or update effects. Omit development-only, test-only, docs-only, badge, and website-copy changes unless ordinary usage changes.
- Describe a new or materially changed repository Skill as a separately installed capability and point to the public guide. Never imply the desktop installer contains it.

Use direct product language:

| Raw change | User-facing release note |
| --- | --- |
| mentor smart fill supports multiple fields and task progress | `导师管理新增智能补全，可为单个或多个导师补充信息。` |
| add batch task resend context | `批量任务支持重新发起未成功项。` |
| route profile pages through full extraction | `智能爬取支持从导师个人主页提取信息。` |
| add schema backup before migration | `升级前自动备份本地数据库。` |
| fix schedule display timezone offset | `修复定时发送时间显示偏移。` |
| fix cache key race | `修复多个任务同时检测同一模型时偶尔失败。` |

## Platform Text

Use exact package names:

- Windows: `AutoEmailSender-Setup-x.y.z.exe`
- macOS Apple Silicon: `AutoEmailSender-x.y.z-arm64.dmg`
- standalone Skill asset: `crawl-mentors-to-xlsx-vx.y.z.zip`

Do not put the Skill asset's direct download URL in the announcement; send users to the installation guide.

Explain macOS first-open behavior in plain language: Apple official verification is not complete, so macOS may block first launch; direct users to “系统设置 > 隐私与安全性” and “仍要打开”. State that Intel Mac is unsupported until an Intel or universal build exists. Do not mention ad-hoc signing, Developer ID, notarization, Gatekeeper commands, or require generic GitHub-only downloads.

Under automatic updates, state briefly that Windows and macOS Apple Silicon support in-app updates. Keep the transition note that pre-Sparkle macOS clients must manually install one current DMG first. Do not promise silent installation or automatic download; Sparkle installation remains user-confirmed.

## Tone

- `新增功能`: name the new supported action or scenario.
- `体验优化`: name the concrete prompt, state, speed, startup, installation, or workflow improvement.
- `问题修复`: name the visible symptom fixed.

Prefer terms already visible in the UI. Release notes should read like a product changelog, not marketing copy or a user manual.
