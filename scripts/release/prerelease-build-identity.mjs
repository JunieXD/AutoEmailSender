#!/usr/bin/env node

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import {
  normalizeCandidateRunId,
  normalizePrereleaseContract,
} from "./prerelease-contract.mjs";

export const RELEASE_IDENTITY_SCHEMA_VERSION = 1;

export function expectedPrereleaseAssetName(platform, version) {
  if (platform === "windows") return `AutoEmailSender-Setup-${version}.exe`;
  if (platform === "macos") return `AutoEmailSender-${version}-arm64.dmg`;
  throw new Error(`不支持的 prerelease 平台：${platform}`);
}

export function createPrereleaseBuildIdentity({
  version,
  channel,
  sourceBranch,
  releaseSha,
  candidateRunId,
  platform,
}) {
  const contract = normalizePrereleaseContract({ version, channel, sourceBranch, releaseSha });
  const runId = normalizeCandidateRunId(candidateRunId);
  const assetName = expectedPrereleaseAssetName(platform, contract.version);
  return {
    schema_version: RELEASE_IDENTITY_SCHEMA_VERSION,
    release_kind: "prerelease",
    version: contract.version,
    channel: contract.channel,
    source_branch: contract.sourceBranch,
    release_sha: contract.releaseSha,
    candidate_run_id: String(runId),
    candidate_asset_name: assetName,
    candidate_asset_sha256: null,
    default_backend_mode: contract.defaultBackendMode,
    diagnostics_schema_version: contract.diagnosticsSchemaVersion,
  };
}

function parseArguments(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    const value = argv[index + 1];
    if (!argument.startsWith("--") || value === undefined) {
      throw new Error(`无法解析参数：${argument}`);
    }
    options[argument.slice(2).replaceAll("-", "_")] = value;
    index += 1;
  }
  for (const name of [
    "version",
    "channel",
    "source_branch",
    "release_sha",
    "candidate_run_id",
    "platform",
    "output",
  ]) {
    if (!options[name]) throw new Error(`缺少 --${name.replaceAll("_", "-")}。`);
  }
  return options;
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const identity = createPrereleaseBuildIdentity({
    version: options.version,
    channel: options.channel,
    sourceBranch: options.source_branch,
    releaseSha: options.release_sha,
    candidateRunId: options.candidate_run_id,
    platform: options.platform,
  });
  const outputPath = path.resolve(options.output);
  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(identity, null, 2)}\n`, "utf8");
  console.log(`[ok] ${identity.candidate_asset_name} 构建身份已写入 ${outputPath}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
