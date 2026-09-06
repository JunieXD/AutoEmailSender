import { useBackgroundTaskNotification } from "@/app/providers/BackgroundTaskNotificationContext";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { BulkProfessorTagDialog } from "@/components/molecules/BulkProfessorTagDialog";
import { KeywordSearchScopeSelect } from "@/components/molecules/KeywordSearchScopeSelect";
import { ManagementProfessorRow } from "@/components/molecules/ManagementProfessorRow";
import { MultiSelectFilter } from "@/components/molecules/MultiSelectFilter";
import { Pagination } from "@/components/molecules/Pagination";
import { ProfessorNoteDialog } from "@/components/molecules/ProfessorNoteDialog";
import { ProfessorTagAssignmentDialog } from "@/components/molecules/ProfessorTagAssignmentDialog";
import { useNotification } from "@/context/NotificationContext";
import { useSelectionContext } from "@/context/SelectionContext";
import { downloadCommunitySharePackage } from "@/entities/community-mentor/api/communityMentors";
import {
  createProfessorInformationEnrichmentJob,
  createSingleProfessorInformationEnrichment,
  getActiveProfessorInformationEnrichment,
  getProfessorInformationEnrichmentJob,
} from "@/entities/professor/api/informationEnrichment";
import {
  archiveProfessor,
  bulkArchiveProfessors,
  bulkUpdateProfessorTags,
  createProfessor,
  createProfessorTag,
  deleteProfessorTag,
  downloadProfessorExport,
  downloadProfessorTemplate,
  getProfessor,
  getProfessorTagUsage,
  importProfessorsFromFile,
  listProfessorTags,
  restoreProfessor,
  searchManagementProfessorIds,
  searchManagementProfessors,
  updateProfessor,
  updateProfessorNote,
  updateProfessorTags,
} from "@/entities/professor/api/professors";
import { AgentProfessorSelectionBanner } from "@/features/agent-ui-handoffs/AgentProfessorSelectionBanner";
import {
  isAgentProfessorManagementHandoff,
  type AgentProfessorSelectionMode,
} from "@/features/agent-ui-handoffs/types";
import { useAgentUiHandoffSurface } from "@/features/agent-ui-handoffs/useAgentUiHandoffSurface";
import {
  buildBulkTagConfirmDescription,
  bulkTagConfirmLabels,
} from "@/features/professor-management/client/bulkTagConfirmCopy";
import {
  MANAGEMENT_KEYWORD_SEARCH_SCOPE_OPTIONS,
  NO_FIELD_FILTER_VALUE,
  NO_TAG_FILTER_VALUE,
  createDefaultManagementFilters,
  getActiveManagementAdvancedFilterCount,
  getManagementKeywordSearchPlaceholder,
  normalizeManagementKeywordSearchScopes,
  type ProfessorManagementFilterState,
  type ProfessorManagementKeywordSearchScope,
} from "@/features/professor-management/client/filterManagementProfessors";
import {
  DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS,
  DEFAULT_PROFESSOR_MANAGEMENT_SORT_KEY,
  PROFESSOR_MANAGEMENT_SORT_OPTIONS,
  type ProfessorManagementSortDirection,
  type ProfessorManagementSortKey,
} from "@/features/professor-management/client/sortManagementProfessors";
import { createCrawlJob } from "@/lib/api/crawlJobsApi";
import { downloadBlob } from "@/lib/api/download";
import {
  COMMUNITY_BATCH_CONTRIBUTION_URL,
  buildCommunityBatchContributionUrl,
  buildCommunityContributionPrefill,
} from "@/lib/communityMentorLinks";
import { safeRecordUserAction } from "@/lib/diagnosticUserActions";
import { openExternalHttpUrl } from "@/lib/externalUrls";
import {
  getStoredPageSize,
  setStoredPageSize,
  type PaginationChange,
} from "@/lib/pagination";
import { useConfirmDialog } from "@/lib/useConfirmDialog";
import type {
  ProfessorBulkTagModeDTO,
  ProfessorFilterOptionsDTO,
  ProfessorImportFileResultDTO,
  ProfessorManagementItemDTO,
  ProfessorTagDTO,
  ProfessorTagPayloadDTO,
} from "@/types";
import clsx from "clsx";
import {
  Archive,
  ArrowDown,
  ArrowUp,
  Bot,
  Check,
  Download,
  ExternalLink,
  FileSpreadsheet,
  Loader2,
  Plus,
  RefreshCcw,
  Search,
  Square,
  SquareCheck,
  SquareMinus,
  Tags,
  Upload,
  Users,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent as ReactClipboardEvent,
  type DragEvent as ReactDragEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { useSearchParams } from "react-router-dom";
import { CreateCrawlJobDialog } from "./components/CreateCrawlJobDialog";
import { ProfessorEditorDialog } from "./components/ProfessorEditorDialog";
import { ProfessorExportDialog } from "./components/ProfessorExportDialog";
import { ProfessorImportDialog } from "./components/ProfessorImportDialog";
import type { TrackedSingleInformationEnrichment } from "./model/enrichmentTracking";
import {
  buildCrawlerStartUrlsAfterMultilinePaste,
  emptyCrawlerJobForm,
  emptyProfessorForm,
  getManagementSortTriggerLabel,
  normalizeCrawlerStartUrls,
  readStoredProfessorManagementState,
  toProfessorForm,
  toProfessorPayload,
  writeStoredProfessorManagementState,
  type ArchiveFilter,
  type CrawlerJobFormState,
  type ProfessorFormState,
} from "./model/professorManagementPage";

type ManagementAgentSelectionState = {
  handoffId: string;
  selectionCount: number;
  selectionMode: AgentProfessorSelectionMode;
  selectedOnly: boolean;
  previous: {
    selectedIds: number[];
    archiveFilter: ArchiveFilter;
    filters: ProfessorManagementFilterState;
    advancedFiltersOpen: boolean;
    sortKey: ProfessorManagementSortKey;
    sortDirections: Record<
      ProfessorManagementSortKey,
      ProfessorManagementSortDirection
    >;
    currentPage: number;
  };
};
const noFieldOptionLabels = { [NO_FIELD_FILTER_VALUE]: "未填写" };
const activeInformationEnrichmentStatuses = new Set(["queued", "running"]);

type IntakeActionTone = "primary" | "amber" | "stone" | "emerald";

const PROFESSORS_PAGE_SIZE_STORAGE_KEY = "professors-management:page-size";
const managementTableColumns =
  "lg:grid-cols-[2.75rem_minmax(0,0.72fr)_minmax(0,0.74fr)_minmax(0,1.08fr)_minmax(0,1.18fr)_minmax(0,1.56fr)_minmax(0,0.78fr)_minmax(12rem,0.92fr)]";

const archiveFilterLabels: Record<ArchiveFilter, string> = {
  active: "正常",
  archived: "回收站",
  all: "全部",
};

const saveCommunitySharePackageBlob = async (
  blob: Blob,
): Promise<"saved" | "canceled"> => {
  const saveWithDesktopDialog =
    window.autoEmailSender?.saveCommunitySharePackage;
  if (!saveWithDesktopDialog) {
    downloadBlob(blob, "community-share.xlsx");
    return "saved";
  }
  const result = await saveWithDesktopDialog(await blob.arrayBuffer());
  return result.status;
};

const getActionErrorMessage = (error: unknown, fallback: string) =>
  error instanceof Error ? error.message : fallback;

const intakeActionToneClassNames: Record<IntakeActionTone, string> = {
  primary: "border-primary/25 bg-[linear-gradient(135deg,#fff7ed,#fff1f2)]",
  amber: "border-amber-200 bg-[linear-gradient(135deg,#fffbeb,#ffffff)]",
  stone: "border-stone-200 bg-white",
  emerald: "border-emerald-200 bg-[linear-gradient(135deg,#ecfdf5,#ffffff)]",
};

const intakeActionIconClassNames: Record<IntakeActionTone, string> = {
  primary:
    "border-primary/15 bg-primary text-white shadow-sm shadow-primary/20",
  amber: "border-amber-200 bg-amber-100 text-amber-700",
  stone: "border-stone-200 bg-stone-100 text-stone-700",
  emerald: "border-emerald-200 bg-emerald-100 text-emerald-700",
};

const IntakeActionCard = ({
  label,
  icon,
  tone,
  children,
}: {
  label: string;
  icon: ReactNode;
  tone: IntakeActionTone;
  children: ReactNode;
}) => (
  <article
    data-testid={`professor-intake-${label}`}
    className={clsx(
      "flex min-h-[7.5rem] flex-col justify-between gap-3 rounded-[24px] border px-4 py-4",
      intakeActionToneClassNames[tone],
    )}
  >
    <div className="flex items-center gap-3">
      <div
        className={clsx(
          "flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl border",
          intakeActionIconClassNames[tone],
        )}
      >
        {icon}
      </div>
      <div className="min-w-0">
        <h2 className="text-base font-semibold leading-6 text-stone-900">
          {label}
        </h2>
      </div>
    </div>
    <div className="flex w-full flex-wrap gap-2">{children}</div>
  </article>
);

const ProfessorsPageLoadingSkeleton = () => (
  <main
    data-testid="professors-page-loading-skeleton"
    className="mx-auto max-w-7xl px-6 py-8"
    aria-label="导师管理加载中"
  >
    <section className="rounded-[32px] border border-stone-200 bg-[linear-gradient(180deg,#fcfbf8,#fffaf2)] p-6 shadow-sm">
      <div className="flex flex-col gap-6">
        <div className="space-y-3">
          <div className="h-9 w-44 animate-pulse rounded-xl bg-stone-200" />
          <div className="h-4 w-56 animate-pulse rounded-full bg-stone-100" />
        </div>
        <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }, (_, index) => (
            <div
              key={index}
              className="h-[7.5rem] animate-pulse rounded-[24px] border border-stone-200 bg-white"
            />
          ))}
        </div>
        <div className="h-11 w-56 animate-pulse rounded-3xl border border-stone-200 bg-white" />
        <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(12rem,1fr)_auto_auto]">
          <div className="h-12 animate-pulse rounded-2xl border border-stone-200 bg-white" />
          <div className="h-12 animate-pulse rounded-2xl border border-stone-200 bg-white" />
          <div className="h-12 animate-pulse rounded-2xl border border-stone-200 bg-white" />
          <div className="h-12 animate-pulse rounded-2xl border border-stone-200 bg-white" />
        </div>
      </div>
    </section>

    <section className="mt-6 overflow-hidden rounded-[32px] border border-stone-200 bg-white shadow-sm">
      <div className="flex items-center gap-3 border-b border-stone-100 px-6 py-4">
        <div className="h-4 w-64 animate-pulse rounded-full bg-stone-100" />
      </div>
      <div className="hidden gap-4 border-b border-stone-100 px-6 py-4 lg:grid lg:grid-cols-[2.75rem_minmax(0,0.72fr)_minmax(0,0.74fr)_minmax(0,1.08fr)_minmax(0,1.18fr)_minmax(0,1.56fr)_minmax(0,0.78fr)_minmax(12rem,0.92fr)]">
        {Array.from({ length: 8 }, (_, index) => (
          <div
            key={index}
            className="h-3 animate-pulse rounded-full bg-stone-100"
          />
        ))}
      </div>
      <div className="divide-y divide-stone-100">
        {Array.from({ length: 6 }, (_, index) => (
          <div
            key={index}
            className="grid gap-4 px-6 py-5 lg:grid-cols-[2.75rem_minmax(0,0.72fr)_minmax(0,0.74fr)_minmax(0,1.08fr)_minmax(0,1.18fr)_minmax(0,1.56fr)_minmax(0,0.78fr)_minmax(12rem,0.92fr)]"
          >
            {Array.from({ length: 8 }, (_, itemIndex) => (
              <div
                key={itemIndex}
                className="h-4 animate-pulse rounded-full bg-stone-100"
              />
            ))}
          </div>
        ))}
      </div>
    </section>
  </main>
);
export const ProfessorsPage = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const linkedKeyword = searchParams.get("keyword")?.trim() ?? "";
  const linkedArchiveFilter: ArchiveFilter | null =
    searchParams.get("archive") === "archived" ? "archived" : null;
  const batchContributionMode =
    searchParams.get("community_contribution") === "batch";
  const { confirm, dialog: confirmDialog } = useConfirmDialog();
  const { selectedLlmProfileId } = useSelectionContext();
  const { notifyError, notifySuccess, notifyWarning } = useNotification();
  const { trackCrawlJob, trackInformationEnrichmentJob } =
    useBackgroundTaskNotification();
  const storedState = useMemo(() => {
    const state = readStoredProfessorManagementState();
    if (!linkedKeyword && linkedArchiveFilter === null) {
      return state;
    }
    const archiveFilter: ArchiveFilter = linkedArchiveFilter ?? "active";
    return {
      ...state,
      archiveFilter,
      filters: {
        ...createDefaultManagementFilters(),
        keyword: linkedKeyword,
      },
      advancedFiltersOpen: false,
      currentPage: 1,
    };
  }, [linkedArchiveFilter, linkedKeyword]);
  const [archiveFilter, setArchiveFilter] = useState<ArchiveFilter>(
    storedState.archiveFilter,
  );
  const [professors, setProfessors] = useState<ProfessorManagementItemDTO[]>(
    [],
  );
  const [professorTags, setProfessorTags] = useState<ProfessorTagDTO[]>([]);
  const [tagEditorProfessor, setTagEditorProfessor] =
    useState<ProfessorManagementItemDTO | null>(null);
  const [noteEditorProfessor, setNoteEditorProfessor] =
    useState<ProfessorManagementItemDTO | null>(null);
  const [tagEditorSelectedIds, setTagEditorSelectedIds] = useState<number[]>(
    [],
  );
  const [savingProfessorTags, setSavingProfessorTags] = useState(false);
  const [savingProfessorNote, setSavingProfessorNote] = useState(false);
  const [creatingAssignmentTag, setCreatingAssignmentTag] = useState(false);
  const [bulkTagDialogOpen, setBulkTagDialogOpen] = useState(false);
  const [savingBulkTags, setSavingBulkTags] = useState(false);
  const primaryTagSaveRef = useRef<
    Map<number, { saving: boolean; pendingTagIds: number[] | null }>
  >(new Map());
  const [filters, setFilters] = useState<ProfessorManagementFilterState>(
    storedState.filters,
  );
  const [advancedFiltersOpen, setAdvancedFiltersOpen] = useState(
    storedState.advancedFiltersOpen,
  );
  const [sortKey, setSortKey] = useState<ProfessorManagementSortKey>(
    storedState.sortKey,
  );
  const [sortDirections, setSortDirections] = useState<
    Record<ProfessorManagementSortKey, ProfessorManagementSortDirection>
  >(storedState.sortDirections);
  const [currentPage, setCurrentPage] = useState(storedState.currentPage);
  const [pageSize, setPageSize] = useState(() =>
    getStoredPageSize(PROFESSORS_PAGE_SIZE_STORAGE_KEY),
  );
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [agentSelection, setAgentSelection] =
    useState<ManagementAgentSelectionState | null>(null);
  const agentSelectionRef = useRef<ManagementAgentSelectionState | null>(null);
  agentSelectionRef.current = agentSelection;
  const [selectingAllProfessors, setSelectingAllProfessors] = useState(false);
  const [selectedAllQueryKey, setSelectedAllQueryKey] = useState<string | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const [hasLoadedProfessors, setHasLoadedProfessors] = useState(false);
  const [totalProfessorCount, setTotalProfessorCount] = useState(0);
  const [hasAnyProfessors, setHasAnyProfessors] = useState(false);
  const [totalProfessorPages, setTotalProfessorPages] = useState(1);
  const [filterOptions, setFilterOptions] = useState<ProfessorFilterOptionsDTO>(
    {
      universities: [],
      schools: [],
      departments: [],
      titles: [],
      tags: [],
    },
  );
  const isRefreshingProfessors = hasLoadedProfessors && loading;
  const shouldShowProfessorIntakePanel =
    isRefreshingProfessors || archiveFilter === "archived" || hasAnyProfessors;
  const latestProfessorsRequestIdRef = useRef(0);
  const cursorByPageRef = useRef<Map<number, string | null>>(
    new Map([[1, null]]),
  );
  const cursorQueryKeyRef = useRef("");
  const selectedAllIdsRef = useRef<number[]>([]);
  const selectionRequestIdRef = useRef(0);
  const pendingAgentSelectionLoadRef = useRef<{
    handoffId: string;
    resolve: (visibleCount: number) => void;
    reject: (error: Error) => void;
    timeoutId: number;
  } | null>(null);
  const professorListStartRef = useRef<HTMLElement | null>(null);
  const [upsertModalOpen, setUpsertModalOpen] = useState(false);
  const [editingProfessor, setEditingProfessor] =
    useState<ProfessorManagementItemDTO | null>(null);
  const [formState, setFormState] =
    useState<ProfessorFormState>(emptyProfessorForm());
  const [savingProfessor, setSavingProfessor] = useState(false);
  const [
    startingSingleInformationEnrichmentIds,
    setStartingSingleInformationEnrichmentIds,
  ] = useState<Set<number>>(new Set());
  const [singleInformationEnrichments, setSingleInformationEnrichments] =
    useState<Record<number, TrackedSingleInformationEnrichment>>({});
  const [
    creatingBulkInformationEnrichment,
    setCreatingBulkInformationEnrichment,
  ] = useState(false);
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importingFile, setImportingFile] = useState(false);
  const [importResult, setImportResult] =
    useState<ProfessorImportFileResultDTO | null>(null);
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [exportingCommunitySharePackage, setExportingCommunitySharePackage] =
    useState(false);
  const [crawlerModalOpen, setCrawlerModalOpen] = useState(false);
  const [crawlerFormState, setCrawlerFormState] = useState<CrawlerJobFormState>(
    emptyCrawlerJobForm(),
  );
  const [creatingCrawlJob, setCreatingCrawlJob] = useState(false);
  const crawlerUrlInputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const [crawlerUrlFocusIndex, setCrawlerUrlFocusIndex] = useState<
    number | null
  >(null);
  useEffect(() => {
    if (crawlerUrlFocusIndex === null) {
      return;
    }

    crawlerUrlInputRefs.current[crawlerUrlFocusIndex]?.focus();
    setCrawlerUrlFocusIndex(null);
  }, [crawlerUrlFocusIndex, crawlerFormState.start_urls.length]);

  const managementFilterQueryKey = JSON.stringify({
    uiHandoffId:
      agentSelection?.selectedOnly === true ? agentSelection.handoffId : null,
    archiveFilter,
    keyword: filters.keyword,
    keywordSearchScopes: filters.keywordSearchScopes,
    universities: filters.universities,
    schools: filters.schools,
    departments: filters.departments,
    titles: filters.titles,
    tagIds: filters.tagIds,
  });
  const managementPageQueryKey = JSON.stringify({
    managementFilterQueryKey,
    sortKey,
    sortDirection: sortDirections[sortKey],
    pageSize,
  });
  const managementFilterQueryKeyRef = useRef(managementFilterQueryKey);
  managementFilterQueryKeyRef.current = managementFilterQueryKey;

  const settleAgentSelectionLoad = useCallback(
    (handoffId: string, error?: Error, visibleCount = 0) => {
      const pending = pendingAgentSelectionLoadRef.current;
      if (!pending || pending.handoffId !== handoffId) {
        return;
      }
      pendingAgentSelectionLoadRef.current = null;
      window.clearTimeout(pending.timeoutId);
      if (error) {
        pending.reject(error);
      } else {
        pending.resolve(visibleCount);
      }
    },
    [],
  );

  const waitForAgentSelectionLoad = useCallback((handoffId: string) => {
    const previous = pendingAgentSelectionLoadRef.current;
    if (previous) {
      window.clearTimeout(previous.timeoutId);
      previous.reject(
        new Error("新的 Agent 导师选择替换了尚未完成的页面加载。"),
      );
    }
    return new Promise<number>((resolve, reject) => {
      const timeoutId = window.setTimeout(() => {
        if (pendingAgentSelectionLoadRef.current?.handoffId === handoffId) {
          pendingAgentSelectionLoadRef.current = null;
          reject(new Error("加载 Agent 选择的导师超时，请重试。"));
        }
      }, 20_000);
      pendingAgentSelectionLoadRef.current = {
        handoffId,
        resolve,
        reject,
        timeoutId,
      };
    });
  }, []);

  useEffect(
    () => () => {
      const pending = pendingAgentSelectionLoadRef.current;
      if (pending) {
        pendingAgentSelectionLoadRef.current = null;
        window.clearTimeout(pending.timeoutId);
        pending.reject(new Error("导师管理页已关闭。"));
      }
    },
    [],
  );

  useEffect(() => {
    if (!linkedKeyword && linkedArchiveFilter === null) {
      return;
    }
    const pending = pendingAgentSelectionLoadRef.current;
    if (pending) {
      settleAgentSelectionLoad(
        pending.handoffId,
        new Error("导师管理页已切换到链接筛选。"),
      );
    }
    setArchiveFilter(linkedArchiveFilter ?? "active");
    setCurrentPage(1);
    setSelectedIds(new Set());
    agentSelectionRef.current = null;
    setAgentSelection(null);
    setSelectedAllQueryKey(null);
    selectedAllIdsRef.current = [];
    setAdvancedFiltersOpen(false);
    setSortKey(DEFAULT_PROFESSOR_MANAGEMENT_SORT_KEY);
    setSortDirections({ ...DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS });
    setFilters({ ...createDefaultManagementFilters(), keyword: linkedKeyword });
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.delete("keyword");
        next.delete("archive");
        return next;
      },
      { replace: true },
    );
  }, [
    linkedArchiveFilter,
    linkedKeyword,
    setSearchParams,
    settleAgentSelectionLoad,
  ]);
  const loadProfessors = useCallback(async () => {
    const requestId = latestProfessorsRequestIdRef.current + 1;
    latestProfessorsRequestIdRef.current = requestId;
    setLoading(true);
    try {
      if (cursorQueryKeyRef.current !== managementPageQueryKey) {
        cursorQueryKeyRef.current = managementPageQueryKey;
        cursorByPageRef.current = new Map([[1, null]]);
      }
      const data = await searchManagementProfessors({
        ui_handoff_id:
          agentSelection?.selectedOnly === true
            ? agentSelection.handoffId
            : null,
        archived: archiveFilter,
        page: currentPage,
        page_size: pageSize,
        cursor: cursorByPageRef.current.get(currentPage),
        keyword: filters.keyword,
        keyword_search_scopes: filters.keywordSearchScopes,
        universities: filters.universities,
        schools: filters.schools,
        departments: filters.departments,
        titles: filters.titles,
        tag_ids: filters.tagIds,
        sort_key: sortKey,
        sort_direction: sortDirections[sortKey],
      });
      if (latestProfessorsRequestIdRef.current !== requestId) {
        return;
      }
      setProfessors(data.items);
      setTotalProfessorCount(data.total_count);
      setHasAnyProfessors(data.has_any_professors);
      setTotalProfessorPages(data.total_pages);
      setFilterOptions(data.filter_options);
      if (data.next_cursor) {
        cursorByPageRef.current.set(data.page + 1, data.next_cursor);
      }
      setHasLoadedProfessors(true);
      if (agentSelection?.selectedOnly === true) {
        settleAgentSelectionLoad(
          agentSelection.handoffId,
          undefined,
          data.total_count,
        );
      }
    } catch (loadError) {
      if (latestProfessorsRequestIdRef.current !== requestId) {
        return;
      }
      setHasLoadedProfessors(true);
      const message = getActionErrorMessage(loadError, "加载导师列表失败");
      if (agentSelection?.selectedOnly === true) {
        settleAgentSelectionLoad(agentSelection.handoffId, new Error(message));
      }
      notifyError("加载导师列表失败", message);
    } finally {
      if (latestProfessorsRequestIdRef.current === requestId) {
        setLoading(false);
      }
    }
  }, [
    archiveFilter,
    agentSelection,
    currentPage,
    filters,
    managementPageQueryKey,
    notifyError,
    pageSize,
    sortDirections,
    sortKey,
    settleAgentSelectionLoad,
  ]);

  const loadProfessorTags = useCallback(async () => {
    try {
      const data = await listProfessorTags();
      setProfessorTags(data);
    } catch (loadError) {
      notifyError(
        "加载标签失败",
        getActionErrorMessage(loadError, "加载标签失败"),
      );
    }
  }, [notifyError]);

  useEffect(() => {
    void loadProfessors();
  }, [loadProfessors]);

  useEffect(() => {
    void loadProfessorTags();
  }, [loadProfessorTags]);

  useAgentUiHandoffSurface("professors.management", async (handoff) => {
    if (!isAgentProfessorManagementHandoff(handoff)) {
      return {
        status: "failed",
        failureMessage: "导师管理页收到的界面交接类型不匹配。",
      };
    }

    const previous: ManagementAgentSelectionState["previous"] = {
      selectedIds: Array.from(selectedIds),
      archiveFilter,
      filters: {
        ...filters,
        keywordSearchScopes: [...filters.keywordSearchScopes],
        universities: [...filters.universities],
        schools: [...filters.schools],
        departments: [...filters.departments],
        titles: [...filters.titles],
        tagIds: [...filters.tagIds],
      },
      advancedFiltersOpen,
      sortKey,
      sortDirections: { ...sortDirections },
      currentPage,
    };
    const nextSelectedIds =
      handoff.payload.selection_mode === "replace"
        ? new Set(handoff.selectedIds)
        : new Set([...selectedIds, ...handoff.selectedIds]);
    const selectedOnly = handoff.payload.display === "selected_only";
    const loadPromise = selectedOnly
      ? waitForAgentSelectionLoad(handoff.handoffId)
      : Promise.resolve(handoff.selectionCount);
    const nextAgentSelection: ManagementAgentSelectionState = {
      handoffId: handoff.handoffId,
      selectionCount: handoff.selectionCount,
      selectionMode: handoff.payload.selection_mode,
      selectedOnly,
      previous,
    };

    latestProfessorsRequestIdRef.current += 1;
    selectionRequestIdRef.current += 1;
    setSelectedIds(nextSelectedIds);
    setSelectedAllQueryKey(null);
    selectedAllIdsRef.current = [];
    agentSelectionRef.current = nextAgentSelection;
    setAgentSelection(nextAgentSelection);
    if (selectedOnly) {
      setArchiveFilter(handoff.payload.archive_scope);
      setFilters(createDefaultManagementFilters());
      setAdvancedFiltersOpen(false);
      setSortKey(DEFAULT_PROFESSOR_MANAGEMENT_SORT_KEY);
      setSortDirections({ ...DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS });
      setCurrentPage(1);
    }

    try {
      const visibleCount = await loadPromise;
      if (selectedOnly && visibleCount !== handoff.selectionCount) {
        throw new Error(
          `Agent 选择包含 ${handoff.selectionCount} 位导师，但当前只能显示 ${visibleCount} 位；请重新筛选。`,
        );
      }
    } catch (error) {
      if (agentSelectionRef.current === nextAgentSelection) {
        latestProfessorsRequestIdRef.current += 1;
        setArchiveFilter(previous.archiveFilter);
        setFilters(previous.filters);
        setAdvancedFiltersOpen(previous.advancedFiltersOpen);
        setSortKey(previous.sortKey);
        setSortDirections(previous.sortDirections);
        setCurrentPage(previous.currentPage);
        setSelectedIds(new Set(previous.selectedIds));
        setSelectedAllQueryKey(null);
        selectedAllIdsRef.current = [];
        agentSelectionRef.current = null;
        setAgentSelection(null);
      }
      throw error;
    }
    return {
      status: "applied",
      result: {
        selected_count: nextSelectedIds.size,
        handoff_selection_count: handoff.selectionCount,
        selection_mode: handoff.payload.selection_mode,
        display: handoff.payload.display,
        surface: handoff.surface,
      },
    };
  });

  const restoreAgentSelectionView = (
    selection: ManagementAgentSelectionState,
  ) => {
    setArchiveFilter(selection.previous.archiveFilter);
    setFilters(selection.previous.filters);
    setAdvancedFiltersOpen(selection.previous.advancedFiltersOpen);
    setSortKey(selection.previous.sortKey);
    setSortDirections(selection.previous.sortDirections);
    setCurrentPage(selection.previous.currentPage);
  };

  const exitAgentSelectedOnly = () => {
    if (!agentSelection?.selectedOnly) {
      return;
    }
    settleAgentSelectionLoad(
      agentSelection.handoffId,
      new Error("用户在页面加载完成前退出了仅看已选。"),
    );
    restoreAgentSelectionView(agentSelection);
    const next = { ...agentSelection, selectedOnly: false };
    agentSelectionRef.current = next;
    setAgentSelection(next);
  };

  const undoAgentSelection = () => {
    if (!agentSelection) {
      return;
    }
    settleAgentSelectionLoad(
      agentSelection.handoffId,
      new Error("用户在页面加载完成前撤销了 Agent 选择。"),
    );
    if (agentSelection.selectedOnly) {
      restoreAgentSelectionView(agentSelection);
    }
    setSelectedIds(new Set(agentSelection.previous.selectedIds));
    setSelectedAllQueryKey(null);
    selectedAllIdsRef.current = [];
    agentSelectionRef.current = null;
    setAgentSelection(null);
  };

  const clearAgentSelection = () => {
    if (!agentSelection) {
      return;
    }
    settleAgentSelectionLoad(
      agentSelection.handoffId,
      new Error("用户在页面加载完成前清除了 Agent 选择。"),
    );
    if (agentSelection.selectedOnly) {
      restoreAgentSelectionView(agentSelection);
    }
    setSelectedIds(new Set());
    setSelectedAllQueryKey(null);
    selectedAllIdsRef.current = [];
    agentSelectionRef.current = null;
    setAgentSelection(null);
  };

  const refreshProfessorAfterInformationEnrichment = useCallback(
    async (professorId: number) => {
      await loadProfessors();
      try {
        const refreshed = await getProfessor(professorId);
        setProfessors((previous) =>
          previous.map((professor) =>
            professor.id === professorId
              ? {
                  ...professor,
                  email: refreshed.email,
                  title: refreshed.title,
                  department: refreshed.department,
                  research_direction: refreshed.research_direction,
                  recent_papers: refreshed.recent_papers ?? [],
                  updated_at: refreshed.updated_at,
                }
              : professor,
          ),
        );
        if (editingProfessor?.id !== professorId) {
          return;
        }
        setEditingProfessor((current) =>
          current?.id === professorId
            ? {
                ...current,
                email: refreshed.email,
                title: refreshed.title,
                department: refreshed.department,
                research_direction: refreshed.research_direction,
                recent_papers: refreshed.recent_papers ?? [],
                updated_at: refreshed.updated_at,
              }
            : current,
        );
        setFormState((previous) => ({
          ...previous,
          email: previous.email.trim()
            ? previous.email
            : (refreshed.email ?? ""),
          title: previous.title.trim()
            ? previous.title
            : (refreshed.title ?? ""),
          department: previous.department.trim()
            ? previous.department
            : (refreshed.department ?? ""),
          research_direction: previous.research_direction.trim()
            ? previous.research_direction
            : (refreshed.research_direction ?? ""),
          recent_papers_text: previous.recent_papers_text.trim()
            ? previous.recent_papers_text
            : (refreshed.recent_papers ?? []).join("\n"),
        }));
      } catch {
        // The list refresh already reflects committed fields; detail refresh is best effort.
      }
    },
    [
      editingProfessor,
      loadProfessors,
      setEditingProfessor,
      setFormState,
      setProfessors,
    ],
  );

  const handleSingleInformationEnrichmentFinished = useCallback(
    async (professorId: number) => {
      try {
        await refreshProfessorAfterInformationEnrichment(professorId);
      } finally {
        setSingleInformationEnrichments((previous) => {
          const next = { ...previous };
          delete next[professorId];
          return next;
        });
      }
    },
    [refreshProfessorAfterInformationEnrichment],
  );

  useEffect(() => {
    const activeEntries = Object.entries(singleInformationEnrichments).filter(
      ([, tracked]) =>
        activeInformationEnrichmentStatuses.has(tracked.job.status),
    );
    if (activeEntries.length === 0) {
      return;
    }
    let disposed = false;
    const poll = async () => {
      await Promise.all(
        activeEntries.map(async ([professorIdText, tracked]) => {
          try {
            const job = await getProfessorInformationEnrichmentJob(
              tracked.job.id,
            );
            if (disposed) {
              return;
            }
            const professorId = Number(professorIdText);
            if (activeInformationEnrichmentStatuses.has(job.status)) {
              setSingleInformationEnrichments((previous) => {
                const current = previous[professorId];
                if (!current || current.job.updated_at === job.updated_at) {
                  return previous;
                }
                return { ...previous, [professorId]: { ...current, job } };
              });
              return;
            }
            await handleSingleInformationEnrichmentFinished(professorId);
          } catch {
            // Transient polling failures are retried without ending the task state.
          }
        }),
      );
    };
    void poll();
    const intervalId = window.setInterval(() => void poll(), 2500);
    return () => {
      disposed = true;
      window.clearInterval(intervalId);
    };
  }, [handleSingleInformationEnrichmentFinished, singleInformationEnrichments]);

  useEffect(() => {
    if (agentSelection?.selectedOnly) {
      return;
    }
    writeStoredProfessorManagementState({
      archiveFilter,
      filters,
      advancedFiltersOpen,
      sortKey,
      sortDirections,
      currentPage,
    });
  }, [
    agentSelection?.selectedOnly,
    archiveFilter,
    advancedFiltersOpen,
    currentPage,
    filters,
    sortDirections,
    sortKey,
  ]);

  const activeAdvancedFilterCount = useMemo(
    () => getActiveManagementAdvancedFilterCount(filters),
    [filters],
  );
  const tagFilterEntries = useMemo(
    () => [
      ...filterOptions.tags.map((tag) => ({
        value: String(tag.id),
        label: tag.name,
      })),
      { value: NO_TAG_FILTER_VALUE, label: "无" },
    ],
    [filterOptions.tags],
  );
  const tagOptionLabels = useMemo(
    () =>
      Object.fromEntries(
        tagFilterEntries.map((entry) => [entry.value, entry.label]),
      ),
    [tagFilterEntries],
  );
  const currentSortDirection = sortDirections[sortKey];
  const visibleProfessors = professors;

  const updateFilters = (
    nextFilters: Partial<ProfessorManagementFilterState>,
  ) => {
    setCurrentPage(1);
    setFilters((previous) => ({ ...previous, ...nextFilters }));
  };

  const setManagementKeywordSearchScopes = (
    keywordSearchScopes: ProfessorManagementKeywordSearchScope[],
  ) => {
    setCurrentPage(1);
    setFilters((previous) => ({
      ...previous,
      keywordSearchScopes:
        normalizeManagementKeywordSearchScopes(keywordSearchScopes),
    }));
  };

  const setFilterValues = (
    key: "universities" | "schools" | "departments" | "titles" | "tagIds",
    nextValues: string[],
  ) => {
    setCurrentPage(1);
    if (key === "universities") {
      setFilterOptions((previous) => ({
        ...previous,
        schools: [],
        departments: [],
      }));
    } else if (key === "schools") {
      setFilterOptions((previous) => ({ ...previous, departments: [] }));
    }
    setFilters((previous) => {
      if (key === "universities") {
        return {
          ...previous,
          universities: nextValues,
          schools: [],
          departments: [],
        };
      }
      if (key === "schools") {
        return { ...previous, schools: nextValues, departments: [] };
      }
      return { ...previous, [key]: nextValues };
    });
  };

  const clearAdvancedFilters = () => {
    setCurrentPage(1);
    setFilters((previous) => ({
      ...previous,
      universities: [],
      schools: [],
      departments: [],
      titles: [],
      tagIds: [],
    }));
  };

  const resetAllFilters = () => {
    setCurrentPage(1);
    setFilters(createDefaultManagementFilters());
    setSortKey(DEFAULT_PROFESSOR_MANAGEMENT_SORT_KEY);
    setSortDirections({ ...DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS });
  };

  const totalPages = totalProfessorPages;
  const safeCurrentPage = Math.min(currentPage, totalPages);
  useEffect(() => {
    setCurrentPage((page) => Math.min(page, totalPages));
  }, [totalPages]);
  const paginatedProfessors = visibleProfessors;
  const isProfessorSelectable = useCallback(
    (professor: ProfessorManagementItemDTO) =>
      archiveFilter === "archived"
        ? Boolean(professor.archived_at)
        : archiveFilter === "active"
          ? !professor.archived_at
          : true,
    [archiveFilter],
  );
  const someFilteredSelected = selectedIds.size > 0;
  const allFilteredSelected =
    selectedAllIdsRef.current.length > 0 &&
    selectedAllQueryKey === managementFilterQueryKey;
  const openCreateModal = () => {
    setEditingProfessor(null);
    setFormState(emptyProfessorForm());
    setUpsertModalOpen(true);
  };

  const handlePaginationChange = (change: PaginationChange) => {
    setCurrentPage(change.page);
    setPageSize(change.pageSize);
    setStoredPageSize(PROFESSORS_PAGE_SIZE_STORAGE_KEY, change.pageSize);
  };

  const handleToggleFilteredSelection = async () => {
    if (selectingAllProfessors) {
      return;
    }
    if (allFilteredSelected) {
      const selectedAllIds = new Set(selectedAllIdsRef.current);
      setSelectedIds((previous) => {
        const next = new Set(previous);
        selectedAllIds.forEach((id) => next.delete(id));
        return next;
      });
      selectedAllIdsRef.current = [];
      setSelectedAllQueryKey(null);
      return;
    }

    const requestId = selectionRequestIdRef.current + 1;
    selectionRequestIdRef.current = requestId;
    const requestQueryKey = managementFilterQueryKey;
    setSelectingAllProfessors(true);
    try {
      const result = await searchManagementProfessorIds({
        ui_handoff_id:
          agentSelection?.selectedOnly === true
            ? agentSelection.handoffId
            : null,
        archived: archiveFilter,
        page: 1,
        page_size: pageSize,
        keyword: filters.keyword,
        keyword_search_scopes: filters.keywordSearchScopes,
        universities: filters.universities,
        schools: filters.schools,
        departments: filters.departments,
        titles: filters.titles,
        tag_ids: filters.tagIds,
        sort_key: sortKey,
        sort_direction: currentSortDirection,
      });
      if (
        selectionRequestIdRef.current !== requestId ||
        managementFilterQueryKeyRef.current !== requestQueryKey
      ) {
        return;
      }
      selectedAllIdsRef.current = result.ids;
      setSelectedIds((previous) => new Set([...previous, ...result.ids]));
      setSelectedAllQueryKey(result.ids.length > 0 ? requestQueryKey : null);
    } catch (selectionError) {
      if (selectionRequestIdRef.current !== requestId) {
        return;
      }
      notifyError(
        "选择筛选结果失败",
        getActionErrorMessage(selectionError, "无法选择全部筛选结果"),
      );
    } finally {
      if (selectionRequestIdRef.current === requestId) {
        setSelectingAllProfessors(false);
      }
    }
  };
  const openEditModal = (professor: ProfessorManagementItemDTO) => {
    setEditingProfessor(professor);
    setFormState(toProfessorForm(professor));
    setUpsertModalOpen(true);
    void getActiveProfessorInformationEnrichment(professor.id)
      .then((result) => {
        if (result.active && result.job) {
          trackInformationEnrichmentJob(result.job, {
            professorName: professor.name,
          });
          setSingleInformationEnrichments((previous) => ({
            ...previous,
            [professor.id]: { job: result.job!, professorName: professor.name },
          }));
          return;
        }
        setSingleInformationEnrichments((previous) => {
          if (!previous[professor.id]) {
            return previous;
          }
          const next = { ...previous };
          delete next[professor.id];
          return next;
        });
      })
      .catch(() => undefined);
  };

  const closeUpsertModal = () => {
    if (savingProfessor) {
      return;
    }
    setUpsertModalOpen(false);
  };

  const handleSaveProfessor = async () => {
    setSavingProfessor(true);
    try {
      const payload = toProfessorPayload(formState);
      if (editingProfessor) {
        await updateProfessor(editingProfessor.id, payload);
        notifySuccess(`已更新导师“${payload.name}”`);
      } else {
        await createProfessor(payload);
        notifySuccess(`已新增导师“${payload.name}”`);
      }
      setUpsertModalOpen(false);
      await loadProfessors();
    } catch (saveError) {
      notifyError(
        "保存导师失败",
        getActionErrorMessage(saveError, "保存导师失败"),
      );
    } finally {
      setSavingProfessor(false);
    }
  };

  const handleContributeProfessor = async () => {
    if (!editingProfessor) {
      return;
    }
    const payload = toProfessorPayload(formState);
    const requiredFields = [
      ["姓名", payload.name],
      ["工作邮箱", payload.email],
      ["学校", payload.university],
      ["学院或研究院", payload.school],
      ["发现导师的来源页", payload.source_url],
    ] as const;
    const missingLabels = requiredFields
      .filter(([, value]) => !value?.trim())
      .map(([label]) => label);
    const prefill = buildCommunityContributionPrefill(payload);
    if (prefill.exceedsSafeLength) {
      notifyWarning(
        "导师基本信息过长",
        "基本字段过长，无法生成可靠的 GitHub 预填链接，请先缩短。",
      );
      return;
    }
    const omittedLabels = prefill.omittedFields.map((field) =>
      field === "research_direction" ? "研究方向" : "代表论文",
    );
    const prefillDescription =
      missingLabels.length > 0
        ? `已预填现有信息；提交前请补全：${missingLabels.join("、")}。`
        : "已预填现有信息；提交前请核对。";
    const lengthDescription =
      omittedLabels.length > 0
        ? `${omittedLabels.join("和")}因过长未带入；完整投稿请使用批量“贡献到社区”。`
        : null;
    const confirmed = await confirm({
      title: `贡献“${payload.name || "这位导师"}”到社区？`,
      description: [prefillDescription, lengthDescription]
        .filter(Boolean)
        .join("\n\n"),
      confirmLabel: "打开已预填的投稿表",
      cancelLabel: "暂不投稿",
    });
    if (!confirmed) {
      return;
    }
    openExternalHttpUrl(prefill.url);
    notifySuccess(
      "已打开预填投稿表",
      omittedLabels.length > 0
        ? `${omittedLabels.join("和")}因过长未带入；完整投稿请使用批量“贡献到社区”。`
        : "请在 GitHub 中核对并提交。",
    );
  };

  const handleBulkExportCommunitySharePackage = async () => {
    const selectedProfessorIds = Array.from(selectedIds);
    if (selectedProfessorIds.length === 0) {
      return;
    }
    if (selectedProfessorIds.length > 500) {
      notifyWarning("选择数量过多", "一次最多导出 500 位导师的社区共享包。");
      return;
    }

    // `professors` is only the current server-paginated page. It is safe for
    // immediate UI hints, but the export itself must use the complete ID set.
    const selectedProfessorsOnCurrentPage = professors.filter((professor) =>
      selectedIds.has(professor.id),
    );
    const incompleteProfessor = selectedProfessorsOnCurrentPage.find(
      (professor) => !professor.email || !professor.source_url,
    );
    if (incompleteProfessor) {
      notifyWarning(
        "暂时无法导出",
        `导师“${incompleteProfessor.name}”缺少公开工作邮箱或发现来源页，请先补全。`,
      );
      return;
    }
    setExportingCommunitySharePackage(true);
    try {
      const blob = await downloadCommunitySharePackage(selectedProfessorIds);
      const saveStatus = await saveCommunitySharePackageBlob(blob);
      if (saveStatus === "canceled") {
        notifyWarning(
          "已取消保存",
          "共享包未保存，因此没有打开 GitHub 投稿页。",
        );
        return;
      }
      openExternalHttpUrl(
        buildCommunityBatchContributionUrl(selectedProfessorsOnCurrentPage),
      );
      notifySuccess(
        "共享包已保存",
        `请将包含 ${selectedProfessorIds.length} 位导师的 XLSX 拖入已打开的 GitHub 表单；私有数据未导出。`,
      );
    } catch (error) {
      notifyError(
        "贡献准备失败",
        getActionErrorMessage(error, "社区共享包生成失败，请稍后重试。"),
      );
    } finally {
      setExportingCommunitySharePackage(false);
    }
  };

  const handleSingleInformationEnrichment = async () => {
    if (!editingProfessor) {
      return;
    }
    if (!selectedLlmProfileId) {
      notifyWarning("请先选择模型", "智能补全会使用当前顶部栏选择的模型。");
      return;
    }
    const professorId = editingProfessor.id;
    setStartingSingleInformationEnrichmentIds((previous) =>
      new Set(previous).add(professorId),
    );
    try {
      const job = await createSingleProfessorInformationEnrichment(
        professorId,
        selectedLlmProfileId,
      );
      setSingleInformationEnrichments((previous) => ({
        ...previous,
        [professorId]: { job, professorName: editingProfessor.name },
      }));
      trackInformationEnrichmentJob(job, {
        professorName: editingProfessor.name,
      });
    } catch (error) {
      notifyError(
        `无法补全：${editingProfessor.name}`,
        getActionErrorMessage(error, "发起信息补全失败"),
      );
    } finally {
      setStartingSingleInformationEnrichmentIds((previous) => {
        const next = new Set(previous);
        next.delete(professorId);
        return next;
      });
    }
  };

  const handleBulkInformationEnrichment = async () => {
    if (archiveFilter === "archived") {
      notifyWarning("回收站导师不可补全", "请先恢复导师后再发起信息补全。");
      return;
    }
    if (selectedIds.size === 0) {
      notifyWarning("请先选择导师", "选择至少一位导师后再批量智能补全。");
      return;
    }
    if (!selectedLlmProfileId) {
      notifyWarning("请先选择模型", "批量智能补全会使用当前顶部栏选择的模型。");
      return;
    }
    const confirmed = await confirm({
      title: `补全选中的 ${selectedIds.size} 位导师信息？`,
      description: "将访问导师主页补全空缺信息，不覆盖现有内容，并消耗 Token。",
      confirmLabel: "开始补全",
      cancelLabel: "取消",
    });
    if (!confirmed) {
      return;
    }
    setCreatingBulkInformationEnrichment(true);
    try {
      const job = await createProfessorInformationEnrichmentJob({
        professorIds: Array.from(selectedIds),
        llmProfileId: selectedLlmProfileId,
      });
      trackInformationEnrichmentJob(job);
      notifySuccess(
        "批量信息补全已创建",
        `已排队 ${job.queued_count} 位，跳过 ${job.skipped_count} 位，可在任务中心查看。`,
      );
    } catch (error) {
      notifyError(
        "创建批量信息补全失败",
        getActionErrorMessage(error, "创建批量信息补全失败"),
      );
    } finally {
      setCreatingBulkInformationEnrichment(false);
    }
  };

  const savePrimaryTagOrder = async (
    professor: ProfessorManagementItemDTO,
    tagIds: number[],
  ) => {
    try {
      const updatedProfessor = await updateProfessorTags(professor.id, tagIds);
      setProfessors((previous) =>
        previous.map((item) =>
          item.id === updatedProfessor.id ? updatedProfessor : item,
        ),
      );
      notifySuccess(`已更新“${updatedProfessor.name}”的标签排序`);
    } catch (saveError) {
      notifyError(
        "保存标签排序失败",
        getActionErrorMessage(saveError, "保存标签排序失败"),
      );
    }
  };

  const handlePrimaryTagSelect = async (
    professor: ProfessorManagementItemDTO,
    tagId: number,
  ) => {
    if (professor.tags[0]?.id === tagId) {
      return;
    }

    const nextTagIds = [
      tagId,
      ...professor.tags
        .map((tag) => tag.id)
        .filter((currentTagId) => currentTagId !== tagId),
    ];

    const existingState = primaryTagSaveRef.current.get(professor.id);
    if (existingState?.saving) {
      existingState.pendingTagIds = nextTagIds;
      return;
    }

    const saveState = { saving: true, pendingTagIds: null as number[] | null };
    primaryTagSaveRef.current.set(professor.id, saveState);

    try {
      let pendingTagIds: number[] | null = nextTagIds;
      while (pendingTagIds) {
        const currentTagIds = pendingTagIds;
        pendingTagIds = null;
        await savePrimaryTagOrder(professor, currentTagIds);
        pendingTagIds = saveState.pendingTagIds;
        saveState.pendingTagIds = null;
      }
    } finally {
      primaryTagSaveRef.current.delete(professor.id);
    }
  };

  const openTagEditor = (professor: ProfessorManagementItemDTO) => {
    setTagEditorProfessor(professor);
    setTagEditorSelectedIds(professor.tags.map((tag) => tag.id));
  };

  const closeTagEditor = () => {
    if (savingProfessorTags || creatingAssignmentTag) {
      return;
    }
    setTagEditorProfessor(null);
    setTagEditorSelectedIds([]);
  };

  const saveTagEditor = async () => {
    if (!tagEditorProfessor) {
      return;
    }
    setSavingProfessorTags(true);
    try {
      const updatedProfessor = await updateProfessorTags(
        tagEditorProfessor.id,
        tagEditorSelectedIds,
      );
      setProfessors((previous) =>
        previous.map((item) =>
          item.id === updatedProfessor.id ? updatedProfessor : item,
        ),
      );
      notifySuccess(`已更新“${updatedProfessor.name}”的标签`);
      setTagEditorProfessor(null);
      setTagEditorSelectedIds([]);
    } catch (saveError) {
      notifyError(
        "保存导师标签失败",
        getActionErrorMessage(saveError, "保存导师标签失败"),
      );
    } finally {
      setSavingProfessorTags(false);
    }
  };

  const saveProfessorNote = async (note: string) => {
    if (!noteEditorProfessor) {
      return;
    }
    setSavingProfessorNote(true);
    try {
      const updated = await updateProfessorNote(noteEditorProfessor.id, note);
      setProfessors((previous) =>
        previous.map((professor) =>
          professor.id === updated.id
            ? {
                ...professor,
                personal_note: updated.personal_note,
                updated_at: updated.updated_at,
              }
            : professor,
        ),
      );
      setNoteEditorProfessor(null);
      notifySuccess(`已更新“${noteEditorProfessor.name}”的备注`);
    } catch (saveError) {
      notifyError(
        "保存备注失败",
        getActionErrorMessage(saveError, "保存备注失败"),
      );
    } finally {
      setSavingProfessorNote(false);
    }
  };

  const createAndRegisterProfessorTag = async (
    payload: ProfessorTagPayloadDTO,
  ): Promise<ProfessorTagDTO | null> => {
    try {
      const createdTag = await createProfessorTag(payload);
      setProfessorTags((previous) => [...previous, createdTag]);
      notifySuccess(`已创建标签“${createdTag.name}”`);
      return createdTag;
    } catch (createError) {
      notifyError(
        "创建标签失败",
        getActionErrorMessage(createError, "创建标签失败"),
      );
      return null;
    }
  };

  const handleCreateProfessorTag = async (
    payload: ProfessorTagPayloadDTO,
  ): Promise<ProfessorTagDTO | null> => {
    const createdTag = await createAndRegisterProfessorTag(payload);
    if (!createdTag) {
      return null;
    }
    setFormState((previous) => ({
      ...previous,
      tag_ids: previous.tag_ids.includes(createdTag.id)
        ? previous.tag_ids
        : [...previous.tag_ids, createdTag.id],
    }));
    return createdTag;
  };

  const handleCreateAssignmentTag = async (payload: ProfessorTagPayloadDTO) => {
    setCreatingAssignmentTag(true);
    try {
      return await createAndRegisterProfessorTag(payload);
    } finally {
      setCreatingAssignmentTag(false);
    }
  };

  const saveBulkTags = async ({
    mode,
    tagIds,
  }: {
    mode: ProfessorBulkTagModeDTO;
    tagIds: number[];
  }) => {
    if (selectedIds.size === 0) {
      notifyWarning("请先选择导师", "选择至少一位导师后再批量修改标签。");
      return;
    }
    const labels = bulkTagConfirmLabels[mode];
    const tagNames = tagIds
      .map((tagId) => professorTags.find((tag) => tag.id === tagId)?.name)
      .filter((tagName): tagName is string => Boolean(tagName));
    const confirmed = await confirm({
      title: labels.title,
      description: buildBulkTagConfirmDescription({
        mode,
        selectedCount: selectedIds.size,
        tagNames,
      }),
      confirmLabel: labels.confirmLabel,
      cancelLabel: "取消",
      tone: mode === "remove" || mode === "replace" ? "danger" : "neutral",
    });
    if (!confirmed) {
      return;
    }
    setSavingBulkTags(true);
    try {
      const result = await bulkUpdateProfessorTags({
        professor_ids: Array.from(selectedIds),
        mode,
        tag_ids: tagIds,
      });
      await loadProfessors();
      notifySuccess(
        "标签已更新",
        `已更新 ${result.affected_count} 位导师的标签。`,
      );
      setBulkTagDialogOpen(false);
    } catch (saveError) {
      notifyError(
        "批量修改标签失败",
        getActionErrorMessage(saveError, "批量修改标签失败"),
      );
    } finally {
      setSavingBulkTags(false);
    }
  };

  const handleDeleteProfessorTag = async (tag: ProfessorTagDTO) => {
    let usageRevision = "";
    let usageProfessors: Array<{
      id: number;
      name: string;
      email: string | null;
      university: string | null;
      school: string | null;
    }> = [];
    try {
      const usage = await getProfessorTagUsage(tag.id);
      usageRevision = usage.revision;
      usageProfessors = usage.professors;
    } catch (usageError) {
      notifyError(
        "查询标签使用情况失败",
        getActionErrorMessage(usageError, "查询标签使用情况失败"),
      );
      return;
    }

    const usageDescription =
      usageProfessors.length === 0
        ? "是否要删除这个标签？"
        : [
            "是否要删除这个标签？下列导师该标签将删除",
            ...usageProfessors.map((professor) => {
              const context = [professor.university, professor.school]
                .filter(Boolean)
                .join(" / ");
              return context
                ? `${professor.name}（${context}）`
                : professor.name;
            }),
          ].join("\n");

    const confirmed = await confirm({
      title: `删除标签“${tag.name}”？`,
      description: usageDescription,
      confirmLabel: "确认删除",
      cancelLabel: "先不删除",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    try {
      const result = await deleteProfessorTag(tag.id, usageRevision);
      setProfessorTags((previous) =>
        previous.filter((item) => item.id !== tag.id),
      );
      setFormState((previous) => ({
        ...previous,
        tag_ids: previous.tag_ids.filter((tagId) => tagId !== tag.id),
      }));
      setProfessors((previous) =>
        previous.map((professor) => ({
          ...professor,
          tags: professor.tags.filter((item) => item.id !== tag.id),
        })),
      );
      notifySuccess("删除标签成功", result.message);
      await loadProfessors();
    } catch (deleteError) {
      notifyError(
        "删除标签失败",
        getActionErrorMessage(deleteError, "删除标签失败"),
      );
    }
  };

  const handleArchiveProfessor = async (
    professor: ProfessorManagementItemDTO,
  ) => {
    const confirmed = await confirm({
      title: `将“${professor.name}”移入回收站？`,
      description:
        "移入回收站后，这位导师会从首页和正常列表中隐藏，历史任务和通信记录仍会保留。尚未完成的邮件任务、匹配分析和信息补全任务会自动取消。正在发送的邮件任务无法取消，系统会提示对应的任务 ID。",
      confirmLabel: "确认移入",
      cancelLabel: "取消",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    try {
      const result = await archiveProfessor(professor.id);
      const automaticActions = [
        (result.canceled_email_task_ids?.length ?? 0) > 0
          ? `邮件任务：ID ${result.canceled_email_task_ids?.join("、")}`
          : null,
        (result.canceled_match_analysis_item_ids?.length ?? 0) > 0
          ? `匹配分析项：ID ${result.canceled_match_analysis_item_ids?.join("、")}`
          : null,
        (result.canceled_information_enrichment_task_ids?.length ?? 0) > 0
          ? `信息补全项：ID ${result.canceled_information_enrichment_task_ids?.join("、")}`
          : null,
      ].filter(Boolean);
      notifySuccess(
        "操作成功",
        automaticActions.length > 0
          ? `${result.message}。系统同时取消了：${automaticActions.join("；")}。`
          : result.message,
      );
      await loadProfessors();
    } catch (archiveError) {
      notifyError(
        "移入回收站失败",
        getActionErrorMessage(archiveError, "移入回收站失败"),
      );
    }
  };

  const handleBulkArchive = async () => {
    if (selectedIds.size === 0) {
      return;
    }
    const confirmed = await confirm({
      title: `将选中的 ${selectedIds.size} 位导师移入回收站？`,
      description:
        "移入后，这些导师会从首页和正常列表中隐藏，历史任务和通信记录仍会保留。尚未完成的邮件任务、匹配分析和信息补全任务会自动取消。",
      confirmLabel: "确认移入",
      cancelLabel: "取消",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    try {
      const result = await bulkArchiveProfessors({ ids: [...selectedIds] });
      setSelectedIds(new Set());
      agentSelectionRef.current = null;
      setAgentSelection(null);
      setSelectedAllQueryKey(null);
      selectedAllIdsRef.current = [];
      notifySuccess("操作成功", result.message);
      await loadProfessors();
    } catch (archiveError) {
      notifyError(
        "批量移入回收站失败",
        getActionErrorMessage(archiveError, "批量移入回收站失败"),
      );
    }
  };

  const handleBulkRestore = async () => {
    if (selectedIds.size === 0) {
      return;
    }
    const confirmed = await confirm({
      title: `恢复选中的 ${selectedIds.size} 位导师？`,
      description: "恢复后会回到正常列表，可继续参与首页筛选和任务创建。",
      confirmLabel: "确认恢复",
      cancelLabel: "取消",
    });
    if (!confirmed) {
      return;
    }

    const results = await Promise.allSettled(
      [...selectedIds].map((id) => restoreProfessor(id)),
    );
    const failedCount = results.filter(
      (item) => item.status === "rejected",
    ).length;
    const successCount = results.length - failedCount;

    if (successCount > 0) {
      notifySuccess("操作成功", `已恢复 ${successCount} 位导师。`);
    }
    if (failedCount > 0) {
      notifyWarning(
        "部分恢复失败",
        `有 ${failedCount} 位导师恢复失败，请稍后重试。`,
      );
    }
    if (successCount === 0) {
      notifyError("批量恢复失败", "所选导师均未恢复成功，请稍后重试。");
    }
    setSelectedIds(new Set());
    agentSelectionRef.current = null;
    setAgentSelection(null);
    setSelectedAllQueryKey(null);
    selectedAllIdsRef.current = [];
    await loadProfessors();
  };

  const handleRestoreProfessor = async (
    professor: ProfessorManagementItemDTO,
  ) => {
    try {
      const result = await restoreProfessor(professor.id);
      notifySuccess("操作成功", result.message);
      await loadProfessors();
    } catch (restoreError) {
      notifyError(
        "恢复导师失败",
        getActionErrorMessage(restoreError, "恢复导师失败"),
      );
    }
  };

  const handleDownloadTemplate = async (format: "xlsx" | "csv") => {
    try {
      await downloadProfessorTemplate(format);
    } catch (downloadError) {
      notifyError(
        "下载导入模板失败",
        getActionErrorMessage(downloadError, "下载导入模板失败"),
      );
    }
  };

  const handleDownloadExport = async (format: "xlsx" | "csv") => {
    try {
      await downloadProfessorExport(format);
    } catch (downloadError) {
      notifyError(
        "导出导师信息失败",
        getActionErrorMessage(downloadError, "导出导师信息失败"),
      );
    }
  };

  const handleChooseImportFile = (event: ChangeEvent<HTMLInputElement>) => {
    const nextFile = event.target.files?.[0] ?? null;
    setImportFile(nextFile);
    setImportResult(null);
  };

  const handleChooseDesktopImportFile = async () => {
    try {
      const selectedFile =
        await window.autoEmailSender?.selectProfessorImportFile?.();
      if (!selectedFile) {
        return;
      }

      setImportFile(
        new File([selectedFile.data], selectedFile.name, {
          type: selectedFile.type,
        }),
      );
      setImportResult(null);
    } catch (selectError) {
      notifyError(
        "选择文件失败",
        getActionErrorMessage(selectError, "选择导师导入文件失败"),
      );
    }
  };

  const handleImportDropZoneClick = (
    event: ReactMouseEvent<HTMLLabelElement>,
  ) => {
    if (!window.autoEmailSender?.selectProfessorImportFile) {
      return;
    }

    event.preventDefault();
    void handleChooseDesktopImportFile();
  };

  const handleDropImportFile = (event: ReactDragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    const nextFile = event.dataTransfer.files?.[0] ?? null;
    if (!nextFile) {
      return;
    }
    setImportFile(nextFile);
    setImportResult(null);
  };

  const handleImportSubmit = async () => {
    if (!importFile) {
      notifyWarning("请先选择文件", "请先选择要导入的 csv 或 xlsx 文件");
      return;
    }
    setImportingFile(true);
    try {
      const result = await importProfessorsFromFile(importFile);
      setImportResult(result);
      notifySuccess("导入完成", result.message);
      await loadProfessors();
    } catch (importError) {
      notifyError(
        "导入导师失败",
        getActionErrorMessage(importError, "导入导师失败"),
      );
    } finally {
      setImportingFile(false);
    }
  };

  const closeCrawlerModal = () => {
    if (creatingCrawlJob) {
      return;
    }
    setCrawlerModalOpen(false);
  };

  const handleCreateCrawlJob = async () => {
    if (!selectedLlmProfileId) {
      notifyWarning("请先选择模型", "智能抓取会使用当前顶部栏选择的模型。");
      return;
    }
    const startUrls = normalizeCrawlerStartUrls(crawlerFormState.start_urls);
    const payload = {
      university: crawlerFormState.university.trim(),
      school: crawlerFormState.school.trim(),
      start_url: startUrls[0] ?? "",
      start_urls: startUrls,
      entry_type: crawlerFormState.entry_type,
      llm_profile_id: selectedLlmProfileId,
    };
    const diagnosticData = {
      university: payload.university,
      school: payload.school,
      start_url: payload.start_url,
      start_urls: payload.start_urls,
      entry_type: payload.entry_type,
    };
    safeRecordUserAction({
      eventName: "professors.crawl_job_create_submitted",
      data: diagnosticData,
    });
    setCreatingCrawlJob(true);
    try {
      const job = await createCrawlJob(payload);
      trackCrawlJob(job);
      safeRecordUserAction({
        eventName: "professors.crawl_job_create_succeeded",
        data: diagnosticData,
      });
      setCrawlerModalOpen(false);
      setCrawlerFormState(emptyCrawlerJobForm());
      notifySuccess("抓取任务已创建", "可在任务中心查看抓取进度。");
    } catch (crawlerError) {
      safeRecordUserAction({
        eventName: "professors.crawl_job_create_failed",
        data: diagnosticData,
        message: getActionErrorMessage(crawlerError, "create crawl job failed"),
        level: "error",
      });
      notifyError(
        "创建抓取任务失败",
        getActionErrorMessage(crawlerError, "创建抓取任务失败"),
      );
    } finally {
      setCreatingCrawlJob(false);
    }
  };

  const crawlerSubmitDisabled =
    creatingCrawlJob ||
    !crawlerFormState.university.trim() ||
    !crawlerFormState.school.trim() ||
    normalizeCrawlerStartUrls(crawlerFormState.start_urls).length === 0;

  const handleCrawlerUrlPaste = (
    event: ReactClipboardEvent<HTMLInputElement>,
    index: number,
  ) => {
    const nextUrls = buildCrawlerStartUrlsAfterMultilinePaste(
      crawlerFormState.start_urls,
      index,
      event.clipboardData.getData("text/plain"),
    );
    if (!nextUrls) {
      return;
    }

    event.preventDefault();
    setCrawlerFormState((previous) => ({
      ...previous,
      start_urls: nextUrls,
    }));
  };

  const handleCrawlerUrlKeyDown = (
    event: ReactKeyboardEvent<HTMLInputElement>,
    index: number,
  ) => {
    if (
      event.key !== "Enter" ||
      event.nativeEvent.isComposing ||
      !crawlerFormState.start_urls[index]?.trim()
    ) {
      return;
    }

    event.preventDefault();
    setCrawlerFormState((previous) => ({
      ...previous,
      start_urls: [
        ...previous.start_urls.slice(0, index + 1),
        "",
        ...previous.start_urls.slice(index + 1),
      ],
    }));
    setCrawlerUrlFocusIndex(index + 1);
  };

  if (!hasLoadedProfessors && loading) {
    return <ProfessorsPageLoadingSkeleton />;
  }

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      {batchContributionMode ? (
        <section
          data-testid="community-batch-contribution-guide"
          aria-labelledby="community-batch-contribution-title"
          className="mb-6 rounded-[28px] border border-orange-200 bg-[linear-gradient(135deg,#fff7ed,#ffffff)] p-5 shadow-sm"
        >
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex min-w-0 items-start gap-3">
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary text-white shadow-sm shadow-primary/20">
                <FileSpreadsheet className="h-5 w-5" />
              </div>
              <div>
                <h2
                  id="community-batch-contribution-title"
                  className="font-semibold text-stone-950"
                >
                  按学校/学院批量贡献
                </h2>
                <p className="mt-1 max-w-3xl text-sm leading-6 text-stone-600">
                  筛选并全选目标学校或学院，再点击底部“贡献到社区”。
                </p>
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              <button
                type="button"
                onClick={() =>
                  openExternalHttpUrl(COMMUNITY_BATCH_CONTRIBUTION_URL)
                }
                className="ui-btn-secondary"
              >
                已有共享包，打开投稿表
                <ExternalLink className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => {
                  setSearchParams(
                    (previous) => {
                      const next = new URLSearchParams(previous);
                      next.delete("community_contribution");
                      return next;
                    },
                    { replace: true },
                  );
                }}
                className="ui-btn-secondary"
              >
                关闭提示
              </button>
            </div>
          </div>
        </section>
      ) : null}
      <section
        aria-labelledby="professors-workbench-title"
        className="rounded-[32px] border border-stone-200 bg-[linear-gradient(180deg,#fcfbf8,#fffaf2)] p-6 shadow-sm"
      >
        <div className="flex flex-col gap-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="max-w-2xl">
              <h1
                id="professors-workbench-title"
                className="text-3xl font-semibold tracking-[0.01em] text-stone-900"
              >
                导师管理
              </h1>
            </div>
          </div>

          {shouldShowProfessorIntakePanel ? (
            <section
              data-testid="professor-intake-panel"
              aria-labelledby="professor-intake-title"
              className="grid gap-3"
            >
              <div className="pl-1 flex flex-wrap items-end justify-between gap-3">
                <div>
                  <h2
                    id="professor-intake-title"
                    className="text-lg font-semibold text-stone-900"
                  >
                    导师导入与导出方式
                  </h2>
                </div>
              </div>
              <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-4">
                <IntakeActionCard
                  label="智能抓取"
                  icon={<Bot className="h-5 w-5" />}
                  tone="primary"
                >
                  <button
                    type="button"
                    onClick={() => {
                      safeRecordUserAction({
                        eventName: "professors.crawler_dialog_opened",
                      });
                      setCrawlerModalOpen(true);
                    }}
                    className="ui-btn-primary h-10 w-full rounded-2xl px-4"
                  >
                    <Bot className="h-4 w-4" />
                    智能抓取
                  </button>
                </IntakeActionCard>

                <IntakeActionCard
                  label="表格导入"
                  icon={<FileSpreadsheet className="h-5 w-5" />}
                  tone="amber"
                >
                  <button
                    type="button"
                    onClick={() => {
                      setImportFile(null);
                      setImportResult(null);
                      setImportModalOpen(true);
                    }}
                    className="ui-btn-secondary h-10 w-full rounded-2xl"
                  >
                    <Upload className="h-4 w-4" />
                    选择文件
                  </button>
                </IntakeActionCard>

                <IntakeActionCard
                  label="手动添加"
                  icon={<Plus className="h-5 w-5" />}
                  tone="stone"
                >
                  <button
                    type="button"
                    onClick={openCreateModal}
                    className="ui-btn-secondary h-10 w-full rounded-2xl"
                  >
                    <Plus className="h-4 w-4" />
                    添加导师
                  </button>
                </IntakeActionCard>

                <IntakeActionCard
                  label="导出导师信息"
                  icon={<Download className="h-5 w-5" />}
                  tone="emerald"
                >
                  <button
                    type="button"
                    onClick={() => setExportModalOpen(true)}
                    className="ui-btn-secondary h-10 w-full rounded-2xl border-emerald-200 bg-emerald-600 text-white shadow-sm shadow-emerald-900/15 hover:border-emerald-300 hover:bg-emerald-700 hover:text-white"
                  >
                    <Download className="h-4 w-4" />
                    导出导师信息
                  </button>
                </IntakeActionCard>
              </div>
            </section>
          ) : null}

          <div className="flex flex-wrap items-center gap-2 rounded-3xl border border-stone-200/80 bg-white/92 p-1.5 shadow-sm">
            {(Object.keys(archiveFilterLabels) as ArchiveFilter[]).map(
              (item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => {
                    if (archiveFilter === item) {
                      return;
                    }
                    if (agentSelection?.selectedOnly) {
                      setFilters(agentSelection.previous.filters);
                      setAdvancedFiltersOpen(
                        agentSelection.previous.advancedFiltersOpen,
                      );
                      setSortKey(agentSelection.previous.sortKey);
                      setSortDirections(agentSelection.previous.sortDirections);
                    }
                    setLoading(true);
                    setArchiveFilter(item);
                    setCurrentPage(1);
                    setSelectedIds(new Set());
                    const pending = pendingAgentSelectionLoadRef.current;
                    if (pending) {
                      settleAgentSelectionLoad(
                        pending.handoffId,
                        new Error("用户切换了导师归档范围。"),
                      );
                    }
                    agentSelectionRef.current = null;
                    setAgentSelection(null);
                    setSelectedAllQueryKey(null);
                    selectedAllIdsRef.current = [];
                  }}
                  className={clsx(
                    "rounded-2xl px-4 py-2 text-sm font-medium transition",
                    archiveFilter === item
                      ? "bg-primary text-white shadow-sm shadow-primary/20"
                      : "text-stone-600 hover:bg-stone-100 hover:text-stone-900",
                  )}
                >
                  {archiveFilterLabels[item]}
                </button>
              ),
            )}
          </div>

          <div className="grid gap-3">
            <div
              data-testid="professor-filter-toolbar"
              className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(12rem,1fr)_auto_auto] lg:items-stretch"
            >
              <label className="flex h-12 min-w-0 items-center gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-0 text-sm text-stone-600 shadow-sm">
                <div className="shrink-0 font-medium leading-5 text-stone-800">
                  关键词
                </div>
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <Search className="h-4 w-4 shrink-0 text-stone-400" />
                  <input
                    value={filters.keyword}
                    onChange={(event) =>
                      updateFilters({ keyword: event.target.value })
                    }
                    placeholder={getManagementKeywordSearchPlaceholder(
                      filters.keywordSearchScopes,
                    )}
                    className="w-full min-w-0 bg-transparent leading-5 outline-none placeholder:text-stone-400"
                  />
                  <KeywordSearchScopeSelect
                    label="搜索范围"
                    options={MANAGEMENT_KEYWORD_SEARCH_SCOPE_OPTIONS}
                    selectedValues={normalizeManagementKeywordSearchScopes(
                      filters.keywordSearchScopes,
                    )}
                    embedded
                    onChange={setManagementKeywordSearchScopes}
                  />
                </div>
              </label>

              <div
                data-testid="professor-sort-control"
                className="flex h-12 min-w-0 items-center gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-0 text-sm text-stone-600 shadow-sm"
              >
                <div className="shrink-0 font-medium leading-5 text-stone-800">
                  排序
                </div>
                <NativeSelectField
                  ariaLabel="排序"
                  value={sortKey}
                  selectedLabel={getManagementSortTriggerLabel(
                    sortKey,
                    currentSortDirection,
                  )}
                  onChange={(event) => {
                    setCurrentPage(1);
                    setSortKey(
                      event.target.value as ProfessorManagementSortKey,
                    );
                  }}
                  wrapperClassName="h-full min-w-0 flex-1"
                  embedded
                  renderOption={(
                    option,
                    { selected, selectOption, closeMenu },
                  ) => {
                    const optionKey =
                      option.value as ProfessorManagementSortKey;
                    const direction = sortDirections[optionKey];

                    return (
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          aria-pressed={selected}
                          aria-label={option.label}
                          disabled={option.disabled}
                          onClick={selectOption}
                          className={clsx(
                            "flex min-w-0 flex-1 items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-[13px] leading-5 transition",
                            option.disabled
                              ? "cursor-not-allowed text-stone-300"
                              : selected
                                ? "bg-primary text-white shadow-sm shadow-primary/25"
                                : "text-stone-700 hover:bg-stone-100/90 hover:text-stone-900",
                          )}
                        >
                          <span className="truncate">{option.label}</span>
                          {selected ? (
                            <Check className="h-4 w-4 shrink-0" />
                          ) : null}
                        </button>
                        <button
                          type="button"
                          aria-label={`切换${option.label}排序方向`}
                          disabled={option.disabled}
                          onClick={(event) => {
                            event.stopPropagation();
                            setCurrentPage(1);
                            setSortDirections((previous) => ({
                              ...previous,
                              [optionKey]:
                                previous[optionKey] === "desc" ? "asc" : "desc",
                            }));
                            setSortKey(optionKey);
                            closeMenu();
                          }}
                          className={clsx(
                            "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border transition",
                            selected
                              ? "border-primary/20 bg-primary/10 text-primary"
                              : "border-stone-200 text-stone-500 hover:border-stone-300 hover:bg-stone-100 hover:text-stone-800",
                          )}
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
                  {PROFESSOR_MANAGEMENT_SORT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </NativeSelectField>
              </div>

              <button
                type="button"
                onClick={() => setAdvancedFiltersOpen((previous) => !previous)}
                className={clsx(
                  "ui-btn-secondary h-12 justify-center whitespace-nowrap",
                  advancedFiltersOpen &&
                    "border-primary/30 bg-primary/5 text-primary",
                )}
              >
                高级筛选
                {activeAdvancedFilterCount > 0
                  ? ` ${activeAdvancedFilterCount}`
                  : ""}
              </button>

              <button
                type="button"
                onClick={resetAllFilters}
                className="ui-btn-secondary h-12 justify-center whitespace-nowrap"
              >
                重置
              </button>
            </div>

            {advancedFiltersOpen ? (
              <div className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                  <div className="text-sm font-semibold text-stone-800">
                    高级筛选
                  </div>
                  <button
                    type="button"
                    onClick={clearAdvancedFilters}
                    className="ui-btn-secondary px-3 py-1.5 text-sm"
                  >
                    清空高级筛选
                  </button>
                </div>

                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  <MultiSelectFilter
                    label="学校"
                    allLabel="全部学校"
                    selectedValues={filters.universities}
                    options={[
                      ...filterOptions.universities,
                      NO_FIELD_FILTER_VALUE,
                    ]}
                    optionLabels={noFieldOptionLabels}
                    onChange={(values) =>
                      setFilterValues("universities", values)
                    }
                  />
                  <MultiSelectFilter
                    label="学院"
                    allLabel="全部学院"
                    selectedValues={filters.schools}
                    options={[...filterOptions.schools, NO_FIELD_FILTER_VALUE]}
                    optionLabels={noFieldOptionLabels}
                    onChange={(values) => setFilterValues("schools", values)}
                  />
                  <MultiSelectFilter
                    label="系所"
                    allLabel="全部系所"
                    selectedValues={filters.departments}
                    options={[
                      ...filterOptions.departments,
                      NO_FIELD_FILTER_VALUE,
                    ]}
                    optionLabels={noFieldOptionLabels}
                    onChange={(values) =>
                      setFilterValues("departments", values)
                    }
                  />
                  <MultiSelectFilter
                    label="职称 / 导师资格"
                    allLabel="全部职称 / 导师资格"
                    selectedValues={filters.titles}
                    options={[...filterOptions.titles, NO_FIELD_FILTER_VALUE]}
                    optionLabels={noFieldOptionLabels}
                    onChange={(values) => setFilterValues("titles", values)}
                  />
                  <MultiSelectFilter
                    label="标签"
                    allLabel="全部标签"
                    selectedValues={filters.tagIds}
                    options={tagFilterEntries.map((entry) => entry.value)}
                    optionLabels={tagOptionLabels}
                    onChange={(values) => setFilterValues("tagIds", values)}
                  />
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </section>

      <section
        ref={professorListStartRef}
        tabIndex={-1}
        aria-label="导师管理列表"
        aria-busy={isRefreshingProfessors}
        className="relative mt-6 scroll-mt-6 overflow-hidden rounded-[32px] border border-stone-200 bg-white shadow-sm focus:outline-none"
      >
        <div className="flex flex-col gap-3 border-b border-stone-100 px-6 py-4">
          {agentSelection ? (
            <AgentProfessorSelectionBanner
              selectionCount={agentSelection.selectionCount}
              totalSelectedCount={selectedIds.size}
              selectionMode={agentSelection.selectionMode}
              selectedOnly={agentSelection.selectedOnly}
              onExitSelectedOnly={exitAgentSelectedOnly}
              onUndo={undoAgentSelection}
              onClear={clearAgentSelection}
            />
          ) : null}
          <div className="text-sm text-stone-600">
            {totalProfessorCount} 位 · {safeCurrentPage}/{totalPages} 页 · 每页{" "}
            {pageSize} 位
          </div>
          {totalProfessorCount > 0 ? (
            <button
              type="button"
              aria-label={allFilteredSelected ? "取消全选" : "全选当前结果"}
              aria-pressed={allFilteredSelected}
              onClick={() => void handleToggleFilteredSelection()}
              disabled={selectingAllProfessors}
              className="inline-flex min-h-10 w-fit items-center gap-2 rounded-2xl border border-stone-200 bg-stone-50 px-3 text-sm font-medium text-stone-700 transition hover:border-primary/40 hover:bg-white hover:text-primary disabled:cursor-wait disabled:opacity-60 lg:hidden"
            >
              {selectingAllProfessors ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : allFilteredSelected ? (
                <SquareCheck className="h-4 w-4" />
              ) : someFilteredSelected ? (
                <SquareMinus className="h-4 w-4" />
              ) : (
                <Square className="h-4 w-4" />
              )}
              {selectingAllProfessors
                ? "正在全选"
                : allFilteredSelected
                  ? "取消全选"
                  : "全选当前结果"}
            </button>
          ) : null}
        </div>

        <div
          data-testid="professor-table-header"
          className={clsx(
            "hidden gap-4 border-b border-stone-100 px-6 py-4 text-xs font-medium uppercase tracking-[0.16em] text-stone-400 lg:grid lg:items-center",
            managementTableColumns,
          )}
        >
          <div className="flex justify-center text-center">
            <span
              aria-hidden="true"
              className="sr-only justify-center text-center"
            >
              选择
            </span>
            <button
              type="button"
              aria-label={allFilteredSelected ? "取消全选" : "全选当前结果"}
              aria-pressed={allFilteredSelected}
              onClick={() => void handleToggleFilteredSelection()}
              disabled={selectingAllProfessors || totalProfessorCount === 0}
              className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/40 hover:text-primary disabled:cursor-not-allowed disabled:opacity-45"
              title={allFilteredSelected ? "取消全选" : "全选当前结果"}
            >
              {selectingAllProfessors ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : allFilteredSelected ? (
                <SquareCheck className="h-4 w-4" />
              ) : someFilteredSelected ? (
                <SquareMinus className="h-4 w-4" />
              ) : (
                <Square className="h-4 w-4" />
              )}
            </button>
          </div>
          <div className="flex justify-center text-center">导师</div>
          <div className="flex justify-center text-center">职称</div>
          <div className="flex justify-center text-center">邮箱</div>
          <div className="flex justify-center text-center">学校 / 学院</div>
          <div className="flex justify-center text-center">研究方向</div>
          <div className="flex justify-center text-center">更新时间</div>
          <div className="flex justify-center text-center">操作</div>
        </div>

        {totalProfessorCount === 0 && hasAnyProfessors ? (
          <div className="px-6 py-16 text-center">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-3xl bg-stone-100 text-stone-400">
              <Search className="h-6 w-6" />
            </div>
            <h2 className="mt-4 text-xl font-semibold text-stone-900">
              没有匹配的导师
            </h2>
            <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-stone-500">
              调整搜索或筛选条件后重试。
            </p>
            <button
              type="button"
              onClick={resetAllFilters}
              className="ui-btn-secondary mt-5"
            >
              <RefreshCcw className="h-4 w-4" />
              清除筛选
            </button>
          </div>
        ) : totalProfessorCount === 0 ? (
          <div className="px-6 py-16 text-center">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-3xl bg-stone-100 text-stone-400">
              <Users className="h-6 w-6" />
            </div>
            <h2 className="mt-4 text-xl font-semibold text-stone-900">
              暂无导师
            </h2>
            <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-stone-500">
              选择一种方式建立导师库。
            </p>
            <div
              data-testid="professor-empty-intake"
              className="mx-auto mt-6 grid max-w-4xl gap-3 text-left lg:grid-cols-3"
            >
              <article
                data-testid="professor-empty-intake-手动添加"
                className="flex min-h-full flex-col justify-between rounded-[28px] border border-stone-200 bg-white p-4 shadow-sm"
              >
                <div>
                  <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-stone-200 bg-stone-100 text-stone-700">
                    <Plus className="h-5 w-5" />
                  </div>
                  <h3 className="mt-3 text-base font-semibold text-stone-900">
                    手动添加
                  </h3>
                </div>
                <button
                  type="button"
                  onClick={openCreateModal}
                  className="ui-btn-primary mt-4 w-full justify-center"
                >
                  <Plus className="h-4 w-4" />
                  添加导师
                </button>
              </article>
              <article
                data-testid="professor-empty-intake-表格导入"
                className="flex min-h-full flex-col justify-between rounded-[28px] border border-amber-200 bg-[linear-gradient(135deg,#fffbeb,#ffffff)] p-4 shadow-sm"
              >
                <div>
                  <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-amber-200 bg-amber-100 text-amber-700">
                    <FileSpreadsheet className="h-5 w-5" />
                  </div>
                  <h3 className="mt-3 text-base font-semibold text-stone-900">
                    表格导入
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-stone-500">
                    从 CSV 或 XLSX 导入。
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setImportFile(null);
                    setImportResult(null);
                    setImportModalOpen(true);
                  }}
                  className="ui-btn-secondary mt-4 w-full justify-center"
                >
                  <Upload className="h-4 w-4" />
                  选择文件
                </button>
              </article>
              <article
                data-testid="professor-empty-intake-智能抓取"
                className="flex min-h-full flex-col justify-between rounded-[28px] border border-primary/25 bg-[linear-gradient(135deg,#fff7ed,#fff1f2)] p-4 shadow-sm"
              >
                <div>
                  <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-primary/15 bg-primary text-white shadow-sm shadow-primary/20">
                    <Bot className="h-5 w-5" />
                  </div>
                  <h3 className="mt-3 text-base font-semibold text-stone-900">
                    智能抓取
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-stone-500">
                    从学院页面抓取并审核。
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    safeRecordUserAction({
                      eventName: "professors.crawler_dialog_opened",
                    });
                    setCrawlerModalOpen(true);
                  }}
                  className="ui-btn-primary mt-4 w-full justify-center"
                >
                  <Bot className="h-4 w-4" />
                  开始抓取
                </button>
              </article>
            </div>
          </div>
        ) : (
          <div className="divide-y divide-stone-100">
            {paginatedProfessors.map((professor) => {
              const selectable = isProfessorSelectable(professor);
              const checked = selectedIds.has(professor.id);
              return (
                <ManagementProfessorRow
                  key={professor.id}
                  professor={professor}
                  checked={checked}
                  selectable={selectable}
                  tableColumns={managementTableColumns}
                  onToggleSelection={() => {
                    setSelectedAllQueryKey(null);
                    selectedAllIdsRef.current = [];
                    setSelectedIds((previous) => {
                      const next = new Set(previous);
                      if (next.has(professor.id)) {
                        next.delete(professor.id);
                      } else {
                        next.add(professor.id);
                      }
                      return next;
                    });
                  }}
                  onEdit={() => openEditModal(professor)}
                  onArchive={() => void handleArchiveProfessor(professor)}
                  onRestore={() => void handleRestoreProfessor(professor)}
                  onEditNote={() => setNoteEditorProfessor(professor)}
                  onPrimaryTagSelect={(tagId) =>
                    void handlePrimaryTagSelect(professor, tagId)
                  }
                  onAddTag={() => openTagEditor(professor)}
                />
              );
            })}
          </div>
        )}

        {totalProfessorCount > 0 ? (
          <Pagination
            page={safeCurrentPage}
            pageSize={pageSize}
            totalCount={totalProfessorCount}
            onChange={handlePaginationChange}
            ariaLabel="导师管理分页"
            unitLabel="位"
            itemLabel="位导师"
            summary={`${totalProfessorCount} 位 · ${safeCurrentPage}/${totalPages} 页 · 已选 ${selectedIds.size} 位`}
            focusTargetRef={professorListStartRef}
            className="border-t border-stone-100 px-6 py-4"
          />
        ) : null}
        {isRefreshingProfessors ? (
          <div
            data-testid="professor-list-refreshing"
            role="status"
            aria-live="polite"
            className="absolute inset-0 z-10 flex cursor-wait items-center justify-center bg-white/35"
          >
            <div className="flex items-center gap-2 rounded-full border border-stone-200 bg-white/95 px-4 py-2 text-sm text-stone-600 shadow-sm">
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
              正在更新导师列表…
            </div>
          </div>
        ) : null}
      </section>

      {selectedIds.size > 0 ? (
        <div className="pointer-events-none sticky bottom-4 z-20 mt-6 flex justify-center px-2">
          <div className="pointer-events-auto flex w-fit max-w-full flex-col items-start gap-3 rounded-[28px] border border-stone-200 bg-white/95 px-5 py-4 shadow-[0_18px_34px_-24px_rgba(41,37,36,0.36)] backdrop-blur-xl">
            <div>
              <div className="text-sm font-medium text-stone-900">
                已选中 {selectedIds.size} 位导师
              </div>
              {archiveFilter === "archived" ? (
                <div className="mt-1 text-xs text-stone-500">
                  恢复后可继续使用
                </div>
              ) : null}
            </div>
            <div className="flex max-w-full flex-wrap gap-3">
              <button
                type="button"
                onClick={() => {
                  if (agentSelection) {
                    clearAgentSelection();
                  } else {
                    setSelectedIds(new Set());
                    setSelectedAllQueryKey(null);
                    selectedAllIdsRef.current = [];
                  }
                }}
                className="ui-btn-secondary"
              >
                清空选择
              </button>
              <button
                type="button"
                onClick={() => setBulkTagDialogOpen(true)}
                className="ui-btn-secondary"
              >
                <Tags className="h-4 w-4" />
                批量改标签
              </button>
              {archiveFilter !== "archived" ? (
                <button
                  type="button"
                  onClick={() => void handleBulkInformationEnrichment()}
                  disabled={creatingBulkInformationEnrichment}
                  className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {creatingBulkInformationEnrichment ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Bot className="h-4 w-4" />
                  )}
                  批量智能补全
                </button>
              ) : null}
              <button
                type="button"
                onClick={() =>
                  archiveFilter === "archived"
                    ? void handleBulkRestore()
                    : void handleBulkArchive()
                }
                className={
                  archiveFilter === "archived"
                    ? "ui-btn-secondary"
                    : "ui-btn-danger"
                }
              >
                {archiveFilter === "archived" ? (
                  <RefreshCcw className="h-4 w-4" />
                ) : (
                  <Archive className="h-4 w-4" />
                )}
                {archiveFilter === "archived" ? "批量恢复" : "批量移入回收站"}
              </button>
              <button
                type="button"
                onClick={() => void handleBulkExportCommunitySharePackage()}
                disabled={exportingCommunitySharePackage}
                className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
              >
                {exportingCommunitySharePackage ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <FileSpreadsheet className="h-4 w-4" />
                )}
                {exportingCommunitySharePackage
                  ? "正在生成共享包…"
                  : "贡献到社区"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <ProfessorEditorDialog
        upsertModalOpen={upsertModalOpen}
        editingProfessor={editingProfessor}
        closeUpsertModal={closeUpsertModal}
        handleSingleInformationEnrichment={handleSingleInformationEnrichment}
        startingSingleInformationEnrichmentIds={
          startingSingleInformationEnrichmentIds
        }
        singleInformationEnrichments={singleInformationEnrichments}
        formState={formState}
        setFormState={setFormState}
        professorTags={professorTags}
        savingProfessor={savingProfessor}
        handleCreateProfessorTag={handleCreateProfessorTag}
        handleDeleteProfessorTag={handleDeleteProfessorTag}
        handleContributeProfessor={handleContributeProfessor}
        handleSaveProfessor={handleSaveProfessor}
      />

      <ProfessorImportDialog
        importModalOpen={importModalOpen}
        importingFile={importingFile}
        setImportModalOpen={setImportModalOpen}
        handleDownloadTemplate={handleDownloadTemplate}
        handleImportDropZoneClick={handleImportDropZoneClick}
        handleDropImportFile={handleDropImportFile}
        handleChooseImportFile={handleChooseImportFile}
        importFile={importFile}
        importResult={importResult}
        setImportResult={setImportResult}
        setImportFile={setImportFile}
        handleImportSubmit={handleImportSubmit}
      />

      <ProfessorExportDialog
        exportModalOpen={exportModalOpen}
        setExportModalOpen={setExportModalOpen}
        handleDownloadExport={handleDownloadExport}
      />

      <CreateCrawlJobDialog
        crawlerModalOpen={crawlerModalOpen}
        closeCrawlerModal={closeCrawlerModal}
        crawlerFormState={crawlerFormState}
        setCrawlerFormState={setCrawlerFormState}
        crawlerUrlInputRefs={crawlerUrlInputRefs}
        handleCrawlerUrlKeyDown={handleCrawlerUrlKeyDown}
        handleCrawlerUrlPaste={handleCrawlerUrlPaste}
        handleCreateCrawlJob={handleCreateCrawlJob}
        crawlerSubmitDisabled={crawlerSubmitDisabled}
        creatingCrawlJob={creatingCrawlJob}
      />

      <ProfessorTagAssignmentDialog
        open={Boolean(tagEditorProfessor)}
        scopeKey={tagEditorProfessor?.id ?? null}
        professorName={tagEditorProfessor?.name ?? ""}
        tags={professorTags}
        selectedTagIds={tagEditorSelectedIds}
        saving={savingProfessorTags}
        creating={creatingAssignmentTag}
        onChange={setTagEditorSelectedIds}
        onCreateTag={handleCreateAssignmentTag}
        onDeleteTag={(tag) => void handleDeleteProfessorTag(tag)}
        onSave={() => void saveTagEditor()}
        onClose={closeTagEditor}
      />

      <ProfessorNoteDialog
        open={Boolean(noteEditorProfessor)}
        professor={noteEditorProfessor}
        initialNote={noteEditorProfessor?.personal_note ?? null}
        saving={savingProfessorNote}
        onSave={(note) => void saveProfessorNote(note)}
        onClose={() => {
          if (!savingProfessorNote) {
            setNoteEditorProfessor(null);
          }
        }}
      />

      <BulkProfessorTagDialog
        open={bulkTagDialogOpen}
        selectedCount={selectedIds.size}
        tags={professorTags}
        saving={savingBulkTags}
        creating={creatingAssignmentTag}
        onCreateTag={handleCreateAssignmentTag}
        onDeleteTag={(tag) => void handleDeleteProfessorTag(tag)}
        onSave={(payload) => void saveBulkTags(payload)}
        onClose={() => {
          if (!savingBulkTags && !creatingAssignmentTag) {
            setBulkTagDialogOpen(false);
          }
        }}
      />

      {confirmDialog}
    </main>
  );
};
