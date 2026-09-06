import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { Pagination } from "@/components/molecules/Pagination";
import { TokenUsageBreakdown } from "@/features/task-center/components/TaskCenterCards";
import {
  DETAIL_PAGE_SIZE_OPTIONS,
  formatDisplayTime,
  formatDuration,
} from "@/features/task-center/model/taskCenterConfig";
import {
  INFORMATION_ENRICHMENT_FIELD_LABELS,
  INFORMATION_ENRICHMENT_ITEM_STATUS_LABELS,
  INFORMATION_ENRICHMENT_ITEM_STATUS_TONES,
  INFORMATION_ENRICHMENT_JOB_STATUS_TONES,
} from "@/features/task-center/model/taskCenterStatus";
import type { PaginationChange } from "@/lib/pagination";
import type { DismissableLayerClickHandlers } from "@/lib/useDismissableLayerClick";
import {
  PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS,
  type ProfessorInformationEnrichmentItemDTO,
  type ProfessorInformationEnrichmentItemStatus,
  type ProfessorInformationEnrichmentJobDTO,
} from "@/types";
import { Bot, Loader2, X } from "lucide-react";
import type { JSX, RefObject } from "react";

type Props = {
  selectedInformationEnrichmentJob: ProfessorInformationEnrichmentJobDTO | null;
  informationEnrichmentDetailsLayer: DismissableLayerClickHandlers;
  closeInformationEnrichmentDetails: () => void;
  informationEnrichmentItemsStartRef: RefObject<HTMLElement | null>;
  informationEnrichmentDetailsLoading: boolean;
  informationEnrichmentItemStatusFilter:
    | "all"
    | ProfessorInformationEnrichmentItemStatus;
  setInformationEnrichmentItemStatusFilter: (
    status: ProfessorInformationEnrichmentItemStatus | "all",
  ) => void;
  informationEnrichmentItemTotalCount: number;
  selectedInformationEnrichmentItems: ProfessorInformationEnrichmentItemDTO[];
  renderCandidateExternalUrl: (url: string | null) => JSX.Element | "暂无";
  informationEnrichmentItemPage: number;
  informationEnrichmentItemPageSize: number;
  handleInformationEnrichmentItemPaginationChange: (
    change: PaginationChange,
  ) => void;
};

export function EnrichmentJobDetailsDialog({
  selectedInformationEnrichmentJob,
  informationEnrichmentDetailsLayer,
  closeInformationEnrichmentDetails,
  informationEnrichmentItemsStartRef,
  informationEnrichmentDetailsLoading,
  informationEnrichmentItemStatusFilter,
  setInformationEnrichmentItemStatusFilter,
  informationEnrichmentItemTotalCount,
  selectedInformationEnrichmentItems,
  renderCandidateExternalUrl,
  informationEnrichmentItemPage,
  informationEnrichmentItemPageSize,
  handleInformationEnrichmentItemPaginationChange,
}: Props) {
  return selectedInformationEnrichmentJob ? (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-end bg-stone-950/30 p-0 sm:p-6"
      onClick={informationEnrichmentDetailsLayer.onBackdropClick}
      onMouseDown={informationEnrichmentDetailsLayer.onBackdropMouseDown}
    >
      <section
        role="dialog"
        aria-label="信息补全任务详情"
        className="flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-5xl sm:rounded-3xl"
        onClick={informationEnrichmentDetailsLayer.onContentClick}
        onMouseDown={informationEnrichmentDetailsLayer.onContentMouseDown}
      >
        <div className="flex items-start justify-between gap-4 border-b border-stone-200 bg-[#fcfbf8] px-4 py-5 sm:px-6">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
              <Bot className="h-4 w-4 text-primary" />
              信息补全任务
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <h2 className="min-w-0 break-words text-xl font-semibold text-stone-900">
                {selectedInformationEnrichmentJob.name}
              </h2>
              <span
                className={`shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium ${INFORMATION_ENRICHMENT_JOB_STATUS_TONES[selectedInformationEnrichmentJob.status]}`}
              >
                {
                  PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS[
                    selectedInformationEnrichmentJob.status
                  ]
                }
              </span>
            </div>
            <p className="mt-2 text-sm text-stone-500">
              创建于{" "}
              {formatDisplayTime(selectedInformationEnrichmentJob.created_at)}
            </p>
          </div>
          <button
            type="button"
            onClick={closeInformationEnrichmentDetails}
            className="ui-btn-secondary shrink-0"
            aria-label="关闭信息补全任务详情"
          >
            <X className="h-4 w-4" />
            关闭
          </button>
        </div>

        <div
          data-testid="information-enrichment-detail-scroll"
          className="flex-1 overflow-y-auto overscroll-contain px-4 py-5 sm:px-6"
        >
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">成功</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedInformationEnrichmentJob.succeeded_count}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">失败</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedInformationEnrichmentJob.failed_count}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">跳过</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedInformationEnrichmentJob.skipped_count}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">取消</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {selectedInformationEnrichmentJob.canceled_count}
              </div>
            </div>
            <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
              <div className="text-xs font-medium text-stone-500">耗时</div>
              <div className="mt-2 text-sm font-semibold text-stone-900">
                {formatDuration(
                  selectedInformationEnrichmentJob.duration_seconds,
                )}
              </div>
            </div>
          </div>

          <TokenUsageBreakdown
            inputTokens={selectedInformationEnrichmentJob.input_tokens}
            outputTokens={selectedInformationEnrichmentJob.output_tokens}
            cachedTokens={selectedInformationEnrichmentJob.cached_tokens}
            totalTokens={selectedInformationEnrichmentJob.total_tokens}
            ariaLabel="信息补全任务 Token 使用汇总"
            variant="metrics"
            className="mt-3"
          />

          {selectedInformationEnrichmentJob.last_error ? (
            <div className="mt-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3">
              <div className="text-xs font-medium text-red-700">最近错误</div>
              <div className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-red-900">
                {selectedInformationEnrichmentJob.last_error}
              </div>
            </div>
          ) : null}

          <section
            ref={informationEnrichmentItemsStartRef}
            tabIndex={-1}
            aria-label="信息补全导师明细"
            className="mt-6 scroll-mt-6 focus:outline-none"
          >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <h3 className="text-sm font-semibold text-stone-900">导师明细</h3>
              <div className="flex flex-wrap items-center gap-2">
                {informationEnrichmentDetailsLoading ? (
                  <span className="inline-flex items-center gap-2 text-xs text-stone-500">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    正在刷新
                  </span>
                ) : null}
                <span className="text-xs text-stone-500">状态</span>
                <NativeSelectField
                  ariaLabel="筛选信息补全导师状态"
                  value={informationEnrichmentItemStatusFilter}
                  onChange={(event) => {
                    setInformationEnrichmentItemStatusFilter(
                      event.target.value as
                        | ProfessorInformationEnrichmentItemStatus
                        | "all",
                    );
                  }}
                  wrapperClassName="w-32"
                  shellClassName="!min-h-0 h-9 rounded-2xl px-3 py-0 shadow-none"
                >
                  <option value="all">全部状态</option>
                  {Object.entries(
                    INFORMATION_ENRICHMENT_ITEM_STATUS_LABELS,
                  ).map(([status, label]) => (
                    <option key={status} value={status}>
                      {label}
                    </option>
                  ))}
                </NativeSelectField>
                <span className="text-xs tabular-nums text-stone-500">
                  {informationEnrichmentItemTotalCount} /{" "}
                  {selectedInformationEnrichmentJob.target_count} 位
                </span>
              </div>
            </div>

            <div className="mt-3 overflow-x-auto rounded-2xl border border-stone-200">
              <table className="w-full min-w-max table-auto divide-y divide-stone-200 text-sm">
                <thead className="bg-stone-50 text-center text-xs font-medium text-stone-500">
                  <tr>
                    <th className="px-4 py-3 align-middle">导师</th>
                    <th className="px-4 py-3 align-middle">状态</th>
                    <th className="px-4 py-3 align-middle">补全字段</th>
                    <th className="px-4 py-3 align-middle">说明</th>
                    <th className="px-3 py-3 align-middle">
                      Token 明细 / 尝试
                    </th>
                    <th className="px-4 py-3 align-middle">主页 / 完成时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-100 bg-white text-stone-700">
                  {selectedInformationEnrichmentItems.length > 0 ? (
                    selectedInformationEnrichmentItems.map((item) => {
                      const itemMessage =
                        item.error_message ||
                        item.skip_reason ||
                        (item.status === "succeeded"
                          ? item.enriched_fields.length > 0
                            ? "补全完成"
                            : "未发现可写入的新信息"
                          : "等待处理");

                      return (
                        <tr key={item.id}>
                          <td className="px-4 py-3 align-middle">
                            <div className="max-w-64 break-words font-medium text-stone-900">
                              {item.professor_name}
                            </div>
                            <div className="mt-1 max-w-64 break-words text-xs leading-5 text-stone-500">
                              {item.professor_email || "暂无邮箱"}
                            </div>
                            <div className="max-w-64 break-words text-xs leading-5 text-stone-500">
                              {[
                                item.professor_title,
                                item.professor_school,
                                item.professor_department,
                              ]
                                .filter(Boolean)
                                .join(" / ") || "暂无补充信息"}
                            </div>
                          </td>
                          <td className="px-4 py-3 text-center align-middle">
                            <span
                              className={`whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-medium ${INFORMATION_ENRICHMENT_ITEM_STATUS_TONES[item.status]}`}
                            >
                              {
                                INFORMATION_ENRICHMENT_ITEM_STATUS_LABELS[
                                  item.status
                                ]
                              }
                            </span>
                          </td>
                          <td className="px-4 py-3 text-center align-middle">
                            {item.enriched_fields.length > 0 ? (
                              <div className="mx-auto flex max-w-48 flex-wrap justify-center gap-1.5">
                                {item.enriched_fields.map((field) => (
                                  <span
                                    key={field}
                                    className="rounded-full bg-emerald-50 px-2 py-1 text-xs text-emerald-700"
                                  >
                                    {INFORMATION_ENRICHMENT_FIELD_LABELS[
                                      field
                                    ] ?? field}
                                  </span>
                                ))}
                              </div>
                            ) : (
                              <span className="text-stone-400">--</span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-center align-middle">
                            <div
                              className={`mx-auto max-w-[22rem] whitespace-pre-wrap break-words leading-6 ${
                                item.error_message
                                  ? "text-red-700"
                                  : "text-stone-700"
                              }`}
                            >
                              {itemMessage}
                            </div>
                          </td>
                          <td className="px-3 py-3 text-center align-middle">
                            <TokenUsageBreakdown
                              inputTokens={item.input_tokens}
                              outputTokens={item.output_tokens}
                              cachedTokens={item.cached_tokens}
                              totalTokens={item.total_tokens}
                              ariaLabel={`${item.professor_name} Token 使用明细`}
                              compactLayout="tight"
                              className="text-left"
                            />
                            <div className="mt-1 text-xs text-stone-500">
                              尝试 {item.attempt_count} 次
                            </div>
                          </td>
                          <td className="px-4 py-3 text-center align-middle">
                            <div className="mx-auto max-w-56 truncate">
                              {renderCandidateExternalUrl(item.profile_url)}
                            </div>
                            <div className="mt-2 text-xs text-stone-500">
                              {formatDisplayTime(item.finished_at, {
                                withSeconds: true,
                              })}
                            </div>
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
                        {selectedInformationEnrichmentJob.target_count > 0
                          ? "当前状态下暂无导师。"
                          : "暂无任务明细。"}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <Pagination
              page={informationEnrichmentItemPage}
              pageSize={informationEnrichmentItemPageSize}
              totalCount={informationEnrichmentItemTotalCount}
              onChange={handleInformationEnrichmentItemPaginationChange}
              ariaLabel="信息补全导师明细分页"
              pageSizeAriaLabel="信息补全导师明细每页数量"
              variant="compact"
              pageSizeOptions={DETAIL_PAGE_SIZE_OPTIONS}
              unitLabel="位"
              itemLabel="位导师"
              focusTargetRef={informationEnrichmentItemsStartRef}
              menuPlacement="popover"
              className="mt-3 border-t border-stone-100 pt-3"
            />
          </section>
        </div>
      </section>
    </div>
  ) : null;
}
