import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  assertDraftSparkleAssets,
  assertSignedDraftSparkleAssets,
} from "./verify-sparkle-draft-assets.mjs";

const repository = "JunieXD/AutoEmailSender";
const tag = "v2.5.4";
const version = "2.5.4";
const dmgName = "AutoEmailSender-2.5.4-arm64.dmg";
const deltaName = "Auto.Email.Sender2.5.4-2.5.3.delta";
const draftReference = "untagged-68cfb27c571c98249a72";
const appcast = `
  <item>
    <sparkle:version>${version}</sparkle:version>
    <enclosure url="https://github.com/${repository}/releases/download/${tag}/${dmgName}" />
    <sparkle:deltas>
      <enclosure url="https://github.com/${repository}/releases/download/${tag}/${deltaName}" sparkle:deltaFrom="2.5.3" />
    </sparkle:deltas>
  </item>`;

function asset(name, size, reference = draftReference) {
  return {
    name,
    size,
    url: `https://github.com/${repository}/releases/download/${reference}/${name}`,
  };
}

function draftRelease(assets, reference = draftReference) {
  return {
    isDraft: true,
    tagName: tag,
    url: `https://github.com/${repository}/releases/tag/${reference}`,
    assets,
  };
}

test("accepts GitHub draft asset URLs while the appcast uses the final tag", () => {
  assert.deepEqual(
    assertDraftSparkleAssets({
      release: draftRelease([asset(dmgName, 200), asset(deltaName, 100)]),
      appcast,
      version,
      repository,
      tag,
    }),
    [dmgName, deltaName],
  );
});

test("rejects a GitHub-normalized delta name before publication", () => {
  const unsafeAppcast = appcast.replaceAll(deltaName, "Auto%20Email%20Sender2.5.4-2.5.3.delta");
  assert.throws(
    () =>
      assertDraftSparkleAssets({
        release: draftRelease([asset(dmgName, 200), asset(deltaName, 100)]),
        appcast: unsafeAppcast,
        version,
        repository,
        tag,
      }),
    /缺少 appcast\.xml 引用的精确资产名/,
  );
});

test("rejects an asset URL belonging to another draft", () => {
  assert.throws(
    () =>
      assertDraftSparkleAssets({
        release: draftRelease([
          asset(dmgName, 200, "untagged-deadbeef"),
          asset(deltaName, 100),
        ]),
        appcast,
        version,
        repository,
        tag,
      }),
    /draft Release 资产 URL 与 appcast\.xml 不一致/,
  );
});

test("rejects an unsigned or modified final appcast before publication", () => {
  assert.throws(
    () =>
      assertSignedDraftSparkleAssets({
        release: draftRelease([asset(dmgName, 200), asset(deltaName, 100)]),
        appcast,
        publicKey: "JRJRe0L2YixWpEKBYPlhqmtS/wa123RdC8iNC30dKxM=",
        version,
        repository,
        tag,
      }),
    /缺少 Sparkle feed 签名/,
  );
});

test("promotion uses current tooling while the tag remains bound to the candidate SHA", () => {
  const workflow = readFileSync(
    new URL("../../.github/workflows/release.yml", import.meta.url),
    "utf8",
  );
  const publishJob = workflow.slice(workflow.indexOf("\n  publish:"));

  assert.match(publishJob, /- name: Checkout promotion tooling/);
  assert.doesNotMatch(publishJob, /ref: \$\{\{ env\.RELEASE_SHA \}\}/);
  assert.match(publishJob, /git cat-file -e "\$RELEASE_SHA\^\{commit\}"/);
});

test("refuses to mutate or validate an already public release", () => {
  assert.throws(
    () =>
      assertDraftSparkleAssets({
        release: { isDraft: false, assets: [] },
        appcast,
        version,
        repository,
        tag,
      }),
    /拒绝核验非 draft Release/,
  );
});
