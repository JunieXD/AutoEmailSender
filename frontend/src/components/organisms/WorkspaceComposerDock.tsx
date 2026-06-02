import { useMemo, type ReactNode } from 'react';
import clsx from 'clsx';
import {
  Bot,
  CalendarClock,
  Check,
  ChevronDown,
  ChevronUp,
  ClipboardCheck,
  MailCheck,
  Paperclip,
  PenLine,
  RefreshCcw,
  Save,
  Send,
  TimerReset,
} from 'lucide-react';
import { EmailTemplateEditor } from '@/components/molecules/EmailTemplateEditor';
import { SubjectTemplateInput } from '@/components/molecules/SubjectTemplateInput';
import { formatApiDateTime } from '@/lib/dateTime';
type RichEmailValue = { html: string; text: string };
import { getTaskModeCopy } from '@/features/create-task/client/taskCopy';
import {
  MATERIAL_TYPE_LABELS,
  type OutreachGenerationMode,
  type WorkspaceTaskSummaryDTO,
  type WorkspaceThreadDTO,
} from '@/types';

type WorkspaceComposerDockProps = {
  thread: WorkspaceThreadDTO;
  currentTask: WorkspaceTaskSummaryDTO;
  currentTaskMode: OutreachGenerationMode;
  draftReady: boolean;
  nextStepTitle: string;
  nextStepDescription: string;
  subject: string;
  content: string;
  contentHtml: string;
  selectedMaterialIds: number[];
  scheduledAt: string;
  acting: boolean;
  canChangeMode: boolean;
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
  onSaveDraft: () => void;
  onSendNow: () => void;
  onScheduleSend: () => void;
  onCancelSchedule: () => void;
  onContinueManually: () => void;
  onStartFollowUp: () => void;
  onCalculateMatch: () => void;
  onGenerateDraft: () => void;
  onChangeMode: (value: OutreachGenerationMode) => void;
};

const MODE_OPTIONS: Array<{
  value: OutreachGenerationMode;
  label: string;
}> = [
  { value: 'llm', label: getTaskModeCopy('llm').title },
  { value: 'template', label: getTaskModeCopy('template').title },
];

const ComposerSection = ({
  icon,
  title,
  description,
  children,
}: {
  icon: ReactNode;
  title: string;
  description?: string;
  children: ReactNode;
}) => (
  <section className="rounded-[22px] border border-stone-200/80 bg-white px-4 py-4 shadow-[0_18px_34px_-32px_rgba(41,37,36,0.2)]">
    <div className="flex items-start gap-3">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-primary/8 text-primary">
        {icon}
      </span>
      <div className="min-w-0">
        <div className="text-sm font-semibold text-stone-900">{title}</div>
        {description ? (
          <div className="mt-1 text-xs leading-5 text-stone-500">{description}</div>
        ) : null}
      </div>
    </div>
    <div className="mt-4">{children}</div>
  </section>
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

const formatScheduleSummary = (value: string) => {
  if (!value) {
    return '未设置';
  }

  const summary = formatApiDateTime(value);
  if (!summary || summary === 'Invalid Date') {
    return '未设置';
  }

  return summary;
};

const formatTokenCount = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN') : '未知';

const buildDraftTokenSummary = (
  task: WorkspaceTaskSummaryDTO,
  mode: OutreachGenerationMode,
) => {
  if (mode === 'template') {
    return '模板模式不消耗 token';
  }

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

  return '暂无 token 记录';
};

export const WorkspaceComposerDock = ({
  thread,
  currentTask,
  currentTaskMode,
  draftReady,
  nextStepTitle,
  nextStepDescription,
  subject,
  contentHtml,
  selectedMaterialIds,
  scheduledAt,
  acting,
  canChangeMode,
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
  onSaveDraft,
  onSendNow,
  onScheduleSend,
  onCancelSchedule,
  onContinueManually,
  onStartFollowUp,
  onCalculateMatch,
  onGenerateDraft,
  onChangeMode,
}: WorkspaceComposerDockProps) => {
  const attachmentNameMap = useMemo(
    () => new Map(thread.material_options.map((material) => [material.id, material.display_name])),
    [thread.material_options],
  );

  const selectedAttachmentNames = selectedMaterialIds
    .map((materialId) => attachmentNameMap.get(materialId))
    .filter((item): item is string => Boolean(item));

  const hasProfessorResearchDirection = Boolean(thread.professor.research_direction?.trim());
  const hasTemplateConfigured = Boolean(
    currentTask.outreach_template_body_text?.trim() || currentTask.outreach_template_body_html?.trim(),
  );
  const scheduledSummary = formatScheduleSummary(scheduledAt);
  const draftTokenSummary = buildDraftTokenSummary(currentTask, currentTaskMode);
  const editorDisabled = acting || draftSaving;
  const limitationHint =
    currentTaskMode === 'template'
      ? hasTemplateConfigured
        ? null
        : '请先在身份页补充模板。'
      : !hasTemplateConfigured
        ? '请先在身份页补充套磁信模板。'
        : !currentTask.primary_material_id
          ? '请选择用于匹配的材料。'
          : !hasProfessorResearchDirection
            ? '请先补充导师研究方向，再使用 AI 生成草稿。'
            : null;

  return (
    <div className="relative z-20 overflow-visible border-t border-stone-200/80 bg-[linear-gradient(180deg,rgba(255,252,246,0.94),rgba(255,248,240,0.98))] px-4 py-4 backdrop-blur-xl sm:px-6">
      <div className="mx-auto w-full max-w-5xl overflow-visible">
        {composerExpanded ? (
          <div className="mb-4 overflow-visible rounded-[32px] border border-stone-200/90 bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(255,250,244,0.98))] p-4 shadow-[0_28px_70px_-42px_rgba(41,37,36,0.42)] sm:p-5">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex min-w-0 items-start gap-3">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary text-white shadow-sm shadow-primary/20">
                  <PenLine className="h-5 w-5" />
                </span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="text-lg font-semibold text-stone-950">写信区</div>
                    <span className="rounded-full border border-stone-200 bg-white px-2.5 py-1 text-xs font-medium text-stone-600">
                      {draftReady ? '草稿可发送' : '等待草稿'}
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

            <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_330px]">
              <section className="min-w-0 overflow-visible rounded-[28px] border border-stone-200/80 bg-stone-50/70 p-4 sm:p-5">
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
                    onChange={onContentChange}
                  />
                </div>
              </section>

              <aside className="space-y-3">
                <ComposerSection
                  icon={<ClipboardCheck className="h-4 w-4" />}
                  title="发送前核对"
                  description="发送前快速确认关键项。"
                >
                  <div className="space-y-2">
                    <SummaryLine label="方式">
                      {getTaskModeCopy(currentTaskMode).title}
                    </SummaryLine>
                    <SummaryLine label="附件">
                      {selectedAttachmentNames.length > 0
                        ? `${selectedAttachmentNames.length} 份`
                        : '未选择'}
                    </SummaryLine>
                    <SummaryLine label="定时">{scheduledSummary}</SummaryLine>
                  </div>
                </ComposerSection>

                <ComposerSection
                  icon={<Bot className="h-4 w-4" />}
                  title="生成草稿"
                  description={limitationHint ?? '选择写信方式，并生成下一版草稿。'}
                >
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-2">
                      {MODE_OPTIONS.map((option) => {
                        const active = currentTaskMode === option.value;
                        return (
                          <button
                            key={option.value}
                            type="button"
                            disabled={editorDisabled || !canChangeMode}
                            onClick={() => onChangeMode(option.value)}
                            className={clsx(
                              'rounded-2xl border px-3 py-3 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60',
                              active
                                ? 'border-primary bg-primary text-white'
                                : 'border-stone-200 bg-white text-stone-700 hover:border-primary/30 hover:bg-primary/5',
                            )}
                          >
                            {option.label}
                          </button>
                        );
                      })}
                    </div>
                    <div className="flex flex-wrap gap-2 border-t border-stone-100 pt-3">
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
                      生成草稿
                    </button>
                    </div>
                    <div className="rounded-2xl border border-stone-100 bg-stone-50/70 px-3 py-2 text-xs leading-5 text-stone-500">
                      {draftTokenSummary}
                    </div>
                  </div>
                </ComposerSection>

                <ComposerSection
                  icon={<Paperclip className="h-4 w-4" />}
                  title="随信附件"
                >
                  {thread.material_options.length === 0 ? (
                    <div className="text-sm text-stone-500">暂无可发送材料。</div>
                  ) : (
                    <div className="max-h-48 space-y-2 overflow-y-auto pr-1">
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
                  )}
                </ComposerSection>
              </aside>
            </div>

            <div className="mt-4">
              <ComposerSection
                icon={<MailCheck className="h-4 w-4" />}
                title="发送动作"
                description="确认无误后发送，或保留定时。"
              >
                <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
                  <div className="min-w-0 space-y-1 text-xs text-stone-500">
                    <div className="truncate">
                      附件：{selectedAttachmentNames.length > 0 ? selectedAttachmentNames.join('、') : '未选择'}
                    </div>
                    <div>定时：{scheduledSummary}</div>
                  </div>

                  <div className="flex flex-wrap gap-3">
                    {canContinueManually ? (
                      <button
                        type="button"
                        onClick={onContinueManually}
                        disabled={editorDisabled}
                        className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        作为单独联系继续
                      </button>
                    ) : null}
                    {canStartFollowUp ? (
                      <button
                        type="button"
                        onClick={onStartFollowUp}
                        disabled={editorDisabled}
                        className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        写跟进邮件
                      </button>
                    ) : null}
                    {canSubmitDraft ? (
                      <button
                        type="button"
                        onClick={onSaveDraft}
                        disabled={editorDisabled}
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
                          disabled={editorDisabled || !draftReady}
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
                        disabled={editorDisabled || !draftReady}
                        className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <Send className="h-4 w-4" />
                        立即发送
                      </button>
                    ) : null}
                  </div>
                </div>
              </ComposerSection>
            </div>
          </div>
        ) : null}

        {!composerExpanded ? (
        <div className="rounded-[28px] border border-stone-200 bg-white/94 px-4 py-4 shadow-[0_18px_40px_-34px_rgba(41,37,36,0.28)]">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-stone-900">
                {draftReady ? '继续写信' : '写第一封信'}
              </div>
              <div className="mt-1 text-xs leading-5 text-stone-500">
                {draftReady
                  ? '草稿已生成，可继续编辑。'
                  : '展开后编辑草稿和发送设置。'}
              </div>
              <div className="mt-3 rounded-2xl border border-primary/15 bg-primary/5 px-4 py-3">
                <div className="text-sm font-semibold text-stone-900">{nextStepTitle}</div>
                <div className="mt-1 text-xs leading-5 text-stone-600">{nextStepDescription}</div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1 text-xs text-stone-600">
                  {getTaskModeCopy(currentTaskMode).title}
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

            <div className="flex flex-wrap gap-2">
              {canContinueManually ? (
                <button
                  type="button"
                  onClick={onContinueManually}
                  disabled={editorDisabled}
                  className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  作为单独联系继续
                </button>
              ) : null}
              {canStartFollowUp ? (
                <button
                  type="button"
                  onClick={onStartFollowUp}
                  disabled={editorDisabled}
                  className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  写跟进邮件
                </button>
              ) : null}
              <button
                type="button"
                onClick={onCalculateMatch}
                disabled={editorDisabled || !canCalculateMatch}
                className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
              >
                <RefreshCcw className="h-4 w-4" />
                分析匹配度
              </button>
              <button
                type="button"
                onClick={onGenerateDraft}
                disabled={editorDisabled || !canGenerateDraft}
                className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
              >
                <RefreshCcw className="h-4 w-4" />
                生成草稿
              </button>
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
