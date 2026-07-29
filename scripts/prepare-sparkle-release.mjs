import { spawnSync } from "node:child_process";
import { createPrivateKey, createPublicKey } from "node:crypto";
import { access, copyFile, mkdtemp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { validateSparklePublicKey } from "./configure-sparkle-info.mjs";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const defaultRepoRoot = path.resolve(scriptDirectory, "..");
const DEFAULT_REPOSITORY = "JunieXD/AutoEmailSender";
const MAXIMUM_DELTAS = 3;
const ED25519_PKCS8_SEED_PREFIX = Buffer.from("302e020100300506032b657004220420", "hex");

export function normalizeReleaseTag(tag) {
  const match = /^v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$/.exec(tag.trim());
  if (match === null) {
    throw new Error(`无效的发布标签：${tag}`);
  }
  return { tag: `v${match[1]}`, version: match[1] };
}

export function getMacDmgName(version) {
  return `AutoEmailSender-${version}-arm64.dmg`;
}

export function deriveSparklePublicKey(privateKey) {
  const normalizedPrivateKey = privateKey.trim();
  const privateSeed = Buffer.from(normalizedPrivateKey, "base64");
  if (privateSeed.length !== 32 || privateSeed.toString("base64") !== normalizedPrivateKey) {
    throw new Error("SPARKLE_ED_PRIVATE_KEY 必须是 Sparkle 导出的 32 字节 Base64 私钥种子。");
  }

  const privateKeyObject = createPrivateKey({
    key: Buffer.concat([ED25519_PKCS8_SEED_PREFIX, privateSeed]),
    format: "der",
    type: "pkcs8",
  });
  const publicKeyDer = createPublicKey(privateKeyObject).export({ format: "der", type: "spki" });
  return publicKeyDer.subarray(-32).toString("base64");
}

export function extractPreviousDmgAssets(appcast, repository, maximum = MAXIMUM_DELTAS) {
  const itemBlocks = appcast.match(/<item\b[\s\S]*?<\/item>/gi) ?? [];
  const assets = [];
  const seen = new Set();

  for (const itemBlock of itemBlocks) {
    const withoutDeltas = itemBlock.replace(
      /<sparkle:deltas\b[\s\S]*?<\/sparkle:deltas>/gi,
      "",
    );
    const enclosure = /<enclosure\b[^>]*\burl=(?:"([^"]+)"|'([^']+)')[^>]*>/i.exec(
      withoutDeltas,
    );
    if (enclosure === null) {
      continue;
    }

    const asset = parseGitHubReleaseAssetUrl(decodeXmlAttribute(enclosure[1] ?? enclosure[2]), repository);
    if (asset === null || !asset.name.toLowerCase().endsWith(".dmg")) {
      continue;
    }

    const key = `${asset.tag}\0${asset.name}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    assets.push(asset);
    if (assets.length >= maximum) {
      break;
    }
  }

  return assets;
}

function decodeXmlAttribute(value) {
  return value
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&amp;", "&");
}

function parseGitHubReleaseAssetUrl(value, repository) {
  let url;
  try {
    url = new URL(value);
  } catch {
    return null;
  }

  if (url.protocol !== "https:" || url.hostname.toLowerCase() !== "github.com") {
    return null;
  }

  const [owner, repo] = repository.split("/");
  if (!owner || !repo) {
    throw new Error(`无效的 GitHub 仓库名：${repository}`);
  }
  const match = /^\/([^/]+)\/([^/]+)\/releases\/download\/([^/]+)\/(.+)$/.exec(url.pathname);
  if (
    match === null ||
    match[1].toLowerCase() !== owner.toLowerCase() ||
    match[2].toLowerCase() !== repo.toLowerCase()
  ) {
    return null;
  }

  return {
    tag: decodeURIComponent(match[3]),
    name: decodeURIComponent(match[4]),
  };
}

function parseArguments(argv) {
  const options = {
    repoRoot: defaultRepoRoot,
    repository: process.env.GITHUB_REPOSITORY ?? DEFAULT_REPOSITORY,
    releaseTag: process.env.GITHUB_REF_NAME ?? "",
    releaseDirectory: "",
    releaseNotesPath: "",
    outputDirectory: "",
    generateAppcastPath: "",
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    const value = argv[index + 1];
    if (!argument.startsWith("--") || value === undefined) {
      throw new Error(`无法解析参数：${argument}`);
    }
    index += 1;
    switch (argument) {
      case "--repo-root":
        options.repoRoot = path.resolve(value);
        break;
      case "--repository":
        options.repository = value;
        break;
      case "--release-tag":
        options.releaseTag = value;
        break;
      case "--release-dir":
        options.releaseDirectory = path.resolve(value);
        break;
      case "--release-notes":
        options.releaseNotesPath = path.resolve(value);
        break;
      case "--output-dir":
        options.outputDirectory = path.resolve(value);
        break;
      case "--generate-appcast":
        options.generateAppcastPath = path.resolve(value);
        break;
      default:
        throw new Error(`未知参数：${argument}`);
    }
  }

  options.releaseDirectory ||= path.join(options.repoRoot, "desktop", "release");
  options.releaseNotesPath ||= path.join(options.repoRoot, "desktop", "release-notes.md");
  options.outputDirectory ||= path.join(options.releaseDirectory, "sparkle-publish");
  options.generateAppcastPath ||= path.join(
    options.repoRoot,
    "desktop",
    "native",
    "sparkle",
    "vendor",
    "bin",
    "generate_appcast",
  );
  return options;
}

function childEnvironmentWithoutPrivateKey() {
  const environment = { ...process.env };
  delete environment.SPARKLE_ED_PRIVATE_KEY;
  return environment;
}

function runCaptured(command, args) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    env: childEnvironmentWithoutPrivateKey(),
    maxBuffer: 10 * 1024 * 1024,
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    const detail = result.stderr.trim() || result.stdout.trim();
    throw new Error(`${command} 执行失败${detail ? `：${detail}` : ""}`);
  }
  return result.stdout;
}

function runInherited(command, args, input) {
  const result = spawnSync(command, args, {
    env: childEnvironmentWithoutPrivateKey(),
    input,
    stdio: ["pipe", "inherit", "inherit"],
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`${command} 执行失败，退出码 ${result.status}。`);
  }
}

async function assertEmptyOutputDirectory(outputDirectory) {
  await mkdir(outputDirectory, { recursive: true });
  const entries = await readdir(outputDirectory);
  if (entries.length > 0) {
    throw new Error(`Sparkle 发布输出目录必须为空：${outputDirectory}`);
  }
}

async function downloadPreviousUpdates(workDirectory, repository) {
  const latestRelease = JSON.parse(
    runCaptured("gh", ["release", "view", "--repo", repository, "--json", "tagName,assets"]),
  );
  const hasAppcast = latestRelease.assets?.some((asset) => asset.name === "appcast.xml");
  if (!hasAppcast) {
    console.log("上一版 Release 没有 appcast.xml，本次将生成首个 Sparkle feed。 ");
    return 0;
  }

  runInherited("gh", [
    "release",
    "download",
    latestRelease.tagName,
    "--repo",
    repository,
    "--pattern",
    "appcast.xml",
    "--dir",
    workDirectory,
  ]);

  const appcastPath = path.join(workDirectory, "appcast.xml");
  const previousAssets = extractPreviousDmgAssets(
    await readFile(appcastPath, "utf8"),
    repository,
  );
  for (const asset of previousAssets) {
    runInherited("gh", [
      "release",
      "download",
      asset.tag,
      "--repo",
      repository,
      "--pattern",
      asset.name,
      "--dir",
      workDirectory,
      "--skip-existing",
    ]);
  }
  return previousAssets.length;
}

async function prepareSparkleRelease(options) {
  const { tag, version } = normalizeReleaseTag(options.releaseTag);
  const privateKey = process.env.SPARKLE_ED_PRIVATE_KEY?.trim();
  if (!privateKey) {
    throw new Error("缺少 SPARKLE_ED_PRIVATE_KEY，无法签名 Sparkle 更新。 ");
  }
  const configuredPublicKey = process.env.SPARKLE_PUBLIC_ED_KEY;
  if (!configuredPublicKey) {
    throw new Error("缺少 SPARKLE_PUBLIC_ED_KEY，无法核对 Sparkle 签名密钥。 ");
  }
  if (deriveSparklePublicKey(privateKey) !== validateSparklePublicKey(configuredPublicKey)) {
    throw new Error("SPARKLE_ED_PRIVATE_KEY 与 SPARKLE_PUBLIC_ED_KEY 不匹配。 ");
  }

  const currentDmgName = getMacDmgName(version);
  const currentDmgPath = path.join(options.releaseDirectory, currentDmgName);
  await Promise.all([
    access(currentDmgPath),
    access(options.releaseNotesPath),
    access(options.generateAppcastPath),
  ]);
  await assertEmptyOutputDirectory(options.outputDirectory);

  const workDirectory = await mkdtemp(path.join(os.tmpdir(), "auto-email-sender-appcast-"));
  try {
    await copyFile(currentDmgPath, path.join(workDirectory, currentDmgName));
    await copyFile(
      options.releaseNotesPath,
      path.join(workDirectory, currentDmgName.replace(/\.dmg$/i, ".md")),
    );

    const previousUpdateCount = await downloadPreviousUpdates(
      workDirectory,
      options.repository,
    );
    const downloadUrlPrefix = `https://github.com/${options.repository}/releases/download/${tag}/`;
    runInherited(
      options.generateAppcastPath,
      [
        "--ed-key-file",
        "-",
        "--maximum-versions",
        String(MAXIMUM_DELTAS + 1),
        "--maximum-deltas",
        String(MAXIMUM_DELTAS),
        "--download-url-prefix",
        downloadUrlPrefix,
        "--link",
        `https://github.com/${options.repository}/releases/tag/${tag}`,
        "--embed-release-notes",
        workDirectory,
      ],
      `${privateKey}\n`,
    );

    const generatedAppcastPath = path.join(workDirectory, "appcast.xml");
    const generatedAppcast = await readFile(generatedAppcastPath, "utf8");
    const escapedVersion = version.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (!new RegExp(`<sparkle:version>\\s*${escapedVersion}\\s*</sparkle:version>`).test(generatedAppcast)) {
      throw new Error(`生成的 appcast.xml 不包含当前版本 ${version}。`);
    }

    await copyFile(currentDmgPath, path.join(options.outputDirectory, currentDmgName));
    await writeFile(
      path.join(options.outputDirectory, "appcast.xml"),
      generatedAppcast,
      "utf8",
    );

    const deltaFiles = (await readdir(workDirectory))
      .filter((name) => name.toLowerCase().endsWith(".delta"))
      .sort();
    for (const deltaFile of deltaFiles) {
      await copyFile(
        path.join(workDirectory, deltaFile),
        path.join(options.outputDirectory, deltaFile),
      );
    }

    console.log(
      `Sparkle 发布产物已准备完成：读取 ${previousUpdateCount} 个旧版本，生成 ${deltaFiles.length} 个差分包。`,
    );
  } finally {
    await rm(workDirectory, { recursive: true, force: true });
  }
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  if (!options.releaseTag) {
    throw new Error("必须通过 --release-tag 或 GITHUB_REF_NAME 指定发布标签。 ");
  }
  await prepareSparkleRelease(options);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
