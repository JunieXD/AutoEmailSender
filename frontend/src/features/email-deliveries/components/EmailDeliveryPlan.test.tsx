import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  EmailDeliveryItemDTO,
  EmailDeliveryListDTO,
  EmailDeliveryView,
} from '@/types';
import { EmailDeliveryPlan } from './EmailDeliveryPlan';

const apiMocks = vi.hoisted(() => ({
  listEmailDeliveries: vi.fn(),
  rescheduleEmailDelivery: vi.fn(),
  cancelEmailDelivery: vi.fn(),
  sendEmailDeliveryNow: vi.fn(),
  cancelBatchTaskItemSend: vi.fn(),
  restoreBatchTaskItemSend: vi.fn(),
}));

const notificationMocks = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

const selectionMock = vi.hoisted(() => ({
  selectedIdentityId: 1,
  identities: [
    {
      id: 1,
      profile_name: '申请身份 A',
      email_address: 'a@example.com',
    },
    {
      id: 2,
      profile_name: '申请身份 B',
      email_address: 'b@example.com',
    },
  ],
  setSelectedIdentityId: vi.fn(),
}));

const confirmMock = vi.hoisted(() => vi.fn().mockResolvedValue(true));

vi.mock('@/context/SelectionContext', () => ({
  useSelectionContext: () => selectionMock,
}));

vi.mock('@/context/NotificationContext', () => ({
  useNotification: () => notificationMocks,
}));

vi.mock('@/lib/useConfirmDialog', () => ({
  useConfirmDialog: () => ({
    confirm: confirmMock,
    dialog: null,
  }),
}));

vi.mock('@/lib/api/emailDeliveriesApi', () => ({
  listEmailDeliveries: apiMocks.listEmailDeliveries,
  rescheduleEmailDelivery: apiMocks.rescheduleEmailDelivery,
  cancelEmailDelivery: apiMocks.cancelEmailDelivery,
  sendEmailDeliveryNow: apiMocks.sendEmailDeliveryNow,
}));

vi.mock('@/lib/api/batchTasksApi', () => ({
  cancelBatchTaskItemSend: apiMocks.cancelBatchTaskItemSend,
  restoreBatchTaskItemSend: apiMocks.restoreBatchTaskItemSend,
}));

const buildItem = (
  overrides: Partial<EmailDeliveryItemDTO> = {},
): EmailDeliveryItemDTO => ({
  id: 101,
  source: 'manual',
  batch_task_id: null,
  batch_task_name: null,
  batch_task_status: null,
  professor_id: 21,
  professor_name: '张老师',
  professor_email: 'mentor@example.edu',
  identity_id: 1,
  identity_name: '申请身份 A',
  sender_email: 'a@example.com',
  subject: '博士申请咨询',
  attachment_count: 1,
  attachment_size_bytes: 1024,
  status: 'waiting_scheduled',
  status_label: '等待发送',
  status_description: '将在计划时间进入发送流程',
  scheduled_at: '2099-08-08T02:00:00Z',
  last_scheduled_at: null,
  schedule_canceled_at: null,
  batch_send_canceled_at: null,
  approved_at: '2099-08-07T01:00:00Z',
  last_send_attempt_at: null,
  sent_at: null,
  last_error: null,
  retry_count: 0,
  created_at: '2099-08-07T01:00:00Z',
  updated_at: '2099-08-07T01:30:00Z',
  can_reschedule: true,
  can_cancel: true,
  can_send_now: true,
  can_restore: false,
  can_edit: true,
  ...overrides,
});

const buildList = (
  overrides: Partial<EmailDeliveryListDTO> = {},
): EmailDeliveryListDTO => ({
  items: [buildItem()],
  counts: { upcoming: 250, attention: 3, history: 80 },
  page: 1,
  page_size: 20,
  total_count: 250,
  total_pages: 13,
  ...overrides,
});

const renderPlan = (initialEntry = '/tasks') =>
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <EmailDeliveryPlan
        onSectionChange={vi.fn()}
        onOpenBatchTask={vi.fn()}
      />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.listEmailDeliveries.mockImplementation(
    async (params: { page: number; pageSize: number; view: EmailDeliveryView }) =>
      buildList({
        page: params.page,
        page_size: params.pageSize,
      }),
  );
  apiMocks.rescheduleEmailDelivery.mockResolvedValue({
    ok: true,
    task_id: 101,
    message: '发送时间已更新',
  });
  apiMocks.cancelEmailDelivery.mockResolvedValue({
    ok: true,
    task_id: 101,
    message: '已取消定时',
  });
  apiMocks.sendEmailDeliveryNow.mockResolvedValue({
    ok: true,
    task_id: 101,
    message: '邮件已发送',
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('EmailDeliveryPlan', () => {
  it('loads all identities by default and keeps pagination on the server', async () => {
    renderPlan();

    expect((await screen.findAllByText('博士申请咨询')).length).toBeGreaterThan(0);
    expect(apiMocks.listEmailDeliveries).toHaveBeenCalledWith(
      expect.objectContaining({
        view: 'upcoming',
        page: 1,
        pageSize: 20,
        identityId: null,
        source: 'all',
        sort: 'scheduled_asc',
        searchFields: [
          'recipient_name',
          'recipient_email',
          'subject',
          'batch_name',
        ],
      }),
      expect.any(AbortSignal),
    );
    expect(screen.getByRole('button', { name: /即将发送\s*250/ })).toBeInTheDocument();
    expect(screen.queryByText(/时间按 .* 显示/)).not.toBeInTheDocument();
    expect(screen.queryByText('发送服务运行中')).not.toBeInTheDocument();
    expect(screen.queryByText('应用关闭期间不会发送')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '下一页' }));

    await waitFor(() => {
      expect(apiMocks.listEmailDeliveries).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2, pageSize: 20 }),
        expect.any(AbortSignal),
      );
    });
  });

  it('debounces search and applies advanced filters through shared selects', async () => {
    renderPlan();
    await screen.findAllByText('博士申请咨询');

    fireEvent.change(screen.getByRole('searchbox', { name: '搜索发送计划' }), {
      target: { value: '张老师' },
    });

    await waitFor(
      () => {
        expect(apiMocks.listEmailDeliveries).toHaveBeenCalledWith(
          expect.objectContaining({ query: '张老师', page: 1 }),
          expect.any(AbortSignal),
        );
      },
      { timeout: 1000 },
    );

    fireEvent.click(
      screen.getByRole('button', {
        name: /搜索范围：选择字段：全部字段/,
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: '全部取消' }));
    fireEvent.click(screen.getByRole('option', { name: '邮件主题' }));

    await waitFor(() => {
      expect(apiMocks.listEmailDeliveries).toHaveBeenCalledWith(
        expect.objectContaining({
          query: '张老师',
          searchFields: ['subject'],
          page: 1,
        }),
        expect.any(AbortSignal),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: '高级筛选' }));
    fireEvent.click(screen.getByRole('button', { name: '筛选发件身份' }));
    fireEvent.click(screen.getByRole('option', { name: '申请身份 B' }));
    fireEvent.click(screen.getByRole('button', { name: '筛选邮件来源' }));
    fireEvent.click(screen.getByRole('option', { name: '批量邮件' }));

    await waitFor(() => {
      expect(apiMocks.listEmailDeliveries).toHaveBeenCalledWith(
        expect.objectContaining({
          identityId: 2,
          source: 'batch',
          query: '张老师',
        }),
        expect.any(AbortSignal),
      );
    });
    expect(screen.getByRole('button', { name: '高级筛选 2' })).toBeInTheDocument();
  });

  it('sorts on the server and resets to the relevant default when views change', async () => {
    renderPlan();
    await screen.findAllByText('博士申请咨询');

    fireEvent.click(screen.getByRole('button', { name: '发送计划排序' }));
    fireEvent.click(screen.getByRole('option', { name: '最晚计划优先' }));

    await waitFor(() => {
      expect(apiMocks.listEmailDeliveries).toHaveBeenCalledWith(
        expect.objectContaining({ view: 'upcoming', sort: 'scheduled_desc' }),
        expect.any(AbortSignal),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: /需处理\s*3/ }));

    await waitFor(() => {
      expect(apiMocks.listEmailDeliveries).toHaveBeenCalledWith(
        expect.objectContaining({ view: 'attention', sort: 'updated_desc' }),
        expect.any(AbortSignal),
      );
    });
    expect(screen.getByRole('button', { name: '发送计划排序' })).toHaveTextContent(
      '最近出现优先',
    );
  });

  it('reschedules with the item version and refreshes the current page', async () => {
    renderPlan();
    await screen.findAllByText('博士申请咨询');

    fireEvent.click(screen.getAllByRole('button', { name: '改期' })[0]);
    const input = screen.getByLabelText('新的发送时间');
    fireEvent.change(input, { target: { value: '2099-08-09T10:30' } });
    fireEvent.click(screen.getByRole('button', { name: '确认修改' }));

    await waitFor(() => {
      expect(apiMocks.rescheduleEmailDelivery).toHaveBeenCalledWith(101, {
        scheduled_at: new Date('2099-08-09T10:30').toISOString(),
        expected_updated_at: '2099-08-07T01:30:00Z',
      });
    });
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith(
      '发送时间已更新',
      expect.stringContaining('张老师'),
    );
    expect(apiMocks.listEmailDeliveries.mock.calls.length).toBeGreaterThan(1);
  });

  it('locates a delivery across views when its status changed', async () => {
    apiMocks.listEmailDeliveries.mockImplementation(
      async (params: {
        page: number;
        pageSize: number;
        view: EmailDeliveryView;
        taskId?: number | null;
      }) =>
        buildList({
          items: [
            buildItem({
              status: 'sent',
              status_label: '已发送',
              status_description: '邮件已成功交给发件服务器',
              scheduled_at: null,
              sent_at: '2099-08-08T02:05:00Z',
              can_reschedule: false,
              can_cancel: false,
              can_send_now: false,
              can_edit: false,
            }),
          ],
          counts: { upcoming: 249, attention: 3, history: 81 },
          page: params.page,
          page_size: params.pageSize,
          total_count: 1,
          total_pages: 1,
        }),
    );

    renderPlan('/tasks?section=delivery&view=upcoming&task_id=101');

    await waitFor(() => {
      expect(apiMocks.listEmailDeliveries).toHaveBeenCalledWith(
        expect.objectContaining({ taskId: 101, view: 'history' }),
        expect.any(AbortSignal),
      );
    });
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith(
      '邮件状态已更新',
      '已切换到“历史”查看。',
    );
  });

  it('does not overlap polling requests while the previous request is running', async () => {
    vi.useFakeTimers();
    apiMocks.listEmailDeliveries.mockImplementation(
      (_params: unknown, signal?: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener('abort', () => {
            reject(new DOMException('aborted', 'AbortError'));
          });
        }),
    );

    const { unmount } = renderPlan();
    expect(apiMocks.listEmailDeliveries).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(5_000);
      await Promise.resolve();
    });

    expect(apiMocks.listEmailDeliveries).toHaveBeenCalledTimes(1);
    unmount();
  });

  it('shows a cached view immediately while its background refresh is pending', async () => {
    let keepUpcomingRefreshPending = false;
    apiMocks.listEmailDeliveries.mockImplementation(
      (params: { page: number; pageSize: number; view: EmailDeliveryView }, signal?: AbortSignal) => {
        if (params.view === 'upcoming' && keepUpcomingRefreshPending) {
          return new Promise((_resolve, reject) => {
            signal?.addEventListener('abort', () => {
              reject(new DOMException('aborted', 'AbortError'));
            });
          });
        }
        return Promise.resolve(buildList({
          items: [buildItem({
            subject: params.view === 'upcoming' ? '已缓存的即将发送邮件' : '需处理邮件',
          })],
          page: params.page,
          page_size: params.pageSize,
        }));
      },
    );

    const { unmount } = renderPlan();
    expect((await screen.findAllByText('已缓存的即将发送邮件')).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /需处理\s*3/ }));
    expect((await screen.findAllByText('需处理邮件')).length).toBeGreaterThan(0);

    keepUpcomingRefreshPending = true;
    fireEvent.click(screen.getByRole('button', { name: /即将发送\s*250/ }));

    expect((await screen.findAllByText('已缓存的即将发送邮件')).length).toBeGreaterThan(0);
    expect(screen.queryByText('正在加载发送计划...')).not.toBeInTheDocument();
    unmount();
  });
});
