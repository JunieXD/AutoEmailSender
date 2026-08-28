import type { ProfessorDashboardItemDTO } from "@/types";

export type ProfessorDashboardSortKey =
  | "latest"
  | "updatedAtDesc"
  | "matchScoreDesc"
  | "sentCountDesc"
  | "nameAsc"
  | "lastSentAt"
  | "lastRepliedAt";

export type ProfessorDashboardSortDirection = "asc" | "desc";

export const DEFAULT_PROFESSOR_DASHBOARD_SORT_KEY: ProfessorDashboardSortKey =
  "updatedAtDesc";

const isProfessorDashboardTimeSortKey = (
  sortKey: ProfessorDashboardSortKey,
): sortKey is "updatedAtDesc" | "lastSentAt" | "lastRepliedAt" =>
  sortKey === "updatedAtDesc" ||
  sortKey === "lastSentAt" ||
  sortKey === "lastRepliedAt";

export const DEFAULT_PROFESSOR_DASHBOARD_SORT_DIRECTIONS: Record<
  ProfessorDashboardSortKey,
  ProfessorDashboardSortDirection
> = {
  latest: "desc",
  updatedAtDesc: "desc",
  matchScoreDesc: "desc",
  sentCountDesc: "desc",
  nameAsc: "asc",
  lastSentAt: "desc",
  lastRepliedAt: "desc",
};

export const PROFESSOR_DASHBOARD_SORT_OPTIONS: Array<{
  value: ProfessorDashboardSortKey;
  label: string;
}> = [
  { value: "latest", label: "导入时间" },
  { value: "updatedAtDesc", label: "更新时间" },
  { value: "matchScoreDesc", label: "匹配度" },
  { value: "sentCountDesc", label: "发送次数" },
  { value: "nameAsc", label: "姓名" },
  { value: "lastSentAt", label: "发送时间" },
  { value: "lastRepliedAt", label: "回复时间" },
];

const getSortDirection = (
  sortKey: ProfessorDashboardSortKey,
  direction: ProfessorDashboardSortDirection | undefined,
) => direction ?? DEFAULT_PROFESSOR_DASHBOARD_SORT_DIRECTIONS[sortKey];

const directionMultiplier = (direction: ProfessorDashboardSortDirection) =>
  direction === "asc" ? 1 : -1;

const compareOptionalNumber = (
  left: number | null | undefined,
  right: number | null | undefined,
  direction: ProfessorDashboardSortDirection,
) => {
  if (left == null && right == null) {
    return 0;
  }
  if (left == null) {
    return 1;
  }
  if (right == null) {
    return -1;
  }
  return (left - right) * directionMultiplier(direction);
};

const getTimeSortValue = (
  professor: ProfessorDashboardItemDTO,
  sortKey: "updatedAtDesc" | "lastSentAt" | "lastRepliedAt",
) => {
  const rawValue =
    sortKey === "updatedAtDesc"
      ? professor.updated_at
      : sortKey === "lastSentAt"
      ? professor.last_sent_at
      : professor.last_replied_at;
  if (!rawValue) {
    return null;
  }

  const timestamp = Date.parse(rawValue);
  return Number.isFinite(timestamp) ? timestamp : null;
};

const sortByOptionalTime = (
  professors: ProfessorDashboardItemDTO[],
  sortKey: "updatedAtDesc" | "lastSentAt" | "lastRepliedAt",
  direction: ProfessorDashboardSortDirection,
) =>
  professors
    .map((professor, index) => ({
      professor,
      index,
      timestamp: getTimeSortValue(professor, sortKey),
    }))
    .sort((left, right) => {
      if (left.timestamp === null && right.timestamp === null) {
        return left.index - right.index;
      }
      if (left.timestamp === null) {
        return 1;
      }
      if (right.timestamp === null) {
        return -1;
      }
      if (left.timestamp === right.timestamp) {
        return left.index - right.index;
      }

      return direction === "asc"
        ? left.timestamp - right.timestamp
        : right.timestamp - left.timestamp;
    })
    .map((item) => item.professor);

export const sortDashboardProfessors = (
  professors: ProfessorDashboardItemDTO[],
  sortKey: ProfessorDashboardSortKey,
  direction?: ProfessorDashboardSortDirection,
): ProfessorDashboardItemDTO[] => {
  const sorted = [...professors];
  const resolvedDirection = getSortDirection(sortKey, direction);

  if (isProfessorDashboardTimeSortKey(sortKey)) {
    return sortByOptionalTime(sorted, sortKey, resolvedDirection);
  }

  if (sortKey === "matchScoreDesc") {
    return sorted.sort((left, right) =>
      compareOptionalNumber(left.match_score, right.match_score, resolvedDirection),
    );
  }

  if (sortKey === "sentCountDesc") {
    return sorted.sort(
      (left, right) =>
        (left.sent_count - right.sent_count) *
        directionMultiplier(resolvedDirection),
    );
  }

  if (sortKey === "nameAsc") {
    return sorted.sort(
      (left, right) =>
        left.name.localeCompare(right.name, "zh-CN") *
        directionMultiplier(resolvedDirection),
    );
  }

  return resolvedDirection === "asc" ? sorted.reverse() : sorted;
};
