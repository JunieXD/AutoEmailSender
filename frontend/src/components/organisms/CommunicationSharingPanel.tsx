import { useMemo, useState, type TransitionEvent } from 'react';
import clsx from 'clsx';
import {
  ChevronDown,
  Loader2,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import { useNotification } from '@/context/NotificationContext';
import { useSelectionContext } from '@/context/SelectionContext';
import {
  createCommunicationGroup,
  deleteCommunicationGroup,
  updateCommunicationGroup,
} from '@/lib/api/communicationGroups';
import { ApiError } from '@/lib/api/client';
import { useConfirmDialog } from '@/lib/useConfirmDialog';
import { SelectionToggleButton } from '@/components/molecules/SelectionToggleButton';
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

const getGroupMatchSourceName = (group: IdentityCommunicationGroupDTO) =>
  group.members.find((member) => member.id === group.match_source_identity_id)
    ?.profile_name ?? null;

export const CommunicationSharingPanel = () => {
  const {
    identities,
    communicationGroups = [],
    selectedIdentity,
    refreshSelections,
  } = useSelectionContext();
  const { notifyError, notifyFormErrors, notifySuccess } = useNotification();
  const { confirm, dialog } = useConfirmDialog();
  const [open, setOpen] = useState(false);
  const [renderContent, setRenderContent] = useState(false);
  const [editorId, setEditorId] = useState<GroupEditorId>(null);
  const [selectedMemberIds, setSelectedMemberIds] = useState<number[]>([]);
  const [selectedMatchSourceIdentityId, setSelectedMatchSourceIdentityId] =
    useState<number | null>(null);
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
  const sharedIdentityCount = useMemo(
    () =>
      new Set(
        communicationGroups.flatMap((group) =>
          group.members.map((member) => member.id),
        ),
      ).size,
    [communicationGroups],
  );
  const summary =
    communicationGroups.length === 0
      ? '未创建共享组'
      : communicationGroups.length === 1
        ? `${sharedIdentityCount} 个身份共享中`
        : `${communicationGroups.length} 个共享组 · ${sharedIdentityCount} 个身份`;

  const toggleOpen = () => {
    setOpen((current) => {
      const next = !current;
      if (next) {
        setRenderContent(true);
      }
      return next;
    });
  };

  const handleContentTransitionEnd = (event: TransitionEvent<HTMLDivElement>) => {
    if (open || event.propertyName !== 'grid-template-rows') {
      return;
    }
    setRenderContent(false);
  };

  const beginCreate = () => {
    setEditorId('new');
    setSelectedMemberIds(selectedIdentity ? [selectedIdentity.id] : []);
    setSelectedMatchSourceIdentityId(null);
  };

  const beginEdit = (group: IdentityCommunicationGroupDTO) => {
    setEditorId(group.id);
    setSelectedMemberIds(group.members.map((member) => member.id));
    setSelectedMatchSourceIdentityId(group.match_source_identity_id ?? null);
  };

  const closeEditor = () => {
    if (saving) {
      return;
    }
    setEditorId(null);
    setSelectedMemberIds([]);
    setSelectedMatchSourceIdentityId(null);
  };

  const toggleIdentity = (identityId: number) => {
    const removingIdentity = selectedMemberIds.includes(identityId);
    if (removingIdentity && selectedMatchSourceIdentityId === identityId) {
      setSelectedMatchSourceIdentityId(null);
    }
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
      title: '合并已有共享组？',
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
      match_source_identity_id: selectedMatchSourceIdentityId,
      confirm_merge_existing_groups: confirmMergeExistingGroups,
    };
    return typeof editorId === 'number'
      ? updateCommunicationGroup(editorId, payload)
      : createCommunicationGroup(payload);
  };

  const saveGroup = async () => {
    if (selectedMemberIds.length < 2) {
      notifyFormErrors('请检查共享成员', ['共享组至少需要两个身份']);
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
      setSelectedMatchSourceIdentityId(null);
      notifySuccess(
        editorId === 'new' ? '共享组已创建' : '共享组已更新',
        '组内身份现在共享通信记录；匹配度显示方式已应用到首页、工作区和统计面板。',
      );
    } catch (error) {
      notifyError(
        '保存共享组失败',
        error instanceof Error ? error.message : '保存共享组失败',
      );
    } finally {
      setSaving(false);
    }
  };

  const dissolveGroup = async (group: IdentityCommunicationGroupDTO) => {
    const confirmed = await confirm({
      title: `解散“${getGroupLabel(group)}”共享组？`,
      description:
        '解散只会停止共享通信记录和匹配度显示关联，不会删除任何身份、任务或通信记录。',
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
        setSelectedMatchSourceIdentityId(null);
      }
      notifySuccess('共享组已解散', '身份和原有通信记录均已保留。');
    } catch (error) {
      notifyError(
        '解散共享组失败',
        error instanceof Error ? error.message : '解散共享组失败',
      );
    } finally {
      setDeletingGroupId(null);
    }
  };

  return (
    <>
      <section className="min-w-0 overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-sm">
        <button
          type="button"
          aria-expanded={open}
          aria-controls="communication-sharing-card-content"
          aria-label={open ? '收起多身份共享' : '展开多身份共享'}
          onClick={toggleOpen}
          className="collapsible-card-toggle flex w-full items-center justify-between gap-4 px-6 py-5 text-left transition hover:bg-stone-50 active:bg-stone-50"
        >
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold text-stone-900">多身份共享</h2>
              <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
                {summary}
              </span>
            </div>
            <p className="mt-2 text-sm leading-6 text-stone-600">
              将多个发件身份组成共享组：组内共享真实收发记录，并可统一显示某个身份的匹配度结果；邮箱配置和任务仍各自独立。
            </p>
          </div>
          <ChevronDown
            className={clsx(
              'h-5 w-5 shrink-0 text-stone-500 transition-transform',
              open ? 'rotate-180' : 'rotate-0',
            )}
          />
        </button>

        {renderContent ? (
          <div
            id="communication-sharing-card-content"
            data-state={open ? 'open' : 'closed'}
            onTransitionEnd={handleContentTransitionEnd}
            className="collapsible-card-content"
          >
            <div className="collapsible-card-body min-h-0 px-6">
              <div className="mt-5 pb-6">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="text-base font-semibold text-stone-900">共享组</h3>
                  <button
                    type="button"
                    onClick={beginCreate}
                    disabled={identities.length < 2 || saving}
                    className="ui-btn-secondary shrink-0 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Plus className="h-4 w-4" />
                    新建共享组
                  </button>
                </div>

                <div className="mt-4 divide-y divide-stone-100 border-y border-stone-200">
                  {communicationGroups.length === 0 ? (
                    <div className="py-4 text-sm text-stone-500">
                      当前没有共享组，各身份的通信记录和匹配度均独立显示。
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
                          <div className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-primary/15 bg-primary/5 px-2.5 py-1 text-xs font-medium text-primary">
                            <Sparkles className="h-3.5 w-3.5" />
                            {getGroupMatchSourceName(group)
                              ? `通信记录已共享 · 匹配度统一使用 ${getGroupMatchSourceName(group)}`
                              : '通信记录已共享 · 匹配度各自独立'}
                          </div>
                        </div>
                        <div className="flex shrink-0 gap-2">
                          <button
                            type="button"
                            aria-label={`编辑 ${getGroupLabel(group)}`}
                            title="编辑共享组"
                            onClick={() => beginEdit(group)}
                            disabled={saving || deletingGroupId !== null}
                            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-stone-200 text-stone-600 transition hover:bg-stone-50 active:translate-y-px disabled:opacity-50"
                          >
                            <Pencil className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            aria-label={`解散 ${getGroupLabel(group)}`}
                            title="解散共享组"
                            onClick={() => void dissolveGroup(group)}
                            disabled={saving || deletingGroupId !== null}
                            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-red-200 text-red-600 transition hover:bg-red-50 active:translate-y-px disabled:opacity-50"
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
                  <div className="mt-5 border-t border-stone-200 pt-5">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-sm font-semibold text-stone-900">
                          {editingGroup ? '编辑共享组' : '新建共享组'}
                        </h3>
                        <p className="mt-1 text-xs leading-5 text-stone-500">
                          选择共享成员，并设置组内匹配度的显示方式。选择其他组成员时，保存前会要求确认合并。
                        </p>
                      </div>
                      <button
                        type="button"
                        aria-label="关闭共享组编辑"
                        title="关闭"
                        onClick={closeEditor}
                        disabled={saving}
                        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-stone-500 transition hover:bg-stone-100 active:translate-y-px disabled:opacity-50"
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
                            <SelectionToggleButton
                              label={`${getIdentityName(identity)}${identity.email_address}`}
                              selected={selectedMemberIdSet.has(identity.id)}
                              onToggle={() => toggleIdentity(identity.id)}
                              disabled={saving}
                              semantics="checkbox"
                              size="sm"
                              className="mt-0.5"
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

                    <fieldset className="mt-5 rounded-2xl border border-stone-200 bg-stone-50/70 p-4">
                      <legend className="px-1 text-sm font-semibold text-stone-900">
                        匹配度显示方式
                      </legend>
                      <p className="mt-1 text-xs leading-5 text-stone-500">
                        统一后，组内所有身份都会显示所选身份的匹配分数、分析理由和建议。
                      </p>
                      <div className="mt-3 grid gap-2">
                        <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-stone-200 bg-white px-3 py-3 transition hover:bg-stone-50">
                          <input
                            type="radio"
                            name="communication-group-match-source"
                            checked={selectedMatchSourceIdentityId === null}
                            onChange={() => setSelectedMatchSourceIdentityId(null)}
                            disabled={saving}
                            className="mt-0.5 h-4 w-4 border-stone-300 text-primary focus:ring-primary/20"
                          />
                          <span>
                            <span className="block text-sm font-medium text-stone-900">
                              各自显示自己的匹配度
                            </span>
                            <span className="mt-0.5 block text-xs leading-5 text-stone-500">
                              每个身份显示使用自己默认材料生成的分数、分析理由和建议。
                            </span>
                          </span>
                        </label>
                        {identities
                          .filter((identity) => selectedMemberIdSet.has(identity.id))
                          .map((identity) => (
                            <label
                              key={identity.id}
                              className="flex cursor-pointer items-start gap-3 rounded-xl border border-stone-200 bg-white px-3 py-3 transition hover:bg-stone-50"
                            >
                              <input
                                type="radio"
                                name="communication-group-match-source"
                                checked={selectedMatchSourceIdentityId === identity.id}
                                onChange={() =>
                                  setSelectedMatchSourceIdentityId(identity.id)
                                }
                                disabled={saving}
                                className="mt-0.5 h-4 w-4 border-stone-300 text-primary focus:ring-primary/20"
                              />
                              <span className="min-w-0">
                                <span className="block text-sm font-medium text-stone-900">
                                  统一显示 {getIdentityName(identity)} 的匹配度
                                </span>
                                <span className="mt-0.5 block text-xs leading-5 text-stone-500">
                                  默认材料：
                                  {identity.current_primary_material?.display_name ||
                                    '尚未设置'}
                                </span>
                                {!identity.current_primary_material_id ? (
                                  <span className="mt-1 block text-xs text-amber-700">
                                    该身份缺少默认材料，设置前无法进行匹配分析。
                                  </span>
                                ) : null}
                              </span>
                            </label>
                          ))}
                      </div>
                    </fieldset>

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
              </div>
            </div>
          </div>
        ) : null}
      </section>
      {dialog}
    </>
  );
};
