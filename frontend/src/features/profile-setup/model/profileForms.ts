import type {
  IdentityDTO,
  IdentityMaterialDTO,
  IdentityMaterialType,
  IdentityPayload,
  LLMProfileDTO,
  LLMProfilePayload,
  OutreachGenerationMode,
  OutreachTemplateDTO,
  OutreachTemplatePayloadDTO,
} from "@/types";

export type IdentityFormState = {
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
  default_outreach_template_id: number | null;
  same_domain_cooldown_minutes: string;
  is_default: boolean;
};

export type OutreachTemplateFormState = {
  name: string;
  outreach_generation_mode: OutreachGenerationMode;
  outreach_template_subject: string;
  outreach_template_body_text: string;
  outreach_template_body_html: string;
  is_default: boolean;
};

export type LLMFormState = {
  name: string;
  api_base_url: string;
  api_key: string;
  model_name: string;
  is_default: boolean;
};

export type EditorId = number | "new" | null;
export type MaterialFilterValue = IdentityMaterialType | "all";
export type ProfileSetupSectionId = "identity" | "materials" | "model" | "test";
export type ProfileSetupItem = {
  id: ProfileSetupSectionId;
  label: string;
  title: string;
  description: string;
  completed: boolean;
  statusDetail: string;
};

const DEFAULT_LLM_PROVIDER = "openai";
export const DEFAULT_LLM_TEMPERATURE = 0.2;
export const DEFAULT_LLM_MAX_TOKENS = 6000;
const PRIMARY_MATERIAL_EXTENSIONS = [".pdf", ".doc", ".docx", ".txt", ".md"];

export const TEMPLATE_FILE_ACCEPT = ".docx,.html,.htm,.txt,.md";

export const PROFILE_SETUP_STAGES = [
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
    description: "配置并测试 AI 模型。",
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
  description?: string;
}>;

export const createEmptyIdentityForm = (): IdentityFormState => ({
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
  default_outreach_template_id: null,
  same_domain_cooldown_minutes: "",
  is_default: false,
});

export const areIdentityFormsEqual = (
  left: IdentityFormState,
  right: IdentityFormState,
) =>
  (Object.keys(left) as Array<keyof IdentityFormState>).every(
    (key) => left[key] === right[key],
  );

export const createEmptyOutreachTemplateForm = (): OutreachTemplateFormState => ({
  name: "",
  outreach_generation_mode: "llm",
  outreach_template_subject: "",
  outreach_template_body_text: "",
  outreach_template_body_html: "",
  is_default: false,
});

export const createEmptyLLMForm = (): LLMFormState => ({
  name: "",
  api_base_url: "",
  api_key: "",
  model_name: "",
  is_default: false,
});

export const inferImapHost = (smtpHost: string) =>
  smtpHost.trim().replace(/smtp/gi, "imap");

export const canUseAsPrimaryMaterial = (material: IdentityMaterialDTO) => {
  const filename = material.original_filename.toLowerCase();
  return PRIMARY_MATERIAL_EXTENSIONS.some((suffix) =>
    filename.endsWith(suffix),
  );
};

export const shouldSyncImapHost = (smtpHost: string, imapHost: string) => {
  const trimmedImapHost = imapHost.trim();
  if (!trimmedImapHost) {
    return true;
  }
  return trimmedImapHost === inferImapHost(smtpHost);
};

export const hasVisibleTemplateBody = ({
  outreach_template_body_text,
}: Pick<OutreachTemplateFormState, "outreach_template_body_text">) =>
  Boolean(outreach_template_body_text.trim());

export const getIdentityProfileName = (identity: IdentityDTO) =>
  identity.profile_name || identity.name;

export const getIdentitySenderName = (identity: IdentityDTO) =>
  identity.sender_name || getIdentityProfileName(identity);

export const toIdentityForm = (identity: IdentityDTO): IdentityFormState => {
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
    default_outreach_template_id: identity.default_outreach_template_id ?? null,
    same_domain_cooldown_minutes:
      identity.same_domain_cooldown_minutes === null
        ? ""
        : String(identity.same_domain_cooldown_minutes),
    is_default: identity.is_default,
  };
};

export const toLLMForm = (profile: LLMProfileDTO): LLMFormState => ({
  name: profile.name,
  api_base_url: profile.api_base_url ?? "",
  api_key: profile.api_key,
  model_name: profile.model_name,
  is_default: profile.is_default,
});

export const toIdentityPayload = (form: IdentityFormState): IdentityPayload => {
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
    default_outreach_template_id: form.default_outreach_template_id,
    same_domain_cooldown_minutes: form.same_domain_cooldown_minutes
      ? Number(form.same_domain_cooldown_minutes)
      : null,
    is_default: form.is_default,
  };
};

export const toOutreachTemplateForm = (
  template: OutreachTemplateDTO,
): OutreachTemplateFormState => ({
  name: template.name,
  outreach_generation_mode: template.recommended_generation_mode,
  outreach_template_subject: template.subject ?? "",
  outreach_template_body_text: template.body_text ?? "",
  outreach_template_body_html: template.body_html ?? "",
  is_default: template.is_default,
});

export const toOutreachTemplatePayload = (
  form: OutreachTemplateFormState,
): OutreachTemplatePayloadDTO => ({
  name: form.name.trim(),
  recommended_generation_mode: form.outreach_generation_mode,
  subject: form.outreach_template_subject.trim() || null,
  body_text: form.outreach_template_body_text.trim() || null,
  body_html: form.outreach_template_body_html.trim() || null,
  is_default: form.is_default,
});

export const applyOutreachTemplateToIdentityForm = (
  form: IdentityFormState,
  template: OutreachTemplateDTO,
): IdentityFormState => ({
  ...form,
  default_outreach_template_id: template.id,
  outreach_generation_mode: template.recommended_generation_mode,
  outreach_template_subject: template.subject ?? "",
  outreach_template_body_text: template.body_text ?? "",
  outreach_template_body_html: template.body_html ?? "",
});

export const clearOutreachTemplateFromIdentityForm = (
  form: IdentityFormState,
): IdentityFormState => ({
  ...form,
  default_outreach_template_id: null,
  outreach_generation_mode: "llm",
  outreach_template_subject: "",
  outreach_template_body_text: "",
  outreach_template_body_html: "",
});

export const toLLMPayload = (form: LLMFormState): LLMProfilePayload => ({
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

export const isExistingEditorId = (value: EditorId): value is number =>
  typeof value === "number";
