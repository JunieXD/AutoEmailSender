---
name: auto-email-sender-release
description: "Use when preparing, publishing, monitoring, or verifying an AutoEmailSender release; releasing repository-delivered Skills such as crawl-mentors-to-xlsx; polishing docs/releases/vX.md release notes; or running the cross-platform release scripts and macOS Sparkle release workflow on Linux, macOS, or Windows."
---

# Auto Email Sender Release

## Overview

Drive the project release flow from a version number to a verified public GitHub Release. Keep release notes clear for ordinary users, use the repository release scripts, investigate failures instead of bypassing checks, and verify the Windows and macOS artifacts after the tag is pushed.

Publish repository-delivered Skills under the same AutoEmailSender version and tag. Keep `crawl-mentors-to-xlsx` canonical under `.agents/skills/crawl-mentors-to-xlsx`, keep `.claude/skills/crawl-mentors-to-xlsx` as its Claude Code entry, and attach `crawl-mentors-to-xlsx-v<version>.zip` to the same GitHub Release. Do not create a separate Skill-only release, submit to a plugin marketplace, or put the Skill inside Electron installers unless the project explicitly changes that distribution policy.

Before handling Sparkle keys, macOS update artifacts, or end-to-end update QA, read `docs/operations/sparkle-release-operations.md`. On the project Mac, read `docs/operations/windows-parallels-release-qa.md` before Windows packaging or release-candidate QA. Treat those repository documents as the detailed sources of truth and keep this skill focused on the release procedure.

## Release Flow

1. Confirm the version matches `x.y.z` or the supported prerelease form `x.y.z-suffix`, without a leading `v`.
2. Fetch current tags from `origin`. If `v<version>` exists only because an unpublished workflow failed, follow Unpublished Tag Reuse before continuing. Then run `node scripts/release/check-release-version.mjs --version <version> --repo-root .`. Require `v<version>` to be absent and the requested version to be greater than the repository's remaining highest valid release tag. Never publish a downgrade from `master`.
3. Check the repository state. Do not stage or touch unrelated changes. A real release must run from `master` with no changes except the prepared `docs/releases/v<version>.md`.
4. Choose the release scripts for the current shell. A real release commits and pushes the versioned candidate, then dispatches `release.yml` with the exact commit SHA. The workflow creates the tag only after both platform builds succeed:
   - Linux/macOS/Git Bash: `./scripts/prepare-release.sh <version>` and later `./scripts/release.sh <version>`.
   - Windows PowerShell: `pwsh -NoLogo -NoProfile -File .\scripts\prepare-release.ps1 <version>` and later `pwsh -NoLogo -NoProfile -File .\scripts\release.ps1 <version>`.
5. Run the prepare-release command. It creates a release note template only; it is not a generated changelog.
6. Find the previous release tag with `git describe --tags --abbrev=0 --match "v*" HEAD^`.
7. Inspect the release context from the previous tag to `HEAD` before writing `docs/releases/v<version>.md`:
   - commit list: `git log --oneline <previousTag>..HEAD`
   - changed-file summary: `git diff --stat <previousTag>..HEAD` and `git diff --name-only <previousTag>..HEAD`
   - key product diffs under `frontend/src`, `backend/app`, `desktop/src`, `desktop`, and `scripts`
   - repository Skill diffs under `.agents/skills`, `.claude/skills`, and `.codex/skills`, plus their installation documentation under `website/docs`
   - changed tests under `backend/test`, `frontend/test`, `frontend/src/**/*.test.*`, `desktop/test`, and `website/test`
   - related owner documentation under `docs/{architecture,product,development,operations}/**` when it changed in the release range
8. Write `docs/releases/v<version>.md` directly from that context as a user-friendly announcement.
9. Keep these sections in order: `### 新增功能`, `### 体验优化`, `### 问题修复`. Put higher-impact changes first.
10. Run Repository Skill Preflight, Windows VM Preflight when available, and Sparkle Preflight below, then run the platform-specific release command from step 4. Confirm the dispatched workflow's `headSha` matches the release commit before treating it as the candidate run.
11. If local verification fails, follow Test Failure Handling before retrying.
12. Monitor the deferred release workflow. It must build and stage every required artifact before creating the tag. After the workflow creates the tag, follow Post-Tag Verification. Do not report the release complete merely because the release script exited successfully.

## Unpublished Tag Reuse

- Keep every tag immutable once its GitHub Release is public, any public asset or update feed references it, or its publish job has succeeded. Fix such a version only with a higher version.
- Reuse a failed tag only when the user has authorized unpublished-tag reuse and all of these checks pass: the exact tag has no GitHub Release (including drafts), no public assets or package entry, no appcast reference, and every workflow publish job for the tag failed, was cancelled, or was skipped before publication.
- Treat a draft Release separately. Inspect its assets before any deletion. Do not reuse the tag while a draft or staged asset exists; remove an empty failed draft only when that destructive cleanup is in scope, then verify the Release is absent.
- Do not reuse artifacts from the failed tag run. Fix and commit the cause, prepare the final versioned candidate without a tag, and require the repository preflight plus relevant Windows and macOS packaging preflights to pass on that exact commit first.
- Immediately before reuse, resolve and record the old local and remote tag targets. Delete only the exact `refs/tags/v<version>` locally and on `origin`, fetch with pruning, and verify the tag and Release are absent. Never use a wildcard or delete another version.
- Dispatch the replacement release only for the already-validated candidate commit. Let the publish job create the replacement tag after both platform builds succeed. Verify the new remote tag target before publication and report that the unpublished tag was replaced.

## Repository Skill Preflight

- Treat `crawl-mentors-to-xlsx` as a versioned deliverable that shares the application's version and tag. Distribute its canonical directory through the tagged repository and as the standalone `crawl-mentors-to-xlsx-v<version>.zip` asset in the same GitHub Release, never through the EXE, DMG, or a plugin marketplace.
- Inspect `git ls-tree -r --name-only HEAD -- .agents/skills/crawl-mentors-to-xlsx .claude/skills/crawl-mentors-to-xlsx`. Compare the result with `expected_canonical_files` in `backend/test/test_crawl_mentors_skill_contract.py`; require all ten canonical files plus the Claude Code forwarding `SKILL.md` to be present in the release commit.
- Use the available `skill-creator` `quick_validate.py` against both Skill directories when either entry changed. From `backend/`, run `uv run python -m unittest test.test_crawl_mentors_skill_contract test.test_crawl_mentors_skill_package`, then generate a local ZIP with `uv run python ../scripts/packaging/package_crawl_mentors_skill.py --version <version> --output-dir <temporary-dir>` and inspect its top-level directory and canonical file list. The normal release scripts run both tests automatically; do not remove or bypass them.
- When the release range changes the Skill guide or website navigation, run `npm test` and `npm run build` in `website` before publishing.
- Confirm the public guide is written for ordinary users who have not cloned the repository. Require paste-ready global installation flows for Codex and Claude Code, a manual Release ZIP flow for Windows/macOS/Linux, the complete-directory requirement, latest `master` versus reproducible tag behavior, the local-global directory scope, and the absence of a marketplace release. Do not imply that installing or updating the desktop application installs or updates the Skill.
- Treat `--dry-run` only as a command-sequence rehearsal: it does not enforce `master` or a clean worktree. Independently complete the branch, status, tracked-file, and version checks before treating the release as ready.

## Sparkle Preflight

- Read `docs/operations/sparkle-release-operations.md` before every macOS release.
- Confirm `gh` is authenticated to `JunieXD/AutoEmailSender` and that `gh secret list` contains both `SPARKLE_PUBLIC_ED_KEY` and `SPARKLE_ED_PRIVATE_KEY`. Check names only; never attempt to read, print, or reconstruct secret values.
- Do not regenerate or rotate the Sparkle keys during a normal release. Do not copy the private key into the repository, command arguments, workflow files, or logs.
- The GitHub Actions workflow owns Sparkle download preparation, signing, appcast generation, delta generation, and publication. Do not create an alternate manual publishing path.
- Treat the whole-feed signature separately from enclosure signatures. After any appcast URL or metadata rewrite, require `sign_update` to re-sign the final XML, then verify that exact output with `SPARKLE_PUBLIC_ED_KEY`. The draft gate must repeat feed verification before publication; URL and asset-name checks alone are insufficient.
- Confirm the macOS package retains the post-sign bundle cleanup and signature verification described in `docs/operations/sparkle-release-operations.md`, so the release can form a clean baseline for future deltas.

## Windows VM Preflight

- The project Mac has a persistent Parallels VM named `Windows 11` with an NTFS checkout at `C:\Users\junie\Projects\AutoEmailSender-Windows-QA`. Use it for release candidates and Windows-specific packaging, process, path, SQLite, or native dependency changes; do not invoke it for every small edit.
- Before tagging a release from this Mac, commit the candidate and run `rtk bash scripts/quality/run-windows-vm-release-qa.sh`. The runner transfers committed `HEAD`; it must not test or conceal uncommitted code. It may reuse a previously successful stage only when the Git tree inputs, toolchain fingerprint, and required outputs match. It always rebuilds the installer and reruns packaged lifecycle checks. Use `--force-full` when establishing a fresh cache baseline or investigating cache integrity.
- Require clean Windows dependency installs, frozen CLI/backend builds, the NSIS artifact, authenticated runtime protocol v3, repeated CLI status stability, safe stale-process failure, and a fresh runtime identity after restart.
- A VM failure blocks the release until it is classified and resolved. Do not bypass a missing native package, locked SQLite file, backend lifecycle failure, or VM prerequisite by manually editing build output.
- For changes to Ctrl+C or shutdown behavior, also perform the Windows Terminal control-event check documented in `docs/operations/windows-parallels-release-qa.md`; a forced-exit lifecycle test does not replace a real console Ctrl+C test.

## Test Failure Handling

- Inspect the failing command, error, stack trace, and recent behavior before deciding whether the release is blocked.
- If the product code is broken, stop and report the failing step. Do not patch around a real defect just to publish.
- If a desktop test failure depends on whether a build ran first, run `npm run clean` in `desktop` and rerun the focused source test before editing it. Treat any regenerated `desktop/dist/test` files or test discovery under `desktop/dist` as a topology regression.
- If a source test is stale and no longer matches intentional behavior, update it minimally to match the current user-facing behavior. Do not change production code unless investigation shows the application is wrong.
- Run the focused failing tests first, then rerun the relevant verification command.
- Because the release script requires a clean tree except for `docs/releases/v<version>.md`, commit a required stale-test fix before retrying. Use a focused Conventional Commit message such as `test(frontend): 修复发布前测试夹具`.
- Do not use `--skip-verify` or `-SkipVerify` unless the user explicitly requests it and understands the risk.

## Release Note Writing Rules

- Do not add a separate summary such as `## 本次更新`; keep grouped bullet sections.
- Write every bullet for ordinary users first: state the concrete user-visible capability or fixed symptom, not the implementation detail or an abstract value proposition.
- Keep each bullet to one or two short clauses. A reader should understand the feature or fixed problem at a glance.
- Treat the release announcement as an overview, not a user manual. Omit field and option lists, overwrite rules, progress locations, task states, button paths, background stages, and similar usage details unless they are essential for installation, upgrades, or data safety.
- Preserve the key action, object, and visible result while removing secondary details. Concise wording must remain specific.
- Do not replace omitted details with generic claims such as `减少手动整理`, `更方便`, `提升效率`, or `体验更好` when the concrete behavior can be stated directly.
- For example, use `导师管理新增智能补全，可为单个或多个导师补充信息。` Avoid both an exhaustive list of fields, fill rules, and progress views and the abstract wording `新增导师资料智能补全，减少手动整理信息。`
- Use short, plain sentences. Prefer direct wording like `批量任务支持重新发起未成功项。`
- Merge similar changes into a small number of stronger bullets. Do not split one user-visible improvement across many tiny points.
- Avoid internal table names, parameter names, protocol branches, cache keys, lock files, or fallback paths unless user impact would otherwise be unclear.
- Prefer concrete results over technical causes: use `模型连接失败时会显示更明确的错误原因。` instead of `新增 SOCKS 初始化错误包装。`
- Translate packaging and signing details into their visible effect. Keep terms such as `ad-hoc`, `Developer ID`, `notarization`, and `Gatekeeper` out of public release notes unless the user explicitly requests technical detail.
- Avoid sub-bullets and marketing language.
- Omit development-only, packaging-only, documentation-only, README, badge, and website-copy changes unless they affect installation, upgrade, onboarding, data safety, reliability, or ordinary product usage.
- Describe a new or materially changed repository Skill as a user-facing capability, and direct ordinary users to the global installation guide backed by the repository version. Do not describe it as built into the desktop installer. Omit contract-test-only or internal Skill maintenance when behavior and installation are unchanged.
- Keep the generated announcement's final `## 导师抓取 Skill` section and its public installation-guide link. This fixed onboarding entry belongs at the bottom even when the release range contains no Skill changes.

## Platform Note Rules

- Keep the generated `## 安装说明` and `## 自动更新` sections in the final public release note.
- Keep `## 导师抓取 Skill` after `## 自动更新`, with the clickable public guide URL `https://juniexd.github.io/AutoEmailSender/docs/mentor-crawler-skill`.
- Name the standalone Skill asset exactly `crawl-mentors-to-xlsx-v<version>.zip`, but do not add its direct download link to the generated announcement; direct users to the installation guide instead.
- Keep the exact package names:
  - Windows: `AutoEmailSender-Setup-x.y.z.exe`
  - macOS Apple Silicon: `AutoEmailSender-x.y.z-arm64.dmg`
- Explain the macOS first-open restriction in plain language: the app has not completed Apple's official verification, so macOS may block the first launch. Tell users to choose “仍要打开” under “系统设置 > 隐私与安全性”. Keep technical signing terminology in internal operations documentation and do not imply that Sparkle removes the restriction.
- State that Intel Mac remains unsupported until an Intel or universal build is added.
- Do not add a generic sentence requiring users to download installers only from the project's GitHub Releases page unless the user explicitly requests that warning.
- In `## 自动更新`, state briefly that Windows and macOS Apple Silicon support in-app updates. Mention automatic checks where useful, but keep updater implementation and step-by-step behavior out of the public announcement.
- Keep the transition note that a pre-Sparkle macOS client must manually install a current DMG once before it can use Sparkle updates.
- Do not claim silent installation or automatic download. Sparkle installation remains user-confirmed, and the macOS first-open restriction remains.

## Post-Tag Verification

- Inspect the exact tag with `git ls-tree -r --name-only v<version> -- .agents/skills/crawl-mentors-to-xlsx .claude/skills/crawl-mentors-to-xlsx`, compare it with the tested manifest, and inspect a `git archive` of the same paths. Require all canonical files and the Claude Code entry in the tagged source payload. Download and inspect `crawl-mentors-to-xlsx-v<version>.zip`; require one top-level `crawl-mentors-to-xlsx` directory containing the same canonical files and no forwarding entry, caches, or extra nesting.
- Locate the `Release Desktop` workflow run for the exact tag with `gh run list`, then wait for it with `gh run watch <run-id> --exit-status`.
- Require `build-windows`, `build-macos`, and `publish` to succeed. The publish job must generate and upload the standalone Skill ZIP with installers and deltas before `appcast.xml`, then publish the staged draft Release. A rerun may continue an existing draft, but it must refuse to edit or replace assets on an already published Release before any upload begins.
- Inspect the exact tag with `gh release view v<version> --json isDraft,assets,url`. Require a non-draft release containing:
  - `AutoEmailSender-Setup-x.y.z.exe`, its `.blockmap`, and `latest.yml`
  - `AutoEmailSender-x.y.z-arm64.dmg`
  - `crawl-mentors-to-xlsx-v<version>.zip`
  - `appcast.xml`
  - zero to three `.delta` files
- Download the public `releases/latest/download/appcast.xml` and verify its whole-feed signature with the public key embedded in the previous installed client. Also verify the current DMG and delta enclosure signatures; a matching URL, filename, or size does not prove the feed itself is valid.
- When an earlier Sparkle appcast exists, inspect the `Generate signed appcast and deltas` log and account for every omitted delta. Never treat a zero-delta result as silently normal.
- For the first Sparkle-enabled release, no delta is expected because no earlier appcast exists. If an older published source cannot produce a delta (for example, because of code-signing extended attributes), verify the signed full-DMG fallback and report the affected source versions. Do not replace published assets; publish the packaging fix as a new clean baseline, then require a later release to generate a delta from that baseline before calling differential updates healthy.
- If `website/**` changed in the release range, locate the `Deploy Website` run for the exact release commit, wait for it to succeed, and verify the public Skill guide opens and still documents the Codex and Claude Code user-level installation paths, paste-ready installation requests, manual Release ZIP installation, complete-directory requirement, and update instructions.
- If macOS functional QA is in scope, verify from an installed previous version that Sparkle displays the release notes, validates the update, and restarts into the new version. Follow Update QA Safety.
- For a transient Actions failure, rerun only after identifying the cause. For a product or packaging defect, fix `master`; use a higher version for any published release, or follow Unpublished Tag Reuse when its strict conditions are satisfied. Do not replace published signed assets or rotate Sparkle keys without explicit user approval.

## Update QA Safety

- Isolate end-to-end macOS update tests from everyday data. Prefer a dedicated macOS test account.
- Do not rely only on Electron's `--user-data-dir`; Sparkle relaunch may drop command-line arguments and reopen the default `~/Library/Application Support/auto-email-sender-desktop` database.
- If using a QA build, set a distinct `userData` path in application code before any data path is read, and ensure both the old and updated builds contain the same isolation.
- Back up the target test database before upgrading and confirm no Auto Email Sender process remains afterward.
- Do not restore, delete, or overwrite a real user database without explicit approval. Do not tell users to disable Gatekeeper or run `xattr` as an installation workaround.

## Rewrite Examples

| Raw change | User-facing release note |
| --- | --- |
| `mentor smart fill supports multiple fields, empty-only writes, and task-center progress` | `导师管理新增智能补全，可为单个或多个导师补充信息。` |
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
