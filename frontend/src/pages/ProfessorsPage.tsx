import {
  type ChangeEvent,
  type ClipboardEvent as ReactClipboardEvent,
  type DragEvent as ReactDragEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import clsx from "clsx";
import { useSearchParams } from "react-router-dom";
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
  Minus,
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
import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { BulkProfessorTagDialog } from "@/components/molecules/BulkProfessorTagDialog";
import { KeywordSearchScopeSelect } from "@/components/molecules/KeywordSearchScopeSelect";
import { ManagementProfessorRow } from "@/components/molecules/ManagementProfessorRow";
import { MultiSelectFilter } from "@/components/molecules/MultiSelectFilter";
import { PageSizeSelector } from "@/components/molecules/PageSizeSelector";
import { ProfessorNoteDialog } from "@/components/molecules/ProfessorNoteDialog";
import { ProfessorTagAssignmentDialog } from "@/components/molecules/ProfessorTagAssignmentDialog";
import { ProfessorTagSelector } from "@/components/molecules/ProfessorTagSelector";
import { useBackgroundTaskNotification } from "@/context/BackgroundTaskNotificationContext";
import { useNotification } from "@/context/NotificationContext";
import { useSelectionContext } from "@/context/SelectionContext";
import { safeRecordUserAction } from "@/lib/diagnosticUserActions";
import {
  getPageItems,
  getStoredPageSize,
  getTotalPages,
  setStoredPageSize,
} from "@/lib/pagination";
import {
  normalizeExternalHttpUrl,
  openExternalHttpUrl,
} from "@/lib/externalUrls";
import { useConfirmDialog } from "@/lib/useConfirmDialog";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";
import { useDocumentScrollLock } from "@/lib/useDocumentScrollLock";
import { createCrawlJob } from "@/lib/api/crawlJobsApi";
import {
  createProfessorInformationEnrichmentJob,
  createSingleProfessorInformationEnrichment,
  getActiveProfessorInformationEnrichment,
  getProfessorInformationEnrichmentJob,
} from "@/lib/api/professorInformationEnrichmentApi";
import {
  archiveProfessor,
  bulkUpdateProfessorTags,
  bulkArchiveProfessors,
  createProfessor,
  createProfessorTag,
  deleteProfessorTag,
  getProfessorExportDownloadUrl,
  getProfessor,
  getProfessorTemplateDownloadUrl,
  importProfessorsFromFile,
  getProfessorTagUsage,
  listProfessorTags,
  listProfessorsForManagement,
  restoreProfessor,
  updateProfessor,
  updateProfessorNote,
  updateProfessorTags,
} from "@/lib/api/professorsApi";
import type {
  CrawlJobEntryTypeDTO,
  ProfessorImportFileResultDTO,
  ProfessorInformationEnrichmentJobDTO,
  ProfessorManagementItemDTO,
  ProfessorBulkTagModeDTO,
  ProfessorTagDTO,
  ProfessorTagPayloadDTO,
  ProfessorUpsertPayloadDTO,
} from "@/types";
import {
  MANAGEMENT_KEYWORD_SEARCH_SCOPE_OPTIONS,
  buildManagementFilterOptions,
  createDefaultManagementFilters,
  filterManagementProfessors,
  getActiveManagementAdvancedFilterCount,
  getManagementKeywordSearchPlaceholder,
  normalizeManagementKeywordSearchScopes,
  NO_FIELD_FILTER_VALUE,
  NO_TAG_FILTER_VALUE,
  pruneManagementFilters,
  type ProfessorManagementKeywordSearchScope,
  type ProfessorManagementFilterState,
} from "@/features/professor-management/client/filterManagementProfessors";
import {
  bulkTagConfirmLabels,
  buildBulkTagConfirmDescription,
} from "@/features/professor-management/client/bulkTagConfirmCopy";
import {
  DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS,
  PROFESSOR_MANAGEMENT_SORT_OPTIONS,
  sortManagementProfessors,
  type ProfessorManagementSortDirection,
  type ProfessorManagementSortKey,
} from "@/features/professor-management/client/sortManagementProfessors";

type ArchiveFilter = "active" | "archived" | "all";
const noFieldOptionLabels = { [NO_FIELD_FILTER_VALUE]: "无" };
const activeInformationEnrichmentStatuses = new Set(["queued", "running"]);
type TrackedSingleInformationEnrichment = {
  job: ProfessorInformationEnrichmentJobDTO;
  professorName: string;
};
type ProfessorFormState = {
  name: string;
  email: string;
  title: string;
  university: string;
  school: string;
  department: string;
  research_direction: string;
  recent_papers_text: string;
  personal_note: string;
  profile_url: string;
  source_url: string;
  tag_ids: number[];
};
type CrawlerJobFormState = {
  university: string;
  school: string;
  start_urls: string[];
  entry_type: CrawlJobEntryTypeDTO;
};
type IntakeActionTone = "primary" | "amber" | "stone" | "emerald";

const PROFESSORS_FILTERS_STORAGE_KEY = "professors_page_filters";
const PROFESSORS_PAGE_SIZE_STORAGE_KEY = "professors-management:page-size";
const MENTOR_CRAWLER_SKILL_GUIDE_URL =
  "https://juniexd.github.io/AutoEmailSender/docs/mentor-crawler-skill";
const managementTableColumns =
  "lg:grid-cols-[2.75rem_minmax(0,0.72fr)_minmax(0,0.74fr)_minmax(0,1.08fr)_minmax(0,1.18fr)_minmax(0,1.56fr)_minmax(0,0.78fr)_minmax(12rem,0.92fr)]";

const archiveFilterLabels: Record<ArchiveFilter, string> = {
  active: "正常",
  archived: "已删除",
  all: "全部",
};

const professorManagementSortKeyValues = new Set<ProfessorManagementSortKey>(
  PROFESSOR_MANAGEMENT_SORT_OPTIONS.map((option) => option.value),
);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const readStringArray = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];

const isArchiveFilter = (value: unknown): value is ArchiveFilter =>
  value === "active" || value === "archived" || value === "all";

const isProfessorManagementSortKey = (
  value: unknown,
): value is ProfessorManagementSortKey =>
  typeof value === "string" &&
  professorManagementSortKeyValues.has(value as ProfessorManagementSortKey);

const isProfessorManagementSortDirection = (
  value: unknown,
): value is ProfessorManagementSortDirection =>
  value === "asc" || value === "desc";

const readStoredManagementSortDirections = (
  value: unknown,
): Record<ProfessorManagementSortKey, ProfessorManagementSortDirection> => {
  const defaults = { ...DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS };
  if (!isRecord(value)) {
    return defaults;
  }

  PROFESSOR_MANAGEMENT_SORT_OPTIONS.forEach((option) => {
    const direction = value[option.value];
    if (isProfessorManagementSortDirection(direction)) {
      defaults[option.value] = direction;
    }
  });
  return defaults;
};

const getManagementSortOptionLabel = (sortKey: ProfessorManagementSortKey) =>
  PROFESSOR_MANAGEMENT_SORT_OPTIONS.find((option) => option.value === sortKey)
    ?.label ?? "";

const getManagementSortDirectionSymbol = (
  direction: ProfessorManagementSortDirection,
) => (direction === "desc" ? "↓" : "↑");

const getManagementSortTriggerLabel = (
  sortKey: ProfessorManagementSortKey,
  direction: ProfessorManagementSortDirection,
) =>
  `${getManagementSortOptionLabel(sortKey)} ${getManagementSortDirectionSymbol(
    direction,
  )}`;

const readStoredProfessorManagementState = () => {
  const defaults = {
    archiveFilter: "active" as ArchiveFilter,
    filters: createDefaultManagementFilters(),
    advancedFiltersOpen: false,
    sortKey: "latest" as ProfessorManagementSortKey,
    sortDirections: { ...DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS },
    currentPage: 1,
  };

  if (typeof window === "undefined") {
    return defaults;
  }

  try {
    const rawValue = window.sessionStorage.getItem(
      PROFESSORS_FILTERS_STORAGE_KEY,
    );
    if (!rawValue) {
      return defaults;
    }

    const parsedValue = JSON.parse(rawValue);
    if (!isRecord(parsedValue)) {
      return defaults;
    }

    const filters = isRecord(parsedValue.filters)
      ? parsedValue.filters
      : null;

    const nextFilters = createDefaultManagementFilters();
    nextFilters.keyword =
      typeof filters?.keyword === "string" ? filters.keyword : "";
    nextFilters.keywordSearchScopes = normalizeManagementKeywordSearchScopes(
      filters?.keywordSearchScopes,
    );
    nextFilters.universities = readStringArray(filters?.universities);
    nextFilters.schools = readStringArray(filters?.schools);
    nextFilters.departments = readStringArray(filters?.departments);
    nextFilters.titles = readStringArray(filters?.titles);
    nextFilters.tagIds = readStringArray(filters?.tagIds);

    const nextSortKey = isProfessorManagementSortKey(parsedValue.sortKey)
      ? parsedValue.sortKey
      : defaults.sortKey;
    const nextSortDirections = readStoredManagementSortDirections(
      parsedValue.sortDirections,
    );
    if (
      isProfessorManagementSortDirection(parsedValue.sortDirection) &&
      !isRecord(parsedValue.sortDirections)
    ) {
      nextSortDirections[nextSortKey] = parsedValue.sortDirection;
    }

    return {
      archiveFilter: isArchiveFilter(parsedValue.archiveFilter)
        ? parsedValue.archiveFilter
        : defaults.archiveFilter,
      filters: nextFilters,
      advancedFiltersOpen:
        typeof parsedValue.advancedFiltersOpen === "boolean"
          ? parsedValue.advancedFiltersOpen
          : defaults.advancedFiltersOpen,
      sortKey: nextSortKey,
      sortDirections: nextSortDirections,
      currentPage:
        typeof parsedValue.currentPage === "number" &&
        Number.isFinite(parsedValue.currentPage) &&
        parsedValue.currentPage > 0
          ? Math.floor(parsedValue.currentPage)
          : defaults.currentPage,
    };
  } catch {
    return defaults;
  }
};

const writeStoredProfessorManagementState = (
  state: {
    archiveFilter: ArchiveFilter;
    filters: ProfessorManagementFilterState;
    advancedFiltersOpen: boolean;
    sortKey: ProfessorManagementSortKey;
    sortDirections: Record<
      ProfessorManagementSortKey,
      ProfessorManagementSortDirection
    >;
    currentPage: number;
  },
) => {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.sessionStorage.setItem(
      PROFESSORS_FILTERS_STORAGE_KEY,
      JSON.stringify(state),
    );
  } catch {
    // 缓存丢失不影响页面使用。
  }
};

const emptyProfessorForm = (): ProfessorFormState => ({
  name: "",
  email: "",
  title: "",
  university: "",
  school: "",
  department: "",
  research_direction: "",
  recent_papers_text: "",
  personal_note: "",
  profile_url: "",
  source_url: "",
  tag_ids: [],
});

const emptyCrawlerJobForm = (): CrawlerJobFormState => ({
  university: "",
  school: "",
  start_urls: [""],
  entry_type: "list",
});

const normalizeCrawlerStartUrls = (urls: string[]) => {
  const seen = new Set<string>();
  return urls
    .map((url) => url.trim())
    .filter((url) => {
      if (!url || seen.has(url)) {
        return false;
      }
      seen.add(url);
      return true;
    });
};

const buildCrawlerStartUrlsAfterMultilinePaste = (
  urls: string[],
  targetIndex: number,
  pastedText: string,
) => {
  if (!/[\r\n]/.test(pastedText)) {
    return null;
  }

  const pastedUrls = normalizeCrawlerStartUrls(
    pastedText.split(/\r\n|\r|\n/),
  );
  if (pastedUrls.length < 2) {
    return null;
  }

  const nextUrls = normalizeCrawlerStartUrls([
    ...urls.slice(0, targetIndex),
    ...pastedUrls,
    ...urls.slice(targetIndex + 1),
  ]);
  return nextUrls.length > 0 ? nextUrls : [""];
};

const toProfessorForm = (
  professor: ProfessorManagementItemDTO,
): ProfessorFormState => ({
  name: professor.name,
  email: professor.email ?? "",
  title: professor.title ?? "",
  university: professor.university ?? "",
  school: professor.school ?? "",
  department: professor.department ?? "",
  research_direction: professor.research_direction ?? "",
  recent_papers_text: professor.recent_papers.join("\n"),
  personal_note: professor.personal_note ?? "",
  profile_url: professor.profile_url ?? "",
  source_url: professor.source_url ?? "",
  tag_ids: professor.tags.map((tag) => tag.id),
});

const toProfessorPayload = (
  form: ProfessorFormState,
): ProfessorUpsertPayloadDTO => ({
  name: form.name.trim(),
  email: form.email.trim(),
  title: form.title.trim() || null,
  university: form.university.trim() || null,
  school: form.school.trim() || null,
  department: form.department.trim() || null,
  research_direction: form.research_direction.trim() || null,
  recent_papers: form.recent_papers_text
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean),
  personal_note: form.personal_note.trim() || null,
  profile_url: form.profile_url.trim() || null,
  source_url: form.source_url.trim() || null,
  tag_ids: form.tag_ids,
});

const fieldLabelClassName =
  "mb-2 inline-flex items-center gap-1 text-sm font-medium text-stone-800";
const inputClassName =
  "w-full rounded-2xl border border-stone-200 bg-white px-3 py-2.5 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15";
const urlInputWithActionClassName =
  "w-full rounded-2xl border border-stone-200 bg-white py-2.5 pl-3 pr-11 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15";

const renderFieldLabel = (label: string, required = false) => (
  <span className={fieldLabelClassName}>
    {required ? (
      <span className="text-base leading-none text-red-500">*</span>
    ) : null}
    <span>{label}</span>
  </span>
);

const UrlInputField = ({
  id,
  label,
  value,
  placeholder,
  openLabel,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  placeholder: string;
  openLabel: string;
  onChange: (value: string) => void;
}) => {
  const openableUrl = normalizeExternalHttpUrl(value);

  return (
    <div className="block">
      <label htmlFor={id}>{renderFieldLabel(label)}</label>
      <div className="relative">
        <input
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className={urlInputWithActionClassName}
          placeholder={placeholder}
        />
        <button
          type="button"
          aria-label={openLabel}
          title={openLabel}
          disabled={!openableUrl}
          onClick={() => {
            if (!openableUrl) {
              return;
            }
            openExternalHttpUrl(openableUrl);
          }}
          className="absolute right-1.5 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-xl border border-stone-200 bg-stone-50 text-stone-500 transition hover:border-primary/40 hover:bg-white hover:text-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-45"
        >
          <ExternalLink className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
};

const triggerDownload = (url: string) => {
  const link = document.createElement("a");
  link.href = url;
  document.body.appendChild(link);
  link.click();
  link.remove();
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
        <h2 className="text-base font-semibold leading-6 text-stone-900">{label}</h2>
      </div>
    </div>
    <div className="flex w-full flex-wrap gap-2">{children}</div>
  </article>
);

const ModalShell = ({
  open,
  title,
  description,
  onClose,
  children,
  headerAction,
  maxWidthClassName = "max-w-3xl",
}: {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  headerAction?: ReactNode;
  maxWidthClassName?: string;
}) => {
  const {
    onBackdropClick,
    onBackdropMouseDown,
    onContentClick,
    onContentMouseDown,
  } =
    useDismissableLayerClick(onClose);
  useDocumentScrollLock(open);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  if (!open) {
    return null;
  }

  return (
    <div
      role="dialog"
      aria-label={title}
      aria-modal="true"
      className="fixed inset-0 z-[80] flex items-center justify-center bg-stone-950/35 p-4 backdrop-blur-md"
      onClick={onBackdropClick}
      onMouseDown={onBackdropMouseDown}
    >
      <div
        className={clsx(
          "relative w-full overflow-hidden rounded-[32px] border border-stone-200/80 bg-[linear-gradient(180deg,rgba(255,252,246,0.98),rgba(255,245,233,0.96))] shadow-[0_34px_90px_-32px_rgba(41,37,36,0.5)]",
          maxWidthClassName,
        )}
        onClick={onContentClick}
        onMouseDown={onContentMouseDown}
      >
        <div
          data-testid="professor-modal-scroll"
          className="relative max-h-[85vh] overflow-y-auto overscroll-contain px-6 py-6"
        >
          <div className="min-w-0">
            <div className="flex min-w-0 items-start justify-between gap-4">
              <h2 className="min-w-0 break-words text-2xl font-semibold tracking-[0.01em] text-stone-900">
                {title}
              </h2>
              {headerAction ? (
                <div className="shrink-0">{headerAction}</div>
              ) : null}
            </div>
            {description ? (
              <p className="mt-2 max-w-2xl text-sm leading-6 text-stone-600">
                {description}
              </p>
            ) : null}
          </div>
          {children}
        </div>
      </div>
    </div>
  );
};

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
            <div key={index} className="h-[7.5rem] animate-pulse rounded-[24px] border border-stone-200 bg-white" />
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
          <div key={index} className="h-3 animate-pulse rounded-full bg-stone-100" />
        ))}
      </div>
      <div className="divide-y divide-stone-100">
        {Array.from({ length: 6 }, (_, index) => (
          <div
            key={index}
            className="grid gap-4 px-6 py-5 lg:grid-cols-[2.75rem_minmax(0,0.72fr)_minmax(0,0.74fr)_minmax(0,1.08fr)_minmax(0,1.18fr)_minmax(0,1.56fr)_minmax(0,0.78fr)_minmax(12rem,0.92fr)]"
          >
            {Array.from({ length: 8 }, (_, itemIndex) => (
              <div key={itemIndex} className="h-4 animate-pulse rounded-full bg-stone-100" />
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
  const { confirm, dialog: confirmDialog } = useConfirmDialog();
  const { selectedLlmProfileId } = useSelectionContext();
  const { notifyError, notifySuccess, notifyWarning } = useNotification();
  const { trackCrawlJob, trackInformationEnrichmentJob } =
    useBackgroundTaskNotification();
  const storedState = useMemo(() => {
    const state = readStoredProfessorManagementState();
    if (!linkedKeyword) {
      return state;
    }
    return {
      ...state,
      archiveFilter: "active" as ArchiveFilter,
      filters: {
        ...createDefaultManagementFilters(),
        keyword: linkedKeyword,
      },
      advancedFiltersOpen: false,
      currentPage: 1,
    };
  }, [linkedKeyword]);
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
  const [tagEditorSelectedIds, setTagEditorSelectedIds] = useState<number[]>([]);
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
  const [loading, setLoading] = useState(false);
  const [hasLoadedProfessors, setHasLoadedProfessors] = useState(false);
  const latestProfessorsRequestIdRef = useRef(0);
  const [upsertModalOpen, setUpsertModalOpen] = useState(false);
  const [editingProfessor, setEditingProfessor] =
    useState<ProfessorManagementItemDTO | null>(null);
  const [formState, setFormState] =
    useState<ProfessorFormState>(emptyProfessorForm());
  const [savingProfessor, setSavingProfessor] = useState(false);
  const [startingSingleInformationEnrichmentIds, setStartingSingleInformationEnrichmentIds] =
    useState<Set<number>>(new Set());
  const [singleInformationEnrichments, setSingleInformationEnrichments] =
    useState<Record<number, TrackedSingleInformationEnrichment>>({});
  const [creatingBulkInformationEnrichment, setCreatingBulkInformationEnrichment] =
    useState(false);
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importingFile, setImportingFile] = useState(false);
  const [importResult, setImportResult] =
    useState<ProfessorImportFileResultDTO | null>(null);
  const [exportModalOpen, setExportModalOpen] = useState(false);
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

  useEffect(() => {
    if (!linkedKeyword) {
      return;
    }
    setArchiveFilter("active");
    setCurrentPage(1);
    setSelectedIds(new Set());
    setAdvancedFiltersOpen(false);
    setSortKey("latest");
    setSortDirections({ ...DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS });
    setFilters({ ...createDefaultManagementFilters(), keyword: linkedKeyword });
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.delete("keyword");
      return next;
    }, { replace: true });
  }, [linkedKeyword, setSearchParams]);
  const loadProfessors = useCallback(
    async (filter: ArchiveFilter = archiveFilter) => {
      const requestId = latestProfessorsRequestIdRef.current + 1;
      latestProfessorsRequestIdRef.current = requestId;
      setLoading(true);
      try {
        const data = await listProfessorsForManagement(filter);
        if (latestProfessorsRequestIdRef.current !== requestId) {
          return;
        }
        setProfessors(data);
        setHasLoadedProfessors(true);
        setSelectedIds((previous) => {
          const next = new Set<number>();
          data.forEach((item) => {
            if (item.archived_at) {
              return;
            }
            if (previous.has(item.id)) {
              next.add(item.id);
            }
          });
          return next;
        });
      } catch (loadError) {
        if (latestProfessorsRequestIdRef.current !== requestId) {
          return;
        }
        setHasLoadedProfessors(true);
        const message = getActionErrorMessage(loadError, "加载导师列表失败");
        notifyError("加载导师列表失败", message);
      } finally {
        if (latestProfessorsRequestIdRef.current === requestId) {
          setLoading(false);
        }
      }
    },
    [archiveFilter, notifyError, setSelectedIds],
  );

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
          email: previous.email.trim() ? previous.email : (refreshed.email ?? ""),
          title: previous.title.trim() ? previous.title : (refreshed.title ?? ""),
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
      ([, tracked]) => activeInformationEnrichmentStatuses.has(tracked.job.status),
    );
    if (activeEntries.length === 0) {
      return;
    }
    let disposed = false;
    const poll = async () => {
      await Promise.all(
        activeEntries.map(async ([professorIdText, tracked]) => {
          try {
            const job = await getProfessorInformationEnrichmentJob(tracked.job.id);
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
    if (professors.length === 0) {
      return;
    }
    setFilters((previous) => pruneManagementFilters(professors, previous));
  }, [professors]);

  useEffect(() => {
    writeStoredProfessorManagementState({
      archiveFilter,
      filters,
      advancedFiltersOpen,
      sortKey,
      sortDirections,
      currentPage,
    });
  }, [
    archiveFilter,
    advancedFiltersOpen,
    currentPage,
    filters,
    sortDirections,
    sortKey,
  ]);

  const filterOptions = useMemo(
    () =>
      buildManagementFilterOptions(professors, {
        universities: filters.universities,
        schools: filters.schools,
      }),
    [filters.schools, filters.universities, professors],
  );
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
  const tagLabelByValue = useMemo(
    () => new Map(tagFilterEntries.map((entry) => [entry.value, entry.label])),
    [tagFilterEntries],
  );
  const tagValueByLabel = useMemo(
    () => new Map(tagFilterEntries.map((entry) => [entry.label, entry.value])),
    [tagFilterEntries],
  );
  const selectedTagLabels = useMemo(
    () =>
      filters.tagIds
        .map((value) => tagLabelByValue.get(value))
        .filter((value): value is string => Boolean(value)),
    [filters.tagIds, tagLabelByValue],
  );
  const filteredProfessors = useMemo(
    () => filterManagementProfessors(professors, filters),
    [filters, professors],
  );
  const currentSortDirection = sortDirections[sortKey];
  const visibleProfessors = useMemo(
    () =>
      sortManagementProfessors(
        filteredProfessors,
        sortKey,
        currentSortDirection,
      ),
    [currentSortDirection, filteredProfessors, sortKey],
  );

  const updateFilters = (nextFilters: Partial<ProfessorManagementFilterState>) => {
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

  const toggleFilterValue = (
    key: "universities" | "schools" | "departments" | "titles" | "tagIds",
    value: string,
  ) => {
    setCurrentPage(1);
    setFilters((previous) => {
      const currentValues = previous[key];
      const nextValues = currentValues.includes(value)
        ? currentValues.filter((item) => item !== value)
        : [...currentValues, value];

      if (key === "universities" || key === "schools") {
        const nextFilters = {
          ...previous,
          [key]: nextValues,
        };
        return pruneManagementFilters(professors, nextFilters);
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
    setSortKey("latest");
    setSortDirections({ ...DEFAULT_PROFESSOR_MANAGEMENT_SORT_DIRECTIONS });
  };

  const totalPages = useMemo(
    () => getTotalPages(visibleProfessors.length, pageSize),
    [pageSize, visibleProfessors.length],
  );
  const safeCurrentPage = Math.min(currentPage, totalPages);
  const paginatedProfessors = useMemo(
    () => getPageItems(visibleProfessors, safeCurrentPage, pageSize),
    [pageSize, safeCurrentPage, visibleProfessors],
  );
  const isProfessorSelectable = useCallback(
    (professor: ProfessorManagementItemDTO) =>
      archiveFilter === "archived"
        ? Boolean(professor.archived_at)
        : !professor.archived_at,
    [archiveFilter],
  );
  const filteredSelectableIds = useMemo(
    () =>
      visibleProfessors
        .filter(isProfessorSelectable)
        .map((professor) => professor.id),
    [isProfessorSelectable, visibleProfessors],
  );
  const filteredSelectedCount = useMemo(
    () => filteredSelectableIds.filter((id) => selectedIds.has(id)).length,
    [filteredSelectableIds, selectedIds],
  );
  const someFilteredSelected = filteredSelectedCount > 0;
  const allFilteredSelected =
    filteredSelectableIds.length > 0 &&
    filteredSelectedCount === filteredSelectableIds.length;
  const openCreateModal = () => {
    setEditingProfessor(null);
    setFormState(emptyProfessorForm());
    setUpsertModalOpen(true);
  };

  const handlePageSizeChange = (nextPageSize: number) => {
    setPageSize(nextPageSize);
    setStoredPageSize(PROFESSORS_PAGE_SIZE_STORAGE_KEY, nextPageSize);
    setCurrentPage(1);
  };

  const handleToggleFilteredSelection = () => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      const allSelected =
        filteredSelectableIds.length > 0 &&
        filteredSelectableIds.every((id) => previous.has(id));

      if (allSelected) {
        filteredSelectableIds.forEach((id) => next.delete(id));
      } else {
        filteredSelectableIds.forEach((id) => next.add(id));
      }
      return next;
    });
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
        notifySuccess("保存成功", `已更新导师“${payload.name}”。`);
      } else {
        await createProfessor(payload);
        notifySuccess("保存成功", `已新增导师“${payload.name}”。`);
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
      description:
        "将访问已保存的主页链接补全缺失信息，不会覆盖已有内容，并计入 Token 消耗。",
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
      notifySuccess("标签已置顶", `已将“${updatedProfessor.name}”的标签排序更新。`);
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
      notifySuccess("标签已更新", `已更新“${updatedProfessor.name}”的导师标签。`);
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
      notifySuccess("备注已更新", `已更新“${noteEditorProfessor.name}”的个人备注。`);
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
      notifySuccess("创建标签成功", `已新增标签“${createdTag.name}”。`);
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

  const handleCreateAssignmentTag = async (
    payload: ProfessorTagPayloadDTO,
  ) => {
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
      const tagsByProfessorId = new Map(
        result.professors.map((professor) => [professor.id, professor.tags]),
      );
      setProfessors((previous) =>
        previous.map((professor) => {
          const tags = tagsByProfessorId.get(professor.id);
          return tags ? { ...professor, tags } : professor;
        }),
      );
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
      const result = await deleteProfessorTag(tag.id);
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
        "移入回收站后，这位导师会从首页与正常列表中隐藏，但历史任务和通信会保留。",
      confirmLabel: "确认移入",
      cancelLabel: "取消",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }
    try {
      const result = await archiveProfessor(professor.id);
      notifySuccess("操作成功", result.message);
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
      description: "移入后会从首页与正常列表中隐藏，但历史任务和通信不会删除。",
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

  const handleDownloadTemplate = (format: "xlsx" | "csv") => {
    triggerDownload(getProfessorTemplateDownloadUrl(format));
  };

  const handleDownloadExport = (format: "xlsx" | "csv") => {
    triggerDownload(getProfessorExportDownloadUrl(format));
  };

  const handleChooseImportFile = (event: ChangeEvent<HTMLInputElement>) => {
    const nextFile = event.target.files?.[0] ?? null;
    setImportFile(nextFile);
    setImportResult(null);
  };

  const handleChooseDesktopImportFile = async () => {
    try {
      const selectedFile = await window.autoEmailSender?.selectProfessorImportFile?.();
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

  const handleImportDropZoneClick = (event: ReactMouseEvent<HTMLLabelElement>) => {
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
      notifyWarning("请先选择模型", "智能爬取会使用当前顶部栏选择的模型。");
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
      notifySuccess(
        "抓取任务已创建",
        "任务中心会继续后台抓取，请到任务中心的教师抓取页签查看进度。",
      );
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
                导师档案管理
              </h1>
            </div>
          </div>

          {professors.length > 0 ? (
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
                  label="模板批量新增"
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
                    模板导入
                  </button>
                </IntakeActionCard>

                <IntakeActionCard
                  label="单个新增"
                  icon={<Plus className="h-5 w-5" />}
                  tone="stone"
                >
                  <button
                    type="button"
                    onClick={openCreateModal}
                    className="ui-btn-secondary h-10 w-full rounded-2xl"
                  >
                    <Plus className="h-4 w-4" />
                    新增导师
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
                    setArchiveFilter(item);
                    setCurrentPage(1);
                    setSelectedIds(new Set());
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
              <label className="flex min-w-0 items-center gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm text-stone-600 shadow-sm">
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
                    onChange={setManagementKeywordSearchScopes}
                  />
                </div>
              </label>

              <div className="flex min-w-0 items-center gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm text-stone-600 shadow-sm">
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
                  wrapperClassName="min-w-0 flex-1"
                  shellClassName="!min-h-0 h-8 border-0 bg-stone-50 px-3 py-0 shadow-none"
                  renderOption={(option, { selected, selectOption, closeMenu }) => {
                    const optionKey = option.value as ProfessorManagementSortKey;
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
                  "ui-btn-secondary h-full justify-center whitespace-nowrap",
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
                className="ui-btn-secondary h-full justify-center whitespace-nowrap"
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
                    onToggle={(value) =>
                      toggleFilterValue("universities", value)
                    }
                    onClear={() => updateFilters({ universities: [] })}
                  />
                  <MultiSelectFilter
                    label="学院"
                    allLabel="全部学院"
                    selectedValues={filters.schools}
                    options={[...filterOptions.schools, NO_FIELD_FILTER_VALUE]}
                    optionLabels={noFieldOptionLabels}
                    onToggle={(value) => toggleFilterValue("schools", value)}
                    onClear={() => updateFilters({ schools: [] })}
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
                    onToggle={(value) =>
                      toggleFilterValue("departments", value)
                    }
                    onClear={() => updateFilters({ departments: [] })}
                  />
                  <MultiSelectFilter
                    label="职称 / 导师资格"
                    allLabel="全部职称 / 导师资格"
                    selectedValues={filters.titles}
                    options={[...filterOptions.titles, NO_FIELD_FILTER_VALUE]}
                    optionLabels={noFieldOptionLabels}
                    onToggle={(value) => toggleFilterValue("titles", value)}
                    onClear={() => updateFilters({ titles: [] })}
                  />
                  <MultiSelectFilter
                    label="标签"
                    allLabel="全部标签"
                    selectedValues={selectedTagLabels}
                    options={tagFilterEntries.map((entry) => entry.label)}
                    onToggle={(label) => {
                      const value = tagValueByLabel.get(label);
                      if (value) {
                        toggleFilterValue("tagIds", value);
                      }
                    }}
                    onClear={() => updateFilters({ tagIds: [] })}
                  />
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </section>

      <section className="mt-6 overflow-hidden rounded-[32px] border border-stone-200 bg-white shadow-sm">
        <div className="flex flex-col gap-3 border-b border-stone-100 px-6 py-4">
          <div className="text-sm text-stone-600">
            共 {visibleProfessors.length} 位符合筛选条件，当前第 {safeCurrentPage} / {totalPages} 页，每页最多 {pageSize} 位
          </div>
          {filteredSelectableIds.length > 0 ? (
            <button
              type="button"
              aria-label={
                allFilteredSelected
                  ? "取消选择全部筛选结果"
                  : "选择全部筛选结果"
              }
              aria-pressed={allFilteredSelected}
              onClick={handleToggleFilteredSelection}
              className="inline-flex min-h-10 w-fit items-center gap-2 rounded-2xl border border-stone-200 bg-stone-50 px-3 text-sm font-medium text-stone-700 transition hover:border-primary/40 hover:bg-white hover:text-primary lg:hidden"
            >
              {allFilteredSelected ? (
                <SquareCheck className="h-4 w-4" />
              ) : someFilteredSelected ? (
                <SquareMinus className="h-4 w-4" />
              ) : (
                <Square className="h-4 w-4" />
              )}
              {allFilteredSelected ? "取消选择全部筛选结果" : "选择全部筛选结果"}
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
              aria-label={
                allFilteredSelected
                  ? "取消选择全部筛选结果"
                  : "选择全部筛选结果"
              }
              aria-pressed={allFilteredSelected}
              onClick={handleToggleFilteredSelection}
              disabled={filteredSelectableIds.length === 0}
              className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/40 hover:text-primary disabled:cursor-not-allowed disabled:opacity-45"
              title={
                allFilteredSelected
                  ? "取消选择全部筛选结果"
                  : "选择全部筛选结果"
              }
            >
              {allFilteredSelected ? (
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

        {loading ? (
          <div className="flex items-center justify-center gap-2 px-6 py-16 text-sm text-stone-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在加载导师列表...
          </div>
        ) : visibleProfessors.length === 0 ? (
          <div className="px-6 py-16 text-center">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-3xl bg-stone-100 text-stone-400">
              <Users className="h-6 w-6" />
            </div>
            <h2 className="mt-4 text-xl font-semibold text-stone-900">
              暂无导师
            </h2>
            <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-stone-500">
              选择一种方式建立导师库，后续可继续筛选、编辑和归档。
            </p>
            <div
              data-testid="professor-empty-intake"
              className="mx-auto mt-6 grid max-w-4xl gap-3 text-left lg:grid-cols-3"
            >
              <article
                data-testid="professor-empty-intake-单个新增"
                className="flex min-h-full flex-col justify-between rounded-[28px] border border-stone-200 bg-white p-4 shadow-sm"
              >
                <div>
                  <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-stone-200 bg-stone-100 text-stone-700">
                    <Plus className="h-5 w-5" />
                  </div>
                  <h3 className="mt-3 text-base font-semibold text-stone-900">
                    单个新增
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-stone-500">
                    手动创建一条导师档案，适合临时补充或精修记录。
                  </p>
                </div>
                <button
                  type="button"
                  onClick={openCreateModal}
                  className="ui-btn-primary mt-4 w-full justify-center"
                >
                  <Plus className="h-4 w-4" />
                  新增导师
                </button>
              </article>
              <article
                data-testid="professor-empty-intake-模板导入"
                className="flex min-h-full flex-col justify-between rounded-[28px] border border-amber-200 bg-[linear-gradient(135deg,#fffbeb,#ffffff)] p-4 shadow-sm"
              >
                <div>
                  <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-amber-200 bg-amber-100 text-amber-700">
                    <FileSpreadsheet className="h-5 w-5" />
                  </div>
                  <h3 className="mt-3 text-base font-semibold text-stone-900">
                    模板导入
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-stone-500">
                    下载模板后批量导入导师信息，适合已有名单或表格。
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
                  模板导入
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
                    从学院页面自动发现导师，抓取结果进入候选审核。
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
                  智能抓取
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

        {!loading && visibleProfessors.length > 0 ? (
          <div className="flex flex-col gap-3 border-t border-stone-100 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm text-stone-500">
              共 {visibleProfessors.length} 位符合筛选条件，当前第 {safeCurrentPage} / {totalPages} 页，已选中 {selectedIds.size} 位
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <PageSizeSelector
                value={pageSize}
                onChange={handlePageSizeChange}
              />
              <button
                type="button"
                onClick={() => setCurrentPage(safeCurrentPage - 1)}
                disabled={safeCurrentPage <= 1}
                className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                上一页
              </button>
              <div className="min-w-28 text-center text-sm text-stone-600">
                第 {safeCurrentPage} / {totalPages} 页
              </div>
              <button
                type="button"
                onClick={() => setCurrentPage(safeCurrentPage + 1)}
                disabled={safeCurrentPage >= totalPages}
                className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                下一页
              </button>
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
              <div className="mt-1 text-xs text-stone-500">
                {archiveFilter === "archived"
                  ? "这些导师会被恢复到正常列表，可重新参与筛选与任务。"
                  : "这些导师会被移入回收站，但历史任务和通信不会删除。"}
              </div>
            </div>
            <div className="flex max-w-full flex-wrap gap-3">
              <button
                type="button"
                onClick={() => setSelectedIds(new Set())}
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
            </div>
          </div>
        </div>
      ) : null}

      <ModalShell
        open={upsertModalOpen}
        title={
          editingProfessor ? `编辑导师：${editingProfessor.name}` : "新增导师"
        }
        description="手动维护一位导师的核心信息。保存后会立刻出现在导师管理页，并可在首页参与筛选与建任务。"
        onClose={closeUpsertModal}
        headerAction={
          editingProfessor ? (
            <button
              type="button"
              onClick={() => void handleSingleInformationEnrichment()}
              disabled={
                startingSingleInformationEnrichmentIds.has(editingProfessor.id) ||
                activeInformationEnrichmentStatuses.has(
                  singleInformationEnrichments[editingProfessor.id]?.job.status ?? "",
                )
              }
              className="ui-btn-secondary whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-60"
            >
              {startingSingleInformationEnrichmentIds.has(editingProfessor.id) ||
              activeInformationEnrichmentStatuses.has(
                singleInformationEnrichments[editingProfessor.id]?.job.status ?? "",
              ) ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Bot className="h-4 w-4" />
              )}
              智能补全
            </button>
          ) : null
        }
      >
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <label className="block">
            {renderFieldLabel("姓名", true)}
            <input
              value={formState.name}
              onChange={(event) =>
                setFormState((previous) => ({
                  ...previous,
                  name: event.target.value,
                }))
              }
              className={inputClassName}
              placeholder="示例：张明远"
            />
          </label>
          <label className="block">
            {renderFieldLabel("邮箱", true)}
            <input
              value={formState.email}
              onChange={(event) =>
                setFormState((previous) => ({
                  ...previous,
                  email: event.target.value,
                }))
              }
              className={inputClassName}
              placeholder="示例：faculty@example.edu"
            />
          </label>
          <label className="block">
            {renderFieldLabel("职称")}
            <input
              value={formState.title}
              onChange={(event) =>
                setFormState((previous) => ({
                  ...previous,
                  title: event.target.value,
                }))
              }
              className={inputClassName}
              placeholder="示例：Associate Professor"
            />
          </label>
          <label className="block">
            {renderFieldLabel("学校")}
            <input
              value={formState.university}
              onChange={(event) =>
                setFormState((previous) => ({
                  ...previous,
                  university: event.target.value,
                }))
              }
              className={inputClassName}
              placeholder="示例：Tsinghua University"
            />
          </label>
          <label className="block">
            {renderFieldLabel("学院")}
            <input
              value={formState.school}
              onChange={(event) =>
                setFormState((previous) => ({
                  ...previous,
                  school: event.target.value,
                }))
              }
              className={inputClassName}
              placeholder="示例：School of Computer Science"
            />
          </label>
          <label className="block">
            {renderFieldLabel("系所")}
            <input
              value={formState.department}
              onChange={(event) =>
                setFormState((previous) => ({
                  ...previous,
                  department: event.target.value,
                }))
              }
              className={inputClassName}
              placeholder="示例：Department of AI"
            />
          </label>
          <div className="md:col-span-2">
            <ProfessorTagSelector
              tags={professorTags}
              selectedTagIds={formState.tag_ids}
              disabled={savingProfessor}
              onChange={(tagIds) =>
                setFormState((previous) => ({
                  ...previous,
                  tag_ids: tagIds,
                }))
              }
              onCreateTag={(payload) => void handleCreateProfessorTag(payload)}
              onDeleteTag={(tag) => void handleDeleteProfessorTag(tag)}
            />
          </div>
          <label className="block md:col-span-2">
            {renderFieldLabel("研究方向")}
            <textarea
              value={formState.research_direction}
              onChange={(event) =>
                setFormState((previous) => ({
                  ...previous,
                  research_direction: event.target.value,
                }))
              }
              className="min-h-28 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
              placeholder="示例：Large Language Models, Information Extraction, NLP"
            />
          </label>
          <label className="block md:col-span-2">
            {renderFieldLabel("近期论文")}
            <textarea
              value={formState.recent_papers_text}
              onChange={(event) =>
                setFormState((previous) => ({
                  ...previous,
                  recent_papers_text: event.target.value,
                }))
              }
              className="min-h-32 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
              placeholder={
                "一行一篇，例如：\nScaling Agents with...\nReasoning for Scientific Discovery..."
              }
            />
          </label>
          <label className="block md:col-span-2">
            {renderFieldLabel("个人备注")}
            <textarea
              aria-label="个人备注"
              value={formState.personal_note}
              onChange={(event) =>
                setFormState((previous) => ({
                  ...previous,
                  personal_note: event.target.value,
                }))
              }
              maxLength={10000}
              className="min-h-28 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
              placeholder="只对自己可见的沟通偏好、判断依据或跟进提醒。"
            />
          </label>
          <UrlInputField
            id="professor-profile-url"
            label="主页链接"
            value={formState.profile_url}
            placeholder="示例：https://faculty.example.edu/profile"
            openLabel="打开主页链接"
            onChange={(value) =>
              setFormState((previous) => ({
                ...previous,
                profile_url: value,
              }))
            }
          />
          <UrlInputField
            id="professor-source-url"
            label="来源链接"
            value={formState.source_url}
            placeholder="示例：https://example.edu/faculty-directory"
            openLabel="打开来源链接"
            onChange={(value) =>
              setFormState((previous) => ({
                ...previous,
                source_url: value,
              }))
            }
          />
        </div>
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <button
            type="button"
            onClick={closeUpsertModal}
            className="ui-btn-secondary"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void handleSaveProfessor()}
            disabled={savingProfessor}
            className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {savingProfessor ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : null}
            保存导师
          </button>
        </div>
      </ModalShell>

      <ModalShell
        open={importModalOpen}
        title="导入导师文件"
        description="下载模板并按列填写。导入时按邮箱覆盖记录，回收站记录会自动恢复。"
        onClose={() => {
          if (importingFile) {
            return;
          }
          setImportModalOpen(false);
        }}
      >
        <div className="mt-6 grid gap-6 lg:grid-cols-[0.95fr,1.05fr]">
          <div className="rounded-[28px] border border-stone-200 bg-white p-5 shadow-sm">
            <div className="text-sm font-semibold text-stone-900">
              先下载模板
            </div>
            <p className="mt-2 text-sm leading-6 text-stone-500">
              支持 csv 和 xlsx。下载后按模板里的说明填写即可。
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => handleDownloadTemplate("xlsx")}
                className="ui-btn-primary"
              >
                <FileSpreadsheet className="h-4 w-4" />
                下载 XLSX 模板
              </button>
              <button
                type="button"
                onClick={() => handleDownloadTemplate("csv")}
                className="ui-btn-secondary"
              >
                <Download className="h-4 w-4" />
                下载 CSV 模板
              </button>
            </div>
            <button
              type="button"
              onClick={() =>
                openExternalHttpUrl(MENTOR_CRAWLER_SKILL_GUIDE_URL)
              }
              className="mt-4 inline-flex items-center gap-2 text-left text-sm font-medium text-primary transition hover:text-primary/80"
            >
              <ExternalLink className="h-4 w-4" />
              用 Codex / Claude Code 从导师官网生成导入表
            </button>
            <ul className="mt-5 space-y-2 text-sm leading-6 text-stone-600">
              <li>模板内已包含字段说明和示例行，下载后可直接照着填写。</li>
              <li>
                Skill 默认生成安全的 10 列 XLSX；省略标签和个人备注列时，
                更新已有导师会保留这两项。
              </li>
              <li>说明行和示例行可以保留，导入时会自动忽略。</li>
              <li>导入时如果邮箱相同，会更新表格中包含的导师信息。</li>
              <li>
                <span className="font-mono text-xs">research_direction</span>{" "}
                多个方向用中文分号；分隔。
              </li>
              <li>
                <span className="font-mono text-xs">recent_papers</span>{" "}
                多篇论文用 | 分隔，最多保留前 8 篇。
              </li>
            </ul>
          </div>

          <div className="rounded-[28px] border border-stone-200 bg-white p-5 shadow-sm">
            <div className="text-sm font-semibold text-stone-900">
              上传并导入
            </div>
            <p className="mt-2 text-sm leading-6 text-stone-500">
              必填列是 name 和 email。格式错误的行会跳过；同邮箱记录会覆盖更新。
            </p>
            <label
              onClick={handleImportDropZoneClick}
              onDragOver={(event) => event.preventDefault()}
              onDrop={handleDropImportFile}
              className="mt-4 flex min-h-44 cursor-pointer flex-col items-center justify-center rounded-[28px] border border-dashed border-stone-300 bg-stone-50/70 px-5 text-center transition hover:border-stone-400 hover:bg-white"
            >
              <input
                type="file"
                accept=".csv,.xlsx"
                className="hidden"
                onChange={handleChooseImportFile}
              />
              <Upload className="h-6 w-6 text-stone-400" />
              <div className="mt-3 text-sm font-medium text-stone-800">
                {importFile
                  ? importFile.name
                  : "拖拽 csv/xlsx 到这里，或点击选择文件"}
              </div>
              <div className="mt-2 text-xs text-stone-500">
                {importFile
                  ? `已选 ${Math.round(importFile.size / 1024)} KB`
                  : "支持 UTF-8 CSV 和 Excel 文件"}
              </div>
            </label>

            {importResult ? (
              <div className="mt-4 rounded-3xl border border-emerald-200 bg-emerald-50 px-4 py-4 text-sm text-emerald-800">
                <div className="font-medium">{importResult.message}</div>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  <span className="rounded-full bg-white/80 px-3 py-1">
                    新增 {importResult.inserted_count}
                  </span>
                  <span className="rounded-full bg-white/80 px-3 py-1">
                    更新 {importResult.updated_count}
                  </span>
                  <span className="rounded-full bg-white/80 px-3 py-1">
                    失败 {importResult.failed_count}
                  </span>
                </div>
              </div>
            ) : null}

            <div className="mt-5 flex flex-wrap justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setImportModalOpen(false);
                  setImportResult(null);
                  setImportFile(null);
                }}
                className="ui-btn-secondary"
              >
                关闭
              </button>
              <button
                type="button"
                onClick={() => void handleImportSubmit()}
                disabled={importingFile}
                className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
              >
                {importingFile ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : null}
                开始导入
              </button>
            </div>
          </div>
        </div>
      </ModalShell>

      <ModalShell
        open={exportModalOpen}
        title="导出导师信息"
        description="将全部正常导师导出为表格文件。字段顺序与导入模板保持一致，便于备份、外部整理或修改后再次导入。"
        onClose={() => setExportModalOpen(false)}
      >
        <div className="mt-6 rounded-[28px] border border-stone-200 bg-white p-5 shadow-sm">
          <div className="text-sm font-semibold text-stone-900">
            选择导出格式
          </div>
          <p className="mt-2 text-sm leading-6 text-stone-500">
            推荐使用 XLSX 直接在表格软件中查看；CSV 适合脚本处理和跨工具交换。
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => handleDownloadExport("xlsx")}
              className="ui-btn-primary"
            >
              <FileSpreadsheet className="h-4 w-4" />
              导出 XLSX
            </button>
            <button
              type="button"
              onClick={() => handleDownloadExport("csv")}
              className="ui-btn-secondary"
            >
              <Download className="h-4 w-4" />
              导出 CSV
            </button>
          </div>
          <ul className="mt-5 space-y-2 text-sm leading-6 text-stone-600">
            <li>导出范围：全部正常导师，不包含回收站导师。</li>
            <li>当前搜索、筛选、分页和勾选状态不会影响导出结果。</li>
            <li>字段顺序与导入模板一致，未修改即可重新导入系统。</li>
            <li>导出文件包含个人备注，请谨慎分享。</li>
            <li>空值会保留为空单元格，CSV 使用 UTF-8 编码。</li>
          </ul>
        </div>
      </ModalShell>

      <ModalShell
        open={crawlerModalOpen}
        title="创建抓取任务"
        description="填写学校、学院和页面 URL，系统会创建抓取任务，抓取结果进入候选审核。"
        onClose={closeCrawlerModal}
        maxWidthClassName="max-w-2xl"
      >
        <div className="mt-6 grid gap-4">
          <label className="block">
            {renderFieldLabel("学校", true)}
            <input
              aria-label="学校"
              value={crawlerFormState.university}
              onChange={(event) =>
                setCrawlerFormState((previous) => ({
                  ...previous,
                  university: event.target.value,
                }))
              }
              className={inputClassName}
              placeholder="示例：示例大学"
            />
          </label>
          <label className="block">
            {renderFieldLabel("学院", true)}
            <input
              aria-label="学院"
              value={crawlerFormState.school}
              onChange={(event) =>
                setCrawlerFormState((previous) => ({
                  ...previous,
                  school: event.target.value,
                }))
              }
              className={inputClassName}
              placeholder="示例：计算机学院"
            />
          </label>
          <fieldset className="grid gap-2">
            <legend className="text-sm font-medium text-stone-800">
              入口类型
            </legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {(
                [
                  {
                    value: "list",
                    label: "列表页",
                    hint: "学院教师列表或师资队伍页面",
                  },
                  {
                    value: "profile",
                    label: "详情页",
                    hint: "单个导师个人主页",
                  },
                ] satisfies Array<{
                  value: CrawlJobEntryTypeDTO;
                  label: string;
                  hint: string;
                }>
              ).map((option) => (
                <label
                  key={option.value}
                  className="flex cursor-pointer items-start gap-2 rounded-2xl border border-stone-200 bg-white px-3 py-2.5 text-sm text-stone-700 transition hover:border-primary/50"
                >
                  <input
                    type="radio"
                    name="crawler-entry-type"
                    aria-label={option.label}
                    value={option.value}
                    checked={crawlerFormState.entry_type === option.value}
                    onChange={() =>
                      setCrawlerFormState((previous) => ({
                        ...previous,
                        entry_type: option.value,
                      }))
                    }
                    className="mt-1"
                  />
                  <span>
                    <span className="block font-medium text-stone-900">
                      {option.label}
                    </span>
                    <span className="block text-xs leading-5 text-stone-500">
                      {option.hint}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-2">
            <div className="flex items-center justify-between gap-3">
              {renderFieldLabel("页面 URL", true)}
              <button
                type="button"
                aria-label="添加页面 URL"
                onClick={() =>
                  setCrawlerFormState((previous) => ({
                    ...previous,
                    start_urls: [...previous.start_urls, ""],
                  }))
                }
                className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-600 transition hover:border-primary/50 hover:text-primary"
              >
                <Plus className="h-4 w-4" />
              </button>
            </div>
            <p
              id="crawler-url-hint"
              className="text-xs leading-5 text-stone-500"
            >
              可一次粘贴多个 URL，每行一个，系统会自动拆分。
            </p>
            {crawlerFormState.start_urls.map((url, index) => (
              <div key={index} className="flex items-center gap-2">
                <input
                  aria-label="页面 URL"
                  aria-describedby="crawler-url-hint"
                  ref={(element) => {
                    crawlerUrlInputRefs.current[index] = element;
                  }}
                  value={url}
                  onChange={(event) => {
                    const nextValue = event.target.value;
                    setCrawlerFormState((previous) => ({
                      ...previous,
                      start_urls: previous.start_urls.map((item, itemIndex) =>
                        itemIndex === index ? nextValue : item,
                      ),
                    }));
                  }}
                  onKeyDown={(event) => handleCrawlerUrlKeyDown(event, index)}
                  onPaste={(event) => handleCrawlerUrlPaste(event, index)}
                  className={inputClassName}
                  placeholder={
                    crawlerFormState.entry_type === "profile"
                      ? "示例：https://example.edu/faculty/zhang"
                      : "示例：https://example.edu/faculty"
                  }
                />
                <button
                  type="button"
                  aria-label="移除页面 URL"
                  onClick={() =>
                    setCrawlerFormState((previous) => ({
                      ...previous,
                      start_urls:
                        previous.start_urls.length > 1
                          ? previous.start_urls.filter(
                              (_, itemIndex) => itemIndex !== index,
                            )
                          : [""],
                    }))
                  }
                  disabled={crawlerFormState.start_urls.length === 1}
                  className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-red-200 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-45"
                >
                  <Minus className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        </div>
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <button
            type="button"
            onClick={closeCrawlerModal}
            className="ui-btn-secondary"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void handleCreateCrawlJob()}
            disabled={crawlerSubmitDisabled}
            className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {creatingCrawlJob ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : null}
            开始抓取
          </button>
        </div>
      </ModalShell>

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
