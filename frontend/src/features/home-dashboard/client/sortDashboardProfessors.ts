import type { ProfessorDashboardItemDTO } from "@/types";

export type ProfessorDashboardSortKey =
  | "latest"
  | "matchScoreDesc"
  | "sentCountDesc"
  | "nameAsc"
  | "lastSentAt"
  | "lastRepliedAt";

export type ProfessorDashboardSortDirection = "asc" | "desc";

export const isProfessorDashboardTimeSortKey = (
  sortKey: ProfessorDashboardSortKey,
): sortKey is "lastSentAt" | "lastRepliedAt" =>
  sortKey === "lastSentAt" || sortKey === "lastRepliedAt";

export const PROFESSOR_DASHBOARD_SORT_OPTIONS: Array<{
  value: ProfessorDashboardSortKey;
  label: string;
}> = [
  { value: "latest", label: "最新导入" },
  { value: "matchScoreDesc", label: "匹配度高到低" },
  { value: "sentCountDesc", label: "发送次数高到低" },
  { value: "nameAsc", label: "姓名 A-Z" },
  { value: "lastSentAt", label: "发送时间" },
  { value: "lastRepliedAt", label: "回复时间" },
];

const getTimeSortValue = (
  professor: ProfessorDashboardItemDTO,
  sortKey: "lastSentAt" | "lastRepliedAt",
) => {
  const rawValue =
    sortKey === "lastSentAt"
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
  sortKey: "lastSentAt" | "lastRepliedAt",
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
  direction: ProfessorDashboardSortDirection = "desc",
): ProfessorDashboardItemDTO[] => {
  const sorted = [...professors];

  if (isProfessorDashboardTimeSortKey(sortKey)) {
    return sortByOptionalTime(sorted, sortKey, direction);
  }

  if (sortKey === "matchScoreDesc") {
    return sorted.sort(
      (left, right) => (right.match_score ?? -1) - (left.match_score ?? -1),
    );
  }

  if (sortKey === "sentCountDesc") {
    return sorted.sort((left, right) => right.sent_count - left.sent_count);
  }

  if (sortKey === "nameAsc") {
    return sorted.sort((left, right) => left.name.localeCompare(right.name));
  }

  return sorted;
};
