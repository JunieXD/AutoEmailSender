import type {
  CrawlCandidateDTO,
  CrawlCandidateReviewStatusDTO,
} from '@/types';

export type CrawlCandidateInformationFilter =
  | 'all'
  | 'missing_email'
  | 'has_email'
  | 'missing_title'
  | 'missing_department'
  | 'missing_profile_url'
  | 'missing_research_direction'
  | 'missing_recent_papers';

export type CrawlCandidateReviewStatusFilter =
  | 'all'
  | CrawlCandidateReviewStatusDTO;

export type CrawlCandidateFilters = {
  keyword: string;
  information: CrawlCandidateInformationFilter;
  reviewStatus: CrawlCandidateReviewStatusFilter;
};

export const DEFAULT_CRAWL_CANDIDATE_FILTERS: CrawlCandidateFilters = {
  keyword: '',
  information: 'all',
  reviewStatus: 'all',
};

const isBlank = (value: string | null | undefined) => !value?.trim();

const normalizeSearchText = (value: string) =>
  value.normalize('NFKC').toLocaleLowerCase();

const matchesInformationFilter = (
  candidate: CrawlCandidateDTO,
  filter: CrawlCandidateInformationFilter,
) => {
  switch (filter) {
    case 'missing_email':
      return isBlank(candidate.email);
    case 'has_email':
      return !isBlank(candidate.email);
    case 'missing_title':
      return isBlank(candidate.title);
    case 'missing_department':
      return isBlank(candidate.department);
    case 'missing_profile_url':
      return isBlank(candidate.profile_url);
    case 'missing_research_direction':
      return isBlank(candidate.research_direction);
    case 'missing_recent_papers':
      return candidate.recent_papers.length === 0;
    default:
      return true;
  }
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
    if (!matchesInformationFilter(candidate, filters.information)) {
      return false;
    }
    if (keywordTerms.length === 0) {
      return true;
    }

    const searchableText = normalizeSearchText(
      [
        candidate.name,
        candidate.email,
        candidate.title,
        candidate.university,
        candidate.school,
        candidate.department,
        candidate.research_direction,
        candidate.recent_papers.join(' '),
        candidate.profile_url,
        candidate.source_url,
      ]
        .filter(Boolean)
        .join(' '),
    );

    return keywordTerms.every((term) => searchableText.includes(term));
  });
};

export const hasActiveCrawlCandidateFilters = (
  filters: CrawlCandidateFilters,
) =>
  Boolean(filters.keyword.trim()) ||
  filters.information !== 'all' ||
  filters.reviewStatus !== 'all';

export const getReviewableCandidateIds = (
  candidates: CrawlCandidateDTO[],
): number[] =>
  candidates
    .filter((candidate) => candidate.review_status === 'pending')
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
