import type { ProfessorManagementItemDTO } from "@/types";
import { extractProfessorTitleTags } from "@/lib/professorTitle";

export const NO_TAG_FILTER_VALUE = "__no_tag__";

export type ProfessorManagementFilterState = {
  keyword: string;
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

const normalize = (value: string | null | undefined): string =>
  value?.trim().toLowerCase() ?? "";

const sortByChinese = (values: Iterable<string>): string[] =>
  Array.from(values).sort((left, right) => left.localeCompare(right, "zh-CN"));

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

const filterTagMatches = (
  professor: ProfessorManagementItemDTO,
  selectedValues: string[],
): boolean => {
  if (selectedValues.length === 0) {
    return true;
  }
  const selectedSet = new Set(selectedValues);
  if (professor.tags.length === 0) {
    return selectedSet.has(NO_TAG_FILTER_VALUE);
  }
  return professor.tags.some((tag) => selectedSet.has(String(tag.id)));
};

export const createDefaultManagementFilters = (): ProfessorManagementFilterState => ({
  keyword: "",
  universities: [],
  schools: [],
  departments: [],
  titles: [],
  tagIds: [],
});

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
    professor.tags.forEach((tag) => {
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
  filters.universities.length +
  filters.schools.length +
  filters.departments.length +
  filters.titles.length +
  filters.tagIds.length;

export const filterManagementProfessors = (
  professors: ProfessorManagementItemDTO[],
  filters: ProfessorManagementFilterState,
): ProfessorManagementItemDTO[] => {
  const keyword = normalize(filters.keyword);

  return professors.filter((professor) => {
    const keywordMatched =
      !keyword ||
      [
        professor.name,
        professor.email,
        professor.university,
        professor.school,
        professor.department,
        professor.title,
        professor.research_direction,
        ...professor.tags.map((tag) => tag.name),
      ]
        .filter(Boolean)
        .some((value) => normalize(value).includes(keyword));

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
  const validTagIds = new Set([
    ...allOptions.tags.map((tag) => String(tag.id)),
    NO_TAG_FILTER_VALUE,
  ]);

  return {
    ...filters,
    universities,
    schools,
    departments: filters.departments.filter((value) =>
      departmentOptions.includes(value),
    ),
    titles: filters.titles.filter((value) => allOptions.titles.includes(value)),
    tagIds: (filters.tagIds ?? []).filter((value) => validTagIds.has(value)),
  };
};
