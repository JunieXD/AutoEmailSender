import {
  closeSync,
  constants as fsConstants,
  fstatSync,
  openSync,
  readFileSync,
} from "node:fs";
import path from "node:path";

import type { DesktopBetaBuildIdentity } from "../diagnostics/exporter.js";

const RELEASE_IDENTITY_SCHEMA_VERSION = 1;
const MAX_RELEASE_IDENTITY_BYTES = 64 * 1024;
const SOURCE_BRANCH_PATTERN = /^[A-Za-z0-9._/+@-]{1,200}$/u;
const SHA_PATTERN = /^[0-9a-f]{40}$/u;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const RUN_ID_PATTERN = /^[1-9][0-9]{0,19}$/u;

export type DesktopReleaseBuildIdentity = {
  releaseKind: "stable" | "prerelease" | "unknown";
  version: string | null;
  channel: "stable" | "alpha" | "beta" | "rc" | "unknown";
  defaultBackendMode: "combined" | "split";
  diagnosticsSchemaVersion: number;
  diagnostics: DesktopBetaBuildIdentity;
  errorCode?: "identity_missing" | "identity_invalid" | "identity_unreadable";
};

export type DesktopReleaseBuildIdentityOptions = {
  isPackaged: boolean;
  resourcesPath: string;
  appVersion: string;
  platform: NodeJS.Platform;
  environment?: NodeJS.ProcessEnv;
};

export function readDesktopReleaseBuildIdentity(
  options: DesktopReleaseBuildIdentityOptions,
): DesktopReleaseBuildIdentity {
  if (!options.isPackaged) {
    return developmentIdentity(options.environment ?? process.env, options.appVersion);
  }
  const identityPath = path.join(options.resourcesPath, "release-identity.json");
  let raw: unknown;
  try {
    raw = JSON.parse(readBoundedRegularFile(identityPath));
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    return fallbackIdentity(code === "ENOENT" ? "identity_missing" : "identity_unreadable");
  }
  const parsed = parsePackagedIdentity(raw, options.appVersion, options.platform);
  return parsed ?? fallbackIdentity("identity_invalid");
}

function parsePackagedIdentity(
  value: unknown,
  appVersion: string,
  platform: NodeJS.Platform,
): DesktopReleaseBuildIdentity | null {
  if (!isRecord(value) || value.schema_version !== RELEASE_IDENTITY_SCHEMA_VERSION) {
    return null;
  }
  if (
    value.release_kind === "stable"
    && value.channel === "stable"
    && value.default_backend_mode === "combined"
    && value.diagnostics_schema_version === 1
  ) {
    return {
      releaseKind: "stable",
      version: typeof value.version === "string" ? value.version : null,
      channel: "stable",
      defaultBackendMode: "combined",
      diagnosticsSchemaVersion: 1,
      diagnostics: {},
    };
  }
  if (value.release_kind !== "prerelease" || value.version !== appVersion) {
    return null;
  }
  const channel = inferPrereleaseChannel(appVersion);
  if (
    channel === null
    || value.channel !== channel
    || value.default_backend_mode !== "split"
    || value.diagnostics_schema_version !== 1
    || typeof value.source_branch !== "string"
    || !isSafeSourceBranch(value.source_branch)
    || typeof value.release_sha !== "string"
    || !SHA_PATTERN.test(value.release_sha)
    || typeof value.candidate_run_id !== "string"
    || !RUN_ID_PATTERN.test(value.candidate_run_id)
    || typeof value.candidate_asset_name !== "string"
    || value.candidate_asset_name !== expectedAssetName(platform, appVersion)
    || !(
      value.candidate_asset_sha256 === null
      || (
        typeof value.candidate_asset_sha256 === "string"
        && SHA256_PATTERN.test(value.candidate_asset_sha256)
      )
    )
  ) {
    return null;
  }
  return {
    releaseKind: "prerelease",
    version: appVersion,
    channel,
    defaultBackendMode: "split",
    diagnosticsSchemaVersion: 1,
    diagnostics: {
      sourceBranch: value.source_branch,
      releaseSha: value.release_sha,
      candidateRunId: value.candidate_run_id,
      candidateAssetName: value.candidate_asset_name,
      candidateAssetSha256: value.candidate_asset_sha256,
    },
  };
}

function developmentIdentity(
  environment: NodeJS.ProcessEnv,
  appVersion: string,
): DesktopReleaseBuildIdentity {
  const channel = inferPrereleaseChannel(appVersion) ?? "unknown";
  const sourceBranch = safeOptional(environment.AUTO_EMAIL_SENDER_RELEASE_SOURCE_BRANCH, isSafeSourceBranch);
  const releaseSha = safeOptional(environment.AUTO_EMAIL_SENDER_RELEASE_SHA, (value) => SHA_PATTERN.test(value));
  const candidateRunId = safeOptional(
    environment.AUTO_EMAIL_SENDER_CANDIDATE_RUN_ID,
    (value) => RUN_ID_PATTERN.test(value),
  );
  const candidateAssetName = safeOptional(
    environment.AUTO_EMAIL_SENDER_CANDIDATE_ASSET_NAME,
    (value) => path.basename(value) === value && /^[A-Za-z0-9_.+()-]{1,180}$/u.test(value),
  );
  const candidateAssetSha256 = safeOptional(
    environment.AUTO_EMAIL_SENDER_CANDIDATE_ASSET_SHA256,
    (value) => SHA256_PATTERN.test(value),
  );
  return {
    releaseKind: channel === "unknown" ? "unknown" : "prerelease",
    version: appVersion,
    channel,
    defaultBackendMode: channel === "unknown" ? "combined" : "split",
    diagnosticsSchemaVersion: 1,
    diagnostics: {
      ...(sourceBranch ? { sourceBranch } : {}),
      ...(releaseSha ? { releaseSha } : {}),
      ...(candidateRunId ? { candidateRunId } : {}),
      ...(candidateAssetName ? { candidateAssetName } : {}),
      ...(candidateAssetSha256 ? { candidateAssetSha256 } : {}),
    },
  };
}

function fallbackIdentity(
  errorCode: DesktopReleaseBuildIdentity["errorCode"],
): DesktopReleaseBuildIdentity {
  return {
    releaseKind: "unknown",
    version: null,
    channel: "unknown",
    defaultBackendMode: "combined",
    diagnosticsSchemaVersion: 1,
    diagnostics: {},
    errorCode,
  };
}

function readBoundedRegularFile(filePath: string): string {
  const noFollow = typeof fsConstants.O_NOFOLLOW === "number" ? fsConstants.O_NOFOLLOW : 0;
  const descriptor = openSync(filePath, fsConstants.O_RDONLY | noFollow);
  try {
    const fileStat = fstatSync(descriptor);
    if (!fileStat.isFile() || fileStat.size > MAX_RELEASE_IDENTITY_BYTES) {
      throw new Error("Release identity is not a bounded regular file.");
    }
    return readFileSync(descriptor, "utf8");
  } finally {
    closeSync(descriptor);
  }
}

function inferPrereleaseChannel(value: string): "alpha" | "beta" | "rc" | null {
  const match = /-(alpha|beta|rc)(?:\.|$)/iu.exec(value.trim());
  return match ? match[1].toLowerCase() as "alpha" | "beta" | "rc" : null;
}

function expectedAssetName(platform: NodeJS.Platform, version: string): string | null {
  if (platform === "win32") return `AutoEmailSender-Setup-${version}.exe`;
  if (platform === "darwin") return `AutoEmailSender-${version}-arm64.dmg`;
  return null;
}

function isSafeSourceBranch(value: string): boolean {
  return SOURCE_BRANCH_PATTERN.test(value)
    && !value.startsWith("refs/")
    && !value.startsWith("-")
    && !value.startsWith("/")
    && !value.endsWith("/")
    && !value.endsWith(".")
    && !value.includes("..")
    && !value.includes("@{")
    && !value.includes("//")
    && !value.split("/").some((part) => part.endsWith(".lock"));
}

function safeOptional(
  value: string | undefined,
  predicate: (candidate: string) => boolean,
): string | null {
  const normalized = value?.trim();
  return normalized && predicate(normalized) ? normalized : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
