import {
  createDefaultManagementFilters,
  normalizeManagementKeywordSearchScopes,
  type ProfessorManagementFilterState,
} from "@/features/professor-management/client/filterManagementProfessors";
import {
  DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS,
  DEFAULT_PROFESSOR_MANAGEMENT_SORT_KEY,
  PROFESSOR_MANAGEMENT_SORT_OPTIONS,
  type ProfessorManagementSortDirection,
  type ProfessorManagementSortKey,
} from "@/features/professor-management/client/sortManagementProfessors";
import type {
  CrawlJobEntryTypeDTO,
  ProfessorManagementItemDTO,
  ProfessorUpsertPayloadDTO,
} from "@/types";

export type ArchiveFilter = "active" | "archived" | "all";
export type ProfessorFormState = {
  name: string;
  email: string;
  title: string;
  university: string;
  school: string;
  department: string;
  research_direction: string;
  recent_papers_text: string;
  personal_note: string;
  profile_url: string;
  source_url: string;
  tag_ids: number[];
};
export type CrawlerJobFormState = {
  university: string;
  school: string;
  start_urls: string[];
  entry_type: CrawlJobEntryTypeDTO;
};

const PROFESSORS_FILTERS_STORAGE_KEY = "professors_page_filters";
const professorManagementSortKeyValues = new Set<ProfessorManagementSortKey>(
  PROFESSOR_MANAGEMENT_SORT_OPTIONS.map((option) => option.value),
);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const readStringArray = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];

const isArchiveFilter = (value: unknown): value is ArchiveFilter =>
  value === "active" || value === "archived" || value === "all";

const isProfessorManagementSortKey = (
  value: unknown,
): value is ProfessorManagementSortKey =>
  typeof value === "string" &&
  professorManagementSortKeyValues.has(value as ProfessorManagementSortKey);

const isProfessorManagementSortDirection = (
  value: unknown,
): value is ProfessorManagementSortDirection =>
  value === "asc" || value === "desc";

const readStoredManagementSortDirections = (
  value: unknown,
): Record<ProfessorManagementSortKey, ProfessorManagementSortDirection> => {
  const defaults = { ...DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS };
  if (!isRecord(value)) {
    return defaults;
  }

  PROFESSOR_MANAGEMENT_SORT_OPTIONS.forEach((option) => {
    const direction = value[option.value];
    if (isProfessorManagementSortDirection(direction)) {
      defaults[option.value] = direction;
    }
  });
  return defaults;
};

export const getManagementSortOptionLabel = (
  sortKey: ProfessorManagementSortKey,
) =>
  PROFESSOR_MANAGEMENT_SORT_OPTIONS.find((option) => option.value === sortKey)
    ?.label ?? "";

export const getManagementSortDirectionSymbol = (
  direction: ProfessorManagementSortDirection,
) => (direction === "desc" ? "↓" : "↑");

export const getManagementSortTriggerLabel = (
  sortKey: ProfessorManagementSortKey,
  direction: ProfessorManagementSortDirection,
) =>
  `${getManagementSortOptionLabel(sortKey)} ${getManagementSortDirectionSymbol(
    direction,
  )}`;

export const readStoredProfessorManagementState = () => {
  const defaults = {
    archiveFilter: "active" as ArchiveFilter,
    filters: createDefaultManagementFilters(),
    advancedFiltersOpen: false,
    sortKey: DEFAULT_PROFESSOR_MANAGEMENT_SORT_KEY,
    sortDirections: { ...DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS },
    currentPage: 1,
  };

  if (typeof window === "undefined") {
    return defaults;
  }

  try {
    const rawValue = window.sessionStorage.getItem(
      PROFESSORS_FILTERS_STORAGE_KEY,
    );
    if (!rawValue) {
      return defaults;
    }

    const parsedValue = JSON.parse(rawValue);
    if (!isRecord(parsedValue)) {
      return defaults;
    }

    const filters = isRecord(parsedValue.filters)
      ? parsedValue.filters
      : null;

    const nextFilters = createDefaultManagementFilters();
    nextFilters.keyword =
      typeof filters?.keyword === "string" ? filters.keyword : "";
    nextFilters.keywordSearchScopes = normalizeManagementKeywordSearchScopes(
      filters?.keywordSearchScopes,
    );
    nextFilters.universities = readStringArray(filters?.universities);
    nextFilters.schools = readStringArray(filters?.schools);
    nextFilters.departments = readStringArray(filters?.departments);
    nextFilters.titles = readStringArray(filters?.titles);
    nextFilters.tagIds = readStringArray(filters?.tagIds);

    const nextSortKey = isProfessorManagementSortKey(parsedValue.sortKey)
      ? parsedValue.sortKey
      : defaults.sortKey;
    const nextSortDirections = readStoredManagementSortDirections(
      parsedValue.sortDirections,
    );
    if (
      isProfessorManagementSortDirection(parsedValue.sortDirection) &&
      !isRecord(parsedValue.sortDirections)
    ) {
      nextSortDirections[nextSortKey] = parsedValue.sortDirection;
    }

    return {
      archiveFilter: isArchiveFilter(parsedValue.archiveFilter)
        ? parsedValue.archiveFilter
        : defaults.archiveFilter,
      filters: nextFilters,
      advancedFiltersOpen:
        typeof parsedValue.advancedFiltersOpen === "boolean"
          ? parsedValue.advancedFiltersOpen
          : defaults.advancedFiltersOpen,
      sortKey: nextSortKey,
      sortDirections: nextSortDirections,
      currentPage:
        typeof parsedValue.currentPage === "number" &&
        Number.isFinite(parsedValue.currentPage) &&
        parsedValue.currentPage > 0
          ? Math.floor(parsedValue.currentPage)
          : defaults.currentPage,
    };
  } catch {
    return defaults;
  }
};

export const writeStoredProfessorManagementState = (
  state: {
    archiveFilter: ArchiveFilter;
    filters: ProfessorManagementFilterState;
    advancedFiltersOpen: boolean;
    sortKey: ProfessorManagementSortKey;
    sortDirections: Record<
      ProfessorManagementSortKey,
      ProfessorManagementSortDirection
    >;
    currentPage: number;
  },
) => {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.sessionStorage.setItem(
      PROFESSORS_FILTERS_STORAGE_KEY,
      JSON.stringify(state),
    );
  } catch {
    // Session persistence is an optimization; the page remains usable without it.
  }
};

export const emptyProfessorForm = (): ProfessorFormState => ({
  name: "",
  email: "",
  title: "",
  university: "",
  school: "",
  department: "",
  research_direction: "",
  recent_papers_text: "",
  personal_note: "",
  profile_url: "",
  source_url: "",
  tag_ids: [],
});

export const emptyCrawlerJobForm = (): CrawlerJobFormState => ({
  university: "",
  school: "",
  start_urls: [""],
  entry_type: "list",
});

export const normalizeCrawlerStartUrls = (urls: string[]) => {
  const seen = new Set<string>();
  return urls
    .map((url) => url.trim())
    .filter((url) => {
      if (!url || seen.has(url)) {
        return false;
      }
      seen.add(url);
      return true;
    });
};

export const buildCrawlerStartUrlsAfterMultilinePaste = (
  urls: string[],
  targetIndex: number,
  pastedText: string,
) => {
  if (!/[\r\n]/.test(pastedText)) {
    return null;
  }

  const pastedUrls = normalizeCrawlerStartUrls(
    pastedText.split(/\r\n|\r|\n/),
  );
  if (pastedUrls.length < 2) {
    return null;
  }

  const nextUrls = normalizeCrawlerStartUrls([
    ...urls.slice(0, targetIndex),
    ...pastedUrls,
    ...urls.slice(targetIndex + 1),
  ]);
  return nextUrls.length > 0 ? nextUrls : [""];
};

export const toProfessorForm = (
  professor: ProfessorManagementItemDTO,
): ProfessorFormState => ({
  name: professor.name,
  email: professor.email ?? "",
  title: professor.title ?? "",
  university: professor.university ?? "",
  school: professor.school ?? "",
  department: professor.department ?? "",
  research_direction: professor.research_direction ?? "",
  recent_papers_text: professor.recent_papers.join("\n"),
  personal_note: professor.personal_note ?? "",
  profile_url: professor.profile_url ?? "",
  source_url: professor.source_url ?? "",
  tag_ids: professor.tags.map((tag) => tag.id),
});

export const toProfessorPayload = (
  form: ProfessorFormState,
): ProfessorUpsertPayloadDTO => ({
  name: form.name.trim(),
  email: form.email.trim(),
  title: form.title.trim() || null,
  university: form.university.trim() || null,
  school: form.school.trim() || null,
  department: form.department.trim() || null,
  research_direction: form.research_direction.trim() || null,
  recent_papers: form.recent_papers_text
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean),
  personal_note: form.personal_note.trim() || null,
  profile_url: form.profile_url.trim() || null,
  source_url: form.source_url.trim() || null,
  tag_ids: form.tag_ids,
});
