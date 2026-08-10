#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import path from "node:path";

const VERSION_PATTERN = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/;

export function parseVersion(value) {
  const match = VERSION_PATTERN.exec(value);
  if (!match) {
    throw new Error(`无效版本号：${value}`);
  }
  return {
    value,
    core: [BigInt(match[1]), BigInt(match[2]), BigInt(match[3])],
    prerelease: match[4]?.split(".") ?? [],
  };
}

export function compareVersions(leftValue, rightValue) {
  const left = parseVersion(leftValue);
  const right = parseVersion(rightValue);
  for (let index = 0; index < left.core.length; index += 1) {
    if (left.core[index] !== right.core[index]) {
      return left.core[index] > right.core[index] ? 1 : -1;
    }
  }

  if (left.prerelease.length === 0 || right.prerelease.length === 0) {
    if (left.prerelease.length === right.prerelease.length) return 0;
    return left.prerelease.length === 0 ? 1 : -1;
  }

  const length = Math.max(left.prerelease.length, right.prerelease.length);
  for (let index = 0; index < length; index += 1) {
    const leftPart = left.prerelease[index];
    const rightPart = right.prerelease[index];
    if (leftPart === undefined || rightPart === undefined) {
      return leftPart === undefined ? -1 : 1;
    }
    if (leftPart === rightPart) continue;

    const leftNumeric = /^\d+$/.test(leftPart);
    const rightNumeric = /^\d+$/.test(rightPart);
    if (leftNumeric && rightNumeric) {
      const leftNumber = BigInt(leftPart);
      const rightNumber = BigInt(rightPart);
      if (leftNumber === rightNumber) continue;
      return leftNumber > rightNumber ? 1 : -1;
    }
    if (leftNumeric !== rightNumeric) return leftNumeric ? -1 : 1;
    return leftPart > rightPart ? 1 : -1;
  }
  return 0;
}

export function assertReleaseVersion(version, tagNames) {
  const parsed = parseVersion(version);
  if (parsed.prerelease.length > 0) {
    throw new Error(`稳定版入口不接受 prerelease 版本：${version}`);
  }
  const targetTag = `v${version}`;
  if (tagNames.includes(targetTag)) {
    throw new Error(`发布 tag 已存在：${targetTag}`);
  }

  const versions = tagNames
    .filter((tag) => /^v\d+\.\d+\.\d+$/.test(tag))
    .map((tag) => tag.slice(1))
    .filter((candidate) => VERSION_PATTERN.test(candidate));
  if (versions.length === 0) return null;

  const highest = versions.reduce((current, candidate) =>
    compareVersions(candidate, current) > 0 ? candidate : current
  );
  if (compareVersions(version, highest) <= 0) {
    throw new Error(`目标版本 v${version} 必须高于当前最高 tag v${highest}`);
  }
  return highest;
}

function parseArguments(arguments_) {
  const result = { version: "", repoRoot: "" };
  for (let index = 0; index < arguments_.length; index += 1) {
    const argument = arguments_[index];
    if (argument === "--version") result.version = arguments_[index += 1] ?? "";
    else if (argument === "--repo-root") result.repoRoot = arguments_[index += 1] ?? "";
    else throw new Error(`未知参数：${argument}`);
  }
  if (!result.version || !result.repoRoot) {
    throw new Error("用法: check-release-version.mjs --version <x.y.z> --repo-root <path>");
  }
  return result;
}

function main() {
  try {
    const { version, repoRoot } = parseArguments(process.argv.slice(2));
    const git = spawnSync("git", ["-C", repoRoot, "tag", "--list", "v*"], {
      encoding: "utf8",
    });
    if (git.status !== 0) {
      throw new Error(git.stderr.trim() || "无法读取本地 release tags");
    }
    const tags = git.stdout.split(/\r?\n/).filter(Boolean);
    const highest = assertReleaseVersion(version, tags);
    console.log(
      highest
        ? `[ok] v${version} 高于当前最高 tag v${highest}`
        : `[ok] v${version} 是仓库中的首个有效 release tag`,
    );
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) main();
