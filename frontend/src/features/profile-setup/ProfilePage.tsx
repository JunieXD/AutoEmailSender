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
  AlertTriangle,
  ArchiveRestore,
  ChevronDown,
  CheckCircle2,
  Copy,
  Download,
  Eye,
  EyeOff,
  ExternalLink,
  FolderOpen,
  Loader2,
  Plus,
  Star,
  Send,
  Trash2,
  Upload,
  X,
  XCircle,
} from "lucide-react";
import { useDesktopBackend } from "@/context/DesktopBackendContext";
import { useNotification } from "@/context/NotificationContext";
import { useSelectionContext } from "@/context/SelectionContext";
import { useWorkspaceDraftGuard } from "@/context/useWorkspaceDraftGuard";
import { NativeSelectField } from "@/components/atoms/NativeSelectField";
import { EmailDeliveryFailureDetails } from "@/components/molecules/EmailDeliveryFailureDetails";
import { EmailTemplateEditor } from "@/components/molecules/EmailTemplateEditor";
import { SubjectTemplateInput } from "@/components/molecules/SubjectTemplateInput";
import { OtherSettingsCard } from "@/components/molecules/OtherSettingsCard";
import { AgentSupportCard } from "@/components/molecules/AgentSupportCard";
import { ProjectAcknowledgements } from "@/components/molecules/ProjectAcknowledgements";
import { DiagnosticLogPanel } from "@/components/organisms/DiagnosticLogPanel";
import { CommunicationSharingPanel } from "@/components/organisms/CommunicationSharingPanel";
import { formatApiDateTime } from "@/lib/dateTime";
import { isDesktopApp, openDesktopMaterial } from "@/lib/desktopApi";
import { openExternalHttpUrl } from "@/lib/externalUrls";
import { PROFILE_HELP_LINKS } from "@/lib/helpLinks";
import { textToEmailHtml } from "@/lib/richEmail";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";
import { useDocumentScrollLock } from "@/lib/useDocumentScrollLock";
import {
  createIdentity,
  deleteIdentity,
  getIdentityDeletionImpact,
  importIdentityTemplate,
  setDefaultIdentity,
  testIdentityImap,
  testIdentitySmtp,
  updateIdentity,
  updateIdentityDefaultOutreachTemplate,
} from "@/lib/api/identities";
import {
  archiveOutreachTemplate,
  createOutreachTemplate,
  duplicateOutreachTemplate,
  listOutreachTemplates,
  restoreOutreachTemplate,
  setGlobalDefaultOutreachTemplate,
  updateOutreachTemplate,
} from "@/lib/api/outreachTemplates";
import {
  deleteMaterial,
  downloadMaterial,
  getMaterialDeletionImpact,
  setPrimaryMaterial,
  uploadIdentityMaterial,
} from "@/lib/api/materials";
import {
  createLLMProfile,
  deleteLLMProfile,
  fetchLLMProfileModelsPreview,
  getLLMProfileDeletionImpact,
  setDefaultLLMProfile,
  testLLMProfilePreview,
  updateLLMProfile,
} from "@/lib/api/llmProfiles";
import { ApiError } from "@/lib/api/client";
import { getTestComposeStatus } from "@/lib/api/testComposeApi";
import {
  MATERIAL_TYPE_LABELS,
  type IdentityDTO,
  type IdentityDeletionImpactDTO,
  type IdentityMaterialDTO,
  type IdentityMaterialType,
  type IdentityReferenceCountsDTO,
  type LLMProfileModelsResultDTO,
  type LLMProfileDeletionImpactDTO,
  type LLMProfileReferenceCountsDTO,
  type LLMProfileTestResultDTO,
  type OutreachGenerationMode,
  type OutreachTemplateDTO,
} from "@/types";
import { useConfirmDialog } from "@/lib/useConfirmDialog";
import { buildMaterialDeletionConfirmationDescription } from "./client/materialDeletionImpact";
import {
  PROFILE_SETUP_STAGES,
  TEMPLATE_FILE_ACCEPT,
  DEFAULT_LLM_MAX_TOKENS,
  DEFAULT_LLM_TEMPERATURE,
  applyOutreachTemplateToIdentityForm,
  areIdentityFormsEqual,
  canUseAsPrimaryMaterial,
  clearOutreachTemplateFromIdentityForm,
  createEmptyIdentityForm,
  createEmptyLLMForm,
  createEmptyOutreachTemplateForm,
  getIdentityProfileName,
  hasVisibleTemplateBody,
  inferImapHost,
  isExistingEditorId,
  shouldSyncImapHost,
  toIdentityForm,
  toIdentityPayload,
  toLLMForm,
  toLLMPayload,
  toOutreachTemplateForm,
  toOutreachTemplatePayload,
  type EditorId,
  type IdentityFormState,
  type LLMFormState,
  type MaterialFilterValue,
  type OutreachTemplateFormState,
  type ProfileSetupItem,
  type ProfileSetupSectionId,
} from "./model/profileForms";

type ActionResultState = "idle" | "success" | "error";
type IdentityConnectionTestSummary = {
  kind: "smtp" | "imap";
  status: "success" | "error";
  message: string;
  possibleCause?: string | null;
};

type TestComposeSetupStatus = "unchecked" | "loading" | "completed" | "pending";

const inputClassName =
  "w-full rounded-xl border border-stone-200 px-3 py-2 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20";

const labelClassName =
  "mb-2 inline-flex items-center gap-1 text-sm font-medium text-stone-800";

const renderFieldLabel = (label: string, required = false) => (
  <span className={labelClassName}>
    {required && (
      <span aria-hidden="true" className="text-base leading-none text-red-500">
        *
      </span>
    )}
    <span>{label}</span>
  </span>
);

const ContextualHelpLink = ({
  href,
  children,
  tone = "quiet",
  compact = false,
}: {
  href: string;
  children: ReactNode;
  tone?: "quiet" | "surface";
  compact?: boolean;
}) => (
  <a
    href={href}
    target="_blank"
    rel="noopener noreferrer"
    onClick={(event) => {
      event.preventDefault();
      openExternalHttpUrl(href);
    }}
    className={clsx(
      "inline-flex shrink-0 items-center gap-1.5 text-xs font-medium text-primary underline decoration-primary/30 underline-offset-4 transition hover:bg-primary/5 hover:decoration-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25",
      compact
        ? "min-h-0 rounded px-1 py-0 leading-5"
        : "min-h-9 rounded-xl px-2.5",
      tone === "surface" &&
        "border border-stone-200 bg-white/90 no-underline shadow-sm hover:border-primary/25 hover:bg-white",
    )}
  >
    <span>{children}</span>
    <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
  </a>
);

const FormFieldHeader = ({
  id,
  label,
  required = false,
  help,
}: {
  id: string;
  label: string;
  required?: boolean;
  help?: ReactNode;
}) => (
  <div className="mb-2 flex min-h-[22px] flex-wrap items-center justify-start gap-x-1 gap-y-1">
    <label
      htmlFor={id}
      className="inline-flex items-center gap-1 text-sm font-medium text-stone-800"
    >
      {required && (
        <span aria-hidden="true" className="text-base leading-none text-red-500">
          *
        </span>
      )}
      <span>{label}</span>
    </label>
    {help}
  </div>
);

function ProfileSetupSection({
  sectionId,
  title,
  description,
  badge,
  helpAction,
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
  helpAction?: ReactNode;
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
      <div
        onClick={(event) => {
          if (
            event.target instanceof Element &&
            event.target.closest("a, button")
          ) {
            return;
          }
          onToggle();
        }}
        className="cursor-pointer px-6 py-5 transition hover:bg-stone-50 active:bg-stone-50"
      >
        <button
          type="button"
          aria-expanded={open}
          aria-controls={`${sectionId}-setup-content`}
          onClick={onToggle}
          className="collapsible-card-toggle flex w-full min-w-0 items-center justify-between gap-4 text-left active:bg-stone-50"
        >
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-stone-900">{title}</h2>
            {badge}
          </div>
          <ChevronDown
            className={clsx(
              "h-5 w-5 shrink-0 text-stone-500 transition-transform",
              open ? "rotate-180" : "rotate-0",
            )}
          />
        </button>
        {description || helpAction ? (
          <div className="mt-2 flex flex-wrap items-center gap-x-1 gap-y-1">
            {description ? (
              <p className="text-xs leading-5 text-stone-600 sm:text-sm sm:leading-6">
                {description}
              </p>
            ) : null}
            {helpAction}
          </div>
        ) : null}
      </div>

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
          {result.consumes_tokens ? "会耗 Token" : "不耗 Token"}
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
      {!result.ok ? (
        <div className="mt-3 border-t border-red-200/80 pt-3">
          <ContextualHelpLink href={PROFILE_HELP_LINKS.llmConfiguration} tone="surface">
            查看模型配置排查步骤
          </ContextualHelpLink>
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
          {result.consumes_tokens ? "会耗 Token" : "不耗 Token"}
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
          输入 Token：{result.prompt_tokens ?? "未返回"}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          输出 Token：{result.completion_tokens ?? "未返回"}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          总 Token：{result.total_tokens ?? "未返回"}
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
      {!result.ok ? (
        <div className="mt-3 border-t border-red-200/80 pt-3">
          <ContextualHelpLink href={PROFILE_HELP_LINKS.llmConfiguration} tone="surface">
            查看模型配置排查步骤
          </ContextualHelpLink>
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
            <div className="text-sm font-medium text-stone-900">全局材料库</div>
            <div className="mt-1 text-xs text-stone-500">
              所有发件身份共享，共 {identity.materials.length} 份
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
        {lastResult.kind === "smtp" && lastResult.status === "error" ? (
          <EmailDeliveryFailureDetails
            possibleCause={lastResult.possibleCause}
            rawError={lastResult.message}
          />
        ) : (
          <div className="mt-1 whitespace-pre-wrap break-words text-stone-600">
            {lastResult.message}
          </div>
        )}
        {lastResult.status === "error" ? (
          <div className="mt-3 border-t border-stone-200 pt-3">
            <ContextualHelpLink href={PROFILE_HELP_LINKS.mailAuthorization} tone="surface">
              按邮箱配置教程逐项检查
            </ContextualHelpLink>
          </div>
        ) : null}
      </div>
    ) : null}
  </div>
);

const OutreachTemplateSummaryCard = ({
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
            <div className="text-sm font-medium text-stone-900">
              发信模板库
            </div>
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
              {effectiveMode === "template"
                ? "直接套用模板"
                : "AI 辅助写信"}
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

const OutreachTemplateModal = ({
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
  } =
    useDismissableLayerClick(onClose);

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
              <div className="text-sm font-medium text-stone-900">
                正在编辑
              </div>
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
                  {form.outreach_template_subject.trim() ? "主题已填写" : "主题待补充"}
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
                <div className="text-sm font-semibold text-stone-900">模板列表</div>
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
                            <span className={clsx(
                              "rounded-full px-2 py-1",
                              template.is_ready
                                ? "bg-emerald-50 text-emerald-700"
                                : "bg-amber-50 text-amber-700",
                            )}>
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
                          <ArchiveRestore aria-hidden="true" className="h-4 w-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>

            <div className="grid min-w-0 gap-6">
              <label className="block">
                {renderFieldLabel("模板名称", true)}
                <input
                  value={form.name}
                  onChange={(event) => onNameChange(event.target.value)}
                  className={inputClassName}
                  placeholder="例如：博士申请通用模板"
                />
              </label>

            <div>
              <div className="text-sm font-medium text-stone-900">推荐写信方式</div>
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
  onDownload,
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
  onDownload: (material: IdentityMaterialDTO) => void;
  onClose: () => void;
  onSetPrimary: (material: IdentityMaterialDTO) => void;
  onDelete: (material: IdentityMaterialDTO) => void;
}) => {
  const {
    onBackdropClick,
    onBackdropMouseDown,
    onContentClick,
    onContentMouseDown,
  } =
    useDismissableLayerClick(onClose);

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
                Global Material Library
              </div>
              <h3 className="mt-2 text-2xl font-semibold text-stone-900">
                全局材料管理
              </h3>
              <p className="mt-1 text-sm text-stone-500">
                {materials.length} 份共享材料
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
                  上传一次，可供所有发件身份复用
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
                          {(material.default_for_identity_ids?.length ?? 0) > 0 ? (
                            <span>
                              {material.default_for_identity_ids?.length} 个身份正在使用默认
                            </span>
                          ) : null}
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
                          onClick={() => onDownload(material)}
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

const LLM_REFERENCE_LABELS: Array<
  [keyof LLMProfileReferenceCountsDTO, string]
> = [
  ["batch_tasks", "批量活动"],
  ["email_tasks", "邮件任务"],
  ["email_logs", "邮件与通信记录"],
  ["match_analysis_jobs", "匹配分析任务"],
  ["match_analysis_job_items", "匹配分析明细"],
  ["match_analysis_runs", "匹配运行记录"],
  ["match_results", "导师匹配结果"],
  ["test_compose_sessions", "测试写信会话"],
  ["test_compose_messages", "测试邮件记录"],
  ["crawl_jobs", "抓取与信息补全任务"],
  ["crawl_runs", "抓取运行记录"],
  ["crawl_pages", "抓取页面"],
  ["crawl_candidates", "抓取候选数据"],
  ["crawl_token_usages", "抓取 Token 记录"],
  ["agent_change_plans", "Agent 待办与历史计划"],
  ["operation_logs", "操作日志"],
];

const IDENTITY_REFERENCE_LABELS: Array<
  [keyof IdentityReferenceCountsDTO, string]
> = [
  ["email_tasks", "邮件任务"],
  ["email_logs", "邮件与通信记录"],
  ["batch_tasks", "批量任务"],
  ["test_compose_sessions", "测试写信会话"],
  ["test_compose_messages", "测试邮件记录"],
  ["match_analysis_jobs", "匹配分析任务"],
  ["match_analysis_runs", "匹配运行记录"],
  ["match_results", "导师匹配结果"],
  ["delivery_attempts", "邮件投递尝试"],
  ["email_observations", "邮件投递观测记录"],
  ["agent_change_plans", "Agent 操作计划"],
];

const isIdentityDeletionImpact = (
  value: unknown,
): value is IdentityDeletionImpactDTO =>
  typeof value === "object" &&
  value !== null &&
  "identity_id" in value &&
  "revision" in value &&
  typeof value.revision === "string" &&
  "blockers" in value &&
  Array.isArray(value.blockers);

const IdentityDeletionDialog = ({
  impact,
  busy,
  onClose,
  onConfirm,
}: {
  impact: IdentityDeletionImpactDTO;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) => {
  useDocumentScrollLock(true);
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, onClose]);

  const references = IDENTITY_REFERENCE_LABELS.filter(
    ([key]) => impact.references[key] > 0,
  );

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-stone-950/45 p-4 backdrop-blur-sm">
      <div
        aria-describedby="identity-deletion-description"
        aria-labelledby="identity-deletion-title"
        aria-modal="true"
        className="flex max-h-[min(88vh,46rem)] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-stone-200 bg-white shadow-2xl"
        role="dialog"
      >
        <div className="flex items-start justify-between gap-4 border-b border-stone-200 px-5 py-4">
          <div className="min-w-0">
            <h2 id="identity-deletion-title" className="text-base font-semibold text-stone-950">
              {impact.can_delete ? "删除身份配置" : "暂时无法删除身份配置"}
            </h2>
            <p id="identity-deletion-description" className="mt-1 text-sm leading-6 text-stone-600">
              “{impact.identity_name}” · {impact.email_address}
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭"
            className="ui-icon-btn shrink-0"
            disabled={busy}
            onClick={onClose}
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {impact.blockers.length > 0 && (
            <section className="border-l-4 border-rose-500 bg-rose-50 px-4 py-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-rose-900">
                <AlertTriangle aria-hidden="true" className="h-4 w-4" />
                以下操作结束前无法删除
              </div>
              <p className="mt-2 text-sm leading-6 text-rose-800">
                这些操作正在执行，直接删除可能导致邮件发送或任务结果异常。请按提示找到并处理对应任务。
              </p>
              <ul className="mt-2 space-y-2 text-sm text-rose-800">
                {impact.blockers.map((blocker) => (
                  <li key={blocker.kind}>
                    {blocker.label}：{blocker.count} 项
                    {blocker.entity_ids.length > 0
                      ? `（ID ${blocker.entity_ids.join("、")}${blocker.count > blocker.entity_ids.length ? " 等" : ""}）`
                      : ""}
                    <span className="mt-0.5 block text-xs text-rose-700">
                      定位：{blocker.surface}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h3 className="text-sm font-semibold text-stone-900">保留的历史数据</h3>
            {references.length > 0 ? (
              <dl className="mt-3 grid grid-cols-1 gap-x-5 gap-y-2 sm:grid-cols-2">
                {references.map(([key, label]) => (
                  <div key={key} className="flex items-center justify-between gap-4 border-b border-stone-100 py-1.5 text-sm">
                    <dt className="text-stone-600">{label}</dt>
                    <dd className="font-medium tabular-nums text-stone-900">
                      {impact.references[key]}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="mt-2 text-sm text-stone-500">没有关联的业务历史。</p>
            )}
          </section>

          {(impact.automatic_actions.cancel_email_task_ids.length > 0 ||
            impact.automatic_actions.stop_batch_task_ids.length > 0 ||
            impact.automatic_actions.cancel_match_analysis_job_ids.length > 0 ||
            impact.automatic_actions.invalidate_agent_change_plan_ids.length > 0) && (
            <section>
              <h3 className="text-sm font-semibold text-stone-900">确认后自动处理</h3>
              <ul className="mt-2 space-y-2 text-sm leading-6 text-stone-600">
                {impact.automatic_actions.cancel_email_task_ids.length > 0 && (
                  <li>取消未开始发送的邮件任务：ID {impact.automatic_actions.cancel_email_task_ids.join("、")}</li>
                )}
                {impact.automatic_actions.stop_batch_task_ids.length > 0 && (
                  <li>停止仍可继续的批量任务：ID {impact.automatic_actions.stop_batch_task_ids.join("、")}</li>
                )}
                {impact.automatic_actions.cancel_match_analysis_job_ids.length > 0 && (
                  <li>取消匹配分析任务：ID {impact.automatic_actions.cancel_match_analysis_job_ids.join("、")}</li>
                )}
                {impact.automatic_actions.invalidate_agent_change_plan_ids.length > 0 && (
                  <li>作废关联的待确认 Agent 计划：ID {impact.automatic_actions.invalidate_agent_change_plan_ids.join("、")}</li>
                )}
              </ul>
            </section>
          )}

          <section>
            <h3 className="text-sm font-semibold text-stone-900">处理方式</h3>
            <ul className="mt-2 space-y-2 text-sm leading-6 text-stone-600">
              {impact.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </section>
        </div>

        <div className="flex flex-wrap justify-end gap-2 border-t border-stone-200 px-5 py-4">
          <button type="button" className="ui-btn-secondary" disabled={busy} onClick={onClose}>
            {impact.can_delete ? "取消" : "知道了"}
          </button>
          {impact.can_delete && (
            <button type="button" className="ui-btn-danger inline-flex items-center gap-2" disabled={busy} onClick={onConfirm}>
              {busy ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <Trash2 aria-hidden="true" className="h-4 w-4" />}
              {busy ? "正在删除" : "确认删除"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

const isDeletionImpact = (value: unknown): value is LLMProfileDeletionImpactDTO =>
  typeof value === "object" &&
  value !== null &&
  "revision" in value &&
  typeof value.revision === "string" &&
  "blockers" in value &&
  Array.isArray(value.blockers);

const LLMDeletionDialog = ({
  impact,
  replacementProfiles,
  replacementProfileId,
  busy,
  onReplacementChange,
  onClose,
  onConfirm,
}: {
  impact: LLMProfileDeletionImpactDTO;
  replacementProfiles: Array<{
    id: number;
    name: string;
    model_name: string;
  }>;
  replacementProfileId: number | null;
  busy: boolean;
  onReplacementChange: (profileId: number | null) => void;
  onClose: () => void;
  onConfirm: () => void;
}) => {
  useDocumentScrollLock(true);
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, onClose]);

  const references = LLM_REFERENCE_LABELS.filter(
    ([key]) => impact.references[key] > 0,
  );
  const automaticActions = [
    impact.automatic_actions.cancel_email_task_ids.length > 0
      ? `等待生成草稿的邮件任务：ID ${impact.automatic_actions.cancel_email_task_ids.join("、")}`
      : null,
    impact.automatic_actions.cancel_match_analysis_job_ids.length > 0
      ? `匹配分析任务：ID ${impact.automatic_actions.cancel_match_analysis_job_ids.join("、")}`
      : null,
    impact.automatic_actions.cancel_crawl_job_ids.length > 0
      ? `智能抓取或信息补全任务：ID ${impact.automatic_actions.cancel_crawl_job_ids.join("、")}`
      : null,
  ].filter((item): item is string => item !== null);

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-stone-950/45 p-4 backdrop-blur-sm">
      <div
        aria-describedby="llm-deletion-description"
        aria-labelledby="llm-deletion-title"
        aria-modal="true"
        className="flex max-h-[min(88vh,48rem)] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-stone-200 bg-white shadow-2xl"
        role="dialog"
      >
        <div className="flex items-start justify-between gap-4 border-b border-stone-200 px-5 py-4">
          <div className="min-w-0">
            <h2 id="llm-deletion-title" className="text-base font-semibold text-stone-950">
              {impact.can_delete ? "删除模型配置" : "暂时无法删除模型配置"}
            </h2>
            <p id="llm-deletion-description" className="mt-1 text-sm leading-6 text-stone-600">
              “{impact.profile_name}” · {impact.model_name}
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭"
            className="ui-icon-btn shrink-0"
            disabled={busy}
            onClick={onClose}
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {impact.blockers.length > 0 && (
            <section className="border-l-4 border-rose-500 bg-rose-50 px-4 py-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-rose-900">
                <AlertTriangle aria-hidden="true" className="h-4 w-4" />
                以下操作结束前无法删除
              </div>
              <ul className="mt-2 space-y-2 text-sm text-rose-800">
                {impact.blockers.map((blocker) => (
                  <li key={blocker.kind}>
                    {blocker.label}：{blocker.count} 项
                    {blocker.entity_ids.length > 0
                      ? `（ID ${blocker.entity_ids.join("、")}${blocker.count > blocker.entity_ids.length ? " 等" : ""}）`
                      : ""}
                    <span className="mt-0.5 block text-xs text-rose-700">
                      定位：{blocker.surface}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {automaticActions.length > 0 && (
            <section className="border-l-4 border-amber-500 bg-amber-50 px-4 py-3">
              <h3 className="text-sm font-semibold text-amber-950">
                确认删除后会自动取消
              </h3>
              <ul className="mt-2 space-y-1 text-sm text-amber-900">
                {automaticActions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h3 className="text-sm font-semibold text-stone-900">保留的历史数据</h3>
            {references.length > 0 ? (
              <dl className="mt-3 grid grid-cols-1 gap-x-5 gap-y-2 sm:grid-cols-2">
                {references.map(([key, label]) => (
                  <div key={key} className="flex items-center justify-between gap-4 border-b border-stone-100 py-1.5 text-sm">
                    <dt className="text-stone-600">{label}</dt>
                    <dd className="font-medium tabular-nums text-stone-900">
                      {impact.references[key]}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="mt-2 text-sm text-stone-500">没有关联的历史业务数据。</p>
            )}
          </section>

          {impact.is_default && impact.can_delete && (
            <section>
              <label htmlFor="llm-default-replacement" className="text-sm font-semibold text-stone-900">
                删除后的默认模型
              </label>
              <select
                id="llm-default-replacement"
                className="ui-input mt-2 w-full"
                disabled={busy}
                value={replacementProfileId ?? ""}
                onChange={(event) =>
                  onReplacementChange(event.target.value ? Number(event.target.value) : null)
                }
              >
                <option value="">暂不设置默认模型</option>
                {replacementProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}（{profile.model_name}）
                  </option>
                ))}
              </select>
            </section>
          )}

          <section className="text-sm leading-6 text-stone-600">
            <p>
              删除后，本地保存的 API Key 和服务地址会被清除；如配置过模型级提示词，也会一并清除。
            </p>
            <p className="mt-2">
              材料与模板中的发信模板独立保存，不受影响。历史邮件、批量活动、匹配分析、智能抓取和 Token 用量记录仍会保留。
            </p>
            <p className="mt-2">
              暂停或失败的 AI 任务再次运行前，需要重新选择模型。系统不会自动使用其他模型。
            </p>
          </section>
        </div>

        <div className="flex flex-wrap justify-end gap-2 border-t border-stone-200 px-5 py-4">
          <button type="button" className="ui-btn-secondary" disabled={busy} onClick={onClose}>
            {impact.can_delete ? "取消" : "知道了"}
          </button>
          {impact.can_delete && (
            <button type="button" className="ui-btn-danger inline-flex items-center gap-2" disabled={busy} onClick={onConfirm}>
              {busy ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <Trash2 aria-hidden="true" className="h-4 w-4" />}
              {busy ? "正在删除" : "确认删除"}
            </button>
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
  const {
    registerWorkspaceDraftGuard,
    requestWorkspaceDraftGuard,
  } = useWorkspaceDraftGuard();
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
  const identityFormRef = useRef(identityForm);
  const identityFormBaselineRef = useRef(identityForm);
  const identityEditorIdRef = useRef<EditorId>(identityEditorId);
  const identityEditorSelectionIdRef = useRef<number | null>(selectedIdentityId);
  const selectedIdentityIdRef = useRef<number | null>(selectedIdentityId);
  const saveIdentityRef = useRef<
    (options?: { silent?: boolean }) => Promise<IdentityDTO | null>
  >(async () => null);
  const [smtpPasswordVisible, setSmtpPasswordVisible] = useState(false);
  const [outreachTemplates, setOutreachTemplates] = useState<
    OutreachTemplateDTO[]
  >([]);
  const [loadingOutreachTemplates, setLoadingOutreachTemplates] =
    useState(true);
  const [templateEditorId, setTemplateEditorId] = useState<EditorId>(null);
  const [outreachTemplateForm, setOutreachTemplateForm] =
    useState<OutreachTemplateFormState>(createEmptyOutreachTemplateForm());
  const [llmForm, setLlmForm] = useState<LLMFormState>(createEmptyLLMForm());
  const [submittingIdentity, setSubmittingIdentity] = useState(false);
  const [loadingIdentityDeletionImpactId, setLoadingIdentityDeletionImpactId] =
    useState<number | null>(null);
  const [identityDeletionImpact, setIdentityDeletionImpact] =
    useState<IdentityDeletionImpactDTO | null>(null);
  const [deletingIdentity, setDeletingIdentity] = useState(false);
  const [savingOutreachTemplate, setSavingOutreachTemplate] = useState(false);
  const [actingOnOutreachTemplate, setActingOnOutreachTemplate] =
    useState(false);
  const [submittingLLM, setSubmittingLLM] = useState(false);
  const [loadingLLMDeletionImpactId, setLoadingLLMDeletionImpactId] = useState<
    number | null
  >(null);
  const [llmDeletionImpact, setLlmDeletionImpact] =
    useState<LLMProfileDeletionImpactDTO | null>(null);
  const [replacementDefaultLLMId, setReplacementDefaultLLMId] = useState<
    number | null
  >(null);
  const [retiringLLM, setRetiringLLM] = useState(false);
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
  const templateEditorIdRef = useRef<EditorId>(null);
  const setupSectionRefs = useRef<
    Record<ProfileSetupSectionId, HTMLElement | null>
  >({
    identity: null,
    materials: null,
    model: null,
    test: null,
  });
  const { confirm, choose, dialog: confirmDialog } = useConfirmDialog();

  templateEditorIdRef.current = templateEditorId;
  identityFormRef.current = identityForm;
  identityEditorIdRef.current = identityEditorId;
  selectedIdentityIdRef.current = selectedIdentityId;

  const focusInput = (element: HTMLInputElement | null) => {
    if (!element) {
      return;
    }
    element.focus();
    element.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const getActionErrorMessage = (error: unknown, fallbackMessage: string) =>
    error instanceof Error ? error.message : fallbackMessage;
  const refreshOutreachTemplates = useCallback(async () => {
    setLoadingOutreachTemplates(true);
    try {
      const templates = await listOutreachTemplates(true);
      setOutreachTemplates(templates);
      return templates.filter((template) => !template.archived_at);
    } catch (templateError) {
      notifyError(
        "模板加载失败",
        templateError instanceof Error
          ? templateError.message
          : "加载发信模板失败",
      );
      return [];
    } finally {
      setLoadingOutreachTemplates(false);
    }
  }, [notifyError]);
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
      const nextForm =
        nextEditor === "new"
          ? createEmptyIdentityForm()
          : toIdentityForm(nextEditor);
      const nextEditorId = nextEditor === "new" ? "new" : nextEditor.id;
      identityFormRef.current = nextForm;
      identityFormBaselineRef.current = nextForm;
      identityEditorIdRef.current = nextEditorId;
      identityEditorSelectionIdRef.current =
        nextEditor === "new"
          ? selectedIdentityIdRef.current
          : nextEditor.id;
      setSmtpPasswordVisible(false);
      setIdentityEditorId(nextEditorId);
      setIdentityForm(nextForm);
      setTemplateModalOpen(false);
      setTestingIdentityConnection(null);
      setLastIdentityConnectionResult(null);
      setHighlightedMaterialId(null);
      setOptimisticMaterial(null);
    },
    [],
  );

  const updateIdentityFormState = useCallback(
    (
      updater: (previous: IdentityFormState) => IdentityFormState,
      persisted: boolean,
    ) => {
      if (persisted) {
        identityFormBaselineRef.current = updater(
          identityFormBaselineRef.current,
        );
      }
      setIdentityForm((previous) => {
        const next = updater(previous);
        identityFormRef.current = next;
        return next;
      });
    },
    [],
  );

  useEffect(() => {
    void refreshOutreachTemplates();
  }, [refreshOutreachTemplates]);

  useEffect(() => {
    if (loading) {
      return;
    }
    if (
      identityEditorId === "new" &&
      selectedIdentityId === identityEditorSelectionIdRef.current
    ) {
      return;
    }

    const selectedEditor =
      identities.find((item) => item.id === selectedIdentityId) ?? null;
    if (selectedEditor) {
      if (identityEditorId !== selectedEditor.id) {
        applyIdentityEditorState(selectedEditor);
      }
      return;
    }

    if (
      isExistingEditorId(identityEditorId) &&
      identities.some((item) => item.id === identityEditorId)
    ) {
      return;
    }

    const fallback = identities[0] ?? null;

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

  const profileModalOpen = materialModalOpen || templateModalOpen;
  useDocumentScrollLock(profileModalOpen);

  useEffect(() => {
    if (!profileModalOpen) {
      return;
    }
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
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [materialModalOpen, profileModalOpen, templateModalOpen]);

  const editingIdentity = isExistingEditorId(identityEditorId)
    ? (identities.find((item) => item.id === identityEditorId) ?? null)
    : null;
  const editingLLM = isExistingEditorId(llmEditorId)
    ? (llmProfiles.find((item) => item.id === llmEditorId) ?? null)
    : null;
  const activeOutreachTemplates = useMemo(
    () => outreachTemplates.filter((template) => !template.archived_at),
    [outreachTemplates],
  );
  const archivedOutreachTemplates = useMemo(
    () => outreachTemplates.filter((template) => Boolean(template.archived_at)),
    [outreachTemplates],
  );
  const identityDefaultOutreachTemplate =
    activeOutreachTemplates.find(
      (template) => template.id === identityForm.default_outreach_template_id,
    ) ?? null;
  const globalDefaultOutreachTemplate =
    activeOutreachTemplates.find((template) => template.is_default) ?? null;

  useEffect(() => {
    if (!templateModalOpen || loadingOutreachTemplates) {
      return;
    }
    if (templateEditorId === "new") {
      return;
    }
    if (
      isExistingEditorId(templateEditorId) &&
      activeOutreachTemplates.some(
        (template) => template.id === templateEditorId,
      )
    ) {
      return;
    }

    const fallback =
      activeOutreachTemplates.find(
        (template) =>
          template.id === identityForm.default_outreach_template_id,
      ) ??
      activeOutreachTemplates.find((template) => template.is_default) ??
      activeOutreachTemplates[0] ??
      null;
    if (fallback) {
      setTemplateEditorId(fallback.id);
      setOutreachTemplateForm(toOutreachTemplateForm(fallback));
      return;
    }
    setTemplateEditorId("new");
    setOutreachTemplateForm(createEmptyOutreachTemplateForm());
  }, [
    activeOutreachTemplates,
    identityForm.default_outreach_template_id,
    loadingOutreachTemplates,
    templateEditorId,
    templateModalOpen,
  ]);

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
  const setupOutreachTemplate =
    activeOutreachTemplates.find(
      (template) =>
        template.id === setupIdentity?.default_outreach_template_id,
    ) ?? globalDefaultOutreachTemplate;
  const setupHasTemplate = setupOutreachTemplate
    ? setupOutreachTemplate.is_ready
    : Boolean(
        setupIdentity?.outreach_template_subject?.trim() &&
          setupIdentity.outreach_template_body_text?.trim(),
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
            : "待保存模型",
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
  const hasResolvedTestComposeSetup =
    selectedIdentityId === null ||
    testComposeSetupStatus === "completed" ||
    testComposeSetupStatus === "pending";
  const shouldShowProfileSetupRecommendations =
    !loadingOutreachTemplates &&
    hasResolvedTestComposeSetup &&
    setupItems.some((item) => !item.completed);

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

  const beginIdentityCreation = async () => {
    if (identityEditorId === "new") {
      return;
    }
    if (
      !(await requestWorkspaceDraftGuard({
        nextIdentityEditorId: "new",
      }))
    ) {
      return;
    }
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

  const openIdentityEditor = async (identityId: number) => {
    const identity = identities.find((item) => item.id === identityId);
    if (!identity) {
      return;
    }
    if (identity.id === identityEditorId) {
      return;
    }
    if (
      !(await requestWorkspaceDraftGuard({
        nextIdentityEditorId: identity.id,
        nextIdentityId: identity.id,
      }))
    ) {
      return;
    }
    if (identity.id === selectedIdentityId) {
      applyIdentityEditorState(identity);
    } else {
      setSelectedIdentityId(identity.id);
    }
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

  const beginOutreachTemplateCreation = () => {
    setTemplateEditorId("new");
    setOutreachTemplateForm(createEmptyOutreachTemplateForm());
  };

  const openOutreachTemplateEditor = (templateId: number) => {
    const template = activeOutreachTemplates.find(
      (item) => item.id === templateId,
    );
    if (!template) {
      return;
    }
    setTemplateEditorId(template.id);
    setOutreachTemplateForm(toOutreachTemplateForm(template));
  };

  const openOutreachTemplateLibrary = () => {
    if (loadingOutreachTemplates) {
      setTemplateEditorId(null);
      setOutreachTemplateForm(createEmptyOutreachTemplateForm());
      setTemplateModalOpen(true);
      return;
    }
    const fallback =
      activeOutreachTemplates.find(
        (template) =>
          template.id === identityForm.default_outreach_template_id,
      ) ??
      activeOutreachTemplates.find((template) => template.is_default) ??
      activeOutreachTemplates[0] ??
      null;
    if (fallback) {
      setTemplateEditorId(fallback.id);
      setOutreachTemplateForm(toOutreachTemplateForm(fallback));
    } else {
      beginOutreachTemplateCreation();
    }
    setTemplateModalOpen(true);
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
      const savedIdentity = await saveIdentity({ silent: true });
      if (!savedIdentity) {
        return;
      }
      const result =
        kind === "smtp"
          ? await testIdentitySmtp(savedIdentity.id)
          : await testIdentityImap(savedIdentity.id);
      if (!result.ok) {
        setLastIdentityConnectionResult({
          kind,
          status: "error",
          message: result.message,
          possibleCause: result.possible_cause,
        });
        notifyError(`${kind.toUpperCase()} 连接测试失败`, result.message);
        return;
      }
      setLastIdentityConnectionResult({
        kind,
        status: "success",
        message: result.message,
        possibleCause: null,
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
        possibleCause: null,
      });
      notifyError(`${kind.toUpperCase()} 连接测试失败`, message);
    } finally {
      setTestingIdentityConnection(null);
    }
  };

  const saveOutreachTemplate = async (): Promise<OutreachTemplateDTO | null> => {
    if (!desktopBackendReady) {
      notifyError(
        "系统正在准备本地数据",
        "请等待系统准备完成后再保存模板，已填写内容不会丢失。",
      );
      return null;
    }
    if (!outreachTemplateForm.name.trim()) {
      notifyFormErrors("请检查表单", ["请填写模板名称"]);
      return null;
    }

    setSavingOutreachTemplate(true);
    try {
      const payload = toOutreachTemplatePayload(outreachTemplateForm);
      const isCreating = templateEditorId === "new";
      const saved = isExistingEditorId(templateEditorId)
        ? await updateOutreachTemplate(templateEditorId, payload)
        : await createOutreachTemplate(payload);
      setTemplateEditorId(saved.id);
      setOutreachTemplateForm(toOutreachTemplateForm(saved));
      if (identityForm.default_outreach_template_id === saved.id) {
        updateIdentityFormState(
          (previous) => applyOutreachTemplateToIdentityForm(previous, saved),
          Boolean(editingIdentity),
        );
      }
      await Promise.all([refreshOutreachTemplates(), refreshSelections()]);
      notifySuccess(
        isCreating ? "模板创建成功" : "模板保存成功",
        "缺失主题或正文时会标记为“待完善”。",
      );
      return saved;
    } catch (saveError) {
      notifyError(
        "模板保存失败",
        getActionErrorMessage(saveError, "保存发信模板失败"),
      );
      return null;
    } finally {
      setSavingOutreachTemplate(false);
    }
  };

  const handleSetIdentityDefaultTemplate = async (
    template: OutreachTemplateDTO,
  ) => {
    setActingOnOutreachTemplate(true);
    try {
      if (editingIdentity) {
        await updateIdentityDefaultOutreachTemplate(
          editingIdentity.id,
          template.id,
        );
        await refreshSelections();
      }
      updateIdentityFormState(
        (previous) => applyOutreachTemplateToIdentityForm(previous, template),
        Boolean(editingIdentity),
      );
      notifySuccess(
        "身份默认模板已更新",
        editingIdentity
          ? `“${getIdentityProfileName(editingIdentity)}”之后创建的任务将默认选择“${template.name}”。`
          : `保存新身份时会将“${template.name}”设为默认模板。`,
      );
    } catch (templateError) {
      notifyError(
        "设置身份默认模板失败",
        getActionErrorMessage(templateError, "设置身份默认模板失败"),
      );
    } finally {
      setActingOnOutreachTemplate(false);
    }
  };

  const handleSetGlobalDefaultTemplate = async (templateId: number) => {
    setActingOnOutreachTemplate(true);
    try {
      const saved = await setGlobalDefaultOutreachTemplate(templateId);
      setOutreachTemplateForm((previous) => ({
        ...previous,
        is_default: templateEditorId === saved.id,
      }));
      await refreshOutreachTemplates();
      notifySuccess(
        "全局默认模板已更新",
        `未设置身份默认模板时，将优先选择“${saved.name}”。`,
      );
    } catch (templateError) {
      notifyError(
        "设置全局默认模板失败",
        getActionErrorMessage(templateError, "设置全局默认模板失败"),
      );
    } finally {
      setActingOnOutreachTemplate(false);
    }
  };

  const handleClearIdentityDefaultTemplate = async () => {
    setActingOnOutreachTemplate(true);
    try {
      if (editingIdentity) {
        await updateIdentityDefaultOutreachTemplate(editingIdentity.id, null);
        await refreshSelections();
      }
      updateIdentityFormState(
        clearOutreachTemplateFromIdentityForm,
        Boolean(editingIdentity),
      );
      notifySuccess(
        "身份默认模板已取消",
        editingIdentity
          ? `“${getIdentityProfileName(editingIdentity)}”之后创建的任务将使用全局默认模板（如有）。`
          : "保存新身份后，将使用全局默认模板（如有）。",
      );
    } catch (templateError) {
      notifyError(
        "取消身份默认模板失败",
        getActionErrorMessage(templateError, "取消身份默认模板失败"),
      );
    } finally {
      setActingOnOutreachTemplate(false);
    }
  };

  const handleDuplicateOutreachTemplate = async (templateId: number) => {
    setActingOnOutreachTemplate(true);
    try {
      const duplicate = await duplicateOutreachTemplate(templateId);
      await refreshOutreachTemplates();
      setTemplateEditorId(duplicate.id);
      setOutreachTemplateForm(toOutreachTemplateForm(duplicate));
      notifySuccess("模板复制成功", `已创建“${duplicate.name}”。`);
    } catch (templateError) {
      notifyError(
        "复制模板失败",
        getActionErrorMessage(templateError, "复制发信模板失败"),
      );
    } finally {
      setActingOnOutreachTemplate(false);
    }
  };

  const handleDeleteOutreachTemplate = async (
    template: OutreachTemplateDTO,
  ) => {
    const confirmed = await confirm({
      title: `归档模板“${template.name}”？`,
      description:
        "归档后会取消默认关联并停止用于新任务；已创建任务和模板内容会保留，可稍后恢复。",
      confirmLabel: "确认归档",
      cancelLabel: "取消",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    setActingOnOutreachTemplate(true);
    try {
      const wasIdentityDefault =
        identityForm.default_outreach_template_id === template.id;
      await archiveOutreachTemplate(template.id);
      if (wasIdentityDefault) {
        updateIdentityFormState(
          clearOutreachTemplateFromIdentityForm,
          Boolean(editingIdentity),
        );
      }
      const [remainingTemplates] = await Promise.all([
        refreshOutreachTemplates(),
        refreshSelections(),
      ]);
      const fallback =
        (!wasIdentityDefault
          ? remainingTemplates.find(
              (item) =>
                item.id === identityForm.default_outreach_template_id,
            )
          : null) ??
        remainingTemplates.find((item) => item.is_default) ??
        remainingTemplates[0] ??
        null;
      if (fallback) {
        setTemplateEditorId(fallback.id);
        setOutreachTemplateForm(toOutreachTemplateForm(fallback));
      } else {
        beginOutreachTemplateCreation();
      }
      notifySuccess(
        "模板已归档",
        "已创建任务不受影响，可在模板库中恢复。",
      );
    } catch (templateError) {
      notifyError(
        "归档模板失败",
        getActionErrorMessage(templateError, "归档发信模板失败"),
      );
    } finally {
      setActingOnOutreachTemplate(false);
    }
  };

  const handleRestoreOutreachTemplate = async (
    template: OutreachTemplateDTO,
  ) => {
    setActingOnOutreachTemplate(true);
    try {
      const restored = await restoreOutreachTemplate(template.id);
      await refreshOutreachTemplates();
      setTemplateEditorId(restored.id);
      setOutreachTemplateForm(toOutreachTemplateForm(restored));
      notifySuccess("模板已恢复", `“${restored.name}”已回到可用模板列表。`);
    } catch (templateError) {
      notifyError(
        "恢复模板失败",
        getActionErrorMessage(templateError, "恢复发信模板失败"),
      );
    } finally {
      setActingOnOutreachTemplate(false);
    }
  };

  const handleTemplateFileImport = async (file: File) => {
    if (importingTemplateFile) {
      return;
    }

    const importTargetEditorId = templateEditorId;
    const hasExistingTemplateBody = hasVisibleTemplateBody(
      outreachTemplateForm,
    );

    if (hasExistingTemplateBody) {
      const shouldReplaceTemplateBody = await confirm({
        title: "确认覆盖当前模板正文？",
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
      if (templateEditorIdRef.current !== importTargetEditorId) {
        return;
      }

      setOutreachTemplateForm((previous) => ({
        ...previous,
        name:
          previous.name.trim() || file.name.replace(/\.[^.]+$/, "").trim(),
        outreach_template_body_text: imported.body_text,
        outreach_template_body_html: imported.body_html,
      }));
      notifySuccess("模板导入成功", `已导入 ${imported.format_name} 并生成纯文本正文。`);
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
    if (!desktopBackendReady) {
      notifyError(
        "系统正在准备本地数据",
        desktopDisableReason ?? "请等待系统准备完成后再测试模型。",
      );
      return;
    }

    setTestingLLMConnection(true);
    setLlmProbeResult(null);
    const testedProfileId = editingLLM.id;
    const testedPayload = toLLMPayload(llmForm);
    try {
      const result = await testLLMProfilePreview(testedPayload);
      if (!result.ok) {
        setLlmProbeResult(result);
        return;
      }

      try {
        await updateLLMProfile(testedProfileId, testedPayload);
        await refreshSelections();
        setLlmProbeResult(result);
      } catch (saveError) {
        const message = getActionErrorMessage(
          saveError,
          "模型配置自动保存失败",
        );
        setLlmProbeResult({
          ...result,
          ok: false,
          message: `模型测试成功，但配置自动保存失败：${message}`,
        });
        notifyError("模型配置自动保存失败", message);
      }
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
    if (!desktopBackendReady) {
      notifyError(
        "系统正在准备本地数据",
        desktopDisableReason ?? "请等待系统准备完成后再获取模型列表。",
      );
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

  const saveIdentity = async (
    { silent = false }: { silent?: boolean } = {},
  ): Promise<IdentityDTO | null> => {
    if (!desktopBackendReady) {
      notifyError(
        "系统正在准备本地数据",
        "这不是身份配置错误。请等待系统准备完成后再保存，已填写内容不会丢失。",
      );
      return null;
    }

    if (!identityForm.profile_name.trim() || !identityForm.sender_name.trim()) {
      notifyFormErrors("请检查表单", ["请填写身份名称和发件人姓名"]);
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
    const isCreatingIdentity = identityEditorId === "new";
    setSubmittingIdentity(true);
    try {
      const payload = toIdentityPayload(identityForm);
      const saved = isExistingEditorId(identityEditorId)
        ? await updateIdentity(identityEditorId, payload)
        : await createIdentity(payload);
      await refreshSelections();
      if (isCreatingIdentity) {
        setSelectedIdentityId(saved.id);
      }
      applyIdentityEditorState(saved);
      if (!silent) {
        notifySuccess(identityEditorId === "new" ? "身份已创建" : "身份已保存");
      }
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

  const openIdentityDeletionImpact = async (identityId: number) => {
    setLoadingIdentityDeletionImpactId(identityId);
    try {
      setIdentityDeletionImpact(await getIdentityDeletionImpact(identityId));
    } catch (impactError) {
      notifyError(
        "无法检查删除影响",
        getActionErrorMessage(impactError, "读取身份关联数据失败"),
      );
    } finally {
      setLoadingIdentityDeletionImpactId(null);
    }
  };

  const deleteSelectedIdentity = async () => {
    const impact = identityDeletionImpact;
    if (!impact?.can_delete || deletingIdentity) {
      return;
    }
    setDeletingIdentity(true);
    try {
      await deleteIdentity(impact.identity_id, impact.revision);
      await refreshSelections();
      const emptyForm = createEmptyIdentityForm();
      identityEditorIdRef.current = null;
      identityFormRef.current = emptyForm;
      identityFormBaselineRef.current = emptyForm;
      setIdentityEditorId(null);
      setIdentityForm(emptyForm);
      setSmtpPasswordVisible(false);
      setIdentityDeletionImpact(null);
      notifySuccess(
        `已删除身份配置“${impact.identity_name}”`,
        "邮箱密码已清除，历史任务、通信记录和材料均已保留。",
      );
    } catch (deleteError) {
      if (deleteError instanceof ApiError) {
        const updatedImpact =
          typeof deleteError.details === "object" &&
          deleteError.details !== null &&
          "impact" in deleteError.details
            ? deleteError.details.impact
            : null;
        if (isIdentityDeletionImpact(updatedImpact)) {
          setIdentityDeletionImpact(updatedImpact);
        }
      }
      notifyError(
        deleteError instanceof ApiError &&
          deleteError.code === "IDENTITY_DELETE_PLAN_STALE"
          ? "关联状态已变化"
          : "删除身份配置失败",
        getActionErrorMessage(deleteError, "删除身份配置失败"),
      );
    } finally {
      setDeletingIdentity(false);
    }
  };

  saveIdentityRef.current = saveIdentity;

  useEffect(() => {
    return registerWorkspaceDraftGuard(async (request) => {
      if (
        request?.nextLlmProfileId !== undefined &&
        request.nextIdentityId === undefined &&
        request.nextIdentityEditorId === undefined &&
        request.nextPath === undefined
      ) {
        return true;
      }

      const nextEditorId =
        request?.nextIdentityEditorId ?? request?.nextIdentityId;
      if (
        nextEditorId !== undefined &&
        nextEditorId === identityEditorIdRef.current &&
        request?.nextPath === undefined
      ) {
        return true;
      }
      if (
        areIdentityFormsEqual(
          identityFormRef.current,
          identityFormBaselineRef.current,
        )
      ) {
        return true;
      }
      if (submittingIdentity || testingIdentityConnection !== null) {
        notifyError(
          "身份配置正在处理",
          "请等待当前保存或连接测试完成后再切换。",
        );
        return false;
      }

      const action = await choose({
        title: "保存身份修改？",
        description: "切换后，未保存的身份配置修改将丢失。",
        confirmLabel: "保存并切换",
        secondaryLabel: "不保存切换",
        cancelLabel: "继续编辑",
      });
      if (action === "cancel") {
        return false;
      }
      if (action === "secondary") {
        return true;
      }

      const saved = await saveIdentityRef.current({ silent: true });
      if (!saved) {
        return false;
      }
      notifySuccess("身份配置已保存");
      return true;
    });
  }, [
    choose,
    notifyError,
    notifySuccess,
    registerWorkspaceDraftGuard,
    submittingIdentity,
    testingIdentityConnection,
  ]);

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
      const wasCreating = llmEditorId === "new";
      const payload = toLLMPayload(llmForm);
      const saved = isExistingEditorId(llmEditorId)
        ? await updateLLMProfile(llmEditorId, payload)
        : await createLLMProfile(payload);
      if (wasCreating) {
        setSelectedLlmProfileId(saved.id);
      }
      await refreshSelections();
      setLlmEditorId(saved.id);
      setLlmForm(toLLMForm(saved));
      notifySuccess(wasCreating ? "模型配置已创建" : "模型配置已保存");
    } catch (saveError) {
      notifyError(
        "模型保存失败",
        getActionErrorMessage(saveError, "模型配置保存失败"),
      );
    } finally {
      setSubmittingLLM(false);
    }
  };

  const openLLMDeletionImpact = async (profileId: number) => {
    setLoadingLLMDeletionImpactId(profileId);
    try {
      const impact = await getLLMProfileDeletionImpact(profileId);
      setReplacementDefaultLLMId(null);
      setLlmDeletionImpact(impact);
    } catch (impactError) {
      notifyError(
        "无法检查删除影响",
        getActionErrorMessage(impactError, "读取模型配置关联数据失败"),
      );
    } finally {
      setLoadingLLMDeletionImpactId(null);
    }
  };

  const retireSelectedLLM = async () => {
    const impact = llmDeletionImpact;
    if (!impact?.can_delete || retiringLLM) {
      return;
    }
    setRetiringLLM(true);
    try {
      const result = await deleteLLMProfile(
        impact.profile_id,
        impact.revision,
        impact.is_default ? replacementDefaultLLMId : null,
      );
      if (selectedLlmProfileId === impact.profile_id) {
        setSelectedLlmProfileId(null);
      }
      await refreshSelections();
      setLlmDeletionImpact(null);
      setReplacementDefaultLLMId(null);
      setLlmEditorId(null);
      setLlmForm(createEmptyLLMForm());
      const preservedCount = Object.values(result.references_preserved).reduce(
        (total, count) => total + count,
        0,
      );
      notifySuccess(
        `已删除模型配置“${result.profile_name}”`,
        preservedCount > 0
          ? `API Key 已清除，${preservedCount} 条关联记录已保留。`
          : "API Key 已清除，历史数据未受影响。",
      );
    } catch (deleteError) {
      if (deleteError instanceof ApiError) {
        const updatedImpact =
          typeof deleteError.details === "object" &&
          deleteError.details !== null &&
          "impact" in deleteError.details
            ? deleteError.details.impact
            : null;
        if (isDeletionImpact(updatedImpact)) {
          setLlmDeletionImpact(updatedImpact);
          setReplacementDefaultLLMId(null);
        }
      }
      notifyError(
        deleteError instanceof ApiError &&
          deleteError.code === "LLM_PROFILE_DELETE_PLAN_STALE"
          ? "关联状态已变化"
          : "删除模型配置失败",
        getActionErrorMessage(deleteError, "删除模型配置失败"),
      );
    } finally {
      setRetiringLLM(false);
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

  const handleDownloadMaterial = async (material: IdentityMaterialDTO) => {
    try {
      await downloadMaterial(material.id, material.original_filename);
    } catch (downloadError) {
      notifyError(
        "下载材料失败",
        getActionErrorMessage(downloadError, "下载材料失败"),
      );
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
    if (!editingIdentity) {
      return;
    }
    setActingOnMaterial(true);
    try {
      await setPrimaryMaterial(editingIdentity.id, material.id);
      await refreshSelections();
      notifySuccess(
        "设为默认材料成功",
        `已将“${material.display_name}”设为“${getIdentityProfileName(editingIdentity)}”的默认材料。`,
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
    setActingOnMaterial(true);
    try {
      const impact = await getMaterialDeletionImpact(material.id);
      const confirmed = await confirm({
        title: `永久删除材料“${material.display_name}”？`,
        description: buildMaterialDeletionConfirmationDescription(impact),
        confirmLabel: "确认永久删除",
        cancelLabel: "先保留",
        tone: "danger",
      });
      if (!confirmed) {
        return;
      }
      await deleteMaterial(material.id, impact.deletion_fingerprint);
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
                void (async () => {
                  if (
                    !(await requestWorkspaceDraftGuard({
                      nextIdentityId: editingIdentity.id,
                    }))
                  ) {
                    return;
                  }
                  setSelectedIdentityId(editingIdentity.id);
                  notifySuccess(`当前身份：${getIdentityProfileName(editingIdentity)}`);
                })();
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
                    updateIdentityFormState(
                      (previous) => ({
                        ...previous,
                        is_default: true,
                      }),
                      true,
                    );
                    notifySuccess(`默认身份：${getIdentityProfileName(editingIdentity)}`);
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
            disabled={loadingIdentityDeletionImpactId === editingIdentity.id}
            onClick={() => void openIdentityDeletionImpact(editingIdentity.id)}
            className="ui-btn-danger"
          >
            {loadingIdentityDeletionImpactId === editingIdentity.id
              ? "检查关联数据…"
              : "删除"}
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
          正在加载配置…
        </div>
      ) : (
        <div className="mt-6 space-y-6">
          {shouldShowProfileSetupRecommendations ? (
            <section className="rounded-3xl border border-stone-200 bg-[linear-gradient(135deg,rgba(248,244,236,0.95),rgba(255,255,255,0.98))] p-6 shadow-sm">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <h2 className="text-xl font-semibold text-stone-900">
                    首次配置
                  </h2>
                  <p className="mt-2 text-sm leading-6 text-stone-600">
                    完成以下 4 项即可开始使用。
                  </p>
                </div>
                <ContextualHelpLink
                  href={PROFILE_HELP_LINKS.firstRun}
                  tone="surface"
                >
                  查看完整配置教程
                </ContextualHelpLink>
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
          ) : null}

          <ProfileSetupSection
            sectionId="identity"
            title="发件身份"
            description="管理发件邮箱与收发设置。"
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
            helpAction={
              <ContextualHelpLink
                href={PROFILE_HELP_LINKS.mailAuthorization}
                compact
              >
                邮箱配置教程
              </ContextualHelpLink>
            }
          >
            <div className="mt-5 rounded-3xl border border-stone-200 bg-[#fcfbf8] p-4">
                <EditorSwitcher
                  label={editingIdentity
                    ? `编辑发件身份：${getIdentityProfileName(editingIdentity)}`
                    : "新建发件身份"}
                  helper={
                    identities.length > 0 ? "点选切换，或新建一套。" : undefined
                  }
                  options={identities.map((identity) => ({
                    ...identity,
                    name: getIdentityProfileName(identity),
                  }))}
                  activeId={identityEditorId}
                  createLabel="新建发件身份"
                  creatingLabel="新建发件身份"
                  onCreate={() => void beginIdentityCreation()}
                  onSelect={(identityId) => void openIdentityEditor(identityId)}
                />
            </div>

            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <label className="block">
                {renderFieldLabel("身份名称", true)}
                <input
                  ref={identityNameInputRef}
                  aria-label="身份名称"
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
                {renderFieldLabel("SMTP 服务器", true)}
                <input
                  value={identityForm.smtp_host}
                  onChange={(event) => handleSmtpHostChange(event.target.value)}
                  className={inputClassName}
                  placeholder="示例：smtp.163.com"
                />
              </label>
              <div className="block">
                <FormFieldHeader
                  id="smtp-password"
                  label="邮箱授权码"
                  required
                  help={
                    <ContextualHelpLink
                      href={PROFILE_HELP_LINKS.mailAuthorization}
                      compact
                    >
                      如何获取授权码
                    </ContextualHelpLink>
                  }
                />
                <div className="group relative">
                  <input
                    id="smtp-password"
                    type={smtpPasswordVisible ? "text" : "password"}
                    value={identityForm.smtp_password}
                    onChange={(event) =>
                      setIdentityForm((previous) => ({
                        ...previous,
                        smtp_password: event.target.value,
                      }))
                    }
                    className={clsx(inputClassName, "pr-11")}
                    placeholder="授权码或应用专用密码"
                  />
                  <button
                    type="button"
                    aria-label={smtpPasswordVisible ? "隐藏授权码" : "显示授权码"}
                    aria-pressed={smtpPasswordVisible}
                    title={smtpPasswordVisible ? "隐藏授权码" : "显示授权码"}
                    onClick={() => setSmtpPasswordVisible((visible) => !visible)}
                    className="pointer-events-none absolute inset-y-0 right-2 my-auto flex h-7 w-7 items-center justify-center rounded-lg text-stone-400 opacity-0 transition hover:bg-stone-100 hover:text-stone-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100"
                  >
                    {smtpPasswordVisible ? (
                      <EyeOff aria-hidden="true" className="h-4 w-4" />
                    ) : (
                      <Eye aria-hidden="true" className="h-4 w-4" />
                    )}
                  </button>
                </div>
                <p className="mt-2 text-xs leading-5 text-stone-500">
                  不是邮箱登录密码，请填写授权码或应用专用密码。
                </p>
              </div>
              <label className="block">
                {renderFieldLabel("SMTP 端口", true)}
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
              <label className="block">
                {renderFieldLabel("IMAP 服务器", true)}
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
                {renderFieldLabel("IMAP 端口", true)}
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
            description="准备匹配材料和发信模板。"
            open={openSetupSections.materials}
            renderContent={renderedSetupSections.materials}
            onToggle={() => toggleSetupSection("materials")}
            onExitComplete={() => handleSetupSectionExitComplete("materials")}
            sectionRef={setMaterialsSetupSectionRef}
            badge={
              <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
                管理材料和模板
              </span>
            }
          >
            <div className="mt-6">
              <OutreachTemplateSummaryCard
                form={identityForm}
                template={identityDefaultOutreachTemplate}
                globalTemplate={globalDefaultOutreachTemplate}
                templateCount={activeOutreachTemplates.length}
                loadingTemplates={loadingOutreachTemplates}
                onOpen={openOutreachTemplateLibrary}
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
            description="连接并测试用于写信的 AI 模型。"
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
            helpAction={
              <ContextualHelpLink
                href={PROFILE_HELP_LINKS.llmConfiguration}
                compact
              >
                模型配置教程
              </ContextualHelpLink>
            }
          >
            <div className="mt-5 rounded-3xl border border-stone-200 bg-[#fcfbf8] p-4">
                <EditorSwitcher
                  label={editingLLM ? `编辑模型：${editingLLM.name}` : "新建模型"}
                  helper={
                    llmProfiles.length > 0
                      ? "点选切换，或新建一套。"
                      : undefined
                  }
                  options={llmProfiles}
                  activeId={llmEditorId}
                  createLabel="新建模型"
                  creatingLabel="新建模型"
                  onCreate={beginLLMCreation}
                  onSelect={openLLMEditor}
                />
              <div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-500">
                <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
                  DeepSeek 示例
                </span>
                <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
                  随机性（Temperature）{DEFAULT_LLM_TEMPERATURE}
                </span>
                <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
                  草稿上限 {DEFAULT_LLM_MAX_TOKENS} Token
                </span>
              </div>
            </div>

            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <label className="block">
                {renderFieldLabel("名称", true)}
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
              <div className="block md:col-span-2">
                <FormFieldHeader
                  id="llm-api-base-url"
                  label="API Base URL"
                  required
                  help={
                    <ContextualHelpLink
                      href={PROFILE_HELP_LINKS.llmConfiguration}
                      compact
                    >
                      查看填写示例
                    </ContextualHelpLink>
                  }
                />
                <input
                  id="llm-api-base-url"
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
                <p className="mt-2 text-xs leading-5 text-stone-500">
                  模型服务商提供的 OpenAI 兼容接口地址，不是平台官网地址。
                </p>
              </div>
              <div className="block">
                <FormFieldHeader
                  id="llm-api-key"
                  label="API Key"
                  required
                  help={
                    <ContextualHelpLink
                      href={PROFILE_HELP_LINKS.llmConfiguration}
                      compact
                    >
                      如何获取 API Key
                    </ContextualHelpLink>
                  }
                />
                <input
                  id="llm-api-key"
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
                <p className="mt-2 text-xs leading-5 text-stone-500">
                  在模型服务商控制台创建，不是账号登录密码。
                </p>
              </div>
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
                保存模型
              </button>
              {editingLLM && (
                <>
                  <button
                    type="button"
                    onClick={() => void runLlmModelsFetch()}
                    disabled={fetchingLLMModels || !desktopBackendReady}
                    className={getActionButtonClassName(
                      llmModelsActionState,
                      fetchingLLMModels || !desktopBackendReady,
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
                    disabled={testingLLMConnection || !desktopBackendReady}
                    className={getActionButtonClassName(
                      llmProbeActionState,
                      testingLLMConnection || !desktopBackendReady,
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
                        void (async () => {
                          if (
                            !(await requestWorkspaceDraftGuard({
                              nextLlmProfileId: editingLLM.id,
                            }))
                          ) {
                            return;
                          }
                          setSelectedLlmProfileId(editingLLM.id);
                          notifySuccess(`当前模型：${editingLLM.name}`);
                        })();
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
                            notifySuccess(`默认模型：${editingLLM.name}`);
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
                    onClick={() => void openLLMDeletionImpact(editingLLM.id)}
                    className="ui-btn-danger inline-flex items-center gap-2"
                    disabled={loadingLLMDeletionImpactId === editingLLM.id}
                  >
                    {loadingLLMDeletionImpactId === editingLLM.id ? (
                      <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
                    ) : (
                      <Trash2 aria-hidden="true" className="h-4 w-4" />
                    )}
                    {loadingLLMDeletionImpactId === editingLLM.id
                      ? "正在检查"
                      : "删除"}
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
            description="先给自己发送一封测试邮件。"
            open={openSetupSections.test}
            renderContent={renderedSetupSections.test}
            onToggle={() => toggleSetupSection("test")}
            onExitComplete={() => handleSetupSectionExitComplete("test")}
            sectionRef={setTestSetupSectionRef}
            badge={
              <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
                测试发信设置
              </span>
            }
          >
            <div className="mt-6">
              <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                <Send className="h-4 w-4 text-primary" />
                给自己发一封测试邮件
              </div>
              <p className="mt-2 text-sm leading-6 text-stone-600">
                检查模板、附件、模型和邮箱设置。
              </p>
              <p className="mt-2 text-sm leading-6 text-stone-500">
                仅用于测试，不会创建导师任务。
              </p>
              <div className="mt-4">
                <Link to="/test-compose" className="ui-btn-primary">
                  <Send className="h-4 w-4" />
                  开始测试
                </Link>
              </div>
            </div>

            <div className="mt-6 rounded-2xl border border-emerald-200 bg-emerald-50/80 px-4 py-4 text-sm leading-6 text-emerald-800">
              测试成功后，导入导师并创建任务。
            </div>
          </ProfileSetupSection>


          <CommunicationSharingPanel />

          <OtherSettingsCard />

          <AgentSupportCard />

          <DiagnosticLogPanel />

          <ProjectAcknowledgements />
        </div>
      )}
      <OutreachTemplateModal
        open={templateModalOpen}
        importingTemplateFile={importingTemplateFile}
        savingTemplate={savingOutreachTemplate}
        actingOnTemplate={actingOnOutreachTemplate}
        loadingTemplates={loadingOutreachTemplates}
        templates={activeOutreachTemplates}
        archivedTemplates={archivedOutreachTemplates}
        editorId={templateEditorId}
        form={outreachTemplateForm}
        identityLabel={editingIdentity ? "当前身份" : "新身份"}
        identityDefaultTemplateId={identityForm.default_outreach_template_id}
        onClose={() => setTemplateModalOpen(false)}
        onComplete={() =>
          void saveOutreachTemplate().then((saved) => {
            if (saved) {
              setTemplateModalOpen(false);
            }
          })
        }
        onCreate={beginOutreachTemplateCreation}
        onSelect={openOutreachTemplateEditor}
        onDuplicate={(templateId) =>
          void handleDuplicateOutreachTemplate(templateId)
        }
        onSetIdentityDefault={(template) =>
          void handleSetIdentityDefaultTemplate(template)
        }
        onClearIdentityDefault={() =>
          void handleClearIdentityDefaultTemplate()
        }
        onSetGlobalDefault={(templateId) =>
          void handleSetGlobalDefaultTemplate(templateId)
        }
        onDelete={(template) => void handleDeleteOutreachTemplate(template)}
        onRestore={(template) => void handleRestoreOutreachTemplate(template)}
        onImport={(file) => void handleTemplateFileImport(file)}
        onNameChange={(value) =>
          setOutreachTemplateForm((previous) => ({
            ...previous,
            name: value,
          }))
        }
        onModeChange={(value) =>
          setOutreachTemplateForm((previous) => ({
            ...previous,
            outreach_generation_mode: value,
          }))
        }
        onSubjectChange={(value) =>
          setOutreachTemplateForm((previous) => ({
            ...previous,
            outreach_template_subject: value,
          }))
        }
        onBodyChange={({ html, text }) =>
          setOutreachTemplateForm((previous) => ({
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
          onDownload={(material) => void handleDownloadMaterial(material)}
          onClose={() => setMaterialModalOpen(false)}
          onSetPrimary={(material) => void handleSetPrimaryMaterial(material)}
          onDelete={(material) => void handleDeleteMaterial(material)}
        />
      )}
      {llmDeletionImpact && (
        <LLMDeletionDialog
          impact={llmDeletionImpact}
          replacementProfiles={llmProfiles.filter(
            (profile) => profile.id !== llmDeletionImpact.profile_id,
          )}
          replacementProfileId={replacementDefaultLLMId}
          busy={retiringLLM}
          onReplacementChange={setReplacementDefaultLLMId}
          onClose={() => {
            if (!retiringLLM) {
              setLlmDeletionImpact(null);
              setReplacementDefaultLLMId(null);
            }
          }}
          onConfirm={() => void retireSelectedLLM()}
        />
      )}
      {identityDeletionImpact && (
        <IdentityDeletionDialog
          impact={identityDeletionImpact}
          busy={deletingIdentity}
          onClose={() => {
            if (!deletingIdentity) {
              setIdentityDeletionImpact(null);
            }
          }}
          onConfirm={() => void deleteSelectedIdentity()}
        />
      )}
      {confirmDialog}
    </main>
  );
};
