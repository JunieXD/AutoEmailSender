#!/usr/bin/env node

import { execFileSync, spawnSync } from "node:child_process";
import { createHash, createPublicKey, verify } from "node:crypto";
import { mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { compareVersions } from "./check-release-version.mjs";
import { validateSparklePublicKey } from "../packaging/configure-sparkle-info.mjs";
import {
  assertRequiredDelta,
  assertSparkleAppcastSignature,
  getMacDmgVersion,
  normalizeReleaseTag,
} from "./prepare-sparkle-release.mjs";

const RELEASE_WORKFLOW_NAME = "Release Desktop";
const WEBSITE_WORKFLOW_NAME = "Deploy Website";
const WEBSITE_PUBLIC_ROOT = "https://juniexd.github.io/AutoEmailSender";
const ED25519_SPKI_PUBLIC_KEY_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

function fail(message) {
  throw new Error(message);
}

function assertSparkleEnclosureSignature(contents, signatureValue, publicKey) {
  const signature = Buffer.from(signatureValue, "base64");
  if (signature.length !== 64) fail("Sparkle enclosure 签名格式无效。 ");
  const publicKeyBytes = Buffer.from(validateSparklePublicKey(publicKey), "base64");
  const publicKeyObject = createPublicKey({
    key: Buffer.concat([ED25519_SPKI_PUBLIC_KEY_PREFIX, publicKeyBytes]),
    format: "der",
    type: "spki",
  });
  if (!verify(null, contents, publicKeyObject, signature)) {
    fail("Sparkle enclosure 未通过 Ed25519 签名验证。 ");
  }
}

function commandOutput(command, args, options = {}) {
  return execFileSync(command, args, { encoding: "utf8", ...options }).trim();
}

function ghJson(args) {
  return JSON.parse(commandOutput("gh", args));
}

function decodeXml(value) {
  return value
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&amp;", "&");
}

function xmlAttributes(tag) {
  const attributes = {};
  for (const match of tag.matchAll(/([:\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) {
    attributes[match[1]] = decodeXml(match[2] ?? match[3]);
  }
  return attributes;
}

function itemBlocks(appcast) {
  return appcast.match(/<item\b[\s\S]*?<\/item>/gi) ?? [];
}

function itemVersion(item) {
  const match = /<sparkle:version>\s*([^<]+?)\s*<\/sparkle:version>/i.exec(item);
  return match === null ? null : decodeXml(match[1].trim());
}

function parseGitHubAssetUrl(value, repository) {
  let url;
  try {
    url = new URL(value);
  } catch {
    fail(`appcast.xml 包含无效下载 URL：${value}`);
  }
  const [owner, repo] = repository.split("/");
  const match = /^\/([^/]+)\/([^/]+)\/releases\/download\/([^/]+)\/(.+)$/.exec(url.pathname);
  if (
    url.protocol !== "https:" ||
    url.hostname.toLowerCase() !== "github.com" ||
    match === null ||
    match[1].toLowerCase() !== owner.toLowerCase() ||
    match[2].toLowerCase() !== repo.toLowerCase()
  ) {
    fail(`appcast.xml 下载 URL 不属于 ${repository}：${value}`);
  }
  return { tag: decodeURIComponent(match[3]), name: decodeURIComponent(match[4]), url: url.toString() };
}

export function extractCurrentSparkleEnclosures(appcast, version, repository, tag) {
  const currentItem = itemBlocks(appcast).find((item) => itemVersion(item) === version);
  if (currentItem === undefined) fail(`appcast.xml 不包含当前版本 ${version}。`);
  const enclosures = [...currentItem.matchAll(/<enclosure\b[^>]*>/gi)].map((match) => {
    const attributes = xmlAttributes(match[0]);
    const asset = parseGitHubAssetUrl(attributes.url ?? "", repository);
    if (asset.tag !== tag) fail(`当前版本 ${version} 的资产 URL 未指向 ${tag}：${asset.name}`);
    const length = Number.parseInt(attributes.length ?? "", 10);
    if (!Number.isSafeInteger(length) || length <= 0) fail(`Sparkle enclosure 长度无效：${asset.name}`);
    if (!attributes["sparkle:edSignature"]) fail(`Sparkle enclosure 缺少签名：${asset.name}`);
    return {
      ...asset,
      length,
      signature: attributes["sparkle:edSignature"],
      deltaFrom: attributes["sparkle:deltaFrom"] ?? null,
    };
  });
  if (enclosures.length === 0) fail(`appcast.xml 当前版本 ${version} 没有 enclosure。`);
  return enclosures;
}

export function selectPreviousSparkleDmg(appcast, currentVersion, repository) {
  const candidates = [];
  for (const item of itemBlocks(appcast)) {
    const version = itemVersion(item);
    if (!version || compareVersions(version, currentVersion) >= 0) continue;
    const withoutDeltas = item.replace(/<sparkle:deltas\b[\s\S]*?<\/sparkle:deltas>/gi, "");
    const enclosureTag = /<enclosure\b[^>]*>/i.exec(withoutDeltas)?.[0];
    if (!enclosureTag) continue;
    const asset = parseGitHubAssetUrl(xmlAttributes(enclosureTag).url ?? "", repository);
    const canonicalVersion = getMacDmgVersion(asset.name);
    if (canonicalVersion !== version) continue;
    candidates.push({ ...asset, version, tag: `v${version}` });
  }
  candidates.sort((left, right) => compareVersions(right.version, left.version));
  if (candidates.length === 0) fail(`appcast.xml 中找不到 ${currentVersion} 的上一版完整 DMG。`);
  return candidates[0];
}

function previousDmgAssets(appcast, currentVersion, repository) {
  return itemBlocks(appcast)
    .map((item) => {
      const version = itemVersion(item);
      if (!version || compareVersions(version, currentVersion) >= 0) return null;
      const withoutDeltas = item.replace(/<sparkle:deltas\b[\s\S]*?<\/sparkle:deltas>/gi, "");
      const enclosureTag = /<enclosure\b[^>]*>/i.exec(withoutDeltas)?.[0];
      if (!enclosureTag) return null;
      const asset = parseGitHubAssetUrl(xmlAttributes(enclosureTag).url ?? "", repository);
      return getMacDmgVersion(asset.name) === version ? { ...asset, tag: `v${version}` } : null;
    })
    .filter(Boolean);
}

function jobMap(run) {
  return new Map((run.jobs ?? []).map((job) => [job.name, job.conclusion]));
}

export function assertReleaseWorkflowRuns(candidate, promotion, expectedSha) {
  for (const [label, run] of [["候选", candidate], ["提升", promotion]]) {
    if (run.workflowName !== RELEASE_WORKFLOW_NAME || run.status !== "completed" || run.conclusion !== "success") {
      fail(`${label} workflow 未成功完成。`);
    }
    if (run.headSha !== expectedSha) fail(`${label} workflow SHA 与候选不一致。`);
  }
  const candidateJobs = jobMap(candidate);
  for (const name of ["preflight", "build-windows", "build-macos", "certify"]) {
    if (candidateJobs.get(name) !== "success") fail(`候选 job ${name} 未成功。`);
  }
  if (candidateJobs.get("publish") !== "skipped") fail("候选 run 不应执行 publish job。");

  const promotionJobs = jobMap(promotion);
  if (promotionJobs.get("publish") !== "success") fail("提升 run 的 publish job 未成功。 ");
  for (const name of ["preflight", "build-windows", "build-macos", "certify"]) {
    if (promotionJobs.get(name) !== "skipped") fail(`提升 run 的 ${name} job 应为 skipped。`);
  }
}

function candidateArtifactRecords(manifest) {
  const records = [];
  for (const platform of ["windows", "macos", "skill"]) {
    const artifacts = manifest.platforms?.[platform]?.artifacts;
    if (!Array.isArray(artifacts)) fail(`候选报告缺少 ${platform} 资产摘要。`);
    records.push(...artifacts);
  }
  return records.sort((left, right) => left.name.localeCompare(right.name));
}

export function assertPublishedRelease(release, manifest) {
  if (release.tagName !== manifest.releaseTag || release.isDraft || release.isPrerelease) {
    fail(`${manifest.releaseTag} 尚未作为稳定公开 Release 发布。`);
  }
  const expected = candidateArtifactRecords(manifest);
  const actual = (release.assets ?? [])
    .map((asset) => ({ name: asset.name, size: asset.size }))
    .sort((left, right) => left.name.localeCompare(right.name));
  if (actual.length !== expected.length) fail("公开 Release 的资产数量与候选不一致。 ");
  for (let index = 0; index < expected.length; index += 1) {
    if (actual[index]?.name !== expected[index].name || actual[index]?.size !== expected[index].size) {
      fail(`公开 Release 资产名或大小与候选不一致：${expected[index].name}`);
    }
  }
  return expected;
}

async function sha256(filePath) {
  return createHash("sha256").update(await readFile(filePath)).digest("hex");
}

async function findFile(root, basename) {
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const entryPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      const nested = await findFile(entryPath, basename);
      if (nested) return nested;
    } else if (entry.isFile() && entry.name === basename) {
      return entryPath;
    }
  }
  return null;
}

async function assertDownloadedArtifacts(directory, records) {
  for (const record of records) {
    const filePath = path.join(directory, record.name);
    const fileStat = await stat(filePath).catch(() => null);
    if (!fileStat?.isFile() || fileStat.size !== record.size || await sha256(filePath) !== record.sha256) {
      fail(`公开资产与候选摘要不一致：${record.name}`);
    }
  }
}

async function download(url, outputPath) {
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok) fail(`下载失败 (${response.status})：${url}`);
  await writeFile(outputPath, Buffer.from(await response.arrayBuffer()));
}

async function fetchPublicPage(url, expectedText) {
  const response = await fetch(url, { redirect: "follow" });
  const body = await response.text();
  if (!response.ok || !body.includes(expectedText)) fail(`公开网站检查失败：${url}`);
}

function extractMountedPublicKey(dmgPath) {
  if (process.platform !== "darwin") fail("从上一版客户端提取 Sparkle 公钥需要在 macOS 上运行。");
  const attached = spawnSync("hdiutil", ["attach", "-readonly", "-nobrowse", "-plist", dmgPath]);
  if (attached.status !== 0) fail(attached.stderr?.toString().trim() || "无法挂载上一版 DMG。");
  const converted = spawnSync("plutil", ["-convert", "json", "-o", "-", "-"], { input: attached.stdout });
  if (converted.status !== 0) fail("无法解析 hdiutil 挂载结果。");
  const entities = JSON.parse(converted.stdout.toString())["system-entities"] ?? [];
  const mountPoint = entities.map((entity) => entity["mount-point"]).find(Boolean);
  if (!mountPoint) fail("上一版 DMG 没有可用挂载点。");
  try {
    const appName = commandOutput("find", [mountPoint, "-maxdepth", "2", "-name", "*.app", "-type", "d"])
      .split(/\r?\n/)
      .find(Boolean);
    if (!appName) fail("上一版 DMG 中找不到应用包。");
    return commandOutput("plutil", [
      "-extract",
      "SUPublicEDKey",
      "raw",
      "-o",
      "-",
      path.join(appName, "Contents", "Info.plist"),
    ]);
  } finally {
    const detached = spawnSync("hdiutil", ["detach", mountPoint]);
    if (detached.status !== 0) fail(`无法卸载上一版 DMG：${mountPoint}`);
  }
}

async function verifySkillZip({ repoRoot, releaseSha, version, publicDirectory, tempRoot, expectedRecord }) {
  const archivePath = path.join(tempRoot, "tagged-source.tar");
  const sourceDirectory = path.join(tempRoot, "tagged-source");
  const outputDirectory = path.join(tempRoot, "rebuilt-skill");
  await Promise.all([
    mkdir(sourceDirectory, { recursive: true }),
    mkdir(outputDirectory, { recursive: true }),
  ]);
  execFileSync("git", ["-C", repoRoot, "archive", "--format=tar", `--output=${archivePath}`, releaseSha]);
  execFileSync("tar", ["-xf", archivePath, "-C", sourceDirectory]);
  execFileSync("python3", [
    path.join(sourceDirectory, "scripts", "packaging", "package_crawl_mentors_skill.py"),
    "--version", version,
    "--repo-root", sourceDirectory,
    "--output-dir", outputDirectory,
  ]);
  const rebuiltPath = path.join(outputDirectory, expectedRecord.name);
  const publicPath = path.join(publicDirectory, expectedRecord.name);
  if (await sha256(rebuiltPath) !== await sha256(publicPath)) fail("公开 Skill ZIP 不是 tagged canonical Skill 的确定性产物。 ");
}

async function verifyWebsiteIfChanged(repoRoot, releaseSha, repository) {
  const previousTag = commandOutput("git", ["-C", repoRoot, "describe", "--tags", "--abbrev=0", "--match", "v*", `${releaseSha}^`]);
  const changed = spawnSync("git", ["-C", repoRoot, "diff", "--quiet", previousTag, releaseSha, "--", "website"]).status !== 0;
  if (!changed) return false;
  const websiteCommit = commandOutput("git", ["-C", repoRoot, "rev-list", "-1", releaseSha, "--", "website"]);
  const runs = ghJson([
    "run", "list", "--repo", repository, "--workflow", "website.yml", "--commit", websiteCommit,
    "--limit", "20", "--json", "workflowName,headSha,status,conclusion,databaseId",
  ]);
  if (!runs.some((run) => run.workflowName === WEBSITE_WORKFLOW_NAME && run.headSha === websiteCommit && run.status === "completed" && run.conclusion === "success")) {
    fail(`website 最新变更提交 ${websiteCommit} 没有成功部署。`);
  }
  await fetchPublicPage(`${WEBSITE_PUBLIC_ROOT}/docs/mentor-crawler-skill`, "导师抓取 Skill");
  await fetchPublicPage(`${WEBSITE_PUBLIC_ROOT}/crawl-benchmark`, "智能抓取实测");
  return true;
}

function remoteTagSha(repoRoot, tag) {
  const output = commandOutput("git", ["-C", repoRoot, "ls-remote", "--tags", "origin", `refs/tags/${tag}`, `refs/tags/${tag}^{}`]);
  const lines = output.split(/\r?\n/).filter(Boolean);
  const peeled = lines.find((line) => line.endsWith(`refs/tags/${tag}^{}`));
  const direct = lines.find((line) => line.endsWith(`refs/tags/${tag}`));
  return (peeled ?? direct)?.split(/\s+/)[0] ?? "";
}

function parseArguments(argv) {
  const options = { version: "", candidateRun: "", promotionRun: "", repository: "JunieXD/AutoEmailSender", repoRoot: process.cwd() };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (!argument.startsWith("--") && !options.version) {
      options.version = argument;
      continue;
    }
    const value = argv[index + 1];
    if (!value) fail(`缺少 ${argument} 的值。`);
    index += 1;
    if (argument === "--candidate-run") options.candidateRun = value;
    else if (argument === "--promotion-run") options.promotionRun = value;
    else if (argument === "--repository") options.repository = value;
    else if (argument === "--repo-root") options.repoRoot = path.resolve(value);
    else fail(`未知参数：${argument}`);
  }
  if (!options.version || !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(options.version) || !/^\d+$/.test(options.candidateRun) || !/^\d+$/.test(options.promotionRun)) {
    fail("用法: verify-release.mjs <version> --candidate-run <id> --promotion-run <id> [--repository owner/repo] [--repo-root path]");
  }
  return options;
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const { tag, version } = normalizeReleaseTag(options.version);
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), `auto-email-sender-verify-${version}-`));
  try {
    const candidateDirectory = path.join(tempRoot, "candidate");
    const publicDirectory = path.join(tempRoot, "public");
    execFileSync("gh", ["run", "download", options.candidateRun, "--repo", options.repository, "--name", "release-candidate", "--dir", candidateDirectory], { stdio: "inherit" });
    execFileSync("gh", ["release", "download", tag, "--repo", options.repository, "--dir", publicDirectory], { stdio: "inherit" });
    const manifestPath = await findFile(candidateDirectory, "release-candidate.json");
    if (!manifestPath) fail("候选 run 缺少 release-candidate.json。");
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    if (manifest.releaseTag !== tag || manifest.version !== version || String(manifest.candidateRunId) !== options.candidateRun) fail("候选报告与请求的版本或 run ID 不一致。 ");

    const runFields = "workflowName,headSha,status,conclusion,jobs";
    const candidate = ghJson(["run", "view", options.candidateRun, "--repo", options.repository, "--json", runFields]);
    const promotion = ghJson(["run", "view", options.promotionRun, "--repo", options.repository, "--json", runFields]);
    assertReleaseWorkflowRuns(candidate, promotion, manifest.releaseSha);
    console.log("[ok] 候选与提升 workflow 拓扑、状态和 SHA 一致。 ");

    if (remoteTagSha(options.repoRoot, tag) !== manifest.releaseSha) fail(`远端 tag ${tag} 未指向候选 SHA。`);
    const release = ghJson(["release", "view", tag, "--repo", options.repository, "--json", "isDraft,isPrerelease,tagName,url,assets"]);
    const records = assertPublishedRelease(release, manifest);
    await assertDownloadedArtifacts(publicDirectory, records);
    console.log(`[ok] 远端 tag 与 ${records.length} 个公开资产均匹配候选。`);

    const taggedNotes = execFileSync("git", ["-C", options.repoRoot, "show", `${manifest.releaseSha}:desktop/release-notes.md`]);
    if (createHash("sha256").update(taggedNotes).digest("hex") !== manifest.releaseNotes?.sha256) fail("tagged release notes 与候选摘要不一致。 ");

    const appcastPath = path.join(publicDirectory, "appcast.xml");
    const latestAppcastPath = path.join(tempRoot, "latest-appcast.xml");
    await download(`https://github.com/${options.repository}/releases/latest/download/appcast.xml`, latestAppcastPath);
    if (await sha256(appcastPath) !== await sha256(latestAppcastPath)) fail("releases/latest appcast.xml 不是当前公开资产。 ");
    const appcast = await readFile(latestAppcastPath);
    const appcastText = appcast.toString("utf8");
    const previousDmg = selectPreviousSparkleDmg(appcastText, version, options.repository);
    const previousDirectory = path.join(tempRoot, "previous");
    execFileSync("gh", ["release", "download", previousDmg.tag, "--repo", options.repository, "--pattern", previousDmg.name, "--dir", previousDirectory], { stdio: "inherit" });
    const publicKey = extractMountedPublicKey(path.join(previousDirectory, previousDmg.name));
    assertSparkleAppcastSignature(appcast, publicKey);
    const enclosures = extractCurrentSparkleEnclosures(appcastText, version, options.repository, tag);
    for (const enclosure of enclosures) {
      const contents = await readFile(path.join(publicDirectory, enclosure.name));
      if (contents.length !== enclosure.length) fail(`Sparkle enclosure 长度与公开资产不一致：${enclosure.name}`);
      assertSparkleEnclosureSignature(contents, enclosure.signature, publicKey);
    }
    assertRequiredDelta(appcastText, version, previousDmgAssets(appcastText, version, options.repository));
    console.log(`[ok] latest appcast feed 与当前版本的 ${enclosures.length} 个 enclosure 签名有效。`);

    const skillRecord = records.find((record) => record.name === `crawl-mentors-to-xlsx-v${version}.zip`);
    if (!skillRecord) fail("候选报告缺少 repository Skill ZIP。 ");
    await verifySkillZip({ repoRoot: options.repoRoot, releaseSha: manifest.releaseSha, version, publicDirectory, tempRoot, expectedRecord: skillRecord });
    console.log("[ok] 公开 Skill ZIP 与 tagged canonical Skill 的确定性打包结果一致。 ");

    const websiteVerified = await verifyWebsiteIfChanged(options.repoRoot, manifest.releaseSha, options.repository);
    console.log(websiteVerified ? "[ok] website 最新变更提交已部署，公开页面可访问。" : "[ok] 本次 release range 未修改 website。 ");
    console.log(`[ok] ${tag} 发布后验收全部通过；临时文件将自动清理。`);
  } finally {
    await rm(tempRoot, { recursive: true, force: true });
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
