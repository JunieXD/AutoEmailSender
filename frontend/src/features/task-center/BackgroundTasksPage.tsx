import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  Activity,
  Ban,
  Bot,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  FileSearch,
  FileText,
  Loader2,
  Mail,
  Pause,
  Pencil,
  Play,
  RotateCcw,
  Save,
  Search,
  Sparkles,
  Square,
  SquareCheck,
  SquareMinus,
  Trash2,
  X,
} from "lucide-react";
import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";
import { AttachmentSizeSummary } from "@/components/molecules/AttachmentSizeSummary";
import { EmailDeliveryFailureDetails } from "@/components/molecules/EmailDeliveryFailureDetails";
import { KeywordSearchScopeSelect } from "@/components/molecules/KeywordSearchScopeSelect";
import { Pagination } from "@/components/molecules/Pagination";
import { SelectionToggleButton } from "@/components/molecules/SelectionToggleButton";
import { ProfessorEditDialog } from "@/components/molecules/ProfessorEditDialog";
import { SubjectTemplateInput } from "@/components/molecules/SubjectTemplateInput";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { useBackgroundTaskNotification } from "@/app/providers/BackgroundTaskNotificationContext";
import { useNotification } from "@/context/NotificationContext";
import { useSelectionContext } from "@/context/SelectionContext";
import { useConfirmDialog } from "@/lib/useConfirmDialog";
import { ApiError } from "@/lib/api/client";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";
import { useDocumentScrollLock } from "@/lib/useDocumentScrollLock";
import { safeRecordUserAction } from "@/lib/diagnosticUserActions";
import {
  approveAllBatchTaskDrafts,
  approveAndSendBatchTaskItemDraft,
  approveBatchTaskItemDraft,
  cancelBatchTaskItemSend,
  deleteBatchTask,
  deleteBatchTaskItem,
  getBatchTaskItemThread,
  getBatchTaskResendContext,
  getBatchTaskSummary,
  listBatchTasks,
  listBatchTaskItems,
  pauseBatchTask,
  regenerateBatchTaskItemDraft,
  rewriteBatchTaskItemDraft,
  retryBatchTaskItemDraft,
  restoreBatchTaskItemSend,
  restoreBatchTask,
  resumeBatchTask,
  stopBatchTask,
  updateBatchTaskItemOutreachConfig,
} from "@/lib/api/batchTasksApi";
import {
  getOutreachTemplate,
  listOutreachTemplates,
} from "@/lib/api/outreachTemplates";
import { writeCreateTaskNavigationHandoff } from "@/features/navigation-handoffs/client/navigationHandoff";
import { BatchTaskResendDialog } from "@/features/batch-tasks/components/BatchTaskResendDialog";
import {
  TaskCenterSectionSwitch,
  type TaskCenterSection,
} from "@/features/email-deliveries/components/TaskCenterSectionSwitch";
import { getEmailSendFailureMessage } from "@/features/email/client/getEmailSendFailureMessage";
import {
  buildBulkLargeAttachmentWarning,
  buildLargeAttachmentWarning,
  formatFileSize,
  getSelectedAttachmentTotalBytes,
  LARGE_ATTACHMENT_WARNING_CONFIRMATION_LABEL,
  shouldPromptForLargeAttachments,
  suppressLargeAttachmentWarnings,
} from "@/features/attachments/attachmentSize";
import {
  cancelMatchAnalysisJob,
  deleteMatchAnalysisJob,
  listMatchAnalysisJobItems,
  listMatchAnalysisJobs,
  restoreMatchAnalysisJob,
  retryFailedMatchAnalysisJob,
} from "@/lib/api/matchAnalysisJobsApi";
import {
  cancelProfessorInformationEnrichmentJob,
  deleteProfessorInformationEnrichmentJob,
  listProfessorInformationEnrichmentItemsPage,
  listProfessorInformationEnrichmentJobsPage,
  restoreProfessorInformationEnrichmentJob,
  retryFailedProfessorInformationEnrichmentJob,
} from "@/entities/professor/api/informationEnrichment";
import { getProfessor } from "@/entities/professor/api/professors";
import {
  cancelCrawlJob,
  approveCrawlCandidates,
  deleteCrawlJob,
  enrichCrawlCandidates,
  getCrawlJob,
  getCrawlJobDetails,
  listCrawlJobsPage,
  pauseCrawlJob,
  retryCrawlJob,
  restoreCrawlJob,
  resumeCrawlJobReview,
  resumeCrawlJob,
  updateCrawlCandidate,
} from "@/lib/api/crawlJobsApi";
import {
  filterCrawlCandidates,
  getImportableCandidateIds,
  getReviewableCandidateIdsWithoutEmail,
  getReviewableCandidateIds,
  hasActiveCrawlCandidateFilters,
  normalizeCrawlCandidateSearchScopes,
  pruneSelectedCandidateIds,
  type CrawlCandidateFilters,
  type CrawlCandidateInformationCondition,
  type CrawlCandidateInformationField,
  type CrawlCandidateInformationMatchMode,
  type CrawlCandidateReviewStatusFilter,
} from "@/features/crawl-review/client/reviewCandidates";
import {
  getCandidateEnrichmentFailureMessage,
  getCrawlEventFailureReason,
} from "@/features/crawl-review/client/crawlJobEvents";
import {
  buildBatchPendingItemAction,
  getOutreachGenerationModeLabel,
  getOutreachTemplateSourceLabel,
  getBatchTaskItemCancellationText,
  getBatchTaskWaitingSendCount,
  isBatchTaskItemMissingResearchDirection,
} from "@/features/batch-tasks/client/batchTaskDisplay";
import { getPageItems, getTotalPages } from "@/lib/pagination";
import { usePaginationState } from "@/lib/usePaginationState";
import {
  normalizeExternalHttpUrl,
  openExternalHttpUrl,
} from "@/lib/externalUrls";
import { textToEmailHtml } from "@/lib/richEmail";
import {
  BATCH_TASK_STATUS_LABELS,
  MATERIAL_TYPE_LABELS,
  MATCH_ANALYSIS_JOB_STATUS_LABELS,
  PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS,
  PROFESSOR_STATUS_LABELS,
  type BatchTaskCardDTO,
  type BatchTaskItemDTO,
  type BatchTaskResendContextDTO,
  type CrawlCandidateDTO,
  type CrawlJobEventDTO,
  type CrawlJobDetailsDTO,
  type CrawlJobSummaryDTO,
  type CrawlPageDTO,
  type MatchAnalysisJobDTO,
  type MatchAnalysisJobItemDTO,
  type MatchAnalysisJobItemsPageDTO,
  type MatchAnalysisJobItemStatus,
  type OutreachTemplateDTO,
  type ProfessorInformationEnrichmentItemDTO,
  type ProfessorInformationEnrichmentItemsPageDTO,
  type ProfessorInformationEnrichmentItemStatus,
  type ProfessorInformationEnrichmentJobDTO,
  type ProfessorDTO,
  type ProfessorManagementItemDTO,
  type WorkspaceThreadDTO,
} from "@/types";
import {
  CrawlJobCard,
  TaskListViewSwitch,
  TokenUsageBreakdown,
} from "./components/TaskCenterCards";
import { TaskListToolbar } from "./components/TaskListToolbar";
import { TaskTypeTabs } from "./components/TaskTypeTabs";
import {
  CRAWL_JOB_STATUS_LABELS,
  canDeleteBatchTask,
  canDeleteInformationEnrichmentJob,
  canDeleteMatchJob,
  canOpenBatchResend,
} from "./model/taskCenterJobs";
import {
  DEFAULT_TASK_SORT_DIRECTIONS,
  createDefaultTaskListFilters,
  filterAndSortTaskItems,
  type TaskListFilters,
  type TaskListViews,
  type TaskListFilter,
  type TaskSortDirection,
  type TaskSortKey,
  type TasksTab,
} from "./model/taskCenterFilters";
import {
  CRAWL_CANDIDATE_EDIT_INPUT_CLASS,
  CRAWL_CANDIDATE_INFORMATION_FIELD_OPTIONS,
  CRAWL_CANDIDATE_REVIEW_STATUS_LABELS,
  CRAWL_CANDIDATE_REVIEW_STATUS_TONES,
  CRAWL_CANDIDATE_SEARCH_SCOPE_OPTIONS,
  createDefaultCrawlCandidateFilters,
  getCrawlCandidateInformationConditionEntries,
  getCrawlCandidateInformationConditionsSummary,
  getCrawlCandidateSearchPlaceholder,
  hasUnsavedCrawlCandidateChanges,
  toCrawlCandidateEditForm,
  toCrawlCandidateUpdatePayload,
  type CrawlCandidateEditForm,
} from "./model/crawlCandidateReview";
import {
  BATCH_ITEM_STATUS_TONES,
  INFORMATION_ENRICHMENT_FIELD_LABELS,
  INFORMATION_ENRICHMENT_ITEM_STATUS_LABELS,
  INFORMATION_ENRICHMENT_ITEM_STATUS_TONES,
  INFORMATION_ENRICHMENT_JOB_STATUS_TONES,
  MATCH_ANALYSIS_ITEM_STATUS_LABELS,
  MATCH_ANALYSIS_ITEM_STATUS_TONES,
  MATCH_ANALYSIS_JOB_STATUS_TONES,
} from "./model/taskCenterStatus";
import {
  BATCH_DETAIL_ITEM_PAGE_SIZE,
  BATCH_REVIEW_DRAFT_SOURCE_LABELS,
  BATCH_TASK_DETAILS_REFRESH_INTERVAL_MS,
  buildBatchTaskSummarySignature,
  CRAWL_DETAILS_REFRESH_INTERVAL_MS,
  CRAWL_DETAIL_CONTENT_REFRESH_INTERVAL_MS,
  CRAWL_REFRESH_INTERVAL_MS,
  DETAIL_PAGE_SIZE_OPTIONS,
  INFORMATION_ENRICHMENT_ITEMS_PAGE_CACHE_SIZE,
  MATCH_JOB_ITEMS_PAGE_CACHE_SIZE,
  MONITOR_PAGE_SIZE_OPTIONS,
  MONITOR_SECTION_PAGE_SIZE,
  PAGE_SIZE_STORAGE_KEYS,
  TASKS_PAGE_SIZE,
  TASKS_PAGE_SIZE_OPTIONS,
  buildScheduleLabel,
  deriveBatchReviewText,
  formatDisplayTime,
  formatDuration,
  getBatchReviewDraft,
  getInformationEnrichmentItemsCacheKey,
  getMatchJobItemsCacheKey,
  isBatchItemScheduledInFuture,
  type RichEmailValue,
} from "./model/taskCenterConfig";

export { CrawlJobCard, TaskListViewSwitch } from "./components/TaskCenterCards";

type BatchReviewItemActionType = "template" | "regenerate" | "delete" | "submit";
type BatchReviewItemActions = Record<number, BatchReviewItemActionType>;
type BatchSendItemAction = {
  itemId: number;
  kind: "cancel" | "restore";
};

export type PendingCrawlJobHandoff = {
  token: number;
  data: CrawlJobDetailsDTO;
};

type BackgroundTasksPageProps = {
  pendingCrawlJobHandoff: PendingCrawlJobHandoff | null;
  onCrawlHandoffApplied: (token: number) => void;
};

export const BackgroundTasksPage = ({
  pendingCrawlJobHandoff,
  onCrawlHandoffApplied,
}: BackgroundTasksPageProps) => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    identities = [],
    selectedIdentityId,
    selectedLlmProfileId,
    selectedLlmProfile,
    setSelectedIdentityId,
  } = useSelectionContext();
  const { notifyError, notifySuccess } = useNotification();
  const {
    stopTrackingInformationEnrichmentJob,
    trackCrawlCandidateEnrichment,
    trackCrawlJob,
    trackInformationEnrichmentJob,
    trackMatchAnalysisJob,
  } = useBackgroundTaskNotification();
  const { confirm, dialog: confirmDialog } = useConfirmDialog();
  const taskCenterSection: TaskCenterSection = "background";
  const requestedBatchTaskId = Number(searchParams.get("batch_task_id"));
  const hasTaskSelection = selectedIdentityId !== null;
  const [activeTab, setActiveTab] = useState<TasksTab>(() =>
    hasTaskSelection ? "batch" : "crawl",
  );
  const [taskListViews, setTaskListViews] = useState<TaskListViews>({
    batch: "current",
    crawl: "current",
    match: "current",
    enrichment: "current",
  });
  const [taskListFilters, setTaskListFilters] = useState<TaskListFilters>(
    createDefaultTaskListFilters,
  );
  const [taskSortDirections, setTaskSortDirections] = useState<
    Record<TaskSortKey, TaskSortDirection>
  >({ ...DEFAULT_TASK_SORT_DIRECTIONS });
  const [advancedTaskFiltersOpen, setAdvancedTaskFiltersOpen] = useState(false);
  const [tasks, setTasks] = useState<BatchTaskCardDTO[]>([]);
  const [currentBatchTasks, setCurrentBatchTasks] = useState<BatchTaskCardDTO[]>([]);
  const [selectedBatchTask, setSelectedBatchTask] =
    useState<BatchTaskCardDTO | null>(null);
  const [selectedBatchTaskItems, setSelectedBatchTaskItems] = useState<
    BatchTaskItemDTO[]
  >([]);
  const [professorEditDialogOpen, setProfessorEditDialogOpen] = useState(false);
  const [professorEditLoading, setProfessorEditLoading] = useState(false);
  const [professorEditProfessor, setProfessorEditProfessor] =
    useState<ProfessorDTO | null>(null);
  const [batchTaskDetailsLoading, setBatchTaskDetailsLoading] = useState(false);
  const [resendContext, setResendContext] = useState<BatchTaskResendContextDTO | null>(null);
  const [resendLoading, setResendLoading] = useState(false);
  const [resendDialogOpen, setResendDialogOpen] = useState(false);
  const [selectedResendProfessorIds, setSelectedResendProfessorIds] = useState<number[]>([]);
  const [batchReviewItemId, setBatchReviewItemId] = useState<number | null>(null);
  const [batchReviewThread, setBatchReviewThread] =
    useState<WorkspaceThreadDTO | null>(null);
  const [batchReviewLoading, setBatchReviewLoading] = useState(false);
  const [batchBulkApprovalLoading, setBatchBulkApprovalLoading] = useState(false);
  const [batchReviewItemActions, setBatchReviewItemActions] =
    useState<BatchReviewItemActions>({});
  const [batchSendItemAction, setBatchSendItemAction] =
    useState<BatchSendItemAction | null>(null);
  const [batchSendActionNowMs, setBatchSendActionNowMs] = useState(() =>
    Date.now(),
  );
  const [batchReviewSubject, setBatchReviewSubject] = useState("");
  const [batchReviewContentText, setBatchReviewContentText] = useState("");
  const [batchReviewContentHtml, setBatchReviewContentHtml] = useState("");
  const [batchReviewOutreachTemplates, setBatchReviewOutreachTemplates] = useState<
    OutreachTemplateDTO[]
  >([]);
  const [batchReviewOutreachTemplatesLoaded, setBatchReviewOutreachTemplatesLoaded] =
    useState(false);
  const [loadingBatchReviewOutreachTemplates, setLoadingBatchReviewOutreachTemplates] =
    useState(false);
  const [batchReviewSelectedMaterialIds, setBatchReviewSelectedMaterialIds] =
    useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [matchAnalysisJobs, setMatchAnalysisJobs] = useState<
    MatchAnalysisJobDTO[]
  >([]);
  const [currentMatchAnalysisJobs, setCurrentMatchAnalysisJobs] = useState<
    MatchAnalysisJobDTO[]
  >([]);
  const [matchJobsLoading, setMatchJobsLoading] = useState(false);
  const [selectedMatchJob, setSelectedMatchJob] =
    useState<MatchAnalysisJobDTO | null>(null);
  const [selectedMatchJobItems, setSelectedMatchJobItems] = useState<
    MatchAnalysisJobItemDTO[]
  >([]);
  const [matchJobItemTotalCount, setMatchJobItemTotalCount] = useState(0);
  const [matchJobDetailsLoading, setMatchJobDetailsLoading] = useState(false);
  const [informationEnrichmentJobs, setInformationEnrichmentJobs] = useState<
    ProfessorInformationEnrichmentJobDTO[]
  >([]);
  const [informationEnrichmentJobTotalCount, setInformationEnrichmentJobTotalCount] =
    useState(0);
  const [currentInformationEnrichmentJobCount, setCurrentInformationEnrichmentJobCount] =
    useState(0);
  const [informationEnrichmentJobsLoading, setInformationEnrichmentJobsLoading] =
    useState(false);
  const [selectedInformationEnrichmentJob, setSelectedInformationEnrichmentJob] =
    useState<ProfessorInformationEnrichmentJobDTO | null>(null);
  const [selectedInformationEnrichmentItems, setSelectedInformationEnrichmentItems] =
    useState<ProfessorInformationEnrichmentItemDTO[]>([]);
  const [informationEnrichmentItemTotalCount, setInformationEnrichmentItemTotalCount] =
    useState(0);
  const [informationEnrichmentDetailsLoading, setInformationEnrichmentDetailsLoading] =
    useState(false);
  const [matchJobItemStatusFilter, setMatchJobItemStatusFilterState] = useState<
    "all" | MatchAnalysisJobItemStatus
  >("all");
  const [informationEnrichmentItemStatusFilter, setInformationEnrichmentItemStatusFilterState] =
    useState<"all" | ProfessorInformationEnrichmentItemStatus>("all");
  const [crawlJobs, setCrawlJobs] = useState<CrawlJobSummaryDTO[]>([]);
  const [crawlJobTotalCount, setCrawlJobTotalCount] = useState(0);
  const [currentCrawlJobCount, setCurrentCrawlJobCount] = useState(0);
  const [crawlJobsLoading, setCrawlJobsLoading] = useState(false);
  const {
    page: batchPage,
    pageSize: batchPageSize,
    setPage: setBatchPage,
    onChange: handleBatchPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.batchTasks,
    initialPageSize: TASKS_PAGE_SIZE,
  });
  const {
    page: matchJobItemPage,
    pageSize: matchJobItemPageSize,
    setPage: setMatchJobItemPage,
    onChange: handleMatchJobItemPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.matchJobItems,
    initialPageSize: 10,
  });
  const {
    page: informationEnrichmentItemPage,
    pageSize: informationEnrichmentItemPageSize,
    setPage: setInformationEnrichmentItemPage,
    onChange: handleInformationEnrichmentItemPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.informationEnrichmentItems,
    initialPageSize: 10,
  });
  const {
    page: matchPage,
    pageSize: matchPageSize,
    setPage: setMatchPage,
    onChange: handleMatchPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.matchJobs,
    initialPageSize: TASKS_PAGE_SIZE,
  });
  const {
    page: informationEnrichmentPage,
    pageSize: informationEnrichmentPageSize,
    setPage: setInformationEnrichmentPage,
    onChange: handleInformationEnrichmentPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.informationEnrichmentJobs,
    initialPageSize: TASKS_PAGE_SIZE,
  });
  const {
    page: crawlPage,
    pageSize: crawlPageSize,
    setPage: setCrawlPage,
    onChange: handleCrawlPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.crawlJobs,
    initialPageSize: TASKS_PAGE_SIZE,
  });
  const {
    page: batchSentItemPage,
    pageSize: batchSentItemPageSize,
    setPage: setBatchSentItemPage,
    onChange: handleBatchSentItemPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.batchSentItems,
    initialPageSize: BATCH_DETAIL_ITEM_PAGE_SIZE,
  });
  const {
    page: batchPendingItemPage,
    pageSize: batchPendingItemPageSize,
    setPage: setBatchPendingItemPage,
    onChange: handleBatchPendingItemPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.batchPendingItems,
    initialPageSize: BATCH_DETAIL_ITEM_PAGE_SIZE,
  });
  const {
    page: batchGeneratingItemPage,
    pageSize: batchGeneratingItemPageSize,
    setPage: setBatchGeneratingItemPage,
    onChange: handleBatchGeneratingItemPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.batchGeneratingItems,
    initialPageSize: BATCH_DETAIL_ITEM_PAGE_SIZE,
  });
  const {
    page: batchDraftFailedItemPage,
    pageSize: batchDraftFailedItemPageSize,
    setPage: setBatchDraftFailedItemPage,
    onChange: handleBatchDraftFailedItemPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.batchDraftFailedItems,
    initialPageSize: BATCH_DETAIL_ITEM_PAGE_SIZE,
  });
  const {
    page: batchFailedItemPage,
    pageSize: batchFailedItemPageSize,
    setPage: setBatchFailedItemPage,
    onChange: handleBatchFailedItemPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.batchFailedItems,
    initialPageSize: BATCH_DETAIL_ITEM_PAGE_SIZE,
  });
  const {
    page: batchReviewItemPage,
    pageSize: batchReviewItemPageSize,
    setPage: setBatchReviewItemPage,
    onChange: handleBatchReviewItemPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.batchReviewItems,
    initialPageSize: BATCH_DETAIL_ITEM_PAGE_SIZE,
  });
  const {
    page: crawlEventPage,
    pageSize: crawlEventPageSize,
    setPage: setCrawlEventPage,
    onChange: handleCrawlEventPaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.crawlEvents,
    initialPageSize: MONITOR_SECTION_PAGE_SIZE,
  });
  const {
    page: crawlDetailPagePage,
    pageSize: crawlDetailPagePageSize,
    setPage: setCrawlDetailPagePage,
    onChange: handleCrawlDetailPagePaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.crawlPages,
    initialPageSize: MONITOR_SECTION_PAGE_SIZE,
  });
  const {
    page: crawlCandidatePage,
    pageSize: crawlCandidatePageSize,
    setPage: setCrawlCandidatePage,
    onChange: handleCrawlCandidatePaginationChange,
  } = usePaginationState({
    storageKey: PAGE_SIZE_STORAGE_KEYS.crawlCandidates,
    initialPageSize: MONITOR_SECTION_PAGE_SIZE,
  });
  const [selectedCrawlJob, setSelectedCrawlJob] =
    useState<CrawlJobSummaryDTO | null>(null);
  const [crawlJobPages, setCrawlJobPages] = useState<CrawlPageDTO[]>([]);
  const [crawlJobCandidates, setCrawlJobCandidates] = useState<
    CrawlCandidateDTO[]
  >([]);
  const [crawlCandidateFilters, setCrawlCandidateFilters] =
    useState<CrawlCandidateFilters>(createDefaultCrawlCandidateFilters);
  const [crawlCandidateInformationFiltersOpen, setCrawlCandidateInformationFiltersOpen] =
    useState(false);
  const [crawlJobEvents, setCrawlJobEvents] = useState<CrawlJobEventDTO[]>([]);
  const [crawlJobDetailsLoading, setCrawlJobDetailsLoading] = useState(false);
  const [selectedCrawlCandidateIds, setSelectedCrawlCandidateIds] = useState<
    number[]
  >([]);
  const [crawlJobApproveLoading, setCrawlJobApproveLoading] = useState(false);
  const [crawlJobEnrichLoading, setCrawlJobEnrichLoading] = useState(false);
  const [retryingCrawlJobId, setRetryingCrawlJobId] = useState<number | null>(
    null,
  );
  const [resumingCrawlJobReviewId, setResumingCrawlJobReviewId] = useState<
    number | null
  >(null);
  const [cancelingMatchJobId, setCancelingMatchJobId] = useState<number | null>(
    null,
  );
  const [retryingMatchJobId, setRetryingMatchJobId] = useState<number | null>(
    null,
  );
  const [cancelingInformationEnrichmentJobId, setCancelingInformationEnrichmentJobId] =
    useState<number | null>(null);
  const [retryingInformationEnrichmentJobId, setRetryingInformationEnrichmentJobId] =
    useState<number | null>(null);
  const [pausingCrawlJobId, setPausingCrawlJobId] = useState<number | null>(
    null,
  );
  const [resumingCrawlJobId, setResumingCrawlJobId] = useState<number | null>(
    null,
  );
  const [selectedCandidateDetail, setSelectedCandidateDetail] =
    useState<CrawlCandidateDTO | null>(null);
  const [candidateEditForm, setCandidateEditForm] =
    useState<CrawlCandidateEditForm | null>(null);
  const [candidateUpdateLoading, setCandidateUpdateLoading] = useState(false);
  const lastLoadErrorRef = useRef<string | null>(null);
  const lastBatchTaskDetailsLoadErrorRef = useRef<string | null>(null);
  const lastMatchJobsLoadErrorRef = useRef<string | null>(null);
  const lastMatchJobDetailsLoadErrorRef = useRef<string | null>(null);
  const lastInformationEnrichmentJobsLoadErrorRef = useRef<string | null>(null);
  const lastInformationEnrichmentDetailsLoadErrorRef = useRef<string | null>(null);
  const lastCrawlJobsLoadErrorRef = useRef<string | null>(null);
  const lastCrawlJobDetailsLoadErrorRef = useRef<string | null>(null);
  const loadedTasksKeyRef = useRef<string | null>(null);
  const crawlJobsPreloadedRef = useRef(false);
  const batchTasksPreloadedKeyRef = useRef<string | null>(null);
  const matchJobsPreloadedKeyRef = useRef<string | null>(null);
  const informationEnrichmentJobsPreloadedRef = useRef(false);
  const activeTasksRequestKeyRef = useRef<string | null>(null);
  const previousTaskListViewsRef = useRef(taskListViews);
  const previousSelectedBatchTaskIdRef = useRef(selectedBatchTask?.id);
  const previousSelectedCrawlJobIdRef = useRef(selectedCrawlJob?.id ?? null);
  const latestTasksRequestIdRef = useRef(0);
  const latestBatchTaskDetailsRequestIdRef = useRef(0);
  const batchTaskSummarySignatureRef = useRef<string | null>(null);
  const latestBatchReviewRequestIdRef = useRef(0);
  const latestProfessorEditRequestIdRef = useRef(0);
  const latestMatchJobsRequestIdRef = useRef(0);
  const latestMatchJobDetailsRequestIdRef = useRef(0);
  const matchJobItemsPageCacheRef = useRef(
    new Map<string, MatchAnalysisJobItemsPageDTO>(),
  );
  const informationEnrichmentItemsPageCacheRef = useRef(
    new Map<string, ProfessorInformationEnrichmentItemsPageDTO>(),
  );
  const latestInformationEnrichmentJobsRequestIdRef = useRef(0);
  const latestInformationEnrichmentDetailsRequestIdRef = useRef(0);
  const latestCrawlJobsRequestIdRef = useRef(0);
  const latestCrawlJobSummaryRequestIdRef = useRef(0);
  const latestCrawlJobDetailsRequestIdRef = useRef(0);
  const taskListStartRef = useRef<HTMLElement | null>(null);
  const batchSentItemsStartRef = useRef<HTMLElement | null>(null);
  const batchPendingItemsStartRef = useRef<HTMLElement | null>(null);
  const matchJobItemsStartRef = useRef<HTMLElement | null>(null);
  const informationEnrichmentItemsStartRef = useRef<HTMLElement | null>(null);
  const crawlEventsStartRef = useRef<HTMLElement | null>(null);
  const crawlPagesStartRef = useRef<HTMLElement | null>(null);
  const crawlCandidatesStartRef = useRef<HTMLElement | null>(null);
  const crawlCandidateFirstItemRef = useRef<HTMLDivElement | null>(null);
  const activeTaskListView = taskListViews[activeTab];
  const activeTaskListFilters = taskListFilters[activeTab];
  const tasksRequestKey =
    selectedIdentityId
      ? `${selectedIdentityId}:${taskListViews.batch}`
      : null;
  const updateTaskCenterSection = useCallback(
    (section: TaskCenterSection) => {
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("section", section);
        if (section === "delivery") {
          next.delete("batch_task_id");
        } else {
          next.delete("task_id");
          next.delete("view");
          next.delete("identity_id");
          next.delete("source");
          next.delete("status");
          next.delete("q");
        }
        return next;
      });
    },
    [setSearchParams],
  );
  const renderCandidateExternalUrl = useCallback(
    (url: string | null) => {
      const normalizedUrl = url?.trim();
      if (!normalizedUrl) {
        return "暂无";
      }

      return (
        <a
          href={normalizedUrl}
          target="_blank"
          rel="noreferrer"
          onClick={(event) => {
            if (
              !window.autoEmailSender?.openExternalUrl ||
              !normalizeExternalHttpUrl(normalizedUrl)
            ) {
              return;
            }

            event.preventDefault();
            openExternalHttpUrl(normalizedUrl);
          }}
          className="inline-flex max-w-full items-center gap-1.5 align-bottom text-primary underline-offset-4 hover:underline"
        >
          <span className="truncate">{normalizedUrl}</span>
        </a>
      );
    },
    [],
  );
  const setActiveTaskPage = (page: number) => {
    if (activeTab === "batch") {
      setBatchPage(page);
    } else if (activeTab === "crawl") {
      setCrawlPage(page);
    } else if (activeTab === "match") {
      setMatchPage(page);
    } else {
      setInformationEnrichmentPage(page);
    }
  };
  const updateActiveTaskListFilters = (patch: Partial<TaskListFilter>) => {
    setTaskListFilters((current) => ({
      ...current,
      [activeTab]: { ...current[activeTab], ...patch },
    }));
    setActiveTaskPage(1);
  };
  const resetActiveTaskListFilters = () => {
    setTaskListFilters((current) => ({
      ...current,
      [activeTab]: createDefaultTaskListFilters()[activeTab],
    }));
    setTaskSortDirections({ ...DEFAULT_TASK_SORT_DIRECTIONS });
    setTaskListViews((current) => ({ ...current, [activeTab]: "current" }));
    setAdvancedTaskFiltersOpen(false);
    setActiveTaskPage(1);
  };
  const sentBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) => item.status === "sent" || item.status === "reply_detected",
      ),
    [selectedBatchTaskItems],
  );
  const pendingBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.batch_send_canceled_at !== null ||
          item.status === "canceled" ||
          (item.status !== "sent" &&
            item.status !== "reply_detected" &&
            item.status !== "generating_draft" &&
            item.status !== "draft_failed" &&
            item.status !== "send_failed"),
      ),
    [selectedBatchTaskItems],
  );
  const generatingDraftBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.batch_send_canceled_at === null &&
          item.status === "generating_draft",
      ),
    [selectedBatchTaskItems],
  );
  const draftFailedBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.batch_send_canceled_at === null && item.status === "draft_failed",
      ),
    [selectedBatchTaskItems],
  );
  const failedBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter((item) => item.status === "send_failed"),
    [selectedBatchTaskItems],
  );
  const reviewRequiredBatchTaskItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.batch_send_canceled_at === null &&
          item.status === "review_required",
      ),
    [selectedBatchTaskItems],
  );
  const templateFallbackReviewCount = useMemo(
    () =>
      reviewRequiredBatchTaskItems.filter(
        (item) => item.draft_generation_source === "template_fallback",
      ).length,
    [reviewRequiredBatchTaskItems],
  );
  const batchReviewQueueItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.batch_send_canceled_at === null &&
          (item.status === "review_required" ||
            item.status === "generating_draft"),
      ),
    [selectedBatchTaskItems],
  );
  const activeBatchReviewItem = useMemo(
    () =>
      selectedBatchTaskItems.find((item) => item.id === batchReviewItemId) ??
      null,
    [batchReviewItemId, selectedBatchTaskItems],
  );
  const selectedBatchWaitingSendCount = selectedBatchTask
    ? getBatchTaskWaitingSendCount(selectedBatchTask)
    : 0;
  const selectedBatchNeedsManualItems = useMemo(
    () =>
      selectedBatchTaskItems.filter(
        (item) =>
          item.batch_send_canceled_at === null &&
          (item.next_action === "complete_professor_profile" ||
            item.next_action === "select_primary_material" ||
            item.next_action === "review_draft" ||
            item.next_action === "missing_schedule" ||
            item.next_action === "retry_draft_generation"),
      ),
    [selectedBatchTaskItems],
  );
  const filteredBatchTasks = useMemo(
    () =>
      filterAndSortTaskItems({
        items: tasks,
        filters: taskListFilters.batch,
        direction: taskSortDirections[taskListFilters.batch.sortKey],
        getSearchValuesByScope: (task) => ({
          name: [task.name],
          emailSubject: [task.email_subject],
          template: [task.outreach_template_name_snapshot],
        }),
        getName: (task) => task.name,
        getStatus: (task) => task.status,
        getCreatedAt: (task) => task.created_at,
        getUpdatedAt: (task) => task.updated_at,
        getProgress: (task) =>
          task.target_count > 0 ? task.completed_count / task.target_count : 0,
      }),
    [taskListFilters.batch, taskSortDirections, tasks],
  );
  const filteredCrawlJobs = useMemo(
    () => {
      if (taskListFilters.crawl.sortKey !== "name") {
        return crawlJobs;
      }
      return filterAndSortTaskItems({
        items: crawlJobs,
        filters: taskListFilters.crawl,
        direction: taskSortDirections[taskListFilters.crawl.sortKey],
        getSearchValuesByScope: (job) => ({
          university: [job.university],
          school: [job.school],
          url: [job.start_url, job.start_urls?.join(" ")],
          event: [job.latest_event_message],
        }),
        getName: (job) => `${job.university} ${job.school}`,
        getStatus: (job) => job.status,
        getCreatedAt: (job) => job.created_at,
        getUpdatedAt: (job) => job.updated_at,
        getProgress: (job) =>
          job.progress_total > 0 ? job.progress_current / job.progress_total : 0,
      });
    },
    [crawlJobs, taskListFilters.crawl, taskSortDirections],
  );
  const filteredMatchAnalysisJobs = useMemo(
    () =>
      filterAndSortTaskItems({
        items: matchAnalysisJobs,
        filters: taskListFilters.match,
        direction: taskSortDirections[taskListFilters.match.sortKey],
        getSearchValuesByScope: (job) => ({ name: [job.name] }),
        getName: (job) => job.name,
        getStatus: (job) => job.status,
        getCreatedAt: (job) => job.created_at,
        getUpdatedAt: (job) => job.updated_at,
        getProgress: (job) =>
          job.target_count > 0
            ? (job.succeeded_count + job.failed_count + job.skipped_count) /
              job.target_count
            : 0,
      }),
    [matchAnalysisJobs, taskListFilters.match, taskSortDirections],
  );
  const filteredInformationEnrichmentJobs = useMemo(
    () => {
      if (taskListFilters.enrichment.sortKey !== "name") {
        return informationEnrichmentJobs;
      }
      return filterAndSortTaskItems({
        items: informationEnrichmentJobs,
        filters: taskListFilters.enrichment,
        direction: taskSortDirections[taskListFilters.enrichment.sortKey],
        getSearchValuesByScope: (job) => ({ name: [job.name] }),
        getName: (job) => job.name,
        getStatus: (job) => job.status,
        getCreatedAt: (job) => job.created_at,
        getUpdatedAt: (job) => job.updated_at,
        getProgress: (job) =>
          job.target_count > 0 ? job.completed_count / job.target_count : 0,
      });
    },
    [informationEnrichmentJobs, taskListFilters.enrichment, taskSortDirections],
  );
  const crawlUsesClientPagination = taskListFilters.crawl.sortKey === "name";
  const informationEnrichmentUsesClientPagination =
    taskListFilters.enrichment.sortKey === "name";
  const displayedCrawlJobTotalCount = crawlUsesClientPagination
    ? filteredCrawlJobs.length
    : crawlJobTotalCount;
  const displayedInformationEnrichmentJobTotalCount =
    informationEnrichmentUsesClientPagination
      ? filteredInformationEnrichmentJobs.length
      : informationEnrichmentJobTotalCount;
  const activeAdvancedTaskFilterCount =
    activeTaskListFilters.status === "all" ? 0 : 1;
  const safeBatchPage = Math.min(
    batchPage,
    getTotalPages(filteredBatchTasks.length, batchPageSize),
  );
  const safeCrawlPage = Math.min(
    crawlPage,
    getTotalPages(displayedCrawlJobTotalCount, crawlPageSize),
  );
  const safeMatchPage = Math.min(
    matchPage,
    getTotalPages(filteredMatchAnalysisJobs.length, matchPageSize),
  );
  const safeInformationEnrichmentPage = Math.min(
    informationEnrichmentPage,
    getTotalPages(
      displayedInformationEnrichmentJobTotalCount,
      informationEnrichmentPageSize,
    ),
  );
  const safeBatchSentItemPage = Math.min(
    batchSentItemPage,
    getTotalPages(sentBatchTaskItems.length, batchSentItemPageSize),
  );
  const safeBatchPendingItemPage = Math.min(
    batchPendingItemPage,
    getTotalPages(pendingBatchTaskItems.length, batchPendingItemPageSize),
  );
  const safeBatchGeneratingItemPage = Math.min(
    batchGeneratingItemPage,
    getTotalPages(
      generatingDraftBatchTaskItems.length,
      batchGeneratingItemPageSize,
    ),
  );
  const safeBatchDraftFailedItemPage = Math.min(
    batchDraftFailedItemPage,
    getTotalPages(draftFailedBatchTaskItems.length, batchDraftFailedItemPageSize),
  );
  const safeBatchFailedItemPage = Math.min(
    batchFailedItemPage,
    getTotalPages(failedBatchTaskItems.length, batchFailedItemPageSize),
  );
  const safeBatchReviewItemPage = Math.min(
    batchReviewItemPage,
    getTotalPages(batchReviewQueueItems.length, batchReviewItemPageSize),
  );
  const crawlExecutionLogEvents = useMemo(
    () => crawlJobEvents.filter((event) => event.event_type !== "page"),
    [crawlJobEvents],
  );
  const safeCrawlEventPage = Math.min(
    crawlEventPage,
    getTotalPages(crawlExecutionLogEvents.length, crawlEventPageSize),
  );
  const safeCrawlDetailPagePage = Math.min(
    crawlDetailPagePage,
    getTotalPages(crawlJobPages.length, crawlDetailPagePageSize),
  );
  const filteredCrawlJobCandidates = useMemo(
    () => filterCrawlCandidates(crawlJobCandidates, crawlCandidateFilters),
    [crawlCandidateFilters, crawlJobCandidates],
  );
  const safeCrawlCandidatePage = Math.min(
    crawlCandidatePage,
    getTotalPages(filteredCrawlJobCandidates.length, crawlCandidatePageSize),
  );
  const hasActiveBatchRestoreDeadline = useMemo(
    () =>
      selectedBatchTaskItems.some(
        (item) =>
          item.batch_send_canceled_at !== null &&
          item.can_restore_send &&
          isBatchItemScheduledInFuture(item, batchSendActionNowMs),
      ),
    [batchSendActionNowMs, selectedBatchTaskItems],
  );
  const visibleSentBatchTaskItems = useMemo(
    () =>
      getPageItems(
        sentBatchTaskItems,
        safeBatchSentItemPage,
        batchSentItemPageSize,
      ),
    [batchSentItemPageSize, safeBatchSentItemPage, sentBatchTaskItems],
  );
  const visiblePendingBatchTaskItems = useMemo(
    () =>
      getPageItems(
        pendingBatchTaskItems,
        safeBatchPendingItemPage,
        batchPendingItemPageSize,
      ),
    [batchPendingItemPageSize, pendingBatchTaskItems, safeBatchPendingItemPage],
  );
  const visibleGeneratingDraftBatchTaskItems = useMemo(
    () =>
      getPageItems(
        generatingDraftBatchTaskItems,
        safeBatchGeneratingItemPage,
        batchGeneratingItemPageSize,
      ),
    [
      batchGeneratingItemPageSize,
      generatingDraftBatchTaskItems,
      safeBatchGeneratingItemPage,
    ],
  );
  const visibleDraftFailedBatchTaskItems = useMemo(
    () =>
      getPageItems(
        draftFailedBatchTaskItems,
        safeBatchDraftFailedItemPage,
        batchDraftFailedItemPageSize,
      ),
    [
      batchDraftFailedItemPageSize,
      draftFailedBatchTaskItems,
      safeBatchDraftFailedItemPage,
    ],
  );
  const visibleFailedBatchTaskItems = useMemo(
    () =>
      getPageItems(
        failedBatchTaskItems,
        safeBatchFailedItemPage,
        batchFailedItemPageSize,
      ),
    [batchFailedItemPageSize, failedBatchTaskItems, safeBatchFailedItemPage],
  );
  const visibleBatchReviewQueueItems = useMemo(
    () =>
      getPageItems(
        batchReviewQueueItems,
        safeBatchReviewItemPage,
        batchReviewItemPageSize,
      ),
    [batchReviewItemPageSize, batchReviewQueueItems, safeBatchReviewItemPage],
  );
  const visibleBatchTasks = useMemo(
    () => getPageItems(filteredBatchTasks, safeBatchPage, batchPageSize),
    [batchPageSize, filteredBatchTasks, safeBatchPage],
  );
  const visibleCrawlJobs = useMemo(
    () =>
      crawlUsesClientPagination
        ? getPageItems(filteredCrawlJobs, safeCrawlPage, crawlPageSize)
        : filteredCrawlJobs.slice(0, crawlPageSize),
    [
      crawlPageSize,
      crawlUsesClientPagination,
      filteredCrawlJobs,
      safeCrawlPage,
    ],
  );
  const visibleMatchJobs = useMemo(
    () =>
      getPageItems(filteredMatchAnalysisJobs, safeMatchPage, matchPageSize),
    [filteredMatchAnalysisJobs, matchPageSize, safeMatchPage],
  );
  const visibleInformationEnrichmentJobs = useMemo(
    () =>
      informationEnrichmentUsesClientPagination
        ? getPageItems(
            filteredInformationEnrichmentJobs,
            safeInformationEnrichmentPage,
            informationEnrichmentPageSize,
          )
        : filteredInformationEnrichmentJobs.slice(
            0,
            informationEnrichmentPageSize,
          ),
    [
      filteredInformationEnrichmentJobs,
      informationEnrichmentPageSize,
      informationEnrichmentUsesClientPagination,
      safeInformationEnrichmentPage,
    ],
  );
  const visibleCrawlJobEvents = useMemo(
    () =>
      getPageItems(
        crawlExecutionLogEvents,
        safeCrawlEventPage,
        crawlEventPageSize,
      ),
    [crawlEventPageSize, crawlExecutionLogEvents, safeCrawlEventPage],
  );
  const visibleCrawlJobPages = useMemo(
    () =>
      getPageItems(
        crawlJobPages,
        safeCrawlDetailPagePage,
        crawlDetailPagePageSize,
      ),
    [crawlDetailPagePageSize, crawlJobPages, safeCrawlDetailPagePage],
  );
  const visibleCrawlJobCandidates = useMemo(
    () =>
      getPageItems(
        filteredCrawlJobCandidates,
        safeCrawlCandidatePage,
        crawlCandidatePageSize,
      ),
    [
      crawlCandidatePageSize,
      filteredCrawlJobCandidates,
      safeCrawlCandidatePage,
    ],
  );
  const selectedBatchTaskId = selectedBatchTask?.id ?? null;
  const selectedBatchTaskStatus = selectedBatchTask?.status ?? null;
  const selectedCrawlJobId = selectedCrawlJob?.id ?? null;
  const taskDetailDialogOpen =
    selectedBatchTask !== null ||
    selectedMatchJob !== null ||
    selectedInformationEnrichmentJob !== null ||
    selectedCrawlJob !== null ||
    resendDialogOpen;
  useDocumentScrollLock(taskDetailDialogOpen);
  const selectedCrawlJobCanReview =
    selectedCrawlJob?.status === "needs_review" ||
    selectedCrawlJob?.status === "partially_completed";
  const selectedCrawlJobNeedsReviewResume =
    selectedCrawlJob?.status === "canceled" ||
    selectedCrawlJob?.status === "failed";
  const reviewableCrawlCandidateIds = useMemo(
    () => getReviewableCandidateIds(crawlJobCandidates),
    [crawlJobCandidates],
  );
  const importableCrawlCandidateIds = useMemo(
    () => getImportableCandidateIds(crawlJobCandidates),
    [crawlJobCandidates],
  );
  const reviewableCrawlCandidateIdsWithoutEmail = useMemo(
    () => getReviewableCandidateIdsWithoutEmail(crawlJobCandidates),
    [crawlJobCandidates],
  );
  const selectedReviewableCrawlCandidateIds = useMemo(
    () =>
      pruneSelectedCandidateIds(selectedCrawlCandidateIds, crawlJobCandidates),
    [crawlJobCandidates, selectedCrawlCandidateIds],
  );
  const filteredReviewableCrawlCandidateIds = useMemo(
    () => getReviewableCandidateIds(filteredCrawlJobCandidates),
    [filteredCrawlJobCandidates],
  );
  const filteredSelectedCrawlCandidateCount = useMemo(
    () =>
      filteredReviewableCrawlCandidateIds.filter((candidateId) =>
        selectedReviewableCrawlCandidateIds.includes(candidateId),
      ).length,
    [
      filteredReviewableCrawlCandidateIds,
      selectedReviewableCrawlCandidateIds,
    ],
  );
  const someFilteredCrawlCandidatesSelected =
    filteredSelectedCrawlCandidateCount > 0;
  const allFilteredCrawlCandidatesSelected =
    filteredReviewableCrawlCandidateIds.length > 0 &&
    filteredSelectedCrawlCandidateCount ===
      filteredReviewableCrawlCandidateIds.length;
  const selectedCrawlCandidateIdsWithoutEmail = useMemo(() => {
    const withoutEmailIds = new Set(reviewableCrawlCandidateIdsWithoutEmail);
    return selectedReviewableCrawlCandidateIds.filter((candidateId) =>
      withoutEmailIds.has(candidateId),
    );
  }, [
    reviewableCrawlCandidateIdsWithoutEmail,
    selectedReviewableCrawlCandidateIds,
  ]);
  const selectedImportableCrawlCandidateIds = useMemo(() => {
    const importableIds = new Set(importableCrawlCandidateIds);
    return selectedReviewableCrawlCandidateIds.filter((candidateId) =>
      importableIds.has(candidateId),
    );
  }, [importableCrawlCandidateIds, selectedReviewableCrawlCandidateIds]);
  const crawlCandidateFiltersActive = hasActiveCrawlCandidateFilters(
    crawlCandidateFilters,
  );
  const activeCrawlCandidateInformationConditionCount =
    getCrawlCandidateInformationConditionEntries(
      crawlCandidateFilters.informationConditions,
    ).length;
  const crawlCandidateInformationConditionsSummary =
    getCrawlCandidateInformationConditionsSummary(crawlCandidateFilters);

  useEffect(() => {
    if (
      hasTaskSelection ||
      activeTab === "crawl" ||
      activeTab === "enrichment"
    ) {
      return;
    }
    setActiveTab("crawl");
  }, [activeTab, hasTaskSelection]);

  useEffect(() => {
    const previousTaskListViews = previousTaskListViewsRef.current;
    previousTaskListViewsRef.current = taskListViews;
    if (previousTaskListViews.batch !== taskListViews.batch) {
      setBatchPage(1);
    }
    if (previousTaskListViews.crawl !== taskListViews.crawl) {
      setCrawlPage(1);
    }
    if (previousTaskListViews.match !== taskListViews.match) {
      setMatchPage(1);
    }
    if (previousTaskListViews.enrichment !== taskListViews.enrichment) {
      setInformationEnrichmentPage(1);
    }
  }, [
    setBatchPage,
    setCrawlPage,
    setInformationEnrichmentPage,
    setMatchPage,
    taskListViews,
  ]);

  const loadTasks = useCallback(async (options?: { showLoading?: boolean }) => {
    if (!tasksRequestKey || !selectedIdentityId) {
      latestTasksRequestIdRef.current += 1;
      activeTasksRequestKeyRef.current = null;
      loadedTasksKeyRef.current = null;
      setTasks([]);
      setCurrentBatchTasks([]);
      lastLoadErrorRef.current = null;
      setLoading(false);
      return;
    }
    const requestId = latestTasksRequestIdRef.current + 1;
    latestTasksRequestIdRef.current = requestId;
    activeTasksRequestKeyRef.current = tasksRequestKey;
    if (options?.showLoading ?? true) {
      setLoading(true);
    }
    try {
      const isCurrentView = taskListViews.batch === "current";
      const [data, currentViewData] = await Promise.all([
        listBatchTasks({
          identityId: selectedIdentityId,
          llmProfileId: selectedLlmProfileId,
          view: taskListViews.batch,
        }),
        isCurrentView
          ? null
          : listBatchTasks({
              identityId: selectedIdentityId,
              llmProfileId: selectedLlmProfileId,
              view: "current",
            }),
      ]);
      const currentData = isCurrentView ? data : (currentViewData ?? data);
      if (
        latestTasksRequestIdRef.current !== requestId ||
        activeTasksRequestKeyRef.current !== tasksRequestKey
      ) {
        return;
      }
      setTasks(data);
      setCurrentBatchTasks(currentData);
      loadedTasksKeyRef.current = tasksRequestKey;
      lastLoadErrorRef.current = null;
    } catch (loadError) {
      if (
        latestTasksRequestIdRef.current !== requestId ||
        activeTasksRequestKeyRef.current !== tasksRequestKey
      ) {
        return;
      }
      if (loadedTasksKeyRef.current !== tasksRequestKey) {
        setTasks([]);
      }
      const message =
        loadError instanceof Error ? loadError.message : "加载任务失败";
      if (lastLoadErrorRef.current !== message) {
        notifyError("加载任务失败", message);
        lastLoadErrorRef.current = message;
      }
    } finally {
      if (
        latestTasksRequestIdRef.current === requestId &&
        activeTasksRequestKeyRef.current === tasksRequestKey &&
        (options?.showLoading ?? true)
      ) {
        setLoading(false);
      }
    }
  }, [
    notifyError,
    selectedIdentityId,
    selectedLlmProfileId,
    taskListViews.batch,
    tasksRequestKey,
  ]);

  const loadCrawlJobs = useCallback(
    async (options?: { showLoading?: boolean }) => {
      const requestId = latestCrawlJobsRequestIdRef.current + 1;
      latestCrawlJobsRequestIdRef.current = requestId;
      if (options?.showLoading ?? true) {
        setCrawlJobsLoading(true);
      }
      try {
        const filters = taskListFilters.crawl;
        const usesClientPagination = filters.sortKey === "name";
        const serverSortKey =
          filters.sortKey === "name" ? "created" : filters.sortKey;
        const serverSortDirection = usesClientPagination
          ? "desc"
          : taskSortDirections[filters.sortKey];
        const data = await listCrawlJobsPage({
          offset: usesClientPagination ? 0 : (crawlPage - 1) * crawlPageSize,
          limit: crawlPageSize,
          view: taskListViews.crawl,
          keyword: filters.keyword.trim() || undefined,
          searchScopes: filters.searchScopes,
          status: filters.status === "all" ? undefined : filters.status,
          sortKey: serverSortKey,
          sortDirection: serverSortDirection,
          unpaged: usesClientPagination,
        });
        if (latestCrawlJobsRequestIdRef.current !== requestId) {
          return;
        }
        setCrawlJobTotalCount(data.total_count);
        setCurrentCrawlJobCount(data.current_total_count);
        const totalPages = getTotalPages(data.total_count, crawlPageSize);
        if (!usesClientPagination && crawlPage > totalPages) {
          setCrawlPage(totalPages);
          lastCrawlJobsLoadErrorRef.current = null;
          return;
        }
        setCrawlJobs(data.items);
        setSelectedCrawlJob((currentJob) => {
          if (!currentJob) {
            return currentJob;
          }
          return data.items.find((job) => job.id === currentJob.id) ?? currentJob;
        });
        lastCrawlJobsLoadErrorRef.current = null;
      } catch (loadError) {
        if (latestCrawlJobsRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error ? loadError.message : "加载抓取任务失败";
        if (lastCrawlJobsLoadErrorRef.current !== message) {
          notifyError("加载抓取任务失败", message);
          lastCrawlJobsLoadErrorRef.current = message;
        }
      } finally {
        if (
          latestCrawlJobsRequestIdRef.current === requestId &&
          (options?.showLoading ?? true)
        ) {
          setCrawlJobsLoading(false);
        }
      }
    },
    [
      crawlPage,
      crawlPageSize,
      notifyError,
      setCrawlPage,
      taskListFilters.crawl,
      taskListViews.crawl,
      taskSortDirections,
    ],
  );

  const loadMatchAnalysisJobs = useCallback(async (options?: { showLoading?: boolean }) => {
    if (!selectedIdentityId) {
      setMatchAnalysisJobs([]);
      setCurrentMatchAnalysisJobs([]);
      lastMatchJobsLoadErrorRef.current = null;
      setMatchJobsLoading(false);
      return;
    }
    const requestId = latestMatchJobsRequestIdRef.current + 1;
    latestMatchJobsRequestIdRef.current = requestId;
    if (options?.showLoading ?? true) {
      setMatchJobsLoading(true);
    }
    try {
      const data = await listMatchAnalysisJobs({
        identityId: selectedIdentityId,
        llmProfileId: selectedLlmProfileId,
        view: taskListViews.match,
      });
      const currentData =
        taskListViews.match === "current"
          ? data
          : await listMatchAnalysisJobs({
              identityId: selectedIdentityId,
              llmProfileId: selectedLlmProfileId,
              view: "current",
            });
      if (latestMatchJobsRequestIdRef.current !== requestId) {
        return;
      }
      setMatchAnalysisJobs(data);
      setCurrentMatchAnalysisJobs(currentData);
      setSelectedMatchJob((currentJob) => {
        if (!currentJob) {
          return currentJob;
        }
        return data.find((job) => job.id === currentJob.id) ?? currentJob;
      });
      lastMatchJobsLoadErrorRef.current = null;
    } catch (loadError) {
      if (latestMatchJobsRequestIdRef.current !== requestId) {
        return;
      }
      const message =
        loadError instanceof Error ? loadError.message : "加载匹配分析任务失败";
      if (lastMatchJobsLoadErrorRef.current !== message) {
        notifyError("加载匹配分析任务失败", message);
        lastMatchJobsLoadErrorRef.current = message;
      }
    } finally {
      if (
        latestMatchJobsRequestIdRef.current === requestId &&
        (options?.showLoading ?? true)
      ) {
        setMatchJobsLoading(false);
      }
    }
  }, [notifyError, selectedIdentityId, selectedLlmProfileId, taskListViews.match]);

  const cacheMatchJobItemsPage = useCallback(
    (key: string, page: MatchAnalysisJobItemsPageDTO) => {
      const cache = matchJobItemsPageCacheRef.current;
      cache.delete(key);
      cache.set(key, page);
      while (cache.size > MATCH_JOB_ITEMS_PAGE_CACHE_SIZE) {
        const oldestKey = cache.keys().next().value;
        if (oldestKey === undefined) {
          return;
        }
        cache.delete(oldestKey);
      }
    },
    [],
  );

  const prefetchMatchJobItemsPage = useCallback(
    async (
      jobId: number,
      cursor: number,
      limit: number,
      status: MatchAnalysisJobItemStatus | "all",
    ) => {
      if (cursor < 0) {
        return;
      }
      const key = getMatchJobItemsCacheKey(jobId, cursor, limit, status);
      if (matchJobItemsPageCacheRef.current.has(key)) {
        return;
      }
      try {
        const page = await listMatchAnalysisJobItems(jobId, {
          cursor,
          limit,
          status: status === "all" ? null : status,
        });
        cacheMatchJobItemsPage(key, page);
      } catch {
        // Prefetch failures should not interrupt the currently visible page.
      }
    },
    [cacheMatchJobItemsPage],
  );

  const loadMatchJobDetails = useCallback(
    async (jobId: number) => {
      const requestId = latestMatchJobDetailsRequestIdRef.current + 1;
      latestMatchJobDetailsRequestIdRef.current = requestId;
      const cursor = (matchJobItemPage - 1) * matchJobItemPageSize;
      const key = getMatchJobItemsCacheKey(
        jobId,
        cursor,
        matchJobItemPageSize,
        matchJobItemStatusFilter,
      );
      const cached = matchJobItemsPageCacheRef.current.get(key);
      if (cached) {
        setSelectedMatchJobItems(cached.items);
        setMatchJobItemTotalCount(cached.total_count);
      }
      setMatchJobDetailsLoading(!cached);
      try {
        const data = await listMatchAnalysisJobItems(jobId, {
          cursor,
          limit: matchJobItemPageSize,
          status:
            matchJobItemStatusFilter === "all"
              ? null
              : matchJobItemStatusFilter,
        });
        cacheMatchJobItemsPage(key, data);
        if (latestMatchJobDetailsRequestIdRef.current !== requestId) {
          return;
        }
        setSelectedMatchJobItems(data.items);
        setMatchJobItemTotalCount(data.total_count);
        lastMatchJobDetailsLoadErrorRef.current = null;
        if (data.has_more) {
          void prefetchMatchJobItemsPage(
            jobId,
            cursor + matchJobItemPageSize,
            matchJobItemPageSize,
            matchJobItemStatusFilter,
          );
        }
        if (cursor > 0) {
          void prefetchMatchJobItemsPage(
            jobId,
            cursor - matchJobItemPageSize,
            matchJobItemPageSize,
            matchJobItemStatusFilter,
          );
        }
      } catch (loadError) {
        if (latestMatchJobDetailsRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error
            ? loadError.message
            : "加载匹配分析任务详情失败";
        if (lastMatchJobDetailsLoadErrorRef.current !== message) {
          notifyError("加载匹配分析任务详情失败", message);
          lastMatchJobDetailsLoadErrorRef.current = message;
        }
      } finally {
        if (latestMatchJobDetailsRequestIdRef.current === requestId) {
          setMatchJobDetailsLoading(false);
        }
      }
    },
    [
      cacheMatchJobItemsPage,
      matchJobItemPage,
      matchJobItemPageSize,
      matchJobItemStatusFilter,
      notifyError,
      prefetchMatchJobItemsPage,
    ],
  );

  const setMatchJobItemStatusFilter = useCallback(
    (status: MatchAnalysisJobItemStatus | "all") => {
      setMatchJobItemStatusFilterState(status);
      setMatchJobItemPage(1);
    },
    [setMatchJobItemPage],
  );

  const loadInformationEnrichmentJobs = useCallback(
    async (options?: { showLoading?: boolean }) => {
      const requestId = latestInformationEnrichmentJobsRequestIdRef.current + 1;
      latestInformationEnrichmentJobsRequestIdRef.current = requestId;
      if (options?.showLoading ?? true) {
        setInformationEnrichmentJobsLoading(true);
      }
      try {
        const filters = taskListFilters.enrichment;
        const usesClientPagination = filters.sortKey === "name";
        const serverSortKey =
          filters.sortKey === "name" ? "created" : filters.sortKey;
        const serverSortDirection = usesClientPagination
          ? "desc"
          : taskSortDirections[filters.sortKey];
        const data = await listProfessorInformationEnrichmentJobsPage({
          offset: usesClientPagination
            ? 0
            : (informationEnrichmentPage - 1) * informationEnrichmentPageSize,
          limit: informationEnrichmentPageSize,
          view: taskListViews.enrichment,
          keyword: filters.keyword.trim() || undefined,
          status: filters.status === "all" ? undefined : filters.status,
          sortKey: serverSortKey,
          sortDirection: serverSortDirection,
          unpaged: usesClientPagination,
        });
        if (latestInformationEnrichmentJobsRequestIdRef.current !== requestId) {
          return;
        }
        setInformationEnrichmentJobTotalCount(data.total_count);
        setCurrentInformationEnrichmentJobCount(data.current_total_count);
        const totalPages = getTotalPages(
          data.total_count,
          informationEnrichmentPageSize,
        );
        if (
          !usesClientPagination &&
          informationEnrichmentPage > totalPages
        ) {
          setInformationEnrichmentPage(totalPages);
          lastInformationEnrichmentJobsLoadErrorRef.current = null;
          return;
        }
        setInformationEnrichmentJobs(data.items);
        setSelectedInformationEnrichmentJob((currentJob) => {
          if (!currentJob) {
            return currentJob;
          }
          return data.items.find((job) => job.id === currentJob.id) ?? currentJob;
        });
        lastInformationEnrichmentJobsLoadErrorRef.current = null;
      } catch (loadError) {
        if (latestInformationEnrichmentJobsRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error
            ? loadError.message
            : "加载信息补全任务失败";
        if (lastInformationEnrichmentJobsLoadErrorRef.current !== message) {
          notifyError("加载信息补全任务失败", message);
          lastInformationEnrichmentJobsLoadErrorRef.current = message;
        }
      } finally {
        if (
          latestInformationEnrichmentJobsRequestIdRef.current === requestId &&
          (options?.showLoading ?? true)
        ) {
          setInformationEnrichmentJobsLoading(false);
        }
      }
    },
    [
      informationEnrichmentPage,
      informationEnrichmentPageSize,
      notifyError,
      setInformationEnrichmentPage,
      taskListFilters.enrichment,
      taskListViews.enrichment,
      taskSortDirections,
    ],
  );

  const cacheInformationEnrichmentItemsPage = useCallback(
    (key: string, page: ProfessorInformationEnrichmentItemsPageDTO) => {
      const cache = informationEnrichmentItemsPageCacheRef.current;
      cache.delete(key);
      cache.set(key, page);
      while (cache.size > INFORMATION_ENRICHMENT_ITEMS_PAGE_CACHE_SIZE) {
        const oldestKey = cache.keys().next().value;
        if (oldestKey === undefined) {
          return;
        }
        cache.delete(oldestKey);
      }
    },
    [],
  );

  const prefetchInformationEnrichmentItemsPage = useCallback(
    async (
      jobId: number,
      cursor: number,
      limit: number,
      status: ProfessorInformationEnrichmentItemStatus | "all",
    ) => {
      if (cursor < 0) {
        return;
      }
      const key = getInformationEnrichmentItemsCacheKey(jobId, cursor, limit, status);
      if (informationEnrichmentItemsPageCacheRef.current.has(key)) {
        return;
      }
      try {
        const page = await listProfessorInformationEnrichmentItemsPage(jobId, {
          cursor,
          limit,
          status: status === "all" ? null : status,
        });
        cacheInformationEnrichmentItemsPage(key, page);
      } catch {
        // Prefetch failures should not interrupt the currently visible page.
      }
    },
    [cacheInformationEnrichmentItemsPage],
  );

  const loadInformationEnrichmentDetails = useCallback(
    async (jobId: number) => {
      const requestId = latestInformationEnrichmentDetailsRequestIdRef.current + 1;
      latestInformationEnrichmentDetailsRequestIdRef.current = requestId;
      const cursor = (informationEnrichmentItemPage - 1) * informationEnrichmentItemPageSize;
      const key = getInformationEnrichmentItemsCacheKey(
        jobId,
        cursor,
        informationEnrichmentItemPageSize,
        informationEnrichmentItemStatusFilter,
      );
      const cached = informationEnrichmentItemsPageCacheRef.current.get(key);
      if (cached) {
        setSelectedInformationEnrichmentItems(cached.items);
        setInformationEnrichmentItemTotalCount(cached.total_count);
      }
      setInformationEnrichmentDetailsLoading(!cached);
      try {
        const data = await listProfessorInformationEnrichmentItemsPage(jobId, {
          cursor,
          limit: informationEnrichmentItemPageSize,
          status:
            informationEnrichmentItemStatusFilter === "all"
              ? null
              : informationEnrichmentItemStatusFilter,
        });
        cacheInformationEnrichmentItemsPage(key, data);
        if (latestInformationEnrichmentDetailsRequestIdRef.current !== requestId) {
          return;
        }
        setSelectedInformationEnrichmentItems(data.items);
        setInformationEnrichmentItemTotalCount(data.total_count);
        lastInformationEnrichmentDetailsLoadErrorRef.current = null;
        if (data.has_more) {
          void prefetchInformationEnrichmentItemsPage(
            jobId,
            cursor + informationEnrichmentItemPageSize,
            informationEnrichmentItemPageSize,
            informationEnrichmentItemStatusFilter,
          );
        }
        if (cursor > 0) {
          void prefetchInformationEnrichmentItemsPage(
            jobId,
            cursor - informationEnrichmentItemPageSize,
            informationEnrichmentItemPageSize,
            informationEnrichmentItemStatusFilter,
          );
        }
      } catch (loadError) {
        if (latestInformationEnrichmentDetailsRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error
            ? loadError.message
            : "加载信息补全任务详情失败";
        if (lastInformationEnrichmentDetailsLoadErrorRef.current !== message) {
          notifyError("加载信息补全任务详情失败", message);
          lastInformationEnrichmentDetailsLoadErrorRef.current = message;
        }
      } finally {
        if (latestInformationEnrichmentDetailsRequestIdRef.current === requestId) {
          setInformationEnrichmentDetailsLoading(false);
        }
      }
    },
    [
      cacheInformationEnrichmentItemsPage,
      informationEnrichmentItemPage,
      informationEnrichmentItemPageSize,
      informationEnrichmentItemStatusFilter,
      notifyError,
      prefetchInformationEnrichmentItemsPage,
    ],
  );

  const setInformationEnrichmentItemStatusFilter = useCallback(
    (status: ProfessorInformationEnrichmentItemStatus | "all") => {
      setInformationEnrichmentItemStatusFilterState(status);
      setInformationEnrichmentItemPage(1);
    },
    [setInformationEnrichmentItemPage],
  );

  const loadBatchTaskDetails = useCallback(
    async (taskId: number) => {
      const requestId = latestBatchTaskDetailsRequestIdRef.current + 1;
      latestBatchTaskDetailsRequestIdRef.current = requestId;
      setBatchTaskDetailsLoading(true);
      try {
        const data = await listBatchTaskItems(taskId);
        if (latestBatchTaskDetailsRequestIdRef.current !== requestId) {
          return;
        }
        setSelectedBatchTaskItems(data);
        lastBatchTaskDetailsLoadErrorRef.current = null;
        try {
          const summary = await getBatchTaskSummary(taskId);
          if (latestBatchTaskDetailsRequestIdRef.current === requestId) {
            batchTaskSummarySignatureRef.current =
              buildBatchTaskSummarySignature(summary);
          }
        } catch {
          // Keep the previous signature; the next poll will retry and refresh
          // the full list if the summary moved on.
        }
      } catch (loadError) {
        if (latestBatchTaskDetailsRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error
            ? loadError.message
            : "加载批量任务详情失败";
        if (lastBatchTaskDetailsLoadErrorRef.current !== message) {
          notifyError("加载批量任务详情失败", message);
          lastBatchTaskDetailsLoadErrorRef.current = message;
        }
      } finally {
        if (latestBatchTaskDetailsRequestIdRef.current === requestId) {
          setBatchTaskDetailsLoading(false);
        }
      }
    },
    [notifyError],
  );

  const refreshBatchTaskDetailsIfSummaryChanged = useCallback(
    async (taskId: number) => {
      let summarySignature: string;
      try {
        const summary = await getBatchTaskSummary(taskId);
        summarySignature = buildBatchTaskSummarySignature(summary);
      } catch {
        return;
      }
      if (summarySignature === batchTaskSummarySignatureRef.current) {
        return;
      }
      await loadBatchTaskDetails(taskId);
    },
    [loadBatchTaskDetails],
  );

  const closeProfessorEditDialog = useCallback(() => {
    latestProfessorEditRequestIdRef.current += 1;
    setProfessorEditDialogOpen(false);
    setProfessorEditLoading(false);
    setProfessorEditProfessor(null);
  }, []);

  const openProfessorEditDialog = useCallback(
    async (item: BatchTaskItemDTO) => {
      const requestId = latestProfessorEditRequestIdRef.current + 1;
      latestProfessorEditRequestIdRef.current = requestId;
      setProfessorEditDialogOpen(true);
      setProfessorEditLoading(true);
      setProfessorEditProfessor(null);
      try {
        const professor = await getProfessor(item.professor_id);
        if (latestProfessorEditRequestIdRef.current !== requestId) {
          return;
        }
        setProfessorEditProfessor(professor);
      } catch (error) {
        if (latestProfessorEditRequestIdRef.current !== requestId) {
          return;
        }
        notifyError(
          "加载导师资料失败",
          error instanceof Error ? error.message : "加载导师资料失败",
        );
        closeProfessorEditDialog();
      } finally {
        if (latestProfessorEditRequestIdRef.current === requestId) {
          setProfessorEditLoading(false);
        }
      }
    },
    [closeProfessorEditDialog, notifyError],
  );

  const refreshAfterProfessorEdit = useCallback(
    async (professor: ProfessorManagementItemDTO) => {
      setBatchReviewThread((currentThread) => {
        if (!currentThread || currentThread.professor.id !== professor.id) {
          return currentThread;
        }
        return {
          ...currentThread,
          professor: {
            ...currentThread.professor,
            name: professor.name,
            email: professor.email,
            title: professor.title,
            university: professor.university,
            school: professor.school,
            department: professor.department,
            research_direction: professor.research_direction,
            recent_papers: professor.recent_papers,
            profile_url: professor.profile_url,
          },
        };
      });
      if (!selectedBatchTask) {
        return;
      }
      await Promise.all([
        loadBatchTaskDetails(selectedBatchTask.id),
        loadTasks(),
      ]);
    },
    [loadBatchTaskDetails, loadTasks, selectedBatchTask],
  );

  const loadCrawlJobSummary = useCallback(
    async (jobId: number) => {
      const requestId = latestCrawlJobSummaryRequestIdRef.current + 1;
      latestCrawlJobSummaryRequestIdRef.current = requestId;
      try {
        const job = await getCrawlJob(jobId);
        if (latestCrawlJobSummaryRequestIdRef.current !== requestId) {
          return;
        }
        setSelectedCrawlJob(job);
        lastCrawlJobDetailsLoadErrorRef.current = null;
      } catch (loadError) {
        if (latestCrawlJobSummaryRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error
            ? loadError.message
            : "加载抓取任务状态失败";
        if (lastCrawlJobDetailsLoadErrorRef.current !== message) {
          notifyError("加载抓取任务状态失败", message);
          lastCrawlJobDetailsLoadErrorRef.current = message;
        }
      }
    },
    [notifyError],
  );

  const loadCrawlJobDetails = useCallback(
    async (jobId: number, options?: { showLoading?: boolean }) => {
      const requestId = latestCrawlJobDetailsRequestIdRef.current + 1;
      latestCrawlJobDetailsRequestIdRef.current = requestId;
      if (options?.showLoading ?? true) {
        setCrawlJobDetailsLoading(true);
      }
      try {
        const data = await getCrawlJobDetails(jobId);
        if (latestCrawlJobDetailsRequestIdRef.current !== requestId) {
          return;
        }
        setSelectedCrawlJob(data.job);
        setCrawlJobPages(data.pages);
        setCrawlJobCandidates(data.candidates);
        setCrawlJobEvents(data.events);
        lastCrawlJobDetailsLoadErrorRef.current = null;
      } catch (loadError) {
        if (latestCrawlJobDetailsRequestIdRef.current !== requestId) {
          return;
        }
        const message =
          loadError instanceof Error
            ? loadError.message
            : "加载抓取任务详情失败";
        if (lastCrawlJobDetailsLoadErrorRef.current !== message) {
          notifyError("加载抓取任务详情失败", message);
          lastCrawlJobDetailsLoadErrorRef.current = message;
        }
      } finally {
        if (
          latestCrawlJobDetailsRequestIdRef.current === requestId &&
          (options?.showLoading ?? true)
        ) {
          setCrawlJobDetailsLoading(false);
        }
      }
    },
    [notifyError],
  );

  useEffect(() => {
    if (!pendingCrawlJobHandoff) {
      return;
    }
    const { token, data } = pendingCrawlJobHandoff;
    latestCrawlJobSummaryRequestIdRef.current += 1;
    latestCrawlJobDetailsRequestIdRef.current += 1;
    setTaskListViews((current) => ({
      ...current,
      crawl: data.job.deleted_at ? "trash" : "current",
    }));
    setActiveTab("crawl");
    setSelectedBatchTask(null);
    setSelectedMatchJob(null);
    setSelectedInformationEnrichmentJob(null);
    setResendDialogOpen(false);
    setSelectedCrawlJob(data.job);
    setCrawlJobPages(data.pages);
    setCrawlJobCandidates(data.candidates);
    setCrawlJobEvents(data.events);
    setCrawlJobDetailsLoading(false);
    lastCrawlJobDetailsLoadErrorRef.current = null;
    onCrawlHandoffApplied(token);
  }, [onCrawlHandoffApplied, pendingCrawlJobHandoff]);

  useEffect(() => {
    if (taskCenterSection !== "background" || activeTab !== "batch") {
      return undefined;
    }
    void loadTasks();
    const timer = window.setInterval(() => {
      void loadTasks({ showLoading: false });
    }, 10000);
    return () => window.clearInterval(timer);
  }, [activeTab, loadTasks, taskCenterSection]);

  useEffect(() => {
    if (batchReviewItemId === null || batchReviewOutreachTemplatesLoaded) {
      return undefined;
    }

    let ignore = false;
    const loadTemplates = async () => {
      setLoadingBatchReviewOutreachTemplates(true);
      try {
        const templates = await listOutreachTemplates(true);
        if (!ignore) {
          setBatchReviewOutreachTemplates(templates);
          setBatchReviewOutreachTemplatesLoaded(true);
        }
      } catch (error) {
        if (!ignore) {
          notifyError(
            "加载发信模板失败",
            error instanceof Error ? error.message : "加载发信模板失败",
          );
        }
      } finally {
        if (!ignore) {
          setLoadingBatchReviewOutreachTemplates(false);
        }
      }
    };

    void loadTemplates();
    return () => {
      ignore = true;
    };
  }, [
    batchReviewItemId,
    batchReviewOutreachTemplatesLoaded,
    notifyError,
  ]);

  useEffect(() => {
    setBatchPage((currentPage) =>
      Math.min(currentPage, getTotalPages(tasks.length, batchPageSize)),
    );
  }, [batchPageSize, setBatchPage, tasks.length]);

  useEffect(() => {
    setCrawlPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(displayedCrawlJobTotalCount, crawlPageSize),
      ),
    );
  }, [crawlPageSize, displayedCrawlJobTotalCount, setCrawlPage]);

  useEffect(() => {
    setMatchPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(matchAnalysisJobs.length, matchPageSize),
      ),
    );
  }, [matchAnalysisJobs.length, matchPageSize, setMatchPage]);

  useEffect(() => {
    setInformationEnrichmentPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(
          displayedInformationEnrichmentJobTotalCount,
          informationEnrichmentPageSize,
        ),
      ),
    );
  }, [
    displayedInformationEnrichmentJobTotalCount,
    informationEnrichmentPageSize,
    setInformationEnrichmentPage,
  ]);

  useEffect(() => {
    setMatchJobItemPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(matchJobItemTotalCount, matchJobItemPageSize),
      ),
    );
  }, [matchJobItemPageSize, matchJobItemTotalCount, setMatchJobItemPage]);

  useEffect(() => {
    setInformationEnrichmentItemPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(
          informationEnrichmentItemTotalCount,
          informationEnrichmentItemPageSize,
        ),
      ),
    );
  }, [
    informationEnrichmentItemPageSize,
    informationEnrichmentItemTotalCount,
    setInformationEnrichmentItemPage,
  ]);

  useEffect(() => {
    setCrawlEventPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(crawlExecutionLogEvents.length, crawlEventPageSize),
      ),
    );
  }, [crawlEventPageSize, crawlExecutionLogEvents.length, setCrawlEventPage]);

  useEffect(() => {
    setCrawlDetailPagePage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(crawlJobPages.length, crawlDetailPagePageSize),
      ),
    );
  }, [crawlDetailPagePageSize, crawlJobPages.length, setCrawlDetailPagePage]);

  useEffect(() => {
    setCrawlCandidatePage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(
          filteredCrawlJobCandidates.length,
          crawlCandidatePageSize,
        ),
      ),
    );
  }, [
    crawlCandidatePageSize,
    filteredCrawlJobCandidates.length,
    setCrawlCandidatePage,
  ]);

  useEffect(() => {
    setBatchSentItemPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(sentBatchTaskItems.length, batchSentItemPageSize),
      ),
    );
  }, [batchSentItemPageSize, sentBatchTaskItems.length, setBatchSentItemPage]);

  useEffect(() => {
    setBatchPendingItemPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(pendingBatchTaskItems.length, batchPendingItemPageSize),
      ),
    );
  }, [
    batchPendingItemPageSize,
    pendingBatchTaskItems.length,
    setBatchPendingItemPage,
  ]);

  useEffect(() => {
    setBatchGeneratingItemPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(
          generatingDraftBatchTaskItems.length,
          batchGeneratingItemPageSize,
        ),
      ),
    );
    setBatchDraftFailedItemPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(
          draftFailedBatchTaskItems.length,
          batchDraftFailedItemPageSize,
        ),
      ),
    );
    setBatchFailedItemPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(failedBatchTaskItems.length, batchFailedItemPageSize),
      ),
    );
    setBatchReviewItemPage((currentPage) =>
      Math.min(
        currentPage,
        getTotalPages(batchReviewQueueItems.length, batchReviewItemPageSize),
      ),
    );
  }, [
    batchDraftFailedItemPageSize,
    batchFailedItemPageSize,
    batchGeneratingItemPageSize,
    batchReviewItemPageSize,
    batchReviewQueueItems.length,
    draftFailedBatchTaskItems.length,
    failedBatchTaskItems.length,
    generatingDraftBatchTaskItems.length,
    setBatchDraftFailedItemPage,
    setBatchFailedItemPage,
    setBatchGeneratingItemPage,
    setBatchReviewItemPage,
  ]);

  useEffect(() => {
    if (
      taskCenterSection !== "background" ||
      activeTab === "crawl" ||
      crawlJobsPreloadedRef.current
    ) {
      return;
    }
    crawlJobsPreloadedRef.current = true;
    void loadCrawlJobs({ showLoading: false });
  }, [activeTab, loadCrawlJobs, taskCenterSection]);

  useEffect(() => {
    if (
      taskCenterSection !== "background" ||
      informationEnrichmentJobsPreloadedRef.current
    ) {
      return;
    }
    informationEnrichmentJobsPreloadedRef.current = true;
    void loadInformationEnrichmentJobs({ showLoading: false });
  }, [loadInformationEnrichmentJobs, taskCenterSection]);

  useEffect(() => {
    if (taskCenterSection !== "background") {
      return;
    }
    if (activeTab === "batch") {
      return;
    }
    if (!tasksRequestKey) {
      batchTasksPreloadedKeyRef.current = null;
      void loadTasks();
      return;
    }
    if (batchTasksPreloadedKeyRef.current === tasksRequestKey) {
      return;
    }
    batchTasksPreloadedKeyRef.current = tasksRequestKey;
    void loadTasks();
  }, [activeTab, loadTasks, taskCenterSection, tasksRequestKey]);

  useEffect(() => {
    if (
      taskCenterSection !== "background" ||
      !Number.isInteger(requestedBatchTaskId) ||
      requestedBatchTaskId <= 0
    ) {
      return;
    }
    if (activeTab !== "batch") {
      setActiveTab("batch");
      return;
    }
    const requestedTask = tasks.find((task) => task.id === requestedBatchTaskId);
    if (!requestedTask) {
      return;
    }
    setSelectedBatchTask(requestedTask);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("batch_task_id");
      return next;
    }, { replace: true });
  }, [
    activeTab,
    requestedBatchTaskId,
    setSearchParams,
    taskCenterSection,
    tasks,
  ]);

  useEffect(() => {
    if (taskCenterSection !== "background") {
      return;
    }
    if (!tasksRequestKey) {
      matchJobsPreloadedKeyRef.current = null;
      void loadMatchAnalysisJobs({ showLoading: false });
      return;
    }
    if (matchJobsPreloadedKeyRef.current === tasksRequestKey) {
      return;
    }
    matchJobsPreloadedKeyRef.current = tasksRequestKey;
    void loadMatchAnalysisJobs({ showLoading: false });
  }, [loadMatchAnalysisJobs, taskCenterSection, tasksRequestKey]);

  useEffect(() => {
    if (taskCenterSection !== "background" || activeTab !== "crawl") {
      return undefined;
    }
    crawlJobsPreloadedRef.current = true;
    void loadCrawlJobs();
    const timer = window.setInterval(() => {
      void loadCrawlJobs({ showLoading: false });
    }, CRAWL_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activeTab, loadCrawlJobs, taskCenterSection]);

  useEffect(() => {
    if (taskCenterSection !== "background" || activeTab !== "match") {
      return undefined;
    }
    void loadMatchAnalysisJobs();
    const timer = window.setInterval(() => {
      void loadMatchAnalysisJobs({ showLoading: false });
    }, CRAWL_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activeTab, loadMatchAnalysisJobs, taskCenterSection]);

  useEffect(() => {
    if (taskCenterSection !== "background" || activeTab !== "enrichment") {
      return undefined;
    }
    void loadInformationEnrichmentJobs();
    const timer = window.setInterval(() => {
      void loadInformationEnrichmentJobs({ showLoading: false });
    }, CRAWL_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [
    activeTab,
    loadInformationEnrichmentJobs,
    taskCenterSection,
  ]);

  useEffect(() => {
    if (!selectedBatchTaskId) {
      return undefined;
    }
    lastBatchTaskDetailsLoadErrorRef.current = null;
    batchTaskSummarySignatureRef.current = null;
    void loadBatchTaskDetails(selectedBatchTaskId);
    if (selectedBatchTaskStatus !== "running") {
      return () => {
        latestBatchTaskDetailsRequestIdRef.current += 1;
      };
    }
    const timer = window.setInterval(() => {
      void refreshBatchTaskDetailsIfSummaryChanged(selectedBatchTaskId);
    }, BATCH_TASK_DETAILS_REFRESH_INTERVAL_MS);
    return () => {
      latestBatchTaskDetailsRequestIdRef.current += 1;
      window.clearInterval(timer);
    };
  }, [
    loadBatchTaskDetails,
    refreshBatchTaskDetailsIfSummaryChanged,
    selectedBatchTaskId,
    selectedBatchTaskStatus,
  ]);

  useEffect(() => {
    if (selectedBatchTask?.id === undefined || !hasActiveBatchRestoreDeadline) {
      return undefined;
    }
    setBatchSendActionNowMs(Date.now());
    const timer = window.setInterval(() => {
      setBatchSendActionNowMs(Date.now());
    }, 1000);
    return () => window.clearInterval(timer);
  }, [hasActiveBatchRestoreDeadline, selectedBatchTask?.id]);

  useEffect(() => {
    if (previousSelectedBatchTaskIdRef.current === selectedBatchTask?.id) {
      return;
    }
    previousSelectedBatchTaskIdRef.current = selectedBatchTask?.id;
    setBatchSentItemPage(1);
    setBatchPendingItemPage(1);
    setBatchGeneratingItemPage(1);
    setBatchDraftFailedItemPage(1);
    setBatchFailedItemPage(1);
    setBatchReviewItemPage(1);
  }, [
    selectedBatchTask?.id,
    setBatchDraftFailedItemPage,
    setBatchFailedItemPage,
    setBatchGeneratingItemPage,
    setBatchPendingItemPage,
    setBatchReviewItemPage,
    setBatchSentItemPage,
  ]);

  useEffect(() => {
    if (batchReviewItemId === null) {
      return;
    }
    const itemIndex = batchReviewQueueItems.findIndex(
      (item) => item.id === batchReviewItemId,
    );
    if (itemIndex >= 0) {
      setBatchReviewItemPage(
        Math.floor(itemIndex / batchReviewItemPageSize) + 1,
      );
    }
  }, [
    batchReviewItemId,
    batchReviewItemPageSize,
    batchReviewQueueItems,
    setBatchReviewItemPage,
  ]);

  useEffect(() => {
    setMatchJobItemStatusFilterState("all");
    setMatchJobItemPage(1);
    setSelectedMatchJobItems([]);
    setMatchJobItemTotalCount(0);
  }, [selectedMatchJob?.id, setMatchJobItemPage]);

  useEffect(() => {
    if (!selectedMatchJob) {
      return undefined;
    }
    lastMatchJobDetailsLoadErrorRef.current = null;
    void loadMatchJobDetails(selectedMatchJob.id);
    const timer = window.setInterval(() => {
      void loadMatchJobDetails(selectedMatchJob.id);
    }, CRAWL_DETAILS_REFRESH_INTERVAL_MS);
    return () => {
      latestMatchJobDetailsRequestIdRef.current += 1;
      window.clearInterval(timer);
    };
  }, [loadMatchJobDetails, selectedMatchJob]);

  useEffect(() => {
    setInformationEnrichmentItemStatusFilterState("all");
    setInformationEnrichmentItemPage(1);
    setSelectedInformationEnrichmentItems([]);
    setInformationEnrichmentItemTotalCount(0);
  }, [selectedInformationEnrichmentJob?.id, setInformationEnrichmentItemPage]);

  useEffect(() => {
    if (!selectedInformationEnrichmentJob) {
      return undefined;
    }
    lastInformationEnrichmentDetailsLoadErrorRef.current = null;
    void loadInformationEnrichmentDetails(selectedInformationEnrichmentJob.id);
    const timer = window.setInterval(() => {
      void loadInformationEnrichmentDetails(selectedInformationEnrichmentJob.id);
    }, CRAWL_DETAILS_REFRESH_INTERVAL_MS);
    return () => {
      latestInformationEnrichmentDetailsRequestIdRef.current += 1;
      window.clearInterval(timer);
    };
  }, [loadInformationEnrichmentDetails, selectedInformationEnrichmentJob]);

  useEffect(() => {
    if (!selectedCrawlJobId) {
      return undefined;
    }
    lastCrawlJobDetailsLoadErrorRef.current = null;
    void loadCrawlJobDetails(selectedCrawlJobId, { showLoading: true });
    const summaryTimer = window.setInterval(() => {
      void loadCrawlJobSummary(selectedCrawlJobId);
    }, CRAWL_DETAILS_REFRESH_INTERVAL_MS);
    const contentTimer = window.setInterval(() => {
      void loadCrawlJobDetails(selectedCrawlJobId, { showLoading: false });
    }, CRAWL_DETAIL_CONTENT_REFRESH_INTERVAL_MS);
    return () => {
      latestCrawlJobSummaryRequestIdRef.current += 1;
      latestCrawlJobDetailsRequestIdRef.current += 1;
      window.clearInterval(summaryTimer);
      window.clearInterval(contentTimer);
    };
  }, [loadCrawlJobDetails, loadCrawlJobSummary, selectedCrawlJobId]);

  useEffect(() => {
    setSelectedCrawlCandidateIds((currentIds) =>
      pruneSelectedCandidateIds(currentIds, crawlJobCandidates),
    );
  }, [crawlJobCandidates]);

  useEffect(() => {
    if (previousSelectedCrawlJobIdRef.current === selectedCrawlJobId) {
      return;
    }
    previousSelectedCrawlJobIdRef.current = selectedCrawlJobId;
    setSelectedCrawlCandidateIds([]);
    setCrawlCandidateFilters(createDefaultCrawlCandidateFilters());
    setCrawlCandidateInformationFiltersOpen(false);
    setCrawlJobApproveLoading(false);
    setCrawlJobEnrichLoading(false);
    setResumingCrawlJobReviewId(null);
    setSelectedCandidateDetail(null);
    setCandidateEditForm(null);
    setCandidateUpdateLoading(false);
    setCrawlEventPage(1);
    setCrawlDetailPagePage(1);
    setCrawlCandidatePage(1);
  }, [
    selectedCrawlJobId,
    setCrawlCandidatePage,
    setCrawlDetailPagePage,
    setCrawlEventPage,
  ]);

  const handleAction = async (
    taskId: number,
    action: "pause" | "resume" | "stop",
  ) => {
    const diagnosticData = { taskId, action };
    try {
      if (action === "pause") {
        safeRecordUserAction({
          eventName: "tasks.batch_task_pause_submitted",
          data: diagnosticData,
        });
        await pauseBatchTask(taskId);
      } else if (action === "resume") {
        safeRecordUserAction({
          eventName: "tasks.batch_task_resume_submitted",
          data: diagnosticData,
        });
        try {
          await resumeBatchTask(taskId);
        } catch (resumeError) {
          if (
            !(resumeError instanceof ApiError) ||
            resumeError.code !== "CAMPAIGN_LLM_PROFILE_REPLACEMENT_REQUIRED"
          ) {
            throw resumeError;
          }
          if (!selectedLlmProfile) {
            throw new Error(
              "原模型配置已删除。请先在顶部选择一个可用模型，再继续活动。",
            );
          }
          const confirmed = await confirm({
            title: "原模型配置已删除",
            description: `待生成的 AI 草稿无法继续。是否改用当前选择的“${selectedLlmProfile.name}”（${selectedLlmProfile.model_name}）？已生成和已发送的历史内容不会改变。`,
            confirmLabel: "改用此模型并继续",
            cancelLabel: "暂不继续",
            tone: "danger",
          });
          if (!confirmed) {
            return;
          }
          await resumeBatchTask(taskId, selectedLlmProfile.id);
        }
      } else {
        const confirmed = await confirm({
          title: "确认终止这个任务？",
          description: "终止后当前批次不会继续推进生成、排程和发送。",
          confirmLabel: "确认终止",
          cancelLabel: "先保留",
          tone: "danger",
        });
        if (!confirmed) {
          return;
        }
        safeRecordUserAction({
          eventName: "tasks.batch_task_stop_submitted",
          data: diagnosticData,
        });
        await stopBatchTask(taskId);
      }
      safeRecordUserAction({
        eventName: `tasks.batch_task_${action}_succeeded`,
        data: diagnosticData,
      });
      await loadTasks();
    } catch (actionError) {
      safeRecordUserAction({
        eventName: `tasks.batch_task_${action}_failed`,
        data: diagnosticData,
        level: "error",
      });
      const message =
        actionError instanceof Error ? actionError.message : "任务操作失败";
      notifyError("任务操作失败", message);
    }
  };

  const handlePauseCrawlJob = async (jobId: number) => {
    const confirmed = await confirm({
      title: "确认暂停这个抓取任务？",
      description: "保留当前结果，可随时继续。",
      confirmLabel: "确认暂停",
      cancelLabel: "先不暂停",
    });
    if (!confirmed) {
      return;
    }

    const diagnosticData = { jobId };
    safeRecordUserAction({
      eventName: "tasks.crawl_job_pause_submitted",
      data: diagnosticData,
    });
    setPausingCrawlJobId(jobId);
    try {
      await pauseCrawlJob(jobId);
      safeRecordUserAction({
        eventName: "tasks.crawl_job_pause_succeeded",
        data: diagnosticData,
      });
      notifySuccess("抓取任务已暂停", "已保留当前结果");
      await loadCrawlJobs();
      if (selectedCrawlJobId === jobId) {
        await loadCrawlJobDetails(jobId, { showLoading: false });
      }
    } catch (actionError) {
      safeRecordUserAction({
        eventName: "tasks.crawl_job_pause_failed",
        data: diagnosticData,
        level: "error",
      });
      const message =
        actionError instanceof Error ? actionError.message : "抓取任务暂停失败";
      notifyError("抓取任务操作失败", message);
    } finally {
      setPausingCrawlJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const ensureSelectedLlmProfile = () => {
    if (selectedLlmProfileId !== null) {
      return selectedLlmProfileId;
    }
    notifyError("请先选择模型配置", "选择模型后再继续。");
    return null;
  };

  const handleResumeCrawlJob = async (jobId: number) => {
    const llmProfileId = ensureSelectedLlmProfile();
    if (llmProfileId === null) {
      return;
    }
    const diagnosticData = { jobId };
    safeRecordUserAction({
      eventName: "tasks.crawl_job_resume_submitted",
      data: diagnosticData,
    });
    setResumingCrawlJobId(jobId);
    try {
      const job = await resumeCrawlJob(jobId, llmProfileId);
      trackCrawlJob(job);
      safeRecordUserAction({
        eventName: "tasks.crawl_job_resume_succeeded",
        data: diagnosticData,
      });
      notifySuccess("抓取任务已继续", "任务已重新进入队列，稍后开始执行");
      await loadCrawlJobs();
      if (selectedCrawlJobId === jobId) {
        await loadCrawlJobDetails(jobId, { showLoading: false });
      }
    } catch (actionError) {
      safeRecordUserAction({
        eventName: "tasks.crawl_job_resume_failed",
        data: diagnosticData,
        level: "error",
      });
      const message =
        actionError instanceof Error ? actionError.message : "抓取任务继续失败";
      notifyError("抓取任务操作失败", message);
    } finally {
      setResumingCrawlJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const handleCancelCrawlJob = async (jobId: number) => {
    const confirmed = await confirm({
      title: "确认取消这个抓取任务？",
      description: "停止抓取并保留已有结果。",
      confirmLabel: "取消抓取",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    const diagnosticData = { jobId };
    safeRecordUserAction({
      eventName: "tasks.crawl_job_cancel_submitted",
      data: diagnosticData,
    });
    try {
      await cancelCrawlJob(jobId);
      safeRecordUserAction({
        eventName: "tasks.crawl_job_cancel_succeeded",
        data: diagnosticData,
      });
      await loadCrawlJobs();
    } catch (actionError) {
      safeRecordUserAction({
        eventName: "tasks.crawl_job_cancel_failed",
        data: diagnosticData,
        level: "error",
      });
      const message =
        actionError instanceof Error ? actionError.message : "抓取任务操作失败";
      notifyError("抓取任务操作失败", message);
    }
  };

  const handleRetryCrawlJob = async (jobId: number) => {
    const llmProfileId = ensureSelectedLlmProfile();
    if (llmProfileId === null) {
      return;
    }

    const confirmed = await confirm({
      title: "确认重新抓取任务？",
      description:
        "将清空本任务已有页面、候选导师和抓取轨迹后重新抓取。已导入的导师、历史运行记录和 Token 用量会保留。",
      confirmLabel: "确认重新抓取",
      cancelLabel: "暂不处理",
    });
    if (!confirmed) {
      return;
    }

    const diagnosticData = { jobId };
    safeRecordUserAction({
      eventName: "tasks.crawl_job_retry_submitted",
      data: diagnosticData,
    });
    setRetryingCrawlJobId(jobId);
    try {
      const job = await retryCrawlJob(jobId, {
        clear_existing_data: true,
        llmProfileId,
      });
      trackCrawlJob(job);
      safeRecordUserAction({
        eventName: "tasks.crawl_job_retry_succeeded",
        data: diagnosticData,
      });
      notifySuccess("抓取任务已重新加入队列", "任务已进入队列，稍后开始执行");
      await loadCrawlJobs();
      if (selectedCrawlJobId === jobId) {
        await loadCrawlJobDetails(jobId, { showLoading: false });
      }
    } catch (actionError) {
      safeRecordUserAction({
        eventName: "tasks.crawl_job_retry_failed",
        data: diagnosticData,
        level: "error",
      });
      const message =
        actionError instanceof Error ? actionError.message : "重新抓取任务失败";
      notifyError("抓取任务操作失败", message);
    } finally {
      setRetryingCrawlJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const handleResumeCrawlJobReview = async (jobId: number) => {
    const confirmed = await confirm({
      title: "确认转入待审核？",
      description: "不重新抓取，仅将已有候选转入待审核。",
      confirmLabel: "转入待审核",
      cancelLabel: "先保留",
    });
    if (!confirmed) {
      return;
    }

    setResumingCrawlJobReviewId(jobId);
    try {
      await resumeCrawlJobReview(jobId);
      notifySuccess("已转入待审核", "可以继续选择候选并补全信息。");
      await loadCrawlJobs({ showLoading: false });
      await loadCrawlJobDetails(jobId, { showLoading: false });
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "转入待审核失败";
      notifyError("转入待审核失败", message);
    } finally {
      setResumingCrawlJobReviewId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const handleCancelMatchJob = async (jobId: number) => {
    const confirmed = await confirm({
      title: "确认取消这个匹配分析任务？",
      description: "已开始的单项分析会在安全点结束，未开始的导师会被取消。",
      confirmLabel: "取消任务",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    setCancelingMatchJobId(jobId);
    try {
      const result = await cancelMatchAnalysisJob(jobId);
      setMatchAnalysisJobs((currentJobs) =>
        currentJobs.map((job) => (job.id === jobId ? result.job : job)),
      );
      notifySuccess("已请求取消", "匹配分析任务会在安全点停止。");
      if (selectedMatchJob?.id === jobId) {
        setSelectedMatchJob(result.job);
      }
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "取消匹配分析任务失败";
      notifyError("取消匹配分析任务失败", message);
    } finally {
      setCancelingMatchJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const handleRetryMatchJob = async (jobId: number) => {
    setRetryingMatchJobId(jobId);
    try {
      const job = await retryFailedMatchAnalysisJob(jobId);
      setMatchAnalysisJobs((currentJobs) => [job, ...currentJobs]);
      trackMatchAnalysisJob(job);
      notifySuccess("已创建重试任务", "失败项已重新加入后台匹配分析队列。");
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "重试匹配分析任务失败";
      notifyError("重试匹配分析任务失败", message);
    } finally {
      setRetryingMatchJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const handleCancelInformationEnrichmentJob = async (jobId: number) => {
    const confirmed = await confirm({
      title: "确认取消这个信息补全任务？",
      description: "未完成的导师会被取消，已经补全并写入的信息会保留。",
      confirmLabel: "取消任务",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    setCancelingInformationEnrichmentJobId(jobId);
    try {
      const result = await cancelProfessorInformationEnrichmentJob(jobId);
      latestInformationEnrichmentJobsRequestIdRef.current += 1;
      setInformationEnrichmentJobsLoading(false);
      setInformationEnrichmentJobs((currentJobs) =>
        currentJobs.map((job) => (job.id === jobId ? result.job : job)),
      );
      if (selectedInformationEnrichmentJob?.id === jobId) {
        setSelectedInformationEnrichmentJob(result.job);
      }
      stopTrackingInformationEnrichmentJob(jobId);
      notifySuccess("已取消信息补全任务", "已写入的导师信息不会回退。");
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "取消信息补全任务失败";
      notifyError("取消信息补全任务失败", message);
    } finally {
      setCancelingInformationEnrichmentJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const handleRetryInformationEnrichmentJob = async (jobId: number) => {
    setRetryingInformationEnrichmentJobId(jobId);
    try {
      const job = await retryFailedProfessorInformationEnrichmentJob(jobId);
      trackInformationEnrichmentJob(job);
      notifySuccess("已创建重试任务", "失败或取消项已重新加入信息补全队列。");
      await loadInformationEnrichmentJobs({ showLoading: false });
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "重试信息补全任务失败";
      notifyError("重试信息补全任务失败", message);
    } finally {
      setRetryingInformationEnrichmentJobId((currentJobId) =>
        currentJobId === jobId ? null : currentJobId,
      );
    }
  };

  const updateCrawlCandidateFilters = (
    patch: Partial<CrawlCandidateFilters>,
  ) => {
    setCrawlCandidateFilters((currentFilters) => ({
      ...currentFilters,
      ...patch,
    }));
    setCrawlCandidatePage(1);
  };

  const updateCrawlCandidateInformationCondition = (
    field: CrawlCandidateInformationField,
    condition: CrawlCandidateInformationCondition | "any",
  ) => {
    setCrawlCandidateFilters((currentFilters) => {
      const informationConditions = {
        ...currentFilters.informationConditions,
      };
      if (condition === "any") {
        delete informationConditions[field];
      } else {
        informationConditions[field] = condition;
      }
      return {
        ...currentFilters,
        informationConditions,
        informationMatchMode:
          Object.keys(informationConditions).length < 2
            ? "all"
            : currentFilters.informationMatchMode,
      };
    });
    setCrawlCandidatePage(1);
  };

  const resetCrawlCandidateFilters = () => {
    setCrawlCandidateFilters(createDefaultCrawlCandidateFilters());
    setCrawlCandidatePage(1);
  };

  const handleToggleFilteredCrawlCandidateSelection = () => {
    setSelectedCrawlCandidateIds((currentIds) => {
      const nextIds = new Set(currentIds);
      const shouldDeselect =
        filteredReviewableCrawlCandidateIds.length > 0 &&
        filteredReviewableCrawlCandidateIds.every((candidateId) =>
          nextIds.has(candidateId),
        );

      filteredReviewableCrawlCandidateIds.forEach((candidateId) => {
        if (shouldDeselect) {
          nextIds.delete(candidateId);
        } else {
          nextIds.add(candidateId);
        }
      });
      return Array.from(nextIds);
    });
  };

  const handleToggleCrawlCandidateSelection = (candidateId: number) => {
    if (!reviewableCrawlCandidateIds.includes(candidateId)) {
      return;
    }

    setSelectedCrawlCandidateIds((currentIds) =>
      currentIds.includes(candidateId)
        ? currentIds.filter((id) => id !== candidateId)
        : [...currentIds, candidateId],
    );
  };

  const handleApproveSelectedCrawlCandidates = async () => {
    if (
      !selectedCrawlJobId ||
      selectedImportableCrawlCandidateIds.length === 0
    ) {
      return;
    }

    const approveDescription =
      selectedCrawlJob?.status === "canceled"
        ? "通过后，这些候选导师会写入导师库，当前抓取任务会保留已取消状态。"
        : selectedCrawlJob?.status === "partially_completed"
          ? "通过后会导入所选候选，任务中剩余待审核候选仍可继续处理。"
          : "通过后，这些候选导师会写入导师库；如仍有待审核候选，任务会标记为部分已导入。";
    const skippedMissingEmailDescription =
      selectedCrawlCandidateIdsWithoutEmail.length > 0
        ? ` 已选中的 ${selectedCrawlCandidateIdsWithoutEmail.length} 位无邮箱候选不会导入，可先使用补全功能。`
        : "";

    const confirmed = await confirm({
      title: `确认通过并导入这 ${selectedImportableCrawlCandidateIds.length} 位候选导师吗？`,
      description: `${approveDescription}${skippedMissingEmailDescription}`,
      confirmLabel: "确认导入",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    setCrawlJobApproveLoading(true);
    try {
      const result = await approveCrawlCandidates(
        selectedCrawlJobId,
        selectedImportableCrawlCandidateIds,
      );
      setSelectedCrawlCandidateIds([]);
      notifySuccess("审核完成", result.message);
      await loadCrawlJobs({ showLoading: false });
      await loadCrawlJobDetails(selectedCrawlJobId, { showLoading: false });
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "审核导入候选导师失败";
      notifyError("审核导入候选导师失败", message);
    } finally {
      setCrawlJobApproveLoading(false);
    }
  };

  const handleEnrichSelectedCrawlCandidates = async () => {
    if (
      !selectedCrawlJobId ||
      selectedReviewableCrawlCandidateIds.length === 0
    ) {
      return;
    }

    const llmProfileId = ensureSelectedLlmProfile();
    if (llmProfileId === null) {
      return;
    }

    setCrawlJobEnrichLoading(true);
    try {
      const result = await enrichCrawlCandidates(
        selectedCrawlJobId,
        selectedReviewableCrawlCandidateIds,
        llmProfileId,
      );
      if (result.operation_id) {
        trackCrawlCandidateEnrichment(selectedCrawlJobId, result.operation_id);
      }
      notifySuccess("候选信息补全已开始", result.message);
      await loadCrawlJobs({ showLoading: false });
      await loadCrawlJobDetails(selectedCrawlJobId, { showLoading: false });
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "补全候选导师信息失败";
      notifyError("补全候选导师信息失败", message);
    } finally {
      setCrawlJobEnrichLoading(false);
    }
  };

  const handleStartCandidateEdit = () => {
    if (
      !selectedCandidateDetail ||
      selectedCandidateDetail.review_status !== "pending" ||
      !selectedCrawlJobCanReview
    ) {
      return;
    }
    setCandidateEditForm(toCrawlCandidateEditForm(selectedCandidateDetail));
  };

  const handleCancelCandidateEdit = () => {
    if (candidateUpdateLoading) {
      return;
    }
    setCandidateEditForm(null);
  };

  const handleCandidateEditFieldChange = (
    field: keyof CrawlCandidateEditForm,
    value: string,
  ) => {
    setCandidateEditForm((currentForm) =>
      currentForm ? { ...currentForm, [field]: value } : currentForm,
    );
  };

  const handleSaveCandidateEdit = async (
    event: FormEvent<HTMLFormElement>,
  ) => {
    event.preventDefault();
    if (
      !selectedCandidateDetail ||
      !candidateEditForm ||
      candidateUpdateLoading
    ) {
      return;
    }
    if (
      selectedCandidateDetail.review_status !== "pending" ||
      !selectedCrawlJobCanReview
    ) {
      notifyError("无法保存导师信息", "该候选导师已不在待审核状态，请刷新任务后重试。");
      return;
    }

    const payload = toCrawlCandidateUpdatePayload(
      selectedCandidateDetail,
      candidateEditForm,
    );
    if (!payload.name) {
      notifyError("无法保存导师信息", "导师姓名不能为空。");
      return;
    }

    setCandidateUpdateLoading(true);
    try {
      const updatedCandidate = await updateCrawlCandidate(
        selectedCandidateDetail.id,
        payload,
      );
      setCrawlJobCandidates((currentCandidates) =>
        currentCandidates.map((candidate) =>
          candidate.id === updatedCandidate.id ? updatedCandidate : candidate,
        ),
      );
      setSelectedCandidateDetail(updatedCandidate);
      setCandidateEditForm(null);
      notifySuccess("导师信息已保存", "后续补全仅填写空缺字段。");
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "保存候选导师信息失败";
      notifyError("保存候选导师信息失败", message);
    } finally {
      setCandidateUpdateLoading(false);
    }
  };

  const closeCrawlJobDetails = () => {
    latestCrawlJobDetailsRequestIdRef.current += 1;
    setSelectedCrawlJob(null);
    setCrawlJobPages([]);
    setCrawlJobCandidates([]);
    setCrawlJobEvents([]);
    setSelectedCrawlCandidateIds([]);
    setCrawlCandidateFilters(createDefaultCrawlCandidateFilters());
    setCrawlCandidateInformationFiltersOpen(false);
    setCrawlJobApproveLoading(false);
    setSelectedCandidateDetail(null);
    setCandidateEditForm(null);
    setCandidateUpdateLoading(false);
    setCrawlEventPage(1);
    setCrawlDetailPagePage(1);
    setCrawlCandidatePage(1);
    setCrawlJobDetailsLoading(false);
    lastCrawlJobDetailsLoadErrorRef.current = null;
  };

  const resetBatchDraftReview = () => {
    latestBatchReviewRequestIdRef.current += 1;
    setBatchReviewItemId(null);
    setBatchReviewThread(null);
    setBatchReviewLoading(false);
    setBatchReviewItemActions({});
    setBatchReviewSubject("");
    setBatchReviewContentText("");
    setBatchReviewContentHtml("");
    setBatchReviewSelectedMaterialIds([]);
  };

  const syncBatchDraftReview = (thread: WorkspaceThreadDTO) => {
    const draft = getBatchReviewDraft(thread);
    setBatchReviewThread(thread);
    setBatchReviewSubject(draft.subject);
    setBatchReviewContentText(draft.text);
    setBatchReviewContentHtml(draft.html);
    setBatchReviewSelectedMaterialIds(draft.selectedMaterialIds);
  };

  const ensureBatchReviewThreadMatchesItem = (
    thread: WorkspaceThreadDTO,
    item: BatchTaskItemDTO,
    task: BatchTaskCardDTO,
  ) => {
    if (
      thread.current_task.id !== item.id ||
      thread.current_task.batch_task_id !== task.id
    ) {
      throw new Error("草稿任务与当前批量任务不一致，请刷新后重试");
    }
  };

  const openBatchDraftReview = async (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return;
    }
    if (
      batchReviewThread?.current_task.id === item.id &&
      batchReviewItemId === item.id
    ) {
      if (batchReviewLoading) {
        latestBatchReviewRequestIdRef.current += 1;
        setBatchReviewLoading(false);
      }
      return;
    }

    const isSwitchingItem = batchReviewThread !== null;
    const requestId = latestBatchReviewRequestIdRef.current + 1;
    latestBatchReviewRequestIdRef.current = requestId;
    if (!isSwitchingItem) {
      setBatchReviewItemId(item.id);
    }
    setBatchReviewLoading(true);
    try {
      const thread = await getBatchTaskItemThread(selectedBatchTask.id, item.id);
      if (latestBatchReviewRequestIdRef.current !== requestId) {
        return;
      }
      ensureBatchReviewThreadMatchesItem(thread, item, selectedBatchTask);
      setBatchReviewItemId(item.id);
      syncBatchDraftReview(thread);
    } catch (actionError) {
      if (latestBatchReviewRequestIdRef.current !== requestId) {
        return;
      }
      const message =
        actionError instanceof Error ? actionError.message : "加载草稿失败";
      notifyError("加载草稿失败", message);
      if (!isSwitchingItem) {
        setBatchReviewItemId(null);
        setBatchReviewThread(null);
      }
    } finally {
      if (latestBatchReviewRequestIdRef.current === requestId) {
        setBatchReviewLoading(false);
      }
    }
  };

  const handleBatchReviewContentChange = (value: RichEmailValue) => {
    setBatchReviewContentHtml(value.html);
    setBatchReviewContentText(value.text);
  };

  const buildBatchReviewPayload = () => ({
    subject: batchReviewSubject.trim() || null,
    body_text:
      batchReviewContentText.trim() ||
      deriveBatchReviewText("", batchReviewContentHtml),
    body_html: batchReviewContentHtml || null,
    selected_material_ids: batchReviewSelectedMaterialIds,
  });
  const batchReviewAttachmentTotalBytes = getSelectedAttachmentTotalBytes(
    batchReviewThread?.material_options ?? [],
    batchReviewSelectedMaterialIds,
  );

  const setBatchReviewItemAction = (
    itemId: number,
    type: BatchReviewItemActionType,
  ) => {
    setBatchReviewItemActions((current) => ({ ...current, [itemId]: type }));
  };

  const clearBatchReviewItemAction = (
    itemId: number,
    type: BatchReviewItemActionType,
  ) => {
    setBatchReviewItemActions((current) => {
      if (current[itemId] !== type) {
        return current;
      }
      const next = { ...current };
      delete next[itemId];
      return next;
    });
  };

  const handleApplyBatchReviewOutreachTemplate = async (templateId: number) => {
    const itemId = batchReviewItemId;
    if (!selectedBatchTask || !activeBatchReviewItem || itemId === null) {
      return;
    }

    const selectedTemplateSummary = batchReviewOutreachTemplates.find(
      (template) => template.id === templateId && !template.archived_at,
    );
    if (!selectedTemplateSummary) {
      notifyError("套用模板失败", "所选模板已不可用，请刷新后重试。");
      return;
    }

    const confirmed = await confirm({
      title: "用模板替换当前草稿？",
      description: `将用“${selectedTemplateSummary.name}”的最新内容替换当前主题和正文，现有草稿不会保留。`,
      confirmLabel: "套用并替换",
      cancelLabel: "取消",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    setBatchReviewItemAction(itemId, "template");
    try {
      const latestTemplate = await getOutreachTemplate(templateId);
      if (latestTemplate.archived_at) {
        throw new Error("所选模板已被删除，不能重新套用。");
      }
      setBatchReviewOutreachTemplates((templates) =>
        templates.some((template) => template.id === latestTemplate.id)
          ? templates.map((template) =>
              template.id === latestTemplate.id ? latestTemplate : template,
            )
          : [...templates, latestTemplate],
      );

      const thread = await updateBatchTaskItemOutreachConfig(
        selectedBatchTask.id,
        itemId,
        {
          outreach_generation_mode: latestTemplate.recommended_generation_mode,
          outreach_template_id: latestTemplate.id,
          outreach_template_subject: latestTemplate.subject,
          outreach_template_body_text: latestTemplate.body_text,
          outreach_template_body_html: latestTemplate.body_html,
        },
      );
      ensureBatchReviewThreadMatchesItem(
        thread,
        activeBatchReviewItem,
        selectedBatchTask,
      );
      setSelectedBatchTaskItems((current) =>
        current.map((item) => {
          if (item.id !== itemId) {
            return item;
          }
          const status = thread.current_task.status ?? item.status;
          return {
            ...item,
            status,
            draft_generation_source:
              thread.current_task.draft_generation_source,
            draft_fallback_reason: thread.current_task.draft_fallback_reason,
            next_action:
              status === "review_required" ? "review_draft" : item.next_action,
          };
        }),
      );
      setBatchReviewItemId((currentItemId) => {
        if (currentItemId === itemId) {
          syncBatchDraftReview(thread);
        }
        return currentItemId;
      });
      notifySuccess(`已套用“${latestTemplate.name}”`);
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "重新套用模板失败";
      notifyError("套用模板失败", message);
    } finally {
      clearBatchReviewItemAction(itemId, "template");
    }
  };

  const handleRegenerateBatchDraft = async () => {
    const itemId = batchReviewItemId;
    if (!selectedBatchTask || !activeBatchReviewItem || itemId === null) {
      return;
    }
    const usesRenderedTemplateDraft =
      batchReviewThread?.current_task.draft?.source === "template";
    const usesTemplateDraft =
      usesRenderedTemplateDraft ||
      batchReviewThread?.current_task.draft_generation_source ===
        "template_fallback" ||
      (!batchReviewThread &&
        activeBatchReviewItem.draft_generation_source === "template_fallback");
    if (
      usesTemplateDraft &&
      !batchReviewThread?.professor.research_direction?.trim()
    ) {
      notifyError(
        "无法使用 AI 改写",
        "该导师缺少研究方向。当前模板草稿不会受到影响，你可以直接审核，或先补全导师资料。",
      );
      return;
    }
    const confirmed = await confirm({
      title: usesTemplateDraft ? "确认使用 AI 改写？" : "确认重新生成草稿？",
      description: usesTemplateDraft
        ? "AI 改写会覆盖当前模板草稿，当前编辑内容将无法保留。"
        : "重新生成后会覆盖当前草稿内容，原草稿将无法保留。",
      confirmLabel: usesTemplateDraft ? "确认使用 AI 改写" : "确认重新生成",
      cancelLabel: usesTemplateDraft ? "继续审核模板草稿" : "先不重新生成",
    });
    if (!confirmed) {
      return;
    }
    setBatchReviewItemAction(itemId, "regenerate");
    try {
      const thread = usesRenderedTemplateDraft
        ? await rewriteBatchTaskItemDraft(selectedBatchTask.id, itemId, {
            ...buildBatchReviewPayload(),
            llm_profile_id: batchReviewThread?.llm_profile.id ?? selectedBatchTask.llm_profile_id,
          })
        : await regenerateBatchTaskItemDraft(selectedBatchTask.id, itemId);
      ensureBatchReviewThreadMatchesItem(thread, activeBatchReviewItem, selectedBatchTask);
      setBatchReviewItemId((currentItemId) => {
        if (currentItemId === itemId) {
          syncBatchDraftReview(thread);
        }
        return currentItemId;
      });
      notifySuccess(usesTemplateDraft ? "AI 改写已完成" : "草稿已重新生成");
      if (selectedBatchTask) {
        await loadBatchTaskDetails(selectedBatchTask.id);
      }
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "重新生成草稿失败";
      notifyError("重新生成草稿失败", message);
    } finally {
      clearBatchReviewItemAction(itemId, "regenerate");
    }
  };

  const handleApproveBatchDraft = async () => {
    if (!batchReviewThread?.current_task.id || !selectedBatchTask || !activeBatchReviewItem) {
      return;
    }
    const attachmentWarning = shouldPromptForLargeAttachments()
      ? buildLargeAttachmentWarning(batchReviewAttachmentTotalBytes)
      : null;
    if (attachmentWarning) {
      const confirmed = await confirm({
        title: "附件超过 1 MB，仍要通过审核吗？",
        description: attachmentWarning,
        confirmLabel: "仍然通过",
        cancelLabel: "返回调整",
        confirmationCheckbox: {
          label: LARGE_ATTACHMENT_WARNING_CONFIRMATION_LABEL,
          onConfirmChecked: suppressLargeAttachmentWarnings,
        },
      });
      if (!confirmed) {
        return;
      }
    }
    const nextItem =
      reviewRequiredBatchTaskItems.find((item) => item.id !== activeBatchReviewItem.id) ??
      null;
    const itemId = activeBatchReviewItem.id;
    setBatchReviewItemAction(itemId, "submit");
    try {
      const thread = await approveBatchTaskItemDraft(
        selectedBatchTask.id,
        itemId,
        buildBatchReviewPayload(),
      );
      ensureBatchReviewThreadMatchesItem(thread, activeBatchReviewItem, selectedBatchTask);
      notifySuccess("草稿已审核通过");
      setSelectedBatchTaskItems((current) =>
        current.map((item) =>
          item.id === activeBatchReviewItem.id
            ? {
                ...item,
                status: "approved",
                next_action:
                  selectedBatchTask.schedule_type === "scheduled" && !item.scheduled_at
                    ? "missing_schedule"
                    : "waiting_send",
              }
            : item,
        ),
      );
      await Promise.all([loadBatchTaskDetails(selectedBatchTask.id), loadTasks()]);
      if (nextItem) {
        await openBatchDraftReview(nextItem);
      } else {
        resetBatchDraftReview();
      }
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "审核草稿失败";
      notifyError("审核草稿失败", message);
    } finally {
      clearBatchReviewItemAction(itemId, "submit");
    }
  };

  const handleApproveAllBatchDrafts = async () => {
    if (!selectedBatchTask || reviewRequiredBatchTaskItems.length === 0) {
      return;
    }

    const taskId = selectedBatchTask.id;
    const itemIds = reviewRequiredBatchTaskItems.map((item) => item.id);
    const approvedCount = itemIds.length;
    const fallbackCount = reviewRequiredBatchTaskItems.filter(
      (item) => item.draft_generation_source === "template_fallback",
    ).length;
    const attachmentWarning = shouldPromptForLargeAttachments()
      ? buildBulkLargeAttachmentWarning(
          reviewRequiredBatchTaskItems.map(
            (item) => item.selected_attachment_size_bytes ?? 0,
          ),
        )
      : null;
    const deliveryDescription =
      selectedBatchTask.status === "paused"
        ? selectedBatchTask.schedule_type === "scheduled"
          ? `任务已暂停；恢复后按原计划（${buildScheduleLabel(selectedBatchTask)}）发送。`
          : "任务已暂停；恢复后才会发送。"
        : selectedBatchTask.schedule_type === "scheduled"
          ? `确认后会按原计划（${buildScheduleLabel(selectedBatchTask)}）进入定时发送流程；邮件发出后无法撤回。`
          : "确认后会立即进入发送队列，邮件发出后无法撤回。";
    const ignoredDraftDescription =
      generatingDraftBatchTaskItems.length > 0 ||
      draftFailedBatchTaskItems.length > 0
        ? "仅处理已生成的待审核草稿；生成中或失败项不受影响。"
        : null;
    const confirmed = await confirm({
      title: `确认全部通过这 ${approvedCount} 封草稿？`,
      description: [
        "系统将直接采用每封邮件当前的主题、正文和附件设置，不再逐封检查。",
        fallbackCount > 0
          ? `其中 ${fallbackCount} 封因导师缺少研究方向，直接使用模板生成，未进行 AI 改写。`
          : null,
        attachmentWarning,
        deliveryDescription,
        ignoredDraftDescription,
      ]
        .filter(Boolean)
        .join("\n"),
      confirmLabel: attachmentWarning ? "仍然全部通过" : "确认全部通过",
      cancelLabel: "继续逐封审核",
      confirmationCheckbox: attachmentWarning
        ? {
            label: LARGE_ATTACHMENT_WARNING_CONFIRMATION_LABEL,
            onConfirmChecked: suppressLargeAttachmentWarnings,
          }
        : undefined,
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    setBatchBulkApprovalLoading(true);
    try {
      const result = await approveAllBatchTaskDrafts(taskId, itemIds);
      setSelectedBatchTask((current) =>
        current?.id === taskId ? result.task : current,
      );
      notifySuccess(
        `已通过 ${result.approved_count} 封草稿`,
        result.task.status === "paused"
          ? "任务仍处于暂停状态，恢复后才会发送。"
          : result.task.schedule_type === "scheduled"
            ? "邮件将按原定时间和每日数量进入发送流程。"
            : "邮件已进入发送队列。",
      );
      await Promise.all([loadBatchTaskDetails(taskId), loadTasks()]);
    } catch (actionError) {
      const message =
        actionError instanceof Error
          ? actionError.message
          : "批量审核草稿失败";
      notifyError("批量审核草稿失败", message);
      await loadBatchTaskDetails(taskId);
    } finally {
      setBatchBulkApprovalLoading(false);
    }
  };

  const handleDeleteBatchDraftItem = async (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return;
    }
    const confirmed = await confirm({
      title: "从批量任务中移除这封草稿？",
      description:
        "该草稿会从当前待处理列表中移除并停止后续发送；记录仍会保留在任务历史中。",
      confirmLabel: "移除草稿",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    const nextItem =
      reviewRequiredBatchTaskItems.find((candidate) => candidate.id !== item.id) ??
      null;
    setBatchReviewItemAction(item.id, "delete");
    try {
      const result = await deleteBatchTaskItem(selectedBatchTask.id, item.id);
      notifySuccess("草稿已从批量任务中移除");
      setSelectedBatchTask(result.task);
      await Promise.all([loadBatchTaskDetails(selectedBatchTask.id), loadTasks()]);
      if (batchReviewItemId === item.id) {
        if (nextItem) {
          await openBatchDraftReview(nextItem);
        } else {
          resetBatchDraftReview();
        }
      }
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "移除草稿失败";
      notifyError("移除草稿失败", message);
    } finally {
      clearBatchReviewItemAction(item.id, "delete");
    }
  };

  const handleRetryBatchTaskItemDraft = async (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return;
    }
    setBatchReviewItemAction(item.id, "regenerate");
    try {
      const result = await retryBatchTaskItemDraft(selectedBatchTask.id, item.id);
      setSelectedBatchTask(result.task);
      notifySuccess("已重新加入草稿生成队列");
      await Promise.all([loadBatchTaskDetails(selectedBatchTask.id), loadTasks()]);
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "重新生成草稿失败";
      notifyError("重新生成草稿失败", message);
    } finally {
      clearBatchReviewItemAction(item.id, "regenerate");
    }
  };

  const handleSendBatchDraftNow = async () => {
    if (!batchReviewThread?.current_task.id || !selectedBatchTask || !activeBatchReviewItem) {
      return;
    }
    const attachmentWarning = shouldPromptForLargeAttachments()
      ? buildLargeAttachmentWarning(batchReviewAttachmentTotalBytes)
      : null;
    const attachmentOverRecommendedLimit = Boolean(attachmentWarning);
    const confirmed = await confirm({
      title: attachmentOverRecommendedLimit
        ? "附件超过 1 MB，仍要发送吗？"
        : "确认立即发送这封真实邮件？",
      description: [
        `将真实发给 ${
          batchReviewThread?.professor.email ?? "当前导师邮箱"
        }，并附带 ${batchReviewSelectedMaterialIds.length} 份附件，共 ${formatFileSize(batchReviewAttachmentTotalBytes)}。`,
        attachmentWarning,
      ]
        .filter(Boolean)
        .join("\n"),
      confirmLabel: attachmentOverRecommendedLimit ? "仍然发送" : "确认发送",
      cancelLabel: attachmentOverRecommendedLimit ? "返回调整" : "再检查一下",
      confirmationCheckbox: attachmentWarning
        ? {
            label: LARGE_ATTACHMENT_WARNING_CONFIRMATION_LABEL,
            onConfirmChecked: suppressLargeAttachmentWarnings,
          }
        : undefined,
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    const itemId = activeBatchReviewItem.id;
    setBatchReviewItemAction(itemId, "submit");
    try {
      const thread = await approveAndSendBatchTaskItemDraft(
        selectedBatchTask.id,
        itemId,
        buildBatchReviewPayload(),
      );
      ensureBatchReviewThreadMatchesItem(thread, activeBatchReviewItem, selectedBatchTask);
      const failureMessage = getEmailSendFailureMessage(
        thread.current_task.status,
        thread.current_task.last_error,
      );
      if (failureMessage) {
        syncBatchDraftReview(thread);
        notifyError("发送邮件失败", failureMessage);
      } else {
        notifySuccess("邮件已发送");
      }
      setSelectedBatchTaskItems((current) =>
        current.map((item) =>
          item.id === activeBatchReviewItem.id
            ? {
                ...item,
                status: thread.current_task.status ?? item.status,
                sent_at: thread.current_task.sent_at,
                last_send_attempt_at: thread.current_task.last_send_attempt_at,
                last_error: thread.current_task.last_error,
                next_action:
                  thread.current_task.status === "send_failed"
                    ? "send_failed"
                    : failureMessage
                      ? item.next_action
                      : null,
              }
            : item,
        ),
      );
      try {
        await Promise.all([loadBatchTaskDetails(selectedBatchTask.id), loadTasks()]);
      } catch (refreshError) {
        const message =
          refreshError instanceof Error ? refreshError.message : "刷新任务状态失败";
        notifyError("刷新任务状态失败", message);
      }
      if (!failureMessage) {
        resetBatchDraftReview();
      }
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "发送邮件失败";
      notifyError("发送邮件失败", message);
    } finally {
      clearBatchReviewItemAction(itemId, "submit");
    }
  };

  const handleCancelBatchItemSend = async (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return;
    }
    const plannedTime = formatDisplayTime(item.scheduled_at);
    const confirmed = await confirm({
      title: `取消给${item.professor_name}的本次发送？`,
      description: `取消 ${plannedTime} 的发送；不影响其他导师，可稍后恢复。`,
      confirmLabel: "确认取消发送",
      cancelLabel: "保留发送",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    const taskId = selectedBatchTask.id;
    setBatchSendItemAction({ itemId: item.id, kind: "cancel" });
    try {
      const result = await cancelBatchTaskItemSend(taskId, item.id);
      setSelectedBatchTask((current) =>
        current?.id === taskId ? result.task : current,
      );
      notifySuccess(
        "已取消发送",
        `不会按原计划给${item.professor_name}发送邮件。`,
      );
      await Promise.all([loadBatchTaskDetails(taskId), loadTasks()]);
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "取消发送失败";
      notifyError("取消发送失败", message);
      await loadBatchTaskDetails(taskId);
    } finally {
      setBatchSendItemAction((current) =>
        current?.itemId === item.id && current.kind === "cancel"
          ? null
          : current,
      );
    }
  };

  const handleRestoreBatchItemSend = async (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return;
    }
    const attachmentWarning = shouldPromptForLargeAttachments()
      ? buildLargeAttachmentWarning(item.selected_attachment_size_bytes ?? 0)
      : null;
    if (attachmentWarning) {
      const confirmed = await confirm({
        title: "附件超过 1 MB，仍要恢复发送吗？",
        description: [
          attachmentWarning,
          `恢复后仍将按原计划于 ${formatDisplayTime(item.scheduled_at)} 发送。`,
        ].join("\n"),
        confirmLabel: "仍然恢复",
        cancelLabel: "保持取消",
        confirmationCheckbox: {
          label: LARGE_ATTACHMENT_WARNING_CONFIRMATION_LABEL,
          onConfirmChecked: suppressLargeAttachmentWarnings,
        },
      });
      if (!confirmed) {
        return;
      }
    }
    if (!isBatchItemScheduledInFuture(item, batchSendActionNowMs)) {
      notifyError("无法恢复发送", "原定发送时间已过，无法恢复发送");
      await loadBatchTaskDetails(selectedBatchTask.id);
      return;
    }

    const taskId = selectedBatchTask.id;
    const taskWasPaused = selectedBatchTask.status === "paused";
    setBatchSendItemAction({ itemId: item.id, kind: "restore" });
    try {
      const result = await restoreBatchTaskItemSend(taskId, item.id);
      setSelectedBatchTask((current) =>
        current?.id === taskId ? result.task : current,
      );
      notifySuccess(
        "已恢复发送",
        taskWasPaused
          ? `仍将按原计划于 ${formatDisplayTime(item.scheduled_at)} 发送；当前批量任务仍处于暂停状态。`
          : `仍将按原计划于 ${formatDisplayTime(item.scheduled_at)} 发送。`,
      );
      await Promise.all([loadBatchTaskDetails(taskId), loadTasks()]);
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "恢复发送失败";
      notifyError("恢复发送失败", message);
      await loadBatchTaskDetails(taskId);
    } finally {
      setBatchSendItemAction((current) =>
        current?.itemId === item.id && current.kind === "restore"
          ? null
          : current,
      );
    }
  };

  const renderBatchItemSendButton = (item: BatchTaskItemDTO) => {
    const activeAction =
      batchSendItemAction?.itemId === item.id ? batchSendItemAction.kind : null;
    const actionBusy = batchSendItemAction !== null;
    if (item.batch_send_canceled_at) {
      if (
        !item.can_restore_send ||
        !isBatchItemScheduledInFuture(item, batchSendActionNowMs)
      ) {
        return null;
      }
      return (
        <button
          type="button"
          onClick={() => void handleRestoreBatchItemSend(item)}
          disabled={actionBusy}
          className="ui-btn-secondary min-h-8 gap-1.5 px-3 py-1.5 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-60"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          {activeAction === "restore" ? "恢复中…" : "恢复发送"}
        </button>
      );
    }
    if (!item.can_cancel_send) {
      return null;
    }
    return (
      <button
        type="button"
        onClick={() => void handleCancelBatchItemSend(item)}
        disabled={actionBusy}
        className="ui-btn-danger min-h-8 gap-1.5 px-3 py-1.5 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-60"
      >
        <Ban className="h-3.5 w-3.5" />
        {activeAction === "cancel" ? "取消中…" : "取消发送"}
      </button>
    );
  };

  const renderBatchTaskItemReviewButton = (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return null;
    }
    const action = buildBatchPendingItemAction(item, selectedBatchTask);
    if (action?.kind !== "review") {
      return null;
    }

    return (
      <button
        type="button"
        onClick={() => void openBatchDraftReview(item)}
        className="ui-btn-primary min-h-8 gap-1.5 px-3 py-1.5 text-xs shadow-primary/15"
      >
        <FileSearch className="h-3.5 w-3.5" />
        {action.text}
      </button>
    );
  };

  const renderBatchTaskItemAction = (item: BatchTaskItemDTO) => {
    if (!selectedBatchTask) {
      return null;
    }
    const action = buildBatchPendingItemAction(item, selectedBatchTask);
    const missingResearchDirection =
      isBatchTaskItemMissingResearchDirection(item);
    let actionContent: ReactNode = null;
    if (action?.kind === "message") {
      actionContent = (
        <span className="font-medium text-stone-600">
          {action.text}
        </span>
      );
    } else if (action?.kind === "professor" && !missingResearchDirection) {
      actionContent = (
        <Link to={action.href} className="font-medium text-primary">
          {action.text}
        </Link>
      );
    } else if (action?.kind === "profile") {
      actionContent = (
        <Link to={action.href} className="font-medium text-primary">
          {action.text}
        </Link>
      );
    } else if (action?.kind === "retry") {
      actionContent = (
        <button
          type="button"
          onClick={() => void handleRetryBatchTaskItemDraft(item)}
          disabled={batchReviewItemActions[item.id] === "regenerate"}
          className="font-medium text-primary disabled:cursor-not-allowed disabled:text-stone-400"
        >
          {batchReviewItemActions[item.id] === "regenerate"
            ? "正在重新生成"
            : action.text}
        </button>
      );
    }
    if (!missingResearchDirection) {
      return actionContent;
    }

    return (
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void openProfessorEditDialog(item)}
          className="inline-flex items-center gap-1.5 font-medium text-primary"
        >
          <Pencil className="h-3.5 w-3.5" />
          补充资料
        </button>
        {actionContent}
      </div>
    );
  };

  const closeBatchTaskDetails = () => {
    latestBatchTaskDetailsRequestIdRef.current += 1;
    closeProfessorEditDialog();
    resetBatchDraftReview();
    setSelectedBatchTask(null);
    setSelectedBatchTaskItems([]);
    setBatchTaskDetailsLoading(false);
    setBatchSendItemAction(null);
    lastBatchTaskDetailsLoadErrorRef.current = null;
  };

  const closeMatchJobDetails = () => {
    latestMatchJobDetailsRequestIdRef.current += 1;
    setSelectedMatchJob(null);
    setSelectedMatchJobItems([]);
    setMatchJobItemTotalCount(0);
    setMatchJobDetailsLoading(false);
    lastMatchJobDetailsLoadErrorRef.current = null;
  };
  const closeInformationEnrichmentDetails = () => {
    latestInformationEnrichmentDetailsRequestIdRef.current += 1;
    setSelectedInformationEnrichmentJob(null);
    setSelectedInformationEnrichmentItems([]);
    setInformationEnrichmentDetailsLoading(false);
    lastInformationEnrichmentDetailsLoadErrorRef.current = null;
  };
  const requestCloseSelectedCandidateDetail = useCallback(async () => {
    if (candidateUpdateLoading) {
      return;
    }
    if (
      selectedCandidateDetail &&
      candidateEditForm &&
      hasUnsavedCrawlCandidateChanges(
        selectedCandidateDetail,
        candidateEditForm,
      )
    ) {
      const shouldDiscardChanges = await confirm({
        title: "放弃未保存的修改？",
        description: "关闭后，本次对候选导师信息的修改将不会保存。",
        confirmLabel: "不保存并关闭",
        cancelLabel: "继续编辑",
        tone: "danger",
      });
      if (!shouldDiscardChanges) {
        return;
      }
    }
    setCandidateEditForm(null);
    setSelectedCandidateDetail(null);
  }, [
    candidateEditForm,
    candidateUpdateLoading,
    confirm,
    selectedCandidateDetail,
  ]);
  const closeSelectedCandidateDetail = useCallback(() => {
    void requestCloseSelectedCandidateDetail();
  }, [requestCloseSelectedCandidateDetail]);
  const batchTaskDetailsLayer = useDismissableLayerClick(closeBatchTaskDetails);
  const matchJobDetailsLayer = useDismissableLayerClick(closeMatchJobDetails);
  const informationEnrichmentDetailsLayer = useDismissableLayerClick(
    closeInformationEnrichmentDetails,
  );
  const crawlJobDetailsLayer = useDismissableLayerClick(closeCrawlJobDetails);
  const candidateDetailLayer = useDismissableLayerClick(closeSelectedCandidateDetail);

  const handleOpenBatchResend = async (task: BatchTaskCardDTO) => {
    setResendDialogOpen(true);
    setResendLoading(true);
    setSelectedResendProfessorIds([]);
    try {
      const context = await getBatchTaskResendContext(task.id);
      setResendContext(context);
      setSelectedResendProfessorIds(
        context.items
          .filter((item) => item.selectable && item.default_selected && item.professor_id !== null)
          .map((item) => item.professor_id as number),
      );
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "请稍后重试";
      notifyError("加载可重新发起项失败", message);
      setResendDialogOpen(false);
    } finally {
      setResendLoading(false);
    }
  };

  const handleToggleResendProfessor = (professorId: number) => {
    setSelectedResendProfessorIds((previous) =>
      previous.includes(professorId)
        ? previous.filter((item) => item !== professorId)
        : [...previous, professorId],
    );
  };

  const handleSelectAllResendProfessors = () => {
    if (!resendContext) {
      return;
    }
    setSelectedResendProfessorIds(
      resendContext.items
        .filter((item) => item.selectable && item.professor_id !== null)
        .map((item) => item.professor_id as number),
    );
  };

  const handleSubmitBatchResend = async () => {
    if (!resendContext || selectedResendProfessorIds.length === 0) {
      return;
    }
    const resendTemplateLabel = getOutreachTemplateSourceLabel(
      resendContext.defaults,
    );
    const resendGenerationModeLabel = getOutreachGenerationModeLabel(
      resendContext.defaults.outreach_generation_mode,
    );
    const confirmed = await confirm({
      title: "确认重新发起这批导师？",
      description: [
        "下一步选择内容策略和发送时间。",
        `发信模板：${resendTemplateLabel}`,
        `写信方式：${resendGenerationModeLabel}`,
      ].join("\n"),
      confirmLabel: "去创建新任务",
      cancelLabel: "继续选择",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    const requiresRegeneration = resendContext.items.some(
      (item) =>
        item.professor_id !== null &&
        selectedResendProfessorIds.includes(item.professor_id) &&
        item.content_reuse_kind === "regenerate",
    );
    try {
      writeCreateTaskNavigationHandoff(selectedResendProfessorIds, {
        sourceTaskId: resendContext.task.id,
        sourceTaskName: resendContext.task.name,
        identityId: resendContext.task.identity_id,
        professorIds: selectedResendProfessorIds,
        requiresRegeneration,
        defaults: resendContext.defaults,
        warnings: resendContext.warnings,
      });
      setSelectedIdentityId(resendContext.task.identity_id);
      navigate("/create-task");
    } catch (handoffError) {
      notifyError(
        "无法打开任务创建页",
        handoffError instanceof Error
          ? handoffError.message
          : "批量重发选择暂时无法交给任务创建页。",
      );
    }
  };
  const handleDeleteBatchTask = async (task: BatchTaskCardDTO) => {
    const confirmed = await confirm({
      title: "移入回收站？",
      description: "任务及其全部历史数据都会保留，可在回收站恢复。",
      confirmLabel: "移入回收站",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    try {
      await deleteBatchTask(task.id);
      notifySuccess("已移入回收站");
      if (selectedBatchTask?.id === task.id) {
        closeBatchTaskDetails();
      }
      await loadTasks();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "移入回收站失败";
      notifyError("移入回收站失败", message);
    }
  };

  const handleRestoreBatchTask = async (taskId: number) => {
    try {
      await restoreBatchTask(taskId);
      notifySuccess("已还原任务");
      await loadTasks();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "还原任务失败";
      notifyError("还原任务失败", message);
    }
  };

  const handleDeleteCrawlJob = async (job: CrawlJobSummaryDTO) => {
    const confirmed = await confirm({
      title: "移入回收站？",
      description: "任务及其全部历史数据都会保留，可在回收站恢复。",
      confirmLabel: "移入回收站",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    try {
      await deleteCrawlJob(job.id);
      notifySuccess("已移入回收站");
      if (selectedCrawlJobId === job.id) {
        closeCrawlJobDetails();
      }
      await loadCrawlJobs();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "移入回收站失败";
      notifyError("移入回收站失败", message);
    }
  };

  const handleRestoreCrawlJob = async (jobId: number) => {
    try {
      await restoreCrawlJob(jobId);
      notifySuccess("已还原任务");
      await loadCrawlJobs();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "还原任务失败";
      notifyError("还原任务失败", message);
    }
  };

  const handleDeleteMatchJob = async (job: MatchAnalysisJobDTO) => {
    const confirmed = await confirm({
      title: "移入回收站？",
      description: "任务及其全部历史数据都会保留，可在回收站恢复。",
      confirmLabel: "移入回收站",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    try {
      await deleteMatchAnalysisJob(job.id);
      notifySuccess("已移入回收站");
      if (selectedMatchJob?.id === job.id) {
        closeMatchJobDetails();
      }
      await loadMatchAnalysisJobs();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "移入回收站失败";
      notifyError("移入回收站失败", message);
    }
  };

  const handleRestoreMatchJob = async (jobId: number) => {
    try {
      await restoreMatchAnalysisJob(jobId);
      notifySuccess("已还原任务");
      await loadMatchAnalysisJobs();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "还原任务失败";
      notifyError("还原任务失败", message);
    }
  };

  const handleDeleteInformationEnrichmentJob = async (
    job: ProfessorInformationEnrichmentJobDTO,
  ) => {
    const confirmed = await confirm({
      title: "移入回收站？",
      description: "任务及其全部历史数据都会保留，可在回收站恢复。",
      confirmLabel: "移入回收站",
      cancelLabel: "先保留",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    try {
      await deleteProfessorInformationEnrichmentJob(job.id);
      notifySuccess("已移入回收站");
      if (selectedInformationEnrichmentJob?.id === job.id) {
        closeInformationEnrichmentDetails();
      }
      await loadInformationEnrichmentJobs();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "移入回收站失败";
      notifyError("移入回收站失败", message);
    }
  };

  const handleRestoreInformationEnrichmentJob = async (jobId: number) => {
    try {
      await restoreProfessorInformationEnrichmentJob(jobId);
      notifySuccess("已还原任务");
      await loadInformationEnrichmentJobs();
    } catch (actionError) {
      const message =
        actionError instanceof Error ? actionError.message : "还原任务失败";
      notifyError("还原任务失败", message);
    }
  };

  const batchDraftReviewOpen = batchReviewItemId !== null;
  const batchReviewEditorHtml =
    batchReviewContentHtml || textToEmailHtml(batchReviewContentText);
  const batchReviewCanSubmit =
    Boolean(batchReviewThread?.current_task.id) &&
    Boolean(
      batchReviewSubject.trim() ||
        batchReviewContentText.trim() ||
        deriveBatchReviewText("", batchReviewContentHtml).trim(),
    );
  const activeBatchReviewAction =
    batchReviewItemId !== null
      ? batchReviewItemActions[batchReviewItemId] ?? null
      : null;
  const activeBatchReviewOutreachTemplates = batchReviewOutreachTemplates.filter(
    (template) => !template.archived_at,
  );
  const selectedBatchReviewOutreachTemplateId =
    batchReviewThread?.current_task.outreach_template_id ?? null;
  const selectedBatchReviewOutreachTemplate =
    batchReviewOutreachTemplates.find(
      (template) => template.id === selectedBatchReviewOutreachTemplateId,
    ) ?? null;
  const batchReviewSourceTemplateLabel = selectedBatchReviewOutreachTemplate
    ? `${selectedBatchReviewOutreachTemplate.name}${selectedBatchReviewOutreachTemplate.archived_at ? " · 已删除" : ""}`
    : selectedBatchReviewOutreachTemplateId !== null
      ? getOutreachTemplateSourceLabel({
          outreach_template_id: selectedBatchReviewOutreachTemplateId,
        })
      : selectedBatchTask
        ? getOutreachTemplateSourceLabel(selectedBatchTask)
        : "未使用模板";
  const batchReviewDraftSource =
    batchReviewThread?.current_task.draft?.source ??
    (batchReviewThread?.current_task.approved_body_text ||
    batchReviewThread?.current_task.approved_body_html
      ? "saved"
      : batchReviewThread?.current_task.generated_content_text ||
          batchReviewThread?.current_task.generated_content_html
        ? "ai_rewrite"
        : batchReviewThread?.current_task.rendered_template_body_text ||
            batchReviewThread?.current_task.rendered_template_body_html ||
            batchReviewThread?.current_task.outreach_template_body_text ||
            batchReviewThread?.current_task.outreach_template_body_html
          ? "template"
          : "manual_empty");
  const batchReviewDraftSourceLabel =
    BATCH_REVIEW_DRAFT_SOURCE_LABELS[batchReviewDraftSource];
  const batchReviewUsesTemplateFallback =
    batchReviewThread
      ? batchReviewThread.current_task.draft_generation_source ===
        "template_fallback"
      : activeBatchReviewItem?.draft_generation_source === "template_fallback";
  const batchReviewUsesTemplateDraft =
    batchReviewDraftSource === "template" ||
    batchReviewUsesTemplateFallback;
  const batchReviewProfessorMissingResearchDirection =
    !batchReviewThread?.professor.research_direction?.trim();
  const batchReviewTemplateReferencesResearchDirection = [
    batchReviewThread?.current_task.outreach_template_subject,
    batchReviewThread?.current_task.outreach_template_body_text,
    batchReviewThread?.current_task.outreach_template_body_html,
  ].some((value) => /\{\{\s*research_direction\s*\}\}/.test(value ?? ""));
  const canSendBatchReviewImmediately =
    selectedBatchTask?.schedule_type === "immediate";

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <div
        data-testid="task-center-header"
        className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-6 shadow-sm"
      >
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-semibold text-stone-900">任务中心</h1>
            <p className="mt-1 text-sm text-stone-500">查看发送计划和后台任务</p>
          </div>
          <TaskCenterSectionSwitch
            activeSection="background"
            onChange={updateTaskCenterSection}
          />
        </div>

        {!hasTaskSelection ? (
          <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            选择身份和模型后显示批量邮件与匹配分析。
          </div>
        ) : null}

      <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <TaskTypeTabs
          activeTab={activeTab}
          hasTaskSelection={hasTaskSelection}
          counts={{
            batch: currentBatchTasks.length,
            crawl: currentCrawlJobCount,
            match: currentMatchAnalysisJobs.length,
            enrichment: currentInformationEnrichmentJobCount,
          }}
          onChange={setActiveTab}
        />

        <TaskListViewSwitch
          activeView={activeTaskListView}
          onViewChange={(view) => {
            setTaskListViews((current) => ({ ...current, [activeTab]: view }));
            setActiveTaskPage(1);
          }}
        />
      </div>

      <TaskListToolbar
        activeTab={activeTab}
        filters={activeTaskListFilters}
        sortDirections={taskSortDirections}
        advancedFiltersOpen={advancedTaskFiltersOpen}
        advancedFilterCount={activeAdvancedTaskFilterCount}
        onFilterChange={updateActiveTaskListFilters}
        onSortDirectionChange={(sortKey) => {
          setTaskSortDirections((current) => ({
            ...current,
            [sortKey]: current[sortKey] === "desc" ? "asc" : "desc",
          }));
          updateActiveTaskListFilters({ sortKey });
        }}
        onAdvancedFiltersToggle={() =>
          setAdvancedTaskFiltersOpen((current) => !current)
        }
        onReset={resetActiveTaskListFilters}
      />
      </div>

      <section
        ref={taskListStartRef}
        tabIndex={-1}
        aria-label="任务列表"
        className="scroll-mt-6 focus:outline-none"
      >
      {activeTab === "batch" && loading ? (
        <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载任务列表…
        </div>
      ) : activeTab === "batch" && tasks.length === 0 ? (
        <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
          {activeTaskListView === "trash" ? "回收站暂无任务。" : "暂无任务。可从首页创建。"}
        </div>
      ) : activeTab === "batch" && filteredBatchTasks.length === 0 ? (
        <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
          没有符合当前条件的批量邮件任务。
        </div>
      ) : activeTab === "batch" ? (
        <>
          <div className="mt-6 grid gap-4">
            {visibleBatchTasks.map((task) => {
              const progress =
                task.target_count === 0
                  ? 0
                  : Math.round(
                      (task.completed_count / task.target_count) * 100,
                    );
              const waitingSendCount = getBatchTaskWaitingSendCount(task);

              return (
                <article
                  key={task.id}
                  className="rounded-2xl border border-stone-200 bg-white px-5 py-5 shadow-sm"
                >
                  <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_240px_minmax(260px,auto)_auto] lg:items-center">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                        <Mail className="h-4 w-4 text-primary" />
                        批量邮件任务
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="mt-2 truncate text-base font-semibold text-stone-900">
                          {task.name}
                        </h2>
                        <span className="rounded-full bg-stone-100 px-2.5 py-1 text-xs font-medium text-stone-700">
                          {BATCH_TASK_STATUS_LABELS[task.status]}
                        </span>
                      </div>
                      <p className="mt-1 truncate text-sm text-stone-500">
                        {buildScheduleLabel(task)}
                      </p>
                    </div>

                    <div>
                      <div className="mb-2 flex items-center justify-between text-xs text-stone-500">
                        <span>
                          {task.completed_count}/{task.target_count}
                        </span>
                        <span>{progress}%</span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-stone-100">
                        <div
                          className="h-full rounded-full bg-primary"
                          style={{ width: `${progress}%` }}
                        />
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                      {task.queued_generation_count > 0 ? (
                        <span className="rounded-full bg-stone-50 px-2.5 py-1 text-xs text-stone-600">
                          排队中 {task.queued_generation_count}
                        </span>
                      ) : null}
                      {task.blocked_generation_count > 0 ? (
                        <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs text-red-700">
                          需处理 {task.blocked_generation_count}
                        </span>
                      ) : null}
                      {task.generating_draft_count > 0 ? (
                        <span className="rounded-full bg-sky-50 px-2.5 py-1 text-xs text-sky-700">
                          生成中 {task.generating_draft_count}
                        </span>
                      ) : null}
                      {task.draft_failed_count > 0 ? (
                        <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs text-red-700">
                          草稿失败 {task.draft_failed_count}
                        </span>
                      ) : null}
                      <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs text-amber-700">
                        待审核 {task.review_required_count}
                      </span>
                      <span className="rounded-full bg-primary/10 px-2.5 py-1 text-xs text-primary">
                        待发送 {waitingSendCount}
                      </span>
                      {task.canceled_send_count > 0 ? (
                        <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs text-red-700">
                          已取消发送 {task.canceled_send_count}
                        </span>
                      ) : null}
                      <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs text-emerald-700">
                        已发送 {task.sent_count + task.replied_count}
                      </span>
                      {task.failed_count > 0 ? (
                        <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs text-red-700">
                          失败 {task.failed_count}
                        </span>
                      ) : null}
                    </div>

                    <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                      {activeTaskListView === "trash" ? (
                        <button
                          type="button"
                          onClick={() => void handleRestoreBatchTask(task.id)}
                          className="ui-btn-primary"
                        >
                          <RotateCcw className="h-4 w-4" />
                          还原任务
                        </button>
                      ) : null}
                      {activeTaskListView === "current" &&
                      canDeleteBatchTask(task) ? (
                        <button
                          type="button"
                          onClick={() => void handleDeleteBatchTask(task)}
                          className="ui-btn-danger"
                        >
                          <Trash2 className="h-4 w-4" />
                          删除
                        </button>
                      ) : null}
                      {activeTaskListView === "current" &&
                      task.status === "running" ? (
                        <button
                          type="button"
                          onClick={() => void handleAction(task.id, "pause")}
                          className="ui-btn-secondary"
                        >
                          <Pause className="h-4 w-4" />
                          暂停
                        </button>
                      ) : null}
                      {activeTaskListView === "current" &&
                      task.status === "paused" ? (
                        <button
                          type="button"
                          onClick={() => void handleAction(task.id, "resume")}
                          className="ui-btn-secondary"
                        >
                          <Play className="h-4 w-4" />
                          继续
                        </button>
                      ) : null}
                      {activeTaskListView === "current" &&
                      task.status !== "stopped" &&
                      task.status !== "completed" &&
                      task.status !== "expired" ? (
                        <button
                          type="button"
                          onClick={() => void handleAction(task.id, "stop")}
                          className="ui-btn-danger"
                        >
                          <Square className="h-4 w-4" />
                          终止
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => setSelectedBatchTask(task)}
                        className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/30 hover:bg-primary/5 hover:text-primary"
                        aria-label="查看详情"
                        title="查看详情"
                      >
                        <ChevronRight className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
          <Pagination
            page={safeBatchPage}
            pageSize={batchPageSize}
            totalCount={filteredBatchTasks.length}
            onChange={handleBatchPaginationChange}
            ariaLabel="批量邮件任务分页"
            pageSizeOptions={TASKS_PAGE_SIZE_OPTIONS}
            unitLabel="个"
            itemLabel="个任务"
            pageStatusPrefix="第 "
            focusTargetRef={taskListStartRef}
            className="mt-4 rounded-2xl border border-stone-200 bg-white px-4 py-3 shadow-sm"
          />
        </>
      ) : activeTab === "match" && matchJobsLoading && matchAnalysisJobs.length === 0 ? (
        <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载匹配分析任务列表…
        </div>
      ) : activeTab === "match" && matchAnalysisJobs.length === 0 ? (
        <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
          {activeTaskListView === "trash"
            ? "回收站暂无任务。"
            : "暂无匹配分析任务。可从首页创建。"}
        </div>
      ) : activeTab === "match" && filteredMatchAnalysisJobs.length === 0 ? (
        <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
          没有符合当前条件的匹配分析任务。
        </div>
      ) : activeTab === "match" ? (
        <>
          <div className="mt-6 grid gap-4">
            {visibleMatchJobs.map((job) => (
              <article
                key={job.id}
                className="rounded-2xl border border-stone-200 bg-white px-5 py-5 shadow-sm"
              >
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px_auto] lg:items-center">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                      <Sparkles className="h-4 w-4 text-primary" />
                      匹配分析任务
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <h2 className="truncate text-base font-semibold text-stone-900">
                        {job.name}
                      </h2>
                      <span
                        className={`rounded-full border px-2.5 py-1 text-xs font-medium ${MATCH_ANALYSIS_JOB_STATUS_TONES[job.status]}`}
                      >
                        {MATCH_ANALYSIS_JOB_STATUS_LABELS[job.status]}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-stone-500">
                      成功 {job.succeeded_count} / 失败 {job.failed_count} / 跳过 {job.skipped_count} / 共 {job.target_count}
                    </p>
                    <p className="mt-1 text-xs text-stone-500">
                      {job.match_source_identity_id === null ? (
                        <>匹配依据 原身份已删除</>
                      ) : (
                        <>
                          {job.match_source_identity_id &&
                          job.match_source_identity_id !== job.identity_id
                            ? '组内统一匹配依据'
                            : '匹配依据'}{' '}
                          {identities.find(
                            (identity) =>
                              identity.id ===
                              (job.match_source_identity_id ?? job.identity_id),
                          )?.profile_name ??
                            `身份 #${job.match_source_identity_id ?? job.identity_id}`}
                        </>
                      )}
                    </p>
                  </div>
                  <div className="min-w-0 space-y-2">
                    <TokenUsageBreakdown
                      inputTokens={job.total_prompt_tokens}
                      outputTokens={job.total_completion_tokens}
                      cachedTokens={job.total_cached_tokens}
                      totalTokens={job.total_tokens}
                      ariaLabel={`${job.name} Token 使用汇总`}
                    />
                    <div className="text-right text-xs text-stone-500">
                      更新 {formatDisplayTime(job.updated_at, { withSeconds: true })}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                    {activeTaskListView === "trash" ? (
                      <button
                        type="button"
                        onClick={() => void handleRestoreMatchJob(job.id)}
                        className="ui-btn-primary"
                      >
                        <RotateCcw className="h-4 w-4" />
                        还原任务
                      </button>
                    ) : null}
                    {activeTaskListView === "current" &&
                    canDeleteMatchJob(job) ? (
                      <button
                        type="button"
                        onClick={() => void handleDeleteMatchJob(job)}
                        className="ui-btn-danger"
                      >
                        <Trash2 className="h-4 w-4" />
                        删除
                      </button>
                    ) : null}
                    {activeTaskListView === "current" &&
                    (job.status === "queued" || job.status === "running") ? (
                      <button
                        type="button"
                        onClick={() => void handleCancelMatchJob(job.id)}
                        className="ui-btn-danger"
                        disabled={cancelingMatchJobId === job.id}
                      >
                        <Square className="h-4 w-4" />
                        取消
                      </button>
                    ) : null}
                    {activeTaskListView === "current" && (
                      job.status === "partial_failed" ||
                      job.status === "failed" ||
                      job.status === "canceled"
                    ) ? (
                      <button
                        type="button"
                        onClick={() => void handleRetryMatchJob(job.id)}
                        className="ui-btn-secondary"
                        disabled={retryingMatchJobId === job.id}
                      >
                        <Play className="h-4 w-4" />
                        重试失败项
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => setSelectedMatchJob(job)}
                      className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/30 hover:bg-primary/5 hover:text-primary"
                      aria-label={`查看匹配分析任务 ${job.name}`}
                      title="查看详情"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </article>
            ))}
          </div>
          <Pagination
            page={safeMatchPage}
            pageSize={matchPageSize}
            totalCount={filteredMatchAnalysisJobs.length}
            onChange={handleMatchPaginationChange}
            ariaLabel="匹配分析任务分页"
            pageSizeOptions={TASKS_PAGE_SIZE_OPTIONS}
            unitLabel="个"
            itemLabel="个任务"
            pageStatusPrefix="第 "
            focusTargetRef={taskListStartRef}
            className="mt-4 rounded-2xl border border-stone-200 bg-white px-4 py-3 shadow-sm"
          />
        </>
      ) : activeTab === "enrichment" &&
        informationEnrichmentJobsLoading &&
        informationEnrichmentJobs.length === 0 ? (
        <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载信息补全任务列表…
        </div>
      ) : activeTab === "enrichment" &&
        informationEnrichmentJobTotalCount === 0 ? (
        <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
          {activeTaskListView === "trash"
            ? "回收站暂无任务。"
            : "暂无信息补全任务。可从导师管理页批量创建。"}
        </div>
      ) : activeTab === "enrichment" &&
        displayedInformationEnrichmentJobTotalCount === 0 ? (
        <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
          没有符合当前条件的信息补全任务。
        </div>
      ) : activeTab === "enrichment" ? (
        <>
          <div className="mt-6 grid gap-4">
            {visibleInformationEnrichmentJobs.map((job) => {
              const progress =
                job.target_count === 0
                  ? 0
                  : Math.round((job.completed_count / job.target_count) * 100);
              const canRetry = job.failed_count + job.canceled_count > 0;

              return (
                <article
                  key={job.id}
                  className="rounded-2xl border border-stone-200 bg-white px-5 py-5 shadow-sm"
                >
                  <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_240px_minmax(250px,auto)_auto] lg:items-center">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-xs font-medium text-stone-500">
                        <Bot className="h-4 w-4 text-primary" />
                        信息补全任务
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <h2 className="min-w-0 truncate text-base font-semibold text-stone-900">
                          {job.name}
                        </h2>
                        <span
                          className={`shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium ${INFORMATION_ENRICHMENT_JOB_STATUS_TONES[job.status]}`}
                        >
                          {PROFESSOR_INFORMATION_ENRICHMENT_STATUS_LABELS[job.status]}
                        </span>
                      </div>
                      <p className="mt-1 text-sm text-stone-500">
                        创建于 {formatDisplayTime(job.created_at)}
                      </p>
                      {job.last_error ? (
                        <p className="mt-2 line-clamp-2 break-all text-xs leading-5 text-red-700">
                          {job.last_error}
                        </p>
                      ) : null}
                    </div>

                    <div className="min-w-0">
                      <div className="mb-2 flex items-center justify-between text-xs text-stone-500">
                        <span>
                          {job.completed_count}/{job.target_count}
                        </span>
                        <span>{progress}%</span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-stone-100">
                        <div
                          className="h-full rounded-full bg-primary"
                          style={{ width: `${progress}%` }}
                        />
                      </div>
                      <TokenUsageBreakdown
                        inputTokens={job.input_tokens}
                        outputTokens={job.output_tokens}
                        cachedTokens={job.cached_tokens}
                        totalTokens={job.total_tokens}
                        ariaLabel={`${job.name} Token 使用汇总`}
                        className="mt-3"
                      />
                      <div className="mt-2 text-right text-xs text-stone-500">
                        耗时 {formatDuration(job.duration_seconds)}
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                      <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs text-emerald-700">
                        成功 {job.succeeded_count}
                      </span>
                      <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs text-red-700">
                        失败 {job.failed_count}
                      </span>
                      <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs text-amber-700">
                        跳过 {job.skipped_count}
                      </span>
                      {job.canceled_count > 0 ? (
                        <span className="rounded-full bg-stone-100 px-2.5 py-1 text-xs text-stone-600">
                          取消 {job.canceled_count}
                        </span>
                      ) : null}
                    </div>

                    <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                      {activeTaskListView === "trash" ? (
                        <button
                          type="button"
                          onClick={() =>
                            void handleRestoreInformationEnrichmentJob(job.id)
                          }
                          className="ui-btn-primary"
                        >
                          <RotateCcw className="h-4 w-4" />
                          还原任务
                        </button>
                      ) : null}
                      {activeTaskListView === "current" &&
                      canDeleteInformationEnrichmentJob(job) ? (
                        <button
                          type="button"
                          onClick={() =>
                            void handleDeleteInformationEnrichmentJob(job)
                          }
                          className="ui-btn-danger"
                        >
                          <Trash2 className="h-4 w-4" />
                          删除
                        </button>
                      ) : null}
                      {activeTaskListView === "current" &&
                      (job.status === "queued" || job.status === "running") ? (
                        <button
                          type="button"
                          onClick={() =>
                            void handleCancelInformationEnrichmentJob(job.id)
                          }
                          className="ui-btn-danger"
                          disabled={cancelingInformationEnrichmentJobId === job.id}
                        >
                          {cancelingInformationEnrichmentJobId === job.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Square className="h-4 w-4" />
                          )}
                          取消
                        </button>
                      ) : null}
                      {activeTaskListView === "current" && canRetry ? (
                        <button
                          type="button"
                          onClick={() =>
                            void handleRetryInformationEnrichmentJob(job.id)
                          }
                          className="ui-btn-secondary"
                          disabled={retryingInformationEnrichmentJobId === job.id}
                        >
                          {retryingInformationEnrichmentJobId === job.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Play className="h-4 w-4" />
                          )}
                          重试失败项
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => setSelectedInformationEnrichmentJob(job)}
                        className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/30 hover:bg-primary/5 hover:text-primary"
                        aria-label={`查看信息补全任务 ${job.name}`}
                        title="查看详情"
                      >
                        <ChevronRight className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
          <Pagination
            page={safeInformationEnrichmentPage}
            pageSize={informationEnrichmentPageSize}
            totalCount={displayedInformationEnrichmentJobTotalCount}
            onChange={handleInformationEnrichmentPaginationChange}
            ariaLabel="信息补全任务分页"
            pageSizeOptions={TASKS_PAGE_SIZE_OPTIONS}
            unitLabel="个"
            itemLabel="个任务"
            pageStatusPrefix="第 "
            focusTargetRef={taskListStartRef}
            className="mt-4 rounded-2xl border border-stone-200 bg-white px-4 py-3 shadow-sm"
          />
        </>
      ) : crawlJobsLoading && crawlJobs.length === 0 ? (
        <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载抓取任务列表…
        </div>
      ) : crawlJobTotalCount === 0 ? (
        <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
          {activeTaskListView === "trash"
            ? "回收站暂无任务。"
            : "暂无抓取任务。可从导师管理页创建。"}
        </div>
      ) : displayedCrawlJobTotalCount === 0 ? (
        <div className="mt-6 rounded-3xl border border-dashed border-stone-300 bg-white px-6 py-14 text-center text-sm text-stone-500 shadow-sm">
          没有符合当前条件的智能抓取任务。
        </div>
      ) : (
        <>
          <div className="mt-6 grid gap-4">
            {visibleCrawlJobs.map((job) => (
              <CrawlJobCard
                key={job.id}
                job={job}
                listView={taskListViews.crawl}
                pausingCrawlJobId={pausingCrawlJobId}
                resumingCrawlJobId={resumingCrawlJobId}
                retryingCrawlJobId={retryingCrawlJobId}
                resumingCrawlJobReviewId={resumingCrawlJobReviewId}
                onOpenDetails={(currentJob) => {
                  safeRecordUserAction({
                    eventName: "tasks.crawl_job_detail_opened",
                    data: { jobId: currentJob.id, status: currentJob.status },
                  });
                  setSelectedCrawlJob(currentJob);
                }}
                onPause={(jobId) => void handlePauseCrawlJob(jobId)}
                onResume={(jobId) => void handleResumeCrawlJob(jobId)}
                onCancel={(jobId) => void handleCancelCrawlJob(jobId)}
                onRetry={(jobId) => void handleRetryCrawlJob(jobId)}
                onResumeReview={(jobId) => void handleResumeCrawlJobReview(jobId)}
                onDelete={(currentJob) => void handleDeleteCrawlJob(currentJob)}
                onRestore={(jobId) => void handleRestoreCrawlJob(jobId)}
                formatUpdatedAt={(value) =>
                  formatDisplayTime(value, { withSeconds: true })
                }
              />
            ))}
          </div>
          <Pagination
            page={safeCrawlPage}
            pageSize={crawlPageSize}
            totalCount={displayedCrawlJobTotalCount}
            onChange={handleCrawlPaginationChange}
            ariaLabel="智能抓取任务分页"
            pageSizeOptions={TASKS_PAGE_SIZE_OPTIONS}
            unitLabel="个"
            itemLabel="个任务"
            pageStatusPrefix="第 "
            focusTargetRef={taskListStartRef}
            className="mt-4 rounded-2xl border border-stone-200 bg-white px-4 py-3 shadow-sm"
          />
        </>
      )}
      </section>
      {selectedBatchTask ? (
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
                {!batchDraftReviewOpen && canOpenBatchResend(selectedBatchTask, activeTaskListView) ? (
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
                    onClick={resetBatchDraftReview}
                    className="ui-btn-secondary"
                  >
                    <ChevronLeft className="h-4 w-4" />
                    返回详情
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={closeBatchTaskDetails}
                  className="ui-btn-secondary"
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
                      {batchReviewLoading && !batchReviewThread ? (
                        <Loader2 className="h-4 w-4 animate-spin text-stone-400" />
                      ) : null}
                    </div>
                    <div className="mt-4 space-y-2">
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
                          className={
                            item.id === batchReviewItemId
                              ? "flex w-full items-stretch overflow-hidden rounded-2xl border border-primary/25 bg-white shadow-sm"
                              : "flex w-full items-stretch overflow-hidden rounded-2xl border border-stone-200 bg-white/70 transition hover:border-primary/20 hover:bg-white"
                          }
                        >
                          <button
                            type="button"
                            onClick={() => void openBatchDraftReview(item)}
                            disabled={itemBusyGenerating}
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
                            disabled={itemDeleting || itemBusyGenerating}
                            className="flex w-11 shrink-0 items-center justify-center border-l border-stone-100 text-stone-400 transition hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      )})}
                    </div>
                    <Pagination
                      page={safeBatchReviewItemPage}
                      pageSize={batchReviewItemPageSize}
                      totalCount={batchReviewQueueItems.length}
                      onChange={handleBatchReviewItemPaginationChange}
                      ariaLabel="待审核草稿分页"
                      pageSizeAriaLabel="待审核草稿每页数量"
                      variant="compact"
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
                                        : activeBatchReviewOutreachTemplates.length > 0
                                          ? "选择模板重新套用…"
                                          : "暂无可用模板"
                                  }
                                  disabled={
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
                                  {activeBatchReviewOutreachTemplates.map((template) => (
                                    <option key={template.id} value={template.id}>
                                      {template.name}
                                      {template.id === selectedBatchReviewOutreachTemplateId
                                        ? " · 当前来源"
                                        : ""}
                                      {template.is_default ? " · 全局默认" : ""}
                                      {template.is_ready ? "" : " · 内容待完善"}
                                    </option>
                                  ))}
                                </NativeSelectField>
                              </div>
                            </section>
                            <SubjectTemplateInput
                              key={`batch-review-subject-${batchReviewThread.current_task.id}`}
                              label="邮件主题"
                              value={batchReviewSubject}
                              onChange={setBatchReviewSubject}
                              placeholder="给导师的邮件主题"
                            />
                            <EmailTemplateEditor
                              key={`batch-review-body-${batchReviewThread.current_task.id}`}
                              label="邮件正文"
                              html={batchReviewEditorHtml}
                              onChange={handleBatchReviewContentChange}
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
                                batchReviewThread.material_options.map((material) => {
                                  const checked = batchReviewSelectedMaterialIds.includes(material.id);
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
                                        onToggle={() =>
                                          setBatchReviewSelectedMaterialIds((current) =>
                                            checked
                                              ? current.filter((id) => id !== material.id)
                                              : [...current, material.id],
                                          )
                                        }
                                      />
                                      <span className="min-w-0">
                                        <span className="block truncate font-medium">
                                          {material.display_name}
                                        </span>
                                        <span className="mt-0.5 block text-xs text-stone-500">
                                          {MATERIAL_TYPE_LABELS[material.material_type]} · {formatFileSize(material.size_bytes)}
                                        </span>
                                      </span>
                                    </label>
                                  );
                                })
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
                                disabled={Boolean(activeBatchReviewAction) || !batchReviewThread}
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
                                disabled={Boolean(activeBatchReviewAction) || !batchReviewCanSubmit}
                                className="ui-btn-primary justify-center disabled:cursor-not-allowed disabled:opacity-60"
                              >
                                <CheckCircle2 className="h-4 w-4" />
                                审核通过
                              </button>
                              {canSendBatchReviewImmediately ? (
                                <button
                                  type="button"
                                  onClick={() => void handleSendBatchDraftNow()}
                                  disabled={Boolean(activeBatchReviewAction) || !batchReviewCanSubmit}
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
                                { label: "学校", value: batchReviewThread.professor.university },
                                { label: "学院", value: batchReviewThread.professor.school },
                                { label: "系所", value: batchReviewThread.professor.department },
                                {
                                  label: "研究方向",
                                  value: batchReviewThread.professor.research_direction,
                                },
                                { label: "主页链接", value: batchReviewThread.professor.profile_url },
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
                                        ? renderCandidateExternalUrl(normalizedValue)
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
                {selectedBatchTask.schedule_type === "scheduled" && selectedBatchWaitingSendCount > 0 ? (
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
                      disabled={batchBulkApprovalLoading || batchTaskDetailsLoading}
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
                      const cancellationText = getBatchTaskItemCancellationText(item);
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
                                item.draft_generation_source === "template_fallback" ? (
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
                            ) : renderBatchTaskItemAction(item)}
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
                      {selectedBatchTask.outreach_template_snapshot_version !== null ? (
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
      ) : null}
      {selectedMatchJob ? (
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
                  <h3 className="text-sm font-semibold text-stone-900">
                    导师明细
                  </h3>
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
                      {matchJobItemTotalCount} / {selectedMatchJob.target_count + selectedMatchJob.skipped_count} 位
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
                                  {item.error_message || item.skip_reason || "已完成"}
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
                                {formatDisplayTime(item.updated_at, { withSeconds: true })}
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
      ) : null}
      {selectedInformationEnrichmentJob ? (
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
                  创建于 {formatDisplayTime(selectedInformationEnrichmentJob.created_at)}
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
                    {formatDuration(selectedInformationEnrichmentJob.duration_seconds)}
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
                      {Object.entries(INFORMATION_ENRICHMENT_ITEM_STATUS_LABELS).map(
                        ([status, label]) => (
                          <option key={status} value={status}>
                            {label}
                          </option>
                        ),
                      )}
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
                                  {INFORMATION_ENRICHMENT_ITEM_STATUS_LABELS[item.status]}
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
                                        {INFORMATION_ENRICHMENT_FIELD_LABELS[field] ?? field}
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
                                    item.error_message ? "text-red-700" : "text-stone-700"
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
      ) : null}
      {selectedCrawlJob ? (
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
                  <div className="text-xs font-medium text-stone-500">
                    当前状态
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {CRAWL_JOB_STATUS_LABELS[selectedCrawlJob.status]}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    已抓页面
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedCrawlJob.page_count}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    候选导师
                  </div>
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
                  <div className="text-xs font-medium text-stone-500">
                    总 Token
                  </div>
                  <div className="mt-2 text-sm font-semibold text-stone-900">
                    {selectedCrawlJob.total_tokens.toLocaleString("zh-CN")}
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-100 bg-white px-4 py-3">
                  <div className="text-xs font-medium text-stone-500">
                    已耗时长
                  </div>
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
                  <div
                    className="mt-3 flex-1 space-y-2"
                    data-monitor-section-list
                  >
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
                  <div
                    className="mt-3 flex-1 space-y-2"
                    data-monitor-section-list
                  >
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
                                    crawlCandidateFilters
                                      .informationConditions[field] ?? "any"
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
                                ["all", "any"] as CrawlCandidateInformationMatchMode[]
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
                                aria-pressed={
                                  allFilteredCrawlCandidatesSelected
                                }
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
                              {crawlJobEnrichLoading
                                ? "补全中…"
                                : "补全缺失信息"}
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
                          ref={
                            index === 0
                              ? crawlCandidateFirstItemRef
                              : undefined
                          }
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
      ) : null}
      {resendDialogOpen ? (
        <BatchTaskResendDialog
          context={resendContext}
          loading={resendLoading}
          selectedProfessorIds={selectedResendProfessorIds}
          onSelectAll={handleSelectAllResendProfessors}
          onClear={() => setSelectedResendProfessorIds([])}
          onToggleProfessor={handleToggleResendProfessor}
          onClose={() => setResendDialogOpen(false)}
          onSubmit={() => void handleSubmitBatchResend()}
        />
      ) : null}      {selectedCandidateDetail ? (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-stone-950/35 p-4"
          onClick={candidateDetailLayer.onBackdropClick}
          onMouseDown={candidateDetailLayer.onBackdropMouseDown}
        >
          <section
            role="dialog"
            aria-label="候选导师详情"
            className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl"
            onClick={candidateDetailLayer.onContentClick}
            onMouseDown={candidateDetailLayer.onContentMouseDown}
          >
            <div className="flex items-start justify-between gap-4 border-b border-stone-200 px-6 py-5">
              <div className="min-w-0">
                <p className="text-xs font-medium uppercase tracking-[0.18em] text-stone-400">
                  {candidateEditForm ? "编辑候选导师" : "候选导师详情"}
                </p>
                <h3 className="mt-2 text-xl font-semibold text-stone-900">
                  {selectedCandidateDetail.name}
                </h3>
                <p className="mt-1 text-sm text-stone-500">
                  {candidateEditForm
                    ? "手动修正待审核资料，保存后仍可继续补全缺失信息。"
                    : selectedCandidateDetail.email?.trim() ||
                      "暂无邮箱（可尝试进行补全）"}
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap justify-end gap-2">
                {!candidateEditForm &&
                selectedCrawlJobCanReview &&
                selectedCandidateDetail.review_status === "pending" ? (
                  <button
                    type="button"
                    onClick={handleStartCandidateEdit}
                    disabled={candidateUpdateLoading}
                    className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <Pencil className="h-4 w-4" />
                    编辑
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={closeSelectedCandidateDetail}
                  disabled={candidateUpdateLoading}
                  className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                  aria-label="关闭候选导师详情"
                >
                  <X className="h-4 w-4" />
                  关闭
                </button>
              </div>
            </div>
            {candidateEditForm ? (
              <form
                onSubmit={(event) => void handleSaveCandidateEdit(event)}
                className="flex min-h-0 flex-1 flex-col"
              >
                <div
                  data-testid="candidate-detail-scroll"
                  className="grid flex-1 gap-4 overflow-y-auto overscroll-contain px-6 py-5 md:grid-cols-2"
                >
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                    姓名
                    <input
                      type="text"
                      required
                      value={candidateEditForm.name}
                      onChange={(event) =>
                        handleCandidateEditFieldChange("name", event.target.value)
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                    邮箱
                    <input
                      type="email"
                      value={candidateEditForm.email}
                      placeholder="例如 professor@example.edu"
                      onChange={(event) =>
                        handleCandidateEditFieldChange("email", event.target.value)
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                    职称
                    <input
                      type="text"
                      value={candidateEditForm.title}
                      onChange={(event) =>
                        handleCandidateEditFieldChange("title", event.target.value)
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                    部门
                    <input
                      type="text"
                      value={candidateEditForm.department}
                      onChange={(event) =>
                        handleCandidateEditFieldChange(
                          "department",
                          event.target.value,
                        )
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                    院校
                    <input
                      type="text"
                      value={candidateEditForm.university}
                      onChange={(event) =>
                        handleCandidateEditFieldChange(
                          "university",
                          event.target.value,
                        )
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500">
                    学院
                    <input
                      type="text"
                      value={candidateEditForm.school}
                      onChange={(event) =>
                        handleCandidateEditFieldChange("school", event.target.value)
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500 md:col-span-2">
                    研究方向
                    <textarea
                      value={candidateEditForm.researchDirection}
                      rows={3}
                      onChange={(event) =>
                        handleCandidateEditFieldChange(
                          "researchDirection",
                          event.target.value,
                        )
                      }
                      className={`${CRAWL_CANDIDATE_EDIT_INPUT_CLASS} resize-y leading-6`}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500 md:col-span-2">
                    近期论文
                    <textarea
                      value={candidateEditForm.recentPapers}
                      rows={5}
                      placeholder="每行填写一篇论文"
                      onChange={(event) =>
                        handleCandidateEditFieldChange(
                          "recentPapers",
                          event.target.value,
                        )
                      }
                      className={`${CRAWL_CANDIDATE_EDIT_INPUT_CLASS} resize-y leading-6`}
                    />
                    <span className="mt-2 block font-normal text-stone-400">
                      每行一篇，空行会在保存时自动忽略。
                    </span>
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500 md:col-span-2">
                    资料页
                    <input
                      type="url"
                      value={candidateEditForm.profileUrl}
                      placeholder="https://example.edu/profile"
                      onChange={(event) =>
                        handleCandidateEditFieldChange(
                          "profileUrl",
                          event.target.value,
                        )
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                  <label className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 text-xs font-medium text-stone-500 md:col-span-2">
                    来源页
                    <input
                      type="url"
                      value={candidateEditForm.sourceUrl}
                      placeholder="https://example.edu/faculty"
                      onChange={(event) =>
                        handleCandidateEditFieldChange(
                          "sourceUrl",
                          event.target.value,
                        )
                      }
                      className={CRAWL_CANDIDATE_EDIT_INPUT_CLASS}
                    />
                  </label>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 bg-stone-50/80 px-6 py-4">
                  <p className="max-w-xl text-pretty text-xs leading-5 text-stone-500">
                    保存后仍可补全缺失信息；已有内容（包括本次手动修改）不会被覆盖。
                  </p>
                  <div className="ml-auto flex items-center gap-2">
                    <button
                      type="button"
                      onClick={handleCancelCandidateEdit}
                      disabled={candidateUpdateLoading}
                      className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      取消
                    </button>
                    <button
                      type="submit"
                      disabled={candidateUpdateLoading}
                      className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {candidateUpdateLoading ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Save className="h-4 w-4" />
                      )}
                      {candidateUpdateLoading ? "保存中…" : "保存修改"}
                    </button>
                  </div>
                </div>
              </form>
            ) : (
              <div
                data-testid="candidate-detail-scroll"
                className="grid flex-1 gap-4 overflow-y-auto overscroll-contain px-6 py-5 md:grid-cols-2"
              >
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs font-medium text-stone-500">职称</div>
                <div className="mt-2 text-sm text-stone-900">
                  {selectedCandidateDetail.title || "暂无"}
                </div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs font-medium text-stone-500">
                  院校 / 学院
                </div>
                <div className="mt-2 text-sm text-stone-900">
                  {[
                    selectedCandidateDetail.university,
                    selectedCandidateDetail.school,
                  ]
                    .filter(Boolean)
                    .join(" / ") || "暂无"}
                </div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs font-medium text-stone-500">部门</div>
                <div className="mt-2 text-sm text-stone-900">
                  {selectedCandidateDetail.department || "暂无"}
                </div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3">
                <div className="text-xs font-medium text-stone-500">
                  审核状态
                </div>
                <div className="mt-2 text-sm text-stone-900">
                  {
                    CRAWL_CANDIDATE_REVIEW_STATUS_LABELS[
                      selectedCandidateDetail.review_status
                    ]
                  }
                </div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 md:col-span-2">
                <div className="text-xs font-medium text-stone-500">
                  研究方向
                </div>
                <div className="mt-2 text-sm leading-6 text-stone-900">
                  {selectedCandidateDetail.research_direction || "暂无"}
                </div>
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 md:col-span-2">
                <div className="text-xs font-medium text-stone-500">
                  近期论文
                </div>
                {selectedCandidateDetail.recent_papers.length > 0 ? (
                  <ul className="mt-2 space-y-2 text-sm text-stone-900">
                    {selectedCandidateDetail.recent_papers.map((paper) => (
                      <li key={paper} className="rounded-xl bg-white px-3 py-2">
                        {paper}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="mt-2 text-sm text-stone-900">暂无</div>
                )}
              </div>
              <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-4 py-3 md:col-span-2">
                <div className="text-xs font-medium text-stone-500">
                  链接信息
                </div>
                <div className="mt-2 space-y-2 text-sm text-stone-900">
                  <div>
                    <span className="text-stone-500">资料页：</span>
                    {renderCandidateExternalUrl(selectedCandidateDetail.profile_url)}
                  </div>
                  <div>
                    <span className="text-stone-500">来源页：</span>
                    {renderCandidateExternalUrl(selectedCandidateDetail.source_url)}
                  </div>
                </div>
              </div>
              {getCandidateEnrichmentFailureMessage(
                selectedCandidateDetail,
                crawlJobEvents,
              ) ? (
                <div className="rounded-2xl border border-red-200 bg-red-50/70 px-4 py-3 md:col-span-2">
                  <div className="text-xs font-medium text-red-700">
                    补全失败原因
                  </div>
                  <div className="mt-2 text-sm leading-6 text-red-900">
                    {getCandidateEnrichmentFailureMessage(
                      selectedCandidateDetail,
                      crawlJobEvents,
                    )}
                  </div>
                </div>
              ) : null}
              </div>
            )}
          </section>
        </div>
      ) : null}
      <ProfessorEditDialog
        open={professorEditDialogOpen}
        professor={professorEditProfessor}
        loading={professorEditLoading}
        onClose={closeProfessorEditDialog}
        onSaved={refreshAfterProfessorEdit}
      />
      {confirmDialog}
    </main>
  );
};
