import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createPrivateKey, sign } from "node:crypto";
import {
  chmodSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  assertPublishableSparkleAssets,
  assertRequiredDelta,
  assertSparkleAppcastSignature,
  deriveSparklePublicKey,
  extractCurrentReleaseAssetNames,
  extractDeltaSourceVersions,
  extractPreviousDmgAssets,
  getMacDmgName,
  getMacDmgVersion,
  normalizeGitHubAssetName,
  normalizeReleaseTag,
  rewriteAppcastAssetNames,
  stagePreviousDmgAssets,
} from "./prepare-sparkle-release.mjs";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const ed25519Pkcs8SeedPrefix = Buffer.from("302e020100300506032b657004220420", "hex");
const placeholderSignature = Buffer.alloc(64, 1).toString("base64");

function enclosureAttributes(length = 1) {
  return `length="${length}" sparkle:edSignature="${placeholderSignature}"`;
}

function signAppcastForTest(appcast, privateKey) {
  const contents = Buffer.from(appcast, "utf8");
  const privateKeyObject = createPrivateKey({
    key: Buffer.concat([ed25519Pkcs8SeedPrefix, Buffer.from(privateKey, "base64")]),
    format: "der",
    type: "pkcs8",
  });
  const signature = sign(null, contents, privateKeyObject).toString("base64");
  return Buffer.concat([
    contents,
    Buffer.from(
      `\n<!-- sparkle-signatures:\nedSignature: ${signature}\nlength: ${contents.length}\n-->\n`,
      "utf8",
    ),
  ]);
}

test("normalizes stable release tags and installer names", () => {
  assert.deepEqual(normalizeReleaseTag("v2.4.0"), { tag: "v2.4.0", version: "2.4.0" });
  assert.equal(getMacDmgName("2.4.0"), "AutoEmailSender-2.4.0-arm64.dmg");
  assert.equal(getMacDmgVersion("AutoEmailSender-2.5.3-arm64.dmg"), "2.5.3");
  assert.equal(getMacDmgVersion("other.dmg"), null);
  assert.throws(() => normalizeReleaseTag("latest"), /无效的发布标签/);
});

test("derives the matching Sparkle public key from an exported private seed", () => {
  const privateKey = Buffer.alloc(32, 9).toString("base64");
  const publicKey = deriveSparklePublicKey(privateKey);

  assert.equal(Buffer.from(publicKey, "base64").length, 32);
  assert.throws(() => deriveSparklePublicKey("not-a-private-key"), /私钥种子/);
});

test("rejects an appcast modified after its feed signature was created", () => {
  const privateKey = Buffer.alloc(32, 7).toString("base64");
  const publicKey = deriveSparklePublicKey(privateKey);
  const signedAppcast = signAppcastForTest("<rss>original URL</rss>", privateKey);

  assert.doesNotThrow(() => assertSparkleAppcastSignature(signedAppcast, publicKey));
  assert.throws(
    () =>
      assertSparkleAppcastSignature(
        Buffer.from(signedAppcast.toString("utf8").replace("original URL", "rewritten URL")),
        publicKey,
      ),
    /最终修改后未通过/,
  );
});

test("extracts full DMGs while ignoring nested deltas and foreign URLs", () => {
  const appcast = `
    <rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
      <channel>
        <item>
          <sparkle:version>2.3.9</sparkle:version>
          <enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.3.9/AutoEmailSender-2.3.9-arm64.dmg" ${enclosureAttributes(239)} />
          <sparkle:deltas>
            <enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.3.9/from-2.3.8.delta" />
          </sparkle:deltas>
        </item>
        <item>
          <enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.3.8/AutoEmailSender-2.3.8-arm64.dmg?download=1&amp;source=feed" ${enclosureAttributes(238)} />
        </item>
        <item>
          <enclosure url="https://example.com/JunieXD/AutoEmailSender/releases/download/v2.3.7/foreign.dmg" />
        </item>
      </channel>
    </rss>`;

  assert.deepEqual(extractPreviousDmgAssets(appcast, "JunieXD/AutoEmailSender", 3), [
    {
      tag: "v2.3.9",
      name: "AutoEmailSender-2.3.9-arm64.dmg",
      length: 239,
      signature: placeholderSignature,
    },
    {
      tag: "v2.3.8",
      name: "AutoEmailSender-2.3.8-arm64.dmg",
      length: 238,
      signature: placeholderSignature,
    },
  ]);
});

test("downloads only the latest delta baseline by default", () => {
  const items = [9, 8, 8, 7, 6].map(
    (patch) => `<item><enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.3.${patch}/AutoEmailSender-2.3.${patch}-arm64.dmg" ${enclosureAttributes(patch)} /></item>`,
  );
  const assets = extractPreviousDmgAssets(`<channel>${items.join("")}</channel>`, "JunieXD/AutoEmailSender");

  assert.deepEqual(assets.map(({ tag }) => tag), ["v2.3.9"]);
});

test("recovers historical release tags after Sparkle rewrites retained download URLs", () => {
  const appcast = `
    <channel>
      <item>
        <enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.4.1/AutoEmailSender-2.4.1-arm64.dmg" ${enclosureAttributes(241)} />
      </item>
      <item>
        <enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.4.1/AutoEmailSender-2.4.0-arm64.dmg" ${enclosureAttributes(240)} />
      </item>
    </channel>`;

  assert.deepEqual(extractPreviousDmgAssets(appcast, "JunieXD/AutoEmailSender", 3), [
    {
      tag: "v2.4.1",
      name: "AutoEmailSender-2.4.1-arm64.dmg",
      length: 241,
      signature: placeholderSignature,
    },
    {
      tag: "v2.4.0",
      name: "AutoEmailSender-2.4.0-arm64.dmg",
      length: 240,
      signature: placeholderSignature,
    },
  ]);
});

test("reuses only signed historical DMGs and redownloads a corrupted cache entry", async () => {
  const tempRoot = mkdtempSync(path.join(os.tmpdir(), "sparkle-dmg-cache-test-"));
  try {
    const cacheDirectory = path.join(tempRoot, "cache");
    const firstWorkDirectory = path.join(tempRoot, "work-1");
    const secondWorkDirectory = path.join(tempRoot, "work-2");
    const thirdWorkDirectory = path.join(tempRoot, "work-3");
    for (const directory of [cacheDirectory, firstWorkDirectory, secondWorkDirectory, thirdWorkDirectory]) {
      mkdirSync(directory, { recursive: true });
    }

    const privateSeed = Buffer.alloc(32, 13);
    const privateKey = createPrivateKey({
      key: Buffer.concat([ed25519Pkcs8SeedPrefix, privateSeed]),
      format: "der",
      type: "pkcs8",
    });
    const publicKey = deriveSparklePublicKey(privateSeed.toString("base64"));
    const contentsByName = new Map([
      ["AutoEmailSender-9.9.8-arm64.dmg", Buffer.from("previous-998")],
      ["AutoEmailSender-9.9.7-arm64.dmg", Buffer.from("previous-997")],
    ]);
    const assets = [...contentsByName].map(([name, contents]) => ({
      tag: `v${name.match(/-(\d+\.\d+\.\d+)-/)?.[1]}`,
      name,
      length: contents.length,
      signature: sign(null, contents, privateKey).toString("base64"),
    }));
    const downloadCalls = [];
    const downloadAsset = async (asset, destination) => {
      downloadCalls.push(asset.name);
      writeFileSync(path.join(destination, asset.name), contentsByName.get(asset.name));
    };
    writeFileSync(
      path.join(cacheDirectory, "AutoEmailSender-9.9.6-arm64.dmg"),
      "stale-baseline",
    );

    await stagePreviousDmgAssets({
      assets,
      workDirectory: firstWorkDirectory,
      cacheDirectory,
      publicKey,
      downloadAsset,
    });
    assert.deepEqual(downloadCalls.sort(), [...contentsByName.keys()].sort());
    assert.deepEqual(readdirSync(cacheDirectory).sort(), [...contentsByName.keys()].sort());

    downloadCalls.length = 0;
    await stagePreviousDmgAssets({
      assets,
      workDirectory: secondWorkDirectory,
      cacheDirectory,
      publicKey,
      downloadAsset,
    });
    assert.deepEqual(downloadCalls, []);

    const corruptedName = assets[0].name;
    writeFileSync(
      path.join(cacheDirectory, corruptedName),
      Buffer.alloc(assets[0].length, "x"),
    );
    downloadCalls.length = 0;
    await stagePreviousDmgAssets({
      assets,
      workDirectory: thirdWorkDirectory,
      cacheDirectory,
      publicKey,
      downloadAsset,
    });
    assert.deepEqual(downloadCalls, [corruptedName]);
    assert.equal(
      readFileSync(path.join(thirdWorkDirectory, corruptedName), "utf8"),
      contentsByName.get(corruptedName).toString("utf8"),
    );
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("requires a delta from the newest clean Sparkle baseline", () => {
  const previousAssets = [
    { tag: "v2.4.1", name: "AutoEmailSender-2.4.1-arm64.dmg" },
    { tag: "v2.5.3", name: "AutoEmailSender-2.5.3-arm64.dmg" },
  ];
  const appcastWithDelta = `
    <channel>
      <item>
        <sparkle:version>2.5.4</sparkle:version>
        <enclosure url="AutoEmailSender-2.5.4-arm64.dmg" />
        <sparkle:deltas>
          <enclosure url="2.5.3-to-2.5.4.delta" sparkle:deltaFrom="2.5.3" />
        </sparkle:deltas>
      </item>
    </channel>`;

  assert.deepEqual(extractDeltaSourceVersions(appcastWithDelta, "2.5.4"), ["2.5.3"]);
  assert.equal(assertRequiredDelta(appcastWithDelta, "2.5.4", previousAssets), "2.5.3");
  const appcastWithoutRequiredDelta = appcastWithDelta
    .replace("2.5.3-to-2.5.4.delta", "missing.delta")
    .replace('sparkle:deltaFrom="2.5.3"', 'sparkle:deltaFrom="2.4.1"');
  assert.throws(
    () => assertRequiredDelta(appcastWithoutRequiredDelta, "2.5.4", previousAssets),
    /拒绝发布仅含全量更新/,
  );
});

test("allows the legacy pre-clean baseline to fall back to a full DMG", () => {
  const previousAssets = [
    { tag: "v2.4.1", name: "AutoEmailSender-2.4.1-arm64.dmg" },
  ];

  assert.equal(
    assertRequiredDelta(
      "<item><sparkle:version>2.5.3</sparkle:version></item>",
      "2.5.3",
      previousAssets,
    ),
    null,
  );
});

test("normalizes generated delta names and keeps appcast URLs aligned", () => {
  const sourceName = "Auto Email Sender2.5.4-2.5.3.delta";
  const targetName = "Auto.Email.Sender2.5.4-2.5.3.delta";
  const appcast = `
    <item>
      <sparkle:version>2.5.4</sparkle:version>
      <enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.5.4/AutoEmailSender-2.5.4-arm64.dmg" />
      <sparkle:deltas>
        <enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.5.4/Auto%20Email%20Sender2.5.4-2.5.3.delta" sparkle:deltaFrom="2.5.3" />
      </sparkle:deltas>
    </item>`;

  assert.equal(normalizeGitHubAssetName(sourceName), targetName);
  const rewritten = rewriteAppcastAssetNames(appcast, new Map([[sourceName, targetName]]));
  assert.deepEqual(
    extractCurrentReleaseAssetNames(
      rewritten,
      "2.5.4",
      "JunieXD/AutoEmailSender",
      "v2.5.4",
    ),
    ["AutoEmailSender-2.5.4-arm64.dmg", targetName],
  );
  assert.doesNotThrow(() =>
    assertPublishableSparkleAssets(
      rewritten,
      "2.5.4",
      "JunieXD/AutoEmailSender",
      "v2.5.4",
      ["AutoEmailSender-2.5.4-arm64.dmg", targetName],
    ),
  );
  assert.throws(
    () =>
      assertPublishableSparkleAssets(
        appcast,
        "2.5.4",
        "JunieXD/AutoEmailSender",
        "v2.5.4",
        ["AutoEmailSender-2.5.4-arm64.dmg", targetName],
      ),
    /可能改写的资产名/,
  );
});

test("passes the private key through stdin and stages only publishable files", () => {
  const tempRoot = mkdtempSync(path.join(os.tmpdir(), "sparkle-release-test-"));
  try {
    const releaseDirectory = path.join(tempRoot, "release");
    const outputDirectory = path.join(tempRoot, "output");
    const binDirectory = path.join(tempRoot, "bin");
    mkdirSync(releaseDirectory, { recursive: true });
    mkdirSync(binDirectory, { recursive: true });
    writeFileSync(path.join(releaseDirectory, "AutoEmailSender-9.9.9-arm64.dmg"), "current-dmg");
    writeFileSync(path.join(tempRoot, "release-notes.md"), "# v9.9.9\n");

    const ghPath = path.join(binDirectory, "gh");
    writeFileSync(
      ghPath,
      `#!/usr/bin/env node
if (process.argv.includes("view")) {
  process.stdout.write(JSON.stringify({ tagName: "v9.9.8", assets: [] }));
  process.exit(0);
}
process.exit(2);
`,
    );
    chmodSync(ghPath, 0o755);

    const privateKey = Buffer.alloc(32, 11).toString("base64");
    const fakeSignerPath = path.join(tempRoot, "sign_update");
    writeFileSync(
      fakeSignerPath,
      `#!/usr/bin/env node
const crypto = require("node:crypto");
const fs = require("node:fs");
const prefix = Buffer.from("302e020100300506032b657004220420", "hex");
let privateKey = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { privateKey += chunk; });
process.stdin.on("end", () => {
  if (privateKey.trim() !== process.env.EXPECTED_PRIVATE_KEY) process.exit(3);
  const filePath = process.argv.at(-1);
  const current = fs.readFileSync(filePath, "utf8");
  const unsigned = current.replace(/\\n?<!-- sparkle-signatures:[\\s\\S]*?-->\\s*$/, "");
  const contents = Buffer.from(unsigned, "utf8");
  const privateKeyObject = crypto.createPrivateKey({
    key: Buffer.concat([prefix, Buffer.from(privateKey.trim(), "base64")]),
    format: "der",
    type: "pkcs8",
  });
  const signature = crypto.sign(null, contents, privateKeyObject).toString("base64");
  fs.writeFileSync(
    filePath,
    Buffer.concat([
      contents,
      Buffer.from("\\n<!-- sparkle-signatures:\\nedSignature: " + signature + "\\nlength: " + contents.length + "\\n-->\\n"),
    ]),
  );
});
`,
    );
    chmodSync(fakeSignerPath, 0o755);

    const fakeGeneratorPath = path.join(tempRoot, "generate_appcast");
    writeFileSync(
      fakeGeneratorPath,
      `#!/usr/bin/env node
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
let privateKey = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { privateKey += chunk; });
process.stdin.on("end", () => {
  if (privateKey.trim() !== process.env.EXPECTED_PRIVATE_KEY) process.exit(3);
  if (process.argv[process.argv.indexOf("--maximum-deltas") + 1] !== "1") process.exit(5);
  if (process.argv[process.argv.indexOf("--maximum-versions") + 1] !== "2") process.exit(6);
  const workDirectory = process.argv.at(-1);
  const appcastPath = path.join(workDirectory, "appcast.xml");
  fs.writeFileSync(appcastPath, [
    "<item>",
    "<sparkle:version>9.9.9</sparkle:version>",
    '<enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v9.9.9/AutoEmailSender-9.9.9-arm64.dmg" />',
    "<sparkle:deltas>",
    '<enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v9.9.9/Auto%20Email%20Sender9.9.9-9.9.8.delta" sparkle:deltaFrom="9.9.8" />',
    "</sparkle:deltas>",
    "</item>",
  ].join(""));
  const signResult = spawnSync(
    process.env.FAKE_SIGN_UPDATE_PATH,
    ["--ed-key-file", "-", appcastPath],
    { input: privateKey },
  );
  if (signResult.status !== 0) process.exit(4);
  fs.writeFileSync(path.join(workDirectory, "Auto Email Sender9.9.9-9.9.8.delta"), "delta");
});
`,
    );
    chmodSync(fakeGeneratorPath, 0o755);

    const result = spawnSync(
      process.execPath,
      [
        path.join(scriptDirectory, "prepare-sparkle-release.mjs"),
        "--release-tag",
        "v9.9.9",
        "--release-dir",
        releaseDirectory,
        "--release-notes",
        path.join(tempRoot, "release-notes.md"),
        "--output-dir",
        outputDirectory,
        "--generate-appcast",
        fakeGeneratorPath,
        "--sign-update",
        fakeSignerPath,
      ],
      {
        encoding: "utf8",
        env: {
          ...process.env,
          PATH: `${binDirectory}${path.delimiter}${process.env.PATH ?? ""}`,
          EXPECTED_PRIVATE_KEY: privateKey,
          FAKE_SIGN_UPDATE_PATH: fakeSignerPath,
          SPARKLE_ED_PRIVATE_KEY: privateKey,
          SPARKLE_PUBLIC_ED_KEY: deriveSparklePublicKey(privateKey),
        },
      },
    );

    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(readdirSync(outputDirectory).sort(), [
      "Auto.Email.Sender9.9.9-9.9.8.delta",
      "AutoEmailSender-9.9.9-arm64.dmg",
      "appcast.xml",
    ]);
    assert.equal(
      readFileSync(path.join(outputDirectory, "AutoEmailSender-9.9.9-arm64.dmg"), "utf8"),
      "current-dmg",
    );
    assert.match(
      readFileSync(path.join(outputDirectory, "appcast.xml"), "utf8"),
      /Auto\.Email\.Sender9\.9\.9-9\.9\.8\.delta/,
    );
    assert.doesNotMatch(
      readFileSync(path.join(outputDirectory, "appcast.xml"), "utf8"),
      /Auto%20Email%20Sender/,
    );
    assert.doesNotThrow(() =>
      assertSparkleAppcastSignature(
        readFileSync(path.join(outputDirectory, "appcast.xml")),
        deriveSparklePublicKey(privateKey),
      ),
    );
    assert.match(result.stdout, /生成 1 个差分包/);
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});
