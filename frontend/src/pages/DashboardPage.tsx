import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Title,
  Tooltip,
} from 'chart.js';
import type { ChartData, ChartOptions, TooltipItem } from 'chart.js';
import { Line } from 'react-chartjs-2';
import {
  BadgeCheck,
  ClipboardCheck,
  GraduationCap,
  Loader2,
  Percent,
  RefreshCcw,
  Reply,
  Send,
  Star,
  Users,
} from 'lucide-react';
import clsx from 'clsx';
import { NativeSelectField } from '@/components/atoms/NativeSelectField';
import { StatisticsSectionNav, type StatisticsSectionNavItem } from '@/components/molecules/StatisticsSectionNav';
import { useNotification } from '@/context/NotificationContext';
import { useSelectionContext } from '@/context/SelectionContext';
import { DistributionPieChart } from '@/components/molecules/DistributionPieChart';
import { TokenVisualizationPanel } from '@/components/molecules/TokenVisualizationPanel';
import { getDashboardOverview } from '@/lib/api/dashboardApi';
import {
  buildAxisTicks,
  resolveFloatingTooltipPosition,
  resolveNiceAxisMax,
} from '@/lib/charting';
import { resolveStatisticsSectionNavTop } from '@/lib/statisticsSectionNav';
import type {
  DashboardEmailTrendBucketDTO,
  DashboardOverviewDTO,
  DashboardProfileCompletenessBucketDTO,
  DashboardSchoolFilterDTO,
} from '@/types';

ChartJS.register(
  CategoryScale,
  Filler,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Title,
  Tooltip,
);

const numberFormatter = new Intl.NumberFormat('zh-CN');
const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  month: '2-digit',
  day: '2-digit',
});

const formatNumber = (value: number) => numberFormatter.format(value);

const formatPercent = (value: number) => `${Math.round(value * 100)}%`;

const formatDate = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return dateFormatter.format(date);
};

const resolveTrendMaxTicks = (bucketCount: number) => {
  if (bucketCount <= 8) {
    return bucketCount;
  }
  if (bucketCount <= 16) {
    return bucketCount;
  }
  return 10;
};

const formatLocalDate = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

const getEmailDateRange = (preset: string): { startDate: string | null; endDate: string | null } => {
  const now = new Date();
  if (preset === 'all') {
    return { startDate: null, endDate: null };
  }
  const start = new Date(now);
  if (preset === '7d') {
    start.setDate(start.getDate() - 6);
  } else if (preset === '30d') {
    start.setDate(start.getDate() - 29);
  } else if (preset === '90d') {
    start.setDate(start.getDate() - 89);
  }
  return {
    startDate: formatLocalDate(start),
    endDate: formatLocalDate(now),
  };
};

type MetricTone = 'teal' | 'amber' | 'rose' | 'sky' | 'violet' | 'stone';

type MatchTooltipState = {
  bucket: string;
  x: number;
  y: number;
};

const matchTooltipWidth = 284;
const matchTooltipHeight = 132;

const mentorDetailGridStyle = {
  gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 28rem), 1fr))',
};

const emailMetricsGridStyle = {
  gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 16rem), 1fr))',
};

const statisticsSectionItems: StatisticsSectionNavItem[] = [
  { id: 'mentor', label: '导师' },
  { id: 'email', label: '邮件' },
  { id: 'token', label: 'Token' },
];

const toneClasses: Record<MetricTone, { icon: string }> = {
  teal: { icon: 'bg-teal-50 text-teal-700' },
  amber: { icon: 'bg-amber-50 text-amber-700' },
  rose: { icon: 'bg-rose-50 text-rose-700' },
  sky: { icon: 'bg-sky-50 text-sky-700' },
  violet: { icon: 'bg-violet-50 text-violet-700' },
  stone: { icon: 'bg-stone-100 text-stone-700' },
};

const DashboardLoadingSkeleton = () => (
  <main className="mx-auto max-w-7xl px-6 py-8" aria-label="统计面板加载中">
    <section className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-6 shadow-sm">
      <div className="h-9 w-40 animate-pulse rounded-xl bg-stone-200" />
      <div className="mt-3 h-4 w-96 max-w-full animate-pulse rounded-full bg-stone-100" />
    </section>
    <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: 8 }, (_, index) => (
        <div key={index} className="h-32 animate-pulse rounded-2xl border border-stone-200 bg-white" />
      ))}
    </div>
    <div className="mt-6 flex items-center justify-center gap-2 text-sm text-stone-500">
      <Loader2 className="h-4 w-4 animate-spin" />
      正在加载统计数据...
    </div>
  </main>
);

const MetricCard = ({
  title,
  value,
  helper,
  icon,
  tone,
}: {
  title: string;
  value: string;
  helper: string;
  icon: ReactNode;
  tone: MetricTone;
}) => (
  <article className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
    <div className="flex items-start gap-4">
      <div className={clsx('flex h-11 w-11 shrink-0 items-center justify-center rounded-xl', toneClasses[tone].icon)}>
        {icon}
      </div>
      <div className="min-w-0">
        <div className="text-sm font-medium text-stone-600">{title}</div>
        <div className="mt-2 text-3xl font-semibold leading-none text-stone-950">{value}</div>
        <div className="mt-2 text-xs leading-5 text-stone-500">{helper}</div>
      </div>
    </div>
  </article>
);

const ModuleHeader = ({
  title,
  description,
  icon,
}: {
  title: string;
  description: string;
  icon: ReactNode;
}) => (
  <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
    <div className="flex min-w-0 items-center gap-3">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-stone-200 bg-white text-stone-700 shadow-sm">
        {icon}
      </div>
      <div className="min-w-0">
        <h2 className="text-xl font-semibold text-stone-950">{title}</h2>
        <p className="mt-1 text-sm text-stone-500">{description}</p>
      </div>
    </div>
  </div>
);

const ChartCard = ({
  title,
  children,
  className,
  testId,
}: {
  title: string;
  children: ReactNode;
  className?: string;
  testId?: string;
}) => (
  <article data-testid={testId} className={clsx('rounded-2xl border border-stone-200 bg-white p-5 shadow-sm', className)}>
    <div className="mb-5">
      <h3 className="text-base font-semibold text-stone-900">{title}</h3>
    </div>
    {children}
  </article>
);

const EmptyState = ({ children }: { children: ReactNode }) => (
  <div className="flex min-h-36 items-center justify-center rounded-xl border border-dashed border-stone-200 bg-stone-50 px-4 py-8 text-center text-sm text-stone-500">
    {children}
  </div>
);

const MatchDistributionChart = ({
  data,
}: {
  data: DashboardOverviewDTO['mentor']['match_score_distribution'];
}) => {
  const [activeBucket, setActiveBucket] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<MatchTooltipState | null>(null);
  const max = Math.max(...data.map((item) => item.count), 0);
  const axisMax = resolveNiceAxisMax(max);
  const ticks = buildAxisTicks(axisMax);
  const total = data.reduce((sum, item) => sum + item.count, 0);
  const activeTooltipItem = tooltip ? data.find((item) => item.bucket === tooltip.bucket) ?? null : null;
  const activeTooltipShare = activeTooltipItem && total > 0 ? activeTooltipItem.count / total : 0;

  const handlePlotMouseMove = (event: ReactMouseEvent<HTMLDivElement>) => {
    const itemIndex = resolveMatchDistributionBucketIndex(event.clientX, event.currentTarget.getBoundingClientRect(), data.length);
    if (itemIndex === null) {
      return;
    }

    const item = data[itemIndex];
    setActiveBucket(item.bucket);
    setTooltip(createMatchTooltipState(item.bucket, event));
  };

  if (data.every((item) => item.count === 0)) {
    return <EmptyState>暂无匹配分数数据</EmptyState>;
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-3 text-xs text-stone-500">
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-teal-500" />已分析</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-stone-300" />未分析</span>
      </div>
      <div
        data-testid="match-distribution-chart-window"
        className="min-w-0 max-w-full overflow-hidden rounded-xl border border-stone-200 bg-white px-3 py-5 sm:px-4"
      >
        <div data-testid="match-distribution-chart-body" className="w-full pl-14 pr-1 sm:pl-16 sm:pr-3">
          <div data-testid="match-distribution-plot" className="relative h-40 border-b border-stone-500">
            {ticks.map((tick) => (
              <div
                key={tick}
                className={clsx(
                  'absolute left-0 right-0',
                  tick === 0 ? 'border-t border-stone-500' : 'border-t border-dashed border-stone-200',
                )}
                style={{ bottom: `${(tick / axisMax) * 100}%` }}
              >
                <span className="absolute right-[calc(100%+0.625rem)] top-0 -translate-y-1/2 whitespace-nowrap text-xs text-stone-500">
                  {formatNumber(tick)} 位
                </span>
              </div>
            ))}
            <div
              data-testid="match-distribution-interaction-layer"
              className="relative z-10 flex h-full items-end justify-between gap-2 sm:gap-3"
              onMouseMove={handlePlotMouseMove}
              onMouseLeave={() => {
                setActiveBucket(null);
                setTooltip(null);
              }}
            >
              {data.map((item) => {
                const share = total > 0 ? item.count / total : 0;
                const height = item.count > 0 ? Math.max((item.count / axisMax) * 100, 1.5) : 0;
                const active = activeBucket === item.bucket;

                return (
                  <div key={item.bucket} className="relative flex h-full min-w-0 flex-1 flex-col items-center justify-end">
                    {active ? <div className="pointer-events-none absolute inset-y-0 -left-1.5 -right-1.5 bg-teal-50/70 sm:-left-2 sm:-right-2" /> : null}
                    <button
                      type="button"
                      aria-label={`${item.label} ${formatNumber(item.count)} 位，占比 ${formatPercent(share)}`}
                      className="relative z-10 flex h-full w-full items-end justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/40"
                      onFocus={() => setActiveBucket(item.bucket)}
                      onBlur={() => {
                        setActiveBucket(null);
                        setTooltip(null);
                      }}
                    >
                      <span
                        className={clsx(
                          'max-w-[80%] rounded-t shadow-[0_0_0_1px_rgba(20,184,166,0.08)] transition-all',
                          'w-8 min-w-5 sm:w-10 sm:min-w-6',
                          item.bucket === 'unmatched' ? 'bg-stone-300' : 'bg-teal-500',
                        )}
                        style={{ height: `${height}%` }}
                      />
                    </button>
                  </div>
                );
              })}
            </div>
            {tooltip && activeTooltipItem ? (
              <MatchBucketTooltip
                item={activeTooltipItem}
                share={activeTooltipShare}
                x={tooltip.x}
                y={tooltip.y}
              />
            ) : null}
          </div>
          <div className="flex justify-between gap-2 pt-3 sm:gap-3">
            {data.map((item) => (
              <span key={item.bucket} className="min-w-0 flex-1 text-center text-xs text-stone-500">
                {item.label}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

function MatchBucketTooltip({
  item,
  share,
  x,
  y,
}: {
  item: DashboardOverviewDTO['mentor']['match_score_distribution'][number];
  share: number;
  x: number;
  y: number;
}) {
  return (
    <div
      role="tooltip"
      className="pointer-events-none fixed z-[80] rounded-xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-700 shadow-[0_16px_38px_-16px_rgba(41,37,36,0.38)]"
      style={{ left: x, top: y, width: matchTooltipWidth }}
    >
      <div className="flex items-center justify-between border-b border-stone-100 pb-2">
        <span className="font-medium text-stone-500">{item.label}</span>
        <span className="font-semibold text-stone-900">{formatPercent(share)}</span>
      </div>
      <div className="space-y-2 pt-3">
        <div className="flex items-center justify-between gap-4">
          <span className="inline-flex items-center gap-2 text-stone-500">
            <span
              className={clsx('h-2.5 w-2.5 rounded-sm', item.bucket === 'unmatched' ? 'bg-stone-300' : 'bg-teal-500')}
            />
            导师数
          </span>
          <span className="font-semibold text-stone-900">{formatNumber(item.count)} 位</span>
        </div>
      </div>
    </div>
  );
}

function createMatchTooltipState(
  bucket: string,
  event: ReactMouseEvent<HTMLElement>,
): MatchTooltipState {
  const position = resolveFloatingTooltipPosition(event.clientX, event.clientY, {
    width: matchTooltipWidth,
    height: matchTooltipHeight,
  });
  return {
    bucket,
    x: position.x,
    y: position.y,
  };
}

function resolveMatchDistributionBucketIndex(
  clientX: number,
  chartRect: Pick<DOMRect, 'left' | 'width'>,
  bucketCount: number,
) {
  if (bucketCount <= 0 || chartRect.width <= 0) {
    return null;
  }

  const ratio = Math.min(0.999999, Math.max(0, (clientX - chartRect.left) / chartRect.width));
  return Math.floor(ratio * bucketCount);
}

const TrendChart = ({ data }: { data: DashboardEmailTrendBucketDTO[] }) => {
  const visibleData = data;
  const chartData = useMemo<ChartData<'line', number[], string>>(
    () => ({
      labels: visibleData.map((item) => item.label ?? formatDate(item.date)),
      datasets: [
        {
          label: '发送',
          data: visibleData.map((item) => item.sent_count),
          borderColor: '#14b8a6',
          backgroundColor: 'rgba(20, 184, 166, 0.12)',
          borderWidth: 2,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHitRadius: 12,
          tension: 0.32,
        },
        {
          label: '回复',
          data: visibleData.map((item) => item.replied_count),
          borderColor: '#0ea5e9',
          backgroundColor: 'rgba(14, 165, 233, 0.12)',
          borderWidth: 2,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHitRadius: 12,
          tension: 0.32,
        },
      ],
    }),
    [visibleData],
  );
  const chartOptions = useMemo<ChartOptions<'line'>>(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        intersect: false,
        mode: 'index',
      },
      plugins: {
        legend: {
          display: false,
        },
        tooltip: {
          enabled: true,
          intersect: false,
          mode: 'index',
          backgroundColor: '#ffffff',
          titleColor: '#44403c',
          bodyColor: '#57534e',
          borderColor: '#e7e5e4',
          borderWidth: 1,
          cornerRadius: 12,
          displayColors: true,
          padding: 12,
          callbacks: {
            label: (context: TooltipItem<'line'>) => {
              const label = context.dataset.label ?? '';
              return `${label}: ${Number(context.raw).toLocaleString('zh-CN')} 封`;
            },
            footer: (tooltipItems: TooltipItem<'line'>[]) => {
              const index = tooltipItems[0]?.dataIndex;
              const bucket = typeof index === 'number' ? visibleData[index] : null;
              return bucket ? `合计 ${formatNumber(bucket.sent_count + bucket.replied_count)} 封` : '';
            },
          },
        },
      },
      scales: {
        x: {
          grid: {
            display: false,
          },
          ticks: {
            autoSkip: true,
            autoSkipPadding: 12,
            color: '#78716c',
            font: {
              size: 11,
            },
            maxRotation: visibleData.length > 10 ? 35 : 0,
            maxTicksLimit: resolveTrendMaxTicks(visibleData.length),
            minRotation: 0,
          },
        },
        y: {
          beginAtZero: true,
          border: {
            display: false,
          },
          grid: {
            color: '#e7e5e4',
            tickBorderDash: [4, 4],
          },
          ticks: {
            color: '#78716c',
            font: {
              size: 11,
            },
            precision: 0,
          },
        },
      },
    }),
    [visibleData],
  );

  if (data.length === 0 || data.every((item) => item.sent_count + item.replied_count === 0)) {
    return <EmptyState>暂无邮件趋势数据</EmptyState>;
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-3 text-xs text-stone-500">
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-teal-500" />发送</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-sky-500" />回复</span>
      </div>
      <div className="h-64 min-w-0 max-w-full overflow-hidden rounded-xl border border-stone-200 bg-white px-4 py-5">
        <Line data={chartData} options={chartOptions} />
      </div>
    </div>
  );
};

const MentorFilterBar = ({
  schoolFilters,
  selectedUniversity,
  selectedSchool,
  schoolOptions,
  onUniversityChange,
  onSchoolChange,
  onClear,
  className,
}: {
  schoolFilters: DashboardSchoolFilterDTO[];
  selectedUniversity: string | null;
  selectedSchool: string | null;
  schoolOptions: DashboardSchoolFilterDTO['schools'];
  onUniversityChange: (value: string | null) => void;
  onSchoolChange: (value: string | null) => void;
  onClear: () => void;
  className?: string;
}) => (
  <article
    data-testid="mentor-filter-bar"
    className={clsx('rounded-2xl border border-stone-200 bg-white p-4 shadow-sm', className)}
  >
    <div className="flex flex-col gap-3 md:flex-row md:items-end">
      <DashboardFilterSelect
        label="学校"
        ariaLabel="学校筛选"
        value={selectedUniversity ?? ''}
        onChange={(value) => onUniversityChange(value || null)}
        options={[
          { value: '', label: '全部学校' },
          ...schoolFilters.map((item) => ({
            value: item.university,
            label: `${item.university}（${item.count}）`,
          })),
        ]}
      />
      <DashboardFilterSelect
        label="学院"
        ariaLabel="学院筛选"
        value={selectedSchool ?? ''}
        disabled={!selectedUniversity || schoolOptions.length === 0}
        onChange={(value) => onSchoolChange(value || null)}
        options={[
          { value: '', label: selectedUniversity ? '全部学院' : '请先选择学校' },
          ...schoolOptions.map((item) => ({
            value: item.school_name,
            label: `${item.school_name}（${item.count}）`,
          })),
        ]}
      />
      <button type="button" onClick={onClear} className="ui-btn-secondary px-4 py-2 text-sm">
        清空筛选
      </button>
    </div>
  </article>
);

type DashboardFilterSelectOption = {
  value: string;
  label: string;
};

function DashboardFilterSelect({
  label,
  ariaLabel,
  value,
  disabled = false,
  options,
  onChange,
}: {
  label: string;
  ariaLabel: string;
  value: string;
  disabled?: boolean;
  options: DashboardFilterSelectOption[];
  onChange: (value: string) => void;
}) {
  return (
    <NativeSelectField
      label={label}
      ariaLabel={ariaLabel}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      wrapperClassName="min-w-0 flex-1"
    >
      {options.map((option) => (
        <option key={`${label}-${option.value || 'empty'}-${option.label}`} value={option.value}>
          {option.label}
        </option>
      ))}
    </NativeSelectField>
  );
}

const EmailOutreachFilterBar = ({
  schoolFilters,
  selectedUniversity,
  selectedSchool,
  schoolOptions,
  datePreset,
  onUniversityChange,
  onSchoolChange,
  onDatePresetChange,
  onClear,
}: {
  schoolFilters: DashboardSchoolFilterDTO[];
  selectedUniversity: string | null;
  selectedSchool: string | null;
  schoolOptions: DashboardSchoolFilterDTO['schools'];
  datePreset: string;
  onUniversityChange: (value: string | null) => void;
  onSchoolChange: (value: string | null) => void;
  onDatePresetChange: (value: string) => void;
  onClear: () => void;
}) => (
  <article data-testid="email-outreach-filters" className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
    <div className="flex flex-col gap-3 md:flex-row md:items-end">
      <DashboardFilterSelect
        label="时间范围"
        ariaLabel="邮件触达时间筛选"
        value={datePreset}
        onChange={onDatePresetChange}
        options={[
          { value: 'all', label: '全部时间' },
          { value: '7d', label: '最近 7 天' },
          { value: '30d', label: '最近 30 天' },
          { value: '90d', label: '最近 90 天' },
        ]}
      />
      <DashboardFilterSelect
        label="学校"
        ariaLabel="邮件触达学校筛选"
        value={selectedUniversity ?? ''}
        onChange={(value) => onUniversityChange(value || null)}
        options={[
          { value: '', label: '全部学校' },
          ...schoolFilters.map((item) => ({
            value: item.university,
            label: `${item.university}（${item.count}）`,
          })),
        ]}
      />
      <DashboardFilterSelect
        label="学院"
        ariaLabel="邮件触达学院筛选"
        value={selectedSchool ?? ''}
        disabled={!selectedUniversity || schoolOptions.length === 0}
        onChange={(value) => onSchoolChange(value || null)}
        options={[
          { value: '', label: selectedUniversity ? '全部学院' : '请先选择学校' },
          ...schoolOptions.map((item) => ({
            value: item.school_name,
            label: `${item.school_name}（${item.count}）`,
          })),
        ]}
      />
      <button type="button" onClick={onClear} className="ui-btn-secondary px-4 py-2 text-sm">
        清空筛选
      </button>
    </div>
  </article>
);

export const DashboardPage = () => {
  const { notifyError } = useNotification();
  const {
    selectedIdentityId,
    selectedLlmProfileId,
    selectedIdentity,
    selectedLlmProfile,
    loading: selectionLoading,
  } = useSelectionContext();
  const [overview, setOverview] = useState<DashboardOverviewDTO | null>(null);
  const [loading, setLoading] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [selectedUniversity, setSelectedUniversity] = useState<string | null>(null);
  const [selectedSchool, setSelectedSchool] = useState<string | null>(null);
  const [emailUniversity, setEmailUniversity] = useState<string | null>(null);
  const [emailSchool, setEmailSchool] = useState<string | null>(null);
  const [emailDatePreset, setEmailDatePreset] = useState('all');
  const [activeSectionId, setActiveSectionId] = useState<string>('mentor');
  const [sectionNavTop, setSectionNavTop] = useState<number | null>(null);
  const requestIdRef = useRef(0);
  const summaryCardRef = useRef<HTMLElement | null>(null);
  const mentorSectionRef = useRef<HTMLElement | null>(null);
  const emailSectionRef = useRef<HTMLElement | null>(null);
  const tokenSectionRef = useRef<HTMLElement | null>(null);
  const emailDateRange = useMemo(() => getEmailDateRange(emailDatePreset), [emailDatePreset]);
  const dashboardRequestKey =
    selectedIdentityId && selectedLlmProfileId
      ? [
          selectedIdentityId,
          selectedLlmProfileId,
          selectedUniversity ?? '',
          selectedSchool ?? '',
          emailUniversity ?? '',
          emailSchool ?? '',
          emailDateRange.startDate ?? '',
          emailDateRange.endDate ?? '',
        ].join(':')
      : null;

  const loadOverview = useCallback(async () => {
    if (!selectedIdentityId || !selectedLlmProfileId || !dashboardRequestKey) {
      requestIdRef.current += 1;
      setOverview(null);
      setHasLoaded(false);
      setLoading(false);
      setErrorMessage(null);
      return;
    }

    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    setErrorMessage(null);
    try {
      const data = await getDashboardOverview({
        identityId: selectedIdentityId,
        llmProfileId: selectedLlmProfileId,
        university: selectedUniversity,
        school: selectedSchool,
        emailUniversity,
        emailSchool,
        startDate: emailDateRange.startDate,
        endDate: emailDateRange.endDate,
      });
      if (requestIdRef.current !== requestId) {
        return;
      }
      setOverview(data);
      setHasLoaded(true);
    } catch (error) {
      if (requestIdRef.current !== requestId) {
        return;
      }
      const message = error instanceof Error ? error.message : '加载统计面板失败';
      setErrorMessage(message);
      notifyError('加载统计面板失败', message);
    } finally {
      if (requestIdRef.current === requestId) {
        setLoading(false);
      }
    }
  }, [
    dashboardRequestKey,
    emailDateRange.endDate,
    emailDateRange.startDate,
    emailSchool,
    emailUniversity,
    notifyError,
    selectedIdentityId,
    selectedLlmProfileId,
    selectedSchool,
    selectedUniversity,
  ]);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  const updateSectionNavTop = useCallback(() => {
    if (typeof window === 'undefined') {
      return;
    }

    const rootFontSize = Number.parseFloat(window.getComputedStyle(document.documentElement).fontSize) || 16;
    const headerBottom =
      document.querySelector<HTMLElement>('[data-app-header="true"]')?.getBoundingClientRect().bottom ?? 0;
    const summaryCardBottom = summaryCardRef.current?.getBoundingClientRect().bottom ?? 0;
    const nextTop = resolveStatisticsSectionNavTop({ headerBottom, summaryCardBottom, rootFontSize });

    setSectionNavTop((currentTop) =>
      currentTop !== null && Math.abs(currentTop - nextTop) < 1 ? currentTop : nextTop,
    );
  }, []);

  useEffect(() => {
    if (!overview) {
      setSectionNavTop(null);
      return;
    }

    updateSectionNavTop();
    window.addEventListener('scroll', updateSectionNavTop, { passive: true });
    window.addEventListener('resize', updateSectionNavTop);

    return () => {
      window.removeEventListener('scroll', updateSectionNavTop);
      window.removeEventListener('resize', updateSectionNavTop);
    };
  }, [overview, updateSectionNavTop]);

  useEffect(() => {
    setSelectedUniversity(null);
    setSelectedSchool(null);
    setEmailUniversity(null);
    setEmailSchool(null);
    setEmailDatePreset('all');
  }, [selectedIdentityId, selectedLlmProfileId]);

  useEffect(() => {
    if (!overview || typeof window === 'undefined' || typeof window.IntersectionObserver === 'undefined') {
      return;
    }

    const visibleRatios = new Map<string, number>();
    const observer = new window.IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const sectionId = entry.target.getAttribute('data-statistics-section');
          if (!sectionId) {
            return;
          }

          if (entry.isIntersecting) {
            visibleRatios.set(sectionId, entry.intersectionRatio);
            return;
          }

          visibleRatios.delete(sectionId);
        });

        const nextActiveSection = statisticsSectionItems
          .map((item) => ({
            id: item.id,
            ratio: visibleRatios.get(item.id) ?? 0,
          }))
          .sort((first, second) => second.ratio - first.ratio)[0];

        if (nextActiveSection && nextActiveSection.ratio > 0) {
          setActiveSectionId(nextActiveSection.id);
        }
      },
      {
        rootMargin: '-18% 0px -52% 0px',
        threshold: [0.05, 0.1, 0.2, 0.4, 0.6, 0.8],
      },
    );

    [mentorSectionRef.current, emailSectionRef.current, tokenSectionRef.current].forEach((section) => {
      if (section) {
        observer.observe(section);
      }
    });

    return () => {
      observer.disconnect();
    };
  }, [overview]);

  const handleSectionSelect = useCallback((sectionId: string) => {
    const sectionElement =
      sectionId === 'mentor'
        ? mentorSectionRef.current
        : sectionId === 'email'
          ? emailSectionRef.current
          : tokenSectionRef.current;

    if (!sectionElement) {
      return;
    }

    setActiveSectionId(sectionId);
    sectionElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const mentorMetrics = useMemo(() => {
    if (!overview) {
      return [];
    }
    const summary = overview.mentor.summary;
    return [
      {
        title: '导师总数',
        value: formatNumber(summary.total_professors),
        helper: '当前导师库未归档导师',
        icon: <Users className="h-5 w-5" />,
        tone: 'teal' as const,
      },
      {
        title: '已匹配导师',
        value: formatNumber(summary.matched_professors),
        helper: `覆盖率 ${formatPercent(summary.matched_rate)}`,
        icon: <BadgeCheck className="h-5 w-5" />,
        tone: 'sky' as const,
      },
      {
        title: '高匹配导师',
        value: formatNumber(summary.high_match_professors),
        helper: `匹配分不低于 ${summary.high_score_threshold}`,
        icon: <Star className="h-5 w-5" />,
        tone: 'amber' as const,
      },
    ];
  }, [overview]);

  const emailMetrics = useMemo(() => {
    if (!overview) {
      return [];
    }
    const summary = overview.email.summary;
    const contactedProfessorCount = Number.isFinite(summary.contacted_professor_count)
      ? summary.contacted_professor_count
      : summary.sent_count;
    return [
      {
        title: '已发送邮件',
        value: formatNumber(summary.sent_count),
        helper: '当前身份和模型下',
        icon: <Send className="h-5 w-5" />,
        tone: 'teal' as const,
      },
      {
        title: '已回复',
        value: formatNumber(summary.replied_count),
        helper: '收到回复的邮件',
        icon: <Reply className="h-5 w-5" />,
        tone: 'sky' as const,
      },
      {
        title: '回复率',
        value: formatPercent(summary.reply_rate),
        helper: `${formatNumber(summary.replied_count)} / ${formatNumber(contactedProfessorCount)} 位导师`,
        icon: <Percent className="h-5 w-5" />,
        tone: 'violet' as const,
      },
    ];
  }, [overview]);

  const selectedSchoolOptions = useMemo(() => {
    if (!overview || !selectedUniversity) {
      return [];
    }
    return overview.mentor.school_filters.find((item) => item.university === selectedUniversity)?.schools ?? [];
  }, [overview, selectedUniversity]);

  const emailSchoolOptions = useMemo(() => {
    if (!overview || !emailUniversity) {
      return [];
    }
    return overview.mentor.school_filters.find((item) => item.university === emailUniversity)?.schools ?? [];
  }, [emailUniversity, overview]);

  const schoolDistributionData = useMemo(
    () =>
      overview?.mentor.school_distribution.map((item) => ({
        key: item.school_name,
        label: item.school_name,
        count: item.count,
      })) ?? [],
    [overview],
  );

  const profileCompletenessData = useMemo(
    () =>
      overview?.mentor.profile_completeness_distribution.map((item: DashboardProfileCompletenessBucketDTO) => ({
        key: item.key,
        label: item.label,
        count: item.count,
      })) ?? [],
    [overview],
  );

  const sectionNavStyle = useMemo<CSSProperties | undefined>(
    () =>
      sectionNavTop === null
        ? undefined
        : ({
            '--statistics-section-nav-top': `${sectionNavTop}px`,
          } as CSSProperties),
    [sectionNavTop],
  );

  if (selectionLoading || (loading && !hasLoaded)) {
    return <DashboardLoadingSkeleton />;
  }

  if (!selectedIdentityId || !selectedLlmProfileId || !selectedIdentity || !selectedLlmProfile) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-8">
        <section className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-8 text-center shadow-sm">
          <h1 className="text-2xl font-semibold text-stone-950">统计面板</h1>
          <p className="mt-3 text-sm text-stone-500">请先选择身份和模型。</p>
          <Link to="/profile" data-interactive="button" className="ui-btn-primary mt-5">
            去个人中心配置
          </Link>
        </section>
      </main>
    );
  }

  return (
    <main data-testid="statistics-panel" className="mx-auto max-w-7xl px-6 py-8">
      <section ref={summaryCardRef} className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-6 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-3xl font-semibold text-stone-950">统计面板</h1>
          </div>
          <button
            type="button"
            onClick={() => void loadOverview()}
            disabled={loading}
            className="ui-btn-secondary shrink-0 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
            刷新统计
          </button>
        </div>
        {errorMessage ? (
          <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {errorMessage}
          </div>
        ) : null}
      </section>

      {overview ? (
        <div data-testid="statistics-sections-shell" className="relative mt-8 lg:pl-24 xl:pl-24">
          <StatisticsSectionNav
            className="mb-6 lg:mb-0 lg:left-[max(-0.5rem,calc((100vw-80rem)/2-0.5rem))]"
            style={sectionNavStyle}
            items={statisticsSectionItems}
            activeSectionId={activeSectionId}
            onSelect={handleSectionSelect}
          />

          <div className="space-y-10">
            <section
              ref={mentorSectionRef}
              id="statistics-mentor"
              data-testid="statistics-section-mentor"
              data-statistics-section="mentor"
              className="scroll-mt-44"
            >
              <ModuleHeader
                title="导师概览"
                description="导师池规模、资料质量和高价值待推进导师"
                icon={<GraduationCap className="h-5 w-5" />}
              />
              <div
                data-testid="mentor-overview-grid"
                className="mt-4 grid items-start gap-4 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,0.85fr)_minmax(0,0.85fr)_minmax(22rem,1.45fr)] lg:items-stretch"
              >
                <MentorFilterBar
                  className="w-full lg:col-span-3"
                  schoolFilters={overview.mentor.school_filters}
                  selectedUniversity={selectedUniversity}
                  selectedSchool={selectedSchool}
                  schoolOptions={selectedSchoolOptions}
                  onUniversityChange={(value) => {
                    setSelectedUniversity(value);
                    setSelectedSchool(null);
                  }}
                  onSchoolChange={setSelectedSchool}
                  onClear={() => {
                    setSelectedUniversity(null);
                    setSelectedSchool(null);
                  }}
                />
                {mentorMetrics.map((metric) => (
                  <MetricCard key={metric.title} {...metric} />
                ))}
                <ChartCard
                  className="h-full min-w-0 lg:col-start-4 lg:row-start-1 lg:row-span-2"
                  testId="mentor-profile-completeness-card"
                  title="资料完整度概览"
                >
                  <DistributionPieChart
                    title="资料完整度概览"
                    data={profileCompletenessData}
                    emptyText="当前筛选下暂无导师"
                    legendLayout="horizontal-scroll"
                  />
                </ChartCard>
              </div>
              <div
                data-testid="mentor-detail-grid"
                className="mt-4 grid items-start gap-4"
                style={mentorDetailGridStyle}
              >
                <ChartCard
                  className="h-[22rem] overflow-hidden"
                  testId="mentor-match-distribution-card"
                  title="匹配分数分布"
                >
                  <MatchDistributionChart data={overview.mentor.match_score_distribution} />
                </ChartCard>
                <ChartCard
                  className="h-[22rem] overflow-hidden"
                  testId="mentor-school-distribution-card"
                  title="学校分布"
                >
                  <DistributionPieChart
                    title="学校分布"
                    data={schoolDistributionData}
                    emptyText="暂无学校分布数据"
                    legendLayout="columns"
                    valueSuffix="位"
                  />
                </ChartCard>
              </div>
            </section>

            <section
              ref={emailSectionRef}
              id="statistics-email"
              data-testid="statistics-section-email"
              data-statistics-section="email"
              className="scroll-mt-44"
            >
              <ModuleHeader
                title="邮件触达"
                description="发送进度、回复效果和触达趋势"
                icon={<ClipboardCheck className="h-5 w-5" />}
              />
              <div className="mb-4">
                <EmailOutreachFilterBar
                  schoolFilters={overview.mentor.school_filters}
                  selectedUniversity={emailUniversity}
                  selectedSchool={emailSchool}
                  schoolOptions={emailSchoolOptions}
                  datePreset={emailDatePreset}
                  onUniversityChange={(value) => {
                    setEmailUniversity(value);
                    setEmailSchool(null);
                  }}
                  onSchoolChange={setEmailSchool}
                  onDatePresetChange={setEmailDatePreset}
                  onClear={() => {
                    setEmailUniversity(null);
                    setEmailSchool(null);
                    setEmailDatePreset('all');
                  }}
                />
              </div>
              <div data-testid="email-metrics-grid" className="grid gap-4" style={emailMetricsGridStyle}>
                {emailMetrics.map((metric) => (
                  <MetricCard key={metric.title} {...metric} />
                ))}
              </div>
              <div data-testid="email-trend-grid" className="mt-4 grid grid-cols-1 gap-4">
                <ChartCard testId="email-trend-card" title="发送趋势">
                  <TrendChart data={overview.email.trend_30_days} />
                </ChartCard>
              </div>
            </section>

            <section
              ref={tokenSectionRef}
              id="statistics-token"
              data-testid="statistics-section-token"
              data-statistics-section="token"
              className="scroll-mt-44"
            >
              <TokenVisualizationPanel />
            </section>
          </div>
        </div>
      ) : (
        <section className="mt-6 rounded-3xl border border-stone-200 bg-white p-8 text-center text-sm text-stone-500 shadow-sm">
          暂无统计数据。
        </section>
      )}
    </main>
  );
};
