import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  cancelProfessorInformationEnrichmentJob,
  createProfessorInformationEnrichmentJob,
  createSingleProfessorInformationEnrichment,
  listProfessorInformationEnrichmentItems,
  listProfessorInformationEnrichmentJobs,
  restoreProfessorInformationEnrichmentJob,
  retryFailedProfessorInformationEnrichmentJob,
} from "@/lib/api/professorInformationEnrichmentApi";

const mockedApiFetch = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/client", () => ({
  apiFetch: mockedApiFetch,
}));

describe("professorInformationEnrichmentApi", () => {
  beforeEach(() => {
    mockedApiFetch.mockReset();
    mockedApiFetch.mockResolvedValue({});
  });

  it("starts a one-time enrichment for a saved professor", async () => {
    await createSingleProfessorInformationEnrichment(17, 3);

    expect(mockedApiFetch).toHaveBeenCalledWith(
      "/api/professors/17/information-enrichment",
      {
        method: "POST",
        body: JSON.stringify({ llm_profile_id: 3 }),
      },
    );
  });

  it("creates a batch enrichment job with selected professors", async () => {
    await createProfessorInformationEnrichmentJob({
      professorIds: [17, 18],
      llmProfileId: 3,
      name: "重点导师补全",
    });

    expect(mockedApiFetch).toHaveBeenCalledWith(
      "/api/professor-information-enrichment-jobs",
      {
        method: "POST",
        body: JSON.stringify({
          professor_ids: [17, 18],
          llm_profile_id: 3,
          name: "重点导师补全",
        }),
      },
    );
  });

  it("lists current jobs and job items from the dedicated endpoints", async () => {
    await listProfessorInformationEnrichmentJobs({ view: "current" });
    await listProfessorInformationEnrichmentItems(23);

    expect(mockedApiFetch).toHaveBeenNthCalledWith(
      1,
      "/api/professor-information-enrichment-jobs",
      undefined,
      { view: "current" },
    );
    expect(mockedApiFetch).toHaveBeenNthCalledWith(
      2,
      "/api/professor-information-enrichment-jobs/23/items",
    );
  });

  it("uses the dedicated lifecycle endpoints", async () => {
    await cancelProfessorInformationEnrichmentJob(23);
    await retryFailedProfessorInformationEnrichmentJob(23);
    await restoreProfessorInformationEnrichmentJob(23);

    expect(mockedApiFetch).toHaveBeenNthCalledWith(
      1,
      "/api/professor-information-enrichment-jobs/23/cancel",
      { method: "POST" },
    );
    expect(mockedApiFetch).toHaveBeenNthCalledWith(
      2,
      "/api/professor-information-enrichment-jobs/23/retry-failed",
      { method: "POST" },
    );
    expect(mockedApiFetch).toHaveBeenNthCalledWith(
      3,
      "/api/professor-information-enrichment-jobs/23/restore",
      { method: "POST" },
    );
  });
});
