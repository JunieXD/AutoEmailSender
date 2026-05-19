import {
  type ChangeEvent,
  type DragEvent as ReactDragEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import clsx from "clsx";
import {
  Archive,
  Bot,
  Download,
  FileSpreadsheet,
  Loader2,
  Minus,
  Plus,
  RefreshCcw,
  Search,
  Square,
  SquareCheck,
  SquareMinus,
  Upload,
  Users,
} from "lucide-react";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { ManagementProfessorRow } from "@/components/molecules/ManagementProfessorRow";
import { useNotification } from "@/context/NotificationContext";
import { useSelectionContext } from "@/context/SelectionContext";
import { safeRecordUserAction } from "@/lib/diagnosticUserActions";
import {
  extractProfessorTitleTags,
  matchesProfessorTitleTag,
} from "@/lib/professorTitle";
import { useConfirmDialog } from "@/lib/useConfirmDialog";
import { createCrawlJob } from "@/lib/api/crawlJobsApi";
import {
  archiveProfessor,
  bulkArchiveProfessors,
  createProfessor,
  getProfessorTemplateDownloadUrl,
  importProfessorsFromFile,
  listProfessorsForManagement,
  restoreProfessor,
  updateProfessor,
} from "@/lib/api/professorsApi";
import type {
  CrawlJobEntryTypeDTO,
  ProfessorImportFileResultDTO,
  ProfessorManagementItemDTO,
  ProfessorUpsertPayloadDTO,
} from "@/types";

type ArchiveFilter = "active" | "archived" | "all";
type ProfessorFormState = {
  name: string;
  email: string;
  title: string;
  university: string;
  school: string;
  department: string;
  research_direction: string;
  recent_papers_text: string;
  profile_url: string;
  source_url: string;
};
type CrawlerJobFormState = {
  university: string;
  school: string;
  start_urls: string[];
  entry_type: CrawlJobEntryTypeDTO;
};
type IntakeActionTone = "primary" | "amber" | "stone";

const PROFESSORS_PER_PAGE = 10;
const ALL_PROFESSOR_FILTER_VALUE = "__all__";
const managementTableColumns =
  "lg:grid-cols-[2.75rem_minmax(0,0.72fr)_minmax(0,0.74fr)_minmax(0,1.08fr)_minmax(0,1.18fr)_minmax(0,1.56fr)_minmax(0,0.78fr)_minmax(12rem,0.92fr)]";

const archiveFilterLabels: Record<ArchiveFilter, string> = {
  active: "正常",
  archived: "已删除",
  all: "全部",
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
  profile_url: "",
  source_url: "",
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
  profile_url: professor.profile_url ?? "",
  source_url: professor.source_url ?? "",
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
  profile_url: form.profile_url.trim() || null,
  source_url: form.source_url.trim() || null,
});

const fieldLabelClassName =
  "mb-2 inline-flex items-center gap-1 text-sm font-medium text-stone-800";
const filterFieldLabelClassName =
  "h-5 text-sm font-medium leading-5 text-stone-800";
const inputClassName =
  "w-full rounded-2xl border border-stone-200 bg-white px-3 py-2.5 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15";

const renderFieldLabel = (label: string, required = false) => (
  <span className={fieldLabelClassName}>
    {required ? (
      <span className="text-base leading-none text-red-500">*</span>
    ) : null}
    <span>{label}</span>
  </span>
);

const triggerDownload = (url: string) => {
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer";
  document.body.appendChild(link);
  link.click();
  link.remove();
};

const getActionErrorMessage = (error: unknown, fallback: string) =>
  error instanceof Error ? error.message : fallback;

const getSchoolPairValue = (professor: ProfessorManagementItemDTO) =>
  `${professor.university ?? ""}\t${professor.school ?? ""}`;

const getSchoolPairLabel = (professor: ProfessorManagementItemDTO) =>
  [professor.university, professor.school].filter(Boolean).join(" / ");

const intakeActionToneClassNames: Record<IntakeActionTone, string> = {
  primary:
    "border-primary/25 bg-[linear-gradient(135deg,#fff7ed,#fff1f2)] shadow-[0_18px_40px_-28px_rgba(153,27,27,0.55)]",
  amber:
    "border-amber-200 bg-[linear-gradient(135deg,#fffbeb,#ffffff)] shadow-[0_18px_40px_-30px_rgba(180,83,9,0.45)]",
  stone: "border-stone-200 bg-white shadow-sm",
};

const intakeActionIconClassNames: Record<IntakeActionTone, string> = {
  primary:
    "border-primary/15 bg-primary text-white shadow-sm shadow-primary/20",
  amber: "border-amber-200 bg-amber-100 text-amber-700",
  stone: "border-stone-200 bg-stone-100 text-stone-700",
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
      "flex min-h-0 flex-col justify-between gap-3 rounded-[28px] border py-3 px-4 transition hover:-translate-y-0.5 hover:shadow-md sm:flex-row sm:items-center",
      intakeActionToneClassNames[tone],
    )}
  >
    <div className="flex items-center gap-3">
      <div
        className={clsx(
          "flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border",
          intakeActionIconClassNames[tone],
        )}
      >
        {icon}
      </div>
      <div className="min-w-0">
        <h2 className="text-base font-semibold text-stone-900">{label}</h2>
      </div>
    </div>
    <div className="flex flex-wrap gap-2 sm:justify-end">{children}</div>
  </article>
);

const ModalShell = ({
  open,
  title,
  description,
  onClose,
  children,
  maxWidthClassName = "max-w-3xl",
}: {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  maxWidthClassName?: string;
}) => {
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
      onClick={onClose}
    >
      <div
        className={clsx(
          "relative w-full overflow-hidden rounded-[32px] border border-stone-200/80 bg-[linear-gradient(180deg,rgba(255,252,246,0.98),rgba(255,245,233,0.96))] shadow-[0_34px_90px_-32px_rgba(41,37,36,0.5)]",
          maxWidthClassName,
        )}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="absolute inset-x-0 top-0 h-24 bg-[radial-gradient(circle_at_top,rgba(153,27,27,0.15),transparent_70%)]" />
        <div className="relative max-h-[85vh] overflow-y-auto px-6 py-6">
          <div className="max-w-2xl">
            <h2 className="text-2xl font-semibold tracking-[0.01em] text-stone-900">
              {title}
            </h2>
            {description ? (
              <p className="mt-2 text-sm leading-6 text-stone-600">
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

export const ProfessorsPage = () => {
  const { confirm, dialog: confirmDialog } = useConfirmDialog();
  const { selectedLlmProfileId } = useSelectionContext();
  const { notifyError, notifySuccess, notifyWarning } = useNotification();
  const [archiveFilter, setArchiveFilter] = useState<ArchiveFilter>("active");
  const [professors, setProfessors] = useState<ProfessorManagementItemDTO[]>(
    [],
  );
  const [keyword, setKeyword] = useState("");
  const [titleFilter, setTitleFilter] = useState(ALL_PROFESSOR_FILTER_VALUE);
  const [schoolPairFilter, setSchoolPairFilter] = useState(
    ALL_PROFESSOR_FILTER_VALUE,
  );
  const [currentPage, setCurrentPage] = useState(1);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [upsertModalOpen, setUpsertModalOpen] = useState(false);
  const [editingProfessor, setEditingProfessor] =
    useState<ProfessorManagementItemDTO | null>(null);
  const [formState, setFormState] =
    useState<ProfessorFormState>(emptyProfessorForm());
  const [savingProfessor, setSavingProfessor] = useState(false);
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importingFile, setImportingFile] = useState(false);
  const [importResult, setImportResult] =
    useState<ProfessorImportFileResultDTO | null>(null);
  const [crawlerModalOpen, setCrawlerModalOpen] = useState(false);
  const [crawlerFormState, setCrawlerFormState] = useState<CrawlerJobFormState>(
    emptyCrawlerJobForm(),
  );
  const [creatingCrawlJob, setCreatingCrawlJob] = useState(false);
  const loadProfessors = useCallback(
    async (filter: ArchiveFilter = archiveFilter) => {
      setLoading(true);
      try {
        const data = await listProfessorsForManagement(filter);
        setProfessors(data);
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
        const message = getActionErrorMessage(loadError, "加载导师列表失败");
        notifyError("加载导师列表失败", message);
      } finally {
        setLoading(false);
      }
    },
    [archiveFilter, notifyError],
  );

  useEffect(() => {
    void loadProfessors();
  }, [loadProfessors]);

  const titleOptions = useMemo(
    () =>
      Array.from(
        new Set(
          professors
            .flatMap((professor) => extractProfessorTitleTags(professor.title))
            .filter((title): title is string => Boolean(title)),
        ),
      ).sort((first, second) => first.localeCompare(second)),
    [professors],
  );

  const schoolPairOptions = useMemo(() => {
    const pairMap = new Map<string, string>();
    professors.forEach((professor) => {
      const label = getSchoolPairLabel(professor);
      if (!label) {
        return;
      }
      pairMap.set(getSchoolPairValue(professor), label);
    });
    return Array.from(pairMap.entries())
      .map(([value, label]) => ({ value, label }))
      .sort((first, second) => first.label.localeCompare(second.label));
  }, [professors]);

  const hasAdvancedFilters =
    titleFilter !== ALL_PROFESSOR_FILTER_VALUE ||
    schoolPairFilter !== ALL_PROFESSOR_FILTER_VALUE;

  const filteredProfessors = useMemo(() => {
    const query = keyword.trim().toLowerCase();
    return professors.filter((professor) => {
      const textMatched =
        !query ||
        [
          professor.name,
          professor.email,
          professor.university,
          professor.school,
          professor.department,
          professor.research_direction,
        ]
          .filter(Boolean)
          .some((value) => value?.toLowerCase().includes(query));
      const titleMatched =
        titleFilter === ALL_PROFESSOR_FILTER_VALUE ||
        matchesProfessorTitleTag(professor.title, titleFilter);
      const schoolPairMatched =
        schoolPairFilter === ALL_PROFESSOR_FILTER_VALUE ||
        getSchoolPairValue(professor) === schoolPairFilter;

      return textMatched && titleMatched && schoolPairMatched;
    });
  }, [keyword, professors, schoolPairFilter, titleFilter]);

  const resetAdvancedFilters = () => {
    setTitleFilter(ALL_PROFESSOR_FILTER_VALUE);
    setSchoolPairFilter(ALL_PROFESSOR_FILTER_VALUE);
  };

  useEffect(() => {
    setCurrentPage(1);
  }, [archiveFilter, keyword, schoolPairFilter, titleFilter]);

  useEffect(() => {
    if (
      titleFilter !== ALL_PROFESSOR_FILTER_VALUE &&
      !titleOptions.includes(titleFilter)
    ) {
      setTitleFilter(ALL_PROFESSOR_FILTER_VALUE);
    }
  }, [titleFilter, titleOptions]);

  useEffect(() => {
    if (
      schoolPairFilter !== ALL_PROFESSOR_FILTER_VALUE &&
      !schoolPairOptions.some((option) => option.value === schoolPairFilter)
    ) {
      setSchoolPairFilter(ALL_PROFESSOR_FILTER_VALUE);
    }
  }, [schoolPairFilter, schoolPairOptions]);

  const totalPages = Math.max(
    1,
    Math.ceil(filteredProfessors.length / PROFESSORS_PER_PAGE),
  );
  const safeCurrentPage = Math.min(currentPage, totalPages);
  const paginatedProfessors = filteredProfessors.slice(
    (safeCurrentPage - 1) * PROFESSORS_PER_PAGE,
    safeCurrentPage * PROFESSORS_PER_PAGE,
  );
  const isProfessorSelectable = (professor: ProfessorManagementItemDTO) => {
    if (archiveFilter === "archived") {
      return Boolean(professor.archived_at);
    }
    return !professor.archived_at;
  };
  const filteredSelectableIds = filteredProfessors
    .filter(isProfessorSelectable)
    .map((professor) => professor.id);
  const filteredSelectedCount = filteredSelectableIds.filter((id) =>
    selectedIds.has(id),
  ).length;
  const someFilteredSelected = filteredSelectedCount > 0;
  const allFilteredSelected =
    filteredSelectableIds.length > 0 &&
    filteredSelectedCount === filteredSelectableIds.length;
  const openCreateModal = () => {
    setEditingProfessor(null);
    setFormState(emptyProfessorForm());
    setUpsertModalOpen(true);
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

  const handleArchiveProfessor = async (
    professor: ProfessorManagementItemDTO,
  ) => {
    const confirmed = await confirm({
      title: `将“${professor.name}”移入回收站？`,
      description:
        "移入回收站后，这位导师会从首页与正常列表中隐藏，但历史任务和通信会保留。",
      confirmLabel: "确认移入",
      cancelLabel: "先不处理",
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
      cancelLabel: "先不处理",
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
      cancelLabel: "先不处理",
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
      await createCrawlJob(payload);
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
              className="rounded-[30px] border border-stone-200 bg-white/86 p-3 shadow-sm"
            >
              <div className="mb-3 mt-1 pl-1 flex flex-wrap items-end justify-between gap-3">
                <div>
                  <h2
                    id="professor-intake-title"
                    className="text-lg font-semibold text-stone-900"
                  >
                    导师录入方式
                  </h2>
                </div>
              </div>
              <div className="grid gap-3 lg:grid-cols-3">
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
                    className="ui-btn-primary h-10 rounded-2xl px-4"
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
                    className="ui-btn-secondary h-10 rounded-2xl"
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
                    className="ui-btn-secondary h-10 rounded-2xl"
                  >
                    <Plus className="h-4 w-4" />
                    新增导师
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
            <div data-testid="professor-search-row" className="min-w-0">
              <label className="flex min-h-12 w-full min-w-0 items-center gap-3 rounded-3xl border border-stone-200 bg-white px-4 py-3 shadow-sm">
                <Search className="h-4 w-4 shrink-0 text-stone-400" />
                <input
                  value={keyword}
                  onChange={(event) => setKeyword(event.target.value)}
                  placeholder="搜索姓名、邮箱、学校、院系或研究方向"
                  className="w-full min-w-0 bg-transparent text-sm text-stone-700 outline-none placeholder:text-stone-400"
                />
              </label>
            </div>

            <div
              data-testid="professor-filter-row"
              className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between"
            >
              <div className="grid min-w-0 flex-1 items-start gap-3 sm:grid-cols-[minmax(11rem,0.65fr)_minmax(15rem,1fr)_auto]">
                <div className="grid gap-2">
                  <div
                    data-testid="professor-title-filter-label"
                    className={filterFieldLabelClassName}
                  >
                    职称 / 导师资格
                  </div>
                  <NativeSelectField
                    ariaLabel="职称筛选"
                    value={titleFilter}
                    onChange={(event) => setTitleFilter(event.target.value)}
                    shellClassName="min-h-[3.1rem] rounded-3xl bg-white shadow-sm"
                  >
                    <option value={ALL_PROFESSOR_FILTER_VALUE}>
                      全部职称 / 导师资格
                    </option>
                    {titleOptions.map((title) => (
                      <option key={title} value={title}>
                        {title}
                      </option>
                    ))}
                  </NativeSelectField>
                </div>

                <div className="grid gap-2">
                  <div
                    data-testid="professor-school-filter-label"
                    className={filterFieldLabelClassName}
                  >
                    学校 / 学院
                  </div>
                  <NativeSelectField
                    ariaLabel="学校学院筛选"
                    value={schoolPairFilter}
                    onChange={(event) =>
                      setSchoolPairFilter(event.target.value)
                    }
                    shellClassName="min-h-[3.1rem] rounded-3xl bg-white shadow-sm"
                  >
                    <option value={ALL_PROFESSOR_FILTER_VALUE}>
                      全部学校 / 学院
                    </option>
                    {schoolPairOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </NativeSelectField>
                </div>

                <div className="grid gap-2">
                  <div
                    data-testid="professor-reset-filter-label"
                    className={filterFieldLabelClassName}
                  >
                    操作
                  </div>
                  <button
                    type="button"
                    onClick={resetAdvancedFilters}
                    disabled={!hasAdvancedFilters}
                    className="ui-select-shell min-h-[3.1rem] w-full justify-center rounded-3xl bg-white font-medium shadow-sm disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <RefreshCcw className="h-4 w-4" />
                    重置筛选
                  </button>
                </div>
              </div>

              <div className="grid gap-2 lg:justify-end">
                <div
                  aria-hidden="true"
                  data-testid="professor-toolbar-spacer"
                  className={filterFieldLabelClassName}
                />
                <div className="flex flex-wrap items-center gap-3 lg:justify-end">
                  <button
                    type="button"
                    onClick={() => void loadProfessors()}
                    className="ui-btn-secondary h-10 rounded-2xl"
                  >
                    <RefreshCcw className="h-4 w-4" />
                    刷新
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="mt-6 overflow-hidden rounded-[32px] border border-stone-200 bg-white shadow-sm">
        <div className="flex flex-col gap-3 border-b border-stone-100 px-6 py-4">
          <div className="text-sm text-stone-600">
            共 {filteredProfessors.length} 位符合筛选条件，当前第 {safeCurrentPage} / {totalPages} 页，每页最多 {PROFESSORS_PER_PAGE} 位
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
            "hidden gap-4 border-b border-stone-100 px-6 py-4 text-xs font-medium uppercase tracking-[0.16em] text-stone-400 lg:grid",
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
        ) : filteredProfessors.length === 0 ? (
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
                />
              );
            })}
          </div>
        )}

        {!loading && filteredProfessors.length > 0 ? (
          <div className="flex flex-col gap-3 border-t border-stone-100 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm text-stone-500">
              共 {filteredProfessors.length} 位符合筛选条件，当前第 {safeCurrentPage} / {totalPages} 页，已选中 {selectedIds.size} 位
            </div>
            <div className="flex items-center gap-2">
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
        <div className="sticky bottom-4 z-20 mt-6">
          <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-3 rounded-[28px] border border-stone-200 bg-white/95 px-5 py-4 shadow-[0_18px_34px_-24px_rgba(41,37,36,0.36)] backdrop-blur-xl">
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
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => setSelectedIds(new Set())}
                className="ui-btn-secondary"
              >
                清空选择
              </button>
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
            {renderFieldLabel("院系")}
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
          <label className="block">
            {renderFieldLabel("主页链接")}
            <input
              value={formState.profile_url}
              onChange={(event) =>
                setFormState((previous) => ({
                  ...previous,
                  profile_url: event.target.value,
                }))
              }
              className={inputClassName}
              placeholder="示例：https://faculty.example.edu/profile"
            />
          </label>
          <label className="block">
            {renderFieldLabel("来源链接")}
            <input
              value={formState.source_url}
              onChange={(event) =>
                setFormState((previous) => ({
                  ...previous,
                  source_url: event.target.value,
                }))
              }
              className={inputClassName}
              placeholder="示例：https://example.edu/faculty-directory"
            />
          </label>
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
            <ul className="mt-5 space-y-2 text-sm leading-6 text-stone-600">
              <li>模板内已包含字段说明和示例行，下载后可直接照着填写。</li>
              <li>更推荐自己写爬虫脚本先获取导师信息，再批量导入。</li>
              <li>说明行和示例行可以保留，导入时会自动忽略。</li>
              <li>导入时如果邮箱相同，会覆盖整条导师信息。</li>
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
            {crawlerFormState.start_urls.map((url, index) => (
              <div key={index} className="flex items-center gap-2">
                <input
                  aria-label="页面 URL"
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

      {confirmDialog}
    </main>
  );
};
