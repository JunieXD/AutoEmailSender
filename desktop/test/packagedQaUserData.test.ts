import { chmod, mkdtemp, mkdir, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  configurePackagedQaUserData,
  getActivePackagedQaIsolatedHomePath,
  getActivePackagedQaUserDataPath,
  getPackagedQaDiagnosticsExportPath,
  PACKAGED_QA_DIAGNOSTICS_EXPORT_ENV,
  PACKAGED_QA_DIAGNOSTICS_EXPORT_NAME,
  PACKAGED_QA_DIAGNOSTICS_EXPORT_VALUE,
  PACKAGED_QA_ENABLE_ENV,
  PACKAGED_QA_ENABLE_VALUE,
  PACKAGED_QA_NONCE_ENV,
  PACKAGED_QA_PATH_MARKER,
  PACKAGED_QA_SENTINEL_NAME,
  PACKAGED_QA_SENTINEL_PROTOCOL_VERSION,
  PACKAGED_QA_USER_DATA_ENV,
  resolvePackagedQaUserDataPath,
} from "../src/main/packaged-qa/user-data.js";

const temporaryDirectories: string[] = [];
const nonce = "qa_nonce_1234567890";

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) => rm(directory, { force: true, recursive: true })),
  );
});

async function createAuthorizedUserData(): Promise<string> {
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "packaged-qa-test-"));
  const root = path.join(temporaryRoot, PACKAGED_QA_PATH_MARKER);
  await mkdir(root, { mode: 0o700 });
  temporaryDirectories.push(temporaryRoot);
  const userDataPath = path.join(root, "用户 data Ω");
  await mkdir(userDataPath, { mode: 0o700 });
  const canonicalPath = await realpath(userDataPath);
  await writeFile(
    path.join(canonicalPath, PACKAGED_QA_SENTINEL_NAME),
    JSON.stringify({
      protocol_version: PACKAGED_QA_SENTINEL_PROTOCOL_VERSION,
      purpose: "packaged-release-qa",
      nonce,
      user_data_path: canonicalPath,
    }),
    { encoding: "utf8", mode: 0o600 },
  );
  await chmod(canonicalPath, 0o700);
  return canonicalPath;
}

function authorizedInput(userDataPath: string) {
  return {
    isPackaged: true,
    argv: ["Auto Email Sender", `--auto-email-sender-packaged-qa=${nonce}`],
    env: {
      [PACKAGED_QA_ENABLE_ENV]: PACKAGED_QA_ENABLE_VALUE,
      [PACKAGED_QA_NONCE_ENV]: nonce,
      [PACKAGED_QA_USER_DATA_ENV]: userDataPath,
    },
    defaultUserDataPath: path.join(os.tmpdir(), "real-user-data"),
    homePath: os.homedir(),
    platform: process.platform,
  } as const;
}

describe("packaged QA userData isolation", () => {
  it("leaves ordinary launches unchanged", () => {
    expect(resolvePackagedQaUserDataPath({
      isPackaged: true,
      argv: ["Auto Email Sender"],
      env: {},
      defaultUserDataPath: "/default/user-data",
      homePath: "/home/alice",
    })).toBeNull();
  });

  it("accepts a fully authorized isolated non-ASCII path", async () => {
    const userDataPath = await createAuthorizedUserData();

    expect(resolvePackagedQaUserDataPath(authorizedInput(userDataPath))).toBe(userDataPath);
  });

  it.each([
    ["source launch", { isPackaged: false }],
    ["wrong enable value", { env: { [PACKAGED_QA_ENABLE_ENV]: "true" } }],
    ["missing command nonce", { argv: ["Auto Email Sender"] }],
    ["protected default path", { defaultUserDataPath: "__USER_DATA__" }],
  ])("fails closed for %s", async (_name, override) => {
    const userDataPath = await createAuthorizedUserData();
    const input = authorizedInput(userDataPath);
    const normalizedOverride = Object.fromEntries(
      Object.entries(override).map(([key, value]) => [
        key,
        value === "__USER_DATA__" ? userDataPath : value,
      ]),
    );

    expect(() => resolvePackagedQaUserDataPath({ ...input, ...normalizedOverride })).toThrow();
  });

  it("rejects a stale sentinel nonce", async () => {
    const userDataPath = await createAuthorizedUserData();
    await writeFile(
      path.join(userDataPath, PACKAGED_QA_SENTINEL_NAME),
      JSON.stringify({
        protocol_version: PACKAGED_QA_SENTINEL_PROTOCOL_VERSION,
        purpose: "packaged-release-qa",
        nonce: "different_nonce_12345",
        user_data_path: userDataPath,
      }),
      { encoding: "utf8", mode: 0o600 },
    );

    expect(() => resolvePackagedQaUserDataPath(authorizedInput(userDataPath))).toThrow(
      "does not authorize",
    );
  });

  it.skipIf(process.platform === "win32")("rejects a symbolic-link sentinel", async () => {
    const userDataPath = await createAuthorizedUserData();
    const sentinelPath = path.join(userDataPath, PACKAGED_QA_SENTINEL_NAME);
    const targetPath = path.join(userDataPath, "sentinel-target.json");
    await writeFile(targetPath, await readFile(sentinelPath));
    await rm(sentinelPath);
    await symlink(targetPath, sentinelPath);

    expect(() => resolvePackagedQaUserDataPath(authorizedInput(userDataPath))).toThrow(
      "regular file",
    );
  });

  it("rejects a path without the dedicated QA marker", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "ordinary-data-"));
    temporaryDirectories.push(root);
    await chmod(root, 0o700);
    const canonicalRoot = await realpath(root);

    expect(() => resolvePackagedQaUserDataPath(authorizedInput(canonicalRoot))).toThrow(
      PACKAGED_QA_PATH_MARKER,
    );
  });

  it("rejects overlap with the normal Electron userData tree", async () => {
    const userDataPath = await createAuthorizedUserData();

    expect(() => resolvePackagedQaUserDataPath({
      ...authorizedInput(userDataPath),
      defaultUserDataPath: path.dirname(userDataPath),
    })).toThrow("protected application path");
  });

  it("sets Electron userData only after validation", async () => {
    const userDataPath = await createAuthorizedUserData();
    const setPath = vi.fn();
    const app = {
      isPackaged: true,
      getPath: (name: "home" | "userData") => (
        name === "home" ? os.homedir() : path.join(os.tmpdir(), "real-user-data")
      ),
      setPath,
    };

    expect(configurePackagedQaUserData(app, {
      argv: authorizedInput(userDataPath).argv,
      env: authorizedInput(userDataPath).env,
      platform: process.platform,
    })).toBe(userDataPath);
    expect(setPath).toHaveBeenCalledOnce();
    expect(setPath).toHaveBeenCalledWith("userData", userDataPath);
    expect(getActivePackagedQaUserDataPath()).toBe(userDataPath);
    expect(getActivePackagedQaIsolatedHomePath()).toBe(
      path.join(userDataPath, "isolated-home"),
    );
    expect(getPackagedQaDiagnosticsExportPath({})).toBeNull();
    expect(() => getPackagedQaDiagnosticsExportPath({
      [PACKAGED_QA_DIAGNOSTICS_EXPORT_ENV]: "true",
    })).toThrow("not authorized");
    expect(getPackagedQaDiagnosticsExportPath({
      [PACKAGED_QA_DIAGNOSTICS_EXPORT_ENV]: PACKAGED_QA_DIAGNOSTICS_EXPORT_VALUE,
    })).toBe(path.join(userDataPath, PACKAGED_QA_DIAGNOSTICS_EXPORT_NAME));
  });

  it.skipIf(process.platform === "win32")("rejects a symbolic-link isolated home", async () => {
    const userDataPath = await createAuthorizedUserData();
    const linkedHomeTarget = await mkdtemp(path.join(os.tmpdir(), "packaged-qa-home-target-"));
    temporaryDirectories.push(linkedHomeTarget);
    await symlink(linkedHomeTarget, path.join(userDataPath, "isolated-home"));
    const app = {
      isPackaged: true,
      getPath: (name: "home" | "userData") => (
        name === "home" ? os.homedir() : path.join(os.tmpdir(), "real-user-data")
      ),
      setPath: vi.fn(),
    };

    expect(() => configurePackagedQaUserData(app, {
      argv: authorizedInput(userDataPath).argv,
      env: authorizedInput(userDataPath).env,
      platform: process.platform,
    })).toThrow("isolated home");
  });
});
