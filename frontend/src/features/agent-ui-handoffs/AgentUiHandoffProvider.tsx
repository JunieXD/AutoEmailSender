import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PropsWithChildren,
} from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import type {
  DesktopAgentUiHandoff,
  DesktopAgentUiHandoffAcknowledgeRequest,
  DesktopAgentUiHandoffSurface,
} from '@/types/desktop';
import { useNotification } from '@/context/NotificationContext';
import { useSelectionContext } from '@/context/SelectionContext';
import { useWorkspaceDraftGuard } from '@/context/useWorkspaceDraftGuard';
import { AgentUiHandoffContext } from './context';
import {
  readStoredAgentUiHandoffs,
  writeStoredAgentUiHandoffs,
  type StoredAgentUiHandoff,
} from './storage';
import {
  getAgentUiHandoffIdentityId,
  validateAgentUiHandoff,
  type AgentUiHandoffApplyResult,
  type AgentUiHandoffSurfaceHandler,
  type ValidatedAgentUiHandoff,
} from './types';

const ACK_RETRY_MAX_DELAY_MS = 15_000;
const MAX_ACKNOWLEDGEMENT_RESULT_BYTES = 16_384;
const INVALID_HANDOFF_FAILURE = '软件无法安全解析该界面交接，请更新软件后重试。';
const WORKSPACE_CONTEXT_SURFACES = new Set<DesktopAgentUiHandoffSurface>([
  'communications.thread',
  'draft.workspace',
]);

const normalizeAcknowledgementResult = (
  value: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined => {
  if (value === undefined) {
    return undefined;
  }
  try {
    const serialized = JSON.stringify(value);
    if (
      typeof serialized !== 'string' ||
      new TextEncoder().encode(serialized).byteLength >
      MAX_ACKNOWLEDGEMENT_RESULT_BYTES
    ) {
      return undefined;
    }
    const parsed = JSON.parse(serialized) as unknown;
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : undefined;
  } catch {
    return undefined;
  }
};

const getSuccessNotification = (handoff: ValidatedAgentUiHandoff) => {
  if (
    handoff.surface === 'professors.management' ||
    handoff.surface === 'professors.home'
  ) {
    return {
      title: `Agent 已选择 ${handoff.selectionCount} 位导师`,
      description:
        handoff.payload.display === 'selected_only'
          ? '页面正在仅显示这次选择，可继续检查或手动调整。'
          : '已在当前页面勾选，可继续检查或手动调整。',
    };
  }
  return {
    title: '已打开 Agent 指定的内容',
    description: '这次界面交接只负责定位，没有执行后续业务操作。',
  };
};

const normalizeApplyResult = (
  value: AgentUiHandoffApplyResult,
): AgentUiHandoffApplyResult => {
  if (
    value.status !== 'applied' &&
    value.status !== 'awaiting_user' &&
    value.status !== 'failed'
  ) {
    return {
      status: 'failed',
      failureMessage: '页面适配器返回了无法识别的结果。',
    };
  }
  if (value.status === 'failed' && !value.failureMessage.trim()) {
    return {
      status: 'failed',
      failureMessage: '页面适配器未能应用界面交接。',
      result: value.result,
    };
  }
  return value;
};

export const AgentUiHandoffProvider = ({ children }: PropsWithChildren) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { notifyError, notifySuccess, notifyWarning } = useNotification();
  const {
    identities,
    loading: selectionLoading,
    selectedIdentityId,
    setSelectedIdentityId,
  } = useSelectionContext();
  const { requestWorkspaceDraftGuard } = useWorkspaceDraftGuard();
  const [records, setRecords] = useState<StoredAgentUiHandoff[]>(() =>
    readStoredAgentUiHandoffs(),
  );
  const recordsRef = useRef(records);
  const handlersRef = useRef(
    new Map<DesktopAgentUiHandoffSurface, AgentUiHandoffSurfaceHandler>(),
  );
  const [handlerRevision, setHandlerRevision] = useState(0);
  const processingRef = useRef(false);
  const acknowledgementInFlightRef = useRef(false);
  const preflightApprovedRef = useRef(new Set<string>());
  const acknowledgementAttemptRef = useRef(new Map<string, number>());
  const [acknowledgementRetryAt, setAcknowledgementRetryAt] = useState(0);
  const storageWarningShownRef = useRef(false);

  const replaceRecords = useCallback(
    (nextRecords: StoredAgentUiHandoff[]) => {
      recordsRef.current = nextRecords;
      setRecords(nextRecords);
      try {
        writeStoredAgentUiHandoffs(nextRecords);
      } catch (error) {
        if (!storageWarningShownRef.current) {
          storageWarningShownRef.current = true;
          notifyWarning(
            '界面交接暂时无法持久化',
            error instanceof Error
              ? error.message
              : '刷新页面前请先完成这次界面交接。',
          );
        }
      }
    },
    [notifyWarning],
  );

  const updateRecords = useCallback(
    (
      updater: (
        current: StoredAgentUiHandoff[],
      ) => StoredAgentUiHandoff[],
    ) => {
      replaceRecords(updater(recordsRef.current));
    },
    [replaceRecords],
  );

  const removeHandoffIfExpired = useCallback(
    (handoff: ValidatedAgentUiHandoff): boolean => {
      const expiresAt = Date.parse(handoff.expiresAt);
      if (!Number.isFinite(expiresAt) || expiresAt > Date.now()) {
        return false;
      }
      const exists = recordsRef.current.some(
        (record) => record.handoff.handoffId === handoff.handoffId,
      );
      if (exists) {
        preflightApprovedRef.current.delete(handoff.handoffId);
        acknowledgementAttemptRef.current.delete(handoff.handoffId);
        updateRecords((current) =>
          current.filter(
            (record) => record.handoff.handoffId !== handoff.handoffId,
          ),
        );
        setAcknowledgementRetryAt(0);
        notifyWarning(
          'Agent 界面交接已过期',
          '请让 Agent 重新发起这次页面定位。',
        );
      }
      return true;
    },
    [notifyWarning, updateRecords],
  );

  const activeRecord = records[0] ?? null;
  const activeHandoff = activeRecord?.handoff ?? null;

  const registerSurfaceHandler = useCallback(
    (
      surface: DesktopAgentUiHandoffSurface,
      handler: AgentUiHandoffSurfaceHandler,
    ) => {
      handlersRef.current.set(surface, handler);
      setHandlerRevision((revision) => revision + 1);
      return () => {
        if (handlersRef.current.get(surface) === handler) {
          handlersRef.current.delete(surface);
          setHandlerRevision((revision) => revision + 1);
        }
      };
    },
    [],
  );

  const queueAcknowledgement = useCallback(
    (
      handoff: ValidatedAgentUiHandoff,
      result: AgentUiHandoffApplyResult,
    ) => {
      const normalized = normalizeApplyResult(result);
      const acknowledgementResult = normalizeAcknowledgementResult(
        normalized.result,
      );
      const acknowledgement: DesktopAgentUiHandoffAcknowledgeRequest = {
        handoffId: handoff.handoffId,
        status: normalized.status,
        ...(acknowledgementResult === undefined
          ? {}
          : { result: acknowledgementResult }),
        ...(normalized.status === 'failed'
          ? { failureMessage: normalized.failureMessage.slice(0, 2_000) }
          : {}),
      };
      preflightApprovedRef.current.delete(handoff.handoffId);
      acknowledgementAttemptRef.current.set(handoff.handoffId, 0);
      updateRecords((current) =>
        current.map((record) =>
          record.handoff.handoffId === handoff.handoffId
            ? { ...record, acknowledgement }
            : record,
        ),
      );
      setAcknowledgementRetryAt(0);
    },
    [updateRecords],
  );

  useEffect(() => {
    const bridge = window.autoEmailSender;
    if (!bridge?.onAgentUiHandoff) {
      return undefined;
    }
    return bridge.onAgentUiHandoff((incoming: DesktopAgentUiHandoff) => {
      let handoff: ValidatedAgentUiHandoff;
      try {
        handoff = validateAgentUiHandoff(incoming);
      } catch (error) {
        notifyError(
          '无法打开 Agent 指定的页面',
          error instanceof Error ? error.message : INVALID_HANDOFF_FAILURE,
        );
        if (
          bridge.acknowledgeAgentUiHandoff &&
          typeof incoming?.handoffId === 'string'
        ) {
          void bridge
            .acknowledgeAgentUiHandoff({
              handoffId: incoming.handoffId,
              status: 'failed',
              failureMessage: INVALID_HANDOFF_FAILURE,
            })
            .catch(() => undefined);
        }
        return;
      }

      updateRecords((current) => {
        const existingIndex = current.findIndex(
          (record) => record.handoff.handoffId === handoff.handoffId,
        );
        if (existingIndex < 0) {
          return [...current, { handoff }];
        }
        return current.map((record, index) =>
          index === existingIndex
            ? { ...record, handoff }
            : record,
        );
      });
    });
  }, [notifyError, updateRecords]);

  useEffect(() => {
    const acknowledgement = activeRecord?.acknowledgement;
    if (!activeRecord || !acknowledgement) {
      return undefined;
    }
    const bridge = window.autoEmailSender;
    const acknowledgeAgentUiHandoff = bridge?.acknowledgeAgentUiHandoff;
    if (!acknowledgeAgentUiHandoff) {
      return undefined;
    }
    const handoff = activeRecord.handoff;
    if (removeHandoffIfExpired(handoff)) {
      return undefined;
    }
    const delay = Math.max(0, acknowledgementRetryAt - Date.now());
    const timer = window.setTimeout(() => {
      if (removeHandoffIfExpired(handoff)) {
        return;
      }
      if (acknowledgementInFlightRef.current) {
        setAcknowledgementRetryAt(Date.now() + 250);
        return;
      }
      acknowledgementInFlightRef.current = true;
      void acknowledgeAgentUiHandoff(acknowledgement)
        .then((state) => {
          if (
            state.handoffId !== handoff.handoffId
          ) {
            throw new Error('桌面主进程返回了不匹配的界面交接 ID');
          }
          if (state.status !== acknowledgement.status) {
            acknowledgementAttemptRef.current.delete(handoff.handoffId);
            updateRecords((current) =>
              current.filter(
                (record) => record.handoff.handoffId !== handoff.handoffId,
              ),
            );
            setAcknowledgementRetryAt(0);
            notifyWarning(
              'Agent 界面交接状态已变化',
              `后端当前状态为 ${state.status}，已停止重试旧回执。`,
            );
            return;
          }
          acknowledgementAttemptRef.current.delete(handoff.handoffId);
          updateRecords((current) =>
            current.filter(
              (record) => record.handoff.handoffId !== handoff.handoffId,
            ),
          );
          setAcknowledgementRetryAt(0);
          if (acknowledgement.status === 'applied') {
            const message = getSuccessNotification(handoff);
            notifySuccess(message.title, message.description);
          }
        })
        .catch(() => {
          const attempt =
            (acknowledgementAttemptRef.current.get(handoff.handoffId) ?? 0) + 1;
          acknowledgementAttemptRef.current.set(handoff.handoffId, attempt);
          const retryDelay = Math.min(
            ACK_RETRY_MAX_DELAY_MS,
            500 * 2 ** Math.min(attempt, 5),
          );
          setAcknowledgementRetryAt(Date.now() + retryDelay);
        })
        .finally(() => {
          acknowledgementInFlightRef.current = false;
        });
    }, delay);
    return () => window.clearTimeout(timer);
  }, [
    acknowledgementRetryAt,
    activeRecord,
    notifySuccess,
    notifyWarning,
    removeHandoffIfExpired,
    updateRecords,
  ]);

  useEffect(() => {
    if (!activeHandoff || activeRecord?.acknowledgement || processingRef.current) {
      return;
    }
    if (removeHandoffIfExpired(activeHandoff)) {
      return;
    }

    const applyHandoff = async () => {
      processingRef.current = true;
      try {
        const identityId = getAgentUiHandoffIdentityId(activeHandoff);
        if (identityId !== null && selectionLoading) {
          return;
        }
        if (
          identityId !== null &&
          !identities.some((identity) => identity.id === identityId)
        ) {
          queueAcknowledgement(activeHandoff, {
            status: 'failed',
            failureMessage: `找不到界面交接指定的发件身份 ${identityId}。`,
          });
          notifyError(
            '无法打开 Agent 指定的页面',
            `找不到发件身份 ${identityId}。`,
          );
          return;
        }

        const identityNeedsChange =
          identityId !== null && selectedIdentityId !== identityId;
        const routeNeedsChange = location.pathname !== activeHandoff.route;
        const workspaceContextNeedsChange = WORKSPACE_CONTEXT_SURFACES.has(
          activeHandoff.surface,
        );
        if (
          (identityNeedsChange || routeNeedsChange || workspaceContextNeedsChange) &&
          !preflightApprovedRef.current.has(activeHandoff.handoffId)
        ) {
          const canContinue = await requestWorkspaceDraftGuard(
            routeNeedsChange ? { nextPath: activeHandoff.route } : undefined,
          );
          if (recordsRef.current[0]?.handoff.handoffId !== activeHandoff.handoffId) {
            return;
          }
          if (removeHandoffIfExpired(activeHandoff)) {
            return;
          }
          if (!canContinue) {
            queueAcknowledgement(activeHandoff, {
              status: 'awaiting_user',
              result: {
                reason: 'workspace_draft_guard',
                route: activeHandoff.route,
              },
            });
            notifyWarning(
              '已保留当前工作区',
              '你选择了继续编辑草稿；可让 Agent 重试这次页面定位。',
            );
            return;
          }
          preflightApprovedRef.current.add(activeHandoff.handoffId);
        }

        if (identityNeedsChange && identityId !== null) {
          setSelectedIdentityId(identityId);
          return;
        }
        if (routeNeedsChange) {
          navigate(activeHandoff.route, {
            state: {
              agentUiHandoff: {
                handoffId: activeHandoff.handoffId,
                surface: activeHandoff.surface,
              },
            },
          });
          return;
        }

        const handler = handlersRef.current.get(activeHandoff.surface);
        if (!handler) {
          return;
        }
        if (removeHandoffIfExpired(activeHandoff)) {
          return;
        }
        try {
          const result = await handler(activeHandoff);
          if (recordsRef.current[0]?.handoff.handoffId !== activeHandoff.handoffId) {
            return;
          }
          if (removeHandoffIfExpired(activeHandoff)) {
            return;
          }
          queueAcknowledgement(activeHandoff, result);
          if (result.status === 'failed') {
            notifyError('页面定位失败', result.failureMessage);
          }
        } catch (error) {
          if (recordsRef.current[0]?.handoff.handoffId !== activeHandoff.handoffId) {
            return;
          }
          if (removeHandoffIfExpired(activeHandoff)) {
            return;
          }
          const message =
            error instanceof Error ? error.message : '页面适配器未能应用界面交接。';
          queueAcknowledgement(activeHandoff, {
            status: 'failed',
            failureMessage: message,
          });
          notifyError('页面定位失败', message);
        }
      } finally {
        processingRef.current = false;
      }
    };

    void applyHandoff();
  }, [
    activeHandoff,
    activeRecord?.acknowledgement,
    handlerRevision,
    identities,
    location.pathname,
    navigate,
    notifyError,
    notifyWarning,
    queueAcknowledgement,
    removeHandoffIfExpired,
    requestWorkspaceDraftGuard,
    selectedIdentityId,
    selectionLoading,
    setSelectedIdentityId,
  ]);

  const contextValue = useMemo(
    () => ({ activeHandoff, registerSurfaceHandler }),
    [activeHandoff, registerSurfaceHandler],
  );

  return (
    <AgentUiHandoffContext.Provider value={contextValue}>
      {children}
    </AgentUiHandoffContext.Provider>
  );
};
