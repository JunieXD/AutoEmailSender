import { apiFetch } from '@/lib/api/client';
import type {
  TestComposeDraftPayloadDTO,
  TestComposeStatusDTO,
  TestComposeThreadDTO,
} from '@/types';

export const getTestComposeStatus = (identityId: number) =>
  apiFetch<TestComposeStatusDTO>(`/api/test-compose/${identityId}/status`);

export const getTestComposeThread = (identityId: number, llmProfileId: number) =>
  apiFetch<TestComposeThreadDTO>(`/api/test-compose/${identityId}/${llmProfileId}`);

export const generateTestComposeDraft = (
  identityId: number,
  llmProfileId: number,
  outreachTemplateId?: number | null,
  templateSnapshot?: Pick<
    TestComposeDraftPayloadDTO,
    'subject' | 'body_text' | 'body_html'
  >,
) =>
  apiFetch<TestComposeThreadDTO>(`/api/test-compose/${identityId}/${llmProfileId}/generate-draft`, {
    method: 'POST',
    body:
      outreachTemplateId === undefined && templateSnapshot === undefined
        ? undefined
        : JSON.stringify({
            outreach_template_id: outreachTemplateId,
            ...templateSnapshot,
          }),
  });

export const saveTestComposeDraft = (
  identityId: number,
  llmProfileId: number,
  payload: TestComposeDraftPayloadDTO,
) =>
  apiFetch<TestComposeThreadDTO>(`/api/test-compose/${identityId}/${llmProfileId}/draft`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const sendTestComposeMessage = (
  identityId: number,
  llmProfileId: number,
  payload: TestComposeDraftPayloadDTO,
) =>
  apiFetch<TestComposeThreadDTO>(`/api/test-compose/${identityId}/${llmProfileId}/send`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
