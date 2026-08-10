#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { normalizePrereleaseContract } from "./prerelease-contract.mjs";

const REQUIRED_NOTE_FRAGMENTS = [
  "## 测试版说明",
  "### 新增功能",
  "### 体验优化",
  "### 问题修复",
  "## 测试重点",
  "## 安装前备份",
  "## 安装与覆盖升级",
  "## 回退与诊断",
  "## 自动更新隔离",
  "## 停止使用与取代",
  "不会自动上传",
  "单进程兼容模式",
  "不会自动重发",
  "不发布稳定通道使用的 `latest.yml`、`appcast.xml`",
];

function readJson(contents, label) {
  try {
    return JSON.parse(contents);
  } catch (error) {
    throw new Error(`${label} 不是有效 JSON：${error.message}`);
  }
}

function extractCliVersion(pyproject) {
  let inProject = false;
  for (const line of pyproject.split(/\r?\n/u)) {
    const section = /^\[([^\]]+)\]\s*$/u.exec(line)?.[1];
    if (section !== undefined) {
      inProject = section === "project";
      continue;
    }
    const version = inProject ? /^version\s*=\s*"([^"]+)"\s*$/u.exec(line)?.[1] : undefined;
    if (version) return version;
  }
  throw new Error("cli/pyproject.toml 缺少 [project].version。");
}

function assertVersion(label, actual, expected) {
  if (actual !== expected) {
    throw new Error(`${label} 版本为 ${actual ?? "<missing>"}，预期为 ${expected}。`);
  }
}

function assertPrereleaseNote(note, contract, label) {
  const expectedTitle = `# ${contract.releaseTag}`;
  if (!note.startsWith(expectedTitle)) {
    throw new Error(`${label} 必须以 ${expectedTitle} 开头。`);
  }
  let previousIndex = -1;
  for (const fragment of REQUIRED_NOTE_FRAGMENTS) {
    const index = note.indexOf(fragment);
    if (index < 0) throw new Error(`${label} 缺少测试版公告合同：${fragment}`);
    if (fragment.startsWith("#") && index <= previousIndex) {
      throw new Error(`${label} 的标题顺序错误：${fragment}`);
    }
    if (fragment.startsWith("#")) previousIndex = index;
  }
  if (/待根据|待列出|TODO|TBD/iu.test(note)) {
    throw new Error(`${label} 仍包含未完成占位文本。`);
  }
  const windowsName = `AutoEmailSender-Setup-${contract.version}.exe`;
  const macosName = `AutoEmailSender-${contract.version}-arm64.dmg`;
  if (!note.includes(windowsName) || !note.includes(macosName)) {
    throw new Error(`${label} 缺少精确候选安装包名称。`);
  }
}

export async function assertPrereleasePreflight({
  version,
  channel,
  sourceBranch,
  releaseSha,
  repoRoot,
}) {
  const contract = normalizePrereleaseContract({ version, channel, sourceBranch, releaseSha });
  const paths = {
    cli: path.join(repoRoot, "cli", "pyproject.toml"),
    desktopPackage: path.join(repoRoot, "desktop", "package.json"),
    desktopLock: path.join(repoRoot, "desktop", "package-lock.json"),
    frontendPackage: path.join(repoRoot, "frontend", "package.json"),
    frontendLock: path.join(repoRoot, "frontend", "package-lock.json"),
    curatedNote: path.join(repoRoot, "docs", "releases", `${contract.releaseTag}.md`),
    desktopNote: path.join(repoRoot, "desktop", "release-notes.md"),
    builderConfig: path.join(repoRoot, "desktop", "electron-builder.yml"),
  };
  const [
    cliToml,
    desktopPackageText,
    desktopLockText,
    frontendPackageText,
    frontendLockText,
    curatedNote,
    desktopNote,
    builderConfig,
  ] = await Promise.all(Object.values(paths).map((filePath) => readFile(filePath, "utf8")));

  const desktopPackage = readJson(desktopPackageText, "desktop/package.json");
  const desktopLock = readJson(desktopLockText, "desktop/package-lock.json");
  const frontendPackage = readJson(frontendPackageText, "frontend/package.json");
  const frontendLock = readJson(frontendLockText, "frontend/package-lock.json");
  assertVersion("cli/pyproject.toml", extractCliVersion(cliToml), contract.version);
  assertVersion("desktop/package.json", desktopPackage.version, contract.version);
  assertVersion("desktop/package-lock.json", desktopLock.version, contract.version);
  assertVersion("desktop/package-lock.json packages['']", desktopLock.packages?.[""]?.version, contract.version);
  assertVersion("frontend/package.json", frontendPackage.version, contract.version);
  assertVersion("frontend/package-lock.json", frontendLock.version, contract.version);
  assertVersion("frontend/package-lock.json packages['']", frontendLock.packages?.[""]?.version, contract.version);
  assertPrereleaseNote(curatedNote, contract, `docs/releases/${contract.releaseTag}.md`);
  if (desktopNote !== curatedNote) {
    throw new Error("desktop/release-notes.md 与测试版公告不一致。");
  }
  if (
    !builderConfig.includes("releases/latest/download/appcast.xml")
    || !/^\s*releaseType:\s*release\s*$/mu.test(builderConfig)
  ) {
    throw new Error("稳定版更新入口合同已改变；prerelease 不得改写稳定 feed。");
  }

  const actualSha = execFileSync("git", ["-C", repoRoot, "rev-parse", "HEAD"], {
    encoding: "utf8",
  }).trim().toLowerCase();
  if (actualSha !== contract.releaseSha) {
    throw new Error(`当前提交 ${actualSha} 与显式 release_sha ${contract.releaseSha} 不一致。`);
  }
  return contract;
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
  for (const name of ["version", "channel", "source_branch", "release_sha", "repo_root"]) {
    if (!options[name]) throw new Error(`缺少 --${name.replaceAll("_", "-")}。`);
  }
  return options;
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const contract = await assertPrereleasePreflight({
    version: options.version,
    channel: options.channel,
    sourceBranch: options.source_branch,
    releaseSha: options.release_sha,
    repoRoot: path.resolve(options.repo_root),
  });
  console.log(`[ok] ${contract.releaseTag} 测试版元数据、公告、更新隔离和候选 SHA 一致。`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
