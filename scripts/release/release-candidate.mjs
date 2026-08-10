#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { normalizeReleaseTag } from "./prepare-sparkle-release.mjs";

const SCHEMA_VERSION = 1;
const CANDIDATE_KIND = "auto-email-sender-release-candidate";
const PLATFORM_KIND = "auto-email-sender-platform-evidence";
const SUPPORTED_PLATFORMS = new Set(["windows", "macos", "skill"]);

function parseArguments(argv) {
  const mode = argv[0] ?? "";
  const options = {};
  for (let index = 1; index < argv.length; index += 1) {
    const argument = argv[index];
    const value = argv[index + 1];
    if (!argument.startsWith("--") || value === undefined) {
      throw new Error(`无法解析参数：${argument}`);
    }
    index += 1;
    options[argument.slice(2).replaceAll("-", "_")] = value;
  }
  return { mode, options };
}

function requireOption(options, name) {
  const value = options[name];
  if (!value) throw new Error(`缺少 --${name.replaceAll("_", "-")}。`);
  return value;
}

function normalizeSha(value) {
  const sha = value.trim().toLowerCase();
  if (!/^[0-9a-f]{40}$/.test(sha)) throw new Error(`无效的候选提交 SHA：${value}`);
  return sha;
}

function normalizeRunId(value) {
  const runId = Number.parseInt(String(value), 10);
  if (!Number.isSafeInteger(runId) || runId <= 0 || String(runId) !== String(value).trim()) {
    throw new Error(`无效的候选 workflow run ID：${value}`);
  }
  return runId;
}

function commandVersion(command, args = ["--version"]) {
  try {
    return execFileSync(command, args, { encoding: "utf8" }).trim();
  } catch {
    return null;
  }
}

async function sha256File(filePath) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(filePath)) hash.update(chunk);
  return hash.digest("hex");
}

function expectedArtifactNames(platform, version, entries) {
  if (platform === "windows") {
    return [
      `AutoEmailSender-Setup-${version}.exe`,
      `AutoEmailSender-Setup-${version}.exe.blockmap`,
      "latest.yml",
    ];
  }
  if (platform === "macos") {
    const deltas = entries.filter((name) => name.endsWith(".delta")).sort();
    if (deltas.length > 3) throw new Error("macOS 候选包含超过 3 个差分包。 ");
    return [`AutoEmailSender-${version}-arm64.dmg`, "appcast.xml", ...deltas];
  }
  return [`crawl-mentors-to-xlsx-v${version}.zip`];
}

async function collectArtifacts(platform, artifactDirectory, version) {
  if (!SUPPORTED_PLATFORMS.has(platform)) throw new Error(`不支持的候选平台：${platform}`);
  const entries = (await readdir(artifactDirectory)).sort();
  const expectedNames = expectedArtifactNames(platform, version, entries);
  const expectedSet = new Set(expectedNames);
  const relevantEntries = entries.filter((name) => {
    if (platform === "windows") return name.endsWith(".exe") || name.endsWith(".blockmap") || name === "latest.yml";
    if (platform === "macos") return name.endsWith(".dmg") || name.endsWith(".delta") || name === "appcast.xml";
    return name.endsWith(".zip");
  });
  if (
    relevantEntries.length !== expectedNames.length ||
    relevantEntries.some((name) => !expectedSet.has(name))
  ) {
    throw new Error(
      `${platform} 候选资产不完整或包含意外文件。预期：${expectedNames.join(", ")}；实际：${relevantEntries.join(", ")}`,
    );
  }

  return Promise.all(
    expectedNames.sort().map(async (name) => {
      const filePath = path.join(artifactDirectory, name);
      const fileStat = await stat(filePath);
      if (!fileStat.isFile() || fileStat.size <= 0) throw new Error(`候选资产为空或不是文件：${name}`);
      return { name, size: fileStat.size, sha256: await sha256File(filePath) };
    }),
  );
}

function assertIdentity(value, expected, label) {
  if (value !== expected) throw new Error(`${label} 不匹配：${value}，预期 ${expected}。`);
}

async function assertArtifactRecords(records, platform, artifactDirectory, version) {
  const actual = await collectArtifacts(platform, artifactDirectory, version);
  if (JSON.stringify(records) !== JSON.stringify(actual)) {
    throw new Error(`${platform} 候选资产摘要与认证记录不一致。`);
  }
  return actual;
}

async function writeJson(outputPath, value) {
  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

export async function createPlatformEvidence({
  platform,
  releaseTag,
  releaseSha,
  runId,
  artifactDirectory,
}) {
  const { tag, version } = normalizeReleaseTag(releaseTag);
  const normalizedSha = normalizeSha(releaseSha);
  const normalizedRunId = normalizeRunId(runId);
  return {
    schemaVersion: SCHEMA_VERSION,
    kind: PLATFORM_KIND,
    platform,
    releaseTag: tag,
    version,
    releaseSha: normalizedSha,
    candidateRunId: normalizedRunId,
    generatedAt: new Date().toISOString(),
    toolchain: {
      runnerOs: process.env.RUNNER_OS ?? process.platform,
      runnerArch: process.env.RUNNER_ARCH ?? process.arch,
      runnerImage: process.env.ImageOS ?? null,
      node: process.version,
      python: commandVersion("python", ["--version"]),
      uv: commandVersion("uv", ["--version"]),
    },
    artifacts: await collectArtifacts(platform, artifactDirectory, version),
  };
}

async function loadPlatformEvidence(filePath, expected) {
  const evidence = JSON.parse(await readFile(filePath, "utf8"));
  assertIdentity(evidence.schemaVersion, SCHEMA_VERSION, "平台证据 schemaVersion");
  assertIdentity(evidence.kind, PLATFORM_KIND, "平台证据 kind");
  assertIdentity(evidence.platform, expected.platform, "平台证据 platform");
  assertIdentity(evidence.releaseTag, expected.releaseTag, "平台证据 releaseTag");
  assertIdentity(evidence.releaseSha, expected.releaseSha, "平台证据 releaseSha");
  assertIdentity(evidence.candidateRunId, expected.runId, "平台证据 candidateRunId");
  await assertArtifactRecords(
    evidence.artifacts,
    expected.platform,
    expected.artifactDirectory,
    expected.version,
  );
  return evidence;
}

export async function createCandidateManifest({
  repository,
  releaseTag,
  releaseSha,
  runId,
  releaseNotesPath,
  platforms,
}) {
  if (!/^[^/\s]+\/[^/\s]+$/.test(repository)) throw new Error(`无效的 GitHub 仓库：${repository}`);
  const { tag, version } = normalizeReleaseTag(releaseTag);
  const normalizedSha = normalizeSha(releaseSha);
  const normalizedRunId = normalizeRunId(runId);
  const expected = { releaseTag: tag, releaseSha: normalizedSha, runId: normalizedRunId, version };
  const platformEvidence = {};
  for (const platform of ["windows", "macos", "skill"]) {
    const input = platforms[platform];
    if (!input) throw new Error(`缺少 ${platform} 候选证据。`);
    platformEvidence[platform] = await loadPlatformEvidence(input.evidencePath, {
      ...expected,
      platform,
      artifactDirectory: input.artifactDirectory,
    });
  }
  const releaseNotes = await readFile(releaseNotesPath);
  return {
    schemaVersion: SCHEMA_VERSION,
    kind: CANDIDATE_KIND,
    repository,
    releaseTag: tag,
    version,
    releaseSha: normalizedSha,
    candidateRunId: normalizedRunId,
    generatedAt: new Date().toISOString(),
    releaseNotes: {
      name: path.basename(releaseNotesPath),
      size: releaseNotes.length,
      sha256: createHash("sha256").update(releaseNotes).digest("hex"),
    },
    platforms: platformEvidence,
  };
}

export async function verifyCandidateManifest({
  manifest,
  repository,
  releaseTag,
  releaseSha,
  runId,
  releaseNotesPath,
  artifactDirectories,
}) {
  const { tag, version } = normalizeReleaseTag(releaseTag);
  const normalizedSha = normalizeSha(releaseSha);
  const normalizedRunId = normalizeRunId(runId);
  assertIdentity(manifest.schemaVersion, SCHEMA_VERSION, "候选报告 schemaVersion");
  assertIdentity(manifest.kind, CANDIDATE_KIND, "候选报告 kind");
  assertIdentity(manifest.repository, repository, "候选报告 repository");
  assertIdentity(manifest.releaseTag, tag, "候选报告 releaseTag");
  assertIdentity(manifest.releaseSha, normalizedSha, "候选报告 releaseSha");
  assertIdentity(manifest.candidateRunId, normalizedRunId, "候选报告 candidateRunId");
  const releaseNotes = await readFile(releaseNotesPath);
  const noteHash = createHash("sha256").update(releaseNotes).digest("hex");
  assertIdentity(manifest.releaseNotes?.sha256, noteHash, "候选报告 release note hash");
  for (const platform of ["windows", "macos", "skill"]) {
    const evidence = manifest.platforms?.[platform];
    if (!evidence) throw new Error(`候选报告缺少 ${platform} 证据。`);
    assertIdentity(evidence.platform, platform, `${platform} 证据 platform`);
    assertIdentity(evidence.releaseTag, tag, `${platform} 证据 releaseTag`);
    assertIdentity(evidence.releaseSha, normalizedSha, `${platform} 证据 releaseSha`);
    assertIdentity(evidence.candidateRunId, normalizedRunId, `${platform} 证据 candidateRunId`);
    await assertArtifactRecords(evidence.artifacts, platform, artifactDirectories[platform], version);
  }
  return manifest;
}

export async function verifyCandidateAsset({
  manifest,
  platform,
  releaseSha,
  runId,
  version,
  assetPath,
}) {
  if (!new Set(["windows", "macos"]).has(platform)) {
    throw new Error(`不支持 packaged QA 候选平台：${platform}`);
  }
  const normalizedSha = normalizeSha(releaseSha);
  const normalizedRunId = normalizeRunId(runId);
  const normalizedVersion = String(version).trim();
  const expectedTag = `v${normalizedVersion}`;
  const normalizedTag = normalizeReleaseTag(expectedTag);
  assertIdentity(manifest.schemaVersion, SCHEMA_VERSION, "候选报告 schemaVersion");
  assertIdentity(manifest.kind, CANDIDATE_KIND, "候选报告 kind");
  assertIdentity(manifest.releaseTag, normalizedTag.tag, "候选报告 releaseTag");
  assertIdentity(manifest.version, normalizedTag.version, "候选报告 version");
  assertIdentity(manifest.releaseSha, normalizedSha, "候选报告 releaseSha");
  assertIdentity(manifest.candidateRunId, normalizedRunId, "候选报告 candidateRunId");

  const evidence = manifest.platforms?.[platform];
  if (!evidence) throw new Error(`候选报告缺少 ${platform} 证据。`);
  assertIdentity(evidence.schemaVersion, SCHEMA_VERSION, `${platform} 证据 schemaVersion`);
  assertIdentity(evidence.kind, PLATFORM_KIND, `${platform} 证据 kind`);
  assertIdentity(evidence.platform, platform, `${platform} 证据 platform`);
  assertIdentity(evidence.releaseTag, normalizedTag.tag, `${platform} 证据 releaseTag`);
  assertIdentity(evidence.version, normalizedTag.version, `${platform} 证据 version`);
  assertIdentity(evidence.releaseSha, normalizedSha, `${platform} 证据 releaseSha`);
  assertIdentity(evidence.candidateRunId, normalizedRunId, `${platform} 证据 candidateRunId`);

  const expectedName = platform === "windows"
    ? `AutoEmailSender-Setup-${normalizedTag.version}.exe`
    : `AutoEmailSender-${normalizedTag.version}-arm64.dmg`;
  const resolvedAssetPath = path.resolve(assetPath);
  assertIdentity(path.basename(resolvedAssetPath), expectedName, `${platform} 候选资产名`);
  if (!Array.isArray(evidence.artifacts)) {
    throw new Error(`${platform} 候选资产记录不是数组。`);
  }
  const records = evidence.artifacts.filter((record) => record?.name === expectedName);
  if (records.length !== 1) {
    throw new Error(`${platform} 候选报告必须恰好包含一个 ${expectedName} 记录。`);
  }
  const record = records[0];
  if (!Number.isSafeInteger(record.size) || record.size <= 0) {
    throw new Error(`${platform} 候选资产记录的 size 无效。`);
  }
  if (!/^[0-9a-f]{64}$/.test(record.sha256 ?? "")) {
    throw new Error(`${platform} 候选资产记录的 SHA-256 无效。`);
  }
  const fileStat = await stat(resolvedAssetPath);
  if (!fileStat.isFile() || fileStat.size <= 0) {
    throw new Error(`${platform} 候选资产为空或不是文件：${resolvedAssetPath}`);
  }
  assertIdentity(fileStat.size, record.size, `${platform} 候选资产 size`);
  const digest = await sha256File(resolvedAssetPath);
  assertIdentity(digest, record.sha256, `${platform} 候选资产 SHA-256`);
  return {
    platform,
    releaseTag: normalizedTag.tag,
    version: normalizedTag.version,
    releaseSha: normalizedSha,
    candidateRunId: normalizedRunId,
    asset: { name: expectedName, size: fileStat.size, sha256: digest },
  };
}

function platformInputs(options) {
  return Object.fromEntries(
    ["windows", "macos", "skill"].map((platform) => [
      platform,
      {
        evidencePath: path.resolve(requireOption(options, `${platform}_evidence`)),
        artifactDirectory: path.resolve(requireOption(options, `${platform}_dir`)),
      },
    ]),
  );
}

function artifactDirectoryInputs(options) {
  return Object.fromEntries(
    ["windows", "macos", "skill"].map((platform) => [
      platform,
      path.resolve(requireOption(options, `${platform}_dir`)),
    ]),
  );
}

async function main() {
  const { mode, options } = parseArguments(process.argv.slice(2));
  if (mode === "platform") {
    const evidence = await createPlatformEvidence({
      platform: requireOption(options, "platform"),
      releaseTag: requireOption(options, "release_tag"),
      releaseSha: requireOption(options, "release_sha"),
      runId: requireOption(options, "run_id"),
      artifactDirectory: path.resolve(requireOption(options, "artifact_dir")),
    });
    await writeJson(path.resolve(requireOption(options, "output")), evidence);
    console.log(`[ok] ${evidence.platform} 候选记录了 ${evidence.artifacts.length} 个资产。`);
    return;
  }
  if (mode === "candidate") {
    const manifest = await createCandidateManifest({
      repository: requireOption(options, "repository"),
      releaseTag: requireOption(options, "release_tag"),
      releaseSha: requireOption(options, "release_sha"),
      runId: requireOption(options, "run_id"),
      releaseNotesPath: path.resolve(requireOption(options, "release_notes")),
      platforms: platformInputs(options),
    });
    await writeJson(path.resolve(requireOption(options, "output")), manifest);
    console.log(`[ok] ${manifest.releaseTag} 候选认证报告已生成。`);
    return;
  }
  if (mode === "verify") {
    const manifest = JSON.parse(await readFile(path.resolve(requireOption(options, "manifest")), "utf8"));
    await verifyCandidateManifest({
      manifest,
      repository: requireOption(options, "repository"),
      releaseTag: requireOption(options, "release_tag"),
      releaseSha: requireOption(options, "release_sha"),
      runId: requireOption(options, "run_id"),
      releaseNotesPath: path.resolve(requireOption(options, "release_notes")),
      artifactDirectories: artifactDirectoryInputs(options),
    });
    console.log(`[ok] ${manifest.releaseTag} 候选报告、公告和全部资产摘要一致。`);
    return;
  }
  if (mode === "asset") {
    const manifest = JSON.parse(await readFile(path.resolve(requireOption(options, "manifest")), "utf8"));
    const evidence = await verifyCandidateAsset({
      manifest,
      platform: requireOption(options, "platform"),
      releaseSha: requireOption(options, "release_sha"),
      runId: requireOption(options, "run_id"),
      version: requireOption(options, "version"),
      assetPath: requireOption(options, "asset"),
    });
    console.log(
      `[ok] ${evidence.platform} ${evidence.asset.name} 已绑定候选 run ${evidence.candidateRunId}。`,
    );
    return;
  }
  throw new Error("用法: release-candidate.mjs <platform|candidate|verify|asset> [options]");
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
