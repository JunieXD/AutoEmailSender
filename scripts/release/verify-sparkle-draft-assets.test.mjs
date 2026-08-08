import assert from "node:assert/strict";
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
const appcast = `
  <item>
    <sparkle:version>${version}</sparkle:version>
    <enclosure url="https://github.com/${repository}/releases/download/${tag}/${dmgName}" />
    <sparkle:deltas>
      <enclosure url="https://github.com/${repository}/releases/download/${tag}/${deltaName}" sparkle:deltaFrom="2.5.3" />
    </sparkle:deltas>
  </item>`;

function asset(name, size) {
  return {
    name,
    size,
    url: `https://github.com/${repository}/releases/download/${tag}/${name}`,
  };
}

test("accepts exact non-empty draft assets referenced by the appcast", () => {
  assert.deepEqual(
    assertDraftSparkleAssets({
      release: { isDraft: true, assets: [asset(dmgName, 200), asset(deltaName, 100)] },
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
        release: { isDraft: true, assets: [asset(dmgName, 200), asset(deltaName, 100)] },
        appcast: unsafeAppcast,
        version,
        repository,
        tag,
      }),
    /缺少 appcast\.xml 引用的精确资产名/,
  );
});

test("rejects an unsigned or modified final appcast before publication", () => {
  assert.throws(
    () =>
      assertSignedDraftSparkleAssets({
        release: { isDraft: true, assets: [asset(dmgName, 200), asset(deltaName, 100)] },
        appcast,
        publicKey: "JRJRe0L2YixWpEKBYPlhqmtS/wa123RdC8iNC30dKxM=",
        version,
        repository,
        tag,
      }),
    /缺少 Sparkle feed 签名/,
  );
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
