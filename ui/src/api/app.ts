import { invoke } from "@tauri-apps/api/core";

// Commands of the desktop app itself (crates/app), beside the core's operations in call.ts.

/** Why the core could not start, or null when it is running. */
export function startupFailure(): Promise<string | null> {
  return invoke<string | null>("startup_failure");
}

/** Asks where to save the logs and saves them there; false when the user cancels. */
export function exportLogs(): Promise<boolean> {
  return invoke<boolean>("export_logs");
}

/** Where the app keeps its data. */
export function dataLocation(): Promise<string> {
  return invoke<string>("data_location");
}

/** Opens the data directory in Finder or Explorer. */
export function openDataLocation(): Promise<void> {
  return invoke<void>("open_data_location");
}
