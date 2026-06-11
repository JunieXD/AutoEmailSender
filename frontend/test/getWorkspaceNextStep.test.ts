import { describe, expect, it } from "vitest";
import { getWorkspaceNextStep } from "@/features/workspace/client/getWorkspaceNextStep";

describe("getWorkspaceNextStep", () => {
  it("prompts for the primary material first when none is selected", () => {
    expect(
      getWorkspaceNextStep({
        status: "matched",
        hasDraft: false,
        hasPrimaryMaterial: false,
        cancellationReason: null,
        canContinueManually: false,
        canWriteFollowUp: false,
      }),
    ).toEqual({
      title: "选择 AI 写信参考材料",
    });
  });

  it("prompts to generate a draft when the primary material exists but no draft has been generated", () => {
    expect(
      getWorkspaceNextStep({
        status: "matched",
        hasDraft: false,
        hasPrimaryMaterial: true,
        cancellationReason: null,
        canContinueManually: false,
        canWriteFollowUp: false,
      }),
    ).toEqual({
      title: "生成邮件草稿",
    });
  });

  it("prompts to confirm scheduled sending for scheduled tasks", () => {
    expect(
      getWorkspaceNextStep({
        status: "scheduled",
        hasDraft: true,
        hasPrimaryMaterial: true,
        cancellationReason: null,
        canContinueManually: false,
        canWriteFollowUp: false,
      }),
    ).toEqual({
      title: "确认发送时间",
    });
  });

  it("falls back to manual review before sending in other cases", () => {
    expect(
      getWorkspaceNextStep({
        status: "approved",
        hasDraft: true,
        hasPrimaryMaterial: true,
        cancellationReason: null,
        canContinueManually: false,
        canWriteFollowUp: false,
      }),
    ).toEqual({
      title: "检查后发送",
    });
  });

  it("still prefers generating a draft when scheduled tasks have no draft yet", () => {
    expect(
      getWorkspaceNextStep({
        status: "scheduled",
        hasDraft: false,
        hasPrimaryMaterial: true,
        cancellationReason: null,
        canContinueManually: false,
        canWriteFollowUp: false,
      }),
    ).toEqual({
      title: "生成邮件草稿",
    });
  });

  it("still prefers selecting materials when other conditions are otherwise ready", () => {
    expect(
      getWorkspaceNextStep({
        status: "scheduled",
        hasDraft: true,
        hasPrimaryMaterial: false,
        cancellationReason: null,
        canContinueManually: false,
        canWriteFollowUp: false,
      }),
    ).toEqual({
      title: "选择 AI 写信参考材料",
    });
  });

  it.each([
    [
      "send_failed",
      {
        status: "send_failed",
        hasDraft: false,
        hasPrimaryMaterial: false,
        cancellationReason: null,
        canContinueManually: false,
        canWriteFollowUp: false,
      },
      "查看失败原因并重试",
    ],
    [
      "canceled",
      {
        status: "canceled",
        hasDraft: false,
        hasPrimaryMaterial: false,
        cancellationReason: "batch_stopped",
        canContinueManually: true,
        canWriteFollowUp: false,
      },
      "作为单独联系继续",
    ],
  ])(
    "keeps terminal status %s from falling back to precondition prompts",
    (_, input, title) => {
      expect(getWorkspaceNextStep(input)).toEqual({ title });
      expect(getWorkspaceNextStep(input).title).not.toBe("选择分析材料");
      expect(getWorkspaceNextStep(input).title).not.toBe("生成邮件草稿");
    },
  );

  it.each([
    {
      name: "sent",
      input: {
        status: "sent" as const,
        hasDraft: false,
        hasPrimaryMaterial: false,
        cancellationReason: null,
        canContinueManually: false,
        canWriteFollowUp: true,
      },
    },
    {
      name: "reply_detected",
      input: {
        status: "reply_detected" as const,
        hasDraft: false,
        hasPrimaryMaterial: false,
        cancellationReason: null,
        canContinueManually: false,
        canWriteFollowUp: true,
      },
    },
  ])("prefers follow-up guidance for %s tasks", ({ input }) => {
    expect(getWorkspaceNextStep(input)).toEqual({
      title: "写跟进邮件",
    });
  });

  it("prefers follow-up guidance whenever canWriteFollowUp is true, even for non-terminal statuses", () => {
    expect(
      getWorkspaceNextStep({
        status: "approved",
        hasDraft: false,
        hasPrimaryMaterial: false,
        cancellationReason: null,
        canContinueManually: false,
        canWriteFollowUp: true,
      }),
    ).toEqual({
      title: "写跟进邮件",
    });
  });

  it("keeps canceled batch-stopped guidance ahead of missing-material prompts", () => {
    expect(
      getWorkspaceNextStep({
        status: "approved",
        hasDraft: false,
        hasPrimaryMaterial: false,
        cancellationReason: null,
        canContinueManually: true,
        canWriteFollowUp: false,
      }),
    ).toEqual({
      title: "作为单独联系继续",
    });
  });
});
