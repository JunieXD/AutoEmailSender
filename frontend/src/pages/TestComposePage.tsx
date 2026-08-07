import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Loader2, RefreshCcw, Save, Send } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useNotification } from "@/context/NotificationContext";
import { useSelectionContext } from "@/context/SelectionContext";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { AttachmentSizeSummary } from "@/components/molecules/AttachmentSizeSummary";
import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";
import { SelectionToggleButton } from "@/components/molecules/SelectionToggleButton";
import { SubjectTemplateInput } from "@/components/molecules/SubjectTemplateInput";
import { getEmailSendFailureMessage } from "@/features/email/client/getEmailSendFailureMessage";
import {
  buildLargeAttachmentWarning,
  formatFileSize,
  getSelectedAttachmentTotalBytes,
  isAttachmentTotalOverRecommendedLimit,
} from "@/features/attachments/attachmentSize";
import {
  generateTestComposeDraft,
  getTestComposeThread,
  saveTestComposeDraft,
  sendTestComposeMessage,
} from "@/lib/api/testComposeApi";
import { listOutreachTemplates } from "@/lib/api/outreachTemplates";
import { textToEmailHtml } from "@/lib/richEmail";
import { useConfirmDialog } from "@/lib/useConfirmDialog";
import {
  MATERIAL_TYPE_LABELS,
  type OutreachTemplateDTO,
  type TestComposeThreadDTO,
} from "@/types";

interface TestComposeActionOptions {
  successTitle?: string;
  errorTitle?: string;
  getFailureMessage?: (thread: TestComposeThreadDTO) => string | null;
}

export const TestComposePage = () => {
  const navigate = useNavigate();
  const { selectedIdentityId, selectedLlmProfileId } = useSelectionContext();
  const { notifyError, notifySuccess } = useNotification();
  const { confirm, dialog: confirmDialog } = useConfirmDialog();
  const [thread, setThread] = useState<TestComposeThreadDTO | null>(null);
  const [subject, setSubject] = useState("");
  const [bodyText, setBodyText] = useState("");
  const [bodyHtml, setBodyHtml] = useState("");
  const [outreachTemplates, setOutreachTemplates] = useState<
    OutreachTemplateDTO[]
  >([]);
  const [selectedOutreachTemplateId, setSelectedOutreachTemplateId] = useState<
    number | null
  >(null);
  const [loadingOutreachTemplates, setLoadingOutreachTemplates] =
    useState(true);
  const [outreachTemplatesLoaded, setOutreachTemplatesLoaded] = useState(false);
  const activeOutreachTemplates = useMemo(
    () => outreachTemplates.filter((template) => !template.archived_at),
    [outreachTemplates],
  );
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState(false);

  const syncDraft = useCallback((nextThread: TestComposeThreadDTO) => {
    setThread(nextThread);
    setSubject(nextThread.draft.subject ?? "");
    setBodyText(nextThread.draft.body_text);
    setBodyHtml(nextThread.draft.body_html || textToEmailHtml(nextThread.draft.body_text));
    setSelectedOutreachTemplateId(
      nextThread.draft.outreach_template_id ?? null,
    );
    setSelectedMaterialIds(nextThread.draft.selected_material_ids);
  }, []);

  const loadThread = useCallback(async () => {
    if (!selectedIdentityId || !selectedLlmProfileId) {
      setThread(null);
      return;
    }

    setLoading(true);
    try {
      const nextThread = await getTestComposeThread(selectedIdentityId, selectedLlmProfileId);
      syncDraft(nextThread);
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载测试写信页失败";
      notifyError("加载测试写信页失败", message);
    } finally {
      setLoading(false);
    }
  }, [notifyError, selectedIdentityId, selectedLlmProfileId, syncDraft]);

  useEffect(() => {
    void loadThread();
  }, [loadThread]);

  useEffect(() => {
    let ignore = false;
    const loadTemplates = async () => {
      setLoadingOutreachTemplates(true);
      try {
        const templates = await listOutreachTemplates(true);
        if (!ignore) {
          setOutreachTemplates(templates);
          setOutreachTemplatesLoaded(true);
        }
      } catch (error) {
        if (!ignore) {
          setOutreachTemplatesLoaded(false);
          notifyError(
            "加载发信模板失败",
            error instanceof Error ? error.message : "加载发信模板失败",
          );
        }
      } finally {
        if (!ignore) {
          setLoadingOutreachTemplates(false);
        }
      }
    };
    void loadTemplates();
    return () => {
      ignore = true;
    };
  }, [notifyError]);

  useEffect(() => {
    if (
      loadingOutreachTemplates ||
      !outreachTemplatesLoaded ||
      selectedOutreachTemplateId === null ||
      outreachTemplates.some(
        (template) => template.id === selectedOutreachTemplateId,
      )
    ) {
      return;
    }
    setSelectedOutreachTemplateId(null);
  }, [
    loadingOutreachTemplates,
    outreachTemplatesLoaded,
    outreachTemplates,
    selectedOutreachTemplateId,
  ]);

  const runAction = useCallback(
    async (
      action: () => Promise<TestComposeThreadDTO>,
      options: TestComposeActionOptions = {},
    ) => {
      setActing(true);
      try {
        const nextThread = await action();
        syncDraft(nextThread);
        const failureMessage = options.getFailureMessage?.(nextThread) ?? null;
        if (failureMessage) {
          notifyError(options.errorTitle ?? "测试写信操作失败", failureMessage);
        } else if (options.successTitle) {
          notifySuccess(options.successTitle, "测试写信内容已更新。");
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "测试写信操作失败";
        notifyError(options.errorTitle ?? "测试写信操作失败", message);
      } finally {
        setActing(false);
      }
    },
    [notifyError, notifySuccess, syncDraft],
  );

  const selectedMaterialSet = useMemo(() => new Set(selectedMaterialIds), [selectedMaterialIds]);
  const selectedAttachmentTotalBytes = useMemo(
    () =>
      getSelectedAttachmentTotalBytes(
        thread?.material_options ?? [],
        selectedMaterialIds,
      ),
    [selectedMaterialIds, thread?.material_options],
  );

  const identityProfileName = thread?.identity.profile_name || thread?.identity.name || "";
  const identitySenderName = thread?.identity.sender_name || identityProfileName;
  const selectedOutreachTemplate =
    outreachTemplates.find(
      (template) => template.id === selectedOutreachTemplateId,
    ) ?? null;

  const applySelectedOutreachTemplate = (templateId: number | null) => {
    setSelectedOutreachTemplateId(templateId);
    if (templateId === null) {
      return;
    }
    const template = outreachTemplates.find((item) => item.id === templateId);
    if (!template) {
      return;
    }
    const nextBodyText = template.body_text ?? "";
    setSubject(template.subject ?? "");
    setBodyText(nextBodyText);
    setBodyHtml(
      template.body_html ??
        (nextBodyText ? textToEmailHtml(nextBodyText) : ""),
    );
  };

  if (!selectedIdentityId || !selectedLlmProfileId) {
    return (
      <main className="mx-auto max-w-4xl px-6 py-10">
        <div className="rounded-3xl border border-dashed border-stone-300 bg-[#fcfbf8] p-10 text-center">
          <h1 className="text-2xl font-semibold text-stone-900">选择身份和模型</h1>
          <p className="mt-3 text-sm text-stone-600">使用顶部选择的身份和模型。</p>
        </div>
      </main>
    );
  }

  const generateDraft = () =>
    runAction(
      () =>
        generateTestComposeDraft(
          selectedIdentityId,
          selectedLlmProfileId,
          selectedOutreachTemplateId,
          {
            subject: subject.trim() || null,
            body_text: bodyText,
            body_html: bodyHtml,
          },
        ),
      { successTitle: "已生成测试草稿" },
    );

  const saveDraft = () =>
    runAction(
      () =>
        saveTestComposeDraft(selectedIdentityId, selectedLlmProfileId, {
          outreach_template_id: selectedOutreachTemplateId,
          subject: subject.trim() || null,
          body_text: bodyText,
          body_html: bodyHtml,
          selected_material_ids: selectedMaterialIds,
        }),
      { successTitle: "已保存测试草稿" },
    );

  const sendMessage = async () => {
    if (isAttachmentTotalOverRecommendedLimit(selectedAttachmentTotalBytes)) {
      const confirmed = await confirm({
        title: "附件超过 1 MB，仍要发送测试邮件吗？",
        description:
          buildLargeAttachmentWarning(selectedAttachmentTotalBytes) ?? undefined,
        confirmLabel: "仍然发送",
        cancelLabel: "返回调整",
      });
      if (!confirmed) {
        return;
      }
    }

    await runAction(
      () =>
        sendTestComposeMessage(selectedIdentityId, selectedLlmProfileId, {
          outreach_template_id: selectedOutreachTemplateId,
          subject: subject.trim() || null,
          body_text: bodyText,
          body_html: bodyHtml,
          selected_material_ids: selectedMaterialIds,
        }),
      {
        successTitle: "测试邮件已发送",
        errorTitle: "测试邮件发送失败",
        getFailureMessage: (nextThread) => {
          const latestMessage = nextThread.history[0];
          return getEmailSendFailureMessage(
            latestMessage?.status,
            latestMessage?.failure_summary,
          );
        },
      },
    );
  };

  return (
    <>
      <main className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-7xl flex-col px-6 py-8">
      {loading || !thread ? (
        <div className="flex flex-1 items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载测试写信页...
        </div>
      ) : (
        <>
          <header className="mb-6 flex flex-wrap items-end justify-between gap-5 border-b border-stone-200 pb-5">
            <div>
              <button
                type="button"
                onClick={() => navigate(-1)}
                className="mb-5 inline-flex items-center gap-2 text-sm font-medium text-stone-500 transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2"
              >
                <ArrowLeft className="h-4 w-4" />
                返回
              </button>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">
                测试邮件工作台
              </div>
              <h1 className="mt-2 text-3xl font-semibold text-stone-950">测试写信</h1>
              <p className="mt-2 text-sm leading-6 text-stone-600">
                发送到自己的邮箱，用于检查模板、附件和发信设置。
              </p>
            </div>
            <div className="grid min-w-full gap-2 text-sm sm:min-w-[520px] sm:grid-cols-3">
              <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3 shadow-sm">
                <div className="text-xs text-stone-500">身份</div>
                <div className="mt-1 truncate font-medium text-stone-900">
                  {identityProfileName}
                </div>
              </div>
              <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3 shadow-sm">
                <div className="text-xs text-stone-500">模型 / {thread.llm_profile.name}</div>
                <div className="mt-1 truncate font-medium text-stone-900">
                  {thread.llm_profile.model_name}
                </div>
              </div>
              <div className="rounded-2xl border border-primary/20 bg-primary/5 px-4 py-3 shadow-sm">
                <div className="text-xs text-primary">测试收件邮箱</div>
                <div className="mt-1 truncate font-medium text-stone-900">
                  {thread.identity.email_address}
                </div>
              </div>
            </div>
          </header>

          <div className="grid flex-1 gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
            <section className="rounded-3xl border border-stone-200 bg-white p-5 shadow-sm">
              <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-xl font-semibold text-stone-900">邮件内容</h2>
                  <p className="mt-1 text-sm text-stone-500">
                    收件人固定为 {thread.identity.email_address}
                  </p>
                </div>
                <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1 text-xs text-stone-500">
                  草稿可随时保存
                </span>
              </div>

              <div className="space-y-4">
                <div className="rounded-2xl border border-stone-200 bg-stone-50/80 p-4">
                  <NativeSelectField
                    label="测试使用的发信模板"
                    value={
                      selectedOutreachTemplateId === null
                        ? ""
                        : String(selectedOutreachTemplateId)
                    }
                    disabled={loadingOutreachTemplates || acting}
                    onChange={(event) =>
                      applySelectedOutreachTemplate(
                        event.target.value ? Number(event.target.value) : null,
                      )
                    }
                  >
                    <option value="">保留当前草稿内容</option>
                    {selectedOutreachTemplate?.archived_at ? (
                      <option value={selectedOutreachTemplate.id} disabled>
                        {selectedOutreachTemplate.name} · 已删除（仅保留历史来源）
                      </option>
                    ) : null}
                    {activeOutreachTemplates.map((template) => (
                      <option key={template.id} value={template.id}>
                        {template.name}
                        {template.is_default ? " · 全局默认" : ""}
                        {template.is_ready ? "" : " · 内容待完善"}
                      </option>
                    ))}
                  </NativeSelectField>
                  <p className="mt-2 text-xs leading-6 text-stone-500">
                    {loadingOutreachTemplates
                      ? "正在加载模板库…"
                      : selectedOutreachTemplate
                        ? `已带入“${selectedOutreachTemplate.name}”。这里的修改只保存到测试草稿。`
                        : "当前内容作为独立测试草稿保存，不会改动模板库。"}
                  </p>
                </div>
                <SubjectTemplateInput
                  label="邮件主题"
                  value={subject}
                  onChange={setSubject}
                  placeholder="测试邮件主题"
                />
                <EmailTemplateEditor
                  label="邮件正文"
                  html={bodyHtml}
                  onChange={({ html, text }) => {
                    setBodyHtml(html);
                    setBodyText(text);
                  }}
                />
              </div>
            </section>

            <aside className="space-y-4">
              <section className="rounded-3xl border border-stone-200 bg-white p-5 shadow-sm">
                <h2 className="text-base font-semibold text-stone-900">发送信息</h2>
                <div className="mt-4 space-y-3 text-sm text-stone-600">
                  <div className="flex items-center justify-between gap-3">
                    <span>身份</span>
                    <span className="truncate font-medium text-stone-900">
                      {identityProfileName}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span>邮箱</span>
                    <span className="truncate font-medium text-stone-900">
                      {thread.identity.email_address}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span>模型</span>
                    <span className="truncate font-medium text-stone-900">
                      {thread.llm_profile.name}
                    </span>
                  </div>
                </div>
                <div className="mt-4 rounded-2xl border border-primary/15 bg-primary/5 px-4 py-3 text-xs leading-6 text-stone-600">
                  <div>{"{{name}} 测试时显示为「测试收件人」"}</div>
                  <div>发件人姓名：{identitySenderName}</div>
                </div>
              </section>

              <section className="rounded-3xl border border-stone-200 bg-white p-5 shadow-sm">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="text-base font-semibold text-stone-900">随信附件</h2>
                  <span className="text-xs text-stone-500">随邮件发送</span>
                </div>
                <div className="mt-4 space-y-2">
                  {thread.material_options.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-stone-300 bg-stone-50 px-4 py-4 text-sm text-stone-500">
                      暂无可发送材料。
                    </div>
                  ) : (
                    thread.material_options.map((material) => {
                      const checked = selectedMaterialSet.has(material.id);
                      return (
                        <label
                          key={material.id}
                          className="flex items-center justify-between gap-3 rounded-2xl border border-stone-200 px-3 py-3 text-sm text-stone-700"
                        >
                          <span>
                            <span className="block font-medium">{material.display_name}</span>
                            <span className="mt-1 block text-xs text-stone-500">
                              {MATERIAL_TYPE_LABELS[material.material_type]} · {formatFileSize(material.size_bytes)}
                            </span>
                          </span>
                          <SelectionToggleButton
                            label={`选择附件 ${material.display_name}`}
                            selected={checked}
                            semantics="checkbox"
                            size="md"
                            onToggle={() => {
                              setSelectedMaterialIds((previous) =>
                                checked
                                  ? previous.filter((item) => item !== material.id)
                                  : [...previous, material.id],
                              );
                            }}
                          />
                        </label>
                      );
                    })
                  )}
                </div>
                <AttachmentSizeSummary
                  selectedCount={selectedMaterialIds.length}
                  totalSizeBytes={selectedAttachmentTotalBytes}
                  className="mt-3"
                />
              </section>

              <section className="rounded-3xl border border-stone-200 bg-white p-5 shadow-sm">
                <h2 className="text-base font-semibold text-stone-900">发送历史</h2>
                <div className="mt-4 max-h-72 space-y-3 overflow-y-auto pr-1">
                  {thread.history.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-stone-300 bg-stone-50 px-4 py-4 text-sm text-stone-500">
                      还没有测试发送记录。
                    </div>
                  ) : (
                    thread.history.map((message) => (
                      <div
                        key={message.id}
                        className="rounded-2xl border border-stone-200 bg-stone-50 px-4 py-4 text-sm text-stone-700"
                      >
                        <div className="font-medium text-stone-900">
                          {message.subject ?? "未命名测试邮件"}
                        </div>
                        <div className="mt-1 text-xs text-stone-500">
                          {message.recipient_email}
                        </div>
                        <div className="mt-2 text-xs text-stone-500">状态：{message.status}</div>
                      </div>
                    ))
                  )}
                </div>
              </section>
            </aside>
          </div>

          <section
            role="region"
            aria-label="测试写信操作"
            className="sticky bottom-4 mt-5 flex flex-wrap items-center justify-between gap-3 rounded-3xl border border-stone-200 bg-white/95 px-4 py-3 shadow-[0_18px_40px_-28px_rgba(41,37,36,0.45)] backdrop-blur"
          >
            <div className="text-sm text-stone-500">
              将发送到 <span className="font-medium text-stone-800">{thread.identity.email_address}</span>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void generateDraft()}
                disabled={acting}
                className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
              >
                <RefreshCcw className="h-4 w-4" />
                生成测试草稿
              </button>
              <button
                type="button"
                onClick={() => void saveDraft()}
                disabled={acting}
                className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Save className="h-4 w-4" />
                保存草稿
              </button>
              <button
                type="button"
                onClick={() => void sendMessage()}
                disabled={acting}
                className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Send className="h-4 w-4" />
                发送测试邮件
              </button>
            </div>
          </section>
        </>
      )}
      </main>
      {confirmDialog}
    </>
  );
};
