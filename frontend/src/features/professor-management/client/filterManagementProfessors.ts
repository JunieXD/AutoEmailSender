import type { ProfessorManagementItemDTO } from "@/types";
import { extractProfessorTitleTags } from "@/lib/professorTitle";

export type ManagementKeywordField =
  | "name"
  | "email"
  | "university"
  | "school"
  | "department"
  | "title"
  | "research_direction";

export const DEFAULT_MANAGEMENT_KEYWORD_FIELDS: ManagementKeywordField[] = [
  "name",
  "email",
  "university",
  "school",
  "department",
  "title",
  "research_direction",
];

const managementKeywordFieldSet = new Set<string>(
  DEFAULT_MANAGEMENT_KEYWORD_FIELDS,
);

export type ProfessorManagementFilterState = {
  keyword: string;
  keywordFields: ManagementKeywordField[];
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

export const normalizeManagementKeywordFields = (
  values: unknown,
): ManagementKeywordField[] => {
  if (!Array.isArray(values)) {
    return [...DEFAULT_MANAGEMENT_KEYWORD_FIELDS];
  }

  const nextValues = values.filter(
    (value): value is ManagementKeywordField =>
      typeof value === "string" && managementKeywordFieldSet.has(value),
  );

  if (nextValues.length === 0 || nextValues.length !== values.length) {
    return [...DEFAULT_MANAGEMENT_KEYWORD_FIELDS];
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
  keywordFields: [...DEFAULT_MANAGEMENT_KEYWORD_FIELDS],
  universities: [],
  schools: [],
  departments: [],
  titles: [],
});

const getManagementKeywordValue = (
  professor: ProfessorManagementItemDTO,
  field: ManagementKeywordField,
): string | null | undefined => professor[field];

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
  const keywordFields = normalizeManagementKeywordFields(filters.keywordFields);

  return professors.filter((professor) => {
    const keywordMatched =
      !keyword ||
      keywordFields.some((field) =>
        normalize(getManagementKeywordValue(professor, field)).includes(keyword),
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
