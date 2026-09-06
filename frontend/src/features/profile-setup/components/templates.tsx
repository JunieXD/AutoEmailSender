import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";
import { SubjectTemplateInput } from "@/components/molecules/SubjectTemplateInput";
import { inputClassName } from "@/features/profile-setup/model/formControls";
import {
  TEMPLATE_FILE_ACCEPT,
  hasVisibleTemplateBody,
  isExistingEditorId,
  type EditorId,
  type IdentityFormState,
  type OutreachTemplateFormState,
} from "@/features/profile-setup/model/profileForms";
import { textToEmailHtml } from "@/lib/richEmail";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";
import { type OutreachGenerationMode, type OutreachTemplateDTO } from "@/types";
import clsx from "clsx";
import {
  ArchiveRestore,
  Copy,
  FolderOpen,
  Loader2,
  Plus,
  Star,
  Upload,
  X,
} from "lucide-react";
import { useState, type DragEvent } from "react";
import { FieldLabel } from "./formControls";

export const OutreachTemplateSummaryCard = ({
  form,
  template,
  globalTemplate,
  templateCount,
  loadingTemplates,
  onOpen,
}: {
  form: IdentityFormState;
  template: OutreachTemplateDTO | null;
  globalTemplate: OutreachTemplateDTO | null;
  templateCount: number;
  loadingTemplates: boolean;
  onOpen: () => void;
}) => {
  const effectiveTemplate = template ?? globalTemplate;
  const hasSubject = effectiveTemplate
    ? Boolean(effectiveTemplate.subject?.trim())
    : Boolean(form.outreach_template_subject.trim());
  const hasTemplateBody = effectiveTemplate
    ? Boolean(effectiveTemplate.body_text?.trim())
    : hasVisibleTemplateBody(form);
  const effectiveMode =
    effectiveTemplate?.recommended_generation_mode ??
    form.outreach_generation_mode;

  return (
    <div className="rounded-[28px] border border-stone-200 bg-[linear-gradient(135deg,#fffdfa,#fff7ee_58%,#fff2e4)] p-5 shadow-sm shadow-stone-200/70">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="space-y-3">
          <div>
            <div className="text-sm font-medium text-stone-900">发信模板库</div>
            <div className="mt-1 text-xs leading-6 text-stone-500">
              模板可单独保存并重复使用。
            </div>
            <div className="mt-1 text-xs leading-6 text-stone-500">
              默认模板：
              {template
                ? template.name
                : globalTemplate
                  ? `使用全局“${globalTemplate.name}”`
                  : "未设置"}
            </div>
            <div className="mt-1 text-xs leading-6 text-stone-500">
              默认写信方式：
              {effectiveMode === "template" ? "直接套用模板" : "AI 辅助写信"}
              {` · ${loadingTemplates ? "正在加载" : `${templateCount} 份可用模板`}`}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="rounded-full border border-stone-200/80 bg-white/90 px-3 py-1 text-xs text-stone-600">
              {hasSubject ? "主题已填写" : "主题待补充"}
            </span>
            <span className="rounded-full border border-stone-200/80 bg-white/90 px-3 py-1 text-xs text-stone-600">
              {hasTemplateBody ? "正文已填写" : "正文待补充"}
            </span>
          </div>
        </div>

        <button
          type="button"
          onClick={onOpen}
          className="inline-flex items-center gap-2 rounded-2xl border border-stone-300 bg-white/95 px-4 py-2.5 text-sm font-medium text-stone-800 shadow-sm transition hover:border-stone-400 hover:bg-white"
        >
          <FolderOpen className="h-4 w-4" />
          管理模板
        </button>
      </div>
    </div>
  );
};

export const OutreachTemplateModal = ({
  open,
  importingTemplateFile,
  savingTemplate,
  actingOnTemplate,
  loadingTemplates,
  templates,
  archivedTemplates,
  editorId,
  form,
  identityLabel,
  identityDefaultTemplateId,
  onClose,
  onComplete,
  onCreate,
  onSelect,
  onDuplicate,
  onSetIdentityDefault,
  onClearIdentityDefault,
  onSetGlobalDefault,
  onDelete,
  onRestore,
  onImport,
  onNameChange,
  onModeChange,
  onSubjectChange,
  onBodyChange,
}: {
  open: boolean;
  importingTemplateFile: boolean;
  savingTemplate: boolean;
  actingOnTemplate: boolean;
  loadingTemplates: boolean;
  templates: OutreachTemplateDTO[];
  archivedTemplates: OutreachTemplateDTO[];
  editorId: EditorId;
  form: OutreachTemplateFormState;
  identityLabel: string;
  identityDefaultTemplateId: number | null;
  onClose: () => void;
  onComplete: () => void;
  onCreate: () => void;
  onSelect: (templateId: number) => void;
  onDuplicate: (templateId: number) => void;
  onSetIdentityDefault: (template: OutreachTemplateDTO) => void;
  onClearIdentityDefault: () => void;
  onSetGlobalDefault: (templateId: number) => void;
  onDelete: (template: OutreachTemplateDTO) => void;
  onRestore: (template: OutreachTemplateDTO) => void;
  onImport: (file: File) => void;
  onNameChange: (value: string) => void;
  onModeChange: (value: OutreachGenerationMode) => void;
  onSubjectChange: (value: string) => void;
  onBodyChange: (value: { html: string; text: string }) => void;
}) => {
  const [isTemplateDropActive, setIsTemplateDropActive] = useState(false);
  const {
    onBackdropClick,
    onBackdropMouseDown,
    onContentClick,
    onContentMouseDown,
  } = useDismissableLayerClick(onClose);

  if (!open) {
    return null;
  }

  const templateEditorHtml =
    form.outreach_template_body_html ||
    textToEmailHtml(form.outreach_template_body_text);
  const editingTemplate = isExistingEditorId(editorId)
    ? (templates.find((template) => template.id === editorId) ?? null)
    : null;
  const templateBusy = savingTemplate || actingOnTemplate;
  const hasUnsavedTemplate = editorId === "new";
  const visibleTemplateCount = templates.length + (hasUnsavedTemplate ? 1 : 0);

  const handleTemplateDragOver = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!importingTemplateFile) {
      setIsTemplateDropActive(true);
    }
  };

  const handleTemplateDragLeave = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setIsTemplateDropActive(false);
  };

  const handleTemplateDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setIsTemplateDropActive(false);

    if (importingTemplateFile) {
      return;
    }

    const file = event.dataTransfer.files?.[0];
    if (!file) {
      return;
    }
    onImport(file);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-stone-950/35 p-4 backdrop-blur-md sm:items-center"
      onClick={onBackdropClick}
      onMouseDown={onBackdropMouseDown}
    >
      <div
        className="relative flex max-h-[86vh] w-full max-w-5xl flex-col overflow-hidden rounded-[32px] border border-stone-200/80 bg-[linear-gradient(180deg,#fffdfa,#fff7ee_18%,#ffffff_40%)] shadow-[0_30px_90px_-28px_rgba(41,37,36,0.45)]"
        onClick={onContentClick}
        onMouseDown={onContentMouseDown}
      >
        <div className="border-b border-stone-200/80 bg-white/75 px-6 py-5 backdrop-blur-md">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-xs uppercase tracking-[0.26em] text-stone-400">
                Outreach Templates
              </div>
              <h3 className="mt-2 text-2xl font-semibold text-stone-900">
                发信模板库
              </h3>
              <p className="mt-1 max-w-3xl text-sm leading-6 text-stone-500">
                模板可复用；修改不影响已创建任务。
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-900"
              aria-label="关闭模板库"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="border-b border-stone-200/80 bg-[#fffaf3] px-6 py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="text-sm font-medium text-stone-900">正在编辑</div>
              <div className="mt-1 flex flex-wrap gap-2 text-xs text-stone-500">
                <span className="rounded-full border border-stone-200 bg-white/90 px-3 py-1">
                  {editorId === "new"
                    ? form.name.trim() || "新模板（未保存）"
                    : (editingTemplate?.name ?? "请选择模板")}
                </span>
                <span className="rounded-full border border-stone-200 bg-white/90 px-3 py-1">
                  写信方式：
                  {form.outreach_generation_mode === "template"
                    ? "直接套用模板"
                    : "AI 辅助写信"}
                </span>
                <span className="rounded-full border border-stone-200 bg-white/90 px-3 py-1">
                  {form.outreach_template_subject.trim()
                    ? "主题已填写"
                    : "主题待补充"}
                </span>
                <span className="rounded-full border border-stone-200 bg-white/90 px-3 py-1">
                  {hasVisibleTemplateBody(form) ? "正文已填写" : "正文待补充"}
                </span>
              </div>
            </div>

            <label
              onDragOver={handleTemplateDragOver}
              onDragLeave={handleTemplateDragLeave}
              onDrop={handleTemplateDrop}
              aria-busy={importingTemplateFile}
              className={clsx(
                "inline-flex cursor-pointer items-center gap-2 rounded-2xl border border-dashed bg-white px-4 py-3 text-sm font-medium shadow-sm transition",
                importingTemplateFile
                  ? "cursor-wait border-stone-200 text-stone-400"
                  : isTemplateDropActive
                    ? "border-primary bg-primary/5 text-primary"
                    : "border-stone-200 text-stone-700 hover:border-stone-300 hover:text-stone-900",
              )}
            >
              {importingTemplateFile ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              {importingTemplateFile
                ? "正在导入模板文件"
                : isTemplateDropActive
                  ? "松开即可导入模板"
                  : "点击或拖拽导入模板正文"}
              <input
                type="file"
                accept={TEMPLATE_FILE_ACCEPT}
                disabled={importingTemplateFile}
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.currentTarget.value = "";
                  if (!file) {
                    return;
                  }
                  onImport(file);
                }}
              />
            </label>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="grid gap-6 lg:grid-cols-[260px,minmax(0,1fr)]">
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-semibold text-stone-900">
                  模板列表
                </div>
                <button
                  type="button"
                  onClick={onCreate}
                  disabled={templateBusy}
                  className="inline-flex items-center gap-1 rounded-xl border border-stone-200 bg-white px-3 py-2 text-xs font-medium text-stone-700 transition hover:border-stone-300 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Plus className="h-3.5 w-3.5" />
                  新建模板
                </button>
              </div>
              <div
                aria-label="模板列表"
                className={clsx(
                  "space-y-2 pr-1",
                  visibleTemplateCount > 3 && "max-h-72 overflow-y-auto",
                )}
              >
                {loadingTemplates ? (
                  <div className="flex items-center gap-2 rounded-2xl border border-stone-200 bg-white px-4 py-5 text-sm text-stone-500">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    正在加载模板
                  </div>
                ) : visibleTemplateCount === 0 ? (
                  <div className="rounded-2xl border border-dashed border-stone-200 bg-white px-4 py-5 text-sm leading-6 text-stone-500">
                    暂无模板，点击“新建模板”创建。
                  </div>
                ) : (
                  <>
                    {hasUnsavedTemplate ? (
                      <div className="w-full rounded-2xl border border-primary/30 bg-primary/5 px-4 py-3 text-left shadow-sm">
                        <div className="break-words text-sm font-medium text-stone-900">
                          {form.name.trim() || "新模板"}
                        </div>
                        <div className="mt-2">
                          <span className="rounded-full bg-sky-50 px-2 py-1 text-[11px] text-sky-700">
                            未保存
                          </span>
                        </div>
                      </div>
                    ) : null}
                    {templates.map((template) => {
                      const active = editorId === template.id;
                      return (
                        <button
                          key={template.id}
                          type="button"
                          onClick={() => onSelect(template.id)}
                          className={clsx(
                            "w-full rounded-2xl border px-4 py-3 text-left transition",
                            active
                              ? "border-primary/30 bg-primary/5 shadow-sm"
                              : "border-stone-200 bg-white hover:border-stone-300",
                          )}
                        >
                          <div className="break-words text-sm font-medium text-stone-900">
                            {template.name}
                          </div>
                          <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
                            <span
                              className={clsx(
                                "rounded-full px-2 py-1",
                                template.is_ready
                                  ? "bg-emerald-50 text-emerald-700"
                                  : "bg-amber-50 text-amber-700",
                              )}
                            >
                              {template.is_ready ? "可用于发信" : "内容待完善"}
                            </span>
                            {identityDefaultTemplateId === template.id ? (
                              <span className="rounded-full bg-sky-50 px-2 py-1 text-sky-700">
                                {identityLabel}默认
                              </span>
                            ) : null}
                            {template.is_default ? (
                              <span className="rounded-full bg-amber-50 px-2 py-1 text-amber-700">
                                全局默认
                              </span>
                            ) : null}
                          </div>
                        </button>
                      );
                    })}
                  </>
                )}
              </div>
              {archivedTemplates.length > 0 ? (
                <div className="border-t border-stone-200 pt-3">
                  <div className="text-xs font-semibold text-stone-500">
                    已归档模板
                  </div>
                  <div className="mt-2 space-y-2">
                    {archivedTemplates.map((template) => (
                      <div
                        key={template.id}
                        className="flex items-center justify-between gap-2 border-b border-stone-100 py-2"
                      >
                        <span className="min-w-0 break-words text-sm text-stone-600">
                          {template.name}
                        </span>
                        <button
                          type="button"
                          className="ui-icon-btn shrink-0"
                          aria-label={`恢复模板“${template.name}”`}
                          title="恢复模板"
                          disabled={templateBusy}
                          onClick={() => onRestore(template)}
                        >
                          <ArchiveRestore
                            aria-hidden="true"
                            className="h-4 w-4"
                          />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>

            <div className="grid min-w-0 gap-6">
              <label className="block">
                {<FieldLabel label={"模板名称"} required={true} />}
                <input
                  value={form.name}
                  onChange={(event) => onNameChange(event.target.value)}
                  className={inputClassName}
                  placeholder="例如：博士申请通用模板"
                />
              </label>

              <div>
                <div className="text-sm font-medium text-stone-900">
                  推荐写信方式
                </div>
                <p className="mt-1 text-xs leading-6 text-stone-500">
                  选择模板时会一并带入，单次任务中仍可调整。
                </p>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                {[
                  {
                    value: "llm" as const,
                    title: "AI 辅助写信",
                    description: "AI 以此模板为基础生成个性化邮件。",
                  },
                  {
                    value: "template" as const,
                    title: "直接套用模板",
                    description: "直接替换占位符生成邮件，适合固定话术。",
                  },
                ].map((option) => {
                  const active = form.outreach_generation_mode === option.value;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => onModeChange(option.value)}
                      className={clsx(
                        "rounded-[26px] border px-4 py-4 text-left transition",
                        active
                          ? "border-primary/20 bg-primary/5 shadow-sm shadow-primary/10"
                          : "border-stone-200 bg-white hover:border-stone-300 hover:bg-stone-50",
                      )}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-sm font-semibold text-stone-900">
                          {option.title}
                        </div>
                        {active ? (
                          <span className="rounded-full bg-primary px-2.5 py-1 text-[11px] font-medium text-white">
                            已选择
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-2 text-sm leading-6 text-stone-500">
                        {option.description}
                      </p>
                    </button>
                  );
                })}
              </div>

              <div className="rounded-2xl border border-stone-200 bg-white/85 px-4 py-3 text-xs leading-6 text-stone-500">
                用“占位符”插入导师姓名等变量，发送时自动替换。
              </div>

              <div className="grid gap-4">
                <SubjectTemplateInput
                  label="模板主题"
                  value={form.outreach_template_subject}
                  onChange={onSubjectChange}
                  inputClassName={`${inputClassName} pr-28`}
                  placeholder="例如：申请与 {{name}} 老师交流科研方向"
                />
                <p className="text-xs leading-6 text-stone-500">
                  导入文件仅包含正文，主题需另填。
                </p>
                <EmailTemplateEditor
                  label="模板正文"
                  html={templateEditorHtml}
                  placeholder="可将套磁信docx拖到此处导入"
                  onFileDrop={onImport}
                  onChange={onBodyChange}
                />
              </div>

              <div className="rounded-2xl border border-dashed border-stone-200 bg-white/85 px-4 py-3 text-xs leading-6 text-stone-500">
                {form.outreach_generation_mode === "template"
                  ? "选用时复制到任务，后续修改互不影响。"
                  : "AI 只在模板基础上调整称呼、个性化理由和主题。"}
              </div>

              {editingTemplate ? (
                <div className="flex flex-wrap gap-2 border-t border-stone-200 pt-4">
                  {identityDefaultTemplateId === editingTemplate.id ? (
                    <button
                      type="button"
                      onClick={onClearIdentityDefault}
                      disabled={templateBusy}
                      className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      取消{identityLabel}默认
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => onSetIdentityDefault(editingTemplate)}
                      disabled={templateBusy}
                      className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      设为{identityLabel}默认
                    </button>
                  )}
                  {!editingTemplate.is_default ? (
                    <button
                      type="button"
                      onClick={() => onSetGlobalDefault(editingTemplate.id)}
                      disabled={templateBusy}
                      className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <Star className="h-4 w-4" />
                      设为全局默认
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => onDuplicate(editingTemplate.id)}
                    disabled={templateBusy}
                    className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <Copy className="h-4 w-4" />
                    复制一份
                  </button>
                  <button
                    type="button"
                    onClick={() => onDelete(editingTemplate)}
                    disabled={templateBusy}
                    className="ui-btn-danger disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    归档模板
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <div className="border-t border-stone-200/80 bg-white/80 px-6 py-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-xs leading-6 text-stone-500">
              只填名称也可保存，缺失内容会标记为“待完善”。
            </div>
            <button
              type="button"
              onClick={onComplete}
              disabled={templateBusy}
              className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
            >
              {savingTemplate && <Loader2 className="h-4 w-4 animate-spin" />}
              {savingTemplate
                ? editorId === "new"
                  ? "正在创建"
                  : "正在保存"
                : editorId === "new"
                  ? "创建模板"
                  : "保存修改"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
