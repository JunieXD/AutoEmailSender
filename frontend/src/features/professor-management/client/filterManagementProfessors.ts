import {
  matchesProfessorSearchField,
  normalizeProfessorSearchText,
} from "@/lib/professorSearchField";
import { extractProfessorTitleTags } from "@/lib/professorTitle";
import type { ProfessorManagementItemDTO } from "@/types";

export const MANAGEMENT_KEYWORD_SEARCH_SCOPE_OPTIONS = [
  { value: "name", label: "姓名" },
  { value: "email", label: "邮箱" },
  { value: "university", label: "学校" },
  { value: "school", label: "学院" },
  { value: "department", label: "系所" },
  { value: "title", label: "职称" },
  { value: "researchDirection", label: "研究方向" },
  { value: "personalNote", label: "备注" },
  { value: "tag", label: "标签" },
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
  | keyof Pick<
      ProfessorManagementItemDTO,
      | "name"
      | "email"
      | "university"
      | "school"
      | "department"
      | "title"
      | "research_direction"
      | "personal_note"
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
  personalNote: "personal_note",
  tag: "tag",
};

export const NO_TAG_FILTER_VALUE = "__no_tag__";
export const NO_FIELD_FILTER_VALUE = "__no_field__";

export type ProfessorManagementFilterState = {
  keyword: string;
  keywordSearchScopes: ProfessorManagementKeywordSearchScope[];
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
  tagIds: string[];
};

export type ProfessorManagementFilterOptions = {
  universities: string[];
  schools: string[];
  departments: string[];
  titles: string[];
  tags: { id: number; name: string }[];
};

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

  if (nextValues.length === 0) {
    return [...DEFAULT_MANAGEMENT_KEYWORD_SEARCH_SCOPES];
  }

  return nextValues;
};

export const getManagementKeywordSearchPlaceholder = (values: unknown): string => {
  const selectedScopes = normalizeManagementKeywordSearchScopes(values);
  const selectedSet = new Set(selectedScopes);
  return MANAGEMENT_KEYWORD_SEARCH_SCOPE_OPTIONS.filter((option) =>
    selectedSet.has(option.value),
  )
    .map((option) => option.label)
    .join("、");
};

const addNonEmpty = (set: Set<string>, value: string | null | undefined) => {
  const trimmed = value?.trim();
  if (trimmed) {
    set.add(trimmed);
  }
};

const getProfessorTags = (professor: ProfessorManagementItemDTO) =>
  professor.tags ?? [];

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

const filterTitleMatches = (
  title: string | null | undefined,
  selectedValues: string[],
): boolean => {
  if (selectedValues.length === 0) {
    return true;
  }

  const tags = extractProfessorTitleTags(title);
  return (
    (!title?.trim() && selectedValues.includes(NO_FIELD_FILTER_VALUE)) ||
    selectedValues.some((value) => tags.includes(value))
  );
};

const filterTagMatches = (
  professor: ProfessorManagementItemDTO,
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

export const createDefaultManagementFilters = (): ProfessorManagementFilterState => ({
  keyword: "",
  keywordSearchScopes: [...DEFAULT_MANAGEMENT_KEYWORD_SEARCH_SCOPES],
  universities: [],
  schools: [],
  departments: [],
  titles: [],
  tagIds: [],
});

const getManagementKeywordValue = (
  professor: ProfessorManagementItemDTO,
  scope: ProfessorManagementKeywordSearchScope,
): string | null | undefined => {
  const field = managementKeywordFieldByScope[scope];
  if (field === "tag") {
    return getProfessorTags(professor)
      .map((tag) => tag.name)
      .join(" ");
  }
  return professor[field];
};

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
    extractProfessorTitleTags(professor.title).forEach((title) => {
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

export const getActiveManagementAdvancedFilterCount = (
  filters: ProfessorManagementFilterState,
): number =>
  Number(filters.universities.length > 0) +
  Number(filters.schools.length > 0) +
  Number(filters.departments.length > 0) +
  Number(filters.titles.length > 0) +
  Number(filters.tagIds.length > 0);

export const filterManagementProfessors = (
  professors: ProfessorManagementItemDTO[],
  filters: ProfessorManagementFilterState,
): ProfessorManagementItemDTO[] => {
  const keyword = normalizeProfessorSearchText(filters.keyword);
  const keywordSearchScopes = normalizeManagementKeywordSearchScopes(
    filters.keywordSearchScopes,
  );

  return professors.filter((professor) => {
    const keywordMatched =
      !keyword ||
      keywordSearchScopes.some((scope) =>
        matchesProfessorSearchField(
          getManagementKeywordValue(professor, scope),
          keyword,
        ),
      );

    return (
      keywordMatched &&
      matchesAny(professor.university, filters.universities) &&
      matchesAny(professor.school, filters.schools) &&
      matchesAny(professor.department, filters.departments) &&
      filterTitleMatches(professor.title, filters.titles) &&
      filterTagMatches(professor, filters.tagIds)
    );
  });
};
