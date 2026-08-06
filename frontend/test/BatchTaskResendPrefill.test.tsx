import { beforeEach, describe, expect, it } from 'vitest';
import {
  BATCH_RESEND_PREFILL_CONTEXT_KEY,
  clearBatchResendPrefillContext,
  readBatchResendPrefillContext,
  writeBatchResendPrefillContext,
} from '@/features/batch-tasks/client/batchTaskResendPrefill';

const context = {
  sourceTaskId: 12,
  sourceTaskName: '原任务',
  identityId: 3,
  professorIds: [88, 89],
  requiresRegeneration: false,
  defaults: {
    identity_id: 3,
    outreach_generation_mode: 'template' as const,
    outreach_template_subject: '主题 {{name}}',
    outreach_template_body_text: '正文 {{sender_name}}',
    outreach_template_body_html: '<p>正文 {{sender_name}}</p>',
    primary_material_id: 10,
    selected_material_ids: [11, 12],
  },
  warnings: ['部分原材料已不存在，未带入新任务'],
};

describe('batchTaskResendPrefill', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('writes and reads resend prefill context', () => {
    writeBatchResendPrefillContext(context);
    expect(readBatchResendPrefillContext()).toEqual(context);
  });

  it('returns null and clears invalid JSON', () => {
    window.sessionStorage.setItem(BATCH_RESEND_PREFILL_CONTEXT_KEY, '{');
    expect(readBatchResendPrefillContext()).toBeNull();
    expect(window.sessionStorage.getItem(BATCH_RESEND_PREFILL_CONTEXT_KEY)).toBeNull();
  });

  it('clears invalid context with mismatched identity', () => {
    writeBatchResendPrefillContext({
      ...context,
      identityId: 4,
    });
    expect(readBatchResendPrefillContext()).toBeNull();
  });

  it('clears context without regeneration metadata', () => {
    const invalidContext = { ...context } as Partial<typeof context>;
    delete invalidContext.requiresRegeneration;
    window.sessionStorage.setItem(
      BATCH_RESEND_PREFILL_CONTEXT_KEY,
      JSON.stringify(invalidContext),
    );
    expect(readBatchResendPrefillContext()).toBeNull();
  });

  it('clears resend prefill context', () => {
    writeBatchResendPrefillContext(context);
    clearBatchResendPrefillContext();
    expect(readBatchResendPrefillContext()).toBeNull();
  });
});
