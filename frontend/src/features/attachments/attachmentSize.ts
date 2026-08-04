import type { IdentityMaterialDTO } from '@/types';

export const RECOMMENDED_ATTACHMENT_TOTAL_BYTES = 1024 * 1024;

export const formatFileSize = (sizeBytes: number) => {
  const normalizedBytes = Number.isFinite(sizeBytes) ? Math.max(0, sizeBytes) : 0;
  if (normalizedBytes < 1024) {
    return `${Math.round(normalizedBytes)} B`;
  }
  if (normalizedBytes < 1024 * 1024) {
    return `${(normalizedBytes / 1024).toFixed(1)} KB`;
  }
  return `${(normalizedBytes / (1024 * 1024)).toFixed(2)} MB`;
};

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
    `附件总大小为 ${formatFileSize(totalSizeBytes)}，建议不超过 1 MB，以减少被邮箱提供商限流的概率。`,
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
    return `每封邮件的附件总大小均为 ${formatFileSize(oversizedTotals[0])}，建议不超过 1 MB，以减少被邮箱提供商限流的概率。`;
  }

  const maximumTotal = Math.max(...oversizedTotals);
  return `其中 ${oversizedTotals.length} 封邮件的附件总大小超过 1 MB，最大为 ${formatFileSize(maximumTotal)}。建议将每封邮件的附件总大小控制在 1 MB 以内，以减少被邮箱提供商限流的概率。`;
};
