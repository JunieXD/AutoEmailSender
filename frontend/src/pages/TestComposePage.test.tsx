import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { TestComposeThreadDTO } from '@/types';
import { TestComposePage } from './TestComposePage';

const apiMocks = vi.hoisted(() => ({
  getTestComposeThread: vi.fn(),
  generateTestComposeDraft: vi.fn(),
  saveTestComposeDraft: vi.fn(),
  sendTestComposeMessage: vi.fn(),
  listOutreachTemplates: vi.fn(),
}));
const confirmMock = vi.hoisted(() => vi.fn());

vi.mock('@/context/SelectionContext', () => ({
  useSelectionContext: () => ({
    selectedIdentityId: 1,
    selectedLlmProfileId: 2,
  }),
}));

vi.mock('@/context/NotificationContext', () => ({
  useNotification: () => ({
    notifyError: vi.fn(),
    notifySuccess: vi.fn(),
  }),
}));

vi.mock('@/lib/useConfirmDialog', () => ({
  useConfirmDialog: () => ({
    confirm: confirmMock,
    dialog: null,
  }),
}));

vi.mock('@/lib/api/testComposeApi', () => ({
  getTestComposeThread: apiMocks.getTestComposeThread,
  generateTestComposeDraft: apiMocks.generateTestComposeDraft,
  saveTestComposeDraft: apiMocks.saveTestComposeDraft,
  sendTestComposeMessage: apiMocks.sendTestComposeMessage,
}));

vi.mock('@/lib/api/outreachTemplates', () => ({
  listOutreachTemplates: apiMocks.listOutreachTemplates,
}));

vi.mock('@/components/molecules/EmailTemplateEditor', () => ({
  EmailTemplateEditor: ({
    label,
    html,
  }: {
    label: string;
    html: string;
  }) => <textarea aria-label={label} value={html} readOnly />,
}));

vi.mock('@/components/molecules/SubjectTemplateInput', () => ({
  SubjectTemplateInput: ({
    label,
    value,
  }: {
    label: string;
    value: string;
  }) => <input aria-label={label} value={value} readOnly />,
}));

const thread: TestComposeThreadDTO = {
  identity: {
    id: 1,
    name: '测试身份',
    profile_name: '测试身份',
    sender_name: '测试同学',
    email_address: 'sender@example.com',
  },
  llm_profile: {
    id: 2,
    name: '测试模型',
    provider: 'openai',
    model_name: 'test-model',
  },
  material_options: [
    {
      id: 7,
      display_name: 'large-portfolio.pdf',
      original_filename: 'large-portfolio.pdf',
      mime_type: 'application/pdf',
      size_bytes: 1024 * 1024 + 1,
      material_type: 'portfolio',
      is_primary: false,
      created_at: '2026-08-04T00:00:00Z',
    },
  ],
  draft: {
    outreach_template_id: null,
    subject: '测试主题',
    body_text: '测试正文',
    body_html: '<p>测试正文</p>',
    selected_material_ids: [7],
  },
  history: [],
};

describe('TestComposePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    apiMocks.getTestComposeThread.mockResolvedValue(thread);
    apiMocks.listOutreachTemplates.mockResolvedValue([]);
    confirmMock.mockResolvedValue(false);
  });

  it('shows attachment sizes and blocks oversized test sends until confirmed', async () => {
    render(
      <MemoryRouter>
        <TestComposePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('large-portfolio.pdf')).toBeInTheDocument();
    expect(screen.getByText(/作品集 · 1\.00 MB/)).toBeInTheDocument();
    expect(screen.getByText(/已选 1 个附件，共 1\.00 MB/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '发送测试邮件' }));

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({
        title: '附件超过 1 MB，仍要发送测试邮件吗？',
        description: expect.stringContaining(
          '建议不超过 1 MB，以减少被邮箱提供商限流的概率。',
        ),
        confirmLabel: '仍然发送',
        cancelLabel: '返回调整',
        confirmationCheckbox: expect.objectContaining({
          label: '我已知晓，不再提示',
          onConfirmChecked: expect.any(Function),
        }),
      }));
    });
    expect(confirmMock.mock.calls[0][0].description).not.toContain('云盘');
    expect(apiMocks.sendTestComposeMessage).not.toHaveBeenCalled();
  });

  it('does not prompt again after the large attachment warning was suppressed', async () => {
    window.localStorage.setItem('large_attachment_warning_suppressed', 'true');
    apiMocks.sendTestComposeMessage.mockResolvedValue(thread);

    render(
      <MemoryRouter>
        <TestComposePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('large-portfolio.pdf')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '发送测试邮件' }));

    await waitFor(() => {
      expect(apiMocks.sendTestComposeMessage).toHaveBeenCalledTimes(1);
    });
    expect(confirmMock).not.toHaveBeenCalled();
  });
});
