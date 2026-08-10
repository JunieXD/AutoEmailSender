import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  assertStableIsolationUnchanged,
  captureStableIsolationSnapshot,
  validateStableIsolationSnapshot,
  verifyPublishedPrereleaseIsolation,
} from "./prerelease-isolation.mjs";

const repository = "JunieXD/AutoEmailSender";
const stableAssets = {
  "appcast.xml": Buffer.from("<rss>stable</rss>"),
  "latest.yml": Buffer.from("version: 2.5.4\n"),
};

function stableRelease() {
  return {
    id: 254,
    tag_name: "v2.5.4",
    draft: false,
    prerelease: false,
    published_at: "2026-08-01T00:00:00Z",
    assets: [
      { id: 101, name: "appcast.xml", url: "https://api.github.com/assets/101" },
      { id: 102, name: "latest.yml", url: "https://api.github.com/assets/102" },
    ],
  };
}

function createFetch(overrides = {}) {
  const prereleaseAssets = overrides.prereleaseAssets ?? [
    { name: "AutoEmailSender-Setup-2.6.0-beta.1.exe" },
    { name: "AutoEmailSender-2.6.0-beta.1-arm64.dmg" },
    { name: "prerelease-candidate.json" },
  ];
  return async (input) => {
    const url = String(input);
    if (url.endsWith(`/repos/${repository}/releases/latest`)) {
      return Response.json(overrides.stableRelease ?? stableRelease());
    }
    if (url === "https://api.github.com/assets/101") {
      return new Response(null, {
        status: 302,
        headers: { location: "https://release-assets.githubusercontent.com/appcast" },
      });
    }
    if (url === "https://api.github.com/assets/102") {
      return new Response(null, {
        status: 302,
        headers: { location: "https://release-assets.githubusercontent.com/latest" },
      });
    }
    if (url === "https://release-assets.githubusercontent.com/appcast") {
      return new Response(overrides.appcast ?? stableAssets["appcast.xml"]);
    }
    if (url === "https://release-assets.githubusercontent.com/latest") {
      return new Response(overrides.latest ?? stableAssets["latest.yml"]);
    }
    if (url.includes("/releases/tags/v2.6.0-beta.1")) {
      return Response.json({
        id: 2601,
        tag_name: "v2.6.0-beta.1",
        draft: false,
        prerelease: true,
        assets: prereleaseAssets,
      });
    }
    if (url.includes("/git/ref/tags/v2.6.0-beta.1")) {
      return Response.json({ object: { type: "commit", sha: "a".repeat(40) } });
    }
    return new Response("not found", { status: 404 });
  };
}

test("captures stable Latest and exact update metadata digests", async () => {
  const snapshot = await captureStableIsolationSnapshot({
    repository,
    token: "secret-never-logged",
    fetchImpl: createFetch(),
    now: () => new Date("2026-08-10T00:00:00Z"),
  });
  assert.equal(snapshot.latestRelease.tag, "v2.5.4");
  assert.equal(snapshot.assets["appcast.xml"].sha256, sha256(stableAssets["appcast.xml"]));
  assert.equal(snapshot.assets["latest.yml"].sha256, sha256(stableAssets["latest.yml"]));
  assert.doesNotThrow(() => validateStableIsolationSnapshot(snapshot));
});

test("compares stable isolation without treating capture time as a change", async () => {
  const baseline = await captureStableIsolationSnapshot({
    repository,
    fetchImpl: createFetch(),
    now: () => new Date("2026-08-10T00:00:00Z"),
  });
  const current = { ...structuredClone(baseline), capturedAt: "2026-08-11T00:00:00Z" };
  assert.equal(assertStableIsolationUnchanged(baseline, current), current);
  current.assets["latest.yml"].sha256 = "f".repeat(64);
  assert.throws(
    () => assertStableIsolationUnchanged(baseline, current),
    /已发生变化/,
  );
});

test("verifies a public non-Latest prerelease with no stable update assets", async () => {
  const baseline = await captureStableIsolationSnapshot({
    repository,
    fetchImpl: createFetch(),
  });
  const result = await verifyPublishedPrereleaseIsolation({
    repository,
    version: "2.6.0-beta.1",
    releaseSha: "a".repeat(40),
    baseline,
    fetchImpl: createFetch(),
  });
  assert.equal(result.releaseTag, "v2.6.0-beta.1");
  assert.equal(result.stableLatestTag, "v2.5.4");
  assert.equal(result.releaseSha, "a".repeat(40));
});

test("rejects prereleases that expose stable feed metadata or move the tag", async () => {
  const baseline = await captureStableIsolationSnapshot({
    repository,
    fetchImpl: createFetch(),
  });
  await assert.rejects(
    verifyPublishedPrereleaseIsolation({
      repository,
      version: "2.6.0-beta.1",
      releaseSha: "a".repeat(40),
      baseline,
      fetchImpl: createFetch({
        prereleaseAssets: [
          { name: "AutoEmailSender-Setup-2.6.0-beta.1.exe" },
          { name: "AutoEmailSender-2.6.0-beta.1-arm64.dmg" },
          { name: "prerelease-candidate.json" },
          { name: "latest.yml" },
        ],
      }),
    }),
    /不得包含稳定更新 metadata/,
  );
  await assert.rejects(
    verifyPublishedPrereleaseIsolation({
      repository,
      version: "2.6.0-beta.1",
      releaseSha: "b".repeat(40),
      baseline,
      fetchImpl: createFetch(),
    }),
    /指向 .*预期/,
  );
});

test("rejects any public asset outside the exact prerelease set", async () => {
  const baseline = await captureStableIsolationSnapshot({
    repository,
    fetchImpl: createFetch(),
  });
  await assert.rejects(
    verifyPublishedPrereleaseIsolation({
      repository,
      version: "2.6.0-beta.1",
      releaseSha: "a".repeat(40),
      baseline,
      fetchImpl: createFetch({
        prereleaseAssets: [
          { name: "AutoEmailSender-Setup-2.6.0-beta.1.exe" },
          { name: "AutoEmailSender-2.6.0-beta.1-arm64.dmg" },
          { name: "prerelease-candidate.json" },
          { name: "unexpected-debug.zip" },
        ],
      }),
    }),
    /候选合同之外/,
  );
});

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}
