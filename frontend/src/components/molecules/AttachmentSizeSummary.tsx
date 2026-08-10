import clsx from 'clsx';

import {
  formatFileSize,
  isAttachmentTotalOverRecommendedLimit,
} from '@/features/attachments/attachmentSize';

type AttachmentSizeSummaryProps = {
  selectedCount: number;
  totalSizeBytes: number;
  className?: string;
};

export const AttachmentSizeSummary = ({
  selectedCount,
  totalSizeBytes,
  className,
}: AttachmentSizeSummaryProps) => {
  const overRecommendedLimit = isAttachmentTotalOverRecommendedLimit(totalSizeBytes);

  return (
    <div
      className={clsx(
        'flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-5',
        overRecommendedLimit ? 'text-amber-800' : 'text-stone-500',
        className,
      )}
    >
      <span className="font-medium">
        已选 {selectedCount} 个附件，共 {formatFileSize(totalSizeBytes)}
      </span>
      {overRecommendedLimit ? (
        <span>建议不超过 1 MB，发送更稳定。</span>
      ) : null}
    </div>
  );
};
