import type {
  DesktopAgentUiHandoff,
  DesktopAgentUiHandoffSurface,
} from '@/types/desktop';

export type AgentProfessorSelectionMode = 'replace' | 'add';
export type AgentProfessorSelectionDisplay = 'keep_current' | 'selected_only';
export type AgentProfessorArchiveScope = 'active' | 'archived' | 'all';

export type AgentProfessorSelectionPayload = {
  kind: 'professor_selection';
  resource: 'professors';
  selection_mode: AgentProfessorSelectionMode;
  display: AgentProfessorSelectionDisplay;
  archive_scope: AgentProfessorArchiveScope;
  matched_count: number;
  excluded_count: number;
  identity_id?: number;
  ui_effects: string[];
};

export type AgentTaskContextPayload = {
  kind: 'task_context';
  resource: 'tasks';
  task_id: number;
  professor_id: number;
  identity_id: number;
  ui_effects: string[];
};

export type AgentCrawlJobContextPayload = {
  kind: 'crawl_job_context';
  resource: 'crawler.jobs';
  job_id: number;
  ui_effects: string[];
};

export type AgentCommunicationThreadContextPayload = {
  kind: 'communication_thread_context';
  resource: 'communications.threads';
  thread_id: string;
  professor_id: number;
  identity_id: number;
  ui_effects: string[];
};

type TypedAgentUiHandoff<
  TSurface extends DesktopAgentUiHandoffSurface,
  TPayload extends Record<string, unknown>,
> = Omit<DesktopAgentUiHandoff, 'surface' | 'payload'> & {
  surface: TSurface;
  payload: TPayload;
};

export type AgentProfessorManagementHandoff = TypedAgentUiHandoff<
  'professors.management',
  AgentProfessorSelectionPayload
>;

export type AgentProfessorHomeHandoff = TypedAgentUiHandoff<
  'professors.home',
  AgentProfessorSelectionPayload & { identity_id: number }
>;

export type AgentTaskCenterHandoff = TypedAgentUiHandoff<
  'tasks.center',
  AgentTaskContextPayload
>;

export type AgentCrawlJobHandoff = TypedAgentUiHandoff<
  'crawler.job',
  AgentCrawlJobContextPayload
>;

export type AgentCommunicationThreadHandoff = TypedAgentUiHandoff<
  'communications.thread',
  AgentCommunicationThreadContextPayload
>;

export type AgentDraftWorkspaceHandoff = TypedAgentUiHandoff<
  'draft.workspace',
  AgentTaskContextPayload
>;

export type ValidatedAgentUiHandoff =
  | AgentProfessorManagementHandoff
  | AgentProfessorHomeHandoff
  | AgentTaskCenterHandoff
  | AgentCrawlJobHandoff
  | AgentCommunicationThreadHandoff
  | AgentDraftWorkspaceHandoff;

export type AgentUiHandoffApplyResult =
  | {
      status: 'applied';
      result?: Record<string, unknown>;
    }
  | {
      status: 'awaiting_user';
      result?: Record<string, unknown>;
    }
  | {
      status: 'failed';
      failureMessage: string;
      result?: Record<string, unknown>;
    };

export type AgentUiHandoffSurfaceHandler = (
  handoff: ValidatedAgentUiHandoff,
) => AgentUiHandoffApplyResult | Promise<AgentUiHandoffApplyResult>;

const HANDOFF_ID_PATTERN = /^uih_[A-Za-z0-9_-]+$/;

const EXPECTED_ROUTE_BY_SURFACE: Record<
  DesktopAgentUiHandoffSurface,
  (payload: Record<string, unknown>) => string | null
> = {
  'professors.management': () => '/professors',
  'professors.home': () => '/',
  'tasks.center': () => '/tasks',
  'crawler.job': () => '/tasks',
  'communications.thread': (payload) => {
    const professorId = positiveInteger(payload.professor_id);
    return professorId === null ? null : `/workspace/${professorId}`;
  },
  'draft.workspace': (payload) => {
    const professorId = positiveInteger(payload.professor_id);
    return professorId === null ? null : `/workspace/${professorId}`;
  },
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const positiveInteger = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value > 0
    ? value
    : null;

const nonNegativeInteger = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0
    ? value
    : null;

const isStringArray = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === 'string');

const requireCommonPayload: (
  payload: Record<string, unknown>,
) => asserts payload is Record<string, unknown> & { ui_effects: string[] } = (
  payload,
) => {
  if (!isStringArray(payload.ui_effects)) {
    throw new Error('界面交接 payload.ui_effects 无效');
  }
};

const requireTaskPayload = (
  payload: Record<string, unknown>,
): AgentTaskContextPayload => {
  requireCommonPayload(payload);
  if (payload.kind !== 'task_context' || payload.resource !== 'tasks') {
    throw new Error('任务界面交接 payload 类型无效');
  }
  const taskId = positiveInteger(payload.task_id);
  const professorId = positiveInteger(payload.professor_id);
  const identityId = positiveInteger(payload.identity_id);
  if (taskId === null || professorId === null || identityId === null) {
    throw new Error('任务界面交接缺少有效的任务、导师或身份 ID');
  }
  return {
    kind: 'task_context',
    resource: 'tasks',
    task_id: taskId,
    professor_id: professorId,
    identity_id: identityId,
    ui_effects: payload.ui_effects,
  };
};

const requireProfessorPayload = (
  payload: Record<string, unknown>,
  surface: 'professors.management' | 'professors.home',
): AgentProfessorSelectionPayload => {
  requireCommonPayload(payload);
  if (
    payload.kind !== 'professor_selection' ||
    payload.resource !== 'professors' ||
    (payload.selection_mode !== 'replace' && payload.selection_mode !== 'add') ||
    (payload.display !== 'keep_current' && payload.display !== 'selected_only') ||
    (payload.archive_scope !== 'active' &&
      payload.archive_scope !== 'archived' &&
      payload.archive_scope !== 'all')
  ) {
    throw new Error('导师选择界面交接 payload 类型无效');
  }
  const matchedCount = nonNegativeInteger(payload.matched_count);
  const excludedCount = nonNegativeInteger(payload.excluded_count);
  if (matchedCount === null || excludedCount === null) {
    throw new Error('导师选择界面交接的匹配计数无效');
  }
  const identityId = positiveInteger(payload.identity_id);
  if (surface === 'professors.home' && identityId === null) {
    throw new Error('首页导师选择界面交接缺少身份 ID');
  }
  if (surface === 'professors.management' && payload.identity_id !== undefined) {
    throw new Error('导师管理页界面交接不应包含身份 ID');
  }
  return {
    kind: 'professor_selection',
    resource: 'professors',
    selection_mode: payload.selection_mode,
    display: payload.display,
    archive_scope: payload.archive_scope,
    matched_count: matchedCount,
    excluded_count: excludedCount,
    ...(identityId === null ? {} : { identity_id: identityId }),
    ui_effects: payload.ui_effects,
  };
};

export const validateAgentUiHandoff = (
  value: DesktopAgentUiHandoff,
): ValidatedAgentUiHandoff => {
  if (
    !isRecord(value) ||
    typeof value.handoffId !== 'string' ||
    value.handoffId.length > 64 ||
    !HANDOFF_ID_PATTERN.test(value.handoffId) ||
    value.schemaVersion !== 1 ||
    value.status !== 'claimed' ||
    typeof value.expiresAt !== 'string' ||
    !Number.isFinite(Date.parse(value.expiresAt)) ||
    typeof value.claimExpiresAt !== 'string' ||
    !Number.isFinite(Date.parse(value.claimExpiresAt))
  ) {
    throw new Error('当前版本无法处理该界面交接');
  }
  if (!isRecord(value.payload)) {
    throw new Error('界面交接 payload 无效');
  }
  const expectedRoute = EXPECTED_ROUTE_BY_SURFACE[value.surface]?.(value.payload);
  if (expectedRoute === null || expectedRoute !== value.route) {
    throw new Error('界面交接目标页面无效');
  }
  if (
    value.selectedIds.some(
      (id) => !Number.isInteger(id) || id <= 0,
    ) ||
    new Set(value.selectedIds).size !== value.selectedIds.length
  ) {
    throw new Error('界面交接包含无效或重复的导师 ID');
  }

  switch (value.surface) {
    case 'professors.management':
    case 'professors.home': {
      if (
        value.selectionCount === 0 ||
        value.selectionCount > 10_000 ||
        value.selectedIds.length !== value.selectionCount
      ) {
        throw new Error('导师选择界面交接的选择数量无效');
      }
      const payload = requireProfessorPayload(value.payload, value.surface);
      return { ...value, surface: value.surface, payload } as
        | AgentProfessorManagementHandoff
        | AgentProfessorHomeHandoff;
    }
    case 'tasks.center':
    case 'draft.workspace': {
      if (value.selectionCount !== 1 || value.selectedIds.length !== 0) {
        throw new Error('任务界面交接的资源数量无效');
      }
      const payload = requireTaskPayload(value.payload);
      return { ...value, surface: value.surface, payload } as
        | AgentTaskCenterHandoff
        | AgentDraftWorkspaceHandoff;
    }
    case 'crawler.job': {
      requireCommonPayload(value.payload);
      const jobId = positiveInteger(value.payload.job_id);
      if (
        value.selectionCount !== 1 ||
        value.selectedIds.length !== 0 ||
        value.payload.kind !== 'crawl_job_context' ||
        value.payload.resource !== 'crawler.jobs' ||
        jobId === null
      ) {
        throw new Error('抓取任务界面交接 payload 无效');
      }
      return {
        ...value,
        surface: 'crawler.job',
        payload: {
          kind: 'crawl_job_context',
          resource: 'crawler.jobs',
          job_id: jobId,
          ui_effects: value.payload.ui_effects,
        },
      };
    }
    case 'communications.thread': {
      requireCommonPayload(value.payload);
      const professorId = positiveInteger(value.payload.professor_id);
      const identityId = positiveInteger(value.payload.identity_id);
      if (
        value.selectionCount !== 1 ||
        value.selectedIds.length !== 0 ||
        value.payload.kind !== 'communication_thread_context' ||
        value.payload.resource !== 'communications.threads' ||
        typeof value.payload.thread_id !== 'string' ||
        value.payload.thread_id !== `${identityId}:${professorId}` ||
        professorId === null ||
        identityId === null
      ) {
        throw new Error('通信线程界面交接 payload 无效');
      }
      return {
        ...value,
        surface: 'communications.thread',
        payload: {
          kind: 'communication_thread_context',
          resource: 'communications.threads',
          thread_id: value.payload.thread_id,
          professor_id: professorId,
          identity_id: identityId,
          ui_effects: value.payload.ui_effects,
        },
      };
    }
  }
};

export const getAgentUiHandoffIdentityId = (
  handoff: ValidatedAgentUiHandoff,
): number | null => {
  switch (handoff.surface) {
    case 'professors.home':
    case 'tasks.center':
    case 'communications.thread':
    case 'draft.workspace':
      return handoff.payload.identity_id;
    case 'professors.management':
    case 'crawler.job':
      return null;
  }
};

export const isAgentProfessorManagementHandoff = (
  handoff: ValidatedAgentUiHandoff,
): handoff is AgentProfessorManagementHandoff =>
  handoff.surface === 'professors.management';

export const isAgentProfessorHomeHandoff = (
  handoff: ValidatedAgentUiHandoff,
): handoff is AgentProfessorHomeHandoff => handoff.surface === 'professors.home';

export const isAgentTaskCenterHandoff = (
  handoff: ValidatedAgentUiHandoff,
): handoff is AgentTaskCenterHandoff => handoff.surface === 'tasks.center';

export const isAgentCrawlJobHandoff = (
  handoff: ValidatedAgentUiHandoff,
): handoff is AgentCrawlJobHandoff => handoff.surface === 'crawler.job';

export const isAgentCommunicationThreadHandoff = (
  handoff: ValidatedAgentUiHandoff,
): handoff is AgentCommunicationThreadHandoff =>
  handoff.surface === 'communications.thread';

export const isAgentDraftWorkspaceHandoff = (
  handoff: ValidatedAgentUiHandoff,
): handoff is AgentDraftWorkspaceHandoff => handoff.surface === 'draft.workspace';
