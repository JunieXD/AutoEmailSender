import { beforeEach, describe, expect, it } from 'vitest';

import {
  isLargeAttachmentWarningSuppressed,
  shouldPromptForLargeAttachments,
  suppressLargeAttachmentWarnings,
} from './attachmentSize';

describe('large attachment warning preference', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('persists the choice to stop showing large attachment confirmations', () => {
    expect(isLargeAttachmentWarningSuppressed()).toBe(false);
    expect(shouldPromptForLargeAttachments()).toBe(true);

    suppressLargeAttachmentWarnings();

    expect(isLargeAttachmentWarningSuppressed()).toBe(true);
    expect(shouldPromptForLargeAttachments()).toBe(false);
  });
});
