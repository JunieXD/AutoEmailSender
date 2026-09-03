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
  professor_archived_at: null,
  identity_id: 1,
  identity_name: '申请身份 A',
  sender_email: 'a@example.com',
  identity_retired_at: null,
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
  expected_updated_at: '2099-08-07T01:30:00.123456+00:00',
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
  counts: { upcoming: 250, history: 83 },
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
  globalThis.localStorage.clear();
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
    expect(screen.getByRole('button', { name: /待发送\s*250/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /历史\s*83/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /需处理/ })).not.toBeInTheDocument();
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

  it('uses the shared custom page-size behavior and accepts one item per page', async () => {
    renderPlan();
    await screen.findAllByText('博士申请咨询');

    fireEvent.click(screen.getByRole('button', { name: '每页数量' }));
    fireEvent.click(screen.getByRole('option', { name: '自定义' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: '自定义每页数量' }), {
      target: { value: '1' },
    });
    fireEvent.click(screen.getByRole('button', { name: '应用' }));

    await waitFor(() => {
      expect(apiMocks.listEmailDeliveries).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1, pageSize: 1 }),
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

    expect(screen.getByRole('button', { name: '发送计划排序' })).toHaveTextContent(
      '计划时间 ↑',
    );
    fireEvent.click(screen.getByRole('button', { name: '发送计划排序' }));
    expect(screen.getByRole('option', { name: '计划时间' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: '最晚计划优先' })).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: '切换计划时间排序方向' }),
    );

    await waitFor(() => {
      expect(apiMocks.listEmailDeliveries).toHaveBeenCalledWith(
        expect.objectContaining({ view: 'upcoming', sort: 'scheduled_desc' }),
        expect.any(AbortSignal),
      );
    });

    fireEvent.click(screen.getByRole('button', { name: /历史\s*83/ }));

    await waitFor(() => {
      expect(apiMocks.listEmailDeliveries).toHaveBeenCalledWith(
        expect.objectContaining({ view: 'history', sort: 'event_desc' }),
        expect.any(AbortSignal),
      );
    });
    expect(screen.getByRole('button', { name: '发送计划排序' })).toHaveTextContent(
      '记录时间 ↓',
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
        expected_updated_at: '2099-08-07T01:30:00.123456+00:00',
      });
    });
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith(
      '发送时间已更新',
      expect.stringContaining('张老师'),
    );
    expect(apiMocks.listEmailDeliveries.mock.calls.length).toBeGreaterThan(1);
  });

  it('keeps located details behind the reschedule dialog during polling', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderPlan('/tasks?section=delivery&view=upcoming&task_id=101');

    expect(
      await screen.findByRole('dialog', { name: '发送项详情' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '修改时间' }));

    expect(
      screen.getByRole('dialog', { name: '发送项详情' }),
    ).toBeInTheDocument();
    expect(screen.getByText('修改发送时间')).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(5_000);
      await Promise.resolve();
    });

    expect(
      screen.getByRole('dialog', { name: '发送项详情' }),
    ).toBeInTheDocument();
    expect(screen.getByText('修改发送时间')).toBeInTheDocument();
  });

  it('refreshes the open reschedule dialog version before submitting', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let requestCount = 0;
    apiMocks.listEmailDeliveries.mockImplementation(
      async (params: { page: number; pageSize: number }) => {
        requestCount += 1;
        return buildList({
          items: [
            buildItem({
              expected_updated_at:
                requestCount === 1
                  ? '2099-08-07T01:30:00.123456+00:00'
                  : '2099-08-07T01:31:00.654321+00:00',
            }),
          ],
          page: params.page,
          page_size: params.pageSize,
        });
      },
    );
    renderPlan('/tasks?section=delivery&view=upcoming&task_id=101');
    expect(
      await screen.findByRole('dialog', { name: '发送项详情' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '修改时间' }));

    await act(async () => {
      vi.advanceTimersByTime(5_000);
      await Promise.resolve();
    });
    fireEvent.change(screen.getByLabelText('新的发送时间'), {
      target: { value: '2099-08-09T10:30' },
    });
    fireEvent.click(screen.getByRole('button', { name: '确认修改' }));

    await waitFor(() => {
      expect(apiMocks.rescheduleEmailDelivery).toHaveBeenCalledWith(
        101,
        expect.objectContaining({
          expected_updated_at: '2099-08-07T01:31:00.654321+00:00',
        }),
      );
    });
  });

  it('closes the reschedule dialog if polling makes the action unavailable', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let requestCount = 0;
    apiMocks.listEmailDeliveries.mockImplementation(
      async (params: { page: number; pageSize: number }) => {
        requestCount += 1;
        return buildList({
          items: [
            buildItem(
              requestCount === 1
                ? {}
                : {
                    status: 'sending',
                    status_label: '正在发送',
                    can_reschedule: false,
                    can_cancel: false,
                    can_send_now: false,
                  },
            ),
          ],
          page: params.page,
          page_size: params.pageSize,
        });
      },
    );
    renderPlan('/tasks?section=delivery&view=upcoming&task_id=101');
    expect(
      await screen.findByRole('dialog', { name: '发送项详情' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '修改时间' }));
    expect(screen.getByText('修改发送时间')).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(5_000);
      await Promise.resolve();
    });

    expect(screen.queryByText('修改发送时间')).not.toBeInTheDocument();
    expect(
      screen.getByRole('dialog', { name: '发送项详情' }),
    ).toBeInTheDocument();
    expect(screen.getAllByText('正在发送').length).toBeGreaterThan(0);
  });

  it('does not reopen a dismissed located detail on the next poll', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderPlan('/tasks?section=delivery&view=upcoming&task_id=101');

    expect(
      await screen.findByRole('dialog', { name: '发送项详情' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭发送项详情' }));
    expect(
      screen.queryByRole('dialog', { name: '发送项详情' }),
    ).not.toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(5_000);
      await Promise.resolve();
    });

    expect(
      screen.queryByRole('dialog', { name: '发送项详情' }),
    ).not.toBeInTheDocument();
  });

  it('submits a reschedule at most once while the request is pending', async () => {
    let resolveReschedule: ((value: { ok: boolean; task_id: number; message: string }) => void) | undefined;
    apiMocks.rescheduleEmailDelivery.mockImplementation(
      () => new Promise((resolve) => {
        resolveReschedule = resolve;
      }),
    );
    renderPlan();
    await screen.findAllByText('博士申请咨询');

    fireEvent.click(screen.getAllByRole('button', { name: '改期' })[0]);
    fireEvent.change(screen.getByLabelText('新的发送时间'), {
      target: { value: '2099-08-09T10:30' },
    });
    const submit = screen.getByRole('button', { name: '确认修改' });
    fireEvent.click(submit);
    fireEvent.click(submit);

    expect(apiMocks.rescheduleEmailDelivery).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveReschedule?.({ ok: true, task_id: 101, message: '发送时间已更新' });
      await Promise.resolve();
    });
  });

  it('uses the lossless item version when canceling a manual schedule', async () => {
    renderPlan();
    await screen.findAllByText('博士申请咨询');
    fireEvent.click(
      screen.getByRole('button', { name: '查看 张老师 的发送详情' }),
    );
    fireEvent.click(screen.getByRole('button', { name: '取消定时' }));

    await waitFor(() => {
      expect(apiMocks.cancelEmailDelivery).toHaveBeenCalledWith(
        101,
        '2099-08-07T01:30:00.123456+00:00',
      );
    });
  });

  it('uses the lossless item version when sending immediately', async () => {
    renderPlan();
    await screen.findAllByText('博士申请咨询');
    fireEvent.click(
      screen.getByRole('button', { name: '查看 张老师 的发送详情' }),
    );
    fireEvent.click(screen.getByRole('button', { name: '立即发送' }));

    await waitFor(() => {
      expect(apiMocks.sendEmailDeliveryNow).toHaveBeenCalledWith(
        101,
        '2099-08-07T01:30:00.123456+00:00',
      );
    });
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
          counts: { upcoming: 249, history: 84 },
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

  it.each([
    ['removed_from_batch', '已从批量任务移除'],
    ['professor_archived', '导师已移入回收站'],
    ['identity_retired', '发件身份已删除'],
    ['llm_profile_retired', '模型配置已删除'],
  ] as const)('shows the %s deletion outcome in history', async (status, label) => {
    apiMocks.listEmailDeliveries.mockResolvedValue(
      buildList({
        items: [
          buildItem({
            status,
            status_label: label,
            status_description: `${label}，因此这封邮件未发送`,
            professor_archived_at:
              status === 'professor_archived' ? '2099-08-08T02:00:00Z' : null,
            identity_retired_at:
              status === 'identity_retired' ? '2099-08-08T02:00:00Z' : null,
            scheduled_at: null,
            can_reschedule: false,
            can_cancel: false,
            can_send_now: false,
            can_edit: false,
          }),
        ],
        counts: { upcoming: 0, history: 1 },
        total_count: 1,
        total_pages: 1,
      }),
    );

    renderPlan('/tasks?section=delivery&view=history');

    expect((await screen.findAllByText(label)).length).toBeGreaterThan(0);
    fireEvent.click(
      screen.getByRole('button', { name: '查看 张老师 的发送详情' }),
    );
    if (status === 'professor_archived') {
      expect(
        screen.getByRole('button', { name: '查看回收站导师' }),
      ).toBeInTheDocument();
    }
    if (status === 'identity_retired') {
      expect(
        screen.queryByRole('button', { name: '打开工作区' }),
      ).not.toBeInTheDocument();
    }
  });

  it('keeps a sent result while linking a subsequently archived professor to the recycle bin', async () => {
    apiMocks.listEmailDeliveries.mockResolvedValue(
      buildList({
        items: [
          buildItem({
            status: 'sent',
            status_label: '已发送',
            status_description: '邮件已成功交给发件服务器',
            professor_archived_at: '2099-08-09T02:00:00Z',
            scheduled_at: null,
            sent_at: '2099-08-08T02:00:00Z',
            can_reschedule: false,
            can_cancel: false,
            can_send_now: false,
            can_edit: false,
          }),
        ],
        counts: { upcoming: 0, history: 1 },
        total_count: 1,
        total_pages: 1,
      }),
    );

    renderPlan('/tasks?section=delivery&view=history');

    expect((await screen.findAllByText('已发送')).length).toBeGreaterThan(0);
    fireEvent.click(
      screen.getByRole('button', { name: '查看 张老师 的发送详情' }),
    );
    expect(
      screen.getByRole('button', { name: '查看回收站导师' }),
    ).toBeInTheDocument();
  });

  it('keeps a sent result without offering an invalid workspace for a retired identity', async () => {
    apiMocks.listEmailDeliveries.mockResolvedValue(
      buildList({
        items: [
          buildItem({
            status: 'sent',
            status_label: '已发送',
            status_description: '邮件已成功交给发件服务器',
            identity_retired_at: '2099-08-09T02:00:00Z',
            scheduled_at: null,
            sent_at: '2099-08-08T02:00:00Z',
            can_reschedule: false,
            can_cancel: false,
            can_send_now: false,
            can_edit: false,
          }),
        ],
        counts: { upcoming: 0, history: 1 },
        total_count: 1,
        total_pages: 1,
      }),
    );

    renderPlan('/tasks?section=delivery&view=history');

    expect((await screen.findAllByText('已发送')).length).toBeGreaterThan(0);
    fireEvent.click(
      screen.getByRole('button', { name: '查看 张老师 的发送详情' }),
    );
    expect(
      screen.queryByRole('button', { name: '打开工作区' }),
    ).not.toBeInTheDocument();
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
            subject: params.view === 'upcoming' ? '已缓存的待发送邮件' : '历史邮件',
          })],
          page: params.page,
          page_size: params.pageSize,
        }));
      },
    );

    const { unmount } = renderPlan();
    expect((await screen.findAllByText('已缓存的待发送邮件')).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /历史\s*83/ }));
    expect((await screen.findAllByText('历史邮件')).length).toBeGreaterThan(0);

    keepUpcomingRefreshPending = true;
    fireEvent.click(screen.getByRole('button', { name: /待发送\s*250/ }));

    expect((await screen.findAllByText('已缓存的待发送邮件')).length).toBeGreaterThan(0);
    expect(screen.queryByText('正在加载发送计划…')).not.toBeInTheDocument();
    unmount();
  });

  it('keeps the actual draft failure reason in details without exposing it in the plan list', async () => {
    const rawError = '模型返回的 JSON 结构无效: 3 validation errors for DraftResponse';
    apiMocks.listEmailDeliveries.mockResolvedValue(
      buildList({
        items: [
          buildItem({
            source: 'batch',
            batch_task_id: 8,
            batch_task_name: '秋季申请批次',
            status: 'draft_failed',
            status_label: '草稿生成失败',
            status_description: '生成邮件草稿时失败，因此未进入发送流程',
            last_error: rawError,
            can_reschedule: false,
            can_cancel: false,
            can_send_now: false,
          }),
        ],
        counts: { upcoming: 0, history: 1 },
        total_count: 1,
        total_pages: 1,
      }),
    );

    renderPlan('/tasks?section=delivery&view=attention&sort=updated_desc');
    await screen.findAllByText('草稿生成失败');
    expect(apiMocks.listEmailDeliveries).toHaveBeenCalledWith(
      expect.objectContaining({ view: 'history', sort: 'event_desc' }),
      expect.any(AbortSignal),
    );
    expect(screen.getByRole('button', { name: /历史\s*1/ })).toHaveAttribute(
      'class',
      expect.stringContaining('bg-primary'),
    );
    expect(screen.queryByRole('button', { name: /需处理/ })).not.toBeInTheDocument();
    expect(screen.queryByText(rawError)).not.toBeInTheDocument();
    screen.getAllByText('博士申请咨询').forEach((subject) => {
      fireEvent.click(subject);
    });
    expect(screen.queryByRole('dialog', { name: '发送项详情' })).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: '查看 张老师 的发送详情' }),
    );

    const detail = screen.getByRole('dialog', { name: '发送项详情' });
    expect(detail).toHaveClass('sm:max-w-4xl', 'sm:rounded-3xl');
    expect(screen.getByText('失败原因')).toBeInTheDocument();
    expect(screen.getByText(rawError)).toBeInTheDocument();
    expect(screen.queryByText('可前往所属批次查看后续处理方式')).not.toBeInTheDocument();
    expect(screen.queryByText('该导师的批量发送已取消')).not.toBeInTheDocument();
  });
});
