import type { DesktopBackendStatus } from "../../../contracts/desktop-ipc.js";

export function backendStatusKeepsApiConnection(
  status: DesktopBackendStatus,
): status is Extract<DesktopBackendStatus, { state: "ready" | "degraded" }> {
  return status.state === "ready" || status.state === "degraded";
}
