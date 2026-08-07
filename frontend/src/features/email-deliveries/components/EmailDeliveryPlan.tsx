import {
  AlertTriangle,
  Ban,
  CalendarClock,
  CheckCircle2,
  ChevronRight,
  Clock3,
  ExternalLink,
  Loader2,
  Mail,
  Paperclip,
  RotateCcw,
  Search,
  Send,
  X,
} from 'lucide-react';
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { NativeSelectField } from '@/components/atoms/NativeSelectField';
import { KeywordSearchScopeSelect } from '@/components/molecules/KeywordSearchScopeSelect';
import { Pagination } from '@/components/molecules/Pagination';
import { useNotification } from '@/context/NotificationContext';
import { useSelectionContext } from '@/context/SelectionContext';
import { cancelBatchTaskItemSend, restoreBatchTaskItemSend } from '@/lib/api/batchTasksApi';
import {
  cancelEmailDelivery,
  listEmailDeliveries,
  rescheduleEmailDelivery,
  sendEmailDeliveryNow,
} from '@/lib/api/emailDeliveriesApi';
import { formatApiDateTime, parseApiDateTime } from '@/lib/dateTime';
import { formatFileSize } from '@/lib/formatFileSize';
import { useConfirmDialog } from '@/lib/useConfirmDialog';
import { useDismissableLayerClick } from '@/lib/useDismissableLayerClick';
import { useDocumentScrollLock } from '@/lib/useDocumentScrollLock';
import { usePaginationState } from '@/lib/usePaginationState';
import type {
  EmailDeliveryItemDTO,
  EmailDeliveryListDTO,
  EmailDeliverySearchField,
  EmailDeliverySort,
  EmailDeliverySourceFilter,
  EmailDeliveryStatus,
  EmailDeliveryView,
} from '@/types';
import {
  TaskCenterSectionSwitch,
  type TaskCenterSection,
} from './TaskCenterSectionSwitch';

const EMPTY_DELIVERY_LIST: EmailDeliveryListDTO = {
  items: [],
  counts: { upcoming: 0, attention: 0, history: 0 },
  page: 1,
  page_size: 20,
  total_count: 0,
  total_pages: 1,
};

const DELIVERY_PAGE_CACHE_LIMIT = 20;
const DELIVERY_REFRESH_INTERVALS: Record<EmailDeliveryView, number> = {
  upcoming: 5_000,
  attention: 10_000,
  history: 30_000,
};
const DELIVERY_SEARCH_REFRESH_INTERVAL = 30_000;

const DELIVERY_VIEW_LABELS: Record<EmailDeliveryView, string> = {
  upcoming: '即将发送',
  attention: '需处理',
  history: '历史',
};

const DELIVERY_STATUS_OPTIONS: Record<
  EmailDeliveryView,
  Array<{ value: EmailDeliveryStatus; label: string }>
> = {
  upcoming: [
    { value: 'waiting_scheduled', label: '等待发送' },
    { value: 'send_asap', label: '尽快发送' },
    { value: 'batch_paused', label: '批次已暂停' },
    { value: 'sending', label: '正在发送' },
  ],
  attention: [
    { value: 'send_failed', label: '发送失败' },
    { value: 'schedule_missed', label: '错过计划' },
    { value: 'batch_stopped', label: '批次已结束' },
  ],
  history: [
    { value: 'sent', label: '已发送' },
    { value: 'replied', label: '已回复' },
    { value: 'canceled_schedule', label: '已取消定时' },
    { value: 'canceled_send', label: '已取消发送' },
  ],
};

const DELIVERY_STATUS_TONES: Record<EmailDeliveryStatus, string> = {
  waiting_scheduled: 'border-sky-200 bg-sky-50 text-sky-700',
  send_asap: 'border-primary/20 bg-primary/8 text-primary',
  batch_paused: 'border-amber-200 bg-amber-50 text-amber-800',
  sending: 'border-violet-200 bg-violet-50 text-violet-700',
  send_failed: 'border-red-200 bg-red-50 text-red-700',
  schedule_missed: 'border-amber-200 bg-amber-50 text-amber-800',
  batch_stopped: 'border-stone-200 bg-stone-100 text-stone-700',
  sent: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  replied: 'border-teal-200 bg-teal-50 text-teal-700',
  canceled_schedule: 'border-stone-200 bg-stone-100 text-stone-600',
  canceled_send: 'border-stone-200 bg-stone-100 text-stone-600',
};

const DELIVERY_STATUS_VIEWS: Record<EmailDeliveryStatus, EmailDeliveryView> = {
  waiting_scheduled: 'upcoming',
  send_asap: 'upcoming',
  batch_paused: 'upcoming',
  sending: 'upcoming',
  send_failed: 'attention',
  schedule_missed: 'attention',
  batch_stopped: 'attention',
  sent: 'history',
  replied: 'history',
  canceled_schedule: 'history',
  canceled_send: 'history',
};

const DEFAULT_DELIVERY_SORTS: Record<EmailDeliveryView, EmailDeliverySort> = {
  upcoming: 'scheduled_asc',
  attention: 'updated_desc',
  history: 'event_desc',
};

const DELIVERY_SEARCH_FIELD_OPTIONS: ReadonlyArray<{
  value: EmailDeliverySearchField;
  label: string;
}> = [
  { value: 'recipient_name', label: '导师姓名' },
  { value: 'recipient_email', label: '导师邮箱' },
  { value: 'subject', label: '邮件主题' },
  { value: 'batch_name', label: '批量任务' },
];

const DEFAULT_DELIVERY_SEARCH_FIELDS = DELIVERY_SEARCH_FIELD_OPTIONS.map(
  (option) => option.value,
);

const parseSearchFields = (value: string | null): EmailDeliverySearchField[] => {
  if (!value) {
    return [...DEFAULT_DELIVERY_SEARCH_FIELDS];
  }
  const requested = new Set(value.split(','));
  const validFields = DELIVERY_SEARCH_FIELD_OPTIONS
    .map((option) => option.value)
    .filter((field) => requested.has(field));
  return validFields.length > 0
    ? validFields
    : [...DEFAULT_DELIVERY_SEARCH_FIELDS];
};

const DELIVERY_SORT_OPTIONS: Record<
  EmailDeliveryView,
  Array<{ value: EmailDeliverySort; label: string }>
> = {
  upcoming: [
    { value: 'scheduled_asc', label: '最先发送优先' },
    { value: 'scheduled_desc', label: '最晚计划优先' },
    { value: 'updated_desc', label: '最近变更优先' },
  ],
  attention: [
    { value: 'updated_desc', label: '最近出现优先' },
    { value: 'updated_asc', label: '最早问题优先' },
    { value: 'scheduled_asc', label: '原计划最早优先' },
  ],
  history: [
    { value: 'event_desc', label: '最近完成优先' },
    { value: 'event_asc', label: '最早完成优先' },
  ],
};

const VIEW_EMPTY_COPY: Record<
  EmailDeliveryView,
  { title: string; description: string }
> = {
  upcoming: {
    title: '暂无待发送邮件',
    description: '在导师工作区完成草稿后，可以选择定时发送。',
  },
  attention: {
    title: '当前没有需要处理的发送问题',
    description: '发送失败或错过计划的邮件会出现在这里。',
  },
  history: {
    title: '暂无发送历史',
    description: '已发送和已取消的邮件会保留在这里。',
  },
};

const isDeliveryView = (value: string | null): value is EmailDeliveryView =>
  value === 'upcoming' || value === 'attention' || value === 'history';

const isSourceFilter = (
  value: string | null,
): value is EmailDeliverySourceFilter =>
  value === 'manual' || value === 'batch' || value === 'all';

const isDeliverySortForView = (
  value: string | null,
  view: EmailDeliveryView,
): value is EmailDeliverySort =>
  DELIVERY_SORT_OPTIONS[view].some((option) => option.value === value);

const parsePositiveInteger = (value: string | null) => {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
};

const toLocalDateTimeInput = (value: string | null) => {
  const date = value ? parseApiDateTime(value) : new Date(Date.now() + 60 * 60 * 1000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
};

const formatDeliveryTime = (item: EmailDeliveryItemDTO) => {
  if (item.status === 'send_asap') {
    return '尽快发送';
  }
  const value = item.sent_at ?? item.scheduled_at ?? item.last_scheduled_at;
  return value ? formatApiDateTime(value) : '时间待确定';
};

const formatFullDateTime = (value: string | null) =>
  value
    ? formatApiDateTime(value, {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : '暂无';

const DeliveryStatusBadge = ({ item }: { item: EmailDeliveryItemDTO }) => (
  <span
    className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${DELIVERY_STATUS_TONES[item.status]}`}
    title={item.status_description}
  >
    {item.status === 'send_failed' || item.status === 'schedule_missed' ? (
      <AlertTriangle className="h-3.5 w-3.5" />
    ) : item.status === 'sent' || item.status === 'replied' ? (
      <CheckCircle2 className="h-3.5 w-3.5" />
    ) : item.status === 'canceled_schedule' || item.status === 'canceled_send' ? (
      <Ban className="h-3.5 w-3.5" />
    ) : (
      <Clock3 className="h-3.5 w-3.5" />
    )}
    {item.status_label}
  </span>
);

type EmailDeliveryPlanProps = {
  onSectionChange: (section: TaskCenterSection) => void;
  onOpenBatchTask: (identityId: number, batchTaskId: number) => void;
};

export const EmailDeliveryPlan = ({
  onSectionChange,
  onOpenBatchTask,
}: EmailDeliveryPlanProps) => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { identities = [], setSelectedIdentityId } = useSelectionContext();
  const { notifyError, notifySuccess } = useNotification();
  const { confirm, dialog: confirmDialog } = useConfirmDialog();
  const view = isDeliveryView(searchParams.get('view'))
    ? searchParams.get('view') as EmailDeliveryView
    : 'upcoming';
  const source = isSourceFilter(searchParams.get('source'))
    ? searchParams.get('source') as EmailDeliverySourceFilter
    : 'all';
  const sort = isDeliverySortForView(searchParams.get('sort'), view)
    ? searchParams.get('sort') as EmailDeliverySort
    : DEFAULT_DELIVERY_SORTS[view];
  const identityId = parsePositiveInteger(searchParams.get('identity_id'));
  const locatedTaskId = parsePositiveInteger(searchParams.get('task_id'));
  const status = searchParams.get('status');
  const query = searchParams.get('q') ?? '';
  const searchFieldsParam = searchParams.get('search_fields');
  const searchFields = useMemo(
    () => parseSearchFields(searchFieldsParam),
    [searchFieldsParam],
  );
  const searchPlaceholder = useMemo(() => {
    const selected = new Set(searchFields);
    return DELIVERY_SEARCH_FIELD_OPTIONS
      .filter((option) => selected.has(option.value))
      .map((option) => option.label)
      .join('、');
  }, [searchFields]);
  const [searchValue, setSearchValue] = useState(query);
  const [data, setData] = useState<EmailDeliveryListDTO>(EMPTY_DELIVERY_LIST);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [advancedFiltersOpen, setAdvancedFiltersOpen] = useState(false);
  const [selectedItem, setSelectedItem] = useState<EmailDeliveryItemDTO | null>(null);
  const [rescheduleItem, setRescheduleItem] = useState<EmailDeliveryItemDTO | null>(null);
  const [rescheduleValue, setRescheduleValue] = useState('');
  const [rescheduleMinValue, setRescheduleMinValue] = useState('');
  const [actingTaskId, setActingTaskId] = useState<number | null>(null);
  const requestIdRef = useRef(0);
  const activeRequestRef = useRef<AbortController | null>(null);
  const pageCacheRef = useRef(new Map<string, EmailDeliveryListDTO>());
  const listStartRef = useRef<HTMLElement | null>(null);
  const lastLoadErrorRef = useRef<string | null>(null);
  const {
    page,
    pageSize,
    setPage,
    onChange: handlePaginationChange,
  } = usePaginationState({
    storageKey: 'tasks:email-deliveries:page-size',
    initialPageSize: 20,
  });
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || '本地时区';
  const activeFilters = Boolean(identityId || source !== 'all' || status || query);
  const activeAdvancedFilterCount =
    Number(Boolean(identityId)) + Number(source !== 'all') + Number(Boolean(status));
  const refreshInterval = query.trim()
    ? DELIVERY_SEARCH_REFRESH_INTERVAL
    : DELIVERY_REFRESH_INTERVALS[view];

  const requestCacheKey = useMemo(
    () => JSON.stringify({
      view,
      page,
      pageSize,
      identityId: locatedTaskId ? null : identityId,
      source: locatedTaskId ? 'all' : source,
      status: locatedTaskId ? null : status,
      sort,
      searchFields,
      query: locatedTaskId ? null : query,
      taskId: locatedTaskId,
    }),
    [
      identityId,
      locatedTaskId,
      page,
      pageSize,
      query,
      searchFields,
      sort,
      source,
      status,
      view,
    ],
  );

  const cachePage = useCallback((key: string, nextData: EmailDeliveryListDTO) => {
    const cache = pageCacheRef.current;
    cache.delete(key);
    cache.set(key, nextData);
    while (cache.size > DELIVERY_PAGE_CACHE_LIMIT) {
      const oldestKey = cache.keys().next().value;
      if (oldestKey === undefined) {
        break;
      }
      cache.delete(oldestKey);
    }
  }, []);

  const updateSearchParams = useCallback(
    (patch: Record<string, string | null>) => {
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set('section', 'delivery');
        Object.entries(patch).forEach(([key, value]) => {
          if (!value) {
            next.delete(key);
          } else {
            next.set(key, value);
          }
        });
        return next;
      }, { replace: true });
    },
    [setSearchParams],
  );

  useEffect(() => {
    setSearchValue(query);
  }, [query]);

  useEffect(() => {
    if (searchValue === query) {
      return undefined;
    }
    const timer = window.setTimeout(() => {
      setPage(1);
      updateSearchParams({ q: searchValue.trim() || null, task_id: null });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query, searchValue, setPage, updateSearchParams]);

  const loadDeliveries = useCallback(
    async (showLoading: boolean, options?: { skipIfBusy?: boolean }) => {
      if (options?.skipIfBusy && activeRequestRef.current) {
        return;
      }
      activeRequestRef.current?.abort();
      const controller = new AbortController();
      activeRequestRef.current = controller;
      const requestId = requestIdRef.current + 1;
      requestIdRef.current = requestId;
      const cached = pageCacheRef.current.get(requestCacheKey);
      if (cached) {
        pageCacheRef.current.delete(requestCacheKey);
        pageCacheRef.current.set(requestCacheKey, cached);
        setData(cached);
      }
      setLoading(showLoading && !cached);
      if (!showLoading || cached) {
        setRefreshing(true);
      }
      try {
        const nextData = await listEmailDeliveries({
          view,
          page,
          pageSize,
          identityId: locatedTaskId ? null : identityId,
          source: locatedTaskId ? 'all' : source,
          status: locatedTaskId ? null : status,
          sort,
          searchFields,
          query: locatedTaskId ? null : query,
          taskId: locatedTaskId,
        }, controller.signal);
        if (requestIdRef.current !== requestId) {
          return;
        }
        cachePage(requestCacheKey, nextData);
        setData(nextData);
        if (nextData.page !== page) {
          setPage(nextData.page);
        }
        setSelectedItem((current) =>
          current
            ? nextData.items.find((item) => item.id === current.id) ?? current
            : current,
        );
        lastLoadErrorRef.current = null;
      } catch (error) {
        if (requestIdRef.current !== requestId) {
          return;
        }
        if (error instanceof DOMException && error.name === 'AbortError') {
          return;
        }
        const message = error instanceof Error ? error.message : '加载发送计划失败';
        if (lastLoadErrorRef.current !== message) {
          notifyError('加载发送计划失败', message);
          lastLoadErrorRef.current = message;
        }
      } finally {
        if (activeRequestRef.current === controller) {
          activeRequestRef.current = null;
        }
        if (requestIdRef.current === requestId) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [
      cachePage,
      identityId,
      locatedTaskId,
      notifyError,
      page,
      pageSize,
      query,
      requestCacheKey,
      searchFields,
      setPage,
      sort,
      source,
      status,
      view,
    ],
  );

  useEffect(() => {
    void loadDeliveries(true);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        void loadDeliveries(false, { skipIfBusy: true });
      }
    }, refreshInterval);
    return () => {
      requestIdRef.current += 1;
      activeRequestRef.current?.abort();
      activeRequestRef.current = null;
      window.clearInterval(timer);
    };
  }, [loadDeliveries, refreshInterval]);

  useEffect(() => {
    if (!locatedTaskId || data.items.length === 0) {
      return;
    }
    const located = data.items.find((item) => item.id === locatedTaskId);
    if (located) {
      setSelectedItem(located);
      const locatedView = DELIVERY_STATUS_VIEWS[located.status];
      if (locatedView !== view) {
        updateSearchParams({ view: locatedView, sort: null });
        notifySuccess(
          '邮件状态已更新',
          `已切换到“${DELIVERY_VIEW_LABELS[locatedView]}”查看。`,
        );
      }
    }
  }, [data.items, locatedTaskId, notifySuccess, updateSearchParams, view]);

  useDocumentScrollLock(Boolean(selectedItem || rescheduleItem));
  const closeDetails = useCallback(() => setSelectedItem(null), []);
  const detailLayer = useDismissableLayerClick(closeDetails);
  const closeReschedule = useCallback(() => setRescheduleItem(null), []);
  const rescheduleLayer = useDismissableLayerClick(closeReschedule);

  const clearFilters = () => {
    setSearchValue('');
    setPage(1);
    updateSearchParams({
      identity_id: null,
      source: null,
      status: null,
      sort: null,
      search_fields: null,
      q: null,
      task_id: null,
    });
  };

  const openReschedule = (item: EmailDeliveryItemDTO) => {
    setSelectedItem(null);
    setRescheduleItem(item);
    setRescheduleValue(toLocalDateTimeInput(item.scheduled_at));
    setRescheduleMinValue(
      toLocalDateTimeInput(new Date(Date.now() + 60_000).toISOString()),
    );
  };

  const handleReschedule = async () => {
    if (!rescheduleItem) {
      return;
    }
    const nextDate = new Date(rescheduleValue);
    if (
      Number.isNaN(nextDate.getTime()) ||
      nextDate.getTime() < Date.now() + 60_000
    ) {
      notifyError('无法修改发送时间', '新的发送时间必须晚于当前时间至少 1 分钟。');
      return;
    }
    setActingTaskId(rescheduleItem.id);
    try {
      const result = await rescheduleEmailDelivery(rescheduleItem.id, {
        scheduled_at: nextDate.toISOString(),
        expected_updated_at: rescheduleItem.updated_at,
      });
      notifySuccess(
        result.message,
        `将于 ${formatApiDateTime(nextDate.toISOString())} 发送给 ${rescheduleItem.professor_name}。`,
      );
      setRescheduleItem(null);
      updateSearchParams({ view: 'upcoming', task_id: String(rescheduleItem.id) });
      await loadDeliveries(false);
    } catch (error) {
      notifyError(
        '修改发送时间失败',
        error instanceof Error ? error.message : '请刷新后重试',
      );
    } finally {
      setActingTaskId(null);
    }
  };

  const handleCancel = async (item: EmailDeliveryItemDTO) => {
    const confirmed = await confirm({
      title: item.source === 'manual' ? '取消这封定时邮件？' : '取消这位导师的发送？',
      description:
        item.source === 'manual'
          ? `将取消给 ${item.professor_name} 的定时发送。邮件草稿和附件仍保留在工作区，可以稍后重新安排。`
          : `只取消给 ${item.professor_name} 的发送，不会停止所属批量任务。`,
      confirmLabel: item.source === 'manual' ? '确认取消定时' : '确认取消发送',
      cancelLabel: '继续保留',
      tone: 'danger',
    });
    if (!confirmed) {
      return;
    }
    setActingTaskId(item.id);
    try {
      if (item.source === 'batch' && item.batch_task_id) {
        await cancelBatchTaskItemSend(item.batch_task_id, item.id);
      } else {
        await cancelEmailDelivery(item.id, item.updated_at);
      }
      notifySuccess(
        item.source === 'manual' ? '已取消定时' : '已取消该封发送',
        item.source === 'manual' ? '草稿仍保留在原工作区。' : '所属批量任务不会停止。',
      );
      setSelectedItem(null);
      updateSearchParams({ task_id: null });
      await loadDeliveries(false);
    } catch (error) {
      notifyError('取消发送失败', error instanceof Error ? error.message : '请刷新后重试');
    } finally {
      setActingTaskId(null);
    }
  };

  const handleRestore = async (item: EmailDeliveryItemDTO) => {
    if (!item.batch_task_id) {
      return;
    }
    setActingTaskId(item.id);
    try {
      await restoreBatchTaskItemSend(item.batch_task_id, item.id);
      notifySuccess('已恢复发送', `将继续按原计划发送给 ${item.professor_name}。`);
      setSelectedItem(null);
      await loadDeliveries(false);
    } catch (error) {
      notifyError('恢复发送失败', error instanceof Error ? error.message : '请刷新后重试');
    } finally {
      setActingTaskId(null);
    }
  };

  const handleSendNow = async (item: EmailDeliveryItemDTO) => {
    const confirmed = await confirm({
      title: '确认立即发送？',
      description: `将立即使用 ${item.identity_name}（${item.sender_email}）发送给 ${item.professor_name}（${item.professor_email ?? '未填写邮箱'}）。邮件进入发送流程后无法撤回。`,
      confirmLabel: '确认立即发送',
      cancelLabel: '继续保留计划',
      tone: 'danger',
    });
    if (!confirmed) {
      return;
    }
    setActingTaskId(item.id);
    try {
      const result = await sendEmailDeliveryNow(item.id, item.updated_at);
      if (result.ok) {
        notifySuccess('邮件已发送', `已发送给 ${item.professor_name}。`);
      } else {
        notifyError('邮件未能发送', result.message);
      }
      setSelectedItem(null);
      updateSearchParams({ task_id: null });
      await loadDeliveries(false);
    } catch (error) {
      notifyError('立即发送失败', error instanceof Error ? error.message : '请刷新后重试');
    } finally {
      setActingTaskId(null);
    }
  };

  const openSource = (item: EmailDeliveryItemDTO) => {
    if (item.source === 'batch' && item.batch_task_id) {
      onOpenBatchTask(item.identity_id, item.batch_task_id);
      return;
    }
    setSelectedIdentityId(item.identity_id);
    navigate(`/workspace/${item.professor_id}?task_id=${item.id}`);
  };

  const statusOptions = DELIVERY_STATUS_OPTIONS[view];
  const emptyCopy = activeFilters
    ? {
        title: '没有符合当前条件的发送项',
        description: '调整搜索内容或清除筛选后重试。',
      }
    : VIEW_EMPTY_COPY[view];

  const selectedStatusLabel = useMemo(
    () => statusOptions.find((option) => option.value === status)?.label ?? '全部状态',
    [status, statusOptions],
  );

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      {confirmDialog}
      <div className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-semibold text-stone-900">任务中心</h1>
            <p className="mt-1 text-sm text-stone-500">统一查看邮件发送与后台任务</p>
          </div>
          <TaskCenterSectionSwitch
            activeSection="delivery"
            onChange={onSectionChange}
          />
        </div>

        <div className="mt-5 inline-flex max-w-full gap-1 overflow-x-auto rounded-2xl border border-stone-200 bg-white p-1 shadow-sm">
          {(Object.keys(DELIVERY_VIEW_LABELS) as EmailDeliveryView[]).map((nextView) => (
            <button
              key={nextView}
              type="button"
              onClick={() => {
                setPage(1);
                updateSearchParams({
                  view: nextView,
                  status: null,
                  sort: null,
                  task_id: null,
                });
              }}
              className={
                view === nextView
                  ? 'inline-flex min-h-9 shrink-0 items-center gap-2 rounded-xl bg-primary px-4 text-sm font-medium text-white'
                  : 'inline-flex min-h-9 shrink-0 items-center gap-2 rounded-xl px-4 text-sm font-medium text-stone-600 hover:bg-stone-50'
              }
            >
              {DELIVERY_VIEW_LABELS[nextView]}
              <span className={view === nextView ? 'text-white/80' : 'text-stone-400'}>
                {data.counts[nextView]}
              </span>
            </button>
          ))}
        </div>

        <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(12rem,1fr)_auto_auto] lg:items-stretch">
          <label className="flex min-h-[54px] items-center gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm text-stone-600 shadow-sm">
            <div className="shrink-0 font-medium leading-5 text-stone-800">
              关键词
            </div>
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <Search className="h-4 w-4 shrink-0 text-stone-400" />
              <input
                type="search"
                aria-label="搜索发送计划"
                value={searchValue}
                onChange={(event) => setSearchValue(event.target.value)}
                placeholder={searchPlaceholder}
                className="w-full min-w-0 bg-transparent leading-5 outline-none"
              />
              <KeywordSearchScopeSelect
                label="搜索范围"
                options={DELIVERY_SEARCH_FIELD_OPTIONS}
                selectedValues={searchFields}
                embedded
                onChange={(nextFields) => {
                  setPage(1);
                  const allFieldsSelected =
                    nextFields.length === DEFAULT_DELIVERY_SEARCH_FIELDS.length;
                  updateSearchParams({
                    search_fields: allFieldsSelected ? null : nextFields.join(','),
                    task_id: null,
                  });
                }}
              />
            </div>
          </label>

          <div className="flex min-h-[54px] items-center gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm text-stone-600 shadow-sm">
            <div className="shrink-0 font-medium leading-5 text-stone-800">
              排序
            </div>
            <NativeSelectField
              ariaLabel="发送计划排序"
              value={sort}
              onChange={(event) => {
                setPage(1);
                updateSearchParams({
                  sort:
                    event.target.value === DEFAULT_DELIVERY_SORTS[view]
                      ? null
                      : event.target.value,
                  task_id: null,
                });
              }}
              wrapperClassName="min-w-0 flex-1"
              shellClassName="!min-h-0 h-8 border-0 bg-stone-50 px-3 py-0 shadow-none"
            >
              {DELIVERY_SORT_OPTIONS[view].map((option) => (
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
            {activeAdvancedFilterCount > 0 ? ` ${activeAdvancedFilterCount}` : ''}
          </button>

          <button
            type="button"
            onClick={clearFilters}
            className="ui-btn-secondary h-full justify-center whitespace-nowrap"
          >
            重置
          </button>
        </div>

        {advancedFiltersOpen ? (
          <div className="mt-3 rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm font-semibold text-stone-800">高级筛选</div>
              <button
                type="button"
                onClick={() => {
                  setPage(1);
                  updateSearchParams({
                    identity_id: null,
                    source: null,
                    status: null,
                    task_id: null,
                  });
                }}
                className="ui-btn-secondary px-3 py-1.5 text-sm"
              >
                清空高级筛选
              </button>
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              <NativeSelectField
                label="发件身份"
                ariaLabel="筛选发件身份"
                value={identityId ? String(identityId) : 'all'}
                selectedLabel={
                  identityId
                    ? identities.find((identity) => identity.id === identityId)?.profile_name ?? '原身份已删除'
                    : '全部身份'
                }
                onChange={(event) => {
                  setPage(1);
                  updateSearchParams({
                    identity_id: event.target.value === 'all' ? null : event.target.value,
                    task_id: null,
                  });
                }}
                wrapperClassName="min-w-0"
                shellClassName="min-h-10 rounded-xl shadow-none"
              >
                <option value="all">全部身份</option>
                {identities.map((identity) => (
                  <option key={identity.id} value={identity.id}>
                    {identity.profile_name}
                  </option>
                ))}
              </NativeSelectField>
              <NativeSelectField
                label="邮件来源"
                ariaLabel="筛选邮件来源"
                value={source}
                onChange={(event) => {
                  setPage(1);
                  updateSearchParams({
                    source: event.target.value === 'all' ? null : event.target.value,
                    task_id: null,
                  });
                }}
                wrapperClassName="min-w-0"
                shellClassName="min-h-10 rounded-xl shadow-none"
              >
                <option value="all">全部来源</option>
                <option value="manual">工作区邮件</option>
                <option value="batch">批量邮件</option>
              </NativeSelectField>
              <NativeSelectField
                label="发送状态"
                ariaLabel="筛选发送状态"
                value={status ?? 'all'}
                selectedLabel={selectedStatusLabel}
                onChange={(event) => {
                  setPage(1);
                  updateSearchParams({
                    status: event.target.value === 'all' ? null : event.target.value,
                    task_id: null,
                  });
                }}
                wrapperClassName="min-w-0"
                shellClassName="min-h-10 rounded-xl shadow-none"
              >
                <option value="all">全部状态</option>
                {statusOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </NativeSelectField>
            </div>
          </div>
        ) : null}
      </div>

      <div className="mt-4 flex flex-col gap-4">
        {locatedTaskId ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-primary/20 bg-primary/5 px-4 py-3 text-sm text-stone-700">
            <span>已从工作区定位到这封邮件，暂时忽略其他筛选条件。</span>
            <button type="button" onClick={clearFilters} className="ui-btn-secondary shadow-none">
              返回完整列表
            </button>
          </div>
        ) : null}
      </div>

      <section
        ref={listStartRef}
        tabIndex={-1}
        aria-label="发送计划列表"
        className="mt-5 scroll-mt-24 focus:outline-none"
      >
        {loading ? (
          <div className="flex items-center justify-center gap-2 rounded-2xl border border-stone-200 bg-white px-6 py-16 text-sm text-stone-500 shadow-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在加载发送计划...
          </div>
        ) : data.items.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-stone-300 bg-white px-6 py-16 text-center shadow-sm">
            <Mail className="mx-auto h-7 w-7 text-stone-300" />
            <h2 className="mt-4 text-base font-semibold text-stone-800">{emptyCopy.title}</h2>
            <p className="mt-2 text-sm text-stone-500">{emptyCopy.description}</p>
            {activeFilters ? (
              <button type="button" onClick={clearFilters} className="ui-btn-secondary mt-5">
                清除筛选
              </button>
            ) : null}
          </div>
        ) : (
          <>
            <div className="hidden overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-sm md:block">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[960px] table-fixed text-left">
                  <thead className="border-b border-stone-200 bg-stone-50 text-xs font-medium text-stone-500">
                    <tr>
                      <th className="w-[150px] px-4 py-3">计划时间</th>
                      <th className="w-[180px] px-4 py-3">收件人</th>
                      <th className="px-4 py-3">邮件</th>
                      <th className="w-[180px] px-4 py-3">发件身份</th>
                      <th className="w-[135px] px-4 py-3">状态</th>
                      <th className="w-[130px] px-4 py-3 text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((item) => (
                      <tr
                        key={item.id}
                        onClick={() => setSelectedItem(item)}
                        className={`cursor-pointer border-b border-stone-100 transition last:border-b-0 hover:bg-stone-50 ${locatedTaskId === item.id ? 'bg-primary/5 shadow-[inset_3px_0_0_0_var(--color-primary)]' : ''}`}
                      >
                        <td className="px-4 py-4 align-top">
                          <div className="text-sm font-medium text-stone-900">{formatDeliveryTime(item)}</div>
                          <div className="mt-1 text-xs text-stone-400">{item.status_description}</div>
                        </td>
                        <td className="px-4 py-4 align-top">
                          <div className="truncate text-sm font-medium text-stone-900">{item.professor_name}</div>
                          <div className="mt-1 truncate text-xs text-stone-500">{item.professor_email ?? '未填写邮箱'}</div>
                        </td>
                        <td className="px-4 py-4 align-top">
                          <div className="line-clamp-2 text-sm font-medium leading-5 text-stone-900">{item.subject?.trim() || '（无主题）'}</div>
                          <div className="mt-1 truncate text-xs text-stone-500">
                            {item.source === 'manual' ? '工作区邮件' : item.batch_task_name ?? '批量邮件'}
                          </div>
                        </td>
                        <td className="px-4 py-4 align-top">
                          <div className="truncate text-sm text-stone-800">{item.identity_name}</div>
                          <div className="mt-1 truncate text-xs text-stone-500">{item.sender_email}</div>
                        </td>
                        <td className="px-4 py-4 align-top"><DeliveryStatusBadge item={item} /></td>
                        <td className="px-4 py-4 align-top">
                          <div className="flex justify-end gap-2">
                            {item.can_reschedule ? (
                              <button
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  openReschedule(item);
                                }}
                                className="ui-btn-secondary h-9 px-3 py-1.5 shadow-none"
                              >
                                <CalendarClock className="h-4 w-4" />
                                改期
                              </button>
                            ) : null}
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                setSelectedItem(item);
                              }}
                              className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/30 hover:bg-primary/5 hover:text-primary"
                              aria-label={`查看 ${item.professor_name} 的发送详情`}
                              title="查看详情"
                            >
                              <ChevronRight className="h-4 w-4" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="grid gap-3 md:hidden">
              {data.items.map((item) => (
                <article
                  key={item.id}
                  className={`rounded-2xl border bg-white p-4 shadow-sm ${locatedTaskId === item.id ? 'border-primary/30 ring-2 ring-primary/10' : 'border-stone-200'}`}
                >
                  <button type="button" onClick={() => setSelectedItem(item)} className="w-full text-left">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-semibold text-stone-900">{formatDeliveryTime(item)}</div>
                        <div className="mt-1 text-xs text-stone-500">{item.professor_name} · {item.professor_email ?? '未填写邮箱'}</div>
                      </div>
                      <DeliveryStatusBadge item={item} />
                    </div>
                    <div className="mt-3 line-clamp-2 text-sm leading-5 text-stone-800">{item.subject?.trim() || '（无主题）'}</div>
                    <div className="mt-3 text-xs text-stone-500">{item.identity_name} · {item.sender_email}</div>
                    <div className="mt-1 text-xs text-stone-400">{item.source === 'manual' ? '工作区邮件' : item.batch_task_name ?? '批量邮件'}</div>
                  </button>
                  <div className="mt-4 flex justify-end gap-2 border-t border-stone-100 pt-3">
                    {item.can_reschedule ? (
                      <button type="button" onClick={() => openReschedule(item)} className="ui-btn-secondary shadow-none">
                        <CalendarClock className="h-4 w-4" />
                        改期
                      </button>
                    ) : null}
                    <button type="button" onClick={() => setSelectedItem(item)} className="ui-btn-secondary shadow-none">
                      查看详情
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  </div>
                </article>
              ))}
            </div>

            <Pagination
              page={data.page}
              pageSize={pageSize}
              totalCount={data.total_count}
              onChange={handlePaginationChange}
              ariaLabel="发送计划分页"
              pageSizeOptions={[20, 50, 100]}
              unitLabel="封"
              itemLabel="封邮件"
              pageStatusPrefix="第 "
              focusTargetRef={listStartRef}
              disabled={loading || refreshing}
              className="mt-4 rounded-2xl border border-stone-200 bg-white px-4 py-3 shadow-sm"
            />
          </>
        )}
      </section>

      {selectedItem ? (
        <div
          className="fixed inset-0 z-[90] flex justify-end bg-stone-950/30 backdrop-blur-sm"
          onClick={detailLayer.onBackdropClick}
          onMouseDown={detailLayer.onBackdropMouseDown}
        >
          <aside
            className="h-full w-full max-w-lg overflow-y-auto border-l border-stone-200 bg-white p-6 shadow-2xl"
            aria-label="发送项详情"
            onClick={detailLayer.onContentClick}
            onMouseDown={detailLayer.onContentMouseDown}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="text-xs font-medium text-stone-400">发送项详情</div>
                <h2 className="mt-2 break-words text-lg font-semibold text-stone-900">{selectedItem.subject?.trim() || '（无主题）'}</h2>
              </div>
              <button
                type="button"
                onClick={closeDetails}
                className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 hover:bg-stone-50"
                aria-label="关闭发送项详情"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-5 rounded-2xl border border-stone-200 bg-stone-50 px-4 py-4">
              <DeliveryStatusBadge item={selectedItem} />
              <p className="mt-3 text-sm leading-6 text-stone-600">{selectedItem.status_description}</p>
              {selectedItem.last_error ? (
                <p className="mt-3 break-words text-sm leading-6 text-red-700">{selectedItem.last_error}</p>
              ) : null}
            </div>

            <dl className="mt-6 divide-y divide-stone-100 border-y border-stone-100">
              <div className="grid gap-2 py-4 sm:grid-cols-[120px_1fr]">
                <dt className="text-sm text-stone-500">计划时间</dt>
                <dd className="text-sm font-medium text-stone-900">{formatFullDateTime(selectedItem.scheduled_at ?? selectedItem.last_scheduled_at)} · {timeZone}</dd>
              </div>
              <div className="grid gap-2 py-4 sm:grid-cols-[120px_1fr]">
                <dt className="text-sm text-stone-500">收件人</dt>
                <dd className="break-words text-sm text-stone-900">{selectedItem.professor_name} · {selectedItem.professor_email ?? '未填写邮箱'}</dd>
              </div>
              <div className="grid gap-2 py-4 sm:grid-cols-[120px_1fr]">
                <dt className="text-sm text-stone-500">发件身份</dt>
                <dd className="break-words text-sm text-stone-900">{selectedItem.identity_name} · {selectedItem.sender_email}</dd>
              </div>
              <div className="grid gap-2 py-4 sm:grid-cols-[120px_1fr]">
                <dt className="text-sm text-stone-500">来源</dt>
                <dd className="text-sm text-stone-900">{selectedItem.source === 'manual' ? '工作区邮件' : selectedItem.batch_task_name ?? '批量邮件'}</dd>
              </div>
              <div className="grid gap-2 py-4 sm:grid-cols-[120px_1fr]">
                <dt className="text-sm text-stone-500">附件</dt>
                <dd className="inline-flex items-center gap-2 text-sm text-stone-900">
                  <Paperclip className="h-4 w-4 text-stone-400" />
                  {selectedItem.attachment_count > 0
                    ? `${selectedItem.attachment_count} 份 · ${formatFileSize(selectedItem.attachment_size_bytes)}`
                    : '未选择附件'}
                </dd>
              </div>
              <div className="grid gap-2 py-4 sm:grid-cols-[120px_1fr]">
                <dt className="text-sm text-stone-500">最近尝试</dt>
                <dd className="text-sm text-stone-900">{formatFullDateTime(selectedItem.last_send_attempt_at)}</dd>
              </div>
              <div className="grid gap-2 py-4 sm:grid-cols-[120px_1fr]">
                <dt className="text-sm text-stone-500">状态更新</dt>
                <dd className="text-sm text-stone-900">{formatFullDateTime(selectedItem.updated_at)}</dd>
              </div>
            </dl>

            <div className="mt-6 flex flex-wrap justify-end gap-3">
              {selectedItem.can_cancel ? (
                <button
                  type="button"
                  onClick={() => void handleCancel(selectedItem)}
                  disabled={actingTaskId === selectedItem.id}
                  className="ui-btn-danger"
                >
                  <Ban className="h-4 w-4" />
                  {selectedItem.source === 'manual' ? '取消定时' : '取消该封'}
                </button>
              ) : null}
              {selectedItem.can_restore ? (
                <button
                  type="button"
                  onClick={() => void handleRestore(selectedItem)}
                  disabled={actingTaskId === selectedItem.id}
                  className="ui-btn-secondary"
                >
                  <RotateCcw className="h-4 w-4" />
                  恢复发送
                </button>
              ) : null}
              {selectedItem.can_send_now ? (
                <button
                  type="button"
                  onClick={() => void handleSendNow(selectedItem)}
                  disabled={actingTaskId === selectedItem.id}
                  className="ui-btn-secondary"
                >
                  <Send className="h-4 w-4" />
                  立即发送
                </button>
              ) : null}
              <button type="button" onClick={() => openSource(selectedItem)} className="ui-btn-secondary">
                <ExternalLink className="h-4 w-4" />
                {selectedItem.source === 'manual' ? '打开工作区' : '打开所属批次'}
              </button>
              {selectedItem.can_reschedule ? (
                <button type="button" onClick={() => openReschedule(selectedItem)} className="ui-btn-primary">
                  <CalendarClock className="h-4 w-4" />
                  修改时间
                </button>
              ) : null}
            </div>
          </aside>
        </div>
      ) : null}

      {rescheduleItem ? (
        <div
          className="fixed inset-0 z-[95] flex items-center justify-center bg-stone-950/35 p-4 backdrop-blur-sm"
          onClick={rescheduleLayer.onBackdropClick}
          onMouseDown={rescheduleLayer.onBackdropMouseDown}
        >
          <div
            className="w-full max-w-md rounded-3xl border border-stone-200 bg-white p-6 shadow-2xl"
            onClick={rescheduleLayer.onContentClick}
            onMouseDown={rescheduleLayer.onContentMouseDown}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold text-stone-900">修改发送时间</h2>
                <p className="mt-2 text-sm leading-6 text-stone-500">{rescheduleItem.professor_name} · {rescheduleItem.professor_email ?? '未填写邮箱'}</p>
              </div>
              <button
                type="button"
                onClick={closeReschedule}
                className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 hover:bg-stone-50"
                aria-label="关闭修改发送时间弹层"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="mt-4 rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm text-stone-600">
              <div>发件身份：{rescheduleItem.identity_name} · {rescheduleItem.sender_email}</div>
              <div className="mt-1">当前计划：{formatFullDateTime(rescheduleItem.scheduled_at)}</div>
            </div>
            <label className="mt-5 block">
              <span className="mb-2 block text-sm font-medium text-stone-800">新的发送时间</span>
              <input
                type="datetime-local"
                value={rescheduleValue}
                min={rescheduleMinValue}
                onChange={(event) => setRescheduleValue(event.target.value)}
                className="form-input"
              />
            </label>
            <p className="mt-2 text-xs text-stone-500">新的发送时间必须晚于当前时间至少 1 分钟。</p>
            <div className="mt-6 flex flex-wrap justify-end gap-3">
              <button type="button" onClick={closeReschedule} disabled={actingTaskId === rescheduleItem.id} className="ui-btn-secondary">取消</button>
              <button type="button" onClick={() => void handleReschedule()} disabled={actingTaskId === rescheduleItem.id} className="ui-btn-primary">
                {actingTaskId === rescheduleItem.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <CalendarClock className="h-4 w-4" />}
                确认修改
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
};
