import { invoke } from "@tauri-apps/api/core";
import type { ExportMentors, RestoreInfo, UpdateStatus } from "@/api/types";

// Commands of the desktop app itself (crates/app), beside the core's operations in call.ts.

/** Why the core could not start, or null when it is running. */
export function startupFailure(): Promise<string | null> {
  return invoke<string | null>("startup_failure");
}

/** Asks where to save the logs and saves them there; false when the user cancels. */
export function exportLogs(): Promise<boolean> {
  return invoke<boolean>("export_logs");
}

/** Saves the log lines of one crawl where the user chooses; false when canceled. */
export function exportCrawlLog(id: number): Promise<boolean> {
  return invoke<boolean>("export_crawl_log", { id });
}

/** Where the app keeps its data. */
export function dataLocation(): Promise<string> {
  return invoke<string>("data_location");
}

/** Opens the data directory in Finder or Explorer. */
export function openDataLocation(): Promise<void> {
  return invoke<void>("open_data_location");
}

/** Saves a mentor table where the user chooses; false when canceled. */
export function exportMentors(input: ExportMentors, fileName: string): Promise<boolean> {
  return invoke<boolean>("export_mentors", { input, fileName });
}

/** Reads the system's login item, including changes made outside the app. */
export function autostartEnabled(): Promise<boolean> {
  return invoke<boolean>("autostart_enabled");
}

/** Updates the system's login item; login launches stay in the background. */
export function setAutostart(enabled: boolean): Promise<void> {
  return invoke<void>("set_autostart", { enabled });
}

/** Saves a backup where the user chooses; false when canceled. */
export function exportBackup(): Promise<boolean> {
  return invoke<boolean>("export_backup");
}

/** Stages and checks the selected backup; null when canceled. */
export function prepareRestore(): Promise<RestoreInfo | null> {
  return invoke<RestoreInfo | null>("prepare_restore");
}

/** Discards the unconfirmed restore. */
export function cancelRestore(): Promise<void> {
  return invoke<void>("cancel_restore");
}

/** Backs up current data and restarts to install the confirmed restore. */
export function restoreBackup(): Promise<void> {
  return invoke<void>("restore_backup");
}

/** Current desktop update state; refresh on update-changed. */
export function updateStatus(): Promise<UpdateStatus> {
  return invoke<UpdateStatus>("update_status");
}

/** Checks now; automatic checks run in Rust even with no visible window. */
export function checkUpdate(): Promise<UpdateStatus> {
  return invoke<UpdateStatus>("check_update");
}

/** Downloads and verifies the update; progress is available through updateStatus. */
export function downloadUpdate(): Promise<void> {
  return invoke<void>("download_update");
}

/** Installs the verified package and restarts the app. */
export function restartUpdate(): Promise<void> {
  return invoke<void>("restart_update");
}
