import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { Pagination } from "@/components/molecules/Pagination";
import { TokenUsageBreakdown } from "@/features/task-center/components/TaskCenterCards";
import {
  DETAIL_PAGE_SIZE_OPTIONS,
  formatDisplayTime,
} from "@/features/task-center/model/taskCenterConfig";
import {
  MATCH_ANALYSIS_ITEM_STATUS_LABELS,
  MATCH_ANALYSIS_ITEM_STATUS_TONES,
} from "@/features/task-center/model/taskCenterStatus";
import type { PaginationChange } from "@/lib/pagination";
import type { DismissableLayerClickHandlers } from "@/lib/useDismissableLayerClick";
import {
  type MatchAnalysisJobDTO,
  type MatchAnalysisJobItemDTO,
  type MatchAnalysisJobItemStatus,
} from "@/types";
import { Loader2, Sparkles, X } from "lucide-react";
import type { RefObject } from "react";

type Props = {
  selectedMatchJob: MatchAnalysisJobDTO | null;
  matchJobDetailsLayer: DismissableLayerClickHandlers;
  closeMatchJobDetails: () => void;
  matchJobItemsStartRef: RefObject<HTMLElement | null>;
  matchJobDetailsLoading: boolean;
  matchJobItemStatusFilter: MatchAnalysisJobItemStatus | "all";
  setMatchJobItemStatusFilter: (
    status: MatchAnalysisJobItemStatus | "all",
  ) => void;
  matchJobItemTotalCount: number;
  selectedMatchJobItems: MatchAnalysisJobItemDTO[];
  matchJobItemPage: number;
  matchJobItemPageSize: number;
  handleMatchJobItemPaginationChange: (change: PaginationChange) => void;
};

export function MatchJobDetailsDialog({
  selectedMatchJob,
  matchJobDetailsLayer,
  closeMatchJobDetails,
  matchJobItemsStartRef,
  matchJobDetailsLoading,
  matchJobItemStatusFilter,
  setMatchJobItemStatusFilter,
  matchJobItemTotalCount,
  selectedMatchJobItems,
  matchJobItemPage,
  matchJobItemPageSize,
  handleMatchJobItemPaginationChange,
}: Props) {
  return selectedMatchJob ? (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-end bg-stone-950/30 p-0 sm:p-6"
      onClick={matchJobDetailsLayer.onBackdropClick}
      onMouseDown={matchJobDetailsLayer.onBackdropMouseDown}
    >
      <section
        role="dialog"
        aria-label="匹配分析任务详情"
        className="flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-4xl sm:rounded-3xl"
        onClick={matchJobDetailsLayer.onContentClick}
        onMouseDown={matchJobDetailsLayer.onContentMouseDown}
      >
        <div className="flex items-start justify-between gap-4 border-b border-stone-200 bg-[#fcfbf8] px-6 py-5">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
              <Sparkles className="h-4 w-4 text-primary" />
              匹配分析任务
            </div>
            <h2 className="mt-2 text-xl font-semibold text-stone-900">
              {selectedMatchJob.name}
            </h2>
            <p className="mt-2 text-sm text-stone-500">
              创建于 {formatDisplayTime(selectedMatchJob.created_at)}
            </p>
          </div>
          <button
            type="button"
            onClick={closeMatchJobDetails}
            className="ui-btn-secondary shrink-0"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
            关闭
          </button>
        </div>

        <div
          data-testid="match-job-detail-scroll"
          className="flex-1 overflow-y-auto overscroll-contain px-6 py-5"
        >
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">成功</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedMatchJob.succeeded_count}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">失败</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedMatchJob.failed_count}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">跳过</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedMatchJob.skipped_count}
              </div>
            </div>
          </div>

          <TokenUsageBreakdown
            inputTokens={selectedMatchJob.total_prompt_tokens}
            outputTokens={selectedMatchJob.total_completion_tokens}
            cachedTokens={selectedMatchJob.total_cached_tokens}
            totalTokens={selectedMatchJob.total_tokens}
            ariaLabel="匹配分析任务 Token 使用汇总"
            variant="metrics"
            className="mt-3"
          />

          <section
            ref={matchJobItemsStartRef}
            tabIndex={-1}
            aria-label="匹配分析导师明细"
            className="mt-6 scroll-mt-6 focus:outline-none"
          >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <h3 className="text-sm font-semibold text-stone-900">导师明细</h3>
              <div className="flex flex-wrap items-center gap-2">
                {matchJobDetailsLoading ? (
                  <span className="inline-flex items-center gap-2 text-xs text-stone-500">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    正在刷新
                  </span>
                ) : null}
                <span className="text-xs text-stone-500">状态</span>
                <NativeSelectField
                  ariaLabel="筛选匹配分析导师状态"
                  value={matchJobItemStatusFilter}
                  onChange={(event) => {
                    setMatchJobItemStatusFilter(
                      event.target.value as MatchAnalysisJobItemStatus | "all",
                    );
                  }}
                  wrapperClassName="w-32"
                  shellClassName="!min-h-0 h-9 rounded-2xl px-3 py-0 shadow-none"
                >
                  <option value="all">全部状态</option>
                  {Object.entries(MATCH_ANALYSIS_ITEM_STATUS_LABELS).map(
                    ([status, label]) => (
                      <option key={status} value={status}>
                        {label}
                      </option>
                    ),
                  )}
                </NativeSelectField>
                <span className="text-xs tabular-nums text-stone-500">
                  {matchJobItemTotalCount} /{" "}
                  {selectedMatchJob.target_count +
                    selectedMatchJob.skipped_count}{" "}
                  位
                </span>
              </div>
            </div>

            <div className="mt-3 overflow-x-auto rounded-2xl border border-stone-200">
              <table className="w-full min-w-max table-auto divide-y divide-stone-200 text-sm">
                <thead className="bg-stone-50 text-center text-xs font-medium text-stone-500">
                  <tr>
                    <th className="px-4 py-3 align-middle">导师</th>
                    <th className="px-4 py-3 align-middle">状态</th>
                    <th className="px-4 py-3 align-middle">匹配分</th>
                    <th className="px-4 py-3 align-middle">说明</th>
                    <th className="px-3 py-3 align-middle">Token 明细</th>
                    <th className="px-4 py-3 align-middle">更新时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-100 bg-white text-stone-700">
                  {selectedMatchJobItems.length > 0 ? (
                    selectedMatchJobItems.map((item) => {
                      const professorDetails = [
                        item.professor_title,
                        item.professor_university,
                        item.professor_school,
                      ]
                        .filter(Boolean)
                        .join(" / ");

                      return (
                        <tr key={item.id}>
                          <td className="px-4 py-3 align-middle">
                            <div className="max-w-56 break-words font-medium text-stone-900">
                              {item.professor_name}
                            </div>
                            {professorDetails ? (
                              <div className="mt-1 max-w-56 break-words text-xs text-stone-500">
                                {professorDetails}
                              </div>
                            ) : null}
                          </td>
                          <td className="px-4 py-3 text-center align-middle">
                            <span
                              className={`whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-medium ${MATCH_ANALYSIS_ITEM_STATUS_TONES[item.status]}`}
                            >
                              {MATCH_ANALYSIS_ITEM_STATUS_LABELS[item.status]}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-center align-middle tabular-nums">
                            {item.match_score ?? "未生成"}
                          </td>
                          <td className="px-4 py-3 text-center align-middle">
                            <div className="max-w-[22rem] break-words">
                              {item.error_message ||
                                item.skip_reason ||
                                "已完成"}
                            </div>
                          </td>
                          <td className="px-3 py-3 text-center align-middle">
                            <TokenUsageBreakdown
                              inputTokens={item.prompt_tokens}
                              outputTokens={item.completion_tokens}
                              cachedTokens={item.cached_tokens}
                              totalTokens={item.total_tokens}
                              ariaLabel={`${item.professor_name} Token 使用明细`}
                              compactLayout="tight"
                              className="text-left"
                            />
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-center align-middle tabular-nums">
                            {formatDisplayTime(item.updated_at, {
                              withSeconds: true,
                            })}
                          </td>
                        </tr>
                      );
                    })
                  ) : (
                    <tr>
                      <td
                        colSpan={6}
                        className="px-4 py-6 text-center text-sm text-stone-500"
                      >
                        {selectedMatchJobItems.length > 0
                          ? "当前状态下暂无导师。"
                          : "暂无任务明细。"}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <Pagination
              page={matchJobItemPage}
              pageSize={matchJobItemPageSize}
              totalCount={matchJobItemTotalCount}
              onChange={handleMatchJobItemPaginationChange}
              ariaLabel="匹配分析导师明细分页"
              pageSizeAriaLabel="匹配分析导师明细每页数量"
              variant="compact"
              pageSizeOptions={DETAIL_PAGE_SIZE_OPTIONS}
              unitLabel="位"
              itemLabel="位导师"
              focusTargetRef={matchJobItemsStartRef}
              menuPlacement="popover"
              className="mt-3 border-t border-stone-100 pt-3"
            />
          </section>
        </div>
      </section>
    </div>
  ) : null;
}
