import { act, render, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  DesktopAgentUiHandoff,
  DesktopAgentUiHandoffAcknowledgeRequest,
  DesktopBridge,
} from '@/types/desktop';
import { AgentUiHandoffProvider } from './AgentUiHandoffProvider';
import { writeStoredAgentUiHandoffs } from './storage';
import {
  validateAgentUiHandoff,
  type AgentUiHandoffSurfaceHandler,
} from './types';
import { useAgentUiHandoffSurface } from './useAgentUiHandoffSurface';

const notificationMock = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
  notifyWarning: vi.fn(),
}));
const selectionMock = vi.hoisted(() => ({
  identities: [{ id: 8 }],
  loading: false,
  selectedIdentityId: 8 as number | null,
  setSelectedIdentityId: vi.fn(),
}));
const draftGuardMock = vi.hoisted(() => ({
  requestWorkspaceDraftGuard: vi.fn(async () => true),
}));

vi.mock('@/context/NotificationContext', () => ({
  useNotification: () => notificationMock,
}));
vi.mock('@/context/SelectionContext', () => ({
  useSelectionContext: () => selectionMock,
}));
vi.mock('@/context/useWorkspaceDraftGuard', () => ({
  useWorkspaceDraftGuard: () => draftGuardMock,
}));

const buildManagementHandoff = (
  overrides: Partial<DesktopAgentUiHandoff> = {},
): DesktopAgentUiHandoff => ({
  handoffId: 'uih_provider_test',
  schemaVersion: 1,
  surface: 'professors.management',
  route: '/professors',
  status: 'claimed',
  selectionCount: 2,
  selectionFingerprint: 'fingerprint',
  uiEffects: ['focus_window', 'navigate', 'replace_selection'],
  result: null,
  failureMessage: null,
  deliveryAttempts: 1,
  expiresAt: new Date(Date.now() + 60_000).toISOString(),
  claimedAt: new Date().toISOString(),
  awaitingUserAt: null,
  appliedAt: null,
  failedAt: null,
  canceledAt: null,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  availableActions: ['read', 'wait', 'cancel'],
  consumerId: 'desktop:test',
  claimExpiresAt: new Date(Date.now() + 30_000).toISOString(),
  payload: {
    kind: 'professor_selection',
    resource: 'professors',
    selection_mode: 'replace',
    display: 'selected_only',
    archive_scope: 'active',
    matched_count: 2,
    excluded_count: 0,
    ui_effects: ['focus_window', 'navigate', 'replace_selection'],
  },
  selectedIds: [11, 12],
  ...overrides,
});

const buildDraftHandoff = (
  overrides: Partial<DesktopAgentUiHandoff> = {},
): DesktopAgentUiHandoff => ({
  ...buildManagementHandoff(),
  handoffId: 'uih_draft_provider_test',
  surface: 'draft.workspace',
  route: '/workspace/21',
  selectionCount: 1,
  selectionFingerprint: null,
  selectedIds: [],
  payload: {
    kind: 'task_context',
    resource: 'tasks',
    task_id: 31,
    professor_id: 21,
    identity_id: 8,
    ui_effects: ['focus_window', 'navigate', 'focus_resource'],
  },
  ...overrides,
});

const Surface = ({
  handler,
}: {
  handler: AgentUiHandoffSurfaceHandler;
}) => {
  useAgentUiHandoffSurface('professors.management', handler);
  return <div>surface</div>;
};

const DraftSurface = ({
  handler,
}: {
  handler: AgentUiHandoffSurfaceHandler;
}) => {
  useAgentUiHandoffSurface('draft.workspace', handler);
  return <div>draft surface</div>;
};

describe('AgentUiHandoffProvider', () => {
  let deliver: ((handoff: DesktopAgentUiHandoff) => void) | null;
  let acknowledge: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    selectionMock.selectedIdentityId = 8;
    draftGuardMock.requestWorkspaceDraftGuard.mockResolvedValue(true);
    deliver = null;
    acknowledge = vi.fn(
      async (request: DesktopAgentUiHandoffAcknowledgeRequest) => ({
        handoffId: request.handoffId,
        status: request.status,
      }),
    );
    window.autoEmailSender = {
      getVersion: vi.fn(),
      checkForUpdate: vi.fn(),
      downloadUpdate: vi.fn(),
      switchToFullDownload: vi.fn(),
      quitAndInstall: vi.fn(),
      onUpdateStatus: vi.fn(() => () => undefined),
      onAgentUiHandoff: (callback) => {
        deliver = callback;
        return () => {
          deliver = null;
        };
      },
      acknowledgeAgentUiHandoff: acknowledge,
    } as DesktopBridge;
  });

  it('applies a validated handoff once and acknowledges it', async () => {
    const handler = vi.fn(async () => ({
      status: 'applied' as const,
      result: { selected_count: 2 },
    }));
    render(
      <MemoryRouter initialEntries={['/professors']}>
        <AgentUiHandoffProvider>
          <Surface handler={handler} />
        </AgentUiHandoffProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(deliver).not.toBeNull());

    act(() => deliver?.(buildManagementHandoff()));

    await waitFor(() => expect(handler).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(acknowledge).toHaveBeenCalledWith(
        expect.objectContaining({
          handoffId: 'uih_provider_test',
          status: 'applied',
          result: { selected_count: 2 },
        }),
      ),
    );
    expect(notificationMock.notifySuccess).toHaveBeenCalledWith(
      'Agent 已选择 2 位导师',
      expect.stringContaining('仅显示'),
    );
    expect(window.sessionStorage.getItem('agent_ui_handoffs_v1')).toBeNull();
  });

  it('keeps an applied result until a transient acknowledgement failure recovers', async () => {
    acknowledge
      .mockRejectedValueOnce(new Error('backend restarting'))
      .mockImplementationOnce(
        async (request: DesktopAgentUiHandoffAcknowledgeRequest) => ({
          handoffId: request.handoffId,
          status: request.status,
        }),
      );
    const handler = vi.fn(async () => ({ status: 'applied' as const }));
    render(
      <MemoryRouter initialEntries={['/professors']}>
        <AgentUiHandoffProvider>
          <Surface handler={handler} />
        </AgentUiHandoffProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(deliver).not.toBeNull());
    act(() => deliver?.(buildManagementHandoff()));

    await waitFor(() => expect(acknowledge).toHaveBeenCalledTimes(1));
    expect(window.sessionStorage.getItem('agent_ui_handoffs_v1')).not.toBeNull();
    await waitFor(() => expect(acknowledge).toHaveBeenCalledTimes(2), {
      timeout: 2_500,
    });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem('agent_ui_handoffs_v1')).toBeNull();
  });

  it('guards a same-route workspace context change before applying it', async () => {
    draftGuardMock.requestWorkspaceDraftGuard.mockResolvedValueOnce(false);
    const handler = vi.fn(async () => ({ status: 'applied' as const }));
    render(
      <MemoryRouter initialEntries={['/workspace/21']}>
        <AgentUiHandoffProvider>
          <DraftSurface handler={handler} />
        </AgentUiHandoffProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(deliver).not.toBeNull());

    act(() => deliver?.(buildDraftHandoff()));

    await waitFor(() =>
      expect(acknowledge).toHaveBeenCalledWith(
        expect.objectContaining({
          handoffId: 'uih_draft_provider_test',
          status: 'awaiting_user',
        }),
      ),
    );
    expect(draftGuardMock.requestWorkspaceDraftGuard).toHaveBeenCalledTimes(1);
    expect(draftGuardMock.requestWorkspaceDraftGuard).toHaveBeenCalledWith(
      undefined,
    );
    expect(handler).not.toHaveBeenCalled();
  });

  it('stops retrying an acknowledgement superseded by backend state', async () => {
    acknowledge.mockResolvedValueOnce({
      handoffId: 'uih_provider_test',
      status: 'canceled',
    });
    const handler = vi.fn(async () => ({ status: 'applied' as const }));
    render(
      <MemoryRouter initialEntries={['/professors']}>
        <AgentUiHandoffProvider>
          <Surface handler={handler} />
        </AgentUiHandoffProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(deliver).not.toBeNull());
    act(() => deliver?.(buildManagementHandoff()));

    await waitFor(() =>
      expect(notificationMock.notifyWarning).toHaveBeenCalledWith(
        'Agent 界面交接状态已变化',
        expect.stringContaining('canceled'),
      ),
    );
    expect(acknowledge).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem('agent_ui_handoffs_v1')).toBeNull();
  });

  it('omits an oversized acknowledgement result instead of retrying forever', async () => {
    const handler = vi.fn(async () => ({
      status: 'applied' as const,
      result: { diagnostic: 'x'.repeat(17_000) },
    }));
    render(
      <MemoryRouter initialEntries={['/professors']}>
        <AgentUiHandoffProvider>
          <Surface handler={handler} />
        </AgentUiHandoffProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(deliver).not.toBeNull());
    act(() => deliver?.(buildManagementHandoff()));

    await waitFor(() => expect(acknowledge).toHaveBeenCalledTimes(1));
    expect(acknowledge).toHaveBeenCalledWith({
      handoffId: 'uih_provider_test',
      status: 'applied',
    });
  });

  it('does not apply a duplicate delivery more than once', async () => {
    const handler = vi.fn(async () => ({ status: 'applied' as const }));
    render(
      <MemoryRouter initialEntries={['/professors']}>
        <AgentUiHandoffProvider>
          <Surface handler={handler} />
        </AgentUiHandoffProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(deliver).not.toBeNull());
    const handoff = buildManagementHandoff();

    act(() => {
      deliver?.(handoff);
      deliver?.({ ...handoff, deliveryAttempts: 2 });
    });

    await waitFor(() => expect(acknowledge).toHaveBeenCalledTimes(1));
    expect(handler).toHaveBeenCalledTimes(1);
    expect(notificationMock.notifySuccess).toHaveBeenCalledTimes(1);
  });

  it('reports awaiting_user when the workspace draft guard is declined', async () => {
    draftGuardMock.requestWorkspaceDraftGuard.mockResolvedValueOnce(false);
    render(
      <MemoryRouter initialEntries={['/workspace/9']}>
        <AgentUiHandoffProvider>
          <div>workspace</div>
        </AgentUiHandoffProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(deliver).not.toBeNull());
    act(() => deliver?.(buildManagementHandoff()));

    await waitFor(() =>
      expect(acknowledge).toHaveBeenCalledWith(
        expect.objectContaining({
          handoffId: 'uih_provider_test',
          status: 'awaiting_user',
          result: expect.objectContaining({ reason: 'workspace_draft_guard' }),
        }),
      ),
    );
    expect(draftGuardMock.requestWorkspaceDraftGuard).toHaveBeenCalledWith({
      nextPath: '/professors',
    });
  });

  it('checks the workspace draft guard only once before navigating and applying', async () => {
    const handler = vi.fn(async () => ({ status: 'applied' as const }));
    render(
      <MemoryRouter initialEntries={['/workspace/9']}>
        <AgentUiHandoffProvider>
          <Routes>
            <Route path="/workspace/:id" element={<div>workspace</div>} />
            <Route path="/professors" element={<Surface handler={handler} />} />
          </Routes>
        </AgentUiHandoffProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(deliver).not.toBeNull());
    act(() => deliver?.(buildManagementHandoff()));

    await waitFor(() => expect(handler).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(acknowledge).toHaveBeenCalledTimes(1));
    expect(draftGuardMock.requestWorkspaceDraftGuard).toHaveBeenCalledTimes(1);
    expect(draftGuardMock.requestWorkspaceDraftGuard).toHaveBeenCalledWith({
      nextPath: '/professors',
    });
  });

  it('retries a persisted acknowledgement after a renderer reload', async () => {
    const handoff = validateAgentUiHandoff(buildManagementHandoff());
    writeStoredAgentUiHandoffs([
      {
        handoff,
        acknowledgement: {
          handoffId: handoff.handoffId,
          status: 'applied',
          result: { recovered_after_reload: true },
        },
      },
    ]);

    render(
      <MemoryRouter initialEntries={['/professors']}>
        <AgentUiHandoffProvider>
          <div>surface</div>
        </AgentUiHandoffProvider>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(acknowledge).toHaveBeenCalledWith(
        expect.objectContaining({ result: { recovered_after_reload: true } }),
      ),
    );
    expect(window.sessionStorage.getItem('agent_ui_handoffs_v1')).toBeNull();
  });

  it('drops an expired cached acknowledgement without applying or retrying it', async () => {
    const handoff = validateAgentUiHandoff(
      buildManagementHandoff({
        expiresAt: new Date(Date.now() - 1_000).toISOString(),
      }),
    );
    writeStoredAgentUiHandoffs([{
      handoff,
      acknowledgement: {
        handoffId: handoff.handoffId,
        status: 'applied',
      },
    }]);
    const handler = vi.fn(async () => ({ status: 'applied' as const }));

    render(
      <MemoryRouter initialEntries={['/professors']}>
        <AgentUiHandoffProvider>
          <Surface handler={handler} />
        </AgentUiHandoffProvider>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(notificationMock.notifyWarning).toHaveBeenCalledWith(
        'Agent 界面交接已过期',
        expect.any(String),
      ),
    );
    expect(handler).not.toHaveBeenCalled();
    expect(acknowledge).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem('agent_ui_handoffs_v1')).toBeNull();
  });
});
