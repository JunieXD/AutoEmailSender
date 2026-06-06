import type { ProfessorManagementItemDTO } from "@/types";
import { extractProfessorTitleTags } from "@/lib/professorTitle";

export const MANAGEMENT_KEYWORD_SEARCH_SCOPE_OPTIONS = [
  { value: "name", label: "姓名" },
  { value: "email", label: "邮箱" },
  { value: "university", label: "学校" },
  { value: "school", label: "学院" },
  { value: "department", label: "系所" },
  { value: "title", label: "职称" },
  { value: "researchDirection", label: "研究方向" },
] as const;

export type ProfessorManagementKeywordSearchScope =
  (typeof MANAGEMENT_KEYWORD_SEARCH_SCOPE_OPTIONS)[number]["value"];

export const DEFAULT_MANAGEMENT_KEYWORD_SEARCH_SCOPES =
  MANAGEMENT_KEYWORD_SEARCH_SCOPE_OPTIONS.map((option) => option.value);

const managementKeywordSearchScopeSet = new Set<string>(
  DEFAULT_MANAGEMENT_KEYWORD_SEARCH_SCOPES,
);

const managementKeywordFieldByScope: Record<
  ProfessorManagementKeywordSearchScope,
  keyof Pick<
    ProfessorManagementItemDTO,
    | "name"
    | "email"
    | "university"
    | "school"
    | "department"
    | "title"
    | "research_direction"
  >
> = {
  name: "name",
  email: "email",
  university: "university",
  school: "school",
  department: "department",
  title: "title",
  researchDirection: "research_direction",
};

export type ProfessorManagementFilterState = {
  keyword: string;
  keywordSearchScopes: ProfessorManagementKeywordSearchScope[];
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
};

export type ProfessorManagementFilterOptions = {
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
};

const normalize = (value: string | null | undefined): string =>
  value?.trim().toLowerCase() ?? "";

const sortByChinese = (values: Iterable<string>): string[] =>
  Array.from(values).sort((left, right) => left.localeCompare(right, "zh-CN"));

export const normalizeManagementKeywordSearchScopes = (
  values: unknown,
): ProfessorManagementKeywordSearchScope[] => {
  if (!Array.isArray(values)) {
    return [...DEFAULT_MANAGEMENT_KEYWORD_SEARCH_SCOPES];
  }

  const nextValues = values.filter(
    (value): value is ProfessorManagementKeywordSearchScope =>
      typeof value === "string" && managementKeywordSearchScopeSet.has(value),
  );

  if (nextValues.length === 0 || nextValues.length !== values.length) {
    return [...DEFAULT_MANAGEMENT_KEYWORD_SEARCH_SCOPES];
  }

  return nextValues;
};

const addNonEmpty = (set: Set<string>, value: string | null | undefined) => {
  const trimmed = value?.trim();
  if (trimmed) {
    set.add(trimmed);
  }
};

const matchesAny = (
  value: string | null | undefined,
  selectedValues: string[],
): boolean =>
  selectedValues.length === 0 || selectedValues.includes(value?.trim() ?? "");

const filterTitleMatches = (
  title: string | null | undefined,
  selectedValues: string[],
): boolean => {
  if (selectedValues.length === 0) {
    return true;
  }

  const tags = extractProfessorTitleTags(title);
  return selectedValues.some((value) => tags.includes(value));
};

export const createDefaultManagementFilters = (): ProfessorManagementFilterState => ({
  keyword: "",
  keywordSearchScopes: [...DEFAULT_MANAGEMENT_KEYWORD_SEARCH_SCOPES],
  universities: [],
  schools: [],
  departments: [],
  titles: [],
});

const getManagementKeywordValue = (
  professor: ProfessorManagementItemDTO,
  scope: ProfessorManagementKeywordSearchScope,
): string | null | undefined => professor[managementKeywordFieldByScope[scope]];

export const buildManagementFilterOptions = (
  professors: ProfessorManagementItemDTO[],
  filters: Pick<ProfessorManagementFilterState, "universities"> &
    Partial<Pick<ProfessorManagementFilterState, "schools">> = {
    universities: [],
    schools: [],
  },
): ProfessorManagementFilterOptions => {
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
    extractProfessorTitleTags(professor.title).forEach((title) => {
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

export const getActiveManagementAdvancedFilterCount = (
  filters: ProfessorManagementFilterState,
): number =>
  filters.universities.length +
  filters.schools.length +
  filters.departments.length +
  filters.titles.length;

export const filterManagementProfessors = (
  professors: ProfessorManagementItemDTO[],
  filters: ProfessorManagementFilterState,
): ProfessorManagementItemDTO[] => {
  const keyword = normalize(filters.keyword);
  const keywordSearchScopes = normalizeManagementKeywordSearchScopes(
    filters.keywordSearchScopes,
  );

  return professors.filter((professor) => {
    const keywordMatched =
      !keyword ||
      keywordSearchScopes.some((scope) =>
        normalize(getManagementKeywordValue(professor, scope)).includes(keyword),
      );

    return (
      keywordMatched &&
      matchesAny(professor.university, filters.universities) &&
      matchesAny(professor.school, filters.schools) &&
      matchesAny(professor.department, filters.departments) &&
      filterTitleMatches(professor.title, filters.titles)
    );
  });
};

export const pruneManagementFilters = (
  professors: ProfessorManagementItemDTO[],
  filters: ProfessorManagementFilterState,
): ProfessorManagementFilterState => {
  const allOptions = buildManagementFilterOptions(professors);
  const universities = filters.universities.filter((value) =>
    allOptions.universities.includes(value),
  );
  const schoolOptions = buildManagementFilterOptions(professors, {
    universities,
    schools: [],
  }).schools;
  const schools = filters.schools.filter((value) => schoolOptions.includes(value));
  const departmentOptions = buildManagementFilterOptions(professors, {
    universities,
    schools,
  }).departments;

  return {
    ...filters,
    universities,
    schools,
    departments: filters.departments.filter((value) =>
      departmentOptions.includes(value),
    ),
    titles: filters.titles.filter((value) => allOptions.titles.includes(value)),
  };
};
