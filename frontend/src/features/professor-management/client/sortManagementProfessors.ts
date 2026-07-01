import type { ProfessorManagementItemDTO } from "@/types";

export type ProfessorManagementSortKey =
  | "latest"
  | "updatedAtDesc"
  | "nameAsc"
  | "universityAsc";

export type ProfessorManagementSortDirection = "asc" | "desc";

export const DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS: Record<
  ProfessorManagementSortKey,
  ProfessorManagementSortDirection
> = {
  latest: "desc",
  updatedAtDesc: "desc",
  nameAsc: "asc",
  universityAsc: "asc",
};

export const PROFESSOR_MANAGEMENT_SORT_OPTIONS: Array<{
  value: ProfessorManagementSortKey;
  label: string;
}> = [
  { value: "latest", label: "导入时间" },
  { value: "updatedAtDesc", label: "更新时间" },
  { value: "nameAsc", label: "姓名" },
  { value: "universityAsc", label: "学校" },
];

const getSortDirection = (
  sortKey: ProfessorManagementSortKey,
  direction: ProfessorManagementSortDirection | undefined,
) => direction ?? DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS[sortKey];

const directionMultiplier = (direction: ProfessorManagementSortDirection) =>
  direction === "asc" ? 1 : -1;

const toTime = (value: string | null | undefined): number => {
  if (!value) {
    return 0;
  }

  const time = Date.parse(value);
  return Number.isFinite(time) ? time : 0;
};

const compareNullableStrings = (
  left: string | null | undefined,
  right: string | null | undefined,
  direction: ProfessorManagementSortDirection,
): number => {
  const normalizedLeft = left?.trim() ?? "";
  const normalizedRight = right?.trim() ?? "";

  if (!normalizedLeft && !normalizedRight) {
    return 0;
  }
  if (!normalizedLeft) {
    return 1;
  }
  if (!normalizedRight) {
    return -1;
  }

  return (
    normalizedLeft.localeCompare(normalizedRight, "zh-CN") *
    directionMultiplier(direction)
  );
};

export const sortManagementProfessors = (
  professors: ProfessorManagementItemDTO[],
  sortKey: ProfessorManagementSortKey,
  direction?: ProfessorManagementSortDirection,
): ProfessorManagementItemDTO[] => {
  const sorted = [...professors];
  const resolvedDirection = getSortDirection(sortKey, direction);

  if (sortKey === "updatedAtDesc") {
    return sorted.sort((left, right) => {
      return (
        (toTime(left.updated_at) - toTime(right.updated_at)) *
        directionMultiplier(resolvedDirection)
      );
    });
  }

  if (sortKey === "nameAsc") {
    return sorted.sort(
      (left, right) =>
        left.name.localeCompare(right.name, "zh-CN") *
        directionMultiplier(resolvedDirection),
    );
  }

  if (sortKey === "universityAsc") {
    return sorted.sort((left, right) => {
      const universityDiff = compareNullableStrings(
        left.university,
        right.university,
        resolvedDirection,
      );
      if (universityDiff !== 0) {
        return universityDiff;
      }
      return left.name.localeCompare(right.name, "zh-CN");
    });
  }

  return sorted.sort((left, right) => {
    return (
      (toTime(left.created_at) - toTime(right.created_at)) *
      directionMultiplier(resolvedDirection)
    );
  });
};
