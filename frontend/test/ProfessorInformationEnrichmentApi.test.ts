import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  cancelProfessorInformationEnrichmentJob,
  createProfessorInformationEnrichmentJob,
  createSingleProfessorInformationEnrichment,
  listProfessorInformationEnrichmentItems,
  listProfessorInformationEnrichmentItemsPage,
  listProfessorInformationEnrichmentJobs,
  listProfessorInformationEnrichmentJobsPage,
  restoreProfessorInformationEnrichmentJob,
  retryFailedProfessorInformationEnrichmentJob,
} from "@/entities/professor/api/informationEnrichment";

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
    await listProfessorInformationEnrichmentItemsPage(23, {
      cursor: 20,
      limit: 10,
      status: "failed",
    });

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
    expect(mockedApiFetch).toHaveBeenNthCalledWith(
      3,
      "/api/professor-information-enrichment-jobs/23/items/page",
      undefined,
      { cursor: 20, limit: 10, status: "failed" },
    );
  });

  it("lists a filtered task-center information enrichment page", async () => {
    await listProfessorInformationEnrichmentJobsPage({
      offset: 32,
      limit: 16,
      view: "current",
      keyword: "重点导师",
      status: "running",
      sortKey: "updated",
      sortDirection: "desc",
      unpaged: true,
    });

    expect(mockedApiFetch).toHaveBeenCalledWith(
      "/api/professor-information-enrichment-jobs/page",
      undefined,
      {
        offset: 32,
        limit: 16,
        view: "current",
        keyword: "重点导师",
        status: "running",
        sort_key: "updated",
        sort_direction: "desc",
        unpaged: 1,
      },
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
