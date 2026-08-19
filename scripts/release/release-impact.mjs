#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import path from "node:path";
import { pathToFileURL } from "node:url";

const CHECKS = {
  "release-metadata": "校验版本号、release note、appcast URL 与资产名，不重建安装包",
  "release-contracts": "运行发布编排、候选报告和平台无关 release 脚本测试",
  "windows-release-contracts": "在 Windows 运行 PowerShell prepare/release 入口契约测试",
  "cli-suite": "运行 CLI 测试",
  "cli-frozen-build": "重建并验证 CLI 冻结包",
  "backend-suite": "运行后端测试",
  "backend-frozen-build": "重建并验证后端冻结包",
  "frontend-suite-build": "运行前端测试、lint 和生产构建",
  "desktop-suite": "运行 Electron typecheck 和测试",
  "website-suite-build": "运行网站测试和构建",
  "repository-skill-contracts": "验证并打包仓库交付的 Skill",
  "windows-quick-qa": "运行 Windows VM quick QA，不构建 NSIS、不跑安装生命周期",
  "windows-formal-qa": "对冻结候选运行一次 Windows VM 正式 NSIS 与生命周期验收",
  "macos-sparkle-candidate": "构建并认证 macOS DMG、Sparkle appcast 和 delta",
};

const RELEASE_NOTE_PATTERN = /^(?:docs\/releases\/v[^/]+\.md|desktop\/release-notes\.md)$/;
const CLI_BUILD_TOOLING_PATTERNS = [
  /^scripts\/build-cli\.(?:ps1|sh)$/,
  /^scripts\/build\/build-cli\.(?:ps1|sh)$/,
  /^scripts\/build\/(?:generate_cli_build_identity|verify_cli_binary)\.py$/,
  /^scripts\/quality\/benchmark_agent_cli\.py$/,
];
const WINDOWS_PACKAGING_PATTERNS = [
  /^desktop\/electron-builder\.yml$/,
  /^desktop\/build\//,
  /^scripts\/build\/.*windows.*\.ps1$/i,
  /^scripts\/build\/(?:build-backend|build-cli)\.ps1$/,
];
const MACOS_PACKAGING_PATTERNS = [
  /^desktop\/native\/sparkle\//,
  /^scripts\/build\/setup-sparkle\.sh$/,
  /^scripts\/packaging\/(?:configure-sparkle-info|sanitize-macos-bundle)(?:\.test)?\.mjs$/,
  /^scripts\/release\/prepare-sparkle-release(?:\.test)?\.mjs$/,
];

function matchesAny(file, patterns) {
  return patterns.some((pattern) => pattern.test(file));
}

function isTestOnly(file, root) {
  return file.startsWith(`${root}/test/`) || /(?:^|\/)test_[^/]+\.py$/.test(file);
}

export function planReleaseImpact(changedFiles, { candidate = false } = {}) {
  const files = [...new Set(changedFiles.map((file) => file.replaceAll("\\", "/")))]
    .filter(Boolean)
    .sort();
  const categories = new Set();
  const required = new Set();

  if (files.length === 0) {
    categories.add("no-changes");
  } else if (files.every((file) => RELEASE_NOTE_PATTERN.test(file))) {
    categories.add("release-note-only");
    required.add("release-metadata");
  } else {
    for (const file of files) {
      if (RELEASE_NOTE_PATTERN.test(file)) {
        categories.add("release-metadata");
        required.add("release-metadata");
      }

      if (file.startsWith("cli/")) {
        categories.add(isTestOnly(file, "cli") ? "cli-tests" : "cli-product");
        required.add("cli-suite");
        if (!isTestOnly(file, "cli")) {
          required.add("cli-frozen-build");
          required.add("windows-quick-qa");
        }
      }

      if (matchesAny(file, CLI_BUILD_TOOLING_PATTERNS)) {
        categories.add("cli-product");
        required.add("cli-suite");
        required.add("cli-frozen-build");
        required.add("windows-quick-qa");
      }

      if (file.startsWith("backend/")) {
        categories.add(isTestOnly(file, "backend") ? "backend-tests" : "backend-product");
        required.add("backend-suite");
        if (!isTestOnly(file, "backend")) {
          required.add("backend-frozen-build");
          required.add("windows-quick-qa");
        }
      }

      if (file.startsWith("frontend/")) {
        categories.add("frontend");
        required.add("frontend-suite-build");
        required.add("desktop-suite");
        required.add("windows-quick-qa");
      }

      if (file.startsWith("desktop/")) {
        categories.add("desktop");
        required.add("desktop-suite");
        if (!matchesAny(file, MACOS_PACKAGING_PATTERNS)) {
          required.add("windows-quick-qa");
        }
      }

      if (file.startsWith("website/")) {
        categories.add("website");
        required.add("website-suite-build");
      }

      if (/^(?:\.agents|\.claude)\/skills\//.test(file)) {
        categories.add("repository-skill");
        required.add("repository-skill-contracts");
      }

      if (
        file === ".github/workflows/release.yml" ||
        file.startsWith("scripts/release/") ||
        file.startsWith(".codex/skills/auto-email-sender-release/") ||
        /^scripts\/quality\/run-windows-(?:vm-)?release-qa\.(?:ps1|sh)$/.test(file)
      ) {
        categories.add("release-orchestration");
        required.add("release-contracts");
        required.add("windows-release-contracts");
        if (file.startsWith("scripts/quality/run-windows-")) {
          required.add("windows-quick-qa");
        }
      }

      if (matchesAny(file, WINDOWS_PACKAGING_PATTERNS)) {
        categories.add("windows-packaging");
        required.delete("windows-quick-qa");
        required.add("windows-formal-qa");
      }

      if (matchesAny(file, MACOS_PACKAGING_PATTERNS)) {
        categories.add("macos-sparkle-packaging");
        required.add("macos-sparkle-candidate");
      }

      if (file.startsWith("docs/") && !RELEASE_NOTE_PATTERN.test(file)) {
        categories.add("docs");
      }
    }
  }

  if (candidate) {
    categories.add("frozen-release-candidate");
    required.delete("windows-quick-qa");
    required.add("release-metadata");
    required.add("release-contracts");
    required.add("windows-release-contracts");
    required.add("windows-formal-qa");
    required.add("macos-sparkle-candidate");
  }

  const orderedRequired = Object.keys(CHECKS).filter((check) => required.has(check));
  return {
    candidate,
    changedFiles: files,
    categories: [...categories].sort(),
    required: orderedRequired.map((id) => ({ id, description: CHECKS[id] })),
    expensiveChecksSkipped: ["windows-formal-qa", "macos-sparkle-candidate"]
      .filter((id) => !required.has(id))
      .map((id) => ({ id, description: CHECKS[id] })),
  };
}

export function changedFilesBetween({ repoRoot, base, head }) {
  const result = spawnSync(
    "git",
    ["-C", repoRoot, "diff", "--name-only", "--diff-filter=ACDMRTUXB", "-z", base, head, "--"],
    { encoding: "utf8" },
  );
  if (result.status !== 0) {
    throw new Error(result.stderr.trim() || `无法读取 ${base}..${head} 的变更。`);
  }
  return result.stdout.split("\0").filter(Boolean);
}

function parseArguments(argv) {
  const options = { repoRoot: process.cwd(), base: "", head: "HEAD", json: false, candidate: false };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--json") options.json = true;
    else if (argument === "--candidate") options.candidate = true;
    else if (["--repo-root", "--base", "--head"].includes(argument)) {
      const value = argv[index += 1];
      if (!value) throw new Error(`缺少 ${argument} 的值。`);
      if (argument === "--repo-root") options.repoRoot = path.resolve(value);
      else if (argument === "--base") options.base = value;
      else options.head = value;
    } else {
      throw new Error(`未知参数：${argument}`);
    }
  }
  if (!options.base) {
    throw new Error("用法: release-impact.mjs --base <ref> [--head <ref>] [--candidate] [--json]");
  }
  return options;
}

function printHuman(plan, base, head) {
  console.log(`变更范围：${base}..${head}`);
  console.log(`分类：${plan.categories.join(", ") || "none"}`);
  if (plan.required.length === 0) {
    console.log("必须重跑：无");
  } else {
    console.log("必须重跑：");
    for (const check of plan.required) console.log(`- ${check.id}: ${check.description}`);
  }
  if (plan.expensiveChecksSkipped.length > 0) {
    console.log("本次可跳过的昂贵检查：");
    for (const check of plan.expensiveChecksSkipped) console.log(`- ${check.id}`);
  }
}

function main() {
  try {
    const options = parseArguments(process.argv.slice(2));
    const changedFiles = changedFilesBetween(options);
    const plan = planReleaseImpact(changedFiles, { candidate: options.candidate });
    if (options.json) console.log(JSON.stringify({ base: options.base, head: options.head, ...plan }, null, 2));
    else printHuman(plan, options.base, options.head);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) main();
