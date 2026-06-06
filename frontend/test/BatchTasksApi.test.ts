import { beforeEach, describe, expect, it, vi } from "vitest";
import { deleteBatchTask, getBatchTaskResendContext, restoreBatchTask } from "@/lib/api/batchTasksApi";

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
  it("restores a batch task from trash with the expected URL", async () => {
    mockedApiFetch.mockResolvedValue({});

    await restoreBatchTask(7);

    expect(mockedApiFetch).toHaveBeenCalledWith("/api/batch-tasks/7/restore", {
      method: "POST",
    });
  });
});
