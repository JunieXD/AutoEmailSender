import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  createCandidateManifest,
  createPlatformEvidence,
  verifyCandidateManifest,
} from "./release-candidate.mjs";

const repository = "JunieXD/AutoEmailSender";
const releaseTag = "v9.9.9";
const releaseSha = "a".repeat(40);
const runId = 123456;

async function createFixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "release-candidate-test-"));
  const directories = {
    windows: path.join(root, "windows"),
    macos: path.join(root, "macos"),
    skill: path.join(root, "skill"),
  };
  await Promise.all(Object.values(directories).map((directory) => mkdir(directory, { recursive: true })));
  await Promise.all([
    writeFile(path.join(directories.windows, "AutoEmailSender-Setup-9.9.9.exe"), "installer"),
    writeFile(path.join(directories.windows, "AutoEmailSender-Setup-9.9.9.exe.blockmap"), "blockmap"),
    writeFile(path.join(directories.windows, "latest.yml"), "version: 9.9.9"),
    writeFile(path.join(directories.macos, "AutoEmailSender-9.9.9-arm64.dmg"), "dmg"),
    writeFile(path.join(directories.macos, "appcast.xml"), "<rss />"),
    writeFile(path.join(directories.macos, "9.9.8-to-9.9.9.delta"), "delta"),
    writeFile(path.join(directories.skill, "crawl-mentors-to-xlsx-v9.9.9.zip"), "zip"),
  ]);
  const releaseNotesPath = path.join(root, "v9.9.9.md");
  await writeFile(releaseNotesPath, "# v9.9.9\n");
  const platforms = {};
  for (const platform of Object.keys(directories)) {
    const evidence = await createPlatformEvidence({
      platform,
      releaseTag,
      releaseSha,
      runId,
      artifactDirectory: directories[platform],
    });
    const evidencePath = path.join(root, `${platform}.json`);
    await writeFile(evidencePath, JSON.stringify(evidence));
    platforms[platform] = { evidencePath, artifactDirectory: directories[platform] };
  }
  return { root, directories, releaseNotesPath, platforms };
}

test("creates and verifies a candidate bound to one run, SHA, note, and artifact set", async () => {
  const fixture = await createFixture();
  try {
    const manifest = await createCandidateManifest({
      repository,
      releaseTag,
      releaseSha,
      runId,
      releaseNotesPath: fixture.releaseNotesPath,
      platforms: fixture.platforms,
    });
    assert.equal(manifest.candidateRunId, runId);
    assert.equal(manifest.platforms.macos.artifacts.length, 3);
    await assert.doesNotReject(
      verifyCandidateManifest({
        manifest,
        repository,
        releaseTag,
        releaseSha,
        runId,
        releaseNotesPath: fixture.releaseNotesPath,
        artifactDirectories: fixture.directories,
      }),
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("rejects changed notes, candidate runs, and artifacts", async () => {
  const fixture = await createFixture();
  try {
    const manifest = await createCandidateManifest({
      repository,
      releaseTag,
      releaseSha,
      runId,
      releaseNotesPath: fixture.releaseNotesPath,
      platforms: fixture.platforms,
    });
    const verify = (overrides = {}) =>
      verifyCandidateManifest({
        manifest,
        repository,
        releaseTag,
        releaseSha,
        runId,
        releaseNotesPath: fixture.releaseNotesPath,
        artifactDirectories: fixture.directories,
        ...overrides,
      });
    await assert.rejects(verify({ runId: runId + 1 }), /candidateRunId 不匹配/);
    await writeFile(fixture.releaseNotesPath, "# changed\n");
    await assert.rejects(verify(), /release note hash 不匹配/);
    await writeFile(fixture.releaseNotesPath, "# v9.9.9\n");
    await writeFile(path.join(fixture.directories.windows, "latest.yml"), "tampered");
    await assert.rejects(verify(), /windows 候选资产摘要/);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("candidate JSON remains portable after serialization", async () => {
  const fixture = await createFixture();
  try {
    const manifest = await createCandidateManifest({
      repository,
      releaseTag,
      releaseSha,
      runId,
      releaseNotesPath: fixture.releaseNotesPath,
      platforms: fixture.platforms,
    });
    const manifestPath = path.join(fixture.root, "release-candidate.json");
    await writeFile(manifestPath, JSON.stringify(manifest));
    assert.deepEqual(JSON.parse(await readFile(manifestPath, "utf8")), manifest);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});
