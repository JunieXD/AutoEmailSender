import {
  memo,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import clsx from 'clsx';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  Building2,
  Check,
  ChevronLeft,
  ChevronRight,
  Database,
  Eye,
  ExternalLink,
  FileSpreadsheet,
  FileWarning,
  Inbox,
  Loader2,
  Mail,
  RefreshCcw,
  Search,
  Square,
  SquareCheck,
  SquareMinus,
  Users,
  X,
} from 'lucide-react';
import { KeywordSearchScopeSelect } from '@/components/molecules/KeywordSearchScopeSelect';
import { MultiSelectFilter } from '@/components/molecules/MultiSelectFilter';
import { Pagination } from '@/components/molecules/Pagination';
import { SelectionToggleButton } from '@/components/molecules/SelectionToggleButton';
import { TopBarSelectMenu } from '@/components/atoms/TopBarSelectMenu';
import { useNotification } from '@/context/NotificationContext';
import {
  importCommunityMentors,
  listCommunityMentors,
  previewCommunityMentorImport,
} from '@/entities/community-mentor/api/communityMentors';
import {
  getCommunityMentorCatalogSessionSnapshot,
  requestCommunityMentorCatalog,
} from '@/entities/community-mentor/api/catalogCache';
import {
  getCommunityMentorPageSessionSnapshot,
  setCommunityMentorPageSessionSnapshot,
  type CommunityMentorSearchScope,
} from '@/lib/communityMentorPageState';
import { buildCommunityReportUrl } from '@/lib/communityMentorLinks';
import { openExternalHttpUrl } from '@/lib/externalUrls';
import { useDismissableLayerClick } from '@/lib/useDismissableLayerClick';
import { useDocumentScrollLock } from '@/lib/useDocumentScrollLock';
import {
  addFilteredCommunityUnitSelection,
  addVisibleRecordSelection,
  getVisibleRecordSelectionState,
  MAX_LOADED_COMMUNITY_MENTORS,
  MAX_SELECTED_COMMUNITY_MENTORS,
  MAX_SELECTED_COMMUNITY_UNITS,
} from '@/lib/communityMentorSelection';
import type {
  CommunityCatalogDTO,
  CommunityCatalogUnitDTO,
  CommunityComparisonCategoryDTO,
  CommunityFieldChoiceDTO,
  CommunityFieldComparisonDTO,
  CommunityFieldStateDTO,
  CommunityMentorComparisonDTO,
  CommunityMentorRecordDTO,
  CommunityLifecycleWarningStatusDTO,
  CommunityRecordsDTO,
} from '@/types';


const MAX_SELECTED_UNITS = MAX_SELECTED_COMMUNITY_UNITS;
const MAX_SELECTED_RECORDS = MAX_SELECTED_COMMUNITY_MENTORS;
const MAX_LOADED_RECORDS = MAX_LOADED_COMMUNITY_MENTORS;
const RECORDS_PER_PAGE = 100;
const DEFAULT_CATALOG_UNITS_PER_PAGE = 9;
const CATALOG_UNIT_PAGE_SIZE_OPTIONS = [9, 18, 36] as const;
const CATALOG_UNIT_SELECTOR_SCROLL_GAP_PX = 16;
const PREVIEW_RECORDS_PER_PAGE = 25;

const COMMUNITY_MENTOR_SEARCH_SCOPE_OPTIONS: ReadonlyArray<{
  value: CommunityMentorSearchScope;
  label: string;
}> = [
  { value: 'name', label: '姓名' },
  { value: 'email', label: '邮箱' },
  { value: 'organization', label: '学校与任职' },
  { value: 'title', label: '职称' },
  { value: 'research_direction', label: '研究方向' },
];
const DEFAULT_COMMUNITY_MENTOR_SEARCH_SCOPES =
  COMMUNITY_MENTOR_SEARCH_SCOPE_OPTIONS.map((option) => option.value);
const COMMUNITY_MENTOR_SEARCH_SCOPE_SET = new Set<CommunityMentorSearchScope>(
  DEFAULT_COMMUNITY_MENTOR_SEARCH_SCOPES,
);

const normalizeCommunityMentorSearchScopes = (
  values: CommunityMentorSearchScope[] | null | undefined,
) => {
  const normalized = (values ?? []).filter((value) =>
    COMMUNITY_MENTOR_SEARCH_SCOPE_SET.has(value),
  );
  return normalized.length > 0
    ? normalized
    : [...DEFAULT_COMMUNITY_MENTOR_SEARCH_SCOPES];
};

const haveSamePaths = (left: string[], right: string[]) =>
  left.length === right.length && left.every((path) => right.includes(path));

const isRecordSelectable = (item: CommunityMentorComparisonDTO) =>
  item.category !== 'retired_or_revoked' && !item.import_blocked;

const sortedUniqueValues = (values: Array<string | null | undefined>) =>
  Array.from(
    new Set(values.map((value) => value?.trim() ?? '').filter(Boolean)),
  ).sort((left, right) => left.localeCompare(right, 'zh-CN'));

const matchesSelectedValues = (
  selectedValues: string[],
  candidateValues: Array<string | null | undefined>,
) => {
  if (selectedValues.length === 0) {
    return true;
  }
  const candidateSet = new Set(
    candidateValues.map((value) => value?.trim() ?? '').filter(Boolean),
  );
  return selectedValues.some((value) => candidateSet.has(value));
};

const githubProfileUrl = (login: string) =>
  `https://github.com/${encodeURIComponent(login)}`;

type CatalogUnitEntry = {
  universityId: string;
  universityName: string;
  unit: CommunityCatalogUnitDTO;
};

const categoryMeta: Record<
  CommunityComparisonCategoryDTO,
  { label: string; className: string; description: string }
> = {
  new: {
    label: '未导入',
    className: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    description: '导入后将新增到本地。',
  },
  linked_unchanged: {
    label: '内容一致',
    className: 'border-stone-200 bg-stone-50 text-stone-600',
    description: '本地和社区资料相同。',
  },
  fill_available: {
    label: '可补充资料',
    className: 'border-sky-200 bg-sky-50 text-sky-700',
    description: '社区有资料可以补充到本地空白处。',
  },
  local_modified: {
    label: '本地有修改',
    className: 'border-violet-200 bg-violet-50 text-violet-700',
    description: '你修改过本地资料，默认保留本地内容。',
  },
  remote_modified: {
    label: '社区有更新',
    className: 'border-blue-200 bg-blue-50 text-blue-700',
    description: '社区资料在上次导入后更新过。',
  },
  conflict: {
    label: '内容不同',
    className: 'border-amber-200 bg-amber-50 text-amber-800',
    description: '本地与社区资料不同，可在预览中选择。',
  },
  archived_local: {
    label: '本地已归档',
    className: 'border-orange-200 bg-orange-50 text-orange-700',
    description: '导入不会自动恢复本地回收站记录。',
  },
  retired_or_revoked: {
    label: '社区已停用',
    className: 'border-red-200 bg-red-50 text-red-700',
    description: '社区已标记为退休、离职或撤销，不会自动改动本地记录。',
  },
};

const categoryOptions = Object.keys(categoryMeta) as CommunityComparisonCategoryDTO[];
const categoryOptionLabels = Object.fromEntries(
  categoryOptions.map((category) => [category, categoryMeta[category].label]),
);

const fieldStateMeta: Record<CommunityFieldStateDTO, { label: string; className: string }> = {
  new: { label: '将新增', className: 'text-emerald-700' },
  same: { label: '内容一致', className: 'text-stone-500' },
  fill_available: { label: '本地为空', className: 'text-sky-700' },
  local_only: { label: '社区为空', className: 'text-violet-700' },
  local_modified: { label: '本地有修改', className: 'text-violet-700' },
  remote_modified: { label: '社区有更新', className: 'text-blue-700' },
  conflict: { label: '内容不同', className: 'text-amber-700' },
};

const previewFieldStateOptions: CommunityFieldStateDTO[] = [
  'conflict',
  'remote_modified',
  'local_modified',
  'fill_available',
  'local_only',
  'new',
];
const previewFieldStateOptionLabels = Object.fromEntries(
  previewFieldStateOptions.map((state) => [state, fieldStateMeta[state].label]),
);

const getChangedFields = (item: CommunityMentorComparisonDTO) =>
  item.fields.filter((field) => field.state !== 'same');

const lifecycleLabels: Record<CommunityLifecycleWarningStatusDTO, string> = {
  active: '在职',
  retired: '已退休',
  departed: '已离职或调动',
  deceased: '已去世',
  stale: '信息过时',
  disputed: '信息有争议',
  removed: '已撤销',
  relocated: '已调动任职',
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

const formatShortDate = (value: string | null | undefined) => {
  if (!value) {
    return '未记录';
  }
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleDateString('zh-CN');
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

const normalizeSearchValues = (values: unknown[]) =>
  values
    .flatMap((value) => (Array.isArray(value) ? value : [value]))
    .filter((value) => value !== null && value !== undefined && value !== '')
    .map((value) => String(value).trim())
    .filter(Boolean)
    .join('\n')
    .toLocaleLowerCase();

const getComparisonSearchValues = (
  item: CommunityMentorComparisonDTO,
  fields: string[],
) => item.fields
  .filter((field) => fields.includes(field.field))
  .flatMap((field) => [field.local_value, field.community_value]);

const buildRecordSearchIndex = (
  item: CommunityMentorComparisonDTO,
): Record<CommunityMentorSearchScope, string> => ({
  name: normalizeSearchValues([
    item.record.name,
    item.local_professor_name,
    ...getComparisonSearchValues(item, ['name']),
  ]),
  email: normalizeSearchValues([
    item.record.email,
    ...item.record.contacts.map((contact) => contact.email),
    ...getComparisonSearchValues(item, ['email']),
  ]),
  organization: normalizeSearchValues([
    item.record.university,
    item.record.school,
    item.record.department,
    ...item.record.affiliations.flatMap((affiliation) => [
      affiliation.university,
      affiliation.school,
      affiliation.department,
    ]),
    ...getComparisonSearchValues(item, ['university', 'school', 'department']),
  ]),
  title: normalizeSearchValues([
    item.record.title,
    ...item.record.affiliations.map((affiliation) => affiliation.title),
    ...getComparisonSearchValues(item, ['title']),
  ]),
  research_direction: normalizeSearchValues([
    item.record.research_direction,
    ...getComparisonSearchValues(item, ['research_direction']),
  ]),
});

const openFeedbackForm = (
  record: CommunityMentorRecordDTO,
  notifySuccess: (title: string, description?: string) => unknown,
) => {
  openExternalHttpUrl(buildCommunityReportUrl(record));
  notifySuccess(
    '反馈页面已打开',
    '已自动填入导师信息。请选择问题，并补充正确信息和官网来源。',
  );
};

const ExternalTextLink = ({
  url,
  children,
  className,
}: {
  url: string;
  children: ReactNode;
  className?: string;
}) => (
  <a
    href={url}
    target="_blank"
    rel="noopener noreferrer"
    className={className}
    onClick={(event) => {
      event.preventDefault();
      openExternalHttpUrl(url);
    }}
  >
    {children}
  </a>
);

const ContributorLinks = ({
  contributors,
}: {
  contributors: CommunityMentorRecordDTO['contributors'];
}) => {
  if (contributors.length === 0) {
    return <span>暂无</span>;
  }
  return (
    <span className="inline-flex flex-wrap gap-x-1.5 gap-y-1">
      {contributors.map((contributor) => (
        <ExternalTextLink
          key={contributor.github_user_id}
          url={githubProfileUrl(contributor.github_login_at_submission)}
          className="font-medium text-primary underline decoration-primary/30 underline-offset-2 hover:decoration-primary"
        >
          @{contributor.github_login_at_submission}
        </ExternalTextLink>
      ))}
    </span>
  );
};

type CommunityMentorRecordCardProps = {
  item: CommunityMentorComparisonDTO;
  selected: boolean;
  onToggle: (recordId: string) => void;
  onOpenDetail: (item: CommunityMentorComparisonDTO) => void;
  onReport: (record: CommunityMentorRecordDTO) => void;
};

const CommunityMentorRecordCard = memo(({
  item,
  selected,
  onToggle,
  onOpenDetail,
  onReport,
}: CommunityMentorRecordCardProps) => {
  const meta = categoryMeta[item.category];
  const selectable = isRecordSelectable(item);
  const organization = [
    item.record.university,
    item.record.school,
    item.record.department,
  ].filter(Boolean).join(' · ');

  return (
    <article className="rounded-2xl border border-stone-200 px-3 py-2.5 transition hover:border-orange-200 hover:shadow-sm">
      <div className="flex items-stretch gap-3">
        <div className="flex shrink-0 items-center">
          <SelectionToggleButton
            label={`选择 ${item.record.name}`}
            disabled={!selectable}
            selected={selected}
            onToggle={() => onToggle(item.record.id)}
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="grid min-w-0 gap-x-5 gap-y-2 lg:grid-cols-[minmax(13rem,0.95fr)_minmax(16rem,1.15fr)_7.5rem] lg:items-center">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1.5">
                <h3 className="font-semibold text-stone-950">{item.record.name}</h3>
                {item.record.title ? <span className="text-sm text-stone-500">{item.record.title}</span> : null}
                <span title={meta.description} className={clsx('rounded-full border px-2 py-0.5 text-[11px] font-medium', meta.className)}>{meta.label}</span>
                {item.record.contacts.length > 1 ? <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[11px] text-blue-700">{item.record.contacts.length} 个邮箱</span> : null}
                {item.record.affiliations.length > 1 ? <span className="rounded-full bg-violet-50 px-2 py-0.5 text-[11px] text-violet-700">{item.record.affiliations.length} 个任职</span> : null}
              </div>
              <p className="mt-1 truncate text-xs text-stone-500" title={organization}>{organization || '暂无任职信息'}</p>
            </div>
            <div className="min-w-0 text-xs text-stone-500">
              <div className="flex min-w-0 items-center gap-1 text-sm text-stone-700">
                <Mail className="h-3.5 w-3.5 shrink-0 text-stone-400" />
                <span className="truncate" title={item.record.email}>{item.record.email}</span>
              </div>
              <p className="mt-1 truncate" title={item.record.research_direction ?? ''}>
                {item.record.research_direction || '研究方向暂无'}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span>核验：{formatShortDate(item.record.last_verified_at)}</span>
                <span className="inline-flex flex-wrap items-center gap-1">
                  贡献者：<ContributorLinks contributors={item.record.contributors} />
                </span>
                <button type="button" className="inline-flex items-center gap-1 font-medium text-primary hover:underline" onClick={() => openExternalHttpUrl(item.record.source_url)}>
                  来源页 <ExternalLink className="h-3 w-3" />
                </button>
                <button type="button" className="inline-flex items-center gap-1 font-medium text-amber-700 hover:underline" onClick={() => onReport(item.record)}>
                  反馈错误 <ExternalLink className="h-3 w-3" />
                </button>
              </div>
            </div>
            <div className="flex items-center lg:justify-end">
              <button type="button" className="ui-btn-secondary min-h-8 justify-center px-2.5 py-1 text-xs" onClick={() => onOpenDetail(item)}>
                <Eye className="h-3.5 w-3.5" /> 查看详情
              </button>
            </div>
          </div>
          {item.import_blocked ? (
            <div className="mt-2 rounded-xl border border-red-200 bg-red-50 px-3 py-1.5 text-xs leading-5 text-red-800">
              <AlertTriangle className="mr-1 inline h-3.5 w-3.5" />
              <strong>暂不可导入：</strong>{item.import_blocked_reason ?? '请先处理这条导师记录的冲突。'}
            </div>
          ) : item.identity_conflict ? (
            <div className="mt-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs leading-5 text-amber-800">
              <AlertTriangle className="mr-1 inline h-3.5 w-3.5" />{item.match_reason}
            </div>
          ) : null}
        </div>
      </div>
    </article>
  );
});

CommunityMentorRecordCard.displayName = 'CommunityMentorRecordCard';

const DetailValue = ({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) => (
  <div className={clsx('rounded-2xl border border-stone-200 bg-white px-4 py-3', className)}>
    <div className="text-xs font-medium text-stone-500">{label}</div>
    <div className="mt-1.5 whitespace-pre-wrap break-words text-sm leading-6 text-stone-800">
      {children}
    </div>
  </div>
);

const CommunityMentorDetailDialog = ({
  item,
  onClose,
  onReport,
}: {
  item: CommunityMentorComparisonDTO | null;
  onClose: () => void;
  onReport: (record: CommunityMentorRecordDTO) => void;
}) => {
  const open = item !== null;
  useDocumentScrollLock(open);
  const {
    onBackdropClick,
    onBackdropMouseDown,
    onContentClick,
    onContentMouseDown,
  } = useDismissableLayerClick(onClose);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, open]);

  if (!item) {
    return null;
  }

  const { record } = item;
  const meta = categoryMeta[item.category];
  const organization = [record.university, record.school, record.department]
    .filter(Boolean)
    .join(' · ');

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`导师详情：${record.name}`}
      className="fixed inset-0 z-[90] flex items-center justify-center bg-stone-950/40 p-4 backdrop-blur-sm"
      onClick={onBackdropClick}
      onMouseDown={onBackdropMouseDown}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-[30px] border border-white/70 bg-stone-50 shadow-2xl"
        onClick={onContentClick}
        onMouseDown={onContentMouseDown}
      >
        <div className="flex items-start justify-between gap-4 border-b border-stone-200 bg-white px-6 py-5">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold text-stone-950">{record.name}</h2>
              {record.title ? <span className="text-sm text-stone-500">{record.title}</span> : null}
              <span className={clsx('rounded-full border px-2 py-0.5 text-xs font-medium', meta.className)}>
                {meta.label}
              </span>
              <span className="rounded-full border border-stone-200 bg-stone-50 px-2 py-0.5 text-xs font-medium text-stone-600">
                {lifecycleLabels[record.status]}
              </span>
            </div>
            <p className="mt-1 truncate text-sm text-stone-500">{organization || '暂无任职信息'}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl p-2 text-stone-500 transition hover:bg-stone-100 hover:text-stone-900"
            aria-label="关闭导师详情"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5 md:p-6">
          <section>
            <h3 className="text-sm font-semibold text-stone-900">基本信息</h3>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <DetailValue label="主要邮箱">{record.email || '暂无'}</DetailValue>
              <DetailValue label="职称">{record.title || '暂无'}</DetailValue>
              <DetailValue label="任职机构" className="md:col-span-2">
                {organization || '暂无'}
              </DetailValue>
              <DetailValue label="研究方向" className="md:col-span-2">
                {record.research_direction || '暂无'}
              </DetailValue>
            </div>
          </section>

          <section>
            <h3 className="text-sm font-semibold text-stone-900">代表论文</h3>
            <div className="mt-3 rounded-2xl border border-stone-200 bg-white px-4 py-3">
              {record.recent_papers.length > 0 ? (
                <ol className="list-decimal space-y-2 pl-5 text-sm leading-6 text-stone-700">
                  {record.recent_papers.map((paper, index) => (
                    <li key={`${paper}-${index}`}>{paper}</li>
                  ))}
                </ol>
              ) : (
                <p className="text-sm text-stone-500">暂无</p>
              )}
            </div>
          </section>

          <section className="grid gap-5 lg:grid-cols-2">
            <div>
              <h3 className="text-sm font-semibold text-stone-900">全部邮箱</h3>
              <div className="mt-3 space-y-2">
                {record.contacts.length > 0 ? record.contacts.map((contact) => (
                  <div key={contact.email} className="rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="break-all font-medium text-stone-800">{contact.email}</span>
                      {contact.is_primary ? <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">主要</span> : null}
                    </div>
                    <ExternalTextLink url={contact.source_url} className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
                      查看来源 <ExternalLink className="h-3 w-3" />
                    </ExternalTextLink>
                  </div>
                )) : <p className="rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-500">暂无</p>}
              </div>
            </div>
            <div>
              <h3 className="text-sm font-semibold text-stone-900">当前任职</h3>
              <div className="mt-3 space-y-2">
                {record.affiliations.length > 0 ? record.affiliations.map((affiliation) => (
                  <div key={affiliation.id} className="rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-stone-800">
                        {[affiliation.university, affiliation.school, affiliation.department].filter(Boolean).join(' · ') || '暂无'}
                      </span>
                      {affiliation.is_primary ? <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">主要</span> : null}
                    </div>
                    <p className="mt-1 text-xs text-stone-500">{affiliation.title || '暂无职称'}</p>
                    <ExternalTextLink url={affiliation.source_url} className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
                      查看来源 <ExternalLink className="h-3 w-3" />
                    </ExternalTextLink>
                  </div>
                )) : <p className="rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-500">暂无</p>}
              </div>
            </div>
          </section>

          <section>
            <h3 className="text-sm font-semibold text-stone-900">来源与核验</h3>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <DetailValue label="导师主页">
                {record.profile_url ? (
                  <ExternalTextLink url={record.profile_url} className="inline-flex items-center gap-1 font-medium text-primary hover:underline">
                    打开导师主页 <ExternalLink className="h-3.5 w-3.5" />
                  </ExternalTextLink>
                ) : '暂无'}
              </DetailValue>
              <DetailValue label="信息来源">
                <ExternalTextLink url={record.source_url} className="inline-flex items-center gap-1 font-medium text-primary hover:underline">
                  打开来源 <ExternalLink className="h-3.5 w-3.5" />
                </ExternalTextLink>
              </DetailValue>
              <DetailValue label="最后核验">{formatDate(record.last_verified_at)}</DetailValue>
              <DetailValue label="贡献者"><ContributorLinks contributors={record.contributors} /></DetailValue>
            </div>
          </section>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 bg-white px-6 py-4">
          <button type="button" className="ui-btn-secondary" onClick={() => onReport(record)}>
            反馈错误 <ExternalLink className="h-3.5 w-3.5" />
          </button>
          <button type="button" className="ui-btn-primary" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
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
  const communityIsEmpty =
    field.community_value === null ||
    field.community_value === undefined ||
    field.community_value === '' ||
    (Array.isArray(field.community_value) && field.community_value.length === 0);
  return (
    <div className="grid gap-1.5 border-t border-stone-100 px-2 py-2 md:grid-cols-[6.75rem_minmax(0,1fr)_minmax(0,1fr)] md:items-stretch">
      <div className="flex items-center justify-between gap-2 px-1 md:flex-col md:items-start md:justify-center">
        <div className="text-xs font-semibold text-stone-900">{field.label}</div>
        <div className={clsx('text-xs font-medium', fieldStateMeta[field.state].className)}>
          {fieldStateMeta[field.state].label}
        </div>
      </div>
      <button
        type="button"
        aria-label={`保留本地${field.label}`}
        aria-pressed={choice === 'local'}
        disabled={!allowLocalChoice}
        onClick={() => onChange('local')}
        className={clsx(
          'min-h-[3.25rem] min-w-0 rounded-lg border px-2.5 py-1.5 text-left transition',
          choice === 'local'
            ? 'border-violet-300 bg-violet-50 ring-2 ring-violet-100'
            : 'border-stone-200 bg-stone-50',
          !allowLocalChoice && 'cursor-not-allowed opacity-50',
        )}
      >
        <span className="flex items-center gap-1 text-[11px] font-semibold text-stone-500">
          {choice === 'local' ? <Check className="h-3 w-3" /> : null}
          {allowLocalChoice ? '保留本地' : '本地无记录'}
        </span>
        <span className="mt-0.5 block max-h-10 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-5 text-stone-800">
          {formatFieldValue(field.local_value)}
        </span>
      </button>
      <button
        type="button"
        aria-label={`采用社区${field.label}`}
        aria-pressed={choice === 'community'}
        onClick={() => onChange('community')}
        className={clsx(
          'min-h-[3.25rem] min-w-0 rounded-lg border px-2.5 py-1.5 text-left transition',
          choice === 'community'
            ? 'border-primary/40 bg-primary/5 ring-2 ring-primary/10'
            : 'border-stone-200 bg-stone-50',
        )}
      >
        <span className="flex items-center gap-1 text-[11px] font-semibold text-primary">
          {choice === 'community' ? <Check className="h-3 w-3" /> : null}
          采用社区
        </span>
        <span
          className={clsx(
            'mt-0.5 block max-h-10 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-5',
            communityIsEmpty ? 'font-medium text-amber-700' : 'text-stone-800',
          )}
        >
          {communityIsEmpty
            ? '空值（将清空本地内容）'
            : formatFieldValue(field.community_value)}
        </span>
      </button>
    </div>
  );
};

export const CommunityMentorsPage = () => {
  const { notifyError, notifySuccess, notifyWarning } = useNotification();
  const [initialPageSnapshot] = useState(() => {
    const snapshot = getCommunityMentorPageSessionSnapshot();
    const cachedCatalog = getCommunityMentorCatalogSessionSnapshot();
    if (
      snapshot?.datasetVersion &&
      cachedCatalog &&
      snapshot.datasetVersion !== cachedCatalog.dataset_version
    ) {
      return null;
    }
    return snapshot;
  });
  const [catalog, setCatalog] = useState<CommunityCatalogDTO | null>(
    getCommunityMentorCatalogSessionSnapshot,
  );
  const [catalogLoading, setCatalogLoading] = useState(
    () => getCommunityMentorCatalogSessionSnapshot() === null,
  );
  const [catalogRefreshing, setCatalogRefreshing] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogUniversityFilters, setCatalogUniversityFilters] = useState<string[]>(
    () => initialPageSnapshot?.catalogUniversityFilters ?? [],
  );
  const [catalogUnitFilters, setCatalogUnitFilters] = useState<string[]>(
    () => initialPageSnapshot?.catalogUnitFilters ?? [],
  );
  const [catalogUnitPage, setCatalogUnitPage] = useState(
    () => initialPageSnapshot?.catalogUnitPage ?? 1,
  );
  const [catalogUnitPageSize, setCatalogUnitPageSize] = useState(
    () => initialPageSnapshot?.catalogUnitPageSize ?? DEFAULT_CATALOG_UNITS_PER_PAGE,
  );
  const [selectedUnitPaths, setSelectedUnitPaths] = useState<string[]>(
    () => initialPageSnapshot?.selectedUnitPaths ?? [],
  );
  const [loadedUnitPaths, setLoadedUnitPaths] = useState<string[] | null>(
    () => initialPageSnapshot?.loadedUnitPaths ?? null,
  );
  const [recordsPayload, setRecordsPayload] = useState<CommunityRecordsDTO | null>(
    () => initialPageSnapshot?.recordsPayload ?? null,
  );
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordKeyword, setRecordKeyword] = useState(
    () => initialPageSnapshot?.recordKeyword ?? '',
  );
  const [recordSearchScopes, setRecordSearchScopes] = useState<
    CommunityMentorSearchScope[]
  >(() => normalizeCommunityMentorSearchScopes(initialPageSnapshot?.recordSearchScopes));
  const [recordUniversityFilters, setRecordUniversityFilters] = useState<string[]>(
    () => initialPageSnapshot?.recordUniversityFilters ?? [],
  );
  const [recordSchoolFilters, setRecordSchoolFilters] = useState<string[]>(
    () => initialPageSnapshot?.recordSchoolFilters ?? [],
  );
  const [recordDepartmentFilters, setRecordDepartmentFilters] = useState<string[]>(
    () => initialPageSnapshot?.recordDepartmentFilters ?? [],
  );
  const [recordTitleFilters, setRecordTitleFilters] = useState<string[]>(
    () => initialPageSnapshot?.recordTitleFilters ?? [],
  );
  const [categoryFilters, setCategoryFilters] = useState<
    CommunityComparisonCategoryDTO[]
  >(() => initialPageSnapshot?.categoryFilters ?? []);
  const [recordPage, setRecordPage] = useState(
    () => initialPageSnapshot?.recordPage ?? 1,
  );
  const [selectedRecordIds, setSelectedRecordIds] = useState<string[]>(
    () => initialPageSnapshot?.selectedRecordIds ?? [],
  );
  const [detailRecord, setDetailRecord] = useState<CommunityMentorComparisonDTO | null>(null);
  const [previewPayload, setPreviewPayload] = useState<CommunityRecordsDTO | null>(
    () => initialPageSnapshot?.previewPayload ?? null,
  );
  const [previewPage, setPreviewPage] = useState(
    () => initialPageSnapshot?.previewPage ?? 1,
  );
  const [previewKeyword, setPreviewKeyword] = useState(
    () => initialPageSnapshot?.previewKeyword ?? '',
  );
  const [previewSearchScopes, setPreviewSearchScopes] = useState<
    CommunityMentorSearchScope[]
  >(() => normalizeCommunityMentorSearchScopes(initialPageSnapshot?.previewSearchScopes));
  const [previewCategoryFilters, setPreviewCategoryFilters] = useState<
    CommunityComparisonCategoryDTO[]
  >(() => initialPageSnapshot?.previewCategoryFilters ?? []);
  const [previewFieldStateFilters, setPreviewFieldStateFilters] = useState<
    CommunityFieldStateDTO[]
  >(() => initialPageSnapshot?.previewFieldStateFilters ?? []);
  const [previewFieldFilters, setPreviewFieldFilters] = useState<string[]>(
    () => initialPageSnapshot?.previewFieldFilters ?? [],
  );
  const [previewOnlyUnconfirmed, setPreviewOnlyUnconfirmed] = useState(
    () => initialPageSnapshot?.previewOnlyUnconfirmed ?? false,
  );
  const [previewBulkField, setPreviewBulkField] = useState<string | null>(
    () => initialPageSnapshot?.previewBulkField ?? null,
  );
  const [previewLoading, setPreviewLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [fieldChoices, setFieldChoices] = useState<
    Record<string, Record<string, CommunityFieldChoiceDTO>>
  >(() => initialPageSnapshot?.fieldChoices ?? {});
  const [identityConfirmations, setIdentityConfirmations] = useState<Record<string, boolean>>(
    () => initialPageSnapshot?.identityConfirmations ?? {},
  );
  const previewScrollRef = useRef<HTMLDivElement | null>(null);
  const catalogUnitSelectorRef = useRef<HTMLElement | null>(null);
  const recordListRef = useRef<HTMLDivElement | null>(null);
  const bulkSelectionTimerRef = useRef<number | null>(null);
  useDocumentScrollLock(previewPayload !== null);

  const scrollToCatalogUnitSelector = useCallback(() => {
    const selector = catalogUnitSelectorRef.current;
    if (!selector) {
      return;
    }

    const headerBottom = document
      .querySelector<HTMLElement>('[data-app-header="true"]')
      ?.getBoundingClientRect().bottom ?? 0;
    selector.style.scrollMarginTop = `${Math.max(0, headerBottom) + CATALOG_UNIT_SELECTOR_SCROLL_GAP_PX}px`;
    try {
      selector.focus({ preventScroll: true });
    } catch {
      selector.focus();
    }
    selector.scrollIntoView?.({ behavior: 'auto', block: 'start' });
  }, []);

  const beginBulkRecordSelection = useCallback(() => {
    const recordList = recordListRef.current;
    if (!recordList) {
      return;
    }
    recordList.classList.add('is-bulk-selecting');
    if (bulkSelectionTimerRef.current !== null) {
      window.clearTimeout(bulkSelectionTimerRef.current);
    }
    bulkSelectionTimerRef.current = window.setTimeout(() => {
      recordList.classList.remove('is-bulk-selecting');
      bulkSelectionTimerRef.current = null;
    }, 50);
  }, []);

  const openRecordDetail = useCallback((item: CommunityMentorComparisonDTO) => {
    setDetailRecord(item);
  }, []);
  const reportRecord = useCallback((record: CommunityMentorRecordDTO) => {
    openFeedbackForm(record, notifySuccess);
  }, [notifySuccess]);

  useEffect(() => () => {
    if (bulkSelectionTimerRef.current !== null) {
      window.clearTimeout(bulkSelectionTimerRef.current);
    }
    recordListRef.current?.classList.remove('is-bulk-selecting');
  }, []);

  useEffect(() => {
    setCommunityMentorPageSessionSnapshot({
      datasetVersion:
        catalog?.dataset_version ??
        recordsPayload?.dataset_version ??
        previewPayload?.dataset_version ??
        null,
      catalogUniversityFilters,
      catalogUnitFilters,
      catalogUnitPage,
      catalogUnitPageSize,
      selectedUnitPaths,
      loadedUnitPaths,
      recordsPayload,
      recordKeyword,
      recordSearchScopes,
      recordUniversityFilters,
      recordSchoolFilters,
      recordDepartmentFilters,
      recordTitleFilters,
      categoryFilters,
      recordPage,
      selectedRecordIds,
      previewPayload,
      previewPage,
      previewKeyword,
      previewSearchScopes,
      previewCategoryFilters,
      previewFieldStateFilters,
      previewFieldFilters,
      previewOnlyUnconfirmed,
      previewBulkField,
      fieldChoices,
      identityConfirmations,
    });
  }, [
    catalog?.dataset_version,
    catalogUnitFilters,
    catalogUnitPage,
    catalogUnitPageSize,
    catalogUniversityFilters,
    categoryFilters,
    fieldChoices,
    identityConfirmations,
    loadedUnitPaths,
    previewPage,
    previewPayload,
    previewBulkField,
    previewCategoryFilters,
    previewFieldFilters,
    previewFieldStateFilters,
    previewKeyword,
    previewOnlyUnconfirmed,
    previewSearchScopes,
    recordDepartmentFilters,
    recordKeyword,
    recordPage,
    recordSchoolFilters,
    recordSearchScopes,
    recordTitleFilters,
    recordUniversityFilters,
    recordsPayload,
    selectedRecordIds,
    selectedUnitPaths,
  ]);

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
          const currentPageDatasetVersion =
            previous?.dataset_version ??
            getCommunityMentorPageSessionSnapshot()?.datasetVersion;
          if (
            currentPageDatasetVersion &&
            currentPageDatasetVersion !== nextCatalog.dataset_version
          ) {
            setCatalogUniversityFilters([]);
            setCatalogUnitFilters([]);
            setCatalogUnitPage(1);
            setSelectedUnitPaths([]);
            setLoadedUnitPaths(null);
            setRecordsPayload(null);
            setRecordKeyword('');
            setRecordSearchScopes([...DEFAULT_COMMUNITY_MENTOR_SEARCH_SCOPES]);
            setRecordUniversityFilters([]);
            setRecordSchoolFilters([]);
            setRecordDepartmentFilters([]);
            setRecordTitleFilters([]);
            setCategoryFilters([]);
            setSelectedRecordIds([]);
            setPreviewPayload(null);
            setPreviewPage(1);
            setPreviewKeyword('');
            setPreviewSearchScopes([...DEFAULT_COMMUNITY_MENTOR_SEARCH_SCOPES]);
            setPreviewCategoryFilters([]);
            setPreviewFieldStateFilters([]);
            setPreviewFieldFilters([]);
            setPreviewOnlyUnconfirmed(false);
            setPreviewBulkField(null);
            setFieldChoices({});
            setIdentityConfirmations({});
            setRecordPage(1);
          }
          return nextCatalog;
        });
        if (nextCatalog.warning && announceResult) {
          notifyWarning('正在使用缓存数据', nextCatalog.warning);
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
      const sessionCatalog = getCommunityMentorCatalogSessionSnapshot();
      if (sessionCatalog) {
        void loadCatalog(true, false);
        return;
      }

      const initialCatalog = await loadCatalog(false, false);
      if (initialCatalog?.source === 'cache') {
        void loadCatalog(true, false);
      }
    };

    void bootstrapCatalog();
  }, [loadCatalog]);

  const catalogUnitEntries = useMemo<CatalogUnitEntry[]>(
    () => (catalog?.universities ?? [])
      .flatMap((university) => university.units.map((unit) => ({
        universityId: university.id,
        universityName: university.name,
        unit,
      })))
      .sort((left, right) => {
        const universityOrder = left.universityName.localeCompare(
          right.universityName,
          'zh-CN',
        );
        return universityOrder || left.unit.name.localeCompare(right.unit.name, 'zh-CN');
      }),
    [catalog],
  );
  const catalogUniversityOptions = useMemo(
    () => [...(catalog?.universities ?? [])]
      .sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'))
      .map((university) => university.id),
    [catalog],
  );
  const catalogUniversityOptionLabels = useMemo(
    () => Object.fromEntries(
      (catalog?.universities ?? []).map((university) => [
        university.id,
        university.name,
      ]),
    ),
    [catalog],
  );
  const catalogUnitOptions = useMemo(
    () => catalogUnitEntries
      .filter((entry) => (
        catalogUniversityFilters.length === 0 ||
        catalogUniversityFilters.includes(entry.universityId)
      ))
      .map((entry) => entry.unit.path),
    [catalogUnitEntries, catalogUniversityFilters],
  );
  const catalogUnitOptionLabels = useMemo(
    () => Object.fromEntries(catalogUnitEntries.map((entry) => [
      entry.unit.path,
      `${entry.universityName} · ${entry.unit.name}`,
    ])),
    [catalogUnitEntries],
  );
  const filteredCatalogUnits = useMemo(
    () => catalogUnitEntries.filter((entry) => (
      (catalogUniversityFilters.length === 0 ||
        catalogUniversityFilters.includes(entry.universityId)) &&
      (catalogUnitFilters.length === 0 || catalogUnitFilters.includes(entry.unit.path))
    )),
    [catalogUnitEntries, catalogUnitFilters, catalogUniversityFilters],
  );
  const totalCatalogUnitPages = Math.max(
    1,
    Math.ceil(filteredCatalogUnits.length / catalogUnitPageSize),
  );
  const currentCatalogUnitPage = Math.min(catalogUnitPage, totalCatalogUnitPages);
  const paginatedCatalogUnits = filteredCatalogUnits.slice(
    (currentCatalogUnitPage - 1) * catalogUnitPageSize,
    currentCatalogUnitPage * catalogUnitPageSize,
  );

  const updateCatalogUniversityFilters = (nextValues: string[]) => {
    const allowedUniversityIds = new Set(
      nextValues.length > 0 ? nextValues : catalogUniversityOptions,
    );
    const allowedUnitPaths = new Set(
      catalogUnitEntries
        .filter((entry) => allowedUniversityIds.has(entry.universityId))
        .map((entry) => entry.unit.path),
    );
    setCatalogUniversityFilters(nextValues);
    setCatalogUnitFilters((current) => current.filter((path) => allowedUnitPaths.has(path)));
    setCatalogUnitPage(1);
  };

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
  const filteredCatalogUnitPaths = useMemo(
    () => filteredCatalogUnits.map((entry) => entry.unit.path),
    [filteredCatalogUnits],
  );
  const {
    selectedVisibleCount: selectedFilteredUnitCount,
    allVisibleSelected: allFilteredUnitsSelected,
    partiallyVisibleSelected: partiallyFilteredUnitsSelected,
  } = getVisibleRecordSelectionState(selectedUnitPaths, filteredCatalogUnitPaths);

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
      setRecordKeyword('');
      setRecordUniversityFilters([]);
      setRecordSchoolFilters([]);
      setRecordDepartmentFilters([]);
      setRecordTitleFilters([]);
      setCategoryFilters([]);
      setSelectedRecordIds([]);
      setPreviewPayload(null);
      setPreviewPage(1);
      setPreviewKeyword('');
      setPreviewSearchScopes([...DEFAULT_COMMUNITY_MENTOR_SEARCH_SCOPES]);
      setPreviewCategoryFilters([]);
      setPreviewFieldStateFilters([]);
      setPreviewFieldFilters([]);
      setPreviewOnlyUnconfirmed(false);
      setPreviewBulkField(null);
      setFieldChoices({});
      setIdentityConfirmations({});
      setRecordPage(1);
      if (result.warning) {
        notifyWarning('学院数据来自缓存', result.warning);
      }
    } catch (error) {
      notifyError('加载导师失败', getErrorMessage(error, '无法加载导师数据'));
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

  const toggleFilteredUnits = () => {
    setSelectedUnitPaths((current) => {
      if (allFilteredUnitsSelected) {
        const filteredPathSet = new Set(filteredCatalogUnitPaths);
        return current.filter((path) => !filteredPathSet.has(path));
      }
      const result = addFilteredCommunityUnitSelection(
        current,
        catalogUnitEntries.map((entry) => ({
          id: entry.unit.path,
          recordCount: entry.unit.record_count,
        })),
        filteredCatalogUnits.map((entry) => ({
          id: entry.unit.path,
          recordCount: entry.unit.record_count,
        })),
      );
      const omittedCount = result.omittedByUnitLimit + result.omittedByRecordLimit;
      if (omittedCount > 0) {
        const reasons = [
          result.omittedByUnitLimit > 0
            ? `${result.omittedByUnitLimit} 个超过 ${MAX_SELECTED_UNITS} 个学院上限`
            : null,
          result.omittedByRecordLimit > 0
            ? `${result.omittedByRecordLimit} 个会超过 ${MAX_LOADED_RECORDS} 位导师上限`
            : null,
        ].filter(Boolean).join('，');
        notifyWarning(
          '已选择能加入的学院',
          `按当前显示顺序完成选择，跳过 ${omittedCount} 个学院：${reasons}。`,
        );
      }
      return result.unitIds;
    });
  };

  const recordUniversityOptions = useMemo(
    () => sortedUniqueValues((recordsPayload?.records ?? []).flatMap((item) => [
      item.record.university,
      ...item.record.affiliations.map((affiliation) => affiliation.university),
    ])),
    [recordsPayload],
  );
  const recordSchoolOptions = useMemo(
    () => sortedUniqueValues((recordsPayload?.records ?? []).flatMap((item) => [
      item.record.school,
      ...item.record.affiliations.map((affiliation) => affiliation.school),
    ])),
    [recordsPayload],
  );
  const recordDepartmentOptions = useMemo(
    () => sortedUniqueValues((recordsPayload?.records ?? []).flatMap((item) => [
      item.record.department,
      ...item.record.affiliations.map((affiliation) => affiliation.department),
    ])),
    [recordsPayload],
  );
  const recordTitleOptions = useMemo(
    () => sortedUniqueValues((recordsPayload?.records ?? []).flatMap((item) => [
      item.record.title,
      ...item.record.affiliations.map((affiliation) => affiliation.title),
    ])),
    [recordsPayload],
  );
  const recordSearchIndexById = useMemo(
    () => new Map(
      (recordsPayload?.records ?? []).map((item) => [
        item.record.id,
        buildRecordSearchIndex(item),
      ]),
    ),
    [recordsPayload],
  );

  const visibleRecords = useMemo(() => {
    const keyword = recordKeyword.trim().toLocaleLowerCase();
    return (recordsPayload?.records ?? []).filter((item) => {
      if (categoryFilters.length > 0 && !categoryFilters.includes(item.category)) {
        return false;
      }
      const affiliations = item.record.affiliations;
      return (
        matchesSelectedValues(recordUniversityFilters, [
          item.record.university,
          ...affiliations.map((affiliation) => affiliation.university),
        ]) &&
        matchesSelectedValues(recordSchoolFilters, [
          item.record.school,
          ...affiliations.map((affiliation) => affiliation.school),
        ]) &&
        matchesSelectedValues(recordDepartmentFilters, [
          item.record.department,
          ...affiliations.map((affiliation) => affiliation.department),
        ]) &&
        matchesSelectedValues(recordTitleFilters, [
          item.record.title,
          ...affiliations.map((affiliation) => affiliation.title),
        ]) &&
        (
          !keyword ||
          recordSearchScopes.some((scope) =>
            (recordSearchIndexById.get(item.record.id)?.[scope] ?? '').includes(keyword),
          )
        )
      );
    });
  }, [
    categoryFilters,
    recordDepartmentFilters,
    recordKeyword,
    recordSearchScopes,
    recordSchoolFilters,
    recordTitleFilters,
    recordUniversityFilters,
    recordSearchIndexById,
    recordsPayload,
  ]);

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
  const previewFieldEntries = useMemo(() => {
    const labels = new Map<string, string>();
    (previewPayload?.records ?? []).forEach((item) => {
      getChangedFields(item).forEach((field) => {
        if (!labels.has(field.field)) {
          labels.set(field.field, field.label);
        }
      });
    });
    return Array.from(labels, ([field, label]) => ({ field, label }));
  }, [previewPayload]);
  const previewFieldOptions = useMemo(
    () => previewFieldEntries.map((entry) => entry.field),
    [previewFieldEntries],
  );
  const previewFieldOptionLabels = useMemo(
    () => Object.fromEntries(
      previewFieldEntries.map((entry) => [entry.field, entry.label]),
    ),
    [previewFieldEntries],
  );
  const previewBulkFieldOptions = useMemo(
    () => previewFieldEntries.map((entry) => ({
      value: entry.field,
      label: entry.label,
    })),
    [previewFieldEntries],
  );
  const previewSearchIndexById = useMemo(
    () => new Map(
      (previewPayload?.records ?? []).map((item) => [
        item.record.id,
        buildRecordSearchIndex(item),
      ]),
    ),
    [previewPayload],
  );
  const filteredPreviewRecords = useMemo(() => {
    const keyword = previewKeyword.trim().toLocaleLowerCase();
    return (previewPayload?.records ?? []).filter((item) => {
      if (
        previewCategoryFilters.length > 0 &&
        !previewCategoryFilters.includes(item.category)
      ) {
        return false;
      }
      if (
        previewOnlyUnconfirmed &&
        (!item.identity_conflict || identityConfirmations[item.record.id])
      ) {
        return false;
      }
      if (
        keyword &&
        !previewSearchScopes.some((scope) =>
          (previewSearchIndexById.get(item.record.id)?.[scope] ?? '').includes(keyword),
        )
      ) {
        return false;
      }

      const matchingFields = getChangedFields(item).filter((field) => (
        previewFieldFilters.length === 0 || previewFieldFilters.includes(field.field)
      ));
      if (previewFieldFilters.length > 0 && matchingFields.length === 0) {
        return false;
      }
      if (
        previewFieldStateFilters.length > 0 &&
        !matchingFields.some((field) => previewFieldStateFilters.includes(field.state))
      ) {
        return false;
      }
      return true;
    });
  }, [
    identityConfirmations,
    previewCategoryFilters,
    previewFieldFilters,
    previewFieldStateFilters,
    previewKeyword,
    previewOnlyUnconfirmed,
    previewPayload,
    previewSearchIndexById,
    previewSearchScopes,
  ]);
  const previewFieldCounts = useMemo(() => {
    const counts = new Map<string, number>();
    (previewPayload?.records ?? []).forEach((item) => {
      getChangedFields(item).forEach((field) => {
        counts.set(field.field, (counts.get(field.field) ?? 0) + 1);
      });
    });
    return counts;
  }, [previewPayload]);
  const previewUnconfirmedCount = useMemo(
    () => (previewPayload?.records ?? []).filter(
      (item) => item.identity_conflict && !identityConfirmations[item.record.id],
    ).length,
    [identityConfirmations, previewPayload],
  );
  const previewHasFilters = Boolean(
    previewKeyword ||
    previewCategoryFilters.length > 0 ||
    previewFieldStateFilters.length > 0 ||
    previewFieldFilters.length > 0 ||
    previewOnlyUnconfirmed,
  );
  const previewBulkEligibleRecords = useMemo(
    () => previewBulkField
      ? filteredPreviewRecords.filter((item) =>
          getChangedFields(item).some((field) => field.field === previewBulkField),
        )
      : [],
    [filteredPreviewRecords, previewBulkField],
  );
  const previewBulkLocalEligibleCount = useMemo(
    () => previewBulkEligibleRecords.filter(
      (item) => item.local_professor_id !== null,
    ).length,
    [previewBulkEligibleRecords],
  );
  const totalPreviewPages = Math.max(
    1,
    Math.ceil(filteredPreviewRecords.length / PREVIEW_RECORDS_PER_PAGE),
  );
  const currentPreviewPage = Math.min(previewPage, totalPreviewPages);
  const paginatedPreviewRecords = filteredPreviewRecords.slice(
    (currentPreviewPage - 1) * PREVIEW_RECORDS_PER_PAGE,
    currentPreviewPage * PREVIEW_RECORDS_PER_PAGE,
  );

  useEffect(() => {
    setCatalogUnitPage((current) => Math.min(current, totalCatalogUnitPages));
  }, [totalCatalogUnitPages]);

  useEffect(() => {
    setRecordPage((current) => Math.min(current, totalRecordPages));
  }, [totalRecordPages]);

  useEffect(() => {
    setPreviewPage((current) => Math.min(current, totalPreviewPages));
  }, [totalPreviewPages]);

  useEffect(() => {
    setPreviewBulkField((current) => {
      if (current && previewFieldOptions.includes(current)) {
        return current;
      }
      return previewFieldOptions[0] ?? null;
    });
  }, [previewFieldOptions]);

  const toggleRecord = useCallback((recordId: string) => {
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
  }, [notifyWarning]);

  const toggleVisibleRecords = () => {
    beginBulkRecordSelection();
    setSelectedRecordIds((current) => {
      if (allVisibleSelected) {
        const visibleIdSet = new Set(selectableVisibleIds);
        return current.filter((id) => !visibleIdSet.has(id));
      }
      const { recordIds, omittedCount } = addVisibleRecordSelection(
        current,
        selectableVisibleIds,
      );
      if (omittedCount > 0) {
        notifyWarning(
          `已选择前 ${MAX_SELECTED_RECORDS} 位导师`,
          `还有 ${omittedCount} 位未选中；一次最多导入 ${MAX_SELECTED_RECORDS} 位，请分批处理。`,
        );
      }
      return recordIds;
    });
  };

  const clearVisibleRecords = () => {
    beginBulkRecordSelection();
    const visibleIdSet = new Set(selectableVisibleIds);
    setSelectedRecordIds((current) => current.filter((id) => !visibleIdSet.has(id)));
  };

  const clearPreviewFilters = () => {
    setPreviewKeyword('');
    setPreviewSearchScopes([...DEFAULT_COMMUNITY_MENTOR_SEARCH_SCOPES]);
    setPreviewCategoryFilters([]);
    setPreviewFieldStateFilters([]);
    setPreviewFieldFilters([]);
    setPreviewOnlyUnconfirmed(false);
    setPreviewPage(1);
  };

  const openPreview = async () => {
    if (
      !catalog ||
      !loadedUnitPaths ||
      selectedRecordIds.length === 0 ||
      recordsSelectionStale
    ) {
      if (recordsSelectionStale) {
        notifyWarning('请重新加载导师', '学院选择已变化，请重新加载导师。');
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
      clearPreviewFilters();
      setPreviewBulkField(
        result.records
          .flatMap((item) => getChangedFields(item))
          .at(0)?.field ?? null,
      );
      setPreviewPayload(result);
    } catch (error) {
      notifyError('生成导入预览失败', getErrorMessage(error, '无法生成导入预览'));
    } finally {
      setPreviewLoading(false);
    }
  };

  const closePreview = () => {
    setPreviewPayload(null);
    clearPreviewFilters();
    setPreviewBulkField(null);
    setFieldChoices({});
    setIdentityConfirmations({});
  };

  const applyChoiceToAllPreviewFields = (choice: CommunityFieldChoiceDTO) => {
    if (!previewPayload) {
      return;
    }
    setFieldChoices(Object.fromEntries(
      previewPayload.records.map((item) => [
        item.record.id,
        Object.fromEntries(
          item.fields.map((field) => [
            field.field,
            choice === 'local' && item.local_professor_id === null
              ? 'community'
              : choice,
          ]),
        ),
      ]),
    ));
  };

  const applyChoiceToFilteredPreviewField = (choice: CommunityFieldChoiceDTO) => {
    if (!previewBulkField || previewBulkEligibleRecords.length === 0) {
      return;
    }
    setFieldChoices((current) => {
      const next = { ...current };
      previewBulkEligibleRecords.forEach((item) => {
        if (choice === 'local' && item.local_professor_id === null) {
          return;
        }
        next[item.record.id] = {
          ...(current[item.record.id] ?? {}),
          [previewBulkField]: choice,
        };
      });
      return next;
    });
  };

  const submitImport = async () => {
    if (!catalog || !previewPayload || !loadedUnitPaths) {
      return;
    }
    if (recordsSelectionStale) {
      notifyWarning('请重新加载导师', '学院选择已变化，请重新加载导师。');
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
      clearPreviewFilters();
      setPreviewOnlyUnconfirmed(true);
      setPreviewPage(1);
      previewScrollRef.current?.scrollTo?.({ top: 0 });
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
      closePreview();
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
              贡献院校数据
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
              <h2 className="font-semibold">正在使用缓存数据</h2>
              <p className="mt-1 text-sm leading-6 text-amber-800">{catalog.warning}</p>
              <p className="mt-1 text-xs text-amber-700">联网后刷新即可更新。</p>
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
                本地导师不会自动变更，请查看证据后处理。
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
          <Link
            to="/professors?community_contribution=batch"
            className="ui-btn-primary mt-6"
          >
            <FileSpreadsheet className="h-4 w-4" />
            批量贡献第一所学校/学院
          </Link>
        </div>
      ) : catalog ? (
        <div
          data-testid="community-mentor-browser-layout"
          className="mt-8 space-y-6"
        >
          <section
            ref={catalogUnitSelectorRef}
            tabIndex={-1}
            data-testid="community-mentor-unit-selector"
            className="rounded-[28px] border border-stone-200 bg-white p-5 shadow-sm focus:outline-none md:p-6"
          >
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-stone-900">选择学校与学院</h2>
                <p className="mt-1 text-xs text-stone-500">
                  已选 {selectedUnitPaths.length} 个学院 · {selectedUnitRecordCount} 位导师
                </p>
              </div>
              <Building2 className="h-5 w-5 text-primary" />
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <MultiSelectFilter
                label="学校"
                allLabel="全部学校"
                selectedValues={catalogUniversityFilters}
                options={catalogUniversityOptions}
                optionLabels={catalogUniversityOptionLabels}
                onChange={updateCatalogUniversityFilters}
              />
              <MultiSelectFilter
                label="学院"
                allLabel="全部学院"
                selectedValues={catalogUnitFilters}
                options={catalogUnitOptions}
                optionLabels={catalogUnitOptionLabels}
                onChange={(nextValues) => {
                  setCatalogUnitFilters(nextValues);
                  setCatalogUnitPage(1);
                }}
              />
            </div>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-stone-50 px-4 py-3">
              <button
                type="button"
                aria-label={allFilteredUnitsSelected ? "取消全选学院" : "全选当前学院"}
                aria-pressed={allFilteredUnitsSelected}
                disabled={filteredCatalogUnitPaths.length === 0}
                onClick={toggleFilteredUnits}
                className="inline-flex items-center gap-2 text-sm text-stone-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {allFilteredUnitsSelected ? (
                  <SquareCheck className="h-5 w-5 shrink-0 text-primary" />
                ) : partiallyFilteredUnitsSelected ? (
                  <SquareMinus className="h-5 w-5 shrink-0 text-primary" />
                ) : (
                  <Square className="h-5 w-5 shrink-0 text-stone-400" />
                )}
                <span>
                  {allFilteredUnitsSelected ? '取消全选' : '全选当前结果'}
                  {selectedFilteredUnitCount > 0
                    ? `（已选 ${selectedFilteredUnitCount}/${filteredCatalogUnitPaths.length}）`
                    : ''}
                </span>
              </button>
              {selectedFilteredUnitCount > 0 ? (
                <button
                  type="button"
                  className="text-xs font-medium text-stone-600 underline decoration-stone-300 underline-offset-2"
                  onClick={() => {
                    const filteredPathSet = new Set(filteredCatalogUnitPaths);
                    setSelectedUnitPaths((current) => current.filter(
                      (path) => !filteredPathSet.has(path),
                    ));
                  }}
                >
                  清除当前选择
                </button>
              ) : null}
            </div>
            <div
              aria-label="学校与学院列表"
              className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3"
            >
              {paginatedCatalogUnits.length === 0 ? (
                <div className="col-span-full rounded-2xl border border-dashed border-stone-200 p-8 text-center text-sm text-stone-500">
                  没有匹配的学院。
                </div>
              ) : paginatedCatalogUnits.map((entry) => {
                const selected = selectedUnitPaths.includes(entry.unit.path);
                return (
                  <button
                    type="button"
                    key={entry.unit.path}
                    aria-label={`${selected ? '取消选择' : '选择'} ${entry.universityName} ${entry.unit.name}`}
                    aria-pressed={selected}
                    onClick={() => toggleUnit(entry.unit.path)}
                    className={clsx(
                      'flex min-h-[72px] w-full items-center gap-3 rounded-2xl border px-3 py-2.5 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2',
                      selected
                        ? 'border-primary/35 bg-primary/[0.035]'
                        : 'border-stone-200 hover:border-orange-200 hover:bg-orange-50/40',
                    )}
                  >
                    <span
                      aria-hidden="true"
                      className={clsx(
                        'flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border text-sm transition',
                        selected
                          ? 'border-primary bg-primary text-white shadow-sm shadow-primary/20'
                          : 'border-stone-200 bg-white text-stone-300',
                      )}
                    >
                      <Check className={clsx('h-3.5 w-3.5', selected ? 'opacity-100' : 'opacity-0')} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-normal text-stone-800">{entry.universityName}</span>
                      <span className="mt-0.5 block truncate text-sm font-normal text-stone-800">{entry.unit.name}</span>
                    </span>
                    <span className="shrink-0 rounded-full bg-stone-100 px-2 py-0.5 text-xs text-stone-500">
                      {entry.unit.record_count} 位
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="mt-4 flex flex-col gap-4 border-t border-stone-100 pt-4 lg:flex-row lg:items-center">
              <Pagination
                page={currentCatalogUnitPage}
                pageSize={catalogUnitPageSize}
                totalCount={filteredCatalogUnits.length}
                onChange={(change) => {
                  setCatalogUnitPage(change.page);
                  setCatalogUnitPageSize(change.pageSize);
                  scrollToCatalogUnitSelector();
                }}
                ariaLabel="学校与学院分页"
                pageSizeAriaLabel="学校与学院每页数量"
                pageSizeOptions={CATALOG_UNIT_PAGE_SIZE_OPTIONS}
                unitLabel="个"
                itemLabel="个学院"
                className="min-w-0 flex-1"
              />
              <button type="button" disabled={recordsLoading || selectedUnitPaths.length === 0} onClick={() => void loadRecordsForPaths(selectedUnitPaths)} className="ui-btn-primary w-full shrink-0 justify-center disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto">
                {recordsLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
                查看导师
              </button>
            </div>
          </section>

          <section className="min-w-0 rounded-[28px] border border-stone-200 bg-white p-5 shadow-sm md:p-6">
            {!recordsPayload ? (
              <div className="flex min-h-64 flex-col items-center justify-center text-center">
                <Users className="h-10 w-10 text-stone-300" />
                <h2 className="mt-4 font-semibold text-stone-900">选择学院后查看导师</h2>
              </div>
            ) : (
              <>
                <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
                  <div>
                    <h2 className="text-xl font-semibold text-stone-900">导师列表</h2>
                    <p className="mt-1 text-sm text-stone-500">已加载 {recordsPayload.records.length} 位 · 已选 {selectedRecordIds.length} 位</p>
                  </div>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-7">
                  <div className="block min-w-0 md:col-span-2 xl:col-span-2 2xl:col-span-2">
                    <div className="mb-2 text-sm font-medium text-stone-800">关键词</div>
                    <div
                      data-testid="community-mentor-keyword-control"
                      className="ui-select-shell h-10 min-h-10 w-full py-0"
                    >
                      <Search className="h-4 w-4 shrink-0 text-stone-400" />
                      <input
                        aria-label="搜索导师"
                        value={recordKeyword}
                        onChange={(event) => {
                          setRecordKeyword(event.target.value);
                          setRecordPage(1);
                        }}
                        className="w-full min-w-0 bg-transparent leading-5 outline-none placeholder:text-stone-400"
                        placeholder={
                          recordSearchScopes.length === 1
                            ? `搜索${COMMUNITY_MENTOR_SEARCH_SCOPE_OPTIONS.find(
                                (option) => option.value === recordSearchScopes[0],
                              )?.label ?? '所选字段'}`
                            : '搜索所选字段'
                        }
                      />
                      <KeywordSearchScopeSelect
                        label="搜索范围"
                        options={COMMUNITY_MENTOR_SEARCH_SCOPE_OPTIONS}
                        selectedValues={recordSearchScopes}
                        embedded
                        onChange={(nextValues) => {
                          setRecordSearchScopes(normalizeCommunityMentorSearchScopes(nextValues));
                          setRecordPage(1);
                        }}
                      />
                    </div>
                  </div>
                  <MultiSelectFilter
                    label="学校"
                    allLabel="全部学校"
                    selectedValues={recordUniversityFilters}
                    options={recordUniversityOptions}
                    onChange={(nextValues) => {
                      setRecordUniversityFilters(nextValues);
                      setRecordPage(1);
                    }}
                  />
                  <MultiSelectFilter
                    label="学院"
                    allLabel="全部学院"
                    selectedValues={recordSchoolFilters}
                    options={recordSchoolOptions}
                    onChange={(nextValues) => {
                      setRecordSchoolFilters(nextValues);
                      setRecordPage(1);
                    }}
                  />
                  <MultiSelectFilter
                    label="系所"
                    allLabel="全部系所"
                    selectedValues={recordDepartmentFilters}
                    options={recordDepartmentOptions}
                    onChange={(nextValues) => {
                      setRecordDepartmentFilters(nextValues);
                      setRecordPage(1);
                    }}
                  />
                  <MultiSelectFilter
                    label="职称"
                    allLabel="全部职称"
                    selectedValues={recordTitleFilters}
                    options={recordTitleOptions}
                    onChange={(nextValues) => {
                      setRecordTitleFilters(nextValues);
                      setRecordPage(1);
                    }}
                  />
                  <MultiSelectFilter
                    label="本地状态"
                    allLabel="全部情况"
                    selectedValues={categoryFilters}
                    options={categoryOptions}
                    optionLabels={categoryOptionLabels}
                    onChange={(nextValues) => {
                      setCategoryFilters(nextValues as CommunityComparisonCategoryDTO[]);
                      setRecordPage(1);
                    }}
                  />
                </div>
                {recordKeyword || recordUniversityFilters.length > 0 ||
                recordSchoolFilters.length > 0 || recordDepartmentFilters.length > 0 ||
                recordTitleFilters.length > 0 || categoryFilters.length > 0 ? (
                  <div className="mt-3 flex justify-end">
                    <button
                      type="button"
                      className="text-xs font-medium text-stone-500 underline decoration-stone-300 underline-offset-2 hover:text-stone-800"
                      onClick={() => {
                        setRecordKeyword('');
                        setRecordUniversityFilters([]);
                        setRecordSchoolFilters([]);
                        setRecordDepartmentFilters([]);
                        setRecordTitleFilters([]);
                        setCategoryFilters([]);
                        setRecordPage(1);
                      }}
                    >
                      清除全部筛选
                    </button>
                  </div>
                ) : null}
                {recordsSelectionStale ? (
                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                    <span className="inline-flex items-center gap-2">
                      <AlertTriangle className="h-4 w-4 shrink-0" />
                      学院选择已变化，请重新加载导师。
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
                    <button
                      type="button"
                      aria-label={allVisibleSelected ? "取消全选导师" : "全选当前导师"}
                      aria-pressed={allVisibleSelected}
                      disabled={selectableVisibleIds.length === 0}
                      onClick={toggleVisibleRecords}
                      className="inline-flex items-center gap-2 text-sm text-stone-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {allVisibleSelected ? (
                        <SquareCheck className="h-5 w-5 shrink-0 text-primary" />
                      ) : partiallyVisibleSelected ? (
                        <SquareMinus className="h-5 w-5 shrink-0 text-primary" />
                      ) : (
                        <Square className="h-5 w-5 shrink-0 text-stone-400" />
                      )}
                      <span>
                        {allVisibleSelected ? '取消全选' : '全选当前结果'}
                        {selectedVisibleCount > 0 ? `（已选 ${selectedVisibleCount}/${selectableVisibleIds.length}）` : ''}
                      </span>
                    </button>
                    {selectedVisibleCount > 0 ? (
                      <button
                        type="button"
                        className="text-xs font-medium text-stone-600 underline decoration-stone-300 underline-offset-2"
                        onClick={clearVisibleRecords}
                      >
                        清除当前选择
                      </button>
                    ) : null}
                  </div>
                  <button type="button" disabled={previewLoading || selectedRecordIds.length === 0 || recordsSelectionStale} onClick={() => void openPreview()} className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-50">
                    {previewLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                    预览并导入 {selectedRecordIds.length > 0 ? selectedRecordIds.length : ''}
                  </button>
                </div>
                <div
                  ref={recordListRef}
                  data-testid="community-mentor-record-list"
                  className="community-mentor-record-list mt-4 space-y-3 [&.is-bulk-selecting_.selection-toggle-button]:transition-none"
                >
                  {visibleRecords.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-stone-200 p-10 text-center text-sm text-stone-500">没有匹配的导师。</div>
                  ) : paginatedVisibleRecords.map((item) => (
                    <CommunityMentorRecordCard
                      key={item.record.id}
                      item={item}
                      selected={selectedRecordIdSet.has(item.record.id)}
                      onToggle={toggleRecord}
                      onOpenDetail={openRecordDetail}
                      onReport={reportRecord}
                    />
                  ))}
                </div>
                {visibleRecords.length > RECORDS_PER_PAGE ? (
                  <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-stone-100 pt-4">
                    <span className="text-xs text-stone-500">
                      {visibleRecords.length} 位 · {currentRecordPage}/{totalRecordPages} 页
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

      <CommunityMentorDetailDialog
        item={detailRecord}
        onClose={() => setDetailRecord(null)}
        onReport={(record) => openFeedbackForm(record, notifySuccess)}
      />

      {previewPayload ? (
        <div role="dialog" aria-modal="true" aria-label="社区导师导入预览" className="fixed inset-0 z-[90] flex items-center justify-center bg-stone-950/40 p-2 backdrop-blur-sm sm:p-4">
          <div className="flex max-h-[calc(100dvh-1rem)] w-full max-w-6xl flex-col overflow-y-auto rounded-[26px] border border-white/60 bg-stone-50 shadow-2xl md:max-h-[92vh] md:overflow-hidden">
            <div className="sticky top-0 z-20 flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-stone-200 bg-white px-4 py-3 sm:px-5 sm:py-4 md:static">
              <div className="min-w-0">
                <h2 className="text-xl font-semibold text-stone-950">导入预览</h2>
                <p className="mt-1 text-xs text-stone-500">
                  共 {previewPayload.records.length} 位 · 当前筛选 {filteredPreviewRecords.length} 位
                  {previewUnconfirmedCount > 0 ? ` · ${previewUnconfirmedCount} 位待确认身份` : ''}
                </p>
              </div>
              <div className="flex flex-wrap items-center justify-end gap-2">
                <button
                  type="button"
                  disabled={importing}
                  onClick={() => applyChoiceToAllPreviewFields('local')}
                  className="ui-btn-secondary min-h-8 px-3 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-60"
                  title="已有导师保留本地资料，新导师使用社区资料"
                >
                  全部保留本地
                </button>
                <button
                  type="button"
                  disabled={importing}
                  onClick={() => applyChoiceToAllPreviewFields('community')}
                  className="ui-btn-secondary min-h-8 px-3 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-60"
                  title="社区空值也会覆盖并清空本地内容"
                >
                  全部采用社区
                </button>
                <button type="button" disabled={importing} onClick={closePreview} className="rounded-xl p-2 text-stone-500 hover:bg-stone-100" aria-label="关闭预览"><X className="h-5 w-5" /></button>
              </div>
            </div>
            <div
              data-testid="community-import-preview-filters"
              className="shrink-0 border-b border-stone-200 bg-stone-50/90 px-4 py-3"
            >
              <div className="grid gap-2.5 md:grid-cols-2 lg:grid-cols-4">
                <div className="block min-w-0">
                  <div className="mb-2 text-sm font-medium text-stone-800">关键词</div>
                  <div className="ui-select-shell h-10 min-h-10 w-full py-0">
                    <Search className="h-4 w-4 shrink-0 text-stone-400" />
                    <input
                      aria-label="搜索导入预览导师"
                      value={previewKeyword}
                      onChange={(event) => {
                        setPreviewKeyword(event.target.value);
                        setPreviewPage(1);
                      }}
                      className="w-full min-w-0 bg-transparent leading-5 outline-none placeholder:text-stone-400"
                      placeholder="搜索所选字段"
                    />
                    <KeywordSearchScopeSelect
                      label="预览搜索范围"
                      options={COMMUNITY_MENTOR_SEARCH_SCOPE_OPTIONS}
                      selectedValues={previewSearchScopes}
                      embedded
                      onChange={(nextValues) => {
                        setPreviewSearchScopes(normalizeCommunityMentorSearchScopes(nextValues));
                        setPreviewPage(1);
                      }}
                    />
                  </div>
                </div>
                <MultiSelectFilter
                  label="本地状态"
                  allLabel="全部情况"
                  selectedValues={previewCategoryFilters}
                  options={categoryOptions}
                  optionLabels={categoryOptionLabels}
                  onChange={(nextValues) => {
                    setPreviewCategoryFilters(nextValues as CommunityComparisonCategoryDTO[]);
                    setPreviewPage(1);
                  }}
                />
                <MultiSelectFilter
                  label="差异类型"
                  allLabel="全部差异"
                  selectedValues={previewFieldStateFilters}
                  options={previewFieldStateOptions}
                  optionLabels={previewFieldStateOptionLabels}
                  onChange={(nextValues) => {
                    setPreviewFieldStateFilters(nextValues as CommunityFieldStateDTO[]);
                    setPreviewPage(1);
                  }}
                />
                <MultiSelectFilter
                  label="涉及字段"
                  allLabel="全部字段"
                  selectedValues={previewFieldFilters}
                  options={previewFieldOptions}
                  optionLabels={previewFieldOptionLabels}
                  onChange={(nextValues) => {
                    setPreviewFieldFilters(nextValues);
                    setPreviewPage(1);
                  }}
                />
              </div>

              <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2">
                <div className="flex items-center gap-2 text-xs text-stone-600">
                  <SelectionToggleButton
                    label="只看待确认导师"
                    selected={previewOnlyUnconfirmed}
                    onToggle={() => {
                      setPreviewOnlyUnconfirmed((current) => !current);
                      setPreviewPage(1);
                    }}
                  />
                  <span>只看待确认导师</span>
                </div>
                {previewFieldEntries.length > 0 ? (
                  <div className="flex min-w-0 flex-[1_1_24rem] items-center gap-1.5 overflow-x-auto pb-1">
                    <span className="shrink-0 text-xs text-stone-500">差异字段：</span>
                    {previewFieldEntries.map((entry) => {
                      const active = previewFieldFilters.length === 1 &&
                        previewFieldFilters[0] === entry.field;
                      return (
                        <button
                          key={entry.field}
                          type="button"
                          aria-label={`只看涉及${entry.label}的导师`}
                          aria-pressed={active}
                          onClick={() => {
                            setPreviewFieldFilters(active ? [] : [entry.field]);
                            setPreviewPage(1);
                          }}
                          className={clsx(
                            'shrink-0 rounded-lg border px-2 py-1 text-[11px] font-medium transition',
                            active
                              ? 'border-primary/35 bg-primary/10 text-primary'
                              : 'border-stone-200 bg-white text-stone-600 hover:border-stone-300',
                          )}
                        >
                          {entry.label} {previewFieldCounts.get(entry.field) ?? 0}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
                {previewHasFilters ? (
                  <button
                    type="button"
                    onClick={clearPreviewFilters}
                    className="ml-auto text-xs font-medium text-stone-500 underline decoration-stone-300 underline-offset-2 hover:text-stone-800"
                  >
                    清除全部筛选
                  </button>
                ) : null}
              </div>

              {previewBulkFieldOptions.length > 0 ? (
                <div className="mt-2.5 flex flex-wrap items-center gap-2 border-t border-stone-200 pt-2.5">
                  <span className="text-xs font-semibold text-stone-700">批量选择资料来源</span>
                  <TopBarSelectMenu
                    placeholder="字段"
                    value={previewBulkField}
                    options={previewBulkFieldOptions}
                    disabled={importing}
                    className="min-w-[11rem] max-w-[15rem] flex-1 sm:flex-none"
                    onChange={(value) => setPreviewBulkField(String(value))}
                  />
                  <span className="text-xs text-stone-500">
                    当前可处理 {previewBulkEligibleRecords.length} 位
                  </span>
                  <div className="ml-auto flex flex-wrap gap-2">
                    <button
                      type="button"
                      aria-label={`当前筛选的${previewFieldOptionLabels[previewBulkField ?? ''] ?? '字段'}全部保留本地`}
                      disabled={importing || previewBulkLocalEligibleCount === 0}
                      onClick={() => applyChoiceToFilteredPreviewField('local')}
                      className="ui-btn-secondary min-h-8 px-3 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      保留本地值
                    </button>
                    <button
                      type="button"
                      aria-label={`当前筛选的${previewFieldOptionLabels[previewBulkField ?? ''] ?? '字段'}全部采用社区`}
                      disabled={importing || previewBulkEligibleRecords.length === 0}
                      onClick={() => applyChoiceToFilteredPreviewField('community')}
                      className="ui-btn-secondary min-h-8 px-3 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      采用社区值
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
            <div ref={previewScrollRef} className="flex-none space-y-3 overflow-visible p-3 md:min-h-0 md:flex-1 md:overflow-y-auto md:p-4">
              {paginatedPreviewRecords.length === 0 ? (
                <div className="flex min-h-48 flex-col items-center justify-center rounded-xl border border-dashed border-stone-200 bg-white px-6 text-center">
                  <Search className="h-7 w-7 text-stone-300" />
                  <p className="mt-3 text-sm font-medium text-stone-700">没有符合筛选条件的导师</p>
                  <button
                    type="button"
                    onClick={clearPreviewFilters}
                    className="mt-2 text-xs font-medium text-primary hover:underline"
                  >
                    清除筛选
                  </button>
                </div>
              ) : paginatedPreviewRecords.map((item) => {
                const visibleFields = item.category === 'new'
                  ? []
                  : getChangedFields(item);
                const allowLocalChoice = item.local_professor_id !== null;
                return (
                  <section key={item.record.id} className="rounded-xl border border-stone-200 bg-white p-2.5 shadow-sm md:p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-1.5"><h3 className="text-sm font-semibold text-stone-950">{item.record.name}</h3><span className={clsx('rounded-full border px-2 py-0.5 text-[11px] font-medium', categoryMeta[item.category].className)}>{categoryMeta[item.category].label}</span></div>
                        <p className="mt-0.5 truncate text-[11px] text-stone-500">{item.record.email} · {[item.record.university, item.record.school].filter(Boolean).join(' · ')}</p>
                      </div>
                      {item.local_professor_id ? <Link to={`/professors?keyword=${encodeURIComponent(item.local_professor_name ?? item.record.name)}`} className="text-xs font-medium text-primary hover:underline">查看本地导师</Link> : null}
                    </div>
                    {item.local_archived ? <div className="mt-2 rounded-lg border border-orange-200 bg-orange-50 px-3 py-1.5 text-xs text-orange-800">这位导师在本地回收站中；导入不会自动恢复。</div> : null}
                    {item.import_blocked ? (
                      <div className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-800">
                        <strong>暂不可导入：</strong>{item.import_blocked_reason ?? '请先处理这条导师记录的冲突。'}
                      </div>
                    ) : item.identity_conflict ? (
                      <div className="mt-2 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                        <SelectionToggleButton
                          label={`确认 ${item.record.name} 是同一位导师`}
                          selected={identityConfirmations[item.record.id] ?? false}
                          onToggle={() => setIdentityConfirmations((current) => ({
                            ...current,
                            [item.record.id]: !(current[item.record.id] ?? false),
                          }))}
                        />
                        <span><strong>人工确认同一导师：</strong>{item.match_reason}</span>
                      </div>
                    ) : null}
                    {visibleFields.length > 0 ? (
                      <div className="mt-2.5 overflow-hidden rounded-xl border border-stone-200">
                        <div className="hidden grid-cols-[6.75rem_minmax(0,1fr)_minmax(0,1fr)] bg-stone-50 px-2 py-1.5 text-[11px] font-medium text-stone-500 md:grid">
                          <span className="px-1">字段</span>
                          <span className="px-2.5">本地资料</span>
                          <span className="px-2.5">社区资料</span>
                        </div>
                        {visibleFields.map((field) => (
                          <DifferenceField key={field.field} field={field} choice={fieldChoices[item.record.id]?.[field.field] ?? field.suggested_choice} allowLocalChoice={allowLocalChoice} onChange={(choice) => setFieldChoices((current) => ({ ...current, [item.record.id]: { ...(current[item.record.id] ?? {}), [field.field]: choice } }))} />
                        ))}
                      </div>
                    ) : (
                      <div className="mt-2 rounded-lg bg-stone-50 px-3 py-2 text-xs text-stone-600">
                        {item.category === 'new'
                          ? '将按社区资料新增到本地。'
                          : '资料一致，导入只会更新社区关联。'}
                      </div>
                    )}
                  </section>
                );
              })}
            </div>
            {filteredPreviewRecords.length > PREVIEW_RECORDS_PER_PAGE ? (
              <div className="shrink-0 flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 bg-stone-50 px-4 py-3 sm:px-6">
                <span className="text-xs text-stone-500">
                  {currentPreviewPage}/{totalPreviewPages} 页 · {' '}
                  {(currentPreviewPage - 1) * PREVIEW_RECORDS_PER_PAGE + 1}–
                  {Math.min(currentPreviewPage * PREVIEW_RECORDS_PER_PAGE, filteredPreviewRecords.length)} / {filteredPreviewRecords.length} 位
                </span>
                <div className="flex gap-2">
                  <button
                    type="button"
                    aria-label="上一页导入预览"
                    disabled={currentPreviewPage <= 1}
                    onClick={() => {
                      setPreviewPage((current) => Math.max(1, current - 1));
                      previewScrollRef.current?.scrollTo?.({ top: 0 });
                    }}
                    className="ui-btn-secondary min-h-8 px-3 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <ChevronLeft className="h-3.5 w-3.5" /> 上一页
                  </button>
                  <button
                    type="button"
                    aria-label="下一页导入预览"
                    disabled={currentPreviewPage >= totalPreviewPages}
                    onClick={() => {
                      setPreviewPage((current) => Math.min(totalPreviewPages, current + 1));
                      previewScrollRef.current?.scrollTo?.({ top: 0 });
                    }}
                    className="ui-btn-secondary min-h-8 px-3 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    下一页 <ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ) : null}
            <div className="sticky bottom-0 z-20 flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-stone-200 bg-white px-4 py-3 sm:px-6 sm:py-4 md:static">
              <p className="text-xs text-stone-500">只导入导师资料；其他本地数据不变。</p>
              <div className="flex gap-3">
                <button type="button" disabled={importing} onClick={closePreview} className="ui-btn-secondary">取消</button>
                <button type="button" disabled={importing || recordsSelectionStale || previewPayload.records.some((item) => !isRecordSelectable(item))} onClick={() => void submitImport()} className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60">{importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}确认导入 {previewPayload.records.length} 位</button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
};
