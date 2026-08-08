#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { parseVersion } from "./check-release-version.mjs";

function parseArguments(argv) {
  const options = { version: "", repoRoot: "", releaseSha: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    const value = argv[index + 1];
    if (!argument.startsWith("--") || value === undefined) {
      throw new Error(`无法解析参数：${argument}`);
    }
    index += 1;
    if (argument === "--version") options.version = value;
    else if (argument === "--repo-root") options.repoRoot = path.resolve(value);
    else if (argument === "--release-sha") options.releaseSha = value;
    else throw new Error(`未知参数：${argument}`);
  }
  if (!options.version || !options.repoRoot) {
    throw new Error("必须提供 --version 和 --repo-root。 ");
  }
  return options;
}

function readJson(contents, label) {
  try {
    return JSON.parse(contents);
  } catch (error) {
    throw new Error(`${label} 不是有效 JSON：${error.message}`);
  }
}

function extractCliVersion(pyproject) {
  let inProject = false;
  for (const line of pyproject.split(/\r?\n/)) {
    const section = /^\[([^\]]+)\]\s*$/.exec(line)?.[1];
    if (section !== undefined) {
      inProject = section === "project";
      continue;
    }
    const version = inProject ? /^version\s*=\s*"([^"]+)"\s*$/.exec(line)?.[1] : undefined;
    if (version) return version;
  }
  throw new Error("cli/pyproject.toml 缺少 [project].version。 ");
}

function assertVersion(label, actual, expected) {
  if (actual !== expected) {
    throw new Error(`${label} 版本为 ${actual ?? "<missing>"}，预期为 ${expected}。`);
  }
}

function assertReleaseNote(note, tag, label) {
  const requiredHeadings = [
    `# ${tag}`,
    "### 新增功能",
    "### 体验优化",
    "### 问题修复",
    "## 安装说明",
    "## 自动更新",
    "## 导师抓取 Skill",
  ];
  let previousIndex = -1;
  for (const heading of requiredHeadings) {
    const currentIndex = note.indexOf(heading);
    if (currentIndex < 0) throw new Error(`${label} 缺少标题：${heading}`);
    if (currentIndex <= previousIndex) throw new Error(`${label} 的标题顺序不正确：${heading}`);
    previousIndex = currentIndex;
  }
}

export async function assertReleasePreflight({ version, repoRoot, releaseSha = "" }) {
  parseVersion(version);
  const tag = `v${version}`;
  const paths = {
    cli: path.join(repoRoot, "cli", "pyproject.toml"),
    desktopPackage: path.join(repoRoot, "desktop", "package.json"),
    desktopLock: path.join(repoRoot, "desktop", "package-lock.json"),
    frontendPackage: path.join(repoRoot, "frontend", "package.json"),
    frontendLock: path.join(repoRoot, "frontend", "package-lock.json"),
    curatedNote: path.join(repoRoot, "docs", "releases", `${tag}.md`),
    desktopNote: path.join(repoRoot, "desktop", "release-notes.md"),
  };
  const [cliToml, desktopPackageText, desktopLockText, frontendPackageText, frontendLockText, curatedNote, desktopNote] =
    await Promise.all(Object.values(paths).map((filePath) => readFile(filePath, "utf8")));

  const desktopPackage = readJson(desktopPackageText, "desktop/package.json");
  const desktopLock = readJson(desktopLockText, "desktop/package-lock.json");
  const frontendPackage = readJson(frontendPackageText, "frontend/package.json");
  const frontendLock = readJson(frontendLockText, "frontend/package-lock.json");
  assertVersion("cli/pyproject.toml", extractCliVersion(cliToml), version);
  assertVersion("desktop/package.json", desktopPackage.version, version);
  assertVersion("desktop/package-lock.json", desktopLock.version, version);
  assertVersion("desktop/package-lock.json packages['']", desktopLock.packages?.[""]?.version, version);
  assertVersion("frontend/package.json", frontendPackage.version, version);
  assertVersion("frontend/package-lock.json", frontendLock.version, version);
  assertVersion("frontend/package-lock.json packages['']", frontendLock.packages?.[""]?.version, version);
  assertReleaseNote(curatedNote, tag, `docs/releases/${tag}.md`);
  if (desktopNote !== curatedNote) {
    throw new Error("desktop/release-notes.md 与版本化发布公告不一致。 ");
  }

  if (releaseSha) {
    const actualSha = execFileSync("git", ["-C", repoRoot, "rev-parse", "HEAD"], {
      encoding: "utf8",
    }).trim();
    if (actualSha !== releaseSha) {
      throw new Error(`当前提交 ${actualSha} 与候选提交 ${releaseSha} 不一致。`);
    }
  }
  return { tag, version };
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const result = await assertReleasePreflight(options);
  console.log(`[ok] ${result.tag} 版本、lockfile、公告和候选提交一致。`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
