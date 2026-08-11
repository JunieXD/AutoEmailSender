---
name: auto-email-sender-release
description: "Use when preparing, certifying, publishing, recovering, observing, superseding, or verifying an AutoEmailSender stable release or alpha/beta/rc prerelease; releasing repository-delivered Skills such as crawl-mentors-to-xlsx; writing docs/releases/vX.md release notes; or running the cross-platform desktop, Windows VM, and macOS Sparkle release workflow."
---

# Auto Email Sender Release

Choose one lane before acting:

- **Stable**: `x.y.z`, source branch `master`, full update metadata, and **Prepare -> Certify -> Promote -> Verify**.
- **Prerelease**: `x.y.z-(alpha|beta|rc).n`, explicit source branch/SHA/channel, manual installers only, and **Prepare Prerelease -> Certify Prerelease -> Publish Prerelease -> Verify Isolation -> Observe -> Supersede/Withdraw**.

Both lanes build expensive artifacts once during certification and publish only those exact bytes. Never route a prerelease through the stable entrypoint or infer one lane's approval from the other.

Before macOS or Sparkle work, read `docs/operations/sparkle-release-operations.md`. Before any alpha/beta/rc work, read `docs/operations/desktop-prerelease-operations.md`. On the project Mac, read `docs/operations/windows-parallels-release-qa.md` before Windows QA. When writing or reviewing any public announcement, read [release-notes.md](references/release-notes.md).

## 1. Prepare

1. Normalize the version to `x.y.z` or `x.y.z-suffix` without a leading `v`. Fetch `origin` and tags.
2. Require `master`. Preserve unrelated changes. A real certification must use a clean, committed SHA; only an uncommitted `docs/releases/v<version>.md` is allowed while preparing the announcement.
3. Run `node scripts/release/check-release-version.mjs --version <version> --repo-root .`. If the tag belongs to a failed unpublished attempt, follow Unpublished Tag Recovery first; do not automatically increment the patch version.
4. Run the shell-appropriate prepare command:
   - POSIX: `./scripts/prepare-release.sh <version>`
   - PowerShell: `pwsh -NoLogo -NoProfile -File .\scripts\prepare-release.ps1 <version>`
5. Find the prior stable release with `node scripts/release/prerelease-contract.mjs latest-stable --repo-root . --ref HEAD^`; prerelease tags are never stable baselines. Inspect commits, changed paths, product/packaging diffs, tests, repository Skills, and owner docs through `HEAD`. Write `docs/releases/v<version>.md` using the release-note reference.
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
rtk bash scripts/quality/run-windows-vm-release-qa.sh \
  --candidate-installer /absolute/path/AutoEmailSender-Setup-<current-version>.exe \
  --candidate-installer-sha256 <release-candidate-json-installer-sha256> \
  --candidate-manifest /absolute/path/release-candidate.json \
  --candidate-run-id <candidate-workflow-run-id> \
  --previous-installer /absolute/path/AutoEmailSender-Setup-<previous-version>.exe \
  --previous-installer-sha256 <published-previous-installer-sha256>
```

The candidate installer, digest, manifest, and run ID must come from the same candidate workflow
run. The runner validates manifest schema, SHA, version, run ID, asset name, size, and digest. The
VM may still build a local installer as a packaging contract, but installed lifecycle and soak
evidence must use the transferred candidate bytes; a same-SHA rebuild is not a substitute.

Use `--quick` only for daily Windows-sensitive changes. Quick mode skips VC++ preparation, NSIS, and packaged lifecycle checks and is not release evidence. Release-note-only changes do not invalidate formal VM evidence. Windows packaging, installer, runtime, native dependency, or frozen-product changes do.

For the desktop API + Worker topology's final certification, run the same frozen SHA with
`--normal-soak --seeded-chaos --seed <recorded-seed>` as documented in
`docs/development/desktop_api_worker_goal_acceptance.md`; do not accumulate shorter runs to satisfy
the 24-hour or 8-hour gates.

For the matching macOS lifecycle certification, bind the previous public DMG with
`--expected-previous-dmg-sha256` as well as binding the current candidate with
`--expected-dmg-sha256`. Pass the same `--candidate-manifest` and `--candidate-run-id` to all three
macOS scenarios. Development smoke may calculate its local previous-package digest, but that
automatically adopted value is not public-asset provenance and is not release evidence.

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

## Prerelease Lane

### Prepare Prerelease

1. Require explicit `version`, `channel`, and `source_branch`. Accept only alpha/beta/rc versions with a matching channel and an increment identifier. Fetch origin and tags; require the current clean branch to equal `source_branch`.
2. Decide whether the source branch needs the latest `master` merged before freezing. This does not authorize merging the source branch back to `master`.
3. Run the shell-appropriate prepare entrypoint:
   - POSIX: `./scripts/prepare-prerelease.sh <version> --channel <channel> --source-branch <branch>`
   - PowerShell: `.\scripts\prepare-prerelease.ps1 <version> -Channel <channel> -SourceBranch <branch>`
4. Complete `docs/releases/v<version>.md` using the prerelease structure in the release-note reference, copy it exactly to `desktop/release-notes.md`, test, commit, and record the final 40-character `release_sha`. Do not change code, versions, or notes after freezing.

### Certify Prerelease

Run dry-run first:

```bash
./scripts/prerelease.sh certify <version> \
  --channel <channel> \
  --source-branch <branch> \
  --release-sha <sha> \
  --dry-run
```

The non-dry command pushes the explicit source branch and dispatches a remote workflow. Require separate user approval for both actions before removing `--dry-run`. Certification must not create a tag or Release. Record the successful `Release Desktop` run ID and require exactly one EXE, one arm64 DMG, and `prerelease-candidate.json`; reject stable feed metadata, blockmaps, deltas, mixed runs, or rebuilt substitutes.

Use the exact workflow assets for formal packaged QA. Pass `--prerelease-certification`, not stable `--certification`: prerelease normal soak is at least 7200 seconds and seeded chaos at least 3600 seconds; the stable 86400/28800 gates remain unchanged. Bind every platform run to the candidate manifest, run ID, release SHA, installer digest, and previous public stable installer digest.

Before another expensive candidate is requested after QA harness changes, run a non-certifying
`--harness-rehearsal` on Windows and macOS. It may use an invalidated candidate or a local package,
but must bind both package digests and must not accept a candidate manifest/run ID. Run it twice:
the first run deliberately interrupts after the previous-stable seed, and the immediate second run
must prove stale installer/process or DMG-mount cleanup plus bounded timeout recovery. Reports must
say `certification_eligible=false` and `evidence_purpose=non-certifying-harness-rehearsal`.

After Certify produces new exact assets, run `--candidate-admission` before source/build suites or
long soaks. Admission binds the manifest, run ID, release SHA, version, current package digest, and
previous public stable package digest, then exercises previous-stable seed, non-ASCII/long paths,
candidate overlay, split/combined lifecycle, migration/integrity, local diagnostic export,
sleep/wake, uninstall, and repeat install. It is still non-certifying and must report
`certification_eligible=false` with `evidence_purpose=non-certifying-candidate-admission`.
Run Windows and macOS serially: Windows admission, macOS admission, Windows formal QA, then macOS
formal QA. Neither rehearsal nor admission can satisfy lifecycle, normal-soak, seeded-chaos, or
publication gates, and an invalidated package must never be mixed into exact-candidate evidence.

### Publish Prerelease

Require completed dual-platform exact-package QA, reviewed notes, no unresolved blocking/high-risk defects, and explicit approval for tag creation and public GitHub Prerelease. Run publish dry-run first, then remove `--dry-run` only after approval:

```bash
./scripts/prerelease.sh publish <version> \
  --channel <channel> \
  --source-branch <branch> \
  --release-sha <sha> \
  --candidate-run <run-id> \
  --dry-run
```

The publish workflow must download and verify the exact candidate run, recheck stable isolation before mutation, create a new immutable tag and draft, verify downloaded draft bytes, then publish with `prerelease=true` and `Latest=false`. It must never rebuild, clobber, upload `latest.yml`/`appcast.xml`/blockmaps/deltas, or publish the repository Skill ZIP.

### Verify Isolation

Require the public tag to target the frozen SHA and the Release to contain exactly the EXE, DMG, and candidate manifest. Require the certification baseline and post-publication snapshot to match for stable Latest release identity plus `appcast.xml` and `latest.yml` IDs, sizes, and SHA-256 values. Confirm `/releases/latest` and the repository home Latest card still show the stable version.

Run real update checks from the previous public Windows and macOS clients and require both to report no prerelease. API/workflow checks do not replace this client evidence.

### Observe

Use only local bundles that internal testers or users actively export and send. Analyze them with `scripts/quality/analyze_beta_diagnostics.py` and the beta diagnostics operations guide. Collect both healthy and failure reports, but never add remote telemetry, automatic upload, polling, or background collection. Never commit raw bundles or extracted user data.

### Supersede / Withdraw

Validate a replacement with `node scripts/release/prerelease-contract.mjs supersede --previous-version <old> --replacement-version <new>`. Require the same core and a strictly higher prerelease version, then run the full prerelease state machine again.

With user approval, mark a dangerous old Release as “停止使用” and point to rollback/diagnostics/replacement guidance. Keep its tag and assets immutable. Never delete, move, overwrite, or reuse public prerelease assets; an abandoned tag/draft also defaults to a higher version unless the user separately authorizes destructive recovery with complete non-exposure evidence.

## Hard Stops

Do not promote when any of these is true:

- Cheap preflight, relevant impact checks, formal Windows candidate QA, or macOS certification is missing or belongs to another SHA.
- `release-candidate.json`, release-note hash, run ID, artifact digest, asset name, or draft URL differs from the candidate.
- The final appcast was changed after signing or fails whole-feed verification.
- A required delta from the latest clean baseline is absent without an investigated, documented reason.
- A public Release, public asset/update feed, or successful publish job may already have exposed the tag.
- The worktree or branch violates the release entrypoint's contract.
- A prerelease source branch/SHA/channel differs from its candidate, exact packaged QA used stable or development-smoke evidence, or stable Latest/feed changed.

Never use `--skip-verify` / `-SkipVerify` unless the user explicitly accepts the risk. Never replace a public signed asset, rotate Sparkle keys during a normal release, or bypass a real product/packaging failure.

## Failure Recovery

Classify a failure before retrying. Run the focused failing test first. For a follow-up commit, run `release-impact.mjs` from the last certified SHA to `HEAD`; rerun only reported checks, but certify new artifacts whenever packaged inputs changed.

If desktop test behavior depends on stale build output, run `npm run clean` in `desktop` and rerun the source test before editing. Treat tests discovered under `desktop/dist` as a topology defect. Update a stale fixture minimally only when production behavior is intentional; do not patch around a product bug.

For prerelease failures after any tag/draft step, preserve evidence and use a higher prerelease version by default. Do not make a draft public merely to recover a run, and do not replace candidate artifacts. Editing a public stop-use notice, withdrawing a Release, deleting a draft/tag, or reusing a version requires separate user approval.

### Unpublished Tag Recovery

Treat a tag as immutable once a public Release, public asset/feed, package entry, or successful publish job may reference it. Use a higher version.

Reuse `v<version>` only with the user's authorization and evidence that no Release exists, no draft/staged asset remains, no appcast references it, and all publish jobs failed/cancelled/skipped before publication. Record exact local/remote tag targets, delete only `refs/tags/v<version>` locally and on `origin`, prune/fetch, and verify absence. Never reuse failed artifacts; certify the fixed final SHA and let Promote create the tag.

## Security And QA Safety

Check only Sparkle secret names (`SPARKLE_PUBLIC_ED_KEY`, `SPARKLE_ED_PRIVATE_KEY`); never print or reconstruct values. Do not put the private key in files, arguments, workflow text, or logs.

Isolate end-to-end update QA. Sparkle relaunch may discard `--user-data-dir`, so prefer a dedicated macOS test account or code-level QA data path present in both versions. Back up the target test database and stop all app processes afterward. Never overwrite real user data, disable Gatekeeper, or recommend `xattr` as an installation workaround.
