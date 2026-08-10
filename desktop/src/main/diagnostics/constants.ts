import path from "node:path";

export const BETA_DIAGNOSTICS_SCHEMA_VERSION = 1;
export const BETA_DIAGNOSTICS_DIRECTORY_NAME = "beta-diagnostics";
export const BETA_DIAGNOSTICS_RETENTION_DAYS = 14;
export const BETA_DIAGNOSTICS_MAX_TOTAL_BYTES = 64 * 1024 * 1024;
export const BETA_DIAGNOSTICS_MAX_SEGMENT_BYTES = 2 * 1024 * 1024;
export const BETA_DIAGNOSTICS_MAX_SEGMENT_AGE_MS = 60 * 60 * 1000;
export const BETA_DIAGNOSTICS_MAX_RECORD_BYTES = 64 * 1024;
export const BETA_DIAGNOSTICS_RESOURCE_SAMPLE_INTERVAL_MS = 10_000;

export type BetaDiagnosticComponent = "electron" | "api" | "worker" | "combined";
export type BetaDiagnosticStream = "timeline" | "resource-samples";

export function getBetaDiagnosticsRoot(userDataPath: string): string {
  return path.join(userDataPath, BETA_DIAGNOSTICS_DIRECTORY_NAME);
}

export function isBetaDiagnosticsEnabled(input: {
  appVersion: string;
  environmentValue?: string;
}): boolean {
  if (input.environmentValue === "enabled-for-tests-only") {
    return true;
  }
  return /-(?:alpha|beta|rc)(?:[.-]|$)/iu.test(input.appVersion.trim());
}
