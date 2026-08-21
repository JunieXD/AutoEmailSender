import {
  DEFAULT_CRAWL_CANDIDATE_FILTERS,
  type CrawlCandidateFilters,
  type CrawlCandidateInformationCondition,
  type CrawlCandidateInformationField,
  type CrawlCandidateSearchScope,
} from "@/features/crawl-review/client/reviewCandidates";
import type {
  CrawlCandidateDTO,
  CrawlCandidateReviewStatusDTO,
  CrawlCandidateUpdatePayloadDTO,
} from "@/types";

export type CrawlCandidateEditForm = {
  name: string;
  email: string;
  title: string;
  university: string;
  school: string;
  department: string;
  researchDirection: string;
  recentPapers: string;
  profileUrl: string;
  sourceUrl: string;
};

export const CRAWL_CANDIDATE_EDIT_INPUT_CLASS =
  "mt-2 w-full rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-900 outline-none transition placeholder:text-stone-400 focus:border-primary focus:ring-2 focus:ring-primary/20";

export const toCrawlCandidateEditForm = (
  candidate: CrawlCandidateDTO,
): CrawlCandidateEditForm => ({
  name: candidate.name,
  email: candidate.email ?? "",
  title: candidate.title ?? "",
  university: candidate.university ?? "",
  school: candidate.school ?? "",
  department: candidate.department ?? "",
  researchDirection: candidate.research_direction ?? "",
  recentPapers: candidate.recent_papers.join("\n"),
  profileUrl: candidate.profile_url ?? "",
  sourceUrl: candidate.source_url ?? "",
});

const toNullableTrimmedText = (value: string) => value.trim() || null;

export const toCrawlCandidateUpdatePayload = (
  candidate: CrawlCandidateDTO,
  form: CrawlCandidateEditForm,
): CrawlCandidateUpdatePayloadDTO => ({
  name: form.name.trim(),
  email: toNullableTrimmedText(form.email),
  title: toNullableTrimmedText(form.title),
  university: toNullableTrimmedText(form.university),
  school: toNullableTrimmedText(form.school),
  department: toNullableTrimmedText(form.department),
  research_direction: toNullableTrimmedText(form.researchDirection),
  recent_papers: form.recentPapers
    .split(/\r?\n/)
    .map((paper) => paper.trim())
    .filter(Boolean),
  profile_url: toNullableTrimmedText(form.profileUrl),
  source_url: toNullableTrimmedText(form.sourceUrl),
  review_status: candidate.review_status,
});

export const hasUnsavedCrawlCandidateChanges = (
  candidate: CrawlCandidateDTO,
  form: CrawlCandidateEditForm,
) => {
  const initialForm = toCrawlCandidateEditForm(candidate);
  return (Object.keys(initialForm) as (keyof CrawlCandidateEditForm)[]).some(
    (field) => initialForm[field] !== form[field],
  );
};

export const CRAWL_CANDIDATE_REVIEW_STATUS_LABELS: Record<
  CrawlCandidateReviewStatusDTO,
  string
> = {
  pending: "待审核",
  accepted: "已通过",
  rejected: "已拒绝",
  merged: "已合并",
};

export const CRAWL_CANDIDATE_REVIEW_STATUS_TONES: Record<
  CrawlCandidateReviewStatusDTO,
  string
> = {
  pending: "border-amber-200 bg-amber-50 text-amber-700",
  accepted: "border-emerald-200 bg-emerald-50 text-emerald-700",
  rejected: "border-red-200 bg-red-50 text-red-700",
  merged: "border-sky-200 bg-sky-50 text-sky-700",
};

export const CRAWL_CANDIDATE_SEARCH_SCOPE_OPTIONS: ReadonlyArray<{
  value: CrawlCandidateSearchScope;
  label: string;
}> = [
  { value: "name", label: "姓名" },
  { value: "email", label: "邮箱" },
  { value: "organization", label: "学校与任职" },
  { value: "title", label: "职称" },
  { value: "research_direction", label: "研究方向" },
  { value: "recent_papers", label: "近期论文" },
];

export const CRAWL_CANDIDATE_INFORMATION_FIELD_OPTIONS: ReadonlyArray<{
  field: CrawlCandidateInformationField;
  label: string;
}> = [
  { field: "email", label: "邮箱" },
  { field: "title", label: "职称" },
  { field: "department", label: "系所" },
  { field: "profile_url", label: "个人主页" },
  { field: "research_direction", label: "研究方向" },
  { field: "recent_papers", label: "近期论文" },
];

const CRAWL_CANDIDATE_INFORMATION_FIELD_LABELS = Object.fromEntries(
  CRAWL_CANDIDATE_INFORMATION_FIELD_OPTIONS.map(({ field, label }) => [
    field,
    label,
  ]),
) as Record<CrawlCandidateInformationField, string>;

export const getCrawlCandidateInformationConditionEntries = (
  conditions: CrawlCandidateFilters["informationConditions"],
) =>
  Object.entries(conditions) as Array<
    [CrawlCandidateInformationField, CrawlCandidateInformationCondition]
  >;

export const getCrawlCandidateInformationConditionLabel = (
  field: CrawlCandidateInformationField,
  condition: CrawlCandidateInformationCondition,
) =>
  `${condition === "present" ? "有" : "无"}${
    CRAWL_CANDIDATE_INFORMATION_FIELD_LABELS[field]
  }`;

export const getCrawlCandidateInformationConditionsSummary = (
  filters: CrawlCandidateFilters,
) => {
  const conditionLabels = getCrawlCandidateInformationConditionEntries(
    filters.informationConditions,
  ).map(([field, condition]) =>
    getCrawlCandidateInformationConditionLabel(field, condition),
  );
  if (conditionLabels.length === 0) {
    return "添加资料条件";
  }

  const connector = filters.informationMatchMode === "all" ? " 且 " : " 或 ";
  if (conditionLabels.length <= 2) {
    return conditionLabels.join(connector);
  }
  return `${conditionLabels.slice(0, 2).join(connector)}等 ${
    conditionLabels.length
  } 项`;
};

export const getCrawlCandidateSearchPlaceholder = (
  scopes: CrawlCandidateSearchScope[],
) => {
  if (scopes.length !== 1) {
    return "搜索所选字段";
  }
  return `搜索${
    CRAWL_CANDIDATE_SEARCH_SCOPE_OPTIONS.find(
      (option) => option.value === scopes[0],
    )?.label ?? "所选字段"
  }`;
};

export const createDefaultCrawlCandidateFilters = (): CrawlCandidateFilters => ({
  ...DEFAULT_CRAWL_CANDIDATE_FILTERS,
  searchScopes: [...DEFAULT_CRAWL_CANDIDATE_FILTERS.searchScopes],
  informationConditions: {},
});
