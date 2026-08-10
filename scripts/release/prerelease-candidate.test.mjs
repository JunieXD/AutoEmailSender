import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { createPrereleaseBuildIdentity } from "./prerelease-build-identity.mjs";
import { verifyPackagedQaCandidateAsset } from "./release-candidate.mjs";
import {
  createPrereleaseCandidateManifest,
  createPrereleasePlatformEvidence,
  verifyPrereleaseCandidateAsset,
  verifyPrereleaseCandidateManifest,
} from "./prerelease-candidate.mjs";

const repository = "JunieXD/AutoEmailSender";
const version = "2.6.0-beta.1";
const channel = "beta";
const sourceBranch = "release/api-worker-dogfood";
const releaseSha = "a".repeat(40);
const runId = 123456;

async function createFixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "prerelease-candidate-test-"));
  const directories = {
    windows: path.join(root, "windows"),
    macos: path.join(root, "macos"),
  };
  await Promise.all(Object.values(directories).map((directory) => mkdir(directory, { recursive: true })));
  await Promise.all([
    writeFile(path.join(directories.windows, `AutoEmailSender-Setup-${version}.exe`), "windows-installer"),
    writeFile(path.join(directories.macos, `AutoEmailSender-${version}-arm64.dmg`), "macos-dmg"),
  ]);
  const platforms = {};
  for (const platform of Object.keys(directories)) {
    const identityPath = path.join(root, `${platform}-identity.json`);
    await writeFile(identityPath, JSON.stringify(createPrereleaseBuildIdentity({
      version,
      channel,
      sourceBranch,
      releaseSha,
      candidateRunId: String(runId),
      platform,
    })));
    const evidence = await createPrereleasePlatformEvidence({
      platform,
      version,
      channel,
      sourceBranch,
      releaseSha,
      runId,
      artifactDirectory: directories[platform],
      buildIdentityPath: identityPath,
    });
    const evidencePath = path.join(root, `${platform}-evidence.json`);
    await writeFile(evidencePath, JSON.stringify(evidence));
    platforms[platform] = {
      evidencePath,
      artifactDirectory: directories[platform],
    };
  }
  const releaseNotesPath = path.join(root, `v${version}.md`);
  await writeFile(releaseNotesPath, `# v${version}\n`);
  const stableIsolationPath = path.join(root, "stable-isolation.json");
  await writeFile(stableIsolationPath, JSON.stringify({
    schemaVersion: 1,
    kind: "auto-email-sender-stable-isolation-snapshot",
    repository,
    capturedAt: "2026-08-10T00:00:00Z",
    latestRelease: {
      releaseId: 254,
      tag: "v2.5.4",
      publishedAt: "2026-08-01T00:00:00Z",
    },
    assets: {
      "appcast.xml": { assetId: 1, size: 10, sha256: "b".repeat(64) },
      "latest.yml": { assetId: 2, size: 10, sha256: "c".repeat(64) },
    },
  }));
  return { root, directories, platforms, releaseNotesPath, stableIsolationPath };
}

function manifestInput(fixture) {
  return {
    repository,
    version,
    channel,
    sourceBranch,
    releaseSha,
    runId,
    releaseNotesPath: fixture.releaseNotesPath,
    stableIsolationPath: fixture.stableIsolationPath,
    platforms: fixture.platforms,
  };
}

test("binds a reusable prerelease candidate to branch, SHA, run, mode, schema, isolation, and two installers", async () => {
  const fixture = await createFixture();
  try {
    const manifest = await createPrereleaseCandidateManifest(manifestInput(fixture));
    assert.equal(manifest.sourceBranch, sourceBranch);
    assert.equal(manifest.releaseSha, releaseSha);
    assert.equal(manifest.candidateRunId, runId);
    assert.equal(manifest.channel, channel);
    assert.equal(manifest.defaultBackendMode, "split");
    assert.equal(manifest.diagnosticsSchemaVersion, 1);
    assert.deepEqual(Object.keys(manifest.platforms).sort(), ["macos", "windows"]);
    await assert.doesNotReject(verifyPrereleaseCandidateManifest({
      manifest,
      repository,
      version,
      channel,
      sourceBranch,
      releaseSha,
      runId,
      releaseNotesPath: fixture.releaseNotesPath,
      artifactDirectories: fixture.directories,
    }));
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("rejects stable update metadata, changed notes, wrong candidate identity, and changed bytes", async () => {
  const fixture = await createFixture();
  try {
    await writeFile(path.join(fixture.directories.windows, "latest.yml"), "must-not-publish");
    await assert.rejects(
      createPrereleaseCandidateManifest(manifestInput(fixture)),
      /只能包含 .*不能包含 latest\.yml/,
    );
    await rm(path.join(fixture.directories.windows, "latest.yml"));
    const manifest = await createPrereleaseCandidateManifest(manifestInput(fixture));
    await writeFile(fixture.releaseNotesPath, "# changed\n");
    await assert.rejects(
      verifyPrereleaseCandidateManifest({
        manifest,
        repository,
        version,
        channel,
        sourceBranch,
        releaseSha,
        runId,
        releaseNotesPath: fixture.releaseNotesPath,
        artifactDirectories: fixture.directories,
      }),
      /release note hash 不匹配/,
    );
    await writeFile(fixture.releaseNotesPath, `# v${version}\n`);
    manifest.platforms.windows.buildIdentity.source_branch = "another/branch";
    await assert.rejects(
      verifyPrereleaseCandidateManifest({
        manifest,
        repository,
        version,
        channel,
        sourceBranch,
        releaseSha,
        runId,
        releaseNotesPath: fixture.releaseNotesPath,
        artifactDirectories: fixture.directories,
      }),
      /构建身份/,
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("binds packaged QA to the exact candidate asset and rejects tampering", async () => {
  const fixture = await createFixture();
  try {
    const manifest = await createPrereleaseCandidateManifest(manifestInput(fixture));
    const windowsAsset = path.join(
      fixture.directories.windows,
      `AutoEmailSender-Setup-${version}.exe`,
    );
    const result = await verifyPrereleaseCandidateAsset({
      manifest,
      platform: "windows",
      version,
      channel,
      sourceBranch,
      releaseSha,
      runId,
      assetPath: windowsAsset,
    });
    assert.equal(result.asset.name, path.basename(windowsAsset));
    const genericQaResult = await verifyPackagedQaCandidateAsset({
      manifest,
      platform: "windows",
      version,
      releaseSha,
      runId,
      assetPath: windowsAsset,
    });
    assert.equal(genericQaResult.asset.name, path.basename(windowsAsset));
    manifest.platforms.windows.channel = "rc";
    await assert.rejects(
      verifyPackagedQaCandidateAsset({
        manifest,
        platform: "windows",
        version,
        releaseSha,
        runId,
        assetPath: windowsAsset,
      }),
      /windows evidence channel 不匹配/,
    );
    manifest.platforms.windows.channel = channel;
    await writeFile(windowsAsset, "tampered installer bytes");
    await assert.rejects(
      verifyPrereleaseCandidateAsset({
        manifest,
        platform: "windows",
        version,
        channel,
        sourceBranch,
        releaseSha,
        runId,
        assetPath: windowsAsset,
      }),
      /size 不匹配|SHA-256 不匹配/,
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});
