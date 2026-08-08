import { apiFetch, apiFetchBlob } from "@/lib/api/client";

export interface OperationLogDTO {
  id: number;
  request_id: string | null;
  category: string;
  event_name: string;
  level: string;
  message: string | null;
  entity_type?: string | null;
  entity_id?: string | null;
  metadata?: unknown;
  created_at: string;
}

export interface OperationLogListParams
  extends Record<string, string | number | null | undefined> {
  limit?: number;
  offset?: number;
  level?: string;
  category?: string;
  event_name?: string;
  request_id?: string;
  entity_type?: string;
  entity_id?: string;
  start_at?: string;
  end_at?: string;
}

export interface OperationLogListResponseDTO {
  items: OperationLogDTO[];
  total: number;
  limit: number;
  offset: number;
}

export interface OperationLogExportResponseDTO {
  exported_at: string;
  items: OperationLogDTO[];
  total: number;
  filters: Record<string, string | null>;
}

export const listOperationLogs = (params: OperationLogListParams = {}) =>
  apiFetch<OperationLogListResponseDTO>(
    "/api/diagnostics/operation-logs",
    undefined,
    params,
  );

export const exportOperationLogs = (params: OperationLogListParams = {}) =>
  apiFetch<OperationLogExportResponseDTO>(
    "/api/diagnostics/export",
    undefined,
    params,
  );

export const exportCrawlerDebugLog = (jobId: number) =>
  apiFetchBlob(`/api/diagnostics/crawler-debug/${jobId}/export`);
