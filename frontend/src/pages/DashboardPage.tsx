import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
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
import { parseApiDateTime } from '@/lib/dateTime';
import {
  ArrowDown,
  ArrowUp,
  BadgeCheck,
  ClipboardCheck,
  GraduationCap,
  Link2,
  Loader2,
  Percent,
  RefreshCcw,
  Reply,
  Send,
  Star,
  UserRoundCheck,
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
import { resolveStatisticsSectionNavTop } from '@/lib/statisticsSectionNav';
import type {
  DashboardEmailTrendBucketDTO,
  DashboardOverviewDTO,
  DashboardProfileCompletenessBucketDTO,
  DashboardReplyWaitDTO,
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
  const date = parseApiDateTime(value);
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
  // time-check: local-control-value, dashboard local date filter clones the current Date object.
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

const emailDatePresetLabels: Record<string, string> = {
  all: '全部时间',
  '7d': '最近 7 天',
  '30d': '最近 30 天',
  '90d': '最近 90 天',
};

type CoverageRankingLevel = 'university' | 'school';
type CoverageSortDirection = 'asc' | 'desc';
type ContactEffectMetric = 'coverage' | 'reply';

const formatDurationHours = (value: number | null) => {
  if (value === null || !Number.isFinite(value)) {
    return '—';
  }
  if (value < 1) {
    return '< 1 小时';
  }
  if (value < 24) {
    return `${Math.round(value)} 小时`;
  }
  const days = value / 24;
  const formattedDays = days >= 10 ? Math.round(days).toString() : days.toFixed(1).replace(/\.0$/, '');
  return `${formattedDays} 天`;
};

type MetricTone = 'teal' | 'amber' | 'rose' | 'sky' | 'violet' | 'stone';

const mentorDetailGridStyle = {
  gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 28rem), 1fr))',
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
      正在加载统计数据…
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
  const unmatchedCount = data.find((item) => item.bucket === 'unmatched')?.count ?? 0;
  const scoreDistribution = data.filter((item) => item.bucket !== 'unmatched');
  const analyzedCount = scoreDistribution.reduce((sum, item) => sum + item.count, 0);
  const total = analyzedCount + unmatchedCount;
  const analyzedRate = total > 0 ? analyzedCount / total : 0;
  const analyzedPercentage = Math.min(100, Math.max(0, analyzedRate * 100));

  if (total === 0) {
    return <EmptyState>暂无匹配分析数据</EmptyState>;
  }

  return (
    <div className="space-y-3">
      <section
        data-testid="match-analysis-coverage"
        className="rounded-xl border border-stone-200 bg-stone-50 px-3 py-3"
      >
        <div className="flex items-center justify-between gap-3 text-xs">
          <span className="font-medium text-stone-600">分析覆盖率</span>
          <span className="whitespace-nowrap text-stone-500">
            <strong className="font-semibold text-stone-900">
              {formatNumber(analyzedCount)} / {formatNumber(total)} 位
            </strong>
            {' · '}{formatPercent(analyzedRate)}
          </span>
        </div>
        <div
          role="progressbar"
          aria-label={`匹配分析覆盖率 ${formatPercent(analyzedRate)}，已分析 ${formatNumber(analyzedCount)} / ${formatNumber(total)} 位导师`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(analyzedPercentage)}
          className="mt-2 h-2.5 overflow-hidden rounded-full bg-stone-200"
        >
          <span
            data-testid="match-analysis-coverage-bar"
            className="block h-full rounded-full bg-teal-500 transition-[width] duration-300"
            style={{ width: `${analyzedPercentage}%` }}
          />
        </div>
        <div className="mt-1.5 flex items-center justify-between gap-3 text-[11px] text-stone-500">
          <span>已分析 {formatNumber(analyzedCount)} 位</span>
          <span>未分析 {formatNumber(unmatchedCount)} 位</span>
        </div>
      </section>

      <section data-testid="match-score-distribution">
        <div className="mb-2 flex items-center justify-between gap-3 text-xs">
          <span className="font-medium text-stone-600">匹配分分布</span>
          <span className="whitespace-nowrap text-stone-500">共 {formatNumber(analyzedCount)} 位</span>
        </div>
        {analyzedCount === 0 ? (
          <div className="flex min-h-28 items-center justify-center rounded-xl border border-dashed border-stone-200 bg-stone-50 px-3 text-center text-xs text-stone-500">
            暂无已分析导师
          </div>
        ) : (
          <div className="space-y-2">
            {scoreDistribution.map((item) => {
              const share = item.count / analyzedCount;
              const percentage = Math.min(100, Math.max(0, share * 100));
              return (
                <div
                  key={item.bucket}
                  role="img"
                  aria-label={`${item.label}，${formatNumber(item.count)} 位导师，占已分析导师 ${formatPercent(share)}`}
                  data-testid={`match-score-row-${item.bucket}`}
                  className="grid grid-cols-[4rem_minmax(3rem,1fr)_auto] items-center gap-2 text-[11px]"
                >
                  <span className="font-medium text-stone-600">{item.label}</span>
                  <span className="h-2.5 overflow-hidden rounded-full bg-stone-100" aria-hidden="true">
                    <span
                      data-testid={`match-score-bar-${item.bucket}`}
                      className="block h-full rounded-full bg-teal-500 transition-[width] duration-300"
                      style={{ width: `${percentage}%` }}
                    />
                  </span>
                  <span className="whitespace-nowrap text-stone-500">
                    <strong className="font-semibold text-stone-900">{formatNumber(item.count)} 位</strong>
                    {' · '}{formatPercent(share)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
};

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

const OutreachCoverageRanking = ({
  data,
  level,
  metric,
  sortDirection,
  selectedUniversity,
  selectedSchool,
  dateLabel,
  onLevelChange,
  onMetricChange,
  onSortDirectionChange,
  onSelectUniversity,
  onSelectSchool,
  onClearSchool,
}: {
  data: DashboardOverviewDTO['email']['outreach_coverage'];
  level: CoverageRankingLevel;
  metric: ContactEffectMetric;
  sortDirection: CoverageSortDirection;
  selectedUniversity: string | null;
  selectedSchool: string | null;
  dateLabel: string;
  onLevelChange: (level: CoverageRankingLevel) => void;
  onMetricChange: (metric: ContactEffectMetric) => void;
  onSortDirectionChange: (direction: CoverageSortDirection) => void;
  onSelectUniversity: (university: string) => void;
  onSelectSchool: (university: string, school: string) => void;
  onClearSchool: () => void;
}) => {
  const visibleData = useMemo(() => {
    const items = level === 'university' ? data.universities : data.schools;
    const scopedItems =
      level === 'school' && selectedUniversity
        ? items.filter((item) => item.university === selectedUniversity)
        : items;
    return [...scopedItems].sort((first, second) => {
      if (metric === 'reply') {
        const firstHasSample = first.contacted_professor_count > 0;
        const secondHasSample = second.contacted_professor_count > 0;
        if (firstHasSample !== secondHasSample) {
          return firstHasSample ? -1 : 1;
        }
      }

      const firstRate = metric === 'reply' ? first.reply_rate : first.sent_professor_rate;
      const secondRate = metric === 'reply' ? second.reply_rate : second.sent_professor_rate;
      const primaryDifference = (sortDirection === 'asc' ? 1 : -1) * (firstRate - secondRate);
      const sampleDifference = metric === 'reply'
        ? second.contacted_professor_count - first.contacted_professor_count
        : second.unsent_professor_count - first.unsent_professor_count;

      return primaryDifference
        || sampleDifference
        || second.total_professor_count - first.total_professor_count
        || first.university.localeCompare(second.university, 'zh-CN')
        || (first.school ?? '').localeCompare(second.school ?? '', 'zh-CN');
    });
  }, [data.schools, data.universities, level, metric, selectedUniversity, sortDirection]);

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="outreach-coverage-ranking">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-stone-500">{dateLabel}</p>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <div
            role="group"
            aria-label="院校联系效果指标"
            className="inline-flex rounded-lg border border-stone-200 bg-stone-50 p-1"
          >
            {([
              ['coverage', '联系覆盖率'],
              ['reply', '回复率'],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={metric === value}
                onClick={() => onMetricChange(value)}
                className={clsx(
                  'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                  metric === value
                    ? value === 'reply'
                      ? 'bg-violet-600 text-white shadow-sm'
                      : 'bg-teal-600 text-white shadow-sm'
                    : 'text-stone-500 hover:text-stone-800',
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <div
            role="group"
            aria-label="院校联系效果排行层级"
            className="inline-flex rounded-lg border border-stone-200 bg-stone-50 p-1"
          >
            {([
              ['university', '学校'],
              ['school', '学院'],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={level === value}
                onClick={() => onLevelChange(value)}
                className={clsx(
                  'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                  level === value
                    ? 'bg-white text-stone-900 shadow-sm ring-1 ring-stone-200'
                    : 'text-stone-500 hover:text-stone-800',
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <div
            role="group"
            aria-label="院校联系效果排序方向"
            className="inline-flex rounded-lg border border-stone-200 bg-stone-50 p-1"
          >
            {([
              ['asc', '升序', ArrowUp],
              ['desc', '降序', ArrowDown],
            ] as const).map(([value, label, DirectionIcon]) => (
              <button
                key={value}
                type="button"
                aria-pressed={sortDirection === value}
                onClick={() => onSortDirectionChange(value)}
                className={clsx(
                  'inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors',
                  sortDirection === value
                    ? 'bg-white text-stone-900 shadow-sm ring-1 ring-stone-200'
                    : 'text-stone-500 hover:text-stone-800',
                )}
              >
                <DirectionIcon className="h-3 w-3" aria-hidden="true" />
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>
      {level === 'school' && selectedSchool ? (
        <div className="mb-2 flex items-center justify-between gap-3 rounded-lg bg-teal-50 px-3 py-2 text-xs text-teal-800">
          <span className="truncate">已高亮：{selectedSchool}</span>
          <button type="button" onClick={onClearSchool} className="shrink-0 font-medium hover:text-teal-950">
            清除学院筛选
          </button>
        </div>
      ) : null}
      {visibleData.length === 0 ? (
        <EmptyState>当前范围暂无院校联系数据</EmptyState>
      ) : (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {visibleData.map((item) => {
            const label =
              level === 'school' && !selectedUniversity
                ? `${item.university} · ${item.label}`
                : item.label;
            const selected =
              level === 'university'
                ? selectedUniversity === item.university
                : selectedUniversity === item.university && selectedSchool === item.school;
            const hasReplySample = item.contacted_professor_count > 0;
            const hasSmallReplySample = hasReplySample && item.contacted_professor_count < 5;
            const activeRate = metric === 'reply'
              ? hasReplySample
                ? item.reply_rate
                : 0
              : item.sent_professor_rate;
            const percentage = Math.min(100, Math.max(0, activeRate * 100));
            const replyDescription = hasReplySample
              ? `已回复 ${item.replied_professor_count} / ${item.contacted_professor_count} 位导师，回复率 ${formatPercent(item.reply_rate)}`
              : '尚无联系记录，无法计算回复率。';
            const handleSelect = () => {
              if (level === 'university') {
                onSelectUniversity(item.university);
                return;
              }
              if (item.school) {
                onSelectSchool(item.university, item.school);
              }
            };

            return (
              <button
                key={`${level}-${item.university}-${item.school ?? ''}`}
                type="button"
                data-testid={`coverage-ranking-row-${level}-${item.university}-${item.school ?? 'all'}`}
                aria-label={`${label}，已联系 ${item.sent_professor_count} / ${item.total_professor_count} 位导师，联系覆盖率 ${formatPercent(item.sent_professor_rate)}；${replyDescription}`}
                onClick={handleSelect}
                className={clsx(
                  'grid w-full gap-2 rounded-xl border px-3 py-2.5 text-left transition-colors sm:grid-cols-[minmax(7rem,0.9fr)_minmax(8rem,1.2fr)_auto] sm:items-center',
                  selected
                    ? 'border-teal-200 bg-teal-50/80'
                    : 'border-transparent bg-stone-50 hover:border-stone-200 hover:bg-white',
                )}
              >
                <span className="min-w-0 truncate text-xs font-medium text-stone-700" title={label}>
                  {label}
                </span>
                <span className="h-2.5 overflow-hidden rounded-full bg-stone-200" aria-hidden="true">
                  <span
                    className={clsx(
                      'block h-full rounded-full transition-[width] duration-300',
                      metric === 'reply' ? 'bg-violet-500' : 'bg-teal-500',
                    )}
                    style={{ width: `${percentage}%` }}
                  />
                </span>
                <span className="min-w-0 text-xs text-stone-500 sm:text-right">
                  <span className="block whitespace-nowrap">
                    {metric === 'coverage' ? (
                      <>
                        <strong className="font-semibold text-stone-900">
                          已联系 {formatNumber(item.sent_professor_count)} / {formatNumber(item.total_professor_count)}
                        </strong>
                        {' · '}{formatPercent(item.sent_professor_rate)}
                      </>
                    ) : hasReplySample ? (
                      <>
                        <strong className="font-semibold text-stone-900">
                          已回复 {formatNumber(item.replied_professor_count)} / {formatNumber(item.contacted_professor_count)}
                        </strong>
                        {' · '}{formatPercent(item.reply_rate)}
                      </>
                    ) : (
                      <strong className="font-semibold text-stone-700">暂无联系样本 · —</strong>
                    )}
                  </span>
                  <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-stone-500 sm:justify-end">
                    <span>
                      {metric === 'coverage'
                        ? hasReplySample
                          ? `回复 ${formatNumber(item.replied_professor_count)} / ${formatNumber(item.contacted_professor_count)} · ${formatPercent(item.reply_rate)}`
                          : '回复：暂无联系样本'
                        : `联系 ${formatNumber(item.sent_professor_count)} / ${formatNumber(item.total_professor_count)} · ${formatPercent(item.sent_professor_rate)}`}
                    </span>
                    {metric === 'reply' && hasSmallReplySample ? (
                      <span className="rounded-full bg-amber-50 px-1.5 py-0.5 font-medium text-amber-700">样本较少</span>
                    ) : null}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

const ReplyWaitDistribution = ({
  data,
  dateLabel,
}: {
  data: DashboardReplyWaitDTO;
  dateLabel: string;
}) => {
  if (data.sample_count === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col" data-testid="reply-wait-distribution">
        <p className="mb-3 text-xs leading-5 text-stone-500">
          统计首次发送至首次回复的用时 · {dateLabel}
        </p>
        <EmptyState>当前范围暂无回复用时数据</EmptyState>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="reply-wait-distribution">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <p className="text-xs leading-5 text-stone-500">
          统计首次发送至首次回复的用时 · {dateLabel}
        </p>
        {data.sample_count < 5 ? (
          <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">样本较少</span>
        ) : null}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl border border-stone-200 bg-stone-50 px-3 py-3">
          <div className="text-xs text-stone-500">中位回复用时</div>
          <div className="mt-1.5 text-xl font-semibold text-stone-950">{formatDurationHours(data.median_hours)}</div>
        </div>
        <div className="rounded-xl border border-stone-200 bg-stone-50 px-3 py-3">
          <div className="text-xs text-stone-500">已回复样本</div>
          <div className="mt-1.5 text-xl font-semibold text-stone-950">{formatNumber(data.sample_count)} 位</div>
        </div>
      </div>
      <p className="mt-3 text-xs text-stone-500">
        75% 的首次回复发生在 <span className="font-medium text-stone-700">{formatDurationHours(data.p75_hours)}</span> 内
      </p>
      <div
        data-testid="reply-wait-distribution-list"
        className="mt-3 min-h-0 flex-1 space-y-2 rounded-xl border border-stone-200 bg-white p-3"
      >
        {data.distribution.map((item) => {
          const percentage = Math.min(100, Math.max(0, item.rate * 100));
          return (
            <div
              key={item.key}
              role="img"
              aria-label={`${item.label}，${formatNumber(item.count)} 位导师，占比 ${formatPercent(item.rate)}`}
              data-testid={`reply-wait-row-${item.key}`}
              className="grid grid-cols-[5rem_minmax(3rem,1fr)_auto] items-center gap-2 text-[11px]"
            >
              <span className="font-medium text-stone-600">{item.label}</span>
              <span className="h-2.5 overflow-hidden rounded-full bg-stone-100" aria-hidden="true">
                <span
                  data-testid={`reply-wait-bar-${item.key}`}
                  className="block h-full rounded-full bg-sky-500 transition-[width] duration-300"
                  style={{ width: `${percentage}%` }}
                />
              </span>
              <span className="whitespace-nowrap text-stone-500">
                <strong className="font-semibold text-stone-900">{formatNumber(item.count)} 位</strong>
                {' · '}{formatPercent(item.rate)}
              </span>
            </div>
          );
        })}
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
        ariaLabel="联系进展时间筛选"
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
        ariaLabel="联系进展学校筛选"
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
        ariaLabel="联系进展学院筛选"
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
    selectedIdentity,
    communicationIdentityIds = [],
    communicationScopeKey = '',
    matchScopeKey = '',
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
  const [coverageRankingLevel, setCoverageRankingLevel] = useState<CoverageRankingLevel>('university');
  const [coverageRankingMetric, setCoverageRankingMetric] = useState<ContactEffectMetric>('coverage');
  const [coverageSortDirection, setCoverageSortDirection] = useState<CoverageSortDirection>('asc');
  const [activeSectionId, setActiveSectionId] = useState<string>('mentor');
  const [sectionNavTop, setSectionNavTop] = useState<number | null>(null);
  const requestIdRef = useRef(0);
  const summaryCardRef = useRef<HTMLElement | null>(null);
  const mentorSectionRef = useRef<HTMLElement | null>(null);
  const emailSectionRef = useRef<HTMLElement | null>(null);
  const tokenSectionRef = useRef<HTMLElement | null>(null);
  const previousSelectedIdentityIdRef = useRef(selectedIdentityId);
  const emailDateRange = useMemo(() => getEmailDateRange(emailDatePreset), [emailDatePreset]);
  const dashboardRequestKey =
    selectedIdentityId
      ? [
          selectedIdentityId,
          communicationScopeKey || selectedIdentityId,
          matchScopeKey || selectedIdentityId,
          selectedUniversity ?? '',
          selectedSchool ?? '',
          emailUniversity ?? '',
          emailSchool ?? '',
          emailDateRange.startDate ?? '',
          emailDateRange.endDate ?? '',
        ].join(':')
      : null;

  const loadOverview = useCallback(async () => {
    if (!selectedIdentityId || !dashboardRequestKey) {
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

  useLayoutEffect(() => {
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
    if (previousSelectedIdentityIdRef.current === selectedIdentityId) {
      return;
    }
    previousSelectedIdentityIdRef.current = selectedIdentityId;
    setSelectedUniversity(null);
    setSelectedSchool(null);
    setEmailUniversity(null);
    setEmailSchool(null);
    setEmailDatePreset('all');
    setCoverageRankingLevel('university');
    setCoverageRankingMetric('coverage');
    setCoverageSortDirection('asc');
  }, [selectedIdentityId]);

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
    const sentProfessorCount = Number.isFinite(summary.sent_professor_count)
      ? summary.sent_professor_count
      : contactedProfessorCount;
    const totalProfessorCount = Number.isFinite(summary.total_professor_count)
      ? summary.total_professor_count
      : overview.mentor.summary.total_professors;
    const sentProfessorRate = Number.isFinite(summary.sent_professor_rate)
      ? summary.sent_professor_rate
      : totalProfessorCount > 0
        ? sentProfessorCount / totalProfessorCount
        : 0;
    return [
      {
        title: '已发送邮件',
        value: formatNumber(summary.sent_count),
        helper: communicationIdentityIds.length > 1 ? '共享通信' : '当前身份下',
        icon: <Send className="h-5 w-5" />,
        tone: 'teal' as const,
      },
      {
        title: '导师联系覆盖率',
        value: formatPercent(sentProfessorRate),
        helper: `已联系 ${formatNumber(sentProfessorCount)} / ${formatNumber(totalProfessorCount)} 位导师`,
        icon: <UserRoundCheck className="h-5 w-5" />,
        tone: 'amber' as const,
      },
      {
        title: '已回复',
        value: formatNumber(summary.replied_count),
        helper: '收到回复的导师',
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
  }, [communicationIdentityIds.length, overview]);

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

  if (!selectedIdentityId || !selectedIdentity) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-8">
        <section className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-8 text-center shadow-sm">
          <h1 className="text-2xl font-semibold text-stone-950">统计面板</h1>
          <p className="mt-3 text-sm text-stone-500">选择身份后查看统计。</p>
          <Link to="/profile" data-interactive="button" className="ui-btn-primary mt-5">
            设置身份
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
                description="导师规模、资料与匹配度"
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
                title="联系进展"
                description="发送、回复与院校表现"
                icon={<ClipboardCheck className="h-5 w-5" />}
              />
              {communicationIdentityIds.length > 1 ? (
                <div className="mb-4 inline-flex items-center gap-2 rounded-lg border border-teal-200 bg-teal-50 px-3 py-2 text-xs font-medium text-teal-800">
                  <Link2 className="h-4 w-4" />
                  共享通信 · {communicationIdentityIds.length} 个身份
                </div>
              ) : null}
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
                    setCoverageRankingLevel(value ? 'school' : 'university');
                  }}
                  onSchoolChange={(value) => {
                    setEmailSchool(value);
                    if (value) {
                      setCoverageRankingLevel('school');
                    }
                  }}
                  onDatePresetChange={setEmailDatePreset}
                  onClear={() => {
                    setEmailUniversity(null);
                    setEmailSchool(null);
                    setEmailDatePreset('all');
                    setCoverageRankingLevel('university');
                  }}
                />
              </div>
              <div
                data-testid="email-metrics-grid"
                className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
              >
                {emailMetrics.map((metric) => (
                  <MetricCard key={metric.title} {...metric} />
                ))}
              </div>
              <div data-testid="email-trend-grid" className="mt-4 grid grid-cols-1 gap-4">
                <ChartCard testId="email-trend-card" title="发送趋势">
                  <TrendChart data={overview.email.trend_30_days} />
                </ChartCard>
              </div>
              <div
                data-testid="email-insight-grid"
                className="mt-4 grid items-stretch gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(22rem,0.9fr)]"
              >
                <ChartCard
                  className="flex h-[24rem] min-w-0 flex-col overflow-hidden"
                  testId="outreach-coverage-card"
                  title="院校联系效果"
                >
                  <OutreachCoverageRanking
                    data={overview.email.outreach_coverage ?? { universities: [], schools: [] }}
                    level={coverageRankingLevel}
                    metric={coverageRankingMetric}
                    sortDirection={coverageSortDirection}
                    selectedUniversity={emailUniversity}
                    selectedSchool={emailSchool}
                    dateLabel={`${emailDatePresetLabels[emailDatePreset] ?? '当前时间范围'}内`}
                    onLevelChange={setCoverageRankingLevel}
                    onMetricChange={setCoverageRankingMetric}
                    onSortDirectionChange={setCoverageSortDirection}
                    onSelectUniversity={(university) => {
                      setEmailUniversity(university);
                      setEmailSchool(null);
                      setCoverageRankingLevel('school');
                    }}
                    onSelectSchool={(university, school) => {
                      setEmailUniversity(university);
                      setEmailSchool(school);
                      setCoverageRankingLevel('school');
                    }}
                    onClearSchool={() => setEmailSchool(null)}
                  />
                </ChartCard>
                <ChartCard
                  className="flex h-[24rem] min-w-0 flex-col overflow-hidden"
                  testId="reply-wait-card"
                  title="首次回复用时分布"
                >
                  <ReplyWaitDistribution
                    data={overview.email.reply_wait ?? {
                      sample_count: 0,
                      median_hours: null,
                      p75_hours: null,
                      distribution: [],
                    }}
                    dateLabel={`${emailDatePresetLabels[emailDatePreset] ?? '当前时间范围'}内`}
                  />
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
