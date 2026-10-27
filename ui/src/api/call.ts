import { invoke } from "@tauri-apps/api/core";
import type { CallError, Operations } from "./types";

/** Runs an operation in the core (crates/api). Rejects with a `CallError`. */
export function call<K extends keyof Operations>(
  op: K,
  input: Operations[K]["input"],
): Promise<Operations[K]["output"]> {
  return invoke<Operations[K]["output"]>("call", { op, input });
}

/** The message to show for a failed call, or for anything else that was thrown. */
export function errorMessage(error: unknown): string {
  return isCallError(error) ? error.message : String(error);
}

function isCallError(error: unknown): error is CallError {
  return typeof error === "object" && error !== null && "message" in error && typeof error.message === "string";
}
