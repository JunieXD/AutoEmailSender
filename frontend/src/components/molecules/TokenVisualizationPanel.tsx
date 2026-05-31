import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import clsx from 'clsx';
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
  Download,
  Hash,
  Loader2,
  RefreshCw,
  Upload,
  Zap,
} from 'lucide-react';
import { DistributionPieChart } from '@/components/molecules/DistributionPieChart';
import { PageSizeSelector } from '@/components/molecules/PageSizeSelector';
import { getTokenUsageVisualization } from '@/lib/api/tokenUsage';
import {
  PAGE_SIZE as DEFAULT_PAGE_SIZE,
  getPageItems,
  getTotalPages,
} from '@/lib/pagination';
import type {
  TokenUsageChartDTO,
  TokenUsageChartPresetDTO,
  TokenUsageFeatureDistributionDTO,
  TokenUsageModelRankingDTO,
  TokenUsageRecordDTO,
  TokenUsageVisualizationDTO,
} from '@/types';
import {
  formatDateTimeLocalValue,
  formatTokenCompactValue,
  formatTokenShare,
  formatTokenUsageBucketLabel,
  formatTokenUsageRecordTime,
  parseDateTimeLocalValue,
} from '@/features/token-usage/client/tokenUsage';

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

const presetOptions: Array<{ value: TokenUsageChartPresetDTO; label: string }> = [
  { value: 'last_6_hours', label: '最近 6 小时' },
  { value: 'last_24_hours', label: '最近 24 小时' },
  { value: 'last_7_days', label: '最近 7 天' },
  { value: 'last_30_days', label: '最近 30 天' },
  { value: 'custom', label: '自定义范围' },
];

type MetricTone = 'teal' | 'amber' | 'sky' | 'violet';

type TrendDatasetKey = 'input_tokens' | 'output_tokens' | 'cached_tokens';

type TrendDatasetConfig = {
  key: TrendDatasetKey;
  label: string;
  borderColor: string;
  backgroundColor: string;
};

const trendDatasets: TrendDatasetConfig[] = [
  {
    key: 'input_tokens',
    label: '输入',
    borderColor: '#14b8a6',
    backgroundColor: 'rgba(20, 184, 166, 0.12)',
  },
  {
    key: 'output_tokens',
    label: '输出',
    borderColor: '#60a5fa',
    backgroundColor: 'rgba(96, 165, 250, 0.12)',
  },
  {
    key: 'cached_tokens',
    label: '缓存',
    borderColor: '#f59e0b',
    backgroundColor: 'rgba(245, 158, 11, 0.12)',
  },
];

const metricToneClasses: Record<MetricTone, { icon: string }> = {
  teal: { icon: 'bg-teal-50 text-teal-700' },
  amber: { icon: 'bg-amber-50 text-amber-700' },
  sky: { icon: 'bg-sky-50 text-sky-700' },
  violet: { icon: 'bg-violet-50 text-violet-700' },
};

export const TokenVisualizationPanel = () => {
  const [preset, setPreset] = useState<TokenUsageChartPresetDTO>('last_30_days');
  const [startAt, setStartAt] = useState<string | null>(null);
  const [endAt, setEndAt] = useState<string | null>(null);
  const [data, setData] = useState<TokenUsageVisualizationDTO | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  const loadData = useCallback(async () => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    setError(null);
    try {
      const result = await getTokenUsageVisualization({ preset, startAt, endAt });
      if (requestIdRef.current !== requestId) {
        return;
      }
      setData(result);
      setLoaded(true);
    } catch (loadError) {
      if (requestIdRef.current !== requestId) {
        return;
      }
      setError(loadError instanceof Error ? loadError.message : '加载 Token 可视化数据失败');
    } finally {
      if (requestIdRef.current === requestId) {
        setLoading(false);
      }
    }
  }, [endAt, preset, startAt]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const handlePresetChange = (nextPreset: TokenUsageChartPresetDTO) => {
    setPreset(nextPreset);
    if (nextPreset !== 'custom') {
      setStartAt(null);
      setEndAt(null);
    }
  };

  const showInitialLoading = loading && !loaded;

  return (
    <section data-testid="token-visualization-panel" className="mt-10">
      <div className="mb-4 rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-stone-950">Token 消耗可视化</h2>
            <p className="mt-1 text-sm text-stone-500">
              按时间范围查看 Token 趋势、来源和最近消耗记录。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {presetOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={preset === option.value}
                onClick={() => handlePresetChange(option.value)}
                className={clsx(
                  'rounded-xl border px-3 py-1.5 text-xs font-medium transition',
                  preset === option.value
                    ? 'border-teal-600 bg-teal-600 text-white'
                    : 'border-stone-200 bg-white text-stone-600 hover:bg-stone-50',
                )}
              >
                {option.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => void loadData()}
              disabled={loading}
              className="ui-btn-secondary h-9 px-3 text-sm disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              刷新
            </button>
          </div>
        </div>

        {preset === 'custom' ? (
          <CustomRangeControls
            startAt={startAt}
            endAt={endAt}
            onStartAtChange={setStartAt}
            onEndAtChange={setEndAt}
          />
        ) : null}

        {data ? <SummaryGrid data={data} /> : null}
      </div>

      {showInitialLoading ? (
        <div className="flex items-center justify-center gap-2 rounded-2xl border border-stone-200 bg-white px-4 py-12 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载 Token 可视化数据...
        </div>
      ) : error ? (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-4 text-sm text-rose-700">
          <div>{error}</div>
          <button
            type="button"
            onClick={() => void loadData()}
            className="ui-btn-secondary mt-3 px-3 py-2 text-xs"
          >
            重试
          </button>
        </div>
      ) : data ? (
        <div className="space-y-4">
          <TokenTrendChart chart={data.chart} />
          <div className="grid gap-4 lg:grid-cols-2">
            <FeatureDistributionCard items={data.feature_distribution} />
            <ModelRankingCard items={data.model_ranking} />
          </div>
          <RecentRecordsTable records={data.recent_records} />
        </div>
      ) : null}
    </section>
  );
};

function CustomRangeControls({
  startAt,
  endAt,
  onStartAtChange,
  onEndAtChange,
}: {
  startAt: string | null;
  endAt: string | null;
  onStartAtChange: (value: string | null) => void;
  onEndAtChange: (value: string | null) => void;
}) {
  return (
    <div className="mt-4 grid gap-3 rounded-2xl border border-stone-200 bg-[#fcfbf8] p-4 md:grid-cols-2">
      <label className="block">
        <span className="mb-2 block text-xs font-medium text-stone-500">开始时间</span>
        <input
          type="datetime-local"
          value={formatDateTimeLocalValue(startAt)}
          onChange={(event) => onStartAtChange(parseDateTimeLocalValue(event.target.value))}
          className="h-10 w-full rounded-xl border border-stone-200 px-3 text-sm text-stone-700 outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20"
        />
      </label>
      <label className="block">
        <span className="mb-2 block text-xs font-medium text-stone-500">结束时间</span>
        <input
          type="datetime-local"
          value={formatDateTimeLocalValue(endAt)}
          onChange={(event) => onEndAtChange(parseDateTimeLocalValue(event.target.value))}
          className="h-10 w-full rounded-xl border border-stone-200 px-3 text-sm text-stone-700 outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20"
        />
      </label>
    </div>
  );
}

function SummaryGrid({ data }: { data: TokenUsageVisualizationDTO }) {
  const cachedShare = data.summary.total_tokens > 0
    ? data.summary.cached_tokens / data.summary.total_tokens
    : 0;
  const items = [
    {
      label: '总 Token',
      value: data.summary.total_tokens.toLocaleString('zh-CN'),
      helper: `${data.summary.record_count.toLocaleString('zh-CN')} 条记录`,
      icon: <Hash className="h-5 w-5" />,
      tone: 'teal' as const,
    },
    {
      label: '输入 Token',
      value: data.summary.input_tokens.toLocaleString('zh-CN'),
      helper: 'Prompt / 输入消耗',
      icon: <Download className="h-5 w-5" />,
      tone: 'sky' as const,
    },
    {
      label: '输出 Token',
      value: data.summary.output_tokens.toLocaleString('zh-CN'),
      helper: 'Completion / 输出消耗',
      icon: <Upload className="h-5 w-5" />,
      tone: 'violet' as const,
    },
    {
      label: '缓存命中',
      value: data.summary.cached_tokens.toLocaleString('zh-CN'),
      helper: `占比 ${formatTokenShare(cachedShare)}`,
      icon: <Zap className="h-5 w-5" />,
      tone: 'amber' as const,
    },
  ];

  return (
    <div className="mt-5 grid gap-4 lg:grid-cols-4">
      {items.map((item) => (
        <article key={item.label} className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
          <div className="flex items-start gap-3 xl:gap-4">
            <div
              className={clsx(
                'flex h-11 w-11 shrink-0 items-center justify-center rounded-xl',
                metricToneClasses[item.tone].icon,
              )}
            >
              {item.icon}
            </div>
            <div className="min-w-0">
              <div className="text-sm font-medium text-stone-600">{item.label}</div>
              <div className="mt-2 text-2xl font-semibold leading-none text-stone-950 xl:text-3xl">{item.value}</div>
              <div className="mt-2 text-xs leading-5 text-stone-500">{item.helper}</div>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

function TokenTrendChart({ chart }: { chart: TokenUsageChartDTO }) {
  const hasData = chart.buckets.some((bucket) => bucket.total_tokens > 0);
  const bucketLabels = useMemo(
    () => chart.buckets.map((bucket) => formatTokenUsageBucketLabel({
      bucketStart: bucket.bucket_start,
      fallbackLabel: bucket.bucket_label,
      granularity: chart.granularity,
    })),
    [chart.buckets, chart.granularity],
  );
  const chartData = useMemo<ChartData<'line', number[], string>>(
    () => ({
      labels: bucketLabels,
      datasets: trendDatasets.map((dataset) => ({
        label: dataset.label,
        data: chart.buckets.map((bucket) => bucket[dataset.key]),
        borderColor: dataset.borderColor,
        backgroundColor: dataset.backgroundColor,
        borderWidth: 2,
        fill: true,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHitRadius: 12,
        tension: 0.32,
      })),
    }),
    [bucketLabels, chart.buckets],
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
              return `${label}: ${formatTokenCompactTooltipValue(Number(context.raw))} tokens`;
            },
            footer: (tooltipItems: TooltipItem<'line'>[]) => {
              const index = tooltipItems[0]?.dataIndex;
              const bucket = typeof index === 'number' ? chart.buckets[index] : null;
              return bucket ? `合计 ${bucket.total_tokens.toLocaleString('zh-CN')} tokens` : '';
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
            maxRotation: chart.buckets.length > 16 ? 35 : 0,
            maxTicksLimit: resolveTokenTrendMaxTicks(chart.buckets.length),
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
            callback: (value) => formatTokenCompactValue(Number(value)),
          },
        },
      },
    }),
    [chart.buckets],
  );

  if (chart.buckets.length === 0 || !hasData) {
    return <PanelCard title="输入 / 输出 / 缓存趋势"><EmptyState>当前时间范围暂无 Token 消耗数据</EmptyState></PanelCard>;
  }

  return (
    <PanelCard
      title="输入 / 输出 / 缓存趋势"
      meta={chart.granularity === 'hour' ? '按小时' : '按天'}
    >
      <div className="mb-4 flex flex-wrap gap-3 text-xs text-stone-500">
        <LegendSwatch color="#14b8a6" label="输入" />
        <LegendSwatch color="#60a5fa" label="输出" />
        <LegendSwatch color="#f59e0b" label="缓存" />
      </div>
      <div className="h-72 min-w-0 max-w-full overflow-hidden rounded-xl border border-stone-200 bg-white px-4 py-5">
        <Line data={chartData} options={chartOptions} />
      </div>
    </PanelCard>
  );
}

function FeatureDistributionCard({ items }: { items: TokenUsageFeatureDistributionDTO[] }) {
  return (
    <PanelCard title="功能消耗分布">
      {items.length === 0 ? (
        <EmptyState>暂无功能消耗数据</EmptyState>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[12rem_minmax(0,1fr)]">
          <DistributionPieChart
            title="功能消耗分布"
            data={items.map((item) => ({
              key: item.feature_type,
              label: item.feature_label,
              count: item.total_tokens,
            }))}
            emptyText="暂无功能消耗数据"
            className="md:grid-cols-1"
          />
          <BreakdownList
            items={items.map((item) => ({
              key: item.feature_type,
              label: item.feature_label,
              value: item.total_tokens,
              share: item.share,
            }))}
          />
        </div>
      )}
    </PanelCard>
  );
}

function ModelRankingCard({ items }: { items: TokenUsageModelRankingDTO[] }) {
  const max = Math.max(...items.map((item) => item.total_tokens), 1);
  return (
    <PanelCard title="模型消耗排行">
      {items.length === 0 ? (
        <EmptyState>暂无模型消耗数据</EmptyState>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <div key={item.model_name} className="grid gap-2">
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="min-w-0 truncate font-medium text-stone-800" title={item.model_name}>
                  {item.model_name}
                </span>
                <span className="shrink-0 text-xs font-semibold text-stone-500">
                  {formatTokenCompactValue(item.total_tokens)} · {formatTokenShare(item.share)}
                </span>
              </div>
              <div className="h-2.5 overflow-hidden rounded-full bg-stone-100">
                <div
                  className="h-full rounded-full bg-teal-500"
                  style={{ width: `${Math.max((item.total_tokens / max) * 100, 3)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </PanelCard>
  );
}

function RecentRecordsTable({ records }: { records: TokenUsageRecordDTO[] }) {
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [currentPage, setCurrentPage] = useState(1);
  const totalPages = getTotalPages(records.length, pageSize);
  const safeCurrentPage = Math.min(currentPage, totalPages);
  const pagedRecords = getPageItems(records, safeCurrentPage, pageSize);

  useEffect(() => {
    setCurrentPage((previous) => Math.min(previous, totalPages));
  }, [totalPages]);

  const handlePageSizeChange = (nextPageSize: number) => {
    setPageSize(nextPageSize);
    setCurrentPage(1);
  };

  return (
    <PanelCard title="最近 Token 消耗记录">
      {records.length === 0 ? (
        <EmptyState>暂无最近 Token 消耗记录</EmptyState>
      ) : (
        <div>
          <div className="overflow-x-auto">
            <table className="min-w-[760px] w-full text-sm">
              <thead>
                <tr className="border-b border-stone-100 text-xs font-medium text-stone-500">
                  <th className="py-2 pr-3 text-center">时间</th>
                  <th className="py-2 pr-3 text-center">功能</th>
                  <th className="py-2 pr-3 text-center">标题</th>
                  <th className="py-2 pr-3 text-center">模型</th>
                  <th className="py-2 pr-3 text-center">输入</th>
                  <th className="py-2 pr-3 text-center">输出</th>
                  <th className="py-2 pr-3 text-center">缓存</th>
                  <th className="py-2 text-center">总计</th>
                </tr>
              </thead>
              <tbody>
                {pagedRecords.map((record) => (
                  <tr key={record.id} className="border-b border-stone-100 last:border-b-0">
                    <td className="py-3 pr-3 text-center text-xs text-stone-500">
                      {formatTokenUsageRecordTime({ value: record.created_at })}
                    </td>
                    <td className="py-3 pr-3 text-center">
                      <span className="rounded-full bg-stone-100 px-2.5 py-1 text-xs font-medium text-stone-700">
                        {record.feature_label}
                      </span>
                    </td>
                    <td className="max-w-64 truncate py-3 pr-3 text-center font-medium text-stone-900" title={record.title}>
                      {record.title}
                    </td>
                    <td className="max-w-40 truncate py-3 pr-3 text-center text-stone-600" title={record.model_name ?? '未关联'}>
                      {record.model_name ?? '未关联'}
                    </td>
                    <td className="py-3 pr-3 text-center text-stone-600">{formatNullableNumber(record.input_tokens)}</td>
                    <td className="py-3 pr-3 text-center text-stone-600">{formatNullableNumber(record.output_tokens)}</td>
                    <td className="py-3 pr-3 text-center text-stone-600">{formatNullableNumber(record.cached_tokens)}</td>
                    <td className="py-3 text-center font-semibold text-stone-900">{formatNullableNumber(record.total_tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-4 flex flex-col gap-3 border-t border-stone-100 pt-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm text-stone-500">
              共 {records.length} 条记录，当前第 {safeCurrentPage} / {totalPages} 页
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <PageSizeSelector value={pageSize} onChange={handlePageSizeChange} unitLabel="条" />
              <button
                type="button"
                onClick={() => setCurrentPage(safeCurrentPage - 1)}
                disabled={safeCurrentPage <= 1}
                className="ui-btn-secondary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                上一页
              </button>
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
        </div>
      )}
    </PanelCard>
  );
}

function PanelCard({
  title,
  meta,
  children,
}: {
  title: string;
  meta?: string;
  children: ReactNode;
}) {
  return (
    <article className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
      <div className="mb-5 flex items-start justify-between gap-4">
        <h3 className="text-base font-semibold text-stone-900">{title}</h3>
        {meta ? (
          <span className="shrink-0 rounded-full bg-stone-100 px-2.5 py-1 text-xs font-medium text-stone-500">
            {meta}
          </span>
        ) : null}
      </div>
      {children}
    </article>
  );
}

function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-36 items-center justify-center rounded-xl border border-dashed border-stone-200 bg-stone-50 px-4 py-8 text-center text-sm text-stone-500">
      {children}
    </div>
  );
}

function BreakdownList({
  items,
}: {
  items: Array<{ key: string; label: string; value: number; share: number }>;
}) {
  return (
    <div className="space-y-2">
      {items.map((item) => (
        <div key={item.key} className="flex items-center justify-between gap-4 rounded-xl bg-stone-50 px-3 py-2 text-sm">
          <span className="min-w-0 truncate font-medium text-stone-700">{item.label}</span>
          <span className="shrink-0 text-xs font-semibold text-stone-500">
            {formatTokenCompactValue(item.value)} · {formatTokenShare(item.share)}
          </span>
        </div>
      ))}
    </div>
  );
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
}

function resolveTokenTrendMaxTicks(bucketCount: number): number {
  if (bucketCount <= 8) {
    return bucketCount;
  }
  if (bucketCount <= 16) {
    return 8;
  }
  return 10;
}

function formatTokenCompactTooltipValue(value: number): string {
  return Number.isFinite(value) ? value.toLocaleString('zh-CN') : '0';
}

function formatNullableNumber(value: number | null): string {
  return value === null ? '未返回' : value.toLocaleString('zh-CN');
}
