#!/usr/bin/env node

import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import {
  normalizeCandidateRunId,
  normalizePrereleaseContract,
} from "./prerelease-contract.mjs";
import {
  expectedPrereleaseAssetName,
  RELEASE_IDENTITY_SCHEMA_VERSION,
} from "./prerelease-build-identity.mjs";
import { validateStableIsolationSnapshot } from "./prerelease-isolation.mjs";

const SCHEMA_VERSION = 1;
const CANDIDATE_KIND = "auto-email-sender-prerelease-candidate";
const PLATFORM_KIND = "auto-email-sender-prerelease-platform-evidence";
const PLATFORMS = ["windows", "macos"];

async function sha256File(filePath) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(filePath)) hash.update(chunk);
  return hash.digest("hex");
}

function assertIdentity(actual, expected, label) {
  if (actual !== expected) {
    throw new Error(`${label} 不匹配：${actual ?? "<missing>"}，预期 ${expected ?? "<null>"}。`);
  }
}

async function collectSingleInstaller(platform, artifactDirectory, version) {
  if (!PLATFORMS.includes(platform)) throw new Error(`不支持的 prerelease 平台：${platform}`);
  const expectedName = expectedPrereleaseAssetName(platform, version);
  const entries = await readdir(artifactDirectory, { withFileTypes: true });
  const visible = entries.filter((entry) => entry.name !== ".DS_Store");
  if (
    visible.length !== 1
    || visible[0].name !== expectedName
    || !visible[0].isFile()
    || visible[0].isSymbolicLink()
  ) {
    throw new Error(
      `${platform} prerelease 目录只能包含 ${expectedName}，不能包含 latest.yml、appcast.xml、blockmap 或 delta。`,
    );
  }
  const assetPath = path.join(artifactDirectory, expectedName);
  const fileStat = await lstat(assetPath);
  if (!fileStat.isFile() || fileStat.isSymbolicLink() || fileStat.size <= 0) {
    throw new Error(`${expectedName} 为空、是 symlink 或不是普通文件。`);
  }
  return {
    name: expectedName,
    size: fileStat.size,
    sha256: await sha256File(assetPath),
  };
}

function validateBuildIdentity(identity, contract, runId, platform) {
  const expectedAssetName = expectedPrereleaseAssetName(platform, contract.version);
  const expected = {
    schema_version: RELEASE_IDENTITY_SCHEMA_VERSION,
    release_kind: "prerelease",
    version: contract.version,
    channel: contract.channel,
    source_branch: contract.sourceBranch,
    release_sha: contract.releaseSha,
    candidate_run_id: String(runId),
    candidate_asset_name: expectedAssetName,
    candidate_asset_sha256: null,
    default_backend_mode: contract.defaultBackendMode,
    diagnostics_schema_version: contract.diagnosticsSchemaVersion,
  };
  if (JSON.stringify(identity) !== JSON.stringify(expected)) {
    throw new Error(`${platform} 构建身份与显式 prerelease 合同不一致。`);
  }
  return identity;
}

export async function createPrereleasePlatformEvidence({
  platform,
  version,
  channel,
  sourceBranch,
  releaseSha,
  runId,
  artifactDirectory,
  buildIdentityPath,
}) {
  const contract = normalizePrereleaseContract({ version, channel, sourceBranch, releaseSha });
  const candidateRunId = normalizeCandidateRunId(runId);
  const buildIdentity = validateBuildIdentity(
    JSON.parse(await readFile(buildIdentityPath, "utf8")),
    contract,
    candidateRunId,
    platform,
  );
  return {
    schemaVersion: SCHEMA_VERSION,
    kind: PLATFORM_KIND,
    platform,
    releaseTag: contract.releaseTag,
    version: contract.version,
    channel: contract.channel,
    sourceBranch: contract.sourceBranch,
    releaseSha: contract.releaseSha,
    candidateRunId,
    defaultBackendMode: contract.defaultBackendMode,
    diagnosticsSchemaVersion: contract.diagnosticsSchemaVersion,
    generatedAt: new Date().toISOString(),
    buildIdentity,
    artifact: await collectSingleInstaller(platform, artifactDirectory, contract.version),
  };
}

async function loadPlatformEvidence(filePath, artifactDirectory, expected, platform) {
  const evidence = JSON.parse(await readFile(filePath, "utf8"));
  assertIdentity(evidence.schemaVersion, SCHEMA_VERSION, `${platform} evidence schemaVersion`);
  assertIdentity(evidence.kind, PLATFORM_KIND, `${platform} evidence kind`);
  assertIdentity(evidence.platform, platform, `${platform} evidence platform`);
  for (const [key, value] of Object.entries({
    releaseTag: expected.contract.releaseTag,
    version: expected.contract.version,
    channel: expected.contract.channel,
    sourceBranch: expected.contract.sourceBranch,
    releaseSha: expected.contract.releaseSha,
    candidateRunId: expected.runId,
    defaultBackendMode: expected.contract.defaultBackendMode,
    diagnosticsSchemaVersion: expected.contract.diagnosticsSchemaVersion,
  })) {
    assertIdentity(evidence[key], value, `${platform} evidence ${key}`);
  }
  validateBuildIdentity(evidence.buildIdentity, expected.contract, expected.runId, platform);
  const artifact = await collectSingleInstaller(platform, artifactDirectory, expected.contract.version);
  if (JSON.stringify(evidence.artifact) !== JSON.stringify(artifact)) {
    throw new Error(`${platform} prerelease 资产摘要与平台证据不一致。`);
  }
  return evidence;
}

export async function createPrereleaseCandidateManifest({
  repository,
  version,
  channel,
  sourceBranch,
  releaseSha,
  runId,
  releaseNotesPath,
  stableIsolationPath,
  platforms,
}) {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/u.test(repository)) {
    throw new Error(`无效的 GitHub 仓库：${repository}`);
  }
  const contract = normalizePrereleaseContract({ version, channel, sourceBranch, releaseSha });
  const candidateRunId = normalizeCandidateRunId(runId);
  const expected = { contract, runId: candidateRunId };
  const platformEvidence = {};
  for (const platform of PLATFORMS) {
    const input = platforms[platform];
    if (!input) throw new Error(`缺少 ${platform} prerelease 候选证据。`);
    platformEvidence[platform] = await loadPlatformEvidence(
      input.evidencePath,
      input.artifactDirectory,
      expected,
      platform,
    );
  }
  const stableIsolation = validateStableIsolationSnapshot(
    JSON.parse(await readFile(stableIsolationPath, "utf8")),
  );
  if (stableIsolation.repository !== repository) {
    throw new Error("稳定更新隔离基线与候选仓库不一致。");
  }
  const notes = await readFile(releaseNotesPath);
  return {
    schemaVersion: SCHEMA_VERSION,
    kind: CANDIDATE_KIND,
    repository,
    releaseTag: contract.releaseTag,
    version: contract.version,
    channel: contract.channel,
    sourceBranch: contract.sourceBranch,
    releaseSha: contract.releaseSha,
    candidateRunId,
    defaultBackendMode: contract.defaultBackendMode,
    diagnosticsSchemaVersion: contract.diagnosticsSchemaVersion,
    generatedAt: new Date().toISOString(),
    releaseNotes: {
      name: path.basename(releaseNotesPath),
      size: notes.length,
      sha256: createHash("sha256").update(notes).digest("hex"),
    },
    stableIsolation,
    platforms: platformEvidence,
  };
}

export async function verifyPrereleaseCandidateManifest({
  manifest,
  repository,
  version,
  channel,
  sourceBranch,
  releaseSha,
  runId,
  releaseNotesPath,
  artifactDirectories,
}) {
  const contract = normalizePrereleaseContract({ version, channel, sourceBranch, releaseSha });
  const candidateRunId = normalizeCandidateRunId(runId);
  assertIdentity(manifest.schemaVersion, SCHEMA_VERSION, "prerelease manifest schemaVersion");
  assertIdentity(manifest.kind, CANDIDATE_KIND, "prerelease manifest kind");
  for (const [key, value] of Object.entries({
    repository,
    releaseTag: contract.releaseTag,
    version: contract.version,
    channel: contract.channel,
    sourceBranch: contract.sourceBranch,
    releaseSha: contract.releaseSha,
    candidateRunId,
    defaultBackendMode: contract.defaultBackendMode,
    diagnosticsSchemaVersion: contract.diagnosticsSchemaVersion,
  })) {
    assertIdentity(manifest[key], value, `prerelease manifest ${key}`);
  }
  validateStableIsolationSnapshot(manifest.stableIsolation);
  assertIdentity(manifest.stableIsolation.repository, repository, "stable isolation repository");
  const notes = await readFile(releaseNotesPath);
  assertIdentity(
    manifest.releaseNotes?.sha256,
    createHash("sha256").update(notes).digest("hex"),
    "prerelease release note hash",
  );
  for (const platform of PLATFORMS) {
    const evidence = manifest.platforms?.[platform];
    if (!evidence) throw new Error(`prerelease manifest 缺少 ${platform} evidence。`);
    assertIdentity(evidence.schemaVersion, SCHEMA_VERSION, `${platform} evidence schemaVersion`);
    assertIdentity(evidence.kind, PLATFORM_KIND, `${platform} evidence kind`);
    assertIdentity(evidence.platform, platform, `${platform} evidence platform`);
    for (const [key, value] of Object.entries({
      releaseTag: contract.releaseTag,
      version: contract.version,
      channel: contract.channel,
      sourceBranch: contract.sourceBranch,
      releaseSha: contract.releaseSha,
      candidateRunId,
      defaultBackendMode: contract.defaultBackendMode,
      diagnosticsSchemaVersion: contract.diagnosticsSchemaVersion,
    })) {
      assertIdentity(evidence[key], value, `${platform} evidence ${key}`);
    }
    validateBuildIdentity(evidence.buildIdentity, contract, candidateRunId, platform);
    const artifact = await collectSingleInstaller(
      platform,
      artifactDirectories[platform],
      contract.version,
    );
    if (JSON.stringify(evidence.artifact) !== JSON.stringify(artifact)) {
      throw new Error(`${platform} prerelease 候选资产与 manifest 不一致。`);
    }
  }
  return manifest;
}

export async function verifyPrereleaseCandidateAsset({
  manifest,
  platform,
  version,
  channel,
  sourceBranch,
  releaseSha,
  runId,
  assetPath,
}) {
  const contract = normalizePrereleaseContract({ version, channel, sourceBranch, releaseSha });
  const candidateRunId = normalizeCandidateRunId(runId);
  assertIdentity(manifest.schemaVersion, SCHEMA_VERSION, "prerelease manifest schemaVersion");
  assertIdentity(manifest.kind, CANDIDATE_KIND, "prerelease manifest kind");
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/u.test(manifest.repository ?? "")) {
    throw new Error("prerelease manifest repository 无效。");
  }
  for (const [key, value] of Object.entries({
    releaseTag: contract.releaseTag,
    version: contract.version,
    channel: contract.channel,
    sourceBranch: contract.sourceBranch,
    releaseSha: contract.releaseSha,
    candidateRunId,
    defaultBackendMode: contract.defaultBackendMode,
    diagnosticsSchemaVersion: contract.diagnosticsSchemaVersion,
  })) {
    assertIdentity(manifest[key], value, `prerelease manifest ${key}`);
  }
  validateStableIsolationSnapshot(manifest.stableIsolation);
  assertIdentity(
    manifest.stableIsolation.repository,
    manifest.repository,
    "stable isolation repository",
  );
  const expectedName = expectedPrereleaseAssetName(platform, contract.version);
  const resolvedPath = path.resolve(assetPath);
  assertIdentity(path.basename(resolvedPath), expectedName, `${platform} prerelease asset name`);
  const evidence = manifest.platforms?.[platform];
  if (!evidence) throw new Error(`prerelease manifest 缺少 ${platform} evidence。`);
  assertIdentity(evidence.schemaVersion, SCHEMA_VERSION, `${platform} evidence schemaVersion`);
  assertIdentity(evidence.kind, PLATFORM_KIND, `${platform} evidence kind`);
  assertIdentity(evidence.platform, platform, `${platform} evidence platform`);
  for (const [key, value] of Object.entries({
    releaseTag: contract.releaseTag,
    version: contract.version,
    channel: contract.channel,
    sourceBranch: contract.sourceBranch,
    releaseSha: contract.releaseSha,
    candidateRunId,
    defaultBackendMode: contract.defaultBackendMode,
    diagnosticsSchemaVersion: contract.diagnosticsSchemaVersion,
  })) {
    assertIdentity(evidence[key], value, `${platform} evidence ${key}`);
  }
  validateBuildIdentity(evidence.buildIdentity, contract, candidateRunId, platform);
  const record = evidence.artifact;
  if (!record) throw new Error(`prerelease manifest 缺少 ${platform} 资产。`);
  const fileStat = await lstat(resolvedPath);
  if (!fileStat.isFile() || fileStat.isSymbolicLink() || fileStat.size <= 0) {
    throw new Error(`${expectedName} 不是有效的候选普通文件。`);
  }
  assertIdentity(record.name, expectedName, `${platform} prerelease record name`);
  assertIdentity(record.size, fileStat.size, `${platform} prerelease asset size`);
  assertIdentity(record.sha256, await sha256File(resolvedPath), `${platform} prerelease asset SHA-256`);
  return {
    platform,
    releaseTag: contract.releaseTag,
    releaseSha: contract.releaseSha,
    candidateRunId,
    asset: record,
  };
}

async function writeJson(outputPath, value) {
  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function parseArguments(argv) {
  const mode = argv[0] ?? "";
  const options = {};
  for (let index = 1; index < argv.length; index += 1) {
    const argument = argv[index];
    const value = argv[index + 1];
    if (!argument.startsWith("--") || value === undefined) {
      throw new Error(`无法解析参数：${argument}`);
    }
    options[argument.slice(2).replaceAll("-", "_")] = value;
    index += 1;
  }
  return { mode, options };
}

function required(options, name) {
  const value = options[name];
  if (!value) throw new Error(`缺少 --${name.replaceAll("_", "-")}。`);
  return value;
}

function commonOptions(options) {
  return {
    version: required(options, "version"),
    channel: required(options, "channel"),
    sourceBranch: required(options, "source_branch"),
    releaseSha: required(options, "release_sha"),
    runId: required(options, "run_id"),
  };
}

function platformInputs(options) {
  return Object.fromEntries(PLATFORMS.map((platform) => [
    platform,
    {
      evidencePath: path.resolve(required(options, `${platform}_evidence`)),
      artifactDirectory: path.resolve(required(options, `${platform}_dir`)),
    },
  ]));
}

function artifactDirectoryInputs(options) {
  return Object.fromEntries(PLATFORMS.map((platform) => [
    platform,
    path.resolve(required(options, `${platform}_dir`)),
  ]));
}

async function main() {
  const { mode, options } = parseArguments(process.argv.slice(2));
  if (mode === "platform") {
    const evidence = await createPrereleasePlatformEvidence({
      platform: required(options, "platform"),
      ...commonOptions(options),
      artifactDirectory: path.resolve(required(options, "artifact_dir")),
      buildIdentityPath: path.resolve(required(options, "build_identity")),
    });
    await writeJson(path.resolve(required(options, "output")), evidence);
    console.log(`[ok] ${evidence.platform} prerelease 候选已绑定 run ${evidence.candidateRunId}。`);
    return;
  }
  if (mode === "candidate") {
    const manifest = await createPrereleaseCandidateManifest({
      repository: required(options, "repository"),
      ...commonOptions(options),
      releaseNotesPath: path.resolve(required(options, "release_notes")),
      stableIsolationPath: path.resolve(required(options, "stable_isolation")),
      platforms: platformInputs(options),
    });
    await writeJson(path.resolve(required(options, "output")), manifest);
    console.log(`[ok] ${manifest.releaseTag} prerelease candidate manifest 已生成。`);
    return;
  }
  if (mode === "verify") {
    const manifest = JSON.parse(await readFile(path.resolve(required(options, "manifest")), "utf8"));
    await verifyPrereleaseCandidateManifest({
      manifest,
      repository: required(options, "repository"),
      ...commonOptions(options),
      releaseNotesPath: path.resolve(required(options, "release_notes")),
      artifactDirectories: artifactDirectoryInputs(options),
    });
    console.log(`[ok] ${manifest.releaseTag} prerelease manifest、公告和资产一致。`);
    return;
  }
  if (mode === "asset") {
    const manifest = JSON.parse(await readFile(path.resolve(required(options, "manifest")), "utf8"));
    const result = await verifyPrereleaseCandidateAsset({
      manifest,
      platform: required(options, "platform"),
      ...commonOptions(options),
      assetPath: required(options, "asset"),
    });
    console.log(`[ok] ${result.asset.name} 已绑定候选 run ${result.candidateRunId}。`);
    return;
  }
  throw new Error("用法: prerelease-candidate.mjs <platform|candidate|verify|asset> [options]");
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
