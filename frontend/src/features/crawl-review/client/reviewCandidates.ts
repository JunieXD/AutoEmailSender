import type {
  CrawlCandidateDTO,
  CrawlCandidateReviewStatusDTO,
} from '@/types';

export type CrawlCandidateSearchScope =
  | 'name'
  | 'email'
  | 'organization'
  | 'title'
  | 'research_direction'
  | 'recent_papers';

export type CrawlCandidateInformationField =
  | 'email'
  | 'title'
  | 'department'
  | 'profile_url'
  | 'research_direction'
  | 'recent_papers';

export type CrawlCandidateInformationCondition = 'present' | 'missing';
export type CrawlCandidateInformationMatchMode = 'all' | 'any';

export type CrawlCandidateReviewStatusFilter =
  | 'all'
  | CrawlCandidateReviewStatusDTO;

export type CrawlCandidateFilters = {
  keyword: string;
  searchScopes: CrawlCandidateSearchScope[];
  informationConditions: Partial<
    Record<CrawlCandidateInformationField, CrawlCandidateInformationCondition>
  >;
  informationMatchMode: CrawlCandidateInformationMatchMode;
  reviewStatus: CrawlCandidateReviewStatusFilter;
};

export const DEFAULT_CRAWL_CANDIDATE_SEARCH_SCOPES: CrawlCandidateSearchScope[] = [
  'name',
  'email',
  'organization',
  'title',
  'research_direction',
  'recent_papers',
];

export const DEFAULT_CRAWL_CANDIDATE_FILTERS: CrawlCandidateFilters = {
  keyword: '',
  searchScopes: [...DEFAULT_CRAWL_CANDIDATE_SEARCH_SCOPES],
  informationConditions: {},
  informationMatchMode: 'all',
  reviewStatus: 'all',
};

const isBlank = (value: string | null | undefined) => !value?.trim();

const normalizeSearchText = (value: string) =>
  value.normalize('NFKC').toLocaleLowerCase();

const hasCandidateInformation = (
  candidate: CrawlCandidateDTO,
  field: CrawlCandidateInformationField,
) => {
  switch (field) {
    case 'email':
      return !isBlank(candidate.email);
    case 'title':
      return !isBlank(candidate.title);
    case 'department':
      return !isBlank(candidate.department);
    case 'profile_url':
      return !isBlank(candidate.profile_url);
    case 'research_direction':
      return !isBlank(candidate.research_direction);
    case 'recent_papers':
      return candidate.recent_papers.some((paper) => !isBlank(paper));
  }
};

const getInformationConditionEntries = (
  conditions: CrawlCandidateFilters['informationConditions'],
) =>
  Object.entries(conditions) as Array<
    [CrawlCandidateInformationField, CrawlCandidateInformationCondition]
  >;

const matchesInformationConditions = (
  candidate: CrawlCandidateDTO,
  filters: CrawlCandidateFilters,
) => {
  const conditionEntries = getInformationConditionEntries(
    filters.informationConditions,
  );
  if (conditionEntries.length === 0) {
    return true;
  }

  const matchesCondition = ([field, condition]: (typeof conditionEntries)[number]) =>
    hasCandidateInformation(candidate, field) === (condition === 'present');

  return filters.informationMatchMode === 'any'
    ? conditionEntries.some(matchesCondition)
    : conditionEntries.every(matchesCondition);
};

const getCandidateSearchText = (
  candidate: CrawlCandidateDTO,
  scope: CrawlCandidateSearchScope,
) => {
  switch (scope) {
    case 'name':
      return candidate.name;
    case 'email':
      return candidate.email ?? '';
    case 'organization':
      return [
        candidate.university,
        candidate.school,
        candidate.department,
      ]
        .filter(Boolean)
        .join(' ');
    case 'title':
      return candidate.title ?? '';
    case 'research_direction':
      return candidate.research_direction ?? '';
    case 'recent_papers':
      return candidate.recent_papers.join(' ');
  }
};

const CRAWL_CANDIDATE_SEARCH_SCOPE_SET = new Set<CrawlCandidateSearchScope>(
  DEFAULT_CRAWL_CANDIDATE_SEARCH_SCOPES,
);

export const normalizeCrawlCandidateSearchScopes = (
  scopes: CrawlCandidateSearchScope[] | null | undefined,
) => {
  const normalizedScopes = Array.from(
    new Set(
      (scopes ?? []).filter((scope) =>
        CRAWL_CANDIDATE_SEARCH_SCOPE_SET.has(scope),
      ),
    ),
  );
  return normalizedScopes.length > 0
    ? normalizedScopes
    : [...DEFAULT_CRAWL_CANDIDATE_SEARCH_SCOPES];
};

export const filterCrawlCandidates = (
  candidates: CrawlCandidateDTO[],
  filters: CrawlCandidateFilters,
): CrawlCandidateDTO[] => {
  const keywordTerms = normalizeSearchText(filters.keyword)
    .trim()
    .split(/\s+/)
    .filter(Boolean);

  return candidates.filter((candidate) => {
    if (
      filters.reviewStatus !== 'all' &&
      candidate.review_status !== filters.reviewStatus
    ) {
      return false;
    }
    if (!matchesInformationConditions(candidate, filters)) {
      return false;
    }
    if (keywordTerms.length === 0) {
      return true;
    }

    const searchScopes = normalizeCrawlCandidateSearchScopes(
      filters.searchScopes,
    );
    const searchableText = normalizeSearchText(
      searchScopes
        .map((scope) => getCandidateSearchText(candidate, scope))
        .join(' '),
    );

    return keywordTerms.every((term) => searchableText.includes(term));
  });
};

export const hasActiveCrawlCandidateFilters = (
  filters: CrawlCandidateFilters,
) =>
  Boolean(filters.keyword.trim()) ||
  getInformationConditionEntries(filters.informationConditions).length > 0 ||
  filters.reviewStatus !== 'all';

export const getReviewableCandidateIds = (
  candidates: CrawlCandidateDTO[],
): number[] =>
  candidates
    .filter((candidate) => candidate.review_status === 'pending')
    .map((candidate) => candidate.id);

export const getImportableCandidateIds = (
  candidates: CrawlCandidateDTO[],
): number[] =>
  candidates
    .filter(
      (candidate) =>
        candidate.review_status === 'pending' && Boolean(candidate.email?.trim()),
    )
    .map((candidate) => candidate.id);

export const getReviewableCandidateIdsWithoutEmail = (
  candidates: CrawlCandidateDTO[],
): number[] =>
  candidates
    .filter(
      (candidate) =>
        candidate.review_status === 'pending' && !candidate.email?.trim(),
    )
    .map((candidate) => candidate.id);

export const pruneSelectedCandidateIds = (
  selectedCandidateIds: number[],
  candidates: CrawlCandidateDTO[],
): number[] => {
  const reviewableIds = new Set(getReviewableCandidateIds(candidates));

  return selectedCandidateIds.filter((candidateId) =>
    reviewableIds.has(candidateId),
  );
};
