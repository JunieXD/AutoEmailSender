import {
  matchesProfessorSearchField,
  normalizeProfessorSearchText,
} from "@/lib/professorSearchField";
import type {
  ProfessorDashboardFilterStatus,
  ProfessorDashboardItemDTO,
} from "@/types";

export const DASHBOARD_KEYWORD_SEARCH_SCOPE_OPTIONS = [
  { value: "name", label: "姓名" },
  { value: "email", label: "邮箱" },
  { value: "university", label: "学校" },
  { value: "school", label: "学院" },
  { value: "department", label: "系所" },
  { value: "title", label: "职称" },
  { value: "researchDirection", label: "研究方向" },
  { value: "tag", label: "标签" },
] as const;

export type DashboardKeywordSearchScope =
  (typeof DASHBOARD_KEYWORD_SEARCH_SCOPE_OPTIONS)[number]["value"];

export const DEFAULT_DASHBOARD_KEYWORD_SEARCH_SCOPES =
  DASHBOARD_KEYWORD_SEARCH_SCOPE_OPTIONS.map((option) => option.value);

const dashboardKeywordSearchScopeSet = new Set<string>(
  DEFAULT_DASHBOARD_KEYWORD_SEARCH_SCOPES,
);

const dashboardKeywordFieldByScope: Record<
  DashboardKeywordSearchScope,
  | keyof Pick<
      ProfessorDashboardItemDTO,
      | "name"
      | "email"
      | "university"
      | "school"
      | "department"
      | "title"
      | "research_direction"
    >
  | "tag"
> = {
  name: "name",
  email: "email",
  university: "university",
  school: "school",
  department: "department",
  title: "title",
  researchDirection: "research_direction",
  tag: "tag",
};

export const NO_TAG_FILTER_VALUE = "__no_tag__";
export const NO_FIELD_FILTER_VALUE = "__no_field__";
export const NO_MATCH_SCORE_FILTER_VALUE = "__no_match_score__";

export type DashboardFilterState = {
  keyword: string;
  keywordSearchScopes: DashboardKeywordSearchScope[];
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
  statuses: ProfessorDashboardFilterStatus[];
  tagIds: string[];
  minMatchScore: string;
  maxMatchScore: string;
};

export type DashboardFilterOptions = {
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
  tags: { id: number; name: string }[];
};

export const createDefaultDashboardFilters = (): DashboardFilterState => ({
  keyword: "",
  keywordSearchScopes: [...DEFAULT_DASHBOARD_KEYWORD_SEARCH_SCOPES],
  universities: [],
  schools: [],
  departments: [],
  titles: [],
  statuses: [],
  tagIds: [],
  minMatchScore: "",
  maxMatchScore: "",
});

const sortByChinese = (values: Iterable<string>): string[] =>
  Array.from(values).sort((left, right) => left.localeCompare(right, "zh-CN"));

export const normalizeDashboardKeywordSearchScopes = (
  values: unknown,
): DashboardKeywordSearchScope[] => {
  if (!Array.isArray(values)) {
    return [...DEFAULT_DASHBOARD_KEYWORD_SEARCH_SCOPES];
  }

  const nextValues = values.filter(
    (value): value is DashboardKeywordSearchScope =>
      typeof value === "string" && dashboardKeywordSearchScopeSet.has(value),
  );

  if (nextValues.length === 0) {
    return [...DEFAULT_DASHBOARD_KEYWORD_SEARCH_SCOPES];
  }

  return nextValues;
};

export const getDashboardKeywordSearchPlaceholder = (values: unknown): string => {
  const selectedScopes = normalizeDashboardKeywordSearchScopes(values);
  const selectedSet = new Set(selectedScopes);
  return DASHBOARD_KEYWORD_SEARCH_SCOPE_OPTIONS.filter((option) =>
    selectedSet.has(option.value),
  )
    .map((option) => option.label)
    .join("、");
};

const DASHBOARD_TITLE_SPLIT_PATTERN = /[、，,/／|｜；;]+/;

const addNonEmpty = (set: Set<string>, value: string | null | undefined) => {
  const trimmed = value?.trim();
  if (trimmed) {
    set.add(trimmed);
  }
};

const getProfessorTags = (professor: ProfessorDashboardItemDTO) =>
  professor.tags ?? [];

const extractDashboardTitleTags = (title: string | null | undefined): string[] => {
  if (!title?.trim()) {
    return [];
  }

  const seen = new Set<string>();
  return title
    .split(DASHBOARD_TITLE_SPLIT_PATTERN)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item) => {
      if (seen.has(item)) {
        return false;
      }
      seen.add(item);
      return true;
    });
};

export const buildDashboardFilterOptions = (
  professors: ProfessorDashboardItemDTO[],
  filters: Pick<DashboardFilterState, "universities"> &
    Partial<Pick<DashboardFilterState, "schools">> = {
    universities: [],
    schools: [],
  },
): DashboardFilterOptions => {
  const universities = new Set<string>();
  const schools = new Set<string>();
  const departments = new Set<string>();
  const titles = new Set<string>();
  const tags = new Map<number, string>();
  const selectedUniversities = filters.universities;
  const selectedSchools = filters.schools ?? [];

  professors.forEach((professor) => {
    addNonEmpty(universities, professor.university);
    if (matchesAny(professor.university, selectedUniversities)) {
      addNonEmpty(schools, professor.school);
    }
    if (
      matchesAny(professor.university, selectedUniversities) &&
      matchesAny(professor.school, selectedSchools)
    ) {
      addNonEmpty(departments, professor.department);
    }
    extractDashboardTitleTags(professor.title).forEach((title) => {
      addNonEmpty(titles, title);
    });
    getProfessorTags(professor).forEach((tag) => {
      tags.set(tag.id, tag.name);
    });
  });

  return {
    universities: sortByChinese(universities),
    schools: sortByChinese(schools),
    departments: sortByChinese(departments),
    titles: sortByChinese(titles),
    tags: Array.from(tags, ([id, name]) => ({ id, name })).sort((left, right) =>
      left.name.localeCompare(right.name, "zh-CN"),
    ),
  };
};

function matchesAny(
  value: string | null | undefined,
  selectedValues: string[],
): boolean {
  if (selectedValues.length === 0) {
    return true;
  }

  const normalizedValue = value?.trim() ?? "";
  return (
    selectedValues.includes(normalizedValue) ||
    (!normalizedValue && selectedValues.includes(NO_FIELD_FILTER_VALUE))
  );
}

const matchesAnyTitle = (
  title: string | null | undefined,
  selectedValues: string[],
): boolean => {
  if (selectedValues.length === 0) {
    return true;
  }
  const tags = extractDashboardTitleTags(title);
  return (
    (!title?.trim() && selectedValues.includes(NO_FIELD_FILTER_VALUE)) ||
    selectedValues.some((value) => tags.includes(value))
  );
};

const matchesAnyStatus = (
  professor: ProfessorDashboardItemDTO,
  selectedValues: ProfessorDashboardFilterStatus[],
): boolean =>
  selectedValues.length === 0 ||
  selectedValues.includes(professor.status) ||
  (Boolean(professor.has_active_schedule) && selectedValues.includes("scheduled"));

const matchesAnyTag = (
  professor: ProfessorDashboardItemDTO,
  selectedValues: string[],
): boolean => {
  if (selectedValues.length === 0) {
    return true;
  }
  const selectedSet = new Set(selectedValues);
  const tags = getProfessorTags(professor);
  if (tags.length === 0) {
    return selectedSet.has(NO_TAG_FILTER_VALUE);
  }
  return tags.some((tag) => selectedSet.has(String(tag.id)));
};

const arraysEqual = (left: string[], right: string[]): boolean =>
  left.length === right.length && left.every((value, index) => value === right[index]);

const parseMatchScoreBoundary = (value: string): number | null => {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }

  const score = Number(trimmed);
  if (!Number.isFinite(score)) {
    return null;
  }

  return Math.min(100, Math.max(0, score));
};

const getDashboardKeywordValue = (
  professor: ProfessorDashboardItemDTO,
  scope: DashboardKeywordSearchScope,
): string | null | undefined => {
  const field = dashboardKeywordFieldByScope[scope];
  if (field === "tag") {
    return getProfessorTags(professor)
      .map((tag) => tag.name)
      .join(" ");
  }
  return professor[field];
};

export const getActiveDashboardFilterCount = (
  filters: DashboardFilterState,
): number =>
  Number(filters.universities.length > 0) +
  Number(filters.schools.length > 0) +
  Number(filters.departments.length > 0) +
  Number(filters.titles.length > 0) +
  Number(filters.statuses.length > 0) +
  Number(filters.tagIds.length > 0) +
  Number(Boolean(filters.minMatchScore.trim() || filters.maxMatchScore.trim()));

export const filterDashboardProfessors = (
  professors: ProfessorDashboardItemDTO[],
  filters: DashboardFilterState,
): ProfessorDashboardItemDTO[] => {
  const keyword = normalizeProfessorSearchText(filters.keyword);
  const keywordSearchScopes = normalizeDashboardKeywordSearchScopes(
    filters.keywordSearchScopes,
  );
  const minMatchScore = parseMatchScoreBoundary(filters.minMatchScore);
  const maxMatchScore = parseMatchScoreBoundary(filters.maxMatchScore);
  const hasMatchScoreRange = minMatchScore !== null || maxMatchScore !== null;
  const hasValidMatchScoreRange =
    minMatchScore === null || maxMatchScore === null || minMatchScore <= maxMatchScore;

  return professors.filter((professor) => {
    const keywordMatched =
      !keyword ||
      keywordSearchScopes.some((scope) =>
        matchesProfessorSearchField(
          getDashboardKeywordValue(professor, scope),
          keyword,
        ),
      );

    const matchScoreMatched =
      filters.minMatchScore === NO_MATCH_SCORE_FILTER_VALUE
        ? professor.match_score === null
        : !hasMatchScoreRange ||
          (hasValidMatchScoreRange &&
            professor.match_score !== null &&
            (minMatchScore === null || professor.match_score >= minMatchScore) &&
            (maxMatchScore === null || professor.match_score <= maxMatchScore));

    return (
      keywordMatched &&
      matchesAny(professor.university, filters.universities) &&
      matchesAny(professor.school, filters.schools) &&
      matchesAny(professor.department, filters.departments) &&
      matchesAnyTitle(professor.title, filters.titles) &&
      matchesAnyStatus(professor, filters.statuses) &&
      matchesAnyTag(professor, filters.tagIds) &&
      matchScoreMatched
    );
  });
};

export const pruneDashboardFilters = (
  professors: ProfessorDashboardItemDTO[],
  filters: DashboardFilterState,
): DashboardFilterState => {
  const allOptions = buildDashboardFilterOptions(professors);
  const universities = filters.universities.filter(
    (value) =>
      value === NO_FIELD_FILTER_VALUE || allOptions.universities.includes(value),
  );
  const schoolOptions = buildDashboardFilterOptions(professors, {
    universities,
    schools: [],
  }).schools;
  const schools = filters.schools.filter(
    (value) => value === NO_FIELD_FILTER_VALUE || schoolOptions.includes(value),
  );
  const departmentOptions = buildDashboardFilterOptions(professors, {
    universities,
    schools,
  }).departments;
  const departments = filters.departments.filter(
    (value) =>
      value === NO_FIELD_FILTER_VALUE || departmentOptions.includes(value),
  );
  const titles = filters.titles.filter(
    (value) => value === NO_FIELD_FILTER_VALUE || allOptions.titles.includes(value),
  );
  const validTagIds = new Set([
    ...allOptions.tags.map((tag) => String(tag.id)),
    NO_TAG_FILTER_VALUE,
  ]);
  const tagIds = (filters.tagIds ?? []).filter((value) => validTagIds.has(value));

  if (
    arraysEqual(universities, filters.universities) &&
    arraysEqual(schools, filters.schools) &&
    arraysEqual(departments, filters.departments) &&
    arraysEqual(titles, filters.titles) &&
    arraysEqual(tagIds, filters.tagIds)
  ) {
    return filters;
  }

  return {
    ...filters,
    universities,
    schools,
    departments,
    titles,
    tagIds,
  };
};
