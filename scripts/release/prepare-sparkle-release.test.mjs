import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
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
  assertRequiredDelta,
  deriveSparklePublicKey,
  extractDeltaSourceVersions,
  extractPreviousDmgAssets,
  getMacDmgName,
  getMacDmgVersion,
  normalizeReleaseTag,
} from "./prepare-sparkle-release.mjs";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));

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

test("extracts full DMGs while ignoring nested deltas and foreign URLs", () => {
  const appcast = `
    <rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
      <channel>
        <item>
          <sparkle:version>2.3.9</sparkle:version>
          <enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.3.9/AutoEmailSender-2.3.9-arm64.dmg" />
          <sparkle:deltas>
            <enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.3.9/from-2.3.8.delta" />
          </sparkle:deltas>
        </item>
        <item>
          <enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.3.8/AutoEmailSender-2.3.8-arm64.dmg?download=1&amp;source=feed" />
        </item>
        <item>
          <enclosure url="https://example.com/JunieXD/AutoEmailSender/releases/download/v2.3.7/foreign.dmg" />
        </item>
      </channel>
    </rss>`;

  assert.deepEqual(extractPreviousDmgAssets(appcast, "JunieXD/AutoEmailSender"), [
    { tag: "v2.3.9", name: "AutoEmailSender-2.3.9-arm64.dmg" },
    { tag: "v2.3.8", name: "AutoEmailSender-2.3.8-arm64.dmg" },
  ]);
});

test("limits delta source downloads to the most recent three unique DMGs", () => {
  const items = [9, 8, 8, 7, 6].map(
    (patch) => `<item><enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.3.${patch}/AutoEmailSender-2.3.${patch}-arm64.dmg" /></item>`,
  );
  const assets = extractPreviousDmgAssets(`<channel>${items.join("")}</channel>`, "JunieXD/AutoEmailSender");

  assert.deepEqual(assets.map(({ tag }) => tag), ["v2.3.9", "v2.3.8", "v2.3.7"]);
});

test("recovers historical release tags after Sparkle rewrites retained download URLs", () => {
  const appcast = `
    <channel>
      <item>
        <enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.4.1/AutoEmailSender-2.4.1-arm64.dmg" />
      </item>
      <item>
        <enclosure url="https://github.com/JunieXD/AutoEmailSender/releases/download/v2.4.1/AutoEmailSender-2.4.0-arm64.dmg" />
      </item>
    </channel>`;

  assert.deepEqual(extractPreviousDmgAssets(appcast, "JunieXD/AutoEmailSender"), [
    { tag: "v2.4.1", name: "AutoEmailSender-2.4.1-arm64.dmg" },
    { tag: "v2.4.0", name: "AutoEmailSender-2.4.0-arm64.dmg" },
  ]);
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
    const fakeGeneratorPath = path.join(tempRoot, "generate_appcast");
    writeFileSync(
      fakeGeneratorPath,
      `#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
let privateKey = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { privateKey += chunk; });
process.stdin.on("end", () => {
  if (privateKey.trim() !== process.env.EXPECTED_PRIVATE_KEY) process.exit(3);
  const workDirectory = process.argv.at(-1);
  fs.writeFileSync(path.join(workDirectory, "appcast.xml"), "<item><sparkle:version>9.9.9</sparkle:version></item>");
  fs.writeFileSync(path.join(workDirectory, "9.9.8-to-9.9.9.delta"), "delta");
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
      ],
      {
        encoding: "utf8",
        env: {
          ...process.env,
          PATH: `${binDirectory}${path.delimiter}${process.env.PATH ?? ""}`,
          EXPECTED_PRIVATE_KEY: privateKey,
          SPARKLE_ED_PRIVATE_KEY: privateKey,
          SPARKLE_PUBLIC_ED_KEY: deriveSparklePublicKey(privateKey),
        },
      },
    );

    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(readdirSync(outputDirectory).sort(), [
      "9.9.8-to-9.9.9.delta",
      "AutoEmailSender-9.9.9-arm64.dmg",
      "appcast.xml",
    ]);
    assert.equal(
      readFileSync(path.join(outputDirectory, "AutoEmailSender-9.9.9-arm64.dmg"), "utf8"),
      "current-dmg",
    );
    assert.match(result.stdout, /生成 1 个差分包/);
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});
