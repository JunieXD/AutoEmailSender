import assert from "node:assert/strict";
import test from "node:test";

import {
  assertPrereleaseSupersession,
  assertPrereleaseVersionAvailable,
  normalizePrereleaseContract,
  normalizeSourceBranch,
  latestStableReleaseTag,
  parsePrereleaseVersion,
  stableReleaseTags,
  toPep440PrereleaseVersion,
} from "./prerelease-contract.mjs";

const sha = "a".repeat(40);

test("normalizes a reusable prerelease contract without binding one branch name", () => {
  assert.deepEqual(
    normalizePrereleaseContract({
      version: "2.6.0-beta.3",
      channel: "beta",
      sourceBranch: "release/api-worker-dogfood",
      releaseSha: sha.toUpperCase(),
    }),
    {
      version: "2.6.0-beta.3",
      releaseTag: "v2.6.0-beta.3",
      channel: "beta",
      sourceBranch: "release/api-worker-dogfood",
      releaseSha: sha,
      defaultBackendMode: "split",
      diagnosticsSchemaVersion: 1,
    },
  );
  assert.equal(normalizeSourceBranch("beta/another-topic"), "beta/another-topic");
  assert.equal(normalizeSourceBranch("release/3.0"), "release/3.0");
  assert.equal(normalizeSourceBranch("qa/topic+candidate@2"), "qa/topic+candidate@2");
});

test("requires explicit matching alpha, beta, or rc channels and exact SHAs", () => {
  assert.equal(parsePrereleaseVersion("2.6.0-alpha.1", "alpha").channel, "alpha");
  assert.equal(parsePrereleaseVersion("2.6.0-rc.2", "rc").channel, "rc");
  assert.equal(toPep440PrereleaseVersion("2.6.0-alpha.1", "alpha"), "2.6.0a1");
  assert.equal(toPep440PrereleaseVersion("2.6.0-beta.12", "beta"), "2.6.0b12");
  assert.equal(toPep440PrereleaseVersion("2.6.0-rc.2", "rc"), "2.6.0rc2");
  assert.throws(
    () => parsePrereleaseVersion("2.6.0-beta.1", "rc"),
    /不一致/,
  );
  assert.throws(
    () => parsePrereleaseVersion("2.6.0-beta", "beta"),
    /递增标识/,
  );
  assert.throws(
    () => parsePrereleaseVersion("2.6.0-beta.preview", "beta"),
    /正整数递增标识/,
  );
  assert.throws(
    () => parsePrereleaseVersion("2.6.0-beta.1.extra", "beta"),
    /正整数递增标识/,
  );
  assert.throws(
    () => normalizePrereleaseContract({
      version: "2.6.0-beta.1",
      channel: "beta",
      sourceBranch: "beta/topic",
      releaseSha: "abc",
    }),
    /40 位/,
  );
});

test("rejects unsafe ref names instead of interpolating them into git or gh", () => {
  for (const branch of [
    "refs/heads/beta/topic",
    "beta/../master",
    "beta topic",
    "beta/中文主题",
    "beta\\topic",
    "beta/topic.lock",
    "-danger",
    "beta/@{upstream}",
  ]) {
    assert.throws(() => normalizeSourceBranch(branch), /无效/);
  }
});

test("stable tag discovery ignores every prerelease tag", () => {
  assert.deepEqual(
    stableReleaseTags([
      "v2.5.4",
      "v2.6.0-alpha.1",
      "v2.6.0-beta.2",
      "v2.6.0-rc.1",
      "not-a-tag",
    ]),
    ["2.5.4"],
  );
  assert.equal(
    latestStableReleaseTag(["v2.9.0", "v2.10.0-beta.1", "v2.10.0", "v3.0.0-rc.1"]),
    "v2.10.0",
  );
});

test("requires a unique prerelease above stable and same-core prereleases", () => {
  assert.deepEqual(
    assertPrereleaseVersionAvailable(
      "2.6.0-beta.2",
      "beta",
      ["v2.5.4", "v2.6.0-alpha.1", "v2.6.0-beta.1"],
    ),
    { highestStable: "2.5.4", highestPrerelease: "2.6.0-beta.1" },
  );
  assert.throws(
    () => assertPrereleaseVersionAvailable("2.6.0-beta.1", "beta", ["v2.6.0-beta.1"]),
    /不可覆盖/,
  );
  assert.throws(
    () => assertPrereleaseVersionAvailable("2.5.4-beta.1", "beta", ["v2.5.4"]),
    /必须高于最新稳定版/,
  );
  assert.throws(
    () => assertPrereleaseVersionAvailable(
      "2.6.0-alpha.2",
      "alpha",
      ["v2.5.4", "v2.6.0-beta.1"],
    ),
    /必须高于同核心版本/,
  );
});

test("supersession never overwrites assets and requires a higher same-core version", () => {
  assert.deepEqual(
    assertPrereleaseSupersession("2.6.0-beta.1", "2.6.0-beta.2"),
    {
      previousTag: "v2.6.0-beta.1",
      replacementTag: "v2.6.0-beta.2",
    },
  );
  assert.deepEqual(
    assertPrereleaseSupersession("2.6.0-beta.2", "2.6.0-rc.1"),
    {
      previousTag: "v2.6.0-beta.2",
      replacementTag: "v2.6.0-rc.1",
    },
  );
  assert.throws(
    () => assertPrereleaseSupersession("2.6.0-beta.2", "2.6.0-beta.1"),
    /必须高于/,
  );
  assert.throws(
    () => assertPrereleaseSupersession("2.6.0-beta.2", "2.7.0-beta.1"),
    /相同核心版本/,
  );
});
