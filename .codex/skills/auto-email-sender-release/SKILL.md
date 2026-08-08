---
name: auto-email-sender-release
description: "Use when preparing, certifying, publishing, recovering, monitoring, or verifying an AutoEmailSender release; releasing repository-delivered Skills such as crawl-mentors-to-xlsx; writing docs/releases/vX.md release notes; or running the cross-platform desktop, Windows VM, and macOS Sparkle release workflow."
---

# Auto Email Sender Release

Use one state machine for every release: **Prepare -> Certify -> Promote -> Verify**. A normal release builds expensive artifacts once during Certify; Promote only publishes those exact certified artifacts.

Before macOS or Sparkle work, read `docs/operations/sparkle-release-operations.md`. On the project Mac, read `docs/operations/windows-parallels-release-qa.md` before Windows QA. When writing or reviewing the public announcement, read [release-notes.md](references/release-notes.md).

## 1. Prepare

1. Normalize the version to `x.y.z` or `x.y.z-suffix` without a leading `v`. Fetch `origin` and tags.
2. Require `master`. Preserve unrelated changes. A real certification must use a clean, committed SHA; only an uncommitted `docs/releases/v<version>.md` is allowed while preparing the announcement.
3. Run `node scripts/release/check-release-version.mjs --version <version> --repo-root .`. If the tag belongs to a failed unpublished attempt, follow Unpublished Tag Recovery first; do not automatically increment the patch version.
4. Run the shell-appropriate prepare command:
   - POSIX: `./scripts/prepare-release.sh <version>`
   - PowerShell: `pwsh -NoLogo -NoProfile -File .\scripts\prepare-release.ps1 <version>`
5. Find the prior release with `git describe --tags --abbrev=0 --match "v*" HEAD^`. Inspect commits, changed paths, product/packaging diffs, tests, repository Skills, and owner docs through `HEAD`. Write `docs/releases/v<version>.md` using the release-note reference.
6. Plan the minimum checks for any follow-up change:

   ```bash
   node scripts/release/release-impact.mjs --base <last-certified-sha> --head HEAD
   ```

   Add `--candidate` only for the frozen final candidate. It always requires one formal Windows and macOS certification. Do not interpret “new commit” as “rerun everything”; run the checks reported by the tool, then certify a new final SHA when packaged inputs changed.
7. For `crawl-mentors-to-xlsx` changes, verify the canonical `.agents` Skill, Claude forwarding entry, contract/package tests, generated ZIP structure, and public installation guide. The Skill shares the app version and Release as `crawl-mentors-to-xlsx-v<version>.zip`; it is never embedded in installers or released through a marketplace.

## 2. Certify

Run the release entrypoint without a promotion ID:

```bash
./scripts/release.sh <version>
# PowerShell: .\scripts\release.ps1 <version>
```

This prepares and pushes the exact release commit, dispatches `release.yml` with `publish=false`, and does not create a tag or Release. Confirm the workflow `headSha` equals the committed SHA.

The workflow must pass its cheap Ubuntu preflight before Windows/macOS jobs. It builds signed artifacts, records their names/sizes/SHA-256 values and toolchains, and emits `release-candidate.json`. Keep the candidate workflow run ID.

On the project Mac, run formal Windows VM QA exactly once for this frozen SHA while certification runs:

```bash
rtk bash scripts/quality/run-windows-vm-release-qa.sh
```

Use `--quick` only for daily Windows-sensitive changes. Quick mode skips VC++ preparation, NSIS, and packaged lifecycle checks and is not release evidence. Release-note-only changes do not invalidate formal VM evidence. Windows packaging, installer, runtime, native dependency, or frozen-product changes do.

Do not edit code, version metadata, or release notes after certification. If anything changes, use `release-impact.mjs` to invalidate only affected local/VM evidence, then certify the new SHA. Candidate artifacts remain bound to their original SHA and cannot be mixed into another candidate.

## 3. Promote

After the candidate workflow, formal Windows VM QA, release note review, and user approval all pass, publish the same artifacts:

```bash
./scripts/release.sh <version> --promote-run <candidate-run-id>
# PowerShell: .\scripts\release.ps1 <version> -PromoteRun <candidate-run-id>
```

Promotion must skip product tests and rebuilds. It verifies that `release-candidate.json`, release tag, exact SHA, release-note hash, workflow run ID, artifact names, sizes, and SHA-256 values still match. It then creates/reuses only the authorized unpublished tag, stages a draft, checks the draft, and makes it public.

For Sparkle, normalize asset names and rewrite URLs before the final whole-feed signature. Re-sign the final XML with `sign_update`, verify it with `SPARKLE_PUBLIC_ED_KEY`, and repeat feed signature plus URL/name/size checks against the actual draft assets. Upload `appcast.xml` last. An enclosure signature does not validate the whole feed.

## 4. Verify

Wait for the exact promotion run and require the certification/download/publish jobs to succeed. Confirm the remote tag targets the certified SHA and the Release is public with:

- Windows EXE, blockmap, and `latest.yml`
- Apple Silicon DMG
- repository Skill ZIP
- signed `appcast.xml`
- zero to three justified Sparkle deltas

Download the public `releases/latest/download/appcast.xml`; verify its whole-feed signature with the public key embedded in the previous client. Verify current DMG/delta enclosure signatures and account for every missing delta. From the v2.5.3 clean baseline onward, the latest clean prior version must produce a delta.

Inspect the tagged repository Skill and downloaded ZIP against the tested canonical manifest. If `website/**` changed, require the exact commit's deployment and public guide checks. When functional update QA is in scope, isolate it from daily data as described in the Sparkle operations guide.

## Hard Stops

Do not promote when any of these is true:

- Cheap preflight, relevant impact checks, formal Windows candidate QA, or macOS certification is missing or belongs to another SHA.
- `release-candidate.json`, release-note hash, run ID, artifact digest, asset name, or draft URL differs from the candidate.
- The final appcast was changed after signing or fails whole-feed verification.
- A required delta from the latest clean baseline is absent without an investigated, documented reason.
- A public Release, public asset/update feed, or successful publish job may already have exposed the tag.
- The worktree or branch violates the release entrypoint's contract.

Never use `--skip-verify` / `-SkipVerify` unless the user explicitly accepts the risk. Never replace a public signed asset, rotate Sparkle keys during a normal release, or bypass a real product/packaging failure.

## Failure Recovery

Classify a failure before retrying. Run the focused failing test first. For a follow-up commit, run `release-impact.mjs` from the last certified SHA to `HEAD`; rerun only reported checks, but certify new artifacts whenever packaged inputs changed.

If desktop test behavior depends on stale build output, run `npm run clean` in `desktop` and rerun the source test before editing. Treat tests discovered under `desktop/dist` as a topology defect. Update a stale fixture minimally only when production behavior is intentional; do not patch around a product bug.

### Unpublished Tag Recovery

Treat a tag as immutable once a public Release, public asset/feed, package entry, or successful publish job may reference it. Use a higher version.

Reuse `v<version>` only with the user's authorization and evidence that no Release exists, no draft/staged asset remains, no appcast references it, and all publish jobs failed/cancelled/skipped before publication. Record exact local/remote tag targets, delete only `refs/tags/v<version>` locally and on `origin`, prune/fetch, and verify absence. Never reuse failed artifacts; certify the fixed final SHA and let Promote create the tag.

## Security And QA Safety

Check only Sparkle secret names (`SPARKLE_PUBLIC_ED_KEY`, `SPARKLE_ED_PRIVATE_KEY`); never print or reconstruct values. Do not put the private key in files, arguments, workflow text, or logs.

Isolate end-to-end update QA. Sparkle relaunch may discard `--user-data-dir`, so prefer a dedicated macOS test account or code-level QA data path present in both versions. Back up the target test database and stop all app processes afterward. Never overwrite real user data, disable Gatekeeper, or recommend `xattr` as an installation workaround.
