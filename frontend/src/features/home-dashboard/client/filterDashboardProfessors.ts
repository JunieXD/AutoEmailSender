import type { ProfessorDashboardItemDTO, ProfessorDashboardStatus } from "@/types";

export type DashboardFilterState = {
  keyword: string;
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
  statuses: ProfessorDashboardStatus[];
  minMatchScore: string;
};

export type DashboardFilterOptions = {
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
};

export const createDefaultDashboardFilters = (): DashboardFilterState => ({
  keyword: "",
  universities: [],
  schools: [],
  departments: [],
  titles: [],
  statuses: [],
  minMatchScore: "",
});

const normalize = (value: string | null | undefined): string =>
  value?.trim().toLowerCase() ?? "";

const sortByChinese = (values: Iterable<string>): string[] =>
  Array.from(values).sort((left, right) => left.localeCompare(right, "zh-CN"));

const DASHBOARD_TITLE_SPLIT_PATTERN = /[、，,/／|｜；;]+/;

const addNonEmpty = (set: Set<string>, value: string | null | undefined) => {
  const trimmed = value?.trim();
  if (trimmed) {
    set.add(trimmed);
  }
};

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
  });

  return {
    universities: sortByChinese(universities),
    schools: sortByChinese(schools),
    departments: sortByChinese(departments),
    titles: sortByChinese(titles),
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

export const getActiveDashboardFilterCount = (
  filters: DashboardFilterState,
): number =>
  filters.universities.length +
  filters.schools.length +
  filters.departments.length +
  filters.titles.length +
  filters.statuses.length +
  (filters.minMatchScore.trim() ? 1 : 0);

export const filterDashboardProfessors = (
  professors: ProfessorDashboardItemDTO[],
  filters: DashboardFilterState,
): ProfessorDashboardItemDTO[] => {
  const keyword = normalize(filters.keyword);
  const minMatchScore = parseMinimumMatchScore(filters.minMatchScore);

  return professors.filter((professor) => {
    const keywordMatched =
      !keyword ||
      [
        professor.name,
        professor.university,
        professor.school,
        professor.department,
        professor.title,
        professor.research_direction,
      ].some((value) => normalize(value).includes(keyword));

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

  if (
    arraysEqual(universities, filters.universities) &&
    arraysEqual(schools, filters.schools) &&
    arraysEqual(departments, filters.departments) &&
    arraysEqual(titles, filters.titles)
  ) {
    return filters;
  }

  return {
    ...filters,
    universities,
    schools,
    departments,
    titles,
  };
};
