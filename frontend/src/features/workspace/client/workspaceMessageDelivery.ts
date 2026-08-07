import type { WorkspaceMessageDTO } from '@/types';

export const isFailedSentMessage = (message: WorkspaceMessageDTO) =>
  message.direction === 'sent' &&
  (message.delivery_status === 'failed' ||
    (message.delivery_status == null && Boolean(message.failure_summary?.trim())));

export const isSuccessfulSentMessage = (message: WorkspaceMessageDTO) =>
  message.direction === 'sent' && !isFailedSentMessage(message);

export const isCommunicationMessage = (message: WorkspaceMessageDTO) =>
  message.direction === 'received' || isSuccessfulSentMessage(message);
