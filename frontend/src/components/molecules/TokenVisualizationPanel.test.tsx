import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TokenVisualizationPanel } from './TokenVisualizationPanel';
import type { TokenUsageRecordDTO, TokenUsageVisualizationDTO } from '@/types';

const getTokenUsageVisualization = vi.fn();
const listTokenUsageRecords = vi.fn();
const lineChartRender = vi.fn();

vi.mock('@/lib/api/tokenUsage', () => ({
  getTokenUsageVisualization: (...args: unknown[]) => getTokenUsageVisualization(...args),
  listTokenUsageRecords: (...args: unknown[]) => listTokenUsageRecords(...args),
}));

vi.mock('react-chartjs-2', () => ({
  Line: (props: unknown) => {
    lineChartRender(props);
    return <div data-testid="token-trend-line-chart" />;
  },
}));

vi.mock('chart.js', () => ({
  CategoryScale: {},
  Chart: { register: vi.fn() },
  Filler: {},
  Legend: {},
  LinearScale: {},
  LineElement: {},
  PointElement: {},
  Title: {},
  Tooltip: {},
}));

const createRecentRecord = (index: number): TokenUsageRecordDTO => ({
  id: `match_analysis:${index}`,
  feature_type: 'match_analysis',
  feature_label: '匹配分析',
  title: `李老师 ${index} - 匹配分析`,
  input_tokens: 1200 + index,
  output_tokens: 220,
  cached_tokens: 300,
  total_tokens: 1720 + index,
  model_name: 'gpt-primary',
  identity_name: '博士申请邮箱',
  created_at: '2026-05-25T11:00:00Z',
  status: 'success',
});

const createChartBucket = (index: number) => ({
  bucket_start: `2026-05-25T${String(index).padStart(2, '0')}:00:00Z`,
  bucket_label: `${String(index).padStart(2, '0')}:00`,
  input_tokens: 300 + index * 10,
  output_tokens: 80 + index,
  cached_tokens: index % 3 === 0 ? 50 : 0,
  total_tokens: 380 + index * 11 + (index % 3 === 0 ? 50 : 0),
});

const visualization: TokenUsageVisualizationDTO = {
  preset: 'last_24_hours',
  summary: {
    input_tokens: 2500,
    output_tokens: 560,
    cached_tokens: 500,
    total_tokens: 3060,
    record_count: 12,
  },
  chart: {
    preset: 'last_24_hours',
    granularity: 'hour',
    range_start: '2026-05-24T13:00:00Z',
    range_end: '2026-05-25T12:00:00Z',
    buckets: [
      {
        bucket_start: '2026-05-25T10:00:00Z',
        bucket_label: '10:00',
        input_tokens: 500,
        output_tokens: 80,
        cached_tokens: 40,
        total_tokens: 620,
      },
      {
        bucket_start: '2026-05-25T11:00:00Z',
        bucket_label: '11:00',
        input_tokens: 1200,
        output_tokens: 220,
        cached_tokens: 300,
        total_tokens: 1720,
      },
    ],
  },
  feature_distribution: [
    {
      feature_type: 'match_analysis',
      feature_label: '匹配分析',
      input_tokens: 1200,
      output_tokens: 220,
      cached_tokens: 300,
      total_tokens: 1720,
      record_count: 1,
      share: 0.562,
    },
    {
      feature_type: 'draft_generation',
      feature_label: 'AI 草稿',
      input_tokens: 800,
      output_tokens: 260,
      cached_tokens: 160,
      total_tokens: 1220,
      record_count: 1,
      share: 0.399,
    },
  ],
  model_ranking: [
    {
      model_name: 'gpt-primary',
      input_tokens: 2000,
      output_tokens: 480,
      cached_tokens: 460,
      total_tokens: 2940,
      record_count: 2,
      share: 0.961,
    },
  ],
  recent_records: Array.from({ length: 12 }, (_, index) => createRecentRecord(index + 1)),
};

const denseVisualization: TokenUsageVisualizationDTO = {
  ...visualization,
  chart: {
    ...visualization.chart,
    buckets: Array.from({ length: 24 }, (_, index) => createChartBucket(index)),
  },
};

const recordList = {
  records: visualization.recent_records.slice(0, 10),
  summary: visualization.summary,
  pagination: {
    page: 1,
    page_size: 10,
    total_records: visualization.recent_records.length,
    total_pages: 2,
  },
  model_options: ['gpt-primary', 'gpt-secondary'],
};

const secondRecordList = {
  ...recordList,
  records: visualization.recent_records.slice(10),
  pagination: {
    ...recordList.pagination,
    page: 2,
  },
};
describe('TokenVisualizationPanel', () => {
  beforeEach(() => {
    getTokenUsageVisualization.mockReset();
    listTokenUsageRecords.mockReset();
    getTokenUsageVisualization.mockResolvedValue(visualization);
    listTokenUsageRecords.mockResolvedValue(recordList);
  });

  it('renders summary cards, trend chart, breakdowns and recent records', async () => {
    render(<TokenVisualizationPanel />);

    expect(await screen.findByText('Token 消耗可视化')).toBeInTheDocument();
    expect(screen.getByText('总 Token')).toBeInTheDocument();
    expect(screen.getByText('3,060')).toBeInTheDocument();
    expect(screen.getByText('输入 / 输出 / 缓存趋势')).toBeInTheDocument();
    expect(screen.getByText('功能消耗分布')).toBeInTheDocument();
    expect(screen.getByText('模型消耗排行')).toBeInTheDocument();
    expect(screen.getByText('Token 消耗记录')).toBeInTheDocument();
    expect(screen.getByText('李老师 1 - 匹配分析')).toBeInTheDocument();
  });


  it('compacts summary token totals from ten million with two decimals', async () => {
    listTokenUsageRecords.mockResolvedValue({
      ...recordList,
      records: [],
      pagination: {
        page: 1,
        page_size: 10,
        total_records: 0,
        total_pages: 0,
      },
    });
    getTokenUsageVisualization.mockResolvedValue({
      ...visualization,
      summary: {
        ...visualization.summary,
        total_tokens: 12_345_678,
      },
    });

    render(<TokenVisualizationPanel />);

    expect(await screen.findByText('12.35M')).toBeInTheDocument();
    expect(screen.queryByText('12,345,678')).not.toBeInTheDocument();
    expect(screen.getByText('12.35M')).toHaveAttribute('title', '12,345,678');
  });

  it('centers all recent token record table headers and cells', async () => {
    render(<TokenVisualizationPanel />);

    await screen.findByText('Token 消耗记录');

    screen.getAllByRole('columnheader').forEach((header) => {
      expect(header).toHaveClass('text-center');
    });
    screen.getAllByRole('cell').forEach((cell) => {
      expect(cell).toHaveClass('text-center');
    });
  });

  it('paginates recent token records with homepage-style controls', async () => {
    listTokenUsageRecords.mockResolvedValueOnce(recordList).mockResolvedValueOnce(secondRecordList);
    render(<TokenVisualizationPanel />);

    expect(await screen.findByText('共 12 条记录，当前第 1 / 2 页')).toBeInTheDocument();
    expect(screen.getByText('李老师 1 - 匹配分析')).toBeInTheDocument();
    expect(screen.queryByText('李老师 11 - 匹配分析')).not.toBeInTheDocument();
    expect(screen.getByText('条')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '下一页' }));

    expect(await screen.findByText('共 12 条记录，当前第 2 / 2 页')).toBeInTheDocument();
    expect(screen.getByText('李老师 11 - 匹配分析')).toBeInTheDocument();
    expect(screen.queryByText('李老师 1 - 匹配分析')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
  });

  it('filters token records by feature model and time range', async () => {
    render(<TokenVisualizationPanel />);

    await screen.findByText('Token 消耗记录');
    fireEvent.click(screen.getByLabelText('Token 记录功能筛选'));
    fireEvent.click(screen.getByRole('option', { name: '匹配分析' }));
    fireEvent.click(screen.getByLabelText('Token 记录模型筛选'));
    fireEvent.click(screen.getByRole('option', { name: 'gpt-primary' }));
    fireEvent.change(screen.getByLabelText('Token 记录开始时间'), { target: { value: '2026-05-24T10:30' } });
    fireEvent.change(screen.getByLabelText('Token 记录结束时间'), { target: { value: '2026-05-25T11:45' } });
    fireEvent.click(screen.getByRole('button', { name: '查询记录' }));

    await waitFor(() => {
      expect(listTokenUsageRecords).toHaveBeenLastCalledWith({
        page: 1,
        pageSize: 10,
        featureType: 'match_analysis',
        modelName: 'gpt-primary',
        startAt: new Date('2026-05-24T10:30').toISOString(),
        endAt: new Date('2026-05-25T11:45').toISOString(),
      });
    });
  });
  it('reloads visualization when preset changes', async () => {
    render(<TokenVisualizationPanel />);

    await screen.findByText('Token 消耗可视化');
    fireEvent.click(screen.getByRole('button', { name: '最近 30 天' }));

    await waitFor(() => {
      expect(getTokenUsageVisualization).toHaveBeenLastCalledWith({
        preset: 'last_30_days',
        startAt: null,
        endAt: null,
      });
    });
  });

  it('does not show a transient refreshing banner when preset changes', async () => {
    let resolveNext: ((value: TokenUsageVisualizationDTO) => void) | undefined;
    getTokenUsageVisualization
      .mockResolvedValueOnce(visualization)
      .mockImplementationOnce(
        () =>
          new Promise<TokenUsageVisualizationDTO>((resolve) => {
            resolveNext = resolve;
          }),
      );

    render(<TokenVisualizationPanel />);

    await screen.findByText('Token 消耗可视化');
    fireEvent.click(screen.getByRole('button', { name: '最近 24 小时' }));

    await waitFor(() => {
      expect(getTokenUsageVisualization).toHaveBeenLastCalledWith({
        preset: 'last_24_hours',
        startAt: null,
        endAt: null,
      });
    });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '刷新' })).toBeDisabled();
    });

    expect(screen.queryByText('正在更新 Token 可视化数据...')).not.toBeInTheDocument();

    resolveNext?.(visualization);
  });

  it('shows bucket tooltip on trend hover', async () => {
    render(<TokenVisualizationPanel />);

    await screen.findByTestId('token-trend-line-chart');

    const props = lineChartRender.mock.calls.at(-1)?.[0] as {
      options: {
        interaction: { intersect: boolean; mode: string };
        plugins: {
          tooltip: {
            callbacks: {
              label: (context: { dataset: { label: string }; raw: number }) => string;
              footer: (items: Array<{ dataIndex: number }>) => string;
            };
          };
        };
      };
    };

    expect(props.options.interaction).toEqual({ intersect: false, mode: 'index' });
    expect(props.options.plugins.tooltip.callbacks.label({
      dataset: { label: '输入' },
      raw: 1200,
    })).toBe('输入: 1,200 tokens');
    expect(props.options.plugins.tooltip.callbacks.footer([{ dataIndex: 1 }])).toContain('合计 1,720 tokens');
  });

  it('renders token trend as responsive line datasets without default horizontal scrolling', async () => {
    getTokenUsageVisualization.mockResolvedValue(denseVisualization);

    render(<TokenVisualizationPanel />);

    expect(await screen.findByTestId('token-trend-line-chart')).toBeInTheDocument();

    const props = lineChartRender.mock.calls.at(-1)?.[0] as {
      data: { labels: string[]; datasets: Array<{ label: string; data: number[]; fill: boolean; tension: number }> };
      options: {
        responsive: boolean;
        maintainAspectRatio: boolean;
        scales: { x: { ticks: { autoSkip: boolean; maxTicksLimit: number } } };
      };
    };

    expect(props.data.labels).toHaveLength(24);
    expect(props.data.datasets.map((dataset) => dataset.label)).toEqual(['输入', '输出', '缓存']);
    expect(props.data.datasets[0].data[1]).toBe(310);
    expect(props.data.datasets.every((dataset) => dataset.fill)).toBe(true);
    expect(props.data.datasets.every((dataset) => dataset.tension === 0.32)).toBe(true);
    expect(props.options.responsive).toBe(true);
    expect(props.options.maintainAspectRatio).toBe(false);
    expect(props.options.scales.x.ticks.autoSkip).toBe(true);
    expect(props.options.scales.x.ticks.maxTicksLimit).toBe(10);
  });

  it('shows empty state for empty visualization data', async () => {
    listTokenUsageRecords.mockResolvedValue({
      ...recordList,
      records: [],
      pagination: {
        page: 1,
        page_size: 10,
        total_records: 0,
        total_pages: 0,
      },
    });
    getTokenUsageVisualization.mockResolvedValue({
      ...visualization,
      summary: {
        input_tokens: 0,
        output_tokens: 0,
        cached_tokens: 0,
        total_tokens: 0,
        record_count: 0,
      },
      chart: { ...visualization.chart, buckets: [] },
      feature_distribution: [],
      model_ranking: [],
      recent_records: [],
    });

    render(<TokenVisualizationPanel />);

    expect(await screen.findByText('当前时间范围暂无 Token 消耗数据')).toBeInTheDocument();
    expect(screen.getByText('暂无功能消耗数据')).toBeInTheDocument();
    expect(screen.getByText('暂无模型消耗数据')).toBeInTheDocument();
    expect(screen.getByText('暂无 Token 消耗记录')).toBeInTheDocument();
  });

  it('shows an error and retries loading', async () => {
    getTokenUsageVisualization.mockRejectedValueOnce(new Error('网络错误'));
    getTokenUsageVisualization.mockResolvedValueOnce(visualization);

    render(<TokenVisualizationPanel />);

    expect(await screen.findByText('网络错误')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByText('总 Token')).toBeInTheDocument();
    expect(getTokenUsageVisualization).toHaveBeenCalledTimes(2);
  });
});
