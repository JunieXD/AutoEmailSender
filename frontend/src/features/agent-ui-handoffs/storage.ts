import type {
  DesktopAgentUiHandoffAcknowledgeRequest,
} from '@/types/desktop';
import {
  validateAgentUiHandoff,
  type ValidatedAgentUiHandoff,
} from './types';

const AGENT_UI_HANDOFF_STORAGE_KEY = 'agent_ui_handoffs_v1';
const MAX_ACKNOWLEDGEMENT_RESULT_BYTES = 16_384;

export type StoredAgentUiHandoff = {
  handoff: ValidatedAgentUiHandoff;
  acknowledgement?: DesktopAgentUiHandoffAcknowledgeRequest;
};

type StoredAgentUiHandoffEnvelope = {
  schemaVersion: 1;
  records: Array<{
    handoff: unknown;
    acknowledgement?: unknown;
  }>;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const parseAcknowledgementResult = (
  value: unknown,
): Record<string, unknown> | undefined => {
  if (value === undefined) {
    return undefined;
  }
  if (!isRecord(value)) {
    throw new Error('界面交接回执结果无效');
  }
  const serialized = JSON.stringify(value);
  if (
    typeof serialized !== 'string' ||
    new TextEncoder().encode(serialized).byteLength >
    MAX_ACKNOWLEDGEMENT_RESULT_BYTES
  ) {
    throw new Error('界面交接回执结果过大');
  }
  const parsed = JSON.parse(serialized) as unknown;
  if (!isRecord(parsed)) {
    throw new Error('界面交接回执结果无效');
  }
  return parsed;
};

const parseAcknowledgement = (
  value: unknown,
  handoffId: string,
): DesktopAgentUiHandoffAcknowledgeRequest | undefined => {
  if (value === undefined) {
    return undefined;
  }
  if (!isRecord(value) || value.handoffId !== handoffId) {
    throw new Error('界面交接回执与交接记录不匹配');
  }
  if (
    value.status !== 'applied' &&
    value.status !== 'awaiting_user' &&
    value.status !== 'failed'
  ) {
    throw new Error('界面交接回执状态无效');
  }
  const result = parseAcknowledgementResult(value.result);
  if (
    value.status === 'failed' &&
    (typeof value.failureMessage !== 'string' ||
      !value.failureMessage.trim() ||
      value.failureMessage.length > 2_000)
  ) {
    throw new Error('失败的界面交接回执缺少原因');
  }
  if (value.status !== 'failed' && value.failureMessage !== undefined) {
    throw new Error('非失败界面交接不应包含失败原因');
  }
  return {
    handoffId,
    status: value.status,
    ...(result === undefined ? {} : { result }),
    ...(value.failureMessage === undefined
      ? {}
      : { failureMessage: value.failureMessage as string }),
  };
};

export const readStoredAgentUiHandoffs = (): StoredAgentUiHandoff[] => {
  try {
    const raw = window.sessionStorage.getItem(AGENT_UI_HANDOFF_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const envelope = JSON.parse(raw) as StoredAgentUiHandoffEnvelope;
    if (
      !isRecord(envelope) ||
      envelope.schemaVersion !== 1 ||
      !Array.isArray(envelope.records)
    ) {
      throw new Error('界面交接缓存格式无效');
    }
    const seen = new Set<string>();
    const records = envelope.records.map((record) => {
      if (!isRecord(record)) {
        throw new Error('界面交接缓存记录无效');
      }
      const handoff = validateAgentUiHandoff(
        record.handoff as Parameters<typeof validateAgentUiHandoff>[0],
      );
      if (seen.has(handoff.handoffId)) {
        throw new Error('界面交接缓存包含重复记录');
      }
      seen.add(handoff.handoffId);
      return {
        handoff,
        acknowledgement: parseAcknowledgement(
          record.acknowledgement,
          handoff.handoffId,
        ),
      };
    });
    return records;
  } catch {
    window.sessionStorage.removeItem(AGENT_UI_HANDOFF_STORAGE_KEY);
    return [];
  }
};

export const writeStoredAgentUiHandoffs = (
  records: StoredAgentUiHandoff[],
): void => {
  if (records.length === 0) {
    window.sessionStorage.removeItem(AGENT_UI_HANDOFF_STORAGE_KEY);
    return;
  }
  const envelope: StoredAgentUiHandoffEnvelope = {
    schemaVersion: 1,
    records,
  };
  window.sessionStorage.setItem(
    AGENT_UI_HANDOFF_STORAGE_KEY,
    JSON.stringify(envelope),
  );
};
