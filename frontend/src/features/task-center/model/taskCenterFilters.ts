import {
  BATCH_TASK_STATUS_LABELS,
  MATCH_ANALYSIS_JOB_STATUS_LABELS,
  PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS,
  type TaskListView,
} from "@/types";
import { CRAWL_JOB_STATUS_LABELS } from "./taskCenterJobs";

export type TasksTab = "batch" | "crawl" | "match" | "enrichment";
export type TaskListViews = Record<TasksTab, TaskListView>;
export type TaskSortKey = "updated" | "created" | "name" | "progress";
export type TaskSortDirection = "asc" | "desc";
export type TaskSearchScope =
  | "name"
  | "emailSubject"
  | "template"
  | "university"
  | "school"
  | "url"
  | "event";
export type TaskListFilter = {
  keyword: string;
  searchScopes: TaskSearchScope[];
  sortKey: TaskSortKey;
  status: string;
};
export type TaskListFilters = Record<TasksTab, TaskListFilter>;

export const TASK_SORT_OPTIONS: ReadonlyArray<{
  value: TaskSortKey;
  label: string;
}> = [
  { value: "updated", label: "最近更新" },
  { value: "created", label: "创建时间" },
  { value: "name", label: "任务名称" },
  { value: "progress", label: "任务进度" },
];

export const DEFAULT_TASK_SORT_DIRECTIONS: Record<
  TaskSortKey,
  TaskSortDirection
> = {
  updated: "desc",
  created: "desc",
  name: "asc",
  progress: "desc",
};

export const TASK_SEARCH_SCOPE_OPTIONS: Record<
  TasksTab,
  ReadonlyArray<{ value: TaskSearchScope; label: string }>
> = {
  batch: [
    { value: "name", label: "任务名称" },
    { value: "emailSubject", label: "邮件主题" },
    { value: "template", label: "邮件模板" },
  ],
  crawl: [
    { value: "university", label: "学校" },
    { value: "school", label: "学院" },
    { value: "url", label: "抓取地址" },
    { value: "event", label: "进度消息" },
  ],
  match: [{ value: "name", label: "任务名称" }],
  enrichment: [{ value: "name", label: "任务名称" }],
};

export const getDefaultTaskSearchScopes = (tab: TasksTab) =>
  TASK_SEARCH_SCOPE_OPTIONS[tab].map((option) => option.value);

export const normalizeTaskSearchScopes = (
  tab: TasksTab,
  values: TaskSearchScope[],
) => {
  const allowedScopes = new Set(getDefaultTaskSearchScopes(tab));
  const normalized = values.filter((value) => allowedScopes.has(value));
  return normalized.length > 0 ? normalized : getDefaultTaskSearchScopes(tab);
};

export const getTaskSearchPlaceholder = (
  tab: TasksTab,
  searchScopes: TaskSearchScope[],
) => {
  const selectedScopes = new Set(normalizeTaskSearchScopes(tab, searchScopes));
  return TASK_SEARCH_SCOPE_OPTIONS[tab]
    .filter((option) => selectedScopes.has(option.value))
    .map((option) => option.label)
    .join("、");
};

export const createDefaultTaskListFilters = (): TaskListFilters => ({
  batch: {
    keyword: "",
    searchScopes: getDefaultTaskSearchScopes("batch"),
    sortKey: "created",
    status: "all",
  },
  crawl: {
    keyword: "",
    searchScopes: getDefaultTaskSearchScopes("crawl"),
    sortKey: "created",
    status: "all",
  },
  match: {
    keyword: "",
    searchScopes: getDefaultTaskSearchScopes("match"),
    sortKey: "created",
    status: "all",
  },
  enrichment: {
    keyword: "",
    searchScopes: getDefaultTaskSearchScopes("enrichment"),
    sortKey: "created",
    status: "all",
  },
});

export const TASK_STATUS_OPTIONS: Record<
  TasksTab,
  ReadonlyArray<{ value: string; label: string }>
> = {
  batch: Object.entries(BATCH_TASK_STATUS_LABELS).map(([value, label]) => ({
    value,
    label,
  })),
  crawl: Object.entries(CRAWL_JOB_STATUS_LABELS).map(([value, label]) => ({
    value,
    label,
  })),
  match: Object.entries(MATCH_ANALYSIS_JOB_STATUS_LABELS).map(
    ([value, label]) => ({ value, label }),
  ),
  enrichment: Object.entries(PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS).map(
    ([value, label]) => ({ value, label }),
  ),
};

const TASK_NAME_COLLATOR = new Intl.Collator("zh-CN", {
  numeric: true,
  sensitivity: "base",
});

export const filterAndSortTaskItems = <T,>({
  items,
  filters,
  direction,
  getSearchValuesByScope,
  getName,
  getStatus,
  getCreatedAt,
  getUpdatedAt,
  getProgress,
}: {
  items: T[];
  filters: TaskListFilter;
  direction: TaskSortDirection;
  getSearchValuesByScope: (
    item: T,
  ) => Partial<
    Record<TaskSearchScope, Array<string | number | null | undefined>>
  >;
  getName: (item: T) => string;
  getStatus: (item: T) => string;
  getCreatedAt: (item: T) => string;
  getUpdatedAt: (item: T) => string;
  getProgress: (item: T) => number;
}) => {
  const normalizedKeyword = filters.keyword.trim().toLocaleLowerCase("zh-CN");
  const directionMultiplier = direction === "asc" ? 1 : -1;

  return items
    .filter((item) => {
      if (filters.status !== "all" && getStatus(item) !== filters.status) {
        return false;
      }
      if (!normalizedKeyword) {
        return true;
      }
      const searchValuesByScope = getSearchValuesByScope(item);
      return filters.searchScopes.some((scope) =>
        (searchValuesByScope[scope] ?? []).some((value) =>
          String(value ?? "")
            .toLocaleLowerCase("zh-CN")
            .includes(normalizedKeyword),
        ),
      );
    })
    .sort((left, right) => {
      let comparison = 0;
      if (filters.sortKey === "name") {
        comparison = TASK_NAME_COLLATOR.compare(getName(left), getName(right));
      } else if (filters.sortKey === "progress") {
        comparison = getProgress(left) - getProgress(right);
      } else {
        const getDate = filters.sortKey === "created" ? getCreatedAt : getUpdatedAt;
        comparison = Date.parse(getDate(left)) - Date.parse(getDate(right));
      }
      return comparison * directionMultiplier;
    });
};
