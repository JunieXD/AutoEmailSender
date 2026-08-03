import rawBenchmarkPayload from "../../data/crawl-benchmark.json";


export type BenchmarkPublicStatus = "verified" | "adapting";

export interface CrawlBenchmarkRecord {
  recordId: string;
  sourceKind: "database" | "legacy_xlsx";
  university: string;
  school: string;
  startUrl: string;
  entryType: "list" | "profile";
  testedAt: string | null;
  appVersion: string | null;
  runtimeVersion: string | null;
  modelName: string | null;
  publicStatus: BenchmarkPublicStatus;
  candidateCount: number;
  emailCount: number;
  titleCount: number;
  researchDirectionCount: number;
  enrichmentSelectedCount: number | null;
  enrichmentSucceededCount: number | null;
  enrichmentPendingCount: number | null;
  enrichmentFailedCount: number | null;
  pageCount: number | null;
  durationSeconds: number;
  inputTokens: number;
  cachedTokens: number;
  outputTokens: number;
  totalTokens: number;
}

export interface CrawlBenchmarkPayload {
  schemaVersion: number;
  generatedAt: string;
  methodology: {
    coverageDefinition: string;
    recordPolicy: string;
    privacy: string;
  };
  records: CrawlBenchmarkRecord[];
}

export interface CrawlBenchmarkSummary {
  universityCount: number;
  targetCount: number;
  verifiedTargetCount: number;
  candidateCount: number;
  emailCoverage: number;
  titleCoverage: number;
  researchDirectionCoverage: number;
}

export interface CrawlBenchmarkVersionStat {
  version: string;
  recordCount: number;
  candidateCount: number;
  emailCoverage: number;
  titleCoverage: number;
  researchDirectionCoverage: number;
}

export const crawlBenchmarkPayload = rawBenchmarkPayload as CrawlBenchmarkPayload;

export function benchmarkTargetKey(record: CrawlBenchmarkRecord): string {
  return `${record.university.trim()}\u0000${record.school.trim()}`;
}

export function compareBenchmarkRecords(
  left: CrawlBenchmarkRecord,
  right: CrawlBenchmarkRecord,
): number {
  const leftTimestamp = left.testedAt ? Date.parse(left.testedAt) : -1;
  const rightTimestamp = right.testedAt ? Date.parse(right.testedAt) : -1;
  if (leftTimestamp !== rightTimestamp) {
    return leftTimestamp - rightTimestamp;
  }

  const versionComparison = compareVersions(left.appVersion, right.appVersion);
  if (versionComparison !== 0) {
    return versionComparison;
  }
  if (left.sourceKind !== right.sourceKind) {
    return left.sourceKind === "database" ? 1 : -1;
  }
  return left.recordId.localeCompare(right.recordId);
}

export function buildLatestBenchmarkRecords(
  records: CrawlBenchmarkRecord[],
): CrawlBenchmarkRecord[] {
  const latestByTarget = new Map<string, CrawlBenchmarkRecord>();
  for (const record of records) {
    const key = benchmarkTargetKey(record);
    const current = latestByTarget.get(key);
    if (!current || compareBenchmarkRecords(record, current) > 0) {
      latestByTarget.set(key, record);
    }
  }
  return [...latestByTarget.values()].sort(
    (left, right) =>
      left.university.localeCompare(right.university, "zh-CN") ||
      left.school.localeCompare(right.school, "zh-CN"),
  );
}

export function groupBenchmarkHistory(
  records: CrawlBenchmarkRecord[],
): Map<string, CrawlBenchmarkRecord[]> {
  const grouped = new Map<string, CrawlBenchmarkRecord[]>();
  for (const record of records) {
    const key = benchmarkTargetKey(record);
    const history = grouped.get(key) ?? [];
    history.push(record);
    grouped.set(key, history);
  }
  for (const history of grouped.values()) {
    history.sort((left, right) => compareBenchmarkRecords(right, left));
  }
  return grouped;
}

export function buildBenchmarkSummary(
  latestRecords: CrawlBenchmarkRecord[],
): CrawlBenchmarkSummary {
  const candidateCount = sum(latestRecords, "candidateCount");
  return {
    universityCount: new Set(latestRecords.map((record) => record.university)).size,
    targetCount: latestRecords.length,
    verifiedTargetCount: latestRecords.filter((record) => record.publicStatus === "verified").length,
    candidateCount,
    emailCoverage: safeCoverage(sum(latestRecords, "emailCount"), candidateCount),
    titleCoverage: safeCoverage(sum(latestRecords, "titleCount"), candidateCount),
    researchDirectionCoverage: safeCoverage(
      sum(latestRecords, "researchDirectionCount"),
      candidateCount,
    ),
  };
}

export function buildBenchmarkVersionStats(
  records: CrawlBenchmarkRecord[],
): CrawlBenchmarkVersionStat[] {
  const grouped = new Map<string, CrawlBenchmarkRecord[]>();
  for (const record of records) {
    if (!record.appVersion) continue;
    const group = grouped.get(record.appVersion) ?? [];
    group.push(record);
    grouped.set(record.appVersion, group);
  }
  return [...grouped.entries()]
    .map(([version, versionRecords]) => {
      const candidateCount = sum(versionRecords, "candidateCount");
      return {
        version,
        recordCount: versionRecords.length,
        candidateCount,
        emailCoverage: safeCoverage(sum(versionRecords, "emailCount"), candidateCount),
        titleCoverage: safeCoverage(sum(versionRecords, "titleCount"), candidateCount),
        researchDirectionCoverage: safeCoverage(
          sum(versionRecords, "researchDirectionCount"),
          candidateCount,
        ),
      };
    })
    .sort((left, right) => compareVersions(right.version, left.version));
}

export function recordCoverage(count: number, candidateCount: number): number | null {
  return candidateCount > 0 ? Math.min(1, Math.max(0, count / candidateCount)) : null;
}

export function formatCoverage(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export function formatDuration(seconds: number): string {
  if (seconds <= 0) return "—";
  if (seconds < 60) return `${seconds} 秒`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = seconds % 60;
  if (hours > 0) {
    return `${hours} 小时 ${minutes} 分`;
  }
  return remainingSeconds > 0 ? `${minutes} 分 ${remainingSeconds} 秒` : `${minutes} 分`;
}

export function formatBenchmarkDate(value: string | null): string {
  if (!value) return "早期实测";
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return "早期实测";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(timestamp));
}

export function formatBenchmarkNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

export function buildPaginationPages(
  currentPage: number,
  totalPages: number,
  maxVisible = 5,
): number[] {
  const resolvedTotalPages = Math.max(1, Math.floor(totalPages));
  const resolvedCurrentPage = Math.min(
    resolvedTotalPages,
    Math.max(1, Math.floor(currentPage)),
  );
  const visibleCount = Math.min(
    resolvedTotalPages,
    Math.max(1, Math.floor(maxVisible)),
  );
  const start = Math.max(
    1,
    Math.min(
      resolvedCurrentPage - Math.floor(visibleCount / 2),
      resolvedTotalPages - visibleCount + 1,
    ),
  );
  return Array.from({ length: visibleCount }, (_, index) => start + index);
}

function sum(
  records: CrawlBenchmarkRecord[],
  key: "candidateCount" | "emailCount" | "titleCount" | "researchDirectionCount",
): number {
  return records.reduce((total, record) => total + record[key], 0);
}

function safeCoverage(count: number, candidateCount: number): number {
  return candidateCount > 0 ? Math.min(1, Math.max(0, count / candidateCount)) : 0;
}

function compareVersions(left: string | null, right: string | null): number {
  const leftParts = versionParts(left);
  const rightParts = versionParts(right);
  const length = Math.max(leftParts.length, rightParts.length);
  for (let index = 0; index < length; index += 1) {
    const difference = (leftParts[index] ?? 0) - (rightParts[index] ?? 0);
    if (difference !== 0) return difference;
  }
  return 0;
}

function versionParts(value: string | null): number[] {
  return value?.match(/\d+/g)?.map(Number) ?? [];
}
