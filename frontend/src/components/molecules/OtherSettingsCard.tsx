import { useCallback, useEffect, useMemo, useState, type TransitionEvent } from "react";
import clsx from "clsx";
import { ChevronDown, Loader2, Save, Settings } from "lucide-react";

import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import {
  defaultDraftRewritePreferences,
  getRuntimeSettings,
  updateRuntimeSettings,
  type RuntimeSettingsDTO,
  type RuntimeSettingsUpdateDTO,
} from "@/lib/api/runtimeSettings";
import type { DesktopStartupAtLoginStatus } from "@/types/desktop";

type RuntimeSettingsKey = keyof RuntimeSettingsUpdateDTO;
type TextSettingsKey = {
  [Key in RuntimeSettingsKey]: RuntimeSettingsUpdateDTO[Key] extends string ? Key : never;
}[RuntimeSettingsKey];
type NumberSettingsKey = {
  [Key in RuntimeSettingsKey]: RuntimeSettingsUpdateDTO[Key] extends number ? Key : never;
}[RuntimeSettingsKey];
type PreferenceSettingsKey = Exclude<TextSettingsKey, "draft_custom_instruction">;
type FormState = Record<RuntimeSettingsKey, string>;

const numberFields: Array<{
  key: NumberSettingsKey;
  label: string;
  hint: string;
  min: number;
  max: number;
  defaultValue: number;
  restartRequired?: boolean;
}> = [
  {
    key: "draft_max_tokens",
    label: "AI 草稿输出 token 上限",
    hint: "LLM 生成邮件草稿时传给模型的 max tokens，全局生效。",
    min: 256,
    max: 32000,
    defaultValue: 6000,
  },
  {
    key: "match_analysis_job_item_concurrency",
    label: "批量匹配分析并发数",
    hint: "单个批量匹配任务内同时分析的导师数量，保存后下一轮后端任务生效。",
    min: 1,
    max: 20,
    defaultValue: 3,
  },
  {
    key: "batch_draft_generation_concurrency",
    label: "批量邮件 LLM 草稿并发数",
    hint: "后台批量生成 AI 草稿时同时执行的 LLM 请求数量，保存后下一轮任务生效。",
    min: 1,
    max: 20,
    defaultValue: 3,
  },
  {
    key: "match_analysis_job_interval_seconds",
    label: "批量匹配轮询间隔",
    hint: "后端检查待处理批量匹配任务的间隔秒数。",
    min: 1,
    max: 300,
    defaultValue: 10,
    restartRequired: true,
  },
  {
    key: "match_analysis_job_worker_count",
    label: "批量匹配 Worker 数",
    hint: "同时处理的批量匹配任务数量。",
    min: 1,
    max: 8,
    defaultValue: 1,
    restartRequired: true,
  },
  {
    key: "crawler_worker_count",
    label: "智能抓取任务并发数",
    hint: "同时运行的抓取任务数量。",
    min: 1,
    max: 8,
    defaultValue: 2,
    restartRequired: true,
  },
  {
    key: "crawler_profile_enrichment_concurrency",
    label: "详情页补全并发数",
    hint: "单个抓取任务内同时补全的详情页数量，保存后下一轮抓取生效。",
    min: 1,
    max: 20,
    defaultValue: 3,
  },
  {
    key: "crawler_host_concurrency",
    label: "同站点抓取并发数",
    hint: "同一域名同时抓取的详情页数量，建议保持 1。",
    min: 1,
    max: 8,
    defaultValue: 1,
  },
];

const preferenceFields: Array<{
  key: PreferenceSettingsKey;
  label: string;
  hint: string;
  options: Array<{ value: string; label: string }>;
}> = [
  {
    key: "draft_rewrite_intensity",
    label: "改写强度",
    hint: "控制 AI 对模板措辞的调整幅度。",
    options: [
      { value: "light", label: "轻微" },
      { value: "moderate", label: "中等" },
      { value: "strong", label: "明显" },
    ],
  },
  {
    key: "draft_rewrite_tone",
    label: "语气",
    hint: "控制邮件表达的沟通气质。",
    options: [
      { value: "polite", label: "礼貌" },
      { value: "professional", label: "专业" },
      { value: "friendly", label: "亲和" },
    ],
  },
  {
    key: "draft_rewrite_formality",
    label: "正式程度",
    hint: "控制句式接近自然表达还是正式学术邮件。",
    options: [
      { value: "natural", label: "更自然" },
      { value: "balanced", label: "默认" },
      { value: "formal", label: "更正式" },
    ],
  },
  {
    key: "draft_rewrite_length",
    label: "长度",
    hint: "控制 AI 是否压缩或展开模板内容。",
    options: [
      { value: "shorter", label: "更短" },
      { value: "default", label: "默认" },
      { value: "more_detailed", label: "更详细" },
    ],
  },
  {
    key: "draft_rewrite_specificity",
    label: "具体性",
    hint: "控制匹配理由的细节密度。",
    options: [
      { value: "concise", label: "概括" },
      { value: "balanced", label: "平衡" },
      { value: "detailed", label: "细节更足" },
    ],
  },
  {
    key: "draft_template_preservation",
    label: "模板保留度",
    hint: "控制 AI 对模板结构和主要话术的保留程度。",
    options: [
      { value: "structure_first", label: "优先保留结构" },
      { value: "balanced", label: "平衡" },
      { value: "content_first", label: "更重内容表达" },
    ],
  },
];

const emptyForm = [...numberFields, ...preferenceFields].reduce((state, field) => {
  state[field.key] = "";
  return state;
}, {} as FormState);
emptyForm.draft_custom_instruction = "";

export function OtherSettingsCard() {
  const [open, setOpen] = useState(false);
  const [renderContent, setRenderContent] = useState(false);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);
  const [startupStatus, setStartupStatus] = useState<DesktopStartupAtLoginStatus | null>(null);
  const [startupLoading, setStartupLoading] = useState(false);
  const [startupSaving, setStartupSaving] = useState(false);
  const [startupError, setStartupError] = useState<string | null>(null);

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const settings = await getRuntimeSettings();
      setForm(toFormState(settings));
      setUpdatedAt(settings.updated_at);
    } catch (loadError) {
      setError(getErrorMessage(loadError, "加载其他设置失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open || loading || updatedAt !== null || error) {
      return;
    }
    void loadSettings();
  }, [error, loadSettings, loading, open, updatedAt]);

  const loadStartupStatus = useCallback(async () => {
    const api = window.autoEmailSender;
    if (!api?.getStartupAtLoginStatus || !api.setStartupAtLoginEnabled) {
      setStartupStatus({
        supported: false,
        enabled: false,
        message: "仅安装后的 Windows 桌面版支持开机自启动。",
      });
      return;
    }

    setStartupLoading(true);
    setStartupError(null);
    try {
      setStartupStatus(await api.getStartupAtLoginStatus());
    } catch (statusError) {
      setStartupError(getErrorMessage(statusError, "读取开机自启动状态失败"));
    } finally {
      setStartupLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open || startupLoading || startupStatus !== null) {
      return;
    }
    void loadStartupStatus();
  }, [loadStartupStatus, open, startupLoading, startupStatus]);

  const summary = useMemo(() => {
    const matchConcurrency = form.match_analysis_job_item_concurrency || "3";
    const crawlConcurrency = form.crawler_profile_enrichment_concurrency || "3";
    const draftMaxTokens = form.draft_max_tokens || "6000";
    const draftConcurrency = form.batch_draft_generation_concurrency || "3";
    const draftMode =
      getPreferenceOptionLabel("draft_rewrite_intensity", form.draft_rewrite_intensity) || "默认";
    return `草稿 ${draftMaxTokens} / 草稿并发 ${draftConcurrency} / 偏好 ${draftMode} / 匹配 ${matchConcurrency} / 抓取 ${crawlConcurrency}`;
  }, [form]);
  const draftPreview = useMemo(() => buildDraftPreferencePreview(form), [form]);

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
    if (open || event.propertyName !== "grid-template-rows") {
      return;
    }
    setRenderContent(false);
  };

  const handleChange = (key: RuntimeSettingsKey, value: string) => {
    setSavedMessage(null);
    setForm((current) => ({
      ...current,
      [key]: value,
    }));
  };

  const resetDraftPreferences = () => {
    setSavedMessage(null);
    setForm((current) => ({
      ...current,
      ...defaultDraftRewritePreferences,
    }));
  };

  const handleSubmit = async () => {
    const payload = toUpdatePayload(form);
    setSaving(true);
    setError(null);
    setSavedMessage(null);
    try {
      const saved = await updateRuntimeSettings(payload);
      setForm(toFormState(saved));
      setUpdatedAt(saved.updated_at);
      setSavedMessage("设置已保存");
    } catch (saveError) {
      setError(getErrorMessage(saveError, "保存其他设置失败"));
    } finally {
      setSaving(false);
    }
  };

  const handleStartupChange = async (enabled: boolean) => {
    const api = window.autoEmailSender;
    if (!api?.setStartupAtLoginEnabled) {
      setStartupError("当前环境不支持开机自启动设置");
      return;
    }

    setStartupSaving(true);
    setStartupError(null);
    try {
      setStartupStatus(await api.setStartupAtLoginEnabled(enabled));
    } catch (saveError) {
      setStartupError(getErrorMessage(saveError, "保存开机自启动设置失败"));
    } finally {
      setStartupSaving(false);
    }
  };

  return (
    <section className="min-w-0 overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-sm">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="other-settings-card-content"
        onClick={toggleOpen}
        className="collapsible-card-toggle flex w-full items-center justify-between gap-4 px-6 py-5 text-left transition hover:bg-stone-50 active:bg-stone-50"
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-stone-900">其他设置</h2>
            <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
              {summary}
            </span>
          </div>
          <p className="mt-2 text-sm leading-6 text-stone-600">
            调整 AI 草稿 token 上限、改写偏好、批量匹配和智能抓取的并发限制。
          </p>
        </div>
        <ChevronDown
          className={clsx(
            "h-5 w-5 shrink-0 text-stone-500 transition-transform",
            open ? "rotate-180" : "rotate-0",
          )}
        />
      </button>

      {renderContent ? (
        <div
          id="other-settings-card-content"
          data-state={open ? "open" : "closed"}
          onTransitionEnd={handleContentTransitionEnd}
          className="collapsible-card-content"
        >
          <div className="collapsible-card-body min-h-0 px-6">
            {loading ? (
              <div className="mt-5 flex items-center justify-center gap-2 rounded-2xl border border-stone-200 bg-stone-50 px-4 py-8 text-sm text-stone-500">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在加载其他设置...
              </div>
            ) : (
              <div className="mt-5 space-y-5">
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {numberFields.map((field) => (
                    <label
                      key={field.key}
                      className="block rounded-2xl border border-stone-200 bg-[#fcfbf8] px-4 py-4"
                    >
                      <span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-stone-900">
                        <span>{field.label}</span>
                        {field.restartRequired ? (
                          <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                            重启生效
                          </span>
                        ) : null}
                      </span>
                      <input
                        aria-label={field.label}
                        type="number"
                        min={field.min}
                        max={field.max}
                        value={form[field.key]}
                        onChange={(event) => handleChange(field.key, event.target.value)}
                        className="mt-3 h-10 w-full rounded-xl border border-stone-200 bg-white px-3 text-sm text-stone-800 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                      />
                      <span className="mt-2 block text-xs leading-5 text-stone-500">
                        {field.hint}
                      </span>
                    </label>
                  ))}
                </div>

                <div className="space-y-4 border-t border-stone-200 pt-5">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h3 className="text-base font-semibold text-stone-900">草稿改写偏好</h3>
                      <p className="mt-1 text-sm leading-6 text-stone-600">
                        调整 AI 润色模板时的表达方式。
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={resetDraftPreferences}
                      className="ui-btn-secondary"
                    >
                      恢复草稿默认
                    </button>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                    {preferenceFields.map((field) => (
                      <div
                        key={field.key}
                        className="block rounded-2xl border border-stone-200 bg-[#fcfbf8] px-4 py-4"
                      >
                        <NativeSelectField
                          label={field.label}
                          ariaLabel={field.label}
                          value={form[field.key]}
                          onChange={(event) => handleChange(field.key, event.target.value)}
                          shellClassName="h-10"
                        >
                          {field.options.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </NativeSelectField>
                        <span className="mt-2 block text-xs leading-5 text-stone-500">
                          {field.hint}
                        </span>
                      </div>
                    ))}
                  </div>

                  <label className="block rounded-2xl border border-stone-200 bg-[#fcfbf8] px-4 py-4">
                    <span className="text-sm font-semibold text-stone-900">
                      AI 草稿补充要求
                    </span>
                    <textarea
                      value={form.draft_custom_instruction}
                      maxLength={2000}
                      onChange={(event) =>
                        handleChange("draft_custom_instruction", event.target.value)
                      }
                      className="mt-3 min-h-32 w-full resize-y rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm leading-6 text-stone-800 outline-none transition placeholder:text-stone-400 focus:border-primary focus:ring-2 focus:ring-primary/20"
                      placeholder="例如：少用套话，语气自然一点；结尾保持简短，不要显得过度热情。"
                    />
                    <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
                      <span className="text-xs leading-5 text-stone-500">
                        这段内容会作为补充要求注入到系统提示词中，不会覆盖模板保护、输出格式和安全约束。
                      </span>
                      <div className="flex items-center gap-2">
                        <span className="text-xs leading-5 text-stone-500">
                          {form.draft_custom_instruction.length}/2000
                        </span>
                        <button
                          type="button"
                          onClick={() => void handleSubmit()}
                          disabled={saving}
                          className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {saving ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Save className="h-4 w-4" />
                          )}
                          保存补充要求
                        </button>
                      </div>
                    </div>
                  </label>

                  <div className="rounded-2xl border border-stone-200 bg-white px-4 py-4">
                    <h4 className="text-sm font-semibold text-stone-900">示例效果</h4>
                    <div className="mt-3 space-y-3 text-sm leading-6">
                      <div>
                        <div className="text-xs font-semibold text-stone-500">原模板意图</div>
                        <p className="mt-1 text-stone-600">{draftPreview.originalIntent}</p>
                      </div>
                      <div>
                        <div className="text-xs font-semibold text-stone-500">模拟改写结果</div>
                        <p className="mt-1 text-stone-700">{draftPreview.rewrittenText}</p>
                      </div>
                      <div>
                        <div className="text-xs font-semibold text-stone-500">当前规则</div>
                        <p className="mt-1 text-stone-600">{draftPreview.ruleSummary}</p>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="space-y-3 border-t border-stone-200 pt-5">
                  <div>
                    <h3 className="text-base font-semibold text-stone-900">系统设置</h3>
                    <p className="mt-1 text-sm leading-6 text-stone-600">
                      控制桌面端与系统集成相关的选项。
                    </p>
                  </div>
                  <label className="flex items-start gap-3 rounded-2xl border border-stone-200 bg-[#fcfbf8] px-4 py-4">
                    <input
                      type="checkbox"
                      checked={Boolean(startupStatus?.supported && startupStatus.enabled)}
                      disabled={
                        startupLoading ||
                        startupSaving ||
                        !startupStatus?.supported ||
                        !window.autoEmailSender?.setStartupAtLoginEnabled
                      }
                      onChange={(event) => void handleStartupChange(event.target.checked)}
                      className="mt-1 h-4 w-4 rounded border-stone-300 text-primary focus:ring-primary/30 disabled:cursor-not-allowed disabled:opacity-60"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-semibold text-stone-900">开机自启动</span>
                      <span className="mt-1 block text-xs leading-5 text-stone-500">
                        {startupLoading
                          ? "正在读取开机自启动状态..."
                          : startupSaving
                            ? "正在保存开机自启动设置..."
                            : startupStatus?.message ??
                              "开启后，Auto Email Sender 会在 Windows 登录后自动启动并停留在托盘。"}
                      </span>
                    </span>
                  </label>
                  {startupError ? (
                    <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                      {startupError}
                    </div>
                  ) : null}
                </div>

                {error ? (
                  <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                    {error}
                  </div>
                ) : null}
                {savedMessage ? (
                  <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
                    {savedMessage}
                  </div>
                ) : null}

                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 pt-5">
                  <div className="flex min-w-0 items-center gap-2 text-xs text-stone-500">
                    <Settings className="h-4 w-4 shrink-0" />
                    <span className="truncate">
                      最后更新：{updatedAt ? new Date(updatedAt).toLocaleString("zh-CN") : "尚未加载"}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleSubmit()}
                    disabled={saving}
                    className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {saving ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Save className="h-4 w-4" />
                    )}
                    保存设置
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function toFormState(settings: RuntimeSettingsDTO): FormState {
  const state = { ...emptyForm };
  for (const field of numberFields) {
    state[field.key] = String(getNumberSetting(settings, field.key, field.defaultValue));
  }
  for (const field of preferenceFields) {
    state[field.key] = getPreferenceSetting(settings, field.key);
  }
  state.draft_custom_instruction = settings.draft_custom_instruction ?? "";
  return state;
}

function getNumberSetting(
  settings: RuntimeSettingsDTO,
  key: NumberSettingsKey,
  fallback: number,
): number {
  const value = settings[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function getPreferenceSetting(settings: RuntimeSettingsDTO, key: PreferenceSettingsKey): string {
  const value = settings[key];
  return typeof value === "string" && value ? value : defaultDraftRewritePreferences[key];
}

function toUpdatePayload(form: FormState): RuntimeSettingsUpdateDTO {
  const payload = {} as RuntimeSettingsUpdateDTO;
  for (const field of numberFields) {
    const value = Number(form[field.key]);
    payload[field.key] = Number.isFinite(value) ? value : field.min;
  }
  const preferencePayload = payload as Record<
    PreferenceSettingsKey,
    RuntimeSettingsUpdateDTO[PreferenceSettingsKey]
  >;
  for (const field of preferenceFields) {
    const defaultValue = defaultDraftRewritePreferences[field.key];
    const value = field.options.some((option) => option.value === form[field.key])
      ? form[field.key]
      : defaultValue;
    preferencePayload[field.key] = value as RuntimeSettingsUpdateDTO[PreferenceSettingsKey];
  }
  payload.draft_custom_instruction = form.draft_custom_instruction.trim();
  return payload;
}

function getPreferenceOptionLabel(key: PreferenceSettingsKey, value: string): string | null {
  const field = preferenceFields.find((candidate) => candidate.key === key);
  return field?.options.find((option) => option.value === value)?.label ?? null;
}

type DraftPreferencePreview = {
  originalIntent: string;
  rewrittenText: string;
  ruleSummary: string;
};

const previewOptions = {
  intensity: {
    light: {
      summary: "轻微调整：尽量保留原句，只做顺滑润色。",
      intent: "我也想",
    },
    moderate: {
      summary: "中等调整：保留核心信息，同时优化句式和衔接。",
      intent: "我希望",
    },
    strong: {
      summary: "更主动：明显重写表达，让动机和匹配理由更突出。",
      intent: "我很希望主动",
    },
  },
  tone: {
    polite: {
      summary: "礼貌语气：表达克制、尊重，减少压迫感。",
      opener: "老师您好，我认真关注到您在人工智能方向的研究",
    },
    professional: {
      summary: "专业语气：突出研究判断和申请目标。",
      opener: "老师您好，基于我对您课题组人工智能研究方向的了解",
    },
    friendly: {
      summary: "表达更亲近：语气更自然，降低距离感。",
      opener: "老师您好，我最近读到您在人工智能方向的工作，感觉和自己的经历很有连接",
    },
  },
  formality: {
    natural: {
      summary: "自然表达：句子更接近日常邮件。",
      request: "和您进一步交流",
    },
    balanced: {
      summary: "平衡正式度：自然但保持申请邮件的边界。",
      request: "进一步了解课题组的研究机会",
    },
    formal: {
      summary: "正式学术邮件：措辞更完整、边界更清晰。",
      request: "进一步了解贵课题组的研究计划与招生安排",
    },
  },
  length: {
    shorter: {
      summary: "更短：压缩背景，只保留动机和请求。",
      detail: "",
    },
    default: {
      summary: "默认长度：保留一条背景信息和一条请求。",
      detail: "我过往项目主要关注智能体系统和研究型工具开发，",
    },
    more_detailed: {
      summary: "增加背景和期待：补充经历、兴趣和后续沟通目标。",
      detail: "我过往项目主要关注智能体系统、研究型工具开发和自动化信息处理，也希望把这些经历延伸到更系统的科研训练中，",
    },
  },
  specificity: {
    concise: {
      summary: "概括匹配：只说明方向相关。",
      match: "我对相关研究方向很感兴趣，",
    },
    balanced: {
      summary: "平衡匹配：说明经历和方向之间的关系。",
      match: "我希望结合自己的项目经历理解课题组当前关注的问题，",
    },
    detailed: {
      summary: "点出研究交集：强调方法、场景或问题意识的连接。",
      match: "我尤其关注智能体评估、科研工作流和模型应用落地之间的交集，",
    },
  },
  preservation: {
    structure_first: {
      summary: "优先保留结构：沿用问候、背景、请求的模板顺序。",
      closer: "因此想请教是否有进一步交流的可能。",
    },
    balanced: {
      summary: "平衡保留：保留主要话术，但允许调整段落重心。",
      closer: "如果方便，我希望进一步了解是否有合适的交流或申请机会。",
    },
    content_first: {
      summary: "优先重组内容：围绕匹配理由重新安排模板信息。",
      closer: "如果这些方向与课题组近期计划契合，我希望后续能进一步交流。",
    },
  },
} as const;

function buildDraftPreferencePreview(form: FormState): DraftPreferencePreview {
  const intensity = getPreviewOption(previewOptions.intensity, form.draft_rewrite_intensity, "moderate");
  const tone = getPreviewOption(previewOptions.tone, form.draft_rewrite_tone, "polite");
  const formality = getPreviewOption(previewOptions.formality, form.draft_rewrite_formality, "balanced");
  const length = getPreviewOption(previewOptions.length, form.draft_rewrite_length, "default");
  const specificity = getPreviewOption(previewOptions.specificity, form.draft_rewrite_specificity, "balanced");
  const preservation = getPreviewOption(
    previewOptions.preservation,
    form.draft_template_preservation,
    "structure_first",
  );

  return {
    originalIntent:
      "老师您好，我对您的人工智能研究很感兴趣，希望结合自己的经历了解课题组机会。",
    rewrittenText: [
      tone.opener,
      `。${length.detail}${specificity.match}${intensity.intent}${formality.request}。`,
      preservation.closer,
    ].join(""),
    ruleSummary: [
      intensity.summary,
      tone.summary,
      formality.summary,
      length.summary,
      specificity.summary,
      preservation.summary,
    ].join(" "),
  };
}

function getPreviewOption<
  Options extends Record<string, Record<string, string>>,
  Fallback extends keyof Options & string,
>(options: Options, value: string, fallback: Fallback): Options[Fallback] {
  return (value in options ? options[value] : options[fallback]) as Options[Fallback];
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}
