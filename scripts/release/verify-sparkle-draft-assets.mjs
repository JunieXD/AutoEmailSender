#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  extractCurrentReleaseAssetNames,
  normalizeReleaseTag,
} from "./prepare-sparkle-release.mjs";

export function assertDraftSparkleAssets({
  release,
  appcast,
  version,
  repository,
  tag,
}) {
  if (release.isDraft !== true) {
    throw new Error(`拒绝核验非 draft Release：${tag}`);
  }

  const expectedNames = extractCurrentReleaseAssetNames(
    appcast,
    version,
    repository,
    tag,
  );
  const actualAssets = new Map((release.assets ?? []).map((asset) => [asset.name, asset]));
  for (const name of expectedNames) {
    const asset = actualAssets.get(name);
    if (asset === undefined) {
      throw new Error(`draft Release 缺少 appcast.xml 引用的精确资产名：${name}`);
    }
    if (!Number.isInteger(asset.size) || asset.size <= 0) {
      throw new Error(`draft Release 资产为空或大小无效：${name}`);
    }
    const expectedUrl = `https://github.com/${repository}/releases/download/${tag}/${name}`;
    if (asset.url !== expectedUrl) {
      throw new Error(`draft Release 资产 URL 与 appcast.xml 不一致：${name}`);
    }
  }
  return expectedNames;
}

function parseArguments(argv) {
  const options = { repository: "", releaseTag: "", appcastPath: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    const value = argv[index + 1];
    if (!argument.startsWith("--") || value === undefined) {
      throw new Error(`无法解析参数：${argument}`);
    }
    index += 1;
    if (argument === "--repository") options.repository = value;
    else if (argument === "--release-tag") options.releaseTag = value;
    else if (argument === "--appcast") options.appcastPath = path.resolve(value);
    else throw new Error(`未知参数：${argument}`);
  }
  if (!options.repository || !options.releaseTag || !options.appcastPath) {
    throw new Error("必须提供 --repository、--release-tag 和 --appcast。 ");
  }
  return options;
}

function loadDraftRelease(repository, tag) {
  const result = spawnSync(
    "gh",
    ["release", "view", tag, "--repo", repository, "--json", "isDraft,assets"],
    { encoding: "utf8" },
  );
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(result.stderr.trim() || `无法读取 draft Release ${tag}。`);
  }
  return JSON.parse(result.stdout);
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const { tag, version } = normalizeReleaseTag(options.releaseTag);
  const appcast = await readFile(options.appcastPath, "utf8");
  const expectedNames = assertDraftSparkleAssets({
    release: loadDraftRelease(options.repository, tag),
    appcast,
    version,
    repository: options.repository,
    tag,
  });
  console.log(`draft Release 已核对 ${expectedNames.length} 个 Sparkle 下载资产。`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
