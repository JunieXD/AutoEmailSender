import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  benchmarkTargetKey,
  buildPaginationPages,
  buildBenchmarkSummary,
  buildBenchmarkVersionStats,
  buildLatestBenchmarkRecords,
  groupBenchmarkHistory,
  type CrawlBenchmarkPayload,
  type CrawlBenchmarkRecord,
} from "../.vitepress/theme/crawlBenchmark";

const readWebsiteFile = (path: string) => readFileSync(resolve(path), "utf8");
const publicData = JSON.parse(readWebsiteFile("data/crawl-benchmark.json")) as CrawlBenchmarkPayload;
const publicSchema = JSON.parse(readWebsiteFile("data/crawl-benchmark.schema.json")) as {
  properties: { schemaVersion: { const: number } };
  $defs: { record: { required: string[]; properties: Record<string, unknown> } };
};

const recordFields = [
  "appVersion",
  "cachedTokens",
  "candidateCount",
  "durationSeconds",
  "emailCount",
  "enrichmentFailedCount",
  "enrichmentPendingCount",
  "enrichmentSelectedCount",
  "enrichmentSucceededCount",
  "entryType",
  "inputTokens",
  "modelName",
  "outputTokens",
  "pageCount",
  "publicStatus",
  "recordId",
  "researchDirectionCount",
  "school",
  "sourceKind",
  "startUrl",
  "testedAt",
  "titleCount",
  "totalTokens",
  "university",
].sort();

function makeRecord(overrides: Partial<CrawlBenchmarkRecord> = {}): CrawlBenchmarkRecord {
  return {
    recordId: "record-default",
    sourceKind: "database",
    university: "测试大学",
    school: "计算机学院",
    startUrl: "https://example.edu/faculty",
    entryType: "list",
    testedAt: "2026-08-01T00:00:00Z",
    appVersion: "2.3.7",
    modelName: "test-model",
    publicStatus: "verified",
    candidateCount: 10,
    emailCount: 8,
    enrichmentSelectedCount: 10,
    enrichmentSucceededCount: 8,
    enrichmentPendingCount: 1,
    enrichmentFailedCount: 1,
    titleCount: 7,
    researchDirectionCount: 6,
    pageCount: 12,
    durationSeconds: 60,
    inputTokens: 100,
    cachedTokens: 20,
    outputTokens: 30,
    totalTokens: 130,
    ...overrides,
  };
}

describe("public crawl benchmark data", () => {
  it("contains only the documented aggregate fields", () => {
    expect(publicData.schemaVersion).toBe(3);
    expect(Number.isNaN(Date.parse(publicData.generatedAt))).toBe(false);
    expect(Object.keys(publicData).sort()).toEqual([
      "generatedAt",
      "methodology",
      "records",
      "schemaVersion",
    ]);
    expect(Object.keys(publicData.methodology).sort()).toEqual([
      "coverageDefinition",
      "privacy",
      "recordPolicy",
    ]);

    for (const record of publicData.records) {
      expect(Object.keys(record).sort()).toEqual(recordFields);
    }
    expect(publicSchema.properties.schemaVersion.const).toBe(3);
    expect([...publicSchema.$defs.record.required].sort()).toEqual(recordFields);
    expect(Object.keys(publicSchema.$defs.record.properties).sort()).toEqual(recordFields);

    const serialized = JSON.stringify(publicData);
    expect(serialized).not.toMatch(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/);
    expect(serialized).not.toMatch(/\bsk-[A-Za-z0-9_-]{12,}\b/);
  });

  it("uses unique ids, safe URLs, bounded counts, and public statuses", () => {
    const recordIds = new Set<string>();

    for (const record of publicData.records) {
      expect(recordIds.has(record.recordId)).toBe(false);
      recordIds.add(record.recordId);

      expect(record.university.trim().length).toBeGreaterThan(1);
      expect(record.school.trim().length).toBeGreaterThan(1);
      const sourceUrl = new URL(record.startUrl);
      expect(["http:", "https:"]).toContain(sourceUrl.protocol);
      expect(sourceUrl.username).toBe("");
      expect(sourceUrl.password).toBe("");

      for (const count of [
        record.candidateCount,
        record.emailCount,
        record.titleCount,
        record.researchDirectionCount,
        record.durationSeconds,
        record.inputTokens,
        record.cachedTokens,
        record.outputTokens,
        record.totalTokens,
      ]) {
        expect(Number.isInteger(count)).toBe(true);
        expect(count).toBeGreaterThanOrEqual(0);
      }

      expect(record.emailCount).toBeLessThanOrEqual(record.candidateCount);
      expect(record.titleCount).toBeLessThanOrEqual(record.candidateCount);
      expect(record.researchDirectionCount).toBeLessThanOrEqual(record.candidateCount);
      for (const count of [
        record.enrichmentSelectedCount,
        record.enrichmentSucceededCount,
        record.enrichmentPendingCount,
        record.enrichmentFailedCount,
      ]) {
        if (count !== null) {
          expect(Number.isInteger(count)).toBe(true);
          expect(count).toBeGreaterThanOrEqual(0);
          expect(count).toBeLessThanOrEqual(record.candidateCount);
        }
      }
      if (record.candidateCount === 0) {
        expect(record.publicStatus).toBe("adapting");
      }
      if (record.publicStatus === "verified") {
        expect(record.candidateCount).toBeGreaterThan(0);
      }
    }
  });
});

describe("crawl benchmark calculations", () => {
  it("selects the latest run per school and keeps history newest first", () => {
    const early = makeRecord({
      recordId: "early",
      testedAt: null,
      appVersion: "2.3.7",
      candidateCount: 5,
    });
    const latest = makeRecord({
      recordId: "latest",
      testedAt: "2026-08-02T00:00:00Z",
      appVersion: null,
      candidateCount: 12,
    });
    const otherSchool = makeRecord({ recordId: "other", school: "软件学院" });
    const records = [early, otherSchool, latest];

    const selected = buildLatestBenchmarkRecords(records);
    expect(selected).toHaveLength(2);
    expect(selected.find((record) => record.school === "计算机学院")?.recordId).toBe("latest");

    const history = groupBenchmarkHistory(records).get(benchmarkTargetKey(latest));
    expect(history?.map((record) => record.recordId)).toEqual(["latest", "early"]);
  });

  it("aggregates latest targets without treating zero candidates as coverage", () => {
    const records = [
      makeRecord({
        recordId: "one",
        candidateCount: 10,
        emailCount: 5,
        titleCount: 8,
        researchDirectionCount: 4,
      }),
      makeRecord({
        recordId: "two",
        school: "软件学院",
        publicStatus: "adapting",
        candidateCount: 0,
        emailCount: 0,
        titleCount: 0,
        researchDirectionCount: 0,
      }),
    ];

    expect(buildBenchmarkSummary(records)).toEqual({
      universityCount: 1,
      targetCount: 2,
      verifiedTargetCount: 1,
      candidateCount: 10,
      emailCoverage: 0.5,
      titleCoverage: 0.8,
      researchDirectionCoverage: 0.4,
    });
  });

  it("groups version statistics and sorts semantic numeric versions", () => {
    const stats = buildBenchmarkVersionStats([
      makeRecord({ recordId: "v29", appVersion: "2.9.0" }),
      makeRecord({ recordId: "v210a", appVersion: "2.10.0", candidateCount: 5 }),
      makeRecord({ recordId: "v210b", appVersion: "2.10.0", school: "软件学院" }),
      makeRecord({ recordId: "unknown", appVersion: null, school: "人工智能学院" }),
    ]);

    expect(stats.map((stat) => stat.version)).toEqual(["2.10.0", "2.9.0"]);
    expect(stats[0]).toMatchObject({ recordCount: 2, candidateCount: 15 });
  });

  it("keeps pagination bounded and centered with many pages", () => {
    expect(buildPaginationPages(1, 100)).toEqual([1, 2, 3, 4, 5]);
    expect(buildPaginationPages(50, 100)).toEqual([48, 49, 50, 51, 52]);
    expect(buildPaginationPages(100, 100)).toEqual([96, 97, 98, 99, 100]);
    expect(buildPaginationPages(999, 3)).toEqual([1, 2, 3]);
  });
});

describe("crawl benchmark website entry points", () => {
  it("links the dashboard from navigation and the homepage", () => {
    const config = readWebsiteFile(".vitepress/config.mts");
    const homepage = readWebsiteFile("index.md");
    const benchmarkPage = readWebsiteFile("crawl-benchmark.md");

    expect(config).toContain('{ text: "实测数据", link: "/crawl-benchmark" }');
    expect(homepage).toContain("import CrawlBenchmarkPromo");
    expect(homepage).toContain("<CrawlBenchmarkPromo />");
    expect(benchmarkPage).toContain("layout: page");
    expect(benchmarkPage).toContain("import CrawlBenchmark");
    expect(benchmarkPage).toContain("<CrawlBenchmark />");
    expect(`${homepage}\n${benchmarkPage}`).not.toMatch(/Excel|导出抓取测试报告/);
  });

  it("uses the compact paginated dashboard and custom filters", () => {
    const component = readWebsiteFile(".vitepress/theme/components/CrawlBenchmark.vue");
    const customFilter = readWebsiteFile(
      ".vitepress/theme/components/BenchmarkFilterSelect.vue",
    );
    const benchmarkPage = readWebsiteFile("crawl-benchmark.md");

    expect(component).toContain("const pageSize = 8;");
    expect(component).toContain('v-for="record in paginatedRecords"');
    expect(component).toContain('class="school-card-list"');
    expect(component).toContain("<BenchmarkFilterSelect");
    expect(component).not.toContain("<select");
    expect(component).toContain(
      '<div v-if="!filteredRecords.length" class="benchmark-empty-state">',
    );
    expect(component).not.toContain('<div v-else class="benchmark-empty-state">');
    expect(customFilter).toContain('role="combobox"');
    expect(customFilter).toContain('role="listbox"');
    expect(component).toContain("<h1>智能抓取效果展示</h1>");
    expect(component).toContain("<h2>数据来源和统计说明</h2>");
    expect(component).toContain("已发起 ${formatBenchmarkNumber(record.enrichmentSelectedCount ?? 0)} 位");
    expect(component).toContain("formatBenchmarkNumber(record.candidateCount)");
    expect(component).not.toContain("公开、克制，也经得起追溯");
    expect(benchmarkPage).toContain("title: 智能抓取效果展示");
  });
});
