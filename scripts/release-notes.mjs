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
    "- macOS 首次打开若提示无法验证开发者，到“系统设置 > 隐私与安全性”点击“仍要打开”，再确认打开。",
    "- Intel Mac 暂未提供安装包。",
    "- 请只从本项目 GitHub Releases 页面下载安装包。",
    "",
    "## 自动更新",
    "",
    "- Windows：应用内可下载并安装更新。",
    "- macOS Apple Silicon：应用会自动检查更新，也可点击“检查更新”；确认后由 Sparkle 下载并重启安装。",
    "- 如果当前 macOS 旧版本仍打开 GitHub Releases，请手动覆盖安装本版本一次；之后即可使用应用内更新。",
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
