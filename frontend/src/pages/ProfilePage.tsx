import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type ReactNode,
  type TransitionEvent,
} from "react";
import { Link } from "react-router-dom";
import clsx from "clsx";
import {
  ChevronDown,
  CheckCircle2,
  Download,
  ExternalLink,
  FolderOpen,
  Loader2,
  Plus,
  Send,
  Upload,
  X,
  XCircle,
} from "lucide-react";
import { useDesktopBackend } from "@/context/DesktopBackendContext";
import { useNotification } from "@/context/NotificationContext";
import { useSelectionContext } from "@/context/SelectionContext";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";
import { SubjectTemplateInput } from "@/components/molecules/SubjectTemplateInput";
import { OtherSettingsCard } from "@/components/molecules/OtherSettingsCard";
import { TokenUsageCenterCard } from "@/components/molecules/TokenUsageCenterCard";
import { DiagnosticLogPanel } from "@/components/organisms/DiagnosticLogPanel";
import { formatApiDateTime } from "@/lib/dateTime";
import { isDesktopApp, openDesktopMaterial } from "@/lib/desktopApi";
import { textToEmailHtml } from "@/lib/richEmail";
import {
  createIdentity,
  deleteIdentity,
  importIdentityTemplate,
  setDefaultIdentity,
  testIdentityImap,
  testIdentitySmtp,
  updateIdentity,
} from "@/lib/api/identities";
import {
  deleteMaterial,
  getMaterialDownloadUrl,
  setPrimaryMaterial,
  uploadIdentityMaterial,
} from "@/lib/api/materials";
import {
  createLLMProfile,
  deleteLLMProfile,
  fetchLLMProfileModelsPreview,
  setDefaultLLMProfile,
  testLLMProfilePreview,
  updateLLMProfile,
} from "@/lib/api/llmProfiles";
import { getTestComposeStatus } from "@/lib/api/testComposeApi";
import {
  MATERIAL_TYPE_LABELS,
  type IdentityDTO,
  type IdentityMaterialDTO,
  type IdentityMaterialType,
  type IdentityPayload,
  type LLMProfileDTO,
  type LLMProfileModelsResultDTO,
  type LLMProfilePayload,
  type LLMProfileTestResultDTO,
  type OutreachGenerationMode,
} from "@/types";
import { useConfirmDialog } from "@/lib/useConfirmDialog";

type IdentityFormState = {
  name: string;
  profile_name: string;
  sender_name: string;
  email_address: string;
  smtp_host: string;
  smtp_port: string;
  smtp_password: string;
  imap_host: string;
  imap_port: string;
  default_language: string;
  outreach_generation_mode: OutreachGenerationMode;
  outreach_template_subject: string;
  outreach_template_body_text: string;
  outreach_template_body_html: string;
  daily_send_limit: string;
  send_interval_min: string;
  send_interval_max: string;
  same_domain_cooldown_minutes: string;
  is_default: boolean;
};

type LLMFormState = {
  name: string;
  api_base_url: string;
  api_key: string;
  model_name: string;
  is_default: boolean;
};

type EditorId = number | "new" | null;
type ActionResultState = "idle" | "success" | "error";
type IdentityConnectionTestSummary = {
  kind: "smtp" | "imap";
  status: "success" | "error";
  message: string;
};

type MaterialFilterValue = IdentityMaterialType | "all";
type ProfileSetupSectionId = "identity" | "materials" | "model" | "test";
type ProfileSetupItem = {
  id: ProfileSetupSectionId;
  label: string;
  title: string;
  description: string;
  completed: boolean;
  statusDetail: string;
};
type TestComposeSetupStatus = "unchecked" | "loading" | "completed" | "pending";

const DEFAULT_LLM_PROVIDER = "openai";
const DEFAULT_LLM_TEMPERATURE = 0.2;
const DEFAULT_LLM_MAX_TOKENS = 6000;
const PRIMARY_MATERIAL_EXTENSIONS = [".pdf", ".doc", ".docx", ".txt", ".md"];
const TEMPLATE_FILE_ACCEPT = ".docx,.html,.htm,.txt,.md";
const TEMPLATE_PLACEHOLDERS = [
  ["{{name}}", "导师姓名"],
  ["{{email}}", "导师邮箱"],
  ["{{title}}", "导师职称"],
  ["{{university}}", "导师学校"],
  ["{{school}}", "导师学院"],
  ["{{department}}", "导师院系"],
  ["{{research_direction}}", "导师研究方向"],
  ["{{sender_name}}", "你的发件人姓名"],
  ["{{sender_email}}", "你的发件邮箱"],
] as const;

const PROFILE_SETUP_STAGES = [
  {
    id: "identity",
    label: "1. 发件身份",
    title: "发件身份",
    description: "配置发件邮箱、SMTP 和 IMAP。",
  },
  {
    id: "materials",
    label: "2. 材料与模板",
    title: "材料与模板",
    description: "准备默认模板和常用材料。",
  },
  {
    id: "model",
    label: "3. 模型配置",
    title: "模型配置",
    description: "以 DeepSeek 为示例配置可用模型并测试连接。",
  },
  {
    id: "test",
    label: "4. 测试写信",
    title: "测试写信",
    description: "用当前身份和模型发送一封测试邮件。",
  },
] as const satisfies ReadonlyArray<{
  id: ProfileSetupSectionId;
  label: string;
  title: string;
  description: string;
}>;

const createEmptyIdentityForm = (): IdentityFormState => ({
  name: "",
  profile_name: "",
  sender_name: "",
  email_address: "",
  smtp_host: "",
  smtp_port: "465",
  smtp_password: "",
  imap_host: "",
  imap_port: "993",
  default_language: "zh-CN",
  outreach_generation_mode: "llm",
  outreach_template_subject: "",
  outreach_template_body_text: "",
  outreach_template_body_html: "",
  daily_send_limit: "",
  send_interval_min: "",
  send_interval_max: "",
  same_domain_cooldown_minutes: "",
  is_default: false,
});

const createEmptyLLMForm = (): LLMFormState => ({
  name: "",
  api_base_url: "",
  api_key: "",
  model_name: "",
  is_default: false,
});

const inferImapHost = (smtpHost: string) =>
  smtpHost.trim().replace(/smtp/gi, "imap");

const canUseAsPrimaryMaterial = (material: IdentityMaterialDTO) => {
  const filename = material.original_filename.toLowerCase();
  return PRIMARY_MATERIAL_EXTENSIONS.some((suffix) =>
    filename.endsWith(suffix),
  );
};

const shouldSyncImapHost = (smtpHost: string, imapHost: string) => {
  const trimmedImapHost = imapHost.trim();
  if (!trimmedImapHost) {
    return true;
  }
  return trimmedImapHost === inferImapHost(smtpHost);
};

const hasVisibleTemplateBody = ({
  outreach_template_body_text,
}: Pick<IdentityFormState, "outreach_template_body_text">) =>
  Boolean(outreach_template_body_text.trim());

const getTemplateValidationMessage = ({
  outreach_template_subject,
  outreach_template_body_text,
}: Pick<
  IdentityFormState,
  "outreach_template_subject" | "outreach_template_body_text"
>) => {
  const hasSubject = Boolean(outreach_template_subject.trim());
  const hasBodyText = Boolean(outreach_template_body_text.trim());

  if (!hasSubject && !hasBodyText) {
    return "请先填写默认套磁信主题和正文";
  }
  if (!hasSubject) {
    return "请先填写默认套磁信主题";
  }
  if (!hasBodyText) {
    return "请先填写默认套磁信正文";
  }
  return null;
};

const getIdentityProfileName = (identity: IdentityDTO) =>
  identity.profile_name || identity.name;

const getIdentitySenderName = (identity: IdentityDTO) =>
  identity.sender_name || getIdentityProfileName(identity);

const toIdentityForm = (identity: IdentityDTO): IdentityFormState => {
  const profileName = getIdentityProfileName(identity);
  return {
    name: profileName,
    profile_name: profileName,
    sender_name: getIdentitySenderName(identity),
    email_address: identity.email_address,
    smtp_host: identity.smtp_host,
    smtp_port: String(identity.smtp_port),
    smtp_password: identity.smtp_password,
    imap_host: identity.imap_host ?? inferImapHost(identity.smtp_host),
    imap_port: identity.imap_port === null ? "" : String(identity.imap_port),
    default_language: identity.default_language,
    outreach_generation_mode: identity.outreach_generation_mode,
    outreach_template_subject: identity.outreach_template_subject ?? "",
    outreach_template_body_text: identity.outreach_template_body_text ?? "",
    outreach_template_body_html: identity.outreach_template_body_html ?? "",
    daily_send_limit:
      identity.daily_send_limit === null
        ? ""
        : String(identity.daily_send_limit),
    send_interval_min:
      identity.send_interval_min === null
        ? ""
        : String(identity.send_interval_min),
    send_interval_max:
      identity.send_interval_max === null
        ? ""
        : String(identity.send_interval_max),
    same_domain_cooldown_minutes:
      identity.same_domain_cooldown_minutes === null
        ? ""
        : String(identity.same_domain_cooldown_minutes),
    is_default: identity.is_default,
  };
};

const toLLMForm = (profile: LLMProfileDTO): LLMFormState => ({
  name: profile.name,
  api_base_url: profile.api_base_url ?? "",
  api_key: profile.api_key,
  model_name: profile.model_name,
  is_default: profile.is_default,
});

const toIdentityPayload = (form: IdentityFormState): IdentityPayload => {
  const profileName = form.profile_name.trim();
  return {
    name: profileName,
    profile_name: profileName,
    sender_name: form.sender_name.trim(),
    email_address: form.email_address.trim(),
    smtp_host: form.smtp_host.trim(),
    smtp_port: Number(form.smtp_port || "465"),
    smtp_username: form.email_address.trim(),
    smtp_password: form.smtp_password,
    imap_host: (form.imap_host.trim() || inferImapHost(form.smtp_host)).trim(),
    imap_port: Number(form.imap_port || "993"),
    imap_username: form.email_address.trim(),
    imap_password: form.smtp_password,
    default_language: form.default_language.trim() || "zh-CN",
    outreach_generation_mode: form.outreach_generation_mode,
    outreach_template_subject: form.outreach_template_subject.trim() || null,
    outreach_template_body_text:
      form.outreach_template_body_text.trim() || null,
    outreach_template_body_html: hasVisibleTemplateBody(form)
      ? form.outreach_template_body_html.trim() || null
      : null,
    daily_send_limit: form.daily_send_limit
      ? Number(form.daily_send_limit)
      : null,
    send_interval_min: form.send_interval_min
      ? Number(form.send_interval_min)
      : null,
    send_interval_max: form.send_interval_max
      ? Number(form.send_interval_max)
      : null,
    same_domain_cooldown_minutes: form.same_domain_cooldown_minutes
      ? Number(form.same_domain_cooldown_minutes)
      : null,
    is_default: form.is_default,
  };
};

const toLLMPayload = (form: LLMFormState): LLMProfilePayload => ({
  name: form.name.trim(),
  provider: DEFAULT_LLM_PROVIDER,
  api_base_url: form.api_base_url.trim() || null,
  api_key: form.api_key.trim(),
  model_name: form.model_name.trim(),
  matcher_prompt_template: null,
  writer_prompt_template: null,
  temperature: DEFAULT_LLM_TEMPERATURE,
  max_tokens: DEFAULT_LLM_MAX_TOKENS,
  is_default: form.is_default,
});

const isExistingEditorId = (value: EditorId): value is number =>
  typeof value === "number";

const inputClassName =
  "w-full rounded-xl border border-stone-200 px-3 py-2 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20";

const labelClassName =
  "mb-2 inline-flex items-center gap-1 text-sm font-medium text-stone-800";

const renderFieldLabel = (label: string, required = false) => (
  <span className={labelClassName}>
    {required && <span className="text-base leading-none text-red-500">*</span>}
    <span>{label}</span>
  </span>
);

function ProfileSetupSection({
  sectionId,
  title,
  description,
  badge,
  open,
  renderContent,
  onToggle,
  onExitComplete,
  sectionRef,
  children,
}: {
  sectionId: ProfileSetupSectionId;
  title: string;
  description: string;
  badge: ReactNode;
  open: boolean;
  renderContent: boolean;
  onToggle: () => void;
  onExitComplete: () => void;
  sectionRef: (element: HTMLElement | null) => void;
  children: ReactNode;
}) {
  const handleContentTransitionEnd = (
    event: TransitionEvent<HTMLDivElement>,
  ) => {
    if (open || event.propertyName !== "grid-template-rows") {
      return;
    }
    onExitComplete();
  };

  return (
    <section
      ref={sectionRef}
      className="overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-sm"
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={`${sectionId}-setup-content`}
        onClick={onToggle}
        className="collapsible-card-toggle flex w-full items-center justify-between gap-4 px-6 py-5 text-left transition hover:bg-stone-50 active:bg-stone-50"
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-stone-900">{title}</h2>
            {badge}
          </div>
          <p className="mt-2 text-sm leading-6 text-stone-600">{description}</p>
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
          id={`${sectionId}-setup-content`}
          data-state={open ? "open" : "closed"}
          onTransitionEnd={handleContentTransitionEnd}
          className="collapsible-card-content"
        >
          <div className="collapsible-card-body min-h-0 px-6">
            {children}
          </div>
        </div>
      ) : null}
    </section>
  );
}

const formatFileSize = (sizeBytes: number) => {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
};

const triggerDownload = (url: string) => {
  const link = document.createElement("a");
  link.href = url;
  document.body.appendChild(link);
  link.click();
  link.remove();
};

const formatDuration = (durationMs: number | null) =>
  durationMs === null ? "未返回" : `${durationMs} ms`;

const LlmModelsFeedbackPanel = ({
  result,
  currentModelName,
  onSelectModel,
}: {
  result: LLMProfileModelsResultDTO | null;
  currentModelName: string;
  onSelectModel: (modelName: string) => void;
}) => {
  const [searchKeyword, setSearchKeyword] = useState("");
  const deferredSearchKeyword = useDeferredValue(searchKeyword);

  if (!result) {
    return null;
  }

  const normalizedKeyword = deferredSearchKeyword.trim().toLowerCase();
  const filteredModels = result.models.filter((model) =>
    normalizedKeyword ? model.toLowerCase().includes(normalizedKeyword) : true,
  );
  const hasExactCurrentModel = result.models.includes(currentModelName.trim());

  return (
    <div
      className={clsx(
        "rounded-3xl border px-4 py-4 shadow-sm",
        result.ok
          ? "border-emerald-200 bg-emerald-50/80"
          : "border-red-200 bg-red-50/80",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-stone-900">基础连通性</span>
        <span
          className={clsx(
            "rounded-full px-2.5 py-1 text-[11px] font-medium",
            result.consumes_tokens
              ? "bg-amber-100 text-amber-700"
              : "bg-stone-900 text-white",
          )}
        >
          {result.consumes_tokens ? "会耗 token" : "不耗 token"}
        </span>
      </div>
      <p className="mt-2 text-sm leading-6 text-stone-700">{result.message}</p>
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-600">
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          状态码：{result.status_code ?? "未返回"}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          耗时：{formatDuration(result.duration_ms)}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          端点：{result.endpoint_kind ?? "未识别"}
        </span>
      </div>
      {result.request_url ? (
        <div className="mt-3 rounded-2xl border border-stone-200 bg-white/90 px-3 py-2 text-xs leading-5 text-stone-600">
          <div className="font-medium text-stone-800">请求 URL</div>
          <div className="mt-1 break-all">{result.request_url}</div>
        </div>
      ) : null}
      {result.models.length > 0 ? (
        <div className="mt-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs font-medium text-stone-700">可用模型</div>
            </div>
            <span className="rounded-full border border-stone-200 bg-white px-2.5 py-1 text-[11px] text-stone-500">
              {filteredModels.length}/{result.models.length}
            </span>
          </div>
          <div className="mt-3 rounded-[24px] border border-stone-200 bg-white/90 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.85)]">
            <input
              value={searchKeyword}
              onChange={(event) => setSearchKeyword(event.target.value)}
              className="w-full rounded-2xl border border-stone-200 bg-stone-50/80 px-3 py-2 text-sm text-stone-700 outline-none transition placeholder:text-stone-400 focus:border-primary focus:bg-white focus:ring-2 focus:ring-primary/15"
              placeholder="搜索模型名，点击进行选择"
            />
            {currentModelName.trim() ? (
              <div className="mt-3 rounded-2xl border border-stone-200 bg-stone-50/85 px-3 py-3">
                <div className="text-[11px] uppercase tracking-[0.18em] text-stone-400">
                  当前选择
                </div>
                <div className="mt-2 break-all text-sm font-medium leading-6 text-stone-800">
                  {currentModelName}
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-stone-500">
                  {hasExactCurrentModel ? (
                    <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-emerald-700">
                      已在列表中
                    </span>
                  ) : (
                    <span className="rounded-full bg-amber-100 px-2.5 py-1 text-amber-700">
                      不在当前列表中
                    </span>
                  )}
                </div>
              </div>
            ) : null}
            <div className="mt-3 max-h-56 overflow-y-auto pr-1">
              {filteredModels.length > 0 ? (
                <div className="space-y-2">
                  {filteredModels.map((model) => {
                    const active = model === currentModelName.trim();
                    return (
                      <button
                        key={model}
                        type="button"
                        onClick={() => onSelectModel(model)}
                        className={clsx(
                          "group flex w-full justify-between items-center gap-3 rounded-2xl border px-3 py-2 text-left transition",
                          active
                            ? "border-primary/20 bg-primary text-white shadow-sm shadow-primary/20"
                            : "border-stone-200 bg-stone-50/75 text-stone-700 hover:border-stone-300 hover:bg-white hover:text-stone-900",
                        )}
                      >
                        <div className="min-w-0 flex-1">
                          <div className="break-all text-sm font-medium leading-5">
                            {model}
                          </div>
                        </div>
                        <div
                          className={clsx(
                            "mt-0.5 shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium",
                            active
                              ? "bg-white/18 text-white"
                              : "bg-stone-100 text-stone-500 group-hover:bg-stone-200 group-hover:text-stone-700",
                          )}
                        >
                          {active ? "当前" : "选择"}
                        </div>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-stone-200 bg-stone-50/70 px-4 py-6 text-center text-xs text-stone-500">
                  没找到匹配的模型名，试试换个关键词。
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};

const LlmTestFeedbackPanel = ({
  result,
}: {
  result: LLMProfileTestResultDTO | null;
}) => {
  if (!result) {
    return null;
  }

  return (
    <div
      className={clsx(
        "rounded-3xl border px-4 py-4 shadow-sm",
        result.ok
          ? "border-emerald-200 bg-emerald-50/80"
          : "border-red-200 bg-red-50/80",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-stone-900">测试模型</span>
        <span
          className={clsx(
            "rounded-full px-2.5 py-1 text-[11px] font-medium",
            result.consumes_tokens
              ? "bg-amber-100 text-amber-700"
              : "bg-stone-900 text-white",
          )}
        >
          {result.consumes_tokens ? "会耗 token" : "不耗 token"}
        </span>
      </div>
      <p className="mt-2 text-sm leading-6 text-stone-700">{result.message}</p>
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-600">
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          状态码：{result.status_code ?? "未返回"}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          耗时：{formatDuration(result.duration_ms)}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          端点：{result.endpoint_kind ?? "未识别"}
        </span>
      </div>
      {result.request_url ? (
        <div className="mt-3 rounded-2xl border border-stone-200 bg-white/90 px-3 py-2 text-xs leading-5 text-stone-600">
          <div className="font-medium text-stone-800">最终请求 URL</div>
          <div className="mt-1 break-all">{result.request_url}</div>
        </div>
      ) : null}
      {result.attempted_urls.length > 1 ? (
        <div className="mt-3 rounded-2xl border border-stone-200 bg-white/90 px-3 py-2 text-xs leading-5 text-stone-600">
          <div className="font-medium text-stone-800">尝试过的 URL</div>
          <div className="mt-1 break-all">
            {result.attempted_urls.join("\n")}
          </div>
        </div>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-600">
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          输入 token：{result.prompt_tokens ?? "未返回"}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          输出 token：{result.completion_tokens ?? "未返回"}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          总 token：{result.total_tokens ?? "未返回"}
        </span>
      </div>
      {result.response_preview ? (
        <div className="mt-3 rounded-2xl border border-stone-200 bg-white/90 px-3 py-2 text-xs leading-5 text-stone-600">
          <div className="font-medium text-stone-800">响应预览</div>
          <div className="mt-1 whitespace-pre-wrap">
            {result.response_preview}
          </div>
        </div>
      ) : null}
    </div>
  );
};

const MATERIAL_TYPE_OPTIONS = Object.entries(MATERIAL_TYPE_LABELS) as [
  IdentityMaterialType,
  string,
][];

const getMaterialTypeLabel = (value: IdentityMaterialType) =>
  MATERIAL_TYPE_LABELS[value];

const getActionButtonClassName = (state: ActionResultState, loading: boolean) =>
  clsx(
    "inline-flex items-center gap-2 rounded-xl border px-4 py-2 text-sm font-medium transition",
    state === "success" &&
      "border-emerald-200 bg-emerald-50 text-emerald-700 hover:border-emerald-300 hover:bg-emerald-100/80",
    state === "error" &&
      "border-red-200 bg-red-50 text-red-700 hover:border-red-300 hover:bg-red-100/80",
    state === "idle" &&
      "border-stone-200 bg-white text-stone-700 hover:border-stone-300 hover:bg-stone-50 hover:text-stone-900",
    loading && "cursor-not-allowed opacity-70",
  );

type EditorOption = {
  id: number;
  name: string;
  is_default: boolean;
};

type EditorSwitcherProps = {
  label: string;
  helper?: string;
  options: EditorOption[];
  activeId: EditorId;
  createLabel: string;
  creatingLabel: string;
  onCreate: () => void;
  onSelect: (id: number) => void;
};

const EditorSwitcher = ({
  label,
  helper,
  options,
  activeId,
  createLabel,
  creatingLabel,
  onCreate,
  onSelect,
}: EditorSwitcherProps) => (
  <div className="rounded-2xl border border-stone-200 bg-white px-4 py-4 shadow-sm shadow-stone-100/60">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="text-sm font-medium text-stone-900">{label}</div>
        {helper ? (
          <p className="mt-1 text-xs leading-5 text-stone-500">{helper}</p>
        ) : null}
      </div>
      {options.length > 0 ? (
        <button
          type="button"
          onClick={onCreate}
          className="inline-flex items-center gap-2 rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm font-medium text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-900"
        >
          <Plus className="h-4 w-4" />
          {createLabel}
        </button>
      ) : null}
    </div>

    <div className="mt-4 flex flex-wrap gap-2">
      {options.length === 0 ? (
        <div className="w-full rounded-2xl border border-dashed border-primary/20 bg-primary/5 px-4 py-4">
          <div className="text-sm font-medium text-primary">
            {creatingLabel}
          </div>
        </div>
      ) : (
        options.map((option) => {
          const isActive = activeId === option.id;
          return (
            <button
              key={option.id}
              type="button"
              onClick={() => onSelect(option.id)}
              className={clsx(
                "inline-flex items-center gap-2 rounded-2xl border px-4 py-3 text-sm font-medium transition-all",
                isActive
                  ? "border-primary/20 bg-primary text-white shadow-sm shadow-primary/20"
                  : "border-stone-200 bg-stone-50 text-stone-700 hover:border-stone-300 hover:bg-white hover:text-stone-900",
              )}
            >
              <span>{option.name}</span>
              {option.is_default && (
                <span
                  className={clsx(
                    "rounded-full px-2 py-0.5 text-[11px]",
                    isActive
                      ? "bg-white/18 text-white"
                      : "bg-white text-stone-500",
                  )}
                >
                  默认
                </span>
              )}
            </button>
          );
        })
      )}

      {options.length > 0 && activeId === "new" && (
        <div className="inline-flex items-center rounded-2xl border border-primary/20 bg-primary/5 px-4 py-3 text-sm font-medium text-primary">
          {creatingLabel}
        </div>
      )}
    </div>
  </div>
);

const MaterialTypePicker = ({
  value,
  onChange,
}: {
  value: IdentityMaterialType;
  onChange: (value: IdentityMaterialType) => void;
}) => (
  <NativeSelectField
    value={value}
    onChange={(event) => onChange(event.target.value as IdentityMaterialType)}
    wrapperClassName="w-full max-w-xs"
    shellClassName="min-h-10 rounded-2xl border-stone-200 bg-white/92 px-4 py-2.5 shadow-sm shadow-stone-100/70"
  >
    {MATERIAL_TYPE_OPTIONS.map(([type, label]) => (
      <option key={type} value={type}>
        {label}
      </option>
    ))}
  </NativeSelectField>
);

const MaterialFilterBar = ({
  value,
  materials,
  onChange,
}: {
  value: MaterialFilterValue;
  materials: IdentityMaterialDTO[];
  onChange: (value: MaterialFilterValue) => void;
}) => (
  <div className="flex flex-wrap gap-2">
    <button
      type="button"
      onClick={() => onChange("all")}
      className={clsx(
        "rounded-full border px-3 py-1.5 text-xs font-medium transition",
        value === "all"
          ? "border-stone-900 bg-stone-900 text-white shadow-sm shadow-stone-900/20"
          : "border-stone-200 bg-white text-stone-600 hover:border-stone-300 hover:bg-stone-50 hover:text-stone-900",
      )}
    >
      全部 {materials.length}
    </button>
    {MATERIAL_TYPE_OPTIONS.map(([type, label]) => {
      const count = materials.filter(
        (material) => material.material_type === type,
      ).length;
      if (!count) {
        return null;
      }
      return (
        <button
          key={type}
          type="button"
          onClick={() => onChange(type)}
          className={clsx(
            "rounded-full border px-3 py-1.5 text-xs font-medium transition",
            value === type
              ? "border-primary bg-primary text-white shadow-sm shadow-primary/20"
              : "border-stone-200 bg-white text-stone-600 hover:border-stone-300 hover:bg-stone-50 hover:text-stone-900",
          )}
        >
          {label} {count}
        </button>
      );
    })}
  </div>
);

const MaterialSummaryCard = ({
  identity,
  onOpen,
}: {
  identity: IdentityDTO;
  onOpen: () => void;
}) => {
  const primaryMaterial = identity.current_primary_material;

  return (
    <div className="rounded-[28px] border border-stone-200 bg-[linear-gradient(135deg,#fffdfa,#fff8ef_55%,#fff3e1)] p-5 shadow-sm shadow-stone-200/70">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="space-y-3">
          <div>
            <div className="text-sm font-medium text-stone-900">材料库</div>
            <div className="mt-1 text-xs text-stone-500">
              共 {identity.materials.length} 份
              {primaryMaterial
                ? ` · 默认材料：${primaryMaterial.display_name}`
                : " · 当前未设默认材料"}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {MATERIAL_TYPE_OPTIONS.map(([type, label]) => {
              const count = identity.materials.filter(
                (material) => material.material_type === type,
              ).length;
              if (!count) {
                return null;
              }
              return (
                <span
                  key={type}
                  className="rounded-full border border-stone-200/80 bg-white/90 px-3 py-1 text-xs text-stone-600"
                >
                  {label} {count}
                </span>
              );
            })}
          </div>
        </div>

        <button
          type="button"
          onClick={onOpen}
          className="inline-flex items-center gap-2 rounded-2xl border border-stone-300 bg-white/95 px-4 py-2.5 text-sm font-medium text-stone-800 shadow-sm transition hover:border-stone-400 hover:bg-white"
        >
          <FolderOpen className="h-4 w-4" />
          打开材料库
        </button>
      </div>
    </div>
  );
};

const IdentityConnectionCard = ({
  testingIdentityConnection,
  lastResult,
  onTestSmtp,
  onTestImap,
}: {
  testingIdentityConnection: "smtp" | "imap" | null;
  lastResult: IdentityConnectionTestSummary | null;
  onTestSmtp: () => void;
  onTestImap: () => void;
}) => (
  <div className="rounded-[28px] border border-stone-200 bg-[linear-gradient(135deg,#fffdfa,#fff9f2_52%,#fff5ea)] p-5 shadow-sm shadow-stone-200/70">
    <div className="flex flex-wrap justify-between items-center gap-4">
      <div className="space-y-2">
        <div className="text-sm font-medium text-stone-900">邮箱连接测试</div>
      </div>
      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          onClick={onTestSmtp}
          disabled={testingIdentityConnection !== null}
          className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
        >
          {testingIdentityConnection === "smtp" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : null}
          测试 SMTP
        </button>
        <button
          type="button"
          onClick={onTestImap}
          disabled={testingIdentityConnection !== null}
          className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
        >
          {testingIdentityConnection === "imap" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : null}
          测试 IMAP
        </button>
      </div>
    </div>
    {lastResult ? (
      <div className="mt-4 rounded-2xl border border-stone-200/80 bg-white/80 px-4 py-3 text-sm text-stone-700">
        <div className="font-medium text-stone-900">
          上次测试：{lastResult.kind.toUpperCase()}
          {lastResult.status === "success" ? " 成功" : " 失败"}
        </div>
        <div className="mt-1 whitespace-pre-wrap break-words text-stone-600">
          {lastResult.message}
        </div>
      </div>
    ) : null}
  </div>
);

const OutreachTemplateSummaryCard = ({
  form,
  onOpen,
}: {
  form: IdentityFormState;
  onOpen: () => void;
}) => {
  const hasSubject = Boolean(form.outreach_template_subject.trim());
  const hasTemplateBody = hasVisibleTemplateBody(form);

  return (
    <div className="rounded-[28px] border border-stone-200 bg-[linear-gradient(135deg,#fffdfa,#fff7ee_58%,#fff2e4)] p-5 shadow-sm shadow-stone-200/70">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="space-y-3">
          <div>
            <div className="text-sm font-medium text-stone-900">
              默认发信模式与默认模板
            </div>
            <div className="mt-1 text-xs leading-6 text-stone-500">
              设置新任务默认使用的写信方式和模板。当前默认模式：
              {form.outreach_generation_mode === "template"
                ? "直接套用模板"
                : "AI 辅助写信"}
              {" · 可直接导入模板文件"}
            </div>
            <div className="mt-1 text-xs leading-6 text-stone-500">
              导入文件只带入正文，主题需单独填写。
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="rounded-full border border-stone-200/80 bg-white/90 px-3 py-1 text-xs text-stone-600">
              {hasSubject ? "主题（必填）已填写" : "主题（必填）未填写"}
            </span>
            <span className="rounded-full border border-stone-200/80 bg-white/90 px-3 py-1 text-xs text-stone-600">
              {hasTemplateBody ? "正文（必填）已填写" : "正文（必填）未填写"}
            </span>
          </div>
        </div>

        <button
          type="button"
          onClick={onOpen}
          className="inline-flex items-center gap-2 rounded-2xl border border-stone-300 bg-white/95 px-4 py-2.5 text-sm font-medium text-stone-800 shadow-sm transition hover:border-stone-400 hover:bg-white"
        >
          <FolderOpen className="h-4 w-4" />
          打开默认值编辑
        </button>
      </div>
    </div>
  );
};

const OutreachTemplateModal = ({
  open,
  importingTemplateFile,
  savingTemplate,
  form,
  onClose,
  onComplete,
  onImport,
  onModeChange,
  onSubjectChange,
  onBodyChange,
}: {
  open: boolean;
  importingTemplateFile: boolean;
  savingTemplate: boolean;
  form: IdentityFormState;
  onClose: () => void;
  onComplete: () => void;
  onImport: (file: File) => void;
  onModeChange: (value: OutreachGenerationMode) => void;
  onSubjectChange: (value: string) => void;
  onBodyChange: (value: { html: string; text: string }) => void;
}) => {
  const [isTemplateDropActive, setIsTemplateDropActive] = useState(false);

  if (!open) {
    return null;
  }

  const templateEditorHtml =
    form.outreach_template_body_html ||
    textToEmailHtml(form.outreach_template_body_text);

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
      onClick={onClose}
    >
      <div
        className="relative flex max-h-[86vh] w-full max-w-5xl flex-col overflow-hidden rounded-[32px] border border-stone-200/80 bg-[linear-gradient(180deg,#fffdfa,#fff7ee_18%,#ffffff_40%)] shadow-[0_30px_90px_-28px_rgba(41,37,36,0.45)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="border-b border-stone-200/80 bg-white/75 px-6 py-5 backdrop-blur-md">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-xs uppercase tracking-[0.26em] text-stone-400">
                Outreach Defaults
              </div>
              <h3 className="mt-2 text-2xl font-semibold text-stone-900">
                默认发信模式与默认模板
              </h3>
              <p className="mt-1 max-w-3xl text-sm leading-6 text-stone-500">
                设置新任务默认带出的模式、主题和正文。
                只影响新任务，不影响已创建任务。
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-900"
              aria-label="关闭默认值编辑窗口"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="border-b border-stone-200/80 bg-[#fffaf3] px-6 py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="text-sm font-medium text-stone-900">
                当前默认值摘要
              </div>
              <div className="mt-1 flex flex-wrap gap-2 text-xs text-stone-500">
                <span className="rounded-full border border-stone-200 bg-white/90 px-3 py-1">
                  模式：
                  {form.outreach_generation_mode === "template"
                    ? "直接套用模板"
                    : "AI 辅助写信"}
                </span>
                <span className="rounded-full border border-stone-200 bg-white/90 px-3 py-1">
                  主题（必填）：
                  {form.outreach_template_subject.trim() ? "已填写" : "未填写"}
                </span>
                <span className="rounded-full border border-stone-200 bg-white/90 px-3 py-1">
                  正文（必填）：
                  {hasVisibleTemplateBody(form) ? "已填写" : "未填写"}
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
                  : "点击或拖拽导入默认模板"}
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
          <div className="grid gap-6">
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                {
                  value: "llm" as const,
                  title: "AI 辅助写信",
                  description: "AI 基于模板生成个性化草稿。",
                },
                {
                  value: "template" as const,
                  title: "直接套用模板",
                  description: "按模板生成邮件，适合统一话术。",
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
                          当前默认
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

            <div className="flex flex-wrap gap-2 text-xs text-stone-500">
              {TEMPLATE_PLACEHOLDERS.map(([token, label]) => (
                <span
                  key={token}
                  className="rounded-full border border-stone-200 bg-white px-3 py-1"
                >
                  {token} {label}
                </span>
              ))}
            </div>

            <div className="grid gap-4">
              <SubjectTemplateInput
                label="默认模板主题"
                required
                value={form.outreach_template_subject}
                onChange={onSubjectChange}
                inputClassName={`${inputClassName} pr-28`}
                placeholder="例如：申请与 {{name}} 老师交流科研方向"
              />
              <p className="text-xs leading-6 text-stone-500">
                导入文件只带入正文，主题需单独填写。
              </p>
              <EmailTemplateEditor
                label="默认模板正文"
                html={templateEditorHtml}
                placeholder="可将套磁信docx拖到此处导入"
                onFileDrop={onImport}
                onChange={onBodyChange}
              />
            </div>

            <div className="rounded-2xl border border-dashed border-stone-200 bg-white/85 px-4 py-3 text-xs leading-6 text-stone-500">
              {form.outreach_generation_mode === "template"
                ? "作为新任务默认值；已创建任务不受后续修改影响。"
                : "AI 只在模板基础上调整称呼、匹配理由和主题。"}
            </div>
          </div>
        </div>

        <div className="border-t border-stone-200/80 bg-white/80 px-6 py-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-xs leading-6 text-stone-500">
              完成编辑会保存到当前身份。
            </div>
            <button
              type="button"
              onClick={onComplete}
              disabled={savingTemplate}
              className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
            >
              {savingTemplate && <Loader2 className="h-4 w-4 animate-spin" />}
              {savingTemplate ? "正在保存" : "完成编辑"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

const MaterialLibraryModal = ({
  open,
  identity,
  materials,
  busy,
  uploading,
  selectedMaterialType,
  materialFilter,
  highlightedMaterialId,
  onChangeMaterialType,
  onChangeMaterialFilter,
  onUpload,
  onOpen,
  onClose,
  onSetPrimary,
  onDelete,
}: {
  open: boolean;
  identity: IdentityDTO;
  materials: IdentityMaterialDTO[];
  busy: boolean;
  uploading: boolean;
  selectedMaterialType: IdentityMaterialType;
  materialFilter: MaterialFilterValue;
  highlightedMaterialId: number | null;
  onChangeMaterialType: (value: IdentityMaterialType) => void;
  onChangeMaterialFilter: (value: MaterialFilterValue) => void;
  onUpload: (file: File) => void;
  onOpen: (material: IdentityMaterialDTO) => void;
  onClose: () => void;
  onSetPrimary: (material: IdentityMaterialDTO) => void;
  onDelete: (material: IdentityMaterialDTO) => void;
}) => {
  if (!open) {
    return null;
  }

  const primaryMaterial = identity.current_primary_material;
  const visibleMaterials =
    materialFilter === "all"
      ? materials
      : materials.filter(
          (material) => material.material_type === materialFilter,
        );

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-stone-950/35 p-4 backdrop-blur-md sm:items-center"
      onClick={onClose}
    >
      <div
        className="relative flex max-h-[86vh] w-full max-w-5xl flex-col overflow-hidden rounded-[32px] border border-stone-200/80 bg-[linear-gradient(180deg,#fffdfa,#fff7ee_18%,#ffffff_40%)] shadow-[0_30px_90px_-28px_rgba(41,37,36,0.45)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="border-b border-stone-200/80 bg-white/75 px-6 py-5 backdrop-blur-md">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-xs uppercase tracking-[0.26em] text-stone-400">
                Material Library
              </div>
              <h3 className="mt-2 text-2xl font-semibold text-stone-900">
                材料管理
              </h3>
              <p className="mt-1 text-sm text-stone-500">
                {identity.materials.length} 份材料
                {primaryMaterial
                  ? ` · 默认材料：${primaryMaterial.display_name}`
                  : " · 当前未设默认材料"}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-900"
              aria-label="关闭材料库"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="border-b border-stone-200/80 bg-[#fffaf3] px-6 py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex min-w-0 flex-1 flex-col gap-3 sm:flex-row sm:items-center">
              <div className="min-w-[6.5rem]">
                <div className="text-sm font-medium text-stone-900">
                  上传新材料
                </div>
                <div className="mt-1 text-xs text-stone-500">
                  选择类型并上传文件
                </div>
              </div>
              <MaterialTypePicker
                value={selectedMaterialType}
                onChange={onChangeMaterialType}
              />
              <span className="inline-flex items-center rounded-full border border-stone-200 bg-white/90 px-3 py-1.5 text-xs text-stone-600 shadow-sm shadow-stone-100/70">
                当前：{getMaterialTypeLabel(selectedMaterialType)}
              </span>
            </div>
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-2xl border border-primary/20 bg-primary px-4 py-3 text-sm font-medium text-white shadow-sm shadow-primary/20 transition hover:bg-primary-dark">
              {uploading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              上传材料
              <input
                type="file"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.currentTarget.value = "";
                  if (!file) {
                    return;
                  }
                  onUpload(file);
                }}
              />
            </label>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium text-stone-900">查看材料</div>
            </div>
            <MaterialFilterBar
              value={materialFilter}
              materials={materials}
              onChange={onChangeMaterialFilter}
            />
          </div>

          {materials.length === 0 ? (
            <div className="rounded-[28px] border border-dashed border-stone-200 bg-white/75 px-6 py-12 text-center text-sm text-stone-500">
              暂无材料。上传一份即可。
            </div>
          ) : visibleMaterials.length === 0 ? (
            <div className="rounded-[28px] border border-dashed border-stone-200 bg-white/75 px-6 py-12 text-center text-sm text-stone-500">
              当前筛选下还没有材料，试试切回“全部”。
            </div>
          ) : (
            <div className="space-y-3">
              {visibleMaterials.map((material) => {
                const canPromote = canUseAsPrimaryMaterial(material);
                return (
                  <article
                    key={material.id}
                    data-material-id={material.id}
                    className={clsx(
                      "rounded-[26px] border px-5 py-4 shadow-sm transition",
                      material.is_primary
                        ? "border-primary/20 bg-primary/5 shadow-primary/5"
                        : "border-stone-200 bg-white shadow-stone-100/60",
                      highlightedMaterialId === material.id &&
                        "border-amber-300 bg-amber-50/70 shadow-amber-100",
                    )}
                  >
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="truncate text-sm font-semibold text-stone-900">
                            {material.display_name}
                          </h3>
                          {material.is_primary ? (
                            <span className="rounded-full bg-primary px-2.5 py-1 text-[11px] font-medium text-white">
                              默认材料
                            </span>
                          ) : null}
                          {!canPromote ? (
                            <span className="rounded-full border border-stone-200 bg-stone-100 px-2.5 py-1 text-[11px] text-stone-500">
                              仅随信发送
                            </span>
                          ) : null}
                          <span className="rounded-full border border-stone-200 bg-white px-2.5 py-1 text-[11px] text-stone-600">
                            {MATERIAL_TYPE_LABELS[material.material_type]}
                          </span>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-3 text-xs text-stone-500">
                          <span>{material.original_filename}</span>
                          <span>{formatFileSize(material.size_bytes)}</span>
                          <span>{formatApiDateTime(material.created_at)}</span>
                        </div>
                      </div>

                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => onOpen(material)}
                          className="ui-btn-secondary"
                        >
                          <ExternalLink className="h-4 w-4" />
                          打开
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            triggerDownload(getMaterialDownloadUrl(material.id))
                          }
                          className="ui-btn-secondary"
                        >
                          <Download className="h-4 w-4" />
                          下载
                        </button>
                        {material.is_primary ? (
                          <span className="inline-flex items-center rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-700">
                            已设为默认材料
                          </span>
                        ) : (
                          <button
                            type="button"
                            disabled={busy || !canPromote}
                            onClick={() => onSetPrimary(material)}
                            className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {canPromote ? "设为默认材料" : "不可设默认材料"}
                          </button>
                        )}
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => onDelete(material)}
                          className="ui-btn-danger disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          删除
                        </button>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export const ProfilePage = () => {
  const {
    identities,
    llmProfiles,
    selectedIdentityId,
    selectedLlmProfileId,
    selectedIdentity,
    selectedLlmProfile,
    setSelectedIdentityId,
    setSelectedLlmProfileId,
    refreshSelections,
    loading,
  } = useSelectionContext();
  const { notifyError, notifyFormErrors, notifySuccess } = useNotification();
  const {
    isReady: desktopBackendReady,
    disableReason: desktopDisableReason,
  } = useDesktopBackend();
  const [identityEditorId, setIdentityEditorId] = useState<EditorId>(null);
  const [llmEditorId, setLlmEditorId] = useState<EditorId>(null);
  const [identityForm, setIdentityForm] = useState<IdentityFormState>(
    createEmptyIdentityForm(),
  );
  const [llmForm, setLlmForm] = useState<LLMFormState>(createEmptyLLMForm());
  const [submittingIdentity, setSubmittingIdentity] = useState(false);
  const [submittingLLM, setSubmittingLLM] = useState(false);
  const [importingTemplateFile, setImportingTemplateFile] = useState(false);
  const [testingIdentityConnection, setTestingIdentityConnection] = useState<
    "smtp" | "imap" | null
  >(null);
  const [lastIdentityConnectionResult, setLastIdentityConnectionResult] =
    useState<IdentityConnectionTestSummary | null>(null);
  const [testingLLMConnection, setTestingLLMConnection] = useState(false);
  const [fetchingLLMModels, setFetchingLLMModels] = useState(false);
  const [llmProbeResult, setLlmProbeResult] =
    useState<LLMProfileTestResultDTO | null>(null);
  const [llmModelsResult, setLlmModelsResult] =
    useState<LLMProfileModelsResultDTO | null>(null);
  const [uploadingMaterial, setUploadingMaterial] = useState(false);
  const [actingOnMaterial, setActingOnMaterial] = useState(false);
  const [newMaterialType, setNewMaterialType] =
    useState<IdentityMaterialType>("resume");
  const [materialFilter, setMaterialFilter] =
    useState<MaterialFilterValue>("all");
  const [templateModalOpen, setTemplateModalOpen] = useState(false);
  const [materialModalOpen, setMaterialModalOpen] = useState(false);
  const [highlightedMaterialId, setHighlightedMaterialId] = useState<
    number | null
  >(null);
  const [optimisticMaterial, setOptimisticMaterial] =
    useState<IdentityMaterialDTO | null>(null);
  const [openSetupSections, setOpenSetupSections] = useState<
    Record<ProfileSetupSectionId, boolean>
  >({
    identity: false,
    materials: false,
    model: false,
    test: false,
  });
  const [renderedSetupSections, setRenderedSetupSections] = useState<
    Record<ProfileSetupSectionId, boolean>
  >({
    identity: false,
    materials: false,
    model: false,
    test: false,
  });
  const [testComposeSetupStatus, setTestComposeSetupStatus] =
    useState<TestComposeSetupStatus>("unchecked");
  const identityNameInputRef = useRef<HTMLInputElement | null>(null);
  const llmNameInputRef = useRef<HTMLInputElement | null>(null);
  const identityEditorIdRef = useRef<EditorId>(null);
  const setupSectionRefs = useRef<
    Record<ProfileSetupSectionId, HTMLElement | null>
  >({
    identity: null,
    materials: null,
    model: null,
    test: null,
  });
  const templateSubjectRef = useRef("");
  const { confirm, dialog: confirmDialog } = useConfirmDialog();

  identityEditorIdRef.current = identityEditorId;
  templateSubjectRef.current = identityForm.outreach_template_subject;

  const focusInput = (element: HTMLInputElement | null) => {
    if (!element) {
      return;
    }
    element.focus();
    element.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const getActionErrorMessage = (error: unknown, fallbackMessage: string) =>
    error instanceof Error ? error.message : fallbackMessage;
  const setSetupSectionRef = useCallback(
    (sectionId: ProfileSetupSectionId, element: HTMLElement | null) => {
      setupSectionRefs.current[sectionId] = element;
    },
    [],
  );
  const toggleSetupSection = useCallback((sectionId: ProfileSetupSectionId) => {
    setRenderedSetupSections((previous) => ({
      ...previous,
      [sectionId]: true,
    }));
    setOpenSetupSections((previous) => ({
      ...previous,
      [sectionId]: !previous[sectionId],
    }));
  }, []);
  const openAndScrollToSetupSection = useCallback(
    (sectionId: ProfileSetupSectionId) => {
      setRenderedSetupSections((previous) => ({
        ...previous,
        [sectionId]: true,
      }));
      setOpenSetupSections((previous) => ({
        ...previous,
        [sectionId]: true,
      }));
      window.requestAnimationFrame(() => {
        setupSectionRefs.current[sectionId]?.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      });
    },
    [],
  );
  const handleSetupSectionExitComplete = useCallback(
    (sectionId: ProfileSetupSectionId) => {
      setRenderedSetupSections((previous) => ({
        ...previous,
        [sectionId]: false,
      }));
    },
    [],
  );

  const applyIdentityEditorState = useCallback(
    (nextEditor: IdentityDTO | "new") => {
      if (nextEditor === "new") {
        setIdentityEditorId("new");
        setIdentityForm(createEmptyIdentityForm());
      } else {
        setIdentityEditorId(nextEditor.id);
        setIdentityForm(toIdentityForm(nextEditor));
      }
      setTemplateModalOpen(false);
      setTestingIdentityConnection(null);
      setLastIdentityConnectionResult(null);
      setHighlightedMaterialId(null);
      setOptimisticMaterial(null);
    },
    [],
  );

  const confirmDeleteTwice = async (targetName: string) => {
    const confirmedOnce = await confirm({
      title: `确认删除${targetName}？`,
      description: "这会移除当前内容，但还不会立即执行最终删除。",
      confirmLabel: "继续删除",
      cancelLabel: "先不删",
      tone: "danger",
    });

    if (!confirmedOnce) {
      return false;
    }

    return confirm({
      title: `再次确认删除${targetName}`,
      description: "删除后无法恢复，请再确认一次。",
      confirmLabel: "确认删除",
      cancelLabel: "返回",
      tone: "danger",
    });
  };

  useEffect(() => {
    if (loading || identityEditorId === "new") {
      return;
    }
    if (
      isExistingEditorId(identityEditorId) &&
      identities.some((item) => item.id === identityEditorId)
    ) {
      return;
    }

    const fallback =
      identities.find((item) => item.id === selectedIdentityId) ??
      identities[0] ??
      null;

    if (fallback) {
      applyIdentityEditorState(fallback);
      return;
    }

    applyIdentityEditorState("new");
  }, [
    applyIdentityEditorState,
    identities,
    identityEditorId,
    loading,
    selectedIdentityId,
  ]);

  useEffect(() => {
    if (loading || llmEditorId === "new") {
      return;
    }
    if (
      isExistingEditorId(llmEditorId) &&
      llmProfiles.some((item) => item.id === llmEditorId)
    ) {
      return;
    }

    const fallback =
      llmProfiles.find((item) => item.id === selectedLlmProfileId) ??
      llmProfiles[0] ??
      null;

    if (fallback) {
      setLlmEditorId(fallback.id);
      setLlmForm(toLLMForm(fallback));
      return;
    }

    setLlmEditorId("new");
    setLlmForm(createEmptyLLMForm());
  }, [llmEditorId, llmProfiles, loading, selectedLlmProfileId]);

  useEffect(() => {
    if (!materialModalOpen && !templateModalOpen) {
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (templateModalOpen) {
          setTemplateModalOpen(false);
          return;
        }
        setMaterialModalOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [materialModalOpen, templateModalOpen]);

  const editingIdentity = isExistingEditorId(identityEditorId)
    ? (identities.find((item) => item.id === identityEditorId) ?? null)
    : null;
  const editingLLM = isExistingEditorId(llmEditorId)
    ? (llmProfiles.find((item) => item.id === llmEditorId) ?? null)
    : null;

  const defaultIdentity = identities.find((item) => item.is_default) ?? null;
  const defaultLLMProfile = llmProfiles.find((item) => item.is_default) ?? null;
  const llmModelsActionState: ActionResultState = llmModelsResult
    ? llmModelsResult.ok
      ? "success"
      : "error"
    : "idle";
  const llmProbeActionState: ActionResultState = llmProbeResult
    ? llmProbeResult.ok
      ? "success"
      : "error"
    : "idle";
  const displayIdentity = useMemo(() => {
    if (
      !editingIdentity ||
      !optimisticMaterial ||
      editingIdentity.materials.some(
        (material) => material.id === optimisticMaterial.id,
      )
    ) {
      return editingIdentity;
    }

    return {
      ...editingIdentity,
      materials: [optimisticMaterial, ...editingIdentity.materials],
      current_primary_material: optimisticMaterial.is_primary
        ? optimisticMaterial
        : editingIdentity.current_primary_material,
      current_primary_material_id: optimisticMaterial.is_primary
        ? optimisticMaterial.id
        : editingIdentity.current_primary_material_id,
    };
  }, [editingIdentity, optimisticMaterial]);
  const setupIdentity =
    displayIdentity ??
    editingIdentity ??
    selectedIdentity ??
    defaultIdentity ??
    identities[0] ??
    null;
  const setupLlmProfile =
    selectedLlmProfile ?? defaultLLMProfile ?? llmProfiles[0] ?? null;
  const setupHasTemplate = Boolean(
    setupIdentity?.outreach_template_subject?.trim() &&
    (setupIdentity.outreach_template_body_text?.trim() ||
      setupIdentity.outreach_template_body_html?.trim()),
  );
  const setupHasMaterial = Boolean(
    setupIdentity?.current_primary_material || setupIdentity?.materials.length,
  );
  const setupItems = useMemo<ProfileSetupItem[]>(() => {
    const hasIdentity = Boolean(setupIdentity);
    const hasLlmProfile = Boolean(setupLlmProfile);
    const materialsCompleted = setupHasTemplate && setupHasMaterial;
    const testComposeCompleted = testComposeSetupStatus === "completed";
    const testComposeStatusDetail =
      testComposeSetupStatus === "loading"
        ? "正在检查测试写信记录"
        : testComposeCompleted
          ? "已发送测试邮件"
          : hasIdentity && hasLlmProfile
            ? "待发送测试邮件确认"
            : "待选择身份和模型";
    const materialStatusDetail = !setupIdentity
      ? "待保存身份后上传材料"
      : materialsCompleted
        ? "默认模板和材料已准备"
        : !setupHasTemplate && !setupHasMaterial
          ? "待填写默认模板并上传材料"
          : !setupHasTemplate
            ? "待填写默认模板"
            : "待上传材料";

    return PROFILE_SETUP_STAGES.map((stage) => {
      if (stage.id === "identity") {
        return {
          ...stage,
          completed: hasIdentity,
          statusDetail: hasIdentity
            ? `已保存身份：${getIdentityProfileName(setupIdentity!)}`
            : "待创建发件身份",
        };
      }
      if (stage.id === "materials") {
        return {
          ...stage,
          completed: materialsCompleted,
          statusDetail: materialStatusDetail,
        };
      }
      if (stage.id === "model") {
        return {
          ...stage,
          completed: hasLlmProfile,
          statusDetail: hasLlmProfile
            ? `已保存模型：${setupLlmProfile!.name}`
            : "待保存模型配置",
        };
      }
      return {
        ...stage,
        completed: testComposeCompleted,
        statusDetail: testComposeStatusDetail,
      };
    });
  }, [
    setupHasMaterial,
    setupHasTemplate,
    setupIdentity,
    setupLlmProfile,
    testComposeSetupStatus,
  ]);

  useEffect(() => {
    if (!selectedIdentityId) {
      setTestComposeSetupStatus("unchecked");
      return;
    }

    let ignore = false;

    const loadTestComposeStatus = async () => {
      setTestComposeSetupStatus("loading");
      try {
        const status = await getTestComposeStatus(selectedIdentityId);
        if (ignore) {
          return;
        }
        setTestComposeSetupStatus(status.completed ? "completed" : "pending");
      } catch {
        if (!ignore) {
          setTestComposeSetupStatus("pending");
        }
      }
    };

    void loadTestComposeStatus();

    return () => {
      ignore = true;
    };
  }, [selectedIdentityId]);

  useEffect(() => {
    if (!editingIdentity) {
      setMaterialModalOpen(false);
    }
  }, [editingIdentity]);

  useEffect(() => {
    if (!editingIdentity || !optimisticMaterial) {
      return;
    }
    if (
      editingIdentity.materials.some(
        (material) => material.id === optimisticMaterial.id,
      )
    ) {
      setOptimisticMaterial(null);
    }
  }, [editingIdentity, optimisticMaterial]);

  useEffect(() => {
    if (!materialModalOpen || highlightedMaterialId === null) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      const element = document.querySelector<HTMLElement>(
        `[data-material-id="${highlightedMaterialId}"]`,
      );
      element?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [displayIdentity, highlightedMaterialId, materialModalOpen]);

  const beginIdentityCreation = () => {
    applyIdentityEditorState("new");
    window.requestAnimationFrame(() =>
      focusInput(identityNameInputRef.current),
    );
  };

  const beginLLMCreation = () => {
    setLlmEditorId("new");
    setLlmForm(createEmptyLLMForm());
    setLlmProbeResult(null);
    setLlmModelsResult(null);
    setTestingLLMConnection(false);
    setFetchingLLMModels(false);
    window.requestAnimationFrame(() => focusInput(llmNameInputRef.current));
  };

  const openIdentityEditor = (identityId: number) => {
    const identity = identities.find((item) => item.id === identityId);
    if (!identity) {
      return;
    }
    applyIdentityEditorState(identity);
  };

  const openLLMEditor = (profileId: number) => {
    const profile = llmProfiles.find((item) => item.id === profileId);
    if (!profile) {
      return;
    }
    setLlmEditorId(profile.id);
    setLlmForm(toLLMForm(profile));
    setLlmProbeResult(null);
    setLlmModelsResult(null);
    setTestingLLMConnection(false);
    setFetchingLLMModels(false);
  };

  const handleSmtpHostChange = (nextSmtpHost: string) => {
    setIdentityForm((previous) => ({
      ...previous,
      smtp_host: nextSmtpHost,
      imap_host: shouldSyncImapHost(previous.smtp_host, previous.imap_host)
        ? inferImapHost(nextSmtpHost)
        : previous.imap_host,
    }));
  };

  const runIdentityConnectionTest = async (kind: "smtp" | "imap") => {
    if (!editingIdentity) {
      return;
    }

    setTestingIdentityConnection(kind);
    try {
      const result =
        kind === "smtp"
          ? await testIdentitySmtp(editingIdentity.id)
          : await testIdentityImap(editingIdentity.id);
      if (!result.ok) {
        setLastIdentityConnectionResult({
          kind,
          status: "error",
          message: result.message,
        });
        notifyError(`${kind.toUpperCase()} 连接测试失败`, result.message);
        return;
      }
      setLastIdentityConnectionResult({
        kind,
        status: "success",
        message: result.message,
      });
      notifySuccess(`${kind.toUpperCase()} 连接测试成功`, result.message);
    } catch (testError) {
      const message = getActionErrorMessage(
        testError,
        `${kind.toUpperCase()} 测试失败`,
      );
      setLastIdentityConnectionResult({
        kind,
        status: "error",
        message,
      });
      notifyError(`${kind.toUpperCase()} 连接测试失败`, message);
    } finally {
      setTestingIdentityConnection(null);
    }
  };

  const handleTemplateFileImport = async (file: File) => {
    if (importingTemplateFile) {
      return;
    }

    const importTargetEditorId = identityEditorId;
    const hasExistingTemplateBody = hasVisibleTemplateBody(identityForm);

    if (hasExistingTemplateBody) {
      const shouldReplaceTemplateBody = await confirm({
        title: "确认覆盖默认模板正文？",
        description: "导入模板文件会替换当前正文内容，主题不会被修改。",
        confirmLabel: "覆盖并导入",
        cancelLabel: "取消",
        tone: "danger",
      });

      if (!shouldReplaceTemplateBody) {
        return;
      }
    }

    setImportingTemplateFile(true);
    try {
      const imported = await importIdentityTemplate(file);
      if (identityEditorIdRef.current !== importTargetEditorId) {
        return;
      }

      const hasSubject = Boolean(templateSubjectRef.current.trim());
      setIdentityForm((previous) => ({
        ...previous,
        outreach_template_body_text: imported.body_text,
        outreach_template_body_html: imported.body_html,
      }));
      notifySuccess(
        "模板导入成功",
        hasSubject
          ? `已导入 ${imported.format_name} 模板文件，并自动生成纯文本正文。`
          : `已导入 ${imported.format_name} 模板文件，并自动生成纯文本正文。请继续填写模板主题后再保存身份。`,
      );
    } catch (importError) {
      notifyError(
        "模板导入失败",
        getActionErrorMessage(importError, "导入模板文件失败"),
      );
    } finally {
      setImportingTemplateFile(false);
    }
  };

  const runLlmConnectionTest = async () => {
    if (!editingLLM) {
      return;
    }

    setTestingLLMConnection(true);
    setLlmProbeResult(null);
    try {
      const result = await testLLMProfilePreview(toLLMPayload(llmForm));
      setLlmProbeResult(result);
    } catch (testError) {
      setLlmProbeResult({
        ok: false,
        message:
          testError instanceof Error ? testError.message : "连接测试失败",
        resolved_base_url: null,
        request_url: null,
        attempted_urls: [],
        endpoint_kind: null,
        status_code: null,
        duration_ms: null,
        consumes_tokens: true,
        prompt_tokens: null,
        completion_tokens: null,
        total_tokens: null,
        response_preview: null,
      });
    } finally {
      setTestingLLMConnection(false);
    }
  };

  const runLlmModelsFetch = async () => {
    if (!editingLLM) {
      return;
    }

    setFetchingLLMModels(true);
    setLlmModelsResult(null);
    try {
      const result = await fetchLLMProfileModelsPreview(toLLMPayload(llmForm));
      setLlmModelsResult(result);
    } catch (testError) {
      setLlmModelsResult({
        ok: false,
        message:
          testError instanceof Error ? testError.message : "获取模型列表失败",
        resolved_base_url: null,
        request_url: null,
        attempted_urls: [],
        endpoint_kind: null,
        status_code: null,
        duration_ms: null,
        consumes_tokens: false,
        models: [],
        selected_model_available: null,
      });
    } finally {
      setFetchingLLMModels(false);
    }
  };

  const handleSelectSuggestedModel = (modelName: string) => {
    setLlmForm((previous) => ({
      ...previous,
      model_name: modelName,
    }));
  };

  const saveIdentity = async ({
    validateTemplate = false,
  }: { validateTemplate?: boolean } = {}): Promise<IdentityDTO | null> => {
    if (!desktopBackendReady) {
      notifyError(
        "系统正在准备本地数据",
        "这不是身份配置错误。请等待系统准备完成后再保存，已填写内容不会丢失。",
      );
      return null;
    }

    if (!identityForm.profile_name.trim() || !identityForm.sender_name.trim()) {
      notifyFormErrors("请检查表单", ["请填写配置名称和发件人姓名"]);
      return null;
    }
    if (
      !identityForm.email_address.trim() ||
      !identityForm.smtp_host.trim() ||
      !identityForm.smtp_password.trim() ||
      !identityForm.imap_host.trim() ||
      !identityForm.imap_port.trim()
    ) {
      notifyFormErrors("请检查表单", ["请先填写所有带红色星号的身份必填项"]);
      return null;
    }
    if (validateTemplate) {
      const templateValidationMessage =
        getTemplateValidationMessage(identityForm);
      if (templateValidationMessage) {
        notifyFormErrors("请检查表单", [templateValidationMessage]);
        return null;
      }
    }

    setSubmittingIdentity(true);
    try {
      const payload = toIdentityPayload(identityForm);
      const saved = isExistingEditorId(identityEditorId)
        ? await updateIdentity(identityEditorId, payload)
        : await createIdentity(payload);
      const isCreatingIdentity = identityEditorId === "new";
      await refreshSelections();
      setIdentityEditorId(saved.id);
      setIdentityForm(toIdentityForm(saved));
      notifySuccess(
        "身份保存成功",
        isCreatingIdentity ? "身份已创建。" : "身份已保存。",
      );
      return saved;
    } catch (saveError) {
      notifyError(
        "身份保存失败",
        getActionErrorMessage(saveError, "身份保存失败"),
      );
      return null;
    } finally {
      setSubmittingIdentity(false);
    }
  };

  const saveLLM = async () => {
    if (
      !llmForm.name.trim() ||
      !llmForm.api_base_url.trim() ||
      !llmForm.api_key.trim() ||
      !llmForm.model_name.trim()
    ) {
      notifyFormErrors("请检查表单", ["请先填写所有带红色星号的模型必填项"]);
      return;
    }

    setSubmittingLLM(true);
    try {
      const payload = toLLMPayload(llmForm);
      const saved = isExistingEditorId(llmEditorId)
        ? await updateLLMProfile(llmEditorId, payload)
        : await createLLMProfile(payload);
      const isCreatingLlm = llmEditorId === "new";
      await refreshSelections();
      setLlmEditorId(saved.id);
      setLlmForm(toLLMForm(saved));
      notifySuccess(
        "模型保存成功",
        isCreatingLlm ? "模型配置已创建。" : "模型配置已保存。",
      );
    } catch (saveError) {
      notifyError(
        "模型保存失败",
        getActionErrorMessage(saveError, "模型配置保存失败"),
      );
    } finally {
      setSubmittingLLM(false);
    }
  };

  const handleOpenMaterial = async (material: IdentityMaterialDTO) => {
    if (!isDesktopApp()) {
      notifyError("无法打开材料", "请在桌面应用中打开材料，或使用下载按钮保存后查看。");
      return;
    }

    const result = await openDesktopMaterial(material.id);
    if (!result.ok) {
      notifyError("无法打开材料", result.message);
    }
  };

  const handleMaterialUpload = async (file: File) => {
    if (!editingIdentity) {
      return;
    }
    setUploadingMaterial(true);
    try {
      const uploadedMaterial = await uploadIdentityMaterial(
        editingIdentity.id,
        {
          file,
          materialType: newMaterialType,
        },
      );
      setOptimisticMaterial(uploadedMaterial);
      setMaterialFilter(uploadedMaterial.material_type);
      setHighlightedMaterialId(uploadedMaterial.id);
      await refreshSelections();
      notifySuccess(
        "材料上传成功",
        `已上传为${getMaterialTypeLabel(uploadedMaterial.material_type)}：${uploadedMaterial.display_name}`,
      );
    } catch (uploadError) {
      notifyError(
        "材料上传失败",
        getActionErrorMessage(uploadError, "材料上传失败"),
      );
    } finally {
      setUploadingMaterial(false);
    }
  };

  const handleSetPrimaryMaterial = async (material: IdentityMaterialDTO) => {
    setActingOnMaterial(true);
    try {
      await setPrimaryMaterial(material.id);
      await refreshSelections();
      notifySuccess(
        "设为默认材料成功",
        `已将“${material.display_name}”设为默认材料。`,
      );
      setHighlightedMaterialId(material.id);
    } catch (materialError) {
      notifyError(
        "设为默认材料失败",
        getActionErrorMessage(materialError, "设置默认材料失败"),
      );
    } finally {
      setActingOnMaterial(false);
    }
  };

  const handleDeleteMaterial = async (material: IdentityMaterialDTO) => {
    if (!(await confirmDeleteTwice(`材料“${material.display_name}”`))) {
      return;
    }
    setActingOnMaterial(true);
    try {
      await deleteMaterial(material.id);
      await refreshSelections();
      notifySuccess(
        "删除材料成功",
        material.is_primary
          ? `材料“${material.display_name}”已删除，当前未设默认材料。`
          : `材料“${material.display_name}”已删除。`,
      );
      if (optimisticMaterial?.id === material.id) {
        setOptimisticMaterial(null);
      }
      if (highlightedMaterialId === material.id) {
        setHighlightedMaterialId(null);
      }
    } catch (materialError) {
      notifyError(
        "删除材料失败",
        getActionErrorMessage(materialError, "删除材料失败"),
      );
    } finally {
      setActingOnMaterial(false);
    }
  };

  const identityActionButtons = (
    <div className="mt-6 flex flex-wrap gap-3">
      <button
        type="button"
        onClick={() => void saveIdentity()}
        disabled={submittingIdentity || !desktopBackendReady}
        className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
      >
        {submittingIdentity && <Loader2 className="h-4 w-4 animate-spin" />}
        {!desktopBackendReady ? (desktopDisableReason ?? "系统准备中") : "保存身份"}
      </button>
      {!desktopBackendReady && (
        <p className="basis-full text-xs text-amber-700">
          本地数据准备完成后即可继续操作，已填写内容不会丢失。
        </p>
      )}
      {editingIdentity && (
        <>
          {selectedIdentityId === editingIdentity.id ? (
            <span className="inline-flex items-center rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-700">
              当前使用中
            </span>
          ) : (
            <button
              type="button"
              onClick={() => {
                setSelectedIdentityId(editingIdentity.id);
                notifySuccess(
                  "已设为当前身份",
                  `当前身份已切换为“${getIdentityProfileName(editingIdentity)}”。`,
                );
              }}
              className="ui-btn-secondary"
            >
              设为当前
            </button>
          )}
          {editingIdentity.is_default ? (
            <span className="inline-flex items-center rounded-xl border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-700">
              已设为默认
            </span>
          ) : (
            <button
              type="button"
              onClick={() => {
                void setDefaultIdentity(editingIdentity.id)
                  .then(async () => {
                    await refreshSelections();
                    setIdentityForm((previous) => ({
                      ...previous,
                      is_default: true,
                    }));
                    notifySuccess(
                      "已设为默认身份",
                      `“${getIdentityProfileName(editingIdentity)}”已设为默认身份。`,
                    );
                  })
                  .catch((defaultError) => {
                    notifyError(
                      "设为默认身份失败",
                      getActionErrorMessage(defaultError, "设置默认身份失败"),
                    );
                  });
              }}
              className="ui-btn-secondary"
            >
              设为默认
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              void (async () => {
                if (
                  !(await confirmDeleteTwice(
                    `身份“${getIdentityProfileName(editingIdentity)}”`,
                  ))
                ) {
                  return;
                }
                try {
                  await deleteIdentity(editingIdentity.id);
                  await refreshSelections();
                  setIdentityEditorId(null);
                  setIdentityForm(createEmptyIdentityForm());
                  notifySuccess(
                    "删除身份成功",
                    `身份“${getIdentityProfileName(editingIdentity)}”已删除。`,
                  );
                } catch (deleteError) {
                  notifyError(
                    "删除身份失败",
                    getActionErrorMessage(deleteError, "删除身份失败"),
                  );
                }
              })();
            }}
            className="ui-btn-danger"
          >
            删除
          </button>
        </>
      )}
    </div>
  );
  const setIdentitySetupSectionRef = useCallback(
    (element: HTMLElement | null) => setSetupSectionRef("identity", element),
    [setSetupSectionRef],
  );
  const setMaterialsSetupSectionRef = useCallback(
    (element: HTMLElement | null) => setSetupSectionRef("materials", element),
    [setSetupSectionRef],
  );
  const setModelSetupSectionRef = useCallback(
    (element: HTMLElement | null) => setSetupSectionRef("model", element),
    [setSetupSectionRef],
  );
  const setTestSetupSectionRef = useCallback(
    (element: HTMLElement | null) => setSetupSectionRef("test", element),
    [setSetupSectionRef],
  );

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <div className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-6 shadow-sm">
        <h1 className="text-3xl font-semibold text-stone-900">个人中心</h1>
        <div className="mt-4 flex flex-wrap gap-3 text-xs text-stone-600">
          <span className="rounded-full border border-stone-200 bg-white px-3 py-1.5">
            身份：
            {selectedIdentity
              ? getIdentityProfileName(selectedIdentity)
              : "未选择"}
          </span>
          <span className="rounded-full border border-stone-200 bg-white px-3 py-1.5">
            模型：{selectedLlmProfile?.name ?? "未选择"}
          </span>
        </div>
      </div>

      {loading ? (
        <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载配置...
        </div>
      ) : (
        <div className="mt-6 space-y-6">
          <section className="rounded-3xl border border-stone-200 bg-[linear-gradient(135deg,rgba(248,244,236,0.95),rgba(255,255,255,0.98))] p-6 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="text-xl font-semibold text-stone-900">
                  首次配置建议
                </h2>
                <p className="mt-2 text-sm leading-6 text-stone-600">
                  按顺序完成身份、材料、模型和测试写信。
                </p>
              </div>
              <span className="rounded-full border border-stone-200 bg-white px-3 py-1.5 text-xs text-stone-600">
                新用户上手流程
              </span>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {setupItems.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => openAndScrollToSetupSection(item.id)}
                  className={clsx(
                    "rounded-2xl border bg-white px-4 py-3 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-primary/20",
                    item.completed ? "border-emerald-200" : "border-amber-200",
                  )}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm font-semibold text-stone-900">
                      {item.label}
                    </span>
                    <span
                      className={clsx(
                        "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium",
                        item.completed
                          ? "bg-emerald-100 text-emerald-700"
                          : "bg-amber-100 text-amber-700",
                      )}
                    >
                      {item.completed ? (
                        <CheckCircle2 className="h-3.5 w-3.5" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5" />
                      )}
                      {item.completed ? "已完成" : "待完成"}
                    </span>
                  </div>
                  <div className="mt-2 text-xs leading-5 text-stone-500">
                    {item.statusDetail}
                  </div>
                </button>
              ))}
            </div>
          </section>

          <ProfileSetupSection
            sectionId="identity"
            title="发件身份"
            description="配置发件邮箱、SMTP 和 IMAP。"
            open={openSetupSections.identity}
            renderContent={renderedSetupSections.identity}
            onToggle={() => toggleSetupSection("identity")}
            onExitComplete={() => handleSetupSectionExitComplete("identity")}
            sectionRef={setIdentitySetupSectionRef}
            badge={
              <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
                默认身份：
                {defaultIdentity
                  ? getIdentityProfileName(defaultIdentity)
                  : "未设置"}
              </span>
            }
          >
            <div className="mt-5 rounded-3xl border border-stone-200 bg-[#fcfbf8] p-4">
              <div className="grid gap-4 lg:grid-cols-[1.2fr,0.8fr]">
                <EditorSwitcher
                  label="当前编辑的身份配置"
                  helper={
                    identities.length > 0 ? "点选切换，或新建一套。" : undefined
                  }
                  options={identities.map((identity) => ({
                    ...identity,
                    name: getIdentityProfileName(identity),
                  }))}
                  activeId={identityEditorId}
                  createLabel="新建身份配置"
                  creatingLabel={
                    identities.length > 0
                      ? "正在新建身份配置"
                      : "新建第一套身份配置"
                  }
                  onCreate={beginIdentityCreation}
                  onSelect={openIdentityEditor}
                />

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3">
                    <div className="text-xs uppercase tracking-[0.18em] text-stone-400">
                      使用中
                    </div>
                    <div className="mt-2 text-sm font-medium text-stone-900">
                      {selectedIdentity
                        ? getIdentityProfileName(selectedIdentity)
                        : "未选择"}
                    </div>
                  </div>
                  <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3">
                    <div className="text-xs uppercase tracking-[0.18em] text-stone-400">
                      编辑中
                    </div>
                    <div className="mt-2 text-sm font-medium text-stone-900">
                      {editingIdentity
                        ? `正在编辑 ${getIdentityProfileName(editingIdentity)}`
                        : "正在新建身份配置"}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <label className="block">
                {renderFieldLabel("配置名称", true)}
                <input
                  ref={identityNameInputRef}
                  aria-label="配置名称"
                  value={identityForm.profile_name}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      name: event.target.value,
                      profile_name: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：博士申请邮箱"
                />
              </label>
              <label className="block">
                {renderFieldLabel("发件人姓名", true)}
                <input
                  aria-label="发件人姓名"
                  value={identityForm.sender_name}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      sender_name: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：张三"
                />
              </label>
              <label className="block">
                {renderFieldLabel("发件邮箱", true)}
                <input
                  value={identityForm.email_address}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      email_address: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：your.name@example.com"
                />
              </label>
              <label className="block">
                {renderFieldLabel("SMTP Host", true)}
                <input
                  value={identityForm.smtp_host}
                  onChange={(event) => handleSmtpHostChange(event.target.value)}
                  className={inputClassName}
                  placeholder="示例：smtp.163.com"
                />
              </label>
              <label className="block">
                {renderFieldLabel("SMTP Port", true)}
                <input
                  type="number"
                  value={identityForm.smtp_port}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      smtp_port: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：465"
                />
              </label>
              <label className="block md:col-span-2">
                {renderFieldLabel("SMTP 密码", true)}
                <input
                  type="password"
                  value={identityForm.smtp_password}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      smtp_password: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：邮箱授权码或应用专用密码（可从网页版邮箱设置页面中获取）"
                />
              </label>
              <label className="block">
                {renderFieldLabel("IMAP Host", true)}
                <input
                  value={identityForm.imap_host}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      imap_host: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：imap.163.com"
                />
              </label>
              <label className="block">
                {renderFieldLabel("IMAP Port", true)}
                <input
                  type="number"
                  value={identityForm.imap_port}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      imap_port: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：993"
                />
              </label>
            </div>

            {identityActionButtons}

            {editingIdentity ? (
              <div className="mt-6">
                <IdentityConnectionCard
                  testingIdentityConnection={testingIdentityConnection}
                  lastResult={lastIdentityConnectionResult}
                  onTestSmtp={() => void runIdentityConnectionTest("smtp")}
                  onTestImap={() => void runIdentityConnectionTest("imap")}
                />
              </div>
            ) : null}
          </ProfileSetupSection>

          <ProfileSetupSection
            sectionId="materials"
            title="材料与模板"
            description="准备默认模板和常用材料。"
            open={openSetupSections.materials}
            renderContent={renderedSetupSections.materials}
            onToggle={() => toggleSetupSection("materials")}
            onExitComplete={() => handleSetupSectionExitComplete("materials")}
            sectionRef={setMaterialsSetupSectionRef}
            badge={
              <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
                任务准备
              </span>
            }
          >
            <div className="mt-6">
              <OutreachTemplateSummaryCard
                form={identityForm}
                onOpen={() => setTemplateModalOpen(true)}
              />
            </div>

            {editingIdentity && (
              <div className="mt-6">
                <MaterialSummaryCard
                  identity={displayIdentity ?? editingIdentity}
                  onOpen={() => {
                    setMaterialFilter("all");
                    setHighlightedMaterialId(null);
                    setMaterialModalOpen(true);
                  }}
                />
              </div>
            )}
            {!editingIdentity ? (
              <div className="mt-6 rounded-2xl border border-dashed border-stone-200 bg-stone-50/80 px-4 py-4 text-sm leading-6 text-stone-500">
                创建并保存发件身份后，可上传材料。
              </div>
            ) : null}
          </ProfileSetupSection>

          <ProfileSetupSection
            sectionId="model"
            title="模型配置"
            description="以 DeepSeek 为示例配置可用模型并测试连接。"
            open={openSetupSections.model}
            renderContent={renderedSetupSections.model}
            onToggle={() => toggleSetupSection("model")}
            onExitComplete={() => handleSetupSectionExitComplete("model")}
            sectionRef={setModelSetupSectionRef}
            badge={
              <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
                默认模型：{defaultLLMProfile?.name ?? "未设置"}
              </span>
            }
          >
            <div className="mt-5 rounded-3xl border border-stone-200 bg-[#fcfbf8] p-4">
              <div className="grid gap-4 lg:grid-cols-[1.2fr,0.8fr]">
                <EditorSwitcher
                  label="当前编辑的模型配置"
                  helper={
                    llmProfiles.length > 0
                      ? "点选切换，或新建一套。"
                      : undefined
                  }
                  options={llmProfiles}
                  activeId={llmEditorId}
                  createLabel="新建模型配置"
                  creatingLabel={
                    llmProfiles.length > 0
                      ? "正在新建模型配置"
                      : "新建第一套模型配置"
                  }
                  onCreate={beginLLMCreation}
                  onSelect={openLLMEditor}
                />

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3">
                    <div className="text-xs uppercase tracking-[0.18em] text-stone-400">
                      使用中
                    </div>
                    <div className="mt-2 text-sm font-medium text-stone-900">
                      {selectedLlmProfile?.name ?? "未选择"}
                    </div>
                  </div>
                  <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3">
                    <div className="text-xs uppercase tracking-[0.18em] text-stone-400">
                      编辑中
                    </div>
                    <div className="mt-2 text-sm font-medium text-stone-900">
                      {editingLLM
                        ? `正在编辑 ${editingLLM.name}`
                        : "正在新建模型配置"}
                    </div>
                  </div>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-500">
                <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
                  DeepSeek 示例
                </span>
                <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
                  Temperature {DEFAULT_LLM_TEMPERATURE}
                </span>
                <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
                  草稿 Token 默认 {DEFAULT_LLM_MAX_TOKENS}
                </span>
              </div>
            </div>

            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <label className="block">
                {renderFieldLabel("配置名称", true)}
                <input
                  ref={llmNameInputRef}
                  value={llmForm.name}
                  onChange={(event) =>
                    setLlmForm((previous) => ({
                      ...previous,
                      name: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：DeepSeek V4 Flash"
                />
              </label>
              <label className="block md:col-span-2">
                {renderFieldLabel("API Base URL", true)}
                <input
                  value={llmForm.api_base_url}
                  onChange={(event) =>
                    setLlmForm((previous) => ({
                      ...previous,
                      api_base_url: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：https://api.deepseek.com"
                />
              </label>
              <label className="block">
                {renderFieldLabel("API Key", true)}
                <input
                  type="password"
                  value={llmForm.api_key}
                  onChange={(event) =>
                    setLlmForm((previous) => ({
                      ...previous,
                      api_key: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：sk-xxxxxxxxxxxxxxxx"
                />
              </label>
              <label className="block">
                {renderFieldLabel("模型名称", true)}
                <input
                  value={llmForm.model_name}
                  onChange={(event) =>
                    setLlmForm((previous) => ({
                      ...previous,
                      model_name: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：deepseek-v4-flash"
                />
              </label>
            </div>

            <div className="mt-6 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => void saveLLM()}
                disabled={submittingLLM}
                className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
              >
                {submittingLLM && <Loader2 className="h-4 w-4 animate-spin" />}
                保存模型配置
              </button>
              {editingLLM && (
                <>
                  <button
                    type="button"
                    onClick={() => void runLlmModelsFetch()}
                    disabled={fetchingLLMModels}
                    className={getActionButtonClassName(
                      llmModelsActionState,
                      fetchingLLMModels,
                    )}
                  >
                    {fetchingLLMModels ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : llmModelsActionState === "success" ? (
                      <CheckCircle2 className="h-4 w-4" />
                    ) : llmModelsActionState === "error" ? (
                      <XCircle className="h-4 w-4" />
                    ) : null}
                    获取模型列表
                  </button>
                  <button
                    type="button"
                    onClick={() => void runLlmConnectionTest()}
                    disabled={testingLLMConnection}
                    className={getActionButtonClassName(
                      llmProbeActionState,
                      testingLLMConnection,
                    )}
                  >
                    {testingLLMConnection ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : llmProbeActionState === "success" ? (
                      <CheckCircle2 className="h-4 w-4" />
                    ) : llmProbeActionState === "error" ? (
                      <XCircle className="h-4 w-4" />
                    ) : null}
                    测试模型
                  </button>
                  {selectedLlmProfileId === editingLLM.id ? (
                    <span className="inline-flex items-center rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-700">
                      当前使用中
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedLlmProfileId(editingLLM.id);
                        notifySuccess(
                          "已设为当前模型",
                          `当前模型已切换为“${editingLLM.name}”。`,
                        );
                      }}
                      className="ui-btn-secondary"
                    >
                      设为当前
                    </button>
                  )}
                  {editingLLM.is_default ? (
                    <span className="inline-flex items-center rounded-xl border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-700">
                      已设为默认
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        void setDefaultLLMProfile(editingLLM.id)
                          .then(async () => {
                            await refreshSelections();
                            setLlmForm((previous) => ({
                              ...previous,
                              is_default: true,
                            }));
                            notifySuccess(
                              "已设为默认模型",
                              `“${editingLLM.name}”已设为默认模型。`,
                            );
                          })
                          .catch((defaultError) => {
                            notifyError(
                              "设为默认模型失败",
                              getActionErrorMessage(
                                defaultError,
                                "设置默认模型失败",
                              ),
                            );
                          });
                      }}
                      className="ui-btn-secondary"
                    >
                      设为默认
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      void (async () => {
                        if (
                          !(await confirmDeleteTwice(
                            `模型配置“${editingLLM.name}”`,
                          ))
                        ) {
                          return;
                        }
                        try {
                          await deleteLLMProfile(editingLLM.id);
                          await refreshSelections();
                          setLlmEditorId(null);
                          setLlmForm(createEmptyLLMForm());
                          notifySuccess(
                            "删除模型配置成功",
                            `模型配置“${editingLLM.name}”已删除。`,
                          );
                        } catch (deleteError) {
                          notifyError(
                            "删除模型配置失败",
                            getActionErrorMessage(
                              deleteError,
                              "删除模型配置失败",
                            ),
                          );
                        }
                      })();
                    }}
                    className="ui-btn-danger"
                  >
                    删除
                  </button>
                </>
              )}
            </div>
            {(llmModelsResult || llmProbeResult) && (
              <div className="mt-5 rounded-[30px] border border-stone-200 bg-[linear-gradient(180deg,rgba(252,251,248,0.96),rgba(255,255,255,0.98))] p-4 shadow-sm shadow-stone-200/60">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-stone-900">
                      连接诊断
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2 text-[11px] text-stone-500">
                    {llmModelsResult ? (
                      <span className="rounded-full border border-stone-200 bg-white px-2.5 py-1">
                        1. 基础连通性
                      </span>
                    ) : null}
                    {llmProbeResult ? (
                      <span className="rounded-full border border-stone-200 bg-white px-2.5 py-1">
                        2. 测试模型
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="mt-4 space-y-4">
                  {llmModelsResult ? (
                    <div className="space-y-2">
                      <div className="pl-1 text-[11px] uppercase tracking-[0.22em] text-stone-400">
                        Step 1
                      </div>
                      <LlmModelsFeedbackPanel
                        result={llmModelsResult}
                        currentModelName={llmForm.model_name}
                        onSelectModel={handleSelectSuggestedModel}
                      />
                    </div>
                  ) : null}
                  {llmProbeResult ? (
                    <div className="space-y-2">
                      <div className="pl-1 text-[11px] uppercase tracking-[0.22em] text-stone-400">
                        Step 2
                      </div>
                      <LlmTestFeedbackPanel result={llmProbeResult} />
                    </div>
                  ) : null}
                </div>
              </div>
            )}
          </ProfileSetupSection>

          <ProfileSetupSection
            sectionId="test"
            title="测试写信"
            description="用当前身份和模型发送一封测试邮件。"
            open={openSetupSections.test}
            renderContent={renderedSetupSections.test}
            onToggle={() => toggleSetupSection("test")}
            onExitComplete={() => handleSetupSectionExitComplete("test")}
            sectionRef={setTestSetupSectionRef}
            badge={
              <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
                完成前检查
              </span>
            }
          >
            <div className="mt-6">
              <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                <Send className="h-4 w-4 text-primary" />
                发送测试邮件
              </div>
              <p className="mt-2 text-sm leading-6 text-stone-600">
                给自己发一封测试邮件，检查模板、附件、模型和 SMTP。
              </p>
              <p className="mt-2 text-sm leading-6 text-stone-500">
                仅发送到当前身份邮箱，不写入导师任务。
              </p>
              <div className="mt-4">
                <Link to="/test-compose" className="ui-btn-primary">
                  <Send className="h-4 w-4" />
                  进入测试写信页
                </Link>
              </div>
            </div>

            <div className="mt-6 rounded-2xl border border-emerald-200 bg-emerald-50/80 px-4 py-4 text-sm leading-6 text-emerald-800">
              接着去「导师管理」导入导师，再回首页创建任务。
            </div>
          </ProfileSetupSection>

          <TokenUsageCenterCard />

          <OtherSettingsCard />

          <DiagnosticLogPanel />
        </div>
      )}
      <OutreachTemplateModal
        open={templateModalOpen}
        importingTemplateFile={importingTemplateFile}
        savingTemplate={submittingIdentity}
        form={identityForm}
        onClose={() => setTemplateModalOpen(false)}
        onComplete={() =>
          void saveIdentity({ validateTemplate: true }).then((saved) => {
            if (saved) {
              setTemplateModalOpen(false);
            }
          })
        }
        onImport={(file) => void handleTemplateFileImport(file)}
        onModeChange={(value) =>
          setIdentityForm((previous) => ({
            ...previous,
            outreach_generation_mode: value,
          }))
        }
        onSubjectChange={(value) =>
          setIdentityForm((previous) => ({
            ...previous,
            outreach_template_subject: value,
          }))
        }
        onBodyChange={({ html, text }) =>
          setIdentityForm((previous) => ({
            ...previous,
            outreach_template_body_text: text,
            outreach_template_body_html: html,
          }))
        }
      />
      {displayIdentity && (
        <MaterialLibraryModal
          open={materialModalOpen}
          identity={displayIdentity}
          materials={displayIdentity.materials}
          busy={actingOnMaterial || uploadingMaterial}
          uploading={uploadingMaterial}
          selectedMaterialType={newMaterialType}
          materialFilter={materialFilter}
          highlightedMaterialId={highlightedMaterialId}
          onChangeMaterialType={setNewMaterialType}
          onChangeMaterialFilter={setMaterialFilter}
          onUpload={(file) => void handleMaterialUpload(file)}
          onOpen={(material) => void handleOpenMaterial(material)}
          onClose={() => setMaterialModalOpen(false)}
          onSetPrimary={(material) => void handleSetPrimaryMaterial(material)}
          onDelete={(material) => void handleDeleteMaterial(material)}
        />
      )}
      {confirmDialog}
    </main>
  );
};
