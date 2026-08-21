import type { CommunityMentorRecordDTO, ProfessorManagementItemDTO } from '@/types';


const COMMUNITY_REPOSITORY_URL =
  'https://github.com/JunieXD/AutoEmailSender-MentorData';

export const COMMUNITY_CONTRIBUTION_URL =
  `${COMMUNITY_REPOSITORY_URL}/issues/new?template=contribute-mentor.yml`;
export const COMMUNITY_BATCH_CONTRIBUTION_URL =
  `${COMMUNITY_REPOSITORY_URL}/issues/new?template=batch-contribution.yml`;
const COMMUNITY_REPORT_URL =
  `${COMMUNITY_REPOSITORY_URL}/issues/new?template=report-error.yml`;
export const COMMUNITY_CONTRIBUTION_SAFE_URL_LENGTH = 7_500;

export type CommunityContributionOmittedField =
  | 'research_direction'
  | 'recent_papers';

export type CommunityContributionPrefill = {
  url: string;
  omittedFields: CommunityContributionOmittedField[];
  exceedsSafeLength: boolean;
};

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

type BatchShareableMentor = Pick<
  ProfessorManagementItemDTO,
  'university' | 'school'
>;

const valueOrEmpty = (value: string | null | undefined) => value?.trim() ?? '';

const setSearchParam = (url: URL, field: string, value: string) => {
  if (value) {
    url.searchParams.set(field, value);
  }
};

export const buildCommunityContributionPrefill = (
  mentor: ShareableMentor,
): CommunityContributionPrefill => {
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
    profile_url: valueOrEmpty(mentor.profile_url),
    source_url: valueOrEmpty(mentor.source_url),
  };
  Object.entries(fields).forEach(([field, value]) => {
    setSearchParam(url, field, value);
  });

  const omittedFields: CommunityContributionOmittedField[] = [];
  const optionalFields: Array<[CommunityContributionOmittedField, string]> = [
    ['research_direction', valueOrEmpty(mentor.research_direction)],
    [
      'recent_papers',
      mentor.recent_papers.map((paper) => paper.trim()).filter(Boolean).join('\n'),
    ],
  ];
  optionalFields.forEach(([field, value]) => {
    if (!value) {
      return;
    }
    url.searchParams.set(field, value);
    if (url.toString().length > COMMUNITY_CONTRIBUTION_SAFE_URL_LENGTH) {
      url.searchParams.delete(field);
      omittedFields.push(field);
    }
  });

  const result = url.toString();
  return {
    url: result,
    omittedFields,
    exceedsSafeLength: result.length > COMMUNITY_CONTRIBUTION_SAFE_URL_LENGTH,
  };
};

export const buildCommunityContributionUrl = (mentor: ShareableMentor) =>
  buildCommunityContributionPrefill(mentor).url;

export const buildCommunityBatchContributionUrl = (
  mentors: BatchShareableMentor[],
) => {
  const url = new URL(COMMUNITY_BATCH_CONTRIBUTION_URL);
  const groups = new Map<
    string,
    { university: string; school: string; count: number }
  >();

  mentors.forEach((mentor) => {
    const university = valueOrEmpty(mentor.university);
    const school = valueOrEmpty(mentor.school);
    const key = `${university}\u0000${school}`;
    const existing = groups.get(key);
    if (existing) {
      existing.count += 1;
      return;
    }
    groups.set(key, { university, school, count: 1 });
  });

  const primaryGroup = Array.from(groups.values()).reduce<
    { university: string; school: string; count: number } | null
  >(
    (current, group) => (!current || group.count > current.count ? group : current),
    null,
  );

  const institution = primaryGroup
    ? `${primaryGroup.university}${primaryGroup.school}`
    : '';
  const suffix = groups.size > 1 ? '等' : '';
  url.searchParams.set(
    'title',
    `[批量投稿] ${institution ? `${institution}${suffix}` : '导师信息'}`,
  );
  return url.toString();
};

const buildCommunityReportCurrentValue = (
  record: CommunityMentorRecordDTO,
  includeResearchDirection: boolean,
) =>
  [
    `姓名：${record.name}`,
    `邮箱：${record.email}`,
    `职称：${valueOrEmpty(record.title)}`,
    `学校：${record.university}`,
    `学院：${valueOrEmpty(record.school)}`,
    `系所：${valueOrEmpty(record.department)}`,
    ...(includeResearchDirection
      ? [`研究方向：${valueOrEmpty(record.research_direction)}`]
      : []),
    `导师主页：${valueOrEmpty(record.profile_url)}`,
    `发现来源页：${record.source_url}`,
  ].join('\n');

export const buildCommunityReportUrl = (record: CommunityMentorRecordDTO) => {
  const url = new URL(COMMUNITY_REPORT_URL);
  const mentorName = record.name.trim();
  const titledMentorName = mentorName.endsWith('老师') ? mentorName : `${mentorName}老师`;
  url.searchParams.set('title', `[信息反馈] ${record.university.trim()}${titledMentorName}`);
  url.searchParams.set('record_id', record.id);
  const coreCurrentValue = buildCommunityReportCurrentValue(record, false);
  url.searchParams.set('current_value', coreCurrentValue);
  if (valueOrEmpty(record.research_direction)) {
    url.searchParams.set('current_value', buildCommunityReportCurrentValue(record, true));
    if (url.toString().length > COMMUNITY_CONTRIBUTION_SAFE_URL_LENGTH) {
      url.searchParams.set('current_value', coreCurrentValue);
    }
  }
  return url.toString();
};
