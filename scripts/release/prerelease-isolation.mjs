#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import {
  normalizeReleaseSha,
  parsePrereleaseVersion,
} from "./prerelease-contract.mjs";

const SNAPSHOT_SCHEMA_VERSION = 1;
const SNAPSHOT_KIND = "auto-email-sender-stable-isolation-snapshot";
const MAX_METADATA_ASSET_BYTES = 32 * 1024 * 1024;
const REQUIRED_STABLE_ASSETS = ["appcast.xml", "latest.yml"];
const SAFE_DOWNLOAD_HOSTS = new Set([
  "api.github.com",
  "github.com",
  "objects.githubusercontent.com",
  "release-assets.githubusercontent.com",
]);

function normalizeRepository(value) {
  const repository = String(value ?? "").trim();
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/u.test(repository)) {
    throw new Error(`无效的 GitHub 仓库：${value || "<empty>"}`);
  }
  return repository;
}

function githubHeaders(token, accept = "application/vnd.github+json") {
  return {
    Accept: accept,
    "User-Agent": "AutoEmailSender-Prerelease-Isolation",
    "X-GitHub-Api-Version": "2022-11-28",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function fetchJson(url, { token, fetchImpl }) {
  const response = await fetchImpl(url, {
    headers: githubHeaders(token),
    redirect: "error",
  });
  if (!response.ok) {
    throw new Error(`GitHub API ${url} 返回 HTTP ${response.status}。`);
  }
  return response.json();
}

function validateAssetApiUrl(value) {
  const url = new URL(String(value));
  if (url.protocol !== "https:" || url.hostname !== "api.github.com") {
    throw new Error("稳定更新资产 API URL 不受信任。");
  }
  return url;
}

async function downloadBoundedAsset(assetUrl, { token, fetchImpl }) {
  let url = validateAssetApiUrl(assetUrl);
  let includeToken = true;
  for (let redirects = 0; redirects <= 5; redirects += 1) {
    const response = await fetchImpl(url, {
      headers: githubHeaders(
        includeToken ? token : "",
        "application/octet-stream",
      ),
      redirect: "manual",
    });
    if (response.status >= 300 && response.status < 400) {
      const location = response.headers.get("location");
      if (!location) throw new Error("稳定更新资产重定向缺少 Location。");
      url = new URL(location, url);
      if (url.protocol !== "https:" || !SAFE_DOWNLOAD_HOSTS.has(url.hostname)) {
        throw new Error(`稳定更新资产重定向到不受信任的主机：${url.hostname}`);
      }
      includeToken = url.hostname === "api.github.com";
      continue;
    }
    if (!response.ok || response.body === null) {
      throw new Error(`稳定更新资产下载返回 HTTP ${response.status}。`);
    }
    const declaredLength = Number(response.headers.get("content-length") ?? "0");
    if (Number.isFinite(declaredLength) && declaredLength > MAX_METADATA_ASSET_BYTES) {
      throw new Error("稳定更新资产超过大小上限。");
    }
    const chunks = [];
    let total = 0;
    for await (const chunk of response.body) {
      const buffer = Buffer.from(chunk);
      total += buffer.length;
      if (total > MAX_METADATA_ASSET_BYTES) {
        throw new Error("稳定更新资产超过大小上限。");
      }
      chunks.push(buffer);
    }
    return Buffer.concat(chunks, total);
  }
  throw new Error("稳定更新资产重定向次数过多。");
}

function normalizeStableRelease(release, repository) {
  if (
    !release
    || !Number.isSafeInteger(release.id)
    || release.id <= 0
    || typeof release.tag_name !== "string"
    || !/^v\d+\.\d+\.\d+$/u.test(release.tag_name)
    || release.draft !== false
    || release.prerelease !== false
    || !Array.isArray(release.assets)
  ) {
    throw new Error(`GitHub Latest 不是有效的稳定 Release：${repository}`);
  }
  return release;
}

export async function captureStableIsolationSnapshot({
  repository,
  token = "",
  fetchImpl = fetch,
  now = () => new Date(),
}) {
  const normalizedRepository = normalizeRepository(repository);
  const release = normalizeStableRelease(
    await fetchJson(
      `https://api.github.com/repos/${normalizedRepository}/releases/latest`,
      { token, fetchImpl },
    ),
    normalizedRepository,
  );
  const assets = {};
  for (const name of REQUIRED_STABLE_ASSETS) {
    const matches = release.assets.filter((asset) => asset?.name === name);
    if (matches.length !== 1) {
      throw new Error(`稳定 Release ${release.tag_name} 必须恰好包含一个 ${name}。`);
    }
    const asset = matches[0];
    if (!Number.isSafeInteger(asset.id) || asset.id <= 0 || typeof asset.url !== "string") {
      throw new Error(`稳定更新资产 ${name} 元数据无效。`);
    }
    const content = await downloadBoundedAsset(asset.url, { token, fetchImpl });
    assets[name] = {
      assetId: asset.id,
      size: content.length,
      sha256: createHash("sha256").update(content).digest("hex"),
    };
  }
  return {
    schemaVersion: SNAPSHOT_SCHEMA_VERSION,
    kind: SNAPSHOT_KIND,
    repository: normalizedRepository,
    capturedAt: now().toISOString(),
    latestRelease: {
      releaseId: release.id,
      tag: release.tag_name,
      publishedAt: typeof release.published_at === "string" ? release.published_at : null,
    },
    assets,
  };
}

export function assertStableIsolationUnchanged(baseline, current) {
  validateStableIsolationSnapshot(baseline);
  validateStableIsolationSnapshot(current);
  if (baseline.repository !== current.repository) {
    throw new Error("稳定更新隔离快照的仓库不一致。");
  }
  const comparable = (snapshot) => ({
    latestRelease: snapshot.latestRelease,
    assets: snapshot.assets,
  });
  if (JSON.stringify(comparable(baseline)) !== JSON.stringify(comparable(current))) {
    throw new Error("Prerelease 发布前后稳定 Latest、appcast.xml 或 latest.yml 已发生变化。");
  }
  return current;
}

export function validateStableIsolationSnapshot(value) {
  if (
    !value
    || value.schemaVersion !== SNAPSHOT_SCHEMA_VERSION
    || value.kind !== SNAPSHOT_KIND
    || normalizeRepository(value.repository) !== value.repository
    || !value.latestRelease
    || !Number.isSafeInteger(value.latestRelease.releaseId)
    || value.latestRelease.releaseId <= 0
    || typeof value.latestRelease.tag !== "string"
    || !/^v\d+\.\d+\.\d+$/u.test(value.latestRelease.tag)
    || !value.assets
  ) {
    throw new Error("稳定更新隔离快照格式无效。");
  }
  for (const name of REQUIRED_STABLE_ASSETS) {
    const asset = value.assets[name];
    if (
      !asset
      || !Number.isSafeInteger(asset.assetId)
      || asset.assetId <= 0
      || !Number.isSafeInteger(asset.size)
      || asset.size <= 0
      || typeof asset.sha256 !== "string"
      || !/^[0-9a-f]{64}$/u.test(asset.sha256)
    ) {
      throw new Error(`稳定更新隔离快照中的 ${name} 无效。`);
    }
  }
  return value;
}

async function resolveRemoteTagSha({ repository, releaseTag, token, fetchImpl }) {
  const reference = await fetchJson(
    `https://api.github.com/repos/${repository}/git/ref/tags/${encodeURIComponent(releaseTag)}`,
    { token, fetchImpl },
  );
  let object = reference?.object;
  for (let depth = 0; depth < 2; depth += 1) {
    if (object?.type === "commit" && typeof object.sha === "string") {
      return normalizeReleaseSha(object.sha);
    }
    if (object?.type !== "tag" || typeof object.url !== "string") break;
    const tagUrl = new URL(object.url);
    if (tagUrl.protocol !== "https:" || tagUrl.hostname !== "api.github.com") {
      throw new Error("Prerelease annotated tag URL 不受信任。");
    }
    object = (await fetchJson(tagUrl, { token, fetchImpl }))?.object;
  }
  throw new Error(`无法把 ${releaseTag} 解析到精确提交。`);
}

export async function verifyPublishedPrereleaseIsolation({
  repository,
  version,
  releaseSha,
  baseline,
  token = "",
  fetchImpl = fetch,
  now = () => new Date(),
}) {
  const normalizedRepository = normalizeRepository(repository);
  const parsed = parsePrereleaseVersion(version);
  const releaseTag = `v${parsed.value}`;
  validateStableIsolationSnapshot(baseline);
  const release = await fetchJson(
    `https://api.github.com/repos/${normalizedRepository}/releases/tags/${encodeURIComponent(releaseTag)}`,
    { token, fetchImpl },
  );
  if (
    !release
    || release.tag_name !== releaseTag
    || release.draft !== false
    || release.prerelease !== true
    || !Array.isArray(release.assets)
  ) {
    throw new Error(`${releaseTag} 不是公开 GitHub Prerelease。`);
  }
  const assetNames = release.assets.map((asset) => asset?.name).filter(Boolean).sort();
  const requiredNames = [
    `AutoEmailSender-${parsed.value}-arm64.dmg`,
    `AutoEmailSender-Setup-${parsed.value}.exe`,
    "prerelease-candidate.json",
  ].sort();
  for (const name of requiredNames) {
    if (assetNames.filter((candidate) => candidate === name).length !== 1) {
      throw new Error(`${releaseTag} 必须恰好包含一个 ${name}。`);
    }
  }
  if (assetNames.some((name) => (
    name === "latest.yml"
    || name === "appcast.xml"
    || name.endsWith(".delta")
    || name.endsWith(".blockmap")
  ))) {
    throw new Error(`${releaseTag} 不得包含稳定更新 metadata 或差分包。`);
  }
  if (JSON.stringify(assetNames) !== JSON.stringify(requiredNames)) {
    throw new Error(`${releaseTag} 包含候选合同之外的公开资产。`);
  }
  const remoteSha = await resolveRemoteTagSha({
    repository: normalizedRepository,
    releaseTag,
    token,
    fetchImpl,
  });
  if (remoteSha !== normalizeReleaseSha(releaseSha)) {
    throw new Error(`${releaseTag} 指向 ${remoteSha}，预期 ${releaseSha}。`);
  }
  const current = await captureStableIsolationSnapshot({
    repository: normalizedRepository,
    token,
    fetchImpl,
    now,
  });
  assertStableIsolationUnchanged(baseline, current);
  return {
    releaseTag,
    releaseId: release.id,
    releaseSha: remoteSha,
    assetNames,
    stableLatestTag: current.latestRelease.tag,
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

async function main() {
  const { mode, options } = parseArguments(process.argv.slice(2));
  if (mode === "compare") {
    const baseline = JSON.parse(await readFile(path.resolve(required(options, "baseline")), "utf8"));
    const current = JSON.parse(await readFile(path.resolve(required(options, "current")), "utf8"));
    assertStableIsolationUnchanged(baseline, current);
    console.log(`[ok] 稳定 Latest ${current.latestRelease.tag} 与候选认证基线完全一致。`);
    return;
  }
  const token = process.env.GH_TOKEN ?? "";
  if (!token) throw new Error("缺少 GH_TOKEN；不会从命令行读取或打印 token。");
  if (mode === "capture") {
    const snapshot = await captureStableIsolationSnapshot({
      repository: required(options, "repository"),
      token,
    });
    await writeFile(path.resolve(required(options, "output")), `${JSON.stringify(snapshot, null, 2)}\n`, "utf8");
    console.log(`[ok] 已记录稳定 Latest ${snapshot.latestRelease.tag} 的隔离基线。`);
    return;
  }
  if (mode === "verify") {
    const baseline = JSON.parse(await readFile(path.resolve(required(options, "baseline")), "utf8"));
    const result = await verifyPublishedPrereleaseIsolation({
      repository: required(options, "repository"),
      version: required(options, "version"),
      releaseSha: required(options, "release_sha"),
      baseline,
      token,
    });
    if (options.output) {
      await writeFile(path.resolve(options.output), `${JSON.stringify(result, null, 2)}\n`, "utf8");
    }
    console.log(`[ok] ${result.releaseTag} 为非 Latest Prerelease，稳定 Latest 仍为 ${result.stableLatestTag}。`);
    return;
  }
  throw new Error("用法: prerelease-isolation.mjs <capture|compare|verify> [options]");
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
