import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  deleteMatchAnalysisJob,
  getMatchAnalysisJob,
  restoreMatchAnalysisJob,
} from "@/lib/api/matchAnalysisJobsApi";

const mockedApiFetch = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/client", () => ({
  apiFetch: mockedApiFetch,
}));

describe("matchAnalysisJobsApi", () => {
  beforeEach(() => {
    mockedApiFetch.mockReset();
  });

  it("moves a match analysis job to trash with the expected URL", async () => {
    mockedApiFetch.mockResolvedValue({});

    await deleteMatchAnalysisJob(7);

    expect(mockedApiFetch).toHaveBeenCalledWith(
      "/api/match-analysis-jobs/7/delete",
      {
        method: "POST",
      },
    );
  });

  it("gets one match analysis job for background monitoring", async () => {
    mockedApiFetch.mockResolvedValue({});

    await getMatchAnalysisJob(7);

    expect(mockedApiFetch).toHaveBeenCalledWith("/api/match-analysis-jobs/7");
  });

  it("restores a match analysis job from trash with the expected URL", async () => {
    mockedApiFetch.mockResolvedValue({});

    await restoreMatchAnalysisJob(7);

    expect(mockedApiFetch).toHaveBeenCalledWith(
      "/api/match-analysis-jobs/7/restore",
      {
        method: "POST",
      },
    );
  });
});
