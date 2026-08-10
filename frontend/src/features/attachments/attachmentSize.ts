import type { IdentityMaterialDTO } from '@/types';
import { formatFileSize } from '@/lib/formatFileSize';

export { formatFileSize } from '@/lib/formatFileSize';

export const RECOMMENDED_ATTACHMENT_TOTAL_BYTES = 1024 * 1024;
export const LARGE_ATTACHMENT_WARNING_CONFIRMATION_LABEL = '我已知晓，不再提示';
export const LARGE_ATTACHMENT_WARNING_SUPPRESSION_KEY =
  'large_attachment_warning_suppressed';

export const isLargeAttachmentWarningSuppressed = () => {
  if (typeof window === 'undefined') {
    return false;
  }
  try {
    return window.localStorage.getItem(LARGE_ATTACHMENT_WARNING_SUPPRESSION_KEY) === 'true';
  } catch {
    return false;
  }
};

export const suppressLargeAttachmentWarnings = () => {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(LARGE_ATTACHMENT_WARNING_SUPPRESSION_KEY, 'true');
  } catch {
    // A blocked storage backend should not prevent the confirmed action.
  }
};

export const shouldPromptForLargeAttachments = () =>
  !isLargeAttachmentWarningSuppressed();

export const getSelectedAttachmentTotalBytes = (
  materials: Pick<IdentityMaterialDTO, 'id' | 'size_bytes'>[],
  selectedMaterialIds: number[],
) => {
  const selectedIds = new Set(selectedMaterialIds);
  return materials.reduce(
    (total, material) =>
      selectedIds.has(material.id) ? total + Math.max(0, material.size_bytes) : total,
    0,
  );
};

export const isAttachmentTotalOverRecommendedLimit = (totalSizeBytes: number) =>
  totalSizeBytes > RECOMMENDED_ATTACHMENT_TOTAL_BYTES;

export const buildLargeAttachmentWarning = (
  totalSizeBytes: number,
  options: { repeatedPerMessage?: boolean } = {},
) => {
  if (!isAttachmentTotalOverRecommendedLimit(totalSizeBytes)) {
    return null;
  }

  return [
    `附件共 ${formatFileSize(totalSizeBytes)}；建议不超过 1 MB，以降低限流风险。`,
    options.repeatedPerMessage ? '这些附件将随每封邮件发送。' : null,
  ]
    .filter(Boolean)
    .join('\n');
};

export const buildBulkLargeAttachmentWarning = (totalSizesBytes: number[]) => {
  const oversizedTotals = totalSizesBytes.filter(
    isAttachmentTotalOverRecommendedLimit,
  );
  if (oversizedTotals.length === 0) {
    return null;
  }

  const allTotalsMatch =
    totalSizesBytes.length > 0 &&
    totalSizesBytes.every((total) => total === totalSizesBytes[0]);
  if (allTotalsMatch) {
    return `每封附件均为 ${formatFileSize(oversizedTotals[0])}；建议不超过 1 MB，以降低限流风险。`;
  }

  const maximumTotal = Math.max(...oversizedTotals);
  return `${oversizedTotals.length} 封附件超过 1 MB，最大 ${formatFileSize(maximumTotal)}；建议压缩以降低限流风险。`;
};
