---
name: auto-email-sender-release
description: "Use when preparing, publishing, monitoring, or verifying an AutoEmailSender release; polishing docs/releases/vX.md release notes; or running the cross-platform release scripts and macOS Sparkle release workflow on Linux, macOS, or Windows."
---

# Auto Email Sender Release

## Overview

Drive the project release flow from a version number to a verified public GitHub Release. Keep release notes clear for ordinary users, use the repository release scripts, investigate failures instead of bypassing checks, and verify the Windows and macOS artifacts after the tag is pushed.

Before handling Sparkle keys, macOS update artifacts, or end-to-end update QA, read `docs/sparkle-release-operations.md`. Treat that repository document as the detailed source of truth and keep this skill focused on the release procedure.

## Release Flow

1. Confirm the version matches `x.y.z` or the supported prerelease form `x.y.z-suffix`, without a leading `v`.
2. Check the repository state. Do not stage or touch unrelated changes. A real release must run from `master` with no changes except the prepared `docs/releases/v<version>.md`.
3. Choose the release scripts for the current shell:
   - Linux/macOS/Git Bash: `./scripts/prepare-release.sh <version>` and later `./scripts/release.sh <version>`.
   - Windows PowerShell: `pwsh -NoLogo -NoProfile -File .\scripts\prepare-release.ps1 <version>` and later `pwsh -NoLogo -NoProfile -File .\scripts\release.ps1 <version>`.
4. Run the prepare-release command. It creates a release note template only; it is not a generated changelog.
5. Find the previous release tag with `git describe --tags --abbrev=0 --match "v*" HEAD^`.
6. Inspect the release context from the previous tag to `HEAD` before writing `docs/releases/v<version>.md`:
   - commit list: `git log --oneline <previousTag>..HEAD`
   - changed-file summary: `git diff --stat <previousTag>..HEAD` and `git diff --name-only <previousTag>..HEAD`
   - key product diffs under `frontend/src`, `backend/app`, `desktop/src`, `desktop`, and `scripts`
   - changed tests under `backend/test`, `frontend/test`, `frontend/src/**/*.test.*`, `desktop/test`, and `website/test`
   - related `docs/superpowers/specs/**` and `docs/superpowers/plans/**` files when they changed in the release range
7. Write `docs/releases/v<version>.md` directly from that context as a user-friendly announcement.
8. Keep these sections in order: `### 新增功能`, `### 体验优化`, `### 问题修复`. Put higher-impact changes first.
9. Run the Sparkle preflight below, then run the platform-specific release command from step 3.
10. If local verification fails, follow Test Failure Handling before retrying.
11. After the tag is pushed, follow Post-Tag Verification. Do not report the release complete merely because the release script exited successfully.

## Sparkle Preflight

- Read `docs/sparkle-release-operations.md` before every macOS release.
- Confirm `gh` is authenticated to `JunieXD/AutoEmailSender` and that `gh secret list` contains both `SPARKLE_PUBLIC_ED_KEY` and `SPARKLE_ED_PRIVATE_KEY`. Check names only; never attempt to read, print, or reconstruct secret values.
- Do not regenerate or rotate the Sparkle keys during a normal release. Do not copy the private key into the repository, command arguments, workflow files, or logs.
- The GitHub Actions workflow owns Sparkle download preparation, signing, appcast generation, delta generation, and publication. Do not create an alternate manual publishing path.

## Test Failure Handling

- Inspect the failing command, error, stack trace, and recent behavior before deciding whether the release is blocked.
- If the product code is broken, stop and report the failing step. Do not patch around a real defect just to publish.
- If a desktop test failure comes from an outdated ignored file under `desktop/dist/test`, run `npm run build` in `desktop` and rerun the focused test before editing source tests.
- If a source test is stale and no longer matches intentional behavior, update it minimally to match the current user-facing behavior. Do not change production code unless investigation shows the application is wrong.
- Run the focused failing tests first, then rerun the relevant verification command.
- Because the release script requires a clean tree except for `docs/releases/v<version>.md`, commit a required stale-test fix before retrying. Use a focused Conventional Commit message such as `test(frontend): 修复发布前测试夹具`.
- Do not use `--skip-verify` or `-SkipVerify` unless the user explicitly requests it and understands the risk.

## Release Note Writing Rules

- Do not add a separate summary such as `## 本次更新`; keep grouped bullet sections.
- Write every bullet for ordinary users first: state the visible result, not the implementation detail.
- Use short, plain sentences. Prefer direct wording like `批量任务支持重新发起未成功项。`
- Avoid vague praise such as `更完整`, `更方便`, `更智能`, `更流畅`, or `提升体验` unless the sentence names the concrete result.
- Merge similar changes into a small number of stronger bullets. Do not split one user-visible improvement across many tiny points.
- Avoid internal table names, parameter names, protocol branches, cache keys, lock files, or fallback paths unless user impact would otherwise be unclear.
- Prefer concrete results over technical causes: use `模型连接失败时会显示更明确的错误原因。` instead of `新增 SOCKS 初始化错误包装。`
- Keep bullets to one sentence in most cases and avoid sub-bullets or marketing language.
- Omit development-only, packaging-only, documentation-only, README, badge, and website-copy changes unless they affect installation, upgrade, onboarding, data safety, reliability, or ordinary product usage.

## Platform Note Rules

- Keep the generated `## 安装说明` and `## 自动更新` sections in the final public release note.
- Keep the exact package names:
  - Windows: `AutoEmailSender-Setup-x.y.z.exe`
  - macOS Apple Silicon: `AutoEmailSender-x.y.z-arm64.dmg`
- Explain that the macOS app is ad-hoc signed but not Developer ID signed or notarized. Keep the first-open Gatekeeper instruction and do not imply that Sparkle removes it.
- State that Intel Mac remains unsupported until an Intel or universal build is added.
- Keep the warning that installers must come only from this project's GitHub Releases page.
- In `## 自动更新`, state that Windows uses its existing in-app updater. State that macOS Apple Silicon automatically checks for updates and can also check on demand; after user confirmation, Sparkle downloads, verifies, replaces, and restarts the app.
- Keep the transition note that a pre-Sparkle macOS client must manually install a current DMG once before it can use Sparkle updates.
- Do not claim silent installation or automatic download. Sparkle installation remains user-confirmed, and the app is still not notarized.

## Post-Tag Verification

- Locate the `Release Desktop` workflow run for the exact tag with `gh run list`, then wait for it with `gh run watch <run-id> --exit-status`.
- Require `build-windows`, `build-macos`, and `publish` to succeed. The publish job must upload installers and deltas before `appcast.xml`, then publish the staged draft Release.
- Inspect the exact tag with `gh release view v<version> --json isDraft,assets,url`. Require a non-draft release containing:
  - `AutoEmailSender-Setup-x.y.z.exe`, its `.blockmap`, and `latest.yml`
  - `AutoEmailSender-x.y.z-arm64.dmg`
  - `appcast.xml`
  - up to three `.delta` files when prior Sparkle releases exist
- For the first Sparkle-enabled release, no delta is expected because no earlier appcast exists. Do not treat that as a failure.
- If macOS functional QA is in scope, verify from an installed previous version that Sparkle displays the release notes, validates the update, and restarts into the new version. Follow Update QA Safety.
- For a transient Actions failure, rerun only after identifying the cause. For a product or packaging defect, fix `master` and publish a new version. Do not move or recreate a pushed tag, manually replace signed assets, or rotate Sparkle keys without explicit user approval.

## Update QA Safety

- Isolate end-to-end macOS update tests from everyday data. Prefer a dedicated macOS test account.
- Do not rely only on Electron's `--user-data-dir`; Sparkle relaunch may drop command-line arguments and reopen the default `~/Library/Application Support/auto-email-sender-desktop` database.
- If using a QA build, set a distinct `userData` path in application code before any data path is read, and ensure both the old and updated builds contain the same isolation.
- Back up the target test database before upgrading and confirm no Auto Email Sender process remains afterward.
- Do not restore, delete, or overwrite a real user database without explicit approval. Do not tell users to disable Gatekeeper or run `xattr` as an installation workaround.

## Rewrite Examples

| Raw change | User-facing release note |
| --- | --- |
| `add batch task resend context` | `批量任务支持重新发起未成功项。` |
| `route profile entry pages through full-page extraction` | `智能爬取支持从导师个人主页提取信息。` |
| `add schema backup before migration` | `升级前自动备份本地数据库。` |
| `fix schedule display timezone offset` | `修复定时发送时间显示偏移。` |
| `fix cache unique key race` | `修复多个任务同时检测同一模型时偶尔失败。` |
| `sync package-lock with package.json` | `修复前端依赖配置不一致导致安装或构建异常。` |

## Tone Guide

- `新增功能`: name the new supported action or scenario.
- `体验优化`: name the concrete prompt, state, speed, startup, installation, or workflow change.
- `问题修复`: name the visible symptom that is fixed.
- Prefer terms already visible in the product UI. If a technical term is necessary, pair it with a plain-language explanation.
- Good bullets read like product changelog entries, not release marketing copy.

## Editing Rules

- If the prepare-release script already created `docs/releases/v<version>.md`, edit that file directly. It is only a template, not completed release content.
- Keep the release note as complete Markdown without developer-only sections such as `技术说明`, `内部变更`, or raw commit lists.
- If local verification fails, stop at that step and follow Test Failure Handling. Do not continue to version bumps, commits, tags, or pushes until verification passes.
- Do not create alternate release paths or bypass the repository scripts.
