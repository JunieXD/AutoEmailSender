import type { QueryClient } from "@tanstack/react-query";

/** After a task, its drafts or the emails it sends changed, here or in the core. */
export function refreshTasks(client: QueryClient) {
  for (const key of [
    "taskWork",
    "listTasks",
    "getTask",
    "listTaskDrafts",
    "getTaskDraft",
    "planTask",
    "restartTask",
    "sendingPlan",
    "scheduledWork",
    "stats",
  ])
    void client.invalidateQueries({ queryKey: [key] });
}
