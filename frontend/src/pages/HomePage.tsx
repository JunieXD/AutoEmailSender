import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import clsx from "clsx";
import {
  ArrowDown,
  ArrowUp,
  Check,
  FolderOpen,
  Loader2,
  MailPlus,
  RefreshCcw,
  Search,
  Sparkles,
  Square,
  SquareCheck,
  Tags,
} from "lucide-react";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { BulkProfessorTagDialog } from "@/components/molecules/BulkProfessorTagDialog";
import { KeywordSearchScopeSelect } from "@/components/molecules/KeywordSearchScopeSelect";
import {
  DashboardProfessorRow,
  type DashboardProfessorRowTimeHighlight,
} from "@/components/molecules/DashboardProfessorRow";
import { ProfessorNoteDialog } from "@/components/molecules/ProfessorNoteDialog";
import { ProfessorTagAssignmentDialog } from "@/components/molecules/ProfessorTagAssignmentDialog";
import { MultiSelectFilter } from "@/components/molecules/MultiSelectFilter";
import { OnboardingChecklistCard } from "@/components/molecules/OnboardingChecklistCard";
import { Pagination } from "@/components/molecules/Pagination";
import { SelectionToggleButton } from "@/components/molecules/SelectionToggleButton";
import { useBackgroundTaskNotification } from "@/app/providers/BackgroundTaskNotificationContext";
import { useNotification } from "@/context/NotificationContext";
import { useSelectionContext } from "@/context/SelectionContext";
import { AgentProfessorSelectionBanner } from "@/features/agent-ui-handoffs/AgentProfessorSelectionBanner";
import {
  isAgentProfessorHomeHandoff,
  type AgentProfessorSelectionMode,
} from "@/features/agent-ui-handoffs/types";
import { useAgentUiHandoffSurface } from "@/features/agent-ui-handoffs/useAgentUiHandoffSurface";
import { writeCreateTaskNavigationHandoff } from "@/features/navigation-handoffs/client/navigationHandoff";
import {
  DASHBOARD_KEYWORD_SEARCH_SCOPE_OPTIONS,
  createDefaultDashboardFilters,
  getActiveDashboardFilterCount,
  getDashboardKeywordSearchPlaceholder,
  normalizeDashboardKeywordSearchScopes,
  NO_FIELD_FILTER_VALUE,
  NO_MATCH_SCORE_FILTER_VALUE,
  NO_TAG_FILTER_VALUE,
  type DashboardFilterState,
  type DashboardKeywordSearchScope,
} from "@/features/home-dashboard/client/filterDashboardProfessors";
import {
  bulkTagConfirmLabels,
  buildBulkTagConfirmDescription,
} from "@/features/professor-management/client/bulkTagConfirmCopy";
import {
  DEFAULT_PROFESSOR_DASHBOARD_SORT_DIRECTIONS,
  PROFESSOR_DASHBOARD_SORT_OPTIONS,
  type ProfessorDashboardSortDirection,
  type ProfessorDashboardSortKey,
} from "@/features/home-dashboard/client/sortDashboardProfessors";
import { getOnboardingState } from "@/features/onboarding/client/getOnboardingState";
import {
  getProfessorDashboardStatusLabel,
  PROFESSOR_DASHBOARD_STATUS_OPTIONS,
} from "@/features/professor-status/dashboardStatus";
import {
  formatTokenUsageDescription,
  type TokenUsage,
} from "@/features/match-analysis/client/tokenUsage";
import { ApiError } from "@/lib/api/client";
import { calculateMatch } from "@/lib/api/emailTasksApi";
import {
  createMatchAnalysisJob,
  getMatchAnalysisSelectionSummary,
} from "@/lib/api/matchAnalysisJobsApi";
import { useConfirmDialog } from "@/lib/useConfirmDialog";
import {
  bulkUpdateProfessorTags,
  createProfessorTag,
  deleteProfessorTag,
  getProfessorTagUsage,
  listProfessorTags,
  searchDashboardProfessorIds,
  searchDashboardProfessors,
  updateProfessorNote,
  updateProfessorTags as updateProfessorTagsRequest,
} from "@/entities/professor/api/professors";
import { ensureWorkspaceTask } from "@/lib/api/workspacesApi";
import { parseApiDateTime } from "@/lib/dateTime";
import {
  getStoredPageSize,
  setStoredPageSize,
  type PaginationChange,
} from "@/lib/pagination";
import type {
  ProfessorDashboardFilterStatus,
  ProfessorDashboardItemDTO,
  ProfessorBulkTagModeDTO,
  ProfessorFilterOptionsDTO,
  MatchAnalysisSelectionSummaryDTO,
  ProfessorTagDTO,
  ProfessorTagPayloadDTO,
} from "@/types";

const FILTERS_SESSION_KEY_PREFIX = "home_dashboard_filters";
const HOME_PAGE_SIZE_STORAGE_KEY = "home-dashboard:page-size";
type HomeAgentSelectionState = {
  handoffId: string;
  identityId: number;
  selectionCount: number;
  selectionMode: AgentProfessorSelectionMode;
  selectedOnly: boolean;
  previous: {
    selectedIds: number[];
    filters: DashboardFilterState;
    advancedFiltersOpen: boolean;
    sortKey: ProfessorDashboardSortKey;
    sortDirections: Record<
      ProfessorDashboardSortKey,
      ProfessorDashboardSortDirection
    >;
    currentPage: number;
  };
};
const noFieldOptionLabels = { [NO_FIELD_FILTER_VALUE]: "未填写" };
const dashboardStatusOptionLabels = Object.fromEntries(
  PROFESSOR_DASHBOARD_STATUS_OPTIONS.map(([value, label]) => [value, label]),
) as Record<string, string>;

const dashboardStatusValues = new Set(
  PROFESSOR_DASHBOARD_STATUS_OPTIONS.map(([status]) => status),
);

const getDashboardFiltersSessionKey = (
  selectedIdentityId: number | null,
) =>
  selectedIdentityId !== null
    ? `${FILTERS_SESSION_KEY_PREFIX}:${selectedIdentityId}`
    : null;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const readStringArray = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];

const readStatusArray = (value: unknown): ProfessorDashboardFilterStatus[] =>
  Array.isArray(value)
    ? value.filter(
        (item): item is ProfessorDashboardFilterStatus =>
          typeof item === "string" &&
          dashboardStatusValues.has(item as ProfessorDashboardFilterStatus),
      )
    : [];

const readStoredDashboardFilters = (
  storageKey: string | null,
): DashboardFilterState => {
  const defaults = createDefaultDashboardFilters();
  if (!storageKey) {
    return defaults;
  }

  try {
    const rawValue = window.sessionStorage.getItem(storageKey);
    if (!rawValue) {
      return defaults;
    }
    const parsedValue = JSON.parse(rawValue);
    if (!isRecord(parsedValue)) {
      return defaults;
    }

    return {
      keyword:
        typeof parsedValue.keyword === "string"
          ? parsedValue.keyword
          : defaults.keyword,
      keywordSearchScopes: normalizeDashboardKeywordSearchScopes(
        parsedValue.keywordSearchScopes,
      ),
      universities: readStringArray(parsedValue.universities),
      schools: readStringArray(parsedValue.schools),
      departments: readStringArray(parsedValue.departments),
      titles: readStringArray(parsedValue.titles),
      statuses: readStatusArray(parsedValue.statuses),
      tagIds: readStringArray(parsedValue.tagIds),
      minMatchScore:
        typeof parsedValue.minMatchScore === "string"
          ? parsedValue.minMatchScore
          : defaults.minMatchScore,
      maxMatchScore:
        typeof parsedValue.maxMatchScore === "string"
          ? parsedValue.maxMatchScore
          : defaults.maxMatchScore,
    };
  } catch {
    return defaults;
  }
};

const writeStoredDashboardFilters = (
  storageKey: string | null,
  filters: DashboardFilterState,
) => {
  if (!storageKey) {
    return;
  }

  try {
    window.sessionStorage.setItem(storageKey, JSON.stringify(filters));
  } catch {
    // Losing ephemeral dashboard filters should not break the page.
  }
};

const hasMatchEvidence = (professor: ProfessorDashboardItemDTO) =>
  Boolean(professor.research_direction?.trim()) ||
  professor.recent_papers.some((paper) => paper.trim());

const formatDashboardTimeLabel = (label: string, value: string | null) => {
  if (!value) {
    return null;
  }
  const date = parseApiDateTime(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return `${label} ${date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })}`;
};

const getSortOptionLabel = (sortKey: ProfessorDashboardSortKey) =>
  PROFESSOR_DASHBOARD_SORT_OPTIONS.find((option) => option.value === sortKey)?.label ??
  "";

const getTimeSortDirectionSymbol = (
  direction: ProfessorDashboardSortDirection,
) => (direction === "desc" ? "↓" : "↑");

const getSortTriggerLabel = (
  sortKey: ProfessorDashboardSortKey,
  direction: ProfessorDashboardSortDirection,
) => {
  const label = getSortOptionLabel(sortKey);
  return `${label} ${getTimeSortDirectionSymbol(direction)}`;
};

const getProfessorTimeHighlight = (
  professor: ProfessorDashboardItemDTO,
  sortKey: ProfessorDashboardSortKey,
): DashboardProfessorRowTimeHighlight => {
  if (sortKey === "lastSentAt" && professor.last_sent_at) {
    return "sent";
  }
  if (sortKey === "lastRepliedAt" && professor.last_replied_at) {
    return "replied";
  }
  return null;
};

const isMatchConflictError = (error: unknown): error is ApiError =>
  error instanceof ApiError && error.status === 409;

const HomePageLoadingSkeleton = () => (
  <main
    data-testid="home-page-loading-skeleton"
    className="mx-auto max-w-7xl px-6 py-8"
    aria-label="首页内容加载中"
  >
    <section className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-6 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-3">
          <div className="h-9 w-36 animate-pulse rounded-xl bg-stone-200" />
          <div className="h-4 w-64 animate-pulse rounded-full bg-stone-100" />
        </div>
        <div className="flex flex-wrap gap-3">
          <div className="h-10 w-24 animate-pulse rounded-2xl bg-stone-200" />
          <div className="h-10 w-28 animate-pulse rounded-2xl bg-stone-200" />
        </div>
      </div>

      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(12rem,1fr)_auto_auto]">
        <div className="h-12 animate-pulse rounded-2xl border border-stone-200 bg-white" />
        <div className="h-12 animate-pulse rounded-2xl border border-stone-200 bg-white" />
        <div className="h-12 animate-pulse rounded-2xl border border-stone-200 bg-white" />
        <div className="h-12 animate-pulse rounded-2xl border border-stone-200 bg-white" />
      </div>
    </section>

    <section className="mt-6 overflow-hidden rounded-3xl border border-stone-200 bg-white shadow-sm">
      <div className="flex items-center gap-3 border-b border-stone-100 px-6 py-4">
        <div className="h-10 w-36 animate-pulse rounded-2xl bg-stone-100" />
        <div className="h-4 w-40 animate-pulse rounded-full bg-stone-100" />
      </div>
      <div className="divide-y divide-stone-100">
        {Array.from({ length: 5 }, (_, index) => (
          <div
            key={index}
            className="grid gap-4 px-6 py-5 md:grid-cols-[minmax(0,1.4fr)_minmax(12rem,0.8fr)_auto]"
          >
            <div className="space-y-3">
              <div className="h-5 w-40 animate-pulse rounded-full bg-stone-200" />
              <div className="h-4 w-full max-w-xl animate-pulse rounded-full bg-stone-100" />
              <div className="h-4 w-2/3 animate-pulse rounded-full bg-stone-100" />
            </div>
            <div className="space-y-3">
              <div className="h-4 w-28 animate-pulse rounded-full bg-stone-100" />
              <div className="h-4 w-36 animate-pulse rounded-full bg-stone-100" />
            </div>
            <div className="flex items-start gap-2">
              <div className="h-9 w-9 animate-pulse rounded-2xl bg-stone-100" />
              <div className="h-9 w-24 animate-pulse rounded-2xl bg-stone-100" />
            </div>
          </div>
        ))}
      </div>
    </section>

    <div className="mt-4 flex items-center justify-center gap-2 text-sm text-stone-500">
      <Loader2 className="h-4 w-4 animate-spin" />
      正在加载首页…
    </div>
  </main>
);

export const HomePage = () => {
  const navigate = useNavigate();
  const { choose, confirm, dialog: confirmDialog } = useConfirmDialog();
  const { notifyError, notifySuccess, notifyWarning } = useNotification();
  const { trackMatchAnalysisJob } = useBackgroundTaskNotification();
  const {
    selectedIdentityId,
    selectedLlmProfileId,
    selectedIdentity,
    selectedLlmProfile,
    communicationScopeKey = "",
    matchSourceIdentity,
    matchUsesGroupSource = false,
    matchScopeKey = "",
    loading: selectionLoading,
  } = useSelectionContext();
  const dashboardFiltersSessionKey = getDashboardFiltersSessionKey(
    selectedIdentityId,
  );
  const [professors, setProfessors] = useState<ProfessorDashboardItemDTO[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [agentSelection, setAgentSelection] =
    useState<HomeAgentSelectionState | null>(null);
  const [filters, setFilters] = useState<DashboardFilterState>(() =>
    readStoredDashboardFilters(dashboardFiltersSessionKey),
  );
  const [advancedFiltersOpen, setAdvancedFiltersOpen] = useState(false);
  const [sortKey, setSortKey] = useState<ProfessorDashboardSortKey>("latest");
  const [sortDirections, setSortDirections] = useState<
    Record<ProfessorDashboardSortKey, ProfessorDashboardSortDirection>
  >(() => ({ ...DEFAULT_PROFESSOR_DASHBOARD_SORT_DIRECTIONS }));
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(() =>
    getStoredPageSize(HOME_PAGE_SIZE_STORAGE_KEY),
  );
  const [loading, setLoading] = useState(false);
  const [totalProfessorCount, setTotalProfessorCount] = useState(0);
  const [hasAnyProfessors, setHasAnyProfessors] = useState(false);
  const [totalProfessorPages, setTotalProfessorPages] = useState(1);
  const [filterOptions, setFilterOptions] = useState<ProfessorFilterOptionsDTO>({
    universities: [],
    schools: [],
    departments: [],
    titles: [],
    tags: [],
  });
  const [hasLoadedProfessors, setHasLoadedProfessors] = useState(false);
  const [bulkScoring, setBulkScoring] = useState(false);
  const [scoringProfessorIds, setScoringProfessorIds] = useState<Set<number>>(
    new Set(),
  );
  const [professorTags, setProfessorTags] = useState<ProfessorTagDTO[]>([]);
  const [tagEditorProfessor, setTagEditorProfessor] =
    useState<ProfessorDashboardItemDTO | null>(null);
  const [noteEditorProfessor, setNoteEditorProfessor] =
    useState<ProfessorDashboardItemDTO | null>(null);
  const [tagEditorSelectedIds, setTagEditorSelectedIds] = useState<number[]>([]);
  const [savingProfessorTags, setSavingProfessorTags] = useState(false);
  const [savingProfessorNote, setSavingProfessorNote] = useState(false);
  const [creatingProfessorTag, setCreatingProfessorTag] = useState(false);
  const [bulkTagDialogOpen, setBulkTagDialogOpen] = useState(false);
  const [savingBulkTags, setSavingBulkTags] = useState(false);
  const [selectingAllProfessors, setSelectingAllProfessors] = useState(false);
  const [selectedAllQueryKey, setSelectedAllQueryKey] = useState<string | null>(null);
  const loadedProfessorsKeyRef = useRef<string | null>(null);
  const professorListStartRef = useRef<HTMLElement | null>(null);
  const activeProfessorsRequestKeyRef = useRef<string | null>(null);
  const latestProfessorsRequestIdRef = useRef(0);
  const filtersSessionKeyRef = useRef(dashboardFiltersSessionKey);
  const skipNextFiltersPersistRef = useRef(false);
  const cursorByPageRef = useRef<Map<number, string | null>>(new Map([[1, null]]));
  const selectedAllIdsRef = useRef<number[]>([]);
  const agentSelectionRef = useRef<HomeAgentSelectionState | null>(null);
  agentSelectionRef.current = agentSelection;
  const pendingAgentSelectionLoadRef = useRef<{
    handoffId: string;
    resolve: (visibleCount: number) => void;
    reject: (error: Error) => void;
    timeoutId: number;
  } | null>(null);
  const professorsRequestKey =
    selectedIdentityId
      ? `${selectedIdentityId}:${communicationScopeKey || selectedIdentityId}:${
          matchScopeKey || selectedIdentityId
        }`
      : null;
  const professorPageQueryKey = JSON.stringify({
    uiHandoffId:
      agentSelection?.selectedOnly === true ? agentSelection.handoffId : null,
    professorsRequestKey,
    filters,
    sortKey,
    sortDirection: sortDirections[sortKey],
    pageSize,
  });
  const cursorQueryKeyRef = useRef(professorPageQueryKey);

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
      previous.reject(new Error("新的 Agent 导师选择替换了尚未完成的页面加载。"));
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
        pending.reject(new Error("首页导师看板已关闭。"));
      }
    },
    [],
  );

  useEffect(() => {
    if (filtersSessionKeyRef.current === dashboardFiltersSessionKey) {
      return;
    }

    filtersSessionKeyRef.current = dashboardFiltersSessionKey;
    skipNextFiltersPersistRef.current = true;
    setFilters(readStoredDashboardFilters(dashboardFiltersSessionKey));
  }, [dashboardFiltersSessionKey]);

  useEffect(() => {
    if (agentSelection?.selectedOnly) {
      return;
    }
    if (skipNextFiltersPersistRef.current) {
      skipNextFiltersPersistRef.current = false;
      return;
    }

    writeStoredDashboardFilters(dashboardFiltersSessionKey, filters);
  }, [agentSelection?.selectedOnly, dashboardFiltersSessionKey, filters]);

  const loadProfessors = useCallback(async () => {
    if (!professorsRequestKey || !selectedIdentityId) {
      latestProfessorsRequestIdRef.current += 1;
      activeProfessorsRequestKeyRef.current = null;
      loadedProfessorsKeyRef.current = null;
      setHasLoadedProfessors(false);
      setProfessors([]);
      setTotalProfessorCount(0);
      setHasAnyProfessors(false);
      setTotalProfessorPages(1);
      setSelectedIds(new Set());
      setSelectedAllQueryKey(null);
      selectedAllIdsRef.current = [];
      const pending = pendingAgentSelectionLoadRef.current;
      if (pending) {
        settleAgentSelectionLoad(
          pending.handoffId,
          new Error("首页发件身份已切换。"),
        );
      }
      agentSelectionRef.current = null;
      setAgentSelection(null);
      setLoading(false);
      return;
    }
    if (loadedProfessorsKeyRef.current !== professorsRequestKey) {
      setHasLoadedProfessors(false);
    }
    const requestId = latestProfessorsRequestIdRef.current + 1;
    latestProfessorsRequestIdRef.current = requestId;
    activeProfessorsRequestKeyRef.current = professorsRequestKey;
    setLoading(true);
    try {
      if (cursorQueryKeyRef.current !== professorPageQueryKey) {
        cursorQueryKeyRef.current = professorPageQueryKey;
        cursorByPageRef.current = new Map([[1, null]]);
      }
      const matchScoreMissing =
        filters.minMatchScore === NO_MATCH_SCORE_FILTER_VALUE;
      const minMatchScore =
        !matchScoreMissing && filters.minMatchScore.trim()
          ? Number(filters.minMatchScore)
          : null;
      const maxMatchScore = filters.maxMatchScore.trim()
        ? Number(filters.maxMatchScore)
        : null;
      const data = await searchDashboardProfessors({
        ui_handoff_id:
          agentSelection?.selectedOnly === true
            ? agentSelection.handoffId
            : null,
        identity_id: selectedIdentityId,
        page: currentPage,
        page_size: pageSize,
        cursor: cursorByPageRef.current.get(currentPage),
        keyword: filters.keyword,
        keyword_search_scopes: filters.keywordSearchScopes,
        universities: filters.universities,
        schools: filters.schools,
        departments: filters.departments,
        titles: filters.titles,
        statuses: filters.statuses,
        tag_ids: filters.tagIds,
        min_match_score: Number.isFinite(minMatchScore) ? minMatchScore : null,
        max_match_score: Number.isFinite(maxMatchScore) ? maxMatchScore : null,
        match_score_missing: matchScoreMissing,
        sort_key: sortKey,
        sort_direction: sortDirections[sortKey],
      });
      if (
        latestProfessorsRequestIdRef.current !== requestId ||
        activeProfessorsRequestKeyRef.current !== professorsRequestKey
      ) {
        return;
      }
      const previousLoadedKey = loadedProfessorsKeyRef.current;
      setProfessors(data.items);
      setTotalProfessorCount(data.total_count);
      setHasAnyProfessors(data.has_any_professors);
      setTotalProfessorPages(data.total_pages);
      setFilterOptions(data.filter_options);
      if (data.next_cursor) {
        cursorByPageRef.current.set(data.page + 1, data.next_cursor);
      }
      setSelectedIds((previous) => {
        if (
          previousLoadedKey !== professorsRequestKey &&
          agentSelectionRef.current === null
        ) {
          return new Set();
        }
        return previous;
      });
      loadedProfessorsKeyRef.current = professorsRequestKey;
      setHasLoadedProfessors(true);
      if (agentSelection?.selectedOnly === true) {
        settleAgentSelectionLoad(
          agentSelection.handoffId,
          undefined,
          data.total_count,
        );
      }
    } catch (loadError) {
      if (
        latestProfessorsRequestIdRef.current !== requestId ||
        activeProfessorsRequestKeyRef.current !== professorsRequestKey
      ) {
        return;
      }
      if (loadedProfessorsKeyRef.current !== professorsRequestKey) {
        setProfessors([]);
        setSelectedIds(new Set());
        setSelectedAllQueryKey(null);
        selectedAllIdsRef.current = [];
      }
      const message =
        loadError instanceof Error ? loadError.message : "加载导师列表失败";
      if (agentSelection?.selectedOnly === true) {
        settleAgentSelectionLoad(
          agentSelection.handoffId,
          new Error(message),
        );
      }
      notifyError("加载导师列表失败", message);
    } finally {
      if (
        latestProfessorsRequestIdRef.current === requestId &&
        activeProfessorsRequestKeyRef.current === professorsRequestKey
      ) {
        setLoading(false);
      }
    }
  }, [
    notifyError,
    agentSelection,
    currentPage,
    filters,
    pageSize,
    professorPageQueryKey,
    professorsRequestKey,
    selectedIdentityId,
    sortDirections,
    sortKey,
    settleAgentSelectionLoad,
  ]);

  useEffect(() => {
    void loadProfessors();
  }, [loadProfessors]);

  useEffect(() => {
    let active = true;
    const loadProfessorTags = async () => {
      try {
        const tags = await listProfessorTags();
        if (active) {
          setProfessorTags(tags);
        }
      } catch (loadError) {
        const message =
          loadError instanceof Error ? loadError.message : "加载标签候选失败";
        notifyError("加载标签候选失败", message);
      }
    };

    void loadProfessorTags();

    return () => {
      active = false;
    };
  }, [notifyError]);

  useAgentUiHandoffSurface("professors.home", async (handoff) => {
    if (!isAgentProfessorHomeHandoff(handoff)) {
      return {
        status: "failed",
        failureMessage: "首页收到的导师界面交接类型不匹配。",
      };
    }
    if (selectedIdentityId !== handoff.payload.identity_id) {
      return {
        status: "failed",
        failureMessage: "首页尚未切换到界面交接指定的发件身份。",
      };
    }

    const previous: HomeAgentSelectionState["previous"] = {
      selectedIds: Array.from(selectedIds),
      filters: {
        ...filters,
        keywordSearchScopes: [...filters.keywordSearchScopes],
        universities: [...filters.universities],
        schools: [...filters.schools],
        departments: [...filters.departments],
        titles: [...filters.titles],
        statuses: [...filters.statuses],
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
    const nextAgentSelection: HomeAgentSelectionState = {
      handoffId: handoff.handoffId,
      identityId: handoff.payload.identity_id,
      selectionCount: handoff.selectionCount,
      selectionMode: handoff.payload.selection_mode,
      selectedOnly,
      previous,
    };

    latestProfessorsRequestIdRef.current += 1;
    setSelectedIds(nextSelectedIds);
    setSelectedAllQueryKey(null);
    selectedAllIdsRef.current = [];
    agentSelectionRef.current = nextAgentSelection;
    setAgentSelection(nextAgentSelection);
    if (selectedOnly) {
      setFilters(createDefaultDashboardFilters());
      setAdvancedFiltersOpen(false);
      setSortKey("latest");
      setSortDirections({ ...DEFAULT_PROFESSOR_DASHBOARD_SORT_DIRECTIONS });
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
        identity_id: handoff.payload.identity_id,
        surface: handoff.surface,
      },
    };
  });

  useEffect(() => {
    if (
      agentSelection !== null &&
      selectedIdentityId !== agentSelection.identityId
    ) {
      setAdvancedFiltersOpen(agentSelection.previous.advancedFiltersOpen);
      setSortKey(agentSelection.previous.sortKey);
      setSortDirections(agentSelection.previous.sortDirections);
      setCurrentPage(agentSelection.previous.currentPage);
      setSelectedIds(new Set());
      setSelectedAllQueryKey(null);
      selectedAllIdsRef.current = [];
      agentSelectionRef.current = null;
      setAgentSelection(null);
      const pending = pendingAgentSelectionLoadRef.current;
      if (pending) {
        settleAgentSelectionLoad(
          pending.handoffId,
          new Error("首页发件身份已切换。"),
        );
      }
    }
  }, [agentSelection, selectedIdentityId, settleAgentSelectionLoad]);

  const restoreAgentSelectionView = (selection: HomeAgentSelectionState) => {
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

  const saveProfessorTags = async (
    professor: ProfessorDashboardItemDTO,
    tagIds: number[],
  ) => {
    setSavingProfessorTags(true);
    try {
      const updatedProfessor = await updateProfessorTagsRequest(professor.id, tagIds);
      setProfessors((previous) =>
        previous.map((item) =>
          item.id === updatedProfessor.id
            ? {
                ...item,
                tags: updatedProfessor.tags,
              }
            : item,
        ),
      );
      notifySuccess(`已更新“${updatedProfessor.name}”的标签`);
      return true;
    } catch (saveError) {
      const message =
        saveError instanceof Error ? saveError.message : "保存导师标签失败";
      notifyError("保存导师标签失败", message);
      return false;
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
            ? { ...professor, personal_note: updated.personal_note }
            : professor,
        ),
      );
      setNoteEditorProfessor(null);
      notifySuccess(`已更新“${noteEditorProfessor.name}”的备注`);
    } catch (saveError) {
      const message =
        saveError instanceof Error ? saveError.message : "保存备注失败";
      notifyError("保存备注失败", message);
    } finally {
      setSavingProfessorNote(false);
    }
  };

  const handleCreateAssignmentTag = async (
    payload: ProfessorTagPayloadDTO,
  ) => {
    setCreatingProfessorTag(true);
    try {
      const createdTag = await createProfessorTag(payload);
      setProfessorTags((previous) => [...previous, createdTag]);
      notifySuccess(`已创建标签“${createdTag.name}”`);
      return createdTag;
    } catch (createError) {
      const message =
        createError instanceof Error ? createError.message : "创建标签失败";
      notifyError("创建标签失败", message);
      return null;
    } finally {
      setCreatingProfessorTag(false);
    }
  };

  const handleDeleteProfessorTag = async (tag: ProfessorTagDTO) => {
    let usageProfessors: Array<{
      id: number;
      name: string;
      email: string | null;
      university: string | null;
      school: string | null;
    }> = [];
    try {
      const usage = await getProfessorTagUsage(tag.id);
      usageProfessors = usage.professors;
    } catch (usageError) {
      const message =
        usageError instanceof Error ? usageError.message : "查询标签使用情况失败";
      notifyError("查询标签使用情况失败", message);
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
      const result = await deleteProfessorTag(tag.id);
      setProfessorTags((previous) =>
        previous.filter((item) => item.id !== tag.id),
      );
      setTagEditorSelectedIds((previous) =>
        previous.filter((tagId) => tagId !== tag.id),
      );
      setTagEditorProfessor((previous) =>
        previous
          ? {
              ...previous,
              tags: previous.tags.filter((item) => item.id !== tag.id),
            }
          : previous,
      );
      setProfessors((previous) =>
        previous.map((professor) => ({
          ...professor,
          tags: professor.tags.filter((item) => item.id !== tag.id),
        })),
      );
      notifySuccess("删除标签成功", result.message);
    } catch (deleteError) {
      const message =
        deleteError instanceof Error ? deleteError.message : "删除标签失败";
      notifyError("删除标签失败", message);
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
      const message =
        saveError instanceof Error ? saveError.message : "批量修改标签失败";
      notifyError("批量修改标签失败", message);
    } finally {
      setSavingBulkTags(false);
    }
  };

  const openTagEditor = (professor: ProfessorDashboardItemDTO) => {
    setTagEditorProfessor(professor);
    setTagEditorSelectedIds(professor.tags.map((tag) => tag.id));
  };

  const closeTagEditor = () => {
    if (savingProfessorTags || creatingProfessorTag) {
      return;
    }
    setTagEditorProfessor(null);
    setTagEditorSelectedIds([]);
  };

  const saveTagEditor = async () => {
    if (!tagEditorProfessor) {
      return;
    }
    const saved = await saveProfessorTags(
      tagEditorProfessor,
      tagEditorSelectedIds,
    );
    if (saved) {
      closeTagEditor();
    }
  };

  const activeAdvancedFilterCount = useMemo(
    () => getActiveDashboardFilterCount(filters),
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

  const updateFilters = (nextFilters: Partial<DashboardFilterState>) => {
    setFilters((previous) => ({ ...previous, ...nextFilters }));
  };

  const setDashboardKeywordSearchScopes = (
    keywordSearchScopes: DashboardKeywordSearchScope[],
  ) => {
    updateFilters({
      keywordSearchScopes:
        normalizeDashboardKeywordSearchScopes(keywordSearchScopes),
    });
  };

  const setStringFilterValues = (
    key: "universities" | "schools" | "departments" | "titles" | "tagIds",
    nextValues: string[],
  ) => {
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

  const handleMatchScoreBoundaryChange = (
    key: "minMatchScore" | "maxMatchScore",
    value: string,
  ) => {
    if (value === "") {
      updateFilters({ [key]: "" });
      return;
    }

    const score = Number(value);
    if (!Number.isFinite(score)) {
      return;
    }

    updateFilters({ [key]: String(Math.min(100, Math.max(0, score))) });
  };

  const hasInvalidMatchScoreRange =
    filters.minMatchScore !== NO_MATCH_SCORE_FILTER_VALUE &&
    filters.minMatchScore.trim() !== "" &&
    filters.maxMatchScore.trim() !== "" &&
    Number(filters.minMatchScore) > Number(filters.maxMatchScore);

  const clearAdvancedFilters = () => {
    setFilters((previous) => ({
      ...previous,
      universities: [],
      schools: [],
      departments: [],
      titles: [],
      statuses: [],
      tagIds: [],
      minMatchScore: "",
      maxMatchScore: "",
    }));
  };

  const resetAllFilters = () => {
    setFilters(createDefaultDashboardFilters());
    setSortKey("latest");
    setSortDirections({ ...DEFAULT_PROFESSOR_DASHBOARD_SORT_DIRECTIONS });
  };

  const currentSortDirection = sortDirections[sortKey];
  const visibleProfessors = professors;
  const totalPages = totalProfessorPages;
  const safeCurrentPage = Math.min(currentPage, totalPages);
  useEffect(() => {
    setCurrentPage((page) => Math.min(page, totalPages));
  }, [totalPages]);
  const pagedProfessors = visibleProfessors;
  const allFilteredProfessorsSelected =
    totalProfessorCount > 0 && selectedAllQueryKey === professorPageQueryKey;

  const handleToggleFilteredProfessors = async () => {
    if (selectingAllProfessors || !selectedIdentityId) {
      return;
    }
    if (allFilteredProfessorsSelected) {
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
    setSelectingAllProfessors(true);
    try {
      const matchScoreMissing =
        filters.minMatchScore === NO_MATCH_SCORE_FILTER_VALUE;
      const minMatchScore =
        !matchScoreMissing && filters.minMatchScore.trim()
          ? Number(filters.minMatchScore)
          : null;
      const maxMatchScore = filters.maxMatchScore.trim()
        ? Number(filters.maxMatchScore)
        : null;
      const result = await searchDashboardProfessorIds({
        ui_handoff_id:
          agentSelection?.selectedOnly === true
            ? agentSelection.handoffId
            : null,
        identity_id: selectedIdentityId,
        page: 1,
        page_size: pageSize,
        keyword: filters.keyword,
        keyword_search_scopes: filters.keywordSearchScopes,
        universities: filters.universities,
        schools: filters.schools,
        departments: filters.departments,
        titles: filters.titles,
        statuses: filters.statuses,
        tag_ids: filters.tagIds,
        min_match_score: Number.isFinite(minMatchScore) ? minMatchScore : null,
        max_match_score: Number.isFinite(maxMatchScore) ? maxMatchScore : null,
        match_score_missing: matchScoreMissing,
        sort_key: sortKey,
        sort_direction: currentSortDirection,
      });
      selectedAllIdsRef.current = result.ids;
      setSelectedIds((previous) => new Set([...previous, ...result.ids]));
      setSelectedAllQueryKey(professorPageQueryKey);
    } catch (selectionError) {
      notifyError(
        "选择筛选结果失败",
        selectionError instanceof Error
          ? selectionError.message
          : "无法选择全部筛选结果",
      );
    } finally {
      setSelectingAllProfessors(false);
    }
  };

  const prevPageResetDepsRef = useRef<{
    filters: DashboardFilterState;
    sortKey: ProfessorDashboardSortKey;
    direction: ProfessorDashboardSortDirection;
    requestKey: string | null;
  } | null>(null);

  useEffect(() => {
    const prev = prevPageResetDepsRef.current;
    // 仅在依赖值真正变化时才重置页码，避免 Activity 切回时 effect 重建导致误重置。
    if (
      prev &&
      prev.filters === filters &&
      prev.sortKey === sortKey &&
      prev.direction === currentSortDirection &&
      prev.requestKey === professorsRequestKey
    ) {
      return;
    }
    prevPageResetDepsRef.current = {
      filters,
      sortKey,
      direction: currentSortDirection,
      requestKey: professorsRequestKey,
    };
    setCurrentPage(1);
  }, [filters, sortKey, currentSortDirection, professorsRequestKey]);

  const handlePaginationChange = (change: PaginationChange) => {
    setCurrentPage(change.page);
    setPageSize(change.pageSize);
    setStoredPageSize(HOME_PAGE_SIZE_STORAGE_KEY, change.pageSize);
  };

  const toggleSelection = (professorId: number) => {
    setSelectedAllQueryKey(null);
    selectedAllIdsRef.current = [];
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(professorId)) {
        next.delete(professorId);
      } else {
        next.add(professorId);
      }
      return next;
    });
  };

  const handleCreateTask = async () => {
    if (selectedIds.size === 0) {
      await confirm({
        title: "未选择导师",
        description: "选择本次要联系的导师。",
        confirmLabel: "知道了",
        cancelLabel: null,
      });
      return;
    }
    try {
      writeCreateTaskNavigationHandoff([...selectedIds]);
      navigate("/create-task");
    } catch (handoffError) {
      notifyError(
        "无法打开任务创建页",
        handoffError instanceof Error
          ? handoffError.message
          : "导师选择暂时无法交给任务创建页。",
      );
    }
  };

  const effectiveMatchSourceIdentity = matchSourceIdentity ?? selectedIdentity;
  const hasMatchPrimaryMaterial = Boolean(
    effectiveMatchSourceIdentity?.current_primary_material_id,
  );
  const matchSourceName =
    effectiveMatchSourceIdentity?.profile_name ||
    effectiveMatchSourceIdentity?.name ||
    "当前身份";
  const hasTemplate =
    selectedIdentity?.effective_outreach_template_is_ready ??
    Boolean(
      selectedIdentity?.outreach_template_body_text?.trim() ||
        selectedIdentity?.outreach_template_body_html?.trim(),
    );
  const hasOnboardingMaterial = Boolean(
    selectedIdentity?.materials?.length ||
      selectedIdentity?.current_primary_material_id,
  );
  const hasMaterialsAndTemplate = hasOnboardingMaterial && hasTemplate;
  const onboardingState = getOnboardingState({
    hasIdentity: Boolean(selectedIdentity),
    hasLlmProfile: Boolean(selectedLlmProfile),
    hasPrimaryMaterial: hasMaterialsAndTemplate,
    hasProfessors: hasAnyProfessors,
    hasFirstTask: false,
  });
  const shouldSkipHomeOnboardingForCurrentStage =
    agentSelection !== null ||
    onboardingState.completed ||
    onboardingState.stage === "first_task";
  const canEvaluateProfessorOnboarding =
    professorsRequestKey === null || hasLoadedProfessors;

  const toggleScoringProfessor = (professorId: number, active: boolean) => {
    setScoringProfessorIds((previous) => {
      const next = new Set(previous);
      if (active) {
        next.add(professorId);
      } else {
        next.delete(professorId);
      }
      return next;
    });
  };

  const runCalculateMatchForProfessor = useCallback(
    async (professorId: number): Promise<TokenUsage> => {
      if (!selectedIdentityId || !selectedLlmProfileId) {
        throw new Error("请先选择身份和模型");
      }

      const workspace = await ensureWorkspaceTask(
        professorId,
        selectedIdentityId,
        selectedLlmProfileId,
      );
      if (!workspace.current_task.id) {
        throw new Error("未能为该导师准备工作区任务");
      }
      const result = await calculateMatch(workspace.current_task.id, selectedLlmProfileId);
      return result.usage;
    },
    [selectedIdentityId, selectedLlmProfileId],
  );

  const handleGenerateOne = async (professorId: number) => {
    if (!hasMatchPrimaryMaterial) {
      notifyWarning(
        "匹配依据身份缺少默认材料",
        `${matchSourceName} 尚未设置默认材料，请先到个人页补充。`,
      );
      return;
    }

    const professor = professors.find((item) => item.id === professorId);
    if (professor && !hasMatchEvidence(professor)) {
      notifyWarning(
        "缺少研究信息",
        "请先补充该导师的研究方向或近期论文，再分析匹配度。",
      );
      return;
    }
    if (
      professor?.match_score !== null &&
      professor?.match_score !== undefined
    ) {
      const action = await choose({
        title: `${professor.name} 当前为 ${professor.match_score} 分`,
        description: "要重新计算吗？",
        confirmLabel: "重新计算",
        secondaryLabel: "保留现有",
        cancelLabel: "取消",
      });
      if (action === "secondary") {
        notifyWarning("已保留现有匹配分");
        return;
      }
      if (action !== "confirm") {
        return;
      }
    }

    toggleScoringProfessor(professorId, true);
    try {
      const usage = await runCalculateMatchForProfessor(professorId);
      await loadProfessors();
      notifySuccess(
        "匹配分析完成",
        `${matchUsesGroupSource ? `已按 ${matchSourceName} 的材料统一计算。` : ""}${formatTokenUsageDescription(usage)}`,
      );
    } catch (actionError) {
      if (isMatchConflictError(actionError)) {
        notifyWarning("匹配分析进行中", "该任务正在分析中，请稍后刷新结果。");
        return;
      }
      const message =
        actionError instanceof Error ? actionError.message : "计算匹配失败";
      notifyError("计算匹配失败", message);
    } finally {
      toggleScoringProfessor(professorId, false);
    }
  };

  const handleGenerateSelected = async () => {
    if (selectedIds.size === 0) {
      await confirm({
        title: "未选择导师",
        description: "选择要批量计算匹配的导师。",
        confirmLabel: "知道了",
        cancelLabel: null,
      });
      return;
    }

    if (!hasMatchPrimaryMaterial) {
      notifyWarning(
        "匹配依据身份缺少默认材料",
        `${matchSourceName} 尚未设置默认材料，请先到个人页补充。`,
      );
      return;
    }
    if (!selectedIdentityId || !selectedLlmProfileId) {
      notifyWarning("缺少运行配置", "请先选择身份和模型。");
      return;
    }

    let selectionSummary: MatchAnalysisSelectionSummaryDTO;
    try {
      selectionSummary = await getMatchAnalysisSelectionSummary({
        identity_id: selectedIdentityId,
        professor_ids: Array.from(selectedIds),
      });
    } catch (summaryError) {
      notifyError(
        "检查批量匹配条件失败",
        summaryError instanceof Error
          ? summaryError.message
          : "无法检查所选导师的匹配条件",
      );
      return;
    }

    if (selectionSummary.analyzable_count === 0) {
      notifyWarning(
        "缺少研究信息",
        "已选导师都缺少研究方向或近期论文，暂不能分析匹配度。",
      );
      return;
    }

    const professorIdsForJob = Array.from(selectedIds);
    let skipExisting = false;
    if (selectionSummary.already_scored_count > 0) {
      const action = await choose({
        title: `${selectionSummary.already_scored_count} 位导师已有匹配分`,
        description: "要重新计算还是保留现有结果？",
        confirmLabel: "重新计算",
        secondaryLabel: "保留现有",
        cancelLabel: "取消",
      });
      if (action === "secondary") {
        if (selectionSummary.unscored_analyzable_count === 0) {
          notifyWarning(
            "没有需要分析的导师",
            "已选导师都已有匹配分，本次已按你的选择跳过。",
          );
          return;
        }
        skipExisting = true;
      } else if (action !== "confirm") {
        return;
      }
    }

    setBulkScoring(true);
    try {
      const job = await createMatchAnalysisJob({
        identity_id: selectedIdentityId,
        llm_profile_id: selectedLlmProfileId,
        professor_ids: professorIdsForJob,
        name: null,
        ...(skipExisting ? { skip_existing: true } : {}),
      });
      trackMatchAnalysisJob(job);
      notifySuccess(
        "已创建批量匹配分析任务",
        `任务中心会继续后台分析 ${job.target_count} 位导师。`,
      );
      if (agentSelection) {
        clearAgentSelection();
      } else {
        setSelectedIds(new Set());
        setSelectedAllQueryKey(null);
        selectedAllIdsRef.current = [];
      }
    } catch (createError) {
      notifyError(
        "创建批量匹配任务失败",
        createError instanceof Error ? createError.message : "创建任务失败",
      );
    } finally {
      setBulkScoring(false);
    }
  };

  if (
    selectionLoading ||
    (professorsRequestKey !== null && !hasLoadedProfessors && loading)
  ) {
    return <HomePageLoadingSkeleton />;
  }

  if (
    canEvaluateProfessorOnboarding &&
    !shouldSkipHomeOnboardingForCurrentStage
  ) {
    return (
      <>
          <main
            data-testid="home-onboarding"
            className="mx-auto max-w-6xl px-6 py-8"
          >
          <OnboardingChecklistCard
            title={onboardingState.title}
            description={onboardingState.description}
            nextActionHref={onboardingState.nextActionHref}
            nextActionLabel="继续设置"
            items={[
              { label: "创建发件身份", done: Boolean(selectedIdentity) },
              { label: "配置 AI 模型", done: Boolean(selectedLlmProfile) },
              { label: "准备材料和模板", done: hasMaterialsAndTemplate },
              { label: "导入导师", done: hasAnyProfessors },
            ]}
          />
        </main>
        {confirmDialog}
      </>
    );
  }

  if (
    !selectedIdentityId ||
    !selectedIdentity
  ) {
    return null;
  }

  return (
    <>
      <main data-testid="home-dashboard" className="mx-auto max-w-7xl px-6 py-8">
        <section className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-6 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-3xl font-semibold text-stone-900">
                导师看板
              </h1>
            </div>
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => void loadProfessors()}
                className="ui-btn-secondary"
              >
                <RefreshCcw className="h-4 w-4" />
                刷新列表
              </button>
              <Link
                to="/professors"
                data-interactive="button"
                className="ui-btn-secondary"
              >
                <FolderOpen className="h-4 w-4" />
                管理导师
              </Link>
            </div>
          </div>

          <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(12rem,1fr)_auto_auto] lg:items-stretch">
            <label className="flex items-center gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm text-stone-600 shadow-sm">
              <div className="shrink-0 font-medium leading-5 text-stone-800">
                关键词
              </div>
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <Search className="h-4 w-4 text-stone-400" />
                <input
                  value={filters.keyword}
                  onChange={(event) =>
                    updateFilters({ keyword: event.target.value })
                  }
                  placeholder={getDashboardKeywordSearchPlaceholder(
                    filters.keywordSearchScopes,
                  )}
                  className="w-full min-w-0 bg-transparent leading-5 outline-none"
                />
                <KeywordSearchScopeSelect
                  label="搜索范围"
                  options={DASHBOARD_KEYWORD_SEARCH_SCOPE_OPTIONS}
                  selectedValues={normalizeDashboardKeywordSearchScopes(
                    filters.keywordSearchScopes,
                  )}
                  onChange={setDashboardKeywordSearchScopes}
                />
              </div>
            </label>

            <div className="flex items-center gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm text-stone-600 shadow-sm">
              <div className="shrink-0 font-medium leading-5 text-stone-800">
                排序
              </div>
              <NativeSelectField
                ariaLabel="排序"
                value={sortKey}
                selectedLabel={getSortTriggerLabel(sortKey, currentSortDirection)}
                onChange={(event) =>
                  setSortKey(event.target.value as ProfessorDashboardSortKey)
                }
                wrapperClassName="min-w-0 flex-1"
                shellClassName="!min-h-0 h-8 border-0 bg-stone-50 px-3 py-0 shadow-none"
                renderOption={(option, { selected, selectOption, closeMenu }) => {
                  const optionKey = option.value as ProfessorDashboardSortKey;
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
                        {selected ? <Check className="h-4 w-4 shrink-0" /> : null}
                      </button>
                      <button
                        type="button"
                        aria-label={`切换${option.label}排序方向`}
                        disabled={option.disabled}
                        onClick={(event) => {
                          event.stopPropagation();
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
                {PROFESSOR_DASHBOARD_SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </NativeSelectField>
            </div>

            <button
              type="button"
              onClick={() => setAdvancedFiltersOpen((previous) => !previous)}
              className="ui-btn-secondary h-full justify-center whitespace-nowrap"
            >
              高级筛选
              {activeAdvancedFilterCount > 0
                ? ` ${activeAdvancedFilterCount}`
                : ""}
            </button>

            <button
              type="button"
              onClick={resetAllFilters}
              className="ui-btn-secondary h-full justify-center whitespace-nowrap"
            >
              重置
            </button>
          </div>

          {advancedFiltersOpen ? (
            <div className="mt-3 rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
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

              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                <MultiSelectFilter
                  label="学校"
                  allLabel="全部学校"
                  selectedValues={filters.universities}
                  options={[...filterOptions.universities, NO_FIELD_FILTER_VALUE]}
                  optionLabels={noFieldOptionLabels}
                  onChange={(values) =>
                    setStringFilterValues("universities", values)
                  }
                />
                <MultiSelectFilter
                  label="学院"
                  allLabel="全部学院"
                  selectedValues={filters.schools}
                  options={[...filterOptions.schools, NO_FIELD_FILTER_VALUE]}
                  optionLabels={noFieldOptionLabels}
                  onChange={(values) =>
                    setStringFilterValues("schools", values)
                  }
                />
                <MultiSelectFilter
                  label="系所"
                  allLabel="全部系所"
                  selectedValues={filters.departments}
                  options={[...filterOptions.departments, NO_FIELD_FILTER_VALUE]}
                  optionLabels={noFieldOptionLabels}
                  onChange={(values) =>
                    setStringFilterValues("departments", values)
                  }
                />
                <MultiSelectFilter
                  label="职称"
                  allLabel="全部职称"
                  selectedValues={filters.titles}
                  options={[...filterOptions.titles, NO_FIELD_FILTER_VALUE]}
                  optionLabels={noFieldOptionLabels}
                  onChange={(values) => setStringFilterValues("titles", values)}
                />
                <MultiSelectFilter
                  label="状态"
                  allLabel="全部状态"
                  selectedValues={filters.statuses}
                  options={PROFESSOR_DASHBOARD_STATUS_OPTIONS.map(
                    ([value]) => value,
                  )}
                  optionLabels={dashboardStatusOptionLabels}
                  onChange={(values) =>
                    updateFilters({
                      statuses: values as ProfessorDashboardFilterStatus[],
                    })
                  }
                />
                <MultiSelectFilter
                  label="标签"
                  allLabel="全部标签"
                  selectedValues={filters.tagIds}
                  options={tagFilterEntries.map((entry) => entry.value)}
                  optionLabels={tagOptionLabels}
                  onChange={(values) => setStringFilterValues("tagIds", values)}
                />
                <div className="block">
                  <div className="mb-2 text-sm font-medium text-stone-800">
                    匹配度区间
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      min={0}
                      max={100}
                      step={1}
                      aria-label="最低匹配度"
                      aria-invalid={hasInvalidMatchScoreRange}
                      aria-describedby={
                        hasInvalidMatchScoreRange
                          ? "match-score-range-error"
                          : undefined
                      }
                      value={
                        filters.minMatchScore === NO_MATCH_SCORE_FILTER_VALUE
                          ? ""
                          : filters.minMatchScore
                      }
                      onChange={(event) =>
                        handleMatchScoreBoundaryChange(
                          "minMatchScore",
                          event.target.value,
                        )
                      }
                      disabled={
                        filters.minMatchScore === NO_MATCH_SCORE_FILTER_VALUE
                      }
                      placeholder="最低 0"
                      className="ui-select-shell min-w-0 flex-1"
                    />
                    <span aria-hidden="true" className="text-stone-400">
                      —
                    </span>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      step={1}
                      aria-label="最高匹配度"
                      aria-invalid={hasInvalidMatchScoreRange}
                      aria-describedby={
                        hasInvalidMatchScoreRange
                          ? "match-score-range-error"
                          : undefined
                      }
                      value={filters.maxMatchScore}
                      onChange={(event) =>
                        handleMatchScoreBoundaryChange(
                          "maxMatchScore",
                          event.target.value,
                        )
                      }
                      disabled={
                        filters.minMatchScore === NO_MATCH_SCORE_FILTER_VALUE
                      }
                      placeholder="最高 100"
                      className="ui-select-shell min-w-0 flex-1"
                    />
                  </div>
                  {hasInvalidMatchScoreRange ? (
                    <div
                      id="match-score-range-error"
                      role="alert"
                      className="mt-2 text-xs text-red-600"
                    >
                      最低匹配度不能高于最高匹配度
                    </div>
                  ) : null}
                  <label className="mt-2 flex items-center gap-2 text-sm text-stone-700">
                    <SelectionToggleButton
                      label="仅看无匹配度"
                      selected={
                        filters.minMatchScore === NO_MATCH_SCORE_FILTER_VALUE
                      }
                      semantics="checkbox"
                      size="sm"
                      onToggle={() =>
                        updateFilters({
                          minMatchScore:
                            filters.minMatchScore !== NO_MATCH_SCORE_FILTER_VALUE
                            ? NO_MATCH_SCORE_FILTER_VALUE
                            : "",
                          maxMatchScore: "",
                        })
                      }
                    />
                    仅看无匹配度
                  </label>
                </div>
              </div>
            </div>
          ) : null}
        </section>

        <section
          ref={professorListStartRef}
          tabIndex={-1}
          aria-label="导师看板列表"
          className="mt-6 scroll-mt-6 overflow-hidden rounded-3xl border border-stone-200 bg-white shadow-sm focus:outline-none"
        >
          <div className="flex flex-wrap items-center gap-3 border-b border-stone-100 px-6 py-4">
            {agentSelection ? (
              <div className="w-full">
                <AgentProfessorSelectionBanner
                  selectionCount={agentSelection.selectionCount}
                  totalSelectedCount={selectedIds.size}
                  selectionMode={agentSelection.selectionMode}
                  selectedOnly={agentSelection.selectedOnly}
                  onExitSelectedOnly={exitAgentSelectedOnly}
                  onUndo={undoAgentSelection}
                  onClear={clearAgentSelection}
                />
              </div>
            ) : null}
            {totalProfessorCount > 0 ? (
              <button
                type="button"
                aria-label={
                  allFilteredProfessorsSelected
                    ? "取消全选"
                    : "全选当前结果"
                }
                aria-pressed={allFilteredProfessorsSelected}
                disabled={selectingAllProfessors}
                onClick={() => void handleToggleFilteredProfessors()}
                className={`inline-flex min-h-10 items-center gap-2 rounded-2xl border px-3 text-sm font-medium transition hover:border-primary/40 hover:bg-white hover:text-primary ${
                  allFilteredProfessorsSelected
                    ? "border-primary/30 bg-primary/5 text-primary"
                    : "border-stone-200 bg-stone-50 text-stone-700"
                }`}
              >
                {selectingAllProfessors ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : allFilteredProfessorsSelected ? (
                  <SquareCheck className="h-4 w-4" />
                ) : (
                  <Square className="h-4 w-4" />
                )}
                {selectingAllProfessors
                  ? "正在全选"
                  : allFilteredProfessorsSelected
                  ? "取消全选"
                  : "全选当前结果"}
              </button>
            ) : null}
            <div className="text-sm text-stone-600">
              共 {totalProfessorCount} 位导师，已选择 {selectedIds.size} 位
            </div>
          </div>

          {loading ? (
            <div className="flex items-center justify-center gap-2 px-6 py-14 text-sm text-stone-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载导师列表…
            </div>
          ) : totalProfessorCount === 0 ? (
            <div className="px-6 py-14 text-center text-sm text-stone-500">
              {hasAnyProfessors ? (
                <>
                  <div>没有符合当前搜索或筛选条件的导师</div>
                  <button
                    type="button"
                    onClick={resetAllFilters}
                    className="ui-btn-secondary mt-5"
                  >
                    <RefreshCcw className="h-4 w-4" />
                    清除筛选
                  </button>
                </>
              ) : (
                <>
                  <div>暂无导师</div>
                  <Link
                    to="/professors"
                    data-interactive="button"
                    className="ui-btn-primary mt-5"
                  >
                    去导师管理
                  </Link>
                </>
              )}
            </div>
          ) : (
            <div className="divide-y divide-stone-100">
              {pagedProfessors.map((professor) => (
                <DashboardProfessorRow
                  key={professor.id}
                  professor={professor}
                  selected={selectedIds.has(professor.id)}
                  bulkDisabled={bulkScoring}
                  scoring={scoringProfessorIds.has(professor.id)}
                  canCalculateMatch={hasMatchEvidence(professor)}
                  statusLabel={getProfessorDashboardStatusLabel(
                    professor.status,
                  )}
                  timeHighlight={getProfessorTimeHighlight(professor, sortKey)}
                  timeLabel={
                    sortKey === "lastSentAt"
                      ? formatDashboardTimeLabel("发送", professor.last_sent_at)
                      : sortKey === "lastRepliedAt"
                        ? formatDashboardTimeLabel("回复", professor.last_replied_at)
                        : null
                  }
                  onToggleSelection={() => toggleSelection(professor.id)}
                  onCalculateMatch={() => void handleGenerateOne(professor.id)}
                  onOpenWorkspace={() => navigate(`/workspace/${professor.id}`)}
                  onEditNote={() => setNoteEditorProfessor(professor)}
                  onAddTag={() => openTagEditor(professor)}
                />
              ))}
            </div>
          )}
          {!loading && totalProfessorCount > 0 ? (
            <Pagination
              page={safeCurrentPage}
              pageSize={pageSize}
              totalCount={totalProfessorCount}
              onChange={handlePaginationChange}
              ariaLabel="导师看板分页"
              unitLabel="位"
              itemLabel="位导师"
              summary={`${totalProfessorCount} 位 · ${safeCurrentPage}/${totalPages} 页 · 已选 ${selectedIds.size} 位`}
              focusTargetRef={professorListStartRef}
              className="border-t border-stone-100 px-6 py-4"
            />
          ) : null}
        </section>

        {selectedIds.size > 0 ? (
          <div className="pointer-events-none sticky bottom-4 z-20 mt-6 flex justify-center px-2">
            <div className="pointer-events-auto flex w-fit max-w-full flex-wrap items-center justify-center gap-3 rounded-[28px] border border-stone-200 bg-white/95 px-5 py-4 shadow-[0_18px_34px_-24px_rgba(41,37,36,0.36)] backdrop-blur-xl">
              <div className="shrink-0">
                <div className="text-sm font-medium text-stone-900">
                  已选中 {selectedIds.size} 位导师
                </div>
                <div className="mt-1 text-xs text-stone-500">
                  {matchUsesGroupSource
                    ? `统一使用 ${matchSourceName} 的默认材料`
                    : "批量操作"}
                </div>
              </div>
              <div className="flex flex-wrap justify-center gap-3">
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
                <button
                  type="button"
                  onClick={() => void handleGenerateSelected()}
                  disabled={bulkScoring}
                  className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {bulkScoring ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Sparkles className="h-4 w-4" />
                  )}
                  批量分析匹配度
                </button>
                <button
                  type="button"
                  onClick={() => void handleCreateTask()}
                  className="ui-btn-primary"
                >
                  <MailPlus className="h-4 w-4" />
                  创建批量任务
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </main>
      <ProfessorTagAssignmentDialog
        open={Boolean(tagEditorProfessor)}
        scopeKey={tagEditorProfessor?.id ?? null}
        professorName={tagEditorProfessor?.name ?? ""}
        tags={professorTags}
        selectedTagIds={tagEditorSelectedIds}
        saving={savingProfessorTags}
        creating={creatingProfessorTag}
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
        creating={creatingProfessorTag}
        onCreateTag={handleCreateAssignmentTag}
        onDeleteTag={(tag) => void handleDeleteProfessorTag(tag)}
        onSave={(payload) => void saveBulkTags(payload)}
        onClose={() => {
          if (!savingBulkTags && !creatingProfessorTag) {
            setBulkTagDialogOpen(false);
          }
        }}
      />
      {confirmDialog}
    </>
  );
};
