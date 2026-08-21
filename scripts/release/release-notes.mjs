import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export function buildReleaseNotes(version) {
  const normalizedVersion = normalizeVersion(version);
  const windowsInstallerName = `AutoEmailSender-Setup-${normalizedVersion}.exe`;
  const macAppleSiliconInstallerName = `AutoEmailSender-${normalizedVersion}-arm64.dmg`;

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
    `- Windows：下载 \`${windowsInstallerName}\` 后双击安装。`,
    `- macOS Apple Silicon：下载 \`${macAppleSiliconInstallerName}\`，打开后把应用拖到“应用程序”。`,
    "- macOS 版本尚未通过 Apple 官方认证，首次打开可能会被系统拦截；请到“系统设置 > 隐私与安全性”点击“仍要打开”，再确认打开。",
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
