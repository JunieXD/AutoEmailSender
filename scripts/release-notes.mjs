import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export function buildReleaseNotes(version) {
  const normalizedVersion = normalizeVersion(version);
  const installerName = `AutoEmailSender Setup ${normalizedVersion}.exe`;

  return [
    `# ${version}`,
    "",
    "## 更新内容",
    "",
    "### 新增功能",
    "",
    "### 体验优化",
    "",
    "### 问题修复",
    "",
    "## 安装说明",
    "",
    `- 普通用户只需下载 \`${installerName}\`。`,
    "",
    "## 自动更新",
    "",
    "- 应用内会自动检查更新。",
    "- 发现新版本后，可以选择增量下载或全量下载。",
    "",
  ].join("\n");
}

export function generateReleaseNotes({
  version,
  outputPath,
}) {
  const currentTag = normalizeTag(version);
  const releaseNotes = buildReleaseNotes(currentTag);
  const resolvedOutputPath = isAbsolute(outputPath)
    ? outputPath
    : resolve(process.cwd(), outputPath);

  mkdirSync(dirname(resolvedOutputPath), { recursive: true });
  writeFileSync(resolvedOutputPath, releaseNotes, "utf8");
  return releaseNotes;
}

function normalizeTag(version) {
  if (version && version.trim()) {
    return version.startsWith("v") ? version : `v${version.trim()}`;
  }
  throw new Error("无法确定当前版本号，请显式传入版本号。");
}

function normalizeVersion(version) {
  return version.replace(/^v/, "");
}

function main() {
  const { version, outputPath } = parseArgs(process.argv.slice(2));
  generateReleaseNotes({ version, outputPath });
}

function parseArgs(argv) {
  let version = "";
  let outputPath = "release-notes.md";

  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--version") {
      version = argv[++index] ?? version;
      continue;
    }
    if (value === "--output") {
      outputPath = argv[++index] ?? outputPath;
      continue;
    }
    if (value === "--repo-root" || value === "--upper-ref") {
      index += 1;
    }
  }

  return { version, outputPath };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main();
}
