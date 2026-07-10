import type { ProfessorDashboardItemDTO, ProfessorDashboardStatus } from "@/types";

export const DASHBOARD_KEYWORD_SEARCH_SCOPE_OPTIONS = [
  { value: "name", label: "姓名" },
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
      | "university"
      | "school"
      | "department"
      | "title"
      | "research_direction"
    >
  | "tag"
> = {
  name: "name",
  university: "university",
  school: "school",
  department: "department",
  title: "title",
  researchDirection: "research_direction",
  tag: "tag",
};

export const NO_TAG_FILTER_VALUE = "__no_tag__";

export type DashboardFilterState = {
  keyword: string;
  keywordSearchScopes: DashboardKeywordSearchScope[];
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
  statuses: ProfessorDashboardStatus[];
  tagIds: string[];
  minMatchScore: string;
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
});

const normalize = (value: string | null | undefined): string =>
  value?.trim().toLowerCase() ?? "";

const EMPTY_FIELD_SEARCH_KEYWORD = "无";

const matchesKeywordValue = (
  value: string | null | undefined,
  keyword: string,
): boolean => {
  const normalizedValue = normalize(value);
  return keyword === EMPTY_FIELD_SEARCH_KEYWORD
    ? normalizedValue === ""
    : normalizedValue.includes(keyword);
};

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
    if (
      selectedUniversities.length === 0 ||
      selectedUniversities.includes(professor.university?.trim() ?? "")
    ) {
      addNonEmpty(schools, professor.school);
    }
    if (
      (selectedUniversities.length === 0 ||
        selectedUniversities.includes(professor.university?.trim() ?? "")) &&
      (selectedSchools.length === 0 ||
        selectedSchools.includes(professor.school?.trim() ?? ""))
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

const matchesAny = (
  value: string | null | undefined,
  selectedValues: string[],
): boolean =>
  selectedValues.length === 0 || selectedValues.includes(value?.trim() ?? "");

const matchesAnyTitle = (
  title: string | null | undefined,
  selectedValues: string[],
): boolean => {
  if (selectedValues.length === 0) {
    return true;
  }
  const tags = extractDashboardTitleTags(title);
  return selectedValues.some((value) => tags.includes(value));
};

const matchesAnyStatus = (
  value: ProfessorDashboardStatus,
  selectedValues: ProfessorDashboardStatus[],
): boolean => selectedValues.length === 0 || selectedValues.includes(value);

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

const parseMinimumMatchScore = (value: string): number | null => {
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
  filters.universities.length +
  filters.schools.length +
  filters.departments.length +
  filters.titles.length +
  filters.statuses.length +
  filters.tagIds.length +
  (filters.minMatchScore.trim() ? 1 : 0);

export const filterDashboardProfessors = (
  professors: ProfessorDashboardItemDTO[],
  filters: DashboardFilterState,
): ProfessorDashboardItemDTO[] => {
  const keyword = normalize(filters.keyword);
  const keywordSearchScopes = normalizeDashboardKeywordSearchScopes(
    filters.keywordSearchScopes,
  );
  const minMatchScore = parseMinimumMatchScore(filters.minMatchScore);

  return professors.filter((professor) => {
    const keywordMatched =
      !keyword ||
      keywordSearchScopes.some((scope) =>
        matchesKeywordValue(getDashboardKeywordValue(professor, scope), keyword),
      );

    const matchScoreMatched =
      minMatchScore === null ||
      (professor.match_score !== null && professor.match_score >= minMatchScore);

    return (
      keywordMatched &&
      matchesAny(professor.university, filters.universities) &&
      matchesAny(professor.school, filters.schools) &&
      matchesAny(professor.department, filters.departments) &&
      matchesAnyTitle(professor.title, filters.titles) &&
      matchesAnyStatus(professor.status, filters.statuses) &&
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
  const universities = filters.universities.filter((value) =>
    allOptions.universities.includes(value),
  );
  const schoolOptions = buildDashboardFilterOptions(professors, {
    universities,
    schools: [],
  }).schools;
  const schools = filters.schools.filter((value) => schoolOptions.includes(value));
  const departmentOptions = buildDashboardFilterOptions(professors, {
    universities,
    schools,
  }).departments;
  const departments = filters.departments.filter((value) =>
    departmentOptions.includes(value),
  );
  const titles = filters.titles.filter((value) => allOptions.titles.includes(value));
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
