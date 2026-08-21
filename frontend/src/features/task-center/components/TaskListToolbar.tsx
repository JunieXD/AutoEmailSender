import { ArrowDown, ArrowUp, Check, Search } from "lucide-react";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { KeywordSearchScopeSelect } from "@/components/molecules/KeywordSearchScopeSelect";
import {
  TASK_SEARCH_SCOPE_OPTIONS,
  TASK_SORT_OPTIONS,
  TASK_STATUS_OPTIONS,
  getTaskSearchPlaceholder,
  normalizeTaskSearchScopes,
  type TaskListFilter,
  type TaskSortDirection,
  type TaskSortKey,
  type TasksTab,
} from "../model/taskCenterFilters";

type TaskListToolbarProps = {
  activeTab: TasksTab;
  filters: TaskListFilter;
  sortDirections: Record<TaskSortKey, TaskSortDirection>;
  advancedFiltersOpen: boolean;
  advancedFilterCount: number;
  onFilterChange: (patch: Partial<TaskListFilter>) => void;
  onSortDirectionChange: (sortKey: TaskSortKey) => void;
  onAdvancedFiltersToggle: () => void;
  onReset: () => void;
};

export const TaskListToolbar = ({
  activeTab,
  filters,
  sortDirections,
  advancedFiltersOpen,
  advancedFilterCount,
  onFilterChange,
  onSortDirectionChange,
  onAdvancedFiltersToggle,
  onReset,
}: TaskListToolbarProps) => {
  const activeSortDirection = sortDirections[filters.sortKey];
  const selectedSortLabel =
    (TASK_SORT_OPTIONS.find((option) => option.value === filters.sortKey)
      ?.label ?? "创建时间") +
    " " +
    (activeSortDirection === "desc" ? "↓" : "↑");

  return (
    <div className="mt-3 grid gap-3">
      <div
        data-testid="task-filter-toolbar"
        className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(12rem,1fr)_auto_auto] lg:items-stretch"
      >
        <label className="flex h-12 min-w-0 items-center gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-0 text-sm text-stone-600 shadow-sm">
          <div className="shrink-0 font-medium leading-5 text-stone-800">
            关键词
          </div>
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <Search className="h-4 w-4 shrink-0 text-stone-400" />
            <input
              type="search"
              aria-label="搜索任务"
              value={filters.keyword}
              onChange={(event) => onFilterChange({ keyword: event.target.value })}
              placeholder={getTaskSearchPlaceholder(
                activeTab,
                filters.searchScopes,
              )}
              className="w-full min-w-0 bg-transparent leading-5 outline-none placeholder:text-stone-400"
            />
            <KeywordSearchScopeSelect
              label="搜索范围"
              options={TASK_SEARCH_SCOPE_OPTIONS[activeTab]}
              selectedValues={filters.searchScopes}
              embedded
              onChange={(searchScopes) =>
                onFilterChange({
                  searchScopes: normalizeTaskSearchScopes(
                    activeTab,
                    searchScopes,
                  ),
                })
              }
            />
          </div>
        </label>

        <div
          data-testid="task-sort-control"
          className="flex h-12 min-w-0 items-center gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-0 text-sm text-stone-600 shadow-sm"
        >
          <div className="shrink-0 font-medium leading-5 text-stone-800">
            排序
          </div>
          <NativeSelectField
            ariaLabel="任务排序"
            value={filters.sortKey}
            selectedLabel={selectedSortLabel}
            onChange={(event) =>
              onFilterChange({ sortKey: event.target.value as TaskSortKey })
            }
            wrapperClassName="h-full min-w-0 flex-1"
            embedded
            renderOption={(option, { selected, selectOption, closeMenu }) => {
              const optionKey = option.value as TaskSortKey;
              const direction = sortDirections[optionKey];
              return (
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    aria-pressed={selected}
                    aria-label={option.label}
                    onClick={selectOption}
                    className={
                      selected
                        ? "flex min-w-0 flex-1 items-center justify-between gap-3 rounded-xl bg-primary px-3 py-2 text-left text-[13px] leading-5 text-white shadow-sm shadow-primary/25 transition"
                        : "flex min-w-0 flex-1 items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-[13px] leading-5 text-stone-700 transition hover:bg-stone-100/90 hover:text-stone-900"
                    }
                  >
                    <span className="truncate">{option.label}</span>
                    {selected ? <Check className="h-4 w-4 shrink-0" /> : null}
                  </button>
                  <button
                    type="button"
                    aria-label={"切换" + option.label + "排序方向"}
                    onClick={(event) => {
                      event.stopPropagation();
                      onSortDirectionChange(optionKey);
                      closeMenu();
                    }}
                    className={
                      selected
                        ? "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary/20 bg-primary/10 text-primary transition"
                        : "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-stone-200 text-stone-500 transition hover:border-stone-300 hover:bg-stone-100 hover:text-stone-800"
                    }
                  >
                    {direction === "desc" ? (
                      <ArrowDown className="h-4 w-4" />
                    ) : (
                      <ArrowUp className="h-4 w-4" />
                    )}
                  </button>
                </div>
              );
            }}
          >
            {TASK_SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </NativeSelectField>
        </div>

        <button
          type="button"
          aria-expanded={advancedFiltersOpen}
          onClick={onAdvancedFiltersToggle}
          className={
            advancedFiltersOpen
              ? "ui-btn-secondary h-12 justify-center whitespace-nowrap border-primary/30 bg-primary/5 text-primary"
              : "ui-btn-secondary h-12 justify-center whitespace-nowrap"
          }
        >
          高级筛选
          {advancedFilterCount > 0 ? " " + advancedFilterCount : ""}
        </button>

        <button
          type="button"
          onClick={onReset}
          className="ui-btn-secondary h-12 justify-center whitespace-nowrap"
        >
          重置
        </button>
      </div>

      {advancedFiltersOpen ? (
        <div
          data-testid="task-advanced-filters"
          className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm"
        >
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm font-semibold text-stone-800">筛选条件</div>
            <button
              type="button"
              onClick={() => onFilterChange({ status: "all" })}
              className="ui-btn-secondary px-3 py-1.5 text-sm"
            >
              清空筛选
            </button>
          </div>
          <div className="max-w-sm">
            <NativeSelectField
              label="任务状态"
              ariaLabel="筛选任务状态"
              value={filters.status}
              selectedLabel={
                filters.status === "all"
                  ? "全部状态"
                  : TASK_STATUS_OPTIONS[activeTab].find(
                      (option) => option.value === filters.status,
                    )?.label ?? "全部状态"
              }
              onChange={(event) => onFilterChange({ status: event.target.value })}
              shellClassName="min-h-10 rounded-xl shadow-none"
            >
              <option value="all">全部状态</option>
              {TASK_STATUS_OPTIONS[activeTab].map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </NativeSelectField>
          </div>
        </div>
      ) : null}
    </div>
  );
};
