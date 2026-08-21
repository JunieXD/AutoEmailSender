import { apiFetch } from '@/lib/api/client';
import {
  buildTokenUsageRecordQueryParams,
  buildTokenUsageVisualizationQueryParams,
} from '@/features/token-usage/client/tokenUsage';
import type {
  TokenUsageChartPresetDTO,
  TokenUsageRecordFeatureFilterDTO,
  TokenUsageRecordListDTO,
  TokenUsageVisualizationDTO,
} from '@/types';

export interface TokenUsageRecordQuery {
  page: number;
  pageSize: number;
  featureType: TokenUsageRecordFeatureFilterDTO;
  modelName: string | null;
  startAt: string | null;
  endAt: string | null;
}

export interface TokenUsageVisualizationQuery {
  preset: TokenUsageChartPresetDTO;
  startAt: string | null;
  endAt: string | null;
}

export const listTokenUsageRecords = (query: TokenUsageRecordQuery) =>
  apiFetch<TokenUsageRecordListDTO>(
    '/api/token-usage/records',
    undefined,
    buildTokenUsageRecordQueryParams(query),
  );

export const getTokenUsageVisualization = (query: TokenUsageVisualizationQuery) =>
  apiFetch<TokenUsageVisualizationDTO>(
    '/api/token-usage/visualization',
    undefined,
    buildTokenUsageVisualizationQueryParams(query),
  );
