import { useEffect, useMemo, useRef, useState } from 'react';
import clsx from 'clsx';
import { ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import { formatApiDateTime } from '@/lib/dateTime';
import {
  extractPlainTextFromHtml,
  hasRenderablePreviewContent,
  sanitizeTemplateHtmlForPreview,
} from '@/lib/htmlPreview';
import {
  isCommunicationMessage,
  isFailedSentMessage,
} from '@/features/workspace/client/workspaceMessageDelivery';
import type {
  WorkspaceIdentityDTO,
  WorkspaceMessageDTO,
  WorkspaceSyncWarningDTO,
} from '@/types';

type WorkspaceMessageThreadProps = {
  messages: WorkspaceMessageDTO[];
  monitoringLabel?: string;
  lastCheckedAt?: Date | null;
  refreshing?: boolean;
  newReceivedCount?: number;
  communicationScope?: WorkspaceIdentityDTO[];
  syncWarnings?: WorkspaceSyncWarningDTO[];
  onRefresh?: () => void;
};

const previewStyle = {
  display: '-webkit-box',
  WebkitBoxOrient: 'vertical' as const,
  WebkitLineClamp: 2,
  overflow: 'hidden',
};

const HTML_MARKUP_PATTERN = /<\/?[a-z][\s\S]*>/i;

const buildPreview = (content: string, contentHtml: string | null) => {
  const htmlSource = contentHtml?.trim() || content.trim();
  if (htmlSource && (contentHtml?.trim() || HTML_MARKUP_PATTERN.test(content))) {
    const htmlText = extractPlainTextFromHtml(htmlSource);
    if (htmlText) {
      return htmlText;
    }
  }
  const normalized = content.replace(/\s+/g, ' ').trim();
  return normalized || '（这条记录没有正文）';
};

const getMessageLabel = (message: WorkspaceMessageDTO) => {
  if (message.direction === 'received') {
    return '老师回复';
  }
  return isFailedSentMessage(message) ? '发送失败' : '已发送';
};

const getMessageBubbleClassName = (message: WorkspaceMessageDTO) =>
  message.direction === 'received'
    ? 'border-stone-200 bg-white text-stone-900 shadow-[0_18px_38px_-30px_rgba(41,37,36,0.26)]'
    : isFailedSentMessage(message)
      ? 'border-red-200 bg-red-50 text-red-950 shadow-[0_22px_42px_-28px_rgba(127,29,29,0.22)]'
    : 'border-primary/15 bg-[linear-gradient(180deg,rgba(153,27,27,0.96),rgba(127,29,29,0.96))] text-white shadow-[0_22px_42px_-28px_rgba(127,29,29,0.38)]';

const getSourceIdentityLabel = (
  identity: WorkspaceIdentityDTO,
  communicationScope: WorkspaceIdentityDTO[],
) => {
  const duplicateName = communicationScope.some(
    (candidate) =>
      candidate.id !== identity.id && candidate.profile_name === identity.profile_name,
  );
  return duplicateName
    ? `${identity.profile_name}（${identity.email_address}）`
    : identity.profile_name;
};

const getMessageSourceLabel = (
  message: WorkspaceMessageDTO,
  communicationScope: WorkspaceIdentityDTO[],
) => {
  const sourceIdentities = message.source_identities ?? [];
  if (sourceIdentities.length === 0) {
    return null;
  }
  const identityNames = sourceIdentities
    .map((identity) => getSourceIdentityLabel(identity, communicationScope))
    .join('、');
  return message.direction === 'received'
    ? `由 ${identityNames} 收取`
    : isFailedSentMessage(message)
      ? `由 ${identityNames} 尝试发送`
    : `由 ${identityNames} 发出`;
};

export const WorkspaceMessageThread = ({
  messages,
  monitoringLabel = '正在监听回复',
  lastCheckedAt = null,
  refreshing = false,
  newReceivedCount = 0,
  communicationScope = [],
  syncWarnings = [],
  onRefresh,
}: WorkspaceMessageThreadProps) => {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [shouldStickToBottom, setShouldStickToBottom] = useState(true);
  const [expandedMessageId, setExpandedMessageId] = useState<number | null>(null);

  const realMessages = useMemo(
    () => messages.filter((message) => message.direction !== 'draft'),
    [messages],
  );

  useEffect(() => {
    const node = scrollRef.current;
    if (!node || !shouldStickToBottom) {
      return;
    }
    node.scrollTop = node.scrollHeight;
  }, [realMessages.length, shouldStickToBottom]);

  const showJumpToReply = newReceivedCount > 0 && !shouldStickToBottom;
  const isEmpty = realMessages.length === 0;
  const communicationMessageCount = realMessages.filter(isCommunicationMessage).length;
  const failedAttemptCount = realMessages.filter(isFailedSentMessage).length;

  return (
    <div
      className={clsx(
        'flex min-h-0 flex-col overflow-hidden bg-[radial-gradient(circle_at_top,rgba(153,27,27,0.06),transparent_22%),linear-gradient(180deg,rgba(255,252,247,0.94),rgba(255,255,255,0.98))] px-4 py-4 sm:px-6',
        isEmpty ? 'shrink-0' : 'min-h-[320px] max-h-[min(68vh,680px)]',
      )}
      data-message-thread-root
    >
      <div
        className="shrink-0 rounded-[24px] border border-stone-200/80 bg-white/92 px-4 py-3 shadow-sm backdrop-blur-xl"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="text-sm font-semibold text-stone-900">通信记录</div>
              {communicationScope.length > 1 ? (
                <span className="rounded-lg border border-teal-200 bg-teal-50 px-2 py-0.5 text-xs font-semibold text-teal-700">
                  共享通信 · {communicationScope.length} 个身份
                </span>
              ) : null}
              {newReceivedCount > 0 ? (
                <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-700">
                  新回复
                </span>
              ) : null}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs leading-5 text-stone-500">
              <span>{monitoringLabel}</span>
              {lastCheckedAt ? (
                <span>
                  上次检查{' '}
                  {lastCheckedAt.toLocaleTimeString('zh-CN', {
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </span>
              ) : null}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {showJumpToReply ? (
              <button
                type="button"
                onClick={() => {
                  const node = scrollRef.current;
                  if (node) {
                    node.scrollTop = node.scrollHeight;
                  }
                  setShouldStickToBottom(true);
                }}
                className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700 transition hover:bg-emerald-100"
              >
                跳到新回复
              </button>
            ) : null}
            <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1 text-xs font-medium text-stone-600">
              {communicationMessageCount} 条
            </span>
            {failedAttemptCount > 0 ? (
              <span className="rounded-full border border-red-200 bg-red-50 px-3 py-1 text-xs font-semibold text-red-700">
                {failedAttemptCount} 次失败尝试
              </span>
            ) : null}
            {onRefresh ? (
              <button
                type="button"
                aria-label="刷新通信记录"
                onClick={onRefresh}
                disabled={refreshing}
                className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:bg-stone-50 disabled:cursor-wait disabled:opacity-60"
              >
                <RefreshCw className={clsx('h-4 w-4', refreshing && 'animate-spin')} />
              </button>
            ) : null}
          </div>
        </div>
        {syncWarnings.length > 0 ? (
          <div className="mt-3 border-t border-amber-100 pt-3 text-xs leading-5 text-amber-800">
            {syncWarnings.map((warning) => (
              <div key={warning.identity_id}>
                {warning.identity_name}：{warning.message}
              </div>
            ))}
          </div>
        ) : null}
      </div>

      <div
        ref={scrollRef}
        onScroll={(event) => {
          const target = event.currentTarget;
          const distanceToBottom =
            target.scrollHeight - target.scrollTop - target.clientHeight;
          setShouldStickToBottom(distanceToBottom < 80);
        }}
        className={clsx(
          'mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col gap-4 overflow-y-auto pt-4',
          isEmpty ? 'overflow-y-auto' : '',
        )}
        data-message-thread-inner
        data-message-thread-scroll
      >
        {isEmpty ? (
          <div className="flex w-full justify-center" data-empty-thread>
            <div className="w-full max-w-2xl rounded-[24px] border border-dashed border-stone-300 bg-white/94 px-5 py-4 text-left shadow-sm">
              <div className="text-sm font-semibold text-stone-900">暂无通信记录</div>
              <div className="mt-1 text-xs leading-6 text-stone-500">
                发出邮件或收到回复后，会显示在这里。
              </div>
            </div>
          </div>
        ) : (
          realMessages.map((message) => {
            const isReceived = message.direction === 'received';
            const isFailed = isFailedSentMessage(message);
            const isExpanded =
              expandedMessageId === message.id &&
              realMessages.some((item) => item.id === expandedMessageId);
            const displayText = buildPreview(message.content, message.content_html);
            const expandedHtml = message.content_html
              ? sanitizeTemplateHtmlForPreview(message.content_html)
              : '';
            const canRenderExpandedHtml =
              Boolean(expandedHtml) && hasRenderablePreviewContent(expandedHtml);
            const sourceLabel = getMessageSourceLabel(message, communicationScope);

            return (
              <div
                key={message.id}
                className={clsx('flex w-full', isReceived ? 'justify-start' : 'justify-end')}
              >
                <button
                  type="button"
                  onClick={() =>
                    setExpandedMessageId((current) =>
                      current === message.id ? null : message.id,
                    )
                  }
                  className={clsx(
                    'w-full max-w-[86%] rounded-[28px] border px-5 py-4 text-left transition hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20',
                    getMessageBubbleClassName(message),
                  )}
                >
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span
                      className={clsx(
                        'rounded-full px-2.5 py-1 font-semibold',
                        isReceived
                          ? 'bg-stone-100 text-stone-700'
                          : isFailed
                            ? 'bg-red-100 text-red-800'
                            : 'bg-white/14 text-white',
                      )}
                    >
                      {getMessageLabel(message)}
                    </span>
                    <span className={isReceived || isFailed ? 'text-stone-500' : 'text-white/70'}>
                      {formatApiDateTime(message.created_at)}
                    </span>
                    {sourceLabel ? (
                      <span className={isReceived || isFailed ? 'text-stone-600' : 'text-white/80'}>
                        {sourceLabel}
                      </span>
                    ) : null}
                    <span
                      className={clsx(
                        'ml-auto inline-flex items-center gap-1 font-medium',
                        isReceived || isFailed ? 'text-stone-500' : 'text-white/75',
                      )}
                    >
                      {isExpanded ? '收起' : '展开'}
                      {isExpanded ? (
                        <ChevronUp className="h-4 w-4" />
                      ) : (
                        <ChevronDown className="h-4 w-4" />
                      )}
                    </span>
                  </div>

                  {isFailed ? (
                    <div role="alert" className="mt-3 rounded-2xl border border-red-200 bg-white/80 px-4 py-3 text-sm leading-6 text-red-800">
                      <span className="font-semibold">发送失败：</span>
                      {message.failure_summary?.trim() || '邮件未成功发出，请检查发件配置后重试。'}
                    </div>
                  ) : null}

                  {message.subject ? (
                    <div className="mt-3 text-sm font-semibold leading-6">
                      {message.subject}
                    </div>
                  ) : null}

                  {isExpanded && canRenderExpandedHtml ? (
                    <div
                      className={clsx(
                        'mt-3 overflow-x-auto rounded-2xl px-4 py-4 text-sm leading-7 shadow-inner',
                        isReceived
                          ? 'border border-stone-100 bg-white text-stone-900 shadow-stone-200/60'
                          : isFailed
                            ? 'border border-red-100 bg-white text-red-950 shadow-red-100/60'
                            : 'border border-white/20 bg-white/10 text-white/92 shadow-black/10 [&_*]:!text-inherit',
                      )}
                      data-message-html
                      dangerouslySetInnerHTML={{ __html: expandedHtml }}
                    />
                  ) : isExpanded ? (
                    <div
                      className={clsx(
                        'mt-3 whitespace-pre-wrap break-words text-sm leading-7',
                        isReceived ? 'text-stone-700' : isFailed ? 'text-red-900' : 'text-white/92',
                      )}
                    >
                      {displayText}
                    </div>
                  ) : (
                    <div
                      className={clsx(
                        'mt-3 text-sm leading-7',
                        isReceived ? 'text-stone-600' : isFailed ? 'text-red-800' : 'text-white/82',
                      )}
                      style={previewStyle}
                    >
                      {displayText}
                    </div>
                  )}
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
