import { useCallback, useEffect, useState, type TransitionEvent } from "react";
import clsx from "clsx";
import { ChevronDown, Loader2, Power, Save, Settings } from "lucide-react";

import { quitDesktopApp } from "@/lib/desktopApi";
import {
  defaultDraftRewritePreferences,
  getRuntimeSettings,
  updateRuntimeSettings,
  type RuntimeSettingsDTO,
  type RuntimeSettingsUpdateDTO,
} from "@/lib/api/runtimeSettings";
import { formatApiDateTime } from "@/lib/dateTime";
import { SelectionToggleButton } from "@/components/molecules/SelectionToggleButton";
import { useNotification } from "@/context/NotificationContext";
import type { DesktopStartupAtLoginStatus } from "@/types/desktop";

type RuntimeSettingsKey = keyof RuntimeSettingsUpdateDTO;
type NumberSettingsKey = {
  [Key in RuntimeSettingsKey]: RuntimeSettingsUpdateDTO[Key] extends number ? Key : never;
}[RuntimeSettingsKey];
type HiddenDraftPreferenceKey = keyof typeof defaultDraftRewritePreferences;
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
    label: "AI 草稿输出 Token 上限",
    hint: "AI 草稿的最大输出长度，全局生效。",
    min: 256,
    max: 32000,
    defaultValue: 6000,
  },
  {
    key: "match_analysis_job_item_concurrency",
    label: "每个匹配任务同时分析导师数",
    hint: "越高速度越快，但请求更多。下个任务生效。",
    min: 1,
    max: 20,
    defaultValue: 5,
  },
  {
    key: "batch_draft_generation_concurrency",
    label: "同时生成草稿数",
    hint: "越高速度越快，但请求更多。下个任务生效。",
    min: 1,
    max: 20,
    defaultValue: 5,
  },
  {
    key: "match_analysis_job_interval_seconds",
    label: "批量匹配轮询间隔",
    hint: "检查待处理匹配任务的间隔秒数。",
    min: 1,
    max: 300,
    defaultValue: 10,
    restartRequired: true,
  },
  {
    key: "match_analysis_job_worker_count",
    label: "同时处理的匹配任务数",
    hint: "建议保持为 1。",
    min: 1,
    max: 8,
    defaultValue: 1,
    restartRequired: true,
  },
  {
    key: "crawler_worker_count",
    label: "同时运行的抓取任务数",
    hint: "越高速度越快，但网站访问和模型请求更多。下个任务生效。",
    min: 1,
    max: 8,
    defaultValue: 1,
  },
  {
    key: "crawler_profile_enrichment_concurrency",
    label: "同时补全导师详情页数",
    hint: "越高速度越快，但更易触发网站限制。下个任务生效。",
    min: 1,
    max: 20,
    defaultValue: 3,
  },
  {
    key: "crawler_host_concurrency",
    label: "同一网站同时抓取页数",
    hint: "建议保持为 1，降低网站限流风险。",
    min: 1,
    max: 8,
    defaultValue: 1,
  },
];

const hiddenDraftPreferenceKeys = Object.keys(
  defaultDraftRewritePreferences,
) as HiddenDraftPreferenceKey[];

const emptyForm = numberFields.reduce((state, field) => {
  state[field.key] = "";
  return state;
}, {} as FormState);
for (const key of hiddenDraftPreferenceKeys) {
  emptyForm[key] = "";
}
emptyForm.draft_custom_instruction = "";
emptyForm.intended_research_direction = "";

export function OtherSettingsCard() {
  const { notifyError, notifySuccess } = useNotification();
  const [open, setOpen] = useState(false);
  const [renderContent, setRenderContent] = useState(false);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [startupStatus, setStartupStatus] = useState<DesktopStartupAtLoginStatus | null>(null);
  const [startupLoading, setStartupLoading] = useState(false);
  const [startupSaving, setStartupSaving] = useState(false);
  const [startupError, setStartupError] = useState<string | null>(null);
  const [quittingApp, setQuittingApp] = useState(false);
  const [quitError, setQuitError] = useState<string | null>(null);

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
    setForm((current) => ({
      ...current,
      [key]: value,
    }));
  };

  const handleSubmit = async () => {
    const payload = toUpdatePayload(form);
    setSaving(true);
    setError(null);
    try {
      const saved = await updateRuntimeSettings(payload);
      setForm(toFormState(saved));
      setUpdatedAt(saved.updated_at);
      notifySuccess("设置已保存");
    } catch (saveError) {
      notifyError(
        "保存其他设置失败",
        getErrorMessage(saveError, "请稍后重试"),
      );
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

  const handleQuitDesktopApp = async () => {
    setQuittingApp(true);
    setQuitError(null);
    try {
      await quitDesktopApp();
    } catch (quitAppError) {
      setQuitError(getErrorMessage(quitAppError, "退出桌面应用失败"));
      setQuittingApp(false);
    }
  };

  return (
    <section className="min-w-0 overflow-visible rounded-2xl border border-stone-200 bg-white shadow-sm">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="other-settings-card-content"
        onClick={toggleOpen}
        className={clsx(
          "collapsible-card-toggle flex w-full items-center justify-between gap-4 px-6 py-5 text-left transition hover:bg-stone-50 active:bg-stone-50",
          open ? "rounded-t-2xl" : "rounded-2xl",
        )}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-stone-900">其他设置</h2>
            <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
              写信偏好、匹配方向与运行参数
            </span>
          </div>
          <p className="mt-2 text-sm leading-6 text-stone-600">
            调整 AI、匹配与抓取参数。
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
          className="collapsible-card-content other-settings-card-content"
        >
          <div className="collapsible-card-body min-h-0 px-6">
            {loading ? (
              <div className="mt-5 flex items-center justify-center gap-2 rounded-2xl border border-stone-200 bg-stone-50 px-4 py-8 text-sm text-stone-500">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在加载其他设置…
              </div>
            ) : (
              <>
                <div data-testid="other-settings-form-content" className="mt-5 space-y-5 pb-6">
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
                    <h3 className="text-base font-semibold text-stone-900">草稿改写偏好</h3>

                    <label className="block rounded-2xl border border-stone-200 bg-[#fcfbf8] px-4 py-4">
                      <span className="text-sm font-semibold text-stone-900">
                        AI 草稿补充要求
                      </span>
                      <textarea
                        aria-label="AI 草稿补充要求"
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
                          作为 AI 改写的附加要求，不覆盖系统规则。
                        </span>
                        <span className="shrink-0 text-xs leading-5 text-stone-500">
                          {form.draft_custom_instruction.length}/2000
                        </span>
                      </div>
                    </label>
                  </div>

                  <div className="space-y-4 border-t border-stone-200 pt-5">
                    <h3 className="text-base font-semibold text-stone-900">匹配分析偏好</h3>

                    <label className="block rounded-2xl border border-stone-200 bg-[#fcfbf8] px-4 py-4">
                      <span className="text-sm font-semibold text-stone-900">意向研究方向</span>
                      <textarea
                        aria-label="意向研究方向"
                        value={form.intended_research_direction}
                        maxLength={2000}
                        onChange={(event) =>
                          handleChange("intended_research_direction", event.target.value)
                        }
                        className="mt-3 min-h-28 w-full resize-y rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm leading-6 text-stone-800 outline-none transition placeholder:text-stone-400 focus:border-primary focus:ring-2 focus:ring-primary/20"
                        placeholder="例如：医学自然语言处理、临床知识图谱、科研智能体。"
                      />
                      <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
                        <span className="text-xs leading-5 text-stone-500">
                          用于匹配分析；留空则只参考材料和导师信息。
                        </span>
                        <div className="text-xs leading-5 text-stone-500">
                          {form.intended_research_direction.length}/2000
                        </div>
                      </div>
                    </label>
                  </div>

                  <div className="space-y-3 border-t border-stone-200 pt-5">
                    <h3 className="text-base font-semibold text-stone-900">系统设置</h3>
                    <label className="flex items-start gap-3 rounded-2xl border border-stone-200 bg-[#fcfbf8] px-4 py-4">
                      <SelectionToggleButton
                        label="开机自启动"
                        selected={Boolean(startupStatus?.supported && startupStatus.enabled)}
                        disabled={
                          startupLoading ||
                          startupSaving ||
                          !startupStatus?.supported ||
                          !window.autoEmailSender?.setStartupAtLoginEnabled
                        }
                        semantics="checkbox"
                        size="sm"
                        className="mt-1"
                        onToggle={() => void handleStartupChange(!startupStatus?.enabled)}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-semibold text-stone-900">开机自启动</span>
                        <span className="mt-1 block text-xs leading-5 text-stone-500">
                          {startupLoading
                            ? "正在读取开机自启动状态…"
                            : startupSaving
                              ? "正在保存开机自启动设置…"
                              : startupStatus?.message ??
                                "登录 Windows 后自动启动并驻留托盘。"}
                        </span>
                      </span>
                    </label>
                    {window.autoEmailSender?.quitApp ? (
                      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-4">
                        <div className="min-w-0">
                          <div className="text-sm font-semibold text-stone-900">退出桌面应用</div>
                          <div className="mt-1 text-xs leading-5 text-stone-500">
                            托盘不可用时从此退出。
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => void handleQuitDesktopApp()}
                          disabled={quittingApp}
                          className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {quittingApp ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Power className="h-4 w-4" />
                          )}
                          退出桌面应用
                        </button>
                      </div>
                    ) : null}
                    {startupError ? (
                      <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                        {startupError}
                      </div>
                    ) : null}
                    {quitError ? (
                      <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                        {quitError}
                      </div>
                    ) : null}
                  </div>

                  {error ? (
                    <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                      {error}
                    </div>
                  ) : null}
                </div>

                {open ? (
                  <div
                    role="region"
                    aria-label="其他设置保存栏"
                    className="sticky bottom-0 z-20 -mx-6 -mb-6 flex flex-wrap items-center justify-between gap-3 rounded-b-2xl border-t border-stone-200 bg-white/95 px-6 py-4 shadow-sm backdrop-blur"
                  >
                    <div className="flex min-w-0 items-center gap-2 text-xs text-stone-500">
                      <Settings className="h-4 w-4 shrink-0" />
                      <span className="truncate">
                        最后更新：{updatedAt ? formatApiDateTime(updatedAt) : "尚未加载"}
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
                ) : null}
              </>
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
  for (const key of hiddenDraftPreferenceKeys) {
    state[key] = defaultDraftRewritePreferences[key];
  }
  state.draft_custom_instruction = settings.draft_custom_instruction ?? "";
  state.intended_research_direction = settings.intended_research_direction ?? "";
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

function toUpdatePayload(form: FormState): RuntimeSettingsUpdateDTO {
  const payload = {} as RuntimeSettingsUpdateDTO;
  for (const field of numberFields) {
    const value = Number(form[field.key]);
    payload[field.key] = Number.isFinite(value) ? value : field.min;
  }
  Object.assign(payload, defaultDraftRewritePreferences);
  payload.draft_custom_instruction = form.draft_custom_instruction.trim();
  payload.intended_research_direction = form.intended_research_direction.trim();
  return payload;
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}
