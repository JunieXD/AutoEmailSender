import { useMemo, useState, type ReactNode } from 'react';
import clsx from 'clsx';
import {
  Bot,
  CalendarClock,
  Check,
  ChevronDown,
  ChevronUp,
  ClipboardCheck,
  FileText,
  MailCheck,
  Paperclip,
  PenLine,
  RefreshCcw,
  Save,
  Send,
  TimerReset,
} from 'lucide-react';
import { EmailTemplateEditor } from '@/components/molecules/EmailTemplateEditor';
import { NativeSelectField } from '@/components/atoms/NativeSelectField';
import { SubjectTemplateInput } from '@/components/molecules/SubjectTemplateInput';
import { formatApiDateTime } from '@/lib/dateTime';
type RichEmailValue = { html: string; text: string };
import {
  MATERIAL_TYPE_LABELS,
  type OutreachTemplateDTO,
  type WorkspaceDraftSourceDTO,
  type WorkspaceTaskSummaryDTO,
  type WorkspaceThreadDTO,
} from '@/types';

type WorkspaceComposerDockProps = {
  thread: WorkspaceThreadDTO;
  currentTask: WorkspaceTaskSummaryDTO;
  draftReady: boolean;
  nextStepTitle: string;
  nextStepDescription: string;
  subject: string;
  content: string;
  contentHtml: string;
  selectedMaterialIds: number[];
  outreachTemplates: OutreachTemplateDTO[];
  selectedOutreachTemplateId: number | null;
  loadingOutreachTemplates: boolean;
  scheduledAt: string;
  acting: boolean;
  isRewriting: boolean;
  hasDraftBody: boolean;
  canCalculateMatch: boolean;
  canGenerateDraft: boolean;
  canContinueManually: boolean;
  canStartFollowUp: boolean;
  canSubmitDraft: boolean;
  draftSaving: boolean;
  composerExpanded: boolean;
  onToggleExpanded: () => void;
  onSubjectChange: (value: string) => void;
  onContentChange: (value: RichEmailValue) => void;
  onSelectedMaterialIdsChange: (ids: number[]) => void;
  onApplyOutreachTemplate: (templateId: number) => void;
  onSaveDraft: () => void;
  onSendNow: () => void;
  onScheduleSend: () => void;
  onCancelSchedule: () => void;
  onContinueManually: () => void;
  onStartFollowUp: () => void;
  onCalculateMatch: () => void;
  onGenerateDraft: () => void;
};

type ComposerToolKey = 'template' | 'ai' | 'attachments' | 'review';

const COMPOSER_TOOL_LABELS: Record<ComposerToolKey, string> = {
  template: '模板',
  ai: 'AI 辅助',
  attachments: '附件',
  review: '发送核对',
};

const ComposerToolButton = ({
  active,
  icon,
  label,
  badge,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  badge?: string;
  onClick: () => void;
}) => (
  <button
    type="button"
    aria-label={`${label}工具`}
    aria-expanded={active}
    aria-controls="composer-tool-panel"
    onClick={onClick}
    className={clsx(
      'flex min-h-11 min-w-0 items-center gap-2 rounded-xl border px-3 py-2 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20',
      active
        ? 'border-primary bg-primary text-white shadow-sm shadow-primary/20'
        : 'border-stone-200 bg-white text-stone-700 hover:border-primary/25 hover:bg-primary/5 hover:text-primary',
    )}
  >
    <span className={clsx('shrink-0', active ? 'text-white' : 'text-primary')}>
      {icon}
    </span>
    <span className="min-w-0 flex-1 truncate text-left">{label}</span>
    {badge ? (
      <span
        className={clsx(
          'min-w-5 shrink-0 rounded-lg px-1.5 py-0.5 text-center text-[11px] font-semibold tabular-nums',
          active ? 'bg-white/16 text-white' : 'bg-stone-100 text-stone-600',
        )}
      >
        {badge}
      </span>
    ) : null}
    <ChevronDown
      className={clsx('h-4 w-4 shrink-0 transition-transform', active && 'rotate-180')}
    />
  </button>
);

const SectionHeading = ({
  icon,
  title,
  description,
}: {
  icon: ReactNode;
  title: string;
  description: string;
}) => (
  <div className="flex items-start gap-3">
    <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-primary text-white shadow-sm shadow-primary/20">
      {icon}
    </span>
    <div className="min-w-0">
      <div className="text-sm font-semibold text-stone-950">{title}</div>
      <div className="mt-1 text-xs leading-5 text-stone-500">{description}</div>
    </div>
  </div>
);

const SummaryLine = ({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) => (
  <div className="flex items-start justify-between gap-3 rounded-xl border border-stone-100 bg-stone-50/70 px-3 py-2">
    <span className="shrink-0 text-xs font-medium text-stone-500">{label}</span>
    <span className="min-w-0 text-right text-xs font-semibold text-stone-800">
      {children}
    </span>
  </div>
);

const DATETIME_LOCAL_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/;

const formatLocalScheduleSummary = (value: string) => {
  const [datePart, timePart] = value.split('T');
  if (!datePart || !timePart) {
    return null;
  }

  const [, month, day] = datePart.split('-');
  if (!month || !day) {
    return null;
  }

  return `${month}/${day} ${timePart}`;
};

const formatScheduleSummary = (value: string) => {
  if (!value) {
    return '未设置';
  }

  if (DATETIME_LOCAL_PATTERN.test(value)) {
    return formatLocalScheduleSummary(value) ?? '未设置';
  }

  const summary = formatApiDateTime(value);
  if (!summary || summary === 'Invalid Date') {
    return '未设置';
  }

  return summary;
};

const formatTokenCount = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN') : '未知';

const buildDraftTokenSummary = (task: WorkspaceTaskSummaryDTO) => {
  if (
    task.last_draft_prompt_tokens != null ||
    task.last_draft_completion_tokens != null ||
    task.last_draft_total_tokens != null
  ) {
    return `上次消耗：输入 ${formatTokenCount(task.last_draft_prompt_tokens)} / 输出 ${formatTokenCount(task.last_draft_completion_tokens)} / 总计 ${formatTokenCount(task.last_draft_total_tokens)}`;
  }

  if (
    task.estimated_prompt_tokens != null ||
    task.estimated_completion_tokens_upper_bound != null ||
    task.estimated_total_tokens_upper_bound != null
  ) {
    return `预计上限：输入 ${formatTokenCount(task.estimated_prompt_tokens)} / 输出最多 ${formatTokenCount(task.estimated_completion_tokens_upper_bound)} / 总计最多 ${formatTokenCount(task.estimated_total_tokens_upper_bound)}`;
  }

  return 'AI 改写会在执行后记录 token';
};

const DRAFT_SOURCE_LABELS: Record<WorkspaceDraftSourceDTO, string> = {
  saved: '已保存草稿',
  ai_rewrite: 'AI 改写结果',
  template: '来自模板',
  manual_empty: '空草稿',
  rewrite_source: '改写前草稿',
};

const getDraftSourceLabel = (source: WorkspaceDraftSourceDTO | null | undefined) =>
  source ? DRAFT_SOURCE_LABELS[source] : '空草稿';

export const WorkspaceComposerDock = ({
  thread,
  currentTask,
  draftReady,
  nextStepTitle,
  nextStepDescription,
  subject,
  contentHtml,
  selectedMaterialIds,
  outreachTemplates,
  selectedOutreachTemplateId,
  loadingOutreachTemplates,
  scheduledAt,
  acting,
  isRewriting,
  hasDraftBody,
  canCalculateMatch,
  canGenerateDraft,
  canContinueManually,
  canStartFollowUp,
  canSubmitDraft,
  draftSaving,
  composerExpanded,
  onToggleExpanded,
  onSubjectChange,
  onContentChange,
  onSelectedMaterialIdsChange,
  onApplyOutreachTemplate,
  onSaveDraft,
  onSendNow,
  onScheduleSend,
  onCancelSchedule,
  onContinueManually,
  onStartFollowUp,
  onCalculateMatch,
  onGenerateDraft,
}: WorkspaceComposerDockProps) => {
  const [activeTool, setActiveTool] = useState<ComposerToolKey | null>('ai');
  const attachmentNameMap = useMemo(
    () => new Map(thread.material_options.map((material) => [material.id, material.display_name])),
    [thread.material_options],
  );

  const selectedAttachmentNames = selectedMaterialIds
    .map((materialId) => attachmentNameMap.get(materialId))
    .filter((item): item is string => Boolean(item));

  const hasProfessorResearchDirection = Boolean(thread.professor.research_direction?.trim());
  const scheduledSummary = formatScheduleSummary(scheduledAt);
  const draftTokenSummary = buildDraftTokenSummary(currentTask);
  const draftSourceLabel = getDraftSourceLabel(currentTask.draft?.source);
  const actionDisabled = acting || draftSaving || isRewriting;
  const editorDisabled = actionDisabled || currentTask.draft?.editable === false;
  const selectedOutreachTemplate =
    outreachTemplates.find(
      (template) => template.id === selectedOutreachTemplateId,
    ) ?? null;
  const activeOutreachTemplates = outreachTemplates.filter(
    (template) => !template.archived_at,
  );
  const sourceTemplateLabel = selectedOutreachTemplate
    ? `${selectedOutreachTemplate.name}${selectedOutreachTemplate.archived_at ? ' · 已删除' : ''}`
    : selectedOutreachTemplateId !== null
      ? '历史来源模板'
      : '未使用模板';
  const rewriteDescription = isRewriting
    ? '正在改写当前草稿，完成前不能保存或发送。'
    : hasDraftBody
      ? '基于当前编辑器内容生成个性化版本。'
      : '先写入正文或配置默认模板后再使用 AI 改写。';
  const limitationHint =
    !hasDraftBody
      ? '先写入正文或配置默认模板后再使用 AI 改写。'
      : !currentTask.primary_material_id
        ? '请选择 AI 写信参考材料。'
        : !hasProfessorResearchDirection
          ? '请先补充导师研究方向，再使用 AI 改写。'
          : null;
  const draftStateLabel = isRewriting ? 'AI 改写中' : hasDraftBody ? '草稿可编辑' : '空草稿';
  const collapsedTitle = isRewriting ? 'AI 正在改写' : hasDraftBody ? '继续写信' : '写第一封信';
  const collapsedDescription = isRewriting
    ? '当前草稿已锁定，完成后会自动显示新版本。'
    : hasDraftBody
      ? '可直接编辑、保存或发送，也可以让 AI 改写。'
      : '先写入正文或配置默认模板后再使用 AI 改写。';

  return (
    <div className="relative z-20 overflow-visible border-t border-stone-200/80 bg-[linear-gradient(180deg,rgba(255,252,246,0.94),rgba(255,248,240,0.98))] px-4 py-4 backdrop-blur-xl sm:px-6">
      <div
        data-composer-shell
        className={clsx(
          'mx-auto w-full overflow-visible transition-[max-width] duration-300',
          composerExpanded ? 'max-w-none' : 'max-w-5xl',
        )}
      >
        {composerExpanded ? (
          <div className="overflow-visible rounded-[32px] border border-stone-200/90 bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(255,250,244,0.98))] p-4 shadow-[0_28px_70px_-42px_rgba(41,37,36,0.42)] sm:p-5">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex min-w-0 items-start gap-3">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary text-white shadow-sm shadow-primary/20">
                  <PenLine className="h-5 w-5" />
                </span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="text-lg font-semibold text-stone-950">写信区</div>
                    <span className="rounded-full border border-stone-200 bg-white px-2.5 py-1 text-xs font-medium text-stone-600">
                      {draftStateLabel}
                    </span>
                  </div>
                  <div className="mt-1 text-sm leading-6 text-stone-500">
                    {nextStepTitle} · {nextStepDescription}
                  </div>
                </div>
              </div>

              <button
                type="button"
                onClick={onToggleExpanded}
                className="ui-btn-secondary shrink-0"
              >
                <ChevronDown className="h-4 w-4" />
                收起
              </button>
            </div>

            <div
              data-composer-layout
              className="mt-5 space-y-4"
            >
              <section
                data-composer-tools
                className="rounded-[22px] border border-stone-200/80 bg-stone-50/75 p-2"
              >
                <div
                  role="toolbar"
                  aria-label="写信工具"
                  className="grid grid-cols-2 gap-2 sm:grid-cols-4"
                >
                  <ComposerToolButton
                    active={activeTool === 'template'}
                    icon={<FileText className="h-4 w-4" />}
                    label={COMPOSER_TOOL_LABELS.template}
                    onClick={() =>
                      setActiveTool((current) => (current === 'template' ? null : 'template'))
                    }
                  />
                  <ComposerToolButton
                    active={activeTool === 'ai'}
                    icon={<Bot className="h-4 w-4" />}
                    label={COMPOSER_TOOL_LABELS.ai}
                    onClick={() =>
                      setActiveTool((current) => (current === 'ai' ? null : 'ai'))
                    }
                  />
                  <ComposerToolButton
                    active={activeTool === 'attachments'}
                    icon={<Paperclip className="h-4 w-4" />}
                    label={COMPOSER_TOOL_LABELS.attachments}
                    badge={
                      selectedAttachmentNames.length > 0
                        ? String(selectedAttachmentNames.length)
                        : undefined
                    }
                    onClick={() =>
                      setActiveTool((current) =>
                        current === 'attachments' ? null : 'attachments',
                      )
                    }
                  />
                  <ComposerToolButton
                    active={activeTool === 'review'}
                    icon={<ClipboardCheck className="h-4 w-4" />}
                    label={COMPOSER_TOOL_LABELS.review}
                    onClick={() =>
                      setActiveTool((current) => (current === 'review' ? null : 'review'))
                    }
                  />
                </div>

                {activeTool ? (
                  <div
                    id="composer-tool-panel"
                    role="region"
                    aria-label={`${COMPOSER_TOOL_LABELS[activeTool]}工具内容`}
                    data-composer-tool-panel={activeTool}
                    className="mt-2 rounded-2xl border border-stone-200/80 bg-white p-4 shadow-[0_16px_30px_-28px_rgba(41,37,36,0.24)]"
                  >
                    {activeTool === 'template' ? (
                      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(16rem,0.72fr)] lg:items-end">
                        <div className="min-w-0">
                          <div className="text-xs font-medium text-stone-500">来源模板</div>
                          <div className="mt-1 flex min-w-0 flex-wrap items-center gap-2">
                            <span className="min-w-0 truncate text-sm font-semibold text-stone-900">
                              {sourceTemplateLabel}
                            </span>
                            <span className="shrink-0 rounded-lg border border-stone-200 bg-stone-50 px-2 py-0.5 text-[11px] font-medium text-stone-600">
                              当前草稿：{draftSourceLabel}
                            </span>
                          </div>
                          <div className="mt-1 text-xs leading-5 text-stone-500">
                            {selectedOutreachTemplateId !== null
                              ? '当前草稿基于此模板创建。'
                              : '当前草稿没有来源模板。'}
                          </div>
                        </div>
                        <NativeSelectField
                          label="重新套用模板"
                          value=""
                          ariaLabel="选择模板重新套用"
                          selectedLabel={
                            loadingOutreachTemplates
                              ? '正在加载模板库…'
                              : activeOutreachTemplates.length > 0
                                ? '选择模板重新套用…'
                                : '暂无可用模板'
                          }
                          disabled={
                            editorDisabled ||
                            loadingOutreachTemplates ||
                            activeOutreachTemplates.length === 0
                          }
                          onChange={(event) => {
                            if (event.target.value) {
                              onApplyOutreachTemplate(Number(event.target.value));
                            }
                          }}
                        >
                          {activeOutreachTemplates.map((template) => (
                            <option key={template.id} value={template.id}>
                              {template.name}
                              {template.id === selectedOutreachTemplateId ? ' · 当前来源' : ''}
                              {template.is_default ? ' · 全局默认' : ''}
                              {template.is_ready ? '' : ' · 内容待完善'}
                            </option>
                          ))}
                        </NativeSelectField>
                      </div>
                    ) : null}

                    {activeTool === 'ai' ? (
                      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                        <div className="min-w-0">
                          <div className="text-xs leading-5 text-stone-500">
                            {rewriteDescription}
                          </div>
                          <div className="mt-3 w-fit max-w-full rounded-xl bg-stone-50 px-3 py-2 text-xs leading-5 text-stone-500">
                            {draftTokenSummary}
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            disabled={editorDisabled || !canCalculateMatch}
                            onClick={onCalculateMatch}
                            className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            <RefreshCcw className="h-4 w-4" />
                            分析匹配度
                          </button>
                          <button
                            type="button"
                            disabled={editorDisabled || !canGenerateDraft}
                            onClick={onGenerateDraft}
                            className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            <RefreshCcw className="h-4 w-4" />
                            AI 改写
                          </button>
                        </div>
                      </div>
                    ) : null}

                    {activeTool === 'attachments' ? (
                      thread.material_options.length === 0 ? (
                        <div className="text-sm text-stone-500">暂无可发送材料。</div>
                      ) : (
                        <div className="grid max-h-56 gap-2 overflow-y-auto pr-1 sm:grid-cols-2 xl:grid-cols-3">
                          {thread.material_options.map((material) => {
                            const checked = selectedMaterialIds.includes(material.id);
                            return (
                              <label
                                key={material.id}
                                className={clsx(
                                  'flex items-center justify-between gap-3 rounded-2xl border px-3 py-3 text-sm transition',
                                  checked
                                    ? 'border-primary/25 bg-primary/8 text-primary'
                                    : 'border-stone-200 bg-white text-stone-700 hover:border-primary/25 hover:bg-primary/5',
                                )}
                              >
                                <span className="flex min-w-0 items-center gap-3">
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    disabled={editorDisabled}
                                    onChange={() => {
                                      if (editorDisabled) {
                                        return;
                                      }
                                      onSelectedMaterialIdsChange(
                                        checked
                                          ? selectedMaterialIds.filter((item) => item !== material.id)
                                          : [...selectedMaterialIds, material.id],
                                      );
                                    }}
                                  />
                                  <span className="min-w-0">
                                    <span className="block truncate font-medium">
                                      {material.display_name}
                                    </span>
                                    <span className="mt-1 block text-xs text-stone-500">
                                      {MATERIAL_TYPE_LABELS[material.material_type]}
                                    </span>
                                  </span>
                                </span>
                                {checked ? <Check className="h-4 w-4 shrink-0" /> : null}
                              </label>
                            );
                          })}
                        </div>
                      )
                    ) : null}

                    {activeTool === 'review' ? (
                      <div className="grid gap-2 sm:grid-cols-3">
                        <SummaryLine label="草稿">{draftSourceLabel}</SummaryLine>
                        <SummaryLine label="附件">
                          {selectedAttachmentNames.length > 0
                            ? `${selectedAttachmentNames.length} 份`
                            : '未选择'}
                        </SummaryLine>
                        <SummaryLine label="定时">{scheduledSummary}</SummaryLine>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </section>

              <section
                data-composer-editor
                className="min-w-0 overflow-visible rounded-[28px] border border-stone-200/80 bg-stone-50/70 p-4 sm:p-5"
              >
                <SectionHeading
                  icon={<PenLine className="h-4 w-4" />}
                  title="正文编辑"
                  description="主题、正文和占位符都在这里处理。"
                />

                <div className="mt-5 space-y-4">
                  <SubjectTemplateInput
                    label="邮件主题"
                    value={subject}
                    disabled={editorDisabled}
                    onChange={onSubjectChange}
                    placeholder="给老师的邮件主题"
                  />

                  <EmailTemplateEditor
                    label="邮件正文"
                    html={contentHtml}
                    disabled={editorDisabled}
                    contentClassName="min-h-[420px] max-h-[min(58vh,680px)]"
                    onChange={onContentChange}
                  />
                </div>
              </section>
            </div>

            <section
              data-composer-actions
              className="sticky bottom-0 z-10 mt-4 rounded-[22px] border border-stone-200/90 bg-white/95 px-4 py-3 shadow-[0_18px_38px_-28px_rgba(41,37,36,0.3)] backdrop-blur-xl"
            >
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="flex min-w-0 items-start gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/8 text-primary">
                    <MailCheck className="h-4 w-4" />
                  </span>
                  <div className="min-w-0 space-y-1 text-xs text-stone-500">
                    <div className="text-sm font-semibold text-stone-900">发送准备</div>
                    <div className="truncate">
                      附件：{selectedAttachmentNames.length > 0 ? selectedAttachmentNames.join('、') : '未选择'}
                    </div>
                    <div>定时：{scheduledSummary}</div>
                  </div>
                </div>

                <div className="flex flex-wrap gap-3">
                    {canContinueManually ? (
                      <button
                        type="button"
                        onClick={onContinueManually}
                        disabled={actionDisabled}
                        className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        作为单独联系继续
                      </button>
                    ) : null}
                    {canStartFollowUp ? (
                      <button
                        type="button"
                        onClick={onStartFollowUp}
                        disabled={actionDisabled}
                        className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        写跟进邮件
                      </button>
                    ) : null}
                    {canSubmitDraft ? (
                      <button
                        type="button"
                        onClick={onSaveDraft}
                        disabled={editorDisabled || !canSubmitDraft}
                        className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <Save className="h-4 w-4" />
                        保存草稿
                      </button>
                    ) : null}
                    {canSubmitDraft ? (
                      currentTask.status === 'scheduled' ? (
                        <button
                          type="button"
                          onClick={onCancelSchedule}
                          disabled={editorDisabled}
                          className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          <TimerReset className="h-4 w-4" />
                          取消定时
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={onScheduleSend}
                          disabled={editorDisabled || !canSubmitDraft || !draftReady}
                          className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          <CalendarClock className="h-4 w-4" />
                          定时发送
                        </button>
                      )
                    ) : null}
                    {canSubmitDraft ? (
                      <button
                        type="button"
                        onClick={onSendNow}
                        disabled={editorDisabled || !canSubmitDraft || !draftReady}
                        className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <Send className="h-4 w-4" />
                        立即发送
                      </button>
                    ) : null}
                </div>
              </div>
            </section>
          </div>
        ) : null}

        {!composerExpanded ? (
        <div className="rounded-[28px] border border-stone-200 bg-white/94 px-4 py-4 shadow-[0_18px_40px_-34px_rgba(41,37,36,0.28)]">
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-stone-900">
                {collapsedTitle}
              </div>
              <div className="mt-1 text-xs leading-5 text-stone-500">
                {collapsedDescription}
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1 text-xs text-stone-600">
                  {draftSourceLabel}
                </span>
                <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1 text-xs text-stone-600">
                  <Paperclip className="mr-1 inline h-3.5 w-3.5" />
                  {selectedAttachmentNames.length > 0 ? `${selectedAttachmentNames.length} 份附件` : '未选附件'}
                </span>
                {scheduledAt ? (
                  <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1 text-xs text-stone-600">
                    <CalendarClock className="mr-1 inline h-3.5 w-3.5" />
                    {scheduledSummary}
                  </span>
                ) : null}
              </div>
            </div>

            <div className="flex flex-wrap gap-2 md:pt-0.5">
              {canContinueManually ? (
                <button
                  type="button"
                  onClick={onContinueManually}
                  disabled={actionDisabled}
                  className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  作为单独联系继续
                </button>
              ) : null}
              {canStartFollowUp ? (
                <button
                  type="button"
                  onClick={onStartFollowUp}
                  disabled={actionDisabled}
                  className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  写跟进邮件
                </button>
              ) : null}
              <button
                type="button"
                onClick={onToggleExpanded}
                className="ui-btn-primary"
              >
                {composerExpanded ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronUp className="h-4 w-4" />
                )}
                {draftReady ? '编辑草稿' : '写信'}
              </button>
            </div>
          </div>

          {limitationHint ? (
            <div className="mt-3 text-xs leading-5 text-stone-500">{limitationHint}</div>
          ) : null}
        </div>
        ) : null}
      </div>
    </div>
  );
};
