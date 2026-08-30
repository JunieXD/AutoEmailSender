#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const SCHEMA_VERSION = 1;
const EVIDENCE_KIND = "auto-email-sender-quality-evidence";
const DEFAULT_MAX_AGE_MS = 24 * 60 * 60 * 1000;
const KNOWN_SUITES = new Set(["backend", "cli", "desktop", "frontend", "website"]);

function commandOutput(command, args) {
  return execFileSync(command, args, { encoding: "utf8" }).trim();
}

export function validateQualityEvidence(
  evidence,
  { gitSha, toolchain, now = Date.now(), maxAgeMs = DEFAULT_MAX_AGE_MS },
) {
  if (evidence?.schemaVersion !== SCHEMA_VERSION || evidence?.kind !== EVIDENCE_KIND) {
    throw new Error("全仓质量证据格式无效。");
  }
  if (evidence.gitSha !== gitSha) {
    throw new Error(`全仓质量证据属于 ${evidence.gitSha ?? "<missing>"}，当前 SHA 是 ${gitSha}。`);
  }
  const generatedAt = Date.parse(evidence.generatedAt);
  if (!Number.isFinite(generatedAt) || generatedAt > now || now - generatedAt > maxAgeMs) {
    throw new Error("全仓质量证据已过期或生成时间无效，请重新运行全仓门禁。");
  }
  for (const [name, expected] of Object.entries(toolchain)) {
    if (evidence.toolchain?.[name] !== expected) {
      throw new Error(`全仓质量证据的 ${name} 工具链不匹配。`);
    }
  }
  if (!Array.isArray(evidence.passedSuites)) {
    throw new Error("全仓质量证据缺少 passedSuites。");
  }
  const suites = [...new Set(evidence.passedSuites)];
  if (suites.some((suite) => !KNOWN_SUITES.has(suite))) {
    throw new Error("全仓质量证据包含未知 suite。");
  }
  return suites.sort();
}

function parseArguments(argv) {
  const options = { evidencePath: "", repoRoot: process.cwd(), maxAgeHours: 24 };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    const value = argv[index + 1];
    if (!value) throw new Error(`缺少 ${argument} 的值。`);
    index += 1;
    if (argument === "--evidence") options.evidencePath = path.resolve(value);
    else if (argument === "--repo-root") options.repoRoot = path.resolve(value);
    else if (argument === "--max-age-hours") options.maxAgeHours = Number(value);
    else throw new Error(`未知参数：${argument}`);
  }
  if (!options.evidencePath || !Number.isFinite(options.maxAgeHours) || options.maxAgeHours <= 0) {
    throw new Error("用法: quality-evidence.mjs --evidence <path> --repo-root <path> [--max-age-hours <hours>]");
  }
  return options;
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const evidence = JSON.parse(await readFile(options.evidencePath, "utf8"));
  const suites = validateQualityEvidence(evidence, {
    gitSha: commandOutput("git", ["-C", options.repoRoot, "rev-parse", "HEAD"]),
    toolchain: {
      node: commandOutput("node", ["--version"]),
      npm: commandOutput("npm", ["--version"]),
      python: commandOutput("uv", ["run", "--project", path.join(options.repoRoot, "backend"), "--no-sync", "python", "--version"]),
      uv: commandOutput("uv", ["--version"]),
    },
    maxAgeMs: options.maxAgeHours * 60 * 60 * 1000,
  });
  console.log(suites.join("\n"));
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
