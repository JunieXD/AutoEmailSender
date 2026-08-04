import { describe, expect, it } from 'vitest';
import {
  COMMUNITY_BATCH_CONTRIBUTION_URL,
  COMMUNITY_CONTRIBUTION_URL,
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
    expect(url.searchParams.get('current_value')).toContain('当前证据：https://example.edu/source');
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
});
