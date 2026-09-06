import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { AttachmentSizeSummary } from "@/components/molecules/AttachmentSizeSummary";
import { EmailDeliveryFailureDetails } from "@/components/molecules/EmailDeliveryFailureDetails";
import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";
import { Pagination } from "@/components/molecules/Pagination";
import { SelectionToggleButton } from "@/components/molecules/SelectionToggleButton";
import { SubjectTemplateInput } from "@/components/molecules/SubjectTemplateInput";
import { formatFileSize } from "@/features/attachments/attachmentSize";
import {
  getBatchTaskItemCancellationText,
  getOutreachGenerationModeLabel,
  getOutreachTemplateSourceLabel,
  isBatchTaskItemMissingResearchDirection,
} from "@/features/batch-tasks/client/batchTaskDisplay";
import {
  buildScheduleLabel,
  DETAIL_PAGE_SIZE_OPTIONS,
  formatDisplayTime,
  isBatchItemScheduledInFuture,
  type RichEmailValue,
} from "@/features/task-center/model/taskCenterConfig";
import { canOpenBatchResend } from "@/features/task-center/model/taskCenterJobs";
import { BATCH_ITEM_STATUS_TONES } from "@/features/task-center/model/taskCenterStatus";
import type { PaginationChange } from "@/lib/pagination";
import type { DismissableLayerClickHandlers } from "@/lib/useDismissableLayerClick";
import type { TaskListView } from "@/types";
import {
  BATCH_TASK_STATUS_LABELS,
  MATERIAL_TYPE_LABELS,
  PROFESSOR_STATUS_LABELS,
  type BatchTaskCardDTO,
  type BatchTaskItemDTO,
  type OutreachTemplateDTO,
  type WorkspaceThreadDTO,
} from "@/types";
import {
  Ban,
  CheckCircle2,
  ChevronLeft,
  FileText,
  Loader2,
  Mail,
  RotateCcw,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import type { Dispatch, JSX, RefObject, SetStateAction } from "react";
import { Link } from "react-router-dom";
import type {
  BatchReviewItemActions,
  BatchReviewItemActionType,
} from "../model/batchReview";

type Props = {
  selectedBatchTask: BatchTaskCardDTO | null;
  batchTaskDetailsLayer: DismissableLayerClickHandlers;
  batchDraftReviewOpen: boolean;
  activeBatchReviewItem: BatchTaskItemDTO | null;
  activeTaskListView: TaskListView;
  handleOpenBatchResend: (task: BatchTaskCardDTO) => Promise<void>;
  handleReturnToBatchTaskDetails: () => Promise<void>;
  batchReviewSaving: boolean;
  requestCloseBatchTaskDetails: () => Promise<void>;
  batchReviewQueueItems: BatchTaskItemDTO[];
  batchReviewLoading: boolean;
  batchReviewThread: WorkspaceThreadDTO | null;
  batchReviewQueueScrollRef: RefObject<HTMLDivElement | null>;
  visibleBatchReviewQueueItems: BatchTaskItemDTO[];
  batchReviewItemActions: BatchReviewItemActions;
  batchReviewItemId: number | null;
  openBatchDraftReview: (item: BatchTaskItemDTO) => Promise<void>;
  handleDeleteBatchDraftItem: (item: BatchTaskItemDTO) => Promise<void>;
  safeBatchReviewItemPage: number;
  batchReviewItemPageSize: number;
  handleBatchReviewItemPaginationChange: (change: PaginationChange) => void;
  batchReviewUsesTemplateFallback: boolean;
  batchReviewSourceTemplateLabel: string;
  batchReviewProfessorMissingResearchDirection: boolean;
  batchReviewTemplateReferencesResearchDirection: boolean;
  openProfessorEditDialog: (item: BatchTaskItemDTO) => Promise<void>;
  batchReviewDraftSourceLabel: string;
  activeBatchReviewAction: BatchReviewItemActionType | null;
  loadingBatchReviewOutreachTemplates: boolean;
  batchReviewOutreachTemplatesLoaded: boolean;
  activeBatchReviewOutreachTemplates: OutreachTemplateDTO[];
  handleApplyBatchReviewOutreachTemplate: (templateId: number) => Promise<void>;
  selectedBatchReviewOutreachTemplateId: number | null;
  batchReviewSubject: string;
  setBatchReviewSubject: Dispatch<SetStateAction<string>>;
  batchReviewEditorHtml: string;
  handleBatchReviewContentChange: (value: RichEmailValue) => void;
  batchReviewSelectedMaterialIds: number[];
  setBatchReviewSelectedMaterialIds: Dispatch<SetStateAction<number[]>>;
  batchReviewAttachmentTotalBytes: number;
  handleRegenerateBatchDraft: () => Promise<void>;
  batchReviewUsesTemplateDraft: boolean;
  handleApproveBatchDraft: () => Promise<void>;
  batchReviewCanSubmit: boolean;
  canSendBatchReviewImmediately: boolean;
  handleSendBatchDraftNow: () => Promise<void>;
  renderCandidateExternalUrl: (url: string | null) => JSX.Element | "暂无";
  batchTaskDetailsLoading: boolean;
  sentBatchTaskItems: BatchTaskItemDTO[];
  selectedBatchWaitingSendCount: number;
  selectedBatchNeedsManualItems: BatchTaskItemDTO[];
  failedBatchTaskItems: BatchTaskItemDTO[];
  batchSentItemsStartRef: RefObject<HTMLElement | null>;
  visibleSentBatchTaskItems: BatchTaskItemDTO[];
  safeBatchSentItemPage: number;
  batchSentItemPageSize: number;
  handleBatchSentItemPaginationChange: (change: PaginationChange) => void;
  batchPendingItemsStartRef: RefObject<HTMLElement | null>;
  reviewRequiredBatchTaskItems: BatchTaskItemDTO[];
  templateFallbackReviewCount: number;
  handleApproveAllBatchDrafts: () => Promise<void>;
  batchBulkApprovalLoading: boolean;
  pendingBatchTaskItems: BatchTaskItemDTO[];
  visiblePendingBatchTaskItems: BatchTaskItemDTO[];
  batchSendActionNowMs: number;
  renderBatchTaskItemReviewButton: (
    item: BatchTaskItemDTO,
  ) => JSX.Element | null;
  renderBatchItemSendButton: (item: BatchTaskItemDTO) => JSX.Element | null;
  renderBatchTaskItemAction: (item: BatchTaskItemDTO) => JSX.Element | null;
  safeBatchPendingItemPage: number;
  batchPendingItemPageSize: number;
  handleBatchPendingItemPaginationChange: (change: PaginationChange) => void;
  generatingDraftBatchTaskItems: BatchTaskItemDTO[];
  visibleGeneratingDraftBatchTaskItems: BatchTaskItemDTO[];
  safeBatchGeneratingItemPage: number;
  batchGeneratingItemPageSize: number;
  handleBatchGeneratingItemPaginationChange: (change: PaginationChange) => void;
  draftFailedBatchTaskItems: BatchTaskItemDTO[];
  visibleDraftFailedBatchTaskItems: BatchTaskItemDTO[];
  safeBatchDraftFailedItemPage: number;
  batchDraftFailedItemPageSize: number;
  handleBatchDraftFailedItemPaginationChange: (
    change: PaginationChange,
  ) => void;
  visibleFailedBatchTaskItems: BatchTaskItemDTO[];
  safeBatchFailedItemPage: number;
  batchFailedItemPageSize: number;
  handleBatchFailedItemPaginationChange: (change: PaginationChange) => void;
};

export function BatchTaskDetailsDialog({
  selectedBatchTask,
  batchTaskDetailsLayer,
  batchDraftReviewOpen,
  activeBatchReviewItem,
  activeTaskListView,
  handleOpenBatchResend,
  handleReturnToBatchTaskDetails,
  batchReviewSaving,
  requestCloseBatchTaskDetails,
  batchReviewQueueItems,
  batchReviewLoading,
  batchReviewThread,
  batchReviewQueueScrollRef,
  visibleBatchReviewQueueItems,
  batchReviewItemActions,
  batchReviewItemId,
  openBatchDraftReview,
  handleDeleteBatchDraftItem,
  safeBatchReviewItemPage,
  batchReviewItemPageSize,
  handleBatchReviewItemPaginationChange,
  batchReviewUsesTemplateFallback,
  batchReviewSourceTemplateLabel,
  batchReviewProfessorMissingResearchDirection,
  batchReviewTemplateReferencesResearchDirection,
  openProfessorEditDialog,
  batchReviewDraftSourceLabel,
  activeBatchReviewAction,
  loadingBatchReviewOutreachTemplates,
  batchReviewOutreachTemplatesLoaded,
  activeBatchReviewOutreachTemplates,
  handleApplyBatchReviewOutreachTemplate,
  selectedBatchReviewOutreachTemplateId,
  batchReviewSubject,
  setBatchReviewSubject,
  batchReviewEditorHtml,
  handleBatchReviewContentChange,
  batchReviewSelectedMaterialIds,
  setBatchReviewSelectedMaterialIds,
  batchReviewAttachmentTotalBytes,
  handleRegenerateBatchDraft,
  batchReviewUsesTemplateDraft,
  handleApproveBatchDraft,
  batchReviewCanSubmit,
  canSendBatchReviewImmediately,
  handleSendBatchDraftNow,
  renderCandidateExternalUrl,
  batchTaskDetailsLoading,
  sentBatchTaskItems,
  selectedBatchWaitingSendCount,
  selectedBatchNeedsManualItems,
  failedBatchTaskItems,
  batchSentItemsStartRef,
  visibleSentBatchTaskItems,
  safeBatchSentItemPage,
  batchSentItemPageSize,
  handleBatchSentItemPaginationChange,
  batchPendingItemsStartRef,
  reviewRequiredBatchTaskItems,
  templateFallbackReviewCount,
  handleApproveAllBatchDrafts,
  batchBulkApprovalLoading,
  pendingBatchTaskItems,
  visiblePendingBatchTaskItems,
  batchSendActionNowMs,
  renderBatchTaskItemReviewButton,
  renderBatchItemSendButton,
  renderBatchTaskItemAction,
  safeBatchPendingItemPage,
  batchPendingItemPageSize,
  handleBatchPendingItemPaginationChange,
  generatingDraftBatchTaskItems,
  visibleGeneratingDraftBatchTaskItems,
  safeBatchGeneratingItemPage,
  batchGeneratingItemPageSize,
  handleBatchGeneratingItemPaginationChange,
  draftFailedBatchTaskItems,
  visibleDraftFailedBatchTaskItems,
  safeBatchDraftFailedItemPage,
  batchDraftFailedItemPageSize,
  handleBatchDraftFailedItemPaginationChange,
  visibleFailedBatchTaskItems,
  safeBatchFailedItemPage,
  batchFailedItemPageSize,
  handleBatchFailedItemPaginationChange,
}: Props) {
  return selectedBatchTask ? (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-end bg-stone-950/30 p-0 sm:p-6"
      onClick={batchTaskDetailsLayer.onBackdropClick}
      onMouseDown={batchTaskDetailsLayer.onBackdropMouseDown}
    >
      <section
        role="dialog"
        aria-label="批量任务详情"
        className={
          batchDraftReviewOpen
            ? "flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-7xl sm:rounded-3xl"
            : "flex h-full w-full flex-col overflow-hidden bg-white shadow-xl sm:max-w-4xl sm:rounded-3xl"
        }
        onClick={batchTaskDetailsLayer.onContentClick}
        onMouseDown={batchTaskDetailsLayer.onContentMouseDown}
      >
        <div className="flex flex-col gap-4 border-b border-stone-200 bg-[#fcfbf8] px-4 py-4 sm:flex-row sm:items-start sm:justify-between sm:px-6 sm:py-5">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
              <Mail className="h-4 w-4 text-primary" />
              {batchDraftReviewOpen ? "批量草稿审核" : "批量邮件任务"}
            </div>
            <h2 className="mt-2 break-words text-xl font-semibold text-stone-900">
              {batchDraftReviewOpen ? "批量审核草稿" : selectedBatchTask.name}
            </h2>
            <p className="mt-2 text-sm text-stone-500">
              {batchDraftReviewOpen
                ? `${selectedBatchTask.name} · ${activeBatchReviewItem?.professor_name ?? "正在加载"}`
                : buildScheduleLabel(selectedBatchTask)}
            </p>
          </div>
          <div className="flex w-full flex-wrap gap-2 sm:w-auto sm:shrink-0 sm:justify-end">
            {!batchDraftReviewOpen &&
            canOpenBatchResend(selectedBatchTask, activeTaskListView) ? (
              <button
                type="button"
                onClick={() => void handleOpenBatchResend(selectedBatchTask)}
                className="ui-btn-primary"
              >
                <RotateCcw className="h-4 w-4" />
                重新发起未成功项
              </button>
            ) : null}
            {batchDraftReviewOpen ? (
              <button
                type="button"
                onClick={() => void handleReturnToBatchTaskDetails()}
                disabled={batchReviewSaving}
                className="ui-btn-secondary disabled:cursor-wait disabled:opacity-60"
              >
                {batchReviewSaving ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ChevronLeft className="h-4 w-4" />
                )}
                {batchReviewSaving ? "正在保存" : "返回详情"}
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => void requestCloseBatchTaskDetails()}
              disabled={batchReviewSaving}
              className="ui-btn-secondary disabled:cursor-wait disabled:opacity-60"
              aria-label="关闭"
            >
              <X className="h-4 w-4" />
              关闭
            </button>
          </div>
        </div>

        <div
          data-testid="batch-task-detail-scroll"
          className="flex-1 overflow-y-auto overscroll-contain px-6 py-5"
        >
          {batchDraftReviewOpen ? (
            <div className="grid min-h-full gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
              <aside className="rounded-3xl border border-stone-200 bg-stone-50/70 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-stone-900">
                      待审核队列
                    </h3>
                    <p className="mt-1 text-xs text-stone-500">
                      {batchReviewQueueItems.length} 封草稿等待处理
                    </p>
                  </div>
                  {batchReviewSaving ? (
                    <span
                      role="status"
                      className="inline-flex items-center gap-1.5 text-xs text-stone-500"
                    >
                      <Loader2 className="h-4 w-4 animate-spin" />
                      保存中
                    </span>
                  ) : batchReviewLoading && !batchReviewThread ? (
                    <Loader2 className="h-4 w-4 animate-spin text-stone-400" />
                  ) : null}
                </div>
                <div
                  ref={batchReviewQueueScrollRef}
                  role="list"
                  aria-label="待审核草稿列表"
                  className="mt-4 max-h-[min(50rem,calc(100dvh-16rem))] space-y-2 overflow-y-auto overscroll-contain pr-1 [scrollbar-gutter:stable]"
                >
                  {visibleBatchReviewQueueItems.map((item) => {
                    const itemGeneratingDraft =
                      item.status === "generating_draft";
                    const itemAction = batchReviewItemActions[item.id] ?? null;
                    const itemDeleting = itemAction === "delete";
                    const itemRegenerating = itemAction === "regenerate";
                    const itemBusyGenerating =
                      itemGeneratingDraft || itemRegenerating;
                    return (
                      <div
                        key={item.id}
                        role="listitem"
                        className={
                          item.id === batchReviewItemId
                            ? "flex w-full items-stretch overflow-hidden rounded-2xl border border-primary/25 bg-white shadow-sm"
                            : "flex w-full items-stretch overflow-hidden rounded-2xl border border-stone-200 bg-white/70 transition hover:border-primary/20 hover:bg-white"
                        }
                      >
                        <button
                          type="button"
                          onClick={() => void openBatchDraftReview(item)}
                          disabled={itemBusyGenerating || batchReviewSaving}
                          className="min-w-0 flex-1 px-4 py-3 text-left disabled:cursor-wait"
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="truncate text-sm font-semibold text-stone-900">
                                  {item.professor_name}
                                </span>
                                {itemBusyGenerating ? (
                                  <span className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-0.5 text-xs text-sky-700">
                                    <Loader2 className="h-3 w-3 animate-spin" />
                                    重新生成中
                                  </span>
                                ) : null}
                                {item.draft_generation_source ===
                                "template_fallback" ? (
                                  <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                                    未进行 AI 改写
                                  </span>
                                ) : null}
                              </div>
                              <div className="mt-1 truncate text-xs text-stone-500">
                                {[item.professor_title, item.professor_school]
                                  .filter(Boolean)
                                  .join(" / ") || "暂无补充信息"}
                              </div>
                            </div>
                            {item.match_score !== null ? (
                              <span className="shrink-0 rounded-full bg-primary/10 px-2 py-1 text-xs text-primary">
                                {item.match_score}
                              </span>
                            ) : null}
                          </div>
                        </button>
                        <button
                          type="button"
                          aria-label="移除草稿"
                          onClick={() => void handleDeleteBatchDraftItem(item)}
                          disabled={
                            itemDeleting ||
                            itemBusyGenerating ||
                            batchReviewSaving
                          }
                          className="flex w-11 shrink-0 items-center justify-center border-l border-stone-100 text-stone-400 transition hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    );
                  })}
                </div>
                <Pagination
                  page={safeBatchReviewItemPage}
                  pageSize={batchReviewItemPageSize}
                  totalCount={batchReviewQueueItems.length}
                  onChange={handleBatchReviewItemPaginationChange}
                  ariaLabel="待审核草稿分页"
                  pageSizeAriaLabel="待审核草稿每页数量"
                  variant="compact"
                  layout="stacked"
                  pageSizeOptions={DETAIL_PAGE_SIZE_OPTIONS}
                  unitLabel="封"
                  itemLabel="封草稿"
                  className="mt-4 border-t border-stone-200 pt-3"
                />
              </aside>

              <section className="min-w-0 rounded-3xl border border-stone-200 bg-white p-5 shadow-sm">
                {batchReviewLoading && !batchReviewThread ? (
                  <div className="flex min-h-[520px] items-center justify-center gap-2 text-sm text-stone-500">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    正在加载草稿…
                  </div>
                ) : batchReviewThread ? (
                  <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_280px]">
                    <div className="min-w-0">
                      {batchReviewUsesTemplateFallback ? (
                        <section
                          aria-label="未进行 AI 改写提示"
                          className="mb-5 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900"
                        >
                          <div className="flex items-center gap-2 font-semibold">
                            <Sparkles className="h-4 w-4" />
                            未进行 AI 改写
                          </div>
                          <p className="mt-1">
                            因缺少研究方向，已直接套用
                            {`「${batchReviewSourceTemplateLabel}」`}
                            模板。
                            {batchReviewProfessorMissingResearchDirection
                              ? "可编辑后审核或先补充资料。"
                              : "资料已补充，可重新改写或直接审核。"}
                          </p>
                          {batchReviewTemplateReferencesResearchDirection ? (
                            <p className="mt-1 font-medium">
                              模板中的研究方向变量为空，请重点检查相关语句。
                            </p>
                          ) : null}
                          {batchReviewProfessorMissingResearchDirection ? (
                            <button
                              type="button"
                              onClick={() => {
                                if (activeBatchReviewItem) {
                                  void openProfessorEditDialog(
                                    activeBatchReviewItem,
                                  );
                                }
                              }}
                              disabled={!activeBatchReviewItem}
                              className="mt-2 inline-flex font-medium text-amber-900 underline underline-offset-4 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              补充资料
                            </button>
                          ) : null}
                        </section>
                      ) : null}
                      <div className="mb-5 rounded-2xl border border-primary/10 bg-primary/5 px-4 py-3">
                        <div className="text-sm font-semibold text-stone-900">
                          {batchReviewThread.professor.name}
                        </div>
                        <div className="mt-1 text-xs leading-5 text-stone-600">
                          {[
                            batchReviewThread.professor.title,
                            batchReviewThread.professor.university,
                            batchReviewThread.professor.school,
                            batchReviewThread.professor.email,
                          ]
                            .filter(Boolean)
                            .join(" / ") || "导师信息待补充"}
                        </div>
                      </div>
                      <div className="space-y-4">
                        <section
                          aria-label="模板"
                          className="rounded-2xl border border-stone-200/80 bg-stone-50/75 p-4"
                        >
                          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(16rem,0.72fr)] lg:items-center">
                            <div className="min-w-0">
                              <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                                <FileText className="h-4 w-4 text-primary" />
                                来源模板
                              </div>
                              <div className="mt-1 flex min-w-0 flex-wrap items-center gap-2">
                                <span className="min-w-0 truncate text-sm font-semibold text-stone-900">
                                  {batchReviewSourceTemplateLabel}
                                </span>
                                <span className="shrink-0 rounded-lg border border-stone-200 bg-white px-2 py-0.5 text-[11px] font-medium text-stone-600">
                                  当前草稿：{batchReviewDraftSourceLabel}
                                </span>
                              </div>
                            </div>
                            <NativeSelectField
                              value=""
                              ariaLabel="选择模板重新套用"
                              selectedLabel={
                                activeBatchReviewAction === "template"
                                  ? "正在套用模板…"
                                  : loadingBatchReviewOutreachTemplates ||
                                      !batchReviewOutreachTemplatesLoaded
                                    ? "正在加载模板库…"
                                    : activeBatchReviewOutreachTemplates.length >
                                        0
                                      ? "选择模板重新套用…"
                                      : "暂无可用模板"
                              }
                              disabled={
                                batchReviewSaving ||
                                Boolean(activeBatchReviewAction) ||
                                loadingBatchReviewOutreachTemplates ||
                                !batchReviewOutreachTemplatesLoaded ||
                                activeBatchReviewOutreachTemplates.length === 0
                              }
                              onChange={(event) => {
                                if (event.target.value) {
                                  void handleApplyBatchReviewOutreachTemplate(
                                    Number(event.target.value),
                                  );
                                }
                              }}
                            >
                              {activeBatchReviewOutreachTemplates.map(
                                (template) => (
                                  <option key={template.id} value={template.id}>
                                    {template.name}
                                    {template.id ===
                                    selectedBatchReviewOutreachTemplateId
                                      ? " · 当前来源"
                                      : ""}
                                    {template.is_default ? " · 全局默认" : ""}
                                    {template.is_ready ? "" : " · 内容待完善"}
                                  </option>
                                ),
                              )}
                            </NativeSelectField>
                          </div>
                        </section>
                        <SubjectTemplateInput
                          key={`batch-review-subject-${batchReviewThread.current_task.id}`}
                          label="邮件主题"
                          value={batchReviewSubject}
                          onChange={setBatchReviewSubject}
                          placeholder="给导师的邮件主题"
                          disabled={batchReviewSaving}
                        />
                        <EmailTemplateEditor
                          key={`batch-review-body-${batchReviewThread.current_task.id}`}
                          label="邮件正文"
                          html={batchReviewEditorHtml}
                          onChange={handleBatchReviewContentChange}
                          disabled={batchReviewSaving}
                        />
                      </div>
                    </div>

                    <aside className="space-y-4">
                      <section
                        aria-label="随信附件"
                        className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3"
                      >
                        <div className="text-xs font-medium text-stone-500">
                          随信附件
                        </div>
                        <div className="mt-3 space-y-2">
                          {batchReviewThread.material_options.length > 0 ? (
                            batchReviewThread.material_options.map(
                              (material) => {
                                const checked =
                                  batchReviewSelectedMaterialIds.includes(
                                    material.id,
                                  );
                                return (
                                  <label
                                    key={material.id}
                                    className="flex items-center gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700"
                                  >
                                    <SelectionToggleButton
                                      label={`选择附件 ${material.display_name}`}
                                      selected={checked}
                                      semantics="checkbox"
                                      size="sm"
                                      disabled={batchReviewSaving}
                                      onToggle={() =>
                                        setBatchReviewSelectedMaterialIds(
                                          (current) =>
                                            checked
                                              ? current.filter(
                                                  (id) => id !== material.id,
                                                )
                                              : [...current, material.id],
                                        )
                                      }
                                    />
                                    <span className="min-w-0">
                                      <span className="block truncate font-medium">
                                        {material.display_name}
                                      </span>
                                      <span className="mt-0.5 block text-xs text-stone-500">
                                        {
                                          MATERIAL_TYPE_LABELS[
                                            material.material_type
                                          ]
                                        }{" "}
                                        · {formatFileSize(material.size_bytes)}
                                      </span>
                                    </span>
                                  </label>
                                );
                              },
                            )
                          ) : (
                            <p className="text-sm text-stone-500">
                              暂无可发送材料。
                            </p>
                          )}
                        </div>
                        <AttachmentSizeSummary
                          selectedCount={batchReviewSelectedMaterialIds.length}
                          totalSizeBytes={batchReviewAttachmentTotalBytes}
                          className="mt-3"
                        />
                      </section>

                      <section
                        aria-label="审核操作"
                        className="rounded-2xl border border-stone-100 bg-white px-4 py-3"
                      >
                        <div className="text-xs leading-5 text-stone-500">
                          通过后进入发送队列；定时任务仍按原计划发送。
                        </div>
                        <div className="mt-4 flex flex-col gap-2">
                          <button
                            type="button"
                            onClick={() => void handleRegenerateBatchDraft()}
                            disabled={
                              batchReviewSaving ||
                              Boolean(activeBatchReviewAction) ||
                              !batchReviewThread
                            }
                            className="ui-btn-secondary justify-center disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {batchReviewUsesTemplateDraft ? (
                              <Sparkles className="h-4 w-4" />
                            ) : (
                              <RotateCcw className="h-4 w-4" />
                            )}
                            {batchReviewUsesTemplateDraft
                              ? "使用 AI 改写"
                              : "重新生成"}
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleApproveBatchDraft()}
                            disabled={
                              batchReviewSaving ||
                              Boolean(activeBatchReviewAction) ||
                              !batchReviewCanSubmit
                            }
                            className="ui-btn-primary justify-center disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            <CheckCircle2 className="h-4 w-4" />
                            审核通过
                          </button>
                          {canSendBatchReviewImmediately ? (
                            <button
                              type="button"
                              onClick={() => void handleSendBatchDraftNow()}
                              disabled={
                                batchReviewSaving ||
                                Boolean(activeBatchReviewAction) ||
                                !batchReviewCanSubmit
                              }
                              className="ui-btn-secondary justify-center disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              <Mail className="h-4 w-4" />
                              立即发送
                            </button>
                          ) : null}
                        </div>
                      </section>

                      <section
                        aria-label="导师详情"
                        className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3"
                      >
                        <div className="text-xs font-medium text-stone-500">
                          导师详情
                        </div>
                        <dl className="mt-2 space-y-1.5">
                          {[
                            {
                              label: "学校",
                              value: batchReviewThread.professor.university,
                            },
                            {
                              label: "学院",
                              value: batchReviewThread.professor.school,
                            },
                            {
                              label: "系所",
                              value: batchReviewThread.professor.department,
                            },
                            {
                              label: "研究方向",
                              value:
                                batchReviewThread.professor.research_direction,
                            },
                            {
                              label: "主页链接",
                              value: batchReviewThread.professor.profile_url,
                            },
                          ].map(({ label, value }) => {
                            const normalizedValue = value?.trim();
                            if (!normalizedValue) {
                              return null;
                            }

                            return (
                              <div
                                key={label}
                                className="grid grid-cols-[3.5rem_minmax(0,1fr)] items-start gap-2 text-xs leading-5"
                              >
                                <dt className="text-stone-500">{label}</dt>
                                <dd className="min-w-0 break-words text-stone-700">
                                  {label === "主页链接"
                                    ? renderCandidateExternalUrl(
                                        normalizedValue,
                                      )
                                    : normalizedValue}
                                </dd>
                              </div>
                            );
                          })}
                        </dl>
                      </section>

                      <section
                        aria-label="匹配摘要"
                        className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3"
                      >
                        <div className="text-xs font-medium text-stone-500">
                          匹配摘要
                        </div>
                        <div className="mt-2 text-sm font-semibold text-stone-900">
                          {batchReviewThread.current_task.match_score !== null
                            ? `匹配分 ${batchReviewThread.current_task.match_score}`
                            : "暂无匹配分"}
                        </div>
                        {batchReviewThread.current_task.match_reason ? (
                          <p className="mt-2 text-xs leading-5 text-stone-600">
                            {batchReviewThread.current_task.match_reason}
                          </p>
                        ) : null}
                      </section>
                    </aside>
                  </div>
                ) : (
                  <div className="flex min-h-[520px] items-center justify-center text-sm text-stone-500">
                    请选择一封待审核草稿。
                  </div>
                )}
              </section>
            </div>
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    当前状态
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {BATCH_TASK_STATUS_LABELS[selectedBatchTask.status]}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    目标人数
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedBatchTask.target_count}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    已完成
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedBatchTask.completed_count}
                  </div>
                </div>
              </div>
              {selectedBatchTask.status === "expired" ? (
                <p className="mt-4 rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm leading-6 text-red-800">
                  发送窗口已过期，剩余邮件已取消。可重新创建任务。
                </p>
              ) : null}

              <section className="mt-6">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="text-sm font-semibold text-stone-900">
                    导师进度
                  </h3>
                  {batchTaskDetailsLoading ? (
                    <span className="inline-flex items-center gap-2 text-xs text-stone-500">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      正在刷新
                    </span>
                  ) : null}
                </div>

                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                  <div className="rounded-2xl border border-emerald-100 bg-emerald-50 px-4 py-3">
                    <div className="text-xs font-medium text-emerald-700">
                      已发送/已回复
                    </div>
                    <div className="mt-2 text-xl font-semibold text-emerald-900">
                      {sentBatchTaskItems.length}
                    </div>
                  </div>
                  <div className="rounded-2xl border border-primary/15 bg-primary/5 px-4 py-3">
                    <div className="text-xs font-medium text-primary">
                      等待发送
                    </div>
                    <div className="mt-2 text-xl font-semibold text-stone-900">
                      {selectedBatchWaitingSendCount}
                    </div>
                  </div>
                  <div className="rounded-2xl border border-amber-100 bg-amber-50 px-4 py-3">
                    <div className="text-xs font-medium text-amber-700">
                      待审核/未处理
                    </div>
                    <div className="mt-2 text-xl font-semibold text-amber-900">
                      {selectedBatchNeedsManualItems.length}
                    </div>
                  </div>
                  <div className="rounded-2xl border border-red-100 bg-red-50 px-4 py-3">
                    <div className="text-xs font-medium text-red-700">
                      发送失败
                    </div>
                    <div className="mt-2 text-xl font-semibold text-red-900">
                      {failedBatchTaskItems.length}
                    </div>
                  </div>
                  <div className="rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3">
                    <div className="text-xs font-medium text-stone-600">
                      已取消发送
                    </div>
                    <div className="mt-2 text-xl font-semibold text-stone-900">
                      {selectedBatchTask.canceled_send_count}
                    </div>
                  </div>
                </div>
              </section>

              <section
                ref={batchSentItemsStartRef}
                tabIndex={-1}
                aria-label="已发送导师列表"
                className="mt-6 scroll-mt-6 focus:outline-none"
              >
                <h3 className="text-sm font-semibold text-stone-900">
                  已发送给
                </h3>
                <div className="mt-3 space-y-2">
                  {sentBatchTaskItems.length > 0 ? (
                    visibleSentBatchTaskItems.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-2xl border border-stone-100 px-4 py-3"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="text-sm font-medium text-stone-900">
                              {item.professor_name}
                            </p>
                            <p className="mt-1 text-xs text-stone-500">
                              {[
                                item.professor_title,
                                item.professor_school,
                                item.professor_email,
                              ]
                                .filter(Boolean)
                                .join(" / ") || "暂无补充信息"}
                            </p>
                          </div>
                          <span
                            className={`rounded-full px-2.5 py-1 text-xs ${BATCH_ITEM_STATUS_TONES[item.status]}`}
                          >
                            {PROFESSOR_STATUS_LABELS[item.status]}
                          </span>
                        </div>
                        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-stone-500">
                          <span>
                            发送时间 {formatDisplayTime(item.sent_at)}
                          </span>
                          <Link
                            to={`/workspace/${item.professor_id}`}
                            className="font-medium text-primary"
                          >
                            查看通信
                          </Link>
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-3 text-sm text-stone-500">
                      暂无已发送导师。
                    </p>
                  )}
                </div>
                <Pagination
                  page={safeBatchSentItemPage}
                  pageSize={batchSentItemPageSize}
                  totalCount={sentBatchTaskItems.length}
                  onChange={handleBatchSentItemPaginationChange}
                  ariaLabel="已发送导师分页"
                  pageSizeAriaLabel="已发送导师每页数量"
                  variant="compact"
                  pageSizeOptions={DETAIL_PAGE_SIZE_OPTIONS}
                  unitLabel="位"
                  itemLabel="位导师"
                  summary={`${(safeBatchSentItemPage - 1) * batchSentItemPageSize + 1}-${Math.min(sentBatchTaskItems.length, safeBatchSentItemPage * batchSentItemPageSize)} / ${sentBatchTaskItems.length}`}
                  focusTargetRef={batchSentItemsStartRef}
                  menuPlacement="popover"
                  className="mt-3 border-t border-stone-100 pt-3"
                />
              </section>

              <section
                ref={batchPendingItemsStartRef}
                tabIndex={-1}
                aria-label="未发送导师列表"
                className="mt-6 scroll-mt-6 focus:outline-none"
              >
                <h3 className="text-sm font-semibold text-stone-900">
                  还未发送给
                </h3>
                {selectedBatchTask.schedule_type === "scheduled" &&
                selectedBatchWaitingSendCount > 0 ? (
                  <p className="mt-2 rounded-2xl border border-primary/15 bg-primary/5 px-4 py-3 text-sm leading-6 text-stone-700">
                    已审核邮件将按批次计划自动发送。
                  </p>
                ) : null}
                {reviewRequiredBatchTaskItems.length > 0 ? (
                  <div className="mt-2 flex flex-col gap-3 rounded-2xl border border-amber-100 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800 sm:flex-row sm:items-center sm:justify-between">
                    <p>
                      {reviewRequiredBatchTaskItems.length} 封草稿待审核。
                      {templateFallbackReviewCount > 0
                        ? `其中 ${templateFallbackReviewCount} 封未进行 AI 改写。`
                        : "均已完成 AI 改写。"}
                      可逐封审核或全部通过。
                    </p>
                    <button
                      type="button"
                      onClick={() => void handleApproveAllBatchDrafts()}
                      disabled={
                        batchBulkApprovalLoading || batchTaskDetailsLoading
                      }
                      className="ui-btn-secondary shrink-0 justify-center border-amber-200 bg-white text-amber-800 hover:border-amber-300 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {batchBulkApprovalLoading ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <CheckCircle2 className="h-4 w-4" />
                      )}
                      {batchBulkApprovalLoading
                        ? `正在通过 ${reviewRequiredBatchTaskItems.length} 封…`
                        : `全部通过审核（${reviewRequiredBatchTaskItems.length} 封）`}
                    </button>
                  </div>
                ) : null}
                <div className="mt-3 space-y-2">
                  {pendingBatchTaskItems.length > 0 ? (
                    visiblePendingBatchTaskItems.map((item) => {
                      const cancellationText =
                        getBatchTaskItemCancellationText(item);
                      const sendCanceled = item.batch_send_canceled_at !== null;
                      const missingResearchDirection =
                        !sendCanceled &&
                        isBatchTaskItemMissingResearchDirection(item);
                      const restoreWindowExpired =
                        sendCanceled &&
                        !isBatchItemScheduledInFuture(
                          item,
                          batchSendActionNowMs,
                        );
                      return (
                        <div
                          key={item.id}
                          data-testid={`batch-task-item-${item.id}`}
                          className={
                            sendCanceled
                              ? "rounded-2xl border border-red-200 bg-red-50/60 px-4 py-3"
                              : "rounded-2xl border border-stone-100 px-4 py-3"
                          }
                        >
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                              <div className="flex flex-wrap items-center gap-1.5">
                                <p className="text-sm font-medium text-stone-900">
                                  {item.professor_name}
                                </p>
                                {missingResearchDirection ? (
                                  <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800">
                                    缺少研究方向
                                  </span>
                                ) : null}
                                {!sendCanceled &&
                                item.draft_generation_source ===
                                  "template_fallback" ? (
                                  <span className="inline-flex items-center rounded-full bg-orange-100 px-2 py-0.5 text-[11px] font-medium text-orange-800">
                                    未进行 AI 改写
                                  </span>
                                ) : null}
                              </div>
                              <p className="mt-1 text-xs text-stone-500">
                                {[
                                  item.professor_title,
                                  item.professor_school,
                                  item.professor_email,
                                ]
                                  .filter(Boolean)
                                  .join(" / ") || "暂无补充信息"}
                              </p>
                            </div>
                            <div className="flex flex-wrap items-center justify-end gap-2">
                              {sendCanceled ? (
                                <span className="inline-flex items-center gap-1.5 rounded-full bg-red-100 px-2.5 py-1 text-xs font-medium text-red-800">
                                  <Ban className="h-3.5 w-3.5" />
                                  已取消发送
                                </span>
                              ) : (
                                <span
                                  className={`rounded-full px-2.5 py-1 text-xs ${BATCH_ITEM_STATUS_TONES[item.status]}`}
                                >
                                  {PROFESSOR_STATUS_LABELS[item.status]}
                                </span>
                              )}
                              {renderBatchTaskItemReviewButton(item)}
                              {renderBatchItemSendButton(item)}
                            </div>
                          </div>
                          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-stone-500">
                            {item.scheduled_at ? (
                              <span>
                                {sendCanceled ? "原计划发送" : "计划发送"}{" "}
                                {formatDisplayTime(item.scheduled_at)}
                              </span>
                            ) : null}
                            {sendCanceled ? (
                              <span className="font-medium text-red-700">
                                {restoreWindowExpired
                                  ? "原定发送时间已过，无法恢复"
                                  : "该导师不会收到本次邮件"}
                              </span>
                            ) : cancellationText ? (
                              <span className="font-medium text-red-700">
                                {cancellationText}
                              </span>
                            ) : (
                              renderBatchTaskItemAction(item)
                            )}
                            {item.match_score !== null ? (
                              <span>匹配分 {item.match_score}</span>
                            ) : null}
                          </div>
                        </div>
                      );
                    })
                  ) : (
                    <p className="rounded-2xl border border-dashed border-stone-200 px-4 py-3 text-sm text-stone-500">
                      暂无未发送导师。
                    </p>
                  )}
                </div>
                <Pagination
                  page={safeBatchPendingItemPage}
                  pageSize={batchPendingItemPageSize}
                  totalCount={pendingBatchTaskItems.length}
                  onChange={handleBatchPendingItemPaginationChange}
                  ariaLabel="未发送导师分页"
                  pageSizeAriaLabel="未发送导师每页数量"
                  variant="compact"
                  pageSizeOptions={DETAIL_PAGE_SIZE_OPTIONS}
                  unitLabel="位"
                  itemLabel="位导师"
                  summary={`${(safeBatchPendingItemPage - 1) * batchPendingItemPageSize + 1}-${Math.min(pendingBatchTaskItems.length, safeBatchPendingItemPage * batchPendingItemPageSize)} / ${pendingBatchTaskItems.length}`}
                  focusTargetRef={batchPendingItemsStartRef}
                  menuPlacement="popover"
                  className="mt-3 border-t border-stone-100 pt-3"
                />
              </section>

              {generatingDraftBatchTaskItems.length > 0 ? (
                <section className="mt-6">
                  <h3 className="text-sm font-semibold text-stone-900">
                    正在生成草稿
                  </h3>
                  <div className="mt-3 space-y-2">
                    {visibleGeneratingDraftBatchTaskItems.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-2xl border border-sky-100 bg-sky-50/50 px-4 py-3"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="text-sm font-medium text-stone-900">
                              {item.professor_name}
                            </p>
                            <p className="mt-1 text-xs text-stone-500">
                              {[
                                item.professor_title,
                                item.professor_school,
                                item.professor_email,
                              ]
                                .filter(Boolean)
                                .join(" / ") || "暂无补充信息"}
                            </p>
                          </div>
                          <div className="flex flex-wrap items-center justify-end gap-2">
                            <span className="rounded-full bg-sky-100 px-2.5 py-1 text-xs text-sky-700">
                              {PROFESSOR_STATUS_LABELS[item.status]}
                            </span>
                            {renderBatchItemSendButton(item)}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                  <Pagination
                    page={safeBatchGeneratingItemPage}
                    pageSize={batchGeneratingItemPageSize}
                    totalCount={generatingDraftBatchTaskItems.length}
                    onChange={handleBatchGeneratingItemPaginationChange}
                    ariaLabel="正在生成草稿分页"
                    pageSizeAriaLabel="正在生成草稿每页数量"
                    variant="compact"
                    pageSizeOptions={DETAIL_PAGE_SIZE_OPTIONS}
                    unitLabel="封"
                    itemLabel="封草稿"
                    className="mt-3 border-t border-stone-100 pt-3"
                  />
                </section>
              ) : null}

              {draftFailedBatchTaskItems.length > 0 ? (
                <section className="mt-6">
                  <h3 className="text-sm font-semibold text-stone-900">
                    草稿生成失败
                  </h3>
                  <div className="mt-3 space-y-2">
                    {visibleDraftFailedBatchTaskItems.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-2xl border border-red-100 bg-red-50/60 px-4 py-3"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="text-sm font-medium text-stone-900">
                              {item.professor_name}
                            </p>
                            <p className="mt-1 text-xs text-red-700">
                              {item.last_error || "暂无失败原因"}
                            </p>
                          </div>
                          <div className="flex flex-wrap items-center justify-end gap-3 text-xs">
                            {renderBatchTaskItemAction(item)}
                            {renderBatchItemSendButton(item)}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                  <Pagination
                    page={safeBatchDraftFailedItemPage}
                    pageSize={batchDraftFailedItemPageSize}
                    totalCount={draftFailedBatchTaskItems.length}
                    onChange={handleBatchDraftFailedItemPaginationChange}
                    ariaLabel="草稿生成失败分页"
                    pageSizeAriaLabel="草稿生成失败每页数量"
                    variant="compact"
                    pageSizeOptions={DETAIL_PAGE_SIZE_OPTIONS}
                    unitLabel="封"
                    itemLabel="封草稿"
                    className="mt-3 border-t border-stone-100 pt-3"
                  />
                </section>
              ) : null}

              {failedBatchTaskItems.length > 0 ? (
                <section className="mt-6">
                  <h3 className="text-sm font-semibold text-stone-900">
                    发送失败
                  </h3>
                  <div className="mt-3 space-y-2">
                    {visibleFailedBatchTaskItems.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-2xl border border-red-100 bg-red-50/60 px-4 py-3"
                      >
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-stone-900">
                            {item.professor_name}
                          </p>
                          <EmailDeliveryFailureDetails
                            possibleCause={item.possible_cause}
                            rawError={item.last_error}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                  <Pagination
                    page={safeBatchFailedItemPage}
                    pageSize={batchFailedItemPageSize}
                    totalCount={failedBatchTaskItems.length}
                    onChange={handleBatchFailedItemPaginationChange}
                    ariaLabel="发送失败分页"
                    pageSizeAriaLabel="发送失败每页数量"
                    variant="compact"
                    pageSizeOptions={DETAIL_PAGE_SIZE_OPTIONS}
                    unitLabel="封"
                    itemLabel="封邮件"
                    className="mt-3 border-t border-stone-100 pt-3"
                  />
                </section>
              ) : null}

              <section className="mt-6">
                <h3 className="text-sm font-semibold text-stone-900">
                  基础信息
                </h3>
                <dl className="mt-3 divide-y divide-stone-100 rounded-2xl border border-stone-100 text-sm">
                  <div className="grid gap-1 px-4 py-3 sm:grid-cols-[120px_1fr]">
                    <dt className="text-stone-500">发信模板</dt>
                    <dd className="text-stone-800">
                      <div className="font-medium text-stone-900">
                        {getOutreachTemplateSourceLabel(selectedBatchTask)}
                      </div>
                      {selectedBatchTask.outreach_template_snapshot_version !==
                      null ? (
                        <div className="mt-1 text-xs leading-5 text-stone-500">
                          使用任务创建时的模板快照。
                        </div>
                      ) : null}
                    </dd>
                  </div>
                  <div className="grid gap-1 px-4 py-3 sm:grid-cols-[120px_1fr]">
                    <dt className="text-stone-500">写信方式</dt>
                    <dd className="text-stone-800">
                      {getOutreachGenerationModeLabel(
                        selectedBatchTask.outreach_generation_mode,
                      )}
                    </dd>
                  </div>
                  <div className="grid gap-1 px-4 py-3 sm:grid-cols-[120px_1fr]">
                    <dt className="text-stone-500">邮件主题</dt>
                    <dd className="text-stone-800">
                      {selectedBatchTask.email_subject || "未设置"}
                    </dd>
                  </div>
                  <div className="grid gap-1 px-4 py-3 sm:grid-cols-[120px_1fr]">
                    <dt className="text-stone-500">创建时间</dt>
                    <dd className="text-stone-800">
                      {formatDisplayTime(selectedBatchTask.created_at)}
                    </dd>
                  </div>
                  <div className="grid gap-1 px-4 py-3 sm:grid-cols-[120px_1fr]">
                    <dt className="text-stone-500">更新时间</dt>
                    <dd className="text-stone-800">
                      {formatDisplayTime(selectedBatchTask.updated_at)}
                    </dd>
                  </div>
                </dl>
              </section>
            </>
          )}
        </div>
      </section>
    </div>
  ) : null;
}
