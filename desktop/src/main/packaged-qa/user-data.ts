import {
  chmodSync,
  lstatSync,
  mkdirSync,
  realpathSync,
  readFileSync,
  statSync,
} from "node:fs";
import path from "node:path";

export const PACKAGED_QA_ENABLE_ENV = "AUTO_EMAIL_SENDER_PACKAGED_QA";
export const PACKAGED_QA_ENABLE_VALUE = "enabled-for-release-certification";
export const PACKAGED_QA_NONCE_ENV = "AUTO_EMAIL_SENDER_PACKAGED_QA_NONCE";
export const PACKAGED_QA_USER_DATA_ENV = "AUTO_EMAIL_SENDER_PACKAGED_QA_USER_DATA";
export const PACKAGED_QA_PATH_MARKER = "auto-email-sender-packaged-qa";
export const PACKAGED_QA_SENTINEL_NAME = ".auto-email-sender-packaged-qa.json";
export const PACKAGED_QA_SENTINEL_PROTOCOL_VERSION = "1";

const PACKAGED_QA_ARGUMENT_PREFIX = "--auto-email-sender-packaged-qa=";
const SAFE_NONCE = /^[A-Za-z0-9_-]{16,128}$/;
let activePackagedQaUserDataPath: string | null = null;

type PackagedQaSentinel = {
  protocol_version: string;
  purpose: string;
  nonce: string;
  user_data_path: string;
};

export type PackagedQaUserDataInput = {
  isPackaged: boolean;
  argv: readonly string[];
  env: NodeJS.ProcessEnv;
  defaultUserDataPath: string;
  homePath: string;
  platform?: NodeJS.Platform;
};

type ElectronPathController = {
  readonly isPackaged: boolean;
  getPath(name: "home" | "userData"): string;
  setPath(name: "userData", value: string): void;
};

/**
 * Resolve the release-QA-only Electron userData override.
 *
 * Every control is intentionally redundant.  A leaked environment variable,
 * a copied command line, or a stale sentinel must never redirect an ordinary
 * packaged launch.  Invalid partial configuration therefore fails closed.
 */
export function resolvePackagedQaUserDataPath(
  input: PackagedQaUserDataInput,
): string | null {
  const enableValue = input.env[PACKAGED_QA_ENABLE_ENV]?.trim() ?? "";
  const nonce = input.env[PACKAGED_QA_NONCE_ENV]?.trim() ?? "";
  const rawUserDataPath = input.env[PACKAGED_QA_USER_DATA_ENV]?.trim() ?? "";
  const qaArgument = input.argv.find((value) => value.startsWith(PACKAGED_QA_ARGUMENT_PREFIX));
  const anyQaControlPresent = Boolean(enableValue || nonce || rawUserDataPath || qaArgument);

  if (!anyQaControlPresent) {
    return null;
  }
  if (!input.isPackaged) {
    throw new Error("Packaged QA userData isolation is available only in a packaged app.");
  }
  if (enableValue !== PACKAGED_QA_ENABLE_VALUE) {
    throw new Error("Packaged QA userData isolation enable value is invalid.");
  }
  if (!SAFE_NONCE.test(nonce)) {
    throw new Error("Packaged QA userData isolation nonce is invalid.");
  }
  if (qaArgument !== `${PACKAGED_QA_ARGUMENT_PREFIX}${nonce}`) {
    throw new Error("Packaged QA command-line nonce does not match the environment.");
  }
  if (!path.isAbsolute(rawUserDataPath)) {
    throw new Error("Packaged QA userData path must be absolute.");
  }

  const resolvedUserDataPath = path.resolve(rawUserDataPath);
  let realUserDataPath: string;
  try {
    realUserDataPath = realpathSync(resolvedUserDataPath);
  } catch (error) {
    throw new Error(`Packaged QA userData path is unavailable: ${getErrorMessage(error)}`);
  }
  if (realUserDataPath !== resolvedUserDataPath) {
    throw new Error("Packaged QA userData path must not traverse symbolic links.");
  }
  const pathRoot = path.parse(realUserDataPath).root;
  if (
    samePath(realUserDataPath, pathRoot, input.platform)
    || samePath(realUserDataPath, input.homePath, input.platform)
    || pathsOverlap(realUserDataPath, input.defaultUserDataPath, input.platform)
  ) {
    throw new Error("Packaged QA userData path overlaps a protected application path.");
  }
  if (!pathComponents(realUserDataPath).includes(PACKAGED_QA_PATH_MARKER)) {
    throw new Error(`Packaged QA userData path must contain ${PACKAGED_QA_PATH_MARKER}.`);
  }

  const directoryStats = statSync(realUserDataPath);
  if (!directoryStats.isDirectory()) {
    throw new Error("Packaged QA userData path must name an existing directory.");
  }
  assertPrivatePermissions(directoryStats.mode, "directory", input.platform);

  const sentinelPath = path.join(realUserDataPath, PACKAGED_QA_SENTINEL_NAME);
  let sentinel: PackagedQaSentinel;
  try {
    const sentinelStats = lstatSync(sentinelPath);
    if (sentinelStats.isSymbolicLink() || !sentinelStats.isFile()) {
      throw new Error("sentinel is not a regular file");
    }
    assertPrivatePermissions(sentinelStats.mode, "sentinel", input.platform);
    sentinel = JSON.parse(readFileSync(sentinelPath, "utf8")) as PackagedQaSentinel;
  } catch (error) {
    throw new Error(`Packaged QA sentinel is invalid: ${getErrorMessage(error)}`);
  }
  if (
    sentinel.protocol_version !== PACKAGED_QA_SENTINEL_PROTOCOL_VERSION
    || sentinel.purpose !== "packaged-release-qa"
    || sentinel.nonce !== nonce
    || !samePath(sentinel.user_data_path, realUserDataPath, input.platform)
  ) {
    throw new Error("Packaged QA sentinel does not authorize this launch.");
  }

  return realUserDataPath;
}

export function configurePackagedQaUserData(
  app: ElectronPathController,
  options: {
    argv?: readonly string[];
    env?: NodeJS.ProcessEnv;
    platform?: NodeJS.Platform;
  } = {},
): string | null {
  const userDataPath = resolvePackagedQaUserDataPath({
    isPackaged: app.isPackaged,
    argv: options.argv ?? process.argv,
    env: options.env ?? process.env,
    defaultUserDataPath: app.getPath("userData"),
    homePath: app.getPath("home"),
    platform: options.platform ?? process.platform,
  });
  if (userDataPath !== null) {
    const isolatedHomePath = path.join(userDataPath, "isolated-home");
    mkdirSync(isolatedHomePath, { recursive: true, mode: 0o700 });
    const isolatedHomeStats = lstatSync(isolatedHomePath);
    if (
      isolatedHomeStats.isSymbolicLink()
      || !isolatedHomeStats.isDirectory()
      || !samePath(realpathSync(isolatedHomePath), isolatedHomePath, options.platform)
    ) {
      throw new Error("Packaged QA isolated home must be a real directory.");
    }
    try {
      chmodSync(isolatedHomePath, 0o700);
    } catch {
      // Windows ACLs do not map to POSIX modes; the parent gate remains authoritative.
    }
    app.setPath("userData", userDataPath);
  }
  activePackagedQaUserDataPath = userDataPath;
  return userDataPath;
}

export function getActivePackagedQaUserDataPath(): string | null {
  return activePackagedQaUserDataPath;
}

export function getActivePackagedQaIsolatedHomePath(): string | null {
  return activePackagedQaUserDataPath === null
    ? null
    : path.join(activePackagedQaUserDataPath, "isolated-home");
}

function pathComponents(value: string): string[] {
  const parsed = path.parse(value);
  return value
    .slice(parsed.root.length)
    .split(path.sep)
    .filter(Boolean);
}

function samePath(
  left: string,
  right: string,
  platform: NodeJS.Platform = process.platform,
): boolean {
  const normalizedLeft = path.resolve(left);
  const normalizedRight = path.resolve(right);
  return platform === "win32"
    ? normalizedLeft.toLowerCase() === normalizedRight.toLowerCase()
    : normalizedLeft === normalizedRight;
}

function pathsOverlap(
  left: string,
  right: string,
  platform: NodeJS.Platform = process.platform,
): boolean {
  const normalize = (value: string) => {
    const resolved = path.resolve(value);
    return platform === "win32" ? resolved.toLowerCase() : resolved;
  };
  const normalizedLeft = normalize(left);
  const normalizedRight = normalize(right);
  const contains = (parent: string, candidate: string) => {
    const prefix = parent.endsWith(path.sep) ? parent : `${parent}${path.sep}`;
    return candidate === parent || candidate.startsWith(prefix);
  };
  return contains(normalizedLeft, normalizedRight) || contains(normalizedRight, normalizedLeft);
}

function assertPrivatePermissions(
  mode: number,
  subject: string,
  platform: NodeJS.Platform = process.platform,
): void {
  if (platform !== "win32" && (mode & 0o022) !== 0) {
    throw new Error(`Packaged QA ${subject} must not be writable by group or others.`);
  }
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
