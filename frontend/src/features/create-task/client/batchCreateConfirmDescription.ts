import type { OutreachGenerationMode } from "@/types";
import { getTaskModeCopy } from "@/features/create-task/client/taskCopy";

export const buildBatchCreateConfirmDescription = (
  taskMode: OutreachGenerationMode,
  scheduleType: "immediate" | "scheduled",
  templateName?: string | null,
) => {
  const actionDescription = taskMode === "template"
    ? scheduleType === "scheduled"
      ? "将套用模板，并按计划自动发送。"
      : "将套用模板并进入发送流程。"
    : scheduleType === "scheduled"
      ? "将生成 AI 草稿；审核后按计划发送。"
      : "将生成 AI 草稿；审核后手动发送。";

  return [
    `发信模板：${templateName?.trim() || "自定义本次内容"}`,
    `写信方式：${getTaskModeCopy(taskMode).title}`,
    actionDescription,
  ].join("\n");
};
