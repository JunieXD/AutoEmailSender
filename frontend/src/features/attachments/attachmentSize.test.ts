import { describe, expect, it } from 'vitest';

import {
  RECOMMENDED_ATTACHMENT_TOTAL_BYTES,
  buildBulkLargeAttachmentWarning,
  buildLargeAttachmentWarning,
  formatFileSize,
  getSelectedAttachmentTotalBytes,
  isAttachmentTotalOverRecommendedLimit,
} from './attachmentSize';

describe('attachmentSize', () => {
  it('formats attachment sizes for display', () => {
    expect(formatFileSize(512)).toBe('512 B');
    expect(formatFileSize(1536)).toBe('1.5 KB');
    expect(formatFileSize(1024 * 1024 + 1)).toBe('1.00 MB');
  });

  it('sums only selected attachments', () => {
    expect(
      getSelectedAttachmentTotalBytes(
        [
          { id: 1, size_bytes: 400 },
          { id: 2, size_bytes: 600 },
          { id: 3, size_bytes: 800 },
        ],
        [1, 3],
      ),
    ).toBe(1200);
  });

  it('warns only when the total strictly exceeds one MiB', () => {
    expect(
      isAttachmentTotalOverRecommendedLimit(
        RECOMMENDED_ATTACHMENT_TOTAL_BYTES,
      ),
    ).toBe(false);
    expect(
      buildLargeAttachmentWarning(RECOMMENDED_ATTACHMENT_TOTAL_BYTES),
    ).toBeNull();

    const warning = buildLargeAttachmentWarning(
      RECOMMENDED_ATTACHMENT_TOTAL_BYTES + 1,
    );
    expect(warning).toContain('建议不超过 1 MB');
    expect(warning).toContain('减少被邮箱提供商限流的概率');
    expect(warning).not.toContain('云盘');
  });

  it('summarizes oversized attachments across multiple messages', () => {
    const warning = buildBulkLargeAttachmentWarning([
      RECOMMENDED_ATTACHMENT_TOTAL_BYTES + 1,
      RECOMMENDED_ATTACHMENT_TOTAL_BYTES + 2,
      1024,
    ]);

    expect(warning).toContain('其中 2 封邮件');
    expect(warning).not.toContain('云盘');
  });
});
