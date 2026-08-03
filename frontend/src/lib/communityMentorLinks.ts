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

export const buildCommunityContributionClipboard = (mentor: ShareableMentor) =>
  [
    '请将以下内容粘贴到 GitHub 投稿表对应字段：',
    `导师姓名：${mentor.name}`,
    `公开工作邮箱：${valueOrEmpty(mentor.email)}`,
    `学校正式名称：${valueOrEmpty(mentor.university)}`,
    `学院或研究院正式名称：${valueOrEmpty(mentor.school)}`,
    `系所或中心：${valueOrEmpty(mentor.department)}`,
    `职称：${valueOrEmpty(mentor.title) || '未知或不填写'}`,
    `研究方向：${valueOrEmpty(mentor.research_direction)}`,
    `近期或代表论文：\n${mentor.recent_papers.join('\n')}`,
    `官方个人主页：${valueOrEmpty(mentor.profile_url)}`,
    `官方证据页面：${valueOrEmpty(mentor.source_url)}`,
  ].join('\n');

export const buildCommunityReportClipboard = (record: CommunityMentorRecordDTO) =>
  [
    '请将以下内容粘贴到 GitHub 反馈表对应字段，并补充新的官方证据：',
    `社区导师 ID：${record.id}`,
    '涉及字段：请填写需要纠正的字段',
    '当前社区值：',
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
  url.searchParams.set('title', `[信息反馈] ${record.name}（${record.id}）`);
  return url.toString();
};

export const copyCommunityText = async (text: string): Promise<void> => {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.setAttribute('readonly', '');
  textArea.style.position = 'fixed';
  textArea.style.opacity = '0';
  document.body.appendChild(textArea);
  textArea.select();
  const copied = document.execCommand('copy');
  textArea.remove();
  if (!copied) {
    throw new Error('无法写入剪贴板');
  }
};
