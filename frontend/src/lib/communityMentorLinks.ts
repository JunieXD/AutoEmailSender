import type { CommunityMentorRecordDTO, ProfessorManagementItemDTO } from '@/types';


const COMMUNITY_REPOSITORY_URL =
  'https://github.com/JunieXD/AutoEmailSender-MentorData';

export const COMMUNITY_CONTRIBUTION_URL =
  `${COMMUNITY_REPOSITORY_URL}/issues/new?template=contribute-mentor.yml`;
export const COMMUNITY_BATCH_CONTRIBUTION_URL =
  `${COMMUNITY_REPOSITORY_URL}/issues/new?template=batch-contribution.yml`;
export const COMMUNITY_REPORT_URL =
  `${COMMUNITY_REPOSITORY_URL}/issues/new?template=report-error.yml`;

type ShareableMentor = Pick<
  ProfessorManagementItemDTO,
  | 'name'
  | 'email'
  | 'title'
  | 'university'
  | 'school'
  | 'department'
  | 'research_direction'
  | 'recent_papers'
  | 'profile_url'
  | 'source_url'
>;

const valueOrEmpty = (value: string | null | undefined) => value?.trim() ?? '';

export const buildCommunityContributionUrl = (mentor: ShareableMentor) => {
  const url = new URL(COMMUNITY_CONTRIBUTION_URL);
  const mentorName = mentor.name.trim();
  const titledMentorName = mentorName.endsWith('老师') ? mentorName : `${mentorName}老师`;
  url.searchParams.set(
    'title',
    `[导师投稿] ${valueOrEmpty(mentor.university)}${titledMentorName}`,
  );
  const fields: Record<string, string> = {
    name: mentorName,
    email: valueOrEmpty(mentor.email),
    university: valueOrEmpty(mentor.university),
    school: valueOrEmpty(mentor.school),
    department: valueOrEmpty(mentor.department),
    academic_title: valueOrEmpty(mentor.title),
    research_direction: valueOrEmpty(mentor.research_direction),
    recent_papers: mentor.recent_papers.join('\n'),
    profile_url: valueOrEmpty(mentor.profile_url),
    source_url: valueOrEmpty(mentor.source_url),
  };
  Object.entries(fields).forEach(([field, value]) => {
    if (value) {
      url.searchParams.set(field, value);
    }
  });
  return url.toString();
};

const buildCommunityReportCurrentValue = (record: CommunityMentorRecordDTO) =>
  [
    `姓名：${record.name}`,
    `邮箱：${record.email}`,
    `职称：${valueOrEmpty(record.title)}`,
    `学校：${record.university}`,
    `学院：${valueOrEmpty(record.school)}`,
    `系所：${valueOrEmpty(record.department)}`,
    `研究方向：${valueOrEmpty(record.research_direction)}`,
    `官方主页：${valueOrEmpty(record.profile_url)}`,
    `当前证据：${record.source_url}`,
  ].join('\n');

export const buildCommunityReportUrl = (record: CommunityMentorRecordDTO) => {
  const url = new URL(COMMUNITY_REPORT_URL);
  const mentorName = record.name.trim();
  const titledMentorName = mentorName.endsWith('老师') ? mentorName : `${mentorName}老师`;
  url.searchParams.set('title', `[信息反馈] ${record.university.trim()}${titledMentorName}`);
  url.searchParams.set('record_id', record.id);
  url.searchParams.set('current_value', buildCommunityReportCurrentValue(record));
  return url.toString();
};
