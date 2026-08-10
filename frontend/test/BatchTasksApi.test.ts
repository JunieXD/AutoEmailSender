import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  deleteBatchTask,
  getBatchTaskResendContext,
  restoreBatchTask,
  rewriteBatchTaskItemDraft,
  updateBatchTaskItemOutreachConfig,
} from "@/lib/api/batchTasksApi";

const mockedApiFetch = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/client", () => ({
  apiFetch: mockedApiFetch,
}));

describe("batchTasksApi", () => {
  beforeEach(() => {
    mockedApiFetch.mockReset();
  });

  it("moves a batch task to trash with the expected URL", async () => {
    mockedApiFetch.mockResolvedValue({});

    await deleteBatchTask(7);

    expect(mockedApiFetch).toHaveBeenCalledWith("/api/batch-tasks/7/delete", {
      method: "POST",
    });
  });

  it("fetches batch task resend context with the expected URL", async () => {
    mockedApiFetch.mockResolvedValue({});

    await getBatchTaskResendContext(12);

    expect(mockedApiFetch).toHaveBeenCalledWith("/api/batch-tasks/12/resend-context");
  });

  it("updates outreach config through the scoped batch item URL", async () => {
    mockedApiFetch.mockResolvedValue({});
    const payload = {
      outreach_generation_mode: "template" as const,
      outreach_template_id: 19,
      outreach_template_subject: "模板主题",
      outreach_template_body_text: "模板正文",
      outreach_template_body_html: "<p>模板正文</p>",
    };

    await updateBatchTaskItemOutreachConfig(7, 11, payload);

    expect(mockedApiFetch).toHaveBeenCalledWith(
      "/api/batch-tasks/7/items/11/outreach-config",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
  });

  it("rewrites a draft through the scoped batch item URL", async () => {
    mockedApiFetch.mockResolvedValue({});
    const payload = {
      subject: "当前主题",
      body_text: "当前正文",
      body_html: "<p>当前正文</p>",
      selected_material_ids: [3],
      llm_profile_id: 5,
    };

    await rewriteBatchTaskItemDraft(7, 11, payload);

    expect(mockedApiFetch).toHaveBeenCalledWith(
      "/api/batch-tasks/7/items/11/rewrite-draft",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
  });

  it("restores a batch task from trash with the expected URL", async () => {
    mockedApiFetch.mockResolvedValue({});

    await restoreBatchTask(7);

    expect(mockedApiFetch).toHaveBeenCalledWith("/api/batch-tasks/7/restore", {
      method: "POST",
    });
  });
});
