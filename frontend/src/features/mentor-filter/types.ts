import type { MentorStatus } from '@/types';

export const ALL_FILTER_VALUE = 'ALL' as const;

export type MentorFilterValue = typeof ALL_FILTER_VALUE;
export type MentorStatusFilter = MentorStatus | MentorFilterValue;

/** 学校+学院合并多选 */
export interface UniversitySchoolPair {
  university: string;
  school: string;
}

export interface MentorFilterState {
  keyword: string;
  universitySchoolPairs: UniversitySchoolPair[];
  title: string;
  matchScoreRange: string;
  status: MentorStatusFilter;
}

export const MATCH_SCORE_RANGES = [
  { label: '全部', value: 'ALL' },
  { label: '90以上', value: '90' },
  { label: '80以上', value: '80' },
  { label: '70以上', value: '70' },
  { label: '60以上', value: '60' },
] as const;

export interface MentorFilterOptions {
  universities: string[];
  allSchools: string[];
  schoolsByUniversity: Record<string, string[]>;
  /** 合并后的选项列表，格式："清华大学 / 计算机系" */
  universitySchoolOptions: UniversitySchoolPair[];
  titleOptions: string[];
}
