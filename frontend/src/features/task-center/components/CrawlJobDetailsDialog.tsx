import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { KeywordSearchScopeSelect } from "@/components/molecules/KeywordSearchScopeSelect";
import { Pagination } from "@/components/molecules/Pagination";
import { SelectionToggleButton } from "@/components/molecules/SelectionToggleButton";
import { getCrawlEventFailureReason } from "@/features/crawl-review/client/crawlJobEvents";
import {
  normalizeCrawlCandidateSearchScopes,
  type CrawlCandidateFilters,
  type CrawlCandidateInformationCondition,
  type CrawlCandidateInformationField,
  type CrawlCandidateInformationMatchMode,
  type CrawlCandidateReviewStatusFilter,
} from "@/features/crawl-review/client/reviewCandidates";
import {
  CRAWL_CANDIDATE_INFORMATION_FIELD_OPTIONS,
  CRAWL_CANDIDATE_REVIEW_STATUS_LABELS,
  CRAWL_CANDIDATE_REVIEW_STATUS_TONES,
  CRAWL_CANDIDATE_SEARCH_SCOPE_OPTIONS,
  getCrawlCandidateSearchPlaceholder,
  toCrawlCandidateEditForm,
  type CrawlCandidateEditForm,
} from "@/features/task-center/model/crawlCandidateReview";
import {
  formatDisplayTime,
  formatDuration,
  MONITOR_PAGE_SIZE_OPTIONS,
} from "@/features/task-center/model/taskCenterConfig";
import { CRAWL_JOB_STATUS_LABELS } from "@/features/task-center/model/taskCenterJobs";
import type { PaginationChange } from "@/lib/pagination";
import type { DismissableLayerClickHandlers } from "@/lib/useDismissableLayerClick";
import {
  type CrawlCandidateDTO,
  type CrawlJobEventDTO,
  type CrawlJobSummaryDTO,
  type CrawlPageDTO,
} from "@/types";
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  FileSearch,
  Loader2,
  Pencil,
  Search,
  Square,
  SquareCheck,
  SquareMinus,
  X,
} from "lucide-react";
import type { Dispatch, RefObject, SetStateAction } from "react";

type Props = {
  selectedCrawlJob: CrawlJobSummaryDTO | null;
  crawlJobDetailsLayer: DismissableLayerClickHandlers;
  closeCrawlJobDetails: () => void;
  crawlJobDetailsLoading: boolean;
  crawlEventsStartRef: RefObject<HTMLElement | null>;
  crawlExecutionLogEvents: CrawlJobEventDTO[];
  visibleCrawlJobEvents: CrawlJobEventDTO[];
  safeCrawlEventPage: number;
  crawlEventPageSize: number;
  handleCrawlEventPaginationChange: (change: PaginationChange) => void;
  crawlPagesStartRef: RefObject<HTMLElement | null>;
  crawlJobPages: CrawlPageDTO[];
  visibleCrawlJobPages: CrawlPageDTO[];
  safeCrawlDetailPagePage: number;
  crawlDetailPagePageSize: number;
  handleCrawlDetailPagePaginationChange: (change: PaginationChange) => void;
  crawlCandidatesStartRef: RefObject<HTMLElement | null>;
  crawlJobCandidates: CrawlCandidateDTO[];
  crawlCandidateFilters: CrawlCandidateFilters;
  updateCrawlCandidateFilters: (patch: Partial<CrawlCandidateFilters>) => void;
  crawlCandidateInformationConditionsSummary: string;
  crawlCandidateInformationFiltersOpen: boolean;
  setCrawlCandidateInformationFiltersOpen: Dispatch<SetStateAction<boolean>>;
  updateCrawlCandidateInformationCondition: (
    field: CrawlCandidateInformationField,
    condition: CrawlCandidateInformationCondition | "any",
  ) => void;
  activeCrawlCandidateInformationConditionCount: number;
  filteredCrawlJobCandidates: CrawlCandidateDTO[];
  selectedCrawlJobCanReview: boolean;
  reviewableCrawlCandidateIds: number[];
  importableCrawlCandidateIds: number[];
  reviewableCrawlCandidateIdsWithoutEmail: number[];
  crawlCandidateFiltersActive: boolean;
  resetCrawlCandidateFilters: () => void;
  allFilteredCrawlCandidatesSelected: boolean;
  handleToggleFilteredCrawlCandidateSelection: () => void;
  filteredReviewableCrawlCandidateIds: number[];
  crawlJobApproveLoading: boolean;
  crawlJobEnrichLoading: boolean;
  someFilteredCrawlCandidatesSelected: boolean;
  selectedReviewableCrawlCandidateIds: number[];
  filteredSelectedCrawlCandidateCount: number;
  selectedCrawlCandidateIdsWithoutEmail: number[];
  setSelectedCrawlCandidateIds: Dispatch<SetStateAction<number[]>>;
  handleEnrichSelectedCrawlCandidates: () => Promise<void>;
  handleApproveSelectedCrawlCandidates: () => Promise<void>;
  selectedImportableCrawlCandidateIds: number[];
  selectedCrawlJobNeedsReviewResume: boolean;
  visibleCrawlJobCandidates: CrawlCandidateDTO[];
  crawlCandidateFirstItemRef: RefObject<HTMLDivElement | null>;
  handleToggleCrawlCandidateSelection: (candidateId: number) => void;
  setSelectedCandidateDetail: Dispatch<
    SetStateAction<CrawlCandidateDTO | null>
  >;
  setCandidateEditForm: Dispatch<SetStateAction<CrawlCandidateEditForm | null>>;
  safeCrawlCandidatePage: number;
  crawlCandidatePageSize: number;
  handleCrawlCandidatePaginationChange: (change: PaginationChange) => void;
};

export function CrawlJobDetailsDialog({
  selectedCrawlJob,
  crawlJobDetailsLayer,
  closeCrawlJobDetails,
  crawlJobDetailsLoading,
  crawlEventsStartRef,
  crawlExecutionLogEvents,
  visibleCrawlJobEvents,
  safeCrawlEventPage,
  crawlEventPageSize,
  handleCrawlEventPaginationChange,
  crawlPagesStartRef,
  crawlJobPages,
  visibleCrawlJobPages,
  safeCrawlDetailPagePage,
  crawlDetailPagePageSize,
  handleCrawlDetailPagePaginationChange,
  crawlCandidatesStartRef,
  crawlJobCandidates,
  crawlCandidateFilters,
  updateCrawlCandidateFilters,
  crawlCandidateInformationConditionsSummary,
  crawlCandidateInformationFiltersOpen,
  setCrawlCandidateInformationFiltersOpen,
  updateCrawlCandidateInformationCondition,
  activeCrawlCandidateInformationConditionCount,
  filteredCrawlJobCandidates,
  selectedCrawlJobCanReview,
  reviewableCrawlCandidateIds,
  importableCrawlCandidateIds,
  reviewableCrawlCandidateIdsWithoutEmail,
  crawlCandidateFiltersActive,
  resetCrawlCandidateFilters,
  allFilteredCrawlCandidatesSelected,
  handleToggleFilteredCrawlCandidateSelection,
  filteredReviewableCrawlCandidateIds,
  crawlJobApproveLoading,
  crawlJobEnrichLoading,
  someFilteredCrawlCandidatesSelected,
  selectedReviewableCrawlCandidateIds,
  filteredSelectedCrawlCandidateCount,
  selectedCrawlCandidateIdsWithoutEmail,
  setSelectedCrawlCandidateIds,
  handleEnrichSelectedCrawlCandidates,
  handleApproveSelectedCrawlCandidates,
  selectedImportableCrawlCandidateIds,
  selectedCrawlJobNeedsReviewResume,
  visibleCrawlJobCandidates,
  crawlCandidateFirstItemRef,
  handleToggleCrawlCandidateSelection,
  setSelectedCandidateDetail,
  setCandidateEditForm,
  safeCrawlCandidatePage,
  crawlCandidatePageSize,
  handleCrawlCandidatePaginationChange,
}: Props) {
  return selectedCrawlJob ? (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-stone-950/30 p-0 sm:p-6"
      onClick={crawlJobDetailsLayer.onBackdropClick}
      onMouseDown={crawlJobDetailsLayer.onBackdropMouseDown}
    >
      <section
        role="dialog"
        aria-label="抓取任务详情"
        className="flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-[min(94vw,1280px)] sm:rounded-3xl"
        onClick={crawlJobDetailsLayer.onContentClick}
        onMouseDown={crawlJobDetailsLayer.onContentMouseDown}
      >
        <div className="flex items-start justify-between gap-4 border-b border-stone-200 bg-[#fcfbf8] px-6 py-5">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
              <Activity className="h-4 w-4 text-primary" />
              实时抓取监控
            </div>
            <h2 className="text-xl font-semibold text-stone-900">
              {selectedCrawlJob.university} / {selectedCrawlJob.school}
            </h2>
            <p className="mt-2 break-all text-sm text-stone-500">
              {selectedCrawlJob.start_url}
            </p>
          </div>
          <button
            type="button"
            onClick={closeCrawlJobDetails}
            className="ui-btn-secondary shrink-0"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
            关闭
          </button>
        </div>

        <div
          data-testid="crawl-job-detail-scroll"
          className="flex-1 space-y-6 overflow-y-auto overscroll-contain px-6 py-5"
        >
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">当前状态</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {CRAWL_JOB_STATUS_LABELS[selectedCrawlJob.status]}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">已抓页面</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedCrawlJob.page_count}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">候选导师</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedCrawlJob.candidate_count}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">
                输入 Token
              </div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedCrawlJob.input_tokens.toLocaleString("zh-CN")}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">
                输出 Token
              </div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedCrawlJob.output_tokens.toLocaleString("zh-CN")}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">
                缓存命中 Token
              </div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedCrawlJob.cached_tokens.toLocaleString("zh-CN")}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">总 Token</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedCrawlJob.total_tokens.toLocaleString("zh-CN")}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">已耗时长</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {formatDuration(selectedCrawlJob.duration_seconds)}
              </div>
            </div>
          </div>
          {selectedCrawlJob.error_message ? (
            <div className="rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
              {selectedCrawlJob.error_message}
            </div>
          ) : null}

          {crawlJobDetailsLoading ? (
            <div className="flex items-center gap-2 text-sm text-stone-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载日志详情…
            </div>
          ) : null}

          <div className="grid items-stretch gap-6 xl:grid-cols-2">
            <section
              ref={crawlEventsStartRef}
              tabIndex={-1}
              aria-label="抓取执行日志"
              className="flex h-full scroll-mt-6 flex-col focus:outline-none"
            >
              <h3 className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                <Activity className="h-4 w-4 text-primary" />
                执行日志
              </h3>
              <div className="mt-3 flex-1 space-y-2" data-monitor-section-list>
                {crawlExecutionLogEvents.length > 0 ? (
                  visibleCrawlJobEvents.map((event) => {
                    const failureReason = getCrawlEventFailureReason(event);
                    return (
                      <div key={event.id} className="flex h-[76px] gap-3">
                        <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-primary" />
                        <div className="flex h-full min-w-0 flex-1 flex-col justify-between rounded-2xl border border-stone-100 px-4 py-3">
                          <p
                            className="truncate text-sm text-stone-800"
                            title={event.message}
                          >
                            {event.message}
                          </p>
                          <div className="mt-1 flex min-w-0 items-center justify-between gap-2">
                            {failureReason ? (
                              <p
                                className="min-w-0 flex-1 truncate text-xs text-red-700"
                                title={`失败原因：${failureReason}`}
                              >
                                失败原因：{failureReason}
                              </p>
                            ) : null}
                            <p className="shrink-0 text-xs text-stone-500">
                              {formatDisplayTime(event.created_at, {
                                withSeconds: true,
                              })}
                            </p>
                          </div>
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-3 text-sm text-stone-500">
                    暂无执行日志。
                  </p>
                )}
              </div>
              <Pagination
                page={safeCrawlEventPage}
                pageSize={crawlEventPageSize}
                totalCount={crawlExecutionLogEvents.length}
                onChange={handleCrawlEventPaginationChange}
                ariaLabel="抓取执行日志分页"
                pageSizeAriaLabel="抓取执行日志每页数量"
                variant="compact"
                pageSizeOptions={MONITOR_PAGE_SIZE_OPTIONS}
                unitLabel="条"
                itemLabel="条日志"
                focusTargetRef={crawlEventsStartRef}
                menuPlacement="popover"
                className="mt-3 border-t border-stone-100 pt-3"
              />
            </section>

            <section
              ref={crawlPagesStartRef}
              tabIndex={-1}
              aria-label="已抓页面列表"
              className="flex h-full scroll-mt-6 flex-col focus:outline-none"
            >
              <h3 className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                <FileSearch className="h-4 w-4 text-sky-600" />
                已抓页面
              </h3>
              <div className="mt-3 flex-1 space-y-2" data-monitor-section-list>
                {crawlJobPages.length > 0 ? (
                  visibleCrawlJobPages.map((page) => (
                    <div
                      key={page.id}
                      className="flex h-[76px] min-w-0 flex-col justify-between rounded-2xl border border-stone-100 px-4 py-3"
                    >
                      <p
                        className="truncate text-sm font-medium text-stone-800"
                        title={page.title ?? page.url}
                      >
                        {page.title ?? page.url}
                      </p>
                      <a
                        href={page.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="mt-1 block truncate text-xs text-primary underline decoration-primary/30 underline-offset-2 transition-colors hover:text-primary-dark hover:decoration-primary focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-1"
                        title={page.url}
                      >
                        {page.url}
                      </a>
                    </div>
                  ))
                ) : (
                  <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-3 text-sm text-stone-500">
                    暂无已抓页面。
                  </p>
                )}
              </div>
              <Pagination
                page={safeCrawlDetailPagePage}
                pageSize={crawlDetailPagePageSize}
                totalCount={crawlJobPages.length}
                onChange={handleCrawlDetailPagePaginationChange}
                ariaLabel="已抓页面分页"
                pageSizeAriaLabel="已抓页面每页数量"
                variant="compact"
                pageSizeOptions={MONITOR_PAGE_SIZE_OPTIONS}
                unitLabel="个"
                itemLabel="个页面"
                focusTargetRef={crawlPagesStartRef}
                menuPlacement="popover"
                className="mt-3 border-t border-stone-100 pt-3"
              />
            </section>
          </div>

          <section
            ref={crawlCandidatesStartRef}
            tabIndex={-1}
            aria-label="候选导师列表"
            className="scroll-mt-6 focus:outline-none"
          >
            <h3 className="flex items-center gap-2 text-sm font-semibold text-stone-900">
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              候选导师
            </h3>
            <div className="mt-3 space-y-2">
              {crawlJobCandidates.length > 0 ? (
                <div
                  data-testid="crawl-candidate-review-toolbar"
                  className="overflow-visible rounded-2xl border border-stone-200 bg-stone-50/70"
                >
                  <div className="grid gap-3 p-4 md:grid-cols-2 xl:grid-cols-[minmax(22rem,2fr)_minmax(12rem,1fr)_minmax(11rem,1fr)]">
                    <div className="min-w-0 md:col-span-2 xl:col-span-1">
                      <div className="mb-2 text-sm font-medium text-stone-800">
                        关键词
                      </div>
                      <div className="ui-select-shell h-10 min-h-10 w-full py-0">
                        <Search className="h-4 w-4 shrink-0 text-stone-400" />
                        <input
                          type="search"
                          aria-label="搜索候选导师"
                          value={crawlCandidateFilters.keyword}
                          onChange={(event) =>
                            updateCrawlCandidateFilters({
                              keyword: event.target.value,
                            })
                          }
                          placeholder={getCrawlCandidateSearchPlaceholder(
                            crawlCandidateFilters.searchScopes,
                          )}
                          className="w-full min-w-0 bg-transparent text-sm leading-5 outline-none placeholder:text-stone-400"
                        />
                        <KeywordSearchScopeSelect
                          label="搜索范围"
                          options={CRAWL_CANDIDATE_SEARCH_SCOPE_OPTIONS}
                          selectedValues={crawlCandidateFilters.searchScopes}
                          embedded
                          onChange={(searchScopes) =>
                            updateCrawlCandidateFilters({
                              searchScopes:
                                normalizeCrawlCandidateSearchScopes(
                                  searchScopes,
                                ),
                            })
                          }
                        />
                      </div>
                    </div>
                    <div className="min-w-0">
                      <div className="mb-2 text-sm font-medium text-stone-800">
                        资料条件
                      </div>
                      <button
                        type="button"
                        aria-label={`资料条件：${crawlCandidateInformationConditionsSummary}`}
                        aria-expanded={crawlCandidateInformationFiltersOpen}
                        aria-controls="crawl-candidate-information-filters"
                        onClick={() =>
                          setCrawlCandidateInformationFiltersOpen(
                            (currentOpen) => !currentOpen,
                          )
                        }
                        className={`ui-select-shell h-10 min-h-10 w-full ${
                          crawlCandidateInformationFiltersOpen
                            ? "border-primary/45 bg-white ring-2 ring-primary/10"
                            : ""
                        }`}
                      >
                        <span className="flex-1 truncate text-left text-sm text-stone-700">
                          {crawlCandidateInformationConditionsSummary}
                        </span>
                        <ChevronDown
                          className={`ui-select-chevron ${
                            crawlCandidateInformationFiltersOpen
                              ? "rotate-180 text-primary"
                              : ""
                          }`}
                        />
                      </button>
                    </div>
                    <NativeSelectField
                      label="审核状态"
                      ariaLabel="候选导师审核状态"
                      value={crawlCandidateFilters.reviewStatus}
                      onChange={(event) =>
                        updateCrawlCandidateFilters({
                          reviewStatus: event.target
                            .value as CrawlCandidateReviewStatusFilter,
                        })
                      }
                      shellClassName="h-10 min-h-10"
                    >
                      <option value="all">全部状态</option>
                      <option value="pending">待审核</option>
                      <option value="accepted">已通过</option>
                      <option value="merged">已合并</option>
                      <option value="rejected">已拒绝</option>
                    </NativeSelectField>
                  </div>

                  {crawlCandidateInformationFiltersOpen ? (
                    <div
                      id="crawl-candidate-information-filters"
                      data-testid="crawl-candidate-information-filters"
                      className="border-t border-stone-200 bg-white px-4 py-4"
                    >
                      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                        {CRAWL_CANDIDATE_INFORMATION_FIELD_OPTIONS.map(
                          ({ field, label }) => (
                            <NativeSelectField
                              key={field}
                              label={label}
                              ariaLabel={`候选导师${label}条件`}
                              value={
                                crawlCandidateFilters.informationConditions[
                                  field
                                ] ?? "any"
                              }
                              onChange={(event) =>
                                updateCrawlCandidateInformationCondition(
                                  field,
                                  event.target.value as
                                    | CrawlCandidateInformationCondition
                                    | "any",
                                )
                              }
                              shellClassName="h-10 min-h-10"
                            >
                              <option value="any">不限</option>
                              <option value="present">有{label}</option>
                              <option value="missing">无{label}</option>
                            </NativeSelectField>
                          ),
                        )}
                      </div>
                      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-stone-100 pt-4">
                        <div>
                          <div className="text-sm font-medium text-stone-800">
                            多个资料条件之间
                          </div>
                          <div className="mt-1 text-xs text-stone-500">
                            {activeCrawlCandidateInformationConditionCount < 2
                              ? "选择两个及以上条件后可切换关系"
                              : `当前有 ${activeCrawlCandidateInformationConditionCount} 个条件`}
                          </div>
                        </div>
                        <div className="inline-flex gap-1 rounded-xl border border-stone-200 bg-stone-50 p-1">
                          {(
                            [
                              "all",
                              "any",
                            ] as CrawlCandidateInformationMatchMode[]
                          ).map((matchMode) => {
                            const selected =
                              crawlCandidateFilters.informationMatchMode ===
                              matchMode;
                            return (
                              <button
                                key={matchMode}
                                type="button"
                                aria-pressed={selected}
                                disabled={
                                  activeCrawlCandidateInformationConditionCount <
                                  2
                                }
                                onClick={() =>
                                  updateCrawlCandidateFilters({
                                    informationMatchMode: matchMode,
                                  })
                                }
                                className={`rounded-lg px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
                                  selected
                                    ? "bg-primary text-white shadow-sm shadow-primary/20"
                                    : "text-stone-600 hover:bg-white hover:text-stone-900"
                                }`}
                              >
                                {matchMode === "all"
                                  ? "全部满足（且）"
                                  : "任一满足（或）"}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  ) : null}

                  <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 bg-white/80 px-4 py-3">
                    <div className="text-sm text-stone-600">
                      显示 {filteredCrawlJobCandidates.length} /{" "}
                      {crawlJobCandidates.length} 位
                      {selectedCrawlJobCanReview ? (
                        <>
                          {" "}
                          · 待审核 {reviewableCrawlCandidateIds.length} 位 ·
                          可导入 {importableCrawlCandidateIds.length} 位 ·
                          无邮箱{" "}
                          {reviewableCrawlCandidateIdsWithoutEmail.length} 位
                        </>
                      ) : null}
                    </div>
                    {crawlCandidateFiltersActive ||
                    selectedCrawlJobCanReview ? (
                      <div className="flex flex-wrap items-center gap-2">
                        {crawlCandidateFiltersActive ? (
                          <button
                            type="button"
                            onClick={resetCrawlCandidateFilters}
                            className="ui-btn-secondary min-h-9 px-3 py-1.5 text-sm"
                          >
                            重置筛选
                          </button>
                        ) : null}
                        {selectedCrawlJobCanReview ? (
                          <button
                            type="button"
                            aria-label={
                              allFilteredCrawlCandidatesSelected
                                ? "取消全选"
                                : "全选当前结果"
                            }
                            aria-pressed={allFilteredCrawlCandidatesSelected}
                            onClick={
                              handleToggleFilteredCrawlCandidateSelection
                            }
                            disabled={
                              filteredReviewableCrawlCandidateIds.length ===
                                0 ||
                              crawlJobApproveLoading ||
                              crawlJobEnrichLoading
                            }
                            className={`inline-flex min-h-9 items-center gap-2 rounded-xl border px-3 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
                              allFilteredCrawlCandidatesSelected
                                ? "border-primary/30 bg-primary/5 text-primary"
                                : "border-stone-200 bg-white text-stone-700 hover:border-primary/40 hover:text-primary"
                            }`}
                          >
                            {allFilteredCrawlCandidatesSelected ? (
                              <SquareCheck className="h-4 w-4" />
                            ) : someFilteredCrawlCandidatesSelected ? (
                              <SquareMinus className="h-4 w-4" />
                            ) : (
                              <Square className="h-4 w-4" />
                            )}
                            {allFilteredCrawlCandidatesSelected
                              ? "取消全选"
                              : "全选当前结果"}
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </div>

                  {selectedCrawlJobCanReview &&
                  selectedReviewableCrawlCandidateIds.length > 0 ? (
                    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-amber-200 bg-amber-50/80 px-4 py-3">
                      <div className="text-sm text-amber-950">
                        已选 {selectedReviewableCrawlCandidateIds.length} 位
                        <span className="mt-1 block text-xs text-amber-700">
                          当前筛选结果中已选{" "}
                          {filteredSelectedCrawlCandidateCount} 位，其中无邮箱{" "}
                          {selectedCrawlCandidateIdsWithoutEmail.length} 位
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => setSelectedCrawlCandidateIds([])}
                          disabled={
                            crawlJobApproveLoading || crawlJobEnrichLoading
                          }
                          className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          清空选择
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            void handleEnrichSelectedCrawlCandidates()
                          }
                          disabled={
                            crawlJobApproveLoading || crawlJobEnrichLoading
                          }
                          className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {crawlJobEnrichLoading ? "补全中…" : "补全缺失信息"}
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            void handleApproveSelectedCrawlCandidates()
                          }
                          disabled={
                            selectedImportableCrawlCandidateIds.length === 0 ||
                            crawlJobApproveLoading ||
                            crawlJobEnrichLoading
                          }
                          className="ui-btn-primary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {crawlJobApproveLoading
                            ? "导入中…"
                            : "审核通过并导入"}
                        </button>
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
              {selectedCrawlJobNeedsReviewResume &&
              reviewableCrawlCandidateIds.length > 0 ? (
                <div className="rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm text-stone-700">
                  请先将任务转入待审核状态，再补全或审核导入候选导师。
                </div>
              ) : null}
              {filteredCrawlJobCandidates.length > 0 ? (
                visibleCrawlJobCandidates.map((candidate, index) => {
                  const candidateMissingEmail = !candidate.email?.trim();
                  const candidateCanEdit =
                    selectedCrawlJobCanReview &&
                    candidate.review_status === "pending";

                  return (
                    <div
                      key={candidate.id}
                      ref={index === 0 ? crawlCandidateFirstItemRef : undefined}
                      tabIndex={index === 0 ? -1 : undefined}
                      className="scroll-mt-6 rounded-2xl border border-stone-100 bg-white px-4 py-3 focus:outline-none"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="flex min-w-0 items-start gap-3">
                          {selectedCrawlJobCanReview ? (
                            <div className="shrink-0 self-center">
                              <SelectionToggleButton
                                label={`选择候选导师 ${candidate.name}`}
                                selected={selectedReviewableCrawlCandidateIds.includes(
                                  candidate.id,
                                )}
                                disabled={
                                  candidate.review_status !== "pending" ||
                                  crawlJobApproveLoading ||
                                  crawlJobEnrichLoading
                                }
                                onToggle={() =>
                                  handleToggleCrawlCandidateSelection(
                                    candidate.id,
                                  )
                                }
                              />
                            </div>
                          ) : null}
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="text-sm font-medium text-stone-800">
                                {candidate.name}
                              </p>
                              {candidate.title ? (
                                <span className="text-xs text-stone-500">
                                  {candidate.title}
                                </span>
                              ) : null}
                              {candidateMissingEmail ? (
                                <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
                                  邮箱为空
                                </span>
                              ) : null}
                            </div>
                            <p
                              className={`mt-1 break-all ${
                                candidateMissingEmail
                                  ? "text-xs text-amber-700"
                                  : "text-sm text-stone-600"
                              }`}
                            >
                              {candidate.email?.trim() ||
                                "暂无邮箱（可手工填写或选中后尝试使用补全功能）"}
                            </p>
                            {[candidate.school, candidate.department]
                              .filter(Boolean)
                              .join(" / ") ? (
                              <p className="mt-1 text-xs text-stone-400">
                                {[candidate.school, candidate.department]
                                  .filter(Boolean)
                                  .join(" / ")}
                              </p>
                            ) : null}
                            {selectedCrawlJobNeedsReviewResume &&
                            candidate.review_status === "pending" ? (
                              <p className="mt-2 text-xs text-amber-700">
                                先转入待审核后才可补全或审核导入
                              </p>
                            ) : null}
                          </div>
                        </div>
                        <div className="flex shrink-0 flex-wrap items-center gap-2">
                          <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs text-emerald-700">
                            置信度 {Math.round(candidate.confidence * 100)}%
                          </span>
                          <span
                            className={`rounded-full border px-3 py-1 text-xs ${
                              CRAWL_CANDIDATE_REVIEW_STATUS_TONES[
                                candidate.review_status
                              ]
                            }`}
                          >
                            {
                              CRAWL_CANDIDATE_REVIEW_STATUS_LABELS[
                                candidate.review_status
                              ]
                            }
                          </span>
                          {candidateMissingEmail && candidateCanEdit ? (
                            <button
                              type="button"
                              onClick={() => {
                                setSelectedCandidateDetail(candidate);
                                setCandidateEditForm(
                                  toCrawlCandidateEditForm(candidate),
                                );
                              }}
                              className="ui-btn-secondary px-3 py-2 text-sm"
                            >
                              <Pencil className="h-4 w-4" />
                              填写邮箱
                            </button>
                          ) : null}
                          <button
                            type="button"
                            onClick={() => {
                              setCandidateEditForm(null);
                              setSelectedCandidateDetail(candidate);
                            }}
                            className="ui-btn-secondary px-3 py-2 text-sm"
                          >
                            查看详情
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })
              ) : crawlJobCandidates.length > 0 ? (
                <div className="rounded-2xl border border-dashed border-stone-200 bg-white px-6 py-8 text-center">
                  <Search className="mx-auto h-6 w-6 text-stone-300" />
                  <p className="mt-3 text-sm font-medium text-stone-700">
                    没有符合筛选条件的候选导师
                  </p>
                </div>
              ) : (
                <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-3 text-sm text-stone-500">
                  暂无候选导师。
                </p>
              )}
            </div>
            {filteredCrawlJobCandidates.length > 0 ? (
              <Pagination
                page={safeCrawlCandidatePage}
                pageSize={crawlCandidatePageSize}
                totalCount={filteredCrawlJobCandidates.length}
                onChange={handleCrawlCandidatePaginationChange}
                ariaLabel="候选导师分页"
                pageSizeAriaLabel="候选导师每页数量"
                variant="compact"
                pageSizeOptions={MONITOR_PAGE_SIZE_OPTIONS}
                unitLabel="位"
                itemLabel="位导师"
                summary={`${filteredCrawlJobCandidates.length} 位 · 已选 ${selectedReviewableCrawlCandidateIds.length} 位`}
                focusTargetRef={crawlCandidateFirstItemRef}
                menuPlacement="popover"
                className="mt-3 border-t border-stone-100 pt-3"
              />
            ) : null}
          </section>
        </div>
      </section>
    </div>
  ) : null;
}
