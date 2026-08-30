import assert from "node:assert/strict";
import { createPrivateKey, sign } from "node:crypto";
import test from "node:test";
import { deriveSparklePublicKey } from "./prepare-sparkle-release.mjs";
import {
  assertPublishedRelease,
  assertReleaseWorkflowRuns,
  extractCurrentSparkleEnclosures,
  selectPreviousSparkleDmg,
} from "./verify-release.mjs";

const repository = "JunieXD/AutoEmailSender";
const privateSeed = Buffer.alloc(32, 7);
const privateKey = createPrivateKey({
  key: Buffer.concat([Buffer.from("302e020100300506032b657004220420", "hex"), privateSeed]),
  format: "der",
  type: "pkcs8",
});
const publicKey = deriveSparklePublicKey(privateSeed.toString("base64"));

function signedEnclosure(name, contents, extra = "") {
  const signature = sign(null, contents, privateKey).toString("base64");
  return `<enclosure url="https://github.com/${repository}/releases/download/v9.9.9/${name}" length="${contents.length}" sparkle:edSignature="${signature}" ${extra}/>`;
}

test("selects the exact current item before reading enclosures", () => {
  const dmg = Buffer.from("current dmg");
  const delta = Buffer.from("current delta");
  const appcast = `
    <item>
      <sparkle:version>9.9.9</sparkle:version>
      ${signedEnclosure("AutoEmailSender-9.9.9-arm64.dmg", dmg)}
      <sparkle:deltas>${signedEnclosure("Auto.Email.Sender9.9.9-9.9.8.delta", delta, 'sparkle:deltaFrom="9.9.8"')}</sparkle:deltas>
    </item>
    <item>
      <sparkle:version>9.9.8</sparkle:version>
      <enclosure url="https://github.com/${repository}/releases/download/v9.9.9/AutoEmailSender-9.9.8-arm64.dmg" />
    </item>`;

  const current = extractCurrentSparkleEnclosures(appcast, "9.9.9", repository, "v9.9.9");
  assert.deepEqual(current.map((entry) => entry.name), [
    "AutoEmailSender-9.9.9-arm64.dmg",
    "Auto.Email.Sender9.9.9-9.9.8.delta",
  ]);
  assert.equal(current[0].signature.length > 0, true);
  assert.equal(publicKey.length > 0, true);
  assert.deepEqual(selectPreviousSparkleDmg(appcast, "9.9.9", repository), {
    name: "AutoEmailSender-9.9.8-arm64.dmg",
    tag: "v9.9.8",
    url: `https://github.com/${repository}/releases/download/v9.9.9/AutoEmailSender-9.9.8-arm64.dmg`,
    version: "9.9.8",
  });
});

test("requires the certify topology for candidates and publish-only topology for promotion", () => {
  const sha = "a".repeat(40);
  const run = (jobs) => ({
    workflowName: "Release Desktop",
    headSha: sha,
    status: "completed",
    conclusion: "success",
    jobs: Object.entries(jobs).map(([name, conclusion]) => ({ name, conclusion })),
  });
  const candidate = run({ preflight: "success", "build-windows": "success", "build-macos": "success", certify: "success", publish: "skipped" });
  const promotion = run({ preflight: "skipped", "build-windows": "skipped", "build-macos": "skipped", certify: "skipped", publish: "success" });
  assert.doesNotThrow(() => assertReleaseWorkflowRuns(candidate, promotion, sha));
  assert.throws(() => assertReleaseWorkflowRuns(candidate, run({ publish: "success", preflight: "success" }), sha), /应为 skipped/);
});

test("requires the public asset set and sizes to match the candidate", () => {
  const manifest = {
    releaseTag: "v9.9.9",
    platforms: {
      windows: { artifacts: [{ name: "installer.exe", size: 10, sha256: "a" }] },
      macos: { artifacts: [{ name: "appcast.xml", size: 20, sha256: "b" }] },
      skill: { artifacts: [{ name: "skill.zip", size: 30, sha256: "c" }] },
    },
  };
  assert.equal(assertPublishedRelease({ tagName: "v9.9.9", isDraft: false, isPrerelease: false, assets: [
    { name: "skill.zip", size: 30 },
    { name: "installer.exe", size: 10 },
    { name: "appcast.xml", size: 20 },
  ] }, manifest).length, 3);
  assert.throws(() => assertPublishedRelease({ tagName: "v9.9.9", isDraft: false, isPrerelease: false, assets: [] }, manifest), /资产数量/);
});
