import type {
  BatchTaskCardDTO,
  BatchTaskItemDTO,
  OutreachGenerationMode,
} from "@/types";

export type BatchPendingItemAction =
  | { kind: "review"; text: string }
  | { kind: "professor"; text: string; href: string }
  | { kind: "profile"; text: string; href: string }
  | { kind: "retry"; text: string }
  | { kind: "message"; text: string };

export const getBatchTaskWaitingSendCount = (task: BatchTaskCardDTO) =>
  task.approved_count + task.scheduled_count;

export const getOutreachGenerationModeLabel = (
  mode: OutreachGenerationMode | null | undefined,
) => {
  if (mode === "template") {
    return "直接套用模板";
  }
  if (mode === "llm") {
    return "AI 辅助写信";
  }
  return "未记录";
};

export const getOutreachTemplateSourceLabel = (source: {
  outreach_template_id?: number | null;
  outreach_template_name_snapshot?: string | null;
}) => {
  const snapshotName = source.outreach_template_name_snapshot?.trim();
  if (snapshotName) {
    return snapshotName;
  }
  if (source.outreach_template_id != null) {
    return "历史来源模板";
  }
  return "自定义本次内容";
};

export const getBatchTaskItemCancellationText = (item: BatchTaskItemDTO) => {
  if (item.cancellation_reason === "schedule_expired") {
    return "发送窗口已过期";
  }
  if (item.cancellation_reason === "batch_stopped") {
    return "批量任务已终止";
  }
  return null;
};

export const isBatchTaskItemMissingResearchDirection = (
  item: BatchTaskItemDTO,
) => {
  // The API now always includes the professor's current direction. Prefer it
  // over a historical fallback reason so the badge disappears as soon as the
  // profile is completed. The action/reason checks keep older responses
  // backwards compatible while the new field is absent.
  if (item.professor_research_direction !== undefined) {
    return !item.professor_research_direction?.trim();
  }
  return (
    item.next_action === "complete_professor_profile" ||
    item.draft_fallback_reason === "missing_research_direction"
  );
};

export const buildBatchPendingItemAction = (
  item: BatchTaskItemDTO,
  task: BatchTaskCardDTO,
): BatchPendingItemAction | null => {
  if (item.batch_send_canceled_at) {
    return null;
  }
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
