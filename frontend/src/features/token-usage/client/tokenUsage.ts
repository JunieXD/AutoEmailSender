import type {
  TokenUsageChartPresetDTO,
  TokenUsageRecordFeatureFilterDTO,
  TokenUsageRecordFeatureTypeDTO,
  TokenUsageRecordStatusDTO,
} from '@/types';
import { parseApiDateTime } from '@/lib/dateTime';

export type TokenRecordFeatureTone = 'amber' | 'emerald' | 'sky' | 'stone';

export const formatTokenValue = (value: number | null): string =>
  value === null ? '未返回' : value.toLocaleString('zh-CN');

export const formatTokenRecordStatus = (
  status: TokenUsageRecordStatusDTO,
): string => {
  const labels: Record<TokenUsageRecordStatusDTO, string> = {
    success: '成功',
    failed: '失败',
    running: '进行中',
    unknown: '未知',
  };
  return labels[status];
};

export const getTokenRecordFeatureTone = (
  featureType: TokenUsageRecordFeatureTypeDTO,
): TokenRecordFeatureTone => {
  const tones: Record<TokenUsageRecordFeatureTypeDTO, TokenRecordFeatureTone> = {
    crawl: 'amber',
    match_analysis: 'emerald',
    draft_generation: 'sky',
  };
  return tones[featureType] ?? 'stone';
};

export const buildTokenUsageRecordQueryParams = ({
  page,
  pageSize,
  featureType,
  modelName,
  startAt,
  endAt,
}: {
  page: number;
  pageSize: number;
  featureType: TokenUsageRecordFeatureFilterDTO;
  modelName: string | null;
  startAt: string | null;
  endAt: string | null;
}) => ({
  page,
  page_size: pageSize,
  ...(featureType !== 'all' ? { feature_type: featureType } : {}),
  ...(modelName ? { model_name: modelName } : {}),
  ...(startAt ? { start_at: startAt } : {}),
  ...(endAt ? { end_at: endAt } : {}),
});

export const buildTokenUsageChartQueryParams = ({
  featureType,
  modelName,
  preset,
  startAt,
  endAt,
}: {
  featureType: TokenUsageRecordFeatureFilterDTO;
  modelName: string | null;
  preset: TokenUsageChartPresetDTO;
  startAt: string | null;
  endAt: string | null;
}) => ({
  preset,
  ...(featureType !== 'all' ? { feature_type: featureType } : {}),
  ...(modelName ? { model_name: modelName } : {}),
  ...(preset === 'custom' && startAt ? { start_at: startAt } : {}),
  ...(preset === 'custom' && endAt ? { end_at: endAt } : {}),
});

export const buildTokenUsageVisualizationQueryParams = ({
  preset,
  startAt,
  endAt,
}: {
  preset: TokenUsageChartPresetDTO;
  startAt: string | null;
  endAt: string | null;
}) => ({
  preset,
  ...(preset === 'custom' && startAt ? { start_at: startAt } : {}),
  ...(preset === 'custom' && endAt ? { end_at: endAt } : {}),
});

export const resolveTokenVisualizationDateRange = (
  preset: TokenUsageChartPresetDTO,
  now = new Date(),
): { preset: TokenUsageChartPresetDTO; startAt: string | null; endAt: string | null } => {
  if (preset !== 'custom') {
    return { preset, startAt: null, endAt: null };
  }
  // time-check: local-control-value, custom range default is derived from the current Date object.
  const start = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  return {
    preset,
    startAt: start.toISOString(),
    endAt: now.toISOString(),
  };
};

export const parseDateTimeLocalValue = (value: string): string | null => {
  if (!value.trim()) {
    return null;
  }
  // time-check: local-control-value, value comes from a datetime-local control.
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date.toISOString();
};

export const formatDateTimeLocalValue = (value: string | null): string => {
  if (!value) {
    return '';
  }
  // time-check: local-control-value, value is an ISO instant being projected into a datetime-local control.
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '';
  }
  return [
    date.getFullYear(),
    '-',
    padDatePart(date.getMonth() + 1),
    '-',
    padDatePart(date.getDate()),
    'T',
    padDatePart(date.getHours()),
    ':',
    padDatePart(date.getMinutes()),
  ].join('');
};

export const formatTokenUsageBucketLabel = ({
  bucketStart,
  fallbackLabel,
  granularity,
  timeZone,
}: {
  bucketStart: string;
  fallbackLabel: string;
  granularity: 'hour' | 'day';
  timeZone?: string;
}): string => {
  const date = parseApiDateTime(bucketStart);
  if (Number.isNaN(date.getTime())) {
    return fallbackLabel;
  }
  const formatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    hour12: false,
    ...(granularity === 'hour'
      ? {
          hour: '2-digit',
          minute: '2-digit',
        }
      : {
          month: '2-digit',
          day: '2-digit',
        }),
  });
  return formatter.format(date).replace(/\//g, '-').replace(/\s+/g, '');
};

export const formatTokenUsageRecordTime = ({
  value,
  timeZone,
}: {
  value: string;
  timeZone?: string;
}): string => {
  const date = parseApiDateTime(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString('zh-CN', {
    timeZone,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
};

export const resolveTokenUsagePageJump = (
  pageInput: string,
  totalPages: number,
): number | null => {
  const nextPage = Number(pageInput);
  if (!Number.isInteger(nextPage) || nextPage < 1 || nextPage > totalPages) {
    return null;
  }
  return nextPage;
};

export const calculateStackedBarSegments = ({
  inputTokens,
  outputTokens,
  maxTotalTokens,
}: {
  inputTokens: number;
  outputTokens: number;
  maxTotalTokens: number;
}) => {
  if (maxTotalTokens <= 0) {
    return { inputPercent: 0, outputPercent: 0, totalPercent: 0 };
  }
  const inputPercent = Math.round((inputTokens / maxTotalTokens) * 100);
  const outputPercent = Math.round((outputTokens / maxTotalTokens) * 100);
  return {
    inputPercent,
    outputPercent,
    totalPercent: inputPercent + outputPercent,
  };
};

export const formatTokenCompactValue = (value: number): string => {
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1).replace(/\.0$/, '')}K`;
  }
  return value.toLocaleString('zh-CN');
};

export const formatTokenShare = (value: number): string =>
  `${(value * 100).toFixed(1).replace(/\.0$/, '')}%`;

const padDatePart = (value: number): string => String(value).padStart(2, '0');
