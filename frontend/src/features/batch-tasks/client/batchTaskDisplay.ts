import type { BatchTaskCardDTO, BatchTaskItemDTO } from "@/types";

export type BatchPendingItemAction =
  | { kind: "review"; text: string }
  | { kind: "professor"; text: string; href: string }
  | { kind: "profile"; text: string; href: string }
  | { kind: "retry"; text: string }
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
  if (item.status === "review_required") {
    return { kind: "review", text: "审核草稿" };
  }
  if (item.status === "approved") {
    if (task.schedule_type === "scheduled" && !item.scheduled_at) {
      return { kind: "message", text: "计划时间缺失，请重新安排发送" };
    }
    return { kind: "message", text: "等待自动发送" };
  }
  if (item.status === "scheduled") {
    return { kind: "message", text: "等待计划时间自动发送" };
  }
  switch (item.next_action) {
    case "waiting_draft_generation":
      return { kind: "message", text: "等待后台生成草稿" };
    case "complete_professor_profile":
      return {
        kind: "professor",
        text: "补全导师资料",
        href: `/professors?keyword=${encodeURIComponent(item.professor_email || item.professor_name)}`,
      };
    case "select_primary_material":
      return { kind: "profile", text: "选择默认材料", href: "/profile" };
    case "waiting_send":
      return { kind: "message", text: "等待自动发送" };
    case "waiting_scheduled_send":
      return { kind: "message", text: "等待计划时间自动发送" };
    case "missing_schedule":
      return { kind: "message", text: "计划时间缺失，请重新安排发送" };
    case "retry_draft_generation":
      return { kind: "retry", text: "重新生成草稿" };
    case "send_failed":
      return { kind: "message", text: "请检查发送失败原因" };
    default:
      return null;
  }
};
