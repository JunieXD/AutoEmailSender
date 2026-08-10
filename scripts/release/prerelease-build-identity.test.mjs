import assert from "node:assert/strict";
import test from "node:test";

import {
  createPrereleaseBuildIdentity,
  expectedPrereleaseAssetName,
} from "./prerelease-build-identity.mjs";

test("creates deterministic platform build identities for the exact candidate run", () => {
  const common = {
    version: "2.6.0-beta.1",
    channel: "beta",
    sourceBranch: "release/api-worker",
    releaseSha: "a".repeat(40),
    candidateRunId: "123456",
  };
  const windows = createPrereleaseBuildIdentity({ ...common, platform: "windows" });
  const macos = createPrereleaseBuildIdentity({ ...common, platform: "macos" });
  assert.equal(windows.candidate_asset_name, "AutoEmailSender-Setup-2.6.0-beta.1.exe");
  assert.equal(macos.candidate_asset_name, "AutoEmailSender-2.6.0-beta.1-arm64.dmg");
  assert.equal(windows.candidate_run_id, "123456");
  assert.equal(windows.default_backend_mode, "split");
  assert.equal(windows.diagnostics_schema_version, 1);
  assert.equal(windows.candidate_asset_sha256, null);
});

test("rejects unsupported platforms instead of inventing asset names", () => {
  assert.throws(
    () => expectedPrereleaseAssetName("linux", "2.6.0-beta.1"),
    /不支持/,
  );
});
