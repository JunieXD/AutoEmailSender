import type {
  BatchTaskResendDefaultsDTO,
  BatchTaskResendPrefillContextDTO,
} from '@/types';

export const NAVIGATION_HANDOFF_STORAGE_KEY = 'app_navigation_handoff_v1';
export const LEGACY_SELECTED_PROFESSOR_IDS_KEY = 'selected_professor_ids';
export const LEGACY_BATCH_RESEND_CONTEXT_KEY = 'batch_resend_prefill_context';

const NAVIGATION_HANDOFF_TTL_MS = 8 * 60 * 60 * 1_000;
const NAVIGATION_HANDOFF_CLOCK_SKEW_MS = 5 * 60 * 1_000;

export type CreateTaskNavigationHandoff = {
  schemaVersion: 1;
  kind: 'create_batch_task';
  target: '/create-task';
  createdAt: number;
  expiresAt: number;
  professorIds: number[];
  resendContext: BatchTaskResendPrefillContextDTO | null;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const normalizeProfessorIds = (value: unknown): number[] | null => {
  if (!Array.isArray(value) || value.length === 0) {
    return null;
  }
  const ids = value.filter(
    (item): item is number =>
      typeof item === 'number' && Number.isInteger(item) && item > 0,
  );
  if (ids.length !== value.length) {
    return null;
  }
  return Array.from(new Set(ids));
};

const parseSelectedMaterialIds = (value: unknown): number[] | null => {
  if (
    !Array.isArray(value) ||
    value.some(
      (item) =>
        typeof item !== 'number' || !Number.isInteger(item) || item <= 0,
    ) ||
    new Set(value).size !== value.length
  ) {
    return null;
  }
  return value as number[];
};

const isNullableString = (value: unknown): value is string | null =>
  value === null || typeof value === 'string';

const isOptionalNullableString = (
  value: unknown,
): value is string | null | undefined =>
  value === undefined || isNullableString(value);

const isOptionalPositiveInteger = (
  value: unknown,
): value is number | null | undefined =>
  value === undefined ||
  value === null ||
  (typeof value === 'number' && Number.isInteger(value) && value > 0);

const parseResendDefaults = (
  value: unknown,
  identityId: number,
): BatchTaskResendDefaultsDTO => {
  if (!isRecord(value)) {
    throw new Error('批量重发导航默认值无效');
  }
  const selectedMaterialIds = parseSelectedMaterialIds(
    value.selected_material_ids,
  );
  if (
    value.identity_id !== identityId ||
    !isOptionalPositiveInteger(value.outreach_template_id) ||
    !isOptionalNullableString(value.outreach_template_name_snapshot) ||
    (value.outreach_generation_mode !== null &&
      value.outreach_generation_mode !== 'llm' &&
      value.outreach_generation_mode !== 'template') ||
    !isNullableString(value.outreach_template_subject) ||
    !isNullableString(value.outreach_template_body_text) ||
    !isNullableString(value.outreach_template_body_html) ||
    !isOptionalPositiveInteger(value.primary_material_id) ||
    value.primary_material_id === undefined ||
    selectedMaterialIds === null
  ) {
    throw new Error('批量重发导航默认值缺少必要字段');
  }
  return {
    identity_id: identityId,
    ...(value.outreach_template_id === undefined
      ? {}
      : { outreach_template_id: value.outreach_template_id }),
    ...(value.outreach_template_name_snapshot === undefined
      ? {}
      : {
          outreach_template_name_snapshot:
            value.outreach_template_name_snapshot,
        }),
    outreach_generation_mode: value.outreach_generation_mode,
    outreach_template_subject: value.outreach_template_subject,
    outreach_template_body_text: value.outreach_template_body_text,
    outreach_template_body_html: value.outreach_template_body_html,
    primary_material_id: value.primary_material_id,
    selected_material_ids: selectedMaterialIds,
  };
};

const parseResendContext = (
  value: unknown,
  professorIds: number[],
): BatchTaskResendPrefillContextDTO | null => {
  if (value === null || value === undefined) {
    return null;
  }
  if (!isRecord(value)) {
    throw new Error('批量重发导航上下文无效');
  }
  const sourceTaskId = value.sourceTaskId;
  const identityId = value.identityId;
  const contextProfessorIds = normalizeProfessorIds(value.professorIds);
  if (
    typeof sourceTaskId !== 'number' ||
    !Number.isInteger(sourceTaskId) ||
    sourceTaskId <= 0 ||
    typeof value.sourceTaskName !== 'string' ||
    typeof identityId !== 'number' ||
    !Number.isInteger(identityId) ||
    identityId <= 0 ||
    contextProfessorIds === null ||
    !Array.isArray(value.warnings) ||
    !value.warnings.every((warning) => typeof warning === 'string')
  ) {
    throw new Error('批量重发导航上下文缺少必要字段');
  }
  if (
    contextProfessorIds.length !== professorIds.length ||
    contextProfessorIds.some((id, index) => id !== professorIds[index])
  ) {
    throw new Error('批量重发上下文与导师选择不匹配');
  }
  const defaults = parseResendDefaults(value.defaults, identityId);
  return {
    sourceTaskId,
    sourceTaskName: value.sourceTaskName,
    identityId,
    professorIds: contextProfessorIds,
    requiresRegeneration:
      typeof value.requiresRegeneration === 'boolean'
        ? value.requiresRegeneration
        : true,
    defaults,
    warnings: value.warnings,
  };
};

const parseNavigationHandoff = (value: unknown): CreateTaskNavigationHandoff => {
  const now = Date.now();
  if (
    !isRecord(value) ||
    value.schemaVersion !== 1 ||
    value.kind !== 'create_batch_task' ||
    value.target !== '/create-task' ||
    typeof value.createdAt !== 'number' ||
    !Number.isFinite(value.createdAt) ||
    typeof value.expiresAt !== 'number' ||
    !Number.isFinite(value.expiresAt) ||
    value.expiresAt <= value.createdAt ||
    value.expiresAt - value.createdAt > NAVIGATION_HANDOFF_TTL_MS ||
    value.createdAt > now + NAVIGATION_HANDOFF_CLOCK_SKEW_MS
  ) {
    throw new Error('页面导航交接格式无效');
  }
  const professorIds = normalizeProfessorIds(value.professorIds);
  if (professorIds === null) {
    throw new Error('页面导航交接的导师选择无效');
  }
  if (value.expiresAt <= now) {
    throw new Error('页面导航交接已经过期');
  }
  return {
    schemaVersion: 1,
    kind: 'create_batch_task',
    target: '/create-task',
    createdAt: value.createdAt,
    expiresAt: value.expiresAt,
    professorIds,
    resendContext: parseResendContext(value.resendContext, professorIds),
  };
};

export const writeCreateTaskNavigationHandoff = (
  professorIdsValue: number[],
  resendContext: BatchTaskResendPrefillContextDTO | null = null,
): CreateTaskNavigationHandoff => {
  const professorIds = normalizeProfessorIds(professorIdsValue);
  if (professorIds === null) {
    throw new Error('至少需要选择一位有效导师');
  }
  const now = Date.now();
  const handoff: CreateTaskNavigationHandoff = {
    schemaVersion: 1,
    kind: 'create_batch_task',
    target: '/create-task',
    createdAt: now,
    expiresAt: now + NAVIGATION_HANDOFF_TTL_MS,
    professorIds,
    resendContext: parseResendContext(resendContext, professorIds),
  };
  window.sessionStorage.setItem(
    NAVIGATION_HANDOFF_STORAGE_KEY,
    JSON.stringify(handoff),
  );
  window.sessionStorage.removeItem(LEGACY_SELECTED_PROFESSOR_IDS_KEY);
  window.sessionStorage.removeItem(LEGACY_BATCH_RESEND_CONTEXT_KEY);
  return handoff;
};

const migrateLegacyNavigationHandoff = (): CreateTaskNavigationHandoff | null => {
  const rawProfessorIds = window.sessionStorage.getItem(
    LEGACY_SELECTED_PROFESSOR_IDS_KEY,
  );
  if (!rawProfessorIds) {
    return null;
  }
  const professorIds = normalizeProfessorIds(JSON.parse(rawProfessorIds));
  if (professorIds === null) {
    throw new Error('旧版导师选择无效');
  }
  const rawResendContext = window.sessionStorage.getItem(
    LEGACY_BATCH_RESEND_CONTEXT_KEY,
  );
  return writeCreateTaskNavigationHandoff(
    professorIds,
    rawResendContext ? JSON.parse(rawResendContext) : null,
  );
};

export const readCreateTaskNavigationHandoff = (): CreateTaskNavigationHandoff | null => {
  try {
    // Prefer a legacy selection when it is present. This supports an in-place
    // app upgrade where an older renderer writes a fresh navigation context
    // while a stale v1 record still exists in the same session.
    if (
      window.sessionStorage.getItem(LEGACY_SELECTED_PROFESSOR_IDS_KEY)
    ) {
      return migrateLegacyNavigationHandoff();
    }
    const raw = window.sessionStorage.getItem(NAVIGATION_HANDOFF_STORAGE_KEY);
    if (raw) {
      return parseNavigationHandoff(JSON.parse(raw));
    }
    return migrateLegacyNavigationHandoff();
  } catch {
    clearCreateTaskNavigationHandoff();
    return null;
  }
};

export const clearCreateTaskNavigationHandoff = () => {
  window.sessionStorage.removeItem(NAVIGATION_HANDOFF_STORAGE_KEY);
  window.sessionStorage.removeItem(LEGACY_SELECTED_PROFESSOR_IDS_KEY);
  window.sessionStorage.removeItem(LEGACY_BATCH_RESEND_CONTEXT_KEY);
};

export const clearCreateTaskResendContext = () => {
  const handoff = readCreateTaskNavigationHandoff();
  if (handoff?.resendContext) {
    writeCreateTaskNavigationHandoff(handoff.professorIds);
  }
};
