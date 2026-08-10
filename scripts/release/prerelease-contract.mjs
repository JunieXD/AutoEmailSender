#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { compareVersions, parseVersion } from "./check-release-version.mjs";

export const PRERELEASE_CHANNELS = Object.freeze(["alpha", "beta", "rc"]);
export const PRERELEASE_DEFAULT_BACKEND_MODE = "split";
export const PRERELEASE_DIAGNOSTICS_SCHEMA_VERSION = 1;

const CHANNELS = new Set(PRERELEASE_CHANNELS);
const STABLE_TAG_PATTERN = /^v(\d+\.\d+\.\d+)$/u;
const PRERELEASE_TAG_PATTERN = /^v(\d+\.\d+\.\d+-(?:alpha|beta|rc)\.[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)$/u;

export function normalizePrereleaseChannel(value) {
  const channel = String(value ?? "").trim().toLowerCase();
  if (!CHANNELS.has(channel)) {
    throw new Error(`测试版 channel 必须是 ${PRERELEASE_CHANNELS.join("、")}，实际为 ${value || "<empty>"}。`);
  }
  return channel;
}

export function parsePrereleaseVersion(value, expectedChannel = "") {
  const normalized = String(value ?? "").trim();
  const parsed = parseVersion(normalized);
  if (parsed.prerelease.length < 2) {
    throw new Error(`测试版版本必须包含 channel 和递增标识，例如 2.6.0-beta.1：${normalized}`);
  }
  const channel = normalizePrereleaseChannel(parsed.prerelease[0]);
  if (expectedChannel && channel !== normalizePrereleaseChannel(expectedChannel)) {
    throw new Error(`版本 ${normalized} 的 channel 为 ${channel}，与显式 channel ${expectedChannel} 不一致。`);
  }
  return { ...parsed, channel };
}

export function normalizeSourceBranch(value) {
  const sourceBranch = String(value ?? "");
  if (
    !/^[A-Za-z0-9._/+@-]{1,200}$/u.test(sourceBranch)
    || sourceBranch !== sourceBranch.trim()
    || sourceBranch.startsWith("refs/")
    || sourceBranch.startsWith("-")
    || sourceBranch.startsWith("/")
    || sourceBranch.endsWith("/")
    || sourceBranch.endsWith(".")
    || sourceBranch.includes("..")
    || sourceBranch.includes("@{")
    || sourceBranch.includes("//")
    || /[\u0000-\u0020\u007f~^:?*\[\\]/u.test(sourceBranch)
    || sourceBranch.split("/").some((part) => !part || part === "." || part === ".." || part.endsWith(".lock"))
  ) {
    throw new Error(`无效的 prerelease source branch：${value || "<empty>"}`);
  }
  return sourceBranch;
}

export function normalizeReleaseSha(value) {
  const releaseSha = String(value ?? "").trim().toLowerCase();
  if (!/^[0-9a-f]{40}$/u.test(releaseSha)) {
    throw new Error(`release_sha 必须是精确的 40 位提交 SHA：${value || "<empty>"}`);
  }
  return releaseSha;
}

export function normalizeCandidateRunId(value) {
  const normalized = String(value ?? "").trim();
  const runId = Number.parseInt(normalized, 10);
  if (!Number.isSafeInteger(runId) || runId <= 0 || String(runId) !== normalized) {
    throw new Error(`candidate run ID 无效：${value || "<empty>"}`);
  }
  return runId;
}

export function normalizePrereleaseContract({ version, channel, sourceBranch, releaseSha }) {
  const parsed = parsePrereleaseVersion(version, channel);
  return {
    version: parsed.value,
    releaseTag: `v${parsed.value}`,
    channel: parsed.channel,
    sourceBranch: normalizeSourceBranch(sourceBranch),
    releaseSha: normalizeReleaseSha(releaseSha),
    defaultBackendMode: PRERELEASE_DEFAULT_BACKEND_MODE,
    diagnosticsSchemaVersion: PRERELEASE_DIAGNOSTICS_SCHEMA_VERSION,
  };
}

export function stableReleaseTags(tagNames) {
  return tagNames
    .map((tag) => STABLE_TAG_PATTERN.exec(String(tag))?.[1])
    .filter(Boolean);
}

export function latestStableReleaseTag(tagNames) {
  const versions = stableReleaseTags(tagNames);
  if (versions.length === 0) return null;
  const latest = versions.reduce((current, candidate) => (
    compareVersions(candidate, current) > 0 ? candidate : current
  ));
  return `v${latest}`;
}

export function assertPrereleaseVersionAvailable(version, channel, tagNames) {
  const target = parsePrereleaseVersion(version, channel);
  const targetTag = `v${target.value}`;
  if (tagNames.includes(targetTag)) {
    throw new Error(`测试版 tag 已存在且不可覆盖：${targetTag}`);
  }

  const stableVersions = stableReleaseTags(tagNames);
  const highestStable = stableVersions.length === 0
    ? null
    : stableVersions.reduce((current, candidate) => (
        compareVersions(candidate, current) > 0 ? candidate : current
      ));
  if (highestStable !== null) {
    const stableCore = parseVersion(highestStable).core;
    const isHigherCore = target.core.some((part, index) => (
      part !== stableCore[index] && part > stableCore[index]
    ) && target.core.slice(0, index).every((prefix, prefixIndex) => prefix === stableCore[prefixIndex]));
    if (!isHigherCore) {
      throw new Error(`测试版 ${targetTag} 的核心版本必须高于最新稳定版 v${highestStable}。`);
    }
  }

  const sameCorePrereleases = tagNames
    .map((tag) => PRERELEASE_TAG_PATTERN.exec(String(tag))?.[1])
    .filter(Boolean)
    .filter((candidate) => {
      const parsed = parsePrereleaseVersion(candidate);
      return parsed.core.every((part, index) => part === target.core[index]);
    });
  if (sameCorePrereleases.length > 0) {
    const highest = sameCorePrereleases.reduce((current, candidate) => (
      compareVersions(candidate, current) > 0 ? candidate : current
    ));
    if (compareVersions(target.value, highest) <= 0) {
      throw new Error(`测试版 v${target.value} 必须高于同核心版本的现有测试版 v${highest}。`);
    }
  }
  return { highestStable, highestPrerelease: sameCorePrereleases.length === 0 ? null : sameCorePrereleases.reduce(
    (current, candidate) => compareVersions(candidate, current) > 0 ? candidate : current,
  ) };
}

export function assertPrereleaseSupersession(previousVersion, replacementVersion) {
  const previous = parsePrereleaseVersion(previousVersion);
  const replacement = parsePrereleaseVersion(replacementVersion);
  if (!previous.core.every((part, index) => part === replacement.core[index])) {
    throw new Error("取代测试版必须使用相同核心版本；正式升级使用新的发布流程。");
  }
  if (compareVersions(replacement.value, previous.value) <= 0) {
    throw new Error(`取代版本 v${replacement.value} 必须高于 v${previous.value}。`);
  }
  return {
    previousTag: `v${previous.value}`,
    replacementTag: `v${replacement.value}`,
  };
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

function main() {
  const { mode, options } = parseArguments(process.argv.slice(2));
  if (mode === "validate") {
    const contract = normalizePrereleaseContract({
      version: required(options, "version"),
      channel: required(options, "channel"),
      sourceBranch: required(options, "source_branch"),
      releaseSha: required(options, "release_sha"),
    });
    console.log(JSON.stringify(contract));
    return;
  }
  if (mode === "check-tags") {
    const repoRoot = path.resolve(required(options, "repo_root"));
    const tags = execFileSync("git", ["-C", repoRoot, "tag", "--list", "v*"], {
      encoding: "utf8",
    }).split(/\r?\n/u).filter(Boolean);
    const result = assertPrereleaseVersionAvailable(
      required(options, "version"),
      required(options, "channel"),
      tags,
    );
    console.log(JSON.stringify(result));
    return;
  }
  if (mode === "latest-stable") {
    const repoRoot = path.resolve(required(options, "repo_root"));
    const tags = execFileSync(
      "git",
      ["-C", repoRoot, "tag", "--merged", required(options, "ref"), "--list", "v*"],
      { encoding: "utf8" },
    ).split(/\r?\n/u).filter(Boolean);
    const tag = latestStableReleaseTag(tags);
    if (tag === null) throw new Error("当前提交没有可达的稳定版本 tag。");
    console.log(tag);
    return;
  }
  if (mode === "supersede") {
    console.log(JSON.stringify(assertPrereleaseSupersession(
      required(options, "previous_version"),
      required(options, "replacement_version"),
    )));
    return;
  }
  throw new Error(
    "用法: prerelease-contract.mjs <validate|check-tags|latest-stable|supersede> [options]",
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
