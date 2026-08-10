import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { readDesktopReleaseBuildIdentity } from "../src/main/release/build-identity.js";

const temporaryRoots: string[] = [];

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

describe("packaged release build identity", () => {
  it("loads an exact prerelease identity embedded in packaged resources", async () => {
    const resourcesPath = await createResources({
      schema_version: 1,
      release_kind: "prerelease",
      version: "2.6.0-beta.1",
      channel: "beta",
      source_branch: "release/api-worker",
      release_sha: "a".repeat(40),
      candidate_run_id: "123456",
      candidate_asset_name: "AutoEmailSender-Setup-2.6.0-beta.1.exe",
      candidate_asset_sha256: null,
      default_backend_mode: "split",
      diagnostics_schema_version: 1,
    });
    const identity = readDesktopReleaseBuildIdentity({
      isPackaged: true,
      resourcesPath,
      appVersion: "2.6.0-beta.1",
      platform: "win32",
      environment: {
        AUTO_EMAIL_SENDER_RELEASE_SHA: "b".repeat(40),
      },
    });
    expect(identity.releaseKind).toBe("prerelease");
    expect(identity.defaultBackendMode).toBe("split");
    expect(identity.diagnostics).toEqual({
      sourceBranch: "release/api-worker",
      releaseSha: "a".repeat(40),
      candidateRunId: "123456",
      candidateAssetName: "AutoEmailSender-Setup-2.6.0-beta.1.exe",
      candidateAssetSha256: null,
    });
  });

  it("rejects a mismatched version, platform asset, or unsafe branch", async () => {
    const resourcesPath = await createResources({
      schema_version: 1,
      release_kind: "prerelease",
      version: "2.6.0-beta.1",
      channel: "beta",
      source_branch: "refs/heads/unsafe",
      release_sha: "a".repeat(40),
      candidate_run_id: "123456",
      candidate_asset_name: "AutoEmailSender-2.6.0-beta.1-arm64.dmg",
      candidate_asset_sha256: null,
      default_backend_mode: "split",
      diagnostics_schema_version: 1,
    });
    const identity = readDesktopReleaseBuildIdentity({
      isPackaged: true,
      resourcesPath,
      appVersion: "2.6.0-beta.1",
      platform: "win32",
    });
    expect(identity.releaseKind).toBe("unknown");
    expect(identity.errorCode).toBe("identity_invalid");
    expect(identity.defaultBackendMode).toBe("combined");
    expect(identity.diagnostics).toEqual({});
  });

  it.runIf(process.platform !== "win32")("does not follow a symlinked identity", async () => {
    const resourcesPath = await createResources(null);
    const outsidePath = path.join(path.dirname(resourcesPath), "outside.json");
    await writeFile(outsidePath, JSON.stringify({ schema_version: 1 }), "utf8");
    await symlink(outsidePath, path.join(resourcesPath, "release-identity.json"));
    const identity = readDesktopReleaseBuildIdentity({
      isPackaged: true,
      resourcesPath,
      appVersion: "2.6.0-beta.1",
      platform: "darwin",
    });
    expect(identity.releaseKind).toBe("unknown");
    expect(identity.errorCode).toBe("identity_unreadable");
  });

  it("allows bounded development overrides without treating them as packaged truth", () => {
    const identity = readDesktopReleaseBuildIdentity({
      isPackaged: false,
      resourcesPath: "/unused",
      appVersion: "2.6.0-beta.1",
      platform: "darwin",
      environment: {
        AUTO_EMAIL_SENDER_RELEASE_SOURCE_BRANCH: "beta/local-qa",
        AUTO_EMAIL_SENDER_RELEASE_SHA: "c".repeat(40),
        AUTO_EMAIL_SENDER_CANDIDATE_RUN_ID: "789",
        AUTO_EMAIL_SENDER_CANDIDATE_ASSET_NAME: "local.dmg",
        AUTO_EMAIL_SENDER_CANDIDATE_ASSET_SHA256: "d".repeat(64),
      },
    });
    expect(identity.diagnostics.releaseSha).toBe("c".repeat(40));
    expect(identity.diagnostics.candidateRunId).toBe("789");
  });
});

async function createResources(identity: object | null): Promise<string> {
  const root = await mkdtemp(path.join(os.tmpdir(), "release-build-identity-"));
  temporaryRoots.push(root);
  const resourcesPath = path.join(root, "resources");
  await mkdir(resourcesPath, { recursive: true });
  if (identity !== null) {
    await writeFile(
      path.join(resourcesPath, "release-identity.json"),
      `${JSON.stringify(identity)}\n`,
      "utf8",
    );
  }
  return resourcesPath;
}
