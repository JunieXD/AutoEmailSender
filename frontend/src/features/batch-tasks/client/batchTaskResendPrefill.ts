import type { BatchTaskResendPrefillContextDTO } from '@/types';

export const BATCH_RESEND_PREFILL_CONTEXT_KEY = 'batch_resend_prefill_context';
export const SELECTED_PROFESSOR_IDS_KEY = 'selected_professor_ids';

const isNumberArray = (value: unknown): value is number[] =>
  Array.isArray(value) && value.every((item) => Number.isFinite(item));

export const clearBatchResendPrefillContext = () => {
  window.sessionStorage.removeItem(BATCH_RESEND_PREFILL_CONTEXT_KEY);
};

export const readBatchResendPrefillContext = (): BatchTaskResendPrefillContextDTO | null => {
  try {
    const raw = window.sessionStorage.getItem(BATCH_RESEND_PREFILL_CONTEXT_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<BatchTaskResendPrefillContextDTO>;
    if (
      !Number.isFinite(parsed.sourceTaskId) ||
      typeof parsed.sourceTaskName !== 'string' ||
      !Number.isFinite(parsed.identityId) ||
      !isNumberArray(parsed.professorIds) ||
      !parsed.defaults ||
      parsed.defaults.identity_id !== parsed.identityId
    ) {
      clearBatchResendPrefillContext();
      return null;
    }
    return {
      ...(parsed as BatchTaskResendPrefillContextDTO),
      requiresRegeneration:
        typeof parsed.requiresRegeneration === 'boolean'
          ? parsed.requiresRegeneration
          : true,
    };
  } catch {
    clearBatchResendPrefillContext();
    return null;
  }
};

export const writeBatchResendPrefillContext = (context: BatchTaskResendPrefillContextDTO) => {
  window.sessionStorage.setItem(BATCH_RESEND_PREFILL_CONTEXT_KEY, JSON.stringify(context));
};

export const writeSelectedProfessorIdsForBatchTask = (professorIds: number[]) => {
  window.sessionStorage.setItem(SELECTED_PROFESSOR_IDS_KEY, JSON.stringify(professorIds));
};
