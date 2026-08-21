import { describe, expect, it } from 'vitest';
import {
  buildTokenUsageVisualizationQueryParams,
  buildTokenUsageRecordQueryParams,
  calculateStackedBarSegments,
  formatTokenCompactValue,
  formatTokenRecordStatus,
  formatTokenShare,
  formatTokenValue,
  formatDateTimeLocalValue,
  formatTokenUsageRecordTime,
  formatTokenUsageBucketLabel,
  getTokenRecordFeatureTone,
  parseDateTimeLocalValue,
  resolveTokenVisualizationDateRange,
  resolveTokenUsagePageJump,
} from './tokenUsage';

describe('token usage center helpers', () => {
  it('formats missing token fields as not returned', () => {
    expect(formatTokenValue(null)).toBe('未返回');
    expect(formatTokenValue(1200)).toBe('1,200');
  });

  it('formats record status labels', () => {
    expect(formatTokenRecordStatus('success')).toBe('成功');
    expect(formatTokenRecordStatus('failed')).toBe('失败');
    expect(formatTokenRecordStatus('running')).toBe('进行中');
    expect(formatTokenRecordStatus('unknown')).toBe('未知');
  });

  it('returns stable visual tones for feature types', () => {
    expect(getTokenRecordFeatureTone('crawl')).toBe('amber');
    expect(getTokenRecordFeatureTone('information_enrichment')).toBe('stone');
    expect(getTokenRecordFeatureTone('match_analysis')).toBe('emerald');
    expect(getTokenRecordFeatureTone('draft_generation')).toBe('sky');
  });

  it('builds paginated record query params without redundant all filter', () => {
    expect(
      buildTokenUsageRecordQueryParams({
        page: 1,
        pageSize: 5,
        featureType: 'all',
        modelName: null,
        startAt: null,
        endAt: null,
      }),
    ).toEqual({
      page: 1,
      page_size: 5,
    });

    expect(
      buildTokenUsageRecordQueryParams({
        page: 3,
        pageSize: 5,
        featureType: 'match_analysis',
        modelName: 'gpt-4o-mini',
        startAt: '2026-04-30T02:00:00.000Z',
        endAt: '2026-04-30T10:00:00.000Z',
      }),
    ).toEqual({
      page: 3,
      page_size: 5,
      feature_type: 'match_analysis',
      model_name: 'gpt-4o-mini',
      start_at: '2026-04-30T02:00:00.000Z',
      end_at: '2026-04-30T10:00:00.000Z',
    });
  });

  it('builds visualization query params for presets and custom ranges', () => {
    expect(
      buildTokenUsageVisualizationQueryParams({
        preset: 'last_30_days',
        startAt: '2026-05-01T00:00:00.000Z',
        endAt: '2026-05-25T00:00:00.000Z',
      }),
    ).toEqual({
      preset: 'last_30_days',
    });

    expect(
      buildTokenUsageVisualizationQueryParams({
        preset: 'custom',
        startAt: '2026-05-01T00:00:00.000Z',
        endAt: '2026-05-25T00:00:00.000Z',
      }),
    ).toEqual({
      preset: 'custom',
      start_at: '2026-05-01T00:00:00.000Z',
      end_at: '2026-05-25T00:00:00.000Z',
    });
  });

  it('resolves visualization date ranges for presets', () => {
    const now = new Date('2026-05-25T12:00:00.000Z');

    expect(resolveTokenVisualizationDateRange('last_24_hours', now)).toEqual({
      preset: 'last_24_hours',
      startAt: null,
      endAt: null,
    });

    expect(resolveTokenVisualizationDateRange('custom', now)).toEqual({
      preset: 'custom',
      startAt: '2026-05-24T12:00:00.000Z',
      endAt: '2026-05-25T12:00:00.000Z',
    });
  });

  it('round-trips date hour input values', () => {
    const parsed = parseDateTimeLocalValue('2026-04-30T10:00');

    expect(formatDateTimeLocalValue(null)).toBe('');
    expect(parseDateTimeLocalValue('')).toBeNull();
    expect(parsed).not.toBeNull();
    expect(formatDateTimeLocalValue(parsed)).toBe('2026-04-30T10:00');
  });

  it('formats chart bucket labels in the viewer timezone', () => {
    expect(
      formatTokenUsageBucketLabel({
        bucketStart: '2026-04-30T10:00:00Z',
        fallbackLabel: '10:00',
        granularity: 'hour',
        timeZone: 'Asia/Shanghai',
      }),
    ).toBe('18:00');

    expect(
      formatTokenUsageBucketLabel({
        bucketStart: '2026-04-30T18:00:00Z',
        fallbackLabel: '04-30',
        granularity: 'day',
        timeZone: 'Asia/Shanghai',
      }),
    ).toBe('05-01');
  });

  it('formats token record timestamps as UTC when timezone suffix is omitted', () => {
    expect(
      formatTokenUsageRecordTime({
        value: '2026-04-30T10:00:00',
        timeZone: 'Asia/Shanghai',
      }),
    ).toContain('18:00');
  });

  it('resolves valid page jumps and rejects invalid ones', () => {
    expect(resolveTokenUsagePageJump('3', 5)).toBe(3);
    expect(resolveTokenUsagePageJump('0', 5)).toBeNull();
    expect(resolveTokenUsagePageJump('6', 5)).toBeNull();
    expect(resolveTokenUsagePageJump('abc', 5)).toBeNull();
  });

  it('calculates stacked bar segment percentages', () => {
    expect(
      calculateStackedBarSegments({
        inputTokens: 120,
        outputTokens: 60,
        maxTotalTokens: 300,
      }),
    ).toEqual({
      inputPercent: 40,
      outputPercent: 20,
      totalPercent: 60,
    });
    expect(
      calculateStackedBarSegments({
        inputTokens: 120,
        outputTokens: 60,
        maxTotalTokens: 0,
      }),
    ).toEqual({
      inputPercent: 0,
      outputPercent: 0,
      totalPercent: 0,
    });
  });

  it('formats compact token numbers and shares', () => {
    expect(formatTokenCompactValue(1200)).toBe('1.2K');
    expect(formatTokenCompactValue(1_200_000)).toBe('1.2M');
    expect(formatTokenShare(0.376)).toBe('37.6%');
  });
});
