import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import clsx from 'clsx';
import { Link, Navigate, useBeforeUnload, useBlocker, useParams } from 'react-router-dom';
import { ArrowLeft, CalendarClock, Loader2, X } from 'lucide-react';
import { WorkspaceComposerDock } from '@/components/organisms/WorkspaceComposerDock';
import { WorkspaceMessageThread } from '@/components/organisms/WorkspaceMessageThread';
import { WorkspaceSidebar } from '@/components/organisms/WorkspaceSidebar';
import { useNotification } from '@/context/NotificationContext';
import { useSelectionContext } from '@/context/SelectionContext';
import { useWorkspaceDraftGuard } from '@/context/useWorkspaceDraftGuard';
import { getWorkspaceNextStep } from '@/features/workspace/client/getWorkspaceNextStep';
import { bootstrapWorkspaceThread } from '@/features/workspace/client/openWorkspaceThread';
import {
  approveAndSchedule,
  approveAndSend,
  calculateMatch,
  cancelScheduledTask,
  continueManually,
  rewriteDraft,
  saveDraft,
  startFollowUp,
} from '@/lib/api/emailTasksApi';
import {
  getWorkspaceThread,
  refreshWorkspaceReplies,
} from '@/lib/api/workspacesApi';
import { parseApiDateTime } from '@/lib/dateTime';
import { extractPlainTextFromHtml } from '@/lib/htmlPreview';
import { textToEmailHtml } from '@/lib/richEmail';
import { useConfirmDialog } from '@/lib/useConfirmDialog';
import { useDismissableLayerClick } from '@/lib/useDismissableLayerClick';
import {
  PROFESSOR_STATUS_LABELS,
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
  sending: PROFESSOR_STATUS_LABELS.sending,
  sent: PROFESSOR_STATUS_LABELS.sent,
  send_failed: PROFESSOR_STATUS_LABELS.send_failed,
  reply_detected: PROFESSOR_STATUS_LABELS.reply_detected,
  canceled: '已取消',
};

const WORKSPACE_THREAD_REFRESH_INTERVAL_MS = 60_000;

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
  )} / 总计 ${formatTokenValue(task?.last_draft_total_tokens)} token，耗时 ${formatElapsedSeconds(
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
  if (messages.some((message) => message.direction === 'sent')) {
    return PROFESSOR_STATUS_LABELS.sent;
  }
  if (!currentTask?.status) {
    return '尚未创建任务';
  }
  return WORKSPACE_STATUS_LABELS[currentTask.status] ?? currentTask.status;
};

const getWorkspaceNextStepDescription = (title: string) => {
  switch (title) {
    case '作为单独联系继续':
      return '从这条批量任务记录中拆出一条单独联系继续推进。';
    case '写跟进邮件':
      return '基于当前沟通记录起草下一封跟进邮件。';
    case '查看失败原因并重试':
      return '检查失败原因，修正后重试。';
    case '选择分析材料':
      return '选择材料后可分析匹配度。';
    case '生成邮件草稿':
      return '用 AI 改写当前草稿后再人工检查。';
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

const ScheduleSendDialog = ({
  open,
  professorEmail,
  selectedMaterialCount,
  value,
  acting,
  onChange,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  professorEmail: string | null | undefined;
  selectedMaterialCount: number;
  value: string;
  acting: boolean;
  onChange: (value: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) => {
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
      className="fixed inset-0 z-[90] flex items-center justify-center bg-stone-950/35 p-4 backdrop-blur-md"
      onClick={onBackdropClick}
      onMouseDown={onBackdropMouseDown}
    >
      <div
        className="relative w-full max-w-md overflow-hidden rounded-[30px] border border-stone-200/80 bg-[linear-gradient(180deg,rgba(255,252,246,0.98),rgba(255,245,233,0.95))] shadow-[0_34px_90px_-32px_rgba(41,37,36,0.5)]"
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
                  将真实发给 {professorEmail ?? '当前导师邮箱'}，并附带 {selectedMaterialCount} 份附件。
                </p>
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
  const professorId = Number(id);
  const { notifyError, notifyFormErrors, notifySuccess } = useNotification();
  const { confirm, choose, dialog: confirmDialog } = useConfirmDialog();
  const { selectedIdentityId, selectedLlmProfileId } = useSelectionContext();
  const { registerWorkspaceDraftGuard } = useWorkspaceDraftGuard();
  const [thread, setThread] = useState<WorkspaceThreadDTO | null>(null);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [subject, setSubject] = useState('');
  const [content, setContent] = useState('');
  const [contentHtml, setContentHtml] = useState<string | null>(null);
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<number[]>([]);
  const [scheduledAt, setScheduledAt] = useState(getDefaultScheduledAtValue);
  const [scheduleDialogOpen, setScheduleDialogOpen] = useState(false);
  const [pendingScheduledAt, setPendingScheduledAt] = useState(getDefaultScheduledAtValue);
  const [composerExpanded, setComposerExpanded] = useState(false);
  const [threadRefreshing, setThreadRefreshing] = useState(false);
  const [lastThreadCheckedAt, setLastThreadCheckedAt] = useState<Date | null>(null);
  const [newReceivedCount, setNewReceivedCount] = useState(0);
  const [composerDirty, setComposerDirty] = useState(false);
  const [draftSaving, setDraftSaving] = useState(false);
  const [draftRewriting, setDraftRewriting] = useState(false);
  const [savingBeforeNavigate, setSavingBeforeNavigate] = useState(false);
  const mountedRef = useRef(true);
  const handledBlockerLocationKeyRef = useRef<string | null>(null);
  const loadedThreadKeyRef = useRef<string | null>(null);
  const activeThreadRequestKeyRef = useRef<string | null>(null);
  const latestThreadRequestIdRef = useRef(0);
  const currentWorkspaceRequestKeyRef = useRef<string | null>(null);
  const latestActionRequestIdRef = useRef(0);
  const latestDraftSaveRequestIdRef = useRef(0);
  const knownReceivedMessageIdsRef = useRef<Set<number>>(new Set());
  const composerDirtyRef = useRef(false);
  const workspaceRequestKey =
    Number.isFinite(professorId) && selectedIdentityId && selectedLlmProfileId
      ? `${professorId}:${selectedIdentityId}:${selectedLlmProfileId}`
      : null;

  useEffect(() => {
    return () => {
      mountedRef.current = false;
    };
  }, []);

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
    setComposerDirty(false);
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
      composerDirtyRef.current = false;
      setComposerDirty(false);
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
      const data = await (options.refreshReplies ? refreshWorkspaceReplies : getWorkspaceThread)(
        professorId,
        selectedIdentityId,
        selectedLlmProfileId,
      );
      const workspaceData = await bootstrapWorkspaceThread(
        data,
        professorId,
        selectedIdentityId,
        selectedLlmProfileId,
      );
      if (
        latestThreadRequestIdRef.current !== requestId ||
        activeThreadRequestKeyRef.current !== workspaceRequestKey
      ) {
        return;
      }
      const hadLoadedThread = loadedThreadKeyRef.current === workspaceRequestKey;
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
          '收到老师回复',
          buildNewReplyNotificationDescription(workspaceData.professor.name, latestReceived),
        );
      }
      syncComposer(workspaceData, { preserveDirty: silent });
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
  }, [notifyError, notifySuccess, professorId, selectedIdentityId, selectedLlmProfileId, syncComposer, workspaceRequestKey]);

  useEffect(() => {
    void loadThread();
  }, [loadThread]);

  useEffect(() => {
    if (!workspaceRequestKey) {
      return undefined;
    }

    const intervalId = window.setInterval(() => {
      void loadThread({ silent: true });
    }, WORKSPACE_THREAD_REFRESH_INTERVAL_MS);

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
  }, [loadThread, workspaceRequestKey]);

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
    setDraftRewriting(false);
    composerDirtyRef.current = false;
    setComposerDirty(false);
  }, [workspaceRequestKey]);

  useEffect(() => {
    setComposerExpanded(false);
  }, [professorId, selectedIdentityId, selectedLlmProfileId]);

  const currentTask = getCurrentTaskOrNull(thread);
  const currentTaskId = currentTask?.id ?? null;
  const statusLabel = getStatusLabel(currentTask, thread?.messages ?? []);
  const blocksDirectDraftActions = shouldBlockDirectDraftActions(currentTask);
  const taskGeneratingDraft = currentTask?.status === 'generating_draft';
  const isRewriting = taskGeneratingDraft || draftRewriting;
  const canCalculateMatch =
    Boolean(currentTaskId) &&
    Boolean(currentTask?.primary_material_id) &&
    hasProfessorMatchEvidence(thread?.professor) &&
    !blocksDirectDraftActions;
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
  const realMessageCount = useMemo(
    () => thread?.messages.filter((message) => message.direction !== 'draft').length ?? 0,
    [thread?.messages],
  );
  const hasRealMessages = realMessageCount > 0;
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
        notifySuccess('草稿已保存', '工作区草稿已更新。');
      },
    );
  }, [notifySuccess, runAction, saveCurrentDraft]);

  const handleSubjectChange = useCallback((value: string) => {
    composerDirtyRef.current = true;
    setComposerDirty(true);
    setSubject(value);
  }, []);

  const handleContentChange = useCallback((value: { html: string; text: string }) => {
    composerDirtyRef.current = true;
    setComposerDirty(true);
    setContent(value.text);
    setContentHtml(value.html);
  }, []);

  const handleSelectedMaterialIdsChange = useCallback((ids: number[]) => {
    composerDirtyRef.current = true;
    setComposerDirty(true);
    setSelectedMaterialIds(ids);
  }, []);

  const handleSendNow = useCallback(() => {
    if (!currentTaskId) {
      return;
    }

    void (async () => {
      const confirmed = await confirm({
        title: '确认立即发送这封真实邮件？',
        description: `将真实发给 ${thread?.professor.email ?? '当前导师邮箱'}，并附带 ${selectedMaterialIds.length} 份附件。`,
        confirmLabel: '确认发送',
        cancelLabel: '再检查一下',
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
        () => setComposerExpanded(false),
      );
    })();
  }, [
    confirm,
    currentTaskId,
    buildDraftPayload,
    runAction,
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
      },
    );
  }, [
    buildDraftPayload,
    currentTaskId,
    notifyFormErrors,
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
    if (!currentTaskId) {
      return;
    }

    const startedAt = Date.now();
    setDraftRewriting(true);
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
        setDraftRewriting(false);
      }
    });
  }, [
    contentHtml,
    currentTaskId,
    notifySuccess,
    preparedBodyText,
    runAction,
    selectedLlmProfileId,
    selectedMaterialIds,
    subject,
  ]);

  const confirmDirtyDraftExit = useCallback(async () => {
    const action = await choose({
      title: '保存草稿修改？',
      description: '你编辑了工作区草稿。离开前可以保存修改，或不保存直接离开。',
      confirmLabel: '保存并离开',
      secondaryLabel: '不保存离开',
      cancelLabel: '继续编辑',
    });

    if (action === 'cancel') {
      return false;
    }

    if (action === 'secondary') {
      composerDirtyRef.current = false;
      setComposerDirty(false);
      return true;
    }

    await saveCurrentDraft();
    composerDirtyRef.current = false;
    setComposerDirty(false);
    notifySuccess('草稿已保存', '工作区草稿已更新。');
    return true;
  }, [choose, notifySuccess, saveCurrentDraft]);

  const hasDirtyDraft = Boolean(composerDirty && currentTaskId);
  const shouldBlockNavigation = hasDirtyDraft && !isRewriting;
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    shouldBlockNavigation && currentLocation.pathname !== nextLocation.pathname,
  );

  useBeforeUnload(
    useCallback(
      (event) => {
        if (!shouldBlockNavigation) {
          return;
        }
        event.preventDefault();
        event.returnValue = '';
      },
      [shouldBlockNavigation],
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
      notifyError('操作正在进行', '请等待当前操作完成后再离开工作区。');
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
    return registerWorkspaceDraftGuard(async () => {
      if (!composerDirtyRef.current || !currentTaskId) {
        return true;
      }
      if (isRewriting) {
        return true;
      }
      if (acting || draftSaving) {
        notifyError('草稿正在保存', '请等待当前操作完成后再切换身份或模型。');
        return false;
      }

      try {
        return await confirmDirtyDraftExit();
      } catch (saveError) {
        const message = saveError instanceof Error ? saveError.message : '保存草稿失败';
        notifyError('保存草稿失败', message);
        return false;
      }
    });
  }, [acting, confirmDirtyDraftExit, currentTaskId, draftSaving, isRewriting, notifyError, registerWorkspaceDraftGuard]);

  if (!Number.isFinite(professorId)) {
    return <Navigate to="/404" replace />;
  }

  if (!selectedIdentityId || !selectedLlmProfileId) {
    return (
      <>
        <main className="mx-auto max-w-4xl px-6 py-10">
          <div className="rounded-3xl border border-dashed border-stone-300 bg-[#fcfbf8] p-10 text-center">
            <h1 className="text-2xl font-semibold text-stone-900">选择身份和模型</h1>
            <p className="mt-3 text-sm text-stone-600">工作区使用顶部选择的身份和模型。</p>
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
          正在打开老师档案...
        </div>
      </main>
    );
  }

  if (!thread) {
    return (
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="rounded-[32px] border border-dashed border-stone-300 bg-white px-6 py-16 text-center text-sm text-stone-500 shadow-sm">
          {loadFailed ? '工作区数据暂时不可用，请返回上一页后重试。' : '未找到工作区数据'}
        </div>
      </main>
    );
  }

  const professorSummary =
    [thread.professor.university, thread.professor.school].filter(Boolean).join(' / ') ||
    '学校信息待补充';

  return (
    <>
      <main className="h-full min-h-0 overflow-hidden bg-[linear-gradient(180deg,rgba(255,250,243,0.92),rgba(255,255,255,0.98))]">
        <div className="mx-auto flex h-full min-h-0 max-w-7xl flex-col px-4 py-4 sm:px-6 sm:py-5">
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
                通信 {realMessageCount} 条
              </span>
            </div>
          </div>
        </header>

        <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="order-1 lg:order-2">
            <WorkspaceSidebar thread={thread} />
          </div>

          <section
            className={clsx(
              'order-2 flex min-h-0 flex-col overflow-hidden rounded-[36px] border border-stone-200 bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(255,252,247,0.98))] shadow-[0_24px_54px_-36px_rgba(41,37,36,0.34)] lg:order-1',
              !hasRealMessages && 'lg:self-start',
            )}
          >
            {currentTask ? (
              <>
                <WorkspaceMessageThread
                  messages={thread.messages}
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
                  scheduledAt={scheduledAt}
                  acting={acting}
                  isRewriting={isRewriting}
                  hasDraftBody={hasDraftBody}
                  canCalculateMatch={canCalculateMatch}
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
                  onSaveDraft={handleSaveDraft}
                  onSendNow={handleSendNow}
                  onScheduleSend={handleScheduleSend}
                  onCancelSchedule={handleCancelSchedule}
                  onContinueManually={handleContinueManually}
                  onStartFollowUp={handleStartFollowUp}
                  onCalculateMatch={handleCalculateMatch}
                  onGenerateDraft={handleGenerateDraft}
                />
              </>
            ) : (
              <div className="flex flex-1 items-center justify-center px-4 py-10 sm:px-6">
                <div className="w-full max-w-2xl rounded-[30px] border border-dashed border-stone-300 bg-[linear-gradient(180deg,rgba(255,251,245,0.98),rgba(252,251,248,0.98))] px-6 py-12 text-center shadow-sm">
                  <div className="text-lg font-semibold text-stone-950">
                    这位老师还没有任务
                  </div>
                  <p className="mt-3 text-sm leading-7 text-stone-600">
                    从首页或任务中心进入后，会自动创建通信记录。
                  </p>
                </div>
              </div>
            )}
          </section>
        </div>
        </div>
      </main>
      <ScheduleSendDialog
        open={scheduleDialogOpen}
        professorEmail={thread?.professor.email}
        selectedMaterialCount={selectedMaterialIds.length}
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
