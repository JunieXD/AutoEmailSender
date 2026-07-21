import { useMemo, useState } from 'react';
import { Link2, Loader2, Pencil, Plus, Trash2, X } from 'lucide-react';
import { useNotification } from '@/context/NotificationContext';
import { useSelectionContext } from '@/context/SelectionContext';
import {
  createCommunicationGroup,
  deleteCommunicationGroup,
  updateCommunicationGroup,
} from '@/lib/api/communicationGroups';
import { ApiError } from '@/lib/api/client';
import { useConfirmDialog } from '@/lib/useConfirmDialog';
import type { IdentityCommunicationGroupDTO, IdentityDTO } from '@/types';

type GroupEditorId = number | 'new' | null;

const getIdentityName = (identity: Pick<IdentityDTO, 'name' | 'profile_name'>) =>
  identity.profile_name || identity.name;

const getGroupLabel = (group: IdentityCommunicationGroupDTO) => {
  const names = group.members.map((member) => member.profile_name);
  if (names.length <= 2) {
    return names.join('、');
  }
  return `${names.slice(0, 2).join('、')} 等 ${names.length} 个身份`;
};

export const CommunicationSharingPanel = () => {
  const {
    identities,
    communicationGroups = [],
    selectedIdentity,
    refreshSelections,
  } = useSelectionContext();
  const { notifyError, notifyFormErrors, notifySuccess } = useNotification();
  const { confirm, dialog } = useConfirmDialog();
  const [editorId, setEditorId] = useState<GroupEditorId>(null);
  const [selectedMemberIds, setSelectedMemberIds] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [deletingGroupId, setDeletingGroupId] = useState<number | null>(null);

  const editingGroup =
    typeof editorId === 'number'
      ? communicationGroups.find((group) => group.id === editorId) ?? null
      : null;
  const selectedMemberIdSet = useMemo(
    () => new Set(selectedMemberIds),
    [selectedMemberIds],
  );

  const beginCreate = () => {
    setEditorId('new');
    setSelectedMemberIds(selectedIdentity ? [selectedIdentity.id] : []);
  };

  const beginEdit = (group: IdentityCommunicationGroupDTO) => {
    setEditorId(group.id);
    setSelectedMemberIds(group.members.map((member) => member.id));
  };

  const closeEditor = () => {
    if (saving) {
      return;
    }
    setEditorId(null);
    setSelectedMemberIds([]);
  };

  const toggleIdentity = (identityId: number) => {
    setSelectedMemberIds((current) =>
      current.includes(identityId)
        ? current.filter((id) => id !== identityId)
        : [...current, identityId],
    );
  };

  const getConflictingGroups = () => {
    const currentGroupId = typeof editorId === 'number' ? editorId : null;
    const conflictIds = new Set(
      identities
        .filter((identity) => selectedMemberIdSet.has(identity.id))
        .map((identity) => identity.communication_group_id)
        .filter(
          (groupId): groupId is number =>
            groupId !== null && groupId !== currentGroupId,
        ),
    );
    return communicationGroups.filter((group) => conflictIds.has(group.id));
  };

  const requestMergeConfirmation = async (
    conflictingGroups: IdentityCommunicationGroupDTO[],
  ) => {
    const affectedMembers = conflictingGroups
      .flatMap((group) => group.members)
      .map((member) => `${member.profile_name}（${member.email_address}）`)
      .join('、');
    return confirm({
      title: '合并已有通信共享组？',
      description: affectedMembers
        ? `所选身份已属于其他共享组。确认后会一并合并这些成员：${affectedMembers}`
        : '所选身份已属于其他共享组。确认后会把相关组的全部成员合并到一起。',
      confirmLabel: '确认合并',
      cancelLabel: '返回检查',
      tone: 'danger',
    });
  };

  const persistGroup = async (confirmMergeExistingGroups: boolean) => {
    const payload = {
      identity_ids: selectedMemberIds,
      confirm_merge_existing_groups: confirmMergeExistingGroups,
    };
    return typeof editorId === 'number'
      ? updateCommunicationGroup(editorId, payload)
      : createCommunicationGroup(payload);
  };

  const saveGroup = async () => {
    if (selectedMemberIds.length < 2) {
      notifyFormErrors('请检查共享成员', ['通信共享组至少需要两个身份']);
      return;
    }

    setSaving(true);
    try {
      const conflictingGroups = getConflictingGroups();
      let confirmMergeExistingGroups = false;
      if (conflictingGroups.length > 0) {
        confirmMergeExistingGroups = await requestMergeConfirmation(conflictingGroups);
        if (!confirmMergeExistingGroups) {
          return;
        }
      }

      try {
        await persistGroup(confirmMergeExistingGroups);
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 409 || confirmMergeExistingGroups) {
          throw error;
        }
        const confirmed = await requestMergeConfirmation([]);
        if (!confirmed) {
          return;
        }
        await persistGroup(true);
      }

      await refreshSelections();
      setEditorId(null);
      setSelectedMemberIds([]);
      notifySuccess(
        editorId === 'new' ? '通信共享组已创建' : '通信共享组已更新',
        '共享范围已经应用到首页、工作区和统计面板。',
      );
    } catch (error) {
      notifyError(
        '保存通信共享组失败',
        error instanceof Error ? error.message : '保存通信共享组失败',
      );
    } finally {
      setSaving(false);
    }
  };

  const dissolveGroup = async (group: IdentityCommunicationGroupDTO) => {
    const confirmed = await confirm({
      title: `解散“${getGroupLabel(group)}”共享组？`,
      description: '解散只会停止合并展示，不会删除任何身份、任务或通信记录。',
      confirmLabel: '解散共享组',
      cancelLabel: '取消',
      tone: 'danger',
    });
    if (!confirmed) {
      return;
    }

    setDeletingGroupId(group.id);
    try {
      await deleteCommunicationGroup(group.id);
      await refreshSelections();
      if (editorId === group.id) {
        setEditorId(null);
        setSelectedMemberIds([]);
      }
      notifySuccess('通信共享组已解散', '身份和原有通信记录均已保留。');
    } catch (error) {
      notifyError(
        '解散通信共享组失败',
        error instanceof Error ? error.message : '解散通信共享组失败',
      );
    } finally {
      setDeletingGroupId(null);
    }
  };

  return (
    <section className="rounded-3xl border border-stone-200 bg-white p-6 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Link2 className="h-5 w-5 text-primary" />
            <h2 className="text-xl font-semibold text-stone-900">通信记录共享</h2>
          </div>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-stone-600">
            组内身份会合并显示真实收发记录和通信统计；发件配置、材料、草稿与任务仍分别归属当前身份。
          </p>
        </div>
        <button
          type="button"
          onClick={beginCreate}
          disabled={identities.length < 2 || saving}
          className="ui-btn-secondary shrink-0 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Plus className="h-4 w-4" />
          创建共享组
        </button>
      </div>

      <div className="mt-5 divide-y divide-stone-100 border-y border-stone-200">
        {communicationGroups.length === 0 ? (
          <div className="py-5 text-sm text-stone-500">
            当前没有通信共享组，各身份只显示自己的通信记录。
          </div>
        ) : (
          communicationGroups.map((group) => (
            <div
              key={group.id}
              className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <div className="text-sm font-semibold text-stone-900">
                  {getGroupLabel(group)}
                </div>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-stone-500">
                  {group.members.map((member) => (
                    <span key={member.id}>
                      {member.profile_name} · {member.email_address}
                    </span>
                  ))}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  type="button"
                  aria-label={`编辑 ${getGroupLabel(group)}`}
                  title="编辑成员"
                  onClick={() => beginEdit(group)}
                  disabled={saving || deletingGroupId !== null}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-stone-200 text-stone-600 transition hover:bg-stone-50 disabled:opacity-50"
                >
                  <Pencil className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  aria-label={`解散 ${getGroupLabel(group)}`}
                  title="解散共享组"
                  onClick={() => void dissolveGroup(group)}
                  disabled={saving || deletingGroupId !== null}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-red-200 text-red-600 transition hover:bg-red-50 disabled:opacity-50"
                >
                  {deletingGroupId === group.id ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {editorId !== null ? (
        <div className="mt-5 border-l-2 border-primary/30 pl-4 sm:pl-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-stone-900">
                {editingGroup ? '编辑共享成员' : '创建通信共享组'}
              </h3>
              <p className="mt-1 text-xs leading-5 text-stone-500">
                至少选择两个身份。选择其他组成员时，保存前会要求确认合并。
              </p>
            </div>
            <button
              type="button"
              aria-label="关闭共享组编辑"
              title="关闭"
              onClick={closeEditor}
              disabled={saving}
              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-stone-500 transition hover:bg-stone-100 disabled:opacity-50"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="mt-4 grid gap-2 md:grid-cols-2">
            {identities.map((identity) => {
              const group = identity.communication_group_id
                ? communicationGroups.find(
                    (item) => item.id === identity.communication_group_id,
                  )
                : null;
              return (
                <label
                  key={identity.id}
                  className="flex cursor-pointer items-start gap-3 rounded-lg border border-stone-200 px-3 py-3 transition hover:bg-stone-50"
                >
                  <input
                    type="checkbox"
                    checked={selectedMemberIdSet.has(identity.id)}
                    onChange={() => toggleIdentity(identity.id)}
                    disabled={saving}
                    className="mt-0.5 h-4 w-4 rounded border-stone-300 text-primary focus:ring-primary/20"
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-stone-900">
                      {getIdentityName(identity)}
                    </span>
                    <span className="mt-0.5 block break-all text-xs text-stone-500">
                      {identity.email_address}
                    </span>
                    {group && group.id !== editingGroup?.id ? (
                      <span className="mt-1 block text-xs text-amber-700">
                        已在“{getGroupLabel(group)}”组
                      </span>
                    ) : null}
                  </span>
                </label>
              );
            })}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => void saveGroup()}
              disabled={saving || selectedMemberIds.length < 2}
              className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              保存共享组
            </button>
            <span className="text-xs text-stone-500">
              已选择 {selectedMemberIds.length} 个身份
            </span>
          </div>
        </div>
      ) : null}
      {dialog}
    </section>
  );
};
