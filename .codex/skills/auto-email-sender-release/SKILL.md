---
name: auto-email-sender-release
description: "Use when preparing, certifying, publishing, recovering, monitoring, or verifying an AutoEmailSender release; releasing repository-delivered Skills such as crawl-mentors-to-xlsx; writing docs/releases/vX.md release notes; or running the cross-platform desktop, Windows VM, and macOS Sparkle release workflow."
---

# Auto Email Sender Release

Release through **Prepare → Certify → Promote → Verify**. Build once, then publish the certified artifacts. A request such as `发布 2.6.4` authorizes this workflow; preparation or verification alone authorizes that stage. Reconfirm changes to the version, repository or recovery scope, not already authorized publication.

## Prepare

Work from `master` and a clean, committed candidate. Fetch origin and tags, then run:

```bash
node scripts/release/check-release-version.mjs --version <version> --repo-root .
./scripts/prepare-release.sh <version>
```

Inspect changes since the prior release and write `docs/releases/v<version>.md` using [release-notes.md](references/release-notes.md). The app and repository Skill ZIP share a version; the Skill is a separate Release asset, not an installer component.

## Certify and promote

```bash
./scripts/release.sh <version>
```

This commits/pushes the candidate and dispatches certification without publishing. Keep the run ID and verify its SHA. Candidate certification requires preflight, CLI gate and both platform builds. On the project Mac, run formal Windows QA for the frozen candidate according to `docs/operations/windows-parallels-release-qa.md`; `--quick` is for daily checks, not release evidence.

Use the release entrypoint's checks. If repository tests were already run, `run_all_tests.py --write-evidence .git/release-quality-evidence.json` produces evidence reusable through `--quality-evidence`; the script checks SHA, toolchain and age. Never fabricate evidence.

When the candidate and required QA pass, publish the same artifacts:

```bash
./scripts/release.sh <version> --promote-run <candidate-run-id>
```

Promotion verifies the candidate manifest, version, SHA, announcement and artifact hashes. It does not rebuild. Follow-up changes use `node scripts/release/release-impact.mjs --base <last-certified-sha> --head HEAD` to select checks; add `--candidate` for final certification. Never mix candidates.

## Verify

```bash
./scripts/verify-release.sh <version> --candidate-run <candidate-run-id> --promotion-run <promotion-run-id>
```

The verifier checks public artifacts, Sparkle signatures, Skill ZIP and relevant website deployment. Candidate runs require `preflight`, `cli-gate`, `build-windows`, `build-macos`, `certify`; promotion requires `publish`. Historical topology is derived by the verifier.

PowerShell has matching `scripts/*.ps1` entrypoints with `-PromoteRun`, `-CandidateRun`, `-PromotionRun`, and `-QualityEvidence` parameters.

## Sparkle and recovery

Read `docs/operations/sparkle-release-operations.md` for macOS signing, historical DMG caches, delta requirements and isolated update QA. Final appcast URLs must be fixed before signing; verify the whole feed and enclosure signatures against actual draft assets, uploading the feed last. Secret values must stay out of files, arguments and logs. Routine releases do not rotate keys or replace public assets.

Diagnose a failed check before retrying. Preserve the failure and thresholds; an environmental failure may justify a repeat with the same inputs, not repeated attempts until green. Use impact checks after fixes. Stale desktop build output can be removed with `npm run clean` before repeating the affected source test.

A public tag or asset is immutable. Reuse an unpublished tag only with user authorization and evidence that no Release, staged asset, appcast reference or successful publication exposed it. Remove only that exact local/remote tag, verify absence, and certify fresh artifacts. When publication is uncertain, investigate before retrying. Never bypass failed product checks or use `--skip-verify` without explicit acceptance.
