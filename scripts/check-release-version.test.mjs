import assert from "node:assert/strict";
import test from "node:test";

import {
  assertReleaseVersion,
  compareVersions,
} from "./check-release-version.mjs";

test("orders stable and prerelease versions using SemVer precedence", () => {
  assert.equal(compareVersions("2.4.0-beta.2", "2.4.0-beta.1"), 1);
  assert.equal(compareVersions("2.4.0", "2.4.0-rc.1"), 1);
  assert.equal(compareVersions("2.4.0-alpha", "2.3.9"), 1);
});

test("accepts only a unique version above the highest release tag", () => {
  assert.equal(assertReleaseVersion("2.4.0", ["v2.3.9", "not-a-release"]), "2.3.9");
  assert.throws(
    () => assertReleaseVersion("2.3.9", ["v2.3.9"]),
    /tag 已存在/,
  );
  assert.throws(
    () => assertReleaseVersion("1.2.3", ["v2.3.9"]),
    /必须高于当前最高 tag/,
  );
});
