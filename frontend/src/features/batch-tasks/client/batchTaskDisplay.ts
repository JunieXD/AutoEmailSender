import type { BatchTaskCardDTO, BatchTaskItemDTO } from "@/types";

export type BatchPendingItemAction =
  | { kind: "link"; text: string }
  | { kind: "message"; text: string };

export const getBatchTaskWaitingSendCount = (task: BatchTaskCardDTO) =>
  task.approved_count + task.scheduled_count;

export const getBatchTaskItemCancellationText = (item: BatchTaskItemDTO) => {
  if (item.cancellation_reason === "schedule_expired") {
    return "发送窗口已过期";
  }
  if (item.cancellation_reason === "batch_stopped") {
    return "批量任务已中止";
  }
  return null;
};

export const buildBatchPendingItemAction = (
  item: BatchTaskItemDTO,
  task: BatchTaskCardDTO,
): BatchPendingItemAction | null => {
  if (item.status === "canceled") {
    return null;
  }
  if (item.status === "matched") {
    return null;
  }
  if (item.status === "review_required") {
    return { kind: "link", text: "审核草稿" };
  }
  if (task.schedule_type === "scheduled" && item.status === "approved" && !item.scheduled_at) {
    return { kind: "message", text: "计划时间缺失，请重新安排发送" };
  }
  if (item.status === "scheduled") {
    return { kind: "message", text: "等待计划时间自动发送" };
  }
  return { kind: "link", text: "去处理" };
};
