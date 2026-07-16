export const EMPTY_PROFESSOR_FIELD_VALUE = "无";

export const normalizeProfessorSearchText = (
  value: string | null | undefined,
): string => value?.trim().toLowerCase() ?? "";

export const isProfessorSearchFieldEmpty = (
  value: string | null | undefined,
): boolean => normalizeProfessorSearchText(value) === "";

export const matchesProfessorSearchField = (
  value: string | null | undefined,
  normalizedKeyword: string,
): boolean =>
  normalizedKeyword === EMPTY_PROFESSOR_FIELD_VALUE
    ? isProfessorSearchFieldEmpty(value)
    : normalizeProfessorSearchText(value).includes(normalizedKeyword);

export const formatProfessorSearchField = (
  value: string | null | undefined,
): string => value?.trim() || EMPTY_PROFESSOR_FIELD_VALUE;
