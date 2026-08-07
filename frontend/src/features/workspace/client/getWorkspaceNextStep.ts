import type { WorkspaceTaskStatus } from "@/types";

export interface WorkspaceNextStepInput {
  status: WorkspaceTaskStatus;
  hasDraft: boolean;
  hasPrimaryMaterial: boolean;
  cancellationReason?: string | null;
  canContinueManually?: boolean;
  canWriteFollowUp?: boolean;
}

export interface WorkspaceNextStep {
  title: string;
}

export const getWorkspaceNextStep = (
  input: WorkspaceNextStepInput,
): WorkspaceNextStep => {
  if (input.canContinueManually) {
    return { title: "作为单独联系继续" };
  }

  if (input.canWriteFollowUp) {
    return { title: "写跟进邮件" };
  }

  if (input.status === "send_failed") {
    return { title: "查看失败原因并重试" };
  }

  if (input.status === "schedule_missed") {
    return { title: "重新安排发送时间" };
  }

  if (!input.hasPrimaryMaterial) {
    return { title: "选择 AI 写信参考材料" };
  }

  if (!input.hasDraft) {
    return { title: "生成邮件草稿" };
  }

  if (input.status === "scheduled") {
    return { title: "确认发送时间" };
  }

  return { title: "检查后发送" };
};
