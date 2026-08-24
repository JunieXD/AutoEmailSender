import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import clsx from 'clsx';
import { Link, Navigate, useBeforeUnload, useBlocker, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, CalendarClock, Loader2, X } from 'lucide-react';
import {
  MODAL_BACKDROP_CLASS_NAME,
  MODAL_SURFACE_CLASS_NAME,
} from '@/components/atoms/modalStyles';
import { WorkspaceComposerDock } from '@/components/organisms/WorkspaceComposerDock';
import { WorkspaceMessageThread } from '@/components/organisms/WorkspaceMessageThread';
import { WorkspaceSidebar } from '@/components/organisms/WorkspaceSidebar';
import { useNotification } from '@/context/NotificationContext';
import { useSelectionContext } from '@/context/SelectionContext';
import { useWorkspaceDraftGuard } from '@/context/useWorkspaceDraftGuard';
import {
  isAgentCommunicationThreadHandoff,
  isAgentDraftWorkspaceHandoff,
} from '@/features/agent-ui-handoffs/types';
import {
  useActiveAgentUiHandoff,
  useAgentUiHandoffSurface,
} from '@/features/agent-ui-handoffs/useAgentUiHandoffSurface';
import {
  buildLargeAttachmentWarning,
  formatFileSize,
  getSelectedAttachmentTotalBytes,
  LARGE_ATTACHMENT_WARNING_CONFIRMATION_LABEL,
  shouldPromptForLargeAttachments,
  suppressLargeAttachmentWarnings,
} from '@/features/attachments/attachmentSize';
import { getEmailSendFailureMessage } from '@/features/email/client/getEmailSendFailureMessage';
import { getWorkspaceNextStep } from '@/features/workspace/client/getWorkspaceNextStep';
import { bootstrapWorkspaceThread } from '@/features/workspace/client/openWorkspaceThread';
import {
  isCommunicationMessage,
  isFailedSentMessage,
  isSuccessfulSentMessage,
} from '@/features/workspace/client/workspaceMessageDelivery';
import {
  approveAndSchedule,
  approveAndSend,
  calculateMatch,
  cancelScheduledTask,
  continueManually,
  getEmailTaskThread,
  rewriteDraft,
  saveDraft,
  startFollowUp,
  updateTaskOutreachConfig,
} from '@/lib/api/emailTasksApi';
import { getOutreachTemplate, listOutreachTemplates } from '@/lib/api/outreachTemplates';
import {
  getWorkspaceThread,
  refreshWorkspaceReplies,
} from '@/lib/api/workspacesApi';
import { parseApiDateTime } from '@/lib/dateTime';
import { extractPlainTextFromHtml } from '@/lib/htmlPreview';
import { textToEmailHtml } from '@/lib/richEmail';
import {
  normalizeTemplatePlaceholderHtmlForCompare,
  prepareTemplateEditorHtml,
} from '@/lib/templatePlaceholders';
import { useConfirmDialog } from '@/lib/useConfirmDialog';
import { useDismissableLayerClick } from '@/lib/useDismissableLayerClick';
import {
  PROFESSOR_STATUS_LABELS,
  type OutreachTemplateDTO,
  type WorkspaceMessageDTO,
  type WorkspaceProfessorDTO,
  type WorkspaceTaskStatusLabelKey,
  type WorkspaceTaskSummaryDTO,
  type WorkspaceThreadDTO,
} from '@/types';

const WORKSPACE_STATUS_LABELS: Record<WorkspaceTaskStatusLabelKey, string> = {
  discovered: '待处理',
  matched: PROFESSOR_STATUS_LABELS.matched,
  generating_draft: PROFESSOR_STATUS_LABELS.generating_draft,
  draft_failed: PROFESSOR_STATUS_LABELS.draft_failed,
  review_required: PROFESSOR_STATUS_LABELS.review_required,
  approved: '待发送',
  scheduled: PROFESSOR_STATUS_LABELS.scheduled,
  schedule_missed: '错过计划',
  sending: PROFESSOR_STATUS_LABELS.sending,
  sent: PROFESSOR_STATUS_LABELS.sent,
  send_failed: PROFESSOR_STATUS_LABELS.send_failed,
  reply_detected: PROFESSOR_STATUS_LABELS.reply_detected,
  canceled: '已取消',
};

const WORKSPACE_THREAD_REFRESH_INTERVAL_MS = 60_000;
const WORKSPACE_REWRITE_REFRESH_INTERVAL_MS = 3_000;

const formatTokenValue = (value: number | null | undefined) =>
  value == null ? '未返回' : value.toLocaleString('zh-CN');

const formatElapsedSeconds = (elapsedMs: number) =>
  `${(Math.max(elapsedMs, 0) / 1000).toFixed(1)} 秒`;

const buildDraftGenerationSuccessDescription = (
  task: WorkspaceTaskSummaryDTO | null | undefined,
  elapsedMs: number,
) =>
  `输入 ${formatTokenValue(task?.last_draft_prompt_tokens)} / 输出 ${formatTokenValue(
    task?.last_draft_completion_tokens,
  )} / 总计 ${formatTokenValue(task?.last_draft_total_tokens)} Token，耗时 ${formatElapsedSeconds(
    elapsedMs,
  )}`;

const buildMessagePreviewText = (message: WorkspaceMessageDTO) => {
  const htmlSource = message.content_html?.trim() || message.content.trim();
  if (htmlSource) {
    const htmlText = extractPlainTextFromHtml(htmlSource);
    if (htmlText) {
      return htmlText;
    }
  }
  return message.content.replace(/\s+/g, ' ').trim();
};

const getReceivedMessages = (messages: WorkspaceMessageDTO[]) =>
  messages.filter((message) => message.direction === 'received');

const buildNewReplyNotificationDescription = (
  professorName: string,
  message: WorkspaceMessageDTO,
) => {
  const subject = message.subject?.trim();
  if (subject) {
    return `${professorName}回复了：${subject}`;
  }
  const content = buildMessagePreviewText(message);
  return `${professorName}回复了${content ? `：${content.slice(0, 36)}` : ''}`;
};

const getDefaultScheduledAtValue = () => {
  const local = new Date(Date.now() + 3600_000);
  local.setMinutes(Math.ceil(local.getMinutes() / 5) * 5);
  // time-check: local-control-value, datetime-local default is derived from local wall-clock time.
  const adjusted = new Date(local.getTime() - local.getTimezoneOffset() * 60000);
  return adjusted.toISOString().slice(0, 16);
};

const getCurrentTaskOrNull = (
  thread: WorkspaceThreadDTO | null,
): WorkspaceTaskSummaryDTO | null =>
  thread?.current_task?.id != null ? thread.current_task : null;

type SyncComposerOptions = {
  preserveDirty?: boolean;
};

const shouldBlockDirectDraftActions = (task: WorkspaceTaskSummaryDTO | null) =>
  Boolean(
      task?.can_continue_manually ||
      task?.can_write_follow_up ||
      task?.status === 'canceled' ||
      task?.status === 'sending' ||
      task?.sent_at ||
      task?.is_replied,
  );

const shouldHideDirectDraftContent = (task: WorkspaceTaskSummaryDTO | null) =>
  Boolean(
      task?.can_continue_manually ||
      task?.can_write_follow_up ||
      task?.status === 'canceled' ||
      task?.status === 'sending' ||
      task?.sent_at ||
      task?.is_replied,
  );

const hasProfessorMatchEvidence = (professor: WorkspaceProfessorDTO | null | undefined) =>
  Boolean(professor?.research_direction?.trim()) ||
  Boolean(professor?.recent_papers?.some((paper) => paper.trim()));

const hasProfessorResearchDirection = (professor: WorkspaceProfessorDTO | null | undefined) =>
  Boolean(professor?.research_direction?.trim());

const getStatusLabel = (
  currentTask: WorkspaceTaskSummaryDTO | null,
  messages: WorkspaceMessageDTO[] = [],
) => {
  if (messages.some((message) => message.direction === 'received')) {
    return PROFESSOR_STATUS_LABELS.reply_detected;
  }
  if (messages.some(isSuccessfulSentMessage)) {
    return PROFESSOR_STATUS_LABELS.sent;
  }
  if (!currentTask?.status) {
    return '尚未创建任务';
  }
  return WORKSPACE_STATUS_LABELS[currentTask.status] ?? currentTask.status;
};

const getWorkspaceNextStepDescription = (title: string) => {
  switch (title) {
    case '单独联系':
      return '从批量任务中拆出，继续联系。';
    case '写跟进邮件':
      return '根据沟通记录起草跟进邮件。';
    case '查看失败原因并重试':
      return '检查失败原因，修正后重试。';
    case '重新安排发送时间':
      return '原计划已错过，请重新定时或确认立即发送。';
    case '选择分析材料':
      return '选择材料后可分析匹配度。';
    case '生成邮件草稿':
      return '让 AI 改写当前草稿，完成后检查。';
    case '确认发送时间':
      return '确认发送时间，或改为立即发送。';
    default:
      return '检查主题、正文和附件后发送。';
  }
};

const deriveBodyTextFromDraft = ({
  content,
  contentHtml,
}: {
  content: string;
  contentHtml: string | null;
}) => {
  const trimmedContent = content.trim();
  if (trimmedContent) {
    return trimmedContent;
  }

  const trimmedHtml = contentHtml?.trim();
  if (!trimmedHtml) {
    return '';
  }
  const normalizedHtml = trimmedHtml
    .replace(/<\s*br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|li|tr|h[1-6])>/gi, '\n');

  if (typeof DOMParser !== 'undefined') {
    const document = new DOMParser().parseFromString(normalizedHtml, 'text/html');
    const text = document.body.textContent?.replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
    if (text) {
      return text;
    }
  }

  return normalizedHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
};

const hasMeaningfulBody = ({
  content,
  contentHtml,
}: {
  content: string;
  contentHtml: string | null;
}) => Boolean(deriveBodyTextFromDraft({ content, contentHtml }).trim());

type ComposerDraftSnapshot = {
  subject: string;
  body: {
    kind: 'html' | 'text';
    value: string;
  };
  selectedMaterialIds: number[];
};

const normalizeDraftHtmlForCompare = (value: string) =>
  normalizeTemplatePlaceholderHtmlForCompare(prepareTemplateEditorHtml(value));

const normalizeSelectedMaterialIds = (ids: number[]) => [...ids].sort((left, right) => left - right);

const buildComposerDraftSnapshot = ({
  subject,
  content,
  contentHtml,
  selectedMaterialIds,
}: {
  subject: string;
  content: string;
  contentHtml: string | null;
  selectedMaterialIds: number[];
}): ComposerDraftSnapshot => {
  const bodyText = deriveBodyTextFromDraft({ content, contentHtml });
  const displayHtml = contentHtml?.trim() || (bodyText ? textToEmailHtml(bodyText) : '');
  const normalizedHtml = displayHtml ? normalizeDraftHtmlForCompare(displayHtml) : '';

  return {
    subject,
    body: normalizedHtml
      ? {
          kind: 'html',
          value: normalizedHtml,
        }
      : {
          kind: 'text',
          value: bodyText,
        },
    selectedMaterialIds: normalizeSelectedMaterialIds(selectedMaterialIds),
  };
};

const areComposerDraftSnapshotsEqual = (
  left: ComposerDraftSnapshot | null,
  right: ComposerDraftSnapshot,
) => {
  if (!left) {
    return false;
  }

  return (
    left.subject === right.subject &&
    left.body.kind === right.body.kind &&
    left.body.value === right.body.value &&
    left.selectedMaterialIds.length === right.selectedMaterialIds.length &&
    left.selectedMaterialIds.every((id, index) => id === right.selectedMaterialIds[index])
  );
};

const buildTaskDraftSnapshot = (task: WorkspaceTaskSummaryDTO): ComposerDraftSnapshot =>
  buildComposerDraftSnapshot({
    subject: task.draft.subject ?? '',
    content: task.draft.body_text ?? '',
    contentHtml: task.draft.body_html ?? null,
    selectedMaterialIds: task.selected_material_ids ?? [],
  });

const ScheduleSendDialog = ({
  open,
  professorEmail,
  selectedMaterialCount,
  selectedAttachmentTotalBytes,
  value,
  acting,
  onChange,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  professorEmail: string | null | undefined;
  selectedMaterialCount: number;
  selectedAttachmentTotalBytes: number;
  value: string;
  acting: boolean;
  onChange: (value: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) => {
  const attachmentWarning = buildLargeAttachmentWarning(selectedAttachmentTotalBytes);
  const {
    onBackdropClick,
    onBackdropMouseDown,
    onContentClick,
    onContentMouseDown,
  } =
    useDismissableLayerClick(onCancel);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onCancel();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onCancel, open]);

  if (!open) {
    return null;
  }

  return (
    <div
      className={`${MODAL_BACKDROP_CLASS_NAME} z-[90]`}
      onClick={onBackdropClick}
      onMouseDown={onBackdropMouseDown}
    >
      <div
        className={`${MODAL_SURFACE_CLASS_NAME} w-full max-w-md`}
        onClick={onContentClick}
        onMouseDown={onContentMouseDown}
      >
        <div className="absolute inset-x-0 top-0 h-24 bg-[radial-gradient(circle_at_top,rgba(251,191,36,0.18),transparent_68%)]" />
        <div className="relative px-6 py-6">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-red-100 text-red-600 shadow-sm shadow-red-100/80">
                <CalendarClock className="h-5 w-5" />
              </div>
              <div>
                <h3 className="text-lg font-semibold tracking-[0.01em] text-stone-900">
                  选择定时发送时间
                </h3>
                <p className="mt-2 text-sm leading-6 text-stone-600">
                  将真实发给 {professorEmail ?? '当前导师邮箱'}，并附带 {selectedMaterialCount} 份附件，共 {formatFileSize(selectedAttachmentTotalBytes)}。
                </p>
                {attachmentWarning ? (
                  <p className="mt-3 whitespace-pre-line text-sm leading-6 text-amber-800">
                    {attachmentWarning}
                  </p>
                ) : null}
              </div>
            </div>
            <button
              type="button"
              onClick={onCancel}
              className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 bg-white/80 text-stone-500 transition hover:border-stone-300 hover:bg-white hover:text-stone-900"
              aria-label="关闭定时发送弹层"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <label className="mt-5 block">
            <div className="mb-2 text-sm font-medium text-stone-800">发送时间</div>
            <input
              type="datetime-local"
              value={value}
              onChange={(event) => onChange(event.target.value)}
              className="form-input"
            />
          </label>

          <div className="mt-6 flex flex-wrap justify-end gap-3">
            <button
              type="button"
              onClick={onCancel}
              disabled={acting}
              className="inline-flex items-center justify-center rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm font-medium text-stone-700 transition hover:border-stone-300 hover:bg-stone-50 hover:text-stone-900 disabled:cursor-not-allowed disabled:opacity-60"
            >
              再检查一下
            </button>
            <button
              type="button"
              onClick={onConfirm}
              disabled={acting}
              className="inline-flex items-center justify-center rounded-2xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-red-200/90 transition hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              确认定时
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export const WorkspacePage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const professorId = Number(id);
  const requestedTaskIdValue = Number(searchParams.get('task_id'));
  const requestedTaskId =
    Number.isInteger(requestedTaskIdValue) && requestedTaskIdValue > 0
      ? requestedTaskIdValue
      : null;
  const { notifyError, notifyFormErrors, notifySuccess } = useNotification();
  const { confirm, choose, dialog: confirmDialog } = useConfirmDialog();
  const {
    selectedIdentityId,
    selectedLlmProfileId,
    selectedIdentity,
    communicationScopeKey = '',
    matchSourceIdentity,
    matchScopeKey = '',
  } = useSelectionContext();
  const { registerWorkspaceDraftGuard } = useWorkspaceDraftGuard();
  const activeAgentUiHandoff = useActiveAgentUiHandoff();
  const pendingCommunicationPresentation =
    activeAgentUiHandoff !== null &&
    isAgentCommunicationThreadHandoff(activeAgentUiHandoff) &&
    activeAgentUiHandoff.payload.professor_id === professorId &&
    activeAgentUiHandoff.payload.identity_id === selectedIdentityId;
  const [thread, setThread] = useState<WorkspaceThreadDTO | null>(null);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [subject, setSubject] = useState('');
  const [content, setContent] = useState('');
  const [contentHtml, setContentHtml] = useState<string | null>(null);
  const [outreachTemplates, setOutreachTemplates] = useState<OutreachTemplateDTO[]>([]);
  const [loadingOutreachTemplates, setLoadingOutreachTemplates] = useState(true);
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<number[]>([]);
  const [scheduledAt, setScheduledAt] = useState(getDefaultScheduledAtValue);
  const [scheduleDialogOpen, setScheduleDialogOpen] = useState(false);
  const [pendingScheduledAt, setPendingScheduledAt] = useState(getDefaultScheduledAtValue);
  const [composerExpanded, setComposerExpanded] = useState(false);
  const [threadRefreshing, setThreadRefreshing] = useState(false);
  const [lastThreadCheckedAt, setLastThreadCheckedAt] = useState<Date | null>(null);
  const [newReceivedCount, setNewReceivedCount] = useState(0);
  const [draftSaving, setDraftSaving] = useState(false);
  const [draftRewriting, setDraftRewriting] = useState(false);
  const [savingBeforeNavigate, setSavingBeforeNavigate] = useState(false);
  const [communicationPresentationOnly, setCommunicationPresentationOnly] =
    useState(pendingCommunicationPresentation);
  const pendingCommunicationPresentationRef = useRef(
    pendingCommunicationPresentation,
  );
  pendingCommunicationPresentationRef.current = pendingCommunicationPresentation;
  const mountedRef = useRef(true);
  const handledBlockerLocationKeyRef = useRef<string | null>(null);
  const approvedNavigationPathRef = useRef<string | null>(null);
  const loadedThreadKeyRef = useRef<string | null>(null);
  const activeThreadRequestKeyRef = useRef<string | null>(null);
  const latestThreadRequestIdRef = useRef(0);
  const currentWorkspaceRequestKeyRef = useRef<string | null>(null);
  const latestActionRequestIdRef = useRef(0);
  const latestDraftSaveRequestIdRef = useRef(0);
  const knownReceivedMessageIdsRef = useRef<Set<number>>(new Set());
  const composerDirtyRef = useRef(false);
  const composerBaselineRef = useRef<ComposerDraftSnapshot | null>(null);
  const draftRewritingRef = useRef(false);
  const activeRewriteTaskIdRef = useRef<number | null>(null);
  const activeRewriteObservedGeneratingRef = useRef(false);
  const activeRewritePreviousDraftRef = useRef<ComposerDraftSnapshot | null>(null);
  const activeRewriteActionRequestIdRef = useRef<number | null>(null);
  const captureNextRewriteActionRequestRef = useRef(false);
  const communicationPresentationScopeRef = useRef(
    `${professorId}:${selectedIdentityId ?? ''}`,
  );
  const workspaceRequestKey =
    Number.isFinite(professorId) && selectedIdentityId && selectedLlmProfileId
      ? `${professorId}:${selectedIdentityId}:${selectedLlmProfileId}:${communicationScopeKey || selectedIdentityId}:${matchScopeKey || selectedIdentityId}:${requestedTaskId ?? 'latest'}`
      : null;
  const currentTask = getCurrentTaskOrNull(thread);
  const currentTaskId = currentTask?.id ?? null;
  const selectedAttachmentTotalBytes = useMemo(
    () =>
      getSelectedAttachmentTotalBytes(
        thread?.material_options ?? [],
        selectedMaterialIds,
      ),
    [selectedMaterialIds, thread?.material_options],
  );
  const taskGeneratingDraft = currentTask?.status === 'generating_draft';
  const isRewriting = taskGeneratingDraft || draftRewriting;

  useEffect(() => {
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const scope = `${professorId}:${selectedIdentityId ?? ''}`;
    if (communicationPresentationScopeRef.current === scope) {
      return;
    }
    communicationPresentationScopeRef.current = scope;
    setCommunicationPresentationOnly(
      pendingCommunicationPresentationRef.current,
    );
  }, [professorId, selectedIdentityId]);

  useEffect(() => {
    let ignore = false;
    const loadTemplates = async () => {
      setLoadingOutreachTemplates(true);
      try {
        const templates = await listOutreachTemplates(true);
        if (!ignore) {
          setOutreachTemplates(templates);
        }
      } catch (error) {
        if (!ignore) {
          notifyError(
            '加载发信模板失败',
            error instanceof Error ? error.message : '加载发信模板失败',
          );
        }
      } finally {
        if (!ignore) {
          setLoadingOutreachTemplates(false);
        }
      }
    };
    void loadTemplates();
    return () => {
      ignore = true;
    };
  }, [notifyError]);

  const syncComposer = useCallback((data: WorkspaceThreadDTO, options: SyncComposerOptions = {}) => {
    if (options.preserveDirty && composerDirtyRef.current) {
      return;
    }

    const currentTask = getCurrentTaskOrNull(data);
    const hiddenDraftContent = shouldHideDirectDraftContent(currentTask);
    const draft = currentTask?.draft;
    const nextSubject = hiddenDraftContent ? '' : draft?.subject ?? '';
    const nextContentHtml = hiddenDraftContent ? null : draft?.body_html ?? null;
    const nextContentText = hiddenDraftContent ? '' : draft?.body_text ?? '';

    setSubject(nextSubject);
    setContent(nextContentText);
    setContentHtml(nextContentHtml);
    setSelectedMaterialIds(hiddenDraftContent ? [] : currentTask?.selected_material_ids ?? []);
    composerBaselineRef.current = buildComposerDraftSnapshot({
      subject: nextSubject,
      content: nextContentText,
      contentHtml: nextContentHtml,
      selectedMaterialIds: hiddenDraftContent ? [] : currentTask?.selected_material_ids ?? [],
    });
    setScheduledAt(
      !hiddenDraftContent && currentTask?.scheduled_at
        ? (() => {
            const scheduled = parseApiDateTime(currentTask.scheduled_at);
            const local = new Date(
              scheduled.getTime() - scheduled.getTimezoneOffset() * 60000,
            );
            return local.toISOString().slice(0, 16);
          })()
        : getDefaultScheduledAtValue(),
    );
    composerDirtyRef.current = false;
  }, []);

  const resetActiveRewriteTracking = useCallback(() => {
    draftRewritingRef.current = false;
    activeRewriteTaskIdRef.current = null;
    activeRewriteObservedGeneratingRef.current = false;
    activeRewritePreviousDraftRef.current = null;
    activeRewriteActionRequestIdRef.current = null;
    captureNextRewriteActionRequestRef.current = false;
    setDraftRewriting(false);
  }, []);

  const loadThread = useCallback(async (options: { refreshReplies?: boolean; silent?: boolean } = {}) => {
    const silent = options.silent ?? false;
    if (!workspaceRequestKey || !selectedIdentityId || !selectedLlmProfileId || !Number.isFinite(professorId)) {
      latestThreadRequestIdRef.current += 1;
      activeThreadRequestKeyRef.current = null;
      loadedThreadKeyRef.current = null;
      knownReceivedMessageIdsRef.current = new Set();
      setThread(null);
      setLoadFailed(false);
      setLoading(false);
      setThreadRefreshing(false);
      setLastThreadCheckedAt(null);
      setNewReceivedCount(0);
      composerBaselineRef.current = null;
      composerDirtyRef.current = false;
      return;
    }

    const requestId = latestThreadRequestIdRef.current + 1;
    latestThreadRequestIdRef.current = requestId;
    activeThreadRequestKeyRef.current = workspaceRequestKey;
    if (silent) {
      setThreadRefreshing(true);
    } else {
      setLoading(true);
    }
    try {
      let workspaceData: WorkspaceThreadDTO;
      if (requestedTaskId !== null) {
        if (options.refreshReplies) {
          await refreshWorkspaceReplies(
            professorId,
            selectedIdentityId,
            selectedLlmProfileId,
          );
        }
        workspaceData = await getEmailTaskThread(requestedTaskId);
      } else {
        const data = await (options.refreshReplies
          ? refreshWorkspaceReplies
          : getWorkspaceThread)(
          professorId,
          selectedIdentityId,
          selectedLlmProfileId,
        );
        workspaceData =
          communicationPresentationOnly ||
          pendingCommunicationPresentationRef.current
          ? data
          : await bootstrapWorkspaceThread(
              data,
              professorId,
              selectedIdentityId,
              selectedLlmProfileId,
            );
      }
      if (
        latestThreadRequestIdRef.current !== requestId ||
        activeThreadRequestKeyRef.current !== workspaceRequestKey
      ) {
        return;
      }
      const hadLoadedThread = loadedThreadKeyRef.current === workspaceRequestKey;
      const refreshedTask = getCurrentTaskOrNull(workspaceData);
      let rewriteCompletedByRefresh = false;
      if (
        silent &&
        draftRewritingRef.current &&
        activeRewriteTaskIdRef.current != null &&
        refreshedTask?.id === activeRewriteTaskIdRef.current
      ) {
        if (refreshedTask.status === 'generating_draft') {
          activeRewriteObservedGeneratingRef.current = true;
        } else {
          const refreshedDraft = buildTaskDraftSnapshot(refreshedTask);
          const previousDraft = activeRewritePreviousDraftRef.current;
          const stillLooksLikePreRewriteDraft =
            previousDraft !== null && areComposerDraftSnapshotsEqual(previousDraft, refreshedDraft);
          rewriteCompletedByRefresh =
            activeRewriteObservedGeneratingRef.current || !stillLooksLikePreRewriteDraft;
          if (rewriteCompletedByRefresh) {
            const rewriteActionRequestId = activeRewriteActionRequestIdRef.current;
            if (
              rewriteActionRequestId !== null &&
              latestActionRequestIdRef.current === rewriteActionRequestId
            ) {
              latestActionRequestIdRef.current += 1;
            }
            setActing(false);
            resetActiveRewriteTracking();
          }
        }
      }
      const receivedMessages = getReceivedMessages(workspaceData.messages);
      const newReceivedMessages = hadLoadedThread
        ? receivedMessages.filter(
            (message) => !knownReceivedMessageIdsRef.current.has(message.id),
          )
        : [];
      knownReceivedMessageIdsRef.current = new Set(
        receivedMessages.map((message) => message.id),
      );
      setThread(workspaceData);
      setLoadFailed(false);
      setLastThreadCheckedAt(new Date());
      if (newReceivedMessages.length > 0) {
        const latestReceived = newReceivedMessages[newReceivedMessages.length - 1];
        setNewReceivedCount((current) => current + newReceivedMessages.length);
        notifySuccess(
          '收到导师回复',
          buildNewReplyNotificationDescription(workspaceData.professor.name, latestReceived),
        );
      }
      syncComposer(workspaceData, { preserveDirty: silent && !rewriteCompletedByRefresh });
      loadedThreadKeyRef.current = workspaceRequestKey;
    } catch (loadError) {
      if (
        latestThreadRequestIdRef.current !== requestId ||
        activeThreadRequestKeyRef.current !== workspaceRequestKey
      ) {
        return;
      }
      const message = loadError instanceof Error ? loadError.message : '加载工作区失败';
      if (loadedThreadKeyRef.current !== workspaceRequestKey) {
        setThread(null);
        setLoadFailed(true);
      } else {
        setLoadFailed(false);
      }
      if (!silent) {
        notifyError('加载工作区失败', message);
      }
    } finally {
      if (
        latestThreadRequestIdRef.current === requestId &&
        activeThreadRequestKeyRef.current === workspaceRequestKey
      ) {
        if (silent) {
          setThreadRefreshing(false);
        } else {
          setLoading(false);
        }
      }
    }
  }, [communicationPresentationOnly, notifyError, notifySuccess, professorId, requestedTaskId, resetActiveRewriteTracking, selectedIdentityId, selectedLlmProfileId, syncComposer, workspaceRequestKey]);

  const commitPresentedThread = useCallback(
    (data: WorkspaceThreadDTO) => {
      latestThreadRequestIdRef.current += 1;
      activeThreadRequestKeyRef.current = workspaceRequestKey;
      knownReceivedMessageIdsRef.current = new Set(
        getReceivedMessages(data.messages).map((message) => message.id),
      );
      setThread(data);
      setLoadFailed(false);
      setLoading(false);
      setThreadRefreshing(false);
      setLastThreadCheckedAt(new Date());
      setNewReceivedCount(0);
      syncComposer(data);
      loadedThreadKeyRef.current = workspaceRequestKey;
    },
    [syncComposer, workspaceRequestKey],
  );

  useEffect(() => {
    void loadThread();
  }, [loadThread]);

  useAgentUiHandoffSurface("communications.thread", async (handoff) => {
    if (!isAgentCommunicationThreadHandoff(handoff)) {
      return {
        status: "failed",
        failureMessage: "工作区收到的通信线程界面交接类型不匹配。",
      };
    }
    if (
      handoff.payload.professor_id !== professorId ||
      handoff.payload.identity_id !== selectedIdentityId
    ) {
      return {
        status: "failed",
        failureMessage: "工作区尚未切换到界面交接指定的导师和身份。",
      };
    }
    if (!selectedLlmProfileId) {
      return {
        status: "failed",
        failureMessage: "打开通信线程前需要先配置并选择一个模型。",
      };
    }

    pendingCommunicationPresentationRef.current = true;
    setCommunicationPresentationOnly(true);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("task_id");
      return next;
    }, { replace: true });
    latestThreadRequestIdRef.current += 1;
    const data = await getWorkspaceThread(
      professorId,
      selectedIdentityId,
      selectedLlmProfileId,
    );
    if (
      data.professor.id !== professorId ||
      data.identity.id !== selectedIdentityId
    ) {
      throw new Error("通信线程响应与界面交接不匹配。");
    }
    commitPresentedThread(data);
    return {
      status: "applied",
      result: {
        surface: handoff.surface,
        thread_id: handoff.payload.thread_id,
        professor_id: professorId,
        identity_id: selectedIdentityId,
        presentation_only: true,
      },
    };
  });

  useAgentUiHandoffSurface("draft.workspace", async (handoff) => {
    if (!isAgentDraftWorkspaceHandoff(handoff)) {
      return {
        status: "failed",
        failureMessage: "工作区收到的草稿界面交接类型不匹配。",
      };
    }
    if (
      handoff.payload.professor_id !== professorId ||
      handoff.payload.identity_id !== selectedIdentityId
    ) {
      return {
        status: "failed",
        failureMessage: "工作区尚未切换到界面交接指定的导师和身份。",
      };
    }
    if (!selectedLlmProfileId) {
      return {
        status: "failed",
        failureMessage: "打开草稿工作区前需要先配置并选择一个模型。",
      };
    }

    setCommunicationPresentationOnly(false);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("task_id", String(handoff.payload.task_id));
      return next;
    }, { replace: true });
    latestThreadRequestIdRef.current += 1;
    const data = await getEmailTaskThread(handoff.payload.task_id);
    if (
      data.professor.id !== professorId ||
      data.identity.id !== selectedIdentityId ||
      data.current_task.id !== handoff.payload.task_id
    ) {
      throw new Error("草稿工作区响应与界面交接不匹配。");
    }
    commitPresentedThread(data);
    setComposerExpanded(true);
    return {
      status: "applied",
      result: {
        surface: handoff.surface,
        task_id: handoff.payload.task_id,
        professor_id: professorId,
        identity_id: selectedIdentityId,
        composer_expanded: true,
      },
    };
  });

  useEffect(() => {
    if (!workspaceRequestKey) {
      return undefined;
    }

    const intervalId = window.setInterval(() => {
      void loadThread({ silent: true });
    }, isRewriting ? WORKSPACE_REWRITE_REFRESH_INTERVAL_MS : WORKSPACE_THREAD_REFRESH_INTERVAL_MS);

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void loadThread({ silent: true });
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [isRewriting, loadThread, workspaceRequestKey]);

  useEffect(() => {
    currentWorkspaceRequestKeyRef.current = workspaceRequestKey;
    latestActionRequestIdRef.current += 1;
    knownReceivedMessageIdsRef.current = new Set();
    setActing(false);
    setScheduleDialogOpen(false);
    setThreadRefreshing(false);
    setLastThreadCheckedAt(null);
    setNewReceivedCount(0);
    setDraftSaving(false);
    resetActiveRewriteTracking();
    composerBaselineRef.current = null;
    composerDirtyRef.current = false;
  }, [resetActiveRewriteTracking, workspaceRequestKey]);

  useEffect(() => {
    setComposerExpanded(false);
  }, [professorId, selectedIdentityId, selectedLlmProfileId]);

  const statusLabel = getStatusLabel(currentTask, thread?.messages ?? []);
  const blocksDirectDraftActions = shouldBlockDirectDraftActions(currentTask);
  const effectiveMatchSourceIdentity = matchSourceIdentity ?? selectedIdentity;
  const resolvedMatchSourceIdentity =
    thread?.match_source_identity ?? effectiveMatchSourceIdentity;
  const canCalculateMatch =
    Boolean(currentTaskId) &&
    Boolean(effectiveMatchSourceIdentity?.current_primary_material_id) &&
    hasProfessorMatchEvidence(thread?.professor) &&
    !blocksDirectDraftActions;
  const matchAnalysisDescription = thread?.match_uses_group_source
    ? `匹配分析将统一使用 ${resolvedMatchSourceIdentity?.profile_name || '匹配依据身份'} 的默认材料。`
    : `匹配分析将使用 ${resolvedMatchSourceIdentity?.profile_name || '当前身份'} 的默认材料。`;
  const preparedBodyText = deriveBodyTextFromDraft({ content, contentHtml });
  const hasDraftBody = hasMeaningfulBody({
    content: preparedBodyText,
    contentHtml,
  });
  const canGenerateDraft =
    Boolean(currentTaskId) &&
    !blocksDirectDraftActions &&
    !isRewriting &&
    hasDraftBody &&
    Boolean(currentTask?.primary_material_id) &&
    hasProfessorResearchDirection(thread?.professor);
  const canSubmitDraft = Boolean(currentTaskId) && !blocksDirectDraftActions;
  const communicationMessageCount = useMemo(
    () => thread?.messages.filter(isCommunicationMessage).length ?? 0,
    [thread?.messages],
  );
  const failedAttemptCount = useMemo(
    () => thread?.messages.filter(isFailedSentMessage).length ?? 0,
    [thread?.messages],
  );
  const hasVisibleMessages = communicationMessageCount + failedAttemptCount > 0;
  const handleRefreshThread = useCallback(() => {
    setNewReceivedCount(0);
    void loadThread({ refreshReplies: true, silent: true });
  }, [loadThread]);
  const hasDraft = Boolean(currentTask?.draft?.sendable || hasDraftBody);
  const buildDraftPayload = useCallback(
    () => ({
      subject: subject.trim() || null,
      body_text: preparedBodyText,
      body_html: contentHtml,
      selected_material_ids: selectedMaterialIds,
    }),
    [contentHtml, preparedBodyText, selectedMaterialIds, subject],
  );
  const buildSavedDraftPayload = useCallback(
    () => ({
      subject: subject.trim(),
      body_text: preparedBodyText,
      body_html: contentHtml,
      selected_material_ids: selectedMaterialIds,
    }),
    [contentHtml, preparedBodyText, selectedMaterialIds, subject],
  );
  const updateComposerDirtyFromSnapshot = useCallback((snapshot: ComposerDraftSnapshot) => {
    composerDirtyRef.current = !areComposerDraftSnapshotsEqual(
      composerBaselineRef.current,
      snapshot,
    );
  }, []);
  const getCurrentComposerSnapshot = useCallback(
    () =>
      buildComposerDraftSnapshot({
        subject,
        content,
        contentHtml,
        selectedMaterialIds,
      }),
    [content, contentHtml, selectedMaterialIds, subject],
  );
  const hasUnsavedComposerChanges = useCallback(
    () =>
      !areComposerDraftSnapshotsEqual(
        composerBaselineRef.current,
        getCurrentComposerSnapshot(),
      ),
    [getCurrentComposerSnapshot],
  );
  const syncComposerDirtyState = useCallback(() => {
    const isDirty = hasUnsavedComposerChanges();
    composerDirtyRef.current = isDirty;
    return isDirty;
  }, [hasUnsavedComposerChanges]);
  const nextStep = currentTask
    ? getWorkspaceNextStep({
        status: currentTask.status ?? 'discovered',
        hasDraft,
        hasPrimaryMaterial: Boolean(currentTask.primary_material_id),
        cancellationReason: currentTask.cancellation_reason,
        canContinueManually: currentTask.can_continue_manually,
        canWriteFollowUp: currentTask.can_write_follow_up,
      })
    : null;
  const nextStepDescription = nextStep ? getWorkspaceNextStepDescription(nextStep.title) : '';

  const runAction = useCallback(
    async (
      action: () => Promise<WorkspaceThreadDTO>,
      fallbackTitle: string,
      fallbackMessage: string,
      onSuccess?: (data: WorkspaceThreadDTO) => void,
      options: { preserveDirtyComposer?: boolean } = {},
    ) => {
      const actionRequestKey = workspaceRequestKey;
      const actionTaskId = currentTaskId;
      const actionRequestId = latestActionRequestIdRef.current + 1;
      latestActionRequestIdRef.current = actionRequestId;
      if (captureNextRewriteActionRequestRef.current) {
        activeRewriteActionRequestIdRef.current = actionRequestId;
        captureNextRewriteActionRequestRef.current = false;
      }
      setActing(true);
      try {
        const data = await action();
        if (
          latestActionRequestIdRef.current !== actionRequestId ||
          currentWorkspaceRequestKeyRef.current !== actionRequestKey
        ) {
          return;
        }
        setThread(data);
        setLoadFailed(false);
        const nextTaskId = data.current_task?.id ?? null;
        const shouldPreserveDirtyComposer =
          options.preserveDirtyComposer === true &&
          actionTaskId != null &&
          nextTaskId === actionTaskId;
        syncComposer(data, { preserveDirty: shouldPreserveDirtyComposer });
        onSuccess?.(data);
      } catch (actionError) {
        if (
          latestActionRequestIdRef.current !== actionRequestId ||
          currentWorkspaceRequestKeyRef.current !== actionRequestKey
        ) {
          return;
        }
        const message = actionError instanceof Error ? actionError.message : fallbackMessage;
        notifyError(fallbackTitle, message);
      } finally {
        if (
          latestActionRequestIdRef.current === actionRequestId &&
          currentWorkspaceRequestKeyRef.current === actionRequestKey
        ) {
          setActing(false);
        }
      }
    },
    [currentTaskId, notifyError, syncComposer, workspaceRequestKey],
  );

  const saveCurrentDraft = useCallback(async () => {
    if (!currentTaskId) {
      throw new Error('当前工作区没有可保存的草稿');
    }

    const draftSaveRequestKey = workspaceRequestKey;
    const draftSaveRequestId = latestDraftSaveRequestIdRef.current + 1;
    latestDraftSaveRequestIdRef.current = draftSaveRequestId;
    setDraftSaving(true);
    try {
      return await saveDraft(currentTaskId, buildSavedDraftPayload());
    } finally {
      if (
        latestDraftSaveRequestIdRef.current === draftSaveRequestId &&
        currentWorkspaceRequestKeyRef.current === draftSaveRequestKey
      ) {
        setDraftSaving(false);
      }
    }
  }, [buildSavedDraftPayload, currentTaskId, workspaceRequestKey]);

  const handleSaveDraft = useCallback(() => {
    void runAction(
      saveCurrentDraft,
      '保存草稿失败',
      '保存草稿失败',
      () => {
        notifySuccess('草稿已保存');
      },
    );
  }, [notifySuccess, runAction, saveCurrentDraft]);

  const handleSubjectChange = useCallback((value: string) => {
    setSubject(value);
    updateComposerDirtyFromSnapshot(
      buildComposerDraftSnapshot({
        subject: value,
        content,
        contentHtml,
        selectedMaterialIds,
      }),
    );
  }, [content, contentHtml, selectedMaterialIds, updateComposerDirtyFromSnapshot]);

  const handleContentChange = useCallback((value: { html: string; text: string }) => {
    setContent(value.text);
    setContentHtml(value.html);
    updateComposerDirtyFromSnapshot(
      buildComposerDraftSnapshot({
        subject,
        content: value.text,
        contentHtml: value.html,
        selectedMaterialIds,
      }),
    );
  }, [selectedMaterialIds, subject, updateComposerDirtyFromSnapshot]);

  const handleSelectedMaterialIdsChange = useCallback((ids: number[]) => {
    setSelectedMaterialIds(ids);
    updateComposerDirtyFromSnapshot(
      buildComposerDraftSnapshot({
        subject,
        content,
        contentHtml,
        selectedMaterialIds: ids,
      }),
    );
  }, [content, contentHtml, subject, updateComposerDirtyFromSnapshot]);

  const handleApplyOutreachTemplate = useCallback(
    (templateId: number) => {
      if (!currentTaskId || !currentTask) {
        return;
      }
      void (async () => {
        const selectedTemplateSummary = outreachTemplates.find(
          (template) => template.id === templateId && !template.archived_at,
        );
        if (!selectedTemplateSummary) {
          notifyError('套用模板失败', '所选模板已不可用，请刷新后重试。');
          return;
        }

        const hasUnsavedChanges = syncComposerDirtyState();
        const replacesExistingDraft =
          hasDraftBody && currentTask.draft.source !== 'template';
        if (hasUnsavedChanges || replacesExistingDraft) {
          const confirmed = await confirm({
            title: '用模板替换当前草稿？',
            description: `将用“${selectedTemplateSummary.name}”的最新内容替换当前主题和正文，现有草稿不会保留。`,
            confirmLabel: '套用并替换',
            cancelLabel: '取消',
            tone: 'danger',
          });
          if (!confirmed) {
            return;
          }
        }

        let appliedTemplate: OutreachTemplateDTO | null = null;

        await runAction(
          async () => {
            const latestTemplate = await getOutreachTemplate(templateId);
            if (latestTemplate.archived_at) {
              throw new Error('所选模板已被删除，不能重新套用。');
            }
            appliedTemplate = latestTemplate;
            setOutreachTemplates((templates) =>
              templates.some((template) => template.id === latestTemplate.id)
                ? templates.map((template) =>
                    template.id === latestTemplate.id ? latestTemplate : template,
                  )
                : [...templates, latestTemplate],
            );
            return updateTaskOutreachConfig(currentTaskId, {
              outreach_generation_mode:
                latestTemplate.recommended_generation_mode,
              outreach_template_id: latestTemplate.id,
              outreach_template_subject: latestTemplate.subject,
              outreach_template_body_text: latestTemplate.body_text,
              outreach_template_body_html: latestTemplate.body_html,
            });
          },
          '套用模板失败',
          '重新套用模板失败',
          () => {
            notifySuccess(`已套用“${appliedTemplate?.name ?? selectedTemplateSummary.name}”`);
          },
        );
      })();
    },
    [
      confirm,
      currentTask,
      currentTaskId,
      hasDraftBody,
      notifyError,
      notifySuccess,
      outreachTemplates,
      runAction,
      syncComposerDirtyState,
    ],
  );

  const handleSendNow = useCallback(() => {
    if (!currentTaskId) {
      return;
    }

    void (async () => {
      const attachmentWarning = shouldPromptForLargeAttachments()
        ? buildLargeAttachmentWarning(selectedAttachmentTotalBytes)
        : null;
      const attachmentOverRecommendedLimit = Boolean(attachmentWarning);
      const confirmed = await confirm({
        title: attachmentOverRecommendedLimit
          ? '附件超过 1 MB，仍要发送吗？'
          : '确认立即发送这封真实邮件？',
        description: [
          `将真实发给 ${thread?.professor.email ?? '当前导师邮箱'}，并附带 ${selectedMaterialIds.length} 份附件，共 ${formatFileSize(selectedAttachmentTotalBytes)}。`,
          attachmentWarning,
        ]
          .filter(Boolean)
          .join('\n'),
        confirmLabel: attachmentOverRecommendedLimit ? '仍然发送' : '确认发送',
        cancelLabel: attachmentOverRecommendedLimit ? '返回调整' : '再检查一下',
        confirmationCheckbox: attachmentWarning
          ? {
              label: LARGE_ATTACHMENT_WARNING_CONFIRMATION_LABEL,
              onConfirmChecked: suppressLargeAttachmentWarnings,
            }
          : undefined,
        tone: 'danger',
      });
      if (!confirmed) {
        return;
      }

      await runAction(
        () =>
          approveAndSend(currentTaskId, buildDraftPayload()),
        '发送失败',
        '发送失败',
        (data) => {
          const failureMessage = getEmailSendFailureMessage(
            data.current_task.status,
            data.current_task.last_error,
          );
          if (failureMessage) {
            notifyError('发送失败', failureMessage);
            return;
          }
          setComposerExpanded(false);
          notifySuccess(`已发送给 ${data.professor.email}`);
        },
      );
    })();
  }, [
    confirm,
    currentTaskId,
    buildDraftPayload,
    notifyError,
    notifySuccess,
    runAction,
    selectedAttachmentTotalBytes,
    selectedMaterialIds,
    thread?.professor.email,
  ]);

  const handleScheduleSend = useCallback(() => {
    if (!currentTaskId) {
      return;
    }

    setPendingScheduledAt(scheduledAt || getDefaultScheduledAtValue());
    setScheduleDialogOpen(true);
  }, [currentTaskId, scheduledAt]);

  const handleConfirmScheduleSend = useCallback(() => {
    if (!currentTaskId) {
      return;
    }

    // time-check: local-control-value, pendingScheduledAt comes from a datetime-local control.
    const scheduleDate = new Date(pendingScheduledAt);
    if (Number.isNaN(scheduleDate.getTime())) {
      notifyFormErrors('请检查表单', ['请先选一个有效的发送时间']);
      return;
    }

    void runAction(
      () =>
        approveAndSchedule(currentTaskId, {
          ...buildDraftPayload(),
          scheduled_at: scheduleDate.toISOString(),
        }),
      '定时发送失败',
      '定时发送失败',
      () => {
        setScheduledAt(pendingScheduledAt);
        setScheduleDialogOpen(false);
        setComposerExpanded(false);
        notifySuccess(
          '已加入发送计划',
          '将在设定时间发送，可在任务中心修改。',
        );
      },
    );
  }, [
    buildDraftPayload,
    currentTaskId,
    notifyFormErrors,
    notifySuccess,
    pendingScheduledAt,
    runAction,
  ]);

  const handleCancelSchedule = useCallback(() => {
    if (!currentTaskId) {
      return;
    }

    void runAction(
      () => cancelScheduledTask(currentTaskId),
      '取消定时失败',
      '取消定时失败',
      undefined,
      { preserveDirtyComposer: true },
    );
  }, [currentTaskId, runAction]);

  const handleContinueManually = useCallback(() => {
    if (!currentTaskId) {
      return;
    }

    void runAction(
      () => continueManually(currentTaskId),
      '继续联系失败',
      '继续联系失败',
      () => setComposerExpanded(true),
      { preserveDirtyComposer: true },
    );
  }, [currentTaskId, runAction]);

  const handleStartFollowUp = useCallback(() => {
    if (!currentTaskId) {
      return;
    }

    void runAction(
      () => startFollowUp(currentTaskId),
      '创建跟进邮件失败',
      '创建跟进邮件失败',
      () => setComposerExpanded(true),
      { preserveDirtyComposer: true },
    );
  }, [currentTaskId, runAction]);

  const handleCalculateMatch = useCallback(() => {
    if (!currentTaskId) {
      return;
    }

    void runAction(
      async () => (await calculateMatch(currentTaskId, selectedLlmProfileId)).thread,
      '计算匹配失败',
      '计算匹配失败',
      undefined,
      { preserveDirtyComposer: true },
    );
  }, [currentTaskId, runAction, selectedLlmProfileId]);

  const handleGenerateDraft = useCallback(() => {
    if (!currentTaskId || !currentTask) {
      return;
    }

    const startedAt = Date.now();
    draftRewritingRef.current = true;
    activeRewriteTaskIdRef.current = currentTaskId;
    activeRewriteObservedGeneratingRef.current = currentTask.status === 'generating_draft';
    activeRewritePreviousDraftRef.current = buildTaskDraftSnapshot(currentTask);
    setDraftRewriting(true);
    captureNextRewriteActionRequestRef.current = true;
    void runAction(
      () =>
        rewriteDraft(currentTaskId, {
          subject: subject.trim() || null,
          body_text: preparedBodyText,
          body_html: contentHtml,
          selected_material_ids: selectedMaterialIds,
          llm_profile_id: selectedLlmProfileId ?? null,
        }),
      'AI 改写失败',
      'AI 改写失败',
      (data) => {
        setComposerExpanded(true);
        notifySuccess(
          'AI 改写已完成',
          buildDraftGenerationSuccessDescription(data.current_task, Date.now() - startedAt),
        );
      },
    ).finally(() => {
      if (mountedRef.current) {
        resetActiveRewriteTracking();
      }
    });
  }, [
    contentHtml,
    currentTask,
    currentTaskId,
    notifySuccess,
    preparedBodyText,
    resetActiveRewriteTracking,
    runAction,
    selectedLlmProfileId,
    selectedMaterialIds,
    subject,
  ]);

  const confirmDirtyDraftExit = useCallback(async () => {
    const action = await choose({
      title: '保存草稿修改？',
      description: '离开后，未保存的修改将丢失。',
      confirmLabel: '保存并离开',
      secondaryLabel: '不保存离开',
      cancelLabel: '继续编辑',
    });

    if (action === 'cancel') {
      return false;
    }

    if (action === 'secondary') {
      composerDirtyRef.current = false;
      return true;
    }

    await saveCurrentDraft();
    composerDirtyRef.current = false;
    notifySuccess('草稿已保存');
    return true;
  }, [choose, notifySuccess, saveCurrentDraft]);

  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    const shouldBlock =
      Boolean(currentTaskId) &&
      !isRewriting &&
      currentLocation.pathname !== nextLocation.pathname &&
      hasUnsavedComposerChanges();
    if (!shouldBlock) {
      return false;
    }
    if (approvedNavigationPathRef.current === nextLocation.pathname) {
      approvedNavigationPathRef.current = null;
      return false;
    }
    return true;
  });

  useBeforeUnload(
    useCallback(
      (event) => {
        if (!currentTaskId || isRewriting || !hasUnsavedComposerChanges()) {
          return;
        }
        event.preventDefault();
        event.returnValue = '';
      },
      [currentTaskId, hasUnsavedComposerChanges, isRewriting],
    ),
  );

  useEffect(() => {
    if (blocker.state !== 'blocked') {
      handledBlockerLocationKeyRef.current = null;
      return;
    }

    const blockerLocationKey =
      blocker.location?.key ?? `${blocker.location?.pathname ?? ''}${blocker.location?.search ?? ''}${blocker.location?.hash ?? ''}`;
    if (handledBlockerLocationKeyRef.current === blockerLocationKey) {
      return;
    }
    handledBlockerLocationKeyRef.current = blockerLocationKey;

    if (savingBeforeNavigate) {
      return;
    }

    if (acting || draftSaving) {
      notifyError('操作正在进行', '操作未完成，请稍后离开工作区。');
      blocker.reset();
      return;
    }

    void (async () => {
      setSavingBeforeNavigate(true);
      try {
        const canLeave = await confirmDirtyDraftExit();
        if (canLeave) {
          blocker.proceed();
        } else {
          blocker.reset();
        }
      } catch (saveError) {
        const message = saveError instanceof Error ? saveError.message : '保存草稿失败';
        notifyError('保存草稿失败', message);
        blocker.reset();
      } finally {
        setSavingBeforeNavigate(false);
      }
    })();
  }, [acting, blocker, confirmDirtyDraftExit, draftSaving, notifyError, savingBeforeNavigate]);

  useEffect(() => {
    return registerWorkspaceDraftGuard(async (request) => {
      if (!currentTaskId || !syncComposerDirtyState()) {
        return true;
      }
      if (isRewriting) {
        return true;
      }
      if (acting || draftSaving) {
        notifyError('草稿正在保存', '草稿未保存，请稍后切换身份或模型。');
        return false;
      }

      try {
        const canLeave = await confirmDirtyDraftExit();
        if (canLeave && request?.nextPath) {
          approvedNavigationPathRef.current = request.nextPath;
        }
        return canLeave;
      } catch (saveError) {
        const message = saveError instanceof Error ? saveError.message : '保存草稿失败';
        notifyError('保存草稿失败', message);
        return false;
      }
    });
  }, [acting, confirmDirtyDraftExit, currentTaskId, draftSaving, isRewriting, notifyError, registerWorkspaceDraftGuard, syncComposerDirtyState]);

  if (!Number.isFinite(professorId)) {
    return <Navigate to="/404" replace />;
  }

  if (!selectedIdentityId || !selectedLlmProfileId) {
    return (
      <>
        <main className="mx-auto max-w-4xl px-6 py-10">
          <div className="rounded-3xl border border-dashed border-stone-300 bg-[#fcfbf8] p-10 text-center">
            <h1 className="text-2xl font-semibold text-stone-900">请先在顶部选择身份和模型</h1>
          </div>
        </main>
        {confirmDialog}
      </>
    );
  }

  if (loading) {
    return (
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="flex items-center justify-center gap-2 rounded-[32px] border border-stone-200 bg-white px-6 py-16 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在打开工作区…
        </div>
      </main>
    );
  }

  if (!thread) {
    return (
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="rounded-[32px] border border-dashed border-stone-300 bg-white px-6 py-16 text-center text-sm text-stone-500 shadow-sm">
          {loadFailed ? '暂时无法打开工作区，请返回后重试。' : '未找到工作区数据'}
        </div>
      </main>
    );
  }

  const professorSummary =
    [thread.professor.university, thread.professor.school].filter(Boolean).join(' / ') ||
    '学校信息待补充';
  const communicationScope = thread.communication_scope ?? [thread.identity];
  const syncWarnings = thread.sync_warnings ?? [];

  return (
    <>
      <main
        data-workspace-page
        className="min-h-full bg-[linear-gradient(180deg,rgba(255,250,243,0.92),rgba(255,255,255,0.98))]"
      >
        <div
          data-workspace-container
          className="mx-auto flex min-h-full max-w-7xl flex-col px-4 py-4 sm:px-6 sm:py-5"
        >
        <header className="mb-4 shrink-0 rounded-[34px] border border-stone-200/80 bg-[radial-gradient(circle_at_top_right,rgba(153,27,27,0.08),transparent_28%),linear-gradient(180deg,rgba(255,248,240,0.98),rgba(255,255,255,0.98))] px-5 py-5 shadow-[0_20px_50px_-34px_rgba(41,37,36,0.28)] sm:px-6">
          <Link
            to="/"
            className="inline-flex items-center gap-2 text-sm font-medium text-stone-500 transition hover:text-primary"
          >
            <ArrowLeft className="h-4 w-4" />
            返回首页
          </Link>

          <div className="mt-4 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-3xl font-semibold tracking-[0.01em] text-stone-950">
                  {thread.professor.name}
                </h1>
                <span className="rounded-full border border-primary/15 bg-primary px-3 py-1 text-xs font-semibold text-white shadow-sm shadow-primary/20">
                  {statusLabel}
                </span>
              </div>
              <p className="mt-2 text-sm text-stone-500">
                {professorSummary}
                {thread.professor.title ? ` · ${thread.professor.title}` : ''}
              </p>
            </div>

            <div className="flex flex-wrap gap-2">
              <span className="rounded-full border border-stone-200 bg-white/90 px-3 py-1 text-xs font-medium text-stone-600">
                通信 {communicationMessageCount} 条
              </span>
              {failedAttemptCount > 0 ? (
                <span className="rounded-full border border-red-200 bg-red-50 px-3 py-1 text-xs font-medium text-red-700">
                  {failedAttemptCount} 次失败尝试
                </span>
              ) : null}
              {communicationScope.length > 1 ? (
                <span className="rounded-lg border border-teal-200 bg-teal-50 px-3 py-1 text-xs font-medium text-teal-800">
                  共享通信 · {communicationScope.length} 个身份
                </span>
              ) : null}
            </div>
          </div>
        </header>

        <div
          data-workspace-layout={composerExpanded ? 'compose' : 'overview'}
          className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_17.5rem] xl:grid-cols-[minmax(0,1fr)_19rem] 2xl:grid-cols-[minmax(0,1fr)_20rem]"
        >
          <div data-workspace-sidebar className="order-1 lg:order-2">
            <WorkspaceSidebar thread={thread} />
          </div>

          <section
            className={clsx(
              'order-2 flex flex-col overflow-hidden rounded-[36px] border border-stone-200 bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(255,252,247,0.98))] shadow-[0_24px_54px_-36px_rgba(41,37,36,0.34)] lg:order-1',
              !hasVisibleMessages && 'lg:self-start',
            )}
          >
            {currentTask ? (
              <>
                <WorkspaceMessageThread
                  messages={thread.messages}
                  communicationScope={communicationScope}
                  syncWarnings={syncWarnings}
                  monitoringLabel="正在监听回复"
                  lastCheckedAt={lastThreadCheckedAt}
                  refreshing={threadRefreshing}
                  newReceivedCount={newReceivedCount}
                  onRefresh={handleRefreshThread}
                />

                <WorkspaceComposerDock
                  thread={thread}
                  currentTask={currentTask}
                  draftReady={hasDraft}
                  nextStepTitle={nextStep?.title ?? '继续整理沟通动作'}
                  nextStepDescription={nextStepDescription}
                  subject={subject}
                  content={content}
                  contentHtml={contentHtml || textToEmailHtml(content)}
                  selectedMaterialIds={selectedMaterialIds}
                  outreachTemplates={outreachTemplates}
                  selectedOutreachTemplateId={currentTask.outreach_template_id ?? null}
                  loadingOutreachTemplates={loadingOutreachTemplates}
                  scheduledAt={scheduledAt}
                  acting={acting}
                  isRewriting={isRewriting}
                  hasDraftBody={hasDraftBody}
                  canCalculateMatch={canCalculateMatch}
                  matchAnalysisDescription={matchAnalysisDescription}
                  canGenerateDraft={canGenerateDraft}
                  canContinueManually={Boolean(currentTask.can_continue_manually)}
                  canStartFollowUp={Boolean(currentTask.can_write_follow_up)}
                  canSubmitDraft={canSubmitDraft}
                  draftSaving={draftSaving}
                  composerExpanded={composerExpanded}
                  onToggleExpanded={() =>
                    setComposerExpanded((current) => !current)
                  }
                  onSubjectChange={handleSubjectChange}
                  onContentChange={handleContentChange}
                  onSelectedMaterialIdsChange={handleSelectedMaterialIdsChange}
                  onApplyOutreachTemplate={handleApplyOutreachTemplate}
                  onSaveDraft={handleSaveDraft}
                  onSendNow={handleSendNow}
                  onScheduleSend={handleScheduleSend}
                  onCancelSchedule={handleCancelSchedule}
                  onViewSchedule={() =>
                    navigate(`/tasks?section=delivery&view=upcoming&task_id=${currentTask.id}`)
                  }
                  onContinueManually={handleContinueManually}
                  onStartFollowUp={handleStartFollowUp}
                  onCalculateMatch={handleCalculateMatch}
                  onGenerateDraft={handleGenerateDraft}
                />
              </>
            ) : (
              <>
                <WorkspaceMessageThread
                  messages={thread.messages}
                  communicationScope={communicationScope}
                  syncWarnings={syncWarnings}
                  monitoringLabel="正在监听回复"
                  lastCheckedAt={lastThreadCheckedAt}
                  refreshing={threadRefreshing}
                  newReceivedCount={newReceivedCount}
                  onRefresh={handleRefreshThread}
                />
                <div className="border-t border-stone-200 bg-stone-50/70 px-5 py-5 text-center">
                  <div className="text-sm font-semibold text-stone-900">
                    当前身份暂无任务
                  </div>
                  <p className="mt-1 text-xs leading-5 text-stone-500">
                    创建任务后即可写信；通信记录仍可查看。
                  </p>
                </div>
              </>
            )}
          </section>
        </div>
        </div>
      </main>
      <ScheduleSendDialog
        open={scheduleDialogOpen}
        professorEmail={thread?.professor.email}
        selectedMaterialCount={selectedMaterialIds.length}
        selectedAttachmentTotalBytes={selectedAttachmentTotalBytes}
        value={pendingScheduledAt}
        acting={acting}
        onChange={setPendingScheduledAt}
        onCancel={() => {
          if (!acting) {
            setScheduleDialogOpen(false);
          }
        }}
        onConfirm={handleConfirmScheduleSend}
      />
      {confirmDialog}
    </>
  );
};
