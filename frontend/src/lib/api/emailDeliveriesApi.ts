import { apiFetch } from '@/lib/api/client';
import type {
  EmailDeliveryActionDTO,
  EmailDeliveryListDTO,
  EmailDeliverySearchField,
  EmailDeliverySort,
  EmailDeliverySourceFilter,
  EmailDeliveryView,
} from '@/types';

export const listEmailDeliveries = (
  params: {
    view: EmailDeliveryView;
    page: number;
    pageSize: number;
    identityId?: number | null;
    source: EmailDeliverySourceFilter;
    status?: string | null;
    sort: EmailDeliverySort;
    searchFields: EmailDeliverySearchField[];
    query?: string | null;
    taskId?: number | null;
  },
  signal?: AbortSignal,
) =>
  apiFetch<EmailDeliveryListDTO>(
    '/api/email-deliveries',
    { signal },
    {
      view: params.view,
      page: params.page,
      page_size: params.pageSize,
      identity_id: params.identityId ?? undefined,
      source: params.source,
      status: params.status ?? undefined,
      sort: params.sort,
      search_fields: params.searchFields.join(','),
      query: params.query?.trim() || undefined,
      task_id: params.taskId ?? undefined,
    },
  );

export const rescheduleEmailDelivery = (
  taskId: number,
  payload: { scheduled_at: string; expected_updated_at: string },
) =>
  apiFetch<EmailDeliveryActionDTO>(`/api/email-deliveries/${taskId}/schedule`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });

export const cancelEmailDelivery = (taskId: number, expectedUpdatedAt: string) =>
  apiFetch<EmailDeliveryActionDTO>(`/api/email-deliveries/${taskId}/cancel`, {
    method: 'POST',
    body: JSON.stringify({ expected_updated_at: expectedUpdatedAt }),
  });

export const sendEmailDeliveryNow = (taskId: number, expectedUpdatedAt: string) =>
  apiFetch<EmailDeliveryActionDTO>(`/api/email-deliveries/${taskId}/send-now`, {
    method: 'POST',
    body: JSON.stringify({ expected_updated_at: expectedUpdatedAt }),
  });
