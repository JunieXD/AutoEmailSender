import { describe, expect, it } from 'vitest';
import {
  COMMUNITY_BATCH_CONTRIBUTION_URL,
  COMMUNITY_CONTRIBUTION_URL,
  buildCommunityContributionClipboard,
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
  it('copies only shareable professor fields for contribution', () => {
    const text = buildCommunityContributionClipboard(professor);

    expect(text).toContain('导师姓名：张老师');
    expect(text).toContain('官方证据页面：https://example.edu/source');
    expect(text).not.toContain('不得复制');
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
