import { describe, expect, it } from 'vitest';
import type { CrawlCandidateDTO } from '@/types';
import {
  DEFAULT_CRAWL_CANDIDATE_FILTERS,
  filterCrawlCandidates,
  getReviewableCandidateIdsWithoutEmail,
  getReviewableCandidateIds,
  hasActiveCrawlCandidateFilters,
  pruneSelectedCandidateIds,
} from './reviewCandidates';

const buildCandidate = (
  overrides: Partial<CrawlCandidateDTO> = {},
): CrawlCandidateDTO => ({
  id: 1,
  job_id: 10,
  professor_id: null,
  name: 'Alice',
  email: 'alice@example.edu',
  title: 'Professor',
  university: 'Test University',
  school: 'Computer Science',
  department: 'AI Lab',
  research_direction: 'LLM',
  recent_papers: ['Paper A'],
  profile_url: 'https://example.edu/alice',
  source_url: 'https://example.edu/faculty/alice',
  confidence: 0.91,
  field_confidence: { email: 0.98 },
  evidence: { source: 'faculty-page' },
  review_status: 'pending',
  created_at: '2026-04-27T10:00:00Z',
  updated_at: '2026-04-27T10:00:00Z',
  ...overrides,
});

describe('reviewCandidates', () => {
  it('returns only pending candidate ids as reviewable', () => {
    const candidates = [
      buildCandidate({ id: 1, review_status: 'pending' }),
      buildCandidate({ id: 2, review_status: 'rejected' }),
      buildCandidate({ id: 3, review_status: 'accepted' }),
      buildCandidate({ id: 4, review_status: 'merged' }),
    ];

    expect(getReviewableCandidateIds(candidates)).toEqual([1]);
  });

  it('returns only pending candidate ids without email', () => {
    const candidates = [
      buildCandidate({ id: 1, email: null, review_status: 'pending' }),
      buildCandidate({ id: 2, email: '', review_status: 'pending' }),
      buildCandidate({ id: 3, email: 'alice@example.edu', review_status: 'pending' }),
      buildCandidate({ id: 4, email: null, review_status: 'accepted' }),
    ];

    expect(getReviewableCandidateIdsWithoutEmail(candidates)).toEqual([1, 2]);
  });

  it('prunes selected ids that no longer exist or are no longer pending', () => {
    const candidates = [
      buildCandidate({ id: 1, review_status: 'pending' }),
      buildCandidate({ id: 2, review_status: 'rejected' }),
      buildCandidate({ id: 3, review_status: 'accepted' }),
      buildCandidate({ id: 4, review_status: 'pending' }),
    ];

    expect(pruneSelectedCandidateIds([4, 3, 2, 999, 1], candidates)).toEqual([4, 1]);
  });

  it('searches only the selected candidate fields', () => {
    const candidates = [
      buildCandidate({
        id: 1,
        name: '张老师',
        department: '人工智能系',
        research_direction: '多模态学习',
      }),
      buildCandidate({
        id: 2,
        name: '李老师',
        department: '软件工程系',
        research_direction: '程序分析',
      }),
    ];

    expect(
      filterCrawlCandidates(candidates, {
        ...DEFAULT_CRAWL_CANDIDATE_FILTERS,
        keyword: '多模态',
        searchScopes: ['name'],
      }).map((candidate) => candidate.id),
    ).toEqual([]);
    expect(
      filterCrawlCandidates(candidates, {
        ...DEFAULT_CRAWL_CANDIDATE_FILTERS,
        keyword: '多模态',
        searchScopes: ['research_direction'],
      }).map((candidate) => candidate.id),
    ).toEqual([1]);
  });

  it('combines information conditions with all/any matching', () => {
    const candidates = [
      buildCandidate({
        id: 1,
        email: 'one@example.edu',
        research_direction: null,
        recent_papers: ['Paper A'],
      }),
      buildCandidate({
        id: 2,
        email: 'two@example.edu',
        research_direction: '多模态学习',
        recent_papers: [],
      }),
      buildCandidate({
        id: 3,
        email: null,
        research_direction: '机器学习',
        recent_papers: ['Paper B'],
      }),
      buildCandidate({
        id: 4,
        email: 'four@example.edu',
        research_direction: '程序分析',
        recent_papers: ['Paper C'],
      }),
    ];

    expect(
      filterCrawlCandidates(candidates, {
        ...DEFAULT_CRAWL_CANDIDATE_FILTERS,
        informationConditions: {
          research_direction: 'present',
          recent_papers: 'present',
        },
      }).map((candidate) => candidate.id),
    ).toEqual([3, 4]);
    expect(
      filterCrawlCandidates(candidates, {
        ...DEFAULT_CRAWL_CANDIDATE_FILTERS,
        informationConditions: {
          email: 'present',
          research_direction: 'missing',
        },
      }).map((candidate) => candidate.id),
    ).toEqual([1]);
    expect(
      filterCrawlCandidates(candidates, {
        ...DEFAULT_CRAWL_CANDIDATE_FILTERS,
        informationConditions: {
          email: 'missing',
          research_direction: 'missing',
        },
        informationMatchMode: 'any',
      }).map((candidate) => candidate.id),
    ).toEqual([1, 3]);
  });

  it('reports whether any candidate filter is active', () => {
    expect(hasActiveCrawlCandidateFilters(DEFAULT_CRAWL_CANDIDATE_FILTERS)).toBe(false);
    expect(
      hasActiveCrawlCandidateFilters({
        ...DEFAULT_CRAWL_CANDIDATE_FILTERS,
        informationConditions: { profile_url: 'missing' },
      }),
    ).toBe(true);
  });
});
