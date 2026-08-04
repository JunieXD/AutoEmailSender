import { describe, expect, it } from 'vitest';
import {
  COMMUNITY_BATCH_CONTRIBUTION_URL,
  COMMUNITY_CONTRIBUTION_SAFE_URL_LENGTH,
  COMMUNITY_CONTRIBUTION_URL,
  buildCommunityBatchContributionUrl,
  buildCommunityContributionPrefill,
  buildCommunityContributionUrl,
  buildCommunityReportUrl,
} from '@/lib/communityMentorLinks';
import type { CommunityMentorRecordDTO, ProfessorManagementItemDTO } from '@/types';


const professor: ProfessorManagementItemDTO = {
  id: 1,
  name: '张老师',
  email: 'zhang@example.edu',
  title: '教授',
  university: '示例大学',
  school: '计算机学院',
  department: '人工智能系',
  research_direction: '智能体',
  personal_note: '不得复制',
  recent_papers: ['Example Paper'],
  profile_url: 'https://example.edu/profile',
  source_url: 'https://example.edu/source',
  crawl_status: 'discovered',
  skip_reason: null,
  archived_at: null,
  created_at: '2026-08-03T00:00:00Z',
  updated_at: '2026-08-03T00:00:00Z',
  tags: [],
};

const record: CommunityMentorRecordDTO = {
  id: 'mentor_example0001',
  name: '张老师',
  email: 'zhang@example.edu',
  title: '教授',
  university: '示例大学',
  school: '计算机学院',
  department: '人工智能系',
  research_direction: '智能体',
  recent_papers: ['Example Paper'],
  profile_url: 'https://example.edu/profile',
  source_url: 'https://example.edu/source',
  status: 'active',
  last_verified_at: '2026-08-03T00:00:00Z',
  contacts: [],
  affiliations: [],
  contributors: [],
};

describe('community mentor GitHub helpers', () => {
  it('prefills the single-mentor form without exposing private fields', () => {
    const url = new URL(buildCommunityContributionUrl(professor));

    expect(url.searchParams.get('template')).toBe('contribute-mentor.yml');
    expect(url.searchParams.get('title')).toBe('[导师投稿] 示例大学张老师');
    expect(url.searchParams.get('name')).toBe('张老师');
    expect(url.searchParams.get('email')).toBe('zhang@example.edu');
    expect(url.searchParams.get('university')).toBe('示例大学');
    expect(url.searchParams.get('school')).toBe('计算机学院');
    expect(url.searchParams.get('department')).toBe('人工智能系');
    expect(url.searchParams.get('academic_title')).toBe('教授');
    expect(url.searchParams.get('research_direction')).toBe('智能体');
    expect(url.searchParams.get('recent_papers')).toBe('Example Paper');
    expect(url.searchParams.get('profile_url')).toBe('https://example.edu/profile');
    expect(url.searchParams.get('source_url')).toBe('https://example.edu/source');
    expect(url.toString()).not.toContain('不得复制');
  });

  it('prefills the report title, stable record id, and current community values', () => {
    const url = new URL(buildCommunityReportUrl(record));

    expect(url.searchParams.get('template')).toBe('report-error.yml');
    expect(url.searchParams.get('title')).toBe('[信息反馈] 示例大学张老师');
    expect(url.searchParams.get('record_id')).toBe('mentor_example0001');
    expect(url.searchParams.get('current_value')).toContain('邮箱：zhang@example.edu');
    expect(url.searchParams.get('current_value')).toContain('发现来源页：https://example.edu/source');
    expect(url.searchParams.get('evidence_url')).toBeNull();

    const titleWithoutTeacherSuffix = new URL(
      buildCommunityReportUrl({ ...record, name: '张伟' }),
    ).searchParams.get('title');
    expect(titleWithoutTeacherSuffix).toBe('[信息反馈] 示例大学张伟老师');
  });

  it('keeps contribution entry points on the dedicated community repository', () => {
    expect(COMMUNITY_CONTRIBUTION_URL).toContain(
      'JunieXD/AutoEmailSender-MentorData/issues/new',
    );
    expect(COMMUNITY_BATCH_CONTRIBUTION_URL).toContain(
      'template=batch-contribution.yml',
    );
  });

  it('prefills a batch title with the shared university and school', () => {
    const url = new URL(buildCommunityBatchContributionUrl([
      professor,
      { ...professor, id: 2, name: '王老师', email: 'wang@example.edu' },
    ]));

    expect(url.searchParams.get('template')).toBe('batch-contribution.yml');
    expect(url.searchParams.get('title')).toBe('[批量投稿] 示例大学计算机学院');
  });

  it('uses the largest institution group and appends 等 for a mixed batch', () => {
    const url = new URL(buildCommunityBatchContributionUrl([
      professor,
      { ...professor, id: 2, name: '王老师', email: 'wang@example.edu' },
      {
        ...professor,
        id: 3,
        name: '李老师',
        email: 'li@example.edu',
        university: '另一大学',
        school: '生命科学学院',
      },
    ]));

    expect(url.searchParams.get('title')).toBe('[批量投稿] 示例大学计算机学院等');
  });

  it('keeps GitHub prefill URLs below the verified safe budget without silently truncating text', () => {
    const result = buildCommunityContributionPrefill({
      ...professor,
      research_direction: '研'.repeat(1_000),
      recent_papers: ['A short paper'],
    });
    const url = new URL(result.url);

    expect(result.url.length).toBeLessThanOrEqual(COMMUNITY_CONTRIBUTION_SAFE_URL_LENGTH);
    expect(result.omittedFields).toEqual(['research_direction']);
    expect(result.exceedsSafeLength).toBe(false);
    expect(url.searchParams.get('research_direction')).toBeNull();
    expect(url.searchParams.get('recent_papers')).toBe('A short paper');
    expect(url.searchParams.get('academic_title')).toBe('教授');
  });

  it('drops an oversized publication field as a whole and keeps the core contribution fields', () => {
    const result = buildCommunityContributionPrefill({
      ...professor,
      recent_papers: ['论'.repeat(1_000)],
    });
    const url = new URL(result.url);

    expect(result.omittedFields).toEqual(['recent_papers']);
    expect(url.searchParams.get('recent_papers')).toBeNull();
    expect(url.searchParams.get('name')).toBe('张老师');
    expect(url.searchParams.get('academic_title')).toBe('教授');
    expect(url.searchParams.get('profile_url')).toBe('https://example.edu/profile');
    expect(url.searchParams.get('source_url')).toBe('https://example.edu/source');
  });
});
