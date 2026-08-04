import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import clsx from 'clsx';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  Building2,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Database,
  ExternalLink,
  FileSpreadsheet,
  FileWarning,
  Inbox,
  Loader2,
  Mail,
  RefreshCcw,
  Search,
  Users,
  X,
} from 'lucide-react';
import { useNotification } from '@/context/NotificationContext';
import {
  importCommunityMentors,
  listCommunityMentors,
  previewCommunityMentorImport,
} from '@/lib/api/communityMentorsApi';
import {
  getCommunityMentorCatalogSessionSnapshot,
  requestCommunityMentorCatalog,
  shouldAutomaticallyRefreshCommunityMentorCatalog,
} from '@/lib/communityMentorCatalogCache';
import { buildCommunityReportUrl } from '@/lib/communityMentorLinks';
import { openExternalHttpUrl } from '@/lib/externalUrls';
import {
  addVisibleRecordSelection,
  getVisibleRecordSelectionState,
  MAX_LOADED_COMMUNITY_MENTORS,
  MAX_SELECTED_COMMUNITY_MENTORS,
} from '@/lib/communityMentorSelection';
import type {
  CommunityCatalogDTO,
  CommunityComparisonCategoryDTO,
  CommunityFieldChoiceDTO,
  CommunityFieldComparisonDTO,
  CommunityFieldStateDTO,
  CommunityMentorComparisonDTO,
  CommunityMentorRecordDTO,
  CommunityMentorStatusDTO,
  CommunityRecordsDTO,
} from '@/types';


const MAX_SELECTED_UNITS = 20;
const MAX_SELECTED_RECORDS = MAX_SELECTED_COMMUNITY_MENTORS;
const MAX_LOADED_RECORDS = MAX_LOADED_COMMUNITY_MENTORS;
const RECORDS_PER_PAGE = 100;

const haveSamePaths = (left: string[], right: string[]) =>
  left.length === right.length && left.every((path) => right.includes(path));

const isRecordSelectable = (item: CommunityMentorComparisonDTO) =>
  item.category !== 'retired_or_revoked' && !item.import_blocked;

const categoryMeta: Record<
  CommunityComparisonCategoryDTO,
  { label: string; className: string; description: string }
> = {
  new: {
    label: '可新增',
    className: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    description: '本地尚无匹配导师，可直接新增。',
  },
  linked_unchanged: {
    label: '已同步',
    className: 'border-stone-200 bg-stone-50 text-stone-600',
    description: '本地与社区当前值一致。',
  },
  fill_available: {
    label: '可补全',
    className: 'border-sky-200 bg-sky-50 text-sky-700',
    description: '社区可以补全本地空字段。',
  },
  local_modified: {
    label: '本地已修改',
    className: 'border-violet-200 bg-violet-50 text-violet-700',
    description: '本地保留了自己的修改，默认不会覆盖。',
  },
  remote_modified: {
    label: '社区有更新',
    className: 'border-blue-200 bg-blue-50 text-blue-700',
    description: '社区值在上次导入后发生变化。',
  },
  conflict: {
    label: '需要选择',
    className: 'border-amber-200 bg-amber-50 text-amber-800',
    description: '本地与社区值不同，请在预览中逐项决定。',
  },
  archived_local: {
    label: '本地已归档',
    className: 'border-orange-200 bg-orange-50 text-orange-700',
    description: '导入不会自动恢复本地回收站记录。',
  },
  retired_or_revoked: {
    label: '已退出或撤销',
    className: 'border-red-200 bg-red-50 text-red-700',
    description: '只提供生命周期提醒，不会静默删除本地记录。',
  },
};

const fieldStateMeta: Record<CommunityFieldStateDTO, { label: string; className: string }> = {
  new: { label: '新字段', className: 'text-emerald-700' },
  same: { label: '一致', className: 'text-stone-500' },
  fill_available: { label: '可补全', className: 'text-sky-700' },
  local_only: { label: '仅本地', className: 'text-violet-700' },
  local_modified: { label: '本地已改', className: 'text-violet-700' },
  remote_modified: { label: '社区已改', className: 'text-blue-700' },
  conflict: { label: '冲突', className: 'text-amber-700' },
};

const lifecycleLabels: Record<CommunityMentorStatusDTO, string> = {
  active: '在职',
  retired: '已退休',
  departed: '已离职或调动',
  deceased: '已去世',
  stale: '信息过时',
  disputed: '信息有争议',
  removed: '已撤销',
};

const getErrorMessage = (error: unknown, fallback: string) =>
  error instanceof Error && error.message ? error.message : fallback;

const formatDate = (value: string | null | undefined) => {
  if (!value) {
    return '未记录';
  }
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN');
};

const formatFieldValue = (value: unknown) => {
  if (value === null || value === undefined || value === '') {
    return '（空）';
  }
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join('\n') : '（空）';
  }
  return String(value);
};

const recordSearchText = (item: CommunityMentorComparisonDTO) =>
  [
    item.record.name,
    item.record.email,
    item.record.title,
    item.record.university,
    item.record.school,
    item.record.department,
    item.record.research_direction,
    ...item.record.contacts.map((contact) => contact.email),
    ...item.record.affiliations.flatMap((affiliation) => [
      affiliation.title,
      affiliation.university,
      affiliation.school,
      affiliation.department,
    ]),
  ]
    .filter(Boolean)
    .join('\n')
    .toLocaleLowerCase();

const openFeedbackForm = (
  record: CommunityMentorRecordDTO,
  notifySuccess: (title: string, description?: string) => unknown,
) => {
  openExternalHttpUrl(buildCommunityReportUrl(record));
  notifySuccess(
    '反馈页面已打开',
    '导师和当前信息已自动填写，请选择问题并补充正确内容和新的官网证据。',
  );
};

const DifferenceField = ({
  field,
  choice,
  allowLocalChoice,
  onChange,
}: {
  field: CommunityFieldComparisonDTO;
  choice: CommunityFieldChoiceDTO;
  allowLocalChoice: boolean;
  onChange: (choice: CommunityFieldChoiceDTO) => void;
}) => {
  const allowCommunityChoice = field.state !== 'local_only';
  return (
    <div className="rounded-2xl border border-stone-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-semibold text-stone-900">{field.label}</div>
        <div className={clsx('text-xs font-medium', fieldStateMeta[field.state].className)}>
          {fieldStateMeta[field.state].label}
        </div>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <button
          type="button"
          disabled={!allowLocalChoice}
          onClick={() => onChange('local')}
          className={clsx(
            'rounded-xl border p-3 text-left transition',
            choice === 'local'
              ? 'border-violet-300 bg-violet-50 ring-2 ring-violet-100'
              : 'border-stone-200 bg-stone-50',
            !allowLocalChoice && 'cursor-not-allowed opacity-50',
          )}
        >
          <span className="text-xs font-semibold text-stone-500">保留本地</span>
          <span className="mt-1 block whitespace-pre-wrap break-words text-sm text-stone-800">
            {formatFieldValue(field.local_value)}
          </span>
        </button>
        <button
          type="button"
          aria-label={`采用社区${field.label}`}
          disabled={!allowCommunityChoice}
          onClick={() => onChange('community')}
          className={clsx(
            'rounded-xl border p-3 text-left transition',
            choice === 'community'
              ? 'border-primary/40 bg-primary/5 ring-2 ring-primary/10'
              : 'border-stone-200 bg-stone-50',
            !allowCommunityChoice && 'cursor-not-allowed opacity-50',
          )}
        >
          <span className="text-xs font-semibold text-primary">采用社区</span>
          <span className="mt-1 block whitespace-pre-wrap break-words text-sm text-stone-800">
            {formatFieldValue(field.community_value)}
          </span>
          {!allowCommunityChoice ? (
            <span className="mt-2 block text-xs text-stone-500">社区没有内容，不能用空值清掉本地资料。</span>
          ) : null}
        </button>
      </div>
    </div>
  );
};

export const CommunityMentorsPage = () => {
  const { notifyError, notifySuccess, notifyWarning } = useNotification();
  const [catalog, setCatalog] = useState<CommunityCatalogDTO | null>(
    getCommunityMentorCatalogSessionSnapshot,
  );
  const [catalogLoading, setCatalogLoading] = useState(
    () => getCommunityMentorCatalogSessionSnapshot() === null,
  );
  const [catalogRefreshing, setCatalogRefreshing] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogKeyword, setCatalogKeyword] = useState('');
  const [selectedUnitPaths, setSelectedUnitPaths] = useState<string[]>([]);
  const [loadedUnitPaths, setLoadedUnitPaths] = useState<string[] | null>(null);
  const [recordsPayload, setRecordsPayload] = useState<CommunityRecordsDTO | null>(null);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordKeyword, setRecordKeyword] = useState('');
  const [categoryFilter, setCategoryFilter] = useState<CommunityComparisonCategoryDTO | 'all'>('all');
  const [recordPage, setRecordPage] = useState(1);
  const [selectedRecordIds, setSelectedRecordIds] = useState<string[]>([]);
  const [previewPayload, setPreviewPayload] = useState<CommunityRecordsDTO | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [fieldChoices, setFieldChoices] = useState<
    Record<string, Record<string, CommunityFieldChoiceDTO>>
  >({});
  const [identityConfirmations, setIdentityConfirmations] = useState<Record<string, boolean>>({});
  const selectVisibleCheckboxRef = useRef<HTMLInputElement>(null);

  const loadCatalog = useCallback(
    async (refresh: boolean, announceResult = refresh) => {
      if (refresh) {
        setCatalogRefreshing(true);
      }
      if (!getCommunityMentorCatalogSessionSnapshot()) {
        setCatalogLoading(true);
      }
      setCatalogError(null);
      try {
        const nextCatalog = await requestCommunityMentorCatalog(refresh);
        setCatalog((previous) => {
          if (previous && previous.dataset_version !== nextCatalog.dataset_version) {
            setSelectedUnitPaths([]);
            setLoadedUnitPaths(null);
            setRecordsPayload(null);
            setSelectedRecordIds([]);
            setPreviewPayload(null);
            setRecordPage(1);
          }
          return nextCatalog;
        });
        if (nextCatalog.warning && announceResult) {
          notifyWarning('正在使用社区缓存', nextCatalog.warning);
        } else if (announceResult) {
          notifySuccess('社区目录已刷新', `当前共有 ${nextCatalog.record_count} 位导师。`);
        }
        return nextCatalog;
      } catch (error) {
        const message = getErrorMessage(error, '社区导师库暂时无法读取');
        setCatalogError(message);
        if (announceResult) {
          notifyError('刷新社区目录失败', message);
        }
        return null;
      } finally {
        setCatalogLoading(false);
        setCatalogRefreshing(false);
      }
    },
    [notifyError, notifySuccess, notifyWarning],
  );

  useEffect(() => {
    const bootstrapCatalog = async () => {
      const initialCatalog = getCommunityMentorCatalogSessionSnapshot()
        ?? await loadCatalog(false, false);
      if (!initialCatalog || !shouldAutomaticallyRefreshCommunityMentorCatalog(initialCatalog)) {
        return;
      }
      void loadCatalog(true, false);
    };

    void bootstrapCatalog();
  }, [loadCatalog]);

  const filteredUniversities = useMemo(() => {
    if (!catalog) {
      return [];
    }
    const keyword = catalogKeyword.trim().toLocaleLowerCase();
    if (!keyword) {
      return catalog.universities;
    }
    return catalog.universities
      .map((university) => ({
        ...university,
        units: university.units.filter(
          (unit) =>
            university.name.toLocaleLowerCase().includes(keyword) ||
            unit.name.toLocaleLowerCase().includes(keyword),
        ),
      }))
      .filter((university) => university.units.length > 0);
  }, [catalog, catalogKeyword]);

  const catalogUnitsByPath = useMemo(
    () => new Map(
      (catalog?.universities ?? []).flatMap((university) =>
        university.units.map((unit) => [unit.path, unit] as const),
      ),
    ),
    [catalog],
  );
  const selectedUnitRecordCount = selectedUnitPaths.reduce(
    (total, path) => total + (catalogUnitsByPath.get(path)?.record_count ?? 0),
    0,
  );

  const loadRecordsForPaths = async (unitPaths: string[]) => {
    if (!catalog || unitPaths.length === 0) {
      return;
    }
    const requestedUnitPaths = [...unitPaths];
    const requestedRecordCount = requestedUnitPaths.reduce(
      (total, path) => total + (catalogUnitsByPath.get(path)?.record_count ?? 0),
      0,
    );
    if (requestedRecordCount > MAX_LOADED_RECORDS) {
      notifyWarning(
        '所选导师太多',
        `当前学院共有 ${requestedRecordCount} 位导师，一次最多加载 ${MAX_LOADED_RECORDS} 位，请分批处理。`,
      );
      return;
    }
    setRecordsLoading(true);
    try {
      const result = await listCommunityMentors({
        dataset_version: catalog.dataset_version,
        unit_paths: requestedUnitPaths,
      });
      setRecordsPayload(result);
      setLoadedUnitPaths(requestedUnitPaths);
      setSelectedRecordIds([]);
      setPreviewPayload(null);
      setRecordPage(1);
      if (result.warning) {
        notifyWarning('学院数据来自缓存', result.warning);
      }
    } catch (error) {
      notifyError('加载导师数据失败', getErrorMessage(error, '无法加载所选学院的导师数据'));
    } finally {
      setRecordsLoading(false);
    }
  };

  const toggleUnit = (path: string) => {
    setSelectedUnitPaths((current) => {
      if (current.includes(path)) {
        return current.filter((item) => item !== path);
      }
      if (current.length >= MAX_SELECTED_UNITS) {
        notifyWarning('学院选择过多', `一次最多加载 ${MAX_SELECTED_UNITS} 个学院。`);
        return current;
      }
      const unit = catalogUnitsByPath.get(path);
      const currentRecordCount = current.reduce(
        (total, item) => total + (catalogUnitsByPath.get(item)?.record_count ?? 0),
        0,
      );
      const nextRecordCount = currentRecordCount + (unit?.record_count ?? 0);
      if (nextRecordCount > MAX_LOADED_RECORDS) {
        notifyWarning(
          '所选导师太多',
          `加入该学院后共有 ${nextRecordCount} 位导师，一次最多加载 ${MAX_LOADED_RECORDS} 位，请分批处理。`,
        );
        return current;
      }
      return [...current, path];
    });
  };

  const visibleRecords = useMemo(() => {
    const keyword = recordKeyword.trim().toLocaleLowerCase();
    return (recordsPayload?.records ?? []).filter((item) => {
      if (categoryFilter !== 'all' && item.category !== categoryFilter) {
        return false;
      }
      return !keyword || recordSearchText(item).includes(keyword);
    });
  }, [categoryFilter, recordKeyword, recordsPayload]);

  const selectableVisibleRecords = useMemo(
    () => visibleRecords.filter(isRecordSelectable),
    [visibleRecords],
  );
  const selectableVisibleIds = useMemo(
    () => selectableVisibleRecords.map((item) => item.record.id),
    [selectableVisibleRecords],
  );
  const selectedRecordIdSet = useMemo(
    () => new Set(selectedRecordIds),
    [selectedRecordIds],
  );
  const {
    selectedVisibleCount,
    allVisibleSelected,
    partiallyVisibleSelected,
  } = getVisibleRecordSelectionState(selectedRecordIds, selectableVisibleIds);
  const totalRecordPages = Math.max(
    1,
    Math.ceil(visibleRecords.length / RECORDS_PER_PAGE),
  );
  const currentRecordPage = Math.min(recordPage, totalRecordPages);
  const paginatedVisibleRecords = visibleRecords.slice(
    (currentRecordPage - 1) * RECORDS_PER_PAGE,
    currentRecordPage * RECORDS_PER_PAGE,
  );
  const recordsSelectionStale = Boolean(
    recordsPayload &&
      loadedUnitPaths &&
      !haveSamePaths(selectedUnitPaths, loadedUnitPaths),
  );

  useEffect(() => {
    if (selectVisibleCheckboxRef.current) {
      selectVisibleCheckboxRef.current.indeterminate = partiallyVisibleSelected;
    }
  }, [partiallyVisibleSelected, selectableVisibleIds, selectedRecordIds]);

  const toggleRecord = (recordId: string) => {
    setSelectedRecordIds((current) => {
      if (current.includes(recordId)) {
        return current.filter((item) => item !== recordId);
      }
      if (current.length >= MAX_SELECTED_RECORDS) {
        notifyWarning(
          '已达到导入上限',
          `一次最多选择 ${MAX_SELECTED_RECORDS} 位导师，请先导入当前选择。`,
        );
        return current;
      }
      return [...current, recordId];
    });
  };

  const toggleVisibleRecords = () => {
    setSelectedRecordIds((current) => {
      if (allVisibleSelected) {
        return current.filter((id) => !selectableVisibleIds.includes(id));
      }
      const { recordIds, omittedCount } = addVisibleRecordSelection(
        current,
        selectableVisibleIds,
      );
      if (omittedCount > 0) {
        notifyWarning(
          '已选择前 500 位导师',
          `还有 ${omittedCount} 位未选中；一次最多导入 ${MAX_SELECTED_RECORDS} 位，请分批处理。`,
        );
      }
      return recordIds;
    });
  };

  const clearVisibleRecords = () => {
    const visibleIdSet = new Set(selectableVisibleIds);
    setSelectedRecordIds((current) => current.filter((id) => !visibleIdSet.has(id)));
  };

  const openPreview = async () => {
    if (
      !catalog ||
      !loadedUnitPaths ||
      selectedRecordIds.length === 0 ||
      recordsSelectionStale
    ) {
      if (recordsSelectionStale) {
        notifyWarning('请先重新加载导师列表', '学院选择已改变，当前列表仍是上一次加载的结果。');
      }
      return;
    }
    const recordIds = selectedRecordIds.slice(0, MAX_SELECTED_RECORDS);
    setPreviewLoading(true);
    try {
      const result = await previewCommunityMentorImport({
        dataset_version: catalog.dataset_version,
        unit_paths: loadedUnitPaths,
        record_ids: recordIds,
      });
      const refreshedById = new Map(
        result.records.map((item) => [item.record.id, item]),
      );
      setRecordsPayload((current) =>
        current
          ? {
              ...current,
              records: current.records.map(
                (item) => refreshedById.get(item.record.id) ?? item,
              ),
            }
          : current,
      );
      const blockedRecords = result.records.filter((item) => !isRecordSelectable(item));
      if (blockedRecords.length > 0) {
        const blockedIds = new Set(blockedRecords.map((item) => item.record.id));
        setSelectedRecordIds((current) => current.filter((id) => !blockedIds.has(id)));
        const firstBlocked = blockedRecords[0];
        notifyWarning(
          '导师状态刚刚发生变化',
          `“${firstBlocked.record.name}”${firstBlocked.import_blocked_reason ? `：${firstBlocked.import_blocked_reason}` : '暂不可导入'}。已从选择中移除，请重新预览。`,
        );
        return;
      }
      const nextChoices: Record<string, Record<string, CommunityFieldChoiceDTO>> = {};
      const nextConfirmations: Record<string, boolean> = {};
      result.records.forEach((comparison) => {
        nextChoices[comparison.record.id] = Object.fromEntries(
          comparison.fields.map((field) => [field.field, field.suggested_choice]),
        );
        nextConfirmations[comparison.record.id] = !comparison.identity_conflict;
      });
      setFieldChoices(nextChoices);
      setIdentityConfirmations(nextConfirmations);
      setPreviewPayload(result);
    } catch (error) {
      notifyError('生成导入预览失败', getErrorMessage(error, '无法生成导入预览'));
    } finally {
      setPreviewLoading(false);
    }
  };

  const submitImport = async () => {
    if (!catalog || !previewPayload || !loadedUnitPaths) {
      return;
    }
    if (recordsSelectionStale) {
      notifyWarning('请先重新加载导师列表', '学院选择已改变，不能用旧列表继续导入。');
      return;
    }
    const blocked = previewPayload.records.find((item) => !isRecordSelectable(item));
    if (blocked) {
      notifyWarning(
        '存在暂不可导入的导师',
        blocked.import_blocked_reason ?? `“${blocked.record.name}”当前不能导入。`,
      );
      return;
    }
    const unconfirmed = previewPayload.records.find(
      (item) => item.identity_conflict && !identityConfirmations[item.record.id],
    );
    if (unconfirmed) {
      notifyWarning('请确认导师身份', `“${unconfirmed.record.name}”存在姓名或学校冲突。`);
      return;
    }
    setImporting(true);
    try {
      const result = await importCommunityMentors({
        dataset_version: previewPayload.dataset_version,
        unit_paths: loadedUnitPaths,
        items: previewPayload.records.map((item) => ({
          community_record_id: item.record.id,
          comparison_token: item.comparison_token,
          field_choices: fieldChoices[item.record.id] ?? {},
          confirm_identity_match: identityConfirmations[item.record.id] ?? false,
        })),
      });
      notifySuccess('社区导师已导入', result.message);
      setPreviewPayload(null);
      setSelectedRecordIds([]);
      await loadRecordsForPaths(loadedUnitPaths);
    } catch (error) {
      notifyError('社区导入失败', getErrorMessage(error, '社区导师导入失败'));
    } finally {
      setImporting(false);
    }
  };

  return (
    <main className="mx-auto w-full max-w-7xl px-6 py-8">
      <section className="overflow-hidden rounded-[32px] border border-orange-100 bg-[radial-gradient(circle_at_top_right,rgba(251,146,60,0.18),transparent_34%),linear-gradient(135deg,#fffaf3,#ffffff)] p-6 shadow-sm md:p-8">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight text-stone-950">社区导师库</h1>
            <p className="mt-2 text-sm text-stone-600">按学校和学院查找导师，预览后导入本地。</p>
            {catalog && catalog.record_count > 0 ? (
              <p className="mt-3 text-xs font-medium text-orange-700">已收录 {catalog.record_count} 位导师</p>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-3">
            <Link
              to="/professors?community_contribution=batch"
              className="ui-btn-primary"
            >
              <FileSpreadsheet className="h-4 w-4" />
              批量贡献学校/学院
            </Link>
            <button
              type="button"
              aria-label="刷新社区目录"
              disabled={catalogRefreshing}
              onClick={() => void loadCatalog(true)}
              className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCcw className={clsx('h-4 w-4', catalogRefreshing && 'animate-spin')} />
              刷新
            </button>
          </div>
        </div>
      </section>

      {catalog?.warning ? (
        <section className="mt-6 rounded-[24px] border border-amber-200 bg-amber-50 px-5 py-4 text-amber-900">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
            <div>
              <h2 className="font-semibold">当前显示的是上次验证成功的数据</h2>
              <p className="mt-1 text-sm leading-6 text-amber-800">{catalog.warning}</p>
              <p className="mt-1 text-xs text-amber-700">你仍可浏览和导入；恢复联网后点击“刷新社区目录”即可获取最新版本。</p>
            </div>
          </div>
        </section>
      ) : null}

      {(catalog?.lifecycle_warnings.length ?? 0) > 0 ? (
        <section className="mt-6 rounded-[28px] border border-red-200 bg-red-50 p-5">
          <div className="flex items-start gap-3">
            <FileWarning className="mt-0.5 h-5 w-5 shrink-0 text-red-600" />
            <div className="min-w-0 flex-1">
              <h2 className="font-semibold text-red-900">已导入导师有生命周期变化</h2>
              <p className="mt-1 text-sm leading-6 text-red-700">
                社区不会静默删除或恢复你的本地导师。请查看证据后自行决定是否归档或修改。
              </p>
              <div className="mt-3 grid gap-2">
                {catalog?.lifecycle_warnings.map((warning) => (
                  <div key={warning.community_record_id} className="flex flex-wrap items-center gap-2 rounded-xl bg-white/70 px-3 py-2 text-sm text-red-900">
                    <Link className="font-semibold underline decoration-red-300 underline-offset-2" to={`/professors?keyword=${encodeURIComponent(warning.professor_name)}`}>
                      {warning.professor_name}
                    </Link>
                    <span>· {lifecycleLabels[warning.status]}</span>
                    {warning.reason ? <span className="text-red-700">· {warning.reason}</span> : null}
                    {warning.source_url ? (
                      <button type="button" className="ml-auto inline-flex items-center gap-1 text-red-700 underline" onClick={() => openExternalHttpUrl(warning.source_url!)}>
                        查看证据 <ExternalLink className="h-3.5 w-3.5" />
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>
      ) : null}

      {catalogLoading && !catalog ? (
        <div className="mt-8 flex min-h-64 items-center justify-center rounded-[28px] border border-stone-200 bg-white">
          <Loader2 className="h-6 w-6 animate-spin text-primary" />
          <span className="ml-3 text-sm text-stone-600">正在加载社区导师库…</span>
        </div>
      ) : catalogError && !catalog ? (
        <div className="mt-8 rounded-[28px] border border-red-200 bg-red-50 p-8 text-center">
          <AlertTriangle className="mx-auto h-8 w-8 text-red-500" />
          <h2 className="mt-3 font-semibold text-red-900">社区导师库暂时无法读取</h2>
          <p className="mt-2 text-sm text-red-700">{catalogError}</p>
          <button type="button" className="ui-btn-secondary mt-5" onClick={() => void loadCatalog(true)}>
            再试一次
          </button>
        </div>
      ) : catalog && catalog.universities.length === 0 ? (
        <div className="mt-8 rounded-[32px] border border-dashed border-orange-200 bg-white p-10 text-center">
          <Inbox className="mx-auto h-10 w-10 text-orange-400" />
          <h2 className="mt-4 text-xl font-semibold text-stone-900">还没有导师数据</h2>
          <p className="mt-2 text-sm text-stone-600">欢迎贡献第一个学校或学院。</p>
          <Link
            to="/professors?community_contribution=batch"
            className="ui-btn-primary mt-6"
          >
            <FileSpreadsheet className="h-4 w-4" />
            批量贡献第一所学校/学院
          </Link>
        </div>
      ) : catalog ? (
        <div className="mt-8 grid gap-6 lg:grid-cols-[21rem,minmax(0,1fr)]">
          <aside className="self-start rounded-[28px] border border-stone-200 bg-white p-5 shadow-sm lg:sticky lg:top-44">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-stone-900">选择学校与学院</h2>
                <p className="mt-1 text-xs text-stone-500">
                  已选 {selectedUnitPaths.length}/{MAX_SELECTED_UNITS} 个学院 · {selectedUnitRecordCount}/{MAX_LOADED_RECORDS} 位
                </p>
              </div>
              <Building2 className="h-5 w-5 text-primary" />
            </div>
            <div className="relative mt-4">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
              <input value={catalogKeyword} onChange={(event) => setCatalogKeyword(event.target.value)} className="w-full rounded-xl border border-stone-200 py-2 pl-9 pr-3 text-sm outline-none focus:border-primary" placeholder="搜索学校或学院" />
            </div>
            <div className="mt-4 max-h-[34rem] space-y-3 overflow-y-auto pr-1">
              {filteredUniversities.map((university) => (
                <div key={university.id} className="rounded-2xl border border-stone-200 p-3">
                  <div className="flex items-center justify-between gap-2 text-sm font-semibold text-stone-900">
                    <span>{university.name}</span>
                    <span className="rounded-full bg-stone-100 px-2 py-0.5 text-xs text-stone-500">{university.record_count}</span>
                  </div>
                  <div className="mt-2 space-y-1.5">
                    {university.units.map((unit) => (
                      <label key={unit.path} className="flex cursor-pointer items-start gap-2 rounded-xl px-2 py-2 text-sm text-stone-700 transition hover:bg-orange-50">
                        <input type="checkbox" className="mt-0.5 h-4 w-4 accent-orange-600" checked={selectedUnitPaths.includes(unit.path)} onChange={() => toggleUnit(unit.path)} />
                        <span className="min-w-0 flex-1">
                          <span className="block break-words">{unit.name}</span>
                          <span className="text-xs text-stone-400">{unit.record_count} 位</span>
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <button type="button" disabled={recordsLoading || selectedUnitPaths.length === 0} onClick={() => void loadRecordsForPaths(selectedUnitPaths)} className="ui-btn-primary mt-5 w-full justify-center disabled:cursor-not-allowed disabled:opacity-50">
              {recordsLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
              加载所选学院
            </button>
          </aside>

          <section className="min-w-0 rounded-[28px] border border-stone-200 bg-white p-5 shadow-sm md:p-6">
            {!recordsPayload ? (
              <div className="flex min-h-[28rem] flex-col items-center justify-center text-center">
                <Users className="h-10 w-10 text-stone-300" />
                <h2 className="mt-4 font-semibold text-stone-900">先从左侧选择学院</h2>
                <p className="mt-2 max-w-md text-sm leading-6 text-stone-500">只会下载你选择的学院分片，不会把整个社区库作为一个超大文件塞进本地。</p>
              </div>
            ) : (
              <>
                <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
                  <div>
                    <h2 className="text-xl font-semibold text-stone-900">导师列表</h2>
                    <p className="mt-1 text-sm text-stone-500">已加载 {recordsPayload.records.length} 位，已选择 {selectedRecordIds.length}/{MAX_SELECTED_RECORDS}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <div className="relative min-w-56 flex-1">
                      <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
                      <input value={recordKeyword} onChange={(event) => { setRecordKeyword(event.target.value); setRecordPage(1); }} className="w-full rounded-xl border border-stone-200 py-2 pl-9 pr-3 text-sm outline-none focus:border-primary" placeholder="姓名、全部邮箱、任职、方向" />
                    </div>
                    <select value={categoryFilter} onChange={(event) => { setCategoryFilter(event.target.value as CommunityComparisonCategoryDTO | 'all'); setRecordPage(1); }} className="rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 outline-none focus:border-primary">
                      <option value="all">全部状态</option>
                      {Object.entries(categoryMeta).map(([value, meta]) => <option key={value} value={value}>{meta.label}</option>)}
                    </select>
                  </div>
                </div>
                {recordsSelectionStale ? (
                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                    <span className="inline-flex items-center gap-2">
                      <AlertTriangle className="h-4 w-4 shrink-0" />
                      当前列表来自上一次加载。学院选择已经改变，请重新加载后再预览或导入。
                    </span>
                    <button
                      type="button"
                      disabled={recordsLoading || selectedUnitPaths.length === 0}
                      onClick={() => void loadRecordsForPaths(selectedUnitPaths)}
                      className="font-semibold text-amber-900 underline decoration-amber-400 underline-offset-2 disabled:opacity-50"
                    >
                      重新加载
                    </button>
                  </div>
                ) : null}
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-stone-50 px-4 py-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-stone-700">
                      <input
                        ref={selectVisibleCheckboxRef}
                        type="checkbox"
                        aria-label="选择当前筛选结果"
                        className="h-4 w-4 accent-orange-600"
                        checked={allVisibleSelected}
                        onChange={toggleVisibleRecords}
                      />
                      <span>
                        选择当前筛选结果
                        {selectedVisibleCount > 0 ? `（已选 ${selectedVisibleCount}/${selectableVisibleIds.length}）` : ''}
                      </span>
                    </label>
                    {selectedVisibleCount > 0 ? (
                      <button
                        type="button"
                        className="text-xs font-medium text-stone-600 underline decoration-stone-300 underline-offset-2"
                        onClick={clearVisibleRecords}
                      >
                        清除当前筛选选择
                      </button>
                    ) : null}
                  </div>
                  <button type="button" disabled={previewLoading || selectedRecordIds.length === 0 || recordsSelectionStale} onClick={() => void openPreview()} className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-50">
                    {previewLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                    预览并导入 {selectedRecordIds.length > 0 ? selectedRecordIds.length : ''}
                  </button>
                </div>
                <div className="mt-4 space-y-3">
                  {visibleRecords.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-stone-200 p-10 text-center text-sm text-stone-500">没有匹配的导师。</div>
                  ) : paginatedVisibleRecords.map((item) => {
                    const meta = categoryMeta[item.category];
                    const selectable = isRecordSelectable(item);
                    return (
                      <article key={item.record.id} className="rounded-2xl border border-stone-200 p-4 transition hover:border-orange-200 hover:shadow-sm">
                        <div className="flex items-start gap-3">
                          <input type="checkbox" aria-label={`选择 ${item.record.name}`} disabled={!selectable} checked={selectedRecordIdSet.has(item.record.id)} onChange={() => toggleRecord(item.record.id)} className="mt-1 h-4 w-4 shrink-0 accent-orange-600 disabled:opacity-40" />
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <h3 className="font-semibold text-stone-950">{item.record.name}</h3>
                              {item.record.title ? <span className="text-sm text-stone-500">{item.record.title}</span> : null}
                              <span title={meta.description} className={clsx('rounded-full border px-2 py-0.5 text-xs font-medium', meta.className)}>{meta.label}</span>
                              {item.record.contacts.length > 1 ? <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-700">{item.record.contacts.length} 个邮箱</span> : null}
                              {item.record.affiliations.length > 1 ? <span className="rounded-full bg-violet-50 px-2 py-0.5 text-xs text-violet-700">{item.record.affiliations.length} 个任职</span> : null}
                            </div>
                            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-stone-600">
                              <span className="inline-flex items-center gap-1"><Mail className="h-3.5 w-3.5" />{item.record.email}</span>
                              <span>{[item.record.university, item.record.school, item.record.department].filter(Boolean).join(' · ')}</span>
                            </div>
                            {item.record.research_direction ? <p className="mt-2 line-clamp-2 text-sm leading-6 text-stone-600">{item.record.research_direction}</p> : null}
                            {item.import_blocked ? (
                              <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-800">
                                <AlertTriangle className="mr-1 inline h-3.5 w-3.5" />
                                <strong>暂不可导入：</strong>{item.import_blocked_reason ?? '请先处理这条导师记录的冲突。'}
                              </div>
                            ) : item.identity_conflict ? <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800"><AlertTriangle className="mr-1 inline h-3.5 w-3.5" />{item.match_reason}</div> : null}
                            <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-stone-500">
                              <span>核验：{formatDate(item.record.last_verified_at)}</span>
                              <span>贡献者：{item.record.contributors.map((contributor) => `@${contributor.github_login_at_submission}`).join('、') || '未记录'}</span>
                              <button type="button" className="inline-flex items-center gap-1 font-medium text-primary hover:underline" onClick={() => openExternalHttpUrl(item.record.source_url)}>官方来源 <ExternalLink className="h-3 w-3" /></button>
                              <button type="button" className="inline-flex items-center gap-1 font-medium text-amber-700 hover:underline" onClick={() => openFeedbackForm(item.record, notifySuccess)}>反馈错误 <ExternalLink className="h-3 w-3" /></button>
                            </div>
                            {(item.record.contacts.length > 1 || item.record.affiliations.length > 1) ? (
                              <details className="mt-3 rounded-xl bg-stone-50 px-3 py-2 text-xs text-stone-600">
                                <summary className="cursor-pointer list-none font-medium text-stone-700"><ChevronDown className="mr-1 inline h-3.5 w-3.5" />查看全部邮箱和任职</summary>
                                <div className="mt-2 grid gap-2 md:grid-cols-2">
                                  <div>{item.record.contacts.map((contact) => <div key={contact.email}>{contact.is_primary ? '主要：' : ''}{contact.email}</div>)}</div>
                                  <div>{item.record.affiliations.map((affiliation) => <div key={affiliation.id}>{affiliation.is_primary ? '主要：' : ''}{[affiliation.university, affiliation.school, affiliation.department].filter(Boolean).join(' · ')}</div>)}</div>
                                </div>
                              </details>
                            ) : null}
                          </div>
                        </div>
                      </article>
                    );
                  })}
                </div>
                {visibleRecords.length > RECORDS_PER_PAGE ? (
                  <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-stone-100 pt-4">
                    <span className="text-xs text-stone-500">
                      第 {currentRecordPage}/{totalRecordPages} 页 · 当前筛选共 {visibleRecords.length} 位
                    </span>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        aria-label="上一页"
                        disabled={currentRecordPage <= 1}
                        onClick={() => setRecordPage((current) => Math.max(1, current - 1))}
                        className="ui-btn-secondary px-3 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <ChevronLeft className="h-4 w-4" />
                        上一页
                      </button>
                      <button
                        type="button"
                        aria-label="下一页"
                        disabled={currentRecordPage >= totalRecordPages}
                        onClick={() => setRecordPage((current) => Math.min(totalRecordPages, current + 1))}
                        className="ui-btn-secondary px-3 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        下一页
                        <ChevronRight className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                ) : null}
              </>
            )}
          </section>
        </div>
      ) : null}

      {previewPayload ? (
        <div role="dialog" aria-modal="true" aria-label="社区导师导入预览" className="fixed inset-0 z-[90] flex items-center justify-center bg-stone-950/40 p-4 backdrop-blur-sm">
          <div className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-[30px] border border-white/60 bg-stone-50 shadow-2xl">
            <div className="flex items-start justify-between gap-4 border-b border-stone-200 bg-white px-6 py-5">
              <div>
                <h2 className="text-xl font-semibold text-stone-950">导入预览与字段选择</h2>
                <p className="mt-1 text-sm text-stone-500">默认只补全空字段；本地修改和冲突默认保留本地。</p>
              </div>
              <button type="button" disabled={importing} onClick={() => setPreviewPayload(null)} className="rounded-xl p-2 text-stone-500 hover:bg-stone-100" aria-label="关闭预览"><X className="h-5 w-5" /></button>
            </div>
            <div className="flex-1 space-y-5 overflow-y-auto p-5 md:p-6">
              {previewPayload.records.map((item) => {
                const visibleFields = item.fields.filter((field) => item.category === 'new' || field.state !== 'same');
                const allowLocalChoice = item.local_professor_id !== null;
                return (
                  <section key={item.record.id} className="rounded-[24px] border border-stone-200 bg-white p-5 shadow-sm">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-stone-950">{item.record.name}</h3><span className={clsx('rounded-full border px-2 py-0.5 text-xs font-medium', categoryMeta[item.category].className)}>{categoryMeta[item.category].label}</span></div>
                        <p className="mt-1 text-sm text-stone-500">{item.record.email} · {[item.record.university, item.record.school].filter(Boolean).join(' · ')}</p>
                      </div>
                      {item.local_professor_id ? <Link to={`/professors?keyword=${encodeURIComponent(item.local_professor_name ?? item.record.name)}`} className="text-xs font-medium text-primary hover:underline">查看本地导师</Link> : null}
                    </div>
                    {item.local_archived ? <div className="mt-4 rounded-xl border border-orange-200 bg-orange-50 px-3 py-2 text-sm text-orange-800">这位导师在本地回收站中。导入可以补全字段，但不会自动恢复。</div> : null}
                    {item.import_blocked ? (
                      <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                        <strong>暂不可导入：</strong>{item.import_blocked_reason ?? '请先处理这条导师记录的冲突。'}
                      </div>
                    ) : item.identity_conflict ? (
                      <label className="mt-4 flex cursor-pointer items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                        <input type="checkbox" className="mt-0.5 h-4 w-4 accent-amber-600" checked={identityConfirmations[item.record.id] ?? false} onChange={(event) => setIdentityConfirmations((current) => ({ ...current, [item.record.id]: event.target.checked }))} />
                        <span><strong>人工确认同一导师：</strong>{item.match_reason}</span>
                      </label>
                    ) : null}
                    {visibleFields.length > 0 ? (
                      <div className="mt-4 grid gap-3">
                        {visibleFields.map((field) => (
                          <DifferenceField key={field.field} field={field} choice={fieldChoices[item.record.id]?.[field.field] ?? field.suggested_choice} allowLocalChoice={allowLocalChoice} onChange={(choice) => setFieldChoices((current) => ({ ...current, [item.record.id]: { ...(current[item.record.id] ?? {}), [field.field]: choice } }))} />
                        ))}
                      </div>
                    ) : <div className="mt-4 rounded-xl bg-stone-50 px-3 py-3 text-sm text-stone-600">当前字段均一致；导入只会建立或刷新稳定社区关联。</div>}
                  </section>
                );
              })}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 bg-white px-6 py-4">
              <p className="text-xs text-stone-500">不会导入标签、个人备注、任务、发送记录或匹配结果。</p>
              <div className="flex gap-3">
                <button type="button" disabled={importing} onClick={() => setPreviewPayload(null)} className="ui-btn-secondary">取消</button>
                <button type="button" disabled={importing || recordsSelectionStale || previewPayload.records.some((item) => !isRecordSelectable(item))} onClick={() => void submitImport()} className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60">{importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}确认导入 {previewPayload.records.length} 位</button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
};
