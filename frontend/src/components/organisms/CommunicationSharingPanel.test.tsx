import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { IdentityCommunicationGroupDTO, IdentityDTO } from '@/types';
import { CommunicationSharingPanel } from './CommunicationSharingPanel';

const apiMocks = vi.hoisted(() => ({
  createCommunicationGroup: vi.fn(),
  deleteCommunicationGroup: vi.fn(),
  updateCommunicationGroup: vi.fn(),
}));
const confirmMock = vi.hoisted(() => vi.fn());
const notificationMocks = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifyFormErrors: vi.fn(),
  notifySuccess: vi.fn(),
}));
const refreshSelections = vi.hoisted(() => vi.fn());

let selectionState: {
  identities: IdentityDTO[];
  communicationGroups: IdentityCommunicationGroupDTO[];
  selectedIdentity: IdentityDTO | null;
  refreshSelections: typeof refreshSelections;
};

vi.mock('@/lib/api/communicationGroups', () => apiMocks);

vi.mock('@/context/SelectionContext', () => ({
  useSelectionContext: () => selectionState,
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

const makeIdentity = (
  id: number,
  profileName: string,
  communicationGroupId: number | null,
): IdentityDTO => ({
  id,
  name: profileName,
  profile_name: profileName,
  sender_name: profileName,
  email_address: `${profileName.toLowerCase()}@example.com`,
  smtp_host: 'smtp.example.com',
  smtp_port: 465,
  smtp_username: `${profileName.toLowerCase()}@example.com`,
  smtp_password: 'secret',
  imap_host: null,
  imap_port: null,
  imap_username: null,
  imap_password: null,
  default_language: 'zh-CN',
  outreach_generation_mode: 'template',
  outreach_template_subject: null,
  outreach_template_body_text: null,
  outreach_template_body_html: null,
  current_primary_material_id: null,
  current_primary_material: null,
  communication_group_id: communicationGroupId,
  match_threshold: null,
  daily_send_limit: null,
  send_interval_min: null,
  send_interval_max: null,
  same_domain_cooldown_minutes: null,
  is_default: id === 1,
  materials: [],
  created_at: '2026-07-21T00:00:00Z',
  updated_at: '2026-07-21T00:00:00Z',
});

const makeGroup = (
  id: number,
  identities: IdentityDTO[],
  matchSourceIdentityId: number | null = null,
): IdentityCommunicationGroupDTO => ({
  id,
  members: identities.map((identity) => ({
    id: identity.id,
    profile_name: identity.profile_name,
    email_address: identity.email_address,
    is_default: identity.is_default,
  })),
  match_source_identity_id: matchSourceIdentityId,
  created_at: '2026-07-21T00:00:00Z',
  updated_at: '2026-07-21T00:00:00Z',
});

const expandPanel = () => {
  fireEvent.click(
    screen.getByRole('button', { name: '展开通信记录共享' }),
  );
};

describe('CommunicationSharingPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const identityA = makeIdentity(1, 'A', null);
    const identityB = makeIdentity(2, 'B', null);
    selectionState = {
      identities: [identityA, identityB],
      communicationGroups: [],
      selectedIdentity: identityA,
      refreshSelections,
    };
    refreshSelections.mockResolvedValue(undefined);
    confirmMock.mockResolvedValue(true);
    apiMocks.createCommunicationGroup.mockResolvedValue(
      makeGroup(10, [identityA, identityB]),
    );
  });

  it('uses the same collapsed summary pattern as the other settings cards', () => {
    render(<CommunicationSharingPanel />);

    const toggle = screen.getByRole('button', { name: '展开通信记录共享' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText('未创建共享组')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '创建共享组' }),
    ).not.toBeInTheDocument();

    expandPanel();

    expect(
      screen.getByRole('button', { name: '收起通信记录共享' }),
    ).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: '创建共享组' })).toBeInTheDocument();
  });

  it('creates a group with the current identity preselected', async () => {
    render(<CommunicationSharingPanel />);

    expandPanel();
    fireEvent.click(screen.getByRole('button', { name: '创建共享组' }));
    expect(screen.getByRole('checkbox', { name: /A/ })).toBeChecked();
    fireEvent.click(screen.getByRole('checkbox', { name: /B/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存共享组' }));

    await waitFor(() => {
      expect(apiMocks.createCommunicationGroup).toHaveBeenCalledWith({
        identity_ids: [1, 2],
        match_source_identity_id: null,
        confirm_merge_existing_groups: false,
      });
      expect(refreshSelections).toHaveBeenCalledTimes(1);
      expect(notificationMocks.notifySuccess).toHaveBeenCalled();
    });
  });

  it('requires explicit confirmation before merging another group', async () => {
    const identityA = makeIdentity(1, 'A', 10);
    const identityB = makeIdentity(2, 'B', 10);
    const identityC = makeIdentity(3, 'C', 20);
    const identityD = makeIdentity(4, 'D', 20);
    const groupAB = makeGroup(10, [identityA, identityB]);
    const groupCD = makeGroup(20, [identityC, identityD]);
    selectionState = {
      identities: [identityA, identityB, identityC, identityD],
      communicationGroups: [groupAB, groupCD],
      selectedIdentity: identityA,
      refreshSelections,
    };
    apiMocks.updateCommunicationGroup.mockResolvedValue(
      makeGroup(10, [identityA, identityB, identityC, identityD]),
    );

    render(<CommunicationSharingPanel />);

    expandPanel();
    fireEvent.click(screen.getByRole('button', { name: '编辑 A、B' }));
    fireEvent.click(
      screen.getByRole('checkbox', { name: /^Cc@example\.com/ }),
    );
    fireEvent.click(screen.getByRole('button', { name: '保存共享组' }));

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '合并已有通信共享组？' }),
      );
      expect(apiMocks.updateCommunicationGroup).toHaveBeenCalledWith(10, {
        identity_ids: [1, 2, 3],
        match_source_identity_id: null,
        confirm_merge_existing_groups: true,
      });
    });
  });

  it('lets the user choose one member as the shared match source', async () => {
    const identityA = makeIdentity(1, 'A', 10);
    const identityB = makeIdentity(2, 'B', 10);
    const group = makeGroup(10, [identityA, identityB], identityA.id);
    selectionState = {
      identities: [identityA, identityB],
      communicationGroups: [group],
      selectedIdentity: identityB,
      refreshSelections,
    };
    apiMocks.updateCommunicationGroup.mockResolvedValue(
      makeGroup(10, [identityA, identityB], identityB.id),
    );

    render(<CommunicationSharingPanel />);
    expandPanel();

    expect(screen.getByText('匹配度统一依据 A')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '编辑 A、B' }));
    expect(screen.getByRole('radio', { name: /统一使用 A/ })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: /统一使用 B/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存共享组' }));

    await waitFor(() => {
      expect(apiMocks.updateCommunicationGroup).toHaveBeenCalledWith(10, {
        identity_ids: [1, 2],
        match_source_identity_id: 2,
        confirm_merge_existing_groups: false,
      });
    });
  });

  it('falls back to independent matching when the source member is removed', () => {
    const identityA = makeIdentity(1, 'A', 10);
    const identityB = makeIdentity(2, 'B', 10);
    const identityC = makeIdentity(3, 'C', 10);
    const group = makeGroup(10, [identityA, identityB, identityC], identityA.id);
    selectionState = {
      identities: [identityA, identityB, identityC],
      communicationGroups: [group],
      selectedIdentity: identityB,
      refreshSelections,
    };

    render(<CommunicationSharingPanel />);
    expandPanel();
    fireEvent.click(screen.getByRole('button', { name: '编辑 A、B 等 3 个身份' }));
    fireEvent.click(screen.getByRole('checkbox', { name: /^Aa@example\.com/ }));

    expect(screen.getByRole('radio', { name: /各身份独立/ })).toBeChecked();
    expect(screen.queryByRole('radio', { name: /统一使用 A/ })).not.toBeInTheDocument();
  });

  it('dissolves a group without deleting identities', async () => {
    const identityA = makeIdentity(1, 'A', 10);
    const identityB = makeIdentity(2, 'B', 10);
    const group = makeGroup(10, [identityA, identityB]);
    selectionState = {
      identities: [identityA, identityB],
      communicationGroups: [group],
      selectedIdentity: identityA,
      refreshSelections,
    };
    apiMocks.deleteCommunicationGroup.mockResolvedValue(undefined);

    render(<CommunicationSharingPanel />);
    expandPanel();
    fireEvent.click(screen.getByRole('button', { name: '解散 A、B' }));

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith(
        expect.objectContaining({
          description: expect.stringContaining('不会删除任何身份、任务或通信记录'),
        }),
      );
      expect(apiMocks.deleteCommunicationGroup).toHaveBeenCalledWith(10);
      expect(refreshSelections).toHaveBeenCalledTimes(1);
    });
  });

  it('keeps the selected members visible after a save failure', async () => {
    apiMocks.createCommunicationGroup.mockRejectedValue(new Error('数据库写入失败'));
    render(<CommunicationSharingPanel />);

    expandPanel();
    fireEvent.click(screen.getByRole('button', { name: '创建共享组' }));
    fireEvent.click(screen.getByRole('checkbox', { name: /B/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存共享组' }));

    await waitFor(() => {
      expect(notificationMocks.notifyError).toHaveBeenCalledWith(
        '保存通信共享组失败',
        '数据库写入失败',
      );
    });
    expect(screen.getByRole('checkbox', { name: /A/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /B/ })).toBeChecked();
  });
});
